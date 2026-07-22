const { app, BrowserWindow, ipcMain, screen, Tray, Menu, nativeImage, globalShortcut } = require('electron');
const { spawn } = require('child_process');

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

let win;
let tray;
let mode = 'full';       // 'full' | 'island'
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
  if(mode === newMode || win.isDestroyed()) return;
  mode = newMode;
  animateTo(computeBounds(mode));
  if(alwaysOnTopEnabled) win.setAlwaysOnTop(true, 'screen-saver');
  win.webContents.send('mode-changed', mode);
}

function createWindow(){
  const bounds = computeBounds('full');
  win = new BrowserWindow({
    ...bounds,
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
  win.on('blur', ()=> { if(alwaysOnTopEnabled && !win.isDestroyed()){ win.setAlwaysOnTop(true, 'screen-saver'); win.moveTop(); } });
  win.loadFile('index.html');

  setInterval(()=>{
    if(alwaysOnTopEnabled && !win.isDestroyed()) win.setAlwaysOnTop(true, 'screen-saver');
  }, 3000);

  ipcMain.on('set-mode', (event, newMode)=> setMode(newMode));
}

let alwaysOnTopEnabled = true;

function toggleAlwaysOnTop(){
  alwaysOnTopEnabled = !alwaysOnTopEnabled;
  if(!win.isDestroyed()) win.setAlwaysOnTop(alwaysOnTopEnabled, 'screen-saver');
  rebuildMenus();
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
  const size = 16;
  const svg = `<svg width="${size}" height="${size}" xmlns="http://www.w3.org/2000/svg">
    <circle cx="${size/2}" cy="${size/2}" r="${size/2-1}" fill="#ff9d3d"/>
  </svg>`;
  const dataUrl = 'data:image/svg+xml;base64,' + Buffer.from(svg).toString('base64');
  const icon = nativeImage.createFromDataURL(dataUrl);
  icon.setTemplateImage(true);
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
  if(alwaysOnTopEnabled && !win.isDestroyed()) win.setAlwaysOnTop(true, 'screen-saver');
});

let prevBounds = null;
ipcMain.on('toggle-fullscreen', ()=> {
  if(win.isDestroyed()) return;
  if(prevBounds){
    win.setBounds(prevBounds);
    prevBounds = null;
  } else {
    prevBounds = win.getBounds();
    const disp = screen.getPrimaryDisplay();
    win.setBounds(disp.workArea);
  }
});

ipcMain.on('toggle-always-top', (event)=> {
  toggleAlwaysOnTop();
  event.sender.send('always-top-changed', alwaysOnTopEnabled);
});

/* --- Orb du bureau --- */
let orbWin = null;
ipcMain.on('toggle-desktop-orb', (event, enable)=>{
  if(enable && !orbWin){
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
      webPreferences: { nodeIntegration: true, contextIsolation: false }
    });
    orbWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    orbWin.loadFile('orb-desktop.html');
    orbWin.on('closed', ()=>{ orbWin = null; });
  } else if(!enable && orbWin){
    orbWin.close();
    orbWin = null;
  }
});

/* Drag de l'orb du bureau */
ipcMain.on('orb-drag-start', ()=>{
  if(orbWin && !orbWin.isDestroyed()) orbWin.setAlwaysOnTop(true, 'screen-saver');
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
const orbStates = ['idle','listening','thinking','speaking','searching','system_search','system_launch'];
ipcMain.on('orb-state-changed', (event, state)=>{
  if(orbWin && !orbWin.isDestroyed()) orbWin.webContents.send('orb-state', state);
});

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
  const brainPath = require('path').join(__dirname, 'brain', '.venv', 'bin', 'python3');
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
app.whenReady().then(()=>{
  createWindow();
  createTray();
  startBrain();
  globalShortcut.register('Alt+Space', ()=> setMode(mode === 'full' ? 'island' : 'full'));
});

app.on('window-all-closed', ()=>{
  // Ne quitte jamais sur simple fermeture de fenêtre — seul le menu tray quitte vraiment.
});

app.on('before-quit', ()=> stopBrain());

app.on('will-quit', ()=>{
  globalShortcut.unregisterAll();
});
