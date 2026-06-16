# -*- coding: utf-8 -*-
"""
Path Uploader (UI-only) for ComfyUI
- POST /az/upload    : multipart/form-data { file, dest_dir } -> streams to disk
- GET  /az/listdir   : ?path=... -> lists sub-folders (and files) for dropdown
"""

import os
import re
import tempfile
import shutil
from aiohttp import web
from server import PromptServer

from . import az_fs  # registers GET /az/listdir (shared: MODEL_ZOO_PATH default + prefix filter)
from .az_fs import safe_expand as _safe_expand, default_root

# ---------- helpers ----------
_SAN = re.compile(r'[\\:*?"<>|\x00-\x1F]')  # leave / and \ alone for paths

def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "")
    base = _SAN.sub("_", base)
    return base or "upload.bin"

# ---------- routes ----------
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
    abs_dest = None  # set as soon as a valid dest_dir part is seen

    temp_path = None
    filename = None
    total = 0
    file_seen = False

    def _cleanup_temp():
        try:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
        except Exception:
            pass

    # Iterate parts and handle them immediately. When we see the file part we stream it to disk.
    # The JS appends dest_dir before file, so abs_dest is known by the time the file arrives and we
    # can stream straight into the destination filesystem -> finalize with an instant rename instead
    # of a cross-device copy (matters on RunPod /workspace and Kaggle, where /tmp is a different mount).
    while True:
        field = await reader.next()
        if field is None:
            break
        # text part
        if field.name == "dest_dir":
            dest_dir = await field.text()
            if dest_dir and dest_dir.strip():
                cand = _safe_expand(dest_dir)
                try:
                    os.makedirs(cand, exist_ok=True)
                    if os.path.isdir(cand) and os.access(cand, os.W_OK):
                        abs_dest = cand
                except Exception:
                    abs_dest = None
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

            # Stream into a temp file on the destination filesystem when known, else system temp.
            tmp_dir = abs_dest if abs_dest else None
            try:
                tmp = tempfile.NamedTemporaryFile(delete=False, dir=tmp_dir, prefix=".azupload-", suffix=".part")
                temp_path = tmp.name
                with tmp:
                    CHUNK_SIZE = 1024 * 1024  # 1 MiB
                    while True:
                        chunk = await field.read_chunk(CHUNK_SIZE)
                        if not chunk:
                            break
                        tmp.write(chunk)
                        total += len(chunk)
            except Exception as e:
                _cleanup_temp()
                return web.json_response({"ok": False, "error": f"Write failed while receiving upload: {e}"}, status=500)
            continue

        # unknown part: consume to keep stream state consistent
        try:
            await field.read()
        except Exception:
            pass

    if not file_seen or not temp_path:
        _cleanup_temp()
        return web.json_response({"ok": False, "error": "No file selected. Please choose a file."}, status=400)

    if not dest_dir or not dest_dir.strip():
        dest_dir = default_root()  # MODEL_ZOO_PATH (or cwd) when none supplied

    # If the dest couldn't be prepared during streaming, validate now with specific errors.
    if abs_dest is None:
        abs_dest = _safe_expand(dest_dir)
        try:
            os.makedirs(abs_dest, exist_ok=True)
        except Exception as e:
            _cleanup_temp()
            return web.json_response({"ok": False, "error": f"Cannot create destination: {e}"}, status=400)
        if not os.path.isdir(abs_dest):
            _cleanup_temp()
            return web.json_response({"ok": False, "error": f"Not a directory: {abs_dest}"}, status=400)
        if not os.access(abs_dest, os.W_OK):
            _cleanup_temp()
            return web.json_response({"ok": False, "error": f"Destination not writable: {abs_dest}"}, status=400)

    save_path = os.path.join(abs_dest, filename)

    try:
        # Same filesystem (temp was written into abs_dest) -> atomic rename, no copy.
        if os.path.dirname(os.path.abspath(temp_path)) == os.path.abspath(abs_dest):
            os.replace(temp_path, save_path)
        else:
            shutil.move(temp_path, save_path)
    except Exception as e:
        _cleanup_temp()
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
