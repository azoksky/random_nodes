import { app } from "../../scripts/app.js";

const ROW_H = 26;   // input / preview row height
const GAP = 6;      // vertical gap between rows
const LABEL_H = 13; // tiny caption
// total inner content height the DOM widget needs
const WIDGET_H = LABEL_H + GAP + ROW_H + GAP + ROW_H;

function injectCSSOnce() {
  const id = "az-iterstr-css";
  if (document.getElementById(id)) return;
  const s = document.createElement("style");
  s.id = id;
  s.textContent = `
    .az-iter-wrap{display:flex;flex-direction:column;gap:${GAP}px;width:100%;
      box-sizing:border-box;
      font-family:var(--font-family,'Segoe UI',sans-serif)}
    .az-iter-label{font-size:10px;line-height:${LABEL_H}px;letter-spacing:.07em;
      text-transform:uppercase;color:var(--descrip-text,#7e8696);padding-left:1px}
    .az-iter-input{width:100%;height:${ROW_H}px;padding:0 9px;border:1px solid var(--border-color,#3a3f4b);
      border-radius:7px;background:var(--comfy-input-bg,#1d2027);color:var(--input-text,#e8ebf1);box-sizing:border-box;
      outline:none;font-size:13px;
      transition:border-color .15s ease, box-shadow .15s ease}
    .az-iter-input::placeholder{color:var(--descrip-text,#5d6473)}
    .az-iter-input:focus{border-color:var(--p-primary-color,#5b8cff);box-shadow:0 0 0 2px rgba(91,140,255,.22)}
    .az-iter-preview{display:flex;align-items:center;gap:8px;height:${ROW_H}px;
      padding:0 9px;border-radius:7px;box-sizing:border-box;
      background:rgba(91,140,255,.08);border:1px solid rgba(91,140,255,.26)}
    .az-iter-dot{width:7px;height:7px;border-radius:50%;background:var(--p-primary-color,#5b8cff);
      box-shadow:0 0 7px var(--p-primary-color,#5b8cff);flex:0 0 auto}
    .az-iter-dot.live{background:#46d39a;box-shadow:0 0 7px #46d39a}
    .az-iter-text{flex:1 1 auto;font-family:ui-monospace,Menlo,Consolas,monospace;
      font-size:12px;color:var(--input-text,#cdd6f4);white-space:nowrap;overflow:hidden;
      text-overflow:ellipsis}
    .az-iter-badge{flex:0 0 auto;font-size:9px;letter-spacing:.05em;
      text-transform:uppercase;color:var(--descrip-text,#8a93a6);background:rgba(255,255,255,.05);
      padding:1px 6px;border-radius:5px}
  `;
  document.head.appendChild(s);
}

app.registerExtension({
  name: "az.iterative.string",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "AzIterativeString") return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated ? onCreated.apply(this, arguments) : undefined;
      injectCSSOnce();

      // Drop the auto-created native "name" widget; our DOM widget carries the value.
      const idx = (this.widgets || []).findIndex((w) => w.name === "name");
      if (idx !== -1) this.widgets.splice(idx, 1);

      let nextN = 1;

      const wrap = document.createElement("div");
      wrap.className = "az-iter-wrap";

      const label = document.createElement("div");
      label.className = "az-iter-label";
      label.textContent = "Name";

      const input = document.createElement("input");
      input.className = "az-iter-input";
      input.type = "text";
      input.placeholder = "output";
      input.value = "output";

      const preview = document.createElement("div");
      preview.className = "az-iter-preview";
      const dot = document.createElement("div");
      dot.className = "az-iter-dot";
      const text = document.createElement("div");
      text.className = "az-iter-text";
      const badge = document.createElement("div");
      badge.className = "az-iter-badge";
      preview.append(dot, text, badge);

      wrap.append(label, input, preview);

      const showLive = () => {
        text.textContent = `${input.value}_${nextN}`;
        badge.textContent = "next";
        dot.classList.remove("live");
      };
      const showActual = (val) => {
        text.textContent = val;
        badge.textContent = "output";
        dot.classList.add("live");
      };

      input.addEventListener("input", () => showLive());

      const widget = this.addDOMWidget("name", "string", wrap, {
        serialize: true,
        getValue: () => input.value,
        setValue: (v) => { input.value = v ?? ""; showLive(); },
      });
      // Report the exact height we need so LiteGraph stops clipping / over-padding.
      widget.computeSize = (width) => [width, WIDGET_H];

      const onExec = this.onExecuted;
      this.onExecuted = function (message) {
        onExec?.apply(this, arguments);
        const t = message?.text;
        const val = Array.isArray(t) ? t[0] : t;
        if (val != null) {
          showActual(String(val));
          const m = String(val).match(/_(\d+)$/);
          if (m) nextN = parseInt(m[1], 10) + 1;
        }
      };

      showLive();
      this.size = this.computeSize();
      this.size[0] = Math.max(this.size[0], 220);
      return r;
    };
  },
});
