// js/prompt_enhancer.js
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

(function injectOnce() {
  if (document.getElementById("az-pe-style")) return;
  const css = document.createElement("style");
  css.id = "az-pe-style";
  css.textContent = `
  .azpe-card { display:flex; flex-direction:column; gap:8px; width:100%; box-sizing:border-box;
               padding:10px; border-radius:10px;
               background:linear-gradient(160deg, rgba(40,48,66,0.55), rgba(22,26,38,0.55));
               border:1px solid var(--border-color,#2b3242); }
  .azpe-head { display:flex; align-items:center; justify-content:space-between; }
  .azpe-title { font-size:12px; font-weight:700; letter-spacing:.3px; color:var(--input-text,#e6eefc);
                text-transform:uppercase; opacity:.9; }
  .azpe-conn { display:flex; align-items:center; gap:6px; font-size:11px; color:var(--descrip-text,#9ab); }
  .azpe-light { width:11px; height:11px; border-radius:50%; background:#e0454a;
                box-shadow:0 0 6px rgba(224,69,74,.9); transition:all .25s; }
  .azpe-light.ok { background:#3ddc84; box-shadow:0 0 8px rgba(61,220,132,.95); }
  .azpe-light.busy { background:#f0b429; box-shadow:0 0 8px rgba(240,180,41,.95);
                     animation:azpePulse .8s ease-in-out infinite; }
  @keyframes azpePulse { 0%,100%{opacity:1;} 50%{opacity:.35;} }

  .azpe-field { display:flex; flex-direction:column; gap:3px; }
  .azpe-lbl { font-size:10px; text-transform:uppercase; letter-spacing:.4px; color:var(--descrip-text,#8a98ad); }
  .azpe-in, .azpe-sel { height:28px; border-radius:7px; padding:0 9px; font-size:12px;
               border:1px solid var(--border-color,#333); background:var(--comfy-input-bg,#1b1f2a);
               color:var(--input-text,#dfe8f7); box-sizing:border-box; width:100%; }
  .azpe-sel:disabled { opacity:.5; }
  .azpe-row { display:grid; grid-template-columns:1fr auto; gap:8px; align-items:end; }
  .azpe-btn { height:28px; padding:0 14px; border-radius:7px; cursor:pointer; font-size:12px; font-weight:600;
              border:1px solid #3a6df0; color:#fff; white-space:nowrap;
              background:linear-gradient(180deg,#4f8bff,#2e63ec); }
  .azpe-btn:hover { filter:brightness(1.08); }
  .azpe-btn:disabled { opacity:.55; cursor:default; }

  .azpe-bar { position:relative; height:6px; border-radius:4px; overflow:hidden;
              background:rgba(255,255,255,0.06); border:1px solid var(--border-color,#2b3242); }
  .azpe-bar .azpe-fill { position:absolute; top:0; bottom:0; left:-35%; width:35%; opacity:0;
              background:linear-gradient(90deg, rgba(60,120,255,0.1), rgba(90,160,255,0.95), rgba(60,120,255,0.1)); }
  .azpe-bar.active .azpe-fill { opacity:1; animation:azpeSlide 1.05s ease-in-out infinite; }
  @keyframes azpeSlide { 0%{left:-35%;} 100%{left:100%;} }

  .azpe-status { font-size:11px; min-height:14px; color:var(--descrip-text,#9ab); }
  .azpe-status.error { color:var(--error-text,#ff8a8a); }
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
      const hide = (w) => { if (w) w.hidden = true; };

      const wUrl = findW("llama_url");
      const wTok = findW("llama_token");
      const wImg = findW("image_model");
      const wLlm = findW("llm_model");
      [wUrl, wTok, wImg, wLlm].forEach(hide);

      // ---- build the card ----
      const card = el("div", "azpe-card");

      const head = el("div", "azpe-head");
      head.append(el("div", "azpe-title", "LLM Backend"));
      const conn = el("div", "azpe-conn");
      const lblConn = el("span", null, "Disconnected");
      const light = el("span", "azpe-light");
      conn.append(lblConn, light);
      head.append(conn);

      const fUrl = el("div", "azpe-field");
      fUrl.append(el("div", "azpe-lbl", "Server URL"));
      const inUrl = el("input", "azpe-in");
      inUrl.placeholder = "https://… (or $LLAMA_URL)";
      inUrl.value = wUrl?.value || "";
      fUrl.append(inUrl);

      const fTok = el("div", "azpe-field");
      fTok.append(el("div", "azpe-lbl", "API Token"));
      const inTok = el("input", "azpe-in");
      inTok.type = "password";
      inTok.placeholder = "Bearer token (or $LLAMA_TOKEN)";
      inTok.value = wTok?.value || "";
      fTok.append(inTok);

      const row = el("div", "azpe-row");
      const fLlm = el("div", "azpe-field");
      fLlm.append(el("div", "azpe-lbl", "LLM Model"));
      const selLlm = el("select", "azpe-sel");
      selLlm.disabled = true;
      const placeholder = el("option", null, "— Connect to load models —");
      placeholder.value = "";
      selLlm.append(placeholder);
      if (wLlm?.value) {
        const o = el("option", null, wLlm.value);
        o.value = wLlm.value;
        o.selected = true;
        selLlm.append(o);
      }
      fLlm.append(selLlm);
      const btnConn = el("button", "azpe-btn", "Connect");
      row.append(fLlm, btnConn);

      const fImg = el("div", "azpe-field");
      fImg.append(el("div", "azpe-lbl", "Image Model (prompt style)"));
      const selImg = el("select", "azpe-sel");
      (wImg?.options?.values || []).forEach((v) => {
        const o = el("option", null, v);
        o.value = v;
        if (v === wImg.value) o.selected = true;
        selImg.append(o);
      });
      fImg.append(selImg);

      const bar = el("div", "azpe-bar");
      bar.append(el("div", "azpe-fill"));
      const status = el("div", "azpe-status");

      card.append(head, fUrl, fTok, row, fImg, bar, status);

      const domW = this.addDOMWidget("azpe_ui", "Prompt Enhancer", card, { serialize: false });
      domW.serializeValue = () => undefined;
      let cardH = 260;
      domW.computeSize = () => [this.size[0] - 20, cardH];
      // float the card to the top of the node
      const i = this.widgets.indexOf(domW);
      if (i > 0) { this.widgets.splice(i, 1); this.widgets.unshift(domW); }

      const fit = () => {
        const h = card.offsetHeight;
        if (h && Math.abs(h + 6 - cardH) > 2) {
          cardH = h + 6;
          const sz = this.computeSize();
          this.setSize([Math.max(400, this.size[0]), sz[1]]);
          this.setDirtyCanvas(true, true);
        }
      };
      requestAnimationFrame(fit);
      setTimeout(fit, 60);

      // ---- helpers ----
      const setLight = (state) => {
        light.classList.remove("ok", "busy");
        if (state === "ok") { light.classList.add("ok"); lblConn.textContent = "Connected"; }
        else if (state === "busy") { light.classList.add("busy"); lblConn.textContent = "Connecting…"; }
        else { lblConn.textContent = "Disconnected"; }
      };
      const setStatus = (t, isErr = false) => {
        status.textContent = t || "";
        status.classList.toggle("error", !!isErr);
      };
      const setBar = (on) => bar.classList.toggle("active", !!on);

      const syncUrlTok = () => {
        if (wUrl) wUrl.value = inUrl.value.trim();
        if (wTok) wTok.value = inTok.value.trim();
      };

      inUrl.addEventListener("input", () => { syncUrlTok(); setLight("off"); });
      inTok.addEventListener("input", () => { syncUrlTok(); setLight("off"); });
      selImg.addEventListener("change", () => { if (wImg) wImg.value = selImg.value; });
      selLlm.addEventListener("change", () => { if (wLlm) wLlm.value = selLlm.value; });

      const connect = async () => {
        syncUrlTok();
        if (!inUrl.value.trim() || !inTok.value.trim()) {
          setStatus("Enter a server URL and token first.", true);
          setLight("off");
          return;
        }
        btnConn.disabled = true;
        setLight("busy");
        setStatus("Fetching models…");
        try {
          const resp = await api.fetchApi("/az_prompt_enhancer/models", {
            method: "POST",
            body: JSON.stringify({ url: inUrl.value.trim(), token: inTok.value.trim() }),
          });
          const d = await resp.json();
          if (!d.ok || !Array.isArray(d.models) || !d.models.length) {
            throw new Error(d.error || "No models returned.");
          }
          const prevSel = wLlm?.value || "";
          selLlm.innerHTML = "";
          d.models.forEach((m) => {
            const o = el("option", null, m);
            o.value = m;
            selLlm.append(o);
          });
          selLlm.value = d.models.includes(prevSel) ? prevSel : d.models[0];
          selLlm.disabled = false;
          if (wLlm) wLlm.value = selLlm.value;
          setLight("ok");
          setStatus(`${d.models.length} model(s) available.`);
        } catch (e) {
          setLight("off");
          setStatus(e?.message || "Connection failed.", true);
        } finally {
          btnConn.disabled = false;
        }
      };
      btnConn.addEventListener("click", connect);

      // ---- live progress + preview from execute() ----
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

      this.size = [400, 380];
      return r;
    };
  },
});
