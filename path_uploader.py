# -*- coding: utf-8 -*-
"""
Path Uploader (UI-only) for ComfyUI
- POST /az/upload    : multipart/form-data { file, dest_dir } -> streams to disk
- GET  /az/listdir   : ?path=... -> lists sub-folders (and files) for dropdown
"""

import os
import re
import pathlib
import tempfile
import shutil
from aiohttp import web
from server import PromptServer

# ---------- helpers ----------
_SAN = re.compile(r'[\\:*?"<>|\x00-\x1F]')  # leave / and \ alone for paths

def _safe_expand(path_str: str) -> str:
    """Expand ~ and normalize to absolute path (Windows/Linux friendly)."""
    p = (path_str or "").strip()
    if not p:
        return os.path.abspath(os.getcwd())
    # Special Windows nicety: treat "C:" like "C:\"
    if len(p) == 2 and p[1] == ":":
        p = p + os.sep
    # Normalize slashes both ways; expand user
    p = os.path.expanduser(p)
    return os.path.abspath(p)

def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "")
    base = _SAN.sub("_", base)
    return base or "upload.bin"

def _listdir(path: str):
    """Return (folders, files) for a directory, sorted."""
    p = pathlib.Path(_safe_expand(path))
    if not p.exists():
        raise FileNotFoundError("Path does not exist")
    if not p.is_dir():
        raise NotADirectoryError("Not a directory")
    folders, files = [], []
    for entry in p.iterdir():
        try:
            if entry.is_dir():
                folders.append(entry.name)
            else:
                files.append(entry.name)
        except PermissionError:
            # skip entries we cannot stat
            continue
    folders.sort()
    files.sort()
    return folders, files

# ---------- routes ----------
@PromptServer.instance.routes.get("/az/listdir")
async def az_listdir(request: web.Request):
    """
    Query:
      ?path=<path>
    Returns:
      { ok: true, root: "<abs>", sep: "\\ or /",
        folders: [ {name, path}, ... ],
        files:   [ {name, path}, ... ] }
      or { ok: false, error: "..." }
    """
    qpath = request.query.get("path", "") or ""
    try:
        abs_root = _safe_expand(qpath)
        sep = os.sep
        folders, files = _listdir(abs_root)

        def make_entries(names):
            out = []
            for n in names:
                out.append({"name": n, "path": os.path.join(abs_root, n)})
            return out

        return web.json_response({
            "ok": True,
            "root": abs_root,
            "sep": sep,
            "folders": make_entries(folders),
            "files": make_entries(files),
        })
    except Exception as e:
        return web.json_response({
            "ok": False,
            "error": str(e),
            "root": _safe_expand(qpath),
            "folders": [],
            "files": [],
        }, status=200)

@PromptServer.instance.routes.post("/az/upload")
async def az_upload(request: web.Request):
    """
    multipart/form-data:
      - file: binary (required)
      - dest_dir: string (required)
    This implementation streams the incoming file part directly to a temporary file
    as soon as the part is encountered so the multipart reader's stream is fully consumed.
    """
    reader = await request.multipart()
    dest_dir = None

    temp_path = None
    filename = None
    total = 0
    file_seen = False

    # Iterate parts and handle them immediately. When we see the file part we stream it to a temp file.
    while True:
        field = await reader.next()
        if field is None:
            break
        # text part
        if field.name == "dest_dir":
            dest_dir_text = await field.text()
            dest_dir = dest_dir_text
            continue

        # file part
        if field.name == "file":
            # Only handle first file part
            if file_seen:
                # consume any additional file parts
                try:
                    while True:
                        chunk = await field.read_chunk()
                        if not chunk:
                            break
                except Exception:
                    pass
                continue

            file_seen = True
            filename = _safe_filename(getattr(field, "filename", "upload.bin"))

            # create temporary file and stream into it
            try:
                tmp = tempfile.NamedTemporaryFile(delete=False)
                temp_path = tmp.name
                with tmp:
                    # Use a larger chunk size to improve throughput (e.g., 256 KiB)
                    CHUNK_SIZE = 262144
                    while True:
                        chunk = await field.read_chunk(CHUNK_SIZE)
                        if not chunk:
                            break
                        tmp.write(chunk)
                        total += len(chunk)
            except Exception as e:
                try:
                    if temp_path and os.path.exists(temp_path):
                        os.unlink(temp_path)
                except Exception:
                    pass
                return web.json_response({"ok": False, "error": f"Write failed while receiving upload: {e}"}, status=500)
            continue

        # unknown part: consume to keep stream state consistent
        try:
            await field.read()
        except Exception:
            pass

    if not file_seen or not temp_path:
        return web.json_response({"ok": False, "error": "No file selected. Please choose a file."}, status=400)

    if not dest_dir or not dest_dir.strip():
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        return web.json_response({"ok": False, "error": "Destination folder is empty. Please enter a folder."}, status=400)

    abs_dest = _safe_expand(dest_dir)
    try:
        os.makedirs(abs_dest, exist_ok=True)
    except Exception as e:
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        return web.json_response({"ok": False, "error": f"Cannot create destination: {e}"}, status=400)

    if not os.path.isdir(abs_dest):
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        return web.json_response({"ok": False, "error": f"Not a directory: {abs_dest}"}, status=400)
    if not os.access(abs_dest, os.W_OK):
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        return web.json_response({"ok": False, "error": f"Destination not writable: {abs_dest}"}, status=400)

    save_path = os.path.join(abs_dest, filename)

    try:
        shutil.move(temp_path, save_path)
    except Exception as e:
        try:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
        except Exception:
            pass
        return web.json_response({"ok": False, "error": f"Failed to move uploaded file into place: {e}"}, status=500)

    return web.json_response({
        "ok": True,
        "filename": filename,
        "path": os.path.abspath(save_path),
        "bytes": total,
    })

# ---------- node stub ----------
class PathUploader:
    """
    UI-only node; widgets are in JS. No queue execution.
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ()
    FUNCTION = "noop"
    CATEGORY = "AZ_Nodes"

    def noop(self):
        return ()