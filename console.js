/* Console Alex — Administration (renderer)
   Tout est défensif : le cerveau/fleet peut être absent, incomplet ou non configuré. */

const config = require('./config.js');

const API = 'http://127.0.0.1:8765';
const POLL_MS = 10000;

/* Accent via la config Alex (themeColor), sinon violet par défaut */
try{
  const accent = (config.read().themeColor) || '#7c6cf0';
  document.documentElement.style.setProperty('--accent', accent);
}catch(e){}

/* ------------------------- utilitaires ------------------------- */

function escapeHtml(s){
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function relTime(iso){
  if(!iso) return '—';
  const t = new Date(iso).getTime();
  if(isNaN(t)) return '—';
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if(s < 60) return 'il y a ' + s + ' s';
  const m = Math.round(s / 60);
  if(m < 60) return 'il y a ' + m + ' min';
  const h = Math.round(m / 60);
  if(h < 24) return 'il y a ' + h + ' h';
  const j = Math.round(h / 24);
  return 'il y a ' + j + ' j';
}

function clockTime(iso){
  if(!iso) return '—';
  const d = new Date(iso);
  if(isNaN(d.getTime())) return '—';
  return String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
}

async function apiGet(path){
  const r = await fetch(API + path);
  let data = null;
  try{ data = await r.json(); }catch(e){}
  if(!r.ok){ const err = new Error('HTTP ' + r.status); err.status = r.status; throw err; }
  return data;
}

async function apiPost(path, body){
  const r = await fetch(API + path, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body || {})
  });
  let data = null;
  try{ data = await r.json(); }catch(e){}
  if(!r.ok){ const err = new Error('HTTP ' + r.status); err.status = r.status; err.data = data; throw err; }
  return data || {};
}

function toast(msg, kind){
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = 'toast' + (kind ? ' ' + kind : '');
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(()=>{ try{ el.remove(); }catch(e){} }, 3500);
}

/* ------------------------- état global ------------------------- */

let fleetState = null;     // dernier /fleet/state valide
let brainReachable = false;
let alertsSince = new Date(Date.now() - 3600 * 1000).toISOString(); // -1 h au chargement
const alertsById = new Map();
let currentAuditDevice = '';

/* ------------------------- badge + panneau non configuré ------------------------- */

function setBrainChip(ok){
  brainReachable = ok;
  document.getElementById('brainChip').hidden = ok;
}

function renderBadge(){
  const el = document.getElementById('roleBadge');
  el.className = 'badge';
  if(!fleetState){
    el.textContent = 'État inconnu';
    el.classList.add('role-none');
    return;
  }
  if(fleetState.configured === false){
    el.textContent = 'Non configurée';
    el.classList.add('role-none');
    return;
  }
  if(fleetState.role === 'mother'){
    el.textContent = 'Rôle : mère';
    el.classList.add('role-mother');
    return;
  }
  if(fleetState.role === 'daughter'){
    if(fleetState.linked && fleetState.mother_username){
      el.textContent = 'Rôle : fille — liée à ' + fleetState.mother_username;
    } else {
      el.textContent = 'Rôle : fille — non liée';
    }
    el.classList.add('role-daughter');
    return;
  }
  el.textContent = 'Rôle inconnu';
}

function showNotConfigured(show){
  document.getElementById('notConfigured').hidden = !show;
  document.getElementById('cards').hidden = show;
}

async function refreshState(){
  try{
    const s = await apiGet('/fleet/state');
    fleetState = (s && typeof s === 'object') ? s : null;
    setBrainChip(true);
  }catch(e){
    fleetState = null;
    setBrainChip(false);
  }
  renderBadge();
  showNotConfigured(!!(fleetState && fleetState.configured === false));
}

/* ------------------------- appareils liés ------------------------- */

const STATUS_LABEL = { online:'En ligne', stopped:'Suspendue', offline:'Hors ligne' };

async function refreshDevices(){
  const body = document.getElementById('devicesBody');
  const count = document.getElementById('devicesCount');
  let list;
  try{
    list = await apiGet('/fleet/devices');
  }catch(e){
    body.innerHTML = '<tr><td colspan="7" class="empty">Appareils injoignables — le cerveau ne répond pas.</td></tr>';
    count.textContent = '';
    return;
  }
  if(!Array.isArray(list) || list.length === 0){
    body.innerHTML = '<tr><td colspan="7" class="empty">Aucun appareil lié.</td></tr>';
    count.textContent = '';
    return;
  }
  count.textContent = list.length + (list.length > 1 ? ' appareils' : ' appareil');
  body.innerHTML = list.map(d=>{
    const status = STATUS_LABEL[d.status] || d.status || 'inconnu';
    const cls = (d.status === 'online' || d.status === 'stopped') ? d.status : 'offline';
    const stopped = d.status === 'stopped';
    return '<tr data-id="' + escapeHtml(d.id) + '" data-user="' + escapeHtml(d.username) + '">' +
      '<td>' + escapeHtml(d.username || '—') + '</td>' +
      '<td>' + escapeHtml(d.device_name || '—') + '</td>' +
      '<td><span class="pill ' + cls + '">' + escapeHtml(status) + '</span></td>' +
      '<td class="dim">' + escapeHtml(relTime(d.last_seen)) + '</td>' +
      '<td class="dim">' + escapeHtml(d.version || '—') + '</td>' +
      '<td class="dim">' + escapeHtml(d.workspace || '—') + '</td>' +
      '<td><div class="actions">' +
        (stopped
          ? '<button class="btn act-start" title="Réactiver">▶ Réactiver</button>'
          : '<button class="btn act-stop" title="Suspendre">⏸ Suspendre</button>') +
        '<button class="btn btn-danger act-revoke" title="Révoquer">⊘ Révoquer</button>' +
      '</div></td>' +
    '</tr>';
  }).join('');
}

async function deviceCommand(id, type){
  try{
    const res = await apiPost('/fleet/devices/' + encodeURIComponent(id) + '/command', { type });
    const seq = (res && typeof res.seq === 'number') ? ' (séquence ' + res.seq + ')' : '';
    toast(type === 'stop'
      ? 'Suspension appliquée' + seq
      : 'Réactivation appliquée' + seq, 'ok');
  }catch(e){
    toast("La commande n'a pas abouti.", 'err');
  }
  refreshDevices();
}

async function revokeDevice(id){
  try{
    await apiPost('/fleet/devices/' + encodeURIComponent(id) + '/revoke', {});
    toast('Lien révoqué.', 'ok');
  }catch(e){
    toast('La révocation a échoué.', 'err');
  }
  refreshDevices();
  refreshAlerts(true);
}

document.getElementById('devicesBody').addEventListener('click', (e)=>{
  const btn = e.target.closest('button');
  if(!btn) return;
  const row = btn.closest('tr');
  if(!row) return;
  const id = row.dataset.id;
  const username = row.dataset.user || 'cet appareil';
  if(!id) return;
  if(btn.classList.contains('act-stop')){
    if(confirm('Suspendre Alex de ' + username + ' ? Elle affichera un écran verrouillé.')){
      deviceCommand(id, 'stop');
    }
  } else if(btn.classList.contains('act-start')){
    deviceCommand(id, 'start');
  } else if(btn.classList.contains('act-revoke')){
    if(!confirm('Révoquer le lien de ' + username + ' ?')) return;
    if(!confirm('Attention : le lien sera définitivement rompu. Confirmer la révocation de ' + username + ' ?')) return;
    revokeDevice(id);
  }
});

/* ------------------------- en attente de liaison ------------------------- */

async function refreshPending(){
  const box = document.getElementById('pendingList');
  const count = document.getElementById('pendingCount');
  let list;
  try{
    list = await apiGet('/fleet/pending');
  }catch(e){
    box.innerHTML = '<div class="empty">Demandes injoignables — le cerveau ne répond pas.</div>';
    count.textContent = '';
    return;
  }
  if(!Array.isArray(list) || list.length === 0){
    box.innerHTML = '<div class="empty">Aucune demande en attente.</div>';
    count.textContent = '';
    return;
  }
  count.textContent = list.length + (list.length > 1 ? ' demandes' : ' demande');
  box.innerHTML = list.map(p=>{
    return '<div class="pending-row" data-id="' + escapeHtml(p.pairing_id) + '" data-user="' + escapeHtml(p.username) + '">' +
      '<div class="p-main">' +
        '<div class="p-user">' + escapeHtml(p.username || '—') + '</div>' +
        '<div class="p-meta">' + escapeHtml(p.device_name || 'Appareil') + ' · ' + escapeHtml(clockTime(p.created_at)) + '</div>' +
      '</div>' +
      '<input type="text" inputmode="numeric" maxlength="8" placeholder="00000000" class="p-code">' +
      '<button class="btn btn-accent p-link">Lier</button>' +
      '<div class="p-error" hidden></div>' +
    '</div>';
  }).join('');
}

document.getElementById('pendingList').addEventListener('click', async (e)=>{
  const btn = e.target.closest('.p-link');
  if(!btn) return;
  const row = btn.closest('.pending-row');
  const input = row.querySelector('.p-code');
  const errEl = row.querySelector('.p-error');
  const pairingId = row.dataset.id;
  const username = row.dataset.user || '';
  const code = (input.value || '').trim();
  errEl.hidden = true;
  errEl.textContent = '';
  if(!/^\d{8}$/.test(code)){
    errEl.textContent = 'Saisis un code à 8 chiffres.';
    errEl.hidden = false;
    return;
  }
  btn.disabled = true;
  try{
    const res = await apiPost('/fleet/approve', { pairing_id: pairingId, code });
    if(res && res.ok){
      toast((res.username || username) + ' liée', 'ok');
      refreshPending();
      refreshDevices();
    } else {
      errEl.textContent = 'Code incorrect';
      errEl.hidden = false;
    }
  }catch(err){
    if(err && err.status === 403){
      errEl.textContent = 'Code incorrect';
      errEl.hidden = false;
    } else {
      errEl.textContent = "Le cerveau n'a pas répondu — réessaie.";
      errEl.hidden = false;
    }
  }
  btn.disabled = false;
});

/* ------------------------- alertes ------------------------- */

async function refreshAlerts(force){
  const box = document.getElementById('alertsList');
  const count = document.getElementById('alertsCount');
  let list;
  try{
    list = await apiGet('/fleet/alerts?since=' + encodeURIComponent(alertsSince));
  }catch(e){
    if(force || alertsById.size === 0){
      box.innerHTML = '<div class="empty">Alertes injoignables — le cerveau ne répond pas.</div>';
    }
    count.textContent = '';
    return;
  }
  if(!Array.isArray(list)) list = [];
  let newest = new Date(alertsSince).getTime();
  for(const a of list){
    if(a && a.id != null) alertsById.set(a.id, a);
    const t = a && a.created_at ? new Date(a.created_at).getTime() : NaN;
    if(!isNaN(t) && t > newest) newest = t;
  }
  if(newest > new Date(alertsSince).getTime()) alertsSince = new Date(newest).toISOString();
  const all = Array.from(alertsById.values())
    .sort((x,y)=> new Date(y.created_at || 0) - new Date(x.created_at || 0))
    .slice(0, 100);
  count.textContent = all.length ? String(all.length) : '';
  if(all.length === 0){
    box.innerHTML = '<div class="empty">Aucune alerte.</div>';
    return;
  }
  box.innerHTML = all.map(a=>{
    const level = (a.level === 'warn' || a.level === 'crit') ? a.level : 'info';
    const label = level === 'crit' ? 'crit' : level;
    return '<div class="alert-row ' + level + '">' +
      '<span class="lvl">[' + escapeHtml(label) + ']</span>' +
      '<span class="msg" title="' + escapeHtml(a.message || '') + '">' + escapeHtml(a.message || '—') + '</span>' +
      '<span class="when">' + escapeHtml(relTime(a.created_at)) + '</span>' +
    '</div>';
  }).join('');
}

/* ------------------------- journal d'intégrité ------------------------- */

async function refreshAuditDevices(keepSelection){
  const sel = document.getElementById('auditDevice');
  let list;
  try{
    list = await apiGet('/fleet/devices');
  }catch(e){ return; }
  if(!Array.isArray(list)) list = [];
  const previous = keepSelection ? sel.value : currentAuditDevice;
  sel.innerHTML = '<option value="">— Appareil —</option>' + list.map(d=>{
    return '<option value="' + escapeHtml(d.id) + '">' +
      escapeHtml(d.username || '—') + ' · ' + escapeHtml(d.device_name || 'appareil') + '</option>';
  }).join('');
  if(previous && list.some(d=> d.id === previous)){
    sel.value = previous;
    currentAuditDevice = previous;
  } else {
    currentAuditDevice = '';
  }
}

async function loadAudit(id){
  const body = document.getElementById('auditBody');
  if(!id){
    body.innerHTML = '<tr><td colspan="4" class="empty">Choisis un appareil pour charger son journal.</td></tr>';
    return;
  }
  let list;
  try{
    list = await apiGet('/fleet/audit/' + encodeURIComponent(id) + '?limit=100');
  }catch(e){
    body.innerHTML = '<tr><td colspan="4" class="empty">Journal injoignable — le cerveau ne répond pas.</td></tr>';
    return;
  }
  if(!Array.isArray(list) || list.length === 0){
    body.innerHTML = '<tr><td colspan="4" class="empty">Journal vide.</td></tr>';
    return;
  }
  body.innerHTML = list.map(e=>{
    let ev = e.event;
    let evStr = (typeof ev === 'string') ? ev : JSON.stringify(ev);
    if(evStr == null) evStr = '';
    if(evStr.length > 90) evStr = evStr.slice(0, 90) + '…';
    const hash = e.hash ? String(e.hash).slice(0, 10) + '…' : '—';
    return '<tr>' +
      '<td class="mono">' + escapeHtml(e.seq != null ? e.seq : '—') + '</td>' +
      '<td class="dim">' + escapeHtml(clockTime(e.ts)) + '</td>' +
      '<td class="mono event-cell" title="' + escapeHtml(typeof ev === 'string' ? ev : JSON.stringify(ev)) + '">' + escapeHtml(evStr) + '</td>' +
      '<td class="mono">' + escapeHtml(hash) + '</td>' +
    '</tr>';
  }).join('');
}

document.getElementById('auditDevice').addEventListener('change', function(){
  currentAuditDevice = this.value;
  loadAudit(this.value);
});

document.getElementById('auditVerify').addEventListener('click', async function(){
  const id = currentAuditDevice || document.getElementById('auditDevice').value;
  if(!id){ toast('Choisis d\'abord un appareil.', 'err'); return; }
  this.disabled = true;
  try{
    const res = await apiPost('/fleet/audit/verify', { device_id: id });
    if(res && res.ok){
      toast('Chaîne valide ✓', 'ok');
    } else if(res && res.broken_at != null){
      toast('Chaîne rompue à la séquence ' + res.broken_at, 'err');
    } else {
      toast('Vérification sans résultat.', 'err');
    }
  }catch(e){
    toast('Vérification impossible — le cerveau ne répond pas.', 'err');
  }
  this.disabled = false;
});

/* ------------------------- panneau non configuré / retry ------------------------- */

document.getElementById('ncRetry').addEventListener('click', async ()=>{
  await refreshState();
  if(!(fleetState && fleetState.configured === false)) pollAll();
});

/* ------------------------- pilotage ------------------------- */

async function pollAll(){
  await refreshState();
  if(fleetState && fleetState.configured === false) return; // cartes masquées
  refreshDevices();
  refreshPending();
  refreshAlerts();
  refreshAuditDevices(true);
}

pollAll();
setInterval(pollAll, POLL_MS);
