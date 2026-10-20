"""ALEX — préférences utilisateur (formulaire de 1ʳᵉ ouverture avec mémoire).

Stocké : ~/.alex/prefs.json
- name, voice, themeColor, greeting, personality, onboarded,
  workspace_dir, account_username, ...
- Lu par sys_prompt() (nom de l'utilisateur) et par la GUI (onboarding).
"""

import json
import os
import threading

PREFS_FILE = os.path.expanduser("~/.alex/prefs.json")
_lock = threading.Lock()

DEFAULTS = {
    "name": "Jordy",
    "voice": "siwis",
    "themeColor": "#f43f5e",
    "greeting": "",
    "personality": "amicale",
    "onboarded": False,
    "workspace_dir": "~/alex-workspace",
    "account_username": "",
}


def workspace_dir_default() -> str:
    """Dossier de travail par défaut (non résolu)."""
    return (DEFAULTS.get("workspace_dir") or "~/alex-workspace").strip()


def workspace_dir() -> str:
    """Dossier de travail courant (chemin absolu, jamais vide)."""
    raw = (load().get("workspace_dir") or "").strip() or workspace_dir_default()
    return os.path.abspath(os.path.expanduser(raw))


def ensure_workspace_dir(raw) -> str:
    """Valide + crée le dossier de travail.

    expanduser puis absolu ; lève ValueError (→ HTTP 400) si le chemin est
    vide/illisible ou si le dossier ne peut pas être créé.
    """
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Chemin du dossier de travail vide.")
    path = os.path.abspath(os.path.expanduser(text))
    if os.path.exists(path) and not os.path.isdir(path):
        raise ValueError(f"Ce chemin existe déjà et n'est pas un dossier : {path}")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Impossible de créer le dossier {path} : {e.strerror or e}")
    return path


def load() -> dict:
    with _lock:
        data = dict(DEFAULTS)
        try:
            with open(PREFS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                data.update(saved)
        except (OSError, json.JSONDecodeError):
            pass
        return data


def save(partial: dict) -> dict:
    with _lock:
        data = dict(DEFAULTS)
        try:
            with open(PREFS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                data.update(saved)
        except (OSError, json.JSONDecodeError):
            pass
        if isinstance(partial, dict):
            data.update(partial)
        os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)
        tmp = PREFS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PREFS_FILE)
        return data


def user_name() -> str:
    return (load().get("name") or "Jordy").strip() or "Jordy"
