"""ALEX — système de todo persistant avec progression (x/y).

Stocker : ~/.alex/todo.json
- items : [{id, label, status: done|pending, created}]
- Alex (opencode) met à jour la todo via l'API du brain pendant son travail.
"""

import json
import os
import threading
import time
import uuid

TODO_FILE = os.path.expanduser("~/.alex/todo.json")
_lock = threading.Lock()


def _load() -> list:
    try:
        with open(TODO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save(items: list):
    os.makedirs(os.path.dirname(TODO_FILE), exist_ok=True)
    tmp = TODO_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TODO_FILE)


def list_items() -> dict:
    with _lock:
        items = _load()
    done = sum(1 for i in items if i.get("status") == "done")
    total = len(items)
    return {
        "items": items,
        "done": done,
        "total": total,
        "pending": total - done,
        "progress": f"{done}/{total}",
        "complete": total > 0 and done == total,
    }


def add(label: str) -> dict:
    label = (label or "").strip()
    if not label:
        raise ValueError("label vide")
    item = {
        "id": uuid.uuid4().hex[:8],
        "label": label,
        "status": "pending",
        "created": int(time.time()),
    }
    with _lock:
        items = _load()
        items.append(item)
        _save(items)
    return item


def set_status(item_id: str, status: str) -> dict:
    if status not in ("done", "pending"):
        raise ValueError("status invalide")
    with _lock:
        items = _load()
        for it in items:
            if it["id"] == item_id:
                it["status"] = status
                _save(items)
                return it
    raise KeyError(item_id)


def toggle(item_id: str) -> dict:
    with _lock:
        items = _load()
        for it in items:
            if it["id"] == item_id:
                it["status"] = "pending" if it.get("status") == "done" else "done"
                _save(items)
                return it
    raise KeyError(item_id)


def remove(item_id: str):
    with _lock:
        items = _load()
        new = [i for i in items if i["id"] != item_id]
        if len(new) == len(items):
            raise KeyError(item_id)
        _save(new)


def clear_done():
    with _lock:
        items = _load()
        _save([i for i in items if i.get("status") != "done"])


def find_by_label(label: str):
    label = (label or "").strip().lower()
    if not label:
        return None
    for it in _load():
        if label in it["label"].lower():
            return it
    return None


def context_for_llm() -> str:
    """État de la todo injecté dans les prompts pour qu'Alex en soit consciente."""
    d = list_items()
    if not d["items"]:
        return ""
    lines = ["", f"## TODO COURANT ({d['progress']} tâches faites)", ""]
    for it in d["items"]:
        mark = "[x]" if it.get("status") == "done" else "[ ]"
        lines.append(f"{mark} {it['id']} {it['label']}")
    lines += [
        "",
        "Synchronise cette todo dans ton travail :",
        "- quand une tâche est terminée, marque-la : curl -s -X POST http://127.0.0.1:8765/todo/{id}/toggle",
        "- quand une nouvelle tâche apparaît, ajoute-la : curl -s -X POST http://127.0.0.1:8765/todo -H 'Content-Type: application/json' -d '{\"label\": \"...\"}'",
        "- rapporte ta progression avec le compteur x/y.",
        "",
    ]
    return "\n".join(lines)
