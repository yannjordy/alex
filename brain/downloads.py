"""ALEX — téléchargements supervisés (>100 Mo).

File de jobs sur disque (~/.alex/downloads) consommée par brain/dl_worker.py
(service systemd alex-dl.service, Restart=always) : survit aux redémarrages
du cerveau, aux coupures réseau et aux fermetures de sessions.

<100 Mo → téléchargement direct (synchrone, dans un thread).
>=100 Mo ou taille inconnue → job supervisé (wget -c, anti-blocage, SHA256).
"""

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import time
import urllib.parse
import urllib.request
from pathlib import Path

DL_DIR = Path(os.environ.get("ALEX_DL_DIR", Path.home() / ".alex" / "downloads"))
JOBS_DIR = DL_DIR / "jobs"
FILES_DIR = DL_DIR / "files"
LOGS_DIR = DL_DIR / "logs"
THRESHOLD = 100 * 1024 * 1024
HEAD_TIMEOUT = 12


def _ensure() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.job.json"


def _status_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.status.json"


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(path)


def read_job(job_id: str):
    try:
        return json.loads(_job_path(job_id).read_text())
    except Exception:
        return None


def read_status(job_id: str):
    try:
        return json.loads(_status_path(job_id).read_text())
    except Exception:
        return None


def write_status(job_id: str, data: dict) -> None:
    data["updated"] = time.time()
    _write_json(_status_path(job_id), data)


def remove_job(job_id: str) -> None:
    try:
        _job_path(job_id).unlink()
    except FileNotFoundError:
        pass


def human(n) -> str:
    if n is None:
        return "?"
    n = float(n)
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} Go"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} Mo"
    if n >= 1024:
        return f"{n / 1024:.0f} Ko"
    return f"{n:.0f} o"


def human_eta(s) -> str:
    if s is None or s <= 0:
        return "calcul…"
    s = int(s)
    if s >= 86400:
        return f"{s // 86400} j {(s % 86400) // 3600} h"
    if s >= 3600:
        return f"{s // 3600} h {(s % 3600) // 60} min"
    if s >= 60:
        return f"{s // 60} min"
    return f"{s} s"


def _filename_from(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    name = urllib.parse.unquote(os.path.basename(path))
    name = re.sub(r"[^\w.\- ()\[\]]", "_", name).strip("._")
    if not name or len(name) > 180:
        name = f"fichier-{time.strftime('%Y%m%d-%H%M%S')}"
    return name


def head_size(url: str):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Alex/1.0"})
        with urllib.request.urlopen(req, timeout=HEAD_TIMEOUT) as r:
            v = r.headers.get("Content-Length")
            return int(v) if v else None
    except Exception:
        return None


def _find_by_dest(dest: str):
    for st in list_status():
        if st.get("dest") == dest and st.get("state") not in ("complete", "cancelled", "failed_permanent", "hash_failed"):
            return st
    return None


def _find_by_url(url: str):
    for st in list_status():
        if st.get("url") == url and st.get("state") not in ("complete", "cancelled", "failed_permanent", "hash_failed"):
            return st
    return None


def _dest_for(name: str, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    base, ext = os.path.splitext(name)
    n = 1
    while dest.exists():
        dest = dest_dir / f"{base}-{n}{ext}"
        n += 1
    return dest


def _direct_download(url: str, dest: Path) -> dict:
    part = dest.with_name(dest.name + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Alex/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f, length=64 * 1024)
        part.replace(dest)
        return {"ok": True, "mode": "direct", "dest": str(dest), "size": dest.stat().st_size}
    except Exception as e:
        try:
            part.unlink()
        except FileNotFoundError:
            pass
        return {"ok": False, "error": str(e)[:200]}


def submit(url: str, filename=None, sha256=None, dest_dir=None, force=False, priority=0) -> dict:
    _ensure()
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL invalide (http ou https attendu)."}

    existing = _find_by_url(url)
    if existing:
        return {"ok": True, "mode": "supervised", "id": existing["id"],
                "dest": existing.get("dest"), "expected": existing.get("expected"),
                "note": "déjà en cours"}

    size = head_size(url)
    name = filename or _filename_from(url)
    ddir = Path(dest_dir).expanduser() if dest_dir else FILES_DIR
    try:
        ddir.resolve().relative_to(Path.home())
    except ValueError:
        try:
            ddir.resolve().relative_to(Path("/media/jordy"))
        except ValueError:
            return {"ok": False, "error": "Destination refusée (hors home ou /media/jordy)."}

    if not force and size is not None and size < THRESHOLD:
        dest = _dest_for(name, ddir)
        return {"ok": True, **_direct_download(url, dest)}

    dest = _dest_for(name, ddir)
    by_dest = _find_by_dest(str(dest))
    if by_dest:
        return {"ok": True, "mode": "supervised", "id": by_dest["id"], "dest": dest.as_posix(),
                "expected": by_dest.get("expected"), "note": "déjà en cours"}

    job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + hashlib.md5(url.encode()).hexdigest()[:6]
    prio = int(priority or 0)
    job = {"id": job_id, "url": url, "dest": str(dest), "sha256": sha256,
           "expected": size, "created": time.time(), "priority": prio}
    _write_json(_job_path(job_id), job)
    write_status(job_id, {"id": job_id, "state": "queued", "url": url, "dest": str(dest),
                          "sha256": sha256, "size": 0, "expected": size, "attempts": 0,
                          "created": time.time(), "priority": prio})
    return {"ok": True, "mode": "supervised", "id": job_id, "dest": str(dest), "expected": size}


def list_status(limit: int = 20):
    _ensure()
    out = []
    for p in JOBS_DIR.glob("*.status.json"):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            pass
    out.sort(key=lambda s: s.get("created", 0), reverse=True)
    return out[:limit]


def cancel(job_id: str) -> dict:
    st = read_status(job_id)
    if not st:
        return {"ok": False, "error": "Téléchargement introuvable."}
    pid = st.get("pid")
    if pid and st.get("state") in ("downloading", "retrying"):
        try:
            os.kill(int(pid), signal.SIGTERM)
            time.sleep(0.5)
            os.kill(int(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, ValueError):
            pass
    remove_job(job_id)
    st["state"] = "cancelled"
    st.pop("pid", None)
    write_status(job_id, st)
    return {"ok": True, "state": "cancelled"}


def retry(job_id: str) -> dict:
    st = read_status(job_id)
    if not st:
        return {"ok": False, "error": "Téléchargement introuvable."}
    if st.get("state") in ("queued", "downloading", "retrying"):
        return {"ok": True, "state": st["state"], "note": "déjà actif"}
    job = {"id": job_id, "url": st.get("url"), "dest": st.get("dest"),
           "sha256": st.get("sha256"), "expected": st.get("expected"),
           "created": st.get("created", time.time()), "priority": int(st.get("priority") or 0)}
    _write_json(_job_path(job_id), job)
    st["state"] = "queued"
    st.pop("pid", None)
    write_status(job_id, st)
    return {"ok": True, "state": "queued"}


async def submit_async(**kw) -> dict:
    return await asyncio.to_thread(submit, **kw)


async def direct_download_async(url: str, filename=None, dest_dir=None) -> dict:
    _ensure()
    size = await asyncio.to_thread(head_size, url)
    name = filename or _filename_from(url)
    ddir = Path(dest_dir).expanduser() if dest_dir else FILES_DIR
    dest = _dest_for(name, ddir)
    return await asyncio.to_thread(_direct_download, url, dest)
