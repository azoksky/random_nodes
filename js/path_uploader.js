// Path Uploader UI: live dropdown under the path box, mouse + keyboard selection,
// upload progress, and automatic "\" -> "/" normalization.
// Nodes 2.0: status + progress are DOM widgets (onDrawForeground is not called by
// the Vue renderer), and all colors use theme CSS variables.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const normalizePath = (p) => (p || "").replace(/\\/g, "/");

// Always join with forward slashes for consistency
function joinPath(base, seg) {
  base = normalizePath(base || "");
  seg  = normalizePath(seg || "");
  if (!base) return seg;
  if (!seg) return base;
  const trailing = base.endsWith("/");
  return trailing ? base + seg : base + "/" + seg;
}

function fmtBytes(b){ if(!b||b<=0) return "0 B"; const u=["B","KB","MB","GB","TB"]; const i=Math.floor(Math.log(b)/Math.log(1024)); return (b/Math.pow(1024,i)).toFixed(i?1:0)+" "+u[i]; }
function fmtETA(s){ if(s==null) return "—"; const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=Math.floor(s%60); if(h) return `${h}h ${m}m ${sec}s`; if(m) return `${m}m ${sec}s`; return `${sec}s`; }

function injectCSSOnce(){
  const id = "az-path-uploader-css";
  if (document.getElementById(id)) return;
  const s = document.createElement("style");
  s.id = id;
  s.textContent = `
    .az-up-input{width:100%;height:26px;padding:2px 8px;border:1px solid var(--border-color,#444);
      border-radius:6px;background:var(--comfy-input-bg,#2a2a2a);color:var(--input-text,#ddd);
      box-sizing:border-box;outline:none}
    .az-up-input:focus{border-color:var(--p-primary-color,#5b8cff)}
    .az-up-dropdown{position:absolute;top:100%;left:0;right:0;background:var(--comfy-menu-bg,#222);
      border:1px solid var(--border-color,#555);z-index:9999;display:none;max-height:180px;
      overflow-y:auto;font-size:12px;border-radius:6px;color:var(--input-text,#ddd)}
    .az-up-row{padding:5px 8px;cursor:pointer;white-space:nowrap;user-select:none}
    .az-up-row.active{background:var(--comfy-menu-secondary-bg,var(--border-color,#444))}
    .az-up-panel{display:flex;flex-direction:column;gap:4px;width:100%;box-sizing:border-box;
      font-family:var(--font-family,'Segoe UI',sans-serif);font-size:12px;padding:2px 2px 4px}
    .az-up-saved{color:var(--p-green-400,#9bc27c);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .az-up-file,.az-up-meta{color:var(--descrip-text,#9aa3b2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .az-up-bar{position:relative;height:16px;border:1px solid var(--border-color,#666);border-radius:7px;
      overflow:hidden;background:var(--comfy-input-bg,#222)}
    .az-up-fill{position:absolute;left:0;top:0;bottom:0;width:0%;
      background:var(--p-primary-color,#4b90ff);transition:width .15s ease}
    .az-up-pct{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
      font-size:11px;color:var(--fg-color,#eee);mix-blend-mode:difference}
  `;
  document.head.appendChild(s);
}

app.registerExtension({
  name: "az.path.uploader",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "PathUploader") return;

    const orig = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = orig ? orig.apply(this, arguments) : undefined;
      injectCSSOnce();

      // ---- persistent + state ----
      this.properties = this.properties || {};
      this.properties.dest_dir = normalizePath(this.properties.dest_dir || "");

      this._status="Idle"; this._progress=0; this._speed=0; this._eta=null;
      this._sent=0; this._total=0; this._savedPath=""; this._filename="";
      this._xhr=null; this._selectedFile=null; this._tPrev=0; this._sentPrev=0;

      // ===== Destination input with custom dropdown =====
      const container = document.createElement("div");
      container.classList.add("az-path-uploader-container");
      Object.assign(container.style,{ position:"relative", width:"100%" });

      const destInput = document.createElement("input");
      destInput.type="text";
      destInput.className="az-up-input";
      destInput.placeholder="Destination folder (e.g. C:/Users/you/Downloads or ~/models)";
      destInput.value = this.properties.dest_dir;

      // dropdown panel anchored under the input
      const dropdown = document.createElement("div");
      dropdown.className="az-up-dropdown";

      container.appendChild(destInput);
      container.appendChild(dropdown);

      const destWidget = this.addDOMWidget("dest_dir","Destination",container);
      // compact row
      destWidget.computeSize = () => [this.size[0]-20, 34];

      let items = []; let active = -1; let debounceTimer=null;

      const renderDropdown = () => {
        dropdown.innerHTML = "";
        if (!items.length) { dropdown.style.display = "none"; active = -1; return; }

        items.forEach((it, idx)=>{
          const row = document.createElement("div");
          row.className = "az-up-row" + (idx===active ? " active" : "");
          row.textContent = it.name;
          row.dataset.idx = String(idx);
          row.tabIndex = -1; // make focusable if needed

          // Highlight on hover
          row.onmouseenter = ()=>{ active = idx; renderDropdown(); };

          // --- IMPORTANT: choose on pointerdown/mousedown so it fires before blur ---
          const choose = () => {
            const chosen = normalizePath(it.path);
            destInput.value = chosen;
            this.properties.dest_dir = chosen;
            items = []; active = -1;
            dropdown.style.display="none";
            scheduleFetch(); // load next level
          };
          row.addEventListener("pointerdown", (e)=>{ e.preventDefault(); e.stopPropagation(); choose(); });
          row.addEventListener("mousedown",   (e)=>{ e.preventDefault(); e.stopPropagation(); choose(); });

          dropdown.appendChild(row);
        });

        // ensure the active item is visible without forcing a big jump
        if (active >= 0 && dropdown.children.length > active) {
          const activeRow = dropdown.children[active];
          try {
            if (typeof activeRow.scrollIntoView === "function") {
              activeRow.scrollIntoView({ block: "nearest" });
            } else {
              const rowTop = activeRow.offsetTop;
              const rowBottom = rowTop + activeRow.offsetHeight;
              if (rowTop < dropdown.scrollTop) dropdown.scrollTop = rowTop;
              else if (rowBottom > dropdown.scrollTop + dropdown.clientHeight) dropdown.scrollTop = rowBottom - dropdown.clientHeight;
            }
          } catch (e) {
            // ignore scroll errors
          }
        }

        dropdown.style.display = "block";
      };

      const scheduleFetch = () => {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(fetchChildren, 200);
      };

      const fetchChildren = async () => {
        // If someone cleared or input not focused, don't show results
        if (!destInput.value || !destInput.value.trim()) { items = []; renderDropdown(); return; }
        const raw = destInput.value.trim();
        const val = normalizePath(raw);
        try {
          const resp = await api.fetchApi(`/az/listdir?path=${encodeURIComponent(val)}`);
          const data = await resp.json();
          if (data?.ok && data.folders) {
            items = data.folders.map(f=>({
              name: f.name,
              path: joinPath(data.root || val, f.name)
            }));
          } else {
            items = [];
          }
        } catch (err) {
          items = [];
        }
        active = items.length ? 0 : -1;
        // Only render dropdown if the input still has focus (prevent re-opening after blur)
        if (document.activeElement === destInput) {
          renderDropdown();
        } else {
          dropdown.style.display = "none";
        }
      };

      // allow keyboard to open/populate the dropdown when Arrow keys are pressed,
      // and let Enter choose the active item even if dropdown wasn't open.
      destInput.addEventListener("keydown", async (e)=>{
        if ((e.key === "ArrowDown" || e.key === "ArrowUp") && dropdown.style.display !== "block") {
          e.preventDefault();
          if (debounceTimer) clearTimeout(debounceTimer);
          await fetchChildren();
        }

        if (dropdown.style.display !== "block" || !items.length) {
          if (!items.length) return;
        }

        if (e.key === "ArrowDown") {
          e.preventDefault();
          active = (active+1) % items.length;
          renderDropdown();
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          active = (active-1+items.length) % items.length;
          renderDropdown();
        } else if (e.key === "Enter") {
          if (items.length) {
            e.preventDefault();
            const it = items[(active >= 0) ? active : 0];
            const chosen = normalizePath(it.path);
            destInput.value = chosen;
            this.properties.dest_dir = chosen;
            items = []; active = -1; dropdown.style.display="none";
            scheduleFetch();
          }
        } else if (e.key === "Escape") {
          dropdown.style.display="none"; items=[]; active=-1;
        }
      });

      // Normalize "\" to "/" as you type, without jumping the caret (fixed delta)
      destInput.addEventListener("input", ()=>{
        const prevStart = destInput.selectionStart, prevEnd = destInput.selectionEnd;
        const before = destInput.value;
        const normalized = normalizePath(before);
        if (normalized !== before) {
          destInput.value = normalized;
          const delta = normalized.length - before.length;
          const pos = Math.max(0, (prevStart||0) + (delta||0));
          destInput.setSelectionRange(pos, pos);
        }
        this.properties.dest_dir = normalized;
        scheduleFetch();
      });

      // When focusing: if field is empty, fetch server root and prefill before fetchChildren.
      destInput.addEventListener("focus", async ()=>{
        if (!destInput.value || !destInput.value.trim()) {
          try {
            const resp = await api.fetchApi(`/az/listdir`);
            const data = await resp.json();
            if (data?.ok && data.root) {
              const root = normalizePath(data.root);
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

      // Hide dropdown on blur immediately and cancel pending fetches
      destInput.addEventListener("blur", ()=>{
        if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
        items = []; active = -1;
        dropdown.style.display = "none";
      });

      // Close dropdown when clicking outside the container (robust)
      const docHandler = (ev) => {
        if (!container.contains(ev.target)) {
          if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
          items = []; active = -1;
          dropdown.style.display = "none";
        }
      };
      document.addEventListener("pointerdown", docHandler);

      // ===== Status + progress (DOM widget; replaces onDrawForeground) =====
      const panel = document.createElement("div");
      panel.className = "az-up-panel";
      const savedEl = document.createElement("div"); savedEl.className = "az-up-saved";
      const fileEl  = document.createElement("div"); fileEl.className  = "az-up-file";
      const metaEl  = document.createElement("div"); metaEl.className  = "az-up-meta";
      const barWrap = document.createElement("div"); barWrap.className = "az-up-bar";
      const barFill = document.createElement("div"); barFill.className = "az-up-fill";
      const barPct  = document.createElement("div"); barPct.className  = "az-up-pct";
      barWrap.append(barFill, barPct);
      panel.append(savedEl, fileEl, metaEl, barWrap);
      const panelWidget = this.addDOMWidget("upload_status", "", panel, { serialize: false });
      panelWidget.computeSize = (width) => [width, 78];

      const render = () => {
        savedEl.style.display = this._savedPath ? "block" : "none";
        savedEl.textContent = this._savedPath ? `Saved: ${this._savedPath}` : "";
        fileEl.style.display = this._filename ? "block" : "none";
        fileEl.textContent = this._filename ? `File: ${this._filename} (${fmtBytes(this._total)})` : "";
        metaEl.textContent = `Status: ${this._status}   •   Speed: ${fmtBytes(this._speed)}/s   •   ETA: ${fmtETA(this._eta)}`;
        const pct = Math.max(0, Math.min(100, this._progress || 0));
        barFill.style.width = pct + "%";
        barPct.textContent = pct.toFixed(0) + "%";
      };

      // ===== File picker =====
      this.addWidget("button","Choose File","Browse…",()=>{
        const picker=document.createElement("input"); picker.type="file";
        picker.onchange=()=>{
          if(!picker.files||!picker.files[0]) return;
          const f=picker.files[0]; this._selectedFile=f; this._filename=f.name; this._total=f.size;
          this._sent=0; this._progress=0; this._status="Ready"; this._savedPath="";
          render();
        };
        picker.click();
      });

      // ===== Upload =====
      this.addWidget("button","Upload","Start",async ()=>{
        if(!this._selectedFile){ this._status="Please select a file first."; render(); return; }
        const dest=normalizePath(this.properties.dest_dir||"").trim();
        if(!dest){ this._status="Please enter destination folder."; render(); return; }
        if(this._xhr) return;

        const form=new FormData();
        form.append("dest_dir", dest);
        form.append("file", this._selectedFile, this._selectedFile.name);

        const xhr=new XMLHttpRequest(); this._xhr=xhr;
        this._status="Uploading…"; this._progress=0; this._sent=0; this._speed=0; this._eta=null; this._savedPath="";
        this._tPrev=performance.now(); this._sentPrev=0; render();

        xhr.upload.onprogress=(e)=>{
          if(e.lengthComputable){ this._sent=e.loaded; this._total=e.total; this._progress=Math.max(0,Math.min(100,(e.loaded/e.total)*100)); }
          const tNow=performance.now(), dt=(tNow-this._tPrev)/1000;
          if(dt>0.25){ const dBytes=this._sent-this._sentPrev; this._speed=dBytes/dt; const remain=Math.max(this._total-this._sent,0); this._eta=this._speed>0?Math.floor(remain/this._speed):null; this._tPrev=tNow; this._sentPrev=this._sent; }
          render();
        };

        xhr.onreadystatechange=()=>{
          if(xhr.readyState===4){
            let data=null; try{ data=JSON.parse(xhr.responseText||"{}"); }catch{}
            if(xhr.status>=200 && xhr.status<300 && data?.ok){ this._status="Complete"; this._savedPath=data.path||""; this._progress=100; }
            else{ const err=(data&&(data.error||data.message))||`HTTP ${xhr.status}`; this._status=`Error: ${err}`; }
            this._xhr=null; render();
          }
        };
        xhr.onerror=()=>{ this._status="Network error"; this._xhr=null; render(); };

        // Use the ComfyUI api base (e.g. "/console") so the POST lands on the same
        // backend as api.fetchApi calls.
        const uploadURL = (api.apiURL ? api.apiURL("/az/upload") : "/az/upload");
        xhr.open("POST", uploadURL, true); xhr.send(form);
      });

      // ===== Cancel =====
      this.addWidget("button","Cancel","Stop",()=>{
        if(this._xhr){ this._xhr.abort(); this._xhr=null; this._status="Canceled"; render(); }
      });

      this.size=[520,300];
      render();

      // Fetch the server's default working directory into destInput (only if empty).
      const fetchDefaultRoot = () => {
        api.fetchApi(`/az/listdir`).then((r) => r.json()).then((data) => {
          if (data?.ok && data.root && !this.properties.dest_dir) {
            const root = normalizePath(data.root);
            destInput.value = root;
            this.properties.dest_dir = root;
            if (debounceTimer) clearTimeout(debounceTimer);
            debounceTimer = setTimeout(fetchChildren, 50);
          }
        }).catch(() => {});
      };

      // Restore DOM field from a loaded/cached workflow (fires after deserialization).
      const prevConfigure = this.onConfigure;
      this.onConfigure = function (info) {
        prevConfigure?.apply(this, arguments);
        const savedDest = normalizePath(this.properties.dest_dir || "").trim();
        if (savedDest) destInput.value = savedDest;
        else fetchDefaultRoot();
      };

      // Fresh node (not from a saved workflow): onConfigure won't fire.
      fetchDefaultRoot();

      // kick suggestions if prefilled (legacy path)
      if(destInput.value) setTimeout(()=>destInput.dispatchEvent(new Event("input")), 50);

      return r;
    };
  },
});
