import { app } from "../../scripts/app.js";

function injectCSSOnce() {
  const id = "az-iterstr-css";
  if (document.getElementById(id)) return;
  const s = document.createElement("style");
  s.id = id;
  s.textContent = `
    .az-iter-wrap{display:flex;flex-direction:column;gap:8px;width:100%;
      font-family:var(--font-family,'Segoe UI',sans-serif)}
    .az-iter-label{font-size:11px;letter-spacing:.06em;text-transform:uppercase;
      color:#8a93a6;padding-left:2px}
    .az-iter-input{width:100%;height:32px;padding:6px 11px;border:1px solid #3a3f4b;
      border-radius:9px;background:linear-gradient(#262a33,#1c1f26);color:#e8ebf1;
      box-sizing:border-box;outline:none;font-size:13px;
      transition:border-color .15s ease, box-shadow .15s ease}
    .az-iter-input::placeholder{color:#5d6473}
    .az-iter-input:focus{border-color:#5b8cff;box-shadow:0 0 0 3px rgba(91,140,255,.20)}
    .az-iter-preview{display:flex;align-items:center;gap:9px;padding:9px 11px;
      border-radius:9px;background:rgba(91,140,255,.08);
      border:1px solid rgba(91,140,255,.28)}
    .az-iter-dot{width:8px;height:8px;border-radius:50%;background:#5b8cff;
      box-shadow:0 0 8px #5b8cff;flex:0 0 auto}
    .az-iter-dot.live{background:#46d39a;box-shadow:0 0 8px #46d39a}
    .az-iter-text{flex:1 1 auto;font-family:ui-monospace,Menlo,Consolas,monospace;
      font-size:13px;color:#cdd6f4;white-space:nowrap;overflow:hidden;
      text-overflow:ellipsis}
    .az-iter-badge{flex:0 0 auto;font-size:10px;letter-spacing:.05em;
      text-transform:uppercase;color:#8a93a6;background:rgba(255,255,255,.05);
      padding:2px 7px;border-radius:6px}
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

      // ComfyUI auto-creates a native widget for the "name" STRING input.
      // Remove it; our styled DOM widget below becomes the value carrier so
      // there's exactly one field and nothing hidden.
      const idx = (this.widgets || []).findIndex((w) => w.name === "name");
      if (idx !== -1) this.widgets.splice(idx, 1);

      let nextN = 1; // best-guess counter for the live preview before a run

      const wrap = document.createElement("div");
      wrap.className = "az-iter-wrap";

      const label = document.createElement("div");
      label.className = "az-iter-label";
      label.textContent = "Name";

      const input = document.createElement("input");
      input.className = "az-iter-input";
      input.type = "text";
      input.placeholder = "output";
      input.value = "output"; // default; overridden by setValue on graph load

      const preview = document.createElement("div");
      preview.className = "az-iter-preview";
      const dot = document.createElement("div");
      dot.className = "az-iter-dot";
      const text = document.createElement("div");
      text.className = "az-iter-text";
      const badge = document.createElement("div");
      badge.className = "az-iter-badge";
      preview.appendChild(dot);
      preview.appendChild(text);
      preview.appendChild(badge);

      wrap.appendChild(label);
      wrap.appendChild(input);
      wrap.appendChild(preview);

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

      // Our styled input IS the "name" widget: its value serializes to the
      // backend in place of the removed native one.
      this.addDOMWidget("name", "string", wrap, {
        serialize: true,
        getValue: () => input.value,
        setValue: (v) => { input.value = v ?? ""; showLive(); },
      });

      // Show the exact string the backend produced after each run.
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
      this.size = [260, 156];
      return r;
    };
  },
});
