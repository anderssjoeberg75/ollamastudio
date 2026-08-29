#!/usr/bin/env python3
"""
Ollama Studio Web
=================

Webbversion av Ollama Studio – en liten webbserver som körs på en (ofta headless)
server där Ollama är installerat, och serverar ett LM Studio-liknande gränssnitt som
du når från en annan dator via webbläsaren.

- Serverar ett grafiskt webb-UI för att installera och avinstallera Ollama-modeller.
- Pratar med Ollama lokalt på servern (standard: http://localhost:11434), så Ollama
  självt behöver inte exponeras på nätverket – bara den här appens port.
- Endast Pythons standardbibliotek. Inga pip-paket.

Starta:
    python3 ollama_web.py

Öppna sedan i en webbläsare på en annan dator:
    http://<serverns-ip-eller-namn>:8080

Miljövariabler (alla valfria):
    OLLAMA_STUDIO_HOST    Adress att lyssna på           (standard: 0.0.0.0 = alla)
    OLLAMA_STUDIO_PORT    Port att lyssna på             (standard: 8080)
    OLLAMA_URL            Var Ollama körs                (standard: http://localhost:11434)
    OLLAMA_STUDIO_TOKEN   Valfritt lösenord/token för åtkomst (standard: inget)

Detta är ett fristående projekt.
"""

import json
import os
import sys
import socket
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_TITLE = "Ollama Studio"
APP_VERSION = "1.0.0"

LISTEN_HOST = os.environ.get("OLLAMA_STUDIO_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("OLLAMA_STUDIO_PORT", "8080"))
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
TOKEN = os.environ.get("OLLAMA_STUDIO_TOKEN", "").strip()

# --------------------------------------------------------------------------
# Kurerad katalog över populära modeller (samma som i skrivbordsappen)
# --------------------------------------------------------------------------
CATALOG = [
    {"pull": "llama3.2:1b",     "name": "Llama 3.2 1B",     "size": "~1.3 GB", "tag": "Liten & snabb",
     "desc": "Metas minsta modell. Blixtsnabb, funkar även på svagare datorer."},
    {"pull": "llama3.2",        "name": "Llama 3.2 3B",     "size": "~2.0 GB", "tag": "Rekommenderad",
     "desc": "Bra allround-modell för chatt och vardagsuppgifter. Lagom liten."},
    {"pull": "llama3.1",        "name": "Llama 3.1 8B",     "size": "~4.9 GB", "tag": "Kraftfull",
     "desc": "Starkare resonemang och längre svar. Kräver lite mer RAM."},
    {"pull": "qwen2.5:3b",      "name": "Qwen 2.5 3B",      "size": "~1.9 GB", "tag": "Flerspråkig",
     "desc": "Alibabas modell. Mycket bra på svenska och andra språk."},
    {"pull": "qwen2.5",         "name": "Qwen 2.5 7B",      "size": "~4.7 GB", "tag": "Flerspråkig",
     "desc": "Starkare Qwen-variant. Bra balans mellan storlek och kvalitet."},
    {"pull": "gemma2:2b",       "name": "Gemma 2 2B",       "size": "~1.6 GB", "tag": "Liten & snabb",
     "desc": "Googles lilla modell. Effektiv och pigg på vanlig hårdvara."},
    {"pull": "gemma2",          "name": "Gemma 2 9B",       "size": "~5.4 GB", "tag": "Kraftfull",
     "desc": "Googles större modell med hög kvalitet på svaren."},
    {"pull": "phi3",            "name": "Phi-3 Mini",       "size": "~2.2 GB", "tag": "Liten & smart",
     "desc": "Microsofts kompakta modell som presterar över sin storlek."},
    {"pull": "mistral",         "name": "Mistral 7B",       "size": "~4.1 GB", "tag": "Allround",
     "desc": "Populär och snabb modell för allmän användning."},
    {"pull": "deepseek-r1:1.5b","name": "DeepSeek-R1 1.5B", "size": "~1.1 GB", "tag": "Resonemang",
     "desc": "Liten resonemangsmodell som 'tänker steg för steg'."},
    {"pull": "deepseek-r1",     "name": "DeepSeek-R1 7B",   "size": "~4.7 GB", "tag": "Resonemang",
     "desc": "Stark på matte, logik och kod. Visar sitt tänkande."},
    {"pull": "llava",           "name": "LLaVA 7B",         "size": "~4.7 GB", "tag": "Ser bilder",
     "desc": "Multimodal modell som kan tolka och beskriva bilder."},
    {"pull": "codellama",       "name": "Code Llama 7B",    "size": "~3.8 GB", "tag": "Kod",
     "desc": "Specialiserad på programmering och kodgenerering."},
    {"pull": "nomic-embed-text","name": "Nomic Embed Text", "size": "~274 MB", "tag": "Embeddings",
     "desc": "Liten embeddingmodell för sök/RAG – inte för chatt."},
]


# --------------------------------------------------------------------------
# HTML/CSS/JS – hela webb-UI:t i en sträng (inga externa filer eller CDN)
# --------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ollama Studio</title>
<style>
  :root{
    --bg:#0f1115; --sidebar:#151922; --card:#1a1f2b; --card-hover:#222838;
    --border:#2a3141; --text:#e7e9ee; --subtle:#9aa3b5; --faint:#6b7280;
    --accent:#7c5cff; --accent-hov:#8f74ff; --accent-dim:#2c2650;
    --danger:#ff5c6c; --danger-dim:#3a2129; --green:#39d67f; --amber:#ffb454; --chip:#232a3a;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{background:var(--bg);color:var(--text);
    font-family:"Segoe UI",Ubuntu,Cantarell,"Noto Sans","DejaVu Sans",system-ui,sans-serif;
    display:flex;height:100vh;overflow:hidden}
  a{color:var(--accent-hov)}

  /* Sidomeny */
  .sidebar{width:240px;min-width:240px;background:var(--sidebar);display:flex;flex-direction:column;
    border-right:1px solid var(--border)}
  .logo{padding:22px 18px 20px}
  .logo .row{display:flex;align-items:center;gap:8px;font-size:20px;font-weight:700}
  .logo .diamond{color:var(--accent)}
  .logo .sub{color:var(--subtle);font-size:11px;letter-spacing:3px;margin-top:2px}
  .nav{padding:6px 10px;flex:1}
  .nav a{display:flex;align-items:center;gap:10px;padding:10px 12px;margin:2px 0;border-radius:8px;
    color:var(--subtle);font-weight:600;font-size:14px;cursor:pointer;text-decoration:none;user-select:none}
  .nav a .dot{color:var(--faint);font-size:11px}
  .nav a:hover{background:var(--card)}
  .nav a.active{background:var(--accent-dim);color:var(--text)}
  .nav a.active .dot{color:var(--accent-hov)}
  .status{padding:16px 18px;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--subtle);
    border-top:1px solid var(--border)}
  .status .dot{font-size:12px;color:var(--faint)}

  /* Innehåll */
  .content{flex:1;display:flex;flex-direction:column;min-width:0}
  .header{display:flex;align-items:center;justify-content:space-between;padding:22px 28px 8px}
  .header h1{font-size:22px;margin:0}
  .header .right{display:flex;align-items:center;gap:14px;color:var(--subtle);font-size:13px}
  .view{flex:1;overflow-y:auto;padding:8px 24px 24px}
  .view.hidden{display:none}

  /* Kort */
  .card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin:8px 2px}
  .card.hoverable:hover{background:var(--card-hover)}
  .card .top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
  .card h3{margin:0;font-size:15px}
  .chip{display:inline-block;background:var(--chip);color:var(--accent-hov);font-size:10px;font-weight:700;
    padding:2px 7px;border-radius:6px;margin-left:10px;vertical-align:middle}
  .pull-name{color:var(--faint);font-size:12px;margin-left:10px}
  .desc{color:var(--subtle);font-size:12px;margin-top:6px;max-width:640px}
  .meta{color:var(--faint);font-size:12px;margin-top:6px}
  .installed{color:var(--green);font-size:13px;font-weight:600;white-space:nowrap}

  /* Knappar */
  .btn{border:none;border-radius:8px;font-weight:700;font-size:13px;padding:9px 15px;cursor:pointer;
    font-family:inherit;white-space:nowrap}
  .btn.accent{background:var(--accent);color:#fff}
  .btn.accent:hover{background:var(--accent-hov)}
  .btn.ghost{background:var(--card);color:var(--text);border:1px solid var(--border)}
  .btn.ghost:hover{background:var(--card-hover)}
  .btn.danger{background:var(--danger-dim);color:var(--danger)}
  .btn.danger:hover{background:var(--danger);color:#fff}
  .btn.small{padding:6px 11px;font-size:12px}
  .btn:disabled{opacity:.5;cursor:default}

  /* Installera valfri modell */
  .install-box{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin:6px 2px 10px}
  .install-box h2{margin:0 0 4px;font-size:15px}
  .install-box p{margin:0 0 10px;color:var(--subtle);font-size:12px}
  .install-row{display:flex;gap:10px}
  .install-row input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:8px;
    color:var(--text);padding:10px 12px;font-size:14px;font-family:inherit}
  .install-row input:focus{outline:none;border-color:var(--accent)}
  .section-title{color:var(--subtle);font-weight:700;font-size:14px;margin:16px 2px 2px}

  /* Nedladdningspanel */
  .dl{position:sticky;bottom:0;background:var(--card);border-top:2px solid var(--accent);
    padding:12px 28px;margin:8px -24px -24px;display:none}
  .dl.show{display:block}
  .dl .top{display:flex;align-items:center;justify-content:space-between}
  .dl .title{font-weight:700;font-size:15px}
  .dl .pct{color:var(--accent-hov);font-size:13px;margin-left:auto;margin-right:14px}
  .bar{height:8px;background:var(--border);border-radius:4px;margin:10px 0 6px;overflow:hidden}
  .bar > div{height:100%;width:0;background:var(--accent);transition:width .15s}
  .dl .st{color:var(--subtle);font-size:12px}

  /* Tomt läge */
  .empty{text-align:center;padding:60px 20px;color:var(--subtle)}
  .empty h2{color:var(--text);margin:0 0 10px}

  /* Toast + modal */
  .toast{position:fixed;right:24px;bottom:24px;padding:12px 18px;border-radius:8px;color:#0f1115;
    font-weight:600;font-size:14px;z-index:50;opacity:0;transform:translateY(10px);transition:.2s}
  .toast.show{opacity:1;transform:none}
  .toast.ok{background:var(--green)} .toast.err{background:var(--danger)}
  .overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:center;
    justify-content:center;z-index:60}
  .overlay.show{display:flex}
  .modal{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px;max-width:420px}
  .modal h3{margin:0 0 10px} .modal p{color:var(--subtle);font-size:14px;white-space:pre-line;margin:0 0 20px}
  .modal .row{display:flex;justify-content:flex-end;gap:10px}
  @media(max-width:640px){ .sidebar{width:64px;min-width:64px} .nav a span.label{display:none} .logo .txt{display:none} }
</style>
</head>
<body>
  <div class="sidebar">
    <div class="logo">
      <div class="row"><span class="diamond">◆</span><span class="txt">Ollama</span></div>
      <div class="sub txt">S T U D I O</div>
    </div>
    <div class="nav">
      <a id="nav-models" class="active" onclick="showView('models')"><span class="dot">●</span><span class="label">Mina modeller</span></a>
      <a id="nav-discover" onclick="showView('discover')"><span class="dot">●</span><span class="label">Upptäck / Installera</span></a>
    </div>
    <div class="status"><span class="dot" id="statusDot">●</span><span id="statusText">Kontrollerar…</span></div>
  </div>

  <div class="content">
    <div class="header">
      <h1 id="title">Mina modeller</h1>
      <div class="right">
        <span id="summary"></span>
        <button class="btn ghost small" onclick="refresh()">↻ Uppdatera</button>
      </div>
    </div>

    <div id="view-models" class="view"><div id="modelsList"></div></div>

    <div id="view-discover" class="view hidden">
      <div class="install-box">
        <h2>Installera valfri modell</h2>
        <p>Skriv exakt modellnamn från ollama.com/library, t.ex. "llama3.1:8b" eller "mistral-nemo".</p>
        <div class="install-row">
          <input id="customName" placeholder="modellnamn…" onkeydown="if(event.key==='Enter')pullCustom()">
          <button class="btn accent" onclick="pullCustom()">↓ Ladda ner</button>
        </div>
      </div>
      <div class="section-title">Populära modeller</div>
      <div id="catalogList"></div>
      <div class="dl" id="dlPanel">
        <div class="top">
          <span class="title" id="dlTitle"></span>
          <span class="pct" id="dlPct"></span>
          <button class="btn ghost small" id="dlCancel" onclick="cancelPull()">Avbryt</button>
        </div>
        <div class="bar"><div id="dlBar"></div></div>
        <div class="st" id="dlStatus"></div>
      </div>
    </div>
  </div>

  <div class="toast" id="toast"></div>
  <div class="overlay" id="overlay">
    <div class="modal">
      <h3 id="mTitle"></h3><p id="mBody"></p>
      <div class="row">
        <button class="btn ghost" onclick="closeModal()">Avbryt</button>
        <button class="btn danger" id="mConfirm">✕ Avinstallera</button>
      </div>
    </div>
  </div>

<script>
const CATALOG = __CATALOG_JSON__;
const AUTH = __AUTH_ENABLED__;
let token = AUTH ? (localStorage.getItem('os_token') || '') : '';
let installed = new Set();
let pullController = null;

function headers(json){
  const h = json ? {'Content-Type':'application/json'} : {};
  if(AUTH && token) h['X-Auth-Token'] = token;
  return h;
}
function ensureToken(){
  if(AUTH && !token){
    token = (prompt('Ange åtkomst-token för Ollama Studio:') || '').trim();
    if(token) localStorage.setItem('os_token', token);
  }
}
async function api(path, opts){
  ensureToken();
  const r = await fetch(path, opts);
  if(r.status === 401){ localStorage.removeItem('os_token'); token=''; throw new Error('Fel token'); }
  return r;
}

function humanSize(b){
  b = Number(b)||0; const u=['B','KB','MB','GB','TB']; let i=0;
  while(b>=1024 && i<u.length-1){ b/=1024; i++; }
  return (i<2? b.toFixed(0): b.toFixed(1)) + ' ' + u[i];
}
function esc(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function showView(v){
  document.getElementById('nav-models').classList.toggle('active', v==='models');
  document.getElementById('nav-discover').classList.toggle('active', v==='discover');
  document.getElementById('view-models').classList.toggle('hidden', v!=='models');
  document.getElementById('view-discover').classList.toggle('hidden', v!=='discover');
  document.getElementById('title').textContent = v==='models' ? 'Mina modeller' : 'Upptäck / Installera';
}
function setStatus(text, color){
  document.getElementById('statusDot').style.color = color;
  document.getElementById('statusText').textContent = text;
}
function toast(msg, err){
  const t = document.getElementById('toast');
  t.textContent = (err?'× ':'✓ ')+msg;
  t.className = 'toast show ' + (err?'err':'ok');
  setTimeout(()=>{ t.className='toast'; }, 2800);
}

async function refresh(){
  setStatus('Kontrollerar Ollama…', 'var(--amber)');
  try{
    const vr = await api('/api/version'); const v = await vr.json();
    const mr = await api('/api/models'); const data = await mr.json();
    const models = (data.models||[]).sort((a,b)=>a.name.localeCompare(b.name));
    installed = new Set(models.map(m=>m.name));
    setStatus('Ansluten · v'+(v.version||'?'), 'var(--green)');
    renderModels(models);
  }catch(e){
    setStatus('Ollama körs inte', 'var(--danger)');
    renderOffline();
  }
  renderCatalog();
}

function renderModels(models){
  const box = document.getElementById('modelsList');
  if(!models.length){
    document.getElementById('summary').textContent = '0 modeller';
    box.innerHTML = '<div class="empty"><h2>Inga modeller installerade än</h2>'
      + '<p>Gå till "Upptäck / Installera" för att ladda ner din första modell.</p>'
      + '<button class="btn accent" onclick="showView(\'discover\')">Öppna Upptäck / Installera</button></div>';
    return;
  }
  const total = models.reduce((s,m)=>s+(m.size||0),0);
  document.getElementById('summary').textContent = models.length+' modeller · '+humanSize(total)+' totalt';
  box.innerHTML = models.map(m=>{
    const d = m.details||{};
    const bits = [d.parameter_size, d.quantization_level, d.family, humanSize(m.size),
                  (m.modified_at||'').slice(0,10)].filter(Boolean).map(esc).join('     ·     ');
    return '<div class="card hoverable"><div class="top"><div>'
      + '<h3>'+esc(m.name)+'</h3><div class="meta">'+bits+'</div></div>'
      + '<button class="btn danger small" onclick="confirmDelete(\''+esc(m.name).replace(/'/g,"\\'")+'\')">✕ Avinstallera</button>'
      + '</div></div>';
  }).join('');
}
function renderOffline(){
  document.getElementById('summary').textContent = '';
  document.getElementById('modelsList').innerHTML =
    '<div class="empty"><h2>Kan inte nå Ollama</h2>'
    + '<p>Kontrollera att Ollama är installerat och startat på servern.<br>'
    + 'Kör:  <code>ollama serve</code>  (eller  <code>systemctl start ollama</code>).</p>'
    + '<button class="btn accent" onclick="refresh()">↻ Försök igen</button></div>';
}
function renderCatalog(){
  document.getElementById('catalogList').innerHTML = CATALOG.map(it=>{
    const done = installed.has(it.pull) || installed.has(it.pull.split(':')[0]+':latest');
    const right = done ? '<span class="installed">✓ Installerad</span>'
      : '<button class="btn accent" onclick="startPull(\''+it.pull+'\')">↓ Installera</button>';
    return '<div class="card"><div class="top"><div>'
      + '<h3>'+esc(it.name)+'<span class="chip">'+esc(it.tag)+'</span>'
      + '<span class="pull-name">'+esc(it.pull)+'</span></h3>'
      + '<div class="desc">'+esc(it.desc)+'</div>'
      + '<div class="meta">Storlek: '+esc(it.size)+'</div></div>'
      + '<div>'+right+'</div></div></div>';
  }).join('');
}

/* ---- Installera / ladda ner (strömmar status från servern) ---- */
function pullCustom(){
  const n = document.getElementById('customName').value.trim();
  if(!n){ toast('Skriv ett modellnamn först', true); return; }
  startPull(n);
}
async function startPull(name){
  if(pullController){ toast('En nedladdning pågår redan', true); return; }
  showView('discover');
  const panel = document.getElementById('dlPanel');
  panel.classList.add('show');
  document.getElementById('dlTitle').textContent = 'Laddar ner  '+name;
  document.getElementById('dlPct').textContent = '';
  document.getElementById('dlStatus').textContent = 'Förbereder…';
  const bar = document.getElementById('dlBar'); bar.style.width='0'; bar.style.background='var(--accent)';
  document.getElementById('dlCancel').textContent = 'Avbryt';

  pullController = new AbortController();
  try{
    const r = await api('/api/pull', {method:'POST', headers:headers(true),
                     body: JSON.stringify({name}), signal: pullController.signal});
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf='';
    while(true){
      const {done, value} = await reader.read();
      if(done) break;
      buf += dec.decode(value, {stream:true});
      let i;
      while((i = buf.indexOf('\n')) >= 0){
        const line = buf.slice(0,i).trim(); buf = buf.slice(i+1);
        if(line){ try{ onProgress(JSON.parse(line)); }catch(e){} }
      }
    }
    pullDone(name, 'success');
  }catch(e){
    if(e.name === 'AbortError') pullDone(name, 'cancelled');
    else pullDone(name, 'error', e.message);
  }finally{
    pullController = null;
  }
}
function onProgress(m){
  const status = m.status || '';
  if(m.total && m.completed != null){
    const f = m.completed/m.total;
    document.getElementById('dlBar').style.width = (f*100)+'%';
    document.getElementById('dlPct').textContent = (f*100).toFixed(0)+'%';
    document.getElementById('dlStatus').textContent = status+'   ·   '+humanSize(m.completed)+' / '+humanSize(m.total);
  }else{
    document.getElementById('dlStatus').textContent = status;
    if(status.includes('success')) document.getElementById('dlBar').style.width='100%';
  }
  if(m.error){ document.getElementById('dlStatus').textContent = 'Fel: '+m.error; }
}
function pullDone(name, outcome, detail){
  const bar = document.getElementById('dlBar');
  const cancel = document.getElementById('dlCancel');
  if(outcome==='success'){
    bar.style.width='100%'; bar.style.background='var(--green)';
    document.getElementById('dlPct').textContent='100%';
    document.getElementById('dlTitle').textContent='✓  '+name+' installerad';
    document.getElementById('dlStatus').textContent='Klar! Modellen finns nu under "Mina modeller".';
    document.getElementById('customName').value='';
    toast('"'+name+'" installerad');
  }else if(outcome==='cancelled'){
    document.getElementById('dlTitle').textContent='Avbruten';
    document.getElementById('dlStatus').textContent='Nedladdningen avbröts.';
    bar.style.background='var(--faint)';
  }else{
    document.getElementById('dlTitle').textContent='Nedladdning misslyckades';
    document.getElementById('dlStatus').textContent = detail || 'Ett fel uppstod.';
    bar.style.background='var(--danger)';
    toast('Misslyckades', true);
  }
  cancel.textContent='Stäng';
  refresh();
}
function cancelPull(){
  if(pullController){ pullController.abort(); }
  else { document.getElementById('dlPanel').classList.remove('show'); }
}

/* ---- Avinstallera ---- */
let deleteTarget = null;
function confirmDelete(name){
  deleteTarget = name;
  document.getElementById('mTitle').textContent = 'Avinstallera modell?';
  document.getElementById('mBody').textContent =
    'Vill du ta bort "'+name+'"?\n\nModellfilerna raderas permanent från disken.\nDu kan alltid ladda ner den igen senare.';
  document.getElementById('overlay').classList.add('show');
}
function closeModal(){ document.getElementById('overlay').classList.remove('show'); deleteTarget=null; }
document.getElementById('mConfirm').onclick = async ()=>{
  const name = deleteTarget; closeModal();
  if(!name) return;
  setStatus('Tar bort '+name+'…', 'var(--amber)');
  try{
    const r = await api('/api/delete', {method:'POST', headers:headers(true), body: JSON.stringify({name})});
    if(!r.ok) throw new Error('HTTP '+r.status);
    toast('"'+name+'" avinstallerad');
  }catch(e){ toast('Kunde inte ta bort: '+e.message, true); }
  refresh();
};

refresh();
</script>
</body>
</html>
"""


def render_page():
    return (PAGE
            .replace("__CATALOG_JSON__", json.dumps(CATALOG, ensure_ascii=False))
            .replace("__AUTH_ENABLED__", "true" if TOKEN else "false"))


# --------------------------------------------------------------------------
# HTTP-hanterare
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "OllamaStudio/" + APP_VERSION

    # Tystare loggning
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- hjälpare ----
    def _auth_ok(self):
        if not TOKEN:
            return True
        return self.headers.get("X-Auth-Token", "") == TOKEN

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _upstream_get(self, path):
        req = urllib.request.Request(OLLAMA_URL + path)
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ---- GET ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body = render_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/api/version", "/api/models"):
            if not self._auth_ok():
                return self._send_json({"error": "unauthorized"}, 401)
            upstream = "/api/version" if path == "/api/version" else "/api/tags"
            try:
                return self._send_json(self._upstream_get(upstream))
            except Exception as e:
                return self._send_json({"error": str(e)}, 502)

        self.send_error(404, "Not found")

    # ---- POST ----
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._auth_ok():
            return self._send_json({"error": "unauthorized"}, 401)

        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}
        name = (data.get("name") or "").strip()

        if path == "/api/delete":
            if not name:
                return self._send_json({"error": "name saknas"}, 400)
            try:
                body = json.dumps({"name": name}).encode()
                req = urllib.request.Request(OLLAMA_URL + "/api/delete", data=body,
                                             method="DELETE",
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    ok = resp.status in (200, 204)
                return self._send_json({"ok": ok})
            except urllib.error.HTTPError as e:
                return self._send_json({"error": "HTTP %d" % e.code}, e.code)
            except Exception as e:
                return self._send_json({"error": str(e)}, 502)

        if path == "/api/pull":
            if not name:
                return self._send_json({"error": "name saknas"}, 400)
            return self._stream_pull(name)

        self.send_error(404, "Not found")

    def _stream_pull(self, name):
        """Strömma NDJSON-status från Ollamas /api/pull vidare till webbläsaren."""
        try:
            body = json.dumps({"name": name, "stream": True}).encode()
            req = urllib.request.Request(OLLAMA_URL + "/api/pull", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            upstream = urllib.request.urlopen(req, timeout=60)
        except Exception as e:
            return self._send_json({"error": str(e)}, 502)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for raw in upstream:
                if not raw:
                    continue
                self.wfile.write(raw if raw.endswith(b"\n") else raw + b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # Webbläsaren avbröt – sluta strömma
            pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass


def _local_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return ips


def main():
    # Kontrollera att Ollama går att nå (varning, inte stopp)
    try:
        urllib.request.urlopen(OLLAMA_URL + "/api/version", timeout=3).read()
        ollama_ok = True
    except Exception:
        ollama_ok = False

    httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print("=" * 60)
    print(" %s Web  v%s" % (APP_TITLE, APP_VERSION))
    print("=" * 60)
    print(" Lyssnar på:     %s:%d" % (LISTEN_HOST, LISTEN_PORT))
    print(" Pratar med:     %s  (%s)" % (OLLAMA_URL, "OK" if ollama_ok else "svarar inte just nu"))
    print(" Åtkomstskydd:   %s" % ("token krävs (OLLAMA_STUDIO_TOKEN)" if TOKEN else "AV (öppet på nätverket)"))
    print("")
    print(" Öppna i webbläsaren från en annan dator:")
    for ip in _local_ips():
        print("     http://%s:%d" % (ip, LISTEN_PORT))
    print("     http://<serverns-namn>:%d" % LISTEN_PORT)
    print("")
    if not TOKEN:
        print(" TIPS: sätt OLLAMA_STUDIO_TOKEN=<hemligt> för att kräva lösenord,")
        print("       eftersom vem som helst på nätverket annars kan radera modeller.")
    print(" Avsluta med Ctrl+C.")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStänger av.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
