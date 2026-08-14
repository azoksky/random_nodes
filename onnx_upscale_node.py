# -*- coding: utf-8 -*-
"""
Run ONNX super-resolution models (e.g. Kim2091's UltraSharpV2 ONNX export)
via onnxruntime-gpu instead of spandrel/PyTorch.

Two reasons to prefer this over the stock "Load Upscale Model" node:
- spandrel only runs architectures it has a Python definition for; an .onnx
  graph is self-describing, so anything exported to ONNX works regardless
  of whether spandrel recognizes its architecture.
- onnxruntime fuses/optimizes the static graph ahead of time and has far
  less per-call Python/dispatch overhead than PyTorch eager mode, which
  matters when upscaling hundreds of video frames one at a time.

Requires `onnxruntime-gpu` in the ComfyUI python env:
    python -m pip install onnxruntime-gpu

Drop .onnx upscale models into the same models/upscale_models folder used
by the stock loader; they're listed separately here since the stock loader
only scans for .ckpt/.pt/.pth/.safetensors/etc, not .onnx.
"""

import os
import sys
import logging

import numpy as np
import torch

import folder_paths
import comfy.utils
import comfy.model_management
from comfy_api.latest import io

_ONNX_FOLDER_KEY = "onnx_upscale_models"
if _ONNX_FOLDER_KEY not in folder_paths.folder_names_and_paths:
    _base_dirs = folder_paths.folder_names_and_paths.get(
        "upscale_models", ([os.path.join(folder_paths.models_dir, "upscale_models")], set())
    )[0]
    folder_paths.folder_names_and_paths[_ONNX_FOLDER_KEY] = (list(_base_dirs), {".onnx"})


def _add_torch_dll_dirs():
    # onnxruntime-gpu's CUDAExecutionProvider needs CUDA/cuDNN DLLs on the
    # search path. Reuse the ones already bundled with the installed torch
    # wheel instead of requiring a separate CUDA toolkit install.
    if sys.platform != "win32":
        return
    try:
        torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.isdir(torch_lib):
            os.add_dll_directory(torch_lib)
    except Exception:
        pass


class OnnxUpscaleModel:
    def __init__(self, session, scale, input_name, output_name, np_dtype):
        self.session = session
        self.scale = scale
        self.input_name = input_name
        self.output_name = output_name
        self.np_dtype = np_dtype


class AzOnnxUpscaleModelLoader(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzOnnxUpscaleModelLoader",
            display_name="Load Upscale Model (ONNX)",
            category="AZ_Nodes",
            description=(
                "Loads a .onnx super-resolution model via onnxruntime-gpu. Works "
                "with any exported architecture, including ones spandrel doesn't "
                "recognize. Requires `pip install onnxruntime-gpu`."
            ),
            inputs=[
                io.Combo.Input("model_name", options=folder_paths.get_filename_list(_ONNX_FOLDER_KEY)),
                io.Combo.Input("provider", options=["CUDA", "CPU"], default="CUDA"),
            ],
            outputs=[
                io.Custom("ONNX_UPSCALE_MODEL").Output(display_name="ONNX_UPSCALE_MODEL"),
            ],
        )

    @classmethod
    def execute(cls, model_name, provider):
        try:
            import onnxruntime as ort
        except ImportError as e:
            raise RuntimeError(
                "onnxruntime is not installed in this ComfyUI python env. "
                "Run: python -m pip install onnxruntime-gpu"
            ) from e

        _add_torch_dll_dirs()

        model_path = folder_paths.get_full_path_or_raise(_ONNX_FOLDER_KEY, model_name)

        providers = ["CPUExecutionProvider"]
        if provider == "CUDA" and "CUDAExecutionProvider" in ort.get_available_providers():
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        elif provider == "CUDA":
            logging.warning("AzOnnxUpscaleModelLoader: CUDAExecutionProvider not available, falling back to CPU.")

        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(model_path, sess_options=sess_options, providers=providers)

        input_meta = session.get_inputs()[0]
        output_meta = session.get_outputs()[0]
        np_dtype = np.float16 if "float16" in input_meta.type else np.float32

        # Probe with a small dummy tile to read the model's actual scale
        # factor off its output shape, instead of hardcoding 2x/4x.
        dummy = np.zeros((1, 3, 64, 64), dtype=np_dtype)
        dummy_out = session.run([output_meta.name], {input_meta.name: dummy})[0]
        scale = dummy_out.shape[-1] / 64.0

        model = OnnxUpscaleModel(session, scale, input_meta.name, output_meta.name, np_dtype)
        return io.NodeOutput(model)


class AzImageUpscaleWithOnnxModel(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzImageUpscaleWithOnnxModel",
            display_name="Upscale Image (using ONNX Model)",
            category="AZ_Nodes",
            description=(
                "Runs images through an ONNX upscale model. Tiling is off by "
                "default; turn it on only if a full image doesn't fit in VRAM "
                "(splitting into tiles adds blending overhead and is slower "
                "when it isn't needed). output_scale + scale_mode control the "
                "final size relative to the model's native scale (e.g. get an "
                "effective 2x out of a 4x model)."
            ),
            inputs=[
                io.Custom("ONNX_UPSCALE_MODEL").Input("upscale_model"),
                io.Image.Input("image"),
                io.Boolean.Input(
                    "tiled_inference", default=False,
                    tooltip="Split each image into overlapping tiles before upscaling. Only needed if a full image OOMs at your resolution.",
                ),
                io.Int.Input("tile", default=512, min=64, max=2048, step=32),
                io.Int.Input("overlap", default=32, min=0, max=256, step=8),
                io.Float.Input(
                    "output_scale", default=0.0, min=0.0, max=8.0, step=0.05,
                    tooltip="0 = keep the model's native scale. Otherwise the final image is this factor of the input, e.g. 2.0 for an effective 2x from a 4x model. Combine with scale_mode below.",
                ),
                io.Combo.Input(
                    "scale_mode", options=["quality", "speed"], default="quality",
                    tooltip=(
                        "Only matters when output_scale < the model's native scale. "
                        "quality: run the model at full native scale on the full-size input, then "
                        "downsample the result (max detail, same compute as native, e.g. a 4x pass). "
                        "speed: shrink the input first so the model's native pass lands directly on "
                        "output_scale (real compute savings -- roughly (output_scale/native_scale)^2 "
                        "of the native cost -- at the expense of some fine detail, since the network "
                        "sees less source resolution)."
                    ),
                ),
            ],
            outputs=[
                io.Image.Output(),
            ],
        )

    @classmethod
    def execute(cls, upscale_model, image, tiled_inference, tile, overlap, output_scale, scale_mode):
        session = upscale_model.session
        np_dtype = upscale_model.np_dtype

        def run_batch(a):
            arr = a.cpu().numpy().astype(np_dtype)
            out = session.run([upscale_model.output_name], {upscale_model.input_name: arr})[0]
            return torch.from_numpy(out.astype(np.float32))

        in_img = image.movedim(-1, -3)
        output_device = comfy.model_management.intermediate_device()

        pre_shrunk = False
        if (
            scale_mode == "speed"
            and output_scale > 0
            and output_scale < upscale_model.scale - 1e-6
        ):
            pre_factor = output_scale / upscale_model.scale
            pre_h = max(1, round(in_img.shape[2] * pre_factor))
            pre_w = max(1, round(in_img.shape[3] * pre_factor))
            in_img = comfy.utils.common_upscale(in_img, pre_w, pre_h, "lanczos", "disabled")
            pre_shrunk = True

        if tiled_inference:
            t = tile
            oom = True
            while oom:
                try:
                    steps = in_img.shape[0] * comfy.utils.get_tiled_scale_steps(
                        in_img.shape[3], in_img.shape[2], tile_x=t, tile_y=t, overlap=overlap
                    )
                    pbar = comfy.utils.ProgressBar(steps)
                    s = comfy.utils.tiled_scale(
                        in_img, run_batch, tile_x=t, tile_y=t, overlap=overlap,
                        upscale_amount=upscale_model.scale, pbar=pbar, output_device=output_device,
                    )
                    oom = False
                except Exception as e:
                    t //= 2
                    if t < 64:
                        raise e
                    logging.warning(f"AzImageUpscaleWithOnnxModel: retrying with smaller tile size {t} after: {e}")
        else:
            try:
                # One session.run call for the whole batch -- fastest path
                # when the onnx graph has a dynamic batch axis.
                s = run_batch(in_img).to(output_device)
            except Exception as e:
                logging.warning(f"AzImageUpscaleWithOnnxModel: whole-batch pass failed ({e}), falling back to per-image.")
                pbar = comfy.utils.ProgressBar(in_img.shape[0])
                outs = []
                for i in range(in_img.shape[0]):
                    outs.append(run_batch(in_img[i:i + 1]).to(output_device))
                    pbar.update(1)
                s = torch.cat(outs, dim=0)

        s = torch.clamp(s, min=0, max=1.0)

        if not pre_shrunk and output_scale > 0 and abs(output_scale - upscale_model.scale) > 1e-3:
            target_h = round(image.shape[1] * output_scale)
            target_w = round(image.shape[2] * output_scale)
            s = comfy.utils.common_upscale(s, target_w, target_h, "lanczos", "disabled")

        return io.NodeOutput(s.movedim(-3, -1))
