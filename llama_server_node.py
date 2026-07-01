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

from .prompt_enhancer_node import _build_system, _clean, _MODEL_OPTIONS

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
DEFAULT_FLAGS = "-ngl 99 -c 8192 -fa on --jinja -np 1"

# Managed singleton engine (one llama-server for this process).
_ENGINE = {"proc": None, "model": None, "port": None, "libdir": None}
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


def _list_gguf(models_dir):
    d = (models_dir or DEFAULT_MODELS_DIR).strip()
    try:
        names = [f for f in os.listdir(d) if f.lower().endswith(".gguf")]
    except Exception:
        return []
    return sorted(names)


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


def _launch(models_dir, model, flags, device, port, bindir):
    with _ENGINE_LOCK:
        libdir = _ENGINE.get("libdir")
        if not libdir or not os.path.exists(os.path.join(libdir, "llama-server")):
            libdir = os.path.join((bindir or DEFAULT_BIN_DIR).strip(), "llama-libs")
        server = os.path.join(libdir, "llama-server")
        if not os.path.exists(server):
            _notify("launch", ok=False, error="binary not downloaded")
            return
        path = os.path.join((models_dir or DEFAULT_MODELS_DIR).strip(), model)
        if not os.path.exists(path):
            _notify("launch", ok=False, error=f"model not found: {path}")
            return
        _stop_engine()
        port = int(port or DEFAULT_PORT)
        cmd = [server, "-m", path, "--host", "127.0.0.1", "--port", str(port),
               "--alias", model] + _prep_flags(flags, device)
        env = {**os.environ, "LD_LIBRARY_PATH": libdir, **_device_env(device)}
        _console(f"launch: {' '.join(cmd)}  [dev={device}]")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, env=env)
        except Exception as e:
            _notify("launch", ok=False, error=str(e))
            return
        _ENGINE.update(proc=proc, model=model, port=port, libdir=libdir)
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


def _fingerprint(image_model, raw, unrestricted, seed, temperature, max_tokens, model, port):
    return (image_model, raw, bool(unrestricted), int(seed),
            round(float(temperature), 4), int(max_tokens), model, int(port))


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
                io.String.Input("prompt", force_input=True),
                io.Combo.Input("image_model", options=_MODEL_OPTIONS, default=_MODEL_OPTIONS[0]),
                io.Combo.Input("device", options=_DEVICE_OPTIONS, default=_DEVICE_OPTIONS[0]),
                io.String.Input("binary_url", default=os.environ.get("LLAMA_BIN_URL", "")),
                io.String.Input("models_dir", default=DEFAULT_MODELS_DIR),
                io.String.Input("launch_flags", default=DEFAULT_FLAGS, multiline=True),
                io.String.Input("llm_model", default=""),
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
    def execute(cls, prompt, image_model, device, binary_url, models_dir, launch_flags,
                llm_model, unrestricted, seed, temperature=0.6, max_tokens=256, port=DEFAULT_PORT):
        node_id = cls.hidden.unique_id
        raw = (prompt or "").strip()
        if not raw:
            raise ValueError("Local Enhancer: empty prompt.")
        model = (llm_model or "").strip()
        if not model:
            raise ValueError("Local Enhancer: no model selected. Download binary, pick a model, Launch.")
        if not _engine_alive():
            raise RuntimeError("Local Enhancer: server not running. Click Launch first.")
        if _ENGINE.get("model") != model:
            raise RuntimeError(f"Local Enhancer: loaded model is '{_ENGINE.get('model')}', "
                               f"selected '{model}'. Relaunch to switch.")
        port = int(_ENGINE.get("port") or port)

        fp = _fingerprint(image_model, raw, unrestricted, seed, temperature, max_tokens, model, port)
        cached = _CACHE.get(node_id)
        if cached and cached[0] == fp:
            enhanced = cached[1]
            _notify("gen", status="start")
            _notify("gen", status="delta", text=enhanced)
            _notify("gen", status="done")
            return io.NodeOutput(enhanced)

        _STOP.pop(node_id, None)
        _notify("gen", status="start")
        full = ""
        stopped = False
        try:
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _build_system(image_model, unrestricted)},
                    {"role": "user", "content": raw},
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
        names = _list_gguf(data.get("models_dir"))
        return web.json_response({"ok": True, "models": names})

    @PromptServer.instance.routes.post("/az_llama/status")
    async def _az_l_status(request):
        return web.json_response({
            "running": _engine_alive(),
            "model": _ENGINE.get("model"),
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
                    data.get("bin_dir"))

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
        return web.json_response({"ok": True})
