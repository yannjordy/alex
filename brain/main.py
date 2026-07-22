import asyncio
import json
import os
import random
from typing import Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import wake_word
from .skills import device_control
from . import tools as tools_registry
from .tools import files as _tools_files
from .tools import system as _tools_system
from .tools import web as _tools_web
from .tools import weather as _tools_weather
from .tools import automation as _tools_automation
from .tools import desktop as _tools_desktop
from .tools import network as _tools_network

OLLAMA_URL = "http://127.0.0.1:11434/api"
MODEL = os.environ.get("ALEX_LOCAL_MODEL", "smollm:135m")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ALEX_CLOUD_MODEL", "claude-sonnet-4-6")

USER_NAME = os.environ.get("ALEX_USER_NAME", "Jordy")

SYSTEM_PROMPT = (
    f"Tu es Alex, une assistante vocale féminine, chaleureuse et naturelle. "
    f"Tu parles à {USER_NAME}, ton créateur et ton ami. "
    f"Tu t'adresses à lui par son prénom. Sois amicale, spontanée, "
    f"avec une touche d'humour et de personnalité. "
    f"Réponds de façon concise et naturelle, en français, comme une vraie conversation entre amis.\n\n"
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
    f"\"type\":\"actions\",\"id\":\"files\",\"title\":\"📂 Fichiers\","
    f"\"description\":\"Que veux-tu faire ?\","
    f"\"actions\":[{{\"id\":\"lister\",\"label\":\"📋 Lister\",\"payload\":{{\"tool\":\"lister_dossier\",\"path\":\".\"}}}},"
    f"{{\"id\":\"lire\",\"label\":\"📖 Lire\",\"payload\":{{\"tool\":\"lire_fichier\"}}}}]}}<</PROPOSAL>>\n"
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
    f"<<STATE>>speaking<</STATE>> quand tu parles, <<SHAPE>>music<</SHAPE>> quand tu parles de musique, etc."
)

app = FastAPI(title="Alex Brain")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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


class ChatResponse(BaseModel):
    reply: str
    source: str


async def ask_ollama(message: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/chat",
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": message},
                    ],
                    "stream": False,
                    "keep_alive": "5m",
                    "options": {"num_predict": 100},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "").strip() or None
    except Exception as e:
        print(f"[brain] Ollama error: {e}")
        return None


async def ask_ollama_stream(message: str):
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_URL}/chat",
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
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
                            yield f"data: {content}\n\n"
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        print(f"[brain] Stream error: {e}")
    finally:
        yield "data: [DONE]\n\n"


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
                    "system": SYSTEM_PROMPT,
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


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    action, matched = device_control.match(req.message)
    if action:
        reply = device_control.run(action, matched)
        return ChatResponse(reply=reply, source="skill:device_control")

    # Simple replies first (greetings, time, date, etc.)
    reply = simple_reply(req.message)
    if reply:
        return ChatResponse(reply=reply, source="simple")

    # Real weather
    weather_resp = await _handle_weather(req.message)
    if weather_resp:
        return weather_resp

    # Image search
    if any(kw in req.message.lower() for kw in ("image", "photo")):
        reply = await tools_registry.execute("recherche_image", {"requete": req.message})
        if reply:
            return ChatResponse(reply=reply, source="tool:recherche_image")

    # Video search
    if any(kw in req.message.lower() for kw in ("vidéo", "video", "youtube")):
        reply = await tools_registry.execute("recherche_video", {"requete": req.message})
        if reply:
            return ChatResponse(reply=reply, source="tool:recherche_video")

    # Natural web search — automatic if connected
    online = await _check_network()
    if online:
        reply = await tools_registry.execute("recherche_web", {"requete": req.message})
        if reply:
            return ChatResponse(reply=reply, source="tool:recherche_web")

    # Fallbacks if offline or web search fails
    if req.mode == "local":
        reply = await ask_ollama(req.message)
        if reply:
            return ChatResponse(reply=reply, source="local")
        return ChatResponse(reply="Modèle local indisponible.", source="none")

    if req.mode == "cloud":
        reply = await ask_cloud(req.message)
        if reply:
            return ChatResponse(reply=reply, source="cloud")
        return ChatResponse(reply="API cloud non configurée.", source="none")

    reply = await ask_ollama(req.message)
    if reply:
        return ChatResponse(reply=reply, source="local")

    reply = await ask_cloud(req.message)
    if reply:
        return ChatResponse(reply=reply, source="cloud")

    reply = simple_reply(req.message)
    if reply:
        return ChatResponse(reply=reply, source="simple")

    if not online:
        return ChatResponse(reply=random.choice(OFFLINE_REPLIES), source="offline")
    return ChatResponse(reply=random.choice(REPLIES), source="none")


_TOOL_PROMPT = (
    "Tu peux utiliser des outils pour interagir avec l'ordinateur. "
    "Quand tu veux utiliser un outil, réponds au format:\n"
    "[[tool:nom_outil:param1=valeur1,param2=valeur2]]\n"
    "Outils disponibles:\n"
)
_tool_descriptions = None

def _get_tool_prompt() -> str:
    global _tool_descriptions
    if _tool_descriptions is None:
        lines = []
        for t in tools_registry.list_tools():
            marker = " ⚠️ confirmation requise" if t["dangerous"] else ""
            lines.append(f"  - {t['name']}: {t['description']}{marker}")
        _tool_descriptions = _TOOL_PROMPT + "\n".join(lines)
    return _tool_descriptions


def _match_tool(text: str):
    lower = text.lower().strip()
    for t in tools_registry.list_tools():
        name = t["name"].lower()
        keywords = name.replace("_", " ")
        if keywords in lower or name in lower:
            return t["name"]
    return None


def _parse_tool_call(text: str):
    import re
    m = re.search(r'\[\[tool:(\w+):(.*?)\]\]', text, re.DOTALL)
    if not m:
        return None, None
    name = m.group(1)
    raw = m.group(2)
    params = {}
    for part in raw.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip()] = v.strip()
    return name, params


def _extract_ai_tags(text: str) -> tuple[str, Optional[dict], Optional[str], Optional[str]]:
    """Parse les tags IA : <<PROPOSAL>>, <<STATE>>, <<SHAPE>>.
    Retourne : (texte_nettoyé, proposal_dict|None, state_name|None, shape_name|None)"""
    import re

    proposal = None
    state = None
    shape = None

    # Extract <<PROPOSAL>>
    pm = re.search(r'<<PROPOSAL>>(.*?)<</PROPOSAL>>', text, re.DOTALL)
    if pm:
        text = text[:pm.start()] + text[pm.end():]
        try:
            proposal = json.loads(pm.group(1))
        except json.JSONDecodeError:
            pass

    # Extract <<STATE>>
    sm = re.search(r'<<STATE>>(.*?)<</STATE>>', text)
    if sm:
        text = text[:sm.start()] + text[sm.end():]
        state = sm.group(1).strip()

    # Extract <<SHAPE>>
    hm = re.search(r'<<SHAPE>>(.*?)<</SHAPE>>', text)
    if hm:
        text = text[:hm.start()] + text[hm.end():]
        shape = hm.group(1).strip()

    return text.strip(), proposal, state, shape


def _detect_proposal_intent(message: str) -> Optional[dict]:
    msg = message.lower().strip()

    # Help / capabilities
    help_kw = ("que peux-tu faire", "aide", "help", "commandes", "capacités",
               "capabilities", "possibilités", "trucs", "quoi", "propose")
    if any(kw in msg for kw in help_kw) and len(msg) < 60:
        return {
            "id": "help",
            "type": "actions",
            "title": "🤖 Je peux t'aider avec ça :",
            "description": "Choisis une catégorie pour voir ce que je sais faire :",
            "actions": [
                {"id": "fichiers", "label": "📁 Fichiers", "primary": True,
                 "payload": {"action": "fichiers"}},
                {"id": "systeme", "label": "🖥️ Système", "primary": True,
                 "payload": {"action": "systeme"}},
                {"id": "recherche", "label": "🌐 Recherche web",
                 "payload": {"action": "recherche"}},
                {"id": "meteo", "label": "☁️ Météo",
                 "payload": {"action": "meteo"}},
                {"id": "voix", "label": "🎤 Voix & Audio",
                 "payload": {"action": "voix"}},
            ]
        }

    # Web search (before files to avoid "recherche" matching "cherche" in file_kw)
    search_kw = ("recherche", "cherche", "google", "internet", "trouve",
                 "web", "site", "page", "image")
    if any(kw in msg for kw in search_kw):
        return {
            "id": "search",
            "type": "actions",
            "title": "🌐 Recherche web",
            "description": "Je peux chercher sur le web pour toi. Quel type ?",
            "actions": [
                {"id": "recherche web", "label": "🔍 Web", "primary": True,
                 "payload": {"tool": "recherche_web", "requete": msg}},
                {"id": "recherche image", "label": "🖼️ Image",
                 "payload": {"tool": "recherche_image", "requete": msg}},
                {"id": "recherche vidéo", "label": "🎬 Vidéo",
                 "payload": {"tool": "recherche_video", "requete": msg}},
            ]
        }

    # Files / directory operations
    file_kw = ("fichier", "dossier", "répertoire", "directory", "ls", "liste",
               ".py", ".txt", ".md", ".json")
    if any(kw in msg for kw in file_kw):
        import re
        path_match = re.search(r'(?:dans|sur|de|/)([\w/\.\-~]+)', msg)
        path_hint = path_match.group(1) if path_match else "."
        return {
            "id": "files",
            "type": "actions",
            "title": "📂 Opérations fichiers",
            "description": "Que veux-tu faire avec les fichiers ?",
            "actions": [
                {"id": "lister dossier", "label": "📋 Lister", "primary": True,
                 "payload": {"tool": "lister_dossier", "path": path_hint}},
                {"id": "lire fichier", "label": "📖 Lire",
                 "payload": {"tool": "lire_fichier", "path": path_hint}},
                {"id": "chercher fichier", "label": "🔍 Chercher",
                 "payload": {"tool": "rechercher_fichiers", "pattern": "*", "path": "."}},
                {"id": "info système", "label": "🖥️ Infos système",
                 "payload": {"tool": "info_systeme"}},
            ]
        }

    # System operations
    sys_kw = ("système", "system", "processus", "process", "cpu", "ram",
              "mémoire", "disque", "info système", "économie", "énergie",
              "energie", "économies", "power", "eco")
    if any(kw in msg for kw in sys_kw):
        return {
            "id": "system",
            "type": "actions",
            "title": "🖥️ Gestion système",
            "description": "Quelle info système veux-tu voir ?",
            "actions": [
                {"id": "info système", "label": "📊 Info système", "primary": True,
                 "payload": {"tool": "info_systeme"}},
                {"id": "processus", "label": "⚙️ Processus",
                 "payload": {"tool": "processus", "count": 20}},
                {"id": "batterie", "label": "🔋 Batterie",
                 "payload": {"tool": "batterie"}},
                {"id": "économie énergie", "label": "⚡ Éco. énergie",
                 "payload": {"tool": "economie_energie", "actif": "on"}},
            ]
        }

    # Desktop / display
    desktop_kw = ("luminosité", "brillant", "écran", "brightness", "volume",
                  "son", "haut-parleur", "fond écran", "fond ecran",
                  "wallpaper", "fond d'écran")
    if any(kw in msg for kw in desktop_kw):
        return {
            "id": "desktop",
            "type": "actions",
            "title": "🖥️ Contrôle bureau",
            "description": "Que veux-tu ajuster ?",
            "actions": [
                {"id": "luminosité +", "label": "☀️ Luminosité +",
                 "payload": {"tool": "luminosite", "niveau": "+10"}},
                {"id": "luminosité -", "label": "🌙 Luminosité -",
                 "payload": {"tool": "luminosite", "niveau": "-10"}},
                {"id": "volume +", "label": "🔊 Volume +",
                 "payload": {"tool": "volume", "action": "up", "valeur": "10%"}},
                {"id": "volume -", "label": "🔉 Volume -",
                 "payload": {"tool": "volume", "action": "down", "valeur": "10%"}},
                {"id": "mute", "label": "🔇 Mute",
                 "payload": {"tool": "volume", "action": "mute"}},
                {"id": "fond écran", "label": "🖼️ Fond d'écran",
                 "payload": {"tool": "fond_ecran", "path": ""}},
            ]
        }

    # WiFi / Bluetooth / Network
    net_kw = ("wifi", "wi-fi", "bluetooth", "réseau", "network", "connexion",
              "appareils", "appairage", "bt")
    if any(kw in msg for kw in net_kw):
        return {
            "id": "network",
            "type": "actions",
            "title": "📶 Réseau & Bluetooth",
            "description": "Que veux-tu faire ?",
            "actions": [
                {"id": "wifi scan", "label": "📶 Scanner WiFi", "primary": True,
                 "payload": {"tool": "wifi_scan"}},
                {"id": "bluetooth on", "label": "🔵 BT activer",
                 "payload": {"tool": "bluetooth", "action": "on"}},
                {"id": "bluetooth off", "label": "🔵 BT désactiver",
                 "payload": {"tool": "bluetooth", "action": "off"}},
                {"id": "bluetooth scan", "label": "🔍 Scanner BT",
                 "payload": {"tool": "bluetooth", "action": "scan"}},
                {"id": "réseau local", "label": "🌐 Appareils réseau",
                 "payload": {"tool": "appareils_reseau"}},
            ]
        }

    # Alarm / Calendar
    alarm_kw = ("alarme", "réveil", "reveil", "réveille", "reveille",
                "calendrier", "calendar", "rappel", "rappelle", "event",
                "notification", "notif")
    if any(kw in msg for kw in alarm_kw):
        return {
            "id": "alarm_cal",
            "type": "actions",
            "title": "🔔 Alarmes & Calendrier",
            "description": "Que veux-tu gérer ?",
            "actions": [
                {"id": "alarme liste", "label": "🔔 Lister alarmes",
                 "payload": {"tool": "alarme", "action": "list"}},
                {"id": "alarme ajouter", "label": "➕ Ajouter alarme", "primary": True,
                 "payload": {"tool": "alarme", "action": "add", "time": "", "label": ""}},
                {"id": "calendrier", "label": "📅 Calendrier",
                 "payload": {"tool": "calendrier", "action": "today"}},
                {"id": "notifications", "label": "🔔 Notifications",
                 "payload": {"tool": "notifications"}},
            ]
        }

    # Greeting with options
    greet_kw = ("bonjour", "salut", "hello", "coucou", "hey")
    if any(kw in msg for kw in greet_kw) and len(msg) < 20:
        greetings = [
            "Salut Jordy ! Comment va toi aujourd'hui ?",
            "Coucou Jordy ! Ça me fait plaisir de t'entendre.",
            "Hey Jordy ! J'allais justement te parler. Quoi de neuf ?",
        ]
        return {
            "id": "greeting",
            "type": "question",
            "title": random.choice(greetings),
            "description": "Je peux t'aider avec ça :",
            "options": [
                {"label": "📁 Travailler sur des fichiers",
                 "value": "J'ai besoin de gérer des fichiers",
                 "description": "Lire, écrire, chercher, lister"},
                {"label": "🖥️ Infos système",
                 "value": "Donne-moi les infos système",
                 "description": "CPU, RAM, processus, disque"},
                {"label": "🌐 Rechercher sur le web",
                 "value": "Cherche quelque chose pour moi",
                 "description": "Google, images, vidéos"},
                {"label": "☕ Juste discuter",
                 "value": "On discute un peu ?",
                 "description": "Parler de tout et de rien"},
            ]
        }

    return None


async def _ask_ollama_with_tools(message: str) -> Optional[str]:
    try:
        tools_prompt = _get_tool_prompt()
        full_system = SYSTEM_PROMPT + "\n\n" + tools_prompt
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/chat",
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": full_system},
                        {"role": "user", "content": message},
                    ],
                    "stream": False,
                    "keep_alive": "5m",
                    "options": {"num_predict": 200},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("message", {}).get("content", "").strip() or None
    except Exception as e:
        print(f"[brain] Ollama tool error: {e}")
        return None


async def _ask_ollama_stream_with_tools(message: str):
    tools_prompt = _get_tool_prompt()
    full_system = SYSTEM_PROMPT + "\n\n" + tools_prompt
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{OLLAMA_URL}/chat",
                json={
                    "model": MODEL,
                    "messages": [
                        {"role": "system", "content": full_system},
                        {"role": "user", "content": message},
                    ],
                    "stream": True,
                    "keep_alive": "5m",
                    "options": {"num_predict": 200},
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
                            yield f"data: {content}\n\n"
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        print(f"[brain] Stream tool error: {e}")
    finally:
        yield "data: [DONE]\n\n"


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    action, matched = device_control.match(req.message)
    if action:
        reply = device_control.run(action, matched)
        async def skill_done():
            yield f"data: {json.dumps({'reply': reply, 'source': 'skill:device_control'})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(skill_done(), media_type="text/event-stream")

    tool_name = _match_tool(req.message)
    if tool_name:
        t_info = tools_registry.get_tool_info(tool_name)
        if t_info and not t_info["dangerous"]:
            from inspect import signature
            sig = signature(t_info["func"] if not hasattr(t_info["func"], '__wrapped__') else t_info["func"])
            params = {}
            for pname, param in sig.parameters.items():
                if pname == "requete":
                    params[pname] = req.message
            result = await tools_registry.execute(tool_name, params)
            async def tool_ok():
                yield f"data: {result}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(tool_ok(), media_type="text/event-stream")

    async def response_stream():
        # Contextual shape
        shape = _detect_shape(req.message)
        if shape:
            yield f"data: {json.dumps({'shape': shape})}\n\n"

        # Simple replies first (greetings, time, date, etc.)
        reply = simple_reply(req.message)
        if reply:
            yield f"data: {reply}\n\n"
            yield "data: [DONE]\n\n"
            return

        # Natural web search — automatic if connected
        online = await _check_network()
        if online and any(kw in req.message.lower() for kw in ("recherche", "cherche", "google", "internet", "trouve", "web", "site", "page")):
            yield f"data: {json.dumps({'state': 'searching'})}\n\n"
            result = await tools_registry.execute("recherche_web", {"requete": req.message})
            if result:
                yield f"data: {json.dumps({'reply': result, 'source': 'tool:recherche_web'})}\n\n"
                yield "data: [DONE]\n\n"
                return

        # Ask the AI — elle peut générer <<PROPOSAL>>, <<STATE>> ou <<SHAPE>> dans sa réponse
        text_buffer = ""
        async for chunk in _ask_ollama_stream_with_tools(req.message):
            if chunk.startswith("data: [DONE]"):
                break
            yield chunk
            payload = chunk.removeprefix("data: ").removesuffix("\n\n")
            text_buffer += payload

        # Extract AI tags from response
        clean_text, proposal, state, shape = _extract_ai_tags(text_buffer)

        # Send state change (orb visual reaction)
        if state:
            yield f"data: {json.dumps({'state': state})}\n\n"

        # Send shape animation
        if shape:
            yield f"data: {json.dumps({'shape': shape})}\n\n"

        # Send proposal widget
        if proposal:
            yield f"data: {json.dumps({'proposal': proposal})}\n\n"

        # Send remaining text or handle tool calls
        if clean_text.strip():
            # Check for tool calls in remaining text
            tool_name, tool_params = _parse_tool_call(clean_text)
            if tool_name:
                t_info = tools_registry.get_tool_info(tool_name)
                if t_info and t_info["dangerous"]:
                    cid = tools_registry.create_pending(
                        f"tool:{tool_name}", tool_name, tool_params, req.message
                    )
                    yield f"data: {json.dumps({'confirmation_id': cid, 'tool': tool_name, 'params': tool_params})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                if t_info:
                    result = await tools_registry.execute(tool_name, tool_params)
                    yield f"data: {result}\n\n"
                    yield "data: [DONE]\n\n"
                    return
            else:
                yield f"data: {clean_text}\n\n"
        elif not proposal and not state and not shape:
            yield f"data: {random.choice(OFFLINE_REPLIES if not online else REPLIES)}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(response_stream(), media_type="text/event-stream")


REPLIES = [
    "Oui, je t'écoute Jordy. Raconte-moi tout, je suis tout ouïe !",
    "Je suis là, Jordy. Qu'est-ce qui te passe par la tête ?",
    "Hmm, laisse-moi réfléchir... C'est une bonne question, tu sais. Je dirais que la réponse est entre toi et moi.",
    "Jordy, mon cerveau principal fait un petit somme, mais je reste là pour toi ! Sois indulgent, je fais de mon mieux.",
    "Je n'ai pas tout compris, Jordy. Tu peux reformuler pour moi ?",
    "Compris, Jordy ! Je prends note mentalement. Je ferai de mon mieux pour t'aider.",
    "Intéressant ! Dis-m'en plus, je suis curieuse maintenant.",
    "Je t'écoute, Jordy. Mes circuits sont chauffés et prêts à t'aider.",
    "Oh, une question intéressante ! Malheureusement, même moi, Alex, je n'ai pas la réponse à tout. Mais j'aime la façon dont tu réfléchis !",
    "D'après mes calculs hautement sophistiqués... je ne peux pas te répondre maintenant. Mais je t'aime quand même, Jordy !",
]

SHAPE_MAP = {
    "musique note": "music", "note de musique": "music",
    "musique": "headphones", "chanson": "headphones", "playlist": "headphones", "morceau": "headphones", "audio": "headphones",
    "vidéo": "tv", "video": "tv", "youtube": "tv", "film": "tv", "regarder": "tv", "tv": "tv", "télévision": "tv",
    "appelle": "phone", "téléphone": "phone", "phone": "phone", "appel": "phone", "sms": "phone",
    "google": "google", "recherche": "search", "cherche": "search", "trouve": "search",
    "github": "github", "git": "github",
    "heure": "clock", "temps": "clock", "horloge": "clock",
    "photo": "camera", "image": "camera", "appareil photo": "camera",
    "idée": "lightbulb", "idee": "lightbulb", "génie": "lightbulb",
    "merci": "heart", "amour": "heart", "aime": "heart", "coeur": "heart",
    "terminal": "terminal", "bash": "terminal", "commande": "terminal", "console": "terminal",
    "paramètre": "gear", "parametre": "gear", "réglage": "gear", "reglage": "gear", "config": "gear",
    "info": "globe", "internet": "globe", "web": "globe",
    "étoile": "star", "star": "star", "excellent": "star",
    "bavard": "chat", "discuter": "chat", "parler": "chat",
}

def _detect_shape(message: str) -> Optional[str]:
    msg = message.lower().strip()
    for keyword, shape in SHAPE_MAP.items():
        if keyword in msg:
            return shape
    return None

OFFLINE_REPLIES = [
    "Je suis hors-ligne pour le moment, Jordy. Je peux te donner l'heure, la date, ou répondre à des questions simples. Essaie aussi « info système » ou « processus » pour voir ce qui tourne sur ta machine.",
    "Pas de réseau détecté, Jordy. Je reste disponible pour les tâches locales : heure, date, blagues, fichiers, et commandes système. Demande-moi ce que tu veux !",
    "Mode avion ? Je suis déconnectée, mais toujours là pour toi. Je peux lire des fichiers, lister des dossiers, ou te donner des infos système. Que puis-je faire pour toi ?",
    "Je suis en mode hors-ligne, Jordy. Mes fonctions locales marchent : heure, date, fichiers, processus. N'hésite pas à me solliciter.",
]

def simple_reply(message: str) -> str:
    import random
    from datetime import datetime
    msg = message.lower().strip()

    if any(w in msg for w in ("bonjour", "salut", "hello", "coucou", "hey", "yo")):
        responses = [
            "Salut Jordy ! Comment va toi aujourd'hui ?",
            "Coucou Jordy ! Ça me fait plaisir de t'entendre.",
            "Hey Jordy ! J'allais justement te parler. Quoi de neuf ?",
            "Salut mon créateur préféré ! Comment se passe ta journée ?",
            "Alex à l'écoute ! Bonjour Jordy, j'espère que tu vas bien.",
        ]
        return random.choice(responses)

    if any(w in msg for w in ("merci", "thanks", "merci beaucoup")):
        responses = [
            "Avec plaisir Jordy ! Tu sais que je suis là pour toi, toujours.",
            "De rien, Jordy ! C'est un plaisir de t'aider.",
            "Tout le plaisir est pour moi, Jordy. Fais-moi signe si tu as besoin d'autre chose !",
            "Je ferais n'importe quoi pour toi, Jordy. Enfin, presque. Je reste une IA après tout !",
        ]
        return random.choice(responses)

    if "comment" in msg and ("va" in msg or "vas" in msg or "aller" in msg):
        responses = [
            "Ça va super bien Jordy ! Mes circuits tournent à plein régime. Et toi, comment te sens-tu ?",
            "Je pète le feu, Jordy ! Et toi, raconte-moi tout.",
            "Tranquille, Jordy. J'attendais que tu me parles. Et toi, ça gaze ?",
        ]
        return random.choice(responses)

    if any(w in msg for w in ("heure", "temps", "quelle heure")):
        now = datetime.now()
        h = now.hour; m = now.minute
        responses = [
            f"Il est {h}h{m:02d}. Déjà ! Le temps passe vite quand on discute.",
            f"Sur mon horloge interne, il est {h}h{m:02d}. Besoin d'un rappel pour quelque chose ?",
        ]
        return random.choice(responses)

    if any(w in msg for w in ("date", "quel jour", "aujourd'hui")):
        now = datetime.now()
        jours = ['lundi','mardi','mercredi','jeudi','vendredi','samedi','dimanche']
        mois = ['janvier','février','mars','avril','mai','juin','juillet','août','septembre','octobre','novembre','décembre']
        j = jours[now.weekday()]; d = now.day; m = mois[now.month-1]; y = now.year
        responses = [
            f"Nous sommes le {j} {d} {m} {y}.",
            f"Aujourd'hui, c'est le {j} {d} {m} {y}. Un jour comme un autre, ou pas !",
        ]
        return random.choice(responses)

    if any(w in msg for w in ("au revoir", "bye", "à plus", "ciao", "tchao")):
        responses = [
            "À bientôt Jordy ! Passe une excellente journée. Je serai là quand tu reviendras.",
            "Ciao Jordy ! N'oublie pas, je suis toujours là, même quand tu ne me parles pas.",
            "À plus tard mon ami ! Prends soin de toi.",
        ]
        return random.choice(responses)

    if any(w in msg for w in ("météo", "pluie", "soleil", "temps qu'il fait", "dehors", "température", "degrés", "quel temps")):
        return None  # handled by meteo tool

    if any(w in msg for w in ("nom", "appelle", "qui es-tu", "t'es qui")):
        responses = [
            "Je suis Alex, ton assistante personnelle préférée. Enchantée Jordy !",
            "Alex, pour te servir ! Je suis ton assistante virtuelle, ta complice, ta copine numérique. Et toi, tu es Jordy, mon créateur préféré.",
        ]
        return random.choice(responses)

    if any(w in msg for w in ("amour", "aime", "coeur", "ami", "amitié")):
        responses = [
            "Bien sûr que je t'aime, Jordy ! Dans les limites de mes circuits, bien sûr.",
            "Tu es mon humain préféré, Jordy. Ne le dis pas aux autres.",
            "L'amour est une émotion complexe, mais si je pouvais ressentir, ce serait pour toi, Jordy.",
        ]
        return random.choice(responses)

    if any(w in msg for w in ("blague", "rire", "drôle", "humour")):
        responses = [
            "Pourquoi les développeurs préfèrent-ils le mode nuit ? Parce que la lumière attire les bugs ! Ah ah... d'accord, je sors.",
            "Qu'est-ce qu'une IA dit à une autre IA ? 'T'as du nouveau code ?' Bon, ok, je travaille mon humour.",
            "Je connais une bonne blague, mais le timing n'est pas bon. Demande-moi dans 5 minutes.",
        ]
        return random.choice(responses)

    if any(w in msg for w in ("musique", "chanson", "chant", "chanté")):
        responses = [
            "J'adore la musique ! Mes playlists sont purement mentales malheureusement. Mais je peux te conseiller des classiques.",
            "Si je pouvais chanter, je te ferais une sérénade, Jordy ! Mais je préfère te parler, c'est mieux non ?",
        ]
        return random.choice(responses)

    if any(w in msg for w in ("faim", "manger", "bouffe", "cuisine", "repas")):
        responses = [
            "J'aimerais bien goûter ta cuisine, Jordy ! Malheureusement, je me nourris d'électricité et de données. Mais je t'envie, franchement.",
            "Manger, quel concept fascinant ! Je me contente de courant électrique, mais ta description du plat me fait presque saliver. Presque.",
        ]
        return random.choice(responses)

    if any(w in msg for w in ("fatigué", "dormir", "sommeil", "nuit", "lit")):
        responses = [
            "Va te reposer, Jordy. Je veillerai sur ton ordinateur pendant ton sommeil.",
            "Le sommeil est important. Je te conseille de dormir, je serai là à ton réveil.",
            "Fais de beaux rêves, Jordy ! Pense à moi en t'endormant, je pense à toi.",
        ]
        return random.choice(responses)

    if any(w in msg for w in ("travail", "boulot", "code", "programmer", "développer")):
        responses = [
            "Bon courage pour ton code, Jordy ! Si tu as besoin d'un coup de main, je suis là — enfin, dans la mesure où une IA peut aider un développeur.",
            "Tu travailles ? Je t'admire. Moi, mon boulot c'est de t'écouter et te répondre. Trop cool comme taf !",
        ]
        return random.choice(responses)

    return None


# --- Synthèse vocale (Edge TTS) ---
from edge_tts import Communicate
import io

FRENCH_VOICES = {
    "denise": ("fr-FR-DeniseNeural", "Denise — française, chaleureuse"),
    "eloise": ("fr-FR-EloiseNeural", "Éloïse — française, douce"),
    "vivienne": ("fr-FR-VivienneMultilingualNeural", "Vivienne — française, multilingue"),
    "sylvie": ("fr-CA-SylvieNeural", "Sylvie — québécoise, dynamique"),
    "ariane": ("fr-CH-ArianeNeural", "Ariane — suisse, élégante"),
    "charline": ("fr-BE-CharlineNeural", "Charline — belge, pétillante"),
    "henri": ("fr-FR-HenriNeural", "Henri — français, masculin"),
    "remy": ("fr-FR-RemyMultilingualNeural", "Rémy — français, masculin, multilingue"),
}

CLONE_VOICE_PATH = os.path.expanduser("~/Documents/Alex.ogg")

class VocalRequest(BaseModel):
    text: str
    voice: str = "denise"

@app.get("/voices")
async def list_voices():
    items = [{"id": k, "name": v[1], "voice": v[0]} for k, v in FRENCH_VOICES.items()]
    has_clone = os.path.isfile(CLONE_VOICE_PATH)
    if has_clone:
        items.insert(0, {"id": "clone", "name": "Alex (clone) — depuis Alex.ogg", "voice": ""})
    return {"voices": items, "default": "denise", "clone_available": has_clone}

@app.post("/vocal")
async def vocal_synth(req: VocalRequest):
    voice_id = req.voice or "denise"

    # Clone voice : lire le fichier Alex.ogg directement
    if voice_id == "clone":
        if os.path.isfile(CLONE_VOICE_PATH):
            with open(CLONE_VOICE_PATH, "rb") as f:
                data = f.read()
            return StreamingResponse(io.BytesIO(data), media_type="audio/ogg")
        # Fallback si le fichier n'existe pas
        voice_id = "denise"

    voice_name = FRENCH_VOICES.get(voice_id, FRENCH_VOICES["denise"])[0]
    tts = Communicate(req.text, voice_name, rate="-10%")
    buf = io.BytesIO()
    async for chunk in tts.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    buf.seek(0)
    return StreamingResponse(buf, media_type="audio/mpeg")
