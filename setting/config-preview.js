// Version "preview navigateur" de config.js : utilise localStorage au lieu
// d'un fichier disque (qui nécessiterait Node/Electron). Même interface
// (window.AlexConfig.read/write/defaults) que la vraie version Electron,
// pour que index.html et settings.html fonctionnent sans modification.
(function(){
  const KEY = 'alex-config-preview';
  const DEFAULTS = {
    brainMode: 'auto',
    assistantName: 'Alex',
    greeting: "Bonjour, je suis Alex. Comment puis-je t'aider ?",
    appearanceStyle: 'orbe',
    activityHints: true
  };

  function readConfig(){
    try{
      const raw = localStorage.getItem(KEY);
      return Object.assign({}, DEFAULTS, raw ? JSON.parse(raw) : {});
    }catch(e){
      return Object.assign({}, DEFAULTS);
    }
  }

  function writeConfig(partial){
    const merged = Object.assign({}, readConfig(), partial);
    try{ localStorage.setItem(KEY, JSON.stringify(merged)); }catch(e){}
    return merged;
  }

  window.AlexConfig = { read: readConfig, write: writeConfig, defaults: DEFAULTS };
})();
