# hf_list_downloader.py
# -*- coding: utf-8 -*-
import os
import time
import threading
import uuid
import shutil
from pathlib import Path
from typing import Tuple

from aiohttp import web
from server import PromptServer
from huggingface_hub import hf_hub_url
import requests
import urllib.request
from urllib.error import URLError, HTTPError

# ---------- Paths & env ----------
COMFY     = Path(os.environ.get("COMFYUI_PATH", "./ComfyUI")).resolve()
WORKSPACE = COMFY.parent.resolve()
MODELS    = Path(
    os.environ.get("MODEL_ZOO_PATH")
    or os.environ.get("COMFYUI_MODEL_PATH")
    or str(COMFY / "models")
).resolve()
HF_TOKEN  = os.environ.get("HF_TOKEN") or None

# Default list URL; can be overridden by env var DOWNLOAD_LIST
LIST_URL_DEFAULT = "https://pastebin.com/raw/WExYr6RB"
LIST_URL_ENV = (os.environ.get("MODELS_URL_LIST") or "").strip() or LIST_URL_DEFAULT

# Default category to use when a list line omits category
DEFAULT_CATEGORY = os.environ.get("HF_DEFAULT_CATEGORY", "Misc")

# ---------- Helpers ----------
def _clean_parts(line: str) -> Tuple[str, str, str, str | None] | None:
    """
    Parse a CSV line for:
      - new format: repo_id,file_in_repo,local_subdir,category
      - legacy:      repo_id,file_in_repo,local_subdir
    Returns a tuple (repo_id, file_in_repo, local_subdir, category_or_None) or None if invalid.
    Note: category may be an empty string in the 4-field format; treat as None here.
    """
    parts = [x.strip() for x in line.split(",", 3)]
    if len(parts) == 4:
        a, b, c, d = parts
        if not a or not b or not c:
            return None
        return (a, b, c, (d if d else None))
    elif len(parts) == 3:
        a, b, c = parts
        if not a or not b or not c:
            return None
        return (a, b, c, None)
    return None

def _read_list_file(p: Path):
    """
    Read and validate the list file.
    Returns (items, errors) where:
      - items: List[Tuple[repo_id, file_in_repo, local_subdir, category_or_None]]
      - errors: List[dict] with line, raw, reason (malformed or incomplete lines are skipped).
    """
    if not p.is_file():
        raise FileNotFoundError(f"No download list found at {p}")
    out = []
    errors = []
    with p.open("r", encoding="utf-8") as f:
        for idx, raw in enumerate(f, start=1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            tup = _clean_parts(s)
            if tup:
                out.append(tup)
            else:
                errors.append({
                    "line": idx,
                    "raw": s,
                    "reason": "Invalid or incomplete line (expected repo_id,file_in_repo,local_subdir[,category])."
                })
    return out, errors

def _atomic_fetch(url: str, dest: Path, timeout: int = 30, attempts: int = 3) -> tuple[bool, str | None]:
    """Download URL to dest atomically with small retry. Returns (ok, error_message)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_err = None
    # Pastebin (and other CDNs) 403 the default "Python-urllib" UA; send a browser one.
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "*/*",
    })
    for i in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            tmp.replace(dest)
            return True, None
        except HTTPError as he:
            last_err = f"HTTP {he.code} {he.reason} while fetching {url}"
        except URLError as ue:
            last_err = f"Network error fetching {url}: {ue.reason}"
        except TimeoutError:
            last_err = f"Timeout fetching {url} after {timeout}s"
        except Exception as e:
            last_err = f"Unexpected error fetching {url}: {type(e).__name__}: {e}"
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    return False, last_err

def _resolve_requested_path(relish: str) -> Path:
    p = (Path(relish).expanduser())
    if p.name == "download_list.txt" and ("/" not in relish and "\\" not in relish):
        return (WORKSPACE / "download_list.txt").resolve()
    return p.resolve()

# ---------- API: read ----------
@PromptServer.instance.routes.get("/hf_list/read")
async def hf_list_read(request):
    relish = (request.query.get("path") or "download_list.txt").strip()
    path = _resolve_requested_path(relish)

    # If local missing and it's the default name, try to fetch (env URL wins)
    if not path.is_file() and path.name == "download_list.txt":
        ok, err = _atomic_fetch(LIST_URL_ENV, path)
        if ok:
            print(f"missing list auto-fetched → {path}")
        else:
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

        payload = {
            "ok": True,
            "file": str(path),
            "total": len(out_items),
            "items": out_items,
            "skipped": len(errors),
            "errors": errors,  # informational; UI may ignore or summarize
        }
        return web.json_response(payload)
    except FileNotFoundError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=404)
    except PermissionError as e:
        return web.json_response({"ok": False, "error": f"Permission denied reading {path}: {e}"}, status=403)
    except UnicodeDecodeError as e:
        return web.json_response({"ok": False, "error": f"Invalid encoding in {path}: {e}"}, status=400)
    except Exception as e:
        return web.json_response({"ok": False, "error": f"Failed to read list {path}: {type(e).__name__}: {e}"}, status=500)

# ---------- API: refresh (force fetch from internet & overwrite local) ----------
@PromptServer.instance.routes.post("/hf_list/refresh")
async def hf_list_refresh(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    relish = (body.get("path") or "download_list.txt").strip()
    path = _resolve_requested_path(relish)

    url = LIST_URL_ENV
    ok, err = _atomic_fetch(url, path)
    if not ok:
        return web.json_response({"ok": False, "error": err or f"Failed to fetch from {url}"}, status=502)
    return web.json_response({"ok": True, "file": str(path), "url": url})

# ---------- background download with live progress ----------
# Downloads run in a daemon thread so they continue regardless of UI focus;
# the frontend polls /hf_list/progress for bytes/speed/elapsed. Streaming
# straight into the target dir (.part -> os.replace) keeps it atomic & fast.
_jobs = {}
_jobs_lock = threading.Lock()


def _new_job():
    gid = uuid.uuid4().hex
    with _jobs_lock:
        # prune old finished jobs so the dict doesn't grow forever
        if len(_jobs) > 200:
            stale = [k for k, v in _jobs.items() if v["state"] in ("done", "error")]
            for k in stale[:100]:
                _jobs.pop(k, None)
        _jobs[gid] = {
            "state": "starting", "downloaded": 0, "total": 0,
            "speed": 0.0, "elapsed": 0.0, "dst": None, "error": None,
            "cancel": False,
        }
    return gid


def _upd(gid, **kw):
    with _jobs_lock:
        if gid in _jobs:
            _jobs[gid].update(kw)


def _cancelled(gid):
    with _jobs_lock:
        return bool(_jobs.get(gid, {}).get("cancel"))


def _download_worker(gid, repo_id, file_in_repo, local_subdir):
    tmp = None
    try:
        target_dir = (MODELS / local_subdir.strip("/\\"))
        target_dir.mkdir(parents=True, exist_ok=True)
        dst = target_dir / Path(file_in_repo).name
        tmp = dst.with_suffix(dst.suffix + ".part")

        url = hf_hub_url(repo_id=repo_id, filename=file_in_repo)
        headers = {"User-Agent": "random_nodes-hf-list/1.0"}
        if HF_TOKEN:
            headers["Authorization"] = f"Bearer {HF_TOKEN}"

        _upd(gid, state="running")
        t0 = time.time()
        last_t, last_b, downloaded = t0, 0, 0

        with requests.get(url, headers=headers, stream=True,
                          timeout=(30, 120), allow_redirects=True) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length")
                        or r.headers.get("X-Linked-Size") or 0)
            _upd(gid, total=total)
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if _cancelled(gid):
                        raise RuntimeError("Cancelled by user")
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_t >= 0.25:
                        _upd(gid, downloaded=downloaded,
                             speed=(downloaded - last_b) / (now - last_t),
                             elapsed=now - t0)
                        last_t, last_b = now, downloaded

        os.replace(tmp, dst)
        _upd(gid, state="done", downloaded=downloaded,
             total=(total or downloaded), speed=0.0,
             elapsed=time.time() - t0, dst=str(dst))
    except Exception as e:
        try:
            if tmp is not None and tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        _upd(gid, state="error", speed=0.0,
             error=f"{type(e).__name__}: {e}")


# ---------- API: start a download (returns a job id immediately) ----------
@PromptServer.instance.routes.post("/hf_list/download")
async def hf_list_download(request):
    body = await request.json()
    repo_id      = (body.get("repo_id")      or "").strip()
    file_in_repo = (body.get("file_in_repo") or "").strip()
    local_subdir = (body.get("local_subdir") or "").strip()

    if not repo_id or not file_in_repo or not local_subdir:
        return web.json_response({"ok": False, "error": "repo_id,file_in_repo,local_subdir required."}, status=400)

    gid = _new_job()
    t = threading.Thread(
        target=_download_worker,
        args=(gid, repo_id, file_in_repo, local_subdir),
        daemon=True,
    )
    t.start()
    return web.json_response({"ok": True, "gid": gid})


# ---------- API: poll progress ----------
@PromptServer.instance.routes.get("/hf_list/progress")
async def hf_list_progress(request):
    gid = request.query.get("gid", "")
    with _jobs_lock:
        job = _jobs.get(gid)
        snap = dict(job) if job else None
    if not snap:
        return web.json_response({"ok": False, "error": "unknown gid"}, status=404)
    snap.pop("cancel", None)
    snap["ok"] = True
    return web.json_response(snap)


# ---------- API: cancel ----------
@PromptServer.instance.routes.post("/hf_list/cancel")
async def hf_list_cancel(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    gid = (body.get("gid") or "").strip()
    _upd(gid, cancel=True)
    return web.json_response({"ok": True})

class HFListDownloader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}  # UI-only

    RETURN_TYPES = ()
    FUNCTION = "noop"
    CATEGORY = "AZ_Nodes"

    def noop(self):
        return ()
