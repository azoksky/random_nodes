# hf_list_aria2.py
# -*- coding: utf-8 -*-
"""
Same list-driven downloader as hf_list_downloader, but pushes each file through
aria2c (multi-connection) instead of a single requests stream. It reads the same
pastebin list, resolves the HF download URL from repo_id + file_in_repo, and
hands it to the shared aria2 RPC daemon with -c -x16 -s16 plus a few extra
speed tweaks (min-split-size, no preallocation, disk cache, gzip off).

Reuses the list parsing / fetch helpers and the aria2 RPC from the existing
modules so there is a single source of truth.
"""
import os
import time
import threading

from aiohttp import web
from server import PromptServer
from huggingface_hub import hf_hub_url

# Shared aria2 daemon + RPC (from the single-URL Aria2 Downloader node)
from .Downloader_helper import _aria2_rpc, _ensure_aria2_daemon

# Reuse list reading/fetch + paths from the streaming HF list node
from .hf_list_downloader import (
    MODELS,
    HF_TOKEN,
    LIST_URL_ENV,
    DEFAULT_CATEGORY,
    _read_list_file,
    _resolve_requested_path,
    _atomic_fetch,
)

# ---------- API: read (independent route, identical payload shape) ----------
@PromptServer.instance.routes.get("/hf_aria2/read")
async def hf_aria2_read(request):
    relish = (request.query.get("path") or "download_list.txt").strip()
    path = _resolve_requested_path(relish)

    if not path.is_file() and path.name == "download_list.txt":
        ok, err = _atomic_fetch(LIST_URL_ENV, path)
        if not ok:
            return web.json_response(
                {"ok": False, "error": err or f"Failed to fetch list from {LIST_URL_ENV}"},
                status=502,
            )

    try:
        raw_items, errors = _read_list_file(path)
        out_items = []
        for i, (repo, file_in_repo, local_subdir, category) in enumerate(raw_items):
            cat = (category or "").strip() or DEFAULT_CATEGORY
            out_items.append({
                "id": i + 1,
                "category": cat,
                "repo_id": repo,
                "file_in_repo": file_in_repo,
                "local_subdir": local_subdir,
            })
        return web.json_response({
            "ok": True, "file": str(path), "total": len(out_items),
            "items": out_items, "skipped": len(errors), "errors": errors,
        })
    except FileNotFoundError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=404)
    except Exception as e:
        return web.json_response(
            {"ok": False, "error": f"Failed to read list {path}: {type(e).__name__}: {e}"},
            status=500,
        )

# ---------- API: refresh ----------
@PromptServer.instance.routes.post("/hf_aria2/refresh")
async def hf_aria2_refresh(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    relish = (body.get("path") or "download_list.txt").strip()
    path = _resolve_requested_path(relish)
    ok, err = _atomic_fetch(LIST_URL_ENV, path)
    if not ok:
        return web.json_response({"ok": False, "error": err or f"Failed to fetch from {LIST_URL_ENV}"}, status=502)
    return web.json_response({"ok": True, "file": str(path), "url": LIST_URL_ENV})

# ---------- job bookkeeping (maps aria2 gid -> start time/destination) ----------
_jobs = {}
_jobs_lock = threading.Lock()


def _remember(gid, dst):
    with _jobs_lock:
        if len(_jobs) > 200:
            for k in list(_jobs)[:100]:
                _jobs.pop(k, None)
        _jobs[gid] = {"t0": time.time(), "dst": dst}


def _job(gid):
    with _jobs_lock:
        return dict(_jobs.get(gid) or {})


# ---------- API: start an aria2 download ----------
@PromptServer.instance.routes.post("/hf_aria2/download")
async def hf_aria2_download(request):
    body = await request.json()
    repo_id      = (body.get("repo_id")      or "").strip()
    file_in_repo = (body.get("file_in_repo") or "").strip()
    local_subdir = (body.get("local_subdir") or "").strip()

    if not repo_id or not file_in_repo or not local_subdir:
        return web.json_response({"ok": False, "error": "repo_id,file_in_repo,local_subdir required."}, status=400)

    try:
        _ensure_aria2_daemon()
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)

    target_dir = str((MODELS / local_subdir.strip("/\\")).resolve())
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as e:
        return web.json_response({"ok": False, "error": f"Cannot create {target_dir}: {e}"}, status=400)

    url = hf_hub_url(repo_id=repo_id, filename=file_in_repo)
    out_name = os.path.basename(file_in_repo)

    headers = ["Accept: */*", "User-Agent: random_nodes-hf-aria2/1.0"]
    if HF_TOKEN:
        headers.append(f"Authorization: Bearer {HF_TOKEN}")

    # -c -x16 -s16 + speed tweaks: 1M min-split so all 16 connections actually
    # engage on mid-size files, no preallocation for instant start, disk cache,
    # gzip off (HF serves already-compressed blobs), follow HF's CDN redirect.
    opts = {
        "continue": "true",
        "max-connection-per-server": "16",
        "split": "16",
        "min-split-size": "1M",
        "dir": target_dir,
        "out": out_name,
        "file-allocation": "none",
        "disk-cache": "64M",
        "remote-time": "true",
        "http-accept-gzip": "false",
        "auto-file-renaming": "false",
        "allow-overwrite": "true",
        "max-tries": "5",
        "retry-wait": "2",
        "header": headers,
    }

    try:
        res = _aria2_rpc("addUri", [[url], opts])
        gid = res.get("result")
        if not gid:
            return web.json_response({"ok": False, "error": "aria2c did not return a gid."}, status=500)
    except Exception as e:
        return web.json_response({"ok": False, "error": f"aria2c RPC error: {e}"}, status=500)

    _remember(gid, os.path.join(target_dir, out_name))
    return web.json_response({"ok": True, "gid": gid})


# ---------- API: poll progress (normalized to the JS-expected shape) ----------
@PromptServer.instance.routes.get("/hf_aria2/progress")
async def hf_aria2_progress(request):
    gid = request.query.get("gid", "")
    if not gid:
        return web.json_response({"ok": False, "error": "unknown gid"}, status=404)

    try:
        res = _aria2_rpc("tellStatus", [gid, [
            "status", "totalLength", "completedLength",
            "downloadSpeed", "errorMessage", "files", "dir",
        ]])
        st = res.get("result", {})
    except Exception as e:
        return web.json_response({"ok": False, "error": f"aria2c RPC error: {e}"}, status=500)

    a_status = st.get("status", "unknown")
    total = int(st.get("totalLength", "0") or "0")
    done = int(st.get("completedLength", "0") or "0")
    speed = int(st.get("downloadSpeed", "0") or "0")

    state = {
        "active": "running", "waiting": "running", "paused": "running",
        "complete": "done", "error": "error", "removed": "error",
    }.get(a_status, "running")

    meta = _job(gid)
    elapsed = (time.time() - meta["t0"]) if meta.get("t0") else 0.0

    dst = meta.get("dst") or ""
    files = st.get("files") or []
    if files and files[0].get("path"):
        dst = files[0]["path"]

    snap = {
        "ok": True, "state": state, "downloaded": done,
        "total": (total or (done if state == "done" else 0)),
        "speed": (0 if state in ("done", "error") else speed),
        "elapsed": elapsed, "dst": dst,
    }
    if state == "error":
        snap["error"] = st.get("errorMessage") or "aria2 download failed"
    return web.json_response(snap)


# ---------- API: cancel ----------
@PromptServer.instance.routes.post("/hf_aria2/cancel")
async def hf_aria2_cancel(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    gid = (body.get("gid") or "").strip()
    if gid:
        try:
            _aria2_rpc("remove", [gid])
        except Exception:
            pass
    return web.json_response({"ok": True})


from comfy_api.latest import io


class HFListAria2Downloader(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="hf_list_aria2",
            display_name="HuggingFace Batch Downloader (aria2)",
            category="AZ_Nodes",
            description="UI-only: queue and download a list of HF files via aria2 (widgets are in JS).",
            inputs=[],
            outputs=[],
        )

    @classmethod
    def execute(cls):
        return io.NodeOutput()
