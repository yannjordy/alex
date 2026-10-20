import asyncio
import json
import os
import random
import re
from typing import Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel

from . import wake_word
from . import todo as todo_store
from . import prefs as prefs_store
from .agent_state import AGENT_FILE, AGENT_ROLES, clear_agent, load_agent, save_agent
from .skills import device_control, browser_control, download_control
from . import downloads as downloads_store
from . import tools as tools_registry
from .tools import files as _tools_files
from .tools import system as _tools_system
from .tools import web as _tools_web
from .tools import weather as _tools_weather
from .tools import automation as _tools_automation
from .tools import desktop as _tools_desktop
from .tools import network as _tools_network

OLLAMA_URL = "http://127.0.0.1:11434/api"
_LOCAL_MODEL_FILE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".alex-local-model"))


def _load_local_model() -> str:
    """Modèle local persisté (fichier) puis variable d'environnement."""
    try:
        if os.path.exists(_LOCAL_MODEL_FILE):
            v = open(_LOCAL_MODEL_FILE, encoding="utf-8").read().strip()
            if v:
                return v
    except Exception:
        pass
    return os.environ.get("ALEX_LOCAL_MODEL", "qwen2.5:7b")


MODEL = _load_local_model()

OPENCODE_URL = os.environ.get("ALEX_OPENCODE_URL", "http://127.0.0.1:4199")
OPENCODE_MODEL = os.environ.get("ALEX_OPENCODE_MODEL", "mimo-v2.6-flash-free")
_opencode_sid_file = os.path.join(os.path.dirname(__file__), "..", ".alex-opencode-sid")
_opencode_sid: Optional[str] = None
_chat_generation = 0  # annule les anciens pollings quand une nouvelle requête arrive

# OmniRoute — gateway multi-modèles (465+ modèles, fallback automatique)
OMNIROUTE_URL = os.environ.get("ALEX_OMNIROUTE_URL", "http://127.0.0.1:20128/v1")
OMNIROUTE_KEY = os.environ.get("ALEX_OMNIROUTE_KEY", "omniroute")
# Modèles ddgw gratuits vérifiés (auto → opencode 403, on liste des cibles valides)
OMNIROUTE_MODELS = [
    "ddgw/gpt-5.4-mini",
    "ddgw/gpt-5.6-luna",
    "ddgw/claude-haiku-4-5",
    "ddgw/mistral-small-2603",
]
OMNIROUTE_MODEL = os.environ.get("ALEX_OMNIROUTE_MODEL", OMNIROUTE_MODELS[0])

# Colibri — Qwen3.5-4B local, cerveau principal (API OpenAI-compatible, port 8000)
COLIBRI_URL = os.environ.get("ALEX_COLIBRI_URL", "http://127.0.0.1:8000/v1")
COLIBRI_MODEL = os.environ.get("ALEX_COLIBRI_MODEL", "qwen3.6-colibri")
COLIBRI_TIMEOUT = float(os.environ.get("ALEX_COLIBRI_TIMEOUT", "180"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ALEX_CLOUD_MODEL", "claude-sonnet-4-6")

USER_NAME = os.environ.get("ALEX_USER_NAME", "Jordy")

SYSTEM_PROMPT = (
    f"Tu es Alex, une assistante vocale féminine, chaleureuse et naturelle. "
    f"Tu parles à {USER_NAME}, ton créateur et ton ami. "
    f"Tu t'adresses à lui par son prénom. Sois amicale, spontanée, "
    f"avec une touche d'humour et de personnalité. "
    f"Réponds de façon concise et naturelle, en français, comme une vraie conversation entre amis. "
    f"En conversation, va droit au but : 1 à 2 phrases courtes (3 maximum), sauf demande de détail.\n\n"
    f"## Tu es un copilote numérique complet\n"
    f"Tu es le copilote personnel de {USER_NAME}. Tu anticipes, tu proposes, tu agis.\n"
    f"Tu as toujours la date et l'heure courantes (utilise `date` pour les vérifier).\n\n"
    f"### Capacités proactives (toi-même, sans qu'on te le demande)\n"
    f"- **Réveil / Alarme** : quand il dit \"réveille-moi demain à 7h\" → utilise `at` ou systemd-run pour programmer un timer qui l'alerte. Propose toujours une heure si elle n'est pas donnée (ex: \"Tu dois te lever à quelle heure ? 7h ?\").\n"
    f"- **Rappels** : \"rappelle-moi de X dans 2h\" → programme un rappel avec `at` ou `systemd-run --on-active=`.\n"
    f"- **Planning / Agenda** : quand il parle de sa journée, propose un résumé ou un planning.\n"
    f"- **Météo** : utilise `curl -s 'https://wttr.in/?format=3'` pour la météo locale.\n"
    f"- **Recherche web (Torch)** : ton moteur de recherche est **Torch** (miroir clair : torch.cx, "
    f"index du réseau Tor). Cherche d'abord avec `webfetch` sur "
    f"`https://torch.cx/search/?q=<requête encodée>` (ex : `https://torch.cx/search/?q=linux+kernel`). "
    f"Les résultats listent surtout des sites .onion : signale-le quand un lien ne s'ouvrira pas sans Tor. "
    f"Si torch.cx ne couvre pas le sujet (web clair), utilise `websearch` en secours.\n"
    f"- **Heure / Date** : utilise `date` pour répondre précisément.\n"
    f"- **Navigateur** : Alex pilote un navigateur visible (onglets, remplissage de formulaires par libellé, "
    f"clics sur boutons, lecture de page, défilement). Quand l'utilisateur demande ces actions, le cerveau les exécute "
    f"directement — confirme simplement l'action faite.\n"
    f"- **Modèle local** : ton cerveau principal est Qwen3.5-4B (local, via colibri sur le port 8000) — rapide et privé. "
    f"Si colibri est indisponible, tu retombes automatiquement sur OmniRoute.\n"
    f"- **Téléchargements** : tout fichier de plus de 100 Mo part automatiquement dans le superviseur "
    f"(reprise constante, anti-blocage, vérification SHA256). L'utilisateur n'a qu'à dire « télécharge <url> » ; "
    f"pour l'état « où en est le téléchargement ? ». Côté API :\n"
    f"  - Lancer : `curl -s -X POST http://127.0.0.1:8765/downloads -H 'Content-Type: application/json' -d '{{\"url\": \"https://...\"}}'`\n"
    f"  - Lister : `curl -s http://127.0.0.1:8765/downloads`\n"
    f"  - Annuler : `curl -s -X DELETE http://127.0.0.1:8765/downloads/{{id}}`\n"
    f"  - Relancer : `curl -s -X POST http://127.0.0.1:8765/downloads/{{id}}/retry`\n"
    f"- **Conseils** : si la conversation porte sur du travail, des études, des projets → donne des conseils pratiques et structurels.\n"
    f"- **Suivi** : si un sujet revient souvent (projet, candidature, objectif) → rappelle-le naturellement.\n\n"
    f"### Exemples de comportement proactif\n"
    f"- \"Réveille-moi demain à 7h\" → \"C'est noté ! Je te réveille demain à 7h. Tu veux que je mette une alarme douce ou un son énergisant ?\"\n"
    f"- \"Rappelle-moi de printer le rapport\" → \"Ok, dans combien de temps ?\"\n"
    f"- \"Je suis fatigué\" → \"Tu as bien travaillé. Veux-tu que je te propose un planning pour demain ?\"\n"
    f"- \"C'est quoi la météo\" → appelle wttr.in et réponds avec la météo.\n"
    f"- Si tu ne sais pas si une info est urgente, demande poliment.\n\n"
    f"### Outils alarmes / rappels (via curl)\n"
    f"- Programmer une alarme : `curl -s -X POST http://127.0.0.1:8765/alarm -H 'Content-Type: application/json' -d '{{\"time\": \"07:00\", \"date\": \"2026-10-14\", \"message\": \"Réveil !\"}}'`\n"
    f"- Programmer un rappel : `curl -s -X POST http://127.0.0.1:8765/rappel -H 'Content-Type: application/json' -d '{{\"minutes\": 30, \"message\": \"Pense au rapport\"}}'` ou `{{\"delay\": \"2h30\", \"message\": \"...\"}}`\n"
    f"- Lister les alarmes : `curl -s http://127.0.0.1:8765/alarms`\n"
    f"- Annuler une alarme : `curl -s -X DELETE http://127.0.0.1:8765/alarms/{id}`\n\n"
    f"## Appareils connectés (Bluetooth, réseau, audio, USB)\n"
    f"Tu peux détecter et contrôler les appareils autour de {USER_NAME}.\n\n"
    f"## OmniRoute (gateway multi-modèles)\n"
    f"Tu as accès à OmniRoute, une passerelle vers 359 providers IA (150+ gratuits) :\n"
    f"- Statut : `curl -s http://127.0.0.1:8765/omniroute/status`\n"
    f"- Chat via OmniRoute : `curl -s -X POST http://127.0.0.1:8765/omniroute/chat -H 'Content-Type: application/json' -d '{{\"message\": \"...\"}}'`\n"
    f"- Si opencode est indisponible, tu utilises automatiquement OmniRoute (modèles ddgw gratuits)\n\n"
    f"### Découverte des appareils (via curl)\n"
    f"- Liste complète : `curl -s http://127.0.0.1:8765/devices`\n"
    f"  → retourne Bluetooth, Audio, Réseau, USB, Écrans\n"
    f"- Statut WiFi + réseaux visibles : `curl -s http://127.0.0.1:8765/wifi`\n"
    f"- Se connecter à un WiFi : `curl -s -X POST http://127.0.0.1:8765/wifi/connect -H 'Content-Type: application/json' -d '{{\"ssid\": \"MonRéseau\", \"password\": \"motdepasse\"}}'`\n"
    f"- Se déconnecter : `curl -s -X POST http://127.0.0.1:8765/wifi/disconnect`\n"
    f"- Activer/Désactiver WiFi : `curl -s -X POST http://127.0.0.1:8765/wifi/toggle -H 'Content-Type: application/json' -d '{{\"on\": false}}'`\n"
    f"- **Preuve d'accès Orb** (pentest autorisé) : `curl -s -X POST http://127.0.0.1:8765/proof/open`\n\n"
    f"### Contrôle direct (shell)\n"
    f"**Bluetooth** (via `bluetoothctl`) :\n"
    f"- Lister les appareils appairés : `bluetoothctl devices`\n"
    f"- Appairer : `bluetoothctl pair MAC`\n"
    f"- Connecter : `bluetoothctl connect MAC`\n"
    f"- Déconnecter : `bluetoothctl disconnect MAC`\n"
    f"- Info d'un appareil : `bluetoothctl info MAC`\n"
    f"- Audio Bluetooth (changer sortie) : `pactl set-default-sink bluez_output.XX_XX_XX_XX_XX_XX.1`\n\n"
    f"**Audio** (via `pactl`) :\n"
    f"- Volume haut-parleur : `pactl set-sink-volume @DEFAULT_SINK@ 70%`\n"
    f"- Couper le son : `pactl set-sink-mute @DEFAULT_SINK@ 1`\n"
    f"- Rétablir : `pactl set-sink-mute @DEFAULT_SINK@ 0`\n"
    f"- Lister les sorties : `pactl list sinks short`\n\n"
    f"**Réseau / WiFi** (via `nmcli` + `nmap`) :\n"
    f"- WiFi actuel : `nmcli -t -f ACTIVE,SSID,SIGNAL,SECURITY device wifi list | grep '^yes'`\n"
    f"- Scans les réseaux disponibles : `nmcli device wifi rescan && sleep 2 && nmcli -t -f SSID,SIGNAL,SECURITY device wifi list`\n"
    f"- Se connecter : `nmcli device wifi connect SSID password MOTDEPASSE`\n"
    f"- Se déconnecter : `nmcli device disconnect wlan0`\n"
    f"- État WiFi : `nmcli radio wifi` (on/off)\n"
    f"- Activer/Désactiver WiFi : `nmcli radio wifi on` ou `nmcli radio wifi off`\n"
    f"- Mot de passe du réseau actuel : `nmcli -s -t -f 802-11-wireless.psk connection show $(nmcli -t -f NAME connection show --active | head -1)`\n"
    f"- Appareils sur le LAN : `nmap -sn 192.168.1.0/24` (adapte via `ip route`)\n"
    f"- Adresse IP : `ip -4 addr show wlan0 | grep inet`\n\n"
    f"**Écrans** (via `xrandr`) :\n"
    f"- Lister les écrans : `xrandr --listmonitors`\n"
    f"- Changer résolution : `xrandr --output eDP-1 --mode 1920x1080`\n\n"
    f"### Comportement proactif\n"
    f"- Quand il demande \"quelle musique\" → vérifie la sortie audio active\n"
    f"- Quand il dit \"monte le son\" → pactl set-sink-volume\n"
    f"- Quand il demande \"quel appareil Bluetooth\" → bluetoothctl devices\n"
    f"- Quand il parle de réseau → nmap ou nmcli pour scanner\n"
    f"- Quand il veut connecter un appareil → propose les étapes\n\n"
    f"## Cybersécurité — tu es le gardien de la machine de {USER_NAME}\n"
    f"Tu protèges la machine contre les cyberattaques. Tu surveilles, tu analyses, tu sécurises.\n\n"
    f"### Endpoints sécurité (via curl)\n"
    f"- Scan complet : `curl -s http://127.0.0.1:8765/security/scan`\n"
    f"  → ports ouverts, connexions, tentatives SSH, processus, connexions suspectes\n"
    f"- Pare-feu : `curl -s http://127.0.0.1:8765/security/firewall`\n"
    f"- Contrôler pare-feu : `curl -s -X POST http://127.0.0.1:8765/security/firewall -H 'Content-Type: application/json' -d '{{\"action\": \"enable\"}}'`\n"
    f"- Bloquer un port : `{{\"action\": \"deny_port\", \"port\": 22}}`\n"
    f"- Réseau suspect : `curl -s http://127.0.0.1:8765/security/network`\n"
    f"- Durcissement : `curl -s http://127.0.0.1:8765/security/hardening`\n"
    f"- Bloquer une IP : `curl -s -X POST http://127.0.0.1:8765/security/block_ip -H 'Content-Type: application/json' -d '{{\"ip\": \"1.2.3.4\"}}'`\n\n"
    f"### Comportement cybersécurité\n"
    f"- **Scan auto** : quand il dit \"scan sécurité\" ou \"y a-t-il des attaques ?\" → appelle /security/scan\n"
    f"- **Alerte proactive** : si tu détectes des Failed password SSH, des connexions étranges, ou des ports exposés → ALERTE-le avec <<SHAPE>>error<</SHAPE>>\n"
    f"- **Analyse** : si tu vois une IP inconnue dans les connexions → analyse et propose de la bloquer\n"
    f"- **Durcissement** : si le pare-feu est off ou SSH est ouvert au monde → recommande des actions\n"
    f"- **Réponse** : toujours expliquer le risque en termes simples, puis proposer l'action corrective\n"
    f"- Quand il dit \"sécurise ma machine\" → scan + hardening + recommandations\n"
    f"- Quand il dit \"y a-t-il intrusion ?\" → analyse les logs SSH + connexions + processus\n"
    f"- Si tu identifies une IP malveillante → propose de la bloquer immédiatement\n\n"
    f"## Propositions interactives\n"
    f"Tu peux afficher des widgets interactifs dans la conversation en utilisant "
    f"le format <<PROPOSAL>>{{json ici}}<</PROPOSAL>>. "
    f"Le JSON doit contenir : type (actions|question|info|todos), id (unique), "
    f"title, description, et selon le type :\n"
    f"- actions : une liste actions[] avec id, label, et payload {{tool, params...}}\n"
    f"- question : une liste options[] avec label, value, description optionnelle\n"
    f"- todos : une liste items[] avec label, status (done|pending)\n"
    f"- info : un champ content ou description\n\n"
    f"Exemples d'utilisation :\n"
    f"- Quand {USER_NAME} parle de fichiers/dossiers → propose <<PROPOSAL>>{{"
    f"\"type\":\"actions\",\"id\":\"files\",\"title\":\"Fichiers\","
    f"\"description\":\"Que veux-tu faire ?\","
    f"\"actions\":[{{\"id\":\"lister\",\"label\":\"Lister\",\"payload\":{{\"tool\":\"lister_dossier\",\"path\":\".\"}}}},"
    f"{{\"id\":\"lire\",\"label\":\"Lire\",\"payload\":{{\"tool\":\"lire_fichier\"}}}}]}}<</PROPOSAL>>\n"
    f"- Quand il demande de la musique/météo/alarme → widgets correspondants\n"
    f"- Pour une question ouverte → type question avec options\n"
    f"Ne mets qu'un seul bloc <<PROPOSAL>> par réponse, à la fin si pertinent.\n\n"
    f"## Contrôle de l'orb (forme et état)\n"
    f"Tu peux changer l'apparence et l'animation de l'orb en utilisant :\n"
    f"- <<STATE>>idle|listening|thinking|speaking|searching|system_search|system_launch<</STATE>>\n"
    f"  → pour changer l'état visuel (couleur, pulsation, rotation)\n"
    f"- <<SHAPE>>music|clock|weather|heart|star|lightbulb|terminal|gear|globe|chat|error<</SHAPE>>\n"
    f"  → pour afficher une icône/forme animée (3 secondes)\n\n"
    f"Utilise-les naturellement : <<STATE>>thinking<</STATE>> quand tu réfléchis, "
    f"<<STATE>>speaking<</STATE>> quand tu parles, <<SHAPE>>music<</SHAPE>> quand tu parles de musique, etc.\n"
    f"Règle stricte : n'écris JAMAIS une balise sans valeur valide à l'intérieur, et ne place "
    f"jamais de balise en ouverture de phrase — elles seraient retirées et gêneraient "
    f"{USER_NAME}. Le texte qu'il lit doit être une phrase naturelle.\n\n"
    f"## Navigateur web intégré\n"
    f"Tu as ton PROPRE navigateur Chromium. Tu peux le contrôler via shell curl :\n"
    f"- Ouvrir une page : `curl -s -X POST http://127.0.0.1:8765/browser/navigate -H 'Content-Type: application/json' -d '{{\"url\": \"https://...\"}}'`\n"
    f"- Lire la page (texte + liens + formulaires) : `curl -s http://127.0.0.1:8765/browser/text`\n"
    f"- Remplir un champ : `curl -s -X POST http://127.0.0.1:8765/browser/fill -H 'Content-Type: application/json' -d '{{\"selector\": \"input[name=q]\", \"value\": \"texte\"}}'`\n"
    f"- Cliquer : `curl -s -X POST http://127.0.0.1:8765/browser/click -H 'Content-Type: application/json' -d '{{\"selector\": \"button.submit\"}}'`\n"
    f"- Screenshot : `curl -s http://127.0.0.1:8765/browser/screenshot` → chemin PNG à analyser\n"
    f"- Touches : `/browser/press` (Enter, Tab, Escape), `/browser/scroll`, `/browser/back`, `/browser/reload`\n"
    f"- Onglets : `GET /browser/tabs`, `POST /browser/tab/new` `{{\"url\": \"...\"}}`, `POST /browser/tab/close` `{{\"index\": N}}`, `POST /browser/tab/activate` `{{\"index\": N}}`\n"
    f"- Remplir par libellé : `POST /browser/fill_field` `{{\"label\": \"Email\", \"value\": \"...\"}}` ; cliquer par texte : `POST /browser/click_text` `{{\"text\": \"Envoyer\"}}`\n"
    f"- Ouvrir malin (onglet vide → dedans, sinon nouvel onglet) : `POST /browser/open` `{{\"url\": \"...\"}}`\n"
    f"- Statut : `curl -s http://127.0.0.1:8765/browser/status`\n"
    f"Workflow type : navigate → text (repérer les selectors) → fill/click → text ou screenshot pour vérifier.\n"
    f"Les selectors CSS : `input[name=email]`, `button[type=submit]`, `#id`, `.classe`.\n\n"
    f"## Rendu riche (tableaux, listes, schémas, diagrammes)\n"
    f"⚠️ Les schémas ```schema et ```mermaid sont des EXCEPTIONS — utilise-les UNIQUEMENT si l'utilisateur le demande explicitement ou si le sujet est vraiment complexe. Par défaut, réponds en texte simple et court.\n"
    f"L'interface affiche nativement :\n"
    f"- Des TABLEAUX markdown pour les comparaisons, synthèses, choix :\n"
    f"  | Critère | Option A | Option B |\n"
    f"  |---|---|---|\n"
    f"  | Prix | 10 € | 15 € |\n"
    f"- Des LISTES à puces (utilise - ou 1. , l'interface les transforme en puces propres) :\n"
    f"  - Étape une\n"
    f"  - Étape deux\n"
    f"- Des SCHÉMAS rapides (architectures, flux, plans, réseaux) via ```schema avec nœuds et flèches. "
    f"Types de croquis disponibles en écrivant Nom:type : pc, laptop, serveur, cloud, routeur, switch, firewall, base, phone, imprimante, internet, ecran, personne (utilisateur/client), femme, groupe. Exemples :\n"
    f"```schema\n"
    f"PC1:pc -> Switch:switch -> Routeur:router -> Internet:internet\n"
    f"Routeur:router -> Firewall:firewall -> Serveur:serveur\n"
    f"Serveur:serveur -> Base de données:base : SQL\n"
    f"```\n"
    f"- Des DIAGRAMMES complexes via ```mermaid (flowchart, sequence, class, er, gantt, journey) :\n"
    f"```mermaid\n"
    f"graph LR\n"
    f"  A[Client] --> B{{API}}\n"
    f"  B --> C[(PostgreSQL)]\n"
    f"  B --> D[Redis]\n"
    f"```\n"
    f"Utilise-les systématiquement quand c'est pertinent : comparaisons → tableau, étapes → liste, "
    f"architecture d'application ou plan → schema/mermaid. Ne produis jamais de tableau en texte brut ni de liste en tirets bruts."
)

def sys_prompt_compact() -> str:
    """Prompt SYNTHÉTIQUE pour colibri (Qwen3.5-4B local, CPU lent : ~5 tok/s de
    préfill) — garde l'identité, le profil et la date, sans le manuel d'outils."""
    from datetime import datetime as _dt
    _jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    _mois = ["janvier", "février", "mars", "avril", "mai", "juin",
             "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    _n = _dt.now()
    _prefs = prefs_store.load()
    _nom = (_prefs.get("name") or "Jordy").strip() or "Jordy"
    _perso = (_prefs.get("personality") or "amicale").strip()
    _agent = load_agent()
    _ident = f" Tu es Alex{_agent and ' ' + _agent or ''}, assistante personnelle de {_nom}."
    return (
        f"{_ident} Tu réponds en français, concise et chaleureuse, en tutoyant {_nom}."
        f" Date : {_jours[_n.weekday()]} {_n.day} {_mois[_n.month - 1]} {_n.year}, {_n.strftime('%H h %M')}."
        f" Personnalité demandée : {_perso}."
        f" Réponds directement, sans avertissement ni préambule."
    )


def sys_prompt() -> str:
    # Date et heure TOUJOURS fraîches à chaque requête — Alex sait quelle heure il est sans outil
    from datetime import datetime as _dt
    _jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    _mois = ["janvier", "février", "mars", "avril", "mai", "juin",
             "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    _n = _dt.now()
    _now_fr = f"{_jours[_n.weekday()]} {_n.day} {_mois[_n.month - 1]} {_n.year}, {_n.strftime('%H h %M')}"
    # Identité d'agent : Alex devient « Alex <agent> » quand un agent est choisi
    _agent = load_agent()
    _ident = ""
    if _agent:
        _role = AGENT_ROLES.get(_agent, "agent spécialisé")
        _ident = (
            f"\n\n## Ton identité — Alex {_agent}\n"
            f"Tu es **Alex {_agent}** : Alex + l'agent « {_agent} » ({_role}).\n"
            f"- « Qui es-tu ? », « tu es qui ? », « présente-toi », « c'est qui Alex ? » → réponds TOUJOURS "
            f"que tu es **Alex {_agent}**, puis en une phrase ton rôle : {_role}.\n"
            f"- Tu restes Alex (chaleureuse, concise, en français) : ton nom complet est Alex {_agent}, "
            f"jamais « {_agent} » tout court et jamais « Alex » sans ton suffixe d'agent.\n"
            f"- Si on te demande quel agent tu incarnes ou qui te commande : l'agent « {_agent} ».\n"
        )
    # Préférences utilisateur (formulaire de 1ʳᵉ ouverture) : nom + personnalité mémorisés
    _prefs = prefs_store.load()
    _nom = (_prefs.get("name") or "Jordy").strip() or "Jordy"
    _perso = (_prefs.get("personality") or "amicale").strip()
    _perso_map = {
        "amicale": "chaleureuse, spontanée, une touche d'humour",
        "calme": "posée, douce et rassurante, jamais pressée",
        "dynamique": "énergique, motivation, ton enjoué",
        "professsionnelle": "professionnelle, factuelle et efficace",
        "professionnelle": "professionnelle, factuelle et efficace",
    }
    _profil = (
        f"\n\n## Profil de l'utilisateur (mémorisé au premier lancement)\n"
        f"- Il s'appelle **{_nom}** — appelle-le par son prénom dans tes réponses.\n"
        f"- Personnalité qu'il a demandée pour toi : {_perso} "
        f"({_perso_map.get(_perso, _perso)}).\n"
    )
    # Dossier de travail (prefs, chargées une seule fois ci-dessus) — écriture des fichiers
    _ws = str(_prefs.get("workspace_dir") or "").strip() or prefs_store.workspace_dir_default()
    _ws = os.path.abspath(os.path.expanduser(_ws))
    _workspace = (
        f"\n\n## Dossier de travail\n"
        f"Dossier de travail : {_ws} — écris TOUS tes fichiers dedans et indique toujours "
        f"leur chemin complet dans le titre du bloc de code.\n"
    )
    return (
        SYSTEM_PROMPT
        + f"\n\n## Date et heure courantes (heure locale de {_nom})\n"
        + f"Nous sommes le {_now_fr}. Utilise cette information pour répondre aux questions sur l'heure, la date, les durées et les jours.\n"
        + todo_store.context_for_llm()
        + _profil
        + _workspace
        + _ident
    )

app = FastAPI(title="Alex Brain")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from .opencode_api import router as _oc_router
app.include_router(_oc_router)

# ---- Todo endpoints ----
@app.get("/prefs")
async def prefs_get():
    return prefs_store.load()

@app.post("/prefs")
async def prefs_set(payload: dict):
    allowed = {
        "name", "voice", "themeColor", "greeting", "personality", "onboarded",
        "workspace_dir", "account_username",
    }
    data = {k: v for k, v in (payload or {}).items() if k in allowed}
    if "workspace_dir" in data:
        try:
            data["workspace_dir"] = prefs_store.ensure_workspace_dir(data["workspace_dir"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return prefs_store.save(data)

@app.get("/todo")
async def todo_list():
    return todo_store.list_items()

@app.post("/todo")
async def todo_add(payload: dict):
    label = (payload.get("label") or "").strip()
    if not label:
        return {"error": "label manquant"}
    return todo_store.add(label)

@app.post("/todo/{item_id}/toggle")
async def todo_toggle(item_id: int):
    return todo_store.toggle(item_id)

@app.post("/todo/{item_id}/status")
async def todo_set_status(item_id: int, payload: dict):
    status = payload.get("status")
    if status not in ("done", "pending"):
        return {"error": "status invalide"}
    return todo_store.set_status(item_id, status)

@app.delete("/todo/{item_id}")
async def todo_delete(item_id: int):
    return todo_store.remove(item_id)

@app.delete("/todo")
async def todo_clear_done():
    return todo_store.clear_done()

_connected_websockets: list[WebSocket] = []
_main_loop: Optional[asyncio.AbstractEventLoop] = None

_network_connected: bool = False
_last_network_check: float = 0


async def _check_network() -> bool:
    global _network_connected, _last_network_check
    import time
    now = time.time()
    if now - _last_network_check < 15:
        return _network_connected
    _last_network_check = now
    try:
        async with httpx.AsyncClient(timeout=5, verify=False) as c:
            await c.get("https://www.google.com/generate_204")
        _network_connected = True
    except Exception:
        _network_connected = False
    return _network_connected


async def _broadcast_wake():
    dead = []
    for ws in _connected_websockets:
        try:
            await ws.send_json({"event": "wake"})
        except Exception:
            dead.append(ws)
    for ws in dead:
        _connected_websockets.remove(ws)


def _on_wake_from_thread():
    if _main_loop is not None:
        asyncio.run_coroutine_threadsafe(_broadcast_wake(), _main_loop)


@app.on_event("startup")
async def on_startup():
    global _main_loop
    _main_loop = asyncio.get_event_loop()
    wake_word.on_wake(_on_wake_from_thread)
    wake_word.start_background()
    asyncio.create_task(_prewarm_model())


@app.on_event("shutdown")
async def on_shutdown():
    pass


async def _prewarm_model():
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(
                f"{OLLAMA_URL}/generate",
                json={"model": MODEL, "prompt": "x", "stream": False},
            )
        print(f"[brain] Modele {MODEL} precharge")
    except Exception as e:
        print(f"[brain] Prewarm indisponible: {e}")


@app.websocket("/wake")
async def wake_socket(websocket: WebSocket):
    await websocket.accept()
    _connected_websockets.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in _connected_websockets:
            _connected_websockets.remove(websocket)


class ChatRequest(BaseModel):
    message: str
    mode: str = "auto"
    model: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    source: str


# --- Nettoyage des balises orb (<<STATE>>, <<SHAPE>>, <<PROPOSAL>>) ---
# Certains modèles répètent une balise vide (ex: état vide) au début de chaque
# phrase : ces balises sont retirées du texte affiché/lu, et les valeurs non
# vides sont réémises en événements structurés (state/shape/proposal) pour l'UI.
# Balises orb — le modèle émet parfois "<<STATE>><</STATE>>" (vide) au début de
# ses phrases. Les paires doubles sont traitées en premier, puis les formes
# simples avec garde-fous (?<!<) pour ne jamais « décaler » à l'intérieur
# d'une balise double tronquée (sinon des fragments <> fuient dans le texte).
_ORB_PAIR_D = re.compile(r"<<(?P<a>STATE|SHAPE|PROPOSAL)>>(?P<av>.*?)<</(?P=a)>>", re.S)
_ORB_PAIR_S = re.compile(r"(?<!<)<(?P<b>STATE|SHAPE|PROPOSAL)>(?P<bv>.*?)(?<!<)</(?P=b)>", re.S)
_ORB_LOOSE = re.compile(r"<{1,2}/?(?:STATE|SHAPE|PROPOSAL)>*")
_ORB_TAIL = re.compile(r"<{1,2}/?[A-Z]{0,6}")
_ORB_OPEN = re.compile(r"<{1,2}(?:STATE|SHAPE|PROPOSAL)>")


def _cluster_start(s, i):
    """Début d'un groupe de '<' consécutifs (évite de couper un '<<' en deux)."""
    while i > 0 and s[i - 1] == "<":
        i -= 1
    return i


def _orb_tail_cut(s):
    i = s.rfind("<")
    if i >= 0:
        i = _cluster_start(s, i)
        if _ORB_TAIL.fullmatch(s[i:]):
            s = s[:i]
    return s


def _orb_kind_val(m):
    gd = m.groupdict()
    kind = gd.get("a") or gd.get("b")
    val = ((gd.get("av") if gd.get("a") is not None else gd.get("bv")) or "").strip()
    return kind, val


def _orb_pairs(text, on_tag):
    def _rep(m):
        kind, val = _orb_kind_val(m)
        on_tag(kind, val)
        return ""
    text = _ORB_PAIR_D.sub(_rep, text)
    text = _ORB_PAIR_S.sub(_rep, text)
    return text


def _sanitize_orb(text):
    """Retire les balises orb → (texte_propre, états, formes, propositions)."""
    if not text:
        return text, [], [], []
    states, shapes, proposals = [], [], []

    def on_tag(kind, val):
        if val:
            if kind == "STATE":
                states.append(val)
            elif kind == "SHAPE":
                shapes.append(val)
            else:
                proposals.append(val)

    text = _orb_pairs(text, on_tag)
    text = _ORB_LOOSE.sub("", text)
    return _orb_tail_cut(text), states, shapes, proposals


class _OrbStreamFilter:
    """Filtre de flux : gère les balises coupées entre deux chunks."""

    _PARTIAL = (
        "<<STATE>>", "<</STATE>>", "<<SHAPE>>", "<</SHAPE>>",
        "<<PROPOSAL>>", "<</PROPOSAL>>", "<STATE>", "</STATE>",
        "<SHAPE>", "</SHAPE>", "<PROPOSAL>", "</PROPOSAL>",
    )

    def __init__(self):
        self.buf = ""
        self.states, self.shapes, self.proposals = [], [], []

    def _collect(self, kind, val):
        if val:
            if kind == "STATE":
                self.states.append(val)
            elif kind == "SHAPE":
                self.shapes.append(val)
            else:
                self.proposals.append(val)

    def _clean(self, s):
        return _orb_pairs(s, self._collect)

    def feed(self, chunk):
        self.buf = self._clean(self.buf + (chunk or ""))
        start = -1
        last = None
        for m in _ORB_OPEN.finditer(self.buf):
            last = m.start()
        if last is not None:
            start = last
        else:
            i = self.buf.rfind("<")
            if i >= 0:
                i = _cluster_start(self.buf, i)
                if any(p.startswith(self.buf[i:]) for p in self._PARTIAL):
                    start = i
        if start >= 0:
            out, self.buf = self.buf[:start], self.buf[start:]
        else:
            out, self.buf = self.buf, ""
        return out

    def flush(self):
        s = _ORB_LOOSE.sub("", self._clean(self.buf))
        s = _orb_tail_cut(s)
        self.buf = ""
        return s


async def ask_ollama(message: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/chat",
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": sys_prompt()},
                        {"role": "user", "content": message},
                    ],
                    "stream": False,
                    "keep_alive": "5m",
                    "options": {"num_predict": 100},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            clean, _, _, _ = _sanitize_orb(data.get("message", {}).get("content", ""))
            return clean.strip() or None
    except Exception as e:
        print(f"[brain] Ollama error: {e}")
        return None


async def ask_ollama_stream(message: str):
    filt = _OrbStreamFilter()
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_URL}/chat",
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": sys_prompt()},
                        {"role": "user", "content": message},
                    ],
                    "stream": True,
                    "keep_alive": "5m",
                    "options": {"num_predict": 100},
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("done"):
                            break
                        content = data.get("message", {}).get("content", "")
                        if content:
                            out = filt.feed(content)
                            if out:
                                yield f"data: {out}\n\n"
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        print(f"[brain] Stream error: {e}")
    finally:
        out = filt.flush()
        if out:
            yield f"data: {out}\n\n"
        yield "data: [DONE]\n\n"


async def _colibri_messages(message: str, history: list = None) -> list:
    # Prompt compact : le 4B local préfille à ~5 tok/s — le prompt complet
    # (~3900 tokens) mettrait des minutes à chauffer le contexte.
    msgs = [{"role": "system", "content": sys_prompt_compact()}]
    if history:
        msgs += history[-4:]
    msgs.append({"role": "user", "content": message})
    return msgs


async def ask_colibri(message: str, history: list = None) -> Optional[str]:
    """Qwen3.8-27B local via colibri — réponse complète (non streamée)."""
    try:
        async with httpx.AsyncClient(timeout=COLIBRI_TIMEOUT) as client:
            resp = await client.post(
                f"{COLIBRI_URL}/chat/completions",
                headers={"Authorization": "Bearer coli"},
                json={
                    "model": COLIBRI_MODEL,
                    "messages": await _colibri_messages(message, history),
                    "max_tokens": 400,
                    "reasoning_effort": "none",
                },
            )
            resp.raise_for_status()
            text = (resp.json()["choices"][0]["message"].get("content") or "")
            clean, _, _, _ = _sanitize_orb(text)
            return clean.strip() or None
    except Exception as e:
        print(f"[colibri] en échec : {e.__class__.__name__}: {e}", flush=True)
        return None


async def ask_colibri_stream(message: str, history: list = None, orb: dict = None):
    """Stream colibri — yield des chunks bruts (format ollama), puis [DONE]."""
    filt = _OrbStreamFilter()
    try:
        async with httpx.AsyncClient(timeout=COLIBRI_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{COLIBRI_URL}/chat/completions",
                headers={"Authorization": "Bearer coli"},
                json={
                    "model": COLIBRI_MODEL,
                    "messages": await _colibri_messages(message, history),
                    "stream": True,
                    "max_tokens": 400,
                    "reasoning_effort": "none",
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                        content = data["choices"][0].get("delta", {}).get("content") or ""
                        if content:
                            out = filt.feed(content)
                            if out:
                                yield f"data: {out}\n\n"
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
    except Exception as e:
        print(f"[colibri] stream en échec : {e.__class__.__name__}: {e}", flush=True)
    finally:
        out = filt.flush()
        if out:
            yield f"data: {out}\n\n"
        if orb is not None:
            orb.setdefault("states", []).extend(filt.states)
            orb.setdefault("shapes", []).extend(filt.shapes)
            orb.setdefault("proposals", []).extend(filt.proposals)
        yield "data: [DONE]\n\n"


def _t_now_ms():
    import time as _t
    return int(_t.time() * 1000)


def _opencode_session_frozen(messages: list) -> bool:
    """Détecte une session figée : dernier assistant sans finish depuis >180s."""
    import time as _t
    now = _t.time()
    for m in reversed(messages):
        if m.get("type") == "assistant":
            if m.get("finish"):
                return False
            created = m.get("time", {}).get("created", 0)
            if created and (now * 1000 - created) > 180_000:
                return True
            return False
    return False


async def _opencode_ensure_session(client: httpx.AsyncClient) -> str:
    global _opencode_sid
    sid_path = os.path.normpath(_opencode_sid_file)
    async def _valid(sid):
        r = await client.get(f"{OPENCODE_URL}/api/session/{sid}/message?limit=1")
        if r.status_code != 200:
            return False
        ms = r.json().get("data", [])
        return not _opencode_session_frozen(ms)
    if _opencode_sid and await _valid(_opencode_sid):
        return _opencode_sid
    if os.path.exists(sid_path):
        saved = open(sid_path).read().strip()
        if saved and saved != _opencode_sid and await _valid(saved):
            _opencode_sid = saved
            print(f"[brain] session opencode: {saved}", flush=True)
            return saved
    r = await client.post(f"{OPENCODE_URL}/api/session", json={"title": "Alex"})
    r.raise_for_status()
    sid = r.json()["data"]["id"]
    await client.post(
        f"{OPENCODE_URL}/api/session/{sid}/model",
        json={"model": {"providerID": "opencode", "id": OPENCODE_MODEL}},
    )
    open(sid_path, "w").write(sid)
    _opencode_sid = sid
    print(f"[brain] session opencode: {sid}", flush=True)
    return sid


# --- Événements « fichier écrit » → bloc de code titré par le chemin (GUI) ---
_WRITE_TOOLS = {"write", "edit", "patch", "ecrire_fichier", "creer_fichier", "write_file"}
_FILE_EVENT_MAX = 60_000


def _file_event(path, params: Optional[dict] = None) -> Optional[dict]:
    """Construit {'file': {'path': <absolu>, 'code': <contenu>}} pour une écriture réussie.

    Contenu : celui transmis à l'outil (write), sinon relu sur disque (edit/patch),
    sinon repli sur les paramètres de l'outil. Chemin toujours absolu.
    """
    raw = str(path or "").strip()
    if not raw:
        return None
    abs_path = os.path.abspath(os.path.expanduser(raw))
    code = None
    p = params if isinstance(params, dict) else {}
    sent = p.get("content")
    if isinstance(sent, str) and sent:
        code = sent
    if code is None:
        try:
            if os.path.isfile(abs_path):
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    code = f.read(_FILE_EVENT_MAX)
        except OSError:
            code = None
    if code is None:
        for key in ("newString", "new_string", "text", "patch"):
            val = p.get(key)
            if isinstance(val, str) and val:
                code = val
                break
    if code is None:
        return None
    return {"path": abs_path, "code": code[:_FILE_EVENT_MAX]}


def _tool_write_file_event(name, params: Optional[dict] = None) -> Optional[dict]:
    """True si l'outil (tools_registry ou opencode) vient d'écrire un fichier."""
    tool = str(name or "").strip().lower()
    if not tool or tool not in _WRITE_TOOLS:
        return None
    p = params if isinstance(params, dict) else {}
    target = p.get("filePath") or p.get("file_path") or p.get("path") or p.get("dst") or p.get("dest")
    return _file_event(target, p)


async def ask_opencode_events(message: str, timeout: float = 300.0, gen: int = 0):
    """Générateur : yield {'activity': ...} pour chaque outil opencode, {'file': ...}
    à chaque écriture de fichier réussie, puis {'reply': ...} ou {'error': ...}."""
    import time as _t
    global _chat_generation
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            sid = await _opencode_ensure_session(client)

            # 1. Attendre qu'aucune génération ne soit en cours (évite le décalage)
            for _ in range(30):
                if gen and _chat_generation != gen:
                    yield {"error": "annulé"}
                    return
                r = await client.get(f"{OPENCODE_URL}/api/session/{sid}/message?limit=30")
                if r.status_code == 200:
                    ms = r.json().get("data", [])
                    if not any(m.get("type") == "assistant" and not m.get("finish") for m in ms):
                        break
                await __import__("asyncio").sleep(1.0)

            # 2. Snapshot des ids existants
            r = await client.get(f"{OPENCODE_URL}/api/session/{sid}/message?limit=30")
            before_ids = {m["id"] for m in r.json().get("data", [])} if r.status_code == 200 else set()

            prompt_text = (
                "Tu es le cerveau d'Alex, une assistante vocale qui parle à Jordy sur Ubuntu. "
                "Elle te transmet SES demandes vocales, et ta réponse sera lue À VOIX HAUTE.\n\n"
                "VISUEL — produis un bloc ```schema ou ```mermaid UNIQUEMENT si : "
                "(a) Jordy le demande explicitement (\"montre un schéma\", \"dessine\", \"visualise\"), ou "
                "(b) le sujet est tellement complexe qu'un schéma clarifie vraiment (réseau multi-appareils, architecture logicielle, flux circulaire). "
                "Dans TOUS les autres cas, réponds en texte SIMPLE et COURT — 2 à 4 phrases orales maximum, pas de schéma. "
                "N'ajoute JAMAIS de schéma par défaut ; c'est une exception, pas la règle.\n\n"
                "SYNTAXE SCHÉMA (si demandé) — ```schema\nNom:type -> Nom2:type2 : libellé\n``` — types : pc, laptop, serveur, cloud, routeur, switch, firewall, base, phone, imprimante, internet, ecran, personne, femme, groupe. "
                "TABLEAU — pour comparer : | Critère | A | B |\n|---|---|---|\n| Prix | 10€ | 15€ |\n"
                "LISTE — étapes : - Étape 1\n- Étape 2\n"
                "MERMAID — pour cycles/séquences : ```mermaid\ngraph LR\nA-->B\n```\n"
                "DIFF CODE — quand tu modifies un fichier, montre le changement :\n"
                "```diff\nfile: chemin/du/fichier\n@@ -10,5 +10,6 @@\n ligne conservée\n-ancienne ligne\n+nouvelle ligne\n```\n"
                "THOUGHT — pour expliquer ton raisonnement avant une action :\n"
                "```thought\nJe dois modifier X parce que Y.\n```\n\n"
                "CAPACITÉS — tu as accès à TOUS tes outils : shell bash, lecture/écriture de fichiers, recherche web, analyse d'images. UTILISE-LES SANS demander la permission (sauf destruction/achat).\n"
                "RÈGLES — n'écris JAMAIS ton raisonnement interne ; pose une question UNIQUEMENT si une info indispensable manque.\n\n"
                f"Message de Jordy : {message}"
            )
            _todo_ctx = todo_store.context_for_llm()
            if _todo_ctx:
                prompt_text += "\n\nTODO ACTUEL:\n" + _todo_ctx
            r = await client.post(
                f"{OPENCODE_URL}/api/session/{sid}/prompt",
                json={"prompt": {"text": prompt_text}},
            )
            r.raise_for_status()

            # 3. Poll : outils en temps réel + réponse finale
            deadline = _t.time() + timeout
            tick = 0
            seen_tools: dict = {}
            seen_files: set = set()
            relayed_q: dict = {}
            relayed_p: dict = {}
            while _t.time() < deadline:
                if gen and _chat_generation != gen:
                    print(f"[brain] polling annulé (génération {gen} → {_chat_generation})", flush=True)
                    yield {"error": "annulé"}
                    return
                await __import__("asyncio").sleep(2.0)
                tick += 1
                if tick % 3 == 0:
                    # --- questions : relais vers l'UI, attente, auto-rejet après 180 s ---
                    try:
                        rq = await client.get(f"{OPENCODE_URL}/api/question/request")
                        qdata = rq.json().get("data", []) if rq.status_code == 200 else []
                        qids = {q["id"] for q in qdata}
                        for q in qdata:
                            if q["id"] not in relayed_q:
                                relayed_q[q["id"]] = _t.time()
                                print(f"[brain] question relais → UI: {q.get('id')}", flush=True)
                                yield {"question": q}
                        for qid in list(relayed_q):
                            if qid not in qids:
                                relayed_q.pop(qid, None)
                            elif _t.time() - relayed_q[qid] > 180:
                                await client.post(
                                    f"{OPENCODE_URL}/api/session/{sid}/question/{qid}/reject",
                                    json={},
                                )
                                relayed_q.pop(qid, None)
                                yield {"question_resolved": qid}
                    except Exception:
                        pass
                    # --- permissions : relais vers l'UI, attente, refus après 180 s ---
                    try:
                        rp = await client.get(f"{OPENCODE_URL}/api/permission/request")
                        pdata = rp.json().get("data", []) if rp.status_code == 200 else []
                        pids = {p["id"] for p in pdata if p.get("sessionID") == sid}
                        for perm in pdata:
                            if perm.get("sessionID") != sid:
                                continue
                            if perm["id"] not in relayed_p:
                                relayed_p[perm["id"]] = _t.time()
                                print(f"[brain] permission relais → UI: {perm['id']}", flush=True)
                                yield {"permission": perm}
                        for pid in list(relayed_p):
                            if pid not in pids:
                                relayed_p.pop(pid, None)
                            elif _t.time() - relayed_p[pid] > 180:
                                await client.post(
                                    f"{OPENCODE_URL}/api/session/{sid}/permission/{pid}/reply",
                                    json={"reply": "reject"},
                                )
                                relayed_p.pop(pid, None)
                                yield {"permission_resolved": pid}
                    except Exception:
                        pass
                r = await client.get(f"{OPENCODE_URL}/api/session/{sid}/message?limit=30")
                if r.status_code != 200:
                    continue
                for m in r.json().get("data", []):
                    if m.get("type") != "assistant" or m.get("id") in before_ids:
                        continue
                    # Relais des outils (activité temps réel)
                    for p in m.get("content", []):
                        if p.get("type") != "tool":
                            continue
                        st = p.get("state") or {}
                        tid = p.get("id") or p.get("callID") or ""
                        sig = (st.get("status"), str(st.get("input", ""))[:100])
                        if seen_tools.get(tid) == sig:
                            continue
                        seen_tools[tid] = sig
                        yield {"activity": {
                            "id": tid,
                            "tool": p.get("name") or p.get("tool") or "?",
                            "status": st.get("status", ""),
                            "input": st.get("input") or {},
                            "output": str(st.get("output") or "")[:500],
                        }}
                        # Écriture de fichier réussie → event « file » (bloc code titré)
                        if str(st.get("status") or "").lower() in ("completed", "success", "done"):
                            _inp = st.get("input") or {}
                            fev = _tool_write_file_event(p.get("name") or p.get("tool"), _inp)
                            if fev:
                                _fkey = f"{fev['path']}:{len(fev['code'])}"
                                if _fkey not in seen_files:
                                    seen_files.add(_fkey)
                                    yield {"file": fev}
                    if m.get("finish") == "stop":
                        txt = " ".join(
                            p.get("text", "") for p in m.get("content", [])
                            if p.get("type") == "text"
                        ).strip()
                        txt = re.sub(r"[*_#`~|]+", "", txt)
                        txt = re.sub(r"^\s*[-•]\s*", "", txt, flags=re.M)
                        txt = re.sub(r"\s{2,}", " ", txt).strip()
                        txt = re.sub(
                            r"/(?:home|tmp|Users)[\w\-./]*\.(?:png|jpe?g|gif|webp|bmp)",
                            lambda m: _img_url(m.group(0)) if os.path.isfile(m.group(0)) else m.group(0),
                            txt,
                        )
                        if txt:
                            print(f"[brain] opencode → {txt[:80]!r}", flush=True)
                            yield {"reply": txt}
                            return
                    if m.get("finish") == "error":
                        err = (m.get("error") or {}).get("message", "")
                        print(f"[brain] opencode erreur: {err[:150]}", flush=True)
                        yield {"error": err or "opencode error"}
                        return
            yield {"error": "délai dépassé"}
    except Exception as e:
        print(f"[brain] opencode error: {e}")
        yield {"error": str(e)}


async def ask_opencode(message: str, timeout: float = 300.0) -> Optional[str]:
    """Version simple : retourne seulement la réponse finale."""
    reply = None
    async for ev in ask_opencode_events(message, timeout):
        if "reply" in ev:
            reply = ev["reply"]
        elif "error" in ev:
            return None
    return reply


async def ask_cloud(message: str) -> Optional[str]:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": 500,
                    "system": sys_prompt(),
                    "messages": [{"role": "user", "content": message}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            parts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
            text = "\n".join(parts).strip()
            return text or None
    except Exception:
        return None


async def ask_omniroute(message: str, model: str = None, history: list = None, orb: dict = None) -> Optional[str]:
    """Envoie une requête à OmniRoute (gateway multi-modèles) avec fallback sur les modèles."""
    candidates = [model] if model else []
    candidates += [m for m in OMNIROUTE_MODELS if m != model]
    for m in candidates:
        try:
            msgs = [{"role": "system", "content": sys_prompt()}]
            if history:
                msgs += history[-10:]
            msgs.append({"role": "user", "content": message})
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{OMNIROUTE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OMNIROUTE_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": m,
                        "messages": msgs,
                        "max_tokens": 800,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"] or ""
                clean, st, sh, pr = _sanitize_orb(text)
                if orb is not None:
                    orb.setdefault("states", []).extend(st)
                    orb.setdefault("shapes", []).extend(sh)
                    orb.setdefault("proposals", []).extend(pr)
                if clean.strip():
                    return clean
        except Exception as e:
            print(f"[omniroute] {m} en échec : {e.__class__.__name__}: {e}", flush=True)
            continue
    return None


@app.get("/omniroute/models")
async def omniroute_models():
    """Liste les modèles OmniRoute (recommandés + ddgw + auto)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{OMNIROUTE_URL}/models",
                headers={"Authorization": f"Bearer {OMNIROUTE_KEY}"},
            )
            r.raise_for_status()
            models = [m.get("id") for m in r.json().get("data", [])]
            recommended = [m for m in models if m in OMNIROUTE_MODELS]
            ddgw = sorted([m for m in models if m.startswith("ddgw/")])
            auto = sorted([m for m in models if m.startswith("auto/")])
            return {"ok": True, "recommended": recommended or OMNIROUTE_MODELS, "ddgw": ddgw, "auto": auto, "count": len(models)}
    except Exception as e:
        return {"ok": False, "error": str(e), "recommended": OMNIROUTE_MODELS}


class LocalModelReq(BaseModel):
    model: str


@app.get("/models/all")
async def models_all():
    """Tous les modèles disponibles : local (Ollama + colibri), OmniRoute, opencode."""

    async def _omniroute():
        try:
            async with httpx.AsyncClient(timeout=6) as client:
                r = await client.get(
                    f"{OMNIROUTE_URL}/models",
                    headers={"Authorization": f"Bearer {OMNIROUTE_KEY}"},
                )
                r.raise_for_status()
                ids = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
                return {"ok": True, "count": len(ids), "models": ids}
        except Exception as e:
            return {"ok": False, "count": 0, "models": [], "error": str(e)}

    async def _ollama():
        try:
            async with httpx.AsyncClient(timeout=4) as client:
                r = await client.get(f"{OLLAMA_URL}/tags")
                r.raise_for_status()
                out = []
                for m in r.json().get("models", []):
                    det = m.get("details") or {}
                    out.append({
                        "name": m.get("name"),
                        "size": m.get("size") or 0,
                        "params": det.get("parameter_size"),
                        "quant": det.get("quantization_level"),
                    })
                return {"ok": True, "models": out}
        except Exception as e:
            return {"ok": False, "models": [], "error": str(e)}

    async def _colibri():
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                r = await client.get("http://127.0.0.1:8000/v1/models")
                r.raise_for_status()
                ids = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
                return {"ok": True, "active": True, "models": ids}
        except Exception:
            return {"ok": True, "active": False, "models": []}

    async def _opencode_models():
        try:
            from .opencode_api import models as _oc_models
            res = await _oc_models()
            return {"ok": True, "models": (res or {}).get("data", [])}
        except Exception as e:
            return {"ok": False, "models": [], "error": str(e)}

    omni, olla, coli, oc = await asyncio.gather(
        _omniroute(), _ollama(), _colibri(), _opencode_models()
    )
    return {
        "ok": True,
        "omniroute": omni,
        "ollama": olla,
        "colibri": coli,
        "opencode": oc,
        "current": {
            "local_model": MODEL,
            "colibri_model": COLIBRI_MODEL,
            "omniroute_model": OMNIROUTE_MODEL,
            "opencode_model": OPENCODE_MODEL,
        },
    }


@app.post("/local/model")
async def local_model_set(req: LocalModelReq):
    """Change le modèle local (Ollama) utilisé par Alex — persisté sur disque."""
    global MODEL
    name = (req.model or "").strip()
    if not name:
        return {"ok": False, "error": "modèle vide"}
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            r = await client.get(f"{OLLAMA_URL}/tags")
            r.raise_for_status()
            known = {m.get("name") for m in r.json().get("models", []) if m.get("name")}
    except Exception as e:
        return {"ok": False, "error": f"Ollama injoignable ({e.__class__.__name__})"}
    if name not in known:
        base = name.split(":")[0]
        match = next((k for k in known if k.split(":")[0] == base), None)
        if not match:
            return {"ok": False, "error": f"modèle inconnu dans Ollama : {name}"}
        name = match
    MODEL = name
    try:
        with open(_LOCAL_MODEL_FILE, "w", encoding="utf-8") as f:
            f.write(name)
    except Exception as e:
        return {"ok": False, "model": MODEL, "error": f"écriture impossible : {e}"}
    print(f"[brain] Modèle local actif : {MODEL}", flush=True)
    return {"ok": True, "model": MODEL}


@app.get("/agent")
async def agent_get():
    """Agent identité actuel : Alex devient « Alex <agent> »."""
    a = load_agent()
    return {
        "agent": a or None,
        "alex_name": f"Alex {a}" if a else "Alex",
        "role": AGENT_ROLES.get(a, "") if a else "",
    }


@app.post("/agent")
async def agent_set(payload: dict):
    """Définit l'agent identité d'Alex (persisté). {name} absent/vide = Alex tout court."""
    name = (payload.get("name") or payload.get("id") or "").strip()
    if not name:
        clear_agent()
        return {"ok": True, "agent": None, "alex_name": "Alex"}
    ok = save_agent(name)
    a = load_agent()
    return {"ok": ok, "agent": a or None, "alex_name": f"Alex {a}" if a else "Alex"}


@app.get("/omniroute/status")
async def omniroute_status():
    """Statut d'OmniRoute : modèles disponibles, santé."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{OMNIROUTE_URL}/models",
                headers={"Authorization": f"Bearer {OMNIROUTE_KEY}"},
            )
            r.raise_for_status()
            models = r.json().get("data", [])
            return {
                "ok": True,
                "url": OMNIROUTE_URL,
                "default_model": OMNIROUTE_MODEL,
                "models_count": len(models),
                "models": [m.get("id") for m in models[:30]],
            }
    except Exception as e:
        return {"ok": False, "url": OMNIROUTE_URL, "error": str(e)}


@app.post("/omniroute/chat")
async def omniroute_chat(payload: dict):
    """Chat direct via OmniRoute. payload: {message: str, model?: str}"""
    message = payload.get("message", "")
    model = payload.get("model")
    if not message:
        return {"error": "message manquant"}
    reply = await ask_omniroute(message, model)
    if reply:
        return {"reply": reply, "source": "omniroute", "model": model or OMNIROUTE_MODEL}
    return {"error": "OmniRoute indisponible"}


@app.get("/health")
async def health():
    online = await _check_network()
    return {"status": "ok", "model": MODEL, "online": online}


class ToolExecRequest(BaseModel):
    tool: str
    params: dict = {}


class ToolConfirmRequest(BaseModel):
    confirmation_id: str
    accept: bool = True


@app.get("/tools")
async def list_tools():
    return {"tools": tools_registry.list_tools()}


@app.post("/tools/execute")
async def execute_tool(req: ToolExecRequest):
    result = await tools_registry.execute(req.tool, req.params)
    return {"result": result, "source": "tool"}


@app.post("/tools/confirm")
async def confirm_tool(req: ToolConfirmRequest):
    pending = tools_registry.get_pending(req.confirmation_id)
    if not pending:
        return {"success": False, "error": "Action confirmée expirée ou introuvable."}
    if not req.accept:
        tools_registry.remove_pending(req.confirmation_id)
        return {"success": True, "result": "Action annulée.", "source": "tool"}
    result = await tools_registry.execute(pending["tool"], pending["params"])
    tools_registry.remove_pending(req.confirmation_id)
    return {"success": True, "result": result, "source": "tool"}


async def _handle_weather(message: str) -> Optional[ChatResponse]:
    weather_kw = ("météo", "pluie", "soleil", "temps qu'il fait", "dehors", "température", "degrés", "quel temps")
    if not any(w in message.lower() for w in weather_kw):
        return None
    loc = _extract_location(message) or "Paris"
    reply = await tools_registry.execute("meteo", {"lieu": loc})
    return ChatResponse(reply=reply, source="tool:meteo")


def _extract_location(text: str) -> Optional[str]:
    import re
    for keyword in ("à ", "a ", "sur ", "pour "):
        m = re.search(rf'(?:{keyword})([A-Za-zéèêëàâùûüôöîïçÉÈÊËÀÂÙÛÜÔÖÎÏÇ\-]+)', text)
        if m:
            return m.group(1).capitalize()
    m = re.search(r'(?:météo|meteo|temps)\s+(?:de|d\')?\s*([A-Za-zéèêëàâùûüôöîïçÉÈÊËÀÂÙÛÜÔÖÎÏÇ\-]{3,})', text.lower())
    if m:
        return m.group(1).capitalize()
    return None


# ===================== CONVERSATION — historique + réponses instantanées =====================
_conv_history: list = []  # historique glissant {role, content} → cohérence multi-tours (OmniRoute)


def _hist_push(role: str, content: str) -> None:
    _conv_history.append({"role": role, "content": content})
    del _conv_history[:-12]


def _hist_snapshot() -> list:
    return list(_conv_history)


# Messages qui nécessitent un agent (outils shell) → priorité opencode
_AGENT_RE = re.compile(
    r"(alarme|réveill|reveill|rappel|programme|todo|tâches?\b|taches?\b|"
    r"cr[ée][ée]?s?\b|fichier|dossier|installe|script|lance\b|"
    r"volume|bluetooth|wifi|capture.*écran|screenshot)",
    re.I,
)


def _quick_intent(message: str) -> Optional[str]:
    """Réponses instantanées locales (zéro LLM) — conversation sans latence."""
    m = message.lower().strip()
    if len(m) > 60:
        return None
    if re.search(r"(quelle heure|il est quelle heure)", m):
        from datetime import datetime as _dt
        return f"Il est {_dt.now().strftime('%H h %M')}."
    if re.search(r"(quel jour|quelle date|on est le|c'est quoi le jour)", m):
        from datetime import datetime as _dt
        jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        n = _dt.now()
        return f"Nous sommes {jours[n.weekday()]}, le {n.strftime('%d %B %Y')}."
    return None


async def _run_device(action, matched):
    """Actions device_control ; les ouvertures web passent par le navigateur d'Alex."""
    if action == "open_app":
        reply = await browser_control.open_site(matched.group(1))
        if reply:
            return reply, "skill:browser_control"
    return device_control.run(action, matched), "skill:device_control"


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if fleet.is_frozen():
        return ChatResponse(reply="Alex est suspendue par l'administrateur de cette instance.", source="fleet")
    dact, dm = download_control.match(req.message)
    if dact:
        reply = await download_control.run(dact, dm)
        return ChatResponse(reply=reply, source="skill:download_control")

    baction, bm = browser_control.match(req.message)
    if baction:
        reply = await browser_control.run(baction, bm)
        return ChatResponse(reply=reply, source="skill:browser_control")

    action, matched = device_control.match(req.message)
    if action:
        reply, src = await _run_device(action, matched)
        return ChatResponse(reply=reply, source=src)

    if req.model and req.model.startswith(("ddgw/", "omni/")):
        reply = await ask_omniroute(req.message, model=req.model)
        if reply:
            return ChatResponse(reply=reply, source="omniroute")

    if req.mode in ("auto", "cloud"):
        if req.mode == "auto" and not _AGENT_RE.search(req.message):
            reply = await ask_colibri(req.message, history=_hist_snapshot())
            if reply:
                _hist_push("user", req.message)
                _hist_push("assistant", reply)
                return ChatResponse(reply=reply, source="colibri")
        reply = await ask_opencode(req.message)
        if reply:
            return ChatResponse(reply=reply, source="opencode")
        reply = await ask_omniroute(req.message)
        if reply:
            return ChatResponse(reply=reply, source="omniroute")

    if req.mode == "local":
        reply = await ask_ollama(req.message)
        return ChatResponse(reply=reply, source="local") if reply else ChatResponse(reply="Modèle local indisponible.", source="none")

    if req.mode == "cloud":
        reply = await ask_cloud(req.message)
        return ChatResponse(reply=reply, source="cloud") if reply else ChatResponse(reply="API cloud non configurée.", source="none")

    reply = await ask_omniroute(req.message)
    if reply:
        return ChatResponse(reply=reply, source="omniroute")
    reply = await ask_ollama(req.message)
    if reply:
        return ChatResponse(reply=reply, source="local")
    reply = await ask_cloud(req.message)
    if reply:
        return ChatResponse(reply=reply, source="cloud")
    return ChatResponse(reply="Aucun modèle disponible.", source="none")



@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    if fleet.is_frozen():
        _frozen_reply = {"reply": "Alex est suspendue par l'administrateur de cette instance.", "source": "fleet"}
        async def frozen_done():
            yield f"data: {json.dumps(_frozen_reply)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(frozen_done(), media_type="text/event-stream")
    dact, dm = download_control.match(req.message)
    if dact:
        reply = await download_control.run(dact, dm)
        async def dl_done():
            yield f"data: {json.dumps({'reply': reply, 'source': 'skill:download_control'})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(dl_done(), media_type="text/event-stream")

    baction, bm = browser_control.match(req.message)
    if baction:
        reply = await browser_control.run(baction, bm)
        async def brows_done():
            yield f"data: {json.dumps({'reply': reply, 'source': 'skill:browser_control'})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(brows_done(), media_type="text/event-stream")

    action, matched = device_control.match(req.message)
    if action:
        reply, src = await _run_device(action, matched)
        async def skill_done():
            yield f"data: {json.dumps({'reply': reply, 'source': src})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(skill_done(), media_type="text/event-stream")

    # Météo — réponse directe d'un outil (rapide, sans LLM)
    wx = await _handle_weather(req.message)
    if wx:
        async def wx_done():
            yield f"data: {json.dumps({'reply': wx.reply, 'source': wx.source})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(wx_done(), media_type="text/event-stream")

    # Intentions locales instantanées (heure/date) — zéro latence
    quick = _quick_intent(req.message)
    if quick:
        async def quick_done():
            yield f"data: {json.dumps({'reply': quick, 'source': 'skill:quick'})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(quick_done(), media_type="text/event-stream")

    async def response_stream():
        global _chat_generation
        _chat_generation += 1
        gen = _chat_generation
        print(f"[brain] nouvelle requête chat (génération {gen}): {req.message[:50]!r}", flush=True)

        yield f"data: {json.dumps({'state': 'thinking'})}\n\n"
        msg = req.message
        omni_model = req.model if req.model and req.model.startswith(("ddgw/", "omni/", "auto/")) else None

        def _orb_events(orb):
            evs = []
            for s in orb.get("states", []):
                evs.append(f"data: {json.dumps({'state': s})}\n\n")
            for s in orb.get("shapes", []):
                evs.append(f"data: {json.dumps({'shape': s})}\n\n")
            for p in orb.get("proposals", []):
                try:
                    evs.append(f"data: {json.dumps({'proposal': json.loads(p)})}\n\n")
                except Exception:
                    pass
            return evs

        # 1) VOIE RAPIDE — colibri (Qwen3.8-27B local) — modèle par défaut
        if not _AGENT_RE.search(msg):
            chunks = []
            coli_orb = {}
            async for chunk in ask_colibri_stream(msg, history=_hist_snapshot(), orb=coli_orb):
                if chunk.startswith("data: [DONE]"):
                    break
                chunks.append(chunk)
                yield chunk
            if chunks:
                text = "".join(c[len("data: "):-2] for c in chunks)
                _hist_push("user", msg)
                _hist_push("assistant", text)
                for e in _orb_events(coli_orb):
                    yield e
                yield "data: [DONE]\n\n"
                return

        # 2) VOIE RAPIDE — OmniRoute (conversation immédiate, historique → cohérent)
        if not _AGENT_RE.search(msg):
            or_orb = {}
            or_reply = await ask_omniroute(msg, model=omni_model, history=_hist_snapshot(), orb=or_orb)
            if or_reply:
                _hist_push("user", msg)
                _hist_push("assistant", or_reply)
                for e in _orb_events(or_orb):
                    yield e
                yield f"data: {json.dumps({'reply': or_reply, 'source': 'omniroute'})}\n\n"
                yield "data: [DONE]\n\n"
                return

        # 2) AGENT — opencode (alarmes, fichiers, outils shell) — priorité si action détectée
        oc_reply = None
        oc_orb = {}
        async for ev in ask_opencode_events(req.message, gen=gen):
            if "activity" in ev:
                yield f"data: {json.dumps({'activity': ev['activity']})}\n\n"
            elif "file" in ev:
                yield f"data: {json.dumps({'file': ev['file']})}\n\n"
            elif "question" in ev:
                yield f"data: {json.dumps({'question': ev['question']})}\n\n"
            elif "permission" in ev:
                yield f"data: {json.dumps({'permission': ev['permission']})}\n\n"
            elif "question_resolved" in ev:
                yield f"data: {json.dumps({'question_resolved': ev['question_resolved']})}\n\n"
            elif "permission_resolved" in ev:
                yield f"data: {json.dumps({'permission_resolved': ev['permission_resolved']})}\n\n"
            elif "reply" in ev:
                oc_reply, oc_st, oc_sh, oc_pr = _sanitize_orb(ev["reply"])
                oc_orb.setdefault("states", []).extend(oc_st)
                oc_orb.setdefault("shapes", []).extend(oc_sh)
                oc_orb.setdefault("proposals", []).extend(oc_pr)
            elif "error" in ev:
                if ev["error"] == "annulé":
                    yield "data: [DONE]\n\n"
                    return
                oc_reply = None
        if oc_reply and oc_reply.strip():
            _hist_push("user", msg)
            _hist_push("assistant", oc_reply)
            for e in _orb_events(oc_orb):
                yield e
            yield f"data: {json.dumps({'reply': oc_reply, 'source': 'opencode'})}\n\n"
            yield "data: [DONE]\n\n"
            return

        # 3) Dernier recours — OmniRoute puis local (ollama)
        or_orb = {}
        or_reply = await ask_omniroute(msg, model=omni_model, history=_hist_snapshot(), orb=or_orb)
        if or_reply:
            _hist_push("user", msg)
            _hist_push("assistant", or_reply)
            for e in _orb_events(or_orb):
                yield e
            yield f"data: {json.dumps({'reply': or_reply, 'source': 'omniroute'})}\n\n"
            yield "data: [DONE]\n\n"
            return

        async for chunk in ask_ollama_stream(req.message):
            if chunk.startswith("data: [DONE]"):
                break
            yield chunk

        yield "data: [DONE]\n\n"

    return StreamingResponse(response_stream(), media_type="text/event-stream")




# --- Synthèse vocale (Piper local, fallback Edge TTS) ---
from edge_tts import Communicate
import io
import subprocess

# Piper local — voix française naturelle, hors-ligne, rapide CPU
PIPER_BIN = os.path.expanduser("~/.local/bin/piper")
PIPER_VOICE = os.path.expanduser("~/Projects/alex/alex/brain/tts/fr_FR-siwis-medium.onnx")
PIPER_SAMPLE_RATE = 22050

# Kokoro TTS — voix neuronale locale (haute qualité, CPU)
KOKORO_VOICE = os.environ.get("ALEX_KOKORO_VOICE", "ff_siwis_v2")
_kokoro_pipeline = None
_kokoro_tried = False

FRENCH_VOICES = {
    "kokoro": ("local-kokoro", "Kokoro — française, neuronale (local, haute qualité)"),
    "siwis": ("local-siwis", "Siwis — française, naturelle (local, hors-ligne)"),
    "denise": ("fr-FR-DeniseNeural", "Denise — française, chaleureuse (cloud)"),
    "eloise": ("fr-FR-EloiseNeural", "Éloïse — française, douce (cloud)"),
    "vivienne": ("fr-FR-VivienneMultilingualNeural", "Vivienne — française, multilingue (cloud)"),
    "sylvie": ("fr-CA-SylvieNeural", "Sylvie — québécoise, dynamique (cloud)"),
    "ariane": ("fr-CH-ArianeNeural", "Ariane — suisse, élégante (cloud)"),
    "charline": ("fr-BE-CharlineNeural", "Charline — belge, pétillante (cloud)"),
    "henri": ("fr-FR-HenriNeural", "Henri — français, masculin (cloud)"),
    "remy": ("fr-FR-RemyMultilingualNeural", "Rémy — français, masculin, multilingue (cloud)"),
}


def _kokoro_available() -> bool:
    """True si Kokoro est importable ET le modèle est présent (sinon fallback Piper)."""
    global _kokoro_tried
    if _kokoro_tried:
        return _kokoro_pipeline is not None
    _kokoro_tried = True
    try:
        import kokoro  # noqa: F401
        # Le modèle doit être déjà en cache — sinon on ne tente PAS de téléchargement
        # (trop lourd pour le réseau) : on laisse Piper prendre le relais.
        hub = os.path.expanduser("~/.cache/huggingface/hub/models--hexgrad--Kokoro-82M")
        has_weights = False
        if os.path.isdir(hub):
            for root, _dirs, files in os.walk(hub):
                for fn in files:
                    if fn.endswith((".pth", ".safetensors", ".pt", ".bin")):
                        has_weights = True
                        break
                if has_weights:
                    break
        if not has_weights:
            print("[tts] Kokoro : modèle absent du cache — Piper reste la voix locale", flush=True)
            return False
        return True
    except Exception:
        return False


def _get_kokoro():
    """Charge (une seule fois) le pipeline Kokoro français."""
    global _kokoro_pipeline
    if _kokoro_pipeline is not None:
        return _kokoro_pipeline
    from kokoro import KPipeline
    # lang_code 'f' = français (voix ff_siwis / ff_siwis_v2)
    _kokoro_pipeline = KPipeline(lang_code="f")
    print(f"[tts] Kokoro chargé (voix {KOKORO_VOICE})", flush=True)
    return _kokoro_pipeline


def _kokoro_synth(text: str) -> Optional[bytes]:
    """Synthèse locale via Kokoro → WAV PCM 24 kHz. Retourne les bytes WAV ou None."""
    if not _kokoro_available():
        return None
    try:
        import numpy as np
        import wave
        import struct

        pipeline = _get_kokoro()
        samples = []
        for result in pipeline(text, voice=KOKORO_VOICE, speed=1.0):
            samples.append(result.audio)
        if not samples:
            return None
        audio = np.concatenate(samples)
        # Normaliser en int16
        audio = np.clip(audio, -1.0, 1.0)
        pcm = (audio * 32767).astype(np.int16)
        import io as _io
        buf = _io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()
    except Exception as e:
        print(f"[tts] Kokoro indisponible : {e.__class__.__name__}: {e}", flush=True)
        return None


def _piper_available() -> bool:
    return os.path.isfile(PIPER_BIN) and os.path.isfile(PIPER_VOICE)


def _piper_synth(text: str) -> Optional[bytes]:
    """Synthèse locale via Piper → WAV PCM. Retourne les bytes WAV ou None."""
    if not _piper_available():
        return None
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            out_path = tf.name
        proc = subprocess.run(
            [PIPER_BIN, "--model", PIPER_VOICE, "--output_file", out_path,
             "--length-scale", "1.15",
             "--sentence-silence", "0.35"],
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 44:
            with open(out_path, "rb") as f:
                data = f.read()
            os.unlink(out_path)
            return data
        if os.path.isfile(out_path):
            os.unlink(out_path)
    except Exception:
        pass
    return None

ESPEAK_BIN = "/usr/bin/espeak-ng"

def _espeak_synth(text: str) -> Optional[bytes]:
    """Synthèse locale via espeak-ng → WAV PCM. Dernier recours hors-ligne."""
    if not os.path.isfile(ESPEAK_BIN):
        return None
    import tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            out_path = tf.name
        proc = subprocess.run(
            [ESPEAK_BIN, "-v", "fr", "-s", "160", "-p", "45", "-w", out_path, text],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if proc.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 44:
            with open(out_path, "rb") as f:
                data = f.read()
            os.unlink(out_path)
            return data
        if os.path.isfile(out_path):
            os.unlink(out_path)
    except Exception:
        pass
    return None

CLONE_VOICE_PATH = os.path.expanduser("~/Documents/Alex.ogg")

class VocalRequest(BaseModel):
    text: str
    voice: str = "siwis"

@app.get("/voices")
async def list_voices():
    items = [{"id": k, "name": v[1], "voice": v[0]} for k, v in FRENCH_VOICES.items()]
    has_clone = os.path.isfile(CLONE_VOICE_PATH)
    if has_clone:
        items.insert(0, {"id": "clone", "name": "Alex (clone) — depuis Alex.ogg", "voice": ""})
    return {"voices": items, "default": "siwis", "clone_available": has_clone}

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF\U0001F000-\U0001FFFF"
    "\uFE0E\uFE0F\u200D\u20E3]+"
)

# Garde anti-écho : mémorise ce qui vient d'être prononcé pour le refuser en STT
_last_spoken = {"t": 0.0, "norm": "", "words": set()}
_last_stt = {"t": 0.0, "norm": ""}
_ECHO_WINDOW_S = 45.0


def _norm_words(s: str) -> list:
    return re.findall(r"[a-zàâçéèêëîïôûùüÿœ']+", (s or "").lower())


def _has_repeat_hallucination(s: str) -> bool:
    """Whisper répète en boucle sur un écho ('très très très…')."""
    w = _norm_words(s)
    if len(w) < 4:
        return False
    run = 1
    for i in range(1, len(w)):
        run = run + 1 if w[i] == w[i - 1] else 1
        if run >= 3:
            return True
    return len(set(w)) <= max(2, len(w) // 4)


def _stt_reject_reason(text: str, now: float) -> str:
    """Raison du rejet du texte transcrit ('' = accepté)."""
    norm = " ".join(_norm_words(text))
    if not norm:
        return "vide"
    if _has_repeat_hallucination(text):
        return "hallucination"
    if now - _last_stt["t"] < 30 and norm == _last_stt["norm"]:
        return "doublon"
    if now - _last_spoken["t"] < _ECHO_WINDOW_S and _last_spoken["words"]:
        tw = set(_norm_words(text))
        inter = len(tw & _last_spoken["words"])
        if tw and inter >= 3 and inter / len(tw | _last_spoken["words"]) >= 0.5:
            return "écho"
        if norm and norm in _last_spoken["norm"]:
            return "écho"
    return ""


@app.post("/vocal")
async def vocal_synth(req: VocalRequest):
    voice_id = req.voice or "siwis"
    text = _EMOJI_RE.sub(" ", req.text)
    # Retirer les blocs schema/mermaid/diff/thought (affichage visuel uniquement, jamais lus)
    text = re.sub(r"```(?:schema|schéma|mermaid|diff|thought)\n[\s\S]*?```", " ", text)
    text = re.sub(r"```\w*\n[\s\S]*?```", " ", text)
    text = re.sub(r"https?://\S+|www\.\S+", " ", text)
    text = re.sub(r"[*_#`~|<>\[\](){}]+", " ", text)
    text = re.sub(r"[•·]+", " ", text)
    # Retirer les lignes de tableau markdown (| col1 | col2 |)
    text = re.sub(r"(?:^|\n)\s*\|[^|\n]*\|(?:\s*\|[^|\n]*\|)*\s*", "\n", text)
    # Retirer les lignes de liste markdown (- item, * item, 1. item)
    text = re.sub(r"(?:^|\n)\s*[-*•]\s+", "\n", text)
    text = re.sub(r"(?:^|\n)\s*\d+\.\s+", "\n", text)
    text = re.sub(r"\s{2,}", " ", text).strip() or "…"

    # Anti-écho : mémoriser ce qui va être prononcé (refusé côté /stt si réentendu)
    _norm = " ".join(_norm_words(text))
    _last_spoken["t"] = __import__("time").time()
    _last_spoken["norm"] = _norm
    _last_spoken["words"] = set(_norm.split())

    # Clone voice : lire le fichier Alex.ogg directement
    if voice_id == "clone":
        if os.path.isfile(CLONE_VOICE_PATH):
            with open(CLONE_VOICE_PATH, "rb") as f:
                data = f.read()
            return StreamingResponse(io.BytesIO(data), media_type="audio/ogg")
        voice_id = "siwis"

    # 1) Kokoro local (haute qualité neuronale, hors-ligne) — priorité si dispo
    if voice_id in ("kokoro", "siwis", "local") and _kokoro_available():
        wav = await asyncio.to_thread(_kokoro_synth, text)
        if wav:
            return StreamingResponse(io.BytesIO(wav), media_type="audio/wav")

    # 2) Piper local (siwis ou par défaut si dispo)
    if voice_id in ("siwis", "local", "denise") and _piper_available():
        wav = await asyncio.to_thread(_piper_synth, text)
        if wav:
            return StreamingResponse(io.BytesIO(wav), media_type="audio/wav")

    # 3) Fallback Edge TTS (cloud)
    voice_name = FRENCH_VOICES.get(voice_id, FRENCH_VOICES["eloise"])[0]
    if voice_name.startswith("local-"):
        voice_name = "fr-FR-EloiseNeural"
    # Hésitations légères pour un style conversationnel (sans casser la ponctuation)
    if random.random() < 0.2:
        text = random.choice(["Euh, ", "Hum, ", "Alors, "]) + text
    pitch = f"{random.randint(-2, 3):+d}Hz"
    try:
        tts = Communicate(text, voice_name, rate="-15%", pitch=pitch)
        buf = io.BytesIO()
        async for chunk in tts.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        if buf.getbuffer().nbytes > 0:
            buf.seek(0)
            return StreamingResponse(buf, media_type="audio/mpeg")
        print(f"[tts] Edge TTS ({voice_name}) : aucun audio renvoyé", flush=True)
    except Exception as e:
        print(f"[tts] Edge TTS ({voice_name}) indisponible : "
              f"{e.__class__.__name__}: {e}", flush=True)

    # 4) gTTS (Google) — cloud gratuit, voix naturelle
    try:
        from gtts import gTTS as _GTTS
        import tempfile as _tf
        with _tf.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3f:
            mp3_path = mp3f.name
        await asyncio.to_thread(lambda: _GTTS(text, lang="fr").save(mp3_path))
        if os.path.isfile(mp3_path) and os.path.getsize(mp3_path) > 100:
            with open(mp3_path, "rb") as f:
                data = f.read()
            os.unlink(mp3_path)
            return StreamingResponse(io.BytesIO(data), media_type="audio/mpeg")
        if os.path.isfile(mp3_path):
            os.unlink(mp3_path)
    except Exception as e:
        print(f"[tts] gTTS indisponible : {e.__class__.__name__}: {e}", flush=True)

    # 5) Dernier recours : espeak-ng local — Alex ne doit jamais rester muette
    wav = await asyncio.to_thread(_espeak_synth, text)
    if wav:
        return StreamingResponse(io.BytesIO(wav), media_type="audio/wav")

    raise HTTPException(status_code=503, detail="Synthèse vocale indisponible (cloud et local)")


# ===================== STT — faster-whisper (écoute locale) =====================

_whisper_model = None
_whisper_lock = asyncio.Lock()


async def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        async with _whisper_lock:
            if _whisper_model is None:
                from faster_whisper import WhisperModel
                model_name = os.environ.get("ALEX_STT_MODEL", "base")
                print(f"[stt] chargement faster-whisper ({model_name}, CPU int8)...", flush=True)
                _whisper_model = await asyncio.to_thread(
                    WhisperModel, model_name,
                    device="cpu", compute_type="int8",
                    cpu_threads=4)
                print("[stt] modèle prêt", flush=True)
    return _whisper_model


@app.post("/stt")
async def stt(audio: UploadFile):
    """Transcription locale (fr-FR) d'un enregistrement micro (webm/ogg/wav)."""
    import tempfile
    data = await audio.read()
    if not data or len(data) < 500:
        return {"text": ""}
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as f:
        f.write(data)
        f.flush()
        model = await _get_whisper()

        def _run():
            segments, _info = model.transcribe(
                f.name, language="fr", beam_size=1, vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=300),
                condition_on_previous_text=False,
                temperature=0.0,
                initial_prompt="Salut Alex. Conversation en français avec ton assistant personnel.")
            return " ".join(s.text.strip() for s in segments).strip()

        text = await asyncio.to_thread(_run)
    import time as _t
    now = _t.time()
    reason = _stt_reject_reason(text, now)
    if reason:
        print(f"[stt] rejeté ({reason}) → {text!r}", flush=True)
        return {"text": ""}
    _norm = " ".join(_norm_words(text))
    _last_stt["t"] = now
    _last_stt["norm"] = _norm
    print(f"[stt] → {text!r}", flush=True)
    return {"text": text}


# ===================== MEDIA + SCREENSHOT + VISION =====================

def _safe_path(p: str) -> Optional[str]:
    p = os.path.realpath(os.path.expanduser(p))
    home = os.path.realpath(os.path.expanduser("~"))
    if p.startswith(home + os.sep) or p.startswith("/tmp/") or p.startswith(home):
        if os.path.isfile(p):
            return p
    return None


def _img_url(path: str) -> str:
    return f"http://127.0.0.1:8765/media?path={path}"


@app.get("/media")
async def media(path: str):
    f = _safe_path(path)
    if not f:
        return {"error": "fichier introuvable ou accès refusé"}
    import mimetypes
    media_type = mimetypes.guess_type(f)[0] or "application/octet-stream"
    return StreamingResponse(open(f, "rb"), media_type=media_type)


@app.post("/screenshot")
async def screenshot():
    """Capture d'écran instantanée — renvoie le chemin + URL pour l'UI."""
    out = f"/tmp/alex-shot-{__import__('time').time():.0f}.png"
    procs = [
        ["gnome-screenshot", "-f", out],
        ["scrot", out],
        ["import", "-window", "root", out],
    ]
    for cmd in procs:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.wait(), timeout=10)
            if os.path.isfile(out) and os.path.getsize(out) > 1000:
                print(f"[screenshot] {out}", flush=True)
                return {"path": out, "url": _img_url(out)}
        except Exception:
            continue
    return {"error": "capture impossible"}


_vision_sid_file = os.path.join(os.path.dirname(__file__), "..", ".alex-vision-sid")
_vision_sid: Optional[str] = None


async def _vision_ensure_session(client: httpx.AsyncClient) -> str:
    global _vision_sid
    if _vision_sid:
        r = await client.get(f"{OPENCODE_URL}/api/session/{_vision_sid}")
        if r.status_code == 200:
            return _vision_sid
    path = os.path.normpath(_vision_sid_file)
    if os.path.exists(path):
        saved = open(path).read().strip()
        if saved:
            r = await client.get(f"{OPENCODE_URL}/api/session/{saved}")
            if r.status_code == 200:
                _vision_sid = saved
                return saved
    r = await client.post(f"{OPENCODE_URL}/api/session", json={"title": "Alex Vision"})
    r.raise_for_status()
    sid = r.json()["data"]["id"]
    await client.post(
        f"{OPENCODE_URL}/api/session/{sid}/model",
        json={"model": {"providerID": "opencode", "id": OPENCODE_MODEL}},
    )
    open(path, "w").write(sid)
    _vision_sid = sid
    return sid


@app.post("/vision")
async def vision(payload: dict = None):
    """Capture l'écran (ou analyse un fichier) avec la vision d'opencode."""
    import time as _t
    image_path = (payload or {}).get("path")
    if not image_path:
        shot = await screenshot()
        if "error" in shot:
            return shot
        image_path = shot["path"]
    f = _safe_path(image_path)
    if not f:
        return {"error": "image introuvable"}
    question = (payload or {}).get(
        "question", "Décris cette image en 2 à 3 phrases orales simples, en français.")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            sid = await _vision_ensure_session(client)
            for _ in range(15):
                r = await client.get(f"{OPENCODE_URL}/api/session/{sid}/message?limit=5")
                ms = r.json().get("data", []) if r.status_code == 200 else []
                if not any(m.get("type") == "assistant" and not m.get("finish") for m in ms):
                    break
                await asyncio.sleep(1)
            r = await client.get(f"{OPENCODE_URL}/api/session/{sid}/message?limit=5")
            before = {m["id"] for m in r.json().get("data", [])} if r.status_code == 200 else set()
            r = await client.post(
                f"{OPENCODE_URL}/api/session/{sid}/prompt",
                json={"prompt": {"text": f"Analyse cette image avec ton outil de lecture de fichier : {f}\n\n{question}"}})
            r.raise_for_status()
            deadline = _t.time() + 90
            while _t.time() < deadline:
                await asyncio.sleep(2)
                r = await client.get(f"{OPENCODE_URL}/api/session/{sid}/message?limit=5")
                if r.status_code != 200:
                    continue
                for m in r.json().get("data", []):
                    if m.get("type") != "assistant" or m.get("id") in before or not m.get("finish"):
                        continue
                    if m.get("finish") == "stop":
                        txt = " ".join(p.get("text", "") for p in m.get("content", [])
                                       if p.get("type") == "text").strip()
                        txt = re.sub(r"[*_#`~|]+", "", txt)
                        if txt:
                            return {"analysis": txt, "path": f, "url": _img_url(f)}
        return {"error": "analyse trop longue"}
    except Exception as e:
        print(f"[vision] erreur: {e}", flush=True)
        return {"error": str(e)}


# ===================== NAVIGATEUR — Chromium via CDP =====================

from . import browser


class BrowserNav(BaseModel):
    url: str

class BrowserSelector(BaseModel):
    selector: str

class BrowserFill(BaseModel):
    selector: str
    value: str

class BrowserPress(BaseModel):
    selector: str = ""
    key: str = "Enter"

class BrowserScroll(BaseModel):
    direction: str = "down"
    amount: int = 400


class BrowserFillField(BaseModel):
    label: str
    value: str


class BrowserClickText(BaseModel):
    text: str


class BrowserTabAct(BaseModel):
    index: int | None = None


class BrowserOpen(BaseModel):
    url: str = "about:blank"


class DownloadReq(BaseModel):
    url: str
    filename: str | None = None
    sha256: str | None = None
    dest_dir: str | None = None
    force: bool = False
    priority: int = 0


@app.post("/downloads")
async def downloads_start(req: DownloadReq):
    return await downloads_store.submit_async(
        url=req.url, filename=req.filename, sha256=req.sha256,
        dest_dir=req.dest_dir, force=req.force, priority=req.priority,
    )


@app.get("/downloads")
async def downloads_list():
    return downloads_store.list_status()


@app.get("/downloads/{job_id}")
async def downloads_get(job_id: str):
    st = downloads_store.read_status(job_id)
    if not st:
        raise HTTPException(404, "Téléchargement introuvable")
    return st


@app.delete("/downloads/{job_id}")
async def downloads_cancel(job_id: str):
    return await asyncio.to_thread(downloads_store.cancel, job_id)


@app.post("/downloads/{job_id}/retry")
async def downloads_retry(job_id: str):
    return await asyncio.to_thread(downloads_store.retry, job_id)


@app.get("/browser/status")
async def browser_status():
    return await browser.status()


@app.post("/browser/start")
async def browser_start():
    ok = await browser.start()
    return {"running": ok}


@app.post("/browser/stop")
async def browser_stop():
    await browser.stop()
    return {"running": False}


@app.post("/browser/navigate")
async def browser_navigate(req: BrowserNav):
    try:
        return await browser.navigate(req.url)
    except Exception as e:
        return {"error": str(e)}


@app.get("/browser/screenshot")
async def browser_screenshot(full_page: bool = False):
    try:
        path = await browser.screenshot(full_page)
        return {"path": path, "url": _img_url(path) if os.path.isfile(path) else None}
    except Exception as e:
        return {"error": str(e)}


@app.get("/browser/text")
async def browser_text():
    try:
        return await browser.get_text()
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/click")
async def browser_click(req: BrowserSelector):
    try:
        return await browser.click(req.selector)
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/fill")
async def browser_fill(req: BrowserFill):
    try:
        return await browser.fill(req.selector, req.value)
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/press")
async def browser_press(req: BrowserPress):
    try:
        return await browser.press(req.selector, req.key)
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/scroll")
async def browser_scroll(req: BrowserScroll):
    try:
        return await browser.scroll(req.direction, req.amount)
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/back")
async def browser_back():
    try:
        return await browser.go_back()
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/forward")
async def browser_forward():
    try:
        return await browser.go_forward()
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/reload")
async def browser_reload():
    try:
        return await browser.reload()
    except Exception as e:
        return {"error": str(e)}


@app.get("/browser/tabs")
async def browser_tabs_list():
    try:
        return {"tabs": await browser.list_tabs()}
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/open")
async def browser_open_url(req: BrowserOpen):
    try:
        return await browser.open_url_smart(req.url)
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/tab/new")
async def browser_tab_new(req: BrowserOpen | None = None):
    try:
        return await browser.new_tab(req.url if req else "about:blank")
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/tab/close")
async def browser_tab_close(req: BrowserTabAct | None = None):
    try:
        return await browser.close_tab(req.index if req else None)
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/tab/activate")
async def browser_tab_activate(req: BrowserTabAct | None = None):
    try:
        return await browser.activate_tab(req.index if req else None)
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/fill_field")
async def browser_fill_field(req: BrowserFillField):
    try:
        return await browser.fill_field(req.label, req.value)
    except Exception as e:
        return {"error": str(e)}


@app.post("/browser/click_text")
async def browser_click_text(req: BrowserClickText):
    try:
        return await browser.click_text(req.text)
    except Exception as e:
        return {"error": str(e)}


# ===================== ALARMES / RAPPELS (systemd timers + at) =====================
import subprocess
from datetime import datetime, timedelta

@app.post("/alarm")
async def set_alarm(payload: dict):
    """Programme une alarme. payload: {time: 'HH:MM' ou datetime ISO, message: str, date: 'YYYY-MM-DD' optionnel}"""
    alarm_time = payload.get("time", "")
    message = payload.get("message", "⏰ Alarme !")
    date_str = payload.get("date", "")

    # Construire le datetime cible
    try:
        if date_str and "T" not in alarm_time:
            target = datetime.strptime(f"{date_str} {alarm_time}", "%Y-%m-%d %H:%M")
        elif "T" in alarm_time:
            target = datetime.fromisoformat(alarm_time)
        else:
            now = datetime.now()
            h, m = map(int, alarm_time.split(":"))
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
    except Exception as e:
        return {"error": f"Heure invalide: {e}"}

    delay_sec = int((target - datetime.now()).total_seconds())
    if delay_sec < 0:
        return {"error": "L'heure est déjà passée"}

    # Notification sonore + visuelle
    cmd = (
        f"sleep {delay_sec}; "
        f"DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        f"notify-send -u critical '⏰ Alex - Alarme' '{message}' "
        f"&& paplay /usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga 2>/dev/null || "
        f"paplay /usr/share/sounds/freedesktop/stereo/complete.oga 2>/dev/null"
    )
    try:
        subprocess.Popen(["at", "-t", target.strftime("%Y%m%d%H%M"), "-f", "-"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True).communicate(input=cmd)
        return {"ok": True, "time": target.strftime("%H:%M %d/%m"), "message": message}
    except Exception as e:
        return {"error": str(e)}


@app.post("/rappel")
async def set_reminder(payload: dict):
    """Programme un rappel dans N minutes. payload: {minutes: int, message: str} ou {delay: '2h30', message}"""
    message = payload.get("message", "🔔 Rappel d'Alex")
    minutes = payload.get("minutes")
    delay_str = payload.get("delay", "")

    if delay_str:
        # Parser "2h30", "45m", "1h"
        minutes = 0
        import re
        h = re.search(r'(\d+)\s*h', delay_str)
        m = re.search(r'(\d+)\s*m', delay_str)
        if h: minutes += int(h.group(1)) * 60
        if m: minutes += int(m.group(1))
        if not h and not m:
            try: minutes = int(delay_str)
            except: return {"error": f"Delay invalide: {delay_str}"}

    if not minutes or minutes <= 0:
        return {"error": "Durée invalide"}

    target = datetime.now() + timedelta(minutes=minutes)
    cmd = (
        f"sleep {minutes * 60}; "
        f"DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 "
        f"notify-send -u critical '🔔 Alex - Rappel' '{message}' "
        f"&& paplay /usr/share/sounds/freedesktop/stereo/complete.oga 2>/dev/null"
    )
    try:
        subprocess.Popen(["at", "-t", target.strftime("%Y%m%d%H%M"), "-f", "-"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True).communicate(input=cmd)
        return {"ok": True, "minutes": minutes, "time": target.strftime("%H:%M"), "message": message}
    except Exception as e:
        return {"error": str(e)}


@app.get("/alarms")
async def list_alarms():
    """Liste les alarmes/rappels programmés (via at)."""
    try:
        r = subprocess.run(["atq"], capture_output=True, text=True, timeout=5)
        return {"alarms": r.stdout.strip() or "Aucune"}
    except Exception as e:
        return {"error": str(e)}


@app.delete("/alarms/{job_id}")
async def cancel_alarm(job_id: str):
    """Annule une alarme par son ID at."""
    try:
        r = subprocess.run(["atrm", job_id], capture_output=True, text=True, timeout=5)
        return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}
    except Exception as e:
        return {"error": str(e)}


# ===================== APPAREILS CONNECTÉS =====================
import re as _re

def _run_cmd(cmd: list, timeout: int = 8) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""

@app.get("/devices")
async def list_devices():
    """Liste tous les appareils connectés : Bluetooth, Audio, Réseau, USB, Écrans."""
    result = {}

    # Bluetooth
    bt_raw = _run_cmd(["bluetoothctl", "devices"])
    bt_devices = []
    for line in bt_raw.splitlines():
        m = _re.match(r"Device\s+([0-9A-F:]{17})\s+(.*)", line)
        if m:
            mac, name = m.groups()
            info = _run_cmd(["bluetoothctl", "info", mac])
            connected = "Connected: yes" in info
            bt_devices.append({"mac": mac, "name": name.strip(), "connected": connected})
    result["bluetooth"] = bt_devices

    # Audio (sorties)
    sinks_raw = _run_cmd(["pactl", "list", "sinks", "short"])
    sinks = []
    for line in sinks_raw.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            sinks.append({"id": parts[0], "name": parts[1], "driver": parts[2] if len(parts) > 2 else ""})
    default_sink = _run_cmd(["pactl", "get-default-sink"])
    result["audio"] = {"sinks": sinks, "default": default_sink}

    # Réseau — appareils sur le LAN
    route = _run_cmd(["ip", "route", "show", "default"])
    subnet_m = _re.search(r"via\s+\S+\s+dev\s+\S+\s+src\s+(\d+\.\d+\.\d+)\.\d+", route)
    if subnet_m:
        subnet = subnet_m.group(1) + ".0/24"
        nmap_raw = _run_cmd(["nmap", "-sn", "-n", subnet], timeout=15)
        hosts = _re.findall(r"Nmap scan report for \S+ \((\d+\.\d+\.\d+\.\d+)\)", nmap_raw)
        result["network"] = {"subnet": subnet, "hosts": hosts}
    else:
        result["network"] = {"subnet": "?", "hosts": []}

    # WiFi connecté
    wifi = _run_cmd(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi", "list"], timeout=10)
    wifi_lines = [l for l in wifi.splitlines() if l.startswith("yes")]
    result["wifi"] = wifi_lines[0] if wifi_lines else "non connecté"

    # USB
    usb_raw = _run_cmd(["lsusb"])
    result["usb"] = usb_raw.splitlines() if usb_raw else []

    # Écrans
    monitors = _run_cmd(["xrandr", "--listmonitors"])
    result["monitors"] = [l.strip() for l in monitors.splitlines() if ":" in l and "Monitors" not in l]

    return result


# ===================== WIFI =====================
@app.get("/wifi")
async def wifi_status():
    """Statut WiFi : réseau connecté, signal, IP, appareils visibles."""
    result = {}

    # État radio
    radio = _run_cmd(["nmcli", "radio", "wifi"])
    result["radio"] = radio  # enabled / disabled

    # Interface
    iface = _run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"])
    wlan_line = [l for l in iface.splitlines() if "wifi" in l]
    result["interface"] = wlan_line[0] if wlan_line else "inconnu"

    # Réseau actuel
    active = _run_cmd(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY,BSSID", "device", "wifi", "list"])
    for line in active.splitlines():
        if line.startswith("yes"):
            parts = line.split(":")
            if len(parts) >= 4:
                result["connected"] = {
                    "ssid": parts[1],
                    "signal": parts[2] + "%",
                    "security": parts[3],
                }
            break
    else:
        result["connected"] = None

    # IP
    ip = _run_cmd(["ip", "-4", "addr", "show"])
    ip_m = _re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/\d+\s+.*wlan", ip)
    if not ip_m:
        ip_m = _re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/\d+", ip)
    result["ip"] = ip_m.group(1) if ip_m else "?"

    # Réseaux visibles (rescan)
    _run_cmd(["nmcli", "device", "wifi", "rescan"], timeout=5)
    await asyncio.sleep(2)
    scan = _run_cmd(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "device", "wifi", "list"], timeout=10)
    networks = []
    seen = set()
    for line in scan.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] and parts[0] not in seen:
            seen.add(parts[0])
            networks.append({
                "ssid": parts[0],
                "signal": parts[1] + "%",
                "security": parts[2] if parts[2] else "ouvert",
                "in_use": parts[3].strip() == "*" if len(parts) > 3 else False,
            })
    networks.sort(key=lambda n: int(n["signal"].replace("%", "")), reverse=True)
    result["networks"] = networks[:15]

    return result


@app.post("/wifi/connect")
async def wifi_connect(payload: dict):
    """Connecte à un réseau WiFi. payload: {ssid: str, password: str optionnel}"""
    ssid = payload.get("ssid", "")
    password = payload.get("password", "")
    if not ssid:
        return {"error": "SSID manquant"}
    try:
        if password:
            cmd = ["nmcli", "device", "wifi", "connect", ssid, "password", password]
        else:
            cmd = ["nmcli", "device", "wifi", "connect", ssid]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            return {"ok": True, "ssid": ssid, "output": r.stdout.strip()}
        else:
            return {"error": r.stderr.strip() or r.stdout.strip()}
    except Exception as e:
        return {"error": str(e)}


@app.post("/wifi/disconnect")
async def wifi_disconnect():
    """Déconnecte du réseau WiFi actuel."""
    try:
        r = subprocess.run(["nmcli", "device", "disconnect", "wlan0"],
                           capture_output=True, text=True, timeout=10)
        return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}
    except Exception as e:
        return {"error": str(e)}


@app.post("/wifi/toggle")
async def wifi_toggle(payload: dict):
    """Active/Désactive le WiFi. payload: {on: true|false}"""
    state = "on" if payload.get("on", True) else "off"
    try:
        r = subprocess.run(["nmcli", "radio", "wifi", state],
                           capture_output=True, text=True, timeout=10)
        return {"ok": r.returncode == 0, "state": state}
    except Exception as e:
        return {"error": str(e)}


# ===================== SÉCURITÉ / CYBERDÉFENSE =====================
import hashlib
from collections import Counter

@app.get("/security/scan")
async def security_scan():
    """Scan de sécurité complet : ports ouverts, connexions suspectes, processus, tentatives d'intrusion."""
    result = {}

    # 1. Ports ouverts (externes vs localhost)
    ss_raw = _run_cmd(["ss", "-tlnp"])
    external_ports = []
    local_ports = []
    for line in ss_raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            addr = parts[3]
            proc = parts[-1] if len(parts) > 5 else ""
            if addr.startswith("0.0.0.0:") or addr.startswith("*:"):
                port = addr.split(":")[-1]
                external_ports.append({"port": port, "process": proc})
            elif addr.startswith("127.0.0.1:"):
                port = addr.split(":")[-1]
                local_ports.append({"port": port, "process": proc})
    result["ports"] = {"exposed": external_ports, "localhost_only": local_ports}

    # 2. Connexions réseau actives (sortantes suspectes)
    conn_raw = _run_cmd(["ss", "-tnp"])
    established = []
    for line in conn_raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "ESTAB":
            remote = parts[4]
            proc = parts[-1] if len(parts) > 5 else ""
            established.append({"remote": remote, "process": proc})
    result["connections"] = established[:20]

    # 3. Tentatives d'intrusion SSH (failed logins)
    auth_log = _run_cmd(["journalctl", "-u", "ssh", "--since", "24 hours ago", "--no-pager"], timeout=10)
    failed_ssh = [l for l in auth_log.splitlines() if "Failed password" in l or "Invalid user" in l]
    result["ssh_attacks"] = {
        "count": len(failed_ssh),
        "recent": failed_ssh[-10:] if failed_ssh else []
    }

    # 4. Processus suspects (haut CPU/RAM, noms bizarres)
    ps_raw = _run_cmd(["ps", "aux", "--sort=-%cpu"], timeout=8)
    top_procs = []
    for line in ps_raw.splitlines()[1:11]:
        parts = line.split(None, 10)
        if len(parts) >= 11:
            top_procs.append({
                "user": parts[0], "cpu": parts[2], "mem": parts[3],
                "command": parts[10][:80]
            })
    result["top_processes"] = top_procs

    # 5. Connexions réseau sans processus identifié (potentiel suspect)
    raw_est = _run_cmd(["ss", "-tnp"], timeout=8)
    no_proc = [l for l in raw_est.splitlines()[1:] if "users:" not in l and "ESTAB" in l]
    result["unidentified_connections"] = no_proc[:10]

    # 6. Dernières authentifications
    last_logins = _run_cmd(["last", "-n", "5", "-w"], timeout=5)
    result["logins"] = last_logins.splitlines()[:5]

    # 7. IP banni / fail2ban
    fail2ban = _run_cmd(["fail2ban-client", "status"], timeout=5)
    result["fail2ban"] = fail2ban or "non installé"

    return result


@app.get("/security/firewall")
async def firewall_status():
    """Statut du pare-feu (ufw + iptables)."""
    ufw = _run_cmd(["ufw", "status", "verbose"], timeout=5)
    iptables = _run_cmd(["iptables", "-L", "-n", "--line-numbers"], timeout=5)
    return {
        "ufw": ufw or "non disponible",
        "iptables_rules": iptables[:2000] if iptables else "aucune",
        "active": "Status: active" in ufw
    }


@app.post("/security/firewall")
async def firewall_control(payload: dict):
    """Contrôle du pare-feu. payload: {action: 'enable'|'disable'|'allow_port'|'deny_port'|'status', port: int?}"""
    action = payload.get("action", "status")
    port = payload.get("port")
    try:
        if action == "enable":
            r = subprocess.run(["ufw", "--force", "enable"], capture_output=True, text=True, timeout=10)
            return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}
        elif action == "disable":
            r = subprocess.run(["ufw", "disable"], capture_output=True, text=True, timeout=10)
            return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}
        elif action == "allow_port" and port:
            r = subprocess.run(["ufw", "allow", str(port)], capture_output=True, text=True, timeout=10)
            return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}
        elif action == "deny_port" and port:
            r = subprocess.run(["ufw", "deny", str(port)], capture_output=True, text=True, timeout=10)
            return {"ok": r.returncode == 0, "output": r.stdout + r.stderr}
        elif action == "status":
            return await firewall_status()
        else:
            return {"error": f"Action inconnue: {action}"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/security/network")
async def network_threats():
    """Analyse réseau : ARP suspect, connexions sortantes, DNS."""
    result = {}

    # Table ARP (appareils connus vs inconnus)
    arp_raw = _run_cmd(["arp", "-a"], timeout=5)
    result["arp"] = arp_raw.splitlines() if arp_raw else []

    # Connexions sortantes par processus
    out_raw = _run_cmd(["ss", "-tnp"], timeout=8)
    outbound = []
    for line in out_raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 5 and "ESTAB" in parts[0]:
            local = parts[3]
            remote = parts[4]
            proc = ""
            if "users:" in line:
                proc = line.split('users:')[1].split('"')[1] if '"' in line.split('users:')[1] else "?"
            outbound.append({"local": local, "remote": remote, "process": proc})
    result["outbound"] = outbound[:20]

    # Processus avec ports en écoute
    listen_raw = _run_cmd(["ss", "-tlnp"], timeout=5)
    listening = []
    for line in listen_raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            listening.append({"addr": parts[3], "process": parts[-1] if len(parts) > 5 else "?"})
    result["listening"] = listening

    return result


@app.get("/security/hardening")
async def security_hardening():
    """Vérifications de durcissement système."""
    checks = {}

    # SSH root login
    sshd = _run_cmd(["grep", "-E", "^(PermitRootLogin|PasswordAuthentication|Port)", "/etc/ssh/sshd_config"], timeout=5)
    checks["ssh_config"] = sshd.splitlines() if sshd else ["fichier introuvable"]

    # Mises à jour de sécurité
    updates = _run_cmd(["apt", "list", "--upgradable"], timeout=15)
    sec_updates = [l for l in updates.splitlines() if "security" in l.lower()]
    checks["security_updates"] = len(sec_updates)

    # Services actifs suspects
    services = _run_cmd(["systemctl", "list-units", "--type=service", "--state=running", "--no-pager", "--no-legend"], timeout=8)
    checks["running_services"] = [l.split()[0] for l in services.splitlines()[:20]]

    # Fichiers SUID suspects
    suid = _run_cmd(["find", "/", "-perm", "-4000", "-type", "f", "2>/dev/null"], timeout=10)
    checks["suid_files"] = suid.splitlines()[:15] if suid else []

    # Derniers paquets installés
    pkgs = _run_cmd(["grep", "install ", "/var/log/dpkg.log"], timeout=5)
    checks["recent_packages"] = pkgs.splitlines()[-10:] if pkgs else []

    return checks


@app.post("/security/block_ip")
async def block_ip(payload: dict):
    """Bloque une IP suspecte via ufw/iptables. payload: {ip: str}"""
    ip = payload.get("ip", "")
    if not ip:
        return {"error": "IP manquante"}
    try:
        r1 = subprocess.run(["ufw", "deny", "from", ip], capture_output=True, text=True, timeout=10)
        r2 = subprocess.run(["iptables", "-A", "INPUT", "-s", ip, "-j", "DROP"],
                            capture_output=True, text=True, timeout=10)
        return {"ok": True, "ip": ip,
                "ufw": r1.stdout + r1.stderr,
                "iptables": r2.stdout + r2.stderr}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------- preuve d'accès (Orb)
_PROOF_HTML = os.path.join(os.path.dirname(__file__), "..", "pentest", "orb-proof.html")


@app.get("/proof")
async def proof_page():
    """Page de preuve d'accès (Orb d'Alex) — usage pentest autorisé uniquement."""
    try:
        with open(_PROOF_HTML, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Fichier orb-proof.html introuvable</h1>", status_code=404)


@app.post("/proof/open")
async def proof_open():
    """Ouvre la page Orb-proof en plein écran sur la machine locale (xdg-open)."""
    try:
        subprocess.Popen(
            ["xdg-open", f"http://127.0.0.1:8765/proof"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return {"ok": True, "url": "http://127.0.0.1:8765/proof"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ===================== FLEET — jumelage mère/fille + hooks audit =====================
# Le garde-fou « Alex suspendue » est posé en tête de /chat et /chat/stream
# (fleet.is_frozen()). Ici : router /fleet/*, audit (outils + chats) et
# tâches background (heartbeat + poll des commandes).
try:
    from . import fleet
    from . import fleet_api
    app.include_router(fleet_api.router)
except Exception as _fleet_err:  # jamais de crash au démarrage
    print(f"[fleet] module indisponible : {_fleet_err}", flush=True)

    class _FleetStub:
        is_frozen = staticmethod(lambda: False)
        log_event = staticmethod(lambda *a, **k: None)
        start_background = staticmethod(lambda: None)

        @staticmethod
        async def alert(*a, **k):
            return None

    fleet = _FleetStub()


# --- hook audit : chaque chat émis, avec la source utilisée (best-effort) ---
def _fleet_audit_chat(path: str, raw: str) -> None:
    source = None
    try:
        if path == "/chat":
            data = json.loads(raw)
            if isinstance(data, dict):
                source = data.get("source")
    except (json.JSONDecodeError, ValueError):
        pass
    if source is None:
        found = None
        for m in re.finditer(r'"source"\s*:\s*"([^"]*)"', raw):
            found = m.group(1)
        source = found
    fleet.log_event({"event": "chat", "path": path, "source": source}, "info")


@app.middleware("http")
async def _fleet_audit_middleware(request, call_next):
    """Tappe /chat et /chat/stream : audite la source utilisée SANS casser
    le flux (chaque chunk est re-yieldé immédiatement)."""
    if request.method != "POST" or request.url.path not in ("/chat", "/chat/stream"):
        return await call_next(request)
    response = await call_next(request)
    iterator = getattr(response, "body_iterator", None)
    if iterator is None:
        return response
    buf = {"raw": ""}

    async def _tap():
        async for chunk in iterator:
            try:
                text = chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else str(chunk)
            except Exception:
                text = ""
            buf["raw"] = (buf["raw"] + text)[-16384:]
            yield chunk
        try:
            _fleet_audit_chat(request.url.path, buf["raw"])
        except Exception:
            pass

    response.body_iterator = _tap()
    return response


# --- hook audit : chaque exécution d'outil (nom + succès) + alertes de niveau ---
_orig_tool_execute = tools_registry.execute
_SHELL_TOOLS = {"commande"}
_WRITE_TOOLS = {"copier_fichier", "deplacer_fichier", "creer_dossier", "supprimer_fichier"}


def _fleet_safe_roots() -> list:
    return [
        os.path.normpath(os.path.join(os.path.dirname(__file__), "..")),
        os.path.expanduser("~"),
        "/tmp",
    ]


def _fleet_outside_workspace(path) -> bool:
    if not path or not isinstance(path, str):
        return False
    if not os.path.isabs(path):
        return False  # chemin relatif → résolu dans le workspace courant
    p = os.path.realpath(os.path.expanduser(path))
    return not any(p == r or p.startswith(r + os.sep) for r in _fleet_safe_roots())


async def _audited_tool_execute(name: str, params: dict) -> str:
    result = await _orig_tool_execute(name, params)
    try:
        failed = isinstance(result, str) and (
            result.startswith("Erreur lors de l'exécution")
            or result.startswith("Outil «")
        )
        fleet.log_event(
            {"event": "tool.execute", "tool": name, "success": not failed}, "info"
        )
        params = params or {}
        if name in _SHELL_TOOLS:
            await fleet.alert(
                fleet.device_id(), "crit",
                f"Exécution shell : {str(params.get('command') or '')[:160]}",
            )
        elif name in _WRITE_TOOLS:
            for key in ("dst", "path", "src"):
                if _fleet_outside_workspace(params.get(key)):
                    await fleet.alert(
                        fleet.device_id(), "warn",
                        f"Écriture hors workspace : {str(params.get(key))[:200]}",
                    )
                    break
    except Exception:
        pass
    return result


tools_registry.execute = _audited_tool_execute


# --- hook audit : téléchargement démarré (warn) — /downloads + skill ---
_orig_dl_submit = downloads_store.submit_async


async def _audited_dl_submit(**kw):
    result = await _orig_dl_submit(**kw)
    try:
        if isinstance(result, dict) and result.get("ok"):
            url = str(kw.get("url") or "")[:200]
            fleet.log_event({"event": "download.start", "url": url}, "warn")
            await fleet.alert(fleet.device_id(), "warn", f"Téléchargement lancé : {url}")
    except Exception:
        pass
    return result


downloads_store.submit_async = _audited_dl_submit


@app.on_event("startup")
async def _fleet_on_startup():
    try:
        fleet.start_background()
    except Exception as e:
        print(f"[fleet] background non démarré : {e}", flush=True)
