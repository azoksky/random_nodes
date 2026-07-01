# -*- coding: utf-8 -*-
import os
import time
import threading
from urllib.parse import quote
from uuid import uuid4
from typing import Dict, Any, Optional

import requests
from aiohttp import web
from server import PromptServer

from . import az_fs  # registers GET /az/listdir (shared: MODEL_ZOO_PATH default + prefix filter)
from .az_fs import default_root

# Env token
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# Minimal in-memory job store
_downloads: Dict[str, Dict[str, Any]] = {}  # gid -> {state, msg, filepath, thread}


def _set(gid: str, **kw):
    _downloads.setdefault(gid, {})
    _downloads[gid].update(kw)


def _get(gid: str, key: str, default=None):
    return _downloads.get(gid, {}).get(key, default)


def _fmt_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("{:.0f} {}" if unit == "B" else "{:.1f} {}").format(n, unit)
        n /= 1024


def _worker(gid: str, repo_id: str, filename: str, dest_dir: str, token: Optional[str], revision: str):
    # Stream the file ourselves so we can surface byte progress / speed / ETA and honour
    # Stop. hf_hub_download hides all of that behind a blocking call (terminal tqdm only).
    try:
        _set(gid, state="running", msg="Resolving...", filepath=None,
             total=0, downloaded=0, speed=0.0, eta=None)

        rel = filename.replace("\\", "/").lstrip("/")
        url = "https://huggingface.co/{}/resolve/{}/{}".format(repo_id, revision, quote(rel))
        headers = {"User-Agent": "az-hf-downloader"}
        if token:
            headers["Authorization"] = "Bearer {}".format(token)

        dest_path = os.path.join(dest_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(dest_path) or dest_dir, exist_ok=True)
        part = dest_path + ".part"

        resume_pos = os.path.getsize(part) if os.path.exists(part) else 0
        if resume_pos:
            headers["Range"] = "bytes={}-".format(resume_pos)

        with requests.get(url, stream=True, headers=headers, allow_redirects=True, timeout=30) as r:
            if r.status_code == 416:  # part already covers the whole file
                os.replace(part, dest_path)
                sz = os.path.getsize(dest_path)
                _set(gid, state="done", msg="File download complete.", filepath=dest_path,
                     total=sz, downloaded=sz, speed=0.0, eta=0)
                return
            r.raise_for_status()

            resumed = r.status_code == 206
            base = resume_pos if resumed else 0
            total = int(r.headers.get("Content-Length", 0) or 0) + base
            _set(gid, total=total, downloaded=base)

            downloaded = base
            t_prev, b_prev, speed = time.time(), base, 0.0
            with open(part, "ab" if resumed else "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if _get(gid, "stop"):
                        _set(gid, state="stopped", msg="Stopped by user.")
                        return
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - t_prev >= 0.4:
                        inst = (downloaded - b_prev) / (now - t_prev)
                        speed = inst if speed == 0 else speed * 0.7 + inst * 0.3
                        eta = (total - downloaded) / speed if (speed > 0 and total) else None
                        pct = "{:.1f}% · ".format(100.0 * downloaded / total) if total else ""
                        _set(gid, downloaded=downloaded, speed=speed, eta=eta,
                             msg="{}{} / {} · {}/s".format(
                                 pct, _fmt_bytes(downloaded), _fmt_bytes(total) if total else "?",
                                 _fmt_bytes(speed)))
                        t_prev, b_prev = now, downloaded

        os.replace(part, dest_path)
        sz = os.path.getsize(dest_path)
        _set(gid, state="done", msg="File download complete.", filepath=dest_path,
             total=sz, downloaded=sz, speed=0.0, eta=0)
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

        # require repo_id and filename, but allow dest_dir to be empty and
        # fall back to environment-based defaults or cwd
        if not repo_id or not filename:
            return web.json_response({"ok": False, "error": "repo_id and filename are required"}, status=400)

        # Default to MODEL_ZOO_PATH (env), else cwd, when dest_dir not provided
        if not dest_dir:
            dest_dir = default_root()
        # normalize path
        dest_dir = os.path.abspath(os.path.expanduser(dest_dir))

        try:
            os.makedirs(dest_dir, exist_ok=True)
        except Exception as e:
            return web.json_response({"ok": False, "error": "Cannot create destination: {}".format(e)}, status=400)

        # If no token provided, default to HF token from env
        if not token:
            token = HF_TOKEN

        revision = (data.get("revision") or "main").strip() or "main"

        gid = data.get("gid") or uuid4().hex
        _downloads[gid] = {
            "state": "starting",
            "msg": "Starting...",
            "filepath": None,
            "thread": None,
            "stop": False,
            "total": 0,
            "downloaded": 0,
            "speed": 0.0,
            "eta": None,
        }

        t = threading.Thread(target=_worker, args=(gid, repo_id, filename, dest_dir, token, revision), daemon=True)
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
        "total": info.get("total", 0),
        "downloaded": info.get("downloaded", 0),
        "speed": info.get("speed", 0.0),
        "eta": info.get("eta"),
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
            _set(gid, stop=True, msg="Stop requested by user.")
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
from comfy_api.latest import io


class hf_hub_downloader(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="hf_hub_downloader",
            display_name="HuggingFace File Downloader",
            category="AZ_Nodes",
            description="UI-only: download a single file from a HF repo (widgets are in JS).",
            inputs=[],
            outputs=[],
        )

    @classmethod
    def execute(cls):
        return io.NodeOutput()
