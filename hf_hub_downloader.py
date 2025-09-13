# -*- coding: utf-8 -*-
import os
import threading
from uuid import uuid4
from typing import Dict, Any, Optional

from aiohttp import web
from server import PromptServer
from huggingface_hub import hf_hub_download

# Env token
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# Minimal in-memory job store
_downloads: Dict[str, Dict[str, Any]] = {}  # gid -> {state, msg, filepath, thread}


def _set(gid: str, **kw):
    _downloads.setdefault(gid, {})
    _downloads[gid].update(kw)


def _get(gid: str, key: str, default=None):
    return _downloads.get(gid, {}).get(key, default)


def _worker(gid: str, repo_id: str, filename: str, dest_dir: str, token: Optional[str]):
    try:
        _set(gid, state="running", msg="Download started...", filepath=None)
        local_path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=dest_dir,
            token=(token or None),
        )
        _set(gid, state="done", msg="File download complete.", filepath=local_path)
    except Exception as e:
        _set(gid, state="error", msg="{}: {}".format(type(e).__name__, e))


# ============ routes (use PromptServer routes so they appear under /api/*) ============
@PromptServer.instance.routes.post("/hf/start")
async def start_download(request: web.Request):
    try:
        data = await request.json()
        repo_id = (data.get("repo_id") or "").strip()
        filename = (data.get("filename") or "").strip()
        dest_dir = (data.get("dest_dir") or "").strip()
        token = (data.get("token_input") or "").strip()

        if not repo_id or not filename or not dest_dir:
            return web.json_response({"ok": False, "error": "repo_id, filename, dest_dir are required"}, status=400)

        try:
            os.makedirs(dest_dir, exist_ok=True)
        except Exception as e:
            return web.json_response({"ok": False, "error": "Cannot create destination: {}".format(e)}, status=400)

        # If no token provided, default to HF token from env
        if not token:
            token = HF_TOKEN

        gid = data.get("gid") or uuid4().hex
        _downloads[gid] = {
            "state": "starting",
            "msg": "Starting...",
            "filepath": None,
            "thread": None,
        }

        t = threading.Thread(target=_worker, args=(gid, repo_id, filename, dest_dir, token), daemon=True)
        _downloads[gid]["thread"] = t
        t.start()

        return web.json_response({"ok": True, "gid": gid, "state": "running", "msg": "Download started..."})
    except Exception as e:
        return web.json_response({"ok": False, "error": "{}: {}".format(type(e).__name__, e)}, status=500)


@PromptServer.instance.routes.get("/hf/status")
async def status_download(request: web.Request):
    gid = request.query.get("gid", "")
    if gid not in _downloads:
        return web.json_response({"ok": False, "error": "unknown gid"}, status=404)
    info = _downloads[gid]
    return web.json_response({
        "ok": True,
        "gid": gid,
        "state": info.get("state", "unknown"),
        "msg": info.get("msg", ""),
        "filepath": info.get("filepath"),
    })


@PromptServer.instance.routes.post("/hf/stop")
async def stop_download(request: web.Request):
    try:
        data = await request.json()
        gid = (data.get("gid") or "").strip()
        if gid not in _downloads:
            return web.json_response({"ok": False, "error": "unknown gid"}, status=404)
        info = _downloads[gid]
        t = info.get("thread")
        if t and t.is_alive():
            _set(gid, state="stopped", msg="Stop requested by user.")
        else:
            _set(gid, state="stopped", msg="Already finished.")
        return web.json_response({"ok": True, "gid": gid, "state": _get(gid, "state"), "msg": _get(gid, "msg")})
    except Exception as e:
        return web.json_response({"ok": False, "error": "{}: {}".format(type(e).__name__, e)}, status=500)


@PromptServer.instance.routes.get("/hf/token")
async def token_full(request: web.Request):
    # Full token for auto-fill
    return web.json_response({"token": HF_TOKEN or ""})


@PromptServer.instance.routes.get("/hf/tokens")
async def token_suffix(request: web.Request):
    # Last-4 hint
    tok = HF_TOKEN or ""
    suffix = tok[-4:] if len(tok) >= 4 else tok
    return web.json_response({"hf": suffix})


# ============ UI node shell (no-op compute) ============
class hf_hub_downloader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = []
    FUNCTION = "noop"
    CATEGORY = "AZ_Nodes"

    def noop(self):
        return ()
