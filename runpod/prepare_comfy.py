#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import threading
from pathlib import Path
from typing import List
import shutil
import urllib.request
from huggingface_hub import hf_hub_download

# ----------------------------
# Environment & paths
# ----------------------------

def _req_env(name: str) -> Path:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return Path(val).expanduser().resolve()   # ensure absolute path

def _env_flag(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "y", "on")

COMFY   = _req_env("COMFYUI_PATH")
MODELS  = _req_env("COMFYUI_MODEL_PATH")
workspace = COMFY.parent
CUSTOM  = COMFY / "custom_nodes"
USER    = COMFY / "user" / "default"

# URLs (overridable via env)
CUSTOM_NODE_URL_LIST_DEFAULT = "https://raw.githubusercontent.com/azoksky/random_nodes/refs/heads/main/runpod/custom_node_list.txt"
CUSTOM_NODE_URL_LIST = os.environ.get("CUSTOM_NODE_URL_LIST", CUSTOM_NODE_URL_LIST_DEFAULT).strip()

MODELS_URL_LIST_DEFAULT = "https://raw.githubusercontent.com/azoksky/random_nodes/refs/heads/main/runpod/models_list.txt"
MODELS_URL_LIST = (os.environ.get("MODELS_URL_LIST") or "").strip() or MODELS_URL_LIST_DEFAULT

SETTINGS_URL_LIST_DEFAULT = "https://raw.githubusercontent.com/azoksky/random_nodes/refs/heads/main/runpod/settings_list.txt"
SETTINGS_URL_LIST = (os.environ.get("SETTINGS_URL_LIST") or "").strip() or SETTINGS_URL_LIST_DEFAULT

# DOWNLOAD_MODELS is now a string: "cat1,cat2[;pos1,pos2][:neg1,neg2]"
DOWNLOAD_MODELS_SPEC = (os.environ.get("DOWNLOAD_MODELS") or "").strip()

# Default/fallback category name if a list line misses category
DEFAULT_CATEGORY = "Misc"

# ----------------------------
# Thread concurrency limit
# ----------------------------
def _env_int(name: str, default: int) -> int:
    """Read positive int from env, or return default on missing/invalid."""
    val = os.environ.get(name)
    if val is None or not str(val).strip():
        return default
    try:
        n = int(str(val).strip())
        return n if n > 0 else default
    except Exception:
        return default

# MAX_CONCURRENT is taken from env MAX_INSTALL_CONCURRENCY (default=2)
MAX_CONCURRENT = _env_int("MAX_INSTALL_CONCURRENCY", 2)
sem = threading.Semaphore(MAX_CONCURRENT)

def threaded(fn):
    """Decorator to enforce concurrency limit in threads."""
    def wrapper(*args, **kwargs):
        with sem:
            return fn(*args, **kwargs)
    return wrapper

# ----------
# Utilities
# ----------

def run(cmd: List[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    pretty = " ".join(cmd)
    print(f"→ {pretty}")
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check)

@threaded
def install_missing_from_env(var: str = "MISSING_PACKAGES") -> None:
    """Install packages from env var: MISSING_PACKAGES=pack1,pack2,..."""
    raw = os.environ.get(var, "")
    if not raw.strip():
        print("⏩ no missing packages specified")
        return
    for pkg in [p.strip() for p in raw.split(",") if p.strip()]:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--no-cache-dir", "-q", pkg])
            print(f"✓ installed: {pkg}")
        except Exception as e:
            print(f"✗ error installing {pkg}: {e}")

def parse_bool(val: str) -> bool:
    """Parse string to bool (yes/true/1)."""
    return str(val).strip().lower() in ("1", "true", "yes")

# ---------------------------
# Installer runner
# ---------------------------

@threaded
def run_installer(ipy: Path) -> None:
    """Run install.py in its directory (blocking)."""
    try:
        print(f"↗ running installer: {ipy}")
        proc = subprocess.Popen([sys.executable, "-B", str(ipy)], cwd=ipy.parent)
        proc.wait()
        if proc.returncode == 0:
            print(f"✓ installer finished: {ipy}")
        else:
            print(f"⚠ installer failed ({proc.returncode}): {ipy}")
    except Exception as e:
        print(f"⚠ installer error for {ipy}: {e}")

# ---------------------------
# Clone with install support
# ---------------------------

def clone(repo: str, dest: Path, threads: list[threading.Thread], name: str | None = None, run_install: bool = False, attempts: int = 2) -> None:
    """Clone repo, and if install.py exists, run in background thread depending on run_install flag."""
    if dest.exists():
        if (dest / ".git").exists():
            print(f"✓ already present: {dest}")
            return
        else:
            print(f"⚠ {dest} exists but is not a valid git repo. Removing...")
            shutil.rmtree(dest, ignore_errors=True)

    dest.parent.mkdir(parents=True, exist_ok=True)

    for i in range(1, attempts + 1):
        try:
            run(["git", "clone", "--depth=1", "--single-branch", "--no-tags", repo, str(dest)])
            print(f"✓ cloned: {repo} → {dest}")

            ipy = dest / "install.py"
            if ipy.is_file():
                if run_install:
                    t = threading.Thread(target=run_installer, args=(ipy,), daemon=False)
                    t.start()
                    threads.append(t)
                    print(f"↗ installer scheduled for node: {name or dest.name}")
                else:
                    print(f"⏩ skipping installer for node: {name or dest.name}")
            else:
                print(f"⏩ no install.py found for {name or dest.name}")

            return
        except subprocess.CalledProcessError as e:
            print(f"⚠ clone attempt {i}/{attempts} failed for {repo}: {e}")
            if i == attempts:
                raise

# ---------------------------
# Fetch node list
# ---------------------------

def fetch_node_list() -> list[tuple[str, bool]]:
    """Download the custom_node_list.txt and return list of (repo, run_install)."""
    try:
        req = urllib.request.Request(CUSTOM_NODE_URL_LIST, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=30) as r:
            content = r.read().decode("utf-8")
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not lines:
            print(f"⚠ Node list from {CUSTOM_NODE_URL_LIST} is empty, skipping custom nodes.")
            return []
        print(f"✓ fetched {len(lines)} entries from {CUSTOM_NODE_URL_LIST}")

        repos: list[tuple[str, bool]] = []
        for idx, line in enumerate(lines, 1):
            parts = [x.strip() for x in line.split(",", 1)]
            repo = parts[0]
            run_install = parse_bool(parts[1]) if len(parts) == 2 else False
            repos.append((repo, run_install))
        return repos
    except Exception as e:
        print(f"⚠ Failed to fetch node list from {CUSTOM_NODE_URL_LIST}: {e}")
        return []

# ---------------------------
# Settings/config fetch
# ---------------------------

@threaded
def apply_settings() -> None:
    """Fetch and apply settings/config files defined in SETTINGS_URL_LIST."""
    try:
        req = urllib.request.Request(SETTINGS_URL_LIST, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=30) as r:
            content = r.read().decode("utf-8")

        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not lines:
            print(f"⚠ Settings list from {SETTINGS_URL_LIST} is empty, skipping settings.")
            return
        print(f"✓ fetched {len(lines)} settings entries from {SETTINGS_URL_LIST}")
    except Exception as e:
        print(f"⚠ Failed to fetch settings list from {SETTINGS_URL_LIST}: {e}")
        return

    for idx, line in enumerate(lines, 1):
        try:
            parts = [x.strip() for x in line.split(",", 1)]
            if len(parts) != 2:
                print(f"⚠ Skipping malformed line {idx}: {line}")
                continue

            url, rel_path = parts
            dest = (COMFY / rel_path).resolve()

            if not str(dest).startswith(str(COMFY.resolve())):
                print(f"✗ Invalid path outside COMFY detected, skipping: {dest}")
                continue

            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")

            success = False
            for attempt in range(1, 4):  # retries
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
                    with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
                        shutil.copyfileobj(r, f)
                    tmp.replace(dest)
                    print(f"✓ downloaded: {dest} ← {url}")
                    success = True
                    break
                except Exception as e:
                    print(f"⚠ attempt {attempt}/3 failed for {url}: {e}")
                    tmp.unlink(missing_ok=True)

            if not success:
                print(f"✗ giving up on {url}")

        except Exception as e:
            print(f"⚠ Error processing line {idx}: {line} → {e}")

# ---------------------------
# Model downloads (with category and positive/negative-token filtering)
# ---------------------------

def _parse_model_line(line: str) -> tuple[str, str, str, str] | None:
   
    """
    Expect: repo_id,file_in_repo,local_subdir[,category]
    Returns (repo_id, file_in_repo, local_subdir, category) or None if malformed.
    If category missing/empty, uses DEFAULT_CATEGORY.
    """
    parts = [x.strip() for x in line.split(",", 3)]
    if len(parts) < 3:
        return None
    if len(parts) == 3:
        repo_id, file_in_repo, local_subdir = parts
        category = DEFAULT_CATEGORY
    else:
        repo_id, file_in_repo, local_subdir, category = parts
        category = category or DEFAULT_CATEGORY
    if not repo_id or not file_in_repo or not local_subdir:
        return None
    return repo_id, file_in_repo, local_subdir, category

def _parse_download_spec(spec: str, available_categories_lower: set[str]) -> tuple[set[str], list[str], list[str], str]:
    """
    Parse DOWNLOAD_MODELS spec into:
      - include_categories_lower: set of lowercase category names to include
      - positive_tokens_lower: list of lowercase substrings to require (optional)
      - negative_tokens_lower: list of lowercase substrings to exclude
      - reason: human-readable summary or reason for no-op
    Rules:
      - "All" includes all available categories.
      - No value → no-op.
      - Categories and positives are separated by ';' (e.g., "wan,flux;vace,nanchaku").
      - Empty categories (e.g., ";pos" or ":neg") → no-op.
      - If specified categories don't match anything, no-op (robust matching applied).
    """
    spec = (spec or "").strip()
    if not spec:
        return set(), [], [], "DOWNLOAD_MODELS not set or empty; skipping model downloads."

    # Split negatives
    if ":" in spec:
        left_raw, neg_raw = spec.split(":", 1)
    else:
        left_raw, neg_raw = spec, ""

    # Split categories and positives
    if ";" in left_raw:
        cats_raw, positives_raw = left_raw.split(";", 1)
    else:
        cats_raw, positives_raw = left_raw, ""

    def _norm_tokens(s: str) -> list[str]:
        # Normalize, split by comma, lower
        return [t.strip().lower() for t in s.split(",") if t.strip()]

    cat_tokens = _norm_tokens(cats_raw)
    pos_tokens = _norm_tokens(positives_raw)
    neg_tokens = _norm_tokens(neg_raw)

    if not cat_tokens:
        return set(), [], [], "No categories specified; nothing to download."

    # Resolve categories: exact match first, then safe substring fallback
    include_categories: set[str] = set()
    if any(t == "all" for t in cat_tokens):
        include_categories = set(available_categories_lower)
    else:
        # exact matches
        for tok in cat_tokens:
            if tok in available_categories_lower:
                include_categories.add(tok)
        # substring fallback (handles stray whitespace/formatting)
        if not include_categories:
            for tok in cat_tokens:
                for cat in available_categories_lower:
                    if tok and tok in cat:
                        include_categories.add(cat)

    if not include_categories:
        cats = ", ".join(sorted(available_categories_lower)) or "(none)"
        return set(), [], [], f"No matching categories in DOWNLOAD_MODELS; available categories: {cats}"

    summary = (
        f"Including categories: {', '.join(sorted(include_categories))}; "
        f"positives: {', '.join(pos_tokens) if pos_tokens else '(none)'}; "
        f"negatives: {', '.join(neg_tokens) if neg_tokens else '(none)'}"
    )
    return include_categories, pos_tokens, neg_tokens, summary

@threaded
def download_models_if_enabled() -> None:
    # Resolve spec
    spec = DOWNLOAD_MODELS_SPEC
    if not spec:
        print("⏩ model downloads disabled: DOWNLOAD_MODELS not set.")
        return

    try:
        file_list_path = workspace / "download_list.txt"
        tmp = file_list_path.with_suffix(file_list_path.suffix + ".part")

        req = urllib.request.Request(MODELS_URL_LIST, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(file_list_path)
        print(f"✓ downloaded: {file_list_path}  ← {MODELS_URL_LIST}")

        # Read list
        with file_list_path.open("r", encoding="utf-8") as f:
            raw_lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

        if not raw_lines:
            print(f"⚠ Model list from {MODELS_URL_LIST} is empty, skipping model downloads.")
            return

        # Parse lines to model entries
        models = []
        malformed = 0
        for idx, line in enumerate(raw_lines, 1):
            parsed = _parse_model_line(line)
            if not parsed:
                malformed += 1
                print(f"⚠ Skipping malformed line {idx}: {line}")
                continue
            repo_id, file_in_repo, local_subdir, category = parsed
            models.append({
                "idx": idx,
                "raw": line,
                "repo_id": repo_id,
                "file_in_repo": file_in_repo,
                "local_subdir": local_subdir,
                "category": (category or DEFAULT_CATEGORY).strip(),
            })

        if not models:
            print("⚠ No valid model entries found; nothing to download.")
            return

        # Category sets (normalized)
        categories_lower = {m["category"].strip().lower() or DEFAULT_CATEGORY.lower() for m in models}
        if DEFAULT_CATEGORY.lower() not in categories_lower:
            categories_lower.add(DEFAULT_CATEGORY.lower())

        # Parse spec against available categories
        include_categories_lower, pos_tokens_lower, neg_tokens_lower, summary = _parse_download_spec(spec, categories_lower)
        print(f"• DOWNLOAD_MODELS spec: {spec}")
        print(f"• {summary}")

        if not include_categories_lower:
            print("⏩ No categories selected; skipping model downloads.")
            return

        # Prepare stage dir
        stage_dir = workspace / "_hfstage"
        stage_dir.mkdir(parents=True, exist_ok=True)

        # Filter models by category, positives, then negatives (case-insensitive substring on full line)
        selected = []
        for m in models:
            cat_l = (m["category"] or DEFAULT_CATEGORY).strip().lower()
            if cat_l not in include_categories_lower:
                continue
            hay = m["raw"].lower()
            # If positive tokens provided, require at least one to match
            if pos_tokens_lower:
                if not any(tok in hay for tok in pos_tokens_lower):
                    continue
            # Exclude any that contain negatives
            if any(tok in hay for tok in neg_tokens_lower):
                continue
            selected.append(m)

        if not selected:
            print("⏩ After applying filters, no models to download.")
            return

        total = len(selected)
        print(f"Found {total} model(s) to download after filtering.")

        for pos, m in enumerate(selected, 1):
            try:
                repo_id = m["repo_id"]
                file_in_repo = m["file_in_repo"]
                local_subdir = m["local_subdir"]
                category = m["category"]

                # Safe target dir inside MODELS
                target_dir = (MODELS / local_subdir.strip("/\\")).resolve()
                if not str(target_dir).startswith(str(MODELS.resolve())):
                    print(f"✗ Invalid target path outside MODELS, skipping: {target_dir} (line {m['idx']})")
                    continue
                target_dir.mkdir(parents=True, exist_ok=True)

                dst = target_dir / Path(file_in_repo).name
                if dst.exists():
                    print(f"[{pos}/{total}] ⏩ already present: {dst}")
                    continue

                print(f"[{pos}/{total}] START {file_in_repo} from {repo_id} (category: {category})")
                # Distinct staging folder per download
                local_stage = stage_dir / f"{m['idx']:05d}"
                local_stage.mkdir(parents=True, exist_ok=True)

                downloaded_path = hf_hub_download(
                    repo_id=repo_id,
                    filename=file_in_repo,
                    token=os.environ.get("HF_TOKEN"),
                    local_dir=str(local_stage)
                )

                src = Path(downloaded_path)
                shutil.move(str(src), str(dst))
                print(f"[{pos}/{total}] ✓ Finished: {dst}")
            except Exception as e:
                print(f"[{pos}/{total}] ⚠ Error on line {m['idx']}: {m['raw']} → {e}")

        if malformed:
            print(f"ℹ Skipped {malformed} malformed line(s) in model list.")
    except Exception as e:
        print(f"⚠ Failed to fetch model list: {e}")
    finally:
        shutil.rmtree(workspace / "_hfstage", ignore_errors=True)

# ---------------------------
# Main
# ---------------------------

def main() -> None:
    # workspace.mkdir(parents=True, exist_ok=True)
    # CUSTOM.mkdir(parents=True, exist_ok=True)

    threads: list[threading.Thread] = []

    # 1) Missing libs
    t_libs = threading.Thread(target=install_missing_from_env, daemon=False)
    t_libs.start()
    threads.append(t_libs)

    # 2) Clone ComfyUI core
    if not COMFY.exists():
        clone("https://github.com/comfyanonymous/ComfyUI.git", COMFY, threads)

    # 3) Fetch & clone custom nodes
    repos = fetch_node_list()
    for repo, run_install in repos:
        name = repo.rstrip("/").split("/")[-1].replace(".git", "")
        dest = CUSTOM / name
        clone(repo, dest, threads, name, run_install)

    # 4) Settings
    t_settings = threading.Thread(target=apply_settings, daemon=False)
    t_settings.start()
    threads.append(t_settings)

    # 5) Models
    t_models = threading.Thread(target=download_models_if_enabled, daemon=False)
    t_models.start()
    threads.append(t_models)

    # 6) Wait
    for t in threads:
        t.join()

    print("🚀 SUCCESSFUL.. NOW RUN COMFY")

if __name__ == "__main__":
    main()
