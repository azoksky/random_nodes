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
  .azpe-preview.err { color:#ff8a8a; }
  /* word-by-word reveal (from the webchat) — each completed word animates once */
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

      // ---- UI ----
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

      // ---- base light colour (connection state) ----
      const lightOk  = () => light.classList.add("ok");
      const lightErr = () => light.classList.remove("ok");

      // ---- gated 300ms blink (overlay), driven purely by app.runningNodeId ----
      let blinking = false, injob = false, phaseOn = false, blinkTimer = null;
      const blinkLoop = () => {
        blinkTimer = null;
        if (injob) return;
        injob = true;
        if (!blinking) { phaseOn = false; light.classList.remove("dim"); injob = false; return; }
        phaseOn = !phaseOn;
        light.classList.toggle("dim", phaseOn);
        blinkTimer = setTimeout(() => { injob = false; blinkLoop(); }, 300);
      };
      const startBlink = () => {
        if (blinking) return;
        blinking = true;
        if (!injob && !blinkTimer) blinkLoop();
      };
      const stopBlink = () => {
        if (!blinking && !blinkTimer && !light.classList.contains("dim")) return;
        blinking = false;
        if (blinkTimer) { clearTimeout(blinkTimer); blinkTimer = null; }
        injob = false; phaseOn = false;
        light.classList.remove("dim");
      };
      // single local source of truth: is THIS node the one executing?
      const watch = setInterval(() => {
        const busy = app.runningNodeId != null && String(app.runningNodeId) === String(this.id);
        if (busy) startBlink(); else stopBlink();
      }, 150);

      // ---- autoscroll: glide only while pinned to bottom; releases on scroll-up ----
      let stick = true, lastSetTop = -1, scrolling = false;
      preview.addEventListener("scroll", () => {
        if (Math.abs(preview.scrollTop - lastSetTop) < 2) return; // ignore our own scrolls
        const gap = preview.scrollHeight - preview.scrollTop - preview.clientHeight;
        stick = gap <= 6;
      }, { passive: true });
      const autoscroll = () => {
        if (!stick || scrolling) return;
        scrolling = true;
        const step = () => {
          if (!stick) { scrolling = false; return; }
          const target = preview.scrollHeight - preview.clientHeight;
          const diff = target - preview.scrollTop;
          if (diff < 0.5) { preview.scrollTop = target; lastSetTop = preview.scrollTop; scrolling = false; return; }
          preview.scrollTop += diff * 0.08;        // slow, soft glide
          lastSetTop = preview.scrollTop;
          requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
      };

      // ---- word reveal: append each completed word once; hold the partial tail ----
      let tail = "";
      const reset = () => { tail = ""; preview.classList.remove("err"); preview.innerHTML = ""; stick = true; };
      const feed = (text) => {
        const parts = (tail + text).split(/(\s+)/);
        tail = "";
        for (let i = 0; i < parts.length; i++) {
          const p = parts[i];
          if (p === "") continue;
          if (/^\s+$/.test(p)) { preview.appendChild(document.createTextNode(p)); continue; }
          if (i === parts.length - 1) { tail = p; }       // unfinished word, wait for next delta
          else preview.appendChild(el("span", "rw rw-anim", p));
        }
        autoscroll();
      };
      const flush = () => {
        if (tail.trim()) preview.appendChild(el("span", "rw rw-anim", tail));
        tail = "";
        autoscroll();
      };

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

      // ---- streamed text only (light is independent) ----
      const handler = (ev) => {
        const d = ev.detail || {};
        if (String(d.id) !== String(this.id)) return;
        if (d.status === "start") reset();
        else if (d.status === "delta") { if (typeof d.text === "string" && d.text) feed(d.text); }
        else if (d.status === "done") flush();
        else if (d.status === "error") { preview.classList.add("err"); preview.textContent = d.error || "Error"; }
      };
      api.addEventListener("az_prompt_enhancer", handler);

      const prevOnRemoved = this.onRemoved;
      this.onRemoved = function () {
        clearInterval(watch);
        if (blinkTimer) { clearTimeout(blinkTimer); blinkTimer = null; }
        api.removeEventListener("az_prompt_enhancer", handler);
        return prevOnRemoved ? prevOnRemoved.apply(this, arguments) : undefined;
      };

      return r;
    };
  },
});
