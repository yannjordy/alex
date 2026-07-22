import os
import platform
import shutil
import subprocess
from pathlib import Path

from . import tool


@tool("info_systeme", "Affiche les informations sur le système (CPU, RAM, disque, OS).")
def system_info() -> str:
    cpu = platform.processor() or "inconnu"
    node = platform.node()
    system = platform.system()
    release = platform.release()
    mem = _get_memory()
    disk = _get_disk()
    return (
        f"🖥 {node} — {system} {release}\n"
        f"⚡ CPU: {cpu} ({os.cpu_count()} threads)\n"
        f"💾 RAM: {mem}\n"
        f"💿 Disque: {disk}"
    )


@tool("processus", "Liste les processus en cours d'exécution.")
def process_list(count: int = 20) -> str:
    try:
        result = subprocess.run(
            ["ps", "aux", "--sort=-%mem"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.strip().splitlines()
        header = lines[0] if lines else ""
        rows = lines[1:count + 1]
        formatted = []
        for row in rows:
            parts = row.split(None, 10)
            if len(parts) >= 11:
                user, pid, cpu, mem, vsz, rss, tty, stat, start, time, cmd = parts
                formatted.append(f"{pid:>6} {cpu:>4}% {mem:>4}% {cmd[:60]}")
        return f"📊 Processus (top {count} par mémoire):\n" + ("USER     PID   CPU  MEM COMMAND\n" + "\n".join(formatted) if formatted else result.stdout[:3000])
    except Exception as e:
        return f"Erreur de liste des processus : {e}"


@tool("commande", "Exécute une commande bash et retourne le résultat. Utile pour tout contrôle système.", dangerous=True)
def run_command(command: str) -> str:
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        out = (result.stdout or "")[:5000]
        err = (result.stderr or "")[:1000]
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]\n{err}")
        ret = "\n".join(parts)
        return ret if ret else "(aucune sortie)"
    except subprocess.TimeoutExpired:
        return "Commande interrompue (délai de 30s dépassé)."
    except Exception as e:
        return f"Erreur d'exécution : {e}"


@tool("ouvrir_application", "Cherche et lance une application sur l'ordinateur. Paramètre : nom (nom de l'app, ex: firefox, vscode, calculatrice).")
def launch_application(nom: str) -> str:
    if not nom or not nom.strip():
        return "Quelle application veux-tu lancer ?"
    name = nom.strip().lower()

    # Chercher dans les .desktop files
    desktop_dirs = [
        Path.home() / ".local" / "share" / "applications",
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
    ]
    matches = []
    for ddir in desktop_dirs:
        if not ddir.exists():
            continue
        for f in ddir.glob("*.desktop"):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if "NoDisplay=true" in content:
                continue
            fname = f.stem.lower()
            if name in fname:
                for line in content.splitlines():
                    if line.startswith("Name="):
                        display = line.split("=", 1)[1].strip()
                        break
                else:
                    display = fname
                matches.append((fname, display, str(f)))

    # Chercher dans PATH
    if not matches:
        for pdir in os.environ.get("PATH", "").split(":"):
            p = Path(pdir) / name
            if p.exists() and os.access(str(p), os.X_OK):
                try:
                    subprocess.Popen([str(p)], start_new_session=True)
                    return f"Lancement de {name}..."
                except Exception as e:
                    return f"Impossible de lancer {name} : {e}"

    if matches:
        best = matches[0]
        name_match, display, path = best
        try:
            subprocess.Popen(["gtk-launch", name_match], start_new_session=True)
            return f"Je lance {display}..."
        except FileNotFoundError:
            try:
                subprocess.Popen(["xdg-open", path], start_new_session=True)
                return f"Je lance {display}..."
            except Exception:
                try:
                    subprocess.Popen([path], start_new_session=True)
                    return f"Je lance {display}..."
                except Exception as e:
                    return f"Impossible de lancer {display} : {e}"

    # Dernier recours : essayer de lancer directement
    try:
        subprocess.Popen([name], start_new_session=True)
        return f"Tentative de lancement de {name}..."
    except Exception:
        pass

    apps_trouvees = ", ".join(m[1] for m in matches[:10]) if matches else ""
    if apps_trouvees:
        return f"Je n'ai pas trouvé « {nom} ». Applications disponibles : {apps_trouvees}."
    return f"Je n'ai pas trouvé d'application « {nom} »."


def _get_memory() -> str:
    try:
        import psutil
        mem = psutil.virtual_memory()
        total = mem.total / (1024**3)
        avail = mem.available / (1024**3)
        return f"{avail:.1f} Go libre / {total:.1f} Go total"
    except ImportError:
        try:
            with open("/proc/meminfo") as f:
                data = f.read()
            total = _parse_meminfo(data, "MemTotal")
            avail = _parse_meminfo(data, "MemAvailable")
            if total:
                return f"{avail:.1f} Go libre / {total:.1f} Go total"
        except Exception:
            pass
        return "indisponible"


def _get_disk() -> str:
    try:
        total, used, free = shutil.disk_usage("/")
        return f"{free / (1024**3):.0f} Go libre / {total / (1024**3):.0f} Go total"
    except Exception:
        return "indisponible"


def _parse_meminfo(data: str, key: str) -> float:
    for line in data.splitlines():
        if line.startswith(key + ":"):
            kb = int(line.split()[1])
            return kb / (1024 * 1024)
    return 0
