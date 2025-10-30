# -*- coding: utf-8 -*-
import os
import re
import json
import time
import shutil
import urllib.request
import urllib.parse
import urllib.error
from urllib.parse import urlparse, urlunparse
from uuid import uuid4
from subprocess import Popen, DEVNULL

from aiohttp import web
from server import PromptServer

# ========= Config =========
ARIA2_SECRET = os.environ.get("COMFY_ARIA2_SECRET", "comfyui_aria2_secret")
HF_TOKEN = os.environ.get("HF_TOKEN", "")
CIVIT_TOKEN = os.environ.get("CIVIT_TOKEN", "")
ARIA2_RPC_URL = os.environ.get("COMFY_ARIA2_RPC", "http://127.0.0.1:6800/jsonrpc")
ARIA2_BIN = shutil.which("aria2c") or "aria2c"
RPC_START_ARGS = [
    ARIA2_BIN,
    "--enable-rpc=true",
    "--rpc-listen-all=false",
    f"--rpc-secret={ARIA2_SECRET}",
    "--daemon=true",
    "--console-log-level=error",
    "--disable-ipv6=true",
]

# ========= RPC helper =========
def _aria2_rpc(method, params=None):
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": f"aria2.{method}",
        "params": [f"token:{ARIA2_SECRET}"] + (params or []),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ARIA2_RPC_URL, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))

def _ensure_aria2_daemon():
    try:
        _aria2_rpc("getVersion")
        return
    except Exception:
        pass
    if not shutil.which(ARIA2_BIN):
        raise RuntimeError("aria2c not found in PATH. Please install aria2c.")
    Popen(RPC_START_ARGS, stdout=DEVNULL, stderr=DEVNULL)
    t0 = time.time()
    while time.time() - t0 < 3.0:
        try:
            _aria2_rpc("getVersion")
            return
        except Exception:
            time.sleep(0.15)
    _aria2_rpc("getVersion")  # raise if still not up

# ========= Helpers =========
_SANITIZE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1F]')

def _sanitize_filename(name):
    return _SANITIZE_RE.sub("_", name).strip()

def _safe_expand(path_str):
    # Expand empty -> current working directory, expanduser and normalize
    return os.path.abspath(os.path.expanduser(path_str or ""))

def _parse_cd_filename(cd):
    if not cd:
        return None
    # RFC 5987: filename*=UTF-8''percent-encoded
    m = re.search(r"filename\*\s*=\s*[^'\";]+''([^;]+)", cd, flags=re.IGNORECASE)
    if m:
        try:
            decoded = urllib.parse.unquote(m.group(1))
            n = _sanitize_filename(os.path.basename(decoded))
            return n or None
        except Exception:
            pass
    # filename="name"
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, flags=re.IGNORECASE)
    if m:
        n = _sanitize_filename(os.path.basename(m.group(1)))
        return n or None
    # filename=name
    m = re.search(r'filename\s*=\s*([^;]+)', cd, flags=re.IGNORECASE)
    if m:
        n = _sanitize_filename(os.path.basename(m.group(1).strip()))
        return n or None
    return None

def _origin_from_url(u):
    try:
        p = urlparse(u)
        return urlunparse((p.scheme, p.netloc, "/", "", "", ""))
    except Exception:
        return ""

def _extract_query_filename(u):
    """Look for CDN hints like ?filename=, ?file=, ?name=, or response-content-disposition=."""
    try:
        q = urllib.parse.parse_qs(urlparse(u).query)
        for key in ("filename", "file", "name", "response-content-disposition"):
            if key in q and q[key]:
                candidate = q[key][0]
                if key == "response-content-disposition":
                    n = _parse_cd_filename(candidate)
                    if n:
                        return n
                n = _sanitize_filename(os.path.basename(candidate))
                if n:
                    return n
    except Exception:
        pass
    return None

def _append_or_replace_query_param(url, key, value):
    """Safely add/replace a query parameter even if URL already has ?/&."""
    try:
        p = urlparse(url)
        q = urllib.parse.parse_qs(p.query, keep_blank_values=True)
        q[key] = [value]
        newq = urllib.parse.urlencode(q, doseq=True)
        return urlunparse((p.scheme, p.netloc, p.path, p.params, newq, p.fragment))
    except Exception:
        return url

def _is_probably_login(final_url, headers):
    """Detect HTML login bounce (e.g., civitai.com/login) to avoid saving 'login' pages."""
    try:
        ct = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
        path = urlparse(final_url).path.lower()
        base = os.path.basename(path)
        if "text/html" in ct and ("login" in path or base in ("login", "signin", "log-in")):
            return True
    except Exception:
        pass
    return False

def _probe_url(url, extra_headers=None):
    """
    Probe URL with HEAD; on 400/401/403/405 fall back to GET with Range: bytes=0-0.
    Returns dict: {ok, status, final_url, headers, filename, confident, note}
    """
    extra_headers = extra_headers or {}
    base_headers = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "Mozilla/5.0",
    }
    hdr = dict(base_headers)
    hdr.update(extra_headers)

    opener = urllib.request.build_opener()

    def _make(req_method, add_range=False):
        h = dict(hdr)
        if add_range:
            h["Range"] = "bytes=0-0"
        return urllib.request.Request(url, method=req_method, headers=h)

    # Try HEAD
    try:
        resp = opener.open(_make("HEAD"), timeout=10)
        status = resp.getcode()
        final_url = resp.geturl()
        h = resp.headers or {}
        if _is_probably_login(final_url, h):
            return {"ok": False, "status": status, "final_url": final_url, "headers": h, "filename": None, "confident": False, "note": "login_html"}
    except urllib.error.HTTPError as e:
        # some hosts reject HEAD; try GET with Range
        if e.code in (400, 401, 403, 405):
            try:
                resp = opener.open(_make("GET", add_range=True), timeout=10)
                status = resp.getcode()
                final_url = resp.geturl()
                h = resp.headers or {}
                if _is_probably_login(final_url, h):
                    return {"ok": False, "status": status, "final_url": final_url, "headers": h, "filename": None, "confident": False, "note": "login_html"}
            except urllib.error.HTTPError as ee:
                return {"ok": False, "status": ee.code, "final_url": url, "headers": dict(ee.headers or {}), "filename": None, "confident": False, "note": "http_error"}
            except Exception:
                return {"ok": False, "status": 0, "final_url": url, "headers": {}, "filename": None, "confident": False, "note": "exception"}
        else:
            return {"ok": False, "status": e.code, "final_url": url, "headers": dict(e.headers or {}), "filename": None, "confident": False, "note": "http_error"}
    except Exception:
        return {"ok": False, "status": 0, "final_url": url, "headers": {}, "filename": None, "confident": False, "note": "exception"}

    # If here, we have resp
    cd = resp.headers.get("Content-Disposition") or resp.headers.get("content-disposition")
    n_from_cd = _parse_cd_filename(cd) if cd else None
    qname = _extract_query_filename(final_url)
    confident = bool(n_from_cd or (qname is not None))
    filename = n_from_cd or qname
    if not filename:
        try:
            filename = _sanitize_filename(os.path.basename(urlparse(final_url).path))
        except Exception:
            filename = None

    ok = 200 <= status < 300 or status == 206
    if _is_probably_login(final_url, resp.headers):
        ok = False

    return {
        "ok": bool(ok),
        "status": status,
        "final_url": final_url,
        "headers": dict(resp.headers or {}),
        "filename": filename,
        "confident": confident,
        "note": "",
    }

def _eta(total_len, done_len, speed):
    try:
        total = int(total_len); done = int(done_len); spd = max(int(speed), 1)
        remain = max(total - done, 0)
        return remain // spd
    except Exception:
        return None

def _negotiate_access(url, token):
    """
    Follow your rules:
      - If token is empty: use plain URL only.
      - If token present: try in order:
          1) Authorization: Bearer <token>
          2) URL ?token=<token> (safely merged)
          3) X-Api-Key: <token>
          4) Cookie: token=<token>
          5) Plain URL
    Return: {ok, url, headers, filename, confident, strategy, status, attempts}
    Each attempt entry: {name, url, status, ok, note}
    """
    attempts = []
    strategies = []

    token = (token or "").strip()

    if token:
        strategies.append(("auth_header", url, {"Authorization": "Bearer {}".format(token)}))
        strategies.append(("query_token", _append_or_replace_query_param(url, "token", token), {}))
        strategies.append(("x_api_key", url, {"X-Api-Key": token}))
        strategies.append(("cookie_token", url, {"Cookie": "token={}".format(token)}))
    # Always try plain at the end (works for public links, HF public files, etc.)
    strategies.append(("plain", url, {}))

    # If no token at all, skip straight to plain
    if not token:
        strategies = [("plain", url, {})]

    chosen = None
    for name, u, hdr in strategies:
        probe = _probe_url(u, hdr)
        attempts.append({
            "name": name,
            "url": u,
            "status": probe.get("status", 0),
            "ok": bool(probe.get("ok", False)),
            "note": probe.get("note", ""),
        })
        if probe.get("ok"):
            chosen = {
                "ok": True,
                "url": u,
                "headers": hdr,
                "filename": probe.get("filename"),
                "confident": probe.get("confident", False),
                "strategy": name,
                "status": probe.get("status", 0),
                "attempts": attempts,
            }
            break

    if chosen:
        return chosen

    # nothing worked
    last = attempts[-1] if attempts else {}
    return {
        "ok": False,
        "url": url,
        "headers": {},
        "filename": None,
        "confident": False,
        "strategy": "none",
        "status": last.get("status", 0),
        "attempts": attempts,
    }

# ========= API routes =========
@PromptServer.instance.routes.post("/aria2/start")
async def aria2_start(request):
    body = await request.json()
    url = (body.get("url") or "").strip()
    dest_dir = _safe_expand(body.get("dest_dir") or os.getcwd())
    # IMPORTANT: do NOT auto-fill token from env here (respect rule 2).
    token = (body.get("token") or "").strip()

    if not url:
        return web.json_response({"error": "URL is required."}, status=400)

    try:
        os.makedirs(dest_dir, exist_ok=True)
    except Exception as e:
        return web.json_response({"error": "Cannot access destination: {}".format(e)}, status=400)
    if not os.path.isdir(dest_dir) or not os.access(dest_dir, os.W_OK):
        return web.json_response({"error": "Destination not writable: {}".format(dest_dir)}, status=400)

    try:
        _ensure_aria2_daemon()
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

    # Negotiate according to rules
    nego = _negotiate_access(url, token)

    if not nego.get("ok"):
        return web.json_response({
            "error": "Could not access the URL with any strategy.",
            "attempts": nego.get("attempts", []),
        }, status=400)

    # Build aria2 options
    opts = {
        "continue": "true",
        "max-connection-per-server": "16",
        "split": "16",
        "dir": dest_dir,
        "auto-file-renaming": "true",
        "remote-time": "true",
        "content-disposition-default-utf8": "true",
        "header": [
            "Accept: */*",
            "Accept-Language: en-US,en;q=0.9",
            "User-Agent: Mozilla/5.0",
        ],
        "max-tries": "5",
    }

    # Apply negotiated headers
    for k, v in (nego.get("headers") or {}).items():
        opts["header"].append(f"{k}: {v}")

    # Referer
    origin = _origin_from_url(nego.get("url") or url)
    if origin:
        opts["referer"] = origin

    # Filename if confident
    if nego.get("confident") and nego.get("filename"):
        opts["out"] = nego["filename"]

    final_url = nego.get("url") or url

    try:
        res = _aria2_rpc("addUri", [[final_url], opts])
        gid = res.get("result")
        if not gid:
            return web.json_response({"error": "aria2c did not return a gid."}, status=500)
        return web.json_response({
            "gid": gid,
            "dest_dir": dest_dir,
            "guessed_out": opts.get("out", "") or "",
            "confident": bool(nego.get("confident")),
            "strategy": nego.get("strategy", "unknown"),
            "probe_status": nego.get("status", 0),
            "attempts": nego.get("attempts", []),
        })
    except Exception as e:
        return web.json_response({"error": f"aria2c RPC error: {e}"}, status=500)

@PromptServer.instance.routes.get("/aria2/status")
async def aria2_status(request):
    gid = request.query.get("gid", "")
    if not gid:
        return web.json_response({"error": "gid is required."}, status=400)
    try:
        res = _aria2_rpc("tellStatus", [gid, ["status", "totalLength", "completedLength", "downloadSpeed", "errorMessage", "files", "dir"]])
        st = res.get("result", {})
    except Exception as e:
        return web.json_response({"error": f"aria2c RPC error: {e}"}, status=500)
    status = st.get("status", "unknown")
    total = int(st.get("totalLength", "0") or "0")
    done = int(st.get("completedLength", "0") or "0")
    speed = int(st.get("downloadSpeed", "0") or "0")
    percent = (done / total * 100.0) if total > 0 else (100.0 if status == "complete" else 0.0)

    filepath = ""
    filename = ""
    files = st.get("files") or []
    if files:
        fp = files[0].get("path") or ""
        if fp:
            filepath = fp
            filename = os.path.basename(fp)
    if not filepath and st.get("dir") and filename:
        filepath = os.path.join(st["dir"], filename)

    out = {
        "status": status,
        "percent": round(percent, 2),
        "completedLength": done,
        "totalLength": total,
        "downloadSpeed": speed,
        "eta": _eta(total, done, speed),
        "filename": filename,
        "filepath": filepath,
    }
    if status == "error":
        out["error"] = st.get("errorMessage", "unknown error")
    return web.json_response(out)

@PromptServer.instance.routes.post("/aria2/stop")
async def aria2_stop(request):
    body = await request.json()
    gid = (body.get("gid") or "").strip()
    if not gid:
        return web.json_response({"error": "gid is required."}, status=400)
    try:
        _aria2_rpc("remove", [gid])
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": f"aria2c RPC error: {e}"}, status=500)

@PromptServer.instance.routes.get("/az/listdir")
async def az_listdir(request):
    """
    Query:
      ?path=<path>
    Returns:
      { ok: true, root: "<abs>", sep: "\\ or /",
        folders: [ {name, path}, ... ],
        files:   [ {name, path}, ... ] }
    """
    raw = request.query.get("path", "") or ""
    abs_root = _safe_expand(raw)

    # If provided path doesn't exist, try parent or fallback to cwd
    root = abs_root
    if not os.path.exists(root):
        root = os.path.dirname(root) or os.getcwd()
    if not os.path.isdir(root):
        root = os.path.dirname(root) or os.getcwd()

    folders = []
    files = []
    try:
        for name in sorted(os.listdir(root)):
            full = os.path.join(root, name)
            if os.path.isdir(full):
                folders.append({"name": name, "path": os.path.join(root, name)})
            else:
                files.append({"name": name, "path": os.path.join(root, name)})
    except Exception:
        pass

    return web.json_response({
        "ok": True,
        "root": root.replace("\\", "/"),
        "sep": os.sep,
        "folders": folders,
        "files": files
    })

@PromptServer.instance.routes.get("/tokens")
async def tokens(request):
    hf_suffix = HF_TOKEN[-4:] if HF_TOKEN and len(HF_TOKEN) >= 4 else HF_TOKEN or ""
    civit_suffix = CIVIT_TOKEN[-4:] if CIVIT_TOKEN and len(CIVIT_TOKEN) >= 4 else CIVIT_TOKEN or ""
    return web.json_response({"hf": hf_suffix, "civit": civit_suffix})

@PromptServer.instance.routes.get("/tokens/resolve")
async def tokens_resolve(request):
    # Used by the UI to auto-fill the token field from env based on URL domain
    url = (request.query.get("url") or "").lower()
    token = ""
    if ("huggingface.co" in url) or ("cdn-lfs.huggingface.co" in url):
        token = HF_TOKEN
    elif "civitai.com" in url:
        token = CIVIT_TOKEN
    return web.json_response({"token": token or ""})

# ========= UI-only node shell =========
class Aria2Downloader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ()
    FUNCTION = "noop"
    CATEGORY = "AZ_Nodes"

    def noop(self):
        return ()