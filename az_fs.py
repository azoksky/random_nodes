# -*- coding: utf-8 -*-
"""
Shared filesystem API for AZ_Nodes.

Registers GET /az/listdir exactly once (module import is inherently single, so
importing this from every node module is safe and avoids the duplicate-route
problem where the first-registered copy silently wins). The endpoint:

  - defaults the browse root to MODEL_ZOO_PATH (env), else the cwd
  - when the typed path is not an existing dir, treats the last path segment as
    a case-insensitive name prefix and lists the parent's matching entries, so
    typing ".../w" suggests folders starting with "w" without a trailing slash

default_root()/safe_expand() are reused by the nodes to default their
download/upload destinations to the same MODEL_ZOO_PATH root.
"""
import os
from aiohttp import web
from server import PromptServer


def safe_expand(path_str):
    """Expand ~ and normalize to an absolute path (Windows/Linux friendly)."""
    p = (path_str or "").strip()
    if not p:
        return os.path.abspath(os.getcwd())
    if len(p) == 2 and p[1] == ":":  # treat "C:" as "C:\"
        p = p + os.sep
    return os.path.abspath(os.path.expanduser(p))


def default_root():
    """Default browse/destination root: MODEL_ZOO_PATH env, else cwd."""
    env = os.environ.get("MODEL_ZOO_PATH")
    if env and env.strip():
        return safe_expand(env)
    return safe_expand(os.getcwd())


@PromptServer.instance.routes.get("/az/listdir")
async def az_listdir(request):
    """
    Query: ?path=<path>
      - empty        -> MODEL_ZOO_PATH (or cwd) children
      - existing dir -> its children
      - otherwise    -> list the parent, filtering children by the last path
                        segment as a case-insensitive name prefix
    Returns: { ok, root, sep, folders:[{name,path}], files:[{name,path}] }
    """
    raw = request.query.get("path", "") or ""
    abs_root = safe_expand(raw) if raw.strip() else default_root()

    prefix = ""
    root = abs_root
    if not os.path.isdir(root):
        prefix = os.path.basename(root)
        root = os.path.dirname(root) or os.getcwd()
        if not os.path.isdir(root):
            root = default_root()
            prefix = ""

    pref_lc = prefix.lower()
    folders, files = [], []
    try:
        for name in sorted(os.listdir(root)):
            if pref_lc and not name.lower().startswith(pref_lc):
                continue
            full = os.path.join(root, name)
            entry = {"name": name, "path": full}
            (folders if os.path.isdir(full) else files).append(entry)
    except Exception:
        pass

    return web.json_response({
        "ok": True,
        "root": root.replace("\\", "/"),
        "sep": os.sep,
        "folders": folders,
        "files": files,
    })
