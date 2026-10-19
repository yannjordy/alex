"""ALEX — préférences utilisateur (formulaire de 1ʳᵉ ouverture avec mémoire).

Stocké : ~/.alex/prefs.json
- name, voice, themeColor, greeting, personality, onboarded, ...
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
}


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
