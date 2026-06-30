// js/prompt_enhancer.js
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

(function injectOnce() {
  if (document.getElementById("az-pe-style")) return;
  const css = document.createElement("style");
  css.id = "az-pe-style";
  css.textContent = `
  .azpe-ui { display:flex; flex-direction:column; gap:8px; width:100%; box-sizing:border-box; padding:2px 0; }
  .azpe-row { display:flex; align-items:center; gap:8px; width:100%; }
  .azpe-light { flex:0 0 auto; width:12px; height:12px; border-radius:50%; background:#e0454a;
                box-shadow:0 0 6px rgba(224,69,74,.9);
                transition:opacity .18s ease, transform .18s ease; }
  .azpe-light.ok { background:#3ddc84; box-shadow:0 0 9px rgba(61,220,132,.95); }
  .azpe-light.dim { opacity:.18; transform:scale(.7); }
  .azpe-sel { flex:1 1 auto; min-width:0; height:28px; border-radius:7px; padding:0 9px; font-size:12px;
              border:1px solid var(--border-color,#333); background:var(--comfy-input-bg,#1b1f2a);
              color:var(--input-text,#dfe8f7); box-sizing:border-box; }
  .azpe-sel:disabled { opacity:.5; }
  .azpe-btn { flex:0 0 auto; height:28px; padding:0 14px; border-radius:7px; cursor:pointer;
              font-size:12px; font-weight:600; border:1px solid #3a6df0; color:#fff;
              background:linear-gradient(180deg,#4f8bff,#2e63ec); }
  .azpe-btn:hover { filter:brightness(1.08); }
  .azpe-btn:disabled { opacity:.55; cursor:default; }
  .azpe-preview { width:100%; box-sizing:border-box; height:220px; overflow-y:auto; overflow-x:hidden;
                  font-size:13px; line-height:1.55; padding:10px 12px; border-radius:8px;
                  border:1px solid var(--border-color,#2b3242); background:var(--comfy-input-bg,#171b24);
                  color:#dbe6f7; white-space:pre-wrap; word-break:break-word; }
  .azpe-preview:empty::before { content:"Output will stream here…"; color:#5d6678; }
  /* word-by-word reveal (copied from the webchat) */
  .azpe-preview .rw { display:inline; }
  .azpe-preview .rw-anim { display:inline-block;
                  animation:azpeWord .34s cubic-bezier(0,0,0,1) both; }
  @keyframes azpeWord { from { opacity:0; filter:blur(5px); transform:translateX(-3px); }
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

      // ---- UI: control row + streaming preview ----
      const ui = el("div", "azpe-ui");
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

      const preview = el("div", "azpe-preview");
      ui.append(row, preview);

      const domW = this.addDOMWidget("azpe_ui", "Prompt Enhancer", ui, { serialize: false });
      domW.serializeValue = () => undefined;
      domW.computeSize = () => [this.size[0] - 20, 272];

      const di = this.widgets.indexOf(domW);
      if (di >= 0) this.widgets.splice(di, 1);
      const li = wLlm ? this.widgets.indexOf(wLlm) : -1;
      if (li >= 0) this.widgets.splice(li + 1, 0, domW);
      else this.widgets.unshift(domW);

      // ---- light: red(off) / green(ok) / green slow-blink(busy) ----
      // Blink is a self-driven 300ms-gated loop. `injob` rejects overlapping
      // toggles, so calling lightBusy() on every delta cannot speed it up.
      let blinking = false;   // desired state: should it be pulsing?
      let injob = false;      // a phase toggle is in progress (the gate)
      let phaseOn = false;    // current dim phase
      let blinkTimer = null;

      const blinkLoop = () => {
        blinkTimer = null;
        if (injob) return;            // a toggle already pending -> reject
        injob = true;
        if (!blinking) {              // stopped: settle to solid, release gate
          phaseOn = false;
          light.classList.remove("dim");
          injob = false;
          return;
        }
        phaseOn = !phaseOn;
        light.classList.toggle("dim", phaseOn);
        // hold this phase for ~300ms, then release the gate and schedule next
        blinkTimer = setTimeout(() => { injob = false; blinkLoop(); }, 300);
      };

      const lightBusy = () => {
        light.classList.add("ok");
        if (blinking) return;         // already pulsing -> ignore repeat calls
        blinking = true;
        if (!injob && !blinkTimer) blinkLoop();
      };
      const stopBlink = () => {
        blinking = false;
        if (blinkTimer) { clearTimeout(blinkTimer); blinkTimer = null; }
        injob = false;
        phaseOn = false;
        light.classList.remove("dim");
      };
      const lightOk  = () => { stopBlink(); light.classList.add("ok"); };
      const lightErr = () => { stopBlink(); light.classList.remove("ok"); };

      // ---- gentle eased autoscroll (webchat-style, unconditional) ----
      let _anim = false;
      const autoscroll = () => {
        if (_anim) return;
        _anim = true;
        const step = () => {
          const target = preview.scrollHeight - preview.clientHeight;
          const diff = target - preview.scrollTop;
          if (diff < 0.5) { preview.scrollTop = target; _anim = false; return; }
          preview.scrollTop += diff * 0.12;       // soft continuous glide
          requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
      };

      // ---- word-by-word reveal: re-render full text, animate only new words ----
      let acc = "";
      let revealed = 0;
      const render = () => {
        const frag = document.createDocumentFragment();
        let idx = 0;
        for (const part of acc.split(/(\s+)/)) {
          if (part === "") continue;
          if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); continue; }
          const span = el("span", idx >= revealed ? "rw rw-anim" : "rw", part);
          idx++;
          frag.appendChild(span);
        }
        preview.innerHTML = "";
        preview.appendChild(frag);
        revealed = idx;
      };
      const reset = () => { acc = ""; revealed = 0; preview.innerHTML = ""; };

      // ---- connect ----
      sel.addEventListener("change", () => { if (wLlm) wLlm.value = sel.value; this.setDirtyCanvas(true, true); });
      const connect = async () => {
        btn.disabled = true;
        try {
          const resp = await api.fetchApi("/az_prompt_enhancer/models", {
            method: "POST",
            body: JSON.stringify({ url: (wUrl?.value || "").trim(), token: (wTok?.value || "").trim() }),
          });
          const d = await resp.json();
          if (!d.ok || !Array.isArray(d.models) || !d.models.length) throw new Error(d.error || "No models");
          const prevSel = wLlm?.value || "";
          sel.innerHTML = "";
          d.models.forEach((m) => { const o = el("option", null, m); o.value = m; sel.append(o); });
          sel.value = d.models.includes(prevSel) ? prevSel : d.models[0];
          sel.disabled = false;
          if (wLlm) wLlm.value = sel.value;
          lightOk();
          this.setDirtyCanvas(true, true);
        } catch (e) {
          lightErr();
        } finally {
          btn.disabled = false;
        }
      };
      btn.addEventListener("click", connect);

      // ---- streaming from execute() ----
      const handler = (ev) => {
        const d = ev.detail || {};
        if (String(d.id) !== String(this.id)) return;
        if (d.status === "start") { reset(); lightBusy(); }
        else if (d.status === "delta") {
          if (typeof d.text === "string" && d.text) {
            acc += d.text;
            lightBusy();           // keep it blinking even if 'start' was missed
            render();
            autoscroll();
          }
        }
        else if (d.status === "done") { lightOk(); autoscroll(); }
        else if (d.status === "error") { lightErr(); }
      };
      api.addEventListener("az_prompt_enhancer", handler);

      // fallback so the light blinks even if the custom messages are delayed
      const parseId = (detail) => {
        if (detail == null) return null;
        const id = (typeof detail === "object") ? (detail.node ?? detail.id ?? detail.node_id) : detail;
        return id == null ? null : String(id);
      };
      const onExec = (ev) => { if (parseId(ev.detail) === String(this.id)) lightBusy(); };
      const onEnd  = () => stopBlink();
      api.addEventListener("executing", onExec);
      api.addEventListener("execution_success", onEnd);
      api.addEventListener("execution_error", onEnd);
      api.addEventListener("execution_interrupted", onEnd);

      const prevOnRemoved = this.onRemoved;
      this.onRemoved = function () {
        if (blinkTimer) { clearTimeout(blinkTimer); blinkTimer = null; }
        api.removeEventListener("az_prompt_enhancer", handler);
        api.removeEventListener("executing", onExec);
        api.removeEventListener("execution_success", onEnd);
        api.removeEventListener("execution_error", onEnd);
        api.removeEventListener("execution_interrupted", onEnd);
        return prevOnRemoved ? prevOnRemoved.apply(this, arguments) : undefined;
      };

      return r;
    };
  },
});
