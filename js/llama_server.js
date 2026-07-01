// js/llama_server.js
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

(function injectOnce() {
  if (document.getElementById("az-ll-style")) return;
  const css = document.createElement("style");
  css.id = "az-ll-style";
  css.textContent = `
  .azll-ui { display:flex; flex-direction:column; gap:7px; width:100%; box-sizing:border-box; padding:2px 0; }
  .azll-row { display:flex; align-items:center; gap:7px; width:100%; }
  .azll-light { flex:0 0 auto; width:11px; height:11px; border-radius:50%; background:#e0454a;
                box-shadow:0 0 6px rgba(224,69,74,.9); transition:opacity .18s ease; }
  .azll-light.ok { background:#3ddc84; box-shadow:0 0 9px rgba(61,220,132,.95); }
  .azll-light.dim { opacity:.2; }
  .azll-sel { flex:1 1 auto; min-width:0; height:26px; border-radius:6px; padding:0 8px; font-size:12px;
              border:1px solid var(--border-color,#333); background:var(--comfy-input-bg,#1b1f2a);
              color:var(--input-text,#dfe8f7); box-sizing:border-box; }
  .azll-sel:disabled { opacity:.5; }
  .azll-btn { flex:0 0 auto; height:26px; padding:0 12px; border-radius:6px; cursor:pointer;
              font-size:12px; font-weight:600; border:1px solid #3a6df0; color:#fff;
              background:linear-gradient(180deg,#4f8bff,#2e63ec); }
  .azll-btn:hover { filter:brightness(1.08); }
  .azll-btn:disabled { opacity:.55; cursor:default; }
  .azll-btn.stop { border-color:#e0454a; background:linear-gradient(180deg,#ff5a5f,#d33a3f); }
  .azll-btn.mini { padding:0 9px; }
  .azll-console { width:100%; box-sizing:border-box; height:96px; overflow-y:auto; overflow-x:hidden;
                  font-family:ui-monospace,Consolas,monospace; font-size:7px; line-height:1.35;
                  padding:5px 7px; border-radius:6px; border:1px solid var(--border-color,#2b3242);
                  background:#0d1017; color:#8fa4bd; white-space:pre-wrap; word-break:break-all; }
  .azll-console:empty::before { content:"llama-server console…"; color:#3f4757; }
  .azll-preview { width:100%; box-sizing:border-box; height:180px; overflow-y:auto; overflow-x:hidden;
                  font-size:13px; line-height:1.55; padding:10px 12px; border-radius:8px;
                  border:1px solid var(--border-color,#2b3242); background:var(--comfy-input-bg,#171b24);
                  color:#dbe6f7; white-space:pre-wrap; word-break:break-word; }
  .azll-preview:empty::before { content:"Output will stream here…"; color:#5d6678; }
  .azll-preview.err { color:#ff8a8a; }
  .azll-preview .rw-anim { display:inline-block; animation:azllWord .34s cubic-bezier(0,0,0,1) both; }
  @keyframes azllWord { from { opacity:0; filter:blur(5px); transform:translateX(-3px); }
                        to   { opacity:1; filter:blur(0);   transform:translateX(0); } }
  `;
  document.head.appendChild(css);
})();

const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};

app.registerExtension({
  name: "comfyui.az_llama_server",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "AzLlamaEnhancer") return;

    const orig = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = orig ? orig.apply(this, arguments) : undefined;
      this.serialize_widgets = true;

      const findW = (n) => this.widgets?.find((w) => w.name === n);
      const wDir = findW("models_dir");
      const wUrl = findW("binary_url");
      const wFlags = findW("launch_flags");
      const wDev = findW("device");
      const wPort = findW("port");
      const wModel = findW("llm_model");

      // ---- UI ----
      const ui = el("div", "azll-ui");

      const dlRow = el("div", "azll-row");
      const dlLight = el("span", "azll-light");
      const dlBtn = el("button", "azll-btn", "Download Binary");
      dlRow.append(dlLight, dlBtn);

      const mRow = el("div", "azll-row");
      const sel = el("select", "azll-sel");
      sel.disabled = true;
      const ph = el("option", null, "— Refresh to list models —");
      ph.value = "";
      sel.append(ph);
      if (wModel?.value) {
        const o = el("option", null, wModel.value);
        o.value = wModel.value; o.selected = true; sel.append(o);
      }
      const refBtn = el("button", "azll-btn mini", "↻");
      mRow.append(sel, refBtn);

      const lRow = el("div", "azll-row");
      const runLight = el("span", "azll-light");
      const runBtn = el("button", "azll-btn", "Launch");
      lRow.append(runLight, runBtn);

      const consoleBox = el("div", "azll-console");
      const preview = el("div", "azll-preview");
      ui.append(dlRow, mRow, lRow, consoleBox, preview);

      // serialize=false as a real property (core-node pattern); DOM widget stays
      // last in this.widgets so LiteGraph's sparse-save / compacted-load can't
      // shift the other widget values into the wrong fields.
      const domW = this.addDOMWidget("azll_ui", "Local llama", ui);
      domW.serialize = false;
      domW.computeSize = () => [this.size[0] - 20, 452];

      // ---- console append (ring-buffered by CSS scroll) ----
      const logLine = (t) => {
        consoleBox.appendChild(document.createTextNode(t + "\n"));
        while (consoleBox.childNodes.length > 600) consoleBox.removeChild(consoleBox.firstChild);
        consoleBox.scrollTop = consoleBox.scrollHeight;
      };

      // ---- preview word reveal (same behavior as remote enhancer) ----
      let stick = true, tail = "";
      preview.addEventListener("scroll", () => {
        const gap = preview.scrollHeight - preview.scrollTop - preview.clientHeight;
        stick = gap <= 6;
      }, { passive: true });
      const autoscroll = () => { if (stick) preview.scrollTop = preview.scrollHeight; };
      const reset = () => { tail = ""; preview.classList.remove("err"); preview.innerHTML = ""; stick = true; };
      const feed = (text) => {
        const parts = (tail + text).split(/(\s+)/);
        tail = "";
        for (let i = 0; i < parts.length; i++) {
          const p = parts[i];
          if (p === "") continue;
          if (/^\s+$/.test(p)) { preview.appendChild(document.createTextNode(p)); continue; }
          if (i === parts.length - 1) tail = p;
          else preview.appendChild(el("span", "rw-anim", p));
        }
        autoscroll();
      };
      const flush = () => { if (tail.trim()) preview.appendChild(el("span", "rw-anim", tail)); tail = ""; autoscroll(); };

      // ---- run/stop button state ----
      let running = false;
      const setRunning = (on, model) => {
        running = on;
        runLight.classList.toggle("ok", on);
        runBtn.textContent = on ? "Stop" : "Launch";
        runBtn.classList.toggle("stop", on);
        if (on && model && wModel) { wModel.value = model; }
      };

      // ---- actions ----
      const payload = () => ({
        models_dir: (wDir?.value || "").trim(),
        binary_url: (wUrl?.value || "").trim(),
        launch_flags: (wFlags?.value || "").trim(),
        device: (wDev?.value || "default"),
        port: parseInt(wPort?.value || "18081", 10),
      });

      const refresh = async () => {
        try {
          const resp = await api.fetchApi("/az_llama/models", {
            method: "POST", body: JSON.stringify({ models_dir: payload().models_dir }) });
          const d = await resp.json();
          const prev = wModel?.value || "";
          sel.innerHTML = "";
          if (!d.models?.length) { const o = el("option", null, "— no .gguf found —"); o.value = ""; sel.append(o); sel.disabled = true; return; }
          d.models.forEach((m) => { const o = el("option", null, m); o.value = m; sel.append(o); });
          sel.value = d.models.includes(prev) ? prev : d.models[0];
          sel.disabled = false;
          if (wModel) wModel.value = sel.value;
          this.setDirtyCanvas(true, true);
        } catch (e) { logLine("refresh error: " + e); }
      };

      const download = async () => {
        dlBtn.disabled = true; dlLight.classList.remove("ok");
        try { await api.fetchApi("/az_llama/download", { method: "POST", body: JSON.stringify(payload()) }); }
        catch (e) { logLine("download error: " + e); dlBtn.disabled = false; }
      };

      const launch = async () => {
        runBtn.disabled = true;
        try {
          await api.fetchApi("/az_llama/launch", {
            method: "POST", body: JSON.stringify({ ...payload(), model: sel.value }) });
        } catch (e) { logLine("launch error: " + e); runBtn.disabled = false; }
      };

      const stop = async () => {
        try { await api.fetchApi("/az_llama/stop", {
          method: "POST", body: JSON.stringify({ id: String(this.id), kill_engine: true }) }); }
        catch (e) {}
        try { await api.interrupt(); } catch (e) {}
      };

      sel.addEventListener("change", () => { if (wModel) wModel.value = sel.value; this.setDirtyCanvas(true, true); });
      dlBtn.addEventListener("click", download);
      refBtn.addEventListener("click", refresh);
      runBtn.addEventListener("click", () => { if (running) stop(); else launch(); });

      // ---- websocket ----
      const handler = (ev) => {
        const d = ev.detail || {};
        if (d.chan === "console") { if (d.line) logLine(d.line); return; }
        if (d.chan === "download") {
          dlBtn.disabled = false;
          if (d.ok) dlLight.classList.add("ok"); else logLine("download: " + (d.error || "failed"));
          return;
        }
        if (d.chan === "launch") {
          runBtn.disabled = false;
          if (d.ok) setRunning(true, d.model); else { setRunning(false); logLine("launch: " + (d.error || "failed")); }
          return;
        }
        if (d.chan === "gen") {
          if (d.status === "start") reset();
          else if (d.status === "delta") { if (d.text) feed(d.text); }
          else if (d.status === "done") flush();
          else if (d.status === "error") { preview.classList.add("err"); preview.textContent = d.error || "Error"; }
        }
      };
      api.addEventListener("az_llama", handler);

      // Robust widget persistence: LiteGraph's positional widgets_values restore
      // shifts values into the wrong fields when force_input / control_after_generate
      // / DOM widgets are mixed. Save by name and re-apply by name after configure.
      const prevSerialize = this.onSerialize;
      this.onSerialize = function (o) {
        if (prevSerialize) prevSerialize.apply(this, arguments);
        o.az_wv = {};
        for (const w of this.widgets || [])
          if (w && w.name && w.serialize !== false) o.az_wv[w.name] = w.value;
      };
      const prevConfigure = this.onConfigure;
      this.onConfigure = function (o) {
        if (prevConfigure) prevConfigure.apply(this, arguments);
        if (o && o.az_wv)
          for (const w of this.widgets || [])
            if (w && w.name && w.name in o.az_wv) w.value = o.az_wv[w.name];
        if (wModel && sel && wModel.value) {
          if (![...sel.options].some((op) => op.value === wModel.value)) {
            const o2 = el("option", null, wModel.value); o2.value = wModel.value; sel.append(o2);
          }
          sel.value = wModel.value;
        }
      };

      // ---- re-sync to an already-running server after reload ----
      (async () => {
        try {
          const resp = await api.fetchApi("/az_llama/status", { method: "POST", body: "{}" });
          const d = await resp.json();
          (d.log || []).slice(-80).forEach(logLine);
          if (d.running) setRunning(true, d.model);
        } catch (e) {}
      })();

      const prevOnRemoved = this.onRemoved;
      this.onRemoved = function () {
        api.removeEventListener("az_llama", handler);
        return prevOnRemoved ? prevOnRemoved.apply(this, arguments) : undefined;
      };

      return r;
    };
  },
});
