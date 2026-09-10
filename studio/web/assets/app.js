const CATALOG = __CATALOG_JSON__;
const AUTH = __AUTH_ENABLED__;
let token = AUTH ? (localStorage.getItem('os_token') || '') : '';
let installed = new Set();
let running = new Map();   // namn -> [ {backend, gpu, size_vram, expires_at, ...}, ... ]
let lastModels = [];       // senast hämtade modell-listan (för lätt omritning)
let pullController = null;
let chatMessages = [];     // konversationshistorik: {role, content}
let chatController = null;
let cfg = {backends:[{label:'Ollama', gpu:null}], multi:false, websearch:false, memory:false};   // /api/config
let uiPrefs = {};   // UI-val (modell, GPU, chattinställningar) – sparas i serverns databas
async function loadPrefs(){
  try{ const r = await api('/api/prefs', {headers: headers(false)}); uiPrefs = (await r.json()) || {}; }
  catch(e){ uiPrefs = {}; }
  applyPrefs();
}
function postPref(key, val){
  try{ api('/api/prefs', {method:'POST', headers:headers(true), body: JSON.stringify({[key]: val})}); }catch(e){}
}
function savePref(key, val){ uiPrefs[key] = val; postPref(key, val); }
let _prefTimers = {};
function savePrefDebounced(key, val, ms){       // för högfrekventa fält (text/slider)
  uiPrefs[key] = val;
  clearTimeout(_prefTimers[key]);
  _prefTimers[key] = setTimeout(()=>postPref(key, val), ms || 500);
}
function applyPrefs(){
  // Chattinställningar (finns oavsett vald vy)
  const P = uiPrefs || {};
  const sys = document.getElementById('csSystem'); if(sys && P.chat_system!=null) sys.value = P.chat_system;
  const temp = document.getElementById('csTemp');
  if(temp && P.chat_temp!=null){ temp.value = P.chat_temp; const tv=document.getElementById('csTempVal'); if(tv) tv.textContent = temp.value; }
  const ctx = document.getElementById('csCtx'); if(ctx && P.chat_ctx!=null) ctx.value = P.chat_ctx;
  const ws = document.getElementById('csWebsearch'); if(ws && P.chat_websearch!=null) ws.checked = (P.chat_websearch===true || P.chat_websearch==='true' || P.chat_websearch==='1');
  const mem = document.getElementById('csMemory'); if(mem && P.chat_memory!=null) mem.checked = (P.chat_memory===true || P.chat_memory==='true' || P.chat_memory==='1');
  // Dölj-filtret i "Upptäck / Installera"
  hideTooBig = (P.hide_too_big === '1' || P.hide_too_big === true);
  renderCatalog();
  // Modeller/GPU sätts av populate-funktionerna som läser uiPrefs
  populateChatModels(); populateBackends(); populateCodeModels();
  if(typeof updateChatWarning==='function') updateChatWarning();
}
let systemTimer = null;    // intervall för System-vyn
let lastSystem = null;     // senaste /api/system (för VRAM-varning i chatten)

function buildRunning(list){
  const map = new Map();
  for(const m of (list||[])){
    if(!map.has(m.name)) map.set(m.name, []);
    map.get(m.name).push(m);
  }
  return map;
}
function runSig(map){
  const arr = [];
  for(const [n, list] of map){ for(const e of list){ arr.push(n+'@'+(e.backend||'')); } }
  return arr.sort().join(',');
}
function gpuLabel(e){
  if(e.gpu !== null && e.gpu !== undefined && e.gpu !== '') return 'GPU '+e.gpu;
  if(cfg.multi && e.backend) return e.backend;
  return '';
}

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
function humanDate(s){
  // Visa datum i lokal tidszon (som skrivbordsappens human_date), inte råsträngen.
  if(!s) return '';
  const d = new Date(s);
  return isNaN(d) ? (''+s).slice(0,10) : d.toLocaleDateString('sv-SE');
}
function esc(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

const TITLES = {models:'Mina modeller', discover:'Upptäck / Installera', chat:'Chatta', system:'System / GPU', settings:'Inställningar', code:'Codex', train:'AI-träning'};
function showView(v){
  for(const k of ['models','discover','chat','system','settings','code','train']){
    document.getElementById('nav-'+k).classList.toggle('active', v===k);
    document.getElementById('view-'+k).classList.toggle('hidden', v!==k);
  }
  document.getElementById('title').textContent = TITLES[v] || '';
  if(v==='chat'){ populateChatModels(); renderConvoSelect(); renderChat(); updateChatWarning(); setTimeout(()=>document.getElementById('chatInput').focus(), 0); }
  if(v==='settings'){ loadSettingsForm(); }
  if(v==='code'){
    updateCodeView();
    if(cfg.code){
      populateCodeModels();
      loadRepos();
      if(localDir) loadLocalTree();
      else if(cfg.code_ws){ loadTree(); gitStatus(); analyzeWorkspace(false); }
      const rb=document.getElementById('codeRunBar'); if(rb) rb.style.display = cfg.code_run ? 'flex' : 'none';
      setTimeout(()=>{ const ci=document.getElementById('codeInput'); if(ci) ci.focus(); }, 0);
    }
  }
  if(v==='train'){ loadTrain(); }
  // System-vyn pollas bara medan den visas
  if(systemTimer){ clearInterval(systemTimer); systemTimer = null; }
  if(v==='system'){ fetchSystem(); systemTimer = setInterval(fetchSystem, 2500); }
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

function sleep(ms){ return new Promise(r=>setTimeout(r, ms)); }

async function waitForServer(){
  // Vänta tills servern svarar igen efter omstarten (upp till ~30 s).
  await sleep(1500);                       // ge processen tid att gå ner först
  for(let i=0;i<40;i++){
    try{
      const r = await fetch('/api/version', {headers: headers(false)});
      if(r.ok || r.status===401) return true;   // svar = servern är uppe igen
    }catch(e){ /* nere ännu – fortsätt polla */ }
    await sleep(750);
  }
  return false;
}

// "Uppdatera"-knappen: hämta senaste kod från GitHub, starta om servern och
// kör sedan den vanliga uppdateringen (refresh) / ladda om sidan med nya UI:t.
async function updateApp(){
  if(!confirm('Hämta senaste kod från GitHub och starta om Ollama Studio?\n\n'
            + 'Finns ny kod startas servern om och sidan laddas om – pågående chatt '
            + 'eller Codex-körning avbryts då. Är allt redan uppdaterat sker ingen omstart.')){
    return;
  }
  setStatus('Hämtar senaste kod från GitHub…', 'var(--amber)');
  let res;
  try{
    const r = await api('/api/self-update', {method:'POST', headers: headers(true)});
    res = await r.json();
  }catch(e){
    setStatus('Uppdatering misslyckades', 'var(--danger)');
    toast('Kunde inte uppdatera: ' + (e && e.message || e), true);
    refresh();
    return;
  }
  if(!res.ok){
    setStatus('Uppdatering misslyckades', 'var(--danger)');
    toast(res.output || 'Uppdatering misslyckades', true);
    refresh();                              // ändå köra vanlig uppdatering
    return;
  }
  if(res.restart){
    setStatus('Ny kod hämtad — startar om servern…', 'var(--amber)');
    toast('Uppdaterad – startar om servern');
    const back = await waitForServer();
    if(back){ location.reload(); return; }  // ladda om → nya UI:t + refresh() vid init
    setStatus('Servern svarar inte efter omstarten', 'var(--danger)');
    toast('Servern kom inte tillbaka i tid – ladda om sidan manuellt', true);
    return;
  }
  // Redan senaste versionen → bara den vanliga uppdateringen.
  toast('Redan senaste versionen');
  refresh();
}

async function refresh(){
  setStatus('Kontrollerar Ollama…', 'var(--amber)');
  try{
    const vr = await api('/api/version'); const v = await vr.json();
    const mr = await api('/api/models'); const data = await mr.json();
    const models = (data.models||[]).sort((a,b)=>a.name.localeCompare(b.name));
    installed = new Set(models.map(m=>m.name));
    try{
      const pr = await api('/api/running'); const pd = await pr.json();
      running = buildRunning(pd.models);
    }catch(e){ running = new Map(); }
    try{ const sr = await api('/api/system'); if(sr.ok) lastSystem = await sr.json(); }catch(e){}
    lastModels = models;
    populateChatModels();
    setStatus('Ansluten · v'+(v.version||'?'), 'var(--green)');
    renderModels(models);
  }catch(e){
    setStatus('Ollama körs inte', 'var(--danger)');
    renderOffline();
  }
  renderCatalog();
}

function runMeta(r){
  // Beskriv var (GPU) en inläst modell körs, hur den använder minne + när den frigörs
  const parts = [];
  const gl = gpuLabel(r);
  if(gl) parts.push(gl);
  const vram = Number(r.size_vram)||0, size = Number(r.size)||0;
  if(vram <= 0) parts.push('körs på CPU/RAM');
  else if(vram >= size) parts.push('helt på GPU · '+humanSize(vram)+' VRAM');
  else parts.push('GPU+CPU · '+humanSize(vram)+' i VRAM');
  if(r.expires_at){
    const d = new Date(r.expires_at);
    if(!isNaN(d)) parts.push('frigörs '+d.toLocaleTimeString('sv-SE',{hour:'2-digit',minute:'2-digit'}));
  }
  return parts.join(' · ');
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
  const activeNames = models.filter(m=>running.has(m.name)).map(m=>m.name);
  let summary = models.length+' modeller · '+humanSize(total)+' totalt';
  if(activeNames.length) summary += ' · '+activeNames.length+' körs nu';
  document.getElementById('summary').textContent = summary;

  // Banner högst upp: vilken modell är aktiv (och på vilken GPU) just nu?
  let banner;
  if(activeNames.length){
    const items = activeNames.map(n=>{
      const gpus = running.get(n).map(gpuLabel).filter(Boolean);
      return esc(n) + (gpus.length ? ' ('+gpus.join(', ')+')' : '');
    });
    banner = '<div class="banner">● Aktiv i minnet just nu: '+items.join(',&nbsp; ')+'</div>';
  }else{
    banner = '<div class="meta" style="margin:6px 2px 8px">Ingen modell är inläst i minnet just nu '
           + '(en modell blir aktiv när den används, t.ex. via <code>ollama run</code> eller ett chattanrop).</div>';
  }

  const cards = models.map(m=>{
    const d = m.details||{};
    const bits = [d.parameter_size, d.quantization_level, d.family, humanSize(m.size),
                  humanDate(m.modified_at)].filter(Boolean).map(esc).join('     ·     ');
    const r = running.get(m.name);
    let liveChip = '', liveMeta = '';
    if(r){
      const gpus = r.map(gpuLabel).filter(Boolean);
      liveChip = '<span class="chip live">● Körs nu'+(gpus.length ? ' · '+esc(gpus.join(', ')) : '')+'</span>';
      liveMeta = r.map(e=>'<div class="meta live">'+esc(runMeta(e))+'</div>').join('');
    }
    return '<div class="card hoverable"><div class="top"><div>'
      + '<h3>'+esc(m.name)+liveChip+'</h3><div class="meta">'+bits+'</div>'+liveMeta+'</div>'
      // data-name (HTML-escapat) i stället för handbyggd JS-sträng: modellnamn med
      // ' eller " bryter inte längre onclick-anropet (board #14).
      + '<button class="btn danger small" data-del="'+esc(m.name)+'">✕ Avinstallera</button>'
      + '</div></div>';
  }).join('');
  box.innerHTML = banner + cards;
  box.onclick = onModelsClick;   // delegerad klickhantering (tål specialtecken i namn)
}
function onModelsClick(ev){
  const btn = ev.target.closest('button[data-del]');
  if(btn) confirmDelete(btn.getAttribute('data-del'));
}
function renderOffline(){
  document.getElementById('summary').textContent = '';
  document.getElementById('modelsList').innerHTML =
    '<div class="empty"><h2>Kan inte nå Ollama</h2>'
    + '<p>Kontrollera att Ollama är installerat och startat på servern.<br>'
    + 'Kör:  <code>ollama serve</code>  (eller  <code>systemctl start ollama</code>).</p>'
    + '<button class="btn accent" onclick="refresh()">↻ Försök igen</button></div>';
}
function estBytesFromSizeStr(s){
  const m = (''+s).match(/([\d.]+)\s*(TB|GB|MB)/i);
  if(!m) return 0;
  const n = parseFloat(m[1]);
  const u = m[2].toUpperCase();
  const mult = u==='TB' ? 1024**4 : (u==='GB' ? 1024**3 : 1024**2);
  return n * mult;
}
function maxGpuVramBytes(){
  const gpus = (lastSystem && lastSystem.gpus) || [];
  let max = 0;
  for(const g of gpus){ if(g.mem_total_mb) max = Math.max(max, g.mem_total_mb*1024*1024); }
  return max;
}
/* ---- Hur stor är modellen, och ryms den i den här datorn? ----
   Ollama laddar det som får plats på GPU:n och kör resten på CPU/RAM, så
   "kan köras" = ryms i VRAM + RAM. Allt är uppskattningar: katalogen har
   storlek i text, Ollamas bibliotek har parametertaggar (t.ex. "8b") och
   Hugging Face-namn innehåller oftast storleken. Vet vi inget gissar vi
   inte – då visas modellen alltid. */
const BYTES_PER_B_PARAM = 0.62 * 1024*1024*1024;   // ≈ Q4_K_M, Ollamas standard
function paramsFromText(text){
  const m = String(text||'').match(/(\d+(?:[.,]\d+)?)\s*b\b/i);
  return m ? parseFloat(m[1].replace(',', '.')) : 0;
}
function estModelBytes(it){
  if(it.size){                                   // "~4.9 GB" ur katalogen
    const b = estBytesFromSizeStr(it.size);
    if(b) return b;
  }
  if(it.sizes && it.sizes.length){               // "0.6b, 1.7b, 8b" ur biblioteket
    const params = it.sizes.map(paramsFromText).filter(Boolean);
    if(params.length) return Math.min(...params) * BYTES_PER_B_PARAM;   // minsta varianten
  }
  const fromName = paramsFromText((it.pull||'') + ' ' + (it.name||''));
  return fromName ? fromName * BYTES_PER_B_PARAM : 0;
}
function systemRamBytes(){
  return (lastSystem && lastSystem.mem && lastSystem.mem.total) || 0;
}
function hardwareCapacityBytes(){
  const ram = systemRamBytes();
  // Lämna lite RAM åt operativsystemet – annars swappar den ihjäl sig.
  return maxGpuVramBytes() + Math.floor(ram * 0.85);
}
function fitsHardware(it){
  const cap = hardwareCapacityBytes(), need = estModelBytes(it) * 1.15;
  if(!(cap > 0 && need > 0)) return true;        // vet vi inget → göm aldrig
  return need <= cap;
}
function fitLabel(it){
  const need = estModelBytes(it) * 1.15;
  if(!need) return '';
  const vram = maxGpuVramBytes(), cap = hardwareCapacityBytes();
  if(!cap) return '';
  if(vram > 0 && need <= vram)
    return '  ·  <span style="color:var(--green)">≈ passar din GPU</span>';
  if(need <= cap)
    return vram
      ? '  ·  <span style="color:var(--amber)">≈ körs delvis på CPU (långsammare)</span>'
      : '  ·  <span style="color:var(--amber)">≈ körs på CPU (långsammare)</span>';
  return '  ·  <span style="color:var(--danger)">⚠ för stor för din hårdvara</span>';
}
function hardwareSummary(){
  const vram = maxGpuVramBytes(), ram = systemRamBytes();
  const parts = [];
  if(vram) parts.push(humanSize(vram)+' VRAM');
  if(ram) parts.push(humanSize(ram)+' RAM');
  return parts.join(' + ');
}
function isInstalled(pull){
  return installed.has(pull) || installed.has(pull.split(':')[0]+':latest');
}
function sourceChip(source){
  return source === 'hf'
    ? '<span class="chip" style="background:#2a2338;color:var(--accent-hov)">Hugging Face</span>'
    : '<span class="chip">Ollama</span>';
}

/* Ett kort per träff – samma utseende oavsett källa (katalog, bibliotek, HF). */
function modelCard(it, index){
  const done = isInstalled(it.pull);
  const buttons = [];
  if(it.source === 'hf')
    buttons.push('<button class="btn ghost small" onclick="hfToggleQuants('+index+')">Varianter</button>');
  buttons.push(done
    ? '<span class="installed">✓ Installerad</span>'
    : '<button class="btn accent" onclick="startPull(\''+esc(it.pull)+'\')">↓ Installera</button>');

  const bits = [];
  if(it.size) bits.push('Storlek: '+esc(it.size)+fitLabel(it));
  else if(estModelBytes(it)) bits.push('Uppskattad storlek: ~'+humanSize(estModelBytes(it))+fitLabel(it));
  if(it.sizes && it.sizes.length) bits.push('Varianter: '+esc(it.sizes.join(', ')));
  if(it.downloads) bits.push(it.downloads.toLocaleString('sv-SE')+' nedladdningar');
  if(it.likes) bits.push('♥ '+it.likes);

  const title = it.url
    ? '<a href="'+esc(it.url)+'" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">'
      + esc(it.name || it.pull) + '</a>'
    : esc(it.name || it.pull);

  return '<div class="card"><div class="top"><div>'
    // Källchippet behövs bara när listan blandar källor, dvs. vid sökning
    + '<h3>'+title+(searchResults ? sourceChip(it.source) : '')
    + (it.tag ? '<span class="chip">'+esc(it.tag)+'</span>' : '')
    + '<span class="pull-name">'+esc(it.pull)+'</span></h3>'
    + (it.desc ? '<div class="desc">'+esc(it.desc)+'</div>' : '')
    + (it.gated ? '<div class="hf-gated">⚠ Kräver godkännande på Hugging Face (gated) – '
        + 'Ollama kan inte hämta den utan det.</div>' : '')
    + (bits.length ? '<div class="meta">'+bits.join('  ·  ')+'</div>' : '')
    + '</div><div style="display:flex;gap:8px;align-items:center">'+buttons.join('')+'</div></div>'
    + (it.source === 'hf' ? '<div class="hf-quants" id="hfq'+index+'" style="display:none"></div>' : '')
    + '</div>';
}

/* Utan sökord visas den kurerade listan; med sökord visas träffarna. */
let searchResults = null;      // {library:[], hf:[]} – null = visa katalogen
let hfModels = [];             // HF-träffarna i listan just nu (för "Varianter")
let hideTooBig = false;        // kryssrutan "dölj det som inte får plats"

function toggleFitFilter(){
  hideTooBig = document.getElementById('fitOnly').checked;
  savePref('hide_too_big', hideTooBig ? '1' : '0');
  renderCatalog();
}
function showTooBig(){        // "visa ändå"-länken under listan
  const box = document.getElementById('fitOnly');
  if(box){ box.checked = false; }
  hideTooBig = false;
  savePref('hide_too_big', '0');
  renderCatalog();
}
function updateFitHint(){
  const box = document.getElementById('fitOnly');
  const hint = document.getElementById('fitHint');
  if(!box || !hint) return;
  const hw = hardwareSummary();
  box.checked = hideTooBig;
  if(!hardwareCapacityBytes()){
    box.disabled = true;
    box.checked = false;
    hint.textContent = 'Hårdvaran kunde inte läsas av – filtret är avstängt.';
    return;
  }
  box.disabled = false;
  hint.textContent = 'Din hårdvara: ' + hw + (maxGpuVramBytes()
    ? ' – Ollama lägger det som får plats på GPU:n och kör resten på CPU.'
    : ' – ingen GPU hittad, så allt körs på CPU (långsammare).');
}

function renderCatalog(){
  const list = document.getElementById('catalogList');
  updateFitHint();
  const searching = !!searchResults;
  const lib = searching ? (searchResults.library || [])
                        : CATALOG.map(it=>Object.assign({source:'ollama'}, it));
  const hf = searching ? (searchResults.hf || []) : [];
  // Filtrera bort det som inte kan köras – men bara när vi vet storleken.
  const keptLib = hideTooBig ? lib.filter(fitsHardware) : lib;
  const keptHf  = hideTooBig ? hf.filter(fitsHardware)  : hf;
  hfModels = keptHf;
  const hidden = (lib.length - keptLib.length) + (hf.length - keptHf.length);

  let html = keptLib.map(it=>modelCard(it, -1)).join('')
           + keptHf.map((it,i)=>modelCard(it, i)).join('');
  if(!keptLib.length && !keptHf.length){
    html = hidden
      ? '<div class="empty">Alla träffar är för stora för den här datorn. '
        + '<a onclick="showTooBig()" style="color:var(--accent-hov);cursor:pointer">Visa dem ändå</a></div>'
      : (searching
          ? '<div class="empty">Inga modeller matchade sökningen. Prova ett kortare ord, '
            + 'eller skriv ett exakt namn och klicka "↓ Ladda ner".</div>'
          : '<div class="empty">Inga modeller att visa.</div>');
  }else if(hidden){
    html += '<div class="hidden-note">' + hidden + (hidden === 1 ? ' modell dold' : ' modeller dolda')
          + ' som inte får plats på din hårdvara · '
          + '<a onclick="showTooBig()">visa ändå</a></div>';
  }
  list.innerHTML = html;
}

/* ---- Sökning: ett fält, båda källorna ---- */
let searchTimer = null, searchSeq = 0;
function onSearchInput(){
  clearTimeout(searchTimer);
  const q = document.getElementById('customName').value.trim();
  if(!q){                                    // tomt fält → tillbaka till listan
    searchResults = null;
    document.getElementById('searchHint').textContent = '';
    renderCatalog();
    return;
  }
  searchTimer = setTimeout(()=>runSearch(q), 350);    // vänta ut skrivandet
}
async function runSearch(q){
  const hint = document.getElementById('searchHint');
  const seq = ++searchSeq;
  // Katalogträffarna finns redan i webbläsaren – visa dem direkt, fyll på sedan.
  const needle = q.toLowerCase();
  searchResults = {library: CATALOG.filter(it=>
      (it.pull+' '+it.name+' '+it.tag+' '+it.desc).toLowerCase().includes(needle))
      .map(it=>Object.assign({source:'ollama'}, it)), hf: []};
  renderCatalog();
  hint.textContent = 'Söker i Ollamas bibliotek och på Hugging Face…';
  try{
    const r = await api('/api/search?q='+encodeURIComponent(q), {headers: headers(false)});
    const d = await r.json();
    if(seq !== searchSeq) return;                     // ett nyare sök hann före
    if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
    searchResults = d;
    const lib = (d.library||[]).length, hf = (d.hf||[]).length;
    hint.textContent = (lib+hf)
      ? ((lib+hf)+' träffar · '+lib+' i Ollamas bibliotek, '+hf+' på Hugging Face')
      : 'Inga träffar.';
    renderCatalog();
  }catch(e){
    if(seq !== searchSeq) return;
    hint.textContent = 'Kunde inte söka på nätet ('+e.message
      + ') – visar träffar ur den inbyggda listan.';
  }
}

/* ======================= AI-träning (Soup) ======================= */
const TRAIN_META = __TRAIN_JSON__;      // basmodeller, uppgifter, hårdvaruprofiler
let trainStatus = null;                 // senaste /api/train/status
let trainForm = {task:'sft', profile:'4gb', epochs:3, max_length:1024, lr:'2e-5', lora_r:16};
let trainRows = [];                     // tabellen i steg 1
let trainTimer = null;                  // pollning under körning
let trainLogNext = 0;
let trainYamlTimer = null;

function toggleTrainHelp(){
  const box = document.getElementById('trainHelp');
  const open = box.style.display === 'none';
  box.style.display = open ? 'block' : 'none';
  document.getElementById('trHelpBtn').textContent = open ? '✕ Dölj instruktioner' : '📖 Instruktioner';
}
function toggleYaml(){
  const box = document.getElementById('trYamlBox');
  const open = box.style.display === 'none';
  box.style.display = open ? 'block' : 'none';
  document.getElementById('trYamlBtn').textContent = open
    ? '⚙ Dölj konfigurationen' : '⚙ Visa konfigurationen (soup.yaml)';
  if(open) refreshYaml();
}
function toggleTrainLog(){
  const box = document.getElementById('trLog');
  const open = box.style.display === 'none';
  box.style.display = open ? 'block' : 'none';
  document.getElementById('trLogBtn').textContent = open ? '📜 Dölj logg' : '📜 Visa logg';
  if(open) box.scrollTop = box.scrollHeight;
}

async function loadTrain(){
  document.getElementById('trainOff').style.display = cfg.train ? 'none' : 'block';
  document.getElementById('trainWrap').style.display = cfg.train ? 'block' : 'none';
  if(!cfg.train) return;
  if(!trainRows.length) trainRows = [{instruction:'',input:'',output:''},
                                     {instruction:'',input:'',output:''},
                                     {instruction:'',input:'',output:''}];
  renderTrainRows(); renderTrainPickers();
  try{
    const saved = uiPrefs.train_form ? JSON.parse(uiPrefs.train_form) : null;
    if(saved) trainForm = Object.assign(trainForm, saved);
  }catch(e){}
  applyTrainForm();
  await refreshTrainStatus();
}

async function refreshTrainStatus(){
  try{
    const r = await api('/api/train/status', {headers: headers(false)});
    trainStatus = await r.json();
  }catch(e){ return; }
  if(!trainStatus || trainStatus.enabled === false) return;
  renderTrainTop(); renderTrainSetup(); renderTrainFiles(); renderTrainRuns();
  const job = trainStatus.job;
  if(job){ renderTrainJob(job); if(job.state === 'kör') startTrainPolling(); }
  updateTrainSteps();
}

function renderTrainTop(){
  const s = trainStatus, soup = s.soup || {};
  const pill = document.getElementById('trPillSoup');
  if(soup.found){
    pill.className = 'tr-pill ok';
    pill.innerHTML = '✓ Soup <b>' + esc(soup.version || 'installerat') + '</b>';
  }else{
    pill.className = 'tr-pill warn';
    pill.innerHTML = '⚠ Soup är inte installerat';
  }
  const gpu = document.getElementById('trPillGpu');
  if(s.gpu && s.gpu.vram_mb){
    gpu.className = 'tr-pill';
    gpu.innerHTML = '🖥 <b>' + esc(s.gpu.name || 'GPU') + '</b> · '
      + (s.gpu.vram_mb/1024).toFixed(0) + ' GB VRAM';
  }else{
    gpu.className = 'tr-pill warn';
    gpu.innerHTML = '🖥 Ingen GPU hittad – träning på CPU är långsam';
  }
  const dir = document.getElementById('trPillDir');
  dir.innerHTML = '📁 <b>' + esc(s.workspace || '') + '</b>';
  dir.title = 'Konfig, dataset och tränade modeller hamnar här';
}

function renderTrainSetup(){
  const s = trainStatus, soup = s.soup || {}, box = document.getElementById('trainSetup');
  if(soup.found){ box.innerHTML = ''; return; }
  const job = s.job && s.job.kind === 'install' ? s.job : null;
  box.innerHTML =
    '<div class="tr-step"><div class="tr-num">0</div><div class="tr-body">'
    + '<h2>Installera träningsmotorn</h2>'
    + '<p class="sub">Själva träningen görs av <a href="' + esc(soup.url||'#') + '" target="_blank" '
    + 'rel="noopener" style="color:var(--accent-hov)">Soup</a> – ett fristående open source-verktyg '
    + 'som inte följer med Ollama Studio. Installera det en gång, sedan är det klart.</p>'
    + '<div class="tr-note">Kommandot som körs: <code style="color:var(--accent-hov)">pip install "'
    + esc(soup.package||'soup-cli[train]') + '"</code><br>Det laddar ner PyTorch och kringpaket '
    + '(flera GB) och tar några minuter. Serverns Python är <b>' + esc(s.python||'?') + '</b>'
    + (s.python_ok ? '' : ' – Soup kräver ' + esc(soup.python_needed||'3.10–3.12')
        + ', så installationen kan misslyckas här') + '.</div>'
    + '<div class="tr-actions">'
    + '<button class="btn accent" onclick="trainInstall()"' + (job && job.state==='kör' ? ' disabled' : '')
    + '>⬇ Installera Soup</button>'
    + '<span class="sub" style="margin:0">…eller kör kommandot själv på servern och klicka '
    + '<a href="#" onclick="refreshTrainStatus();return false" style="color:var(--accent-hov)">uppdatera</a>.</span>'
    + '</div></div></div>';
}

/* ---- Steg 1: data ---- */
function trainDataTab(which){
  for(const [tab, box] of [['trTabTable','trDataTable'],['trTabFile','trDataFile'],['trTabPaste','trDataPaste']]){
    const on = tab.toLowerCase().includes(which);
    document.getElementById(tab).classList.toggle('sel', on);
    document.getElementById(box).style.display = on ? 'block' : 'none';
  }
}
function renderTrainRows(){
  document.getElementById('trRows').innerHTML = trainRows.map((r,i)=>
    '<tr>'
    + '<td><textarea placeholder="T.ex. Vad är vår returpolicy?" oninput="trainRowEdit('+i+',\'instruction\',this.value)">'+esc(r.instruction||'')+'</textarea></td>'
    + '<td><textarea placeholder="(lämna tomt oftast)" oninput="trainRowEdit('+i+',\'input\',this.value)">'+esc(r.input||'')+'</textarea></td>'
    + '<td><textarea placeholder="Svaret du vill få" oninput="trainRowEdit('+i+',\'output\',this.value)">'+esc(r.output||'')+'</textarea></td>'
    + '<td><button class="del" title="Ta bort raden" onclick="trainDelRow('+i+')">✕</button></td></tr>').join('');
}
function trainRowEdit(i, field, value){ if(trainRows[i]) trainRows[i][field] = value; }
function trainAddRow(){ trainRows.push({instruction:'',input:'',output:''}); renderTrainRows(); }
function trainDelRow(i){ trainRows.splice(i,1); if(!trainRows.length) trainAddRow(); else renderTrainRows(); }

async function trainDataPost(body){
  const r = await api('/api/train/dataset', {method:'POST', headers:headers(true),
                                             body: JSON.stringify(body)});
  const d = await r.json();
  if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
  return d;
}
async function trainSaveRows(){
  const filled = trainRows.filter(r=>(r.instruction||'').trim() && (r.output||'').trim());
  if(!filled.length){ toast('Fyll i minst en rad med fråga och svar', true); return; }
  try{
    const name = (document.getElementById('trDataName').value||'').trim() || 'mitt-dataset.jsonl';
    const d = await trainDataPost({action:'save', name, rows: filled});
    afterDatasetSaved(d);
  }catch(e){ toast('Kunde inte spara: '+e.message, true); }
}
async function trainSavePaste(){
  try{
    const name = (document.getElementById('trPasteName').value||'').trim() || 'mitt-dataset.jsonl';
    const d = await trainDataPost({action:'paste', name,
                                   content: document.getElementById('trPaste').value});
    afterDatasetSaved(d);
  }catch(e){ toast('Kunde inte spara: '+e.message, true); }
}
async function trainDemoData(){
  try{
    const d = await trainDataPost({action:'demo', name:'exempeldata.jsonl'});
    trainRows = TRAIN_META.demo_rows.map(r=>Object.assign({}, r));
    renderTrainRows();
    afterDatasetSaved(d);
    toast('Exempeldata skapad – nu kan du köra hela flödet');
  }catch(e){ toast('Kunde inte skapa exempeldata: '+e.message, true); }
}
function afterDatasetSaved(info){
  trainForm.data = info.path;
  renderDataInfo(info);
  refreshTrainStatus();
  refreshYaml();
}
async function trainPickFile(){
  const path = document.getElementById('trFileSelect').value;
  if(!path){ return; }
  trainForm.data = path;
  try{
    const r = await api('/api/train/dataset?path='+encodeURIComponent(path), {headers: headers(false)});
    const d = await r.json();
    if(d.error) throw new Error(d.error);
    renderDataInfo(d);
  }catch(e){ document.getElementById('trDataInfo').innerHTML =
    '<div class="tr-note bad">Kunde inte läsa filen: '+esc(e.message)+'</div>'; }
  refreshYaml(); updateTrainSteps();
}
function renderTrainFiles(){
  const sel = document.getElementById('trFileSelect');
  const files = (trainStatus.datasets||[]);
  sel.innerHTML = files.length
    ? files.map(f=>'<option value="'+esc(f.path)+'">'+esc(f.name)+'  ('+humanSize(f.size)+')</option>').join('')
    : '<option value="">Inga filer i mappen data/ ännu</option>';
  if(trainForm.data && files.some(f=>f.path===trainForm.data)) sel.value = trainForm.data;
  document.getElementById('trFileHint').innerHTML = 'Lägg egna filer i <code>'
    + esc((trainStatus.workspace||'')+'/data') + '</code> på servern så dyker de upp här.';
}
function renderDataInfo(info){
  if(!info){ document.getElementById('trDataInfo').innerHTML=''; return; }
  const rows = (info.examples||[]).map(x=>
    '<div class="row"><div class="q">▸ '+esc(x.in || '(ingen fråga)')+'</div>'
    + '<div class="a">→ '+esc(x.ut||'')+'</div></div>').join('');
  const probs = (info.problems||[]).map(p=>'<div class="tr-note warn">'+esc(p)+'</div>').join('');
  document.getElementById('trDataInfo').innerHTML =
    '<div class="tr-note good">✓ <b>'+esc(info.path||'')+'</b> – '+info.rows+' rader · format <b>'
    + esc(info.format)+'</b> · ca '+(info.est_tokens||0).toLocaleString('sv-SE')+' tokens</div>'
    + probs + (rows ? '<div class="tr-prev">'+rows+'</div>' : '');
}

/* ---- Steg 2: modell och metod ---- */
function renderTrainPickers(){
  document.getElementById('trBases').innerHTML = TRAIN_META.bases.map(b=>
    '<div class="tr-pick" data-base="'+esc(b.id)+'" onclick="trainPickBase(\''+esc(b.id)+'\')">'
    + '<div class="t">'+esc(b.name)+(b.gated?' <span class="chip" style="background:#3a2f1a;color:var(--amber)">⚠ gated</span>':'')+'</div>'
    + '<div class="d">'+esc(b.note)+'</div>'
    + '<div class="s">'+esc(b.id)+' · '+esc(b.size)+'</div></div>').join('');
  document.getElementById('trTasks').innerHTML = TRAIN_META.tasks.map(t=>
    '<div class="tr-pick" data-task="'+esc(t.id)+'" onclick="trainPickTask(\''+esc(t.id)+'\')">'
    + '<div class="t">'+esc(t.name)+'</div><div class="d">'+esc(t.desc)+'</div>'
    + '<div class="s">Data: '+esc(t.data)+'</div></div>').join('');
  document.getElementById('trProfiles').innerHTML = TRAIN_META.profiles.map(p=>
    '<div class="tr-pick" data-profile="'+esc(p.id)+'" onclick="trainPickProfile(\''+esc(p.id)+'\')">'
    + '<div class="t">'+esc(p.name)+'</div><div class="d">'+esc(p.desc)+'</div>'
    + '<div class="s">'+esc(p.quantization)+(p.stream_layers?' · lagerströmning':'')
    + ' · LoRA r='+p.lora_r+' · '+p.max_length+' tokens</div></div>').join('');
}
function markPick(attr, value){
  document.querySelectorAll('[data-'+attr+']').forEach(el=>
    el.classList.toggle('sel', el.getAttribute('data-'+attr) === value));
}
function trainPickBase(id){
  trainForm.base = id; document.getElementById('trBaseCustom').value = '';
  markPick('base', id); trainFormChanged();
}
function trainCustomBase(){
  const v = document.getElementById('trBaseCustom').value.trim();
  if(v){ trainForm.base = v; markPick('base', ''); }
  trainFormChanged();
}
function trainPickTask(id){ trainForm.task = id; markPick('task', id); trainFormChanged(); }
function trainPickProfile(id){
  trainForm.profile = id;
  const p = TRAIN_META.profiles.find(x=>x.id===id);
  if(p){                                    // profilen sätter de tekniska fälten
    trainForm.quantization = p.quantization; trainForm.stream_layers = p.stream_layers;
    trainForm.lora_r = p.lora_r; trainForm.lora_alpha = p.lora_alpha;
    trainForm.max_length = p.max_length; trainForm.batch_size = p.batch_size;
    document.getElementById('trMaxLen').value = p.max_length;
    document.getElementById('trLoraR').value = p.lora_r;
  }
  markPick('profile', id); applyTrainForm(); trainFormChanged();
}
function applyTrainForm(){
  const set = (id,v)=>{ const el=document.getElementById(id); if(el && v!=null) el.value = v; };
  set('trName', trainForm.name || '');
  set('trEpochs', trainForm.epochs || 3);
  set('trMaxLen', trainForm.max_length || 1024);
  set('trLoraR', trainForm.lora_r || 16);
  set('trLr', trainForm.lr || '2e-5');
  if(trainForm.base && !TRAIN_META.bases.some(b=>b.id===trainForm.base))
    set('trBaseCustom', trainForm.base);
  markPick('base', trainForm.base || '');
  markPick('task', trainForm.task || 'sft');
  markPick('profile', trainForm.profile || '4gb');
  document.getElementById('trEpochsVal').textContent = trainForm.epochs || 3;
  document.getElementById('trMaxLenVal').textContent = trainForm.max_length || 1024;
  document.getElementById('trLoraRVal').textContent = trainForm.lora_r || 16;
}
function trainFormChanged(){
  trainForm.name = document.getElementById('trName').value.trim();
  trainForm.epochs = parseInt(document.getElementById('trEpochs').value, 10);
  trainForm.max_length = parseInt(document.getElementById('trMaxLen').value, 10);
  trainForm.lora_r = parseInt(document.getElementById('trLoraR').value, 10);
  trainForm.lora_alpha = trainForm.lora_r * 2;
  trainForm.lr = document.getElementById('trLr').value;
  document.getElementById('trEpochsVal').textContent = trainForm.epochs;
  document.getElementById('trMaxLenVal').textContent = trainForm.max_length;
  document.getElementById('trLoraRVal').textContent = trainForm.lora_r;
  updateTrainSteps();
  clearTimeout(trainYamlTimer);
  trainYamlTimer = setTimeout(refreshYaml, 350);      // vänta ut skrivandet
}
async function refreshYaml(){
  try{
    const r = await api('/api/train/config', {method:'POST', headers:headers(true),
      body: JSON.stringify({form: trainForm, save: true})});
    const d = await r.json();
    if(d.yaml) document.getElementById('trYaml').textContent = d.yaml;
  }catch(e){}
}

/* ---- Steg 3: körningen ---- */
function updateTrainSteps(){
  const hasData = !!trainForm.data, hasModel = !!trainForm.base;
  document.getElementById('trNum1').classList.toggle('done', hasData);
  document.getElementById('trNum2').classList.toggle('done', hasData && hasModel);
  const job = trainStatus && trainStatus.job;
  const trained = (trainStatus && (trainStatus.runs||[]).some(r=>r.has_model));
  document.getElementById('trNum3').classList.toggle('done', trained);
  document.getElementById('trNum4').classList.toggle('done',
    !!(trainStatus && (trainStatus.runs||[]).some(r=>r.gguf)));
  const btn = document.getElementById('trStartBtn');
  const soupOk = trainStatus && trainStatus.soup && trainStatus.soup.found;
  const busy = job && job.state === 'kör';
  btn.disabled = !hasData || !hasModel || !soupOk || busy;
  let hint = '';
  if(!soupOk) hint = 'Installera Soup först (steg 0 ovan).';
  else if(!hasData) hint = 'Välj eller spara ett dataset i steg 1.';
  else if(!hasModel) hint = 'Välj en basmodell i steg 2.';
  else if(busy) hint = 'En körning pågår.';
  else hint = 'Tränar ' + (trainForm.base||'') + ' på ' + (trainForm.data||'') + '.';
  document.getElementById('trStartHint').textContent = hint;
}
async function trainStart(){
  try{
    const r = await api('/api/train/start', {method:'POST', headers:headers(true),
      body: JSON.stringify({form: trainForm})});
    const d = await r.json();
    if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
    trainLogNext = 0;
    renderTrainJob(d.job); startTrainPolling(); updateTrainSteps();
    toast('Träningen har startat');
  }catch(e){ toast('Kunde inte starta: '+e.message, true); }
}
async function trainStop(){
  try{
    await api('/api/train/stop', {method:'POST', headers:headers(true), body:'{}'});
    toast('Avbryter körningen…');
  }catch(e){ toast('Kunde inte avbryta: '+e.message, true); }
}
async function trainInstall(){
  try{
    const r = await api('/api/train/install', {method:'POST', headers:headers(true), body:'{}'});
    const d = await r.json();
    if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
    trainLogNext = 0;
    renderTrainJob(d.job); startTrainPolling();
    document.getElementById('trLog').style.display = 'block';
    toast('Installerar Soup – det tar några minuter');
  }catch(e){ toast('Kunde inte installera: '+e.message, true); }
}
async function trainExport(run){
  try{
    const r = await api('/api/train/export', {method:'POST', headers:headers(true),
      body: JSON.stringify({run})});
    const d = await r.json();
    if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
    trainLogNext = 0;
    renderTrainJob(d.job); startTrainPolling();
    toast('Exporterar till Ollama som "'+d.ollama_name+'"');
  }catch(e){ toast('Kunde inte exportera: '+e.message, true); }
}
function startTrainPolling(){
  if(trainTimer) return;
  trainTimer = setInterval(pollTrainJob, 1500);
}
function stopTrainPolling(){ clearInterval(trainTimer); trainTimer = null; }
async function pollTrainJob(){
  try{
    const r = await api('/api/train/log?since='+trainLogNext, {headers: headers(false)});
    const d = await r.json();
    if(!d.job){ stopTrainPolling(); return; }
    renderTrainJob(d.job);
    if(d.job.state !== 'kör'){
      stopTrainPolling();
      refreshTrainStatus();
      refresh();                                  // ev. ny modell i Ollama
      if(d.job.state === 'klar') toast(d.job.label + ' – klart!');
      else if(d.job.state === 'fel') toast(d.job.label + ' misslyckades', true);
    }
  }catch(e){ stopTrainPolling(); }
}
function renderTrainJob(job){
  if(!job) return;
  document.getElementById('trRunBox').style.display = 'block';
  document.getElementById('trStopBtn').style.display = job.state === 'kör' ? 'inline-flex' : 'none';
  document.getElementById('trStartBtn').disabled = job.state === 'kör';
  const m = job.metrics || {};
  const pct = m.percent != null ? m.percent : (job.state === 'klar' ? 100 : 0);
  const prog = document.getElementById('trProgress');
  prog.classList.toggle('done', job.state === 'klar');
  prog.classList.toggle('bad', job.state === 'fel');
  document.getElementById('trProgressBar').style.width = pct + '%';
  document.getElementById('trJobPct').textContent = m.percent != null ? (m.percent + '%') : '';
  document.getElementById('trJobLabel').innerHTML = '<b>' + esc(job.label||'') + '</b>';
  document.getElementById('trStep').textContent = m.step != null
    ? (m.step + (m.total ? ' / ' + m.total : '')) : '–';
  document.getElementById('trLoss').textContent = m.loss != null ? m.loss.toFixed(4) : '–';
  document.getElementById('trEpoch').textContent = m.epoch != null ? m.epoch.toFixed(2) : '–';
  document.getElementById('trElapsed').textContent = fmtDuration(job.elapsed);
  document.getElementById('trEta').textContent = m.eta || '–';
  const state = {'kör':'⏳ Kör…','klar':'✓ Klart','fel':'✕ Misslyckades','stoppad':'■ Avbruten'}[job.state] || job.state;
  document.getElementById('trJobState').innerHTML = esc(state)
    + (job.error ? ' – <span style="color:var(--danger)">'+esc(job.error)+'</span>' : '');
  const chart = document.getElementById('trChart');
  chart.style.display = job.kind === 'train' ? '' : 'none';
  if(job.kind === 'train') drawLossChart(job.history || []);
  // Steg/loss/epok är bara meningsfullt under träning
  document.getElementById('trStatsRow').style.display = job.kind === 'train' ? 'flex' : 'none';
  if(job.lines && job.lines.length){
    const box = document.getElementById('trLog');
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    box.textContent += (box.textContent ? '\n' : '') + job.lines.join('\n');
    if(atBottom) box.scrollTop = box.scrollHeight;
    trainLogNext = job.next;
  }
  if(job.state === 'fel' && job.error) document.getElementById('trLog').style.display = 'block';
}
function fmtDuration(sec){
  sec = Math.max(0, parseInt(sec||0, 10));
  const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60), s = sec%60;
  return (h ? h+'h ' : '') + (h||m ? m+'m ' : '') + s + 's';
}
function drawLossChart(history){
  const svg = document.getElementById('trChart');
  if(!history.length){ svg.innerHTML = '<text x="300" y="60" fill="#4b5563" font-size="12" '
    + 'text-anchor="middle">Loss-kurvan ritas när träningen kommit igång</text>'; return; }
  const losses = history.map(p=>p.loss);
  const min = Math.min(...losses), max = Math.max(...losses), span = (max-min) || 1;
  const pts = history.map((p,i)=>{
    const x = history.length === 1 ? 300 : (i/(history.length-1))*580 + 10;
    const y = 96 - ((p.loss - min)/span)*82;
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(' ');
  svg.innerHTML =
    '<polyline points="'+pts+'" fill="none" stroke="#7c5cff" stroke-width="2"/>'
    + '<text x="8" y="14" fill="#6b7280" font-size="10">loss '+max.toFixed(3)+'</text>'
    + '<text x="8" y="106" fill="#6b7280" font-size="10">'+min.toFixed(3)+'</text>'
    + '<text x="592" y="106" fill="#6b7280" font-size="10" text-anchor="end">steg '
    + (history[history.length-1].step||history.length)+'</text>';
}

/* ---- Steg 4: färdiga modeller ---- */
function renderTrainRuns(){
  const runs = (trainStatus.runs||[]);
  const box = document.getElementById('trRuns');
  if(!runs.length){
    box.innerHTML = '<div class="tr-note">Här dyker dina tränade modeller upp. Kör steg 1–3 först.</div>';
    return;
  }
  const busy = trainStatus.job && trainStatus.job.state === 'kör';
  box.innerHTML = runs.map(r=>{
    const inOllama = installed.has(r.ollama_name+':latest') || installed.has(r.ollama_name);
    const right = inOllama
      ? '<span class="installed">✓ I Ollama</span>'
        + '<button class="btn ghost small" onclick="chatWithModel(\''+esc(r.ollama_name)+'\')">💬 Chatta</button>'
      : (r.has_model
          ? '<button class="btn accent small" onclick="trainExport(\''+esc(r.name)+'\')"'
            + (busy?' disabled':'') + '>📦 Lägg in i Ollama</button>'
          : '<span class="meta">Ingen färdig modell i mappen</span>');
    return '<div class="tr-runitem"><div><div class="name">'+esc(r.name)+'</div>'
      + '<div class="meta">'+esc(r.path)+' · '+(r.gguf?'GGUF klar · ':'')
      + 'som <code>'+esc(r.ollama_name)+'</code></div></div>'
      + '<div class="right">'+right+'</div></div>';
  }).join('');
}
function chatWithModel(name){
  showView('chat');
  const sel = document.getElementById('chatModel');
  const match = [...sel.options].find(o=>o.value === name || o.value === name+':latest');
  if(match){ sel.value = match.value; saveChatModel(); }
}

/* ---- Hugging Face: kvantiseringar för ett repo (fälls ut i träfflistan) ---- */
let hfQuants = {};             // repo -> kvantiseringar (hämtas vid utfällning)
async function hfToggleQuants(i){
  const m = hfModels[i]; if(!m) return;
  const box = document.getElementById('hfq'+i);
  if(box.style.display !== 'none'){ box.style.display='none'; return; }
  box.style.display='block';
  if(!hfQuants[m.id]){
    box.innerHTML = '<div class="hf-meta">Hämtar filer…</div>';
    try{
      const r = await api('/api/hf/files?repo='+encodeURIComponent(m.id), {headers: headers(false)});
      const d = await r.json();
      if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
      hfQuants[m.id] = d;
    }catch(e){
      box.innerHTML = '<div class="hf-meta">Kunde inte läsa filerna: '+esc(e.message)+'</div>';
      return;
    }
  }
  const d = hfQuants[m.id];
  const rows = (d.quants||[]).map(q=>{
    const size = q.size ? humanSize(q.size) : 'okänd storlek';
    const parts = q.parts > 1 ? ('  ·  '+q.parts+' delar') : '';
    const dflt = (q.quant === d.default) ? ' <span class="chip">standard</span>' : '';
    return '<div class="hf-quant"><span class="q">'+esc(q.quant)+'</span>'
      + '<span class="sz">'+esc(size)+esc(parts)+'</span>'+dflt
      + '<button class="btn ghost small" onclick="startPull(\''+esc(q.pull)+'\')">↓ Installera</button></div>';
  }).join('');
  box.innerHTML = rows || '<div class="hf-meta">Inga GGUF-filer i det här repot.</div>';
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
  document.getElementById('dlExtra').innerHTML = '';
  pullHf = null; pullError = null;
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
    if(pullError) pullDone(name, 'error', pullError);
    else pullDone(name, 'success');
  }catch(e){
    if(e.name === 'AbortError') pullDone(name, 'cancelled');
    else pullDone(name, 'error', e.message);
  }finally{
    pullController = null;
  }
}
function onProgress(m){
  if(m.hf){ showHfSwitch(m.hf); return; }        // servern bytte källa till Hugging Face
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
  if(m.error){
    pullError = m.error;                       // avgör utfallet när strömmen tar slut
    document.getElementById('dlStatus').textContent = 'Fel: '+m.error;
  }
}
let pullHf = null;      // Hugging Face-träffen servern valde (om den bytte källa)
let pullError = null;   // sista felraden i strömmen (en ström kan sluta med fel)
function showHfSwitch(hf){
  pullHf = hf;
  const size = hf.size ? ('  ·  ' + humanSize(hf.size)) : '';
  const quant = hf.quant ? ('  ·  ' + hf.quant) : '';
  let html = '🤗 Hugging Face: <a href="'+esc(hf.url||'')+'" target="_blank" rel="noopener" '
    + 'style="color:var(--accent-hov)">'+esc(hf.repo)+'</a>'+esc(quant)+esc(size);
  if(hf.gated) html += '<br><span style="color:var(--amber)">⚠ Repot är gated – du måste '
    + 'godkänna villkoren på Hugging Face först.</span>';
  const alts = hf.alternatives || [];
  if(alts.length){
    html += '<br>Andra träffar: ' + alts.map(a =>
      '<a href="#" onclick="startPull(\''+esc(a.pull)+'\');return false" '
      + 'style="color:var(--subtle)">'+esc(a.id)+'</a>').join('  ·  ');
  }
  if(hf.auto) document.getElementById('dlTitle').textContent = 'Laddar ner  ' + (hf.pull || hf.repo);
  document.getElementById('dlExtra').innerHTML = html;
}
function pullDone(name, outcome, detail){
  const bar = document.getElementById('dlBar');
  const cancel = document.getElementById('dlCancel');
  const shown = (pullHf && pullHf.auto && pullHf.pull) ? pullHf.pull : name;
  if(outcome==='success'){
    bar.style.width='100%'; bar.style.background='var(--green)';
    document.getElementById('dlPct').textContent='100%';
    document.getElementById('dlTitle').textContent='✓  '+shown+' installerad';
    document.getElementById('dlStatus').textContent='Klar! Modellen finns nu under "Mina modeller".';
    document.getElementById('customName').value='';
    toast('"'+shown+'" installerad');
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

/* ---- Chatt ---- */
function populateChatModels(){
  const sel = document.getElementById('chatModel');
  if(!sel) return;
  const names = lastModels.map(m=>m.name);
  const cur = sel.value;
  if(!names.length){ sel.innerHTML = '<option value="">Inga modeller installerade</option>'; return; }
  sel.innerHTML = names.map(n=>'<option>'+esc(n)+'</option>').join('');
  const saved = uiPrefs.chat_model || '';
  if(cur && names.includes(cur)) sel.value = cur;                 // behåll aktivt val
  else if(saved && names.includes(saved)) sel.value = saved;      // ihågkommet val (databas)
  else{
    const active = [...running.keys()][0];   // annars den som redan är i minnet
    sel.value = (active && names.includes(active)) ? active : names[0];
  }
}
function saveChatModel(){ savePref('chat_model', document.getElementById('chatModel').value); }
function autoGrow(el){ el.style.height='auto'; el.style.height=Math.min(el.scrollHeight,160)+'px'; }

/* ---- Bildbilagor (vision-modeller, t.ex. llava) ---- */
let pendingImages = [];   // dataUrls som väntar på att skickas
function onChatFiles(ev){
  const files = Array.from(ev.target.files || []);
  files.forEach(f=>{
    if(!f.type || f.type.indexOf('image/') !== 0) return;
    const reader = new FileReader();
    reader.onload = ()=>{ pendingImages.push(reader.result); renderAttachments(); };
    reader.readAsDataURL(f);
  });
  ev.target.value = '';   // tillåt att välja samma fil igen
}
function removeAttachment(i){ pendingImages.splice(i, 1); renderAttachments(); }
function renderAttachments(){
  const el = document.getElementById('chatAttachments');
  if(!el) return;
  if(!pendingImages.length){ el.style.display = 'none'; el.innerHTML = ''; return; }
  el.style.display = 'flex';
  el.innerHTML = pendingImages.map((d, i)=>
    '<div class="thumb"><img src="'+d+'"><button title="Ta bort" onclick="removeAttachment('+i+')">✕</button></div>').join('');
}
function stripDataUrl(d){ return (''+d).replace(/^data:[^;]+;base64,/, ''); }

function renderChat(){
  const box = document.getElementById('chatMessages');
  if(!chatMessages.length){
    box.innerHTML = '<div class="chat-empty">Välj en modell och skriv ett meddelande för att börja chatta.</div>';
    return;
  }
  box.innerHTML = chatMessages.map(m=>{
    if(m.role === 'assistant'){
      const body = m.content ? mdToHtml(m.content) : '…';
      const st = m.stats ? '<div class="msg-stats">'+esc(fmtStats(m.stats))+'</div>' : '';
      return '<div class="msg assistant">'+body+st+'</div>';
    }
    const imgs = (m.images && m.images.length)
      ? '<div class="msg-imgs">'+m.images.map(d=>'<img src="'+esc(d)+'">').join('')+'</div>' : '';
    return '<div class="msg user">'+imgs+esc(m.content||'')+'</div>';
  }).join('');
  box.scrollTop = box.scrollHeight;
}

/* ---- Enkel, säker Markdown-rendering (kod, rubriker, listor, fetstil m.m.) ---- */
function fmtStats(s){
  const p = [];
  if(s.tps) p.push(s.tps.toFixed(1)+' tok/s');
  if(s.tokens) p.push(s.tokens+' tokens');
  if(s.secs) p.push(s.secs.toFixed(1)+' s');
  if(s.gpu) p.push(s.gpu);
  return p.join('  ·  ');
}
function mdInline(s){
  // Dela på inline-kod (`...`) och formatera bara texten mellan – inga platshållare behövs
  const parts = s.split(/(`[^`]+`)/g);
  return parts.map(seg=>{
    if(seg.length > 1 && seg[0] === '`' && seg[seg.length-1] === '`'){
      return '<code class="inline">' + seg.slice(1,-1) + '</code>';
    }
    seg = seg.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    seg = seg.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    seg = seg.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    seg = seg.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    return seg;
  }).join('');
}
function mdToHtml(src){
  const lines = esc(src).split('\n');
  let html = '', i = 0, inCode = false, codeBuf = [], listType = null;
  const closeList = ()=>{ if(listType){ html += '</'+listType+'>'; listType = null; } };
  while(i < lines.length){
    const line = lines[i];
    const fence = line.match(/^```(\w*)\s*$/);
    if(fence){
      if(!inCode){ inCode = true; codeBuf = []; }
      else { inCode = false; closeList();
        html += '<pre class="code"><button class="copy" onclick="copyCode(this)">Kopiera</button><code>'
              + codeBuf.join('\n') + '</code></pre>'; }
      i++; continue;
    }
    if(inCode){ codeBuf.push(line); i++; continue; }
    let m;
    if(m = line.match(/^(#{1,4})\s+(.*)$/)){ closeList(); const l = m[1].length; html += '<h'+l+'>'+mdInline(m[2])+'</h'+l+'>'; i++; continue; }
    if(m = line.match(/^\s*[-*]\s+(.*)$/)){ if(listType!=='ul'){ closeList(); html+='<ul>'; listType='ul'; } html += '<li>'+mdInline(m[1])+'</li>'; i++; continue; }
    if(m = line.match(/^\s*\d+\.\s+(.*)$/)){ if(listType!=='ol'){ closeList(); html+='<ol>'; listType='ol'; } html += '<li>'+mdInline(m[1])+'</li>'; i++; continue; }
    if(line.trim()===''){ closeList(); i++; continue; }
    closeList(); html += '<p>'+mdInline(line)+'</p>'; i++;
  }
  if(inCode){ html += '<pre class="code"><code>'+codeBuf.join('\n')+'</code></pre>'; }  // ofullständigt block
  closeList();
  return html;
}
function copyCode(btn){
  const code = btn.parentElement.querySelector('code');
  const text = code ? code.textContent : '';
  if(navigator.clipboard){
    navigator.clipboard.writeText(text).then(()=>{
      btn.textContent = 'Kopierat!'; setTimeout(()=>{ btn.textContent = 'Kopiera'; }, 1500);
    }).catch(()=>{});
  }
}
function clearChat(){
  if(chatController) chatController.abort();
  chatMessages = [];
  renderChat();
}
async function sendChat(){
  const model = document.getElementById('chatModel').value;
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if(!model){ toast('Ingen modell vald', true); return; }
  if(chatController) return;
  if(!text && !pendingImages.length) return;

  const userMsg = {role:'user', content:text};
  if(pendingImages.length){ userMsg.images = pendingImages.slice(); }
  chatMessages.push(userMsg);
  input.value=''; autoGrow(input);
  pendingImages = []; renderAttachments();
  chatMessages.push({role:'assistant', content:''});
  const idx = chatMessages.length - 1;
  renderChat();
  const box = document.getElementById('chatMessages');
  const send = document.getElementById('chatSend');
  send.textContent = 'Stoppa';
  // Under strömning visas råtext – behåll radbrytningar tills markdown renderas vid klar
  if(box.lastChild) box.lastChild.style.whiteSpace = 'pre-wrap';

  const backend = document.getElementById('chatBackend').value || undefined;
  chatController = new AbortController();
  try{
    const sys = chatSystemPrompt();
    const convo = chatMessages.slice(0, idx).map(m=>{
      const mm = {role:m.role, content:m.content};
      if(m.images && m.images.length) mm.images = m.images.map(stripDataUrl);  // Ollama vill ha rå base64
      return mm;
    });
    const msgs = sys ? [{role:'system', content:sys}].concat(convo) : convo;
    const wsEl = document.getElementById('csWebsearch');
    const websearch = !!(cfg.websearch && wsEl && wsEl.checked);
    const memEl = document.getElementById('csMemory');
    const memory = !!(cfg.memory && memEl && memEl.checked);
    const r = await api('/api/chat', {method:'POST', headers:headers(true),
      body: JSON.stringify({model, backend, messages: msgs, options: chatOptions(), websearch, memory}),
      signal: chatController.signal});
    if(!r.ok){ throw new Error('HTTP '+r.status); }
    const reader = r.body.getReader(); const dec = new TextDecoder(); let buf='';
    while(true){
      const {done, value} = await reader.read();
      if(done) break;
      buf += dec.decode(value, {stream:true});
      let i;
      while((i = buf.indexOf('\n')) >= 0){
        const line = buf.slice(0,i).trim(); buf = buf.slice(i+1);
        if(!line) continue;
        try{
          const msg = JSON.parse(line);
          if(msg.status === 'searching'){
            if(box.lastChild) box.lastChild.textContent = '🔎 Söker på nätet'
              + (msg.query ? ': ”'+msg.query+'”' : '') + '…';
            box.scrollTop = box.scrollHeight;
            continue;
          }
          if(msg.status === 'reading'){
            if(box.lastChild) box.lastChild.textContent = '📄 Läser '
              + (msg.count || '') + ' sidor…';
            box.scrollTop = box.scrollHeight;
            continue;
          }
          if(msg.message && msg.message.content){
            chatMessages[idx].content += msg.message.content;
            if(box.lastChild) box.lastChild.textContent = chatMessages[idx].content;
            box.scrollTop = box.scrollHeight;
          }
          if(msg.done && msg.eval_count && msg.eval_duration){
            chatMessages[idx].stats = {
              tps: msg.eval_count / (msg.eval_duration/1e9),
              tokens: msg.eval_count,
              secs: (msg.total_duration||0)/1e9,
              gpu: (document.getElementById('chatBackend').value || '')
            };
          }
          if(msg.error){ chatMessages[idx].content += '\n[Fel: '+msg.error+']'; }
        }catch(e){}
      }
    }
    if(!chatMessages[idx].content) chatMessages[idx].content = '(inget svar)';
    renderChat();
    if(memory) memWrite(text, chatMessages[idx].content);   // spara utbytet i delat minne
  }catch(e){
    if(e.name === 'AbortError') chatMessages[idx].content += '  [avbruten]';
    else { chatMessages[idx].content = '[Fel: '+e.message+']'; toast('Chatt misslyckades', true); }
    renderChat();
  }finally{
    chatController = null;
    document.getElementById('chatSend').textContent = 'Skicka';
    saveCurrentConvo();
  }
}
document.getElementById('chatSend').onclick = ()=>{ if(chatController) chatController.abort(); else sendChat(); };
document.getElementById('chatInput').addEventListener('input', e=>autoGrow(e.target));
document.getElementById('chatInput').addEventListener('keydown', e=>{
  if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); sendChat(); }
});

/* ---- Chattinställningar: systemprompt, temperatur, kontextlängd ---- */
function toggleChatSettings(){
  const el = document.getElementById('chatSettings');
  el.style.display = (el.style.display === 'none' || !el.style.display) ? 'block' : 'none';
}
function chatSystemPrompt(){ return document.getElementById('csSystem').value.trim(); }
function chatOptions(){
  const o = {};
  const t = parseFloat(document.getElementById('csTemp').value);
  if(!isNaN(t)) o.temperature = t;
  const c = parseInt(document.getElementById('csCtx').value, 10);
  if(c) o.num_ctx = c;
  return o;
}
(function csInit(){
  // Standard tills prefs laddats från databasen (applyPrefs kan sedan skriva över).
  document.getElementById('csWebsearch').checked = true;   // på om servern stödjer det
  document.getElementById('csMemory').checked = true;
  document.getElementById('csTempVal').textContent = document.getElementById('csTemp').value;
  // Ändringar sparas i serverns databas (prefs).
  document.getElementById('csSystem').addEventListener('input', e=>savePrefDebounced('chat_system', e.target.value));
  document.getElementById('csTemp').addEventListener('input', e=>{
    document.getElementById('csTempVal').textContent = e.target.value; savePrefDebounced('chat_temp', e.target.value);
  });
  document.getElementById('csCtx').addEventListener('change', e=>savePref('chat_ctx', e.target.value));
  document.getElementById('csWebsearch').addEventListener('change',
    e=>savePref('chat_websearch', e.target.checked ? '1' : '0'));
  document.getElementById('csMemory').addEventListener('change',
    e=>savePref('chat_memory', e.target.checked ? '1' : '0'));
})();

/* ---- Sparade konversationer (localStorage) ---- */
let conversations = [];
let currentConvoId = null;
function loadConvos(){
  try{ conversations = JSON.parse(localStorage.getItem('os_convos') || '[]'); }catch(e){ conversations = []; }
  if(!Array.isArray(conversations)) conversations = [];
}
function persistConvos(){
  try{
    conversations = conversations.slice(0, 50);   // behåll de 50 senaste
    localStorage.setItem('os_convos', JSON.stringify(conversations));
  }catch(e){}
}
function renderConvoSelect(){
  const sel = document.getElementById('convoSelect');
  if(!sel) return;
  if(!conversations.length){ sel.innerHTML = '<option value="">(inga sparade)</option>'; sel.value = ''; return; }
  const opts = conversations.map(c=>'<option value="'+c.id+'">'+esc(c.title||'Namnlös')+'</option>').join('');
  sel.innerHTML = (currentConvoId ? '' : '<option value="">Ny konversation</option>') + opts;
  sel.value = currentConvoId || '';
}
function convoTitleFrom(msgs){
  const u = msgs.find(m=>m.role==='user');
  let t = u ? u.content.trim().replace(/\s+/g,' ') : 'Ny konversation';
  return t.length > 40 ? t.slice(0,40)+'…' : (t || 'Ny konversation');
}
function saveCurrentConvo(){
  if(!chatMessages.length) return;
  const now = Date.now();
  let c = conversations.find(x=>x.id === currentConvoId);
  if(!c){
    c = { id: currentConvoId || (''+now), title: convoTitleFrom(chatMessages) };
    currentConvoId = c.id;
  } else {
    conversations = conversations.filter(x=>x.id !== c.id);   // flytta överst
  }
  conversations.unshift(c);
  // Spara text/statistik men inte bilddata (skulle snabbt fylla localStorage)
  c.messages = JSON.parse(JSON.stringify(chatMessages)).map(m=>{ delete m.images; return m; });
  c.model = document.getElementById('chatModel').value;
  c.backend = document.getElementById('chatBackend').value;
  c.updatedAt = now;
  persistConvos();
  setActiveConvo(currentConvoId);   // kom ihåg vilken chatt som var öppen (för omladdning)
  renderConvoSelect();
}
function setActiveConvo(id){
  try{ if(id) localStorage.setItem('os_active_convo', id);
       else localStorage.removeItem('os_active_convo'); }catch(e){}
}
function restoreActiveConvo(){
  // Återställ den senast öppna chatten vid omladdning så den inte försvinner.
  let id = '';
  try{ id = localStorage.getItem('os_active_convo') || ''; }catch(e){}
  const c = id && conversations.find(x=>x.id === id);
  if(c){ chatMessages = JSON.parse(JSON.stringify(c.messages || [])); currentConvoId = id; }
}
function clearChatConfirm(){
  if(!chatMessages.length){ toast('Chatten är redan tom'); return; }
  if(!confirm('Töm den här chatten? Meddelandena tas bort.')) return;
  if(chatController) chatController.abort();
  chatMessages = [];
  if(currentConvoId){
    conversations = conversations.filter(x=>x.id !== currentConvoId);   // ta bort sparad kopia
    persistConvos();
    currentConvoId = null;
  }
  setActiveConvo(null);
  renderChat();
  renderConvoSelect();
  updateChatWarning();
  const inp = document.getElementById('chatInput'); if(inp) inp.focus();
}
function newConversation(){
  if(chatController) chatController.abort();
  chatMessages = [];
  currentConvoId = null;
  setActiveConvo(null);
  renderChat();
  renderConvoSelect();
  updateChatWarning();
  const inp = document.getElementById('chatInput'); if(inp) inp.focus();
}
function onConvoSelect(){
  const id = document.getElementById('convoSelect').value;
  if(id) loadConversation(id); else newConversation();
}
function loadConversation(id){
  const c = conversations.find(x=>x.id === id);
  if(!c) return;
  if(chatController) chatController.abort();
  chatMessages = JSON.parse(JSON.stringify(c.messages || []));
  currentConvoId = id;
  setActiveConvo(id);
  const ms = document.getElementById('chatModel');
  if(c.model && [...ms.options].some(o=>o.value === c.model)) ms.value = c.model;
  const bs = document.getElementById('chatBackend');
  if(c.backend && [...bs.options].some(o=>o.value === c.backend)) bs.value = c.backend;
  renderChat();
  renderConvoSelect();
  updateChatWarning();
}
function deleteConversation(){
  if(!currentConvoId){ newConversation(); return; }
  conversations = conversations.filter(x=>x.id !== currentConvoId);
  persistConvos();
  newConversation();
}
function renameConversation(){
  if(!currentConvoId){ toast('Ingen sparad konversation vald', true); return; }
  const c = conversations.find(x=>x.id === currentConvoId);
  if(!c) return;
  const t = prompt('Namn på konversationen:', c.title || '');
  if(t !== null){ c.title = t.trim() || c.title; persistConvos(); renderConvoSelect(); }
}
loadConvos();
restoreActiveConvo();   // återställ den senast öppna chatten vid omladdning

/* ---- Varning: får modellen plats på vald GPU? ---- */
function modelSizeBytes(name){
  const m = lastModels.find(x=>x.name===name);
  return m ? (Number(m.size)||0) : 0;
}
function selectedBackendGpu(){
  const sel = document.getElementById('chatBackend');
  const b = (cfg.backends||[]).find(x=>x.label === (sel ? sel.value : ''));
  if(b) return b.gpu;
  if(cfg.backends && cfg.backends.length === 1) return cfg.backends[0].gpu;
  return null;
}
async function updateChatWarning(){
  const el = document.getElementById('chatWarn');
  if(!el) return;
  el.style.display = 'none'; el.innerHTML = '';
  const model = document.getElementById('chatModel').value;
  const size = modelSizeBytes(model);
  if(!model || !size) return;

  try{
    const r = await fetch('/api/system', {headers: headers(false)});
    if(r.ok) lastSystem = await r.json();
  }catch(e){}
  const gpus = (lastSystem && lastSystem.gpus) || [];
  if(!gpus.length) return;                       // ingen GPU-info -> ingen varning

  let g = null;
  const idx = selectedBackendGpu();
  if(idx !== null && idx !== undefined && idx !== '') g = gpus.find(x=>String(x.index)===String(idx));
  else if(gpus.length === 1) g = gpus[0];
  if(!g || !g.mem_total_mb) return;

  const totalB = g.mem_total_mb * 1024*1024;
  const usedB  = (g.mem_used_mb || 0) * 1024*1024;
  const freeB  = Math.max(0, totalB - usedB);
  const needB  = size * 1.15;                     // uppskattat: vikter + lite overhead
  const label  = (document.getElementById('chatBackend').value) || (g.name || ('GPU '+g.index));

  let cls, msg;
  if(needB > totalB){
    cls = 'err';
    msg = '⚠ Modellen får inte plats på ' + esc(label) + ' (' + humanSize(totalB) + '). '
        + 'Den behöver ~' + humanSize(needB) + ' och skulle då köras delvis på CPU (långsamt). '
        + 'Välj en mindre modell eller ett kort med mer VRAM.';
  } else if(needB > freeB){
    cls = 'warn';
    msg = '⚠ Kan bli trångt på ' + esc(label) + ': ~' + humanSize(needB) + ' behövs men bara '
        + humanSize(freeB) + ' ledigt just nu (' + humanSize(totalB) + ' totalt). '
        + 'Frigör en modell eller välj en annan GPU.';
  } else {
    cls = 'ok';
    msg = '✓ Får plats på ' + esc(label) + ': ~' + humanSize(needB) + ' behövs, '
        + humanSize(freeB) + ' ledigt av ' + humanSize(totalB) + '.';
  }
  el.className = 'chatwarn ' + cls;
  el.innerHTML = msg;
  el.style.display = 'block';
}
document.getElementById('chatModel').addEventListener('change', updateChatWarning);
document.getElementById('chatModel').addEventListener('change', saveChatModel);
document.getElementById('chatBackend').addEventListener('change', updateChatWarning);
document.getElementById('chatBackend').addEventListener('change',
  ()=>savePref('chat_backend', document.getElementById('chatBackend').value));

// Uppdatera "aktiv modell" automatiskt var 5:e sekund (den kan laddas/frigöras när som helst)
async function refreshRunning(){
  if(AUTH && !token) return;         // undvik upprepade token-frågor
  if(!lastModels.length) return;
  try{
    const pr = await fetch('/api/running', {headers: headers(false)});
    if(!pr.ok) return;
    const pd = await pr.json();
    const next = buildRunning(pd.models);
    if(runSig(next) !== runSig(running)){ running = next; renderModels(lastModels); }
    else running = next;
  }catch(e){ /* tyst – nästa intervall försöker igen */ }
}
setInterval(refreshRunning, 5000);

/* ---- Backends (GPU-instanser) ---- */
async function loadConfig(){
  try{
    const r = await fetch('/api/config', {headers: headers(false)});
    if(r.ok) cfg = await r.json();
  }catch(e){}
  populateBackends();
  // Visa webbsök-inställningen bara om servern stödjer det
  const wsRow = document.getElementById('csWebsearchRow');
  if(wsRow) wsRow.style.display = cfg.websearch ? 'flex' : 'none';
  // Visa minnes-inställningen bara om servern har Mem0 konfigurerat
  const memRow = document.getElementById('csMemoryRow');
  if(memRow) memRow.style.display = cfg.memory ? 'flex' : 'none';
  const memTools = document.getElementById('csMemoryTools');
  if(memTools) memTools.style.display = cfg.memory ? 'block' : 'none';
  updateCodeView();   // Codex-fliken syns alltid; visa av-läge om den inte är påslagen
  updateHfView();     // Hugging Face-sök syns bara om stödet är påslaget
  updateTrainNav();   // AI-träningsfliken kräver soup_train.py
}
function updateTrainNav(){
  // AI-träningen är DOLD som standard – appen fokuserar på Codex. Slå på den under
  // ⚙ Inställningar → AI-träning ("Visa AI-träning i menyn"). Fliken kräver dessutom
  // att soup_train.py ligger bredvid appen.
  const nav = document.getElementById('nav-train');
  const show = !!(cfg.train_module && cfg.train_menu);
  if(nav) nav.style.display = show ? '' : 'none';
  // Står man i den dolda vyn när den göms: gå tillbaka till Codex.
  if(!show && document.getElementById('nav-train')
     && document.getElementById('nav-train').classList.contains('active')) showView('code');
}
function updateHfView(){
  // Ett sökfält för allt – texten säger bara vilka källor som är påslagna.
  const hint = document.getElementById('customHint');
  if(!hint) return;
  hint.textContent = cfg.hf
    ? 'Sök bland modeller i Ollamas bibliotek och på Hugging Face – träffarna visas nedan. '
      + 'Skriver du ett exakt namn (även "hf.co/ägare/repo:Q4_K_M" eller en Hugging Face-länk) '
      + 'laddar knappen ner det direkt.'
    : 'Sök bland modeller i Ollamas bibliotek – träffarna visas nedan. Skriver du ett exakt '
      + 'namn laddar knappen ner det direkt.';
}
function updateCodeView(){
  const off = document.getElementById('codeOff');
  const wrap = document.getElementById('codeWrap');
  const on = !!cfg.code;                        // växeln på → vyn funkar
  const ws = !!cfg.code_ws || !!localDir;       // server-arbetsyta ELLER lokal mapp → fil-träd/spara
  if(wrap) wrap.style.display = on ? 'flex' : 'none';
  if(off){
    off.style.display = on ? 'none' : 'block';
    if(!on){
      off.innerHTML = '<h2>💻 Codex är avstängd</h2>'
        + '<p>Codex hjälper dig skriva kod. Slå på den under Inställningar.<br>'
        + 'Utan en arbetsyta funkar den som en kod-chatt; med en arbetsyta (på servern eller '
        + 'en lokal mapp i webbläsaren) kan den läsa projektet och spara ändringar.</p>'
        + '<button class="btn accent" onclick="showView(\'settings\')">Öppna Inställningar</button>';
    }
  }
  const tree = document.querySelector('#view-code .code-tree');
  if(tree) tree.style.display = ws ? 'flex' : 'none';
  const repoBar = document.getElementById('codeRepoBar');
  if(repoBar) repoBar.style.display = on ? 'flex' : 'none';
  const noWs = document.getElementById('codeNoWs');
  if(noWs){
    noWs.style.display = (on && !ws) ? 'block' : 'none';
    // Skilj "ingen arbetsyta vald" från "vald men hittades inte" – annars letar
    // man efter fel sak (t.ex. bland tokens) när sökvägen bara är felstavad.
    const settingsLink = '<a href="#" onclick="showView(\'settings\');return false">Inställningar</a>';
    noWs.innerHTML = (cfg.code_ws_set && !cfg.code_ws)
      ? '⚠ Arbetsytan <code>' + esc(cfg.code_ws_path || '') + '</code> hittades inte på servern ('
        + esc(cfg.server_os || '?') + '). Codex kan därför bara skissa kod. Kontrollera att '
        + 'sökvägen finns <b>på servern</b> där appen körs, och att den går att läsa – rätta '
        + 'den i ' + settingsLink + '.'
      : '💡 Skisslage – ingen arbetsyta vald. Codex skriver kod åt dig men kan inte läsa '
        + 'projektet eller spara till disk. Kopiera koden, eller välj en arbetsyta i '
        + settingsLink + ' för att läsa/spara/köra.';
  }
  // Knapp för lokal mapp: visa när växeln är på (och webbläsaren stödjer det)
  const lb = document.getElementById('codeLocalBar');
  if(lb) lb.style.display = (on && FS_OK) ? 'flex' : 'none';
  const li = document.getElementById('codeLocalInfo');
  if(li) li.textContent = localDir ? ('📂 '+localDirName) : '';
  const cb = document.getElementById('codeLocalClose');
  if(cb) cb.style.display = localDir ? '' : 'none';
  updateModeBar();
}

/* ---- Inställningar (sparas i lokal SQLite på servern) ---- */
let mem0KeyIsSet = false;      // om en nyckel redan finns sparad
let mem0KeyClear = false;      // användaren har valt att ta bort nyckeln
let ghTokenIsSet = false, ghTokenClear = false;
let hfTokenIsSet = false, hfTokenClear = false;
async function loadSettingsForm(){
  let s = {};
  try{ const r = await api('/api/settings', {headers: headers(false)}); s = await r.json(); }
  catch(e){ toast('Kunde inte hämta inställningar', true); return; }
  const set = (id, v)=>{ const el=document.getElementById(id); if(el) el.value = (v==null?'':v); };
  const chk = (id, v)=>{ const el=document.getElementById(id); if(el) el.checked = !!v; };
  chk('stWebsearch', s.websearch);
  chk('stChatTime', s.chat_time);
  set('stSearchPages', s.websearch_pages);
  set('stKeepAlive', s.keep_alive);
  const timeState = document.getElementById('stTimeState');
  if(timeState) timeState.textContent = s.server_time
    ? ('Serverns klocka: ' + s.server_time + ' – ligger den fel, sätt rätt tidszon på servern '
       + '(t.ex. Environment=TZ=Europe/Stockholm i systemd-tjänsten).')
    : '';
  chk('stMem0Enabled', s.mem0_enabled);
  set('stMem0User', s.mem0_user_id);
  set('stMem0Base', s.mem0_base_url);
  set('stMem0Ver', s.mem0_api_version);
  set('stMem0Auth', s.mem0_auth_scheme);
  set('stMem0Org', s.mem0_org_id);
  set('stMem0Proj', s.mem0_project_id);
  mem0KeyIsSet = !!s.mem0_api_key_set; mem0KeyClear = false;
  const keyEl = document.getElementById('stMem0Key'); if(keyEl) keyEl.value='';
  document.getElementById('stMem0KeyState').textContent =
    mem0KeyIsSet ? '● En nyckel är sparad (lämna tomt för att behålla den)' : 'Ingen nyckel sparad';
  document.getElementById('stMem0Test').textContent = '';
  chk('stHfEnabled', s.hf_enabled);
  chk('stHfAuto', s.hf_auto);
  hfTokenIsSet = !!s.hf_token_set; hfTokenClear = false;
  const hfEl = document.getElementById('stHfToken'); if(hfEl) hfEl.value='';
  const hfState = document.getElementById('stHfTokenState');
  if(hfState) hfState.textContent = hfTokenIsSet
    ? '● En token är sparad (lämna tomt för att behålla den)' : 'Ingen token sparad';
  const hfInfo = document.getElementById('stHfState');
  if(hfInfo){
    if(!s.hf_module) hfInfo.textContent = 'Status: modulen huggingface.py saknas bredvid appen – '
      + 'stödet är inaktivt. Hämta senaste versionen med ↻ Uppdatera.';
    else if(!s.hf_active) hfInfo.textContent = 'Status: avstängt.';
    else hfInfo.textContent = 'Status: ✓ på · ' + (s.hf_auto_active
      ? 'okända modellnamn hämtas automatiskt från Hugging Face'
      : 'okända modellnamn visar träffar från Hugging Face att välja bland');
  }
  chk('stTrainEnabled', s.train_enabled);
  chk('stTrainMenu', s.train_menu);
  set('stTrainWs', s.train_workspace);
  set('stTrainBin', s.train_soup_bin);
  const twState = document.getElementById('stTrainWsState');
  if(twState) twState.innerHTML = s.train_workspace_path
    ? ('Används: <code>' + esc(s.train_workspace_path) + '</code> (skapas när du sparar ett dataset)')
    : '';
  const tState = document.getElementById('stTrainState');
  if(tState){
    if(!s.train_module) tState.textContent = 'Status: modulen soup_train.py saknas bredvid appen '
      + '– fliken är dold. Hämta senaste versionen med ↻ Uppdatera.';
    else if(!s.train_active) tState.textContent = 'Status: avstängd.';
    else tState.textContent = 'Status: ✓ på – öppna fliken 🎓 AI-träning i menyn.';
  }
  chk('stCodeEnabled', s.code_enabled);
  set('stCodeWs', s.code_workspace);
  set('stCodePerm', s.code_mode || 'ask');
  set('stCodeSteps', s.code_steps);
  set('stCodeCtx', s.code_ctx);
  set('stCodeTemp', s.code_temp);
  const permHint = document.getElementById('stCodePermHint');
  if(permHint) permHint.textContent = CODE_MODE_HINTS[s.code_mode] || CODE_MODE_HINTS.ask;
  const permSel = document.getElementById('stCodePerm');
  if(permSel && !permSel._wired){
    permSel._wired = true;
    permSel.addEventListener('change', ()=>{
      if(permHint) permHint.textContent = CODE_MODE_HINTS[permSel.value] || '';
    });
  }
  const cws = document.getElementById('stCodeWsState');
  if(cws){
    if(!s.code_workspace){
      cws.innerHTML = 'Servern kör på <b>'+esc(s.server_os||'?')+'</b> – ange en sökväg som finns '
        + 'på <b>serverns</b> filsystem (aktuell mapp: <code>'+esc(s.server_cwd||'')+'</code>).';
    } else if(s.code_workspace_ok){
      cws.textContent = '✓ Mappen hittades';
    } else {
      cws.innerHTML = '✕ Mappen finns inte på servern. Servern kör på <b>'+esc(s.server_os||'?')+'</b> – '
        + 'sökvägen måste finnas där appen körs (inte på din egen dator). '
        + (s.server_os==='Windows' ? '' : 'En Windows-sökväg som <code>D:\\…</code> funkar inte på en Linux-server. ')
        + 'Serverns aktuella mapp: <code>'+esc(s.server_cwd||'')+'</code>.';
    }
  }
  set('stGhBase', s.github_base);
  chk('stRunEnabled', s.code_run_enabled);
  set('stRunAllow', s.code_run_allowlist);
  set('stRunTimeout', s.code_run_timeout);
  const cg = document.getElementById('stCodeGit');
  if(cg){
    if(!s.code_toggle){ cg.textContent = 'Status: avstängd – slå på Codex för att använda den.'; }
    else if(!s.code_active){
      cg.innerHTML = 'Status: <b>skisslage</b> – ingen arbetsyta vald. Codex skriver kod men '
        + 'kan inte läsa projektet eller spara. Välj en arbetsyta för att läsa/spara/git/köra.';
    }
    else{
      const parts = ['✓ Aktiv'];
      parts.push(s.git_available ? 'git finns' : '⚠ git saknas på servern');
      if(s.git_repo){
        parts.push(s.git_slug ? ('GitHub: '+s.git_slug) : '⚠ ingen github.com-remote (push/PR funkar ej)');
        parts.push(s.github_token_set ? 'token satt' : '⚠ ingen token (push/PR kräver token)');
      }else{
        parts.push('⚠ arbetsytan är inte ett git-repo (git/PR-knapparna döljs)');
      }
      parts.push(s.code_run_active ? 'kommandokörning PÅ' : 'kommandokörning av');
      parts.push('behörighet: ' + (s.code_mode_label || s.code_mode || 'ask'));
      parts.push(s.code_steps ? ('max ' + s.code_steps + ' steg') : 'obegränsat antal steg');
      parts.push('kontext ' + (s.code_ctx ? s.code_ctx + ' token' : 'Ollamas standard'));
      cg.textContent = 'Status: ' + parts.join(' · ');
    }
  }
  ghTokenIsSet = !!s.github_token_set; ghTokenClear = false;
  const ghEl = document.getElementById('stGhToken'); if(ghEl) ghEl.value='';
  const ghState = document.getElementById('stGhTokenState');
  if(ghState) ghState.textContent = ghTokenIsSet
    ? '● En token är sparad (lämna tomt för att behålla den)' : 'Ingen token sparad';
  document.getElementById('stDbPath').textContent = s.db_path ? ('Sparas i: '+s.db_path) : '';
}
function clearMem0Key(){
  mem0KeyClear = true; mem0KeyIsSet = false;
  const keyEl = document.getElementById('stMem0Key'); if(keyEl) keyEl.value='';
  document.getElementById('stMem0KeyState').textContent = '✕ Nyckeln tas bort när du sparar';
}
function clearHfToken(){
  hfTokenClear = true; hfTokenIsSet = false;
  const el = document.getElementById('stHfToken'); if(el) el.value='';
  document.getElementById('stHfTokenState').textContent = '✕ Token tas bort när du sparar';
}
function clearGhToken(){
  ghTokenClear = true; ghTokenIsSet = false;
  const el = document.getElementById('stGhToken'); if(el) el.value='';
  document.getElementById('stGhTokenState').textContent = '✕ Token tas bort när du sparar';
}
function collectSettings(){
  const val = id => (document.getElementById(id).value||'').trim();
  const body = {
    websearch: document.getElementById('stWebsearch').checked,
    chat_time: document.getElementById('stChatTime').checked,
    websearch_pages: val('stSearchPages'),
    keep_alive: val('stKeepAlive'),
    mem0_enabled: document.getElementById('stMem0Enabled').checked,
    mem0_user_id: val('stMem0User'),
    mem0_base_url: val('stMem0Base'),
    mem0_api_version: val('stMem0Ver'),
    mem0_auth_scheme: val('stMem0Auth'),
    mem0_org_id: val('stMem0Org'),
    mem0_project_id: val('stMem0Proj'),
    hf_enabled: document.getElementById('stHfEnabled').checked,
    hf_auto: document.getElementById('stHfAuto').checked,
    train_enabled: document.getElementById('stTrainEnabled').checked,
    train_menu: document.getElementById('stTrainMenu').checked,
    train_workspace: val('stTrainWs'),
    train_soup_bin: val('stTrainBin'),
    code_enabled: document.getElementById('stCodeEnabled').checked,
    code_workspace: val('stCodeWs'),
    github_base: val('stGhBase'),
    code_run_enabled: document.getElementById('stRunEnabled').checked,
    code_run_allowlist: document.getElementById('stRunAllow').value,
    code_run_timeout: val('stRunTimeout'),
    code_permission: val('stCodePerm'),
    code_max_steps: val('stCodeSteps'),
    code_ctx: val('stCodeCtx'),
    code_temp: val('stCodeTemp')
  };
  const key = val('stMem0Key');
  if(mem0KeyClear && !key) body.mem0_api_key = null;   // rensa
  else if(key) body.mem0_api_key = key;                // ny nyckel (annars orörd)
  const gh = val('stGhToken');
  if(ghTokenClear && !gh) body.github_token = null;    // rensa
  else if(gh) body.github_token = gh;                  // ny token (annars orörd)
  const hft = val('stHfToken');
  if(hfTokenClear && !hft) body.hf_token = null;       // rensa
  else if(hft) body.hf_token = hft;                    // ny token (annars orörd)
  return body;
}
async function saveSettings(){
  try{
    const r = await api('/api/settings', {method:'POST', headers:headers(true),
      body: JSON.stringify(collectSettings())});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'okänt fel');
    toast('Inställningar sparade');
    try{ const cr = await fetch('/api/config', {headers: headers(false)}); if(cr.ok) cfg = await cr.json(); }catch(e){}
    populateBackends();
    const wsRow=document.getElementById('csWebsearchRow'); if(wsRow) wsRow.style.display = cfg.websearch?'flex':'none';
    const memRow=document.getElementById('csMemoryRow'); if(memRow) memRow.style.display = cfg.memory?'flex':'none';
    const memTools=document.getElementById('csMemoryTools'); if(memTools) memTools.style.display = cfg.memory?'block':'none';
    updateCodeView();
    updateHfView();
    updateTrainNav();
    loadSettingsForm();
  }catch(e){ toast('Kunde inte spara: '+e.message, true); }
}
async function testMem0(){
  const el = document.getElementById('stMem0Test');
  el.textContent = 'Sparar & testar…'; el.style.color='var(--subtle)';
  await saveSettings();          // testa exakt det som står i formuläret
  el.textContent = 'Testar…';
  try{
    const r = await api('/api/settings/test-mem0', {method:'POST', headers:headers(true), body:'{}'});
    const d = await r.json();
    el.textContent = d.ok ? ('✓ Ansluten till Mem0 (hittade '+(d.count||0)+' minne(n) för användar-ID:t)')
                          : ('✕ '+(d.error||'kunde inte ansluta'));
    el.style.color = d.ok ? 'var(--green)' : 'var(--danger)';
  }catch(e){ el.textContent = '✕ '+e.message; el.style.color='var(--danger)'; }
}

/* ---- Kodassistent ---- */
let codeMessages = [];      // {role, content} som skickas till /api/agent
let codeController = null;
const CODE_MSGS_KEY = 'os_code_msgs';
function saveCodeMsgs(){
  // Spara Codex-konversationen (kontexten) så den överlever omladdning. Behåll de senaste.
  try{ localStorage.setItem(CODE_MSGS_KEY, JSON.stringify(codeMessages.slice(-40))); }catch(e){}
}
function loadCodeMsgs(){
  try{ const a = JSON.parse(localStorage.getItem(CODE_MSGS_KEY) || '[]');
       codeMessages = Array.isArray(a) ? a : []; }catch(e){ codeMessages = []; }
}
function codeMsgText(content){
  // Läsbar prosa ur ett assistentsvar: ta bort redigeringsblock och ev. TOOL-rader.
  let t = stripEditsJs(content || '');
  t = t.replace(/^\s*TOOL\s+\w+\s+\{[\s\S]*?\}\s*$/gm, '').trim();
  return t;
}
function restoreCodeLog(){
  // Rita upp den sparade Codex-konversationen igen (som text) efter omladdning.
  const box = codeLogEl();
  if(!box || !codeMessages.length) return;
  box.innerHTML = '';
  for(const m of codeMessages){
    if(m.role === 'user'){
      codeAppend('<div class="code-user">'+esc(m.content||'')+'</div>');
    } else {
      const t = codeMsgText(m.content||'');
      if(t) codeAppend('<div class="code-msg">'+mdToHtml(t)+'</div>');
      else  codeAppend('<div class="code-tool">↩ tidigare kodförslag (återställt vid omladdning)</div>');
    }
  }
}
function clearCode(){
  const box = codeLogEl();
  if(!codeMessages.length && box && box.querySelector('.chat-empty')){ toast('Codex är redan tom'); return; }
  // Var tydlig med vad som faktiskt försvinner: loggen och kontexten – aldrig
  // filerna. Väntande, osparade förslag lever bara i loggen och följer med.
  const pending = pendingEdits().length;
  const warn = pending
    ? ('\n\n⚠ ' + pending + (pending === 1 ? ' föreslagen ändring som du inte sparat'
        : ' föreslagna ändringar som du inte sparat') + ' försvinner.')
    : '';
  if(!confirm('Töm Codex-loggen?\n\nKonversationen och kontexten rensas. Filerna i arbetsytan '
      + 'rörs inte – ändringar du redan sparat ligger kvar på disken.' + warn)) return;
  if(codeController) codeController.abort();
  codeMessages = [];
  codeWrites = [];
  planNode = null;
  updateModeBar();
  try{ localStorage.removeItem(CODE_MSGS_KEY); }catch(e){}
  if(box) box.innerHTML = '<div class="chat-empty">Be Codex läsa koden, ändra en fil eller köra '
    + 'testerna. Den arbetar bara i mappen ovan, och <b>Behörighet</b> ovanför styr vad den får '
    + 'göra utan att fråga.</div>';
  const inp = document.getElementById('codeInput'); if(inp) inp.focus();
}
function populateCodeModels(){
  const sel = document.getElementById('codeModel');
  if(!sel) return;
  const names = lastModels.map(m=>m.name);
  const cur = sel.value;
  if(!names.length){ sel.innerHTML = '<option value="">Inga modeller installerade</option>'; return; }
  sel.innerHTML = names.map(n=>'<option>'+esc(n)+'</option>').join('');
  // Codex har en egen, ihågkommen modell (databas) – oberoende av chattens val.
  const saved = uiPrefs.code_model || '';
  const coder = names.find(n=>/coder|codellama|deepseek|starcoder|qwen.*cod/i.test(n));
  if(saved && names.includes(saved)) sel.value = saved;
  else if(cur && names.includes(cur)) sel.value = cur;
  else sel.value = (coder || names[0]);
}
function saveCodeModel(){ savePref('code_model', document.getElementById('codeModel').value); }
async function loadTree(){
  const box = document.getElementById('codeTree');
  const pathEl = document.getElementById('codeWsPath');
  if(!box) return;
  box.innerHTML = '<div class="hint" style="padding:6px 8px">Hämtar…</div>';
  try{
    const r = await api('/api/agent/tree', {headers: headers(false)});
    const d = await r.json();
    if(pathEl) pathEl.textContent = d.root || '';
    const files = d.files || [];
    if(!files.length){ box.innerHTML = '<div class="hint" style="padding:6px 8px">(tom eller ingen arbetsyta)</div>'; return; }
    box.innerHTML = files.map(f=>'<div class="f" title="'+esc(f)+'" onclick="askAboutFile(\''
      + esc(f).replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">'+esc(f)+'</div>').join('');
  }catch(e){ box.innerHTML = '<div class="hint" style="padding:6px 8px">Kunde inte hämta trädet.</div>'; }
}
function askAboutFile(path){
  const inp = document.getElementById('codeInput');
  inp.value = 'Förklara vad '+path+' gör.';
  inp.focus();
}
/* ---- Analys av arbetsytan ----
   Läser igenom projektet så Codex vet vad den jobbar med och var funktionerna
   bor, i stället för att börja blint och bränna steg på att leta. Körs när en
   arbetsyta väljs; resultatet cachas på servern. */
let analyzedPath = null;          // vilken arbetsyta som redan analyserats
function analyzeNote(html, kind){
  const el = document.getElementById('codeAnalyzeNote');
  if(!el) return;
  el.style.display = html ? 'block' : 'none';
  el.className = 'chatwarn ' + (kind || 'ok');
  el.innerHTML = html || '';
}
async function analyzeWorkspace(force){
  if(!cfg.code_ws) return;                        // inget att analysera
  const path = cfg.code_ws_path || '';
  if(!force && analyzedPath === path) return;     // redan gjord för den här ytan
  analyzeNote('🔎 <b>Analyserar repot…</b> läser igenom filerna för att se vad '
    + 'projektet innehåller och var funktionerna finns. Det tar oftast bara någon '
    + 'sekund – du kan börja skriva under tiden.', 'warn');
  try{
    const r = await api('/api/agent/analyze', {method:'POST', headers:headers(true),
      body: JSON.stringify({force: !!force})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error || 'kunde inte analysera');
    analyzedPath = path;
    analyzeNote('✓ <b>Repot är analyserat.</b> ' + esc(d.summary)
      + ' — Codex vet nu vad projektet innehåller och kan slå upp var något '
      + 'definieras i stället för att leta.', 'ok');
    // Låt beskedet stå en stund och tona sedan bort; det är information, inte en varning.
    setTimeout(()=>{ if(analyzedPath === path) analyzeNote(''); }, 12000);
  }catch(e){
    analyzeNote('⚠ Kunde inte analysera repot: ' + esc(e.message)
      + '. Codex fungerar ändå, men får leta sig fram.', 'warn');
  }
}

/* ---- Behörighetsläge: fråga om lov, skriv själv, eller fria händer ---- */
const CODE_MODE_HINTS = {
  ask: 'Codex frågar innan den skriver en fil, kör ett kommando eller rör git. Tryggast.',
  auto_edit: 'Codex ändrar filer direkt (varje skrivning går att ångra), men frågar innan '
    + 'den kör kommandon eller committar.',
  full: '⚠ Codex gör allt själv – skriver filer, kör kommandon (även utanför listan) och '
    + 'committar utan att fråga. Använd bara i ett projekt du kan återställa.'
};
function updateModeBar(){
  const bar = document.getElementById('codeModeBar');
  const sel = document.getElementById('codeMode');
  const hint = document.getElementById('codeModeHint');
  if(!bar || !sel) return;
  const ws = !!cfg.code_ws;                       // läget gäller server-arbetsytan
  bar.style.display = cfg.code ? 'flex' : 'none';
  const mode = CODE_MODE_HINTS[cfg.code_mode] ? cfg.code_mode : 'ask';
  sel.value = mode;
  bar.classList.toggle('full', mode === 'full');
  if(hint) hint.textContent = ws ? CODE_MODE_HINTS[mode]
    : 'Gäller när en arbetsyta på servern är vald. I skisslage finns inga verktyg att godkänna.';
  const ub = document.getElementById('codeUndoBtn');
  if(ub) ub.style.display = (ws && codeWrites.length) ? '' : 'none';
}
async function saveCodeMode(){
  const mode = document.getElementById('codeMode').value;
  try{
    const r = await api('/api/agent/mode', {method:'POST', headers:headers(true),
      body: JSON.stringify({mode})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'kunde inte spara');
    cfg.code_mode = mode;
    updateModeBar();
    toast('Behörighet: ' + d.label);
  }catch(e){ toast('Kunde inte byta läge: '+e.message, true); }
}
/* Filer Codex skrivit i den här körningen – ger Ångra-knappen något att peka på. */
let codeWrites = [];
function noteWrite(path){
  if(!path) return;
  codeWrites = codeWrites.filter(p=>p!==path);
  codeWrites.push(path);
  updateModeBar();
}
async function undoFile(path, node){
  try{
    const r = await api('/api/agent/undo', {method:'POST', headers:headers(true),
      body: JSON.stringify({path})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.message||d.error||'kunde inte ångra');
    codeWrites = codeWrites.filter(p=>p!==path);
    if(node){ const st = node.querySelector('.state'); if(st) st.textContent = '↩ Ångrad'; }
    toast(d.message); loadTree(); gitStatus(); updateModeBar();
  }catch(e){ toast('Kunde inte ångra: '+e.message, true); }
}
function undoLast(){
  const path = codeWrites[codeWrites.length-1];
  if(!path){ toast('Inget att ångra'); return; }
  if(confirm('Ångra den senaste ändringen av ' + path + '?')) undoFile(path, null);
}
/* ---- Frågerutor: agenten vill göra något och väntar på svar ---- */
let askSeq = 0;
function renderAsk(ev){
  const id = 'ask'+(askSeq++);
  const detail = ev.detail ? '<pre class="code-diff">'+diffToHtml(ev.detail)+'</pre>' : '';
  const node = codeAppend(
    '<div class="code-ask'+(ev.danger?' danger':'')+'" id="'+id+'">'
    + '<div class="ah"><span class="what">'+(ev.danger?'⚠ ':'🔐 ')+esc(ev.title||'Får jag?')+'</span>'
    + '<span class="acts">'
    + '<button class="btn accent small" onclick="answerAsk(\''+id+'\',true,false)">Tillåt</button>'
    + '<button class="btn ghost small" onclick="answerAsk(\''+id+'\',true,true)" '
    + 'title="Tillåt det här för resten av körningen">Tillåt alltid</button>'
    + '<button class="btn ghost small" onclick="answerAsk(\''+id+'\',false,false)">Neka</button>'
    + '</span></div>' + detail + '</div>');
  node._askId = ev.id;
  node.scrollIntoView({block:'nearest'});
  return node;
}
async function answerAsk(nodeId, allow, always){
  const node = document.getElementById(nodeId);
  if(!node || !node._askId) return;
  node.classList.add('done');
  node.querySelector('.ah').insertAdjacentHTML('beforeend',
    '<span class="state">'+(allow ? (always?'✓ Tillåtet (alltid)':'✓ Tillåtet') : '✕ Nekat')+'</span>');
  try{
    await api('/api/agent/permission', {method:'POST', headers:headers(true),
      body: JSON.stringify({id: node._askId, allow, always})});
  }catch(e){ toast('Kunde inte skicka svaret: '+e.message, true); }
}
let planNode = null;      // planen ritas om i samma panel under en körning
function renderPlan(items){
  const list = items || [];
  const done = list.filter(i=>i.done).length;
  const html = '<div class="t">PLAN · '+done+'/'+list.length+' klara</div>'
    + list.map(i=>'<div class="i'+(i.done?' done':(i.active?' active':''))+'">'
      + (i.done?'✓ ':(i.active?'▸ ':'○ ')) + esc(i.text) + '</div>').join('');
  // Uppdatera den befintliga panelen – annars staplas en ny kopia för varje gång
  // agenten bockar av en punkt, och loggen blir omöjlig att följa.
  if(planNode && planNode.isConnected){ planNode.innerHTML = html; return planNode; }
  planNode = codeAppend('<div class="code-plan">'+html+'</div>');
  return planNode;
}
/* En ändring som agenten redan skrivit (auto_edit / fria händer) – med Ångra.
   I lokalt mappläge sparas det gamla innehållet i webbläsaren i stället för på servern. */
const localUndo = new Map();     // sökväg -> innehåll före ändringen (null = fanns inte)
function renderApplied(ev){
  const id = 'appl'+(codeEditSeq++);
  const node = codeAppend(
    '<div class="code-edit done" id="'+id+'">'
    + '<div class="eh"><span class="path">'+esc(ev.path)+(ev.created?' <span class="hint">(ny fil)</span>':'')+'</span>'
    + '<span class="acts2"><button class="btn ghost small">↩ Ångra</button></span>'
    + '<span class="state">✓ Skrivet</span></div>'
    + '<pre class="code-diff">'+diffToHtml(ev.diff||'')+'</pre></div>');
  const local = !!ev.local;
  if(local && ev.before !== undefined) localUndo.set(ev.path, ev.created ? null : ev.before);
  const btn = node.querySelector('.acts2 button');
  if(btn) btn.onclick = ()=> local ? undoLocal(ev.path, node) : undoFile(ev.path, node);
  if(!local) noteWrite(ev.path);
  return node;
}
async function undoLocal(path, node){
  if(!localUndo.has(path)){ toast('Inget att ångra för '+path, true); return; }
  const before = localUndo.get(path);
  try{
    if(before === null){
      // Filen fanns inte innan – töm den (webbläsaren får inte radera filer utan vidare).
      await fsWrite(path, '');
      toast('Tömde '+path+' (filen fanns inte innan – ta bort den själv om du vill)');
    } else {
      await fsWrite(path, before);
      toast('Återställde '+path);
    }
    localUndo.delete(path);
    if(node){ const st = node.querySelector('.state'); if(st) st.textContent = '↩ Ångrad'; }
    loadLocalTree();
  }catch(e){ toast('Kunde inte ångra: '+(e.message||e), true); }
}
/* Kort, läsbar form av verktygets argument – hela filinnehåll ska inte fylla loggen. */
function toolArgsText(args){
  if(!args || typeof args!=='object') return '';
  const out = {};
  for(const k of Object.keys(args)){
    const v = args[k];
    out[k] = (typeof v==='string' && v.length>80) ? (v.slice(0,80)+'… ('+v.length+' tecken)') : v;
  }
  try{ return JSON.stringify(out); }catch(e){ return ''; }
}
function codeLogEl(){ return document.getElementById('codeLog'); }
function codeAppend(html){
  const box = codeLogEl();
  if(box.querySelector('.chat-empty')) box.innerHTML='';
  const div = document.createElement('div');
  div.innerHTML = html;
  const node = div.firstElementChild;
  box.appendChild(node);
  box.scrollTop = box.scrollHeight;
  return node;
}
function diffToHtml(diff){
  return esc(diff||'').split('\n').map(l=>{
    let c='ctx';
    if(l.startsWith('+++')||l.startsWith('---')) c='hd';
    else if(l.startsWith('@@')) c='hd';
    else if(l.startsWith('+')) c='add';
    else if(l.startsWith('-')) c='del';
    return '<span class="'+c+'">'+l+'</span>';
  }).join('\n');
}
/* Enkel rad-diff (LCS) mellan gammalt och nytt innehåll -> unified-liknande text. */
function jsLineDiff(oldText, newText){
  const A=(oldText||'').split('\n'), B=(newText||'').split('\n');
  const n=A.length, m=B.length;
  if(n>1500 || m>1500) return null;   // för stor -> hoppa diff (visa nytt innehåll)
  const dp=[]; for(let i=0;i<=n;i++){ dp.push(new Int32Array(m+1)); }
  for(let i=n-1;i>=0;i--) for(let j=m-1;j>=0;j--)
    dp[i][j] = (A[i]===B[j]) ? dp[i+1][j+1]+1 : Math.max(dp[i+1][j], dp[i][j+1]);
  const out=[]; let i=0,j=0;
  while(i<n && j<m){
    if(A[i]===B[j]){ out.push(' '+A[i]); i++; j++; }
    else if(dp[i+1][j] >= dp[i][j+1]){ out.push('-'+A[i]); i++; }
    else { out.push('+'+B[j]); j++; }
  }
  while(i<n){ out.push('-'+A[i]); i++; }
  while(j<m){ out.push('+'+B[j]); j++; }
  return out.join('\n');
}
let codeEditSeq = 0;
function renderEdit(ed){
  const id = 'edit'+(codeEditSeq++);
  let acts, bodyHtml;
  if(ed.local){                                   // lokal mapp i webbläsaren → skriv lokalt
    acts = '<button class="btn accent small" onclick="applyEditLocal(\''+id+'\')">Godkänn</button>'
         + '<button class="btn ghost small" onclick="rejectEdit(\''+id+'\')">Avvisa</button>';
    let d = (!ed.isNew && ed.old!=null && ed.old!==ed.content) ? jsLineDiff(ed.old, ed.content) : null;
    bodyHtml = d ? diffToHtml(d) : esc(ed.content);
  } else if(ed.scratch || !cfg.code_ws){          // ingen arbetsyta → bara kopiera
    acts = '<button class="btn ghost small" onclick="copyEdit(\''+id+'\')">Kopiera</button>';
    bodyHtml = esc(ed.content);
  } else {                                        // server-arbetsyta → skriv på servern
    acts = '<button class="btn accent small" onclick="applyEdit(\''+id+'\')">Godkänn</button>'
         + '<button class="btn ghost small" onclick="rejectEdit(\''+id+'\')">Avvisa</button>';
    bodyHtml = ed.diff ? diffToHtml(ed.diff) : esc(ed.content);
  }
  const tag = ed.local && ed.isNew ? ' <span class="hint">(ny fil)</span>' : '';
  const node = codeAppend(
    '<div class="code-edit" id="'+id+'">'
    + '<div class="eh"><span class="path">'+esc(ed.path)+tag+'</span>'
    + '<span class="acts">'+acts+'</span></div>'
    + '<pre class="code-diff">'+bodyHtml+'</pre></div>');
  node._edit = ed;
  return node;
}
function copyEdit(id){
  const node = document.getElementById(id);
  if(!node || !node._edit) return;
  const text = node._edit.content || '';
  if(navigator.clipboard){ navigator.clipboard.writeText(text).then(()=>{
    node.querySelector('.eh').insertAdjacentHTML('beforeend','<span class="state">✓ Kopierat</span>');
  }).catch(()=>toast('Kunde inte kopiera', true)); }
}
async function applyEdit(id){
  const node = document.getElementById(id);
  if(!node || !node._edit) return;
  try{
    const r = await api('/api/agent/apply', {method:'POST', headers:headers(true),
      body: JSON.stringify({path: node._edit.path, content: node._edit.content})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'fel');
    node.classList.add('done');
    node.querySelector('.eh').insertAdjacentHTML('beforeend','<span class="state">✓ Skrivet</span>');
    noteWrite(d.path || node._edit.path);   // så Ångra-knappen når även godkända förslag
    toast('Ändring skriven: '+node._edit.path);
    loadTree(); gitStatus();
  }catch(e){ toast('Kunde inte skriva: '+e.message, true); }
}
function rejectEdit(id){
  const node = document.getElementById(id);
  if(!node) return;
  node.classList.add('done');
  node.querySelector('.eh').insertAdjacentHTML('beforeend','<span class="state">✕ Avvisad</span>');
}
/* ---- Godkänn/avvisa alla väntande ändringar ---- */
function pendingEdits(){
  return [...document.querySelectorAll('#codeLog .code-edit:not(.done)')]
    .filter(n=>n._edit && (n._edit.local || (!n._edit.scratch && cfg.code_ws)));
}
function appendBatchBar(){
  if(pendingEdits().length < 2) return;
  const barId='batch'+(codeEditSeq++);
  const n = pendingEdits().length;
  codeAppend('<div class="code-batch" id="'+barId+'">'+n+' föreslagna ändringar · '
    +'<button class="btn accent small" onclick="approveAll(\''+barId+'\')">✓ Godkänn alla</button> '
    +'<button class="btn ghost small" onclick="rejectAll(\''+barId+'\')">✕ Avvisa alla</button></div>');
}
async function approveAll(barId){
  const bar=document.getElementById(barId); if(bar) bar.remove();
  for(const node of pendingEdits()){
    if(node._edit.local) await applyEditLocal(node.id);
    else await applyEdit(node.id);
  }
}
function rejectAll(barId){
  const bar=document.getElementById(barId); if(bar) bar.remove();
  pendingEdits().forEach(node=>rejectEdit(node.id));
}
async function sendAgent(){
  const model = document.getElementById('codeModel').value;
  const inp = document.getElementById('codeInput');
  const text = inp.value.trim();
  if(!model){ toast('Ingen modell vald', true); return; }
  if(codeController || !text) return;
  codeMessages.push({role:'user', content:text});
  planNode = null;          // ny fråga → ny plan
  saveCodeMsgs();
  codeAppend('<div class="code-user">'+esc(text)+'</div>');
  inp.value='';
  // Knappen blir en stoppknapp under körningen (klick → codeController.abort()).
  // Den får INTE stängas av – då går körningen inte att avbryta.
  const send = document.getElementById('codeSend'); send.textContent='■ Stoppa';
  codeController = new AbortController();
  try{
    if(localDir) await runAgentLocal(model);      // lokal mapp i webbläsaren
    else await runAgentServer(model);             // server-arbetsyta eller skisslage
  }catch(e){
    if(e.name!=='AbortError') codeAppend('<div class="code-tool">⚠ '+esc(e.message)+'</div>');
  }finally{
    codeController=null; send.textContent='Skicka';
    saveCodeMsgs();   // spara konversationen (överlever omladdning)
  }
}
async function runAgentServer(model){
  // finalMessage = modellens rena slutsvar (servern har redan strippat TOOL-rader
  // och FIL-block). Det är DET som ska sparas som konversationen – inte varje stegs
  // råtext. Förut klistrades alla steg ihop UTAN avskiljare, så den sparade texten
  // blev "TOOL list_dir {...}TOOL read_file {...}…" på en enda rad: omöjlig att
  // städa, synlig i loggen efter omladdning, och skickad tillbaka till modellen som
  // historik – vilket lärde den att fortsätta klistra ihop TOOL-rader.
  let think = null, thinkText='', finalMessage='', stepTexts=[];
  const r = await api('/api/agent', {method:'POST', headers:headers(true),
    body: JSON.stringify({model, messages: codeMessages}), signal: codeController.signal});
  if(!r.ok){ const d=await r.json().catch(()=>({})); throw new Error(d.error||('HTTP '+r.status)); }
  const reader = r.body.getReader(); const dec = new TextDecoder(); let buf='';
  while(true){
    const {done, value} = await reader.read();
    if(done) break;
    buf += dec.decode(value, {stream:true});
    let i;
    while((i = buf.indexOf('\n')) >= 0){
      const line = buf.slice(0,i).trim(); buf = buf.slice(i+1);
      if(!line) continue;
      let ev; try{ ev = JSON.parse(line); }catch(e){ continue; }
      if(ev.type==='step'){
        if(thinkText.trim()) stepTexts.push(thinkText.trim());   // reserv, se nedan
        thinkText=''; think=null;
        // Utan steg-tak är det här enda tecknet på att den fortfarande jobbar.
        const send=document.getElementById('codeSend');
        if(send && codeController) send.textContent='■ Stoppa (steg '+ev.n+')';
      }
      else if(ev.type==='start'){
        if(ev.mode && ev.mode!==cfg.code_mode){ cfg.code_mode = ev.mode; updateModeBar(); }
        codeAppend('<div class="code-step">Behörighet: '+esc(ev.mode_label||ev.mode||'')
          + ' · ' + (ev.steps ? 'max '+ev.steps+' steg' : 'obegränsat antal steg')
          + (ev.ctx ? ' · kontext '+ev.ctx+' token' : '')+'</div>');
      }
      else if(ev.type==='delta'){
        thinkText += ev.text;
        if(!think) think = codeAppend('<div class="code-think"></div>');
        think.textContent = thinkText;
        codeLogEl().scrollTop = codeLogEl().scrollHeight;
      }
      else if(ev.type==='ask'){
        if(think){ think.remove(); think=null; }
        renderAsk(ev);
      }
      else if(ev.type==='answer'){ /* svaret ritas redan när knappen trycks */ }
      else if(ev.type==='tool'){
        if(think){ think.remove(); think=null; }
        if(ev.todo){ renderPlan(ev.todo); continue; }
        const icon = ev.name==='run_command' ? '▶'
          : (ev.denied ? '🚫' : (ev.wrote ? '✍' : '🔧'));
        let html = '<div class="code-tool">'+icon+' <b>'+esc(ev.name)+'</b> '
          + esc(toolArgsText(ev.args))+' → '+esc(ev.summary||'');
        if(ev.detail) html += '<pre class="code-diff" style="margin-top:6px">'+esc(ev.detail)+'</pre>';
        codeAppend(html+'</div>');
        // Skrev agenten en fil? Visa diffen med en Ångra-knapp.
        if(ev.wrote && ev.path){ renderApplied({path: ev.path, diff: ev.diff||''}); }
      }
      else if(ev.type==='message'){
        if(think){ think.remove(); think=null; }
        if(ev.text){ finalMessage = ev.text;
          codeAppend('<div class="code-msg">'+mdToHtml(ev.text)+'</div>'); }
      }
      else if(ev.type==='applied'){ if(think){ think.remove(); think=null; } renderApplied(ev); }
      else if(ev.type==='edit'){ renderEdit(ev); }
      else if(ev.type==='summary'){
        const parts = [];
        if((ev.files||[]).length) parts.push((ev.files.length===1?'1 fil ändrad: ':ev.files.length+' filer ändrade: ')+ev.files.join(', '));
        if(ev.commands) parts.push(ev.commands+' kommando'+(ev.commands===1?'':'n')+' kört');
        if(ev.denied) parts.push(ev.denied+' åtgärd'+(ev.denied===1?'':'er')+' nekad'+(ev.denied===1?'':'e'));
        if(ev.steps) parts.unshift(ev.steps+' steg');
        if(parts.length) codeAppend('<div class="code-summary">Klart · '+esc(parts.join(' · '))+'</div>');
        if(ev.files && ev.files.length){ loadTree(); gitStatus(); }
      }
      else if(ev.type==='error'){ codeAppend('<div class="code-tool">⚠ '+esc(ev.text)+'</div>'); }
    }
  }
  appendBatchBar();
  // Spara slutsvaret. Kom inget (avbrutet, eller taket nått) faller vi tillbaka på
  // stegens text – då med radbrytning MELLAN stegen, så TOOL-raderna går att städa.
  let saved = finalMessage.trim();
  if(!saved){
    if(thinkText.trim()) stepTexts.push(thinkText.trim());
    saved = codeMsgText(stepTexts.join('\n\n'));
  }
  if(saved) codeMessages.push({role:'assistant', content:saved});
}
/* Anropa modellen (via /api/chat) och strömma svaret. Returnerar full text. */
async function streamModel(convo, onDelta){
  const model = document.getElementById('codeModel').value;
  const r = await api('/api/chat', {method:'POST', headers:headers(true),
    body: JSON.stringify({model, messages: convo}), signal: codeController.signal});
  if(!r.ok) throw new Error('HTTP '+r.status);
  const reader = r.body.getReader(); const dec = new TextDecoder(); let buf='', full='';
  while(true){
    const {done, value} = await reader.read();
    if(done) break;
    buf += dec.decode(value, {stream:true});
    let i;
    while((i = buf.indexOf('\n')) >= 0){
      const line = buf.slice(0,i).trim(); buf = buf.slice(i+1);
      if(!line) continue;
      let msg; try{ msg = JSON.parse(line); }catch(e){ continue; }
      const c = msg.message && msg.message.content;
      if(c){ full += c; if(onDelta) onDelta(c); }
    }
  }
  return full;
}
document.getElementById('codeSend').onclick = ()=>{ if(codeController) codeController.abort(); else sendAgent(); };
document.getElementById('codeInput').addEventListener('keydown', e=>{
  if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); sendAgent(); }
});
document.getElementById('codeModel').addEventListener('change', saveCodeModel);

/* ---- Lokal mapp i webbläsaren (File System Access API) ----
   Låter Codex arbeta mot en mapp på DIN dator även om servern kör någon annanstans.
   Filerna läses/skrivs lokalt i webbläsaren; bara modell-anropen går till servern. */
const FS_OK = ('showDirectoryPicker' in window);
let localDir = null, localDirName = '';
const LOCAL_SKIP = new Set(['.git','__pycache__','node_modules','.venv','venv','.idea','.vscode','dist','build','.mypy_cache']);
function agentLocalSys(){
  // Samma arbetssätt som serverns agent, men allt sker i webbläsaren mot din mapp.
  const mode = CODE_MODE_HINTS[cfg.code_mode] ? cfg.code_mode : 'ask';
  const rule = mode==='full'
    ? 'Du har fria händer: dina ändringar skrivs direkt utan att användaren tillfrågas.'
    : (mode==='auto_edit'
       ? 'Dina filändringar skrivs direkt utan att fråga.'
       : 'Varje skrivning måste användaren godkänna. Får du NEKAT: gör inte om samma sak.');
  return 'Du är Codex, en kodagent som arbetar i en projektmapp på användarens dator. '
    + 'Svara på svenska.\n\n'
    + 'ARBETSSÄTT: läs och sök i koden först – gissa aldrig hur en fil ser ut. Är uppgiften i '
    + 'flera steg, lägg upp en plan med TOOL todo. Ändra sedan med edit_file (byt ut en exakt '
    + 'textbit) eller write_file (ny/liten fil). Sammanfatta kort till slut.\n\n'
    + 'VERKTYG – skriv EXAKT en rad som börjar med "TOOL " följt av namn och ett JSON-objekt, '
    + 'och inget annat på den raden:\n'
    + '  TOOL list_dir {"path": "."}\n'
    + '  TOOL tree {}\n'
    + '  TOOL read_file {"path": "fil.py", "start": 1, "end": 200}\n'
    + '  TOOL search {"query": "text"}\n'
    + '  TOOL edit_file {"path": "fil.py", "old_text": "exakt text", "new_text": "det den ska bli"}\n'
    + '  TOOL write_file {"path": "ny.py", "content": "hela filens innehåll"}\n'
    + '  TOOL todo {"items": ["Läs koden", "Ändra X"]}\n\n'
    + 'REGLER:\n- ' + rule + '\n'
    + '- edit_file kräver att old_text finns exakt en gång – ta med omgivande rader.\n'
    + '- Det finns inga kommandon eller git här (mappen ligger i webbläsaren).\n'
    + '- När du är klar: skriv svaret som vanlig text utan TOOL-rad.';
}

/* Fråga om lov i lokalt läge – samma ruta, men svaret stannar i webbläsaren. */
function needsOkLocal(kind){
  const mode = CODE_MODE_HINTS[cfg.code_mode] ? cfg.code_mode : 'ask';
  if(mode==='full') return false;
  if(mode==='auto_edit' && kind==='edit') return false;
  return true;
}
const localAlways = new Set();
function askLocal(kind, key, title, detail){
  if(!needsOkLocal(kind) || localAlways.has(key)) return Promise.resolve(true);
  return new Promise(resolve=>{
    const id = 'lask'+(askSeq++);
    const body = detail ? '<pre class="code-diff">'+diffToHtml(detail)+'</pre>' : '';
    const node = codeAppend(
      '<div class="code-ask" id="'+id+'">'
      + '<div class="ah"><span class="what">🔐 '+esc(title)+'</span>'
      + '<span class="acts">'
      + '<button class="btn accent small" data-a="1">Tillåt</button>'
      + '<button class="btn ghost small" data-a="2">Tillåt alltid</button>'
      + '<button class="btn ghost small" data-a="0">Neka</button>'
      + '</span></div>' + body + '</div>');
    node.scrollIntoView({block:'nearest'});
    const done = (allow, always)=>{
      node.classList.add('done');
      node.querySelector('.ah').insertAdjacentHTML('beforeend',
        '<span class="state">'+(allow?(always?'✓ Tillåtet (alltid)':'✓ Tillåtet'):'✕ Nekat')+'</span>');
      if(allow && always) localAlways.add(key);
      resolve(allow);
    };
    node.querySelectorAll('button').forEach(b=>{
      b.onclick = ()=>done(b.dataset.a!=='0', b.dataset.a==='2');
    });
    // Avbryter användaren körningen räknas det som nej.
    if(codeController) codeController.signal.addEventListener('abort', ()=>done(false,false), {once:true});
  });
}
async function pickLocalDir(){
  if(!FS_OK){ toast('Din webbläsare stödjer inte lokal mapp – använd Chrome/Edge', true); return; }
  try{ localDir = await window.showDirectoryPicker(); }
  catch(e){ return; }   // användaren avbröt
  localDirName = localDir.name;
  try{ if(localDir.requestPermission) await localDir.requestPermission({mode:'readwrite'}); }catch(e){}
  toast('Lokal mapp öppnad: '+localDirName);
  updateCodeView(); loadLocalTree();
}
function closeLocalDir(){ localDir=null; localDirName=''; updateCodeView(); }

async function fsSubdir(path){
  let dir = localDir;
  for(const part of (path||'.').split('/')){ if(part && part!=='.') dir = await dir.getDirectoryHandle(part); }
  return dir;
}
async function fsGetFile(path, create){
  const parts = path.split('/').filter(Boolean);
  let dir = localDir;
  for(let i=0;i<parts.length-1;i++){ dir = await dir.getDirectoryHandle(parts[i], {create}); }
  return await dir.getFileHandle(parts[parts.length-1], {create});
}
async function fsRead(path){ const fh=await fsGetFile(path,false); const f=await fh.getFile(); return await f.text(); }
async function fsWrite(path, content){ const fh=await fsGetFile(path,true); const w=await fh.createWritable(); await w.write(content); await w.close(); }
async function fsWalk(dir, prefix, out, depth){
  for await (const [name, handle] of dir.entries()){
    if(LOCAL_SKIP.has(name)) continue;
    const p = prefix ? prefix+'/'+name : name;
    if(handle.kind==='directory'){ out.push(p+'/'); if(depth<8) await fsWalk(handle,p,out,depth+1); }
    else out.push(p);
    if(out.length>1200) return;
  }
}
async function loadLocalTree(){
  const box = document.getElementById('codeTree'); const pathEl=document.getElementById('codeWsPath');
  if(!box) return;
  if(pathEl) pathEl.textContent = '📂 '+localDirName+' (lokal, i webbläsaren)';
  box.innerHTML = '<div class="hint" style="padding:6px 8px">Läser…</div>';
  try{
    const out=[]; await fsWalk(localDir, '', out, 0);
    out.sort();
    box.innerHTML = out.map(f=>'<div class="f" title="'+esc(f)+'" onclick="askAboutFile(\''
      + esc(f).replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">'+esc(f)+'</div>').join('')
      || '<div class="hint" style="padding:6px 8px">(tom mapp)</div>';
  }catch(e){ box.innerHTML='<div class="hint" style="padding:6px 8px">Kunde inte läsa mappen.</div>'; }
}
const TOOL_NAMES = new Set(['list_dir','tree','read_file','search','edit_file','write_file',
  'run_command','git_status','git_diff','git_branch','git_commit','todo']);
/* Läs ett komplett JSON-objekt som börjar vid text[i]==='{' (klarar flera rader). */
function jsonObjectAt(text, i){
  if(text[i] !== '{') return [null, i];
  let depth=0, inStr=false, esc=false;
  for(let j=i;j<text.length;j++){
    const ch = text[j];
    if(inStr){ if(esc) esc=false; else if(ch==='\\') esc=true; else if(ch==='"') inStr=false; continue; }
    if(ch==='"') inStr=true;
    else if(ch==='{') depth++;
    else if(ch==='}'){ depth--; if(depth===0){
      try{ return [JSON.parse(text.slice(i,j+1)), j+1]; }catch(e){ return [null, j+1]; } } }
  }
  return [null, i];
}
/* Samma toleranta tolkning som servern: flerrads-JSON, ```-block, "TOOL: namn". */
function parseToolJs(text){
  text = text || '';
  const re = /(?:^|\n)[ \t>*-]*TOOL[:\s]+([A-Za-z_]\w*)[ \t]*/g;
  let m;
  while((m = re.exec(text))){
    const name = m[1];
    if(!TOOL_NAMES.has(name)) continue;
    let k = m.index + m[0].length;
    while(k<text.length && ' \t\r\n`'.includes(text[k])){
      if(text.startsWith('```', k)){ k+=3; while(k<text.length && text[k]!=='\n' && text[k]!=='\r') k++; }
      else k++;
    }
    const [args] = jsonObjectAt(text, k);
    if(args && typeof args==='object') return {name, args};
    if(name==='git_status' || name==='tree') return {name, args:{}};
  }
  let i = text.indexOf('{');
  while(i>=0){
    const [obj, nxt] = jsonObjectAt(text, i);
    if(obj && typeof obj==='object'){
      const name = obj.tool || obj.name || obj.verktyg;
      if(TOOL_NAMES.has(name)){
        const args = (obj.args && typeof obj.args==='object') ? obj.args
          : Object.fromEntries(Object.entries(obj).filter(([k])=>!['tool','name','verktyg'].includes(k)));
        return {name, args};
      }
    }
    i = text.indexOf('{', Math.max(nxt, i+1));
  }
  return null;
}
function parseEditsJs(text){
  const edits=[]; const lines=(text||'').split('\n'); let i=0;
  while(i<lines.length){
    const m = lines[i].match(/^\*\*\* ?FIL:\s*(.+?)\s*$/);
    if(m){ const path=m[1].trim(); i++; const body=[];
      while(i<lines.length && !/^\*\*\* ?SLUT\s*$/.test(lines[i])){ body.push(lines[i]); i++; }
      edits.push({path, content: body.join('\n')}); i++; continue; }
    i++;
  }
  return edits;
}
function stripEditsJs(text){
  const lines=(text||'').split('\n'); const out=[]; let i=0;
  while(i<lines.length){
    if(/^\*\*\* ?FIL:/.test(lines[i])){ i++; while(i<lines.length && !/^\*\*\* ?SLUT\s*$/.test(lines[i])) i++; i++; continue; }
    out.push(lines[i]); i++;
  }
  return out.join('\n').trim();
}
/* Enkel rad-diff till förhandsvisning i frågerutan (lokalt läge). */
function previewDiff(oldText, newText, path){
  const d = jsLineDiff(oldText||'', newText||'');
  return d ? ('--- a/'+path+'\n+++ b/'+path+'\n'+d) : ('+++ b/'+path+'\n(för stor för diff)');
}
async function execToolLocal(call){
  try{
    if(call.name==='tree'){
      const out=[]; await fsWalk(localDir,'',out,8);
      return 'Filer i mappen:\n'+(out.slice(0,600).join('\n')||'(tom)');
    }
    if(call.name==='todo'){
      let items = call.args.items || call.args.todos || call.args.plan || [];
      if(typeof items==='string') items = items.split('\n').map(t=>t.replace(/^[-*\s]+/,'').trim()).filter(Boolean);
      const norm = (items||[]).slice(0,20).map(i=> (i && typeof i==='object')
        ? {text:String(i.text||i.task||''), done:!!i.done, active:(String(i.status||'').toLowerCase()==='doing')}
        : {text:String(i), done:false, active:false}).filter(i=>i.text);
      if(!norm.length) return 'FEL: items saknas (en lista med punkter)';
      renderPlan(norm);
      return 'Planen är noterad och visas för användaren.';
    }
    if(call.name==='write_file'){
      const path=(call.args.path||'').trim();
      const content=call.args.content;
      if(!path) return 'FEL: path saknas';
      if(content==null) return 'FEL: content saknas';
      let cur=''; try{ cur = await fsRead(path); }catch(e){}
      const ok = await askLocal('edit', 'write:'+path, 'Skriva filen '+path, previewDiff(cur, content, path));
      if(!ok) return 'NEKAT: användaren sa nej till att skriva '+path+'. Gör inte om samma sak.';
      await fsWrite(path, String(content));
      renderApplied({path, diff: previewDiff(cur, content, path), created: cur==='',
                     local:true, before: cur});
      loadLocalTree();
      return 'OK: skrev '+path+' ('+String(content).length+' tecken).';
    }
    if(call.name==='edit_file'){
      const path=(call.args.path||'').trim();
      const oldText = call.args.old_text!=null ? call.args.old_text : call.args.old;
      const newText = call.args.new_text!=null ? call.args.new_text : call.args.new;
      if(!path) return 'FEL: path saknas';
      if(!oldText) return 'FEL: old_text saknas – ange den exakta text som ska bytas ut.';
      let cur; try{ cur = await fsRead(path); }catch(e){ return 'FEL: ingen fil '+path; }
      const hits = cur.split(oldText).length-1;
      if(hits!==1) return 'FEL: texten finns '+hits+' gånger i '+path
        + '. Den måste finnas exakt en gång – läs filen och ta med fler omgivande rader.';
      const updated = cur.replace(oldText, newText==null?'':newText);
      const ok = await askLocal('edit', 'edit:'+path, 'Ändra i filen '+path, previewDiff(cur, updated, path));
      if(!ok) return 'NEKAT: användaren sa nej till att ändra '+path+'. Gör inte om samma sak.';
      await fsWrite(path, updated);
      renderApplied({path, diff: previewDiff(cur, updated, path), created:false,
                     local:true, before: cur});
      loadLocalTree();
      return 'OK: ändrade '+path+'.';
    }
    if(call.name==='run_command' || call.name==='git_status' || call.name==='git_diff'
       || call.name==='git_branch' || call.name==='git_commit'){
      return 'FEL: '+call.name+' finns inte i lokalt mappläge (mappen ligger i webbläsaren, '
        + 'inte på servern). Välj en arbetsyta på servern om du behöver köra kommandon eller git.';
    }
    if(call.name==='list_dir'){
      const out=[]; await fsWalk(await fsSubdir(call.args.path||'.'), '', out, 6);
      return 'Innehåll:\n'+(out.slice(0,300).join('\n')||'(tom)');
    }
    if(call.name==='read_file'){
      const t = await fsRead(call.args.path); const ln=t.split('\n');
      let s=Math.max(1, call.args.start||1), e=Math.min(ln.length, call.args.end||ln.length);
      return 'Fil '+call.args.path+' (rad '+s+'–'+e+' av '+ln.length+'):\n'
        + ln.slice(s-1,e).map((l,k)=>(s+k)+'\t'+l).join('\n');
    }
    if(call.name==='search'){
      const q=call.args.query||''; const files=[]; await fsWalk(localDir,'',files,8);
      const hits=[];
      for(const f of files){ if(f.endsWith('/')) continue;
        try{ const t=await fsRead(f); const ln=t.split('\n');
          for(let k=0;k<ln.length;k++){ if(ln[k].includes(q)){ hits.push(f+':'+(k+1)+': '+ln[k].trim().slice(0,200)); if(hits.length>=40) break; } }
        }catch(e){}
        if(hits.length>=40) break;
      }
      return 'Sökträffar för '+JSON.stringify(q)+':\n'+(hits.join('\n')||'(inga)');
    }
    return 'Okänt verktyg: '+call.name;
  }catch(e){ return 'FEL: '+(e.message||e); }
}
async function runAgentLocal(model){
  let convo = [{role:'system', content: agentLocalSys()}].concat(codeMessages);
  let assistantFull='';
  const maxSteps = Math.max(1, Math.min(100, cfg.code_steps || 25));
  for(let step=0; step<maxSteps; step++){
    if(codeController.signal.aborted) break;
    let think=null, thinkText='';
    const full = await streamModel(convo, d=>{
      thinkText+=d; if(!think) think=codeAppend('<div class="code-think"></div>');
      think.textContent=thinkText; codeLogEl().scrollTop=codeLogEl().scrollHeight;
    });
    assistantFull = full;
    const call = parseToolJs(full);
    if(call && step<maxSteps-1){
      if(think) think.remove();
      const res = await execToolLocal(call);
      codeAppend('<div class="code-tool">🔧 <b>'+esc(call.name)+'</b> '+esc(JSON.stringify(call.args))
        +'<pre class="code-diff" style="margin-top:6px">'+esc(res.slice(0,4000))+'</pre></div>');
      convo.push({role:'assistant', content:full});
      convo.push({role:'user', content:'VERKTYGSRESULTAT ('+call.name+'):\n'+res});
      continue;
    }
    if(think) think.remove();
    for(const ed of parseEditsJs(full)){
      let cur=''; try{ cur = await fsRead(ed.path); }catch(e){}
      renderEdit({path:ed.path, content:ed.content, local:true, isNew: cur==='', old: cur});
    }
    const msg = stripEditsJs(full);
    if(msg) codeAppend('<div class="code-msg">'+mdToHtml(msg)+'</div>');
    break;
  }
  appendBatchBar();
  // Samma sak lokalt: spara prosan, inte TOOL-raden som råkade vara sist.
  const savedLocal = codeMsgText(assistantFull);
  if(savedLocal) codeMessages.push({role:'assistant', content:savedLocal});
}
async function applyEditLocal(id){
  const node=document.getElementById(id); if(!node||!node._edit) return;
  try{
    await fsWrite(node._edit.path, node._edit.content);
    node.classList.add('done');
    node.querySelector('.eh').insertAdjacentHTML('beforeend','<span class="state">✓ Skrivet lokalt</span>');
    toast('Skrivet: '+node._edit.path); loadLocalTree();
  }catch(e){ toast('Kunde inte skriva: '+(e.message||e), true); }
}

/* ---- Kommandokörning (fas 4) ---- */
async function runManual(){
  const inp = document.getElementById('codeRunInput');
  const cmd = (inp.value||'').trim();
  if(!cmd) return;
  codeAppend('<div class="code-user">▶ '+esc(cmd)+'</div>');
  inp.value='';
  try{
    const r = await api('/api/agent/run', {method:'POST', headers:headers(true), body: JSON.stringify({cmd})});
    const d = await r.json();
    codeAppend('<div class="code-tool">'+(d.ok?'✓':'✕')+' <b>'+esc(cmd)+'</b>'
      + (d.output ? '<pre class="code-diff" style="margin-top:6px">'+esc(d.output)+'</pre>' : '')+'</div>');
  }catch(e){ codeAppend('<div class="code-tool">⚠ '+esc(e.message)+'</div>'); }
}

/* ---- Git / GitHub (fas 3) ---- */
let lastGit = null;
function gitMsg(text, err){
  const el = document.getElementById('codeGitMsg');
  if(el){ el.innerHTML = text || ''; el.style.color = err ? 'var(--danger)' : 'var(--faint)'; }
}
/* ---- GitHub-repo: välj i listan, hämta hem, arbeta, pusha tillbaka ---- */
let repoList = [];
let localRepos = [];        // repon som redan ligger på serverns disk
let repoDir = '';           // mappen på servern där hämtade repon hamnar
async function loadRepos(force){
  const sel = document.getElementById('codeRepoSelect');
  const hint = document.getElementById('codeRepoHint');
  if(!sel) return;
  if(repoList.length && !force){ renderRepos(); return; }
  sel.innerHTML = '<option value="">Hämtar dina repon…</option>';
  try{
    const r = await api('/api/github/repos', {headers: headers(false)});
    const d = await r.json();
    repoList = d.repos || [];
    localRepos = d.local || [];
    if(d.error){
      sel.innerHTML = '<option value="">'+esc(d.error)+'</option>';
      hint.innerHTML = 'Lägg in en GitHub-token i <a href="#" onclick="showView(\'settings\');'
        + 'return false" style="color:var(--accent-hov)">Inställningar</a> för att kunna välja repo.';
      return;
    }
    repoDir = d.dir || '';
    renderRepos(d.current);
  }catch(e){
    sel.innerHTML = '<option value="">Kunde inte hämta listan</option>';
    hint.textContent = e.message;
  }
}
function renderRepos(currentPath){
  const sel = document.getElementById('codeRepoSelect');
  if(!repoList.length){
    sel.innerHTML = '<option value="">Inga repon hittades för din token</option>';
    return;
  }
  sel.innerHTML = '<option value="">Välj ett repo…</option>' + repoList.map(r=>
    '<option value="'+esc(r.slug)+'">'+esc(r.slug)+(r.private?'  🔒':'')
    + (r.desc ? '  –  '+esc(r.desc) : '')+'</option>').join('');
  // Är arbetsytan redan ett hämtat repo? Förvälj det. cfg.code_ws_path är med
  // därför att listan cachas: nästa gång man öppnar Codex anropas renderRepos()
  // utan currentPath, och utan reserven tappades valet – och med det knappen
  // "Ta bort lokalt" – trots att repot fortfarande låg kvar på disken.
  const mine = (currentPath || cfg.code_ws_path || '').split('/').pop();
  const match = repoList.find(r=>mine && mine === r.slug.replace('/','__'));
  if(match) sel.value = match.slug;
  // Utan det här står valet kvar men knapparna vet inte om det: "Ta bort lokalt"
  // förblev dold tills man bytte i listan.
  onRepoPick();
}
function localRepo(slug){
  return localRepos.find(r=>r.slug === slug) || null;
}
function onRepoPick(){
  const slug = document.getElementById('codeRepoSelect').value;
  const btn = document.getElementById('codeRepoFetch');
  if(btn) btn.disabled = !slug;
  // "Ta bort lokalt" visas bara för repon som faktiskt ligger på servern
  const rm = document.getElementById('codeRepoRemove');
  const local = localRepo(slug);
  if(rm){
    rm.style.display = local ? '' : 'none';
    rm.title = local ? ('Radera ' + local.path + ' från serverns disk') : '';
  }
  const hint = document.getElementById('codeRepoHint');
  if(!hint) return;
  if(local){
    const bits = ['📁 hämtat: ' + local.path, 'gren ' + (local.branch||'?')];
    if(local.dirty) bits.push(local.dirty + (local.dirty === 1
      ? ' osparad ändring' : ' osparade ändringar'));
    if(local.ahead) bits.push(local.ahead + (local.ahead === 1
      ? ' opushad commit' : ' opushade commits'));
    hint.textContent = bits.join(' · ');
  }else if(slug){
    hint.textContent = 'Inte hämtat än – klicka "⬇ Hämta & arbeta här".';
  }else{
    // Inget valt: låt inte förra repots text stå kvar och se aktuell ut.
    const fetched = localRepos.length;
    hint.textContent = fetched
      ? ('Välj ett repo i listan. ' + fetched + (fetched === 1
          ? ' repo ligger hämtat på servern' : ' repon ligger hämtade på servern')
        + ' – välj det för att arbeta vidare eller ta bort det.')
      : (repoDir ? ('Hämtas till ' + repoDir) : '');
  }
}
async function removeRepo(){
  const slug = document.getElementById('codeRepoSelect').value;
  const local = localRepo(slug);
  if(!local){ toast('Repot är inte hämtat', true); return; }

  // Varning nummer ett: vad som raderas, och vad som går förlorat.
  const risk = [];
  if(local.dirty) risk.push(local.dirty + (local.dirty === 1
    ? ' osparad ändring' : ' osparade ändringar'));
  if(local.ahead) risk.push(local.ahead + (local.ahead === 1
    ? ' commit som inte pushats till GitHub' : ' commits som inte pushats till GitHub'));
  if(local.ahead === null) risk.push('grenen "' + (local.branch||'?') + '" finns inte på GitHub '
    + '– allt arbete i den är opushat');
  const warn = risk.length
    ? '\n\n⚠ DU FÖRLORAR:\n· ' + risk.join('\n· ') + '\nDet går inte att ångra.'
    : '\n\nAllt arbete verkar pushat till GitHub, så det går att hämta hem igen.';
  if(!confirm('Radera ' + slug + ' från serverns disk?\n\nMappen som tas bort:\n' + local.path
      + warn)) return;

  // Varning nummer två – bara när något faktiskt riskerar att försvinna.
  if(risk.length && !confirm('Sista kontrollen: ' + risk.join(' och ')
      + ' i ' + slug + ' försvinner för alltid.\n\nRadera ändå?')) return;

  const rm = document.getElementById('codeRepoRemove');
  const hint = document.getElementById('codeRepoHint');
  rm.disabled = true;
  hint.textContent = 'Raderar ' + slug + '…';
  try{
    const r = await api('/api/github/remove', {method:'POST', headers:headers(true),
      body: JSON.stringify({repo: slug})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.message || d.error || ('HTTP '+r.status));
    localRepos = d.local || [];
    hint.textContent = '✓ ' + d.message;
    toast(d.message);
    // Arbetsytan kan ha släppts på servern – hämta om läget
    try{ const cr = await fetch('/api/config', {headers: headers(false)}); if(cr.ok) cfg = await cr.json(); }catch(e){}
    updateCodeView(); onRepoPick();
    if(cfg.code_ws){ loadTree(); gitStatus(); }
    else { const t = document.getElementById('codeTree'); if(t) t.innerHTML = ''; gitStatus(); }
  }catch(e){
    hint.textContent = '✕ ' + e.message;
    toast('Kunde inte radera: '+e.message, true);
  }finally{
    rm.disabled = false;
  }
}
async function fetchRepo(){
  const slug = document.getElementById('codeRepoSelect').value;
  if(!slug){ toast('Välj ett repo först', true); return; }
  const btn = document.getElementById('codeRepoFetch');
  const hint = document.getElementById('codeRepoHint');
  btn.disabled = true;
  hint.textContent = 'Hämtar ' + slug + '… (första gången kan ta en stund)';
  analyzeNote('⬇ <b>Hämtar ' + esc(slug) + '…</b> repot analyseras så fort det är nere.', 'warn');
  try{
    const r = await api('/api/github/fetch', {method:'POST', headers:headers(true),
      body: JSON.stringify({repo: slug})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.message || d.error || ('HTTP '+r.status));
    hint.textContent = '✓ ' + d.message + ' · arbetsyta: ' + d.path;
    toast('Arbetar nu mot ' + slug);
    await loadRepos(true);                      // repot finns nu lokalt
    document.getElementById('codeRepoSelect').value = slug;
    onRepoPick();
    // Arbetsytan bytte på servern – hämta om konfig, filträd och git-status
    try{ const cr = await fetch('/api/config', {headers: headers(false)}); if(cr.ok) cfg = await cr.json(); }catch(e){}
    updateCodeView(); loadTree(); gitStatus();
    // Repot är nytt – servern har redan analyserat det vid hämtningen.
    analyzedPath = cfg.code_ws_path || '';
    if(d.analysis) analyzeNote('✓ <b>Repot är analyserat.</b> ' + esc(d.analysis)
      + ' — Codex vet nu vad projektet innehåller och var funktionerna finns.', 'ok');
    else analyzeWorkspace(true);
  }catch(e){
    hint.textContent = '✕ ' + e.message;
    toast('Kunde inte hämta repot: '+e.message, true);
  }finally{
    btn.disabled = false;
  }
}

async function gitStatus(){
  const bar = document.getElementById('codeGit');
  try{
    const r = await api('/api/git/status', {headers: headers(false)});
    const g = await r.json(); lastGit = g;
    if(!g.repo){ bar.style.display='none'; return; }
    bar.style.display='flex';
    const slug = (g.owner && g.repo_name) ? (g.owner+'/'+g.repo_name) : 'ingen GitHub-remote';
    document.getElementById('codeGitInfo').innerHTML =
      'Gren <b>'+esc(g.branch||'?')+'</b> · '+g.changed+' ändrade filer · '+esc(slug)
      + (g.has_token ? '' : ' · <span style="color:var(--amber)">ingen token</span>');
  }catch(e){ bar.style.display='none'; }
}
async function gitPost(path, body){
  const r = await api(path, {method:'POST', headers:headers(true), body: JSON.stringify(body||{})});
  return await r.json();
}
async function gitBranch(){
  const name = prompt('Namn på ny gren:', 'claude/andring');
  if(!name) return;
  gitMsg('Skapar gren…');
  const d = await gitPost('/api/git/branch', {name});
  if(d.status) lastGit=d.status, gitStatus();
  gitMsg(d.ok ? ('✓ Gren skapad: '+esc(name)) : ('✕ '+esc(d.message||d.error||'fel')), !d.ok);
}
async function gitCommit(){
  const msg = prompt('Commit-meddelande:', 'Ändringar via kodassistenten');
  if(!msg) return;
  gitMsg('Committar…');
  const d = await gitPost('/api/git/commit', {message: msg});
  gitStatus();
  gitMsg(d.ok ? '✓ Committat' : ('✕ '+esc(d.message||d.error||'fel')), !d.ok);
}
async function gitPush(){
  const branch = lastGit && lastGit.branch;
  if(!confirm('Pusha grenen "'+(branch||'')+'" till GitHub?')) return;
  gitMsg('Pushar…');
  const d = await gitPost('/api/git/push', {});
  gitMsg(d.ok ? '✓ Pushad' : ('✕ '+esc(d.message||d.error||'fel')), !d.ok);
}
async function githubPR(){
  if(lastGit && lastGit.branch && !lastGit.has_token){
    gitMsg('✕ Ingen GitHub-token sparad (⚙ Inställningar).', true); return;
  }
  const title = prompt('PR-titel:', 'Ändringar via kodassistenten');
  if(title===null) return;
  const body = prompt('PR-beskrivning (valfritt):', '') || '';
  gitMsg('Skapar pull request…');
  const d = await gitPost('/api/github/pr', {title, body});
  if(d.ok && d.url){
    gitMsg('✓ PR skapad: <a href="'+esc(d.url)+'" target="_blank" rel="noopener">'+esc(d.url)+'</a>');
    toast('Pull request skapad');
  }else{
    gitMsg('✕ '+esc(d.message||d.error||'kunde inte skapa PR'), true);
  }
}

/* ---- Delat minne (Mem0) ---- */
function toggleMemoryPanel(){
  const p = document.getElementById('memoryPanel');
  if(!p) return;
  const show = (p.style.display === 'none' || !p.style.display);
  p.style.display = show ? 'block' : 'none';
  if(show) loadMemories();
}
async function memWrite(userText, assistantText){
  try{
    await api('/api/memory/add', {method:'POST', headers:headers(true),
      body: JSON.stringify({messages:[
        {role:'user', content:userText||''},
        {role:'assistant', content:assistantText||''}
      ]})});
  }catch(e){ /* tyst – minnet är en bonus, inte kritiskt */ }
}
async function loadMemories(){
  const list = document.getElementById('memList');
  const cnt = document.getElementById('memCount');
  if(!list) return;
  list.innerHTML = '<div class="mem-empty">Hämtar…</div>';
  try{
    const r = await api('/api/memory', {headers: headers(false)});
    const d = await r.json();
    const mems = d.memories || [];
    if(cnt) cnt.textContent = mems.length ? '('+mems.length+')' : '';
    if(!mems.length){ list.innerHTML = '<div class="mem-empty">Inga sparade minnen än.</div>'; return; }
    list.innerHTML = mems.map(m=>
      '<div class="mem-item"><span>'+esc(m.text)+'</span>'
      + (m.id ? '<button title="Ta bort" onclick="deleteMemory(\''+esc(String(m.id)).replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">✕</button>' : '')
      + '</div>').join('');
  }catch(e){ list.innerHTML = '<div class="mem-empty">Kunde inte hämta minnet.</div>'; }
}
async function addMemory(){
  const inp = document.getElementById('memAddInput');
  const t = (inp.value||'').trim();
  if(!t){ return; }
  inp.value='';
  await memWrite(t, '');   // spara som ett användarpåstående
  toast('Sparat i minnet');
  setTimeout(loadMemories, 600);   // Mem0 kan extrahera med viss fördröjning
}
async function deleteMemory(id){
  try{
    const r = await api('/api/memory/delete', {method:'POST', headers:headers(true),
      body: JSON.stringify({id})});
    const d = await r.json().catch(()=>({}));
    if(!d.ok){ toast('Kunde inte ta bort'+(d.error?': '+d.error:''), true); }
    loadMemories();   // ladda om oavsett så listan speglar faktiskt läge
  }catch(e){ toast('Kunde inte ta bort', true); }
}
async function clearMemories(){
  if(!confirm('Rensa ALLA sparade minnen för den här användaren?')) return;
  try{
    const r = await api('/api/memory/delete', {method:'POST', headers:headers(true),
      body: JSON.stringify({all:true})});   // uttryckligt val – aldrig via tomt id
    const d = await r.json().catch(()=>({}));
    if(d.ok) toast('Minnet rensat');
    else toast('Kunde inte rensa'+(d.error?': '+d.error:''), true);
    loadMemories();
  }catch(e){ toast('Kunde inte rensa', true); }
}
function populateBackends(){
  const sel = document.getElementById('chatBackend');
  const lbl = document.getElementById('chatGpuLabel');
  if(!sel) return;
  if(cfg.multi && cfg.backends && cfg.backends.length > 1){
    const cur = sel.value;
    sel.innerHTML = cfg.backends.map(b=>
      '<option value="'+esc(b.label)+'">'+esc(b.label)+'</option>').join('');
    const labels = cfg.backends.map(b=>b.label);
    const saved = uiPrefs.chat_backend || '';
    if(cur && labels.includes(cur)) sel.value = cur;              // behåll aktivt val
    else if(saved && labels.includes(saved)) sel.value = saved;  // ihågkommet val (databas)
    sel.style.display=''; lbl.style.display='';
  }else{
    sel.style.display='none'; lbl.style.display='none';
  }
}

/* ---- System / GPU ---- */
function mbSize(mb){ return humanSize((Number(mb)||0)*1024*1024); }
function pctBar(frac, color){
  const w = Math.max(0, Math.min(100, frac*100)).toFixed(1);
  return '<div class="usebar"><div style="width:'+w+'%;background:'+(color||'var(--accent)')+'"></div></div>';
}
async function fetchSystem(){
  try{
    const r = await fetch('/api/system', {headers: headers(false)});
    if(!r.ok){ document.getElementById('systemBody').innerHTML='<div class="sys-warn">Kunde inte hämta systeminfo.</div>'; return; }
    lastSystem = await r.json();
    renderSystem(lastSystem);
  }catch(e){}
}
function renderSystem(s){
  const cpu = s.cpu||{}, mem = s.mem||{};
  let html = '<div class="sysgrid">';
  html += '<div class="metric"><div class="h"><span class="name">Processor (CPU)</span>'
        + '<span class="val">'+(cpu.percent!=null?cpu.percent+'%':'–')+'</span></div>'
        + pctBar((cpu.percent||0)/100)
        + '<div class="sub">'+(cpu.cores?cpu.cores+' kärnor':'')
        + (cpu.load?' · load '+cpu.load.map(x=>x.toFixed(2)).join(' / '):'')+'</div></div>';
  const mfrac = (mem.total&&mem.used!=null)?mem.used/mem.total:0;
  html += '<div class="metric"><div class="h"><span class="name">Minne (RAM)</span>'
        + '<span class="val">'+(mem.total?humanSize(mem.used)+' / '+humanSize(mem.total):'–')+'</span></div>'
        + pctBar(mfrac)
        + '<div class="sub">'+(mem.total?(mfrac*100).toFixed(0)+'% använt':'')+'</div></div>';
  html += '</div>';

  html += '<div class="section-title" style="margin-top:14px">Grafikkort (GPU)</div>';
  if(s.gpu_error) html += '<div class="sysnote">'+esc(s.gpu_error)+'</div>';
  if(!s.gpus || !s.gpus.length){
    if(!s.gpu_error) html += '<div class="sysnote">Inga GPU:er rapporterades.</div>';
  } else {
    for(const g of s.gpus){
      const memFrac = g.mem_total_mb ? g.mem_used_mb/g.mem_total_mb : 0;
      const utilFrac = g.util!=null ? g.util/100 : 0;
      const gidx = 'GPU '+g.index;
      let title = '<div class="title"><span class="gidx">'+gidx+'</span>'
                + '<span class="gname">'+esc(g.name||'')+'</span>';
      // Heter backenden samma som indexbrickan blir det bara samma text två gånger.
      for(const bl of (g.backends||[])){
        if(bl.trim() !== gidx) title += '<span class="badge">'+esc(bl)+'</span>';
      }
      const busy = (g.procs||[]).some(p=>p.is_ollama) || (g.mem_used_mb||0) > 400;
      title += '<button class="btn ghost small gpu-unload" data-gpu="'+g.index+'"'
             + (busy ? '' : ' disabled')
             + ' title="'+(busy ? 'Ladda ur modellen så VRAM:et blir ledigt'
                                : 'Ingen modell ligger laddad på det här kortet')+'">'
             + '⏏ Ladda ur</button>';
      title += '</div>';
      const hd = (t,v)=>'<div style="display:flex;justify-content:space-between;font-size:12px;color:var(--subtle);margin-bottom:6px"><span>'+t+'</span><span>'+v+'</span></div>';
      const metrics = '<div class="gpu-metrics">'
        + '<div>'+hd('Användning', g.util!=null?g.util+'%':'–')+pctBar(utilFrac)+'</div>'
        + '<div>'+hd('VRAM', g.mem_total_mb?mbSize(g.mem_used_mb)+' / '+mbSize(g.mem_total_mb):'–')+pctBar(memFrac, g.mem_total_mb&&memFrac>0.9?'var(--danger)':'var(--accent)')+'</div>'
        + '</div>';
      let stats = '<div class="gpu-stats">';
      if(g.temp!=null) stats += '<span>Temp: '+g.temp+' °C</span>';
      if(g.power!=null) stats += '<span>Effekt: '+g.power.toFixed(0)+(g.power_limit?' / '+g.power_limit.toFixed(0):'')+' W</span>';
      stats += '</div>';
      let procs = '';
      const plist = g.procs||[];
      if(plist.length){
        procs = '<div class="gpu-procs">';
        for(const p of plist){
          procs += '<div class="row'+(p.is_ollama?' oll':'')+'"><span>'+(p.is_ollama?'● ':'')
                 + esc(p.name)+' (pid '+p.pid+')</span><span>'+(p.mem_mb!=null?mbSize(p.mem_mb):'')+'</span></div>';
        }
        procs += '</div>';
      } else {
        procs = '<div class="gpu-procs"><div class="row">Inga processer använder denna GPU just nu.</div></div>';
      }
      html += '<div class="gpu-card">'+title+metrics+stats+procs+'</div>';
    }
  }
  document.getElementById('systemBody').innerHTML = html;
  document.querySelectorAll('#systemBody .gpu-unload').forEach(btn=>{
    btn.onclick = ()=>unloadGpu(parseInt(btn.dataset.gpu, 10), btn);
  });
}
/* Ladda ur modellerna som ligger på en GPU, så kortet blir ledigt. */
async function unloadGpu(index, btn){
  const card = btn.closest('.gpu-card');
  const name = card ? (card.querySelector('.gname')||{}).textContent : '';
  // Är inget kort låst till just den här GPU:n träffar urladdningen allt som
  // instansen håller. Säg det INNAN, inte efteråt.
  const multi = (cfg.backends||[]).some(b=>b.gpu!==null && b.gpu!==undefined && b.gpu!=='');
  const warn = multi ? '' :
    '\n\nOBS: Ollama kör som EN instans för alla kort här, så alla laddade '
    + 'modeller laddas ur – inte bara den på GPU '+index+'.';
  if(!confirm('Ladda ur modellen på GPU '+index+(name?' ('+name+')':'')+'?\n\n'
      + 'VRAM:et frigörs. Nästa fråga får ladda modellen igen, vilket tar några '
      + 'sekunder.' + warn)) return;
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '⏳ Laddar ur…';
  try{
    const r = await api('/api/gpu/unload', {method:'POST', headers:headers(true),
      body: JSON.stringify({index})});
    const d = await r.json();
    if(!d.ok) throw new Error((d.failed&&d.failed[0]&&d.failed[0].error) || d.error || 'kunde inte ladda ur');
    if(!d.unloaded.length) toast('Ingen modell låg laddad på GPU '+index);
    else toast('Laddade ur ' + d.unloaded.join(', ')
               + (d.freed_bytes ? ' · ' + humanSize(d.freed_bytes) + ' frigjort' : ''));
    refresh();                                   // visa det tomma kortet direkt
  }catch(e){
    toast('Kunde inte ladda ur: '+e.message, true);
    btn.disabled = false; btn.textContent = old;
  }
}

loadConfig();
refresh();
loadPrefs();   // hämta sparade UI-val (modell, GPU, chattinställningar) från databasen
loadCodeMsgs();   // återställ Codex-konversationen vid omladdning
restoreCodeLog();
