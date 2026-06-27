import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Nodes 2.0: the whole UI lives in ONE wrap element behind a single addDOMWidget.
// Multiple separate DOM widgets collapse under the Vue renderer (buttons vanish).
function injectCSSOnce() {
  const id = "az-aria2-css";
  if (document.getElementById(id)) return;
  const style = document.createElement("style");
  style.id = id;
  style.textContent =
    ".az-aria-wrap{display:flex;flex-direction:column;gap:8px;width:100%;box-sizing:border-box;" +
      "font-family:var(--font-family,'Segoe UI',sans-serif)}" +
    ".az-row{width:100%}" +
    ".az-btn{padding:8px 14px;border:1px solid var(--border-color,#555);border-radius:6px;" +
      "background:var(--p-primary-color,#2f75ff);color:var(--p-button-text-primary-color,#fff);cursor:pointer}" +
    ".az-btn:disabled{opacity:.6;cursor:not-allowed}" +
    ".az-btn-secondary{background:var(--comfy-input-bg,#333);color:var(--input-text,#ddd);border-color:var(--border-color,#555)}" +
    ".az-flex{display:flex;gap:8px;align-items:center;justify-content:center;width:100%}" +
    ".az-aria-input{width:100%;height:26px;padding:8px;border:1px solid var(--border-color,#444);" +
      "border-radius:6px;background:var(--comfy-input-bg,#2a2a2a);color:var(--input-text,#ddd);box-sizing:border-box;outline:none}" +
    ".az-aria-input:focus{border-color:var(--p-primary-color,#5b8cff)}" +
    ".az-aria-dd{position:fixed;background:var(--comfy-menu-bg,#222);border:1px solid var(--border-color,#555);" +
      "z-index:999999;display:none;max-height:200px;overflow-y:auto;font-size:12px;border-radius:6px;" +
      "min-width:180px;box-shadow:0 8px 16px rgba(0,0,0,.35);color:var(--input-text,#ddd)}" +
    ".az-aria-row{padding:6px 10px;cursor:pointer;white-space:nowrap;user-select:none}" +
    ".az-aria-row.active{background:var(--comfy-menu-secondary-bg,var(--border-color,#444))}" +
    ".az-aria-meta{color:var(--descrip-text,#bbb);font-size:12px;width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
    ".az-aria-saved{color:var(--descrip-text,#8fa3b7);font-size:12px;width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
    ".az-aria-status{color:var(--descrip-text,#ccc);font-size:12px;width:100%;text-align:center}" +
    ".az-bar{position:relative;height:16px;border:1px solid var(--border-color,#666);border-radius:7px;" +
      "overflow:hidden;background:var(--comfy-input-bg,#222);width:100%;box-sizing:border-box}" +
    ".az-bar-fill{position:absolute;left:0;top:0;bottom:0;width:0%;background:var(--p-primary-color,#4b90ff);transition:width .15s ease}" +
    ".az-bar-pct{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;" +
      "font-size:11px;color:var(--fg-color,#eee);mix-blend-mode:difference}";
  document.head.appendChild(style);
}

function fmtBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  const decimals = v < 10 && i > 0 ? 1 : 0;
  return v.toFixed(decimals) + " " + units[i];
}

function fmtETA(sec) {
  if (sec == null || !isFinite(sec)) return "--";
  sec = Math.max(0, sec | 0);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return h + "h " + m + "m";
  if (m) return m + "m " + s + "s";
  return s + "s";
}

function ensureActiveVisible(dropdown, activeIndex) {
  if (activeIndex < 0 || activeIndex >= dropdown.children.length) return;
  const el = dropdown.children[activeIndex];
  const top = el.offsetTop, bottom = top + el.offsetHeight;
  const viewTop = dropdown.scrollTop, viewBottom = viewTop + dropdown.clientHeight;
  if (top < viewTop) dropdown.scrollTop = top;
  else if (bottom > viewBottom) dropdown.scrollTop = bottom - dropdown.clientHeight;
}

app.registerExtension({
  name: "comfyui.aria2.downloader",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (!nodeData || nodeData.name !== "Aria2Downloader") return;
    const orig = nodeType.prototype.onNodeCreated;

    nodeType.prototype.onNodeCreated = function () {
      const r = orig ? orig.apply(this, arguments) : undefined;
      injectCSSOnce();

      // Persisted properties
      this.properties = this.properties || {};
      this.properties.url = this.properties.url || "";
      this.properties.token = this.properties.token || "";
      this.properties.dest_dir = this.properties.dest_dir || "";
      this.serialize_widgets = true;

      // State
      this.gid = null;
      this._pollTimer = null;
      this._status = "Idle";
      this._progress = 0;
      this._speed = 0;
      this._eta = null;
      this._filename = "";
      this._filepath = "";
      this._startTS = null;
      this._elapsedSec = 0;
      this._autoToken = (this.properties.token || "").trim() === "";

      // ===== One wrap holds the whole UI (single DOM widget) =====
      const wrap = document.createElement("div");
      wrap.className = "az-aria-wrap";

      // URL input (top)
      const urlInput = document.createElement("input");
      urlInput.type = "text";
      urlInput.placeholder = "URL";
      urlInput.value = this.properties.url || "";
      urlInput.className = "az-aria-input";

      // Token input + hint
      const tokenRow = document.createElement("div");
      tokenRow.className = "az-row az-flex";

      const tokenInput = document.createElement("input");
      tokenInput.type = "password";
      tokenInput.placeholder = "Secret Token";
      tokenInput.value = this.properties.token || "";
      tokenInput.className = "az-aria-input";
      tokenInput.style.flex = "1";

      const tokenHint = document.createElement("span");
      tokenHint.style.color = "var(--descrip-text,#888)";
      tokenHint.style.fontSize = "12px";

      tokenRow.appendChild(tokenInput);
      tokenRow.appendChild(tokenHint);

      // Track server-provided last-4 token hints
      let tokenSuffixes = { hf: "", civit: "" };

      // Show hint only when a token is present; choose based on URL
      const updateTokenHint = () => {
        const hasToken = (tokenInput.value || "").trim().length > 0;
        if (!hasToken) {
          tokenHint.textContent = "";
          return;
        }
        const u = (urlInput.value || "").toLowerCase();
        if ((u.indexOf("huggingface.co") >= 0 || u.indexOf("cdn-lfs.huggingface.co") >= 0) && tokenSuffixes.hf) {
          tokenHint.textContent = "HF ..." + tokenSuffixes.hf;
        } else if (u.indexOf("civitai.com") >= 0 && tokenSuffixes.civit) {
          tokenHint.textContent = "Civit ..." + tokenSuffixes.civit;
        } else {
          tokenHint.textContent = "••••";
        }
      };

      tokenInput.addEventListener("input", () => {
        this._autoToken = false;
        this.properties.token = tokenInput.value;
        updateTokenHint();
      });

      // Destination input (below token)
      const container = document.createElement("div");
      container.classList.add("az-path-uploader-container");
      Object.assign(container.style, { position: "relative", width: "100%" });

      const destInput = document.createElement("input");
      destInput.type = "text";
      destInput.placeholder = "Destination folder (e.g. C:/Users/you/Downloads)";
      destInput.value = this.properties.dest_dir || "";
      destInput.className = "az-aria-input";

      const dropdown = document.createElement("div");
      dropdown.className = "az-aria-dd";
      document.body.appendChild(dropdown);

      const placeDropdown = () => {
        const rct = destInput.getBoundingClientRect();
        dropdown.style.left = rct.left + "px";
        dropdown.style.top = (rct.bottom + 2) + "px";
        dropdown.style.width = rct.width + "px";
      };

      container.appendChild(destInput);

      let items = []; let active = -1; let debounceTimer = null;

      const renderDropdown = () => {
        const prevScroll = dropdown.scrollTop;
        dropdown.innerHTML = "";
        if (!items.length) {
          dropdown.style.display = "none"; active = -1; return;
        }
        for (let idx = 0; idx < items.length; idx++) {
          const it = items[idx];
          const row = document.createElement("div");
          row.className = "az-aria-row" + (idx === active ? " active" : "");
          row.textContent = it.name;
          row.dataset.idx = String(idx);
          row.tabIndex = -1;
          row.addEventListener("mousedown", (e) => {
            e.preventDefault();
            const chosen = it.path.replace(/\\/g, "/").replace(/\/{2,}/g, "/");
            destInput.value = chosen;
            this.properties.dest_dir = chosen;
            items = []; active = -1;
            dropdown.style.display = "none";
            scheduleFetch();
          });
          row.onmouseenter = () => { active = idx; renderDropdown(); };
          dropdown.appendChild(row);
        }
        placeDropdown();
        dropdown.style.display = "block";
        dropdown.scrollTop = prevScroll;
        if (active >= 0 && dropdown.children.length > active) {
          const activeRow = dropdown.children[active];
          try {
            if (typeof activeRow.scrollIntoView === "function") {
              activeRow.scrollIntoView({ block: "nearest" });
            } else {
              ensureActiveVisible(dropdown, active);
            }
          } catch (e) { /* ignore */ }
        }
      };

      const fetchChildren = async () => {
        const raw = (destInput.value || "").trim();
        if (!raw) { items = []; renderDropdown(); return; }
        const val = raw.replace(/\\/g, "/").replace(/\/{2,}/g, "/");
        try {
          const resp = await api.fetchApi("/az/listdir?path=" + encodeURIComponent(val));
          const data = await resp.json();
          if (data && data.ok && Array.isArray(data.folders)) {
            items = data.folders.map(function (f) {
              return { name: f.name, path: ((data.root || val) + "/" + f.name).replace(/\\/g, "/").replace(/\/{2,}/g, "/") };
            });
          } else {
            items = [];
          }
        } catch (e) {
          items = [];
        }
        active = items.length ? 0 : -1;
        if (document.activeElement === destInput) {
          renderDropdown();
        } else {
          dropdown.style.display = "none";
        }
      };

      const scheduleFetch = () => {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(fetchChildren, 180);
      };

      // input normalization + caret restore (fixed delta)
      destInput.addEventListener("input", () => {
        const raw = destInput.value;
        const prevStart = destInput.selectionStart || 0;
        const normalized = raw.replace(/\\/g, "/").replace(/\/{2,}/g, "/");
        if (normalized !== raw) {
          const delta = normalized.length - raw.length;
          destInput.value = normalized;
          const pos = Math.max(0, prevStart + delta);
          destInput.setSelectionRange(pos, pos);
        }
        this.properties.dest_dir = destInput.value;
        placeDropdown();
        scheduleFetch();
      });

      destInput.addEventListener("focus", async () => {
        placeDropdown();
        if (!destInput.value || !destInput.value.trim()) {
          try {
            const resp = await api.fetchApi(`/az/listdir`);
            const data = await resp.json();
            if (data?.ok && data.root) {
              const root = (data.root || "").replace(/\\/g, "/");
              if (!this.properties.dest_dir) {
                destInput.value = root;
                this.properties.dest_dir = root;
                if (debounceTimer) clearTimeout(debounceTimer);
                debounceTimer = setTimeout(fetchChildren, 50);
                return;
              }
            }
          } catch (e) {
            // ignore
          }
        }
        scheduleFetch();
      });

      destInput.addEventListener("keydown", (e) => {
        if (dropdown.style.display !== "block" || !items.length) return;
        if (e.key === "ArrowDown") {
          e.preventDefault(); active = (active + 1) % items.length; renderDropdown();
        } else if (e.key === "ArrowUp") {
          e.preventDefault(); active = (active - 1 + items.length) % items.length; renderDropdown();
        } else if (e.key === "Enter" && active >= 0) {
          e.preventDefault();
          const it = items[active];
          const chosen = it.path.replace(/\\/g, "/").replace(/\/{2,}/g, "/");
          destInput.value = chosen;
          this.properties.dest_dir = chosen;
          items = []; active = -1;
          dropdown.style.display = "none";
          scheduleFetch();
        } else if (e.key === "Escape") {
          dropdown.style.display = "none"; items = []; active = -1;
        }
      });

      destInput.addEventListener("blur", () => {
        if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
        items = []; active = -1;
        dropdown.style.display = "none";
      });

      const docHandler = (ev) => {
        if (!container.contains(ev.target) && !dropdown.contains(ev.target)) {
          if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
          items = []; active = -1;
          dropdown.style.display = "none";
        }
      };
      document.addEventListener("pointerdown", docHandler);

      // Fetch token suffixes once; show only if token input has value
      api.fetchApi("/tokens")
        .then(function (res) { return res.json(); })
        .then((data) => {
          tokenSuffixes.hf = (data && data.hf) ? data.hf : "";
          tokenSuffixes.civit = (data && data.civit) ? data.civit : "";
          updateTokenHint();
        })
        .catch(function () { });

      // Resolve token for URL automatically (user can delete)
      let urlDebounce = null;
      const resolveAndApplyToken = async () => {
        const url = (urlInput.value || "").trim();
        if (!url) return;
        try {
          const resp = await api.fetchApi("/tokens/resolve?url=" + encodeURIComponent(url));
          const data = await resp.json();
          const tok = (data && data.token) ? data.token : "";
          if (tok && (this._autoToken || tokenInput.value.trim() === "")) {
            tokenInput.value = tok;
            this.properties.token = tok;
            this._autoToken = true;
            updateTokenHint();
          }
        } catch (e) { }
      };
      const scheduleResolveToken = () => {
        clearTimeout(urlDebounce);
        urlDebounce = setTimeout(resolveAndApplyToken, 200);
      };
      urlInput.addEventListener("input", () => {
        this.properties.url = urlInput.value;
        scheduleResolveToken();
        updateTokenHint();
      });
      urlInput.addEventListener("paste", () => {
        setTimeout(() => { scheduleResolveToken(); updateTokenHint(); }, 0);
      });
      urlInput.addEventListener("blur", () => {
        resolveAndApplyToken();
        updateTokenHint();
      });

      // DOM Buttons
      const btnRow = document.createElement("div");
      btnRow.className = "az-row az-flex";

      const downloadBtn = document.createElement("button");
      downloadBtn.className = "az-btn";
      downloadBtn.textContent = "Download";

      const stopBtn = document.createElement("button");
      stopBtn.className = "az-btn az-btn-secondary";
      stopBtn.textContent = "Stop";
      stopBtn.disabled = true;

      btnRow.appendChild(downloadBtn);
      btnRow.appendChild(stopBtn);

      // Small DOM status
      const statusEl = document.createElement("div");
      statusEl.className = "az-aria-status";
      statusEl.textContent = "Ready";

      // Meta line + saved path + progress bar
      const metaEl = document.createElement("div");
      metaEl.className = "az-aria-meta";

      const savedEl = document.createElement("div");
      savedEl.className = "az-aria-saved";

      const bar = document.createElement("div");
      bar.className = "az-bar";
      const barFill = document.createElement("div");
      barFill.className = "az-bar-fill";
      const barPct = document.createElement("div");
      barPct.className = "az-bar-pct";
      bar.append(barFill, barPct);

      // Assemble the single wrap
      wrap.append(urlInput, tokenRow, container, btnRow, statusEl, metaEl, savedEl, bar);
      const uiWidget = this.addDOMWidget("aria2_ui", "Aria2 Downloader", wrap);
      uiWidget.computeSize = () => [this.size[0] - 20, 250];

      const renderProgress = () => {
        metaEl.textContent = "Status: " + this._status
          + " • Speed: " + fmtBytes(this._speed) + "/s"
          + " • ETA: " + fmtETA(this._eta)
          + " • Elapsed: " + fmtETA(this._elapsedSec);
        const show = this._filepath || this._filename;
        savedEl.style.display = show ? "block" : "none";
        savedEl.textContent = show ? "Saved as: " + show : "";
        const pct = Math.max(0, Math.min(100, this._progress || 0));
        barFill.style.width = pct + "%";
        barPct.textContent = pct.toFixed(0) + "%";
      };

      const setDownloading = (on) => {
        downloadBtn.disabled = on;
        stopBtn.disabled = !on;
      };

      // Start download
      downloadBtn.addEventListener("click", async () => {
        if (this.gid) return;
        const url = (urlInput.value || "").trim();
        const dest = (destInput.value || "").trim();
        const token = (tokenInput.value || "").trim();
        if (!url) {
          statusEl.textContent = "Missing URL";
          return;
        }
        statusEl.textContent = "Negotiating...";
        this._status = "Starting...";
        this._progress = 0; this._speed = 0; this._eta = null;
        this._filename = ""; this._filepath = "";
        this._startTS = Date.now();
        this._elapsedSec = 0;
        renderProgress();
        setDownloading(true);

        try {
          const resp = await api.fetchApi("/aria2/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: url, dest_dir: dest, token: token })
          });
          const data = await resp.json();
          if (!resp.ok || data.error) {
            let extra = "";
            if (data && Array.isArray(data.attempts)) {
              const parts = data.attempts.map((a) => a.name + ":" + a.status + (a.note ? "(" + a.note + ")" : ""));
              extra = " [tried " + parts.join(", ") + "]";
            }
            statusEl.textContent = "Error: " + (data.error || resp.status) + extra;
            this._status = statusEl.textContent;
            setDownloading(false);
            renderProgress();
            return;
          }
          this.gid = data.gid;
          this._status = "Active";
          statusEl.textContent = "Active" + (data.strategy ? " (" + data.strategy + ")" : "");
          renderProgress();

          const poll = async () => {
            if (!this.gid) return;
            try {
              const sResp = await api.fetchApi("/aria2/status?gid=" + encodeURIComponent(this.gid));
              const s = await sResp.json();
              if (s.error) {
                this._status = "Error: " + s.error;
                statusEl.textContent = this._status;
                this.gid = null;
                setDownloading(false);
                renderProgress();
                return;
              }
              this._status = s.status || "active";
              this._progress = s.percent || 0;
              this._speed = s.downloadSpeed || 0;
              this._eta = s.eta || null;
              if (s.filename) this._filename = s.filename;
              if (s.filepath) this._filepath = s.filepath;

              if (this._startTS) {
                this._elapsedSec = Math.max(0, ((Date.now() - this._startTS) / 1000) | 0);
              }
              statusEl.textContent = "Status: " + this._status;
              renderProgress();

              if (["complete", "error", "removed"].includes(this._status)) {
                this.gid = null;
                setDownloading(false);
                return;
              }
              this._pollTimer = setTimeout(poll, 500);
            } catch (e) {
              this._pollTimer = setTimeout(poll, 500);
            }
          };
          poll();
        } catch (e) {
          this._status = "Error starting download";
          statusEl.textContent = this._status;
          setDownloading(false);
          renderProgress();
        }
      });

      // Stop
      stopBtn.addEventListener("click", async () => {
        if (!this.gid) {
          statusEl.textContent = "Stopped.";
          setDownloading(false);
          return;
        }
        try {
          await api.fetchApi("/aria2/stop", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ gid: this.gid })
          });
          statusEl.textContent = "Stopped.";
        } catch (e) {
          statusEl.textContent = "Error stopping: " + e.message;
        } finally {
          this.gid = null;
          setDownloading(false);
        }
      });

      this.size = [520, 330];
      renderProgress();

      // Reposition dropdown on scroll/resize
      const onScroll = () => { placeDropdown(); };
      const onResize = () => { placeDropdown(); };
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", onResize);

      // Cleanup
      const oldRemoved = this.onRemoved;
      this.onRemoved = function () {
        if (this._pollTimer) clearTimeout(this._pollTimer);
        try { if (dropdown && dropdown.parentNode) dropdown.parentNode.removeChild(dropdown); } catch (e) { }
        window.removeEventListener("scroll", onScroll, true);
        window.removeEventListener("resize", onResize);
        document.removeEventListener("pointerdown", docHandler);
        if (oldRemoved) oldRemoved.apply(this, arguments);
      };

      // Fetch the server's default working directory into destInput (only if empty).
      const fetchDefaultRoot = () => {
        api.fetchApi(`/az/listdir`).then((r) => r.json()).then((data) => {
          if (data?.ok && data.root && !this.properties.dest_dir) {
            const root = (data.root || "").replace(/\\/g, "/");
            destInput.value = root;
            this.properties.dest_dir = root;
            if (debounceTimer) clearTimeout(debounceTimer);
            debounceTimer = setTimeout(fetchChildren, 50);
          }
        }).catch(() => {});
      };

      // Restore DOM fields from a loaded/cached workflow (fires after deserialization).
      const prevConfigure = this.onConfigure;
      this.onConfigure = function (info) {
        prevConfigure?.apply(this, arguments);
        if (this.properties.url) urlInput.value = this.properties.url;
        if ((this.properties.token || "").trim() !== "") tokenInput.value = this.properties.token;
        const savedDest = (this.properties.dest_dir || "").trim();
        if (savedDest) destInput.value = savedDest;
        else fetchDefaultRoot();
        if (this.properties.url && ((this.properties.token || "").trim() === "" || this._autoToken)) {
          resolveAndApplyToken();
        }
        updateTokenHint();
      };

      // Fresh node (not from a saved workflow): onConfigure won't fire.
      fetchDefaultRoot();

      return r;
    };
  },
});
