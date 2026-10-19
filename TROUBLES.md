# TROUBLES.md — Pièges rencontrés et solutions (session du 16 oct 2026)

> Référence pour ne **plus jamais retomber** dans ces pièges. Chaque entrée :
> **Cause → Conséquence → Solution.**

---

## 1. Zone de saisi invisible (0×0) — « Alex ne répond plus au clavier »

- **Cause :** `index.html` avait les classes `mic-active mode-island` **codées en dur** dans le HTML
  (`<div id="app" class="mic-active mode-island">`), alors que `main.js` démarre en mode `full`.
  `setMode()` fait un *early-return* si le mode est déjà `full` → l'événement `mode-changed` n'est
  **jamais envoyé** au démarrage → le renderer garde les classes îlot → CSS
  `#app.mode-island #inputBar { display:none }`.
- **Conséquence :** `#textInput` fait 0×0, `elementFromPoint` renvoie `BODY`, rien de cliquable ni
  tapable. L'interface paraît « cassée » alors que le HTML/JS est valide.
- **Solution :** (a) HTML par défaut `class="mode-full"` ; (b) `main.js` envoie systématiquement
  `win.webContents.send('mode-changed', mode)` sur `did-finish-load` pour synchroniser
  main ↔ renderer. *(Fait : index.html + main.js corrigés.)*

## 2. L'ancienne copie d'Alex « vole » le lancement (verrou d'instance unique)

- **Cause :** `main.js` utilise `app.requestSingleInstanceLock()` avec un profil commun
  (`~/.config/alex-assistant`). Le raccourci du dock `alex.desktop` pointe vers
  **`~/Documents/alex/lancer-alex.sh`** (ancienne copie). D'autres instances
  (`~/Documents/alex`, `~/Projects/alex/alex`) pouvaient tourner.
- **Conséquence :** la nouvelle instance (prioritaire) détecte le verrou et **quitte
  silencieusement** (zygotes orphelins, pas de fenêtre) ; l'utilisateur voit l'ancienne copie
  **sans les corrections** → les bugs « reviennent ».
- **Solution :** avant tout lancement, tuer toutes les instances :
  `kill -9 $(pgrep -f "[a]lex-prioritaire/[n]ode_modules")` et les copies
  (`[D]ocuments/alex`, `[P]rojects/alex/alex`). **À faire :** rediriger `alex.desktop` +
  `lancer-alex.sh` vers `~/Projects/alex/alex-prioritaire`.

## 3. Port CDP (9223/9225) détenu par le cerveau — débogage impossible

- **Cause :** `startBrain` dans `main.js` lance `uvicorn` comme **enfant** d'Electron **sans fermer
  les fds** → uvicorn **hérite** du socket LISTEN DevTools ; quand Electron meurt, le brain garde
  le port. Également : `bind() failed: Adresse déjà utilisée (98)`.
- **Conséquence :** `curl http://127.0.0.1:922X/json/*` et les WebSockets CDP échouent
  (connection refused / handshake timeout) alors que le log dit « DevTools listening ».
- **Solution :** utiliser un **port libre différent** (ex. 9226) ; **relancer le cerveau**
  (`fuser -k 8765/tcp` puis uvicorn) libère l'fd hérité. Vérifier le détenteur :
  `ss -tlnp | grep 922X`.

## 4. Clavier avalé — socket D-Bus ibus manquant (cause racine du clavier)

- **Cause :** `ibus-daemon` tourne (pid vivant) mais son socket `~/.cache/ibus/dbus-*` a disparu
  ; le fichier d'adresse `~/.config/ibus/bus/` est obsolète. Electron (Xwayland) ne peut pas se
  connecter à l'IME → logs : `IBUS-WARNING: Unable to connect to ibus` +
  `Events queue growing too big, will start to drop` → **les touches sont jetées**.
- **Conséquence :** les frappes n'arrivent jamais (ou arrivent sans insertion de texte :
  `keydown` reçu mais aucun événement `input`, `value` reste vide). Symptôme exact :
  « Alex ne peut pas lire les touches de mon clavier ».
- **Solution :** `ibus restart` (recrée socket + fichier d'adresse), **puis** relancer Alex avec
  `IBUS_ADDRESS="$(ibus address)"`. Vérification : `ls ~/.cache/ibus/dbus-*` + zéro
  `IBUS-WARNING` dans le log.

## 5. Lancement sans environnement de session GUI

- **Cause :** lancement via `setsid nohup` depuis un shell d'automatisation **sans** les variables
  de session (DISPLAY, XAUTHORITY, XDG_RUNTIME_DIR, DBUS, IBUS).
- **Conséquence :** IBus/GTK/D-Bus impossibles à joindre (cf. §4), fenêtre mal reliée à la
  session, clavier/système de fichiers instables.
- **Solution :** lancer avec l'env complet :
  ```bash
  env DISPLAY=:0 \
      XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.* \   # NOM VARIABLE à chaque login !
      XDG_RUNTIME_DIR=/run/user/1000 \
      DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
      IBUS_ADDRESS="$(ibus address)" \
      ./node_modules/.bin/electron . --no-sandbox
  ```
  Le nom `XAUTHORITY` se lit dans `/proc/<pid d'un process jordy>/environ`.

## 6. Combat de focus Xwayland (blur → setAlwaysOnTop → perte de focus)

- **Cause :** à chaque `blur`, main.js réappliquait **immédiatement** `setAlwaysOnTop(true,
  'floating')` ; le handler `force-focus` faisait idem — sous Xwayland, ré-appliquer le niveau
  always-on-top provoque des micro-perte/redonnées de focus.
- **Conséquence :** `document.activeElement = textInput` mais `document.hasFocus() === false`
  → les touches n'atteignent jamais la fenêtre ; parfois les `keydown` arrivent sans insertion.
- **Solution :** blur handler **différé (250 ms)** et conditionné à `!win.isFocused()` ;
  `force-focus` n'appelle `win.focus()` **que si** la fenêtre n'a pas le focus (ne jamais toucher
  `setAlwaysOnTop` dans ce handler).

## 7. Auto-effondrement en îlot après une réponse (saisie qui disparaît)

- **Cause :** `scheduleAutoCollapse()` envoie `set-mode 'island'` **4 s** après l'affichage d'une
  « proposal » (comportement voulu : réduire Alex en mini-îlot).
- **Conséquence :** la saisie se referme quelques secondes après le démarrage ou une réponse —
  on croit que « ça ne marche plus ».
- **Solution :** pour taper : recliquer le bouton de déploiement / `set-mode 'full'` (Alt+Space
  bascule îlot ↔ plein). Vérifier `document.getElementById('app').className` avant de conclure à
  un bug clavier (cf. §1).

## 8. Piège d'auto-destruction du shell (rappel + variante nouvelle)

- **Cause :** `pgrep -f "electron"` / `kill -9 $(pgrep -f ...)` avec le **mot en clair présent dans
  la propre commande** (y compris dans un `printf`/`echo` de la même ligne) → le shell de
  l'agent se tue lui-même → timeout inexpliqué.
- **Conséquence :** commandes « plantées » sans erreur, perte de temps.
- **Solution :** motif entre crochets **et** aucune occurrence du mot en clair :
  `[e]lectro[n]`, `printf "app:%s" "$(pgrep -cf '[e]lectro[n]')"`. Ne jamais écrire `electron`
  en clair dans la même ligne qu'un pgrep/pkill.

---

## 9. Navigateur qui « dit » ouvrir un site mais aucune fenêtre — « Alex ne lance pas vraiment »

- **Causes (3 cumulées) :**
  1. **Instance zombie sans `DISPLAY`** : `xdg-open` passe par DBus → Chrome lancé
     **sans variables graphiques** → 22 processus, zéro fenêtre possible, mais l'URL
     est « acceptée » (notification « … est prêt » = leurrant). Diagnostic :
     `tr '\0' '\n' < /proc/<pid>/environ | grep -c DISPLAY=` → **0**.
  2. **Ozone Wayland natif** : même avec l'env complet, lancé en arrière-plan le
     Chrome Wayland ne **mappe pas** sa fenêtre. Solution : **`--ozone-platform=x11`**
     (+ `--new-window`) → fenêtre Xwayland visible et détectable par `xwininfo`.
  3. **Snap Chromium cassé** : `snap list chromium` → état « cassé »,
     fichier `/var/lib/snapd/snaps/chromium_XXXX.snap` **absent** →
     `snap remove` + `snap install` (téléchargement ~200 Mo, suivre
     `~/.opencode/rapports/install-chromium.md`).
- **Conséquence :** Alex annonce « J'ouvre YouTube » (motif regex local, le code
  fait bien `webbrowser.open`) mais l'utilisateur ne voit rien — message « faux ».
- **Solution :** `device_control._open_url()` : env session complet (`DISPLAY`,
  `XAUTHORITY` variable, `DBUS`, `XDG_RUNTIME_DIR`) + lancement **direct** du
  binaire navigateur (pas `xdg-open`) + `--ozone-platform=x11 --new-window`,
  navigateur par défaut d'abord (xdg-mime) puis pile Chrome → Chromium →
  Firefox → xdg-open → webbrowser.
- **Vérification :** `xwininfo -root -tree | grep -i "Google Chrome"` → 1 fenêtre
  + capture d'écran visuelle.

---

## Vérifications rapides (checklist avant de dire « c'est cassé »)

```bash
# 1. Quelle instance tourne ? (doit être alex-prioritaire)
pgrep -af "[a]lex-prioritaire/[n]ode_modules" | head -3

# 2. États des services
curl -s http://127.0.0.1:8765/health          # cerveau
ss -tlnp | grep -E "922[0-9]"                  # qui tient le port CDP ?

# 3. État de l'UI (via CDP) : classe + saisie
#    app.className doit contenir mode-full, #inputBar display:flex

# 4. Clavier : zéro IBUS-WARNING dans le log
grep -c IBUS-WARNING /tmp/alex-ime.log
```

## État connu à la fin de la session

- Saisie : corrigée (mode-full + sync) ; clavier : ibus redémarré + env complet.
- Fenêtre : centrée `750,218` en `420×640`, `_NET_WM_STATE_ABOVE` (premier plan) ✓.
- Port CDP utilisé : **9226** (9223/9225 pollués par le cerveau, cf. §3).
- **Reste à faire :** rediriger `alex.desktop` + `~/Documents/alex/lancer-alex.sh` vers
  `alex-prioritaire` (§2) ; tester une frappe physique après ibus restart.
