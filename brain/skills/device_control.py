"""
ALEX — compétence "contrôle d'appareils" (étape 4/4, v1 : cette machine)

Reconnaissance simple par motifs (regex) de commandes système de base.
Volontairement basique pour commencer : pas de NLU, juste des motifs
courants en français. Le routage vers cette compétence se fait dans
main.py, avant d'interroger le modèle de langage — si une commande est
reconnue ici, on ne dérange pas le LLM.

Actions destructrices (éteindre/redémarrer) : nécessitent une phrase
de confirmation explicite séparée, pour éviter qu'une phrase mal
comprise n'éteigne l'ordinateur par erreur.

Prochaine étape naturelle (pas encore fait) : étendre à d'autres
appareils (téléphone, autres machines) via le protocole interne
"un adaptateur par appareil" déjà évoqué.
"""

import re
import shutil
import subprocess
import os
import glob


def _session_env():
    """Environnement graphique complet — le navigateur doit s'ouvrir même si le cerveau a été lancé sans DISPLAY."""
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/run/user/1000/bus"
    if not env.get("XAUTHORITY"):
        auths = sorted(glob.glob("/run/user/1000/.mutter-Xwaylandauth.*"))
        if auths:
            env["XAUTHORITY"] = auths[0]
    return env


def _default_browser_bin():
    """Binaire du navigateur par défaut du système (via xdg), ou None."""
    try:
        out = subprocess.run(
            ["xdg-mime", "query", "default", "x-scheme-handler/https"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        out = ""
    desk_map = {
        "google-chrome.desktop": "google-chrome",
        "google-chrome-stable.desktop": "google-chrome-stable",
        "chromium.desktop": "chromium",
        "chromium-browser.desktop": "chromium-browser",
        "firefox.desktop": "firefox",
        "firefox-esr.desktop": "firefox",
    }
    bin_ = desk_map.get(out)
    return bin_ if bin_ and shutil.which(bin_) else None


def _spawn(bin_: str, url: str, env, ozone: bool) -> bool:
    cmd = [bin_]
    if ozone and ("chrome" in bin_ or "chromium" in bin_):
        # --ozone-platform=x11 (Xwayland) : Wayland natif ne mappe pas la
        # fenêtre quand lancé en arrière-plan ; --new-window = fenêtre réelle
        cmd += ["--ozone-platform=x11", "--new-window", url]
    else:
        if "firefox" in bin_:
            cmd += ["--new-window", url]
        else:
            cmd += [url]
    try:
        subprocess.Popen(
            cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


def _open_url(url: str) -> bool:
    """Ouvre l'URL — navigateur par défaut d'abord, avec env session complet."""
    env = _session_env()
    # 0) Navigateur par défaut du système (xdg)
    default_bin = _default_browser_bin()
    if default_bin and _spawn(default_bin, url, env, ozone=True):
        return True
    # 1) Pile de secours : Chrome puis Chromium
    for bin_ in ("google-chrome-stable", "google-chrome", "chromium-browser", "chromium"):
        if shutil.which(bin_) and _spawn(bin_, url, env, ozone=True):
            return True
    # 2) Firefox (pas de flag ozone)
    if shutil.which("firefox") and _spawn("firefox", url, env, ozone=False):
        return True
    # 3) xdg-open (peut perdre l'env via DBus — dernier recours)
    if shutil.which("xdg-open"):
        try:
            subprocess.Popen(
                ["xdg-open", url], env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            return True
        except OSError:
            pass
    # 4) Dernier recours
    try:
        import webbrowser
        return bool(webbrowser.open(url))
    except Exception:
        return False

# Sites web connus → URLs
WEB_SITES = {
    "youtube": "https://www.youtube.com",
    "yt": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "maps": "https://maps.google.com",
    "github": "https://github.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "tiktok": "https://www.tiktok.com",
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
    "wikipedia": "https://fr.wikipedia.org",
    "amazon": "https://www.amazon.fr",
    "reddit": "https://www.reddit.com",
    "linkedin": "https://www.linkedin.com",
    "chatgpt": "https://chat.openai.com",
    "claude": "https://claude.ai",
    "deepl": "https://www.deepl.com",
    "translate": "https://translate.google.com",
    "meteo": "https://meteofrance.com",
    "doctolib": "https://www.doctolib.fr",
}

COMMANDS = [
    (re.compile(r"\b(?:ouvre|lance|démarre|va sur|affiche)\s+(.+)", re.I), "open_app"),
    (re.compile(r"\b(?:monte|augmente)\s+le\s+volume", re.I), "volume_up"),
    (re.compile(r"\b(?:baisse|diminue)\s+le\s+volume", re.I), "volume_down"),
    (re.compile(r"\b(?:coupe|active)\s+le\s+son", re.I), "toggle_mute"),
    (re.compile(r"verrouille\s+l.?écran", re.I), "lock_screen"),
    (re.compile(r"confirme\s+(?:l.?)?extinction", re.I), "confirm_shutdown"),
    (re.compile(r"confirme\s+(?:le\s+)?redémarrage", re.I), "confirm_reboot"),
    (re.compile(r"\b(?:éteins|éteindre)\s+l.?ordinateur", re.I), "ask_shutdown"),
    (re.compile(r"\bredémarr\w*\s+l.?ordinateur", re.I), "ask_reboot"),
]


def match(text: str):
    """Retourne (action, match) si le texte correspond à une commande connue, sinon (None, None)."""
    for pattern, action in COMMANDS:
        m = pattern.search(text)
        if m:
            if action == "open_app":
                arg = m.group(1).lower()
                if "commande" in arg or "shell" in arg or "terminal" in arg or len(arg.split()) > 4:
                    continue
            return action, m
    return None, None


def _open_app(name: str) -> str:
    clean = name.strip().lower().rstrip(".!?")

    # 1) Site web connu → ouvrir dans le navigateur
    if clean in WEB_SITES:
        _open_url(WEB_SITES[clean])
        return f"J'ouvre {clean.title()} dans le navigateur."

    # 2) URL directe
    if clean.startswith("http") or "." in clean and " " not in clean:
        url = clean if clean.startswith("http") else f"https://{clean}"
        _open_url(url)
        return f"J'ouvre {url}."

    # 3) Application locale
    slug = clean.replace(" ", "-")
    binary = shutil.which(slug) or shutil.which(clean)
    if binary:
        subprocess.Popen([binary])
        return f"J'ouvre {name.strip()}."

    try:
        subprocess.Popen(["gtk-launch", slug])
        return f"J'ouvre {name.strip()}."
    except FileNotFoundError:
        pass

    # 4) Dernier recours : recherche web
    from urllib.parse import quote
    _open_url(f"https://www.google.com/search?q={quote(name.strip())}")
    return f"Je ne trouve pas « {name.strip()} » en local — j'ai ouvert une recherche Google."


def run(action: str, match_obj) -> str:
    try:
        if action == "open_app":
            return _open_app(match_obj.group(1))

        if action == "volume_up":
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"], check=True)
            return "Volume augmenté de 10%."

        if action == "volume_down":
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"], check=True)
            return "Volume baissé de 10%."

        if action == "toggle_mute":
            subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"], check=True)
            return "Son activé/coupé."

        if action == "lock_screen":
            subprocess.run(["loginctl", "lock-session"], check=False)
            return "Écran verrouillé."

        if action == "ask_shutdown":
            return "Pour confirmer, dis « confirme extinction »."

        if action == "ask_reboot":
            return "Pour confirmer, dis « confirme redémarrage »."

        if action == "confirm_shutdown":
            subprocess.run(["systemctl", "poweroff"], check=False)
            return "Extinction en cours."

        if action == "confirm_reboot":
            subprocess.run(["systemctl", "reboot"], check=False)
            return "Redémarrage en cours."

    except FileNotFoundError as e:
        return f"Commande système introuvable sur cette machine : {e}"
    except subprocess.CalledProcessError as e:
        return f"Échec de la commande système : {e}"

    return "Action reconnue mais non implémentée."
