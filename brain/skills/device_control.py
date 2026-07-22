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

COMMANDS = [
    (re.compile(r"\b(?:ouvre|lance|démarre)\s+(.+)", re.I), "open_app"),
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
            return action, m
    return None, None


def _open_app(name: str) -> str:
    slug = name.strip().lower().replace(" ", "-")

    binary = shutil.which(slug) or shutil.which(name.strip().lower())
    if binary:
        subprocess.Popen([binary])
        return f"J'ouvre {name.strip()}."

    try:
        subprocess.Popen(["gtk-launch", slug])
        return f"J'ouvre {name.strip()}."
    except FileNotFoundError:
        pass

    return f"Je ne trouve pas comment ouvrir « {name.strip()} » sur cette machine."


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
