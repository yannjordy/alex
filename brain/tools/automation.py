import asyncio
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from . import tool

CALENDAR_FILE = Path.home() / ".alex_calendar.json"
ALARM_FILE = Path.home() / ".alex_alarms.json"


def _load_calendar() -> list[dict]:
    if CALENDAR_FILE.exists():
        try:
            return json.loads(CALENDAR_FILE.read_text())
        except Exception:
            return []
    return []


def _save_calendar(events: list[dict]):
    CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CALENDAR_FILE.write_text(json.dumps(events, indent=2, ensure_ascii=False))


def _load_alarms() -> list[dict]:
    if ALARM_FILE.exists():
        try:
            return json.loads(ALARM_FILE.read_text())
        except Exception:
            return []
    return []


def _save_alarms(alarms: list[dict]):
    ALARM_FILE.parent.mkdir(parents=True, exist_ok=True)
    ALARM_FILE.write_text(json.dumps(alarms, indent=2, ensure_ascii=False))


@tool("alarme", "Programme ou liste les alarmes. Paramètre : action (add/list/remove), time (HH:MM pour add), label (description).")
def alarm(action: str = "list", time: str = "", label: str = "") -> str:
    alarms = _load_alarms()
    if action == "list":
        if not alarms:
            return "Aucune alarme programmée."
        lines = ["🔔 Alarmes enregistrées :"]
        for i, a in enumerate(alarms, 1):
            lines.append(f"  {i}. {a['time']} — {a.get('label', 'Sans titre')}")
        return "\n".join(lines)

    if action == "add":
        if not time:
            return "Il me faut une heure (HH:MM) pour l'alarme."
        alarms.append({"time": time, "label": label or "Réveil"})
        _save_alarms(alarms)
        return f"Alarme programmée à {time} — {label or 'Réveil'} ✓"

    if action == "remove":
        if not time:
            return "Quelle alarme veux-tu supprimer ? (donne l'heure)"
        alarms = [a for a in alarms if a["time"] != time]
        _save_alarms(alarms)
        return f"Alarme à {time} supprimée ✓"

    return "Actions disponibles : add (HH:MM), list, remove (HH:MM)"


@tool("calendrier", "Gère les événements du calendrier local. Paramètre : action (add/list/remove/today), date (JJ/MM), time, title, description.")
def calendar_tool(action: str = "today", date: str = "", time: str = "",
                  title: str = "", description: str = "") -> str:
    events = _load_calendar()

    if action == "today":
        today = datetime.now().strftime("%d/%m")
        today_events = [e for e in events if e.get("date") == today]
        if not today_events:
            return "Rien de prévu aujourd'hui 📅"
        lines = ["📅 Aujourd'hui :"]
        for e in today_events:
            t = e.get("time", "")
            lines.append(f"  {t or '—'} {e['title']}")
        return "\n".join(lines)

    if action == "list":
        if not events:
            return "Calendrier vide."
        lines = ["📅 Événements :"]
        for e in sorted(events, key=lambda x: x.get("date", "") + x.get("time", "")):
            d = e.get("date", "??")
            t = e.get("time", "")
            lines.append(f"  {d} {t or '—'} {e['title']}")
        return "\n".join(lines)

    if action == "add":
        if not title:
            return "Quel est le titre de l'événement ?"
        events.append({
            "date": date or datetime.now().strftime("%d/%m"),
            "time": time or "",
            "title": title,
            "description": description or "",
        })
        _save_calendar(events)
        return f"Événement ajouté : {title} le {date or 'aujourd\'hui'} ✓"

    if action == "remove":
        if not title:
            return "Quel événement veux-tu supprimer ?"
        events = [e for e in events if e["title"].lower() != title.lower()]
        _save_calendar(events)
        return f"Événement « {title} » supprimé ✓"

    return "Actions : add, list, today, remove"


@tool("notifications", "Lit les notifications système récentes (via Dunst ou notify-send).")
def read_notifications() -> str:
    # Try reading Dunst history
    try:
        result = subprocess.run(
            ["dunstctl", "history"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            if output and output != "[]":
                return f"🔔 Dernières notifications :\n{output[:2000]}"
    except Exception:
        pass

    # Try reading from journal
    try:
        result = subprocess.run(
            ["journalctl", "--user", "-n", "10", "--no-pager", "-t", "notify-send"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            lines = result.stdout.strip().splitlines()
            return "🔔 Notifications récentes :\n" + "\n".join(lines[-10:])
    except Exception:
        pass

    return "Aucune notification récente trouvée."


@tool("notification_envoyer", "Envoie une notification système. Paramètre : title, message.")
def send_notification(title: str = "Alex", message: str = "") -> str:
    if not message:
        return "Que veux-tu que je notifie ?"
    try:
        subprocess.run(
            ["notify-send", title, message],
            timeout=3
        )
        return "Notification envoyée ✓"
    except Exception as e:
        return f"Impossible d'envoyer la notification : {e}"
