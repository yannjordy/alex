const { contextBridge, ipcRenderer } = require('electron');

/* =====================================================================
   ALEX DESKTOP — Preload Script (sécurité IPC)
   ===================================================================== */

contextBridge.exposeInMainWorld('alex', {
  // Contrôles de fenêtre
  window: {
    minimize: () => ipcRenderer.send('window-minimize'),
    maximize: () => ipcRenderer.send('window-maximize'),
    close: () => ipcRenderer.send('window-close'),
    getInfo: () => ipcRenderer.invoke('get-window-info')
  },

  // Toujours au premier plan
  alwaysOnTop: {
    toggle: () => ipcRenderer.send('toggle-always-top'),
    onChange: (callback) => ipcRenderer.on('always-top-changed', (_, enabled) => callback(enabled))
  },

  // Fichiers
  files: {
    read: (filePath) => ipcRenderer.invoke('read-file', filePath),
    write: (filePath, content) => ipcRenderer.invoke('write-file', { filePath, content }),
    list: (dirPath) => ipcRenderer.invoke('list-directory', dirPath),
    selectDirectory: () => ipcRenderer.invoke('select-directory'),
    search: (directory, query) => ipcRenderer.invoke('search-files', { directory, query })
  },

  // Commandes
  terminal: {
    execute: (command, cwd) => ipcRenderer.invoke('execute-command', { command, cwd })
  },

  // Dialogues
  dialog: {
    saveFile: (content, defaultName) => ipcRenderer.invoke('save-file-dialog', { content, defaultName })
  },

  // Événements
  events: {
    on: (channel, callback) => {
      const validChannels = ['mode-changed', 'settings-sync'];
      if (validChannels.includes(channel)) {
        ipcRenderer.on(channel, (_, ...args) => callback(...args));
      }
    },
    off: (channel, callback) => {
      ipcRenderer.removeListener(channel, callback);
    }
  }
});
