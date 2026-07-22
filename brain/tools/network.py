import subprocess
import re

from . import tool


@tool("wifi_scan", "Scanne les réseaux WiFi disponibles à proximité.")
def wifi_scan() -> str:
    # Try nmcli
    try:
        result = subprocess.run(
            ["nmcli", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "yes"],
            capture_output=True, text=True, timeout=30
        )
        if result.stdout.strip():
            lines = [l for l in result.stdout.splitlines() if l.strip()]
            # Remove header and sort by signal
            if len(lines) > 1:
                header = lines[0]
                networks = []
                for line in lines[1:]:
                    parts = line.rsplit(None, 1)
                    if len(parts) >= 2:
                        ssid_signal = parts[0].rsplit(None, 1)
                        if len(ssid_signal) >= 2:
                            ssid = ssid_signal[0]
                            signal = ssid_signal[1]
                            sec = parts[-1]
                        else:
                            ssid = parts[0]
                            signal = "?"
                            sec = parts[-1] if len(parts) > 1 else ""
                    else:
                        ssid = parts[0]
                        signal = "?"
                        sec = ""
                    if ssid and ssid != "SSID":
                        networks.append((int(signal) if signal.isdigit() else 0, ssid, sec))

                networks.sort(key=lambda x: -x[0])
                out = ["📶 WiFi à proximité :"]
                for sig, ssid, sec in networks[:15]:
                    bars = "█" * max(1, sig // 20) + "░" * max(0, 5 - sig // 20)
                    lock = "🔒" if sec and sec != "--" else "🌐"
                    out.append(f"  {bars} {sig:>2}% {lock} {ssid}")
                return "\n".join(out)
            return "Aucun réseau WiFi trouvé."
    except Exception:
        pass

    # Try iwlist
    try:
        # Find wireless interface
        iface = None
        try:
            iw = subprocess.run(["iw", "dev"], capture_output=True, text=True, timeout=5)
            for line in iw.stdout.splitlines():
                if "Interface" in line:
                    iface = line.split()[-1]
                    break
        except Exception:
            pass

        if not iface:
            try:
                iwconfig = subprocess.run(["iwconfig"], capture_output=True, text=True, timeout=5)
                for line in iwconfig.stdout.splitlines():
                    if "IEEE" in line:
                        iface = line.split()[0]
                        break
            except Exception:
                pass

        if not iface:
            return "Aucune interface WiFi trouvée."

        result = subprocess.run(
            ["sudo", "iwlist", iface, "scan"],
            capture_output=True, text=True, timeout=30
        )
        if result.stdout.strip():
            networks = []
            current_ssid = None
            current_signal = None
            for line in result.stdout.splitlines():
                line = line.strip()
                if "ESSID:" in line:
                    current_ssid = line.split('"')[1] if '"' in line else line.split("ESSID:")[1]
                if "Signal level" in line:
                    m = re.search(r'(-?\d+) dBm', line)
                    if m:
                        dbm = int(m.group(1))
                        pct = max(0, min(100, int(2 * (dbm + 100))))
                        current_signal = pct
                    if current_ssid and current_ssid:
                        networks.append((current_signal or 0, current_ssid))
                        current_ssid = None
                        current_signal = None

            networks.sort(key=lambda x: -x[0])
            out = ["📶 WiFi à proximité :"]
            for sig, ssid in networks[:15]:
                bars = "█" * max(1, sig // 20) + "░" * max(0, 5 - sig // 20)
                out.append(f"  {bars} {sig:>2}% {ssid}")
            if len(out) > 1:
                return "\n".join(out)

    except Exception:
        pass

    return "Impossible de scanner les réseaux WiFi. nmcli est requis."


@tool("appareils_reseau", "Liste les appareils connectés au réseau local (scan ARP).")
def network_devices() -> str:
    try:
        # Scan ARP table
        result = subprocess.run(
            ["arp", "-a"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        if lines:
            out = ["🌐 Appareils sur le réseau local :"]
            for line in lines:
                m = re.match(r'[\w-]+\s+\(([\d.]+)\)\s+at\s+([\da-f:]+)', line)
                if m:
                    out.append(f"  • {m.group(1)}  ({m.group(2)})")
                else:
                    out.append(f"  • {line}")
            return "\n".join(out)

        # Try nmap if available
        try:
            local_ip = subprocess.run(
                ["hostname", "-I"], capture_output=True, text=True, timeout=3
            ).stdout.strip().split()[0]
            subnet = ".".join(local_ip.split(".")[:3]) + ".0/24"
            result = subprocess.run(
                ["nmap", "-sn", subnet],
                capture_output=True, text=True, timeout=60
            )
            hosts = []
            for line in result.stdout.splitlines():
                m = re.match(r'Nmap scan report for ([\d.]+)', line)
                if m:
                    hosts.append(m.group(1))
            if hosts:
                out = ["🌐 Appareils sur le réseau :"]
                for h in hosts:
                    out.append(f"  • {h}")
                return "\n".join(out)
        except Exception:
            pass

        return "Aucun appareil réseau détecté."
    except Exception as e:
        return f"Erreur scan réseau : {e}"
