// js/hf_list_downloader.js
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

(function injectOnce(){
  if (document.getElementById("hf-list-dl-style")) return;
  const css = document.createElement("style");
  css.id = "hf-list-dl-style";
  css.textContent = `
  .hfld-wrap { display:flex; flex-direction:column; gap:8px; width:100%; }
  .hfld-row { display:grid; grid-template-columns: 22px 1fr max-content; align-items:center;
              gap:8px; padding:6px 8px; border:1px solid var(--border-color,#333); border-radius:8px; background:var(--comfy-input-bg,#1f1f1f);
              position: relative; overflow: hidden; min-height: 40px; box-sizing: border-box; }
  .hfld-row > * { position: relative; z-index: 1; }
  .hfld-row div:not(.hfld-fill) { background: none !important; }

  /* Determinate progress fill (width driven by JS) sits behind the content,
     a solid blue bar that grows across the whole row as bytes arrive. */
  .hfld-fill { position:absolute !important; left:0; top:0; bottom:0; width:0%;
               background: linear-gradient(90deg, rgba(38,110,255,0.55), rgba(70,140,255,0.65)) !important;
               z-index:0; transition: width .25s linear; pointer-events:none; }
  /* Indeterminate (unknown total): sliding shimmer */
  .hfld-row.indet .hfld-fill { width:35% !important; transition:none;
               animation: hfldSlide 1.1s ease-in-out infinite; }
  @keyframes hfldSlide { 0% { left:-35%; } 100% { left:100%; } }

  /* Two-line cell: filename (left) + centered status detail */
  .hfld-cell { min-width:0; display:flex; flex-direction:column; justify-content:center; }
  .hfld-lab { font-size: 12px; line-height: 16px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .hfld-detail { font-size: 11px; line-height: 15px; color:var(--input-text,#cfe0f5); text-align:center;
                 font-variant-numeric: tabular-nums; white-space: nowrap;
                 overflow: hidden; text-overflow: ellipsis; min-height:15px; }
  .hfld-row.downloading .hfld-detail { color:#eaf2ff; font-weight:600; }

  .hfld-row.done { background: rgba(60,200,120,0.18); border-color:#3dc878; }
  .hfld-row.done .hfld-fill { background: rgba(60,200,120,0.30) !important; }
  .hfld-row.error { background: rgba(220,80,80,0.18); border-color:#e07070; }
  .hfld-row.error .hfld-detail { color:#f0a0a0; }

  .hfld-list { flex: 1; overflow:auto; display:flex; flex-direction:column; gap:6px; }
  .hfld-toolbar { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
  .hfld-btn, .hfld-input { height:26px; border-radius:6px; border:1px solid var(--border-color,#444); background:var(--comfy-input-bg,#2a2a2a); color:var(--input-text,#ddd); padding:0 8px; }
  .hfld-btn { cursor:pointer; }
  .hfld-msg { color:var(--descrip-text,#9ab); font-size:12px; min-height:16px; }
  .hfld-time { font-size:11px; color:var(--descrip-text,#cbd); padding-left:10px; white-space:nowrap; }
  .hfld-input.hfld-search { width: 200px; }
  .hfld-input.hfld-category { min-width: 140px; }
  `;
  document.head.appendChild(css);
})();

app.registerExtension({
  name: "comfyui.hf_list_downloader",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "hf_list_downloader") return;

    const orig = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = orig ? orig.apply(this, arguments) : undefined;

      this.properties = this.properties || {};
      this.properties.list_path = this.properties.list_path || "download_list.txt";
      this.properties.category_filter = this.properties.category_filter || "All";
      this.properties.search_query = this.properties.search_query || "";
      this.serialize_widgets = true;

      const wrap = document.createElement("div");
      wrap.className = "hfld-wrap";

      // Toolbar
      const bar = document.createElement("div");
      bar.className = "hfld-toolbar";

      const pathInput = document.createElement("input");
      pathInput.className = "hfld-input";
      pathInput.placeholder = "Path to download_list.txt";
      pathInput.value = this.properties.list_path;

      // Category dropdown (always includes 'All' first)
      const selCategory = document.createElement("select");
      selCategory.className = "hfld-input hfld-category";

      // Search box (case-insensitive, min 3 characters)
      const searchInput = document.createElement("input");
      searchInput.className = "hfld-input hfld-search";
      searchInput.placeholder = "Search… (min 3 chars)";
      searchInput.value = this.properties.search_query || "";

      const btnRead = document.createElement("button");
      btnRead.className = "hfld-btn";
      btnRead.textContent = "Read";

      const btnRefresh = document.createElement("button");
      btnRefresh.className = "hfld-btn";
      btnRefresh.textContent = "Fetch";

      const btnSelectAll = document.createElement("button");
      btnSelectAll.className = "hfld-btn";
      btnSelectAll.textContent = "Select All";

      const btnClear = document.createElement("button");
      btnClear.className = "hfld-btn";
      btnClear.textContent = "Clear";

      const btnDownload = document.createElement("button");
      btnDownload.className = "hfld-btn";
      btnDownload.textContent = "Download";

      // This button refreshes node definitions (same as pressing R)
      const btnPull = document.createElement("button");
      btnPull.className = "hfld-btn";
      btnPull.textContent = "Refresh";

      // Order: path, category, search, then actions
      bar.append(pathInput, selCategory, searchInput, btnRead, btnRefresh, btnSelectAll, btnClear, btnDownload, btnPull);

      // List
      const list = document.createElement("div");
      list.className = "hfld-list";

      // Message line
      const msg = document.createElement("div");
      msg.className = "hfld-msg";

      wrap.append(bar, list, msg);

      const widget = this.addDOMWidget("hfld_ui", "HF List Downloader", wrap);
      widget.computeSize = () => [this.size[0] - 20, 440];

      // State
      let items = []; // {id, category, repo_id, file_in_repo, local_subdir, el, cb, timeEl, lab}
      let lastRendered = [];
      const ALL = "All";
      const FALLBACK_CATEGORY = "Misc";

      const setMsg = (t, isErr=false) => { msg.textContent = t || ""; msg.style.color = isErr? "var(--error-text,#e88)" : "var(--descrip-text,#9ab)"; };

      const fmtTime = (ms) => {
        ms = Math.max(0, Math.floor(ms));
        const s = Math.floor(ms / 1000);
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const ss = s % 60;
        const pad = (n) => String(n).padStart(2, "0");
        return `${pad(h)}:${pad(m)}:${pad(ss)}`;
      };

      const fmtBytes = (n) => {
        n = Number(n) || 0;
        if (n <= 0) return "0 B";
        const u = ["B", "KB", "MB", "GB", "TB"];
        let i = 0, v = n;
        while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
        return `${v.toFixed(i === 0 || v >= 100 ? 0 : 1)} ${u[i]}`;
      };
      const fmtSpeed = (bps) => `${fmtBytes(bps)}/s`;

      const getDisplayCategory = (it) => {
        const c = (it?.category ?? "").trim();
        return c || FALLBACK_CATEGORY;
      };

      // Build dropdown options from current items; 'All' first
      const buildCategoryOptions = () => {
        const unique = new Set();
        items.forEach(it => unique.add(getDisplayCategory(it)));
        const sorted = Array.from(unique).sort((a, b) => a.localeCompare(b));

        selCategory.innerHTML = "";
        const makeOpt = (val) => {
          const o = document.createElement("option");
          o.value = val; o.textContent = val;
          return o;
        };
        selCategory.appendChild(makeOpt(ALL));
        sorted.forEach(c => selCategory.appendChild(makeOpt(c)));

        // Restore persisted selection or fallback to ALL
        const desired = this.properties.category_filter || ALL;
        const allowed = new Set([ALL, ...sorted]);
        selCategory.value = allowed.has(desired) ? desired : ALL;
      };

      // Render based on category + search filters
      const render = () => {
        list.innerHTML = "";
        const selectedCat = selCategory.value || ALL;
        const q = (searchInput.value || "").trim().toLowerCase();
        const hasQuery = q.length >= 3;

        const toRender = items.filter(it => {
          const inCat = (selectedCat === ALL) ? true : (getDisplayCategory(it) === selectedCat);
          if (!inCat) return false;
          if (!hasQuery) return true;
          const hay = `${it.repo_id} ${it.file_in_repo} ${it.local_subdir} ${getDisplayCategory(it)}`.toLowerCase();
          return hay.includes(q);
        });

        lastRendered = toRender;

        toRender.forEach(it => {
          const row = document.createElement("div");
          row.className = "hfld-row";

          const fill = document.createElement("div");
          fill.className = "hfld-fill";

          const cb = document.createElement("input");
          cb.type = "checkbox";

          const cell = document.createElement("div");
          cell.className = "hfld-cell";

          const lab = document.createElement("div");
          lab.className = "hfld-lab";
          lab.style.userSelect = "text";
          // Show only file name and destination folder
          const baseName = (it.file_in_repo || "").split("/").pop() || it.file_in_repo;
          lab.textContent = `${baseName} → ${it.local_subdir}`;
          // Keep full info as tooltip (does not affect search)
          lab.title = `${it.repo_id}, ${it.file_in_repo}, ${it.local_subdir}`;

          const detail = document.createElement("div");
          detail.className = "hfld-detail";
          detail.textContent = "";

          cell.append(lab, detail);

          const timeEl = document.createElement("div");
          timeEl.className = "hfld-time";
          timeEl.textContent = "";

          row.append(fill, cb, cell, timeEl);
          list.appendChild(row);
          it.el = row; it.cb = cb; it.timeEl = timeEl; it.lab = lab;
          it.detail = detail; it.fill = fill;
        });
      };

      const readList = async () => {
        const p = (pathInput.value || "").trim();
        this.properties.list_path = p;
        setMsg("Reading list…");
        try {
          const resp = await api.fetchApi(`/hf_list/read?path=${encodeURIComponent(p)}`);
          const data = await resp.json();
          if (!resp.ok || !data.ok) throw new Error(data?.error || `HTTP ${resp.status}`);

          // Normalize items; ensure category exists (fallback to 'Misc' if server didn't supply)
          items = Array.isArray(data.items) ? data.items.map(it => ({
            ...it,
            category: (typeof it.category === "string" && it.category.trim()) ? it.category.trim() : FALLBACK_CATEGORY
          })) : [];

          buildCategoryOptions();
          render();

          const skipped = Number.isFinite(data.skipped) ? data.skipped : 0;
          if (skipped > 0) {
            setMsg(`Loaded ${items.length} item(s) from ${data.file}. Skipped ${skipped} malformed line(s).`);
          } else {
            setMsg(`Loaded ${items.length} item(s) from ${data.file}.`);
          }
        } catch (e) {
          items = [];
          buildCategoryOptions();
          render();
          setMsg(e?.message || "Failed to read list.", true);
        }
      };

      const refreshList = async () => {
        const p = (pathInput.value || "").trim() || "download_list.txt";
        setMsg("Refreshing list from internet…");
        btnRefresh.disabled = true;
        try {
          const resp = await api.fetchApi("/hf_list/refresh", {
            method: "POST",
            body: JSON.stringify({ path: p })
          });
          const data = await resp.json();
          if (!resp.ok || !data.ok) throw new Error(data?.error || `HTTP ${resp.status}`);

          setMsg(`Refreshed from ${data.url} → ${data.file}. Loading…`);
          // Immediately read after successful refresh
          await readList();
        } catch (e) {
          setMsg(e?.message || "Refresh failed.", true);
        } finally {
          btnRefresh.disabled = false;
        }
      };

      const selectAll = () => lastRendered.forEach(it => it.cb && (it.cb.checked = true));
      const clearSel  = () => lastRendered.forEach(it => it.cb && (it.cb.checked = false));

      // Refresh node definitions (equivalent to pressing "R")
      const refreshComfy = async () => {
        btnPull.disabled = true;
        setMsg("Refreshing node definitions…");
        try {
          // Preferred API (matches the "R" hotkey behavior)
          if (typeof api.refreshNodeDefs === "function") {
            await api.refreshNodeDefs();
          }
          // Some forks expose it on the app object
          else if (typeof app.refreshNodes === "function") {
            const res = app.refreshNodes();
            if (res && typeof res.then === "function") await res;
          }
          // Last resort: simulate an "R" key press
          else {
            const ev = new KeyboardEvent("keydown", {
              key: "r",
              code: "KeyR",
              keyCode: 82,
              which: 82,
              bubbles: true
            });
            document.dispatchEvent(ev);
          }

          // Force UI to redraw after defs refresh
          if (app?.graph && typeof app.graph.setDirtyCanvas === "function") {
            app.graph.setDirtyCanvas(true, true);
          }
          setMsg("Node definitions refreshed.");
        } catch (err) {
          console.error("Node refresh failed:", err);
          setMsg(err?.message || "Node refresh failed.", true);
        } finally {
          btnPull.disabled = false;
        }
      };

      const updateRow = (it, d) => {
        if (!it?.el) return;
        const total = Number(d.total) || 0;
        const done = Number(d.downloaded) || 0;
        if (it.timeEl) it.timeEl.textContent = fmtTime((d.elapsed || 0) * 1000);
        if (total > 0) {
          it.el.classList.remove("indet");
          const pct = Math.max(0, Math.min(1, done / total));
          if (it.fill) it.fill.style.width = (pct * 100).toFixed(1) + "%";
          if (it.detail) it.detail.textContent =
            `${fmtSpeed(d.speed)} · ${fmtBytes(done)} / ${fmtBytes(total)} · ${fmtBytes(total - done)} left`;
        } else {
          it.el.classList.add("indet");
          if (it.detail) it.detail.textContent = `${fmtSpeed(d.speed)} · ${fmtBytes(done)}`;
        }
      };

      // Start a server-side download, then poll progress until done/error.
      // Polling (and the server download) keep running even if the node loses
      // focus / scrolls out of view; on return the UI reflects current state.
      const downloadOne = (it) => new Promise(async (resolve) => {
        if (!it?.el) return resolve({ ok:false, error:"Bad item" });
        if (it.timer) { clearInterval(it.timer); it.timer = null; }
        it.el.classList.remove("done", "error");
        it.el.classList.add("downloading", "indet");
        it.el.title = ""; if (it.lab) it.lab.title = "";
        if (it.fill) it.fill.style.width = "0%";
        if (it.detail) it.detail.textContent = "Starting…";
        if (it.timeEl) it.timeEl.textContent = "";

        let gid;
        try {
          const resp = await api.fetchApi("/hf_list/download", {
            method: "POST",
            body: JSON.stringify({
              repo_id: it.repo_id,
              file_in_repo: it.file_in_repo,
              local_subdir: it.local_subdir
            })
          });
          const data = await resp.json();
          if (!resp.ok || !data.ok || !data.gid) throw new Error(data?.error || `HTTP ${resp.status}`);
          gid = data.gid;
        } catch (e) {
          it.el.classList.remove("downloading", "indet");
          it.el.classList.add("error");
          const errMsg = e?.message || "Failed to start";
          if (it.detail) it.detail.textContent = errMsg;
          if (it.lab) it.lab.title = errMsg;
          return resolve({ ok:false, error: errMsg });
        }
        it.gid = gid;

        const poll = async () => {
          let d;
          try {
            const r = await api.fetchApi(`/hf_list/progress?gid=${encodeURIComponent(gid)}`);
            d = await r.json();
            if (!r.ok || !d.ok) throw new Error(d?.error || `HTTP ${r.status}`);
          } catch (e) {
            return; // transient; keep polling
          }
          updateRow(it, d);
          if (d.state === "done") {
            clearInterval(it.timer); it.timer = null;
            it.el.classList.remove("downloading", "indet");
            it.el.classList.add("done");
            if (it.fill) it.fill.style.width = "100%";
            const size = Number(d.total) || Number(d.downloaded) || 0;
            if (it.detail) it.detail.textContent = `Done · ${fmtBytes(size)}`;
            resolve({ ok:true, dst: d.dst, ms: (d.elapsed || 0) * 1000 });
          } else if (d.state === "error") {
            clearInterval(it.timer); it.timer = null;
            it.el.classList.remove("downloading", "indet");
            it.el.classList.add("error");
            if (it.fill) it.fill.style.width = "0%";
            const errMsg = d.error || "Download failed";
            if (it.detail) it.detail.textContent = errMsg;
            if (it.lab) it.lab.title = errMsg;
            resolve({ ok:false, error: errMsg, ms: (d.elapsed || 0) * 1000 });
          }
        };
        it.timer = setInterval(poll, 400);
        poll();
      });

      const downloadSelected = async () => {
        const chosen = lastRendered.filter(it => it.cb && it.cb.checked);
        if (!chosen.length) { setMsg("Nothing selected."); return; }
        setMsg(`Downloading ${chosen.length} item(s)…`);
        // Disable everything that could re-render the list (which would detach
        // the rows whose intervals are tracking live progress).
        const locked = [btnDownload, btnRead, btnRefresh, btnPull, btnSelectAll,
                        btnClear, pathInput, searchInput, selCategory];
        locked.forEach(el => el.disabled = true);
        let okCount = 0, errCount = 0;
        const batchStart = performance.now();

        for (const it of chosen) {
          const res = await downloadOne(it);
          if (res.ok) okCount += 1; else errCount += 1;
        }

        const totalMs = performance.now() - batchStart;
        locked.forEach(el => el.disabled = false);
        if (errCount) setMsg(`Finished with ${okCount} success, ${errCount} error(s) in ${fmtTime(totalMs)}. Hover rows for details.`, true);
        else setMsg(`All ${okCount} item(s) downloaded in ${fmtTime(totalMs)}.`);
      };

      // Wire up
      btnRead.addEventListener("click", readList);
      btnRefresh.addEventListener("click", refreshList);
      btnSelectAll.addEventListener("click", selectAll);
      btnClear.addEventListener("click", clearSel);
      btnDownload.addEventListener("click", downloadSelected);
      btnPull.addEventListener("click", refreshComfy);

      // Persist category selection and re-render on change
      selCategory.addEventListener("change", () => {
        this.properties.category_filter = selCategory.value || ALL;
        render();
      });

      // Persist search query and re-render on change
      searchInput.addEventListener("input", () => {
        this.properties.search_query = (searchInput.value || "");
        render();
      });

      // Node canvas sizing
      this.size = [570, 500];

      // Initialize dropdown with default until list is read
      selCategory.innerHTML = "";
      const defOpt = document.createElement("option");
      defOpt.value = "All";
      defOpt.textContent = "All";
      selCategory.appendChild(defOpt);
      selCategory.value = this.properties.category_filter || "All";

      // Stop any in-flight poll loops if the node is deleted mid-download.
      const prevOnRemoved = this.onRemoved;
      this.onRemoved = function () {
        try { items.forEach(it => it.timer && clearInterval(it.timer)); } catch (e) {}
        return prevOnRemoved ? prevOnRemoved.apply(this, arguments) : undefined;
      };

      return r;
    };
  },
});