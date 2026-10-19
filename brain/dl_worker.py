"""ALEX — worker des téléchargements supervisés.

Consomme ~/.alex/downloads/jobs/*.job.json (FIFO) : wget -c avec reprise,
watchdog anti-blocage (5 min sans progression → relance), vérification
taille + SHA256, retries réseau en boucle, échec permanent sur erreur
HTTP (code wget 8). Lancé par systemd : alex-dl.service (Restart=always).

Usage : python3 -m brain.dl_worker   (WorkingDirectory = alex-prioritaire)
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from brain import downloads as dl

STALL_TIMEOUT = 300
RETRY_DELAY = 15
PERMANENT_RC = {3, 5, 6, 8}
# 403 : les URL signées HuggingFace (us.aws.cdn.hf.co) expirent en cours de
# transfert long — chaque tentative re-demande l'URL resolve → signature fraîche,
# wget -c / aria2 -c reprennent le fichier. (Erreurs définitives : annulation.)
TRANSIENT_HTTP = {403, 408, 425, 429, 500, 502, 503, 504}
PERMANENT_HTTP = {400, 401, 404, 410}
BACKOFF_CAP = 900

# aria2c multi-connexions : le throttle HF est par-connexion (wget ~20 Ko/s,
# aria2 x8 mesuré ~213 Mo/s) — transport par défaut si dispo, wget en secours.
_ARIA2 = shutil.which("aria2c")


def _last_http_code(log: Path) -> int | None:
    """Dernier code HTTP mentionné dans le log (wget : "erreur 403", aria2 : "status ... 403")."""
    try:
        tail = log.read_bytes()[-4096:].decode("utf-8", "replace")
    except OSError:
        return None
    codes = re.findall(r"(?:erreur|error|code|status)[^\d]{0,12}(\d{3})", tail, re.IGNORECASE)
    return int(codes[-1]) if codes else None


def _backoff_delay(attempts: int) -> int:
    """60s, 120s, 240s... plafonné à 15 min."""
    return min(60 * (2 ** max(0, attempts - 1)), BACKOFF_CAP)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _next_job():
    jobs = []
    for p in sorted(dl.JOBS_DIR.glob("*.job.json")):
        try:
            jobs.append(json.loads(p.read_text()))
        except Exception:
            p.unlink(missing_ok=True)
    # priorité d'abord (décroissante), puis FIFO (id horodaté)
    jobs.sort(key=lambda j: (-int(j.get("priority") or 0), j.get("id") or ""))
    return jobs[0] if jobs else None


def _run_job(job: dict) -> None:
    job_id = job["id"]
    url = job["url"]
    dest = Path(job["dest"])
    expected = job.get("expected")
    sha_expect = (job.get("sha256") or "").lower() or None
    log = dl.LOGS_DIR / f"{job_id}.wget.log"
    dest.parent.mkdir(parents=True, exist_ok=True)

    st = dl.read_status(job_id) or {"id": job_id, "created": time.time()}
    st.update(state="downloading", url=url, dest=str(dest), expected=expected, sha256=sha_expect)
    st["attempts"] = int(st.get("attempts") or 0) + 1

    speed = 0.0
    last_size = 0
    stall = 0

    while True:
        if dl.read_job(job_id) is None:
            st["state"] = "cancelled"
            st.pop("pid", None)
            dl.write_status(job_id, st)
            return

        if _ARIA2:
            # 8 connexions : 500x-10000x plus vite que wget mono-connexion sur HF
            cmd = [
                _ARIA2, "-c", "-o", dest.name, f"--dir={dest.parent}", url,
                "--max-connection-per-server=8", "--split=8", "--min-split-size=4M",
                "--file-allocation=none", "--auto-file-renaming=false", "--allow-overwrite=true",
                "--max-tries=0", "--retry-wait=10", "--timeout=60", "--connect-timeout=15",
                "--max-file-not-found=5", "--summary-interval=0",
                "--lowest-speed-limit=64K",
                "--console-log-level=warn", "--download-result=hide",
            ]
        else:
            cmd = [
                "wget", "-c", "-O", str(dest), url,
                "--tries=0", "--timeout=30", "--read-timeout=60",
                "--waitretry=10", "--retry-connrefused",
                "--user-agent=Alex/1.0",
            ]
        with open(log, "ab") as lf:
            proc = subprocess.Popen(
                cmd,
                stdout=lf, stderr=subprocess.STDOUT,
            )
        st["pid"] = proc.pid
        dl.write_status(job_id, st)
        last_size = dest.stat().st_size if dest.exists() else 0
        stall = 0
        killed = False
        t_prev = time.time()

        while proc.poll() is None:
            time.sleep(2)  # rafraîchissement rapide (PWA)
            size = dest.stat().st_size if dest.exists() else 0
            now = time.time()
            dt = max(now - t_prev, 0.1)
            t_prev = now
            if size > last_size + 2048:  # >2 Ko/tick = vraie progression (les micro-gouttes ne réveillent plus le watchdog)
                inst = (size - last_size) / dt
                speed = inst if speed == 0 else 0.7 * speed + 0.3 * inst
                stall = 0
            else:
                stall += dt
            last_size = size
            st.update(state="downloading", size=size, speed_bps=round(speed),
                      eta_seconds=int((expected - size) / speed) if expected and speed > 1 else None)
            dl.write_status(job_id, st)
            if stall >= STALL_TIMEOUT:
                proc.kill()
                proc.wait()
                killed = True
                st["state"] = "retrying"
                st["note"] = "connexion gelée, relance"
                dl.write_status(job_id, st)
                break

        if killed:
            time.sleep(RETRY_DELAY)
            continue

        rc = proc.returncode
        size = dest.stat().st_size if dest.exists() else 0
        st.update(size=size, speed_bps=round(speed))
        st.pop("pid", None)

        if rc == 0:
            if expected and size != expected:
                st["state"] = "failed"
                st["error"] = f"taille inattendue ({size} au lieu de {expected})"
                dl.remove_job(job_id)
                dl.write_status(job_id, st)
                return
            if sha_expect:
                st["state"] = "verifying"
                dl.write_status(job_id, st)
                if _sha256(dest).lower() == sha_expect:
                    st["state"] = "complete"
                    dl.remove_job(job_id)
                    dl.write_status(job_id, st)
                    return
                dest.unlink(missing_ok=True)
                st["state"] = "hash_failed"
                st["error"] = "SHA256 incorrect — fichier supprimé, relance manuelle possible"
                dl.remove_job(job_id)
                dl.write_status(job_id, st)
                return
            st["state"] = "complete"
            dl.remove_job(job_id)
            dl.write_status(job_id, st)
            return

        http = _last_http_code(log)
        if http in TRANSIENT_HTTP:
            st["attempts"] = int(st.get("attempts") or 0) + 1
            delay = _backoff_delay(int(st["attempts"]))
            st["state"] = "retrying"
            st["error"] = f"HTTP {http} transitoire, relance dans {delay}s"
            dl.write_status(job_id, st)
            time.sleep(delay)
            continue

        if _ARIA2:
            # aria2 --max-tries=0 ne rend la main que sur erreur dure (404...) →
            # HTTP définitif = échec permanent, sinon relance générale 15 s.
            if http in PERMANENT_HTTP:
                st["state"] = "failed_permanent"
                st["error"] = f"HTTP {http} définitif (aria2 rc {rc}) — voir {log.name} (relance : retry)"
                dl.remove_job(job_id)
                dl.write_status(job_id, st)
                return
            st["state"] = "retrying"
            st["error"] = f"aria2 rc {rc}, nouvelle tentative dans {RETRY_DELAY}s"
            dl.write_status(job_id, st)
            time.sleep(RETRY_DELAY)
            continue

        if rc in PERMANENT_RC:
            st["state"] = "failed_permanent"
            st["error"] = f"wget code {rc} (HTTP {http}) — voir {log.name} (relance : retry)"
            dl.remove_job(job_id)
            dl.write_status(job_id, st)
            return

        st["state"] = "retrying"
        st["error"] = f"wget code {rc}, nouvelle tentative dans {RETRY_DELAY}s"
        dl.write_status(job_id, st)
        time.sleep(RETRY_DELAY)


def main() -> None:
    dl._ensure()
    print("[alex-dl] worker démarré", flush=True)
    while True:
        try:
            job = _next_job()
            if job:
                _run_job(job)
            else:
                time.sleep(2)
        except Exception as e:
            print(f"[alex-dl] erreur : {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
