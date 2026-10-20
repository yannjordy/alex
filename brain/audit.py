"""ALEX — journal d'audit local chaîné (hash-linké) + miroir Supabase best-effort.

Chaque ligne du JSONL ~/.alex/fleet/audit.jsonl :
  {"seq": int, "ts": ISO8601, "event": dict, "prev_hash": str, "hash": str}

hash = sha256(prev_hash + json.dumps(event, sort_keys, ensure_ascii=False,
separateurs=(",", ":")).encode())
La première ligne chaîne sur prev_hash = "" (vide).
La vérification recalcule toute la chaîne et renvoie le premier seq cassé.

Le miroir Supabase (table audit_log) est BEST-EFFORT : une panne du transport
ne doit jamais bloquer ni faire échouer l'audit local. Zero-trust : le transport
n'est pas une source de vérité, le journal local signé/chaîné l'est.
"""

import asyncio
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from typing import Optional

AUDIT_FILE = os.path.expanduser("~/.alex/fleet/audit.jsonl")
_lock = threading.Lock()


def _canonical_event(event: dict) -> str:
    return json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _compute_hash(prev_hash: str, event: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical_event(event)).encode("utf-8")).hexdigest()


def _read_tail() -> tuple:
    """(last_seq, last_hash) du fichier local — (0, "") si absent/vide."""
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as f:
            last = None
            for line in f:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        continue
        if last and isinstance(last.get("seq"), int):
            return last["seq"], last.get("hash") or ""
    except OSError:
        pass
    return 0, ""


def append(event: dict, level: str = "info") -> dict:
    """Ajoute une entrée chaînée au journal local (+ miroir Supabase best-effort).

    - level : info | warn | crit (informatique pour les alertes ; n'entre PAS
      dans le hash — seul `event` est chaîné, comme spécifié).
    """
    if not isinstance(event, dict):
        event = {"value": str(event)}
    with _lock:
        last_seq, prev_hash = _read_tail()
        seq = last_seq + 1
        ts = datetime.now(timezone.utc).isoformat()
        h = _compute_hash(prev_hash, event)
        entry = {
            "seq": seq,
            "ts": ts,
            "event": event,
            "prev_hash": prev_hash,
            "hash": h,
        }
        os.makedirs(os.path.dirname(AUDIT_FILE), exist_ok=True)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # miroir cloud best-effort (jamais bloquant)
    _mirror_async(entry)
    return entry


def verify() -> dict:
    """Recalcule toute la chaîne locale.

    Retourne {"ok": bool, "broken_at": int | None, "count": int}.
    broken_at = seq de la première entrée dont le hash ne correspond pas.
    """
    broken_at = None
    count = 0
    prev_hash = ""
    prev_seq = 0
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    e = json.loads(raw)
                except json.JSONDecodeError:
                    # ligne corrompue → chaîne brisée à cette position
                    return {"ok": False, "broken_at": prev_seq + 1, "count": count}
                seq = e.get("seq")
                event = e.get("event") or {}
                expected = _compute_hash(prev_hash, event)
                if (
                    seq != prev_seq + 1
                    or e.get("prev_hash") != prev_hash
                    or e.get("hash") != expected
                ):
                    broken_at = seq if isinstance(seq, int) else prev_seq + 1
                    return {"ok": False, "broken_at": broken_at, "count": count}
                prev_hash = e["hash"]
                prev_seq = seq
                count += 1
    except OSError:
        return {"ok": True, "broken_at": None, "count": 0}
    return {"ok": True, "broken_at": None, "count": count}


def entries(limit: int = 100) -> list:
    """Dernières entrées du journal local (les plus récentes en dernier)."""
    out = []
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass
    return out[-limit:] if limit and limit > 0 else out


# ---------------------------------------------------------------------------
# Miroir Supabase best-effort (fire-and-forget, jamais bloquant)
# ---------------------------------------------------------------------------

def _mirror_async(entry: dict) -> None:
    from . import fleet  # import tardif : évite une boucle d'imports

    if not fleet.is_configured():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # appel depuis un thread sans boucle → thread daemon détaché
        import threading as _t

        def _run():
            try:
                asyncio.run(_mirror_supabase(entry))
            except Exception:
                pass

        _t.Thread(target=_run, daemon=True).start()
        return
    loop.create_task(_safe_mirror(entry))


async def _safe_mirror(entry: dict) -> None:
    try:
        await _mirror_supabase(entry)
    except Exception:
        pass  # transport non fiable : le local reste la source de vérité


async def _mirror_supabase(entry: dict) -> None:
    from . import fleet

    device_id = fleet.device_id()
    if not device_id:
        return
    await fleet.sb_insert(
        "audit_log",
        {
            "device_id": device_id,
            "seq": entry["seq"],
            "ts": entry["ts"],
            "event": entry["event"],
            "prev_hash": entry["prev_hash"],
            "hash": entry["hash"],
        },
    )
