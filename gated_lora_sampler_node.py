# -*- coding: utf-8 -*-
"""
Gated LoRA Sampler -- Krea2-style early/late LoRA without hooks.

Memory-safe and quantization-safe: the LoRA is baked once into a model clone
(the same path a normal LoRA loader uses, so it fits wherever your normal LoRA
workflows fit), then sampling is split into two stock passes at `crossover`:
one pass uses the LoRA model, the other the base model, handing the latent over
mid-schedule. No per-step weight hooks, so no requantization OOM on fp8/NVFP4
models.

  apply_during=early -> LoRA active steps [0, crossover], base after  (locks content)
  apply_during=late  -> base steps [0, crossover], LoRA after         (refines detail)

This is the bulletproof alternative to AzGatedLoraLoader (hooks), which is
elegant but needs spare VRAM to requantize quantized weights mid-sampling.
"""

import comfy.sd
import comfy.utils
import comfy.samplers
import folder_paths
from comfy_api.latest import io


class AzGatedLoraSampler(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzGatedLoraSampler",
            display_name="Gated LoRA Sampler (timestep)",
            category="AZ_Nodes",
            description="Sample with a LoRA active only early or late in the schedule "
                        "(Krea2-style crossover), via two stock passes. Memory-safe on "
                        "quantized models; no hooks.",
            inputs=[
                io.Model.Input("model", tooltip="Base model. LoRA is baked into a clone internally."),
                io.Conditioning.Input("positive"),
                io.Conditioning.Input("negative"),
                io.Latent.Input("latent_image"),
                io.Combo.Input("lora_name", options=folder_paths.get_filename_list("loras")),
                io.Float.Input("strength_model", default=1.0, min=-20.0, max=20.0, step=0.01,
                               tooltip="LoRA strength on the UNet."),
                io.Combo.Input("apply_during", options=["early", "late"], default="early",
                               tooltip="early = LoRA on for steps 0..crossover; late = on for crossover..end."),
                io.Float.Input("crossover", default=0.5, min=0.0, max=1.0, step=0.01,
                               tooltip="Handoff point as a fraction of total steps."),
                io.Int.Input("seed", default=0, min=0, max=0xffffffffffffffff,
                             control_after_generate=True),
                io.Int.Input("steps", default=20, min=1, max=10000),
                io.Float.Input("cfg", default=8.0, min=0.0, max=100.0, step=0.1),
                io.Combo.Input("sampler_name", options=comfy.samplers.KSampler.SAMPLERS),
                io.Combo.Input("scheduler", options=comfy.samplers.KSampler.SCHEDULERS),
                io.Float.Input("denoise", default=1.0, min=0.0, max=1.0, step=0.01, optional=True),
            ],
            outputs=[
                io.Latent.Output(),
            ],
        )

    @classmethod
    def execute(cls, model, positive, negative, latent_image, lora_name, strength_model,
                apply_during, crossover, seed, steps, cfg, sampler_name, scheduler, denoise=1.0):
        from nodes import common_ksampler

        lora_model = model
        if strength_model != 0.0:
            lora_path = folder_paths.get_full_path("loras", lora_name)
            if lora_path is None:
                raise FileNotFoundError(f"LoRA not found: {lora_name}")
            lora = comfy.utils.load_torch_file(lora_path, safe_load=True)
            lora_model, _ = comfy.sd.load_lora_for_models(model, None, lora, strength_model, 0.0)

        early = apply_during == "early"
        first_model = lora_model if early else model
        second_model = model if early else lora_model

        cross = max(0, min(steps, round(crossover * steps)))

        # Degenerate splits: one model covers the whole schedule.
        if cross <= 0:
            return io.NodeOutput(common_ksampler(
                second_model, seed, steps, cfg, sampler_name, scheduler,
                positive, negative, latent_image, denoise=denoise)[0])
        if cross >= steps:
            return io.NodeOutput(common_ksampler(
                first_model, seed, steps, cfg, sampler_name, scheduler,
                positive, negative, latent_image, denoise=denoise)[0])

        # Pass 1: steps [0, cross], keep leftover noise for the handoff.
        (stage1,) = common_ksampler(
            first_model, seed, steps, cfg, sampler_name, scheduler,
            positive, negative, latent_image, denoise=denoise,
            start_step=0, last_step=cross, force_full_denoise=False)

        # Pass 2: steps [cross, end], no fresh noise, finish the denoise.
        (stage2,) = common_ksampler(
            second_model, seed, steps, cfg, sampler_name, scheduler,
            positive, negative, stage1, denoise=denoise, disable_noise=True,
            start_step=cross, last_step=steps, force_full_denoise=True)

        return io.NodeOutput(stage2)
