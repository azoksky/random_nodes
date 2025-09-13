import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

function ensureActiveVisible(dropdown, activeIndex) {
  if (activeIndex < 0 || activeIndex >= dropdown.children.length) return;
  const el = dropdown.children[activeIndex];
  const top = el.offsetTop;
  const bottom = top + el.offsetHeight;
  const viewTop = dropdown.scrollTop;
  const viewBottom = viewTop + dropdown.clientHeight;
  if (top < viewTop) dropdown.scrollTop = top;
  else if (bottom > viewBottom) dropdown.scrollTop = bottom - dropdown.clientHeight;
}

function injectCSSOnce() {
  const id = "az-hf-css";
  if (document.getElementById(id)) return;
  const style = document.createElement("style");
  style.id = id;
  style.textContent =
    ".az-row{width:100%}\
     .az-btn{padding:8px 14px;border:1px solid #555;border-radius:6px;background:#2f75ff;color:#fff;cursor:pointer}\
     .az-btn:disabled{opacity:.6;cursor:not-allowed}\
     .az-btn-secondary{background:#333;color:#ddd}\
     .az-flex{display:flex;gap:8px;align-items:center;justify-content:center;width:100%}\
     .az-progress{width:100%;height:12px;border:1px solid #666;border-radius:6px;background:#222;overflow:hidden;display:none}\
     .az-progress .bar{position:relative;height:100%;width:40%;background:linear-gradient(#9ec7ff,#4b90ff);animation:az-hf-indeterminate 1.2s infinite ease}\
     @keyframes az-hf-indeterminate{0%{transform:translateX(-100%);width:40%}50%{transform:translateX(50%);width:60%}100%{transform:translateX(200%);width:40%}}";
  document.head.appendChild(style);
}

function fmtDuration(sec) {
  if (sec == null || !isFinite(sec)) return "--";
  sec = Math.max(0, sec | 0);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  if (h) return h + "h " + m + "m";
  if (m) return m + "m " + s + "s";
  return s + "s";
}

app.registerExtension({
  name: "aznodes.hf_hub_downloader",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (!nodeData || nodeData.name !== "hf_hub_downloader") return;
    const orig = nodeType.prototype.onNodeCreated;

    nodeType.prototype.onNodeCreated = function () {
      const r = orig ? orig.apply(this, arguments) : undefined;
      injectCSSOnce();

      // Persisted properties
      this.properties = this.properties || {};
      this.properties.repo_id = this.properties.repo_id || "";
      this.properties.filename = this.properties.filename || "";
      this.properties.dest_dir = this.properties.dest_dir || "";
      this.properties.token = this.properties.token || "";
      this.serialize_widgets = true;

      // State
      this.gid = null;
      this._pollTimer = null;
      this._autoToken = (this.properties.token || "").trim() === "";
      this._startTS = null;
      this._elapsedTimer = null;

      const rowH = 40;
      const smallRowH = 24;

      // Repository input
      const repoInput = document.createElement("input");
      repoInput.type = "text";
      repoInput.placeholder = "Repository ID (e.g. runwayml/stable-diffusion-v1-5)";
      repoInput.value = this.properties.repo_id || "";
      Object.assign(repoInput.style, {
        width: "100%", height: "26px", padding: "8px",
        border: "1px solid #444", borderRadius: "6px",
        background: "var(--comfy-input-bg, #2a2a2a)", color: "#ddd",
        boxSizing: "border-box", outline: "none"
      });
      const repoWidget = this.addDOMWidget("repo_id", "Repository", repoInput);
      repoWidget.computeSize = () => [this.size[0] - 20, rowH];
      repoInput.addEventListener("input", () => {
        this.properties.repo_id = repoInput.value;
      });

      // Filename input
      const fileInput = document.createElement("input");
      fileInput.type = "text";
      fileInput.placeholder = "Filename (e.g. model.safetensors)";
      fileInput.value = this.properties.filename || "";
      Object.assign(fileInput.style, {
        width: "100%", height: "26px", padding: "8px",
        border: "1px solid #444", borderRadius: "6px",
        background: "var(--comfy-input-bg, #2a2a2a)", color: "#ddd",
        boxSizing: "border-box", outline: "none"
      });
      const fileWidget = this.addDOMWidget("filename", "Filename", fileInput);
      fileWidget.computeSize = () => [this.size[0] - 20, rowH];
      fileInput.addEventListener("input", () => {
        this.properties.filename = fileInput.value;
      });

      // Token input + hint
      const tokenRow = document.createElement("div");
      tokenRow.className = "az-row az-flex";

      const tokenInput = document.createElement("input");
      tokenInput.type = "password";
      tokenInput.placeholder = "HF Token (auto-filled from env if available)";
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

      tokenInput.addEventListener("input", () => {
        this._autoToken = false;
        this.properties.token = tokenInput.value;
      });

      // Fetch token (full) and hint on node display
      api.fetchApi("/hf/token")
        .then(function (res) { return res.json(); })
        .then((data) => {
          const tok = (data && data.token) ? data.token : "";
          if (tok && (this._autoToken || tokenInput.value.trim() === "")) {
            tokenInput.value = tok;
            this.properties.token = tok;
            this._autoToken = true;
          }
        })
        .catch(function () { });

      api.fetchApi("/hf/tokens")
        .then(function (res) { return res.json(); })
        .then((data) => {
          if (data && data.hf) tokenHint.textContent = "HF ..." + data.hf;
        })
        .catch(function () { });

      // Destination input with dropdown
      const container = document.createElement("div");
      Object.assign(container.style, { position: "relative", width: "100%" });

      const destInput = document.createElement("input");
      destInput.type = "text";
      destInput.placeholder = "Destination folder (e.g. ./models)";
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
        display: "none", maxHeight: "200px", overflowY: "auto", fontSize: "12px",
        borderRadius: "6px", boxShadow: "0 8px 16px rgba(0,0,0,.35)",
        zIndex: "999999", minWidth: "180px"
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

      let items = [];
      let active = -1;
      let debounceTimer = null;

      const renderDropdown = () => {
        const prevScroll = dropdown.scrollTop;
        dropdown.innerHTML = "";
        if (!items.length) {
          dropdown.style.display = "none";
          active = -1;
          return;
        }
        for (let idx = 0; idx < items.length; idx++) {
          const it = items[idx];
          const row = document.createElement("div");
          row.textContent = it.name;
          Object.assign(row.style, {
            padding: "6px 10px", cursor: "pointer", whiteSpace: "nowrap",
            background: idx === active ? "#444" : "transparent", userSelect: "none"
          });
          row.addEventListener("mousedown", (e) => {
            e.preventDefault();
            const chosen = it.path.replace(/\\/g, "/").replace(/\/{2,}/g, "/");
            destInput.value = chosen;
            this.properties.dest_dir = chosen;
            items = [];
            active = -1;
            dropdown.style.display = "none";
            scheduleFetch();
          });
          row.onmouseenter = () => {
            active = idx;
            renderDropdown();
          };
          dropdown.appendChild(row);
        }
        placeDropdown();
        dropdown.style.display = "block";
        dropdown.scrollTop = prevScroll;
        ensureActiveVisible(dropdown, active);
      };

      const fetchChildren = async () => {
        const raw = destInput.value.trim();
        if (!raw) {
          items = [];
          renderDropdown();
          return;
        }
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
        renderDropdown();
      };

      const scheduleFetch = () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(fetchChildren, 180);
      };

      destInput.addEventListener("input", () => {
        const raw = destInput.value;
        const prevStart = destInput.selectionStart;
        const normalized = raw.replace(/\\/g, "/").replace(/\/{2,}/g, "/");
        if (normalized !== raw) {
          const delta = normalized.length - raw.length;
          destInput.value = normalized;
          const pos = Math.max(0, (prevStart || 0) + delta);
          destInput.setSelectionRange(pos, pos);
        }
        this.properties.dest_dir = destInput.value;
        placeDropdown();
        scheduleFetch();
      });

      destInput.addEventListener("focus", () => {
        placeDropdown();
        fetchChildren();
      });

      destInput.addEventListener("keydown", (e) => {
        if (dropdown.style.display !== "block" || !items.length) return;
        if (e.key === "ArrowDown") {
          e.preventDefault();
          active = (active + 1) % items.length;
          renderDropdown();
          ensureActiveVisible(dropdown, active);
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          active = (active - 1 + items.length) % items.length;
          renderDropdown();
          ensureActiveVisible(dropdown, active);
        } else if (e.key === "Enter" && active >= 0) {
          e.preventDefault();
          const it = items[active];
          const chosen = it.path.replace(/\\/g, "/").replace(/\/{2,}/g, "/");
          destInput.value = chosen;
          this.properties.dest_dir = chosen;
          items = [];
          active = -1;
          dropdown.style.display = "none";
          scheduleFetch();
        } else if (e.key === "Escape") {
          dropdown.style.display = "none";
          items = [];
          active = -1;
        }
      });

      destInput.addEventListener("blur", () => {
        setTimeout(function () { dropdown.style.display = "none"; }, 120);
      });

      // Buttons row (DOM buttons)
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

      // Progress row (indeterminate)
      const progress = document.createElement("div");
      progress.className = "az-progress";
      const bar = document.createElement("div");
      bar.className = "bar";
      progress.appendChild(bar);
      const progressWidget = this.addDOMWidget("progress", "", progress);
      progressWidget.computeSize = () => [this.size[0] - 20, smallRowH];

      // Status row (DOM text)
      const statusEl = document.createElement("div");
      statusEl.style.color = "#ccc";
      statusEl.style.fontSize = "12px";
      statusEl.style.width = "100%";
      statusEl.style.textAlign = "center";
      statusEl.textContent = "Ready";
      const statusWidget = this.addDOMWidget("status", "", statusEl);
      statusWidget.computeSize = () => [this.size[0] - 20, smallRowH];

      // Elapsed row
      const timeEl = document.createElement("div");
      timeEl.style.color = "#999";
      timeEl.style.fontSize = "12px";
      timeEl.style.width = "100%";
      timeEl.style.textAlign = "center";
      timeEl.textContent = "Elapsed: 0s";
      const timeWidget = this.addDOMWidget("elapsed", "", timeEl);
      timeWidget.computeSize = () => [this.size[0] - 20, smallRowH];

      const setDownloading = (on) => {
        downloadBtn.disabled = on;
        stopBtn.disabled = !on;
        progress.style.display = on ? "block" : "none";
      };

      const startElapsed = () => {
        this._startTS = Date.now();
        if (this._elapsedTimer) clearInterval(this._elapsedTimer);
        const tick = () => {
          if (!this._startTS) return;
          const sec = Math.max(0, ((Date.now() - this._startTS) / 1000) | 0);
          timeEl.textContent = "Elapsed: " + fmtDuration(sec);
        };
        tick();
        this._elapsedTimer = setInterval(tick, 1000);
      };

      const stopElapsed = () => {
        if (this._elapsedTimer) clearInterval(this._elapsedTimer);
        this._elapsedTimer = null;
      };

      const startPoll = () => {
        const poll = async () => {
          if (!this.gid) return;
          try {
            const res = await api.fetchApi("/hf/status?gid=" + encodeURIComponent(this.gid));
            const s = await res.json();
            if (!s.ok) {
              statusEl.textContent = "Error: " + (s.error || "Unknown");
              this.gid = null;
              setDownloading(false);
              stopElapsed();
              return;
            }
            statusEl.textContent = s.msg || s.state || "running";
            if (s.state === "done" || s.state === "error" || s.state === "stopped") {
              this.gid = null;
              setDownloading(false);
              stopElapsed();
              return;
            }
            this._pollTimer = setTimeout(poll, 900);
          } catch (e) {
            this._pollTimer = setTimeout(poll, 1100);
          }
        };
        poll();
      };

      // Button events
      downloadBtn.addEventListener("click", async () => {
        if (this.gid) return;
        const repo_id = (repoInput.value || "").trim();
        const filename = (fileInput.value || "").trim();
        const dest_dir = (destInput.value || "").trim();
        const token = (tokenInput.value || "").trim();
        if (!repo_id || !filename || !dest_dir) {
          statusEl.textContent = "Please fill all fields";
          return;
        }
        statusEl.textContent = "Starting...";
        setDownloading(true);
        startElapsed();
        try {
          const res = await api.fetchApi("/hf/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repo_id: repo_id, filename: filename, dest_dir: dest_dir, token_input: token })
          });
          const out = await res.json();
          if (!res.ok || !out.ok) {
            statusEl.textContent = "Error: " + (out.error || res.status);
            setDownloading(false);
            stopElapsed();
            return;
          }
          this.gid = out.gid;
          statusEl.textContent = "Download started...";
          startPoll();
        } catch (e) {
          statusEl.textContent = "Error starting: " + e.message;
          setDownloading(false);
          stopElapsed();
        }
      });

      stopBtn.addEventListener("click", async () => {
        if (!this.gid) {
          setDownloading(false);
          statusEl.textContent = "Stopped.";
          stopElapsed();
          return;
        }
        try {
          await api.fetchApi("/hf/stop", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ gid: this.gid })
          });
          statusEl.textContent = "Stopped.";
        } catch (e) {
          statusEl.textContent = "Error stopping: " + e.message;
        } finally {
          setDownloading(false);
          stopElapsed();
          this.gid = null;
        }
      });

      // Reposition dropdown on scroll/resize
      const onScroll = () => { placeDropdown(); };
      const onResize = () => { placeDropdown(); };
      window.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", onResize);

      // Cleanup
      const oldRemoved = this.onRemoved;
      this.onRemoved = function () {
        if (this._pollTimer) clearTimeout(this._pollTimer);
        stopElapsed();
        try { if (dropdown && dropdown.parentNode) dropdown.parentNode.removeChild(dropdown); } catch (e) { }
        window.removeEventListener("scroll", onScroll, true);
        window.removeEventListener("resize", onResize);
        if (oldRemoved) oldRemoved.apply(this, arguments);
      };

      // Good default size
      this.size = [520, 320];

      return r;
    };
  },
});
