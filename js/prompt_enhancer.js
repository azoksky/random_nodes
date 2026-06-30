// js/prompt_enhancer.js
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

(function injectOnce() {
  if (document.getElementById("az-pe-style")) return;
  const css = document.createElement("style");
  css.id = "az-pe-style";
  css.textContent = `
  .azpe-ctrl { display:flex; flex-direction:column; gap:6px; width:100%; box-sizing:border-box; padding:2px 0; }
  .azpe-row { display:flex; align-items:center; gap:8px; width:100%; }
  .azpe-light { flex:0 0 auto; width:11px; height:11px; border-radius:50%; background:#e0454a;
                box-shadow:0 0 6px rgba(224,69,74,.9); transition:all .25s; }
  .azpe-light.ok { background:#3ddc84; box-shadow:0 0 8px rgba(61,220,132,.95); }
  .azpe-light.busy { background:#f0b429; box-shadow:0 0 8px rgba(240,180,41,.95);
                     animation:azpePulse .8s ease-in-out infinite; }
  @keyframes azpePulse { 0%,100%{opacity:1;} 50%{opacity:.35;} }
  .azpe-sel { flex:1 1 auto; min-width:0; height:28px; border-radius:7px; padding:0 9px; font-size:12px;
              border:1px solid var(--border-color,#333); background:var(--comfy-input-bg,#1b1f2a);
              color:var(--input-text,#dfe8f7); box-sizing:border-box; }
  .azpe-sel:disabled { opacity:.5; }
  .azpe-btn { flex:0 0 auto; height:28px; padding:0 14px; border-radius:7px; cursor:pointer;
              font-size:12px; font-weight:600; border:1px solid #3a6df0; color:#fff;
              background:linear-gradient(180deg,#4f8bff,#2e63ec); }
  .azpe-btn:hover { filter:brightness(1.08); }
  .azpe-btn:disabled { opacity:.55; cursor:default; }
  .azpe-status { font-size:11px; color:var(--descrip-text,#9ab); white-space:nowrap;
                 overflow:hidden; text-overflow:ellipsis; }
  .azpe-status.error { color:var(--error-text,#ff8a8a); }
  .azpe-bar { position:relative; height:6px; border-radius:4px; overflow:hidden;
              background:rgba(255,255,255,0.06); border:1px solid var(--border-color,#2b3242); }
  .azpe-bar .azpe-fill { position:absolute; top:0; bottom:0; left:-35%; width:35%; opacity:0;
              background:linear-gradient(90deg, rgba(60,120,255,0.1), rgba(90,160,255,0.95), rgba(60,120,255,0.1)); }
  .azpe-bar.active .azpe-fill { opacity:1; animation:azpeSlide 1.05s ease-in-out infinite; }
  @keyframes azpeSlide { 0%{left:-35%;} 100%{left:100%;} }
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
  name: "comfyui.az_prompt_enhancer",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "AzPromptEnhancer") return;

    const orig = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = orig ? orig.apply(this, arguments) : undefined;
      this.serialize_widgets = true;

      const findW = (n) => this.widgets?.find((w) => w.name === n);
      const wUrl = findW("llama_url");
      const wTok = findW("llama_token");
      const wLlm = findW("llm_model");

      // ---- slim control strip (light + model picker + Connect; progress bar) ----
      const ctrl = el("div", "azpe-ctrl");
      const row = el("div", "azpe-row");
      const light = el("span", "azpe-light");
      const sel = el("select", "azpe-sel");
      sel.disabled = true;
      const ph = el("option", null, "— Connect to load models —");
      ph.value = "";
      sel.append(ph);
      if (wLlm?.value) {
        const o = el("option", null, wLlm.value);
        o.value = wLlm.value; o.selected = true;
        sel.append(o);
      }
      const btn = el("button", "azpe-btn", "Connect");
      row.append(light, sel, btn);

      const status = el("div", "azpe-status", "Disconnected");
      const bar = el("div", "azpe-bar");
      bar.append(el("div", "azpe-fill"));
      ctrl.append(row, status, bar);

      const domW = this.addDOMWidget("azpe_ctrl", "Backend", ctrl, { serialize: false });
      domW.serializeValue = () => undefined;
      domW.computeSize = () => [this.size[0] - 20, 64];

      // place the strip just under the llm_model widget
      const di = this.widgets.indexOf(domW);
      if (di >= 0) this.widgets.splice(di, 1);
      const li = wLlm ? this.widgets.indexOf(wLlm) : -1;
      if (li >= 0) this.widgets.splice(li + 1, 0, domW);
      else this.widgets.unshift(domW);

      // ---- helpers ----
      const setLight = (s) => {
        light.classList.remove("ok", "busy");
        if (s === "ok") light.classList.add("ok");
        else if (s === "busy") light.classList.add("busy");
      };
      const setStatus = (t, isErr = false) => {
        status.textContent = t || "";
        status.classList.toggle("error", !!isErr);
      };
      const setBar = (on) => bar.classList.toggle("active", !!on);

      sel.addEventListener("change", () => {
        if (wLlm) { wLlm.value = sel.value; }
        this.setDirtyCanvas(true, true);
      });

      const connect = async () => {
        const url = (wUrl?.value || "").trim();
        const tok = (wTok?.value || "").trim();
        if (!url || !tok) { setStatus("Set Server URL and API Token first.", true); setLight("off"); return; }
        btn.disabled = true;
        setLight("busy"); setStatus("Fetching models…");
        try {
          const resp = await api.fetchApi("/az_prompt_enhancer/models", {
            method: "POST",
            body: JSON.stringify({ url, token: tok }),
          });
          const d = await resp.json();
          if (!d.ok || !Array.isArray(d.models) || !d.models.length) throw new Error(d.error || "No models returned.");
          const prevSel = wLlm?.value || "";
          sel.innerHTML = "";
          d.models.forEach((m) => { const o = el("option", null, m); o.value = m; sel.append(o); });
          sel.value = d.models.includes(prevSel) ? prevSel : d.models[0];
          sel.disabled = false;
          if (wLlm) wLlm.value = sel.value;
          setLight("ok"); setStatus(`Connected · ${d.models.length} model(s)`);
          this.setDirtyCanvas(true, true);
        } catch (e) {
          setLight("off"); setStatus(e?.message || "Connection failed.", true);
        } finally {
          btn.disabled = false;
        }
      };
      btn.addEventListener("click", connect);

      // ---- live progress from execute() ----
      const handler = (ev) => {
        const d = ev.detail || {};
        if (String(d.id) !== String(this.id)) return;
        if (d.status === "start") { setBar(true); setStatus("Enhancing prompt…"); }
        else if (d.status === "done") { setBar(false); setStatus("Done."); }
        else if (d.status === "error") { setBar(false); setStatus(d.error || "Error", true); }
      };
      api.addEventListener("az_prompt_enhancer", handler);

      const prevOnRemoved = this.onRemoved;
      this.onRemoved = function () {
        api.removeEventListener("az_prompt_enhancer", handler);
        return prevOnRemoved ? prevOnRemoved.apply(this, arguments) : undefined;
      };

      return r;
    };
  },
});
