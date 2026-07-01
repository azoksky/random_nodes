# -*- coding: utf-8 -*-
"""
Prompt Enhancer (Local llama.cpp) -> STRING.

Self-hosts a llama-server (downloaded binary + a local GGUF), then rewrites a
loose prompt into a structured, image-model-tailored prompt and streams the
result into a preview box. STRING in, STRING out - no CLIP, no CONDITIONING.

The server lifecycle (download binary, launch on a chosen device, stop) is driven
from the node UI. Enhancement talks to the local server over loopback with no
auth. The rewrite contract is shared with the remote enhancer node.
"""

import os
import re
import json
import time
import signal
import shutil
import asyncio
import threading
import subprocess
from collections import deque

import requests

from comfy_api.latest import io

from .prompt_enhancer_node import (
    _build_system, _clean, _MODEL_OPTIONS,
    _BASE_RULES, MODEL_GUIDES, _NSFW_RULES, _SFW_RULES,
)

try:
    from server import PromptServer
except Exception:
    PromptServer = None
try:
    from aiohttp import web
except Exception:
    web = None
try:
    from comfy.model_management import InterruptProcessingException
except Exception:
    class InterruptProcessingException(Exception):
        pass
try:
    from comfy.model_management import get_gpu_device_options
    _DEVICE_OPTIONS = get_gpu_device_options()
except Exception:
    _DEVICE_OPTIONS = ["default", "cpu", "gpu:0", "gpu:1"]


DEFAULT_MODELS_DIR = "/kaggle/pamel/models/large_lms"
DEFAULT_BIN_DIR = "/kaggle/tmp"
DEFAULT_PORT = 18081
DEFAULT_FLAGS = "-ngl 999 -c 4096 -fa on --jinja -np 1 --no-webui -cram 2048 -ctk q8_0 -ctv q8_0"

# Managed singleton engine (one llama-server for this process).
_ENGINE = {"proc": None, "model": None, "port": None, "libdir": None, "mmproj": None}
_LOG_BUF = deque(maxlen=500)
_ENGINE_LOCK = threading.Lock()

# node_id -> True when the user pressed Stop mid-stream
_STOP = {}
# node_id -> (fingerprint, enhanced_text) of the last completed run
_CACHE = {}


def _notify(chan, **data):
    if PromptServer is None:
        return
    try:
        PromptServer.instance.send_sync("az_llama", {"chan": chan, **data})
    except Exception:
        pass


def _console(msg):
    _LOG_BUF.append(msg)
    _notify("console", line=msg)


def _list_models(models_dir):
    """Split *.gguf in the dir into chat models vs mmproj (vision/audio) projectors."""
    d = (models_dir or DEFAULT_MODELS_DIR).strip()
    try:
        names = [f for f in os.listdir(d) if f.lower().endswith(".gguf")]
    except Exception:
        return [], []
    mmprojs = sorted(f for f in names if "mmproj" in f.lower())
    models = sorted(f for f in names if "mmproj" not in f.lower())
    return models, mmprojs


def _device_env(device):
    """Map a get_gpu_device_options() choice to child-process env overrides."""
    if device == "cpu":
        return {"CUDA_VISIBLE_DEVICES": ""}
    if device and device.startswith("gpu:"):
        return {"CUDA_VISIBLE_DEVICES": device[4:]}
    return {}  # "default": inherit


_NGL_FLAGS = {"-ngl", "--gpu-layers", "--n-gpu-layers"}


def _prep_flags(flags, device):
    """Split user flags; on CPU strip any -ngl and force 0 layers on GPU."""
    toks = (flags or "").split()
    if device != "cpu":
        return toks
    out, i = [], 0
    while i < len(toks):
        if toks[i] in _NGL_FLAGS:
            i += 2 if i + 1 < len(toks) and not toks[i + 1].startswith("-") else 1
            continue
        out.append(toks[i])
        i += 1
    return out + ["-ngl", "0"]


def _encode_image(image):
    """ComfyUI IMAGE tensor [B,H,W,C] 0..1 -> base64 PNG data URI. llama.cpp's
    mmproj resizes to the encoder's own resolution, so we only cap the longest
    side to keep the payload small."""
    import io as _io
    import base64 as _b64
    import numpy as np
    from PIL import Image as _PILImage
    arr = image[0] if getattr(image, "ndim", 3) == 4 else image
    arr = (arr.detach().cpu().numpy() * 255.0).clip(0, 255).astype("uint8")
    im = _PILImage.fromarray(arr)
    w, h = im.size
    m = max(w, h)
    if m > 1024:
        s = 1024.0 / m
        im = im.resize((max(1, round(w * s)), max(1, round(h * s))), _PILImage.LANCZOS)
    buf = _io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()


_IMG_ONLY_RULES = """

TASK - IMAGE TO PROMPT (no text idea was supplied):
The image itself is your brief. Reverse-engineer it into the prompt that would have produced it, then
write that prompt. Account for every prominent thing a viewer notices first and work down to the
supporting detail: the subject(s) and their identity, gender, age and count; pose, action and the way
they relate to each other; wardrobe; the setting and background; props and their materials; the palette;
the light and mood; and the camera - shot size, angle, lens feel and depth of field. Read attributes off
the pixels rather than assuming them. Claim nothing that is not visible and drop nothing that is
prominent. Write it as a forward generation prompt, never as commentary about a picture you were shown."""

_IMG_TEXT_RULES = """

TASK - IMAGE + TEXT (composite; the TEXT wins every conflict):
Treat the image as the base layer of a scene and the text as an override layer laid on top. Deliver the
single scene that results once the text is applied over the image.

Resolve it concept by concept:
1. Decompose the image into concepts - subject identity, gender, age and count; pose and action; wardrobe;
   setting and background; props; style or medium; lighting; palette; framing.
2. For each concept, check whether the text speaks to it. If it does, the text's version REPLACES the
   image's and the image's is discarded. If the text is silent, KEEP the image's version verbatim.
   Anything the text names that has no counterpart in the image is ADDED.
3. Fuse the kept, replaced and added concepts into one seamless scene: a replacement must inherit the
   role its predecessor held - the same placement, pose, scale and lighting - unless the text overrides
   those too.

Study these for the decision logic, not the phrasing (each isolates a different kind of override):
- Base shows a golden retriever asleep, curled on a leather couch in warm afternoon light. Override says
  "a sleeping tabby cat." -> swap the animal only; the cat keeps the identical curl, the same couch, the
  same light and shot. (subject identity)
- Base shows a lone figure in a red jacket on a snowy ridge under a flat grey sky. Override says "on Mars."
  -> keep the solitary figure, the red jacket and the wide lonely framing; replace the snow and grey sky
  with rust-coloured rock and a dust-pink atmosphere. (environment)
- Base shows a crisp photographic close-up of a woman with short black hair, expression neutral. Override
  says "long red hair, laughing, as an oil painting." -> keep the same woman and the tight portrait crop;
  replace hair length and colour, replace the expression, and re-render the medium as painterly oils.
  (attribute + medium)

Once resolved, WRITE only the finished composite as a fresh prompt. Do not mention the image, the text,
the words keep/replace/add, or that any merging took place."""


def _build_system_local(model, unrestricted, mode):
    base = _BASE_RULES.format(model=model, guide=MODEL_GUIDES[model])
    base += (_NSFW_RULES if unrestricted else _SFW_RULES)
    if mode == "image":
        base += _IMG_ONLY_RULES.format(model=model)
    elif mode == "image_text":
        base += _IMG_TEXT_RULES.format(model=model)
    return base


def _engine_alive():
    p = _ENGINE["proc"]
    return p is not None and p.poll() is None


def _stop_engine():
    p = _ENGINE["proc"]
    if p is not None and p.poll() is None:
        try:
            p.send_signal(signal.SIGTERM)
        except Exception:
            pass
        for _ in range(10):
            if p.poll() is not None:
                break
            time.sleep(0.1)
        if p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
    _ENGINE["proc"] = None
    _ENGINE["model"] = None
    _ENGINE["mmproj"] = None


def _download_extract(url, bindir):
    """Download llama-bin.zip and extract to {bindir}/llama-libs. Returns the
    libdir on success. Preserves symlinked .so chains via `cp -P`."""
    bindir = (bindir or DEFAULT_BIN_DIR).strip()
    libdir = os.path.join(bindir, "llama-libs")
    server = os.path.join(libdir, "llama-server")
    if os.path.exists(server) and os.path.getsize(server) > 1024 * 1024:
        _console(f"binary already present: {server}")
        return libdir

    os.makedirs(bindir, exist_ok=True)
    zip_path = os.path.join(bindir, "llama-bin.zip")
    token = os.environ.get("HF_TOKEN", "").strip() if "huggingface.co" in url else ""
    _console(f"downloading {url}" + (" (auth)" if token else ""))
    if shutil.which("aria2c"):
        cmd = ["aria2c", "-c", "-x4", "-s4", "--quiet", "-d", bindir, "-o", "llama-bin.zip"]
        if token:
            cmd.append(f"--header=Authorization: Bearer {token}")
        rc = subprocess.run(cmd + [url]).returncode
        if rc != 0:
            hint = " (HTTP auth failed - set HF_TOKEN)" if rc == 24 else ""
            raise RuntimeError(f"aria2c failed rc={rc}{hint}")
    else:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with requests.get(url, stream=True, timeout=(10, 600), headers=headers) as r:
            r.raise_for_status()
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
    _console("extracting")
    tmp = os.path.join(bindir, "llama-libs_tmp")
    rc = subprocess.run(["bash", "-c",
        f"rm -rf '{libdir}' '{tmp}' && mkdir -p '{tmp}' && "
        f"unzip -o '{zip_path}' -d '{tmp}' >/dev/null 2>&1 && mkdir -p '{libdir}' && "
        f"find '{tmp}' -type f -exec cp -P {{}} '{libdir}/' \\; && "
        f"find '{tmp}' -type l -exec cp -P {{}} '{libdir}/' \\; && "
        f"rm -rf '{tmp}' && chmod +x '{libdir}'/*"
    ]).returncode
    if rc != 0 or not os.path.exists(server):
        raise RuntimeError("binary missing after extract")
    _ENGINE["libdir"] = libdir
    _console(f"binary ready: {server}")
    return libdir


def _drain(proc):
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        if line:
            _console(line)


def _launch(models_dir, model, flags, device, port, bindir, mmproj=""):
    with _ENGINE_LOCK:
        libdir = _ENGINE.get("libdir")
        if not libdir or not os.path.exists(os.path.join(libdir, "llama-server")):
            libdir = os.path.join((bindir or DEFAULT_BIN_DIR).strip(), "llama-libs")
        server = os.path.join(libdir, "llama-server")
        if not os.path.exists(server):
            _notify("launch", ok=False, error="binary not downloaded")
            return
        mdir = (models_dir or DEFAULT_MODELS_DIR).strip()
        path = os.path.join(mdir, model)
        if not os.path.exists(path):
            _notify("launch", ok=False, error=f"model not found: {path}")
            return
        mmproj = (mmproj or "").strip()
        mmproj_path = os.path.join(mdir, mmproj) if mmproj else ""
        if mmproj and not os.path.exists(mmproj_path):
            _notify("launch", ok=False, error=f"mmproj not found: {mmproj_path}")
            return
        _stop_engine()
        port = int(port or DEFAULT_PORT)
        # Everything but host/port/alias/mmproj comes straight from the flag box.
        args = _prep_flags(flags, device)
        cmd = [server, "-m", path, "--host", "127.0.0.1", "--port", str(port),
               "--alias", model] + args
        if mmproj_path:
            cmd += ["--mmproj", mmproj_path]
        env = {**os.environ, "LD_LIBRARY_PATH": libdir, **_device_env(device)}
        _console(f"launch: {' '.join(cmd)}  [dev={device}]")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, env=env)
        except Exception as e:
            _notify("launch", ok=False, error=str(e))
            return
        _ENGINE.update(proc=proc, model=model, port=port, libdir=libdir,
                       mmproj=(mmproj if mmproj_path else None))
    threading.Thread(target=_drain, args=(proc,), daemon=True).start()

    for _ in range(240):
        if proc.poll() is not None:
            _notify("launch", ok=False, error=f"exited rc={proc.returncode}")
            return
        try:
            r = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
            if r.status_code == 200:
                _notify("launch", ok=True, model=model, port=port)
                _console(f"{model} ready on :{port}")
                return
        except Exception:
            pass
        time.sleep(1)
    _notify("launch", ok=False, error="health timeout")


def _fingerprint(image_model, raw, unrestricted, seed, temperature, max_tokens, model, port, img_hash, mode):
    return (image_model, raw, bool(unrestricted), int(seed),
            round(float(temperature), 4), int(max_tokens), model, int(port), img_hash, mode)


class AzLlamaEnhancer(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzLlamaEnhancer",
            display_name="Prompt Enhancer (Local llama.cpp)",
            category="AZ_Nodes",
            description="Self-host a local llama-server and rewrite a loose prompt into a "
                        "structured, model-tailored prompt. STRING in, STRING out.",
            inputs=[
                io.String.Input("prompt", force_input=True, optional=True),
                io.Image.Input("image", optional=True),
                io.Combo.Input("image_model", options=_MODEL_OPTIONS, default=_MODEL_OPTIONS[0]),
                io.Combo.Input("device", options=_DEVICE_OPTIONS, default=_DEVICE_OPTIONS[0]),
                io.String.Input("binary_url", default=os.environ.get("LLAMA_BIN_URL", "")),
                io.String.Input("models_dir", default=DEFAULT_MODELS_DIR),
                io.String.Input("launch_flags", default=DEFAULT_FLAGS, multiline=True),
                io.String.Input("llm_model", default=""),
                io.String.Input("mmproj_file", default=""),
                io.Boolean.Input("unrestricted", default=True, label_on="Uncensored", label_off="SFW"),
                io.Int.Input("seed", default=0, min=0, max=0xffffffffffffffff,
                             control_after_generate=True),
                io.Float.Input("temperature", default=0.6, min=0.0, max=2.0, step=0.05, optional=True),
                io.Int.Input("max_tokens", default=256, min=16, max=4096, optional=True),
                io.Int.Input("port", default=DEFAULT_PORT, min=1024, max=65535, optional=True),
            ],
            outputs=[io.String.Output(display_name="text")],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, prompt=None, image=None, image_model=_MODEL_OPTIONS[0], device="default",
                binary_url="", models_dir=DEFAULT_MODELS_DIR, launch_flags=DEFAULT_FLAGS,
                llm_model="", mmproj_file="", unrestricted=True, seed=0,
                temperature=0.6, max_tokens=256, port=DEFAULT_PORT):
        node_id = cls.hidden.unique_id
        raw = (prompt or "").strip()
        has_image = image is not None
        if not raw and not has_image:
            raise ValueError("Local Enhancer: provide a text prompt, an image, or both.")
        model = (llm_model or "").strip()
        if not model:
            raise ValueError("Local Enhancer: no model selected. Download binary, pick a model, Launch.")
        if not _engine_alive():
            raise RuntimeError("Local Enhancer: server not running. Click Launch first.")
        if _ENGINE.get("model") != model:
            raise RuntimeError(f"Local Enhancer: loaded model is '{_ENGINE.get('model')}', "
                               f"selected '{model}'. Relaunch to switch.")
        if has_image and not _ENGINE.get("mmproj"):
            raise RuntimeError("Local Enhancer: image input needs a vision model. Pick an mmproj and relaunch.")
        port = int(_ENGINE.get("port") or port)

        img_uri = ""
        img_hash = ""
        if has_image:
            import hashlib
            img_uri = _encode_image(image)
            img_hash = hashlib.sha1(img_uri.encode()).hexdigest()

        mode = "image_text" if (has_image and raw) else ("image" if has_image else "text")

        fp = _fingerprint(image_model, raw, unrestricted, seed, temperature, max_tokens,
                          model, port, img_hash, mode)
        cached = _CACHE.get(node_id)
        if cached and cached[0] == fp:
            enhanced = cached[1]
            _notify("gen", status="start")
            _notify("gen", status="delta", text=enhanced)
            _notify("gen", status="done")
            return io.NodeOutput(enhanced)

        if mode == "text":
            system = _build_system(image_model, unrestricted)
            user_content = raw
        else:
            system = _build_system_local(image_model, unrestricted, mode)
            image_part = {"type": "image_url", "image_url": {"url": img_uri}}
            if mode == "image_text":
                user_content = [{"type": "text", "text": f"TEXT INSTRUCTION (takes precedence): {raw}"},
                                image_part]
            else:
                user_content = [{"type": "text", "text": "Write the prompt that reproduces this image."},
                                image_part]

        _STOP.pop(node_id, None)
        _notify("gen", status="start")
        full = ""
        stopped = False
        try:
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                "temperature": float(temperature),
                "max_tokens": int(max_tokens),
                "seed": int(seed),
                "stream": True,
                "cache_prompt": True,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            resp = requests.post(f"http://127.0.0.1:{port}/v1/chat/completions",
                                 headers={"Content-Type": "application/json"},
                                 data=json.dumps(body), stream=True, timeout=(10, 300))
            if resp.status_code != 200:
                raise RuntimeError(f"LLM HTTP {resp.status_code}: {resp.text[:300]}")
            for line in resp.iter_lines(decode_unicode=True):
                if _STOP.get(node_id):
                    stopped = True
                    break
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    delta = json.loads(chunk)["choices"][0]["delta"].get("content") or ""
                except Exception:
                    delta = ""
                if delta:
                    full += delta
                    _notify("gen", status="delta", text=delta)
            try:
                resp.close()
            except Exception:
                pass
            if stopped:
                _notify("gen", status="done")
                raise InterruptProcessingException()
            enhanced = _clean(full)
            if not enhanced:
                raise RuntimeError("LLM returned empty content.")
        except InterruptProcessingException:
            raise
        except Exception as e:
            _notify("gen", status="error", error=str(e))
            raise
        finally:
            _STOP.pop(node_id, None)

        _notify("gen", status="done")
        _CACHE[node_id] = (fp, enhanced)
        return io.NodeOutput(enhanced)


if PromptServer is not None and web is not None:
    @PromptServer.instance.routes.post("/az_llama/models")
    async def _az_l_models(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        models, mmprojs = _list_models(data.get("models_dir"))
        return web.json_response({"ok": True, "models": models, "mmprojs": mmprojs})

    @PromptServer.instance.routes.post("/az_llama/status")
    async def _az_l_status(request):
        return web.json_response({
            "running": _engine_alive(),
            "model": _ENGINE.get("model"),
            "mmproj": _ENGINE.get("mmproj"),
            "port": _ENGINE.get("port"),
            "log": list(_LOG_BUF),
        })

    @PromptServer.instance.routes.post("/az_llama/download")
    async def _az_l_download(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        url = (data.get("binary_url") or os.environ.get("LLAMA_BIN_URL", "")).strip()
        bindir = data.get("bin_dir") or DEFAULT_BIN_DIR
        if not url:
            return web.json_response({"ok": False, "error": "no binary URL (field or LLAMA_BIN_URL)"})

        def _run():
            try:
                _download_extract(url, bindir)
                _notify("download", ok=True)
            except Exception as e:
                _console(f"download failed: {e}")
                _notify("download", ok=False, error=str(e))

        threading.Thread(target=_run, daemon=True).start()
        return web.json_response({"ok": True, "status": "downloading"})

    @PromptServer.instance.routes.post("/az_llama/launch")
    async def _az_l_launch(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        model = (data.get("model") or "").strip()
        if not model:
            return web.json_response({"ok": False, "error": "no model selected"})

        def _run():
            _launch(data.get("models_dir"), model, data.get("launch_flags"),
                    data.get("device") or "default", data.get("port") or DEFAULT_PORT,
                    data.get("bin_dir"), data.get("mmproj") or "")

        threading.Thread(target=_run, daemon=True).start()
        return web.json_response({"ok": True, "status": "launching"})

    @PromptServer.instance.routes.post("/az_llama/stop")
    async def _az_l_stop(request):
        try:
            data = await request.json()
        except Exception:
            data = {}
        node_id = data.get("id")
        if node_id is not None:
            _STOP[str(node_id)] = True
        if data.get("kill_engine"):
            with _ENGINE_LOCK:
                _stop_engine()
            _console("engine stopped")
            _notify("stopped")
        return web.json_response({"ok": True})
