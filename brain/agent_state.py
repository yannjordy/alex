"""Identité d'agent d'Alex — persistée dans .alex-agent (racine du projet).

Quand Jordy choisit un agent dans le panneau Agents, Alex devient « Alex <agent> »
et se présente ainsi (voir sys_prompt() dans main.py).
"""
import os

AGENT_FILE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".alex-agent"))

# Ce ne sont pas des identités : modes et agents anonymes sans nom
NON_IDENTITY = {"plan", "build", "explore", "general"}
_ANON_PREFIXES = ("vision-", "task", "subtask")

AGENT_ROLES = {
    "ive": "orchestrateur central — code et coordination des agents",
    "stela": "agent email Gmail — lit, envoie et classe tes emails",
    "zoe": "administration Ubuntu — paquets, services et réseau",
    "nora": "candidatures — CV et lettres optimisés ATS",
    "panda": "cybersécurité — audit, investigation et détection",
    "max": "code ciblé — écrit, corrige et teste du code",
    "flore": "téléchargements en arrière-plan",
    "luna": "recherches web approfondies",
    "email-agent": "outils Gmail",
}


def load_agent() -> str:
    """Agent identité courant ("" = Alex tout court)."""
    try:
        if os.path.exists(AGENT_FILE):
            v = open(AGENT_FILE, encoding="utf-8").read().strip()
            if v and v not in NON_IDENTITY:
                return v
    except Exception:
        pass
    return ""


def save_agent(name: str) -> bool:
    """Persiste l'agent identité. Ignore les modes (plan/build) et agents anonymes."""
    name = (name or "").strip()
    if not name or name in NON_IDENTITY or name.startswith(_ANON_PREFIXES):
        return False
    try:
        with open(AGENT_FILE, "w", encoding="utf-8") as f:
            f.write(name)
        return True
    except Exception:
        return False


def clear_agent() -> None:
    try:
        os.remove(AGENT_FILE)
    except OSError:
        pass
