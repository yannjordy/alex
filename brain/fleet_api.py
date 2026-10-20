"""ALEX — API Fleet (contrôle administrateur VISIBLE et consenti).

APIRouter(prefix="/fleet") — le contrat JSON (snake_case, erreurs
{"detail": ...}) est consommé tel quel par la console d'administration.
Rôle piloté par ALEX_FLEET_ROLE (mother | daughter).
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import fleet

router = APIRouter(prefix="/fleet", tags=["fleet"])

_SUSPENDED_MSG = "Alex est suspendue par l'administrateur de cette instance."


def _need_config():
    if not fleet.is_configured():
        raise HTTPException(503, "fleet non configuré (ALEX_SUPABASE_URL / ALEX_SUPABASE_KEY)")


def _need_mother():
    _need_config()
    if fleet.current_role() != "mother":
        raise HTTPException(403, "endpoint réservé au rôle mother")


def _need_daughter():
    _need_config()
    if fleet.current_role() != "daughter":
        raise HTTPException(403, "endpoint réservé au rôle daughter")


def _err(exc: Exception, fallback: str) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc) or "accès refusé")
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc) or "introuvable")
    return HTTPException(500, str(exc) or fallback)


# ---------------------------------------------------------------------------
# GET /fleet/state
# ---------------------------------------------------------------------------

@router.get("/state")
async def fleet_state():
    ident = None
    try:
        ident = fleet._load_or_create_identity()
    except Exception:
        pass
    return {
        "configured": fleet.is_configured(),
        "role": fleet.current_role(),
        "linked": bool(ident and ident.get("linked")),
        "username": (ident or {}).get("username"),
        "mother_username": (ident or {}).get("mother_username"),
        "frozen": fleet.is_frozen(),
        "device_id": (ident or {}).get("device_id"),
    }


# ---------------------------------------------------------------------------
# Pairing (fille)
# ---------------------------------------------------------------------------

@router.post("/pairing/generate")
async def pairing_generate():
    _need_daughter()
    try:
        return await fleet.pairing_generate()
    except Exception as e:
        raise _err(e, "génération impossible")


@router.get("/pairing/status")
async def pairing_status():
    _need_daughter()
    try:
        return await fleet.pairing_status()
    except Exception as e:
        raise _err(e, "statut indisponible")


# ---------------------------------------------------------------------------
# Mère : pending / approve
# ---------------------------------------------------------------------------

@router.get("/pending")
async def pending():
    _need_mother()
    try:
        return await fleet.mother_pending()
    except Exception as e:
        raise _err(e, "liste indisponible")


class ApproveReq(BaseModel):
    pairing_id: int
    code: str


@router.post("/approve")
async def approve(req: ApproveReq):
    _need_mother()
    try:
        return await fleet.mother_approve(req.pairing_id, req.code)
    except Exception as e:
        raise _err(e, "approbation impossible")


# ---------------------------------------------------------------------------
# Mère : devices / command / revoke
# ---------------------------------------------------------------------------

@router.get("/devices")
async def devices():
    _need_mother()
    try:
        return await fleet.mother_devices()
    except Exception as e:
        raise _err(e, "liste indisponible")


class CommandReq(BaseModel):
    type: str  # "stop" | "start"


@router.post("/devices/{device_id}/command")
async def device_command(device_id: str, req: CommandReq):
    _need_mother()
    try:
        return await fleet.mother_command(device_id, req.type)
    except Exception as e:
        raise _err(e, "commande impossible")


@router.post("/devices/{device_id}/revoke")
async def device_revoke(device_id: str):
    _need_mother()
    try:
        return await fleet.mother_revoke(device_id)
    except Exception as e:
        raise _err(e, "révocation impossible")


# ---------------------------------------------------------------------------
# Alertes + audit
# ---------------------------------------------------------------------------

@router.get("/alerts")
async def alerts(since: Optional[str] = None):
    _need_config()
    try:
        return await fleet.cloud_alerts(since)
    except Exception as e:
        raise _err(e, "alertes indisponibles")


@router.get("/audit/{device_id}")
async def audit_entries(device_id: str, limit: int = 100):
    _need_config()
    try:
        return await fleet.cloud_audit_entries(device_id, limit)
    except Exception as e:
        raise _err(e, "audit indisponible")


class AuditVerifyReq(BaseModel):
    device_id: str


@router.post("/audit/verify")
async def audit_verify(req: AuditVerifyReq):
    _need_config()
    try:
        return await fleet.cloud_audit_verify(req.device_id)
    except Exception as e:
        raise _err(e, "vérification impossible")


# ---------------------------------------------------------------------------
# Dissociation (fille, volontaire)
# ---------------------------------------------------------------------------

@router.post("/unlink")
async def unlink():
    _need_daughter()
    try:
        return await fleet.daughter_unlink()
    except Exception as e:
        raise _err(e, "dissociation impossible")
