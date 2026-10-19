const { app, BrowserWindow, ipcMain, screen, Tray, Menu, nativeImage, globalShortcut } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const alexConfig = require('./config.js');

/* =====================================================================
   ALEX — coquille (étape 1/4)
   - Une seule fenêtre, deux "formes" : chat complet (orb centré) et
     îlot flottant minimisé (façon Dynamic Island), avec transition
     animée entre les deux.
   - Fermer OU minimiser => contracte en îlot (ne quitte jamais l'app).
   - Quitter réellement : uniquement via le menu de la barre système.
   ===================================================================== */

const FULL_W = 420, FULL_H = 640;
const ISLAND_W = 210, ISLAND_H = 46;
const INFO_W = 340, INFO_H = 110;
let win;
let tray;
let mode = 'full';       // 'full' | 'island' | 'info'
let prevMode = 'island';
let animTimer = null;

function computeBounds(targetMode){
  const disp = screen.getPrimaryDisplay();
  if(targetMode === 'island'){
    return {
      width: ISLAND_W,
      height: ISLAND_H,
      x: Math.round(disp.workArea.x + disp.workArea.width/2 - ISLAND_W/2),
      y: disp.workArea.y + 2
    };
  }
  if(targetMode === 'info'){
    return {
      width: INFO_W,
      height: INFO_H,
      x: Math.round(disp.workArea.x + disp.workArea.width/2 - INFO_W/2),
      y: disp.workArea.y + 4
    };
  }
  return {
    width: FULL_W,
    height: FULL_H,
    x: Math.round(disp.workArea.x + disp.workArea.width/2 - FULL_W/2),
    y: Math.round(disp.workArea.y + disp.workArea.height/2 - FULL_H/2)
  };
}

function animateTo(targetBounds, duration = 320){
  clearInterval(animTimer);
  if(win.isDestroyed()) return;
  const start = win.getBounds();
  const startTime = Date.now();
  animTimer = setInterval(()=>{
    if(win.isDestroyed()){ clearInterval(animTimer); return; }
    const t = Math.min(1, (Date.now()-startTime)/duration);
    const e = 1 - Math.pow(1-t, 3); // easeOutCubic — contraction/expansion douce
    win.setBounds({
      x: Math.round(start.x + (targetBounds.x-start.x)*e),
      y: Math.round(start.y + (targetBounds.y-start.y)*e),
      width: Math.round(start.width + (targetBounds.width-start.width)*e),
      height: Math.round(start.height + (targetBounds.height-start.height)*e)
    });
    if(t>=1) clearInterval(animTimer);
  }, 16);
}

function setMode(newMode){
  if(win.isDestroyed()) return;
  const target = computeBounds(newMode);
  const b = win.getBounds();
  // Idem seulement si le mode ET les bounds réels correspondent déjà
  const already = mode === newMode
    && Math.abs(b.width - target.width) <= 2
    && Math.abs(b.height - target.height) <= 2;
  if(already) return;
  mode = newMode;
  prevBounds = null; // invalide un éventuel état plein écran en attente
  animateTo(target);
  if(alwaysOnTopEnabled) win.setAlwaysOnTop(true, 'floating');
  win.webContents.send('mode-changed', mode);
}

function createWindow(){
  const bounds = computeBounds('full');
  win = new BrowserWindow({
    ...bounds,
    icon: path.join(__dirname, 'icon.png'),
    transparent: true,
    frame: false,
    resizable: false,
    movable: true,
    hasShadow: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    backgroundColor: '#00000000',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });

  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.on('blur', ()=> {
    if(!alwaysOnTopEnabled || win.isDestroyed()) return;
    // Xwayland : ré-appliquer alwaysOnTop pendant le blur crée un combat de focus → différé
    setTimeout(()=>{
      if(win && !win.isDestroyed() && !win.isFocused() && alwaysOnTopEnabled) win.setAlwaysOnTop(true, 'floating');
    }, 250);
  });
  win.loadFile('index.html');
  // Synchronise le renderer avec le mode réel au chargement (évite classes HTML en dur désynchronisées)
  win.webContents.on('did-finish-load', ()=>{
    if(win && !win.isDestroyed()) win.webContents.send('mode-changed', mode);
  });

  ipcMain.on('force-focus', ()=> {
    if(win && !win.isDestroyed() && !win.isFocused()){
      win.focus();
    }
  });
  ipcMain.on('set-mode', (event, newMode)=> {
    setMode(newMode);
  });
  ipcMain.on('shot-now', (event, file)=> {
    win.webContents.capturePage().then(img=>{
      require('fs').writeFileSync(file, img.toPNG());
    }).catch(e=> console.error('shot fail', e));
  });
}

let alwaysOnTopEnabled = alexConfig.read().alwaysOnTop !== false;

/* Applique un état explicite (évite les bascules croisées entre les fenêtres) */
function setAlwaysOnTop(next){
  const target = next !== false;
  if(target !== alwaysOnTopEnabled){
    alwaysOnTopEnabled = target;
    if(win && !win.isDestroyed()) win.setAlwaysOnTop(alwaysOnTopEnabled, 'floating');
    rebuildMenus();
  }
  alexConfig.write({ alwaysOnTop: alwaysOnTopEnabled });
  return alwaysOnTopEnabled;
}

function toggleAlwaysOnTop(){
  return setAlwaysOnTop(!alwaysOnTopEnabled);
}

function rebuildMenus(){
  const menu = Menu.buildFromTemplate([
    { label: 'Ouvrir Alex', click: ()=> setMode('full') },
    { label: 'Réduire en îlot', click: ()=> setMode('island') },
    { type: 'separator' },
    { label: 'Toujours au premier plan', type: 'checkbox', checked: alwaysOnTopEnabled, click: toggleAlwaysOnTop },
    { type: 'separator' },
    { label: 'Quitter Alex', click: ()=> app.quit() }
  ]);
  tray.setContextMenu(menu);
}

function createTray(){
  const iconPath = path.join(__dirname, 'icon.png');
  let icon;
  if (require('fs').existsSync(iconPath)) {
    icon = nativeImage.createFromPath(iconPath);
  } else {
    const size = 16;
    const svg = `<svg width="${size}" height="${size}" xmlns="http://www.w3.org/2000/svg">
      <circle cx="${size/2}" cy="${size/2}" r="${size/2-1}" fill="#ff9d3d"/>
    </svg>`;
    icon = nativeImage.createFromDataURL('data:image/svg+xml;base64,' + Buffer.from(svg).toString('base64'));
    icon.setTemplateImage(true);
  }
  tray = new Tray(icon);
  tray.setToolTip('Alex');
  rebuildMenus();
}

ipcMain.on('show-context-menu', (event)=> {
  const menu = Menu.buildFromTemplate([
    { label: 'Ouvrir Alex', click: ()=> setMode('full') },
    { label: 'Réduire en îlot', click: ()=> setMode('island') },
    { type: 'separator' },
    { label: 'Toujours au premier plan', type: 'checkbox', checked: alwaysOnTopEnabled, click: toggleAlwaysOnTop },
    { type: 'separator' },
    { label: 'Quitter Alex', click: ()=> app.quit() }
  ]);
  menu.popup({ window: win });
});

ipcMain.on('keep-top', ()=> {
  if(alwaysOnTopEnabled && !win.isDestroyed()) win.setAlwaysOnTop(true, 'floating');
});

/* Info expand — agrandit temporairement l'îlot pour afficher heure/date/météo */
ipcMain.on('set-info-mode', (event, enable)=>{
  if(win.isDestroyed()) return;
  if(enable && mode === 'island'){
    prevMode = mode;
    animateTo(computeBounds('info'), 250);
    mode = 'info';
    win.webContents.send('mode-changed', 'info');
  } else if(!enable && mode === 'info'){
    animateTo(computeBounds(prevMode), 250);
    mode = prevMode;
    win.webContents.send('mode-changed', prevMode);
  }
});

let prevBounds = null;
let prevModeFullscreen = null;
ipcMain.on('toggle-fullscreen', ()=> {
  if(win.isDestroyed()) return;
  if(prevBounds){
    win.setBounds(prevBounds);
    prevBounds = null;
    if(prevModeFullscreen){
      mode = prevModeFullscreen;
      prevModeFullscreen = null;
      win.webContents.send('mode-changed', mode);
    }
  } else {
    prevBounds = win.getBounds();
    prevModeFullscreen = mode;
    const disp = screen.getPrimaryDisplay();
    win.setBounds(disp.workArea);
  }
});

ipcMain.on('toggle-always-top', (event, enable)=> {
  const next = (typeof enable === 'boolean') ? enable : !alwaysOnTopEnabled;
  const applied = setAlwaysOnTop(next);
  if(event && event.sender && !event.sender.isDestroyed()){
    event.sender.send('always-top-changed', applied);
  }
  if(win && !win.isDestroyed()){
    win.webContents.send('settings-sync', alexConfig.read());
  }
});

/* --- Orb du bureau --- */
let orbWin = null;
function createOrbWindow(){
  if(orbWin && !orbWin.isDestroyed()) return;
  const disp = screen.getPrimaryDisplay();
  const sz = 300;
  orbWin = new BrowserWindow({
    x:Math.round(disp.bounds.x+disp.bounds.width/2-sz/2),
    y:Math.round(disp.bounds.y+disp.bounds.height/2-sz/2),
    width:sz, height:sz,
    transparent: true, frame: false, resizable: false,
    hasShadow: false, skipTaskbar: true,
    focusable: false, alwaysOnTop: false,
    backgroundColor: '#00000000',
    webPreferences: { nodeIntegration: true, contextIsolation: false, backgroundThrottling: false }
  });
  orbWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  orbWin.loadFile('orb-desktop.html');
  orbWin.on('closed', ()=>{ orbWin = null; });
}
ipcMain.on('toggle-desktop-orb', (event, enable)=>{
  const on = enable !== false;
  if(on && !orbWin){
    createOrbWindow();
  } else if(!on && orbWin){
    orbWin.close();
    orbWin = null;
  }
  alexConfig.write({ desktopOrb: on });
});

/* L'orb signale qu'il est prêt → lui renvoyer le dernier état */
ipcMain.on('orb-ready', ()=>{
  if(orbWin && !orbWin.isDestroyed()){
    orbWin.webContents.send('orb-state', _lastOrbState);
  }
});

/* Drag de l'orb du bureau */
ipcMain.on('orb-drag-start', ()=>{
  if(orbWin && !orbWin.isDestroyed()) orbWin.setAlwaysOnTop(true, 'floating');
});
ipcMain.on('orb-drag-move', (event, dx, dy)=>{
  if(orbWin && !orbWin.isDestroyed()){
    const b = orbWin.getBounds();
    orbWin.setBounds({ x: b.x + dx, y: b.y + dy, width: b.width, height: b.height });
  }
});
ipcMain.on('orb-drag-end', ()=>{
  if(orbWin && !orbWin.isDestroyed()) orbWin.setAlwaysOnTop(false);
});

/* Synchroniser l'état de l'orb vers la fenêtre du bureau */
let _lastOrbState = {name:'idle', energy:0.12, rotSpeed:0.0035};
ipcMain.on('orb-state-changed', (event, data)=>{
  _lastOrbState = data;
  if(orbWin && !orbWin.isDestroyed()) orbWin.webContents.send('orb-state', data);
});

/* --- Orb : clic → lire l'écran (vision opencode) --- */
let _orbBusy = false;
ipcMain.on('orb-click', async ()=>{
  if(_orbBusy) return;
  _orbBusy = true;
  /* orb passe en écoute (feedback visuel) */
  if(orbWin && !orbWin.isDestroyed()) orbWin.webContents.send('orb-state', {name:'listening', energy:0.2, rotSpeed:0.01});
  try {
    const resp = await fetch('http://127.0.0.1:8765/vision', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({question:"Décris ce qui est à l'écran en 2-3 phrases orales, français."})
    });
    const data = await resp.json();
    const text = data.analysis || data.error || "Je n'ai rien vu.";
    /* orb parle (feedback) */
    if(orbWin && !orbWin.isDestroyed()) orbWin.webContents.send('orb-state', {name:'speaking', energy:0.4, rotSpeed:0.006});
    /* afficher dans la fenêtre principale + prononcer */
    if(win && !win.isDestroyed()){
      win.webContents.send('orb-vision-result', {text, url: data.url || null});
    }
    setTimeout(()=>{ if(orbWin && !orbWin.isDestroyed()) orbWin.webContents.send('orb-state', {name:'idle', energy:0.12, rotSpeed:0.0035}); }, 3000);
  } catch(e) {
    console.error('[orb-vision]', e);
    if(orbWin && !orbWin.isDestroyed()) orbWin.webContents.send('orb-state', {name:'idle', energy:0.12, rotSpeed:0.0035});
  }
  _orbBusy = false;
});

/* --- Orb : clic droit → menu --- */
ipcMain.on('orb-menu', ()=>{
  if(!orbWin || orbWin.isDestroyed()) return;
  const b = orbWin.getBounds();
  const menu = Menu.buildFromTemplate([
    { label:'Lire l\'écran', click: ()=> orbWin.webContents.send('orb-click') },
    { label:'Ouvrir Alex', click: ()=>{ if(win && !win.isDestroyed()){ win.show(); win.focus(); } } },
    { type:'separator' },
    { label:'Muette', type:'checkbox', checked: _orbMuted, click: (mi)=>{ _orbMuted = mi.checked; } },
    { label:'Position par défaut', click: ()=>{
        const disp = screen.getPrimaryDisplay();
        orbWin.setBounds({ x: Math.round(disp.bounds.x+disp.bounds.width/2-150), y: Math.round(disp.bounds.y+disp.bounds.height/2-150), width:300, height:300 });
      }},
    { type:'separator' },
    { label:'Fermer l\'orb', click: ()=>{ orbWin.close(); orbWin=null; } }
  ]);
  menu.popup({ window: orbWin });
});
let _orbMuted = false;

/* --- Media viewer : repositionne la fenêtre --- */
let prevModeForMedia = null;
ipcMain.on('media-view', (event, open)=>{
  if(open){
    prevModeForMedia = mode;
    if(mode === 'island') return;
    const disp = screen.getPrimaryDisplay();
    const islandBounds = {
      width: ISLAND_W, height: ISLAND_H,
      x: Math.round(disp.workArea.x + disp.workArea.width/2 - ISLAND_W/2),
      y: disp.workArea.y + 2
    };
    animateTo(islandBounds, 250);
    mode = 'island';
    win.webContents.send('mode-changed', 'island');
  } else {
    if(prevModeForMedia === 'full'){
      animateTo(computeBounds('full'), 250);
      mode = 'full';
      win.webContents.send('mode-changed', 'full');
    }
    prevModeForMedia = null;
  }
});

/* --- Fenêtre de Réglages --- */
let settingsWin = null;
ipcMain.on('open-settings', ()=>{
  if(settingsWin && !settingsWin.isDestroyed()){ settingsWin.focus(); return; }
  const disp = screen.getPrimaryDisplay();
  settingsWin = new BrowserWindow({
    width: 560, height: 640,
    x: Math.round(disp.bounds.x + disp.bounds.width/2 - 280),
    y: Math.round(disp.bounds.y + disp.bounds.height/2 - 320),
    transparent: false,
    frame: true,
    resizable: false,
    alwaysOnTop: false,
    backgroundColor: '#0a0808',
    webPreferences: { nodeIntegration: true, contextIsolation: false }
  });
  settingsWin.loadFile('settings.html');
  settingsWin.setMenuBarVisibility(false);
  settingsWin.on('closed', ()=>{ settingsWin = null; });
});
ipcMain.on('close-settings', ()=>{
  if(settingsWin && !settingsWin.isDestroyed()) settingsWin.close();
});
ipcMain.on('settings-updated', (event, config)=>{
  if(win && !win.isDestroyed()) win.webContents.send('settings-sync', config);
});

/* ---------------------------------------------------------------------
   Cerveau Python (FastAPI/uvicorn) — démarré/arrêté avec l'app
--------------------------------------------------------------------- */
let brainProcess = null;
function startBrain(){
  const _fs = require('fs');
  let brainPath = require('path').join(__dirname, 'brain', '.venv', 'bin', 'python3');
  if(!_fs.existsSync(brainPath)) brainPath = 'python3';
  brainProcess = spawn(
    brainPath,
    ['-m', 'uvicorn', 'brain.main:app', '--host', '127.0.0.1', '--port', '8765'],
    { cwd: __dirname, stdio: 'ignore' }
  );
  brainProcess.on('error', (err)=>{
    console.error("Impossible de démarrer le cerveau Python :", err.message);
  });
}
function stopBrain(){
  if(brainProcess){ brainProcess.kill(); brainProcess = null; }
}

app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
app.commandLine.appendSwitch('remote-debugging-port', '9226');
app.commandLine.appendSwitch('remote-allow-origins', '*');

// Instance unique : relancer depuis le menu focus la fenêtre existante.
if(!app.requestSingleInstanceLock()){
  app.quit();
} else {
  app.on('second-instance', ()=>{
    if(win && !win.isDestroyed()){ win.show(); win.focus(); }
  });
}

app.whenReady().then(()=>{
  createWindow();
  createTray();
  startBrain();
  if(alexConfig.read().desktopOrb) createOrbWindow();
  globalShortcut.register('Alt+Space', ()=> setMode(mode === 'full' ? 'island' : 'full'));
});

app.on('window-all-closed', ()=>{
  // Ne quitte jamais sur simple fermeture de fenêtre — seul le menu tray quitte vraiment.
});

app.on('before-quit', ()=> stopBrain());

app.on('will-quit', ()=>{
  globalShortcut.unregisterAll();
});
