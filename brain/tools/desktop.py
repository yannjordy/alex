import os
import subprocess
from pathlib import Path

from . import tool


@tool("fond_ecran", "Change le fond d'écran du bureau. Paramètre : path (chemin vers l'image).")
def set_wallpaper(path: str = "") -> str:
    if not path:
        return "Quelle image veux-tu utiliser comme fond d'écran ? Donne le chemin."
    path = path.strip().replace("~", str(Path.home()))
    img = Path(path)
    if not img.exists():
        return f"Fichier introuvable : {path}"
    ext = img.suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"):
        return f"Format d'image non supporté : {ext}"

    # Try GNOME
    try:
        uri = img.resolve().as_uri()
        subprocess.run(
            ["gsettings", "set", "org.gnome.desktop.background", "picture-uri", uri],
            timeout=5
        )
        subprocess.run(
            ["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", uri],
            timeout=5
        )
        return f"Fond d'écran changé → {img.name} ✓"
    except Exception:
        pass

    # Try KDE Plasma
    try:
        script = f'''
        var allDesktops = desktops();
        for (var i=0; i<allDesktops.length; i++) {{
            allDesktops[i].wallpaperPlugin = "org.kde.image";
            allDesktops[i].currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
            allDesktops[i].writeConfig("Image", "file://{img.resolve()}");
        }}
        '''
        subprocess.run(
            ["qdbus", "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", script],
            timeout=5
        )
        return f"Fond d'écran changé → {img.name} ✓"
    except Exception:
        pass

    # Try feh (lightweight WMs)
    try:
        subprocess.run(["feh", "--bg-fill", str(img)], timeout=5)
        return f"Fond d'écran changé → {img.name} ✓"
    except Exception:
        pass

    # Try xwallpaper
    try:
        subprocess.run(["xwallpaper", "--zoom", str(img)], timeout=5)
        return f"Fond d'écran changé → {img.name} ✓"
    except Exception:
        pass

    # Try nitrogen
    try:
        subprocess.run(["nitrogen", "--set-zoom-fill", str(img)], timeout=5)
        return f"Fond d'écran changé → {img.name} ✓"
    except Exception:
        pass

    return "Je n'ai pas trouvé comment changer le fond d'écran sur cet environnement."


@tool("luminosite", "Ajuste la luminosité de l'écran. Paramètre : niveau (0-100 ou +/-5).")
def set_brightness(niveau: str = "") -> str:
    if not niveau:
        return "Quel niveau de luminosité ? (0-100, ou +5/-5)"
    niveau = niveau.strip()

    # Find brightness controller
    for backlight in Path("/sys/class/backlight").iterdir():
        max_bright = backlight / "max_brightness"
        actual = backlight / "brightness"
        if not max_bright.exists() or not actual.exists():
            continue
        try:
            max_val = int(max_bright.read_text().strip())
            current = int(actual.read_text().strip())
        except Exception:
            continue

        if niveau.startswith("+") or niveau.startswith("-"):
            delta = int(niveau)
            new_pct = max(0, min(100, int(current / max_val * 100 + delta)))
        else:
            new_pct = max(0, min(100, int(niveau)))

        new_val = max(1, int(new_pct / 100 * max_val))
        try:
            subprocess.run(
                ["pkexec", "tee", str(actual)],
                input=f"{new_val}\n".encode(),
                timeout=5
            )
            subprocess.run(
                ["brightnessctl", "set", f"{new_pct}%"],
                timeout=5, capture_output=True
            )
            return f"Luminosité → {new_pct}% ✓"
        except Exception:
            pass

    # Try brightnessctl directly
    try:
        if niveau.startswith("+") or niveau.startswith("-"):
            subprocess.run(["brightnessctl", "set", f"{niveau}%"], timeout=5)
        else:
            subprocess.run(["brightnessctl", "set", f"{niveau}%"], timeout=5)
        return f"Luminosité → {niveau}% ✓"
    except Exception:
        pass

    # Try xrandr
    try:
        if niveau.startswith("+") or niveau.startswith("-"):
            result = subprocess.run(
                ["xrandr", "--current", "--verbose"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if " connected" in line:
                    output = line.split()[0]
                    subprocess.run(
                        ["xrandr", "--output", output, "--brightness", "0.8"],
                        timeout=5
                    )
                    return f"Luminosité ajustée via xrandr ✓"
        else:
            pct = max(0.1, min(1.0, int(niveau) / 100))
            result = subprocess.run(
                ["xrandr", "--current", "--verbose"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if " connected" in line:
                    output = line.split()[0]
                    subprocess.run(
                        ["xrandr", "--output", output, "--brightness", str(pct)],
                        timeout=5
                    )
            return f"Luminosité → {niveau}% ✓"
    except Exception:
        pass

    return "Impossible de régler la luminosité. brightnessctl est requis."


@tool("volume", "Contrôle le volume audio. Paramètre : action (up/down/mute/set), valeur (0-100, ou +/-5).")
def volume_control(action: str = "", valeur: str = "") -> str:
    if not action:
        return "Que faire ? up, down, mute, ou set 0-100."

    try:
        if action == "mute":
            subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"], timeout=3)
            return "Volume muet/activé ✓"

        if action == "up" or action == "+":
            val = valeur or "5%"
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{val}"], timeout=3)
            return "Volume augmenté ✓"

        if action == "down" or action == "-":
            val = valeur or "5%"
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{val}"], timeout=3)
            return "Volume baissé ✓"

        if action == "set":
            pct = max(0, min(100, int(valeur or "50")))
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"], timeout=3)
            return f"Volume → {pct}% ✓"
    except Exception:
        pass

    # Fallback to amixer
    try:
        if action == "mute":
            subprocess.run(["amixer", "set", "Master", "toggle"], timeout=3)
            return "Volume muet/activé ✓"
        if action == "up" or action == "+":
            subprocess.run(["amixer", "set", "Master", "5%+"], timeout=3)
            return "Volume augmenté ✓"
        if action == "down" or action == "-":
            subprocess.run(["amixer", "set", "Master", "5%-"], timeout=3)
            return "Volume baissé ✓"
        if action == "set":
            subprocess.run(["amixer", "set", "Master", f"{valeur or '50'}%"], timeout=3)
            return f"Volume → {valeur}% ✓"
    except Exception as e:
        return f"Erreur volume : {e}"

    return "Impossible de contrôler le volume. pulseaudio-utils requis."


@tool("batterie", "Affiche le niveau de batterie et l'état d'alimentation.")
def battery_status() -> str:
    try:
        result = subprocess.run(
            ["acpi", "-b"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            return f"🔋 {result.stdout.strip()}"
    except Exception:
        pass

    # Read from sysfs
    for bat in Path("/sys/class/power_supply").glob("BAT*"):
        try:
            cap = (bat / "capacity").read_text().strip()
            status = (bat / "status").read_text().strip()
            return f"🔋 Batterie : {cap}% ({status})"
        except Exception:
            pass

    try:
        result = subprocess.run(
            ["upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT0"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "percentage" in line:
                return f"🔋 {line.strip()}"
    except Exception:
        pass

    return "Aucune batterie détectée ou information indisponible."


@tool("economie_energie", "Active ou désactive le mode économie d'énergie. Paramètre : actif (on/off).")
def power_saver(actif: str = "") -> str:
    if actif not in ("on", "off", "oui", "non", "yes", "no"):
        return "Mode économie : on ou off ?"

    enable = actif in ("on", "oui", "yes")

    # Try powerprofilesctl
    try:
        profile = "power-saver" if enable else "performance"
        subprocess.run(
            ["powerprofilesctl", "set", profile],
            timeout=5, capture_output=True
        )
    except Exception:
        pass

    # Try tuned-adm
    try:
        profile = "powersave" if enable else "default"
        subprocess.run(
            ["tuned-adm", "profile", profile],
            timeout=5, capture_output=True
        )
    except Exception:
        pass

    # Lower brightness automatically
    if enable:
        try:
            subprocess.run(["brightnessctl", "set", "30%"], timeout=5, capture_output=True)
        except Exception:
            pass

    # Try laptop-mode-tools
    if enable:
        try:
            subprocess.run(
                ["sudo", "laptop_mode", "start"],
                timeout=5, capture_output=True
            )
        except Exception:
            pass

    # Try setting via sysfs
    try:
        for pwr in Path("/sys/class/power_supply").glob("*"):
            control = pwr / "device/control"
            if control.exists():
                control.write_text("auto" if enable else "max")
    except Exception:
        pass

    mode = "activé" if enable else "désactivé"
    return f"⚡ Mode économie d'énergie {mode} ✓"


@tool("bluetooth", "Active ou désactive le Bluetooth, ou liste les appareils. Paramètre : action (on/off/list/scan).")
def bluetooth_control(action: str = "status") -> str:
    if action == "on":
        try:
            subprocess.run(["bluetoothctl", "power", "on"], timeout=5)
            subprocess.run(["rfkill", "unblock", "bluetooth"], timeout=5)
            return "Bluetooth activé ✓"
        except Exception as e:
            return f"Erreur activation Bluetooth : {e}"

    if action == "off":
        try:
            subprocess.run(["bluetoothctl", "power", "off"], timeout=5)
            return "Bluetooth désactivé ✓"
        except Exception as e:
            return f"Erreur désactivation Bluetooth : {e}"

    if action == "list":
        try:
            result = subprocess.run(
                ["bluetoothctl", "devices"],
                capture_output=True, text=True, timeout=10
            )
            devices = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            if devices:
                lines = ["📶 Appareils Bluetooth connus :"]
                for d in devices:
                    parts = d.split(" ", 2)
                    if len(parts) >= 3:
                        lines.append(f"  • {parts[2]} ({parts[1]})")
                return "\n".join(lines)
            return "Aucun appareil Bluetooth enregistré."
        except Exception as e:
            return f"Erreur liste Bluetooth : {e}"

    if action == "scan":
        try:
            subprocess.run(["bluetoothctl", "scan", "on"], timeout=2, capture_output=True)
            result = subprocess.run(
                ["bluetoothctl", "devices"],
                capture_output=True, text=True, timeout=10
            )
            subprocess.run(["bluetoothctl", "scan", "off"], timeout=2, capture_output=True)
            devices = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            if devices:
                lines = ["🔍 Appareils Bluetooth à proximité :"]
                for d in devices:
                    parts = d.split(" ", 2)
                    if len(parts) >= 3:
                        lines.append(f"  • {parts[2]} ({parts[1]})")
                return "\n".join(lines)
            return "Aucun appareil Bluetooth trouvé à proximité."
        except Exception as e:
            return f"Erreur scan Bluetooth : {e}"

    # Status
    try:
        result = subprocess.run(
            ["bluetoothctl", "show"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "Powered" in line:
                status = "activé" if "yes" in line.lower() else "désactivé"
                return f"📶 Bluetooth : {status}"
        return "Statut Bluetooth : inconnu"
    except Exception:
        return "Impossible de lire le statut Bluetooth."
