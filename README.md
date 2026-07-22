# Alex — assistant personnel de bureau (Ubuntu)

Fenêtre de chat avec orb centré. Se contracte en îlot flottant façon
Dynamic Island quand on la ferme ou la réduit (reste toujours au-dessus
du bureau — ne quitte jamais vraiment, sauf via le menu de la barre
système).

## Étapes du projet

1. ✅ **La coquille** — fenêtre de chat + orb + bascule chat/îlot animée
2. ✅ **Le cerveau** — service Python (FastAPI), local (Ollama) ou cloud (Claude), au choix selon dispo
3. ✅ **Le mot d'activation vocal** — Porcupine, écoute locale du mot "Alex"
4. ✅ **Première compétence** — contrôle de cette machine (v1)

## Installation

### 1. L'appli Electron
```bash
npm install
```

### 2. Le cerveau Python
```bash
cd brain
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
```

Le cerveau est démarré et arrêté automatiquement par Electron (voir
`main.js`, fonctions `startBrain`/`stopBrain`) — pas besoin de le lancer
à la main en temps normal.

⚠️ Si tu utilises un environnement virtuel (`.venv`), assure-toi que
`python3` dans le PATH pointe vers celui-ci quand tu lances `npm start`
(active le venv dans le même terminal avant de lancer npm), sinon
`uvicorn`/`fastapi` ne seront pas trouvés par le process spawné.

### 3. Lancer Alex
```bash
npm start
```

## Configurer le mot d'activation vocal ("Alex")

1. Crée un compte gratuit sur https://console.picovoice.ai/
2. Dans la console, section **Porcupine** → crée un mot-clé personnalisé
   `Alex`, choisis la plateforme **Linux**, entraîne-le puis télécharge
   le fichier `.ppn` généré
3. Récupère aussi ta clé d'accès (**AccessKey**) affichée dans ta console
4. Configure les deux variables d'environnement avant `npm start` :

```bash
export ALEX_PORCUPINE_ACCESS_KEY="ta-clé-picovoice"
export ALEX_PORCUPINE_KEYWORD_PATH="/chemin/vers/Alex_fr_linux.ppn"
npm start
```

Sans ces deux variables, le wake word est simplement désactivé (aucune
erreur bloquante) — tu peux toujours parler via le bouton micro ou
`Alt+Espace`.

**Comment ça marche** : Porcupine tourne dans le service Python, écoute
en continu (100% local, aucune donnée envoyée nulle part) uniquement le
mot "Alex". Dès qu'il le détecte, l'interface (connectée en WebSocket)
rouvre le chat si besoin et démarre directement l'écoute de ta phrase
via la reconnaissance vocale du navigateur.

## Configurer le cerveau

Par défaut, Alex tente d'abord un modèle **local** (Ollama), et bascule
sur le **cloud** (Claude) seulement si le local échoue.

**Pour le local (gratuit, hors-ligne)** :
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3
ollama serve   # si pas déjà lancé en arrière-plan
```

**Pour le cloud (Claude)**, définis une variable d'environnement avant
de lancer `npm start` :
```bash
export ANTHROPIC_API_KEY="ta-clé-ici"
npm start
```

Sans Ollama qui tourne ET sans clé API configurée, Alex répond avec un
message expliquant qu'aucun cerveau n'est disponible (pas de plantage).

## Comportement de la fenêtre

- **Chat complet** : fenêtre centrée, orb en haut, messages en dessous,
  barre de saisie + micro en bas
- **Bouton "—"** (ou fermer) : contracte en îlot flottant en haut de
  l'écran
- **Cliquer sur l'îlot** : rouvre le chat complet
- **`Alt+Espace`** : bascule entre les deux, depuis n'importe où
- **Icône barre système** : "Ouvrir Alex" / "Réduire en îlot" / "Quitter Alex"
  (seul moyen de vraiment fermer l'application)

## Compétence : contrôle d'appareils (v1 — cette machine)

Reconnaissance simple par motifs (pas de NLU) — testée avant d'interroger
le modèle de langage, donc ça marche même sans Ollama ni clé API :

| Tu dis | Alex fait |
|---|---|
| "ouvre firefox" / "lance le terminal" | Lance l'application (si trouvée) |
| "monte / baisse le volume" | Ajuste de 10% via `pactl` |
| "coupe le son" | Mute/unmute via `pactl` |
| "verrouille l'écran" | `loginctl lock-session` |
| "éteins l'ordinateur" | Demande confirmation |
| "confirme extinction" | `systemctl poweroff` |
| "redémarre l'ordinateur" | Demande confirmation |
| "confirme redémarrage" | `systemctl reboot` |

Les actions destructrices (éteindre/redémarrer) exigent une phrase de
confirmation séparée — pour éviter qu'une commande mal comprise par la
reconnaissance vocale n'éteigne la machine par erreur.

Le code est dans `brain/skills/device_control.py` — ajoute de nouveaux
motifs/actions ici pour étendre les capacités. Prochaine étape logique
(pas encore faite) : étendre à d'autres appareils (téléphone, autres
machines) via un adaptateur dédié par appareil, comme discuté.

## Ce qui n'est pas encore fait

- Panneau de réglages (Assistant / Mode vocal / Apparence / etc.)
- Mode veille prolongée, formes émotionnelles avancées — repris plus
  tard de `oda-orb-desktop` si voulu
- Contrôle d'autres appareils que cette machine (téléphone, robots, IoT)
- Mémoire de conversation persistante entre les sessions
