# hf_list_downloader.py
# -*- coding: utf-8 -*-
import os
import json
import shutil
from pathlib import Path
from typing import List, Tuple

from aiohttp import web
from server import PromptServer
from huggingface_hub import hf_hub_download
import urllib.request
from urllib.error import URLError, HTTPError

# ---------- Paths & env ----------
COMFY     = Path(os.environ.get("COMFYUI_PATH", "./ComfyUI")).resolve()
WORKSPACE = COMFY.parent.resolve()
MODELS    = Path(os.environ.get("COMFYUI_MODEL_PATH", str(COMFY / "models"))).resolve()
HF_TOKEN  = os.environ.get("HF_TOKEN") or None

# Default list URL; can be overridden by env var DOWNLOAD_LIST
LIST_URL_DEFAULT = "https://raw.githubusercontent.com/azoksky/random_nodes/refs/heads/main/runpod/models_list.txt"
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
    for i in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r, open(tmp, "wb") as f:
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

# ---------- API: download one ----------
@PromptServer.instance.routes.post("/hf_list/download")
async def hf_list_download(request):
    body = await request.json()
    repo_id      = (body.get("repo_id")      or "").strip()
    file_in_repo = (body.get("file_in_repo") or "").strip()
    local_subdir = (body.get("local_subdir") or "").strip()

    if not repo_id or not file_in_repo or not local_subdir:
        return web.json_response({"ok": False, "error": "Invalid or incomplete line data (repo_id,file_in_repo,local_subdir required)."}, status=400)

    stage_dir = (WORKSPACE / "_hfstage")
    try:
        stage_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return web.json_response({"ok": False, "error": f"Cannot create staging dir {stage_dir}: {e}"}, status=500)

    target_dir = (MODELS / local_subdir.strip("/\\"))
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return web.json_response({"ok": False, "error": f"Cannot create target dir {target_dir}: {e}"}, status=400)

    try:
        downloaded = hf_hub_download(
            repo_id=repo_id,
            filename=file_in_repo,
            token=HF_TOKEN,
            local_dir=str(stage_dir),
        )
        src = Path(downloaded)
        dst = (target_dir / src.name)

        try:
            shutil.move(str(src), str(dst))
        except PermissionError as e:
            return web.json_response({"ok": False, "error": f"Permission denied moving to {dst}: {e}"}, status=403)
        except OSError as e:
            return web.json_response({"ok": False, "error": f"Filesystem error moving to {dst}: {e}"}, status=500)

        return web.json_response({
            "ok": True,
            "dst": str(dst),
            "repo_id": repo_id,
            "file_in_repo": file_in_repo,
            "local_subdir": local_subdir,
        })
    except HTTPError as he:
        return web.json_response({"ok": False, "error": f"HuggingFace HTTP {he.code} {he.reason} for {repo_id}/{file_in_repo}"}, status=502)
    except URLError as ue:
        return web.json_response({"ok": False, "error": f"Network error contacting HuggingFace: {ue.reason}"}, status=502)
    except Exception as e:
        return web.json_response({"ok": False, "error": f"Download failed for {repo_id}/{file_in_repo}: {type(e).__name__}: {e}"}, status=500)
    finally:
        try:
            if stage_dir.exists():
                shutil.rmtree(stage_dir, ignore_errors=True)
                print(f"🧹 Cleaned up staging folder: {stage_dir}")
        except Exception as e:
            print(f"⚠ Failed to remove staging folder {stage_dir}: {e}")

class HFListDownloader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}  # UI-only

    RETURN_TYPES = ()
    FUNCTION = "noop"
    CATEGORY = "AZ_Nodes"

    def noop(self):
        return ()
