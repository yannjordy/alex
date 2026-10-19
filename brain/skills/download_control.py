"""ALEX — compétence "téléchargement" : lancer, suivre, annuler, relancer.

Expressions en français → brain/downloads.py (file supervisée >100 Mo,
direct <100 Mo). Routée dans main.py AVANT browser_control.

match() = synchrone (regex). run() = asynchrone.
"""

import re

from .. import downloads as dl

COMMANDS = [
    ("dl_start", re.compile(
        r"(?:t[ée]l[ée]charg(?:e|er|es|ez)|r[ée]cup[èe]re(?:-moi)?|envoie(?:-moi)?)\s+"
        r"(?:moi\s+)?(?:le\s+fichier\s+|ce\s+fichier\s+|cette\s+page\s+)?(https?://\S+)",
        re.I)),
    ("dl_status", re.compile(
        r"o[uù]\s+en\s+(?:est|est-ce)\s+(?:du?\s+|de\s+|le\s+|la\s+|l['’]\s+|des\s+)?t[ée]l[ée]chargement|"
        r"t[ée]l[ée]chargements?\s+(?:en\s+cours|actifs?|status|[ée]tat|etats?)|"
        r"(?:[ée]tat|statut|avancement|avance|progression)\s+(?:du|des)\s+t[ée]l[ée]chargement|"
        r"liste\s+(?:des\s+|mes\s+)?t[ée]l[ée]chargements?",
        re.I)),
    ("dl_cancel", re.compile(
        r"(?:annule|r[ée]voque|stoppe?|arr[êe]te|coupe)\s+"
        r"(?:le\s+|ce\s+|tout\s+(?:le\s+)?|mes\s+)?t[ée]l[ée]chargements?",
        re.I)),
    ("dl_retry", re.compile(
        r"(?:relance|reprends?|continue|red[eé]marre)\s+"
        r"(?:le\s+|ce\s+|mes\s+)?t[ée]l[ée]chargements?",
        re.I)),
]

_ETATS = {
    "queued": "en file d'attente",
    "downloading": "en cours",
    "retrying": "en reprise (coupure réseau)",
    "verifying": "vérification SHA256",
    "complete": "terminé ✓",
    "cancelled": "annulé",
    "hash_failed": "échec d'intégrité (SHA256)",
    "failed": "échec",
    "failed_permanent": "échec définitif (relance possible)",
}


def match(text: str):
    t = text.strip()
    for action, pattern in COMMANDS:
        m = pattern.search(t)
        if m:
            return action, m
    return None, None


def _resume(st: dict) -> str:
    etat = _ETATS.get(st.get("state"), st.get("state", "?"))
    nom = (st.get("dest") or "").rsplit("/", 1)[-1]
    size, exp = st.get("size"), st.get("expected")
    pct = f"{size * 100 / exp:.1f} %" if exp and size else None
    parts = [f"« {nom} » — {etat}"]
    if st.get("state") == "downloading" and pct:
        vitesse = dl.human(st.get("speed_bps")) + "/s" if st.get("speed_bps") else "?"
        reste = dl.human_eta(st.get("eta_seconds"))
        parts.append(f"{pct} ({dl.human(size)} / {dl.human(exp)}, {vitesse}, reste {reste})")
    elif size and exp:
        parts.append(f"{dl.human(size)} / {dl.human(exp)}")
    if st.get("error"):
        parts.append(st["error"][:120])
    return " — ".join(parts)


async def run(action: str, m) -> str:
    try:
        if action == "dl_start":
            url = m.group(1).strip().rstrip(").,")
            r = await dl.submit_async(url=url)
            if not r.get("ok"):
                return f"Refus : {r.get('error', 'erreur')}."
            if r.get("mode") == "direct":
                return (f"Téléchargé — {(r.get('dest') or '').rsplit('/', 1)[-1]} "
                        f"({dl.human(r.get('size'))}) dans ~/.alex/downloads/files/.")
            note = f" ({r['note']})" if r.get("note") else ""
            taille = f" — {dl.human(r.get('expected'))}" if r.get("expected") else ""
            return (f"Téléchargement lancé{note}{taille}. Je le supervise : "
                    f"reprise automatique et vérification. Demande-moi « où en est le "
                    f"téléchargement ? » à tout moment.")

        if action == "dl_status":
            jobs = dl.list_status()
            if not jobs:
                return "Aucun téléchargement."
            if len(jobs) == 1:
                return _resume(jobs[0])
            lignes = [_resume(s) for s in jobs[:5]]
            return f"{len(jobs)} téléchargements — " + " | ".join(lignes)

        if action == "dl_cancel":
            jobs = [s for s in dl.list_status() if s.get("state") in ("queued", "downloading", "retrying")]
            if not jobs:
                return "Aucun téléchargement en cours à annuler."
            nom = (jobs[0].get("dest") or "").rsplit("/", 1)[-1]
            await __import__("asyncio").to_thread(dl.cancel, jobs[0]["id"])
            return f"Téléchargement annulé — « {nom} »."

        if action == "dl_retry":
            relances = [s for s in dl.list_status()
                        if s.get("state") in ("failed", "failed_permanent", "hash_failed", "cancelled")]
            if not relances:
                return "Rien à relancer — aucun téléchargement en échec ou annulé."
            r = await __import__("asyncio").to_thread(dl.retry, relances[0]["id"])
            nom = (relances[0].get("dest") or "").rsplit("/", 1)[-1]
            return f"Repris — « {nom} » (reprise automatique au dernier octet)."

    except Exception as e:
        return f"Erreur téléchargement : {str(e)[:200]}"
    return "Action téléchargement inconnue."
