"""ALEX — Fleet : jumelage visible et consenti d'instances Alex (mère / fille).

CONTRÔLE ADMINISTRATEUR — modèle zero-trust :
- Supabase est un TRANSPORT NON FIABLE. Toute la sécurité est dans les
  signatures Ed25519 (pynacl). Une Supabase compromise ne peut PAS :
  * fabriquer un stop/start (elle n'a pas la clé privée mère), ni
  * rejouer une commande (seq strictement croissant stocké côté fille,
    signature vérifiée contre mother_pubkey, rejet si seq <= last_seq).
- RESTE POSSIBLE (documenté volontairement) : un DoS via fausses alertes —
  quiconque a la clé service role peut insérer des lignes dans `alerts`.
  C'est un risque de désinformation UI, pas d'exécution : les alertes ne
  déclenchent aucune action, les commandes restent signées.
- La fille peut SE DISSOCIER à tout moment (POST /fleet/unlink) : on oublie
  mother_pubkey + cert, on supprime le fichier `frozen`.

Identité locale (~/.alex/fleet/) :
- device.key   : clé Ed25519 de l'appareil (chmod 600, générée au 1er accès)
- fleet.json   : {"device_id", "username", "mother_pubkey", "cert",
                  "last_seq", "linked"}
- frozen       : fichier présent = Alex suspendue par la mère
- audit.jsonl  : journal local chaîné (voir brain/audit.py)

Config (env, jamais dans le repo) :
- ALEX_SUPABASE_URL, ALEX_SUPABASE_KEY (service role), ALEX_FLEET_ROLE.
- Sans ces vars : tout tourne en mode « non configuré », aucun crash.
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import socket
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from . import audit as audit_log
from . import prefs as prefs_store

try:
    from nacl.signing import SigningKey, VerifyKey
    from nacl.exceptions import BadSignatureError
    NACL_OK = True
except Exception:  # pynacl absent → fleet « non configuré » dégradé, pas de crash
    NACL_OK = False
    SigningKey = VerifyKey = None  # type: ignore
    BadSignatureError = Exception  # type: ignore

FLEET_DIR = os.path.expanduser("~/.alex/fleet")
DEVICE_KEY_FILE = os.path.join(FLEET_DIR, "device.key")
FLEET_JSON = os.path.join(FLEET_DIR, "fleet.json")
FROZEN_FILE = os.path.join(FLEET_DIR, "frozen")


def _load_dotenv() -> None:
    """Charge brain/.env puis .env racine — setdefault : l'env réel prime."""
    import pathlib
    here = pathlib.Path(__file__).resolve().parent
    for p in (here / ".env", here.parent / ".env"):
        try:
            if not p.is_file():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


_load_dotenv()

SUPABASE_URL = (os.environ.get("ALEX_SUPABASE_URL") or "").rstrip("/")
SUPABASE_KEY = os.environ.get("ALEX_SUPABASE_KEY") or ""
ROLE = (os.environ.get("ALEX_FLEET_ROLE") or "daughter").strip().lower()
if ROLE not in ("mother", "daughter"):
    ROLE = "daughter"

_lock = threading.RLock()
_identity_cache: Optional[dict] = None
_tasks_started = False

COMMAND_POLL_S = 15.0
HEARTBEAT_S = 30.0
ONLINE_WINDOW_S = 90


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY and NACL_OK)


def current_role() -> str:
    return ROLE


# ---------------------------------------------------------------------------
# Identité locale
# ---------------------------------------------------------------------------

def _ensure_dir() -> None:
    os.makedirs(FLEET_DIR, mode=0o700, exist_ok=True)


def _load_or_create_identity() -> dict:
    """Charge ~/.alex/fleet/fleet.json ; génère la clé Ed25519 au premier accès."""
    global _identity_cache
    with _lock:
        if _identity_cache is not None:
            return _identity_cache
        _ensure_dir()
        ident = {
            "device_id": None,
            "username": None,
            "mother_pubkey": None,
            "mother_username": None,
            "cert": None,
            "last_seq": 0,
            "linked": False,
        }
        try:
            with open(FLEET_JSON, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                ident.update({k: saved.get(k, ident.get(k)) for k in ident})
        except (OSError, json.JSONDecodeError):
            pass
        if not ident["device_id"]:
            import uuid
            ident["device_id"] = str(uuid.uuid4())
        # clé Ed25519 : générée au premier accès, chmod 600
        if NACL_OK:
            if not os.path.exists(DEVICE_KEY_FILE):
                sk = SigningKey.generate()
                with open(DEVICE_KEY_FILE, "wb") as f:
                    f.write(bytes(sk))
                os.chmod(DEVICE_KEY_FILE, 0o600)
        _save_identity_locked(ident)
        _identity_cache = ident
        return ident


def _save_identity_locked(ident: dict) -> None:
    tmp = FLEET_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ident, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FLEET_JSON)


def save_identity(partial: dict) -> dict:
    with _lock:
        ident = _load_or_create_identity()
        ident.update(partial or {})
        _save_identity_locked(ident)
        _identity_cache = ident
        return dict(ident)


def device_id() -> Optional[str]:
    try:
        return _load_or_create_identity()["device_id"]
    except Exception:
        return None


def _signing_key() -> Optional["SigningKey"]:
    if not NACL_OK:
        return None
    try:
        _load_or_create_identity()
        with open(DEVICE_KEY_FILE, "rb") as f:
            return SigningKey(f.read())
    except Exception:
        return None


def device_pubkey_b64() -> Optional[str]:
    sk = _signing_key()
    if not sk:
        return None
    return base64.b64encode(bytes(sk.verify_key)).decode("ascii")


def _username() -> str:
    return (prefs_store.user_name() or "Alex").strip() or "Alex"


def _device_name() -> str:
    return socket.gethostname()


# ---------------------------------------------------------------------------
# Transport Supabase REST (httpx — PAS de paquet supabase-py)
# ---------------------------------------------------------------------------

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def _sb_url(path: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{path}"


async def sb_request(method: str, path: str, *, params: dict = None,
                     json_body=None, headers: dict = None) -> httpx.Response:
    """Requête REST Supabase générique. L'appelant gère les erreurs."""
    h = _sb_headers()
    if headers:
        h.update(headers)
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.request(
            method, _sb_url(path), params=params, json=json_body, headers=h
        )


async def sb_insert(table: str, row: dict) -> bool:
    """Insert best-effort (jamais bloquant pour l'appelant)."""
    if not is_configured():
        return False
    try:
        r = await sb_request("POST", table, json_body=row,
                             headers={"Prefer": "return=minimal"})
        return r.status_code < 300
    except Exception:
        return False


async def sb_upsert(table: str, row: dict, on_conflict: str) -> bool:
    if not is_configured():
        return False
    try:
        r = await sb_request(
            "POST", table, json_body=row,
            params={"on_conflict": on_conflict},
            headers={"Prefer": "return=minimal,resolution=merge-duplicates"},
        )
        return r.status_code < 300
    except Exception:
        return False


async def sb_select(table: str, params: dict) -> list:
    """SELECT ; renvoie [] si non configuré / erreur (jamais de crash)."""
    if not is_configured():
        return []
    try:
        r = await sb_request("GET", table, params=params)
        if r.status_code >= 300:
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def sb_patch(table: str, params: dict, body: dict) -> bool:
    if not is_configured():
        return False
    try:
        r = await sb_request("PATCH", table, params=params, json_body=body,
                             headers={"Prefer": "return=minimal"})
        return r.status_code < 300
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Frozen / suspension
# ---------------------------------------------------------------------------

def is_frozen() -> bool:
    try:
        return os.path.exists(FROZEN_FILE)
    except Exception:
        return False


def _set_frozen(on: bool) -> None:
    _ensure_dir()
    if on:
        with open(FROZEN_FILE, "w", encoding="utf-8") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    else:
        try:
            os.remove(FROZEN_FILE)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Journal + alertes
# ---------------------------------------------------------------------------

def log_event(event: dict, level: str = "info") -> None:
    """Audit local chaîné (best-effort vers Supabase via audit.py)."""
    try:
        audit_log.append(event, level)
    except Exception:
        pass


async def alert(device_id_: str, level: str, message: str) -> None:
    """Insère une ligne dans `alerts` (best-effort).

    L'appelant journalise lui-même l'événement métier dans l'audit via
    log_event() — ici on ne duplique pas l'entrée.
    """
    await sb_insert("alerts", {
        "device_id": device_id_, "level": level, "message": message,
    })


# ---------------------------------------------------------------------------
# Signature / vérification (canonical JSON, Ed25519)
# ---------------------------------------------------------------------------

def canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def sign_payload(priv: "SigningKey", payload: dict) -> str:
    msg = canonical_json(payload).encode("utf-8")
    return base64.b64encode(priv.sign(msg).signature).decode("ascii")


def verify_payload(pub_b64: str, payload: dict, sig_b64: str) -> bool:
    """Vérifie une signature Ed25519 contre la pubkey mère (base64)."""
    if not NACL_OK or not pub_b64 or not sig_b64:
        return False
    try:
        vk = VerifyKey(base64.b64decode(pub_b64))
        vk.verify(canonical_json(payload).encode("utf-8"),
                  base64.b64decode(sig_b64))
        return True
    except (BadSignatureError, Exception):
        return False


# ---------------------------------------------------------------------------
# Pairing (fille)
# ---------------------------------------------------------------------------

async def pairing_generate() -> dict:
    """Génère un code à 8 chiffres (10 min, un seul actif à la fois).

    Stocke sha256(code) — jamais le code en clair — dans `pairings`.
    """
    if not is_configured():
        raise RuntimeError("fleet non configuré")
    sk = _signing_key()
    if not sk:
        raise RuntimeError("clé Ed25519 indisponible")
    code = f"{secrets.randbelow(10**8):08d}"
    code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
    pubkey = device_pubkey_b64()
    ident = _load_or_create_identity()
    # un seul pairing actif : on invalide les anciens de cet appareil
    # (filtrage par pubkey = identité réelle du device)
    await sb_request(
        "PATCH", "pairings",
        params={"pubkey": f"eq.{pubkey}"},
        json_body={"expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()},
        headers={"Prefer": "return=minimal"},
    )
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
    ok = await sb_insert("pairings", {
        "code_hash": code_hash,
        "pubkey": pubkey,
        "username": _username(),
        "device_name": _device_name(),
        "expires_at": expires_at,
    })
    if not ok:
        raise RuntimeError("écriture Supabase impossible")
    save_identity({"username": _username()})
    log_event({"event": "pairing.generate", "device_id": ident["device_id"],
               "username": _username()}, "crit")
    await alert(ident["device_id"], "crit",
                f"Code de jumelage généré pour {_username()} / {_device_name()}")
    return {"code": code, "expires_in": 600}


async def pairing_status() -> dict:
    """Poll fille : none | pending | linked.

    Quand la mère a approuvé avec le BON code, la row contient approved_cert
    (signé par la mère) : on le vérifie contre mother_pubkey, on le stocke,
    linked = true.
    """
    ident = _load_or_create_identity()
    base = {"state": "none", "username": ident.get("username"),
            "mother_username": None}
    if ident.get("linked"):
        base["state"] = "linked"
        base["mother_username"] = ident.get("mother_username")
        return base
    if not is_configured():
        return base
    pubkey = device_pubkey_b64()
    rows = await sb_select("pairings", {
        "select": "*",
        "pubkey": f"eq.{pubkey}",
        "order": "created_at.desc",
        "limit": "10",
    })
    if not rows:
        return base
    # L'ordre rendu par le cloud n'est pas une garantie (timestamps parfois
    # identiques) : tri local défensif, puis on préfère systématiquement une
    # row approuvée (cert) à une row expirée sans cert — sinon un poll pourrait
    # retomber sur un ancien pairing expiré et afficher `none` au lieu de
    # `linked`.
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    now = datetime.now(timezone.utc)

    def _not_expired(r: dict) -> bool:
        exp = r.get("expires_at")
        if not exp:
            return True
        try:
            return datetime.fromisoformat(exp.replace("Z", "+00:00")) >= now
        except ValueError:
            return True

    approved = [r for r in rows if r.get("approved_cert")]
    if approved:
        row = approved[0]
    else:
        valid = [r for r in rows if _not_expired(r)]
        if not valid:
            return base
        row = valid[0]
    cert = row.get("approved_cert")
    if not cert:
        base["state"] = "pending"
        base["username"] = row.get("username") or ident.get("username")
        return base
    # approuvée : valider le cert signé par la mère
    try:
        cert_obj = json.loads(cert)
        mother_pub = cert_obj.get("mother_pubkey")
        sig = cert_obj.get("sig")
        payload = cert_obj.get("payload") or {}
    except (json.JSONDecodeError, AttributeError):
        return base
    if not verify_payload(mother_pub, payload, sig):
        # transport non fiable : cert falsifié → on n'accepte pas
        log_event({"event": "pairing.cert_invalid"}, "warn")
        return base
    if payload.get("daughter_pub") != pubkey:
        return base
    save_identity({
        "mother_pubkey": mother_pub,
        "cert": base64.b64encode(cert.encode("utf-8")).decode("ascii"),
        "linked": True,
        "username": payload.get("username") or ident.get("username"),
        "mother_username": cert_obj.get("mother_username"),
    })
    log_event({"event": "pairing.linked",
               "device_id": ident["device_id"],
               "mother_username": cert_obj.get("mother_username")}, "crit")
    await alert(ident["device_id"], "crit",
                f"Jumelage accepté par {cert_obj.get('mother_username') or 'la mère'}")
    base["state"] = "linked"
    base["username"] = payload.get("username")
    base["mother_username"] = cert_obj.get("mother_username")
    return base


# ---------------------------------------------------------------------------
# Mère : pending / approve / devices / command / revoke
# ---------------------------------------------------------------------------

async def mother_pending() -> list:
    """Paires non expirées en attente d'approbation."""
    if not is_configured():
        return []
    now = datetime.now(timezone.utc).isoformat()
    rows = await sb_select("pairings", {
        "select": "id,username,device_name,created_at,expires_at,approved_cert",
        "expires_at": f"gt.{now}",
        "order": "created_at.asc",
    })
    out = []
    for r in rows:
        if r.get("approved_cert"):
            continue
        out.append({
            "pairing_id": r.get("id"),
            "username": r.get("username"),
            "device_name": r.get("device_name"),
            "created_at": r.get("created_at"),
        })
    return out


async def mother_approve(pairing_id, code: str) -> dict:
    """La fille montre son écran : le code prouve sa présence physique.

    Vérifie sha256(code) == code_hash SINON 403 ; puis signe un cert
    {"daughter_pub", "username", "exp"} avec la clé privée mère et
    l'écrit dans la row.
    """
    if not is_configured():
        raise RuntimeError("fleet non configuré")
    sk = _signing_key()
    if not sk:
        raise RuntimeError("clé Ed25519 mère indisponible")
    rows = await sb_select("pairings", {
        "select": "*",
        "id": f"eq.{pairing_id}",
        "limit": "1",
    })
    if not rows:
        raise LookupError("pairing introuvable")
    row = rows[0]
    code_hash = hashlib.sha256((code or "").encode("utf-8")).hexdigest()
    if not secrets.compare_digest(code_hash, row.get("code_hash") or ""):
        log_event({"event": "pairing.approve_bad_code",
                   "pairing_id": pairing_id}, "warn")
        raise PermissionError("code incorrect")
    exp = int((datetime.now(timezone.utc) + timedelta(days=365)).timestamp())
    payload = {
        "daughter_pub": row.get("pubkey"),
        "username": row.get("username"),
        "exp": exp,
    }
    sig = sign_payload(sk, payload)
    cert_obj = {
        "payload": payload,
        "sig": sig,
        "mother_pubkey": device_pubkey_b64(),
        "mother_username": _username(),
    }
    ok = await sb_patch(
        "pairings", {"id": f"eq.{pairing_id}"},
        {"approved_cert": json.dumps(cert_obj, ensure_ascii=False)},
    )
    if not ok:
        raise RuntimeError("écriture Supabase impossible")
    # la fille crée/met à jour sa propre row `devices` via son heartbeat
    log_event({"event": "pairing.approved",
               "pairing_id": pairing_id,
               "username": row.get("username")}, "crit")
    return {"ok": True, "username": row.get("username")}


async def mother_devices() -> list:
    """Liste des appareils ; status : frozen→stopped, <90s→online, sinon offline.

    Mapping strictement littéral du contrat (aucun cas supplémentaire) : la
    révocation n'apparaît pas dans `status` (heartbeat qui stagne → offline
    naturellement), le champ `frozen` reste exposé pour lever l'ambiguïté.
    """
    if not is_configured():
        return []
    rows = await sb_select("devices", {
        "select": "*", "order": "last_seen.desc",
    })
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        if r.get("status") == "revoked":
            continue  # lien rompu : plus listable (reste dans alertes/journal)
        frozen = bool(r.get("frozen"))
        last_seen = r.get("last_seen")
        online = False
        if last_seen:
            try:
                dt = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
                online = (now - dt).total_seconds() < ONLINE_WINDOW_S
            except ValueError:
                pass
        if frozen:
            status = "stopped"
        elif online:
            status = "online"
        else:
            status = "offline"
        out.append({
            "id": r.get("id"),
            "username": r.get("username"),
            "device_name": r.get("device_name"),
            "status": status,
            "last_seen": last_seen,
            "version": r.get("version"),
            "workspace": r.get("workspace"),
            "frozen": frozen,
        })
    return out


async def mother_command(device_id_: str, cmd_type: str) -> dict:
    """Écrit une commande signée (seq strictement croissant) dans `commands`."""
    if not is_configured():
        raise RuntimeError("fleet non configuré")
    sk = _signing_key()
    if not sk:
        raise RuntimeError("clé Ed25519 mère indisponible")
    if cmd_type not in ("stop", "start"):
        raise ValueError("type invalide")
    rows = await sb_select("commands", {
        "select": "seq", "device_id": f"eq.{device_id_}",
        "order": "seq.desc", "limit": "1",
    })
    last_seq = int(rows[0]["seq"]) if rows and rows[0].get("seq") is not None else 0
    seq = last_seq + 1
    payload = {"device_id": device_id_, "seq": seq, "type": cmd_type}
    sig = sign_payload(sk, payload)
    ok = await sb_insert("commands", {
        "device_id": device_id_, "seq": seq, "type": cmd_type, "sig": sig,
    })
    if not ok:
        raise RuntimeError("écriture Supabase impossible")
    log_event({"event": f"command.{cmd_type}", "device_id": device_id_,
               "seq": seq}, "crit")
    return {"ok": True, "seq": seq}


async def mother_revoke(device_id_: str) -> dict:
    """Révoque un appareil : status=revoked → la fille oublie la mère au prochain poll."""
    if not is_configured():
        raise RuntimeError("fleet non configuré")
    ok = await sb_patch("devices", {"id": f"eq.{device_id_}"},
                        {"status": "revoked"})
    if not ok:
        raise RuntimeError("écriture Supabase impossible")
    log_event({"event": "device.revoke", "device_id": device_id_}, "crit")
    await alert(device_id_, "crit", "Appareil révoqué par l'administrateur")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Fille : heartbeat + poll des commandes
# ---------------------------------------------------------------------------

def _app_version() -> str:
    try:
        pkg = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "package.json"))
        with open(pkg, "r", encoding="utf-8") as f:
            return str(json.load(f).get("version") or "")
    except Exception:
        return ""


def _workspace() -> str:
    try:
        import pathlib
        return str(pathlib.Path(__file__).resolve().parent.parent)
    except Exception:
        return ""


async def daughter_heartbeat_once() -> None:
    """Upsert de la row devices (last_seen, version, workspace, frozen, username).

    Continue même si frozen=true (la mère doit voir « stopped »).
    """
    if not is_configured() or ROLE != "daughter":
        return
    ident = _load_or_create_identity()
    if not ident.get("linked"):
        return
    # NB : pas de champ "status" dans l'upsert — sinon le heartbeat
    # réactiverait un appareil révoqué par la mère (status=revoked).
    await sb_upsert("devices", {
        "id": ident["device_id"],
        "username": ident.get("username") or _username(),
        "device_name": _device_name(),
        "pubkey": device_pubkey_b64(),
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "version": _app_version(),
        "workspace": _workspace(),
        "frozen": is_frozen(),
    }, "id")


async def daughter_poll_commands_once() -> None:
    """Pour chaque row non traitée avec seq > last_seq : vérifie sig + type.

    - stop  → crée ~/.alex/fleet/frozen + audit + alerte crit
    - start → supprime frozen + audit
    - status=revoked → oublie mother_pubkey/cert, linked=false, supprime
      frozen, audit (dissociation forcée par la mère)
    Zero-trust : la Supabase ne peut ni fabriquer (pas la clé privée mère)
    ni rejouer (seq strictement croissant, rejet si seq <= last_seq).
    """
    if not is_configured() or ROLE != "daughter":
        return
    ident = _load_or_create_identity()
    # révocation : détectée même sans linked (état résiduel)
    devs = await sb_select("devices", {
        "select": "status,frozen", "id": f"eq.{ident['device_id']}", "limit": "1",
    })
    if devs and (devs[0].get("status") == "revoked"):
        if ident.get("linked") or ident.get("mother_pubkey") or is_frozen():
            _unlink_local(reason="revoke")
            log_event({"event": "fleet.revoked", "device_id": ident["device_id"]},
                      "crit")
        return
    if not ident.get("linked") or not ident.get("mother_pubkey"):
        return
    rows = await sb_select("commands", {
        "select": "*", "device_id": f"eq.{ident['device_id']}",
        "seq": f"gt.{int(ident.get('last_seq') or 0)}",
        "order": "seq.asc",
    })
    for row in rows:
        seq = int(row.get("seq") or 0)
        if seq <= int(ident.get("last_seq") or 0):
            continue  # anti-rejeu
        payload = {"device_id": row.get("device_id"), "seq": seq,
                   "type": row.get("type")}
        if not verify_payload(ident["mother_pubkey"], payload, row.get("sig") or ""):
            # Signature invalide → on N'APPLIQUE RIEN et on N'AVANCE PAS le
            # curseur : avancer permettrait à une Supabase compromise de
            # sacrifier une commande légitime de même seq. On re-vérifie au
            # prochain poll (log warn répété, ~4/min, acceptable).
            log_event({"event": "command.bad_sig", "seq": seq,
                       "device_id": ident["device_id"]}, "warn")
            continue
        cmd = row.get("type")
        if cmd == "stop":
            _set_frozen(True)
            log_event({"event": "fleet.frozen", "device_id": ident["device_id"],
                       "seq": seq}, "crit")
            await alert(ident["device_id"], "crit", "Suspension appliquée")
        elif cmd == "start":
            _set_frozen(False)
            log_event({"event": "fleet.unfrozen", "device_id": ident["device_id"],
                       "seq": seq}, "crit")
            await alert(ident["device_id"], "crit", "Suspension levée")
        else:
            log_event({"event": "command.unknown", "type": cmd, "seq": seq}, "warn")
        save_identity({"last_seq": seq})


def _unlink_local(reason: str) -> None:
    """Oubli local de la mère + suppression du frozen (dissociation)."""
    save_identity({"mother_pubkey": None, "cert": None, "linked": False,
                   "mother_username": None})
    _set_frozen(False)
    log_event({"event": "fleet.unlink", "reason": reason}, "crit")


async def daughter_unlink() -> dict:
    """Se dissocier volontairement (consentement révocable à tout moment)."""
    ident = _load_or_create_identity()
    _unlink_local(reason="voluntary")
    if is_configured():
        await sb_patch("devices", {"id": f"eq.{ident['device_id']}"},
                       {"status": "revoked"})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Audit cloud (mère consulte le journal d'une fille via Supabase)
# ---------------------------------------------------------------------------

async def cloud_audit_entries(device_id_: str, limit: int = 100) -> list:
    if not is_configured():
        return []
    rows = await sb_select("audit_log", {
        "select": "seq,ts,event,hash,prev_hash",
        "device_id": f"eq.{device_id_}",
        "order": "seq.desc",
        "limit": str(limit),
    })
    rows.reverse()
    return [{"seq": r.get("seq"), "ts": r.get("ts"), "event": r.get("event"),
             "hash": r.get("hash")} for r in rows]


async def cloud_audit_verify(device_id_: str) -> dict:
    """Recalcule la chaîne depuis Supabase (transport non fiable → contrôle local du hash)."""
    if not is_configured():
        return {"ok": False, "broken_at": None}
    rows = await sb_select("audit_log", {
        "select": "seq,ts,event,prev_hash,hash",
        "device_id": f"eq.{device_id_}",
        "order": "seq.asc",
        "limit": "10000",
    })
    prev_hash = ""
    prev_seq = 0
    for r in rows:
        seq = r.get("seq")
        event = r.get("event") or {}
        if seq != prev_seq + 1:
            return {"ok": False, "broken_at": seq}
        if (r.get("prev_hash") or "") != prev_hash:
            return {"ok": False, "broken_at": seq}
        expected = hashlib.sha256(
            (prev_hash + audit_log._canonical_event(event)).encode("utf-8")
        ).hexdigest()
        if (r.get("hash") or "") != expected:
            return {"ok": False, "broken_at": seq}
        prev_hash = r.get("hash") or ""
        prev_seq = seq
    return {"ok": True, "broken_at": None}


async def cloud_alerts(since: Optional[str]) -> list:
    if not is_configured():
        return []
    params = {"select": "id,device_id,level,message,created_at",
              "order": "created_at.asc", "limit": "500"}
    if since:
        params["created_at"] = f"gt.{since}"
    rows = await sb_select("alerts", params)
    return rows


# ---------------------------------------------------------------------------
# Boucles background (fille)
# ---------------------------------------------------------------------------

async def _heartbeat_loop() -> None:
    while True:
        try:
            await daughter_heartbeat_once()
        except Exception as e:
            print(f"[fleet] heartbeat error: {e}", flush=True)
        await asyncio.sleep(HEARTBEAT_S)


async def _command_loop() -> None:
    while True:
        try:
            await daughter_poll_commands_once()
        except Exception as e:
            print(f"[fleet] poll error: {e}", flush=True)
        await asyncio.sleep(COMMAND_POLL_S)


def start_background() -> None:
    """Démarre heartbeat + poll commandes (fille uniquement, si configuré).

    Idempotent ; aucun crash si non configuré (les boucles tournent en veille).
    """
    global _tasks_started
    if _tasks_started:
        return
    _tasks_started = True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # pas de boucle (import hors FastAPI) : rien à faire
    loop.create_task(_heartbeat_loop())
    loop.create_task(_command_loop())
    print(f"[fleet] démarré (role={ROLE}, configured={is_configured()})", flush=True)
