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
  // gpu_actual = där modellen FAKTISKT ligger (matchat mot nvidia-smi).
  // e.gpu är bara backendens etikett, och den binder inte Ollama till kortet –
  // därför kunde den här vyn säga GPU 0 medan System / GPU visade GPU 1.
  if(e.gpu_actual !== null && e.gpu_actual !== undefined) return 'GPU '+e.gpu_actual;
  if(e.gpu !== null && e.gpu !== undefined && e.gpu !== '') return 'GPU '+e.gpu;
  if(cfg.multi && e.backend) return e.backend;
  return '';
}
/* Säger etiketten och verkligheten olika saker? Då är instansen inte låst till
   sitt kort, och det är värt att veta – annars letar man efter fel fel. */
function gpuMismatch(e){
  return (e.gpu_actual !== null && e.gpu_actual !== undefined
          && e.gpu !== null && e.gpu !== undefined && e.gpu !== ''
          && String(e.gpu_actual) !== String(e.gpu))
    ? ('Located on GPU ' + e.gpu_actual + ', but instance is named "' + (e.backend||'')
       + '". The instance is not locked to its card – set CUDA_VISIBLE_DEVICES '
       + 'if you want to control which GPU is used.')
    : '';
}

function headers(json){
  const h = json ? {'Content-Type':'application/json'} : {};
  if(AUTH && token) h['X-Auth-Token'] = token;
  return h;
}
function ensureToken(){
  if(AUTH && !token){
    token = (prompt('Enter access token for Ollama Studio:') || '').trim();
    if(token) localStorage.setItem('os_token', token);
  }
}
async function api(path, opts){
  ensureToken();
  const r = await fetch(path, opts);
  if(r.status === 401){ localStorage.removeItem('os_token'); token=''; throw new Error('Invalid token'); }
  return r;
}

function humanSize(b){
  b = Number(b)||0; const u=['B','KB','MB','GB','TB']; let i=0;
  while(b>=1024 && i<u.length-1){ b/=1024; i++; }
  return (i<2? b.toFixed(0): b.toFixed(1)) + ' ' + u[i];
}
function humanDate(s){
  if(!s) return '';
  const d = new Date(s);
  return isNaN(d) ? (''+s).slice(0,10) : d.toLocaleDateString('en-US');
}
function esc(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

const TITLES = {models:'My Models', discover:'Discover / Install', chat:'Chat', system:'System / GPU', settings:'Settings', code:'Codex', train:'AI Training'};
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
  // System view only polled while shown
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
  await sleep(1500);
  for(let i=0;i<40;i++){
    try{
      const r = await fetch('/api/version', {headers: headers(false)});
      if(r.ok || r.status===401) return true;
    }catch(e){ /* still down – keep polling */ }
    await sleep(750);
  }
  return false;
}

async function updateApp(){
  if(!confirm('Fetch the latest code from GitHub and restart Ollama Studio?\n\n'
            + 'If there is new code, the server will restart and the page will reload – ongoing chat '
            + 'or Codex runs will be interrupted. If everything is already up to date, no restart occurs.')){
    return;
  }
  setStatus('Fetching latest code from GitHub…', 'var(--amber)');
  let res;
  try{
    const r = await api('/api/self-update', {method:'POST', headers: headers(true)});
    res = await r.json();
  }catch(e){
    setStatus('Update failed', 'var(--danger)');
    toast('Could not update: ' + (e && e.message || e), true);
    refresh();
    return;
  }
  if(!res.ok){
    setStatus('Update failed', 'var(--danger)');
    toast(res.output || 'Update failed', true);
    refresh();
    return;
  }
  if(res.restart){
    setStatus('New code fetched — restarting server…', 'var(--amber)');
    toast('Updated – restarting server');
    const back = await waitForServer();
    if(back){ location.reload(); return; }
    setStatus('Server not responding after restart', 'var(--danger)');
    toast('Server did not come back in time – please reload the page manually', true);
    return;
  }
  toast('Already up to date');
  refresh();
}

async function refresh(){
  setStatus('Checking Ollama…', 'var(--amber)');
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
    setStatus('Connected · v'+(v.version||'?'), 'var(--green)');
    renderModels(models);
  }catch(e){
    setStatus('Ollama is not running', 'var(--danger)');
    renderOffline();
  }
  renderCatalog();
}

function runMeta(r){
  const parts = [];
  const warn = gpuMismatch(r);
  const gl = gpuLabel(r);
  if(gl) parts.push(gl + (gpuMismatch(r) ? ' ⚠' : ''));
  const vram = Number(r.size_vram)||0, size = Number(r.size)||0;
  if(vram <= 0) parts.push('running on CPU/RAM');
  else if(vram >= size) parts.push('entirely on GPU · '+humanSize(vram)+' VRAM');
  else parts.push('GPU+CPU · '+humanSize(vram)+' in VRAM');
  if(r.expires_at){
    const d = new Date(r.expires_at);
    if(!isNaN(d)) parts.push('unloads at '+d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}));
  }
  const text = parts.join(' · ');
  return warn ? '<span title="'+esc(warn)+'">'+esc(text)+'</span>' : esc(text);
}
function renderModels(models){
  const box = document.getElementById('modelsList');
  if(!models.length){
    document.getElementById('summary').textContent = '0 models';
    box.innerHTML = '<div class="empty"><h2>No models installed yet</h2>'
      + '<p>Go to "Discover / Install" to download your first model.</p>'
      + '<button class="btn accent" onclick="showView(\'discover\')">Open Discover / Install</button></div>';
    return;
  }
  const total = models.reduce((s,m)=>s+(m.size||0),0);
  const activeNames = models.filter(m=>running.has(m.name)).map(m=>m.name);
  let summary = models.length+(models.length===1 ? ' model · ' : ' models · ')+humanSize(total)+' total';
  if(activeNames.length) summary += ' · '+activeNames.length+' running now';
  document.getElementById('summary').textContent = summary;

  let banner;
  if(activeNames.length){
    const items = activeNames.map(n=>{
      const gpus = running.get(n).map(gpuLabel).filter(Boolean);
      return esc(n) + (gpus.length ? ' ('+gpus.join(', ')+')' : '');
    });
    banner = '<div class="banner">● Active in memory right now: '+items.join(',&nbsp; ')+'</div>';
  }else{
    banner = '<div class="meta" style="margin:6px 2px 8px">No model is currently loaded in memory '
           + '(a model becomes active when used, e.g. via <code>ollama run</code> or a chat request).</div>';
  }

  const cards = models.map(m=>{
    const d = m.details||{};
    const bits = [d.parameter_size, d.quantization_level, d.family, humanSize(m.size),
                  humanDate(m.modified_at)].filter(Boolean).map(esc).join('     ·     ');
    const r = running.get(m.name);
    let liveChip = '', liveMeta = '';
    if(r){
      const gpus = r.map(gpuLabel).filter(Boolean);
      liveChip = '<span class="chip live">● Running now'+(gpus.length ? ' · '+esc(gpus.join(', ')) : '')+'</span>';
      liveMeta = r.map(e=>'<div class="meta live">'+runMeta(e)+'</div>').join('');
    }
    return '<div class="card hoverable"><div class="top"><div>'
      + '<h3>'+esc(m.name)+liveChip+'</h3><div class="meta">'+bits+'</div>'+liveMeta+'</div>'
      + '<button class="btn danger small" data-del="'+esc(m.name)+'">✕ Uninstall</button>'
      + '</div></div>';
  }).join('');
  box.innerHTML = banner + cards;
  box.onclick = onModelsClick;
}
function onModelsClick(ev){
  const btn = ev.target.closest('button[data-del]');
  if(btn) confirmDelete(btn.getAttribute('data-del'));
}
function renderOffline(){
  document.getElementById('summary').textContent = '';
  document.getElementById('modelsList').innerHTML =
    '<div class="empty"><h2>Cannot reach Ollama</h2>'
    + '<p>Make sure Ollama is installed and running on the server.<br>'
    + 'Run:  <code>ollama serve</code>  (or  <code>systemctl start ollama</code>).</p>'
    + '<button class="btn accent" onclick="refresh()">↻ Try again</button></div>';
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
const BYTES_PER_B_PARAM = 0.62 * 1024*1024*1024;
function paramsFromText(text){
  const m = String(text||'').match(/(\d+(?:[.,]\d+)?)\s*b\b/i);
  return m ? parseFloat(m[1].replace(',', '.')) : 0;
}
function estModelBytes(it){
  if(it.size){
    const b = estBytesFromSizeStr(it.size);
    if(b) return b;
  }
  if(it.sizes && it.sizes.length){
    const params = it.sizes.map(paramsFromText).filter(Boolean);
    if(params.length) return Math.min(...params) * BYTES_PER_B_PARAM;
  }
  const fromName = paramsFromText((it.pull||'') + ' ' + (it.name||''));
  return fromName ? fromName * BYTES_PER_B_PARAM : 0;
}
function systemRamBytes(){
  return (lastSystem && lastSystem.mem && lastSystem.mem.total) || 0;
}
function hardwareCapacityBytes(){
  const ram = systemRamBytes();
  return maxGpuVramBytes() + Math.floor(ram * 0.85);
}
function fitsHardware(it){
  const cap = hardwareCapacityBytes(), need = estModelBytes(it) * 1.15;
  if(!(cap > 0 && need > 0)) return true;
  return need <= cap;
}
function fitLabel(it){
  const need = estModelBytes(it) * 1.15;
  if(!need) return '';
  const vram = maxGpuVramBytes(), cap = hardwareCapacityBytes();
  if(!cap) return '';
  if(vram > 0 && need <= vram)
    return '  ·  <span style="color:var(--green)">≈ fits your GPU</span>';
  if(need <= cap)
    return vram
      ? '  ·  <span style="color:var(--amber)">≈ runs partially on CPU (slower)</span>'
      : '  ·  <span style="color:var(--amber)">≈ runs on CPU (slower)</span>';
  return '  ·  <span style="color:var(--danger)">⚠ too large for your hardware</span>';
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

function modelCard(it, index){
  const done = isInstalled(it.pull);
  const buttons = [];
  if(it.source === 'hf')
    buttons.push('<button class="btn ghost small" onclick="hfToggleQuants('+index+')">Variants</button>');
  buttons.push(done
    ? '<span class="installed">✓ Installed</span>'
    : '<button class="btn accent" onclick="startPull(\''+esc(it.pull)+'\')">↓ Install</button>');

  const bits = [];
  if(it.size) bits.push('Size: '+esc(it.size)+fitLabel(it));
  else if(estModelBytes(it)) bits.push('Estimated size: ~'+humanSize(estModelBytes(it))+fitLabel(it));
  if(it.sizes && it.sizes.length) bits.push('Variants: '+esc(it.sizes.join(', ')));
  if(it.downloads) bits.push(it.downloads.toLocaleString('en-US')+' downloads');
  if(it.likes) bits.push('♥ '+it.likes);

  const title = it.url
    ? '<a href="'+esc(it.url)+'" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">'
      + esc(it.name || it.pull) + '</a>'
    : esc(it.name || it.pull);

  return '<div class="card"><div class="top"><div>'
    + '<h3>'+title+(searchResults ? sourceChip(it.source) : '')
    + (it.tag ? '<span class="chip">'+esc(it.tag)+'</span>' : '')
    + '<span class="pull-name">'+esc(it.pull)+'</span></h3>'
    + (it.desc ? '<div class="desc">'+esc(it.desc)+'</div>' : '')
    + (it.gated ? '<div class="hf-gated">⚠ Requires approval on Hugging Face (gated) – '
        + 'Ollama cannot download it without access.</div>' : '')
    + (bits.length ? '<div class="meta">'+bits.join('  ·  ')+'</div>' : '')
    + '</div><div style="display:flex;gap:8px;align-items:center">'+buttons.join('')+'</div></div>'
    + (it.source === 'hf' ? '<div class="hf-quants" id="hfq'+index+'" style="display:none"></div>' : '')
    + '</div>';
}

let searchResults = null;
let hfModels = [];
let hideTooBig = false;

function toggleFitFilter(){
  hideTooBig = document.getElementById('fitOnly').checked;
  savePref('hide_too_big', hideTooBig ? '1' : '0');
  renderCatalog();
}
function showTooBig(){
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
    hint.textContent = 'Hardware could not be detected – filter is disabled.';
    return;
  }
  box.disabled = false;
  hint.textContent = 'Your hardware: ' + hw + (maxGpuVramBytes()
    ? ' – Ollama loads what fits into GPU and runs the rest on CPU.'
    : ' – no GPU found, so everything runs on CPU (slower).');
}

function renderCatalog(){
  const list = document.getElementById('catalogList');
  updateFitHint();
  const searching = !!searchResults;
  const lib = searching ? (searchResults.library || [])
                        : CATALOG.map(it=>Object.assign({source:'ollama'}, it));
  const hf = searching ? (searchResults.hf || []) : [];
  const keptLib = hideTooBig ? lib.filter(fitsHardware) : lib;
  const keptHf  = hideTooBig ? hf.filter(fitsHardware)  : hf;
  hfModels = keptHf;
  const hidden = (lib.length - keptLib.length) + (hf.length - keptHf.length);

  let html = keptLib.map(it=>modelCard(it, -1)).join('')
           + keptHf.map((it,i)=>modelCard(it, i)).join('');
  if(!keptLib.length && !keptHf.length){
    html = hidden
      ? '<div class="empty">All results are too large for this machine. '
        + '<a onclick="showTooBig()" style="color:var(--accent-hov);cursor:pointer">Show them anyway</a></div>'
      : (searching
          ? '<div class="empty">No models matched the search. Try a shorter keyword, '
            + 'or enter an exact model name and click "↓ Download".</div>'
          : '<div class="empty">No models to display.</div>');
  }else if(hidden){
    html += '<div class="hidden-note">' + hidden + (hidden === 1 ? ' model hidden' : ' models hidden')
          + ' that do not fit your hardware · '
          + '<a onclick="showTooBig()">show anyway</a></div>';
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
  hint.textContent = 'Searching Ollama library and Hugging Face…';
  try{
    const r = await api('/api/search?q='+encodeURIComponent(q), {headers: headers(false)});
    const d = await r.json();
    if(seq !== searchSeq) return;
    if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
    searchResults = d;
    const lib = (d.library||[]).length, hf = (d.hf||[]).length;
    hint.textContent = (lib+hf)
      ? ((lib+hf)+' results · '+lib+' in Ollama library, '+hf+' on Hugging Face')
      : 'No results.';
    renderCatalog();
  }catch(e){
    if(seq !== searchSeq) return;
    hint.textContent = 'Could not search online ('+e.message
      + ') – showing results from built-in list.';
  }
}

/* ======================= AI Training (Soup) ======================= */
const TRAIN_META = __TRAIN_JSON__;
let trainStatus = null;
let trainForm = {task:'sft', profile:'4gb', epochs:3, max_length:1024, lr:'2e-5', lora_r:16};
let trainRows = [];
let trainTimer = null;
let trainLogNext = 0;
let trainYamlTimer = null;

function toggleTrainHelp(){
  const box = document.getElementById('trainHelp');
  const open = box.style.display === 'none';
  box.style.display = open ? 'block' : 'none';
  document.getElementById('trHelpBtn').textContent = open ? '✕ Hide instructions' : '📖 Instructions';
}
function toggleYaml(){
  const box = document.getElementById('trYamlBox');
  const open = box.style.display === 'none';
  box.style.display = open ? 'block' : 'none';
  document.getElementById('trYamlBtn').textContent = open
    ? '⚙ Hide configuration' : '⚙ Show configuration (soup.yaml)';
  if(open) refreshYaml();
}
function toggleTrainLog(){
  const box = document.getElementById('trLog');
  const open = box.style.display === 'none';
  box.style.display = open ? 'block' : 'none';
  document.getElementById('trLogBtn').textContent = open ? '📜 Hide log' : '📜 Show log';
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
    pill.innerHTML = '✓ Soup <b>' + esc(soup.version || 'installed') + '</b>';
  }else{
    pill.className = 'tr-pill warn';
    pill.innerHTML = '⚠ Soup is not installed';
  }
  const gpu = document.getElementById('trPillGpu');
  if(s.gpu && s.gpu.vram_mb){
    gpu.className = 'tr-pill';
    gpu.innerHTML = '🖥 <b>' + esc(s.gpu.name || 'GPU') + '</b> · '
      + (s.gpu.vram_mb/1024).toFixed(0) + ' GB VRAM';
  }else{
    gpu.className = 'tr-pill warn';
    gpu.innerHTML = '🖥 No GPU found – training on CPU is slow';
  }
  const dir = document.getElementById('trPillDir');
  dir.innerHTML = '📁 <b>' + esc(s.workspace || '') + '</b>';
  dir.title = 'Config, dataset, and trained models are stored here';
}

function renderTrainSetup(){
  const s = trainStatus, soup = s.soup || {}, box = document.getElementById('trainSetup');
  if(soup.found){ box.innerHTML = ''; return; }
  const job = s.job && s.job.kind === 'install' ? s.job : null;
  box.innerHTML =
    '<div class="tr-step"><div class="tr-num">0</div><div class="tr-body">'
    + '<h2>Install Training Engine</h2>'
    + '<p class="sub">Training is performed by <a href="' + esc(soup.url||'#') + '" target="_blank" '
    + 'rel="noopener" style="color:var(--accent-hov)">Soup</a> – an open source tool '
    + 'not bundled with Ollama Studio. Install once and it is ready to use.</p>'
    + '<div class="tr-note">Command executed: <code style="color:var(--accent-hov)">pip install "'
    + esc(soup.package||'soup-cli[train]') + '"</code><br>Downloads PyTorch and related packages '
    + '(several GB) and takes a few minutes. Server Python is <b>' + esc(s.python||'?') + '</b>'
    + (s.python_ok ? '' : ' – Soup requires ' + esc(soup.python_needed||'3.10–3.12')
        + ', so installation may fail here') + '.</div>'
    + '<div class="tr-actions">'
    + '<button class="btn accent" onclick="trainInstall()"' + (job && job.state==='kör' ? ' disabled' : '')
    + '>⬇ Install Soup</button>'
    + '<span class="sub" style="margin:0">…or run the command yourself on the server and click '
    + '<a href="#" onclick="refreshTrainStatus();return false" style="color:var(--accent-hov)">refresh</a>.</span>'
    + '</div></div></div>';
}

/* ---- Step 1: data ---- */
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
    + '<td><textarea placeholder="E.g. What is our return policy?" oninput="trainRowEdit('+i+',\'instruction\',this.value)">'+esc(r.instruction||'')+'</textarea></td>'
    + '<td><textarea placeholder="(leave empty usually)" oninput="trainRowEdit('+i+',\'input\',this.value)">'+esc(r.input||'')+'</textarea></td>'
    + '<td><textarea placeholder="The desired answer" oninput="trainRowEdit('+i+',\'output\',this.value)">'+esc(r.output||'')+'</textarea></td>'
    + '<td><button class="del" title="Delete row" onclick="trainDelRow('+i+')">✕</button></td></tr>').join('');
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
  if(!filled.length){ toast('Please fill in at least one row with question and answer', true); return; }
  try{
    const name = (document.getElementById('trDataName').value||'').trim() || 'my-dataset.jsonl';
    const d = await trainDataPost({action:'save', name, rows: filled});
    afterDatasetSaved(d);
  }catch(e){ toast('Could not save: '+e.message, true); }
}
async function trainSavePaste(){
  try{
    const name = (document.getElementById('trPasteName').value||'').trim() || 'my-dataset.jsonl';
    const d = await trainDataPost({action:'paste', name,
                                   content: document.getElementById('trPaste').value});
    afterDatasetSaved(d);
  }catch(e){ toast('Could not save: '+e.message, true); }
}
async function trainDemoData(){
  try{
    const d = await trainDataPost({action:'demo', name:'example-data.jsonl'});
    trainRows = TRAIN_META.demo_rows.map(r=>Object.assign({}, r));
    renderTrainRows();
    afterDatasetSaved(d);
    toast('Demo data created – you can now run the entire flow');
  }catch(e){ toast('Could not create demo data: '+e.message, true); }
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
    '<div class="tr-note bad">Could not read file: '+esc(e.message)+'</div>'; }
  refreshYaml(); updateTrainSteps();
}
function renderTrainFiles(){
  const sel = document.getElementById('trFileSelect');
  const files = (trainStatus.datasets||[]);
  sel.innerHTML = files.length
    ? files.map(f=>'<option value="'+esc(f.path)+'">'+esc(f.name)+'  ('+humanSize(f.size)+')</option>').join('')
    : '<option value="">No files in data/ directory yet</option>';
  if(trainForm.data && files.some(f=>f.path===trainForm.data)) sel.value = trainForm.data;
  document.getElementById('trFileHint').innerHTML = 'Place custom files in <code>'
    + esc((trainStatus.workspace||'')+'/data') + '</code> on the server to show them here.';
}
function renderDataInfo(info){
  if(!info){ document.getElementById('trDataInfo').innerHTML=''; return; }
  const rows = (info.examples||[]).map(x=>
    '<div class="row"><div class="q">▸ '+esc(x.in || '(no question)')+'</div>'
    + '<div class="a">→ '+esc(x.ut||'')+'</div></div>').join('');
  const probs = (info.problems||[]).map(p=>'<div class="tr-note warn">'+esc(p)+'</div>').join('');
  document.getElementById('trDataInfo').innerHTML =
    '<div class="tr-note good">✓ <b>'+esc(info.path||'')+'</b> – '+info.rows+' rows · format <b>'
    + esc(info.format)+'</b> · approx '+(info.est_tokens||0).toLocaleString('en-US')+' tokens</div>'
    + probs + (rows ? '<div class="tr-prev">'+rows+'</div>' : '');
}

/* ---- Step 2: model and method ---- */
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
    + '<div class="s">'+esc(p.quantization)+(p.stream_layers?' · layer streaming':'')
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
  if(p){
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
  trainYamlTimer = setTimeout(refreshYaml, 350);
}
async function refreshYaml(){
  try{
    const r = await api('/api/train/config', {method:'POST', headers:headers(true),
      body: JSON.stringify({form: trainForm, save: true})});
    const d = await r.json();
    if(d.yaml) document.getElementById('trYaml').textContent = d.yaml;
  }catch(e){}
}

/* ---- Step 3: execution ---- */
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
  if(!soupOk) hint = 'Install Soup first (step 0 above).';
  else if(!hasData) hint = 'Select or save a dataset in step 1.';
  else if(!hasModel) hint = 'Select a base model in step 2.';
  else if(busy) hint = 'A training run is in progress.';
  else hint = 'Training ' + (trainForm.base||'') + ' on ' + (trainForm.data||'') + '.';
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
    toast('Training has started');
  }catch(e){ toast('Could not start: '+e.message, true); }
}
async function trainStop(){
  try{
    await api('/api/train/stop', {method:'POST', headers:headers(true), body:'{}'});
    toast('Cancelling run…');
  }catch(e){ toast('Could not cancel: '+e.message, true); }
}
async function trainInstall(){
  try{
    const r = await api('/api/train/install', {method:'POST', headers:headers(true), body:'{}'});
    const d = await r.json();
    if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
    trainLogNext = 0;
    renderTrainJob(d.job); startTrainPolling();
    document.getElementById('trLog').style.display = 'block';
    toast('Installing Soup – this will take a few minutes');
  }catch(e){ toast('Could not install: '+e.message, true); }
}
async function trainExport(run){
  try{
    const r = await api('/api/train/export', {method:'POST', headers:headers(true),
      body: JSON.stringify({run})});
    const d = await r.json();
    if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
    trainLogNext = 0;
    renderTrainJob(d.job); startTrainPolling();
    toast('Exporting to Ollama as "'+d.ollama_name+'"');
  }catch(e){ toast('Could not export: '+e.message, true); }
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
      refresh();
      if(d.job.state === 'klar') toast(d.job.label + ' – done!');
      else if(d.job.state === 'fel') toast(d.job.label + ' failed', true);
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
  const state = {'kör':'⏳ Running…','klar':'✓ Done','fel':'✕ Failed','stoppad':'■ Stopped'}[job.state] || job.state;
  document.getElementById('trJobState').innerHTML = esc(state)
    + (job.error ? ' – <span style="color:var(--danger)">'+esc(job.error)+'</span>' : '');
  const chart = document.getElementById('trChart');
  chart.style.display = job.kind === 'train' ? '' : 'none';
  if(job.kind === 'train') drawLossChart(job.history || []);
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
    + 'text-anchor="middle">Loss curve will be plotted once training starts</text>'; return; }
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
    + '<text x="592" y="106" fill="#6b7280" font-size="10" text-anchor="end">step '
    + (history[history.length-1].step||history.length)+'</text>';
}

/* ---- Step 4: finished models ---- */
function renderTrainRuns(){
  const runs = (trainStatus.runs||[]);
  const box = document.getElementById('trRuns');
  if(!runs.length){
    box.innerHTML = '<div class="tr-note">Your trained models will appear here. Run steps 1–3 first.</div>';
    return;
  }
  const busy = trainStatus.job && trainStatus.job.state === 'kör';
  box.innerHTML = runs.map(r=>{
    const inOllama = installed.has(r.ollama_name+':latest') || installed.has(r.ollama_name);
    const right = inOllama
      ? '<span class="installed">✓ In Ollama</span>'
        + '<button class="btn ghost small" onclick="chatWithModel(\''+esc(r.ollama_name)+'\')">💬 Chat</button>'
      : (r.has_model
          ? '<button class="btn accent small" onclick="trainExport(\''+esc(r.name)+'\')"'
            + (busy?' disabled':'') + '>📦 Add to Ollama</button>'
          : '<span class="meta">No model ready in folder</span>');
    return '<div class="tr-runitem"><div><div class="name">'+esc(r.name)+'</div>'
      + '<div class="meta">'+esc(r.path)+' · '+(r.gguf?'GGUF ready · ':'')
      + 'as <code>'+esc(r.ollama_name)+'</code></div></div>'
      + '<div class="right">'+right+'</div></div>';
  }).join('');
}
function chatWithModel(name){
  showView('chat');
  const sel = document.getElementById('chatModel');
  const match = [...sel.options].find(o=>o.value === name || o.value === name+':latest');
  if(match){ sel.value = match.value; saveChatModel(); }
}

/* ---- Hugging Face: quantizations for a repo ---- */
let hfQuants = {};
async function hfToggleQuants(i){
  const m = hfModels[i]; if(!m) return;
  const box = document.getElementById('hfq'+i);
  if(box.style.display !== 'none'){ box.style.display='none'; return; }
  box.style.display='block';
  if(!hfQuants[m.id]){
    box.innerHTML = '<div class="hf-meta">Fetching files…</div>';
    try{
      const r = await api('/api/hf/files?repo='+encodeURIComponent(m.id), {headers: headers(false)});
      const d = await r.json();
      if(!r.ok || d.error) throw new Error(d.error || ('HTTP '+r.status));
      hfQuants[m.id] = d;
    }catch(e){
      box.innerHTML = '<div class="hf-meta">Could not read files: '+esc(e.message)+'</div>';
      return;
    }
  }
  const d = hfQuants[m.id];
  const rows = (d.quants||[]).map(q=>{
    const size = q.size ? humanSize(q.size) : 'unknown size';
    const parts = q.parts > 1 ? ('  ·  '+q.parts+' parts') : '';
    const dflt = (q.quant === d.default) ? ' <span class="chip">default</span>' : '';
    return '<div class="hf-quant"><span class="q">'+esc(q.quant)+'</span>'
      + '<span class="sz">'+esc(size)+esc(parts)+'</span>'+dflt
      + '<button class="btn ghost small" onclick="startPull(\''+esc(q.pull)+'\')">↓ Install</button></div>';
  }).join('');
  box.innerHTML = rows || '<div class="hf-meta">No GGUF files in this repository.</div>';
}

/* ---- Install / download ---- */
function pullCustom(){
  const n = document.getElementById('customName').value.trim();
  if(!n){ toast('Enter a model name first', true); return; }
  startPull(n);
}
async function startPull(name){
  if(pullController){ toast('A download is already in progress', true); return; }
  showView('discover');
  const panel = document.getElementById('dlPanel');
  panel.classList.add('show');
  document.getElementById('dlTitle').textContent = 'Downloading  '+name;
  document.getElementById('dlPct').textContent = '';
  document.getElementById('dlStatus').textContent = 'Preparing…';
  document.getElementById('dlExtra').innerHTML = '';
  pullHf = null; pullError = null;
  const bar = document.getElementById('dlBar'); bar.style.width='0'; bar.style.background='var(--accent)';
  document.getElementById('dlCancel').textContent = 'Cancel';

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
  if(m.hf){ showHfSwitch(m.hf); return; }
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
    pullError = m.error;
    document.getElementById('dlStatus').textContent = 'Error: '+m.error;
  }
}
let pullHf = null;
let pullError = null;
function showHfSwitch(hf){
  pullHf = hf;
  const size = hf.size ? ('  ·  ' + humanSize(hf.size)) : '';
  const quant = hf.quant ? ('  ·  ' + hf.quant) : '';
  let html = '🤗 Hugging Face: <a href="'+esc(hf.url||'')+'" target="_blank" rel="noopener" '
    + 'style="color:var(--accent-hov)">'+esc(hf.repo)+'</a>'+esc(quant)+esc(size);
  if(hf.gated) html += '<br><span style="color:var(--amber)">⚠ Repository is gated – you must '
    + 'accept terms on Hugging Face first.</span>';
  const alts = hf.alternatives || [];
  if(alts.length){
    html += '<br>Other results: ' + alts.map(a =>
      '<a href="#" onclick="startPull(\''+esc(a.pull)+'\');return false" '
      + 'style="color:var(--subtle)">'+esc(a.id)+'</a>').join('  ·  ');
  }
  if(hf.auto) document.getElementById('dlTitle').textContent = 'Downloading  ' + (hf.pull || hf.repo);
  document.getElementById('dlExtra').innerHTML = html;
}
function pullDone(name, outcome, detail){
  const bar = document.getElementById('dlBar');
  const cancel = document.getElementById('dlCancel');
  const shown = (pullHf && pullHf.auto && pullHf.pull) ? pullHf.pull : name;
  if(outcome==='success'){
    bar.style.width='100%'; bar.style.background='var(--green)';
    document.getElementById('dlPct').textContent='100%';
    document.getElementById('dlTitle').textContent='✓  '+shown+' installed';
    document.getElementById('dlStatus').textContent='Done! The model is now available under "My Models".';
    document.getElementById('customName').value='';
    toast('"'+shown+'" installed');
  }else if(outcome==='cancelled'){
    document.getElementById('dlTitle').textContent='Cancelled';
    document.getElementById('dlStatus').textContent='Download was cancelled.';
    bar.style.background='var(--faint)';
  }else{
    document.getElementById('dlTitle').textContent='Download failed';
    document.getElementById('dlStatus').textContent = detail || 'An error occurred.';
    bar.style.background='var(--danger)';
    toast('Failed', true);
  }
  cancel.textContent='Close';
  refresh();
}
function cancelPull(){
  if(pullController){ pullController.abort(); }
  else { document.getElementById('dlPanel').classList.remove('show'); }
}

/* ---- Uninstall ---- */
let deleteTarget = null;
function confirmDelete(name){
  deleteTarget = name;
  document.getElementById('mTitle').textContent = 'Uninstall model?';
  document.getElementById('mBody').textContent =
    'Do you want to delete "'+name+'"?\n\nModel files will be permanently removed from disk.\nYou can always download it again later.';
  document.getElementById('overlay').classList.add('show');
}
function closeModal(){ document.getElementById('overlay').classList.remove('show'); deleteTarget=null; }
document.getElementById('mConfirm').onclick = async ()=>{
  const name = deleteTarget; closeModal();
  if(!name) return;
  setStatus('Removing '+name+'…', 'var(--amber)');
  try{
    const r = await api('/api/delete', {method:'POST', headers:headers(true), body: JSON.stringify({name})});
    if(!r.ok) throw new Error('HTTP '+r.status);
    toast('"'+name+'" uninstalled');
  }catch(e){ toast('Could not delete: '+e.message, true); }
  refresh();
};

/* ---- Chatt ---- */
function populateChatModels(){
  const sel = document.getElementById('chatModel');
  if(!sel) return;
  const names = lastModels.map(m=>m.name);
  const cur = sel.value;
  if(!names.length){ sel.innerHTML = '<option value="">No models installed</option>'; return; }
  sel.innerHTML = names.map(n=>'<option>'+esc(n)+'</option>').join('');
  const saved = uiPrefs.chat_model || '';
  if(cur && names.includes(cur)) sel.value = cur;
  else if(saved && names.includes(saved)) sel.value = saved;
  else{
    const active = [...running.keys()][0];
    sel.value = (active && names.includes(active)) ? active : names[0];
  }
}
function saveChatModel(){ savePref('chat_model', document.getElementById('chatModel').value); }
function autoGrow(el){ el.style.height='auto'; el.style.height=Math.min(el.scrollHeight,160)+'px'; }

/* ---- Image attachments (vision models, e.g. llava) ---- */
let pendingImages = [];
function onChatFiles(ev){
  const files = Array.from(ev.target.files || []);
  files.forEach(f=>{
    if(!f.type || f.type.indexOf('image/') !== 0) return;
    const reader = new FileReader();
    reader.onload = ()=>{ pendingImages.push(reader.result); renderAttachments(); };
    reader.readAsDataURL(f);
  });
  ev.target.value = '';
}
function removeAttachment(i){ pendingImages.splice(i, 1); renderAttachments(); }
function renderAttachments(){
  const el = document.getElementById('chatAttachments');
  if(!el) return;
  if(!pendingImages.length){ el.style.display = 'none'; el.innerHTML = ''; return; }
  el.style.display = 'flex';
  el.innerHTML = pendingImages.map((d, i)=>
    '<div class="thumb"><img src="'+d+'"><button title="Remove" onclick="removeAttachment('+i+')">✕</button></div>').join('');
}
function stripDataUrl(d){ return (''+d).replace(/^data:[^;]+;base64,/, ''); }

function renderChat(){
  const box = document.getElementById('chatMessages');
  if(!chatMessages.length){
    box.innerHTML = '<div class="chat-empty">Select a model and type a message to start chatting.</div>';
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

/* ---- Markdown rendering ---- */
function fmtStats(s){
  const p = [];
  if(s.tps) p.push(s.tps.toFixed(1)+' tok/s');
  if(s.tokens) p.push(s.tokens+' tokens');
  if(s.secs) p.push(s.secs.toFixed(1)+' s');
  if(s.gpu) p.push(s.gpu);
  return p.join('  ·  ');
}
function mdInline(s){
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
        html += '<pre class="code"><button class="copy" onclick="copyCode(this)">Copy</button><code>'
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
  if(inCode){ html += '<pre class="code"><code>'+codeBuf.join('\n')+'</code></pre>'; }
  closeList();
  return html;
}
function copyCode(btn){
  const code = btn.parentElement.querySelector('code');
  const text = code ? code.textContent : '';
  if(navigator.clipboard){
    navigator.clipboard.writeText(text).then(()=>{
      btn.textContent = 'Copied!'; setTimeout(()=>{ btn.textContent = 'Copy'; }, 1500);
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
  if(!model){ toast('No model selected', true); return; }
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
  send.textContent = 'Stop';
  if(box.lastChild) box.lastChild.style.whiteSpace = 'pre-wrap';

  const backend = document.getElementById('chatBackend').value || undefined;
  chatController = new AbortController();
  try{
    const sys = chatSystemPrompt();
    const convo = chatMessages.slice(0, idx).map(m=>{
      const mm = {role:m.role, content:m.content};
      if(m.images && m.images.length) mm.images = m.images.map(stripDataUrl);
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
            if(box.lastChild) box.lastChild.textContent = '🔎 Searching the web'
              + (msg.query ? ': “'+msg.query+'”' : '') + '…';
            box.scrollTop = box.scrollHeight;
            continue;
          }
          if(msg.status === 'reading'){
            if(box.lastChild) box.lastChild.textContent = '📄 Reading '
              + (msg.count || '') + ' pages…';
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
          if(msg.error){ chatMessages[idx].content += '\n[Error: '+msg.error+']'; }
        }catch(e){}
      }
    }
    if(!chatMessages[idx].content) chatMessages[idx].content = '(no response)';
    renderChat();
    if(memory) memWrite(text, chatMessages[idx].content);
  }catch(e){
    if(e.name === 'AbortError') chatMessages[idx].content += '  [cancelled]';
    else { chatMessages[idx].content = '[Error: '+e.message+']'; toast('Chat failed', true); }
    renderChat();
  }finally{
    chatController = null;
    document.getElementById('chatSend').textContent = 'Send';
    saveCurrentConvo();
  }
}
document.getElementById('chatSend').onclick = ()=>{ if(chatController) chatController.abort(); else sendChat(); };
document.getElementById('chatInput').addEventListener('input', e=>autoGrow(e.target));
document.getElementById('chatInput').addEventListener('keydown', e=>{
  if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); sendChat(); }
});

/* ---- Chat settings: system prompt, temperature, context length ---- */
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
  document.getElementById('csWebsearch').checked = true;
  document.getElementById('csMemory').checked = true;
  document.getElementById('csTempVal').textContent = document.getElementById('csTemp').value;
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

/* ---- Saved conversations (localStorage) ---- */
let conversations = [];
let currentConvoId = null;
function loadConvos(){
  try{ conversations = JSON.parse(localStorage.getItem('os_convos') || '[]'); }catch(e){ conversations = []; }
  if(!Array.isArray(conversations)) conversations = [];
}
function persistConvos(){
  try{
    conversations = conversations.slice(0, 50);
    localStorage.setItem('os_convos', JSON.stringify(conversations));
  }catch(e){}
}
function renderConvoSelect(){
  const sel = document.getElementById('convoSelect');
  if(!sel) return;
  if(!conversations.length){ sel.innerHTML = '<option value="">(none saved)</option>'; sel.value = ''; return; }
  const opts = conversations.map(c=>'<option value="'+c.id+'">'+esc(c.title||'Untitled')+'</option>').join('');
  sel.innerHTML = (currentConvoId ? '' : '<option value="">New conversation</option>') + opts;
  sel.value = currentConvoId || '';
}
function convoTitleFrom(msgs){
  const u = msgs.find(m=>m.role==='user');
  let t = u ? u.content.trim().replace(/\s+/g,' ') : 'New conversation';
  return t.length > 40 ? t.slice(0,40)+'…' : (t || 'New conversation');
}
function saveCurrentConvo(){
  if(!chatMessages.length) return;
  const now = Date.now();
  let c = conversations.find(x=>x.id === currentConvoId);
  if(!c){
    c = { id: currentConvoId || (''+now), title: convoTitleFrom(chatMessages) };
    currentConvoId = c.id;
  } else {
    conversations = conversations.filter(x=>x.id !== c.id);
  }
  conversations.unshift(c);
  c.messages = JSON.parse(JSON.stringify(chatMessages)).map(m=>{ delete m.images; return m; });
  c.model = document.getElementById('chatModel').value;
  c.backend = document.getElementById('chatBackend').value;
  c.updatedAt = now;
  persistConvos();
  setActiveConvo(currentConvoId);
  renderConvoSelect();
}
function setActiveConvo(id){
  try{ if(id) localStorage.setItem('os_active_convo', id);
       else localStorage.removeItem('os_active_convo'); }catch(e){}
}
function restoreActiveConvo(){
  let id = '';
  try{ id = localStorage.getItem('os_active_convo') || ''; }catch(e){}
  const c = id && conversations.find(x=>x.id === id);
  if(c){ chatMessages = JSON.parse(JSON.stringify(c.messages || [])); currentConvoId = id; }
}
function clearChatConfirm(){
  if(!chatMessages.length){ toast('Chat is already empty'); return; }
  if(!confirm('Clear this chat? Messages will be deleted.')) return;
  if(chatController) chatController.abort();
  chatMessages = [];
  if(currentConvoId){
    conversations = conversations.filter(x=>x.id !== currentConvoId);
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
  if(!currentConvoId){ toast('No saved conversation selected', true); return; }
  const c = conversations.find(x=>x.id === currentConvoId);
  if(!c) return;
  const t = prompt('Conversation name:', c.title || '');
  if(t !== null){ c.title = t.trim() || c.title; persistConvos(); renderConvoSelect(); }
}
loadConvos();
restoreActiveConvo();

/* ---- Warning: does the model fit on the selected GPU? ---- */
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
  if(!gpus.length) return;

  let g = null;
  const idx = selectedBackendGpu();
  if(idx !== null && idx !== undefined && idx !== '') g = gpus.find(x=>String(x.index)===String(idx));
  else if(gpus.length === 1) g = gpus[0];
  if(!g || !g.mem_total_mb) return;

  const totalB = g.mem_total_mb * 1024*1024;
  const usedB  = (g.mem_used_mb || 0) * 1024*1024;
  const freeB  = Math.max(0, totalB - usedB);
  const needB  = size * 1.15;
  const label  = (document.getElementById('chatBackend').value) || (g.name || ('GPU '+g.index));

  let cls, msg;
  if(needB > totalB){
    cls = 'err';
    msg = '⚠ Model does not fit on ' + esc(label) + ' (' + humanSize(totalB) + '). '
        + 'It requires ~' + humanSize(needB) + ' and would run partially on CPU (slow). '
        + 'Select a smaller model or a card with more VRAM.';
  } else if(needB > freeB){
    cls = 'warn';
    msg = '⚠ May be tight on ' + esc(label) + ': ~' + humanSize(needB) + ' needed but only '
        + humanSize(freeB) + ' free right now (' + humanSize(totalB) + ' total). '
        + 'Unload a model or select another GPU.';
  } else {
    cls = 'ok';
    msg = '✓ Fits on ' + esc(label) + ': ~' + humanSize(needB) + ' needed, '
        + humanSize(freeB) + ' free of ' + humanSize(totalB) + '.';
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

async function refreshRunning(){
  if(AUTH && !token) return;
  if(!lastModels.length) return;
  try{
    const pr = await fetch('/api/running', {headers: headers(false)});
    if(!pr.ok) return;
    const pd = await pr.json();
    const next = buildRunning(pd.models);
    if(runSig(next) !== runSig(running)){ running = next; renderModels(lastModels); }
    else running = next;
  }catch(e){}
}
setInterval(refreshRunning, 5000);

/* ---- Backends (GPU instances) ---- */
async function loadConfig(){
  try{
    const r = await fetch('/api/config', {headers: headers(false)});
    if(r.ok) cfg = await r.json();
  }catch(e){}
  populateBackends();
  const wsRow = document.getElementById('csWebsearchRow');
  if(wsRow) wsRow.style.display = cfg.websearch ? 'flex' : 'none';
  const memRow = document.getElementById('csMemoryRow');
  if(memRow) memRow.style.display = cfg.memory ? 'flex' : 'none';
  const memTools = document.getElementById('csMemoryTools');
  if(memTools) memTools.style.display = cfg.memory ? 'block' : 'none';
  updateCodeView();
  updateHfView();
  updateTrainNav();
}
function updateTrainNav(){
  const nav = document.getElementById('nav-train');
  const show = !!(cfg.train_module && cfg.train_menu);
  if(nav) nav.style.display = show ? '' : 'none';
  if(!show && document.getElementById('nav-train')
     && document.getElementById('nav-train').classList.contains('active')) showView('code');
}
function updateHfView(){
  const hint = document.getElementById('customHint');
  if(!hint) return;
  hint.textContent = cfg.hf
    ? 'Search models in Ollama library and Hugging Face – results appear below. '
      + 'Entering an exact name (e.g. "hf.co/owner/repo:Q4_K_M" or Hugging Face URL) '
      + 'downloads it directly.'
    : 'Search models in Ollama library – results appear below. Entering an exact '
      + 'name downloads it directly.';
}
function updateCodeView(){
  const off = document.getElementById('codeOff');
  const wrap = document.getElementById('codeWrap');
  const on = !!cfg.code;
  const ws = !!cfg.code_ws || !!localDir;
  if(wrap) wrap.style.display = on ? 'flex' : 'none';
  if(off){
    off.style.display = on ? 'none' : 'block';
    if(!on){
      off.innerHTML = '<h2>💻 Codex is disabled</h2>'
        + '<p>Codex helps you write code. Enable it under Settings.<br>'
        + 'Without a workspace it functions as a code chat; with a workspace (on server or '
        + 'a local folder in browser) it can read the project and save edits.</p>'
        + '<button class="btn accent" onclick="showView(\'settings\')">Open Settings</button>';
    }
  }
  const tree = document.querySelector('#view-code .code-tree');
  if(tree) tree.style.display = ws ? 'flex' : 'none';
  const repoBar = document.getElementById('codeRepoBar');
  if(repoBar) repoBar.style.display = on ? 'flex' : 'none';
  const noWs = document.getElementById('codeNoWs');
  if(noWs){
    noWs.style.display = (on && !ws) ? 'block' : 'none';
    const settingsLink = '<a href="#" onclick="showView(\'settings\');return false">Settings</a>';
    noWs.innerHTML = (cfg.code_ws_set && !cfg.code_ws)
      ? '⚠ Workspace <code>' + esc(cfg.code_ws_path || '') + '</code> was not found on the server ('
        + esc(cfg.server_os || '?') + '). Codex can therefore only draft code. Verify that the '
        + 'path exists <b>on the server</b> where the app is running and is readable – fix '
        + 'it in ' + settingsLink + '.'
      : '💡 Draft mode – no workspace selected. Codex writes code for you but cannot read '
        + 'the project or save to disk. Copy the code, or select a workspace in '
        + settingsLink + ' to read/save/run.';
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
let secTokenIsSet = false, secTokenClear = false;
async function loadSettingsForm(){
  let s = {};
  try{ const r = await api('/api/settings', {headers: headers(false)}); s = await r.json(); }
  catch(e){ toast('Could not fetch settings', true); return; }
  const set = (id, v)=>{ const el=document.getElementById(id); if(el) el.value = (v==null?'':v); };
  const chk = (id, v)=>{ const el=document.getElementById(id); if(el) el.checked = !!v; };
  chk('stWebsearch', s.websearch);
  chk('stChatTime', s.chat_time);
  set('stSearchPages', s.websearch_pages);
  set('stKeepAlive', s.keep_alive);
  const timeState = document.getElementById('stTimeState');
  if(timeState) timeState.textContent = s.server_time
    ? ('Server clock: ' + s.server_time + ' – if incorrect, set correct timezone on server '
       + '(e.g. Environment=TZ=Europe/Stockholm in systemd service).')
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
    mem0KeyIsSet ? '● An API key is saved (leave empty to keep)' : 'No API key saved';
  document.getElementById('stMem0Test').textContent = '';
  chk('stHfEnabled', s.hf_enabled);
  chk('stHfAuto', s.hf_auto);
  hfTokenIsSet = !!s.hf_token_set; hfTokenClear = false;
  const hfEl = document.getElementById('stHfToken'); if(hfEl) hfEl.value='';
  const hfState = document.getElementById('stHfTokenState');
  if(hfState) hfState.textContent = hfTokenIsSet
    ? '● A token is saved (leave empty to keep)' : 'No token saved';
  const hfInfo = document.getElementById('stHfState');
  if(hfInfo){
    if(!s.hf_module) hfInfo.textContent = 'Status: module huggingface.py missing beside app – '
      + 'support is inactive. Fetch latest version via ↻ Update.';
    else if(!s.hf_active) hfInfo.textContent = 'Status: disabled.';
    else hfInfo.textContent = 'Status: ✓ on · ' + (s.hf_auto_active
      ? 'unknown model names fetched automatically from Hugging Face'
      : 'unknown model names show Hugging Face results to choose from');
  }
  chk('stTrainEnabled', s.train_enabled);
  chk('stTrainMenu', s.train_menu);
  set('stTrainWs', s.train_workspace);
  set('stTrainBin', s.train_soup_bin);
  const twState = document.getElementById('stTrainWsState');
  if(twState) twState.innerHTML = s.train_workspace_path
    ? ('Used: <code>' + esc(s.train_workspace_path) + '</code> (created when saving a dataset)')
    : '';
  const tState = document.getElementById('stTrainState');
  if(tState){
    if(!s.train_module) tState.textContent = 'Status: module soup_train.py missing beside app '
      + '– tab is hidden. Fetch latest version via ↻ Update.';
    else if(!s.train_active) tState.textContent = 'Status: disabled.';
    else tState.textContent = 'Status: ✓ on – open the 🎓 AI Training tab in the menu.';
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
      cws.innerHTML = 'Server runs on <b>'+esc(s.server_os||'?')+'</b> – specify a path existing '
        + 'on the <b>server</b> filesystem (current folder: <code>'+esc(s.server_cwd||'')+'</code>).';
    } else if(s.code_workspace_ok){
      cws.textContent = '✓ Directory found';
    } else {
      cws.innerHTML = '✕ Directory does not exist on server. Server runs on <b>'+esc(s.server_os||'?')+'</b> – '
        + 'path must exist where the app runs (not on your local machine). '
        + (s.server_os==='Windows' ? '' : 'A Windows path like <code>D:\\…</code> does not work on a Linux server. ')
        + 'Server current folder: <code>'+esc(s.server_cwd||'')+'</code>.';
    }
  }
  set('stGhBase', s.github_base);
  set('stGlUrl', s.gitlab_url || 'http://gitlab.andrix.se');
  set('stGlBase', s.gitlab_base || 'main');
  chk('stRunEnabled', s.code_run_enabled);
  set('stRunAllow', s.code_run_allowlist);
  set('stRunTimeout', s.code_run_timeout);
  const cg = document.getElementById('stCodeGit');
  if(cg){
    if(!s.code_toggle){ cg.textContent = 'Status: disabled – enable Codex to use it.'; }
    else if(!s.code_active){
      cg.innerHTML = 'Status: <b>draft mode</b> – no workspace selected. Codex writes code but '
        + 'cannot read project or save. Select a workspace to read/save/git/run.';
    }
    else{
      const parts = ['✓ Active'];
      parts.push(s.git_available ? 'git available' : '⚠ git missing on server');
      if(s.git_repo){
        const prov = (s.git_provider === 'gitlab') ? 'GitLab' : ((s.git_provider === 'github') ? 'GitHub' : 'Git');
        parts.push(s.git_slug ? (prov + ': ' + s.git_slug) : 'git repo active');
        const tokSet = (s.git_provider === 'gitlab') ? s.gitlab_token_set : s.github_token_set;
        parts.push(tokSet ? 'token set' : '⚠ no token for ' + prov + ' (push/MR/PR requires token)');
      }else{
        parts.push('⚠ workspace is not a git repo (git/PR buttons hidden)');
      }
      parts.push(s.code_run_active ? 'command execution ON' : 'command execution off');
      parts.push('permission: ' + (s.code_mode_label || s.code_mode || 'ask'));
      parts.push(s.code_steps ? ('max ' + s.code_steps + ' steps') : 'unlimited steps');
      parts.push('context ' + (s.code_ctx ? s.code_ctx + ' tokens' : 'Ollama default'));
      cg.textContent = 'Status: ' + parts.join(' · ');
    }
  }
  ghTokenIsSet = !!s.github_token_set; ghTokenClear = false;
  const ghEl = document.getElementById('stGhToken'); if(ghEl) ghEl.value='';
  const ghState = document.getElementById('stGhTokenState');
  if(ghState) ghState.textContent = ghTokenIsSet
    ? '● A token is saved (leave empty to keep)' : 'No token saved';
  glTokenIsSet = !!s.gitlab_token_set; glTokenClear = false;
  const glEl = document.getElementById('stGlToken'); if(glEl) glEl.value='';
  const glState = document.getElementById('stGlTokenState');
  if(glState) glState.textContent = glTokenIsSet
    ? '● A token is saved (leave empty to keep)' : 'No token saved';
  set('stSecTarget', s.sec_target_url || '');
  set('stSecMode', s.sec_test_mode || 'safe');
  secTokenIsSet = !!s.sec_target_token_set; secTokenClear = false;
  const secEl = document.getElementById('stSecToken'); if(secEl) secEl.value='';
  const secState = document.getElementById('stSecTokenState');
  if(secState) secState.textContent = secTokenIsSet
    ? '● A token is saved (leave empty to keep)' : 'No token saved';
  renderProjectTargets(s.project_targets || {});
  populateSecProjectSelect();
  document.getElementById('stDbPath').textContent = s.db_path ? ('Saved in: '+s.db_path) : '';
}
let currentProjectTargets = {};
function renderProjectTargets(targets){
  currentProjectTargets = targets || {};
  const listEl = document.getElementById('stSecProjList');
  if(!listEl) return;
  const keys = Object.keys(currentProjectTargets);
  if(!keys.length){
    listEl.innerHTML = '<span style="color:var(--faint);font-style:italic">No project-specific servers saved yet (uses global target server above).</span>';
    return;
  }
  let html = '';
  for(const k of keys){
    const item = currentProjectTargets[k];
    const tokBadge = item.has_token ? '<span style="background:var(--card);border:1px solid var(--border);padding:1px 5px;border-radius:4px;font-size:10px">🔑 token set</span>' : '';
    html += `
      <div style="display:flex;align-items:center;justify-content:space-between;background:var(--card);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:12px">
        <div style="display:flex;align-items:center;gap:8px">
          <b>🦊 ${esc(k)}:</b>
          <code style="color:var(--accent-hov)">${esc(item.prod_server || '–')}</code>
          ${tokBadge}
        </div>
        <button class="btn ghost small" type="button" onclick="deleteProjectTarget('${esc(k)}')" style="color:var(--danger);padding:2px 6px;font-size:11px" title="Delete mapping">✕</button>
      </div>
    `;
  }
  listEl.innerHTML = html;
}
async function populateSecProjectSelect(){
  const sel = document.getElementById('stSecProjSelect');
  if(!sel || sel._loaded) return;
  sel._loaded = true;
  try{
    const r = await api('/api/gitlab/repos');
    const d = await r.json();
    const repos = d.repos || [];
    let html = '<option value="">-- Select GitLab project --</option>';
    for(const repo of repos){
      const slug = repo.slug || repo.name;
      html += `<option value="${esc(slug)}">${esc(slug)}</option>`;
    }
    sel.innerHTML = html;
    sel.onchange = ()=>{
      const p = sel.value;
      const pt = currentProjectTargets[p] || {};
      const srvEl = document.getElementById('stSecProjServer');
      const tokEl = document.getElementById('stSecProjToken');
      if(srvEl) srvEl.value = pt.prod_server || '';
      if(tokEl) tokEl.value = '';
    };
  }catch(e){
    sel.innerHTML = '<option value="">Could not load projects</option>';
  }
}
async function saveProjectTarget(){
  const sel = document.getElementById('stSecProjSelect');
  const project = (sel ? sel.value : '').trim();
  if(!project){
    toast('Select a GitLab project first', true);
    return;
  }
  const server = (document.getElementById('stSecProjServer').value || '').trim();
  const token = document.getElementById('stSecProjToken').value;
  try{
    const r = await api('/api/gitlab/targets', {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify({project: project, prod_server: server, prod_token: token})
    });
    const d = await r.json();
    if(!d.ok) throw new Error(d.error || 'could not save target server');
    toast('Production server saved for ' + project);
    renderProjectTargets(d.targets);
  }catch(e){
    toast('Could not save: ' + e.message, true);
  }
}
async function deleteProjectTarget(project){
  try{
    const r = await api('/api/gitlab/targets', {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify({project: project, prod_server: '', prod_token: ''})
    });
    const d = await r.json();
    if(!d.ok) throw new Error(d.error || 'could not delete');
    toast('Production server removed for ' + project);
    renderProjectTargets(d.targets);
  }catch(e){
    toast('Could not delete: ' + e.message, true);
  }
}
function clearMem0Key(){
  mem0KeyClear = true; mem0KeyIsSet = false;
  const keyEl = document.getElementById('stMem0Key'); if(keyEl) keyEl.value='';
  document.getElementById('stMem0KeyState').textContent = '✕ Key will be removed upon saving';
}
function clearHfToken(){
  hfTokenClear = true; hfTokenIsSet = false;
  const el = document.getElementById('stHfToken'); if(el) el.value='';
  document.getElementById('stHfTokenState').textContent = '✕ Token will be removed upon saving';
}
function clearGhToken(){
  ghTokenClear = true; ghTokenIsSet = false;
  const el = document.getElementById('stGhToken'); if(el) el.value='';
  document.getElementById('stGhTokenState').textContent = '✕ Token will be removed upon saving';
}
function clearGlToken(){
  glTokenClear = true; glTokenIsSet = false;
  const el = document.getElementById('stGlToken'); if(el) el.value='';
  document.getElementById('stGlTokenState').textContent = '✕ Token will be removed upon saving';
}
function clearSecToken(){
  secTokenClear = true; secTokenIsSet = false;
  const el = document.getElementById('stSecToken'); if(el) el.value='';
  document.getElementById('stSecTokenState').textContent = '✕ Token will be removed upon saving';
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
    gitlab_url: val('stGlUrl') || 'http://gitlab.andrix.se',
    gitlab_base: val('stGlBase'),
    code_run_enabled: document.getElementById('stRunEnabled').checked,
    code_run_allowlist: document.getElementById('stRunAllow').value,
    code_run_timeout: val('stRunTimeout'),
    code_permission: val('stCodePerm'),
    code_max_steps: val('stCodeSteps'),
    code_ctx: val('stCodeCtx'),
    code_temp: val('stCodeTemp'),
    sec_target_url: val('stSecTarget'),
    sec_test_mode: val('stSecMode')
  };
  const key = val('stMem0Key');
  if(mem0KeyClear && !key) body.mem0_api_key = null;
  else if(key) body.mem0_api_key = key;
  const gh = val('stGhToken');
  if(ghTokenClear && !gh) body.github_token = null;
  else if(gh) body.github_token = gh;
  const gl = val('stGlToken');
  if(glTokenClear && !gl) body.gitlab_token = null;
  else if(gl) body.gitlab_token = gl;
  const hft = val('stHfToken');
  if(hfTokenClear && !hft) body.hf_token = null;
  else if(hft) body.hf_token = hft;
  const sec = val('stSecToken');
  if(secTokenClear && !sec) body.sec_target_token = null;
  else if(sec) body.sec_target_token = sec;
  return body;
}
async function saveSettings(){
  try{
    const r = await api('/api/settings', {method:'POST', headers:headers(true),
      body: JSON.stringify(collectSettings())});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'unknown error');
    toast('Settings saved');
    try{ const cr = await fetch('/api/config', {headers: headers(false)}); if(cr.ok) cfg = await cr.json(); }catch(e){}
    populateBackends();
    const wsRow=document.getElementById('csWebsearchRow'); if(wsRow) wsRow.style.display = cfg.websearch?'flex':'none';
    const memRow=document.getElementById('csMemoryRow'); if(memRow) memRow.style.display = cfg.memory?'flex':'none';
    const memTools=document.getElementById('csMemoryTools'); if(memTools) memTools.style.display = cfg.memory?'block':'none';
    updateCodeView();
    updateHfView();
    updateTrainNav();
    loadSettingsForm();
  }catch(e){ toast('Could not save: '+e.message, true); }
}
async function testMem0(){
  const el = document.getElementById('stMem0Test');
  el.textContent = 'Saving & testing…'; el.style.color='var(--subtle)';
  await saveSettings();
  el.textContent = 'Testing…';
  try{
    const r = await api('/api/settings/test-mem0', {method:'POST', headers:headers(true), body:'{}'});
    const d = await r.json();
    el.textContent = d.ok ? ('✓ Connected to Mem0 (found '+(d.count||0)+' memory item(s) for user ID)')
                          : ('✕ '+(d.error||'could not connect'));
    el.style.color = d.ok ? 'var(--green)' : 'var(--danger)';
  }catch(e){ el.textContent = '✕ '+e.message; el.style.color='var(--danger)'; }
}

/* ---- Code assistant ---- */
let codeMessages = [];
let codeController = null;
const CODE_MSGS_KEY = 'os_code_msgs';
function saveCodeMsgs(){
  try{ localStorage.setItem(CODE_MSGS_KEY, JSON.stringify(codeMessages.slice(-40))); }catch(e){}
}
function loadCodeMsgs(){
  try{ const a = JSON.parse(localStorage.getItem(CODE_MSGS_KEY) || '[]');
       codeMessages = Array.isArray(a) ? a : []; }catch(e){ codeMessages = []; }
}
function codeMsgText(content){
  let t = stripEditsJs(content || '');
  t = t.replace(/^\s*TOOL\s+\w+\s+\{[\s\S]*?\}\s*$/gm, '').trim();
  return t;
}
function restoreCodeLog(){
  const box = codeLogEl();
  if(!box || !codeMessages.length) return;
  box.innerHTML = '';
  for(const m of codeMessages){
    if(m.role === 'user'){
      codeAppend('<div class="code-user">'+esc(m.content||'')+'</div>');
    } else {
      const t = codeMsgText(m.content||'');
      if(t) codeAppend('<div class="code-msg">'+mdToHtml(t)+'</div>');
      else  codeAppend('<div class="code-tool">↩ previous code proposal (restored on reload)</div>');
    }
  }
}
function clearCode(){
  const box = codeLogEl();
  if(!codeMessages.length && box && box.querySelector('.chat-empty')){ toast('Codex is already empty'); return; }
  const pending = pendingEdits().length;
  const warn = pending
    ? ('\n\n⚠ ' + pending + (pending === 1 ? ' proposed change not saved'
        : ' proposed changes not saved') + ' will be lost.')
    : '';
  if(!confirm('Clear Codex log?\n\nConversation and context will be cleared. Files in workspace '
      + 'remain untouched – changes already saved stay on disk.' + warn)) return;
  if(codeController) codeController.abort();
  codeMessages = [];
  codeWrites = [];
  planNode = null;
  updateModeBar();
  try{ localStorage.removeItem(CODE_MSGS_KEY); }catch(e){}
  if(box) box.innerHTML = '<div class="chat-empty">Ask Codex to inspect the code, modify a file, or run '
    + 'tests. It only works within the directory above, and <b>Permission</b> controls what it may '
    + 'do without confirmation.</div>';
  const inp = document.getElementById('codeInput'); if(inp) inp.focus();
}
function populateCodeModels(){
  const sel = document.getElementById('codeModel');
  if(!sel) return;
  const names = lastModels.map(m=>m.name);
  const cur = sel.value;
  if(!names.length){ sel.innerHTML = '<option value="">No models installed</option>'; return; }
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
  box.innerHTML = '<div class="hint" style="padding:6px 8px">Loading…</div>';
  try{
    const r = await api('/api/agent/tree', {headers: headers(false)});
    const d = await r.json();
    if(pathEl) pathEl.textContent = d.root || '';
    const files = d.files || [];
    if(!files.length){ box.innerHTML = '<div class="hint" style="padding:6px 8px">(empty or no workspace)</div>'; return; }
    box.innerHTML = files.map(f=>'<div class="f" title="'+esc(f)+'" onclick="askAboutFile(\''
      + esc(f).replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">'+esc(f)+'</div>').join('');
  }catch(e){ box.innerHTML = '<div class="hint" style="padding:6px 8px">Could not load tree.</div>'; }
}
function askAboutFile(path){
  const inp = document.getElementById('codeInput');
  inp.value = 'Explain what '+path+' does.';
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
  analyzeNote('🔎 <b>Analyzing repository…</b> scanning files to understand project '
    + 'structure and locate functions. Usually takes just a second — '
    + 'you can start typing in the meantime.', 'warn');
  try{
    const r = await api('/api/agent/analyze', {method:'POST', headers:headers(true),
      body: JSON.stringify({force: !!force})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error || 'could not analyze');
    analyzedPath = path;
    analyzeNote('✓ <b>Repository analyzed.</b> ' + esc(d.summary)
      + ' — Codex now knows project structure and can look up '
      + 'definitions directly instead of searching.', 'ok');
    // Låt beskedet stå en stund och tona sedan bort; det är information, inte en varning.
    setTimeout(()=>{ if(analyzedPath === path) analyzeNote(''); }, 12000);
  }catch(e){
    analyzeNote('⚠ Could not analyze repository: ' + esc(e.message)
      + '. Codex will still work, but may take longer to locate files.', 'warn');
  }
}

/* ---- Behörighetsläge: fråga om lov, skriv själv, eller fria händer ---- */
const CODE_MODE_HINTS = {
  ask: 'Codex asks before writing a file, running a command, or touching git. Safest.',
  auto_edit: 'Codex modifies files directly (every edit can be undone), but asks before '
    + 'running commands or committing.',
  full: '⚠ Codex does everything autonomously — writes files, runs commands, and '
    + 'commits without asking. Use only in projects you can restore.'
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
    : 'Applies when a server workspace is selected. In scratchpad mode there are no tools to approve.';
  const ub = document.getElementById('codeUndoBtn');
  if(ub) ub.style.display = (ws && codeWrites.length) ? '' : 'none';
}
async function saveCodeMode(){
  const mode = document.getElementById('codeMode').value;
  try{
    const r = await api('/api/agent/mode', {method:'POST', headers:headers(true),
      body: JSON.stringify({mode})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'could not save');
    cfg.code_mode = mode;
    updateModeBar();
    toast('Permission: ' + d.label);
  }catch(e){ toast('Could not change mode: '+e.message, true); }
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
    if(!d.ok) throw new Error(d.message||d.error||'could not undo');
    codeWrites = codeWrites.filter(p=>p!==path);
    if(node){ const st = node.querySelector('.state'); if(st) st.textContent = '↩ Undone'; }
    toast(d.message); loadTree(); gitStatus(); updateModeBar();
  }catch(e){ toast('Could not undo: '+e.message, true); }
}
function undoLast(){
  const path = codeWrites[codeWrites.length-1];
  if(!path){ toast('Nothing to undo'); return; }
  if(confirm('Undo latest change to ' + path + '?')) undoFile(path, null);
}
/* ---- Frågerutor: agenten vill göra något och väntar på svar ---- */
let askSeq = 0;
function renderAsk(ev){
  const id = 'ask'+(askSeq++);
  const detail = ev.detail ? '<pre class="code-diff">'+diffToHtml(ev.detail)+'</pre>' : '';
  const node = codeAppend(
    '<div class="code-ask'+(ev.danger?' danger':'')+'" id="'+id+'">'
    + '<div class="ah"><span class="what">'+(ev.danger?'⚠ ':'🔐 ')+esc(ev.title||'Permission requested')+'</span>'
    + '<span class="acts">'
    + '<button class="btn accent small" onclick="answerAsk(\''+id+'\',true,false)">Allow</button>'
    + '<button class="btn ghost small" onclick="answerAsk(\''+id+'\',true,true)" '
    + 'title="Allow for the rest of this run">Allow always</button>'
    + '<button class="btn ghost small" onclick="answerAsk(\''+id+'\',false,false)">Deny</button>'
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
    '<span class="state">'+(allow ? (always?'✓ Allowed (always)':'✓ Allowed') : '✕ Denied')+'</span>');
  try{
    await api('/api/agent/permission', {method:'POST', headers:headers(true),
      body: JSON.stringify({id: node._askId, allow, always})});
  }catch(e){ toast('Could not send response: '+e.message, true); }
}
let planNode = null;      // planen ritas om i samma panel under en körning
function renderPlan(items){
  const list = items || [];
  const done = list.filter(i=>i.done).length;
  const html = '<div class="t">PLAN · '+done+'/'+list.length+' completed</div>'
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
    + '<div class="eh"><span class="path">'+esc(ev.path)+(ev.created?' <span class="hint">(new file)</span>':'')+'</span>'
    + '<span class="acts2"><button class="btn ghost small">↩ Undo</button></span>'
    + '<span class="state">✓ Written</span></div>'
    + '<pre class="code-diff">'+diffToHtml(ev.diff||'')+'</pre></div>');
  const local = !!ev.local;
  if(local && ev.before !== undefined) localUndo.set(ev.path, ev.created ? null : ev.before);
  const btn = node.querySelector('.acts2 button');
  if(btn) btn.onclick = ()=> local ? undoLocal(ev.path, node) : undoFile(ev.path, node);
  if(!local) noteWrite(ev.path);
  return node;
}
async function undoLocal(path, node){
  if(!localUndo.has(path)){ toast('Nothing to undo for '+path, true); return; }
  const before = localUndo.get(path);
  try{
    if(before === null){
      // Filen fanns inte innan – töm den (webbläsaren får inte radera filer utan vidare).
      await fsWrite(path, '');
      toast('Cleared '+path+' (file did not exist before — remove it manually if desired)');
    } else {
      await fsWrite(path, before);
      toast('Restored '+path);
    }
    localUndo.delete(path);
    if(node){ const st = node.querySelector('.state'); if(st) st.textContent = '↩ Undone'; }
    loadLocalTree();
  }catch(e){ toast('Could not undo: '+(e.message||e), true); }
}
/* Kort, läsbar form av verktygets argument – hela filinnehåll ska inte fylla loggen. */
function toolArgsText(args){
  if(!args || typeof args!=='object') return '';
  const out = {};
  for(const k of Object.keys(args)){
    const v = args[k];
    out[k] = (typeof v==='string' && v.length>80) ? (v.slice(0,80)+'… ('+v.length+' chars)') : v;
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
    acts = '<button class="btn accent small" onclick="applyEditLocal(\''+id+'\')">Apply</button>'
         + '<button class="btn ghost small" onclick="rejectEdit(\''+id+'\')">Reject</button>';
    let d = (!ed.isNew && ed.old!=null && ed.old!==ed.content) ? jsLineDiff(ed.old, ed.content) : null;
    bodyHtml = d ? diffToHtml(d) : esc(ed.content);
  } else if(ed.scratch || !cfg.code_ws){          // ingen arbetsyta → bara kopiera
    acts = '<button class="btn ghost small" onclick="copyEdit(\''+id+'\')">Copy</button>';
    bodyHtml = esc(ed.content);
  } else {                                        // server-arbetsyta → skriv på servern
    acts = '<button class="btn accent small" onclick="applyEdit(\''+id+'\')">Apply</button>'
         + '<button class="btn ghost small" onclick="rejectEdit(\''+id+'\')">Reject</button>';
    bodyHtml = ed.diff ? diffToHtml(ed.diff) : esc(ed.content);
  }
  const tag = ed.local && ed.isNew ? ' <span class="hint">(new file)</span>' : '';
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
    node.querySelector('.eh').insertAdjacentHTML('beforeend','<span class="state">✓ Copied</span>');
  }).catch(()=>toast('Could not copy', true)); }
}
async function applyEdit(id){
  const node = document.getElementById(id);
  if(!node || !node._edit) return;
  try{
    const r = await api('/api/agent/apply', {method:'POST', headers:headers(true),
      body: JSON.stringify({path: node._edit.path, content: node._edit.content})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||'error');
    node.classList.add('done');
    node.querySelector('.eh').insertAdjacentHTML('beforeend','<span class="state">✓ Written</span>');
    noteWrite(d.path || node._edit.path);   // så Ångra-knappen når även godkända förslag
    toast('Change applied: '+node._edit.path);
    loadTree(); gitStatus();
  }catch(e){ toast('Could not write: '+e.message, true); }
}
function rejectEdit(id){
  const node = document.getElementById(id);
  if(!node) return;
  node.classList.add('done');
  node.querySelector('.eh').insertAdjacentHTML('beforeend','<span class="state">✕ Rejected</span>');
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
  codeAppend('<div class="code-batch" id="'+barId+'">'+n+' suggested changes · '
    +'<button class="btn accent small" onclick="approveAll(\''+barId+'\')">✓ Apply all</button> '
    +'<button class="btn ghost small" onclick="rejectAll(\''+barId+'\')">✕ Reject all</button></div>');
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
  if(!model){ toast('No model selected', true); return; }
  if(codeController || !text) return;
  codeMessages.push({role:'user', content:text});
  planNode = null;          // ny fråga → ny plan
  saveCodeMsgs();
  codeAppend('<div class="code-user">'+esc(text)+'</div>');
  inp.value='';
  // Knappen blir en stoppknapp under körningen (klick → codeController.abort()).
  // Den får INTE stängas av – då går körningen inte att avbryta.
  const send = document.getElementById('codeSend'); send.textContent='■ Stop';
  codeController = new AbortController();
  try{
    if(localDir) await runAgentLocal(model);      // lokal mapp i webbläsaren
    else await runAgentServer(model);             // server-arbetsyta eller skisslage
  }catch(e){
    if(e.name!=='AbortError') codeAppend('<div class="code-tool">⚠ '+esc(e.message)+'</div>');
  }finally{
    codeController=null; send.textContent='Send';
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
        if(send && codeController) send.textContent='■ Stop (step '+ev.n+')';
      }
      else if(ev.type==='start'){
        if(ev.mode && ev.mode!==cfg.code_mode){ cfg.code_mode = ev.mode; updateModeBar(); }
        codeAppend('<div class="code-step">Permission: '+esc(ev.mode_label||ev.mode||'')
          + ' · ' + (ev.steps ? 'max '+ev.steps+' steps' : 'unlimited steps')
          + (ev.ctx ? ' · context '+ev.ctx+' tokens' : '')+'</div>');
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
        if((ev.files||[]).length) parts.push((ev.files.length===1?'1 file modified: ':ev.files.length+' files modified: ')+ev.files.join(', '));
        if(ev.commands) parts.push(ev.commands+' command'+(ev.commands===1?'':'s')+' run');
        if(ev.denied) parts.push(ev.denied+' action'+(ev.denied===1?'':'s')+' denied');
        if(ev.steps) parts.unshift(ev.steps+' steps');
        if(parts.length) codeAppend('<div class="code-summary">Done · '+esc(parts.join(' · '))+'</div>');
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

function runSecurityAudit(){
  if(codeController){ toast('Codex is already working — wait or stop first', true); return; }
  const inp = document.getElementById('codeInput');
  if(!inp) return;
  inp.value = 'Perform a comprehensive security audit of the project following the rules in .agents/skills/security-audit/SKILL.md.\n\n'
    + 'WORKFLOW & REQUIREMENTS:\n'
    + '1. Thoroughly investigate the source code using TOOL search and TOOL read_file.\n'
    + '2. Review authentication, sessions, database queries, input sanitization, tokens, API keys, and error messages.\n'
    + '3. FOR EACH INDIVIDUAL FINDING / VULNERABILITY: IMMEDIATELY invoke TOOL create_issue with:\n'
    + '   - title: "[SEC-X] Descriptive title for this specific issue"\n'
    + '   - description: A complete and detailed remediation description formatted in markdown so anyone can fix it:\n'
    + '       ### Location & Affected Code\n'
    + '       Exact filename, function, and line numbers.\n'
    + '       ### Problem & Security Risk\n'
    + '       What the issue is, why it is a risk, and potential impact.\n'
    + '       ### Step-by-Step Remediation Plan & Code Examples\n'
    + '       Exact instructions and complete code examples (before/after) showing exactly how to update the code.\n'
    + '       ### Acceptance Criteria & Verification\n'
    + '       How the developer or tester can verify the fix works.\n'
    + '   - labels: "security,P1" (or P2/P3)\n'
    + '4. IMPORTANT: Create EXACTLY ONE separate issue per finding. NEVER group multiple findings into a single issue.\n'
    + '5. Finish with a brief summary of the created issues.';
  sendAgent();
}

function runPonytailAudit(){
  if(codeController){ toast('Codex is already working — wait or stop first', true); return; }
  const inp = document.getElementById('codeInput');
  if(!inp) return;
  inp.value = 'Read .agents/skills/ponytail-audit/SKILL.md and perform a full codebase audit (ponytail-audit) to identify over-engineering, unnecessary complexity, duplicate logic, and dead code.\n\n'
    + 'WORKFLOW & REQUIREMENTS:\n'
    + '1. Thoroughly investigate the source code using TOOL tree {} and TOOL search / TOOL read_file.\n'
    + '2. Identify places where the standard library can replace custom code, unnecessary wrappers/abstractions, unused code, or excessive complexity (tags: delete, stdlib, native, reuse, yagni, shrink).\n'
    + '3. FOR EACH INDIVIDUAL FINDING / SIMPLIFICATION ITEM: IMMEDIATELY invoke TOOL create_issue with:\n'
    + '   - title: "[PONYTAIL-X] Descriptive title for the simplification"\n'
    + '   - description: A complete and detailed remediation description formatted in markdown so anyone can implement the simplification:\n'
    + '       ### Location & Affected Code\n'
    + '       Exact filename, function, and line numbers.\n'
    + '       ### Problem & Over-engineering / Complexity\n'
    + '       Why this is unnecessary, over-engineered, duplicated, or dead code, and which simplification tag applies (delete/stdlib/native/reuse/yagni/shrink).\n'
    + '       ### Step-by-Step Simplification Plan & Code Examples\n'
    + '       Exact instructions and complete code examples (before/after) showing how the code is removed or replaced with simpler/stdlib code.\n'
    + '       ### Acceptance Criteria & Verification\n'
    + '       How the developer or tester verifies tests still pass and functionality is retained.\n'
    + '   - labels: "refactor,ponytail,P2" (or P3)\n'
    + '4. IMPORTANT: Create EXACTLY ONE separate issue per finding. NEVER group multiple findings into a single issue.\n'
    + '5. Finish with a summary of lines and dependencies saved.';
  sendAgent();
}

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
    ? 'You have full autonomy: your changes are written directly without asking the user.'
    : (mode==='auto_edit'
       ? 'Your file edits are written directly without asking.'
       : 'Every write requires user approval. If DENIED: do not repeat the exact same action.');
  return 'You are Codex, a coding agent working in a project folder on the user\'s computer. '
    + 'Respond in English.\n\n'
    + 'WORKFLOW: read and search code first — never guess file contents. If a task has multiple '
    + 'steps, make a plan with TOOL todo. Then modify with edit_file (replace exact text snippet) '
    + 'or write_file (new/small file). Summarize briefly at the end.\n\n'
    + 'TOOLS — output EXACTLY one line starting with "TOOL " followed by the tool name and a JSON object, '
    + 'and nothing else on that line:\n'
    + '  TOOL list_dir {"path": "."}\n'
    + '  TOOL tree {}\n'
    + '  TOOL read_file {"path": "file.py", "start": 1, "end": 200}\n'
    + '  TOOL search {"query": "text"}\n'
    + '  TOOL edit_file {"path": "file.py", "old_text": "exact text", "new_text": "replacement"}\n'
    + '  TOOL write_file {"path": "new.py", "content": "entire file content"}\n'
    + '  TOOL todo {"items": ["Read code", "Modify X"]}\n\n'
    + 'RULES:\n- ' + rule + '\n'
    + '- edit_file requires old_text to appear exactly once — include surrounding lines if needed.\n'
    + '- Shell commands and git are not available here (directory is in browser FS).\n'
    + '- When done: write the response as regular text without a TOOL line.';
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
      + '<button class="btn accent small" data-a="1">Allow</button>'
      + '<button class="btn ghost small" data-a="2">Allow always</button>'
      + '<button class="btn ghost small" data-a="0">Deny</button>'
      + '</span></div>' + body + '</div>');
    node.scrollIntoView({block:'nearest'});
    const done = (allow, always)=>{
      node.classList.add('done');
      node.querySelector('.ah').insertAdjacentHTML('beforeend',
        '<span class="state">'+(allow?(always?'✓ Allowed (always)':'✓ Allowed'):'✕ Denied')+'</span>');
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
  if(!FS_OK){ toast('Your browser does not support local folders — use Chrome or Edge', true); return; }
  try{ localDir = await window.showDirectoryPicker(); }
  catch(e){ return; }   // användaren avbröt
  localDirName = localDir.name;
  try{ if(localDir.requestPermission) await localDir.requestPermission({mode:'readwrite'}); }catch(e){}
  toast('Local folder opened: '+localDirName);
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
  if(pathEl) pathEl.textContent = '📂 '+localDirName+' (local, browser FS)';
  box.innerHTML = '<div class="hint" style="padding:6px 8px">Reading…</div>';
  try{
    const out=[]; await fsWalk(localDir, '', out, 0);
    out.sort();
    box.innerHTML = out.map(f=>'<div class="f" title="'+esc(f)+'" onclick="askAboutFile(\''
      + esc(f).replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">'+esc(f)+'</div>').join('')
      || '<div class="hint" style="padding:6px 8px">(empty folder)</div>';
  }catch(e){ box.innerHTML='<div class="hint" style="padding:6px 8px">Could not read folder.</div>'; }
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
  return d ? ('--- a/'+path+'\n+++ b/'+path+'\n'+d) : ('+++ b/'+path+'\n(too large for diff)');
}
async function execToolLocal(call){
  try{
    if(call.name==='tree'){
      const out=[]; await fsWalk(localDir,'',out,8);
      return 'Files in folder:\n'+(out.slice(0,600).join('\n')||'(empty)');
    }
    if(call.name==='todo'){
      let items = call.args.items || call.args.todos || call.args.plan || [];
      if(typeof items==='string') items = items.split('\n').map(t=>t.replace(/^[-*\s]+/,'').trim()).filter(Boolean);
      const norm = (items||[]).slice(0,20).map(i=> (i && typeof i==='object')
        ? {text:String(i.text||i.task||''), done:!!i.done, active:(String(i.status||'').toLowerCase()==='doing')}
        : {text:String(i), done:false, active:false}).filter(i=>i.text);
      if(!norm.length) return 'ERROR: items missing (a list of tasks)';
      renderPlan(norm);
      return 'Plan recorded and displayed to the user.';
    }
    if(call.name==='write_file'){
      const path=(call.args.path||'').trim();
      const content=call.args.content;
      if(!path) return 'ERROR: path missing';
      if(content==null) return 'ERROR: content missing';
      let cur=''; try{ cur = await fsRead(path); }catch(e){}
      const ok = await askLocal('edit', 'write:'+path, 'Write file '+path, previewDiff(cur, content, path));
      if(!ok) return 'DENIED: user refused writing '+path+'. Do not repeat this action.';
      await fsWrite(path, String(content));
      renderApplied({path, diff: previewDiff(cur, content, path), created: cur==='',
                     local:true, before: cur});
      loadLocalTree();
      return 'OK: wrote '+path+' ('+String(content).length+' chars).';
    }
    if(call.name==='edit_file'){
      const path=(call.args.path||'').trim();
      const oldText = call.args.old_text!=null ? call.args.old_text : call.args.old;
      const newText = call.args.new_text!=null ? call.args.new_text : call.args.new;
      if(!path) return 'ERROR: path missing';
      if(!oldText) return 'ERROR: old_text missing — specify the exact text to replace.';
      let cur; try{ cur = await fsRead(path); }catch(e){ return 'ERROR: no file '+path; }
      const hits = cur.split(oldText).length-1;
      if(hits!==1) return 'ERROR: text occurs '+hits+' times in '+path
        + '. It must occur exactly once — read the file and include more surrounding lines.';
      const updated = cur.replace(oldText, newText==null?'':newText);
      const ok = await askLocal('edit', 'edit:'+path, 'Modify file '+path, previewDiff(cur, updated, path));
      if(!ok) return 'DENIED: user refused modifying '+path+'. Do not repeat this action.';
      await fsWrite(path, updated);
      renderApplied({path, diff: previewDiff(cur, updated, path), created:false,
                     local:true, before: cur});
      loadLocalTree();
      return 'OK: modified '+path+'.';
    }
    if(call.name==='run_command' || call.name==='git_status' || call.name==='git_diff'
       || call.name==='git_branch' || call.name==='git_commit'){
      return 'ERROR: '+call.name+' is not available in local folder mode (folder is in browser FS, '
        + 'not on server). Select a server workspace if you need to run shell commands or git.';
    }
    if(call.name==='list_dir'){
      const out=[]; await fsWalk(await fsSubdir(call.args.path||'.'), '', out, 6);
      return 'Contents:\n'+(out.slice(0,300).join('\n')||'(empty)');
    }
    if(call.name==='read_file'){
      const t = await fsRead(call.args.path); const ln=t.split('\n');
      let s=Math.max(1, call.args.start||1), e=Math.min(ln.length, call.args.end||ln.length);
      return 'File '+call.args.path+' (lines '+s+'–'+e+' of '+ln.length+'):\n'
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
      return 'Search results for '+JSON.stringify(q)+':\n'+(hits.join('\n')||'(none)');
    }
    return 'Unknown tool: '+call.name;
  }catch(e){ return 'ERROR: '+(e.message||e); }
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
      convo.push({role:'user', content:'TOOL RESULT ('+call.name+'):\n'+res});
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
    node.querySelector('.eh').insertAdjacentHTML('beforeend','<span class="state">✓ Written locally</span>');
    toast('Written: '+node._edit.path); loadLocalTree();
  }catch(e){ toast('Could not write: '+(e.message||e), true); }
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

/* ---- Git / GitLab & GitHub (fas 3) ---- */
let lastGit = null;
function gitMsg(text, err){
  const el = document.getElementById('codeGitMsg');
  if(el){ el.innerHTML = text || ''; el.style.color = err ? 'var(--danger)' : 'var(--faint)'; }
}
/* ---- Repo: välj i listan eller ange själv, hämta hem, arbeta, pusha tillbaka ---- */
let repoProvider = 'gitlab';
let repoList = [];
let localRepos = [];        // repon som redan ligger på serverns disk
let repoDir = '';           // mappen på servern där hämtade repon hamnar

function onProviderChange(){
  const el = document.getElementById('codeRepoProvider');
  if(el) repoProvider = el.value || 'gitlab';
  repoList = [];
  loadRepos(true);
}

async function loadRepos(force){
  const sel = document.getElementById('codeRepoSelect');
  const hint = document.getElementById('codeRepoHint');
  const provEl = document.getElementById('codeRepoProvider');
  if(provEl && provEl.value) repoProvider = provEl.value;
  if(!sel) return;
  if(repoList.length && !force){ renderRepos(); return; }
  sel.innerHTML = '<option value="">Loading your '+(repoProvider==='gitlab'?'GitLab':'GitHub')+' repos…</option>';
  const endpoint = (repoProvider === 'gitlab') ? '/api/gitlab/repos' : '/api/github/repos';
  try{
    const r = await api(endpoint, {headers: headers(false)});
    const d = await r.json();
    repoList = d.repos || [];
    localRepos = d.local || [];
    repoDir = d.dir || '';
    if(d.error){
      sel.innerHTML = '<option value="">'+esc(d.error)+'</option>';
      const provName = repoProvider === 'gitlab' ? 'GitLab token' : 'GitHub token';
      hint.innerHTML = 'Add a ' + provName + ' in <a href="#" onclick="showView(\'settings\');'
        + 'return false" style="color:var(--accent-hov)">Settings</a> to list repos automatically, or click <b>➕ Custom repo…</b> to enter directly.';
      renderRepos(d.current);
      return;
    }
    renderRepos(d.current);
  }catch(e){
    sel.innerHTML = '<option value="">Could not load repo list</option>';
    hint.textContent = e.message;
  }
}
function renderRepos(currentPath){
  const sel = document.getElementById('codeRepoSelect');
  if(!sel) return;
  const seen = new Set();
  const options = [];
  repoList.forEach(r => {
    seen.add(r.slug);
    options.push({slug: r.slug, desc: r.desc, priv: r.private});
  });
  localRepos.forEach(lr => {
    if(!seen.has(lr.slug)){
      options.push({slug: lr.slug, desc: '📁 Cloned locally', priv: false});
    }
  });
  if(!options.length){
    sel.innerHTML = '<option value="">No repos in list (click "➕ Custom repo…" to enter directly)</option>';
    onRepoPick();
    return;
  }
  sel.innerHTML = '<option value="">Select a repo…</option>' + options.map(r=>
    '<option value="'+esc(r.slug)+'">'+esc(r.slug)+(r.priv?'  🔒':'')
    + (r.desc ? '  –  '+esc(r.desc) : '')+'</option>').join('');
  const mine = (currentPath || cfg.code_ws_path || '').split('/').pop();
  const match = options.find(r=>mine && mine === r.slug.replace('/','__'));
  if(match) sel.value = match.slug;
  onRepoPick();
}
function localRepo(slug){
  return localRepos.find(r=>r.slug === slug) || null;
}
function onRepoPick(){
  const slug = document.getElementById('codeRepoSelect').value;
  const btn = document.getElementById('codeRepoFetch');
  if(btn) btn.disabled = !slug;
  const rm = document.getElementById('codeRepoRemove');
  const local = localRepo(slug);
  if(rm){
    rm.style.display = local ? '' : 'none';
    rm.title = local ? ('Delete ' + local.path + ' from server disk') : '';
  }
  const hint = document.getElementById('codeRepoHint');
  if(!hint) return;
  if(local){
    const bits = ['📁 cloned: ' + local.path, 'branch ' + (local.branch||'?')];
    if(local.dirty) bits.push(local.dirty + (local.dirty === 1
      ? ' uncommitted change' : ' uncommitted changes'));
    if(local.ahead) bits.push(local.ahead + (local.ahead === 1
      ? ' unpushed commit' : ' unpushed commits'));
    hint.textContent = bits.join(' · ');
  }else if(slug){
    hint.textContent = 'Not cloned yet — click "⬇ Clone & work here".';
  }else{
    const fetched = localRepos.length;
    hint.textContent = fetched
      ? ('Select a repo or click "➕ Custom repo…". ' + fetched + (fetched === 1
          ? ' repo is cloned on server.' : ' repos are cloned on server.'))
      : (repoDir ? ('Cloned into ' + repoDir) : '');
  }
}
async function promptCustomRepo(){
  const prov = repoProvider === 'gitlab' ? 'GitLab' : 'GitHub';
  const ex = repoProvider === 'gitlab'
    ? 'anderssjoeberg75/ollamastudio or http://gitlab.andrix.se/anderssjoeberg75/ollamastudio.git'
    : 'owner/repo or https://github.com/…';
  const val = prompt('Enter '+prov+' repo (slug or URL):\nExample: '+ex, '');
  if(!val || !val.trim()) return;
  await doFetchRepo(val.trim());
}
async function removeRepo(){
  const slug = document.getElementById('codeRepoSelect').value;
  const local = localRepo(slug);
  if(!local){ toast('Repo is not cloned', true); return; }

  const risk = [];
  if(local.dirty) risk.push(local.dirty + (local.dirty === 1
    ? ' uncommitted change' : ' uncommitted changes'));
  if(local.ahead) risk.push(local.ahead + (local.ahead === 1
    ? ' unpushed commit' : ' unpushed commits'));
  if(local.ahead === null) risk.push('branch "' + (local.branch||'?') + '" does not exist on origin '
    + '— all commits are unpushed');
  const warn = risk.length
    ? '\n\n⚠ YOU WILL LOSE:\n· ' + risk.join('\n· ') + '\nThis action cannot be undone.'
    : '\n\nAll work appears pushed to remote, so it can be re-cloned.';
  if(!confirm('Delete ' + slug + ' from server disk?\n\nFolder to remove:\n' + local.path
      + warn)) return;

  if(risk.length && !confirm('Final confirmation: ' + risk.join(' and ')
      + ' in ' + slug + ' will be permanently lost.\n\nDelete anyway?')) return;

  const rm = document.getElementById('codeRepoRemove');
  const hint = document.getElementById('codeRepoHint');
  rm.disabled = true;
  hint.textContent = 'Deleting ' + slug + '…';
  try{
    const r = await api('/api/github/remove', {method:'POST', headers:headers(true),
      body: JSON.stringify({repo: slug})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.message || d.error || ('HTTP '+r.status));
    localRepos = d.local || [];
    hint.textContent = '✓ ' + d.message;
    toast(d.message);
    try{ const cr = await fetch('/api/config', {headers: headers(false)}); if(cr.ok) cfg = await cr.json(); }catch(e){}
    updateCodeView(); onRepoPick();
    if(cfg.code_ws){ loadTree(); gitStatus(); }
    else { const t = document.getElementById('codeTree'); if(t) t.innerHTML = ''; gitStatus(); }
  }catch(e){
    hint.textContent = '✕ ' + e.message;
    toast('Could not delete: '+e.message, true);
  }finally{
    rm.disabled = false;
  }
}
async function fetchRepo(){
  const slug = document.getElementById('codeRepoSelect').value;
  if(!slug){ toast('Select a repo first', true); return; }
  await doFetchRepo(slug);
}
async function doFetchRepo(target){
  const btn = document.getElementById('codeRepoFetch');
  const hint = document.getElementById('codeRepoHint');
  if(btn) btn.disabled = true;
  hint.textContent = 'Cloning ' + target + '… (initial clone may take a moment)';
  analyzeNote('⬇ <b>Cloning ' + esc(target) + '…</b> repo will be analyzed upon completion.', 'warn');
  const endpoint = (repoProvider === 'gitlab') ? '/api/gitlab/fetch' : '/api/github/fetch';
  try{
    const r = await api(endpoint, {method:'POST', headers:headers(true),
      body: JSON.stringify({repo: target})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.message || d.error || ('HTTP '+r.status));
    hint.textContent = '✓ ' + d.message + ' · workspace: ' + d.path;
    toast('Now working with ' + target);
    await loadRepos(true);
    document.getElementById('codeRepoSelect').value = target;
    onRepoPick();
    try{ const cr = await fetch('/api/config', {headers: headers(false)}); if(cr.ok) cfg = await cr.json(); }catch(e){}
    updateCodeView(); loadTree(); gitStatus();
    analyzedPath = cfg.code_ws_path || '';
    if(d.analysis) analyzeNote('✓ <b>Repository analyzed.</b> ' + esc(d.analysis)
      + ' — Codex now knows project structure and functions.', 'ok');
    else analyzeWorkspace(true);
  }catch(e){
    hint.textContent = '✕ ' + e.message;
    toast('Could not clone repo: '+e.message, true);
  }finally{
    if(btn) btn.disabled = false;
  }
}

async function gitStatus(){
  const bar = document.getElementById('codeGit');
  try{
    const r = await api('/api/git/status', {headers: headers(false)});
    const g = await r.json(); lastGit = g;
    if(!g.repo){ bar.style.display='none'; return; }
    bar.style.display='flex';
    const slug = g.slug || ((g.owner && g.repo_name) ? (g.owner+'/'+g.repo_name) : 'git-remote');
    const provName = g.provider === 'gitlab' ? 'GitLab' : (g.provider === 'github' ? 'GitHub' : 'Git');
    document.getElementById('codeGitInfo').innerHTML =
      'Branch <b>'+esc(g.branch||'?')+'</b> · '+g.changed+' modified files · '+provName+': '+esc(slug)
      + (g.has_token ? '' : ' · <span style="color:var(--amber)">no token</span>');
    const prBtn = document.getElementById('codePrMrBtn');
    if(prBtn){
      prBtn.textContent = (g.provider === 'gitlab') ? 'Create MR' : 'Create PR';
    }
  }catch(e){ bar.style.display='none'; }
}
async function gitPost(path, body){
  const r = await api(path, {method:'POST', headers:headers(true), body: JSON.stringify(body||{})});
  return await r.json();
}
async function gitBranch(){
  const name = prompt('New branch name:', 'feature/update');
  if(!name) return;
  gitMsg('Creating branch…');
  const d = await gitPost('/api/git/branch', {name});
  if(d.status) lastGit=d.status, gitStatus();
  gitMsg(d.ok ? ('✓ Branch created: '+esc(name)) : ('✕ '+esc(d.message||d.error||'error')), !d.ok);
}
async function gitCommit(){
  const msg = prompt('Commit message:', 'Changes via AI assistant');
  if(!msg) return;
  gitMsg('Committing…');
  const d = await gitPost('/api/git/commit', {message: msg});
  gitStatus();
  gitMsg(d.ok ? '✓ Committed' : ('✕ '+esc(d.message||d.error||'error')), !d.ok);
}
async function gitPush(){
  const branch = lastGit && lastGit.branch;
  const prov = (lastGit && lastGit.provider === 'gitlab') ? 'GitLab' : ((lastGit && lastGit.provider === 'github') ? 'GitHub' : 'remote');
  if(!confirm('Push branch "'+(branch||'')+'" to '+prov+'?')) return;
  gitMsg('Pushing…');
  const d = await gitPost('/api/git/push', {});
  gitMsg(d.ok ? '✓ Pushed' : ('✕ '+esc(d.message||d.error||'error')), !d.ok);
}
async function createPrOrMr(){
  if(lastGit && lastGit.provider === 'gitlab'){
    await gitlabMR();
  } else {
    await githubPR();
  }
}
async function gitlabMR(){
  if(lastGit && lastGit.branch && !lastGit.has_token){
    gitMsg('✕ No GitLab token saved (⚙ Settings).', true); return;
  }
  const title = prompt('Merge Request title:', 'Changes via AI assistant');
  if(title===null) return;
  const body = prompt('MR description (optional):', '') || '';
  gitMsg('Creating Merge Request…');
  const d = await gitPost('/api/gitlab/mr', {title, body});
  if(d.ok && d.url){
    gitMsg('✓ MR created: <a href="'+esc(d.url)+'" target="_blank" rel="noopener">'+esc(d.url)+'</a>');
    toast('Merge Request created');
  }else{
    gitMsg('✕ '+esc(d.message||d.error||'could not create MR'), true);
  }
}
async function githubPR(){
  if(lastGit && lastGit.branch && !lastGit.has_token){
    gitMsg('✕ No GitHub token saved (⚙ Settings).', true); return;
  }
  const title = prompt('PR title:', 'Changes via AI assistant');
  if(title===null) return;
  const body = prompt('PR description (optional):', '') || '';
  gitMsg('Creating pull request…');
  const d = await gitPost('/api/github/pr', {title, body});
  if(d.ok && d.url){
    gitMsg('✓ PR created: <a href="'+esc(d.url)+'" target="_blank" rel="noopener">'+esc(d.url)+'</a>');
    toast('Pull request created');
  }else{
    gitMsg('✕ '+esc(d.message||d.error||'could not create PR'), true);
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
  list.innerHTML = '<div class="mem-empty">Loading…</div>';
  try{
    const r = await api('/api/memory', {headers: headers(false)});
    const d = await r.json();
    const mems = d.memories || [];
    if(cnt) cnt.textContent = mems.length ? '('+mems.length+')' : '';
    if(!mems.length){ list.innerHTML = '<div class="mem-empty">No saved memories yet.</div>'; return; }
    list.innerHTML = mems.map(m=>
      '<div class="mem-item"><span>'+esc(m.text)+'</span>'
      + (m.id ? '<button title="Delete" onclick="deleteMemory(\''+esc(String(m.id)).replace(/\\/g,"\\\\").replace(/'/g,"\\'")+'\')">✕</button>' : '')
      + '</div>').join('');
  }catch(e){ list.innerHTML = '<div class="mem-empty">Could not load memory.</div>'; }
}
async function addMemory(){
  const inp = document.getElementById('memAddInput');
  const t = (inp.value||'').trim();
  if(!t){ return; }
  inp.value='';
  await memWrite(t, '');   // spara som ett användarpåstående
  toast('Saved to memory');
  setTimeout(loadMemories, 600);   // Mem0 kan extrahera med viss fördröjning
}
async function deleteMemory(id){
  try{
    const r = await api('/api/memory/delete', {method:'POST', headers:headers(true),
      body: JSON.stringify({id})});
    const d = await r.json().catch(()=>({}));
    if(!d.ok){ toast('Could not delete'+(d.error?': '+d.error:''), true); }
    loadMemories();   // ladda om oavsett så listan speglar faktiskt läge
  }catch(e){ toast('Could not delete', true); }
}
async function clearMemories(){
  if(!confirm('Clear ALL saved memories for this user?')) return;
  try{
    const r = await api('/api/memory/delete', {method:'POST', headers:headers(true),
      body: JSON.stringify({all:true})});   // uttryckligt val – aldrig via tomt id
    const d = await r.json().catch(()=>({}));
    if(d.ok) toast('Memories cleared');
    else toast('Could not clear memories'+(d.error?': '+d.error:''), true);
    loadMemories();
  }catch(e){ toast('Could not clear memories', true); }
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
    if(!r.ok){ document.getElementById('systemBody').innerHTML='<div class="sys-warn">Could not load system info.</div>'; return; }
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
        + '<div class="sub">'+(cpu.cores?cpu.cores+' cores':'')
        + (cpu.load?' · load '+cpu.load.map(x=>x.toFixed(2)).join(' / '):'')+'</div></div>';
  const mfrac = (mem.total&&mem.used!=null)?mem.used/mem.total:0;
  html += '<div class="metric"><div class="h"><span class="name">Memory (RAM)</span>'
        + '<span class="val">'+(mem.total?humanSize(mem.used)+' / '+humanSize(mem.total):'–')+'</span></div>'
        + pctBar(mfrac)
        + '<div class="sub">'+(mem.total?(mfrac*100).toFixed(0)+'% used':'')+'</div></div>';
  html += '</div>';

  html += '<div class="section-title" style="margin-top:14px">Graphics Card (GPU)</div>';
  if(s.gpu_error) html += '<div class="sysnote">'+esc(s.gpu_error)+'</div>';
  if(!s.gpus || !s.gpus.length){
    if(!s.gpu_error) html += '<div class="sysnote">No GPUs reported.</div>';
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
             + ' title="'+(busy ? 'Unload model to free VRAM'
                                : 'No model currently loaded on this GPU')+'">'
             + '⏏ Unload</button>';
      title += '</div>';
      const hd = (t,v)=>'<div style="display:flex;justify-content:space-between;font-size:12px;color:var(--subtle);margin-bottom:6px"><span>'+t+'</span><span>'+v+'</span></div>';
      const metrics = '<div class="gpu-metrics">'
        + '<div>'+hd('Utilization', g.util!=null?g.util+'%':'–')+pctBar(utilFrac)+'</div>'
        + '<div>'+hd('VRAM', g.mem_total_mb?mbSize(g.mem_used_mb)+' / '+mbSize(g.mem_total_mb):'–')+pctBar(memFrac, g.mem_total_mb&&memFrac>0.9?'var(--danger)':'var(--accent)')+'</div>'
        + '</div>';
      let stats = '<div class="gpu-stats">';
      if(g.temp!=null) stats += '<span>Temp: '+g.temp+' °C</span>';
      if(g.power!=null) stats += '<span>Power: '+g.power.toFixed(0)+(g.power_limit?' / '+g.power_limit.toFixed(0):'')+' W</span>';
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
        procs = '<div class="gpu-procs"><div class="row">No processes using this GPU right now.</div></div>';
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
    '\n\nNOTE: Ollama runs as ONE instance across all cards here, so all loaded '
    + 'models will be unloaded — not only the one on GPU '+index+'.';
  if(!confirm('Unload model on GPU '+index+(name?' ('+name+')':'')+'?\n\n'
      + 'VRAM will be freed. The next query will reload the model, which may take '
      + 'a few seconds.' + warn)) return;
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '⏳ Unloading…';
  try{
    const r = await api('/api/gpu/unload', {method:'POST', headers:headers(true),
      body: JSON.stringify({index})});
    const d = await r.json();
    if(!d.ok) throw new Error((d.failed&&d.failed[0]&&d.failed[0].error) || d.error || 'could not unload');
    if(d.unloaded.length){
      toast('Unloaded ' + d.unloaded.join(', ')
            + (d.freed_bytes ? ' · ' + humanSize(d.freed_bytes) + ' freed' : ''));
    } else {
      // Inget laddades ur. Servern säger varför – visa DET, inte ett tomt besked.
      alert(d.message || ('No model was loaded on GPU ' + index));
      // Ligger modellen i en annan instans? Erbjud att tömma den i stället.
      const other = (d.elsewhere||[])[0];
      if(other && confirm('Unload ' + other.model + ' in ' + other.backend + ' instead?\n\n'
          + 'This frees VRAM, but applies to that entire instance.')){
        const r2 = await api('/api/gpu/unload', {method:'POST', headers:headers(true),
          body: JSON.stringify({backend: other.backend})});
        const d2 = await r2.json();
        if(!d2.ok) throw new Error(d2.error || 'could not unload');
        toast('Unloaded ' + d2.unloaded.join(', ')
              + (d2.freed_bytes ? ' · ' + humanSize(d2.freed_bytes) + ' freed' : ''));
      }
    }
    refresh();                                   // visa det tomma kortet direkt
  }catch(e){
    toast('Could not unload: '+e.message, true);
    btn.disabled = false; btn.textContent = old;
  }
}

/* ---- Schemalagda granskningar (GitLab) ---- */
let schPollTimer = null;

function escSch(s){
  return (s || '').toString().replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function openScheduleModal(){
  const ov = document.getElementById('scheduleOverlay');
  if(ov) ov.style.display = 'flex';
  populateScheduleProjects();
  loadScheduleJobs();
}

function closeScheduleModal(){
  const ov = document.getElementById('scheduleOverlay');
  if(ov) ov.style.display = 'none';
  if(schPollTimer){
    clearInterval(schPollTimer);
    schPollTimer = null;
  }
}

async function populateScheduleProjects(){
  const sel = document.getElementById('schProjectSelect');
  if(!sel) return;
  sel.innerHTML = '<option value="">Loading projects from GitLab…</option>';
  try{
    const r = await api('/api/gitlab/repos');
    const d = await r.json();
    const repos = d.repos || [];
    if(!repos.length){
      sel.innerHTML = '<option value="">No GitLab projects found (verify GitLab token under ⚙ Settings)</option>';
      return;
    }
    let html = '<option value="">-- Select GitLab project --</option>';
    for(const repo of repos){
      const slug = repo.slug || repo.name;
      html += `<option value="${escSch(slug)}">${escSch(slug)}</option>`;
    }
    sel.innerHTML = html;
    sel.onchange = ()=>{
      const p = sel.value;
      const pt = currentProjectTargets[p] || {};
      const srvEl = document.getElementById('schProdServer');
      const tokEl = document.getElementById('schProdToken');
      const secModeEl = document.getElementById('schSecMode');
      if(srvEl) srvEl.value = pt.prod_server || '';
      if(tokEl) tokEl.value = '';
      if(secModeEl) secModeEl.value = pt.sec_mode || 'safe';
      if(window._lastScheduleJobs){
        const existing = window._lastScheduleJobs.find(j => j.project === p);
        if(existing){
          const intEl = document.getElementById('schInterval');
          const timeEl = document.getElementById('schTime');
          const secEl = document.getElementById('schRunSecurity');
          const penEl = document.getElementById('schRunPentest');
          const pnyEl = document.getElementById('schRunPonytail');
          if(intEl && existing.interval_hours) intEl.value = existing.interval_hours;
          if(timeEl && existing.run_time) timeEl.value = existing.run_time;
          if(secEl) secEl.checked = !!existing.run_security;
          if(penEl) penEl.checked = existing.run_pentest !== undefined ? !!existing.run_pentest : true;
          if(pnyEl) pnyEl.checked = !!existing.run_ponytail;
          if(secModeEl && existing.sec_mode) secModeEl.value = existing.sec_mode;
        }
      }
    };
  }catch(e){
    sel.innerHTML = '<option value="">Could not load GitLab projects: ' + escSch(e.message) + '</option>';
  }
}

async function loadScheduleJobs(){
  const container = document.getElementById('schJobsList');
  if(!container) return;
  if(document.querySelector('[id^="schEditDt_"]')){
    return; // Användaren redigerar en tid just nu – undvik att skriva över inputfältet
  }
  if(document.activeElement && document.activeElement.classList && document.activeElement.classList.contains('sch-mode-select')){
    return; // Användaren har lägesmenyn öppen
  }
  try{
    const r = await api('/api/schedule/jobs');
    const d = await r.json();
    const jobs = d.jobs || [];
    window._lastScheduleJobs = jobs;
    if(!jobs.length){
      container.innerHTML = '<div style="color:var(--faint);font-size:13px;padding:12px;text-align:center;background:var(--bg);border-radius:8px">No scheduled audits yet. Select a project above and save to schedule.</div>';
      return;
    }
    let hasRunning = false;
    let html = '';
    for(const job of jobs){
      if(job.running || (job.last_status && (job.last_status.includes('Körs nu') || job.last_status.includes('Running')))) {
        hasRunning = true;
      }
      // Säkerhet toggle-knapp
      const secOn = !!job.run_security;
      const secStyle = secOn
        ? 'background:rgba(124,92,255,0.12);border:1px solid var(--accent);color:var(--accent-hov);font-weight:600'
        : 'background:transparent;border:1px dashed var(--border);color:var(--faint);font-weight:500;text-decoration:line-through;opacity:0.45';
      const secBadge = `<button type="button" class="sch-badge-btn" onclick="toggleScheduleFeature('${escSch(job.id)}', 'run_security', ${!secOn})" title="${secOn ? 'Security audit active — click to disable' : 'Security audit inactive — click to enable'}" style="${secStyle}"><span>🛡</span><span>Security</span></button>`;

      // Pentest toggle-knapp
      const penOn = job.run_pentest !== undefined ? !!job.run_pentest : true;
      const penStyle = penOn
        ? 'background:rgba(255,180,84,0.12);border:1px solid var(--amber);color:var(--amber);font-weight:600'
        : 'background:transparent;border:1px dashed var(--border);color:var(--faint);font-weight:500;text-decoration:line-through;opacity:0.45';
      const penBadge = `<button type="button" class="sch-badge-btn" onclick="toggleScheduleFeature('${escSch(job.id)}', 'run_pentest', ${!penOn})" title="${penOn ? 'Penetration test active — click to disable' : 'Penetration test inactive — click to enable'}" style="${penStyle}"><span>🎯</span><span>Pentest</span></button>`;

      // Ponytail toggle-knapp
      const pnyOn = !!job.run_ponytail;
      const pnyStyle = pnyOn
        ? 'background:rgba(57,214,127,0.12);border:1px solid var(--green);color:var(--green);font-weight:600'
        : 'background:transparent;border:1px dashed var(--border);color:var(--faint);font-weight:500;text-decoration:line-through;opacity:0.45';
      const pnyBadge = `<button type="button" class="sch-badge-btn" onclick="toggleScheduleFeature('${escSch(job.id)}', 'run_ponytail', ${!pnyOn})" title="${pnyOn ? 'Ponytail audit active — click to disable' : 'Ponytail audit inactive — click to enable'}" style="${pnyStyle}"><span>✂</span><span>Ponytail</span></button>`;

      // Säkerhetsläge / Testläge väljare
      let modeBorder = 'var(--accent)';
      let modeColor = 'var(--accent-hov)';
      let modeBg = 'rgba(124,92,255,0.12)';
      if(job.sec_mode === 'full'){
        modeBorder = 'var(--danger)';
        modeColor = 'var(--danger)';
        modeBg = 'rgba(255,92,108,0.12)';
      } else if(job.sec_mode === 'passive'){
        modeBorder = 'var(--subtle)';
        modeColor = 'var(--subtle)';
        modeBg = 'rgba(154,163,181,0.12)';
      }
      const secModeBadge = `
        <span style="position:relative;display:inline-flex;align-items:center">
          <select class="sch-mode-select" onchange="updateScheduleSecMode('${escSch(job.id)}', this.value)"
                  title="Security mode: click to select level"
                  style="background:${modeBg};border:1px solid ${modeBorder};color:${modeColor};font-weight:600;outline:none;appearance:none;-webkit-appearance:none">
            <option value="safe" ${job.sec_mode !== 'full' && job.sec_mode !== 'passive' ? 'selected' : ''}>🛡️ Safe mode</option>
            <option value="full" ${job.sec_mode === 'full' ? 'selected' : ''}>⚠️ Full pentest</option>
            <option value="passive" ${job.sec_mode === 'passive' ? 'selected' : ''}>🔍 Passive analysis</option>
          </select>
          <span style="position:absolute;right:6px;pointer-events:none;font-size:9px;color:${modeColor}">▾</span>
        </span>
      `;

      let prodBadge = '';
      if(job.prod_server){
        const cleanServer = job.prod_server.replace(/^https?:\/\//i, '').replace(/\/+$/, '');
        prodBadge = `<span class="sch-prod-badge" style="background:var(--card);border:1px solid var(--border);color:var(--subtle)" title="Production server: ${escSch(job.prod_server)} (click to open)" onclick="window.open('${escSch(job.prod_server)}', '_blank')"><span>🌐</span><span class="sch-prod-text">${escSch(cleanServer)}</span></span>`;
      }
      const statusColor = job.running ? 'var(--amber)' : (job.last_status && (job.last_status.startsWith('Fel') || job.last_status.startsWith('Error')) ? 'var(--danger)' : 'var(--subtle)');
      const statusIcon = job.running ? '⏳ ' : '';
      
      const nextRunStr = job.next_run ? new Date(job.next_run * 1000).toLocaleString('en-US', {dateStyle:'short', timeStyle:'short'}) : '-';
      const lastRunStr = job.last_run ? new Date(job.last_run * 1000).toLocaleString('en-US', {dateStyle:'short', timeStyle:'short'}) : 'Never';

      html += `
        <div style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px;display:flex;flex-direction:column;gap:6px;overflow:hidden">
          <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;min-width:0;max-width:100%">
              <span style="font-weight:600;font-size:14px;color:var(--text);white-space:nowrap">🦊 ${escSch(job.project)}</span>
              ${secBadge} ${penBadge} ${pnyBadge} ${secModeBadge} ${prodBadge}
            </div>
            <div style="display:flex;align-items:center;gap:6px;flex-shrink:0">
              <button class="btn accent small" onclick="runScheduleJobNow('${escSch(job.id)}')" ${job.running ? 'disabled' : ''} title="Run audit now">
                ${job.running ? '⏳ Running now…' : '▶ Run now'}
              </button>
              <button class="btn ghost small" onclick="toggleScheduleJob('${escSch(job.id)}', ${!job.enabled})" title="${job.enabled ? 'Pause schedule' : 'Enable schedule'}">
                ${job.enabled ? '⏸ Pause' : '▶ Enable'}
              </button>
              <button class="btn ghost small" onclick="deleteScheduleJob('${escSch(job.id)}')" title="Delete this schedule" style="color:var(--danger)">
                🗑
              </button>
            </div>
          </div>
          <div style="display:flex;gap:14px;font-size:12px;color:var(--faint);flex-wrap:wrap;align-items:center">
            <span>⏱ Interval: <b>every ${job.interval_hours}h</b></span>
            ${job.run_time ? `<span>⏰ Time: <b>${escSch(job.run_time)}</b></span>` : ''}
            <span id="schNextRunWrapper_${escSch(job.id)}" style="display:inline-flex;align-items:center;gap:4px">
              <span class="sch-cal-btn" onclick="openEditNextRun('${escSch(job.id)}', ${job.next_run})"
                    title="Click to edit next run time"
                    style="cursor:pointer;padding:1px 4px;border-radius:4px;border:1px solid var(--border);background:var(--card);font-size:12px;display:inline-flex;align-items:center;line-height:1;transition:all .15s"
                    onmouseover="this.style.borderColor='var(--accent)';this.style.transform='scale(1.1)'"
                    onmouseout="this.style.borderColor='var(--border)';this.style.transform='none'">📅</span>
              <span>Next run: <b style="cursor:pointer;text-decoration:underline dashed var(--border)" onclick="openEditNextRun('${escSch(job.id)}', ${job.next_run})" title="Click calendar icon or time to edit">${nextRunStr}</b></span>
            </span>
            <span>🕒 Last run: <b>${lastRunStr}</b></span>
            <span>Status: <b style="color:${job.enabled ? 'var(--green)' : 'var(--faint)'}">${job.enabled ? 'Active' : 'Paused'}</b></span>
          </div>
          ${job.last_status ? `
            <div style="font-size:12px;color:${statusColor};background:var(--card);padding:6px 10px;border-radius:6px;border:1px solid var(--border);word-break:break-word">
              ${statusIcon}${escSch(job.last_status)}
            </div>
          ` : ''}
        </div>
      `;
    }
    container.innerHTML = html;

    if(hasRunning && !schPollTimer){
      schPollTimer = setInterval(loadScheduleJobs, 3000);
    } else if(!hasRunning && schPollTimer){
      clearInterval(schPollTimer);
      schPollTimer = null;
    }
  }catch(e){
    container.innerHTML = '<div style="color:var(--danger);font-size:13px">Could not load schedules: ' + escSch(e.message) + '</div>';
  }
}

function toLocalDatetimeInput(ts){
  const d = ts ? new Date(ts * 1000) : new Date();
  const pad = n => String(n).padStart(2, '0');
  const year = d.getFullYear();
  const month = pad(d.getMonth() + 1);
  const day = pad(d.getDate());
  const hours = pad(d.getHours());
  const minutes = pad(d.getMinutes());
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

function openEditNextRun(jobId, currentNextRun){
  const wrapper = document.getElementById('schNextRunWrapper_' + jobId);
  if(!wrapper) return;
  const isoVal = toLocalDatetimeInput(currentNextRun);
  wrapper.innerHTML = `
    <span style="display:inline-flex;align-items:center;gap:4px;background:var(--card);padding:2px 6px;border-radius:6px;border:1px solid var(--accent)">
      <span>📅</span>
      <input type="datetime-local" id="schEditDt_${jobId}" value="${isoVal}"
             style="background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);padding:2px 6px;font-size:12px;box-sizing:border-box"
             onkeydown="if(event.key==='Enter') saveEditedNextRun('${jobId}'); else if(event.key==='Escape') loadScheduleJobs();">
      <button class="btn accent small" style="padding:2px 8px;font-size:11px" onclick="saveEditedNextRun('${jobId}')" title="Save new time">Save</button>
      <button class="btn ghost small" style="padding:2px 6px;font-size:11px" onclick="loadScheduleJobs()" title="Cancel">✕</button>
    </span>
  `;
  const inp = document.getElementById('schEditDt_' + jobId);
  if(inp){
    inp.focus();
    if(typeof inp.showPicker === 'function'){
      try{ inp.showPicker(); }catch(_){}
    }
  }
}

async function saveEditedNextRun(jobId){
  const inp = document.getElementById('schEditDt_' + jobId);
  if(!inp || !inp.value){
    toast('Enter a valid date and time', true);
    return;
  }
  const dt = new Date(inp.value);
  if(isNaN(dt.getTime())){
    toast('Invalid date or time', true);
    return;
  }
  const nextRunTs = Math.floor(dt.getTime() / 1000);
  const pad = n => String(n).padStart(2, '0');
  const runTimeStr = `${pad(dt.getHours())}:${pad(dt.getMinutes())}`;

  try{
    const r = await api('/api/schedule/jobs', {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify({
        id: jobId,
        next_run: nextRunTs,
        run_time: runTimeStr
      })
    });
    const d = await r.json();
    if(!d.ok) throw new Error(d.error || 'could not save');
    toast('Next run updated to ' + dt.toLocaleString('en-US', {dateStyle:'short', timeStyle:'short'}));
    loadScheduleJobs();
  }catch(e){
    toast('Could not update time: ' + e.message, true);
  }
}

async function addScheduleJob(){
  const sel = document.getElementById('schProjectSelect');
  const project = (sel ? sel.value : '').trim();
  if(!project){
    toast('Select a GitLab project first', true);
    return;
  }
  const interval = parseInt(document.getElementById('schInterval').value, 10) || 24;
  const runTime = (document.getElementById('schTime') ? document.getElementById('schTime').value : '').trim();
  const runSecurity = document.getElementById('schRunSecurity') ? document.getElementById('schRunSecurity').checked : true;
  const runPentest = document.getElementById('schRunPentest') ? document.getElementById('schRunPentest').checked : true;
  const runPonytail = document.getElementById('schRunPonytail') ? document.getElementById('schRunPonytail').checked : true;
  const prodServer = (document.getElementById('schProdServer') ? document.getElementById('schProdServer').value : '').trim();
  const prodToken = (document.getElementById('schProdToken') ? document.getElementById('schProdToken').value : '').trim();
  const secMode = (document.getElementById('schSecMode') ? document.getElementById('schSecMode').value : 'safe').trim() || 'safe';

  if(!runSecurity && !runPentest && !runPonytail){
    toast('Select at least one audit type (security, pentest, or ponytail)', true);
    return;
  }

  try{
    const r = await api('/api/schedule/jobs', {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify({
        project: project,
        interval_hours: interval,
        run_time: runTime,
        run_security: runSecurity,
        run_pentest: runPentest,
        run_ponytail: runPonytail,
        prod_server: prodServer,
        prod_token: prodToken,
        sec_mode: secMode,
        enabled: true
      })
    });
    const d = await r.json();
    if(!d.ok) throw new Error(d.error || 'could not save schedule');
    toast('Schedule saved for ' + project);
    if(prodServer || secMode){
      currentProjectTargets[project] = {project, prod_server: prodServer, has_token: !!prodToken, sec_mode: secMode};
      renderProjectTargets(currentProjectTargets);
    }
    loadScheduleJobs();
  }catch(e){
    toast('Could not save schedule: ' + e.message, true);
  }
}

async function runScheduleJobNow(id){
  try{
    const r = await api('/api/schedule/run', {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify({id})
    });
    const d = await r.json();
    if(!d.ok) throw new Error(d.error || 'could not start job');
    toast('Audit started in background');
    loadScheduleJobs();
  }catch(e){
    toast('Could not start audit: ' + e.message, true);
  }
}

async function toggleScheduleJob(id, enabled){
  try{
    const r = await api('/api/schedule/jobs', {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify({id, enabled})
    });
    const d = await r.json();
    if(!d.ok) throw new Error(d.error || 'could not update');
    toast(enabled ? 'Schedule enabled' : 'Schedule paused');
    loadScheduleJobs();
  }catch(e){
    toast('Could not update: ' + e.message, true);
  }
}

async function toggleScheduleFeature(id, featureKey, newVal){
  const jobs = window._lastScheduleJobs || [];
  const job = jobs.find(j => j.id === id);
  if(job && !newVal){
    const willSec = featureKey === 'run_security' ? false : !!job.run_security;
    const willPen = featureKey === 'run_pentest' ? false : (job.run_pentest !== undefined ? !!job.run_pentest : true);
    const willPny = featureKey === 'run_ponytail' ? false : !!job.run_ponytail;
    if(!willSec && !willPen && !willPny){
      toast('At least one audit type (security, pentest, or ponytail) must be active', true);
      return;
    }
  }

  const payload = { id: id };
  payload[featureKey] = !!newVal;

  try{
    const r = await api('/api/schedule/jobs', {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if(!d.ok) throw new Error(d.error || 'could not update');
    const labelMap = {
      run_security: 'Security audit',
      run_pentest: 'Penetration test',
      run_ponytail: 'Ponytail audit'
    };
    const name = labelMap[featureKey] || featureKey;
    toast(name + (newVal ? ' enabled' : ' disabled'));
    loadScheduleJobs();
  }catch(e){
    toast('Could not update: ' + e.message, true);
  }
}

async function updateScheduleSecMode(id, newMode){
  try{
    const r = await api('/api/schedule/jobs', {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify({ id: id, sec_mode: newMode })
    });
    const d = await r.json();
    if(!d.ok) throw new Error(d.error || 'could not update');
    const modeNames = {
      safe: 'Safe mode (non-destructive)',
      full: 'Full pentest (active)',
      passive: 'Passive analysis'
    };
    toast('Security mode changed to ' + (modeNames[newMode] || newMode));
    loadScheduleJobs();
  }catch(e){
    toast('Could not change security mode: ' + e.message, true);
  }
}

async function deleteScheduleJob(id){
  if(!confirm('Delete this schedule?')) return;
  try{
    const r = await api('/api/schedule/delete', {
      method: 'POST',
      headers: headers(true),
      body: JSON.stringify({id})
    });
    const d = await r.json();
    if(!d.ok) throw new Error(d.error || 'could not delete');
    toast('Schedule deleted');
    loadScheduleJobs();
  }catch(e){
    toast('Could not delete schedule: ' + e.message, true);
  }
}

loadConfig();
refresh();
loadPrefs();   // hämta sparade UI-val (modell, GPU, chattinställningar) från databasen
loadCodeMsgs();   // återställ Codex-konversationen vid omladdning
restoreCodeLog();
