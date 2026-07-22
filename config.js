// Config Electron : lecture/écriture sur disque (JSON)
const fs = require('fs');
const path = require('path');

let configDir;
try {
  configDir = require('electron').app.getPath('userData');
} catch(e) {
  configDir = path.join(__dirname, '.alex-config');
}
const CONFIG_PATH = path.join(configDir, 'alex-config.json');
if (!fs.existsSync(configDir)) {
  try { fs.mkdirSync(configDir, { recursive: true }); } catch(e) {}
}

const DEFAULTS = {
  brainMode: 'auto',
  assistantName: 'Alex',
  greeting: "Salut Jordy, ravie de te retrouver !",
  appearanceStyle: 'orbe',
  activityHints: true,
  voiceEnabled: true,
  currentVoice: 'denise',
  alwaysOnTop: true,
  desktopOrb: false,
  listenAlways: true,
};

function readConfig() {
  try {
    if (fs.existsSync(CONFIG_PATH)) {
      const raw = fs.readFileSync(CONFIG_PATH, 'utf8');
      return Object.assign({}, DEFAULTS, JSON.parse(raw));
    }
  } catch (e) {}
  return Object.assign({}, DEFAULTS);
}

function writeConfig(partial) {
  const merged = Object.assign({}, readConfig(), partial);
  try {
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(merged, null, 2), 'utf8');
  } catch (e) {}
  return merged;
}

module.exports = { read: readConfig, write: writeConfig, defaults: DEFAULTS };
