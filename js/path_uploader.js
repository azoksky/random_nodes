// Path Uploader UI: live dropdown under the path box, mouse + keyboard selection,
// upload progress, and automatic "\" -> "/" normalization.
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

app.registerExtension({
  name: "az.path.uploader",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== "PathUploader") return;

    const orig = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = orig ? orig.apply(this, arguments) : undefined;

      // ---- persistent + state ----
      this.properties = this.properties || {};
      this.properties.dest_dir = normalizePath(this.properties.dest_dir || "");

      this._status="Idle"; this._progress=0; this._speed=0; this._eta=null;
      this._sent=0; this._total=0; this._savedPath=""; this._filename="";
      this._xhr=null; this._selectedFile=null; this._tPrev=0; this._sentPrev=0;

      // ===== Destination input with custom dropdown =====
      const container = document.createElement("div");
      Object.assign(container.style,{ position:"relative", width:"100%" });

      const destInput = document.createElement("input");
      destInput.type="text";
      destInput.placeholder="Destination folder (e.g. C:/Users/you/Downloads or ~/models)";
      Object.assign(destInput.style,{
        width:"100%", height:"26px", padding:"2px 8px",
        border:"1px solid #444", borderRadius:"6px",
        background:"var(--comfy-input-bg, #2a2a2a)", color:"#ddd",
        boxSizing:"border-box", outline:"none"
      });
      destInput.value = this.properties.dest_dir;

      // dropdown panel anchored under the input
      const dropdown = document.createElement("div");
      Object.assign(dropdown.style,{
        position:"absolute", top:"100%", left:"0", right:"0",
        background:"#222", border:"1px solid #555",
        zIndex:"9999", display:"none", maxHeight:"180px",
        overflowY:"auto", fontSize:"12px", borderRadius:"6px"
      });

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
          row.textContent = it.name;
          Object.assign(row.style,{
            padding:"5px 8px", cursor:"pointer", whiteSpace:"nowrap",
            background: idx===active ? "#444" : "transparent",
            userSelect: "none"
          });

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

        dropdown.style.display = "block";
      };

      const scheduleFetch = () => {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(fetchChildren, 200);
      };

      const fetchChildren = async () => {
        const raw = destInput.value.trim();
        if (!raw) { items = []; renderDropdown(); return; }
        const val = normalizePath(raw);
        try{
          const resp = await api.fetchApi(`/az/listdir?path=${encodeURIComponent(val)}`);
          const data = await resp.json();
          if (data?.ok && data.folders) {
            items = data.folders.map(f=>({
              name: f.name,
              path: joinPath(data.root || val, f.name)
            }));
          } else { items = []; }
          active = items.length ? 0 : -1;
          renderDropdown();
        }catch{ items = []; renderDropdown(); }
      };

      // Normalize "\" to "/" as you type, without jumping the caret
      destInput.addEventListener("input", ()=>{
        const prevStart = destInput.selectionStart, prevEnd = destInput.selectionEnd;
        const normalized = normalizePath(destInput.value);
        if (normalized !== destInput.value) {
          destInput.value = normalized;
          // best-effort caret restore
          const delta = normalized.length - (destInput.value.length); // 0 because we reassigned
          const pos = Math.max(0, (prevStart||0) + (delta||0));
          destInput.setSelectionRange(pos, pos);
        }
        this.properties.dest_dir = normalized;
        scheduleFetch();
      });

      destInput.addEventListener("focus", ()=>{ scheduleFetch(); });

      // keyboard navigation
      destInput.addEventListener("keydown", (e)=>{
        if (dropdown.style.display !== "block" || !items.length) return;
        if (e.key === "ArrowDown") { e.preventDefault(); active = (active+1) % items.length; renderDropdown(); }
        else if (e.key === "ArrowUp") { e.preventDefault(); active = (active-1+items.length) % items.length; renderDropdown(); }
        else if (e.key === "Enter") {
          if (active >= 0) {
            e.preventDefault();
            const it = items[active];
            const chosen = normalizePath(it.path);
            destInput.value = chosen;
            this.properties.dest_dir = chosen;
            items = []; active = -1; dropdown.style.display="none";
            scheduleFetch();
          }
        } else if (e.key === "Escape") { dropdown.style.display="none"; items=[]; active=-1; }
      });

      // Delay hiding so clicks can register (we also handle on pointerdown)
      destInput.addEventListener("blur", ()=>{ setTimeout(()=>{ dropdown.style.display="none"; }, 120); });

      // ===== File picker =====
      this.addWidget("button","Choose File","Browse…",()=>{
        const picker=document.createElement("input"); picker.type="file";
        picker.onchange=()=>{
          if(!picker.files||!picker.files[0]) return;
          const f=picker.files[0]; this._selectedFile=f; this._filename=f.name; this._total=f.size;
          this._sent=0; this._progress=0; this._status="Ready"; this._savedPath="";
          this.setDirtyCanvas(true);
        };
        picker.click();
      });

      // ===== Upload =====
      this.addWidget("button","Upload","Start",async ()=>{
        if(!this._selectedFile){ this._status="Please select a file first."; this.setDirtyCanvas(true); return; }
        const dest=normalizePath(this.properties.dest_dir||"").trim();
        if(!dest){ this._status="Please enter destination folder."; this.setDirtyCanvas(true); return; }
        if(this._xhr) return;

        const form=new FormData();
        form.append("file", this._selectedFile, this._selectedFile.name);
        form.append("dest_dir", dest);

        const xhr=new XMLHttpRequest(); this._xhr=xhr;
        this._status="Uploading…"; this._progress=0; this._sent=0; this._speed=0; this._eta=null; this._savedPath="";
        this._tPrev=performance.now(); this._sentPrev=0; this.setDirtyCanvas(true);

        xhr.upload.onprogress=(e)=>{
          if(e.lengthComputable){ this._sent=e.loaded; this._total=e.total; this._progress=Math.max(0,Math.min(100,(e.loaded/e.total)*100)); }
          const tNow=performance.now(), dt=(tNow-this._tPrev)/1000;
          if(dt>0.25){ const dBytes=this._sent-this._sentPrev; this._speed=dBytes/dt; const remain=Math.max(this._total-this._sent,0); this._eta=this._speed>0?Math.floor(remain/this._speed):null; this._tPrev=tNow; this._sentPrev=this._sent; }
          this.setDirtyCanvas(true);
        };

        xhr.onreadystatechange=()=>{
          if(xhr.readyState===4){
            let data=null; try{ data=JSON.parse(xhr.responseText||"{}"); }catch{}
            if(xhr.status>=200 && xhr.status<300 && data?.ok){ this._status="Complete"; this._savedPath=data.path||""; this._progress=100; }
            else{ const err=(data&&(data.error||data.message))||`HTTP ${xhr.status}`; this._status=`Error: ${err}`; }
            this._xhr=null; this.setDirtyCanvas(true);
          }
        };
        xhr.onerror=()=>{ this._status="Network error"; this._xhr=null; this.setDirtyCanvas(true); };

        xhr.open("POST","/az/upload",true); xhr.send(form);
      });

      // ===== Cancel =====
      this.addWidget("button","Cancel","Stop",()=>{
        if(this._xhr){ this._xhr.abort(); this._xhr=null; this._status="Canceled"; this.setDirtyCanvas(true); }
      });

      // ===== layout & drawing =====
      this.size=[520,290];
      this.onDrawForeground=(ctx)=>{
        const pad=10,w=this.size[0]-pad*2,barH=14,yBar=this.size[1]-pad-barH-4;

        if(this._savedPath){ ctx.font="12px sans-serif"; ctx.textAlign="left"; ctx.textBaseline="bottom"; ctx.fillStyle="#9bc27c";
          ctx.fillText(`Saved: ${this._savedPath}`, pad, yBar-48); }

        if(this._filename){ ctx.font="12px sans-serif"; ctx.textAlign="left"; ctx.textBaseline="bottom"; ctx.fillStyle="#8fa3b7";
          ctx.fillText(`File: ${this._filename} (${fmtBytes(this._total)})`, pad, yBar-32); }

        ctx.font="12px sans-serif"; ctx.textAlign="left"; ctx.textBaseline="bottom"; ctx.fillStyle="#bbb";
        const meta=`Status: ${this._status}   •   Speed: ${fmtBytes(this._speed)}/s   •   ETA: ${fmtETA(this._eta)}`;
        ctx.fillText(meta, pad, yBar-16);

        const radius=7; ctx.lineWidth=1; ctx.strokeStyle="#666";
        ctx.beginPath();
        ctx.moveTo(pad+radius,yBar); ctx.lineTo(pad+w-radius,yBar);
        ctx.quadraticCurveTo(pad+w,yBar,pad+w,yBar+radius);
        ctx.lineTo(pad+w,yBar+barH-radius); ctx.quadraticCurveTo(pad+w,yBar+barH,pad+w-radius,yBar+barH);
        ctx.lineTo(pad+radius,yBar+barH); ctx.quadraticCurveTo(pad,yBar+barH,pad,yBar+barH-radius);
        ctx.lineTo(pad,yBar+radius); ctx.quadraticCurveTo(pad,yBar,pad+radius,yBar); ctx.closePath(); ctx.stroke();

        const pct=Math.max(0,Math.min(100,this._progress||0)); const fillW=Math.round((w*pct)/100);
        ctx.save(); ctx.beginPath(); ctx.rect(pad+1,yBar+1,Math.max(0,fillW-2),barH-2);
        const g=ctx.createLinearGradient(pad,yBar,pad,yBar+barH); g.addColorStop(0,"#9ec7ff"); g.addColorStop(1,"#4b90ff");
        ctx.fillStyle=g; ctx.fill(); ctx.restore();

        ctx.font="12px sans-serif"; ctx.textAlign="center"; ctx.textBaseline="middle"; ctx.fillStyle="#111";
        ctx.fillText(`${pct.toFixed(0)}%`, pad+w/2, yBar+barH/2);
      };

      // kick suggestions if prefilled
      if(destInput.value) setTimeout(()=>destInput.dispatchEvent(new Event("input")), 50);

      return r;
    };
  },
});
