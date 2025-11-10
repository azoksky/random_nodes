import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

function injectCSSOnce() {
  const id = "az-aria2-css";
  if (document.getElementById(id)) return;
  const style = document.createElement("style");
  style.id = id;
  style.textContent =
    ".az-row{width:100%}" +
    ".az-btn{padding:8px 14px;border:1px solid #555;border-radius:6px;background:#2f75ff;color:#fff;cursor:pointer}" +
    ".az-btn:disabled{opacity:.6;cursor:not-allowed}" +
    ".az-btn-secondary{background:#333;color:#ddd}" +
    ".az-flex{display:flex;gap:8px;align-items:center;justify-content:center;width:100%}";
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

      const rowH = 40;

      // URL input (top)
      const urlInput = document.createElement("input");
      urlInput.type = "text";
      urlInput.placeholder = "URL";
      urlInput.value = this.properties.url || "";
      Object.assign(urlInput.style, {
        width: "100%", height: "26px", padding: "8px",
        border: "1px solid #444", borderRadius: "6px",
        background: "var(--comfy-input-bg, #2a2a2a)", color: "#ddd",
        boxSizing: "border-box", outline: "none"
      });
      const urlWidget = this.addDOMWidget("url", "URL", urlInput);
      urlWidget.computeSize = () => [this.size[0] - 20, rowH];

      // Token input + hint
      const tokenRow = document.createElement("div");
      tokenRow.className = "az-row az-flex";

      const tokenInput = document.createElement("input");
      tokenInput.type = "password";
      tokenInput.placeholder = "Secret Token";
      tokenInput.value = this.properties.token || "";
      Object.assign(tokenInput.style, {
        flex: "1", height: "26px", padding: "8px",
        border: "1px solid #444", borderRadius: "6px",
        background: "var(--comfy-input-bg, #2a2a2a)", color: "#ddd",
        boxSizing: "border-box", outline: "none"
      });

      const tokenHint = document.createElement("span");
      tokenHint.style.color = "#888";
      tokenHint.style.fontSize = "12px";

      tokenRow.appendChild(tokenInput);
      tokenRow.appendChild(tokenHint);
      const tokenWidget = this.addDOMWidget("token", "Token", tokenRow);
      tokenWidget.computeSize = () => [this.size[0] - 20, rowH];

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
      Object.assign(destInput.style, {
        width: "100%", height: "26px", padding: "8px",
        border: "1px solid #444", borderRadius: "6px",
        background: "var(--comfy-input-bg, #2a2a2a)", color: "#ddd",
        boxSizing: "border-box", outline: "none"
      });

      const dropdown = document.createElement("div");
      Object.assign(dropdown.style, {
        position: "fixed", background: "#222", border: "1px solid #555",
        zIndex: "999999", display: "none", maxHeight: "200px",
        overflowY: "auto", fontSize: "12px", borderRadius: "6px",
        minWidth: "180px", boxShadow: "0 8px 16px rgba(0,0,0,.35)"
      });
      document.body.appendChild(dropdown);

      const placeDropdown = () => {
        const rct = destInput.getBoundingClientRect();
        dropdown.style.left = rct.left + "px";
        dropdown.style.top = (rct.bottom + 2) + "px";
        dropdown.style.width = rct.width + "px";
      };

      container.appendChild(destInput);
      const destWidget = this.addDOMWidget("dest_dir", "Destination", container);
      destWidget.computeSize = () => [this.size[0] - 20, rowH];

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
          row.textContent = it.name;
          row.dataset.idx = String(idx);
          row.tabIndex = -1;
          Object.assign(row.style, {
            padding: "6px 10px", cursor: "pointer", whiteSpace: "nowrap",
            background: idx === active ? "#444" : "transparent", userSelect: "none"
          });
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
        // restore scroll, then make sure active is visible
        dropdown.style.display = "block";
        dropdown.scrollTop = prevScroll;
        // scroll active into view (nearest) so keyboard navigation doesn't hide selection
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
        // If input empty, clear
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
        // Only show dropdown if input is focused; prevents async re-open after blur
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

      // When focusing: if field is empty, fetch server root (prefers COMFYUI_MODEL_PATH then COMFYUI_PATH on server).
      destInput.addEventListener("focus", async () => {
        placeDropdown();
        if (!destInput.value || !destInput.value.trim()) {
          try {
            const resp = await api.fetchApi(`/az/listdir`);
            const data = await resp.json();
            if (data?.ok && data.root) {
              const root = (data.root || "").replace(/\\/g, "/");
              // Only override if dest wasn't already set in node properties
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

      // keyboard nav + ensure active is visible
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

      // Hide dropdown on blur immediately and cancel pending fetches
      destInput.addEventListener("blur", () => {
        if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
        items = []; active = -1;
        dropdown.style.display = "none";
      });

      // Close dropdown when clicking outside container (robust)
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

      // Resolve token for URL automatically (old strategy; user can delete)
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

      const btnWidget = this.addDOMWidget("actions", "", btnRow);
      btnWidget.computeSize = () => [this.size[0] - 20, rowH];

      // Small DOM status
      const statusEl = document.createElement("div");
      statusEl.style.color = "#ccc";
      statusEl.style.fontSize = "12px";
      statusEl.style.width = "100%";
      statusEl.style.textAlign = "center";
      statusEl.textContent = "Ready";
      const statusWidget = this.addDOMWidget("status", "", statusEl);
      statusWidget.computeSize = () => [this.size[0] - 20, 24];

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
        this.setDirtyCanvas(true);
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
            this.setDirtyCanvas(true);
            return;
          }
          this.gid = data.gid;
          this._status = "Active";
          statusEl.textContent = "Active" + (data.strategy ? " (" + data.strategy + ")" : "");
          this.setDirtyCanvas(true);

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
                this.setDirtyCanvas(true);
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
              this.setDirtyCanvas(true);

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
          this.setDirtyCanvas(true);
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

      // Canvas size & drawing (progress + meta)
      this.size = [520, 320];
      this.onDrawForeground = (ctx) => {
        const pad = 10;
        const w = this.size[0] - pad * 2;
        const barH = 14;
        const yBar = this.size[1] - pad - barH - 4;
        ctx.font = "12px sans-serif";
        ctx.textAlign = "left";
        ctx.textBaseline = "bottom";
        ctx.fillStyle = "#bbb";
        const meta = "Status: " + this._status
          + " • Speed: " + fmtBytes(this._speed) + "/s"
          + " • ETA: " + fmtETA(this._eta)
          + " • Elapsed: " + fmtETA(this._elapsedSec);
        ctx.fillText(meta, pad, yBar - 26);
        if (this._filename || this._filepath) {
          const show = this._filepath || this._filename;
          ctx.fillStyle = "#8fa3b7";
          ctx.fillText("Saved as: " + show, pad, yBar - 10);
        }
        // Progress outline
        const radius = 7;
        ctx.lineWidth = 1; ctx.strokeStyle = "#666";
        ctx.beginPath();
        ctx.moveTo(pad + radius, yBar);
        ctx.lineTo(pad + w - radius, yBar);
        ctx.quadraticCurveTo(pad + w, yBar, pad + w, yBar + radius);
        ctx.lineTo(pad + w, yBar + barH - radius);
        ctx.quadraticCurveTo(pad + w, yBar + barH, pad + w - radius, yBar + barH);
        ctx.lineTo(pad + radius, yBar + barH);
        ctx.quadraticCurveTo(pad, yBar + barH, pad, yBar + barH - radius);
        ctx.lineTo(pad, yBar + radius);
        ctx.quadraticCurveTo(pad, yBar, pad + radius, yBar);
        ctx.closePath();
        ctx.stroke();
        // Fill bar
        const pct = Math.max(0, Math.min(100, this._progress || 0));
        const fillW = Math.round((w * pct) / 100);
        ctx.save();
        ctx.beginPath();
        ctx.rect(pad + 1, yBar + 1, Math.max(0, fillW - 2), barH - 2);
        const grad = ctx.createLinearGradient(pad, yBar, pad, yBar + barH);
        grad.addColorStop(0, "#9ec7ff");
        grad.addColorStop(1, "#4b90ff");
        ctx.fillStyle = grad;
        ctx.fill();
        ctx.restore();
        // Percentage text
        ctx.font = "12px sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillStyle = "#111";
        ctx.fillText(pct.toFixed(0) + "%", pad + w / 2, yBar + barH / 2);
      };

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

      // Prefill the destInput with the server's working directory (ComfyUI installation/run folder)
      // The server's /az/listdir with no path returns root = os.path.abspath(os.getcwd()).
      (async ()=>{
        try {
          const resp = await api.fetchApi(`/az/listdir`);
          const data = await resp.json();
          if (data?.ok && data.root) {
            const root = (data.root || "").replace(/\\/g, "/");
            // Only override if dest wasn't already set in node properties
            if (!this.properties.dest_dir) {
              destInput.value = root;
              this.properties.dest_dir = root;
              // prefetch children for snappy keyboard navigation; do NOT render dropdown unless focused
              if (debounceTimer) clearTimeout(debounceTimer);
              debounceTimer = setTimeout(fetchChildren, 50);
            }
          }
        } catch (e) {
          // ignore; leave whatever the current value is
        }
      })();

      // If persisted URL and empty token, auto-resolve once
      if (this.properties.url && (!this.properties.token || this._autoToken)) {
        urlInput.value = this.properties.url;
        resolveAndApplyToken();
      }
      // If persisted token exists, reflect it in the hint once suffixes load
      if ((this.properties.token || "").trim() !== "") {
        tokenInput.value = this.properties.token;
        updateTokenHint();
      }

      return r;
    };
  },
});
