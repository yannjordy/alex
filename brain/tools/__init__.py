import asyncio
import uuid
from typing import Any, Callable, Optional

_tools: dict[str, dict] = {}
_pending_actions: dict[str, dict] = {}


def tool(name: str, description: str, dangerous: bool = False):
    def decorator(func: Callable):
        _tools[name] = {
            "func": func,
            "name": name,
            "description": description,
            "dangerous": dangerous,
        }
        return func
    return decorator


def list_tools() -> list[dict]:
    return [
        {"name": t["name"], "description": t["description"], "dangerous": t["dangerous"]}
        for t in _tools.values()
    ]


async def execute(name: str, params: dict) -> str:
    t = _tools.get(name)
    if not t:
        return f"Outil « {name} » inconnu."
    try:
        func = t["func"]
        if asyncio.iscoroutinefunction(func):
            return await func(**params)
        return func(**params)
    except Exception as e:
        return f"Erreur lors de l'exécution de « {name} » : {e}"


def create_pending(action: str, tool_name: str, params: dict, user_message: str) -> str:
    cid = uuid.uuid4().hex[:12]
    _pending_actions[cid] = {
        "tool": tool_name,
        "params": params,
        "action": action,
        "user_message": user_message,
    }
    return cid


def get_pending(cid: str) -> Optional[dict]:
    return _pending_actions.get(cid)


def remove_pending(cid: str):
    _pending_actions.pop(cid, None)


def get_tool_info(name: str) -> Optional[dict]:
    return _tools.get(name)
