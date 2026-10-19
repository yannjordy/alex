"""ALEX — compétence "navigateur" : onglets, formulaires, clics, lecture de page.

Expressions en français → opérations CDP sur le navigateur d'Alex
(brain/browser.py, instance visible, port 9224). Routée dans main.py
AVANT device_control pour que « ouvre un nouvel onglet » ne tombe pas
dans le motif « ouvre <site> ».

match() = synchrone (regex). run() = asynchrone (appels CDP).
"""

import re

from .. import browser
from .device_control import WEB_SITES

COMMANDS = [
    # Aller à l'onglet N (1-based) — avant tab_new pour « ouvre l'onglet 2 »
    ("tab_goto", re.compile(
        r"(?:passe|va|affiche|montre|ouvre|s[ée]lectionne|active)\s+(?:à|au|sur)\s+"
        r"l['’]?\s*onglet\s*(?:num[eé]ro\s*)?#?(\d+)|^onglet\s*(\d+)$|l['’]?\s*onglet\s*(\d+)\s*$",
        re.I)),
    # Nouvel onglet (avec URL/site optionnel)
    ("tab_new", re.compile(
        r"(?:ouvre|cr[ée]e|cree|lance|ajoute|met|ouvre-moi)\s+(?:un\s+|le\s+)?"
        r"(?:nouvel?\s+|autre\s+|vide\s+)?onglet"
        r"(?:\s+(?:avec|sur|vers|pour|:)\s+(.+))?$|^onglet\s+(?:nouveau|vide)$",
        re.I)),
    # Fermer un onglet
    ("tab_close", re.compile(
        r"ferme\s+(?:tous\s+les\s+)?l['’]?\s*onglet(?:\s*(?:num[eé]ro\s*)?#?(\d+))?",
        re.I)),
    ("tab_list", re.compile(
        r"(?:liste|montre|affiche|combien\s+(?:d['’]|de\s+))\s*(?:les\s+|des\s+)?onglets?|"
        r"quels?\s+onglets?\s+(?:sont\s+)?(?:ouverts|actifs)", re.I)),
    ("tab_next", re.compile(
        r"onglet\s+(?:suivant|d['’]apr[eè]s|suivante)|passe\s+(?:à|au)\s+l['’]?\s*onglet\s+suivant",
        re.I)),
    ("tab_prev", re.compile(
        r"onglet\s+(?:pr[eé]c[eé]dent|d['’]avant)|passe\s+(?:à|au)\s+l['’]?\s*onglet\s+pr[eé]c[eé]dent",
        re.I)),
    # Touche Entrée (avant click : « appuie sur entrée »)
    ("press_enter", re.compile(
        r"(?:appuie|valide|tape|envoie|entre)\s+(?:sur\s+)?(?:la\s+touche\s+)?(?:entr[ée]e|enter|return)\b",
        re.I)),
    # Remplir : « remplis le champ X avec Y »
    ("fill_with", re.compile(
        r"(?:rempli[sz]?|remplit|saisis|saisi|entre|[ée]cris|tape|met[sz]|colle)\s+"
        r"(?:moi\s+)?(?:dans\s+)?(?:le\s+|la\s+|l['’]\s*)?(?:champ\s+|case\s+|zone\s+|input\s+)?"
        r"(.+?)\s+avec\s+(.+)$", re.I)),
    # Remplir : « saisis Y dans le champ X »
    ("fill_in", re.compile(
        r"(?:rempli[sz]?|saisis|saisi|entre|[ée]cris|tape|met[sz]|colle)\s+(.+?)\s+"
        r"dans\s+(?:le\s+|la\s+|l['’]\s*)?(?:champ\s+|case\s+|zone\s+|input\s+)?(.+)$", re.I)),
    # Clic sur bouton/lien/texte
    ("click", re.compile(
        r"(?:clique|clic|appuie|s[ée]lectionne|choisis|valid[ée]?)\s+(?:sur\s+)?"
        r"(?:le\s+|la\s+|l['’]\s*)?(?:bouton\s+|lien\s+)?(?:«\s*([^»]+?)\s*»|\"([^\"]+)\"|(.+))$",
        re.I)),
    # Navigation
    ("back", re.compile(
        r"reviens?\s+en\s+arri[èe]re|page\s+pr[eé]c[eé]dente|retour(?:ne)?\s+(?:en\s+arri[èe]re|en\s+back)",
        re.I)),
    ("forward", re.compile(
        r"avance(?:\s+la\s+page)?|page\s+suivante", re.I)),
    ("reload", re.compile(
        r"recharge(?:\s+la\s+page)?|rafra[îi]chis(?:\s+la\s+page)?|\breload\b", re.I)),
    # Défilement
    ("scroll_down", re.compile(
        r"d[ée]file\s*(?:vers\s+)?(?:le\s+)?bas|descends?(?:\s+(?:la\s+page|vers\s+le\s+bas))?"
        r"|\bscroll\s*(?:vers\s+)?(?:le\s+)?bas|va\s+en\s+bas", re.I)),
    ("scroll_up", re.compile(
        r"remonte(?:\s+(?:la\s+page|en\s+haut))?|monte\s+(?:vers\s+)?(?:le\s+)?haut"
        r"|\bscroll\s+(?:vers\s+)?(?:le\s+)?haut|va\s+en\s+haut", re.I)),
    # Lecture / état
    ("read", re.compile(
        r"\b(?:lis|relis)(?:[- ]moi)?(?:\s+(?:moi\s+)?(?:la\s+page|cette\s+page|le\s+contenu|le\s+texte|tout))?"
        r"|\bque\s+(?:dit|dis|contient)\s+(?:la|cette)\s+page\b", re.I)),
    ("page_info", re.compile(
        r"quelle\s+page|on\s+est\s+[oó]ù|c['’]est\s+quoi\s+(?:la|cette)\s+page"
        r"|o[uù]\s+en\s+sommes|quelle\s+site\s+on\s+est", re.I)),
]


def match(text: str):
    """Retourne (action, match) si la phrase est une commande navigateur."""
    t = text.strip()
    for action, pattern in COMMANDS:
        m = pattern.search(t)
        if m:
            return action, m
    return None, None


async def open_site(arg: str):
    """Ouvre un site/URL dans le navigateur d'Alex. Retourne None si ce n'est pas du web
    (laisse device_control gérer applications locales / recherche)."""
    clean = (arg or "").strip().lower().rstrip(".!?")
    if not clean:
        return None
    url = WEB_SITES.get(clean)
    if not url and clean.startswith(("http://", "https://")):
        url = clean
    elif not url and "." in clean and " " not in clean:
        url = "https://" + clean
    if not url:
        return None
    try:
        r = await browser.open_url_smart(url)
        title = r.get("title") or url
        return f"Ouvert dans le navigateur d'Alex — « {title} »."
    except Exception as e:
        return f"Erreur d'ouverture : {str(e)[:150]}"


def _g(m, *groups):
    for i in groups:
        if i <= len(m.groups()) and m.group(i):
            return m.group(i).strip()
    return ""


async def _champs_disponibles(limit: int = 6) -> str:
    try:
        page = await browser.get_text()
        labels = []
        for inp in page.get("inputs", [])[:40]:
            if inp.get("tag") == "button" or inp.get("type") in ("file", "hidden", "submit", "button", "image"):
                continue
            lab = inp.get("label") or inp.get("placeholder") or inp.get("aria") or inp.get("name")
            if lab and lab not in labels:
                labels.append(lab)
        if labels:
            return " Champs visibles : " + ", ".join(labels[:limit]) + "."
    except Exception:
        pass
    return ""


async def run(action: str, m) -> str:
    """Exécute l'action navigateur et retourne une réponse courte en français."""
    try:
        if action == "tab_new":
            arg = (_g(m, 1) or "").strip()
            url = "about:blank"
            if arg:
                site = WEB_SITES.get(arg.lower().rstrip(".!?"))
                if site:
                    url = site
                elif arg.startswith("http") or ("." in arg and " " not in arg):
                    url = arg if arg.startswith("http") else f"https://{arg}"
                else:
                    url = f"https://www.google.com/search?q={arg}"
            r = await browser.new_tab(url)
            n = r.get("tabs", 1)
            quoi = r.get("title") or (url if url != "about:blank" else "vide")
            return f"Nouvel onglet ouvert ({n} au total) — « {quoi} »."

        if action == "tab_close":
            num = _g(m, 1)
            idx = int(num) - 1 if num else None
            r = await browser.close_tab(idx)
            if r.get("ok"):
                return f"Onglet fermé — « {r.get('closed', '')} ». Il en reste {r.get('tabs', 0)}."
            return f"Impossible de fermer l'onglet : {r.get('error', 'erreur')}."

        if action == "tab_list":
            tabs = await browser.list_tabs()
            if not tabs:
                return "Aucun onglet ouvert."
            lignes = []
            for t in tabs:
                marque = " (actif)" if t.get("active") else ""
                nom = t.get("title") or t.get("url") or ""
                lignes.append(f"{t['index'] + 1}. « {nom[:50]} »{marque}")
            return f"{len(tabs)} onglet(s) — " + " | ".join(lignes)

        if action == "tab_next":
            r = await browser.activate_tab()
            if r.get("ok"):
                return f"Onglet {r['index'] + 1} — « {r.get('title') or r.get('url')} »."
            return f"Pas d'onglet suivant : {r.get('error', '')}."

        if action == "tab_prev":
            tabs = await browser.list_tabs()
            if not tabs:
                return "Aucun onglet ouvert."
            cur = next((t["index"] for t in tabs if t["active"]), 0)
            prev = (cur - 1) % len(tabs)
            r = await browser.activate_tab(prev)
            if r.get("ok"):
                return f"Onglet {r['index'] + 1} — « {r.get('title') or r.get('url')} »."
            return "Impossible de changer d'onglet."

        if action == "tab_goto":
            num = _g(m, 1, 2, 3)
            r = await browser.activate_tab(int(num) - 1 if num else None)
            if r.get("ok"):
                return f"Onglet {r['index'] + 1} — « {r.get('title') or r.get('url')} »."
            return f"{r.get('error', 'Onglet introuvable')}."

        if action == "fill_with":
            label, value = _g(m, 1), _g(m, 2)
            r = await browser.fill_field(label, value)
            if r.get("ok"):
                return f"Champ « {label} » rempli avec « {value} »."
            return f"Je ne trouve pas le champ « {label} » sur cette page." + await _champs_disponibles()

        if action == "fill_in":
            value, label = _g(m, 1), _g(m, 2)
            r = await browser.fill_field(label, value)
            if r.get("ok"):
                return f"Champ « {label} » rempli avec « {value} »."
            return f"Je ne trouve pas le champ « {label} » sur cette page." + await _champs_disponibles()

        if action == "click":
            text = (_g(m, 1, 2, 3) or "").strip().rstrip(".!?")
            r = await browser.click_text(text)
            if r.get("ok"):
                return f"Cliqué sur « {r.get('text') or text} »."
            return f"Je ne trouve pas « {text} » sur la page." + await _champs_disponibles()

        if action == "press_enter":
            await browser.press_key("Enter")
            return "Touche Entrée envoyée."

        if action == "back":
            r = await browser.go_back()
            return f"Page précédente — « {r.get('title') or r.get('url')} »."

        if action == "forward":
            r = await browser.go_forward()
            return f"Page suivante — « {r.get('title') or r.get('url')} »."

        if action == "reload":
            r = await browser.reload()
            return f"Page rechargée — « {r.get('title') or r.get('url')} »."

        if action == "scroll_down":
            await browser.scroll("down")
            return "Je défile vers le bas."

        if action == "scroll_up":
            await browser.scroll("up")
            return "Je remonte vers le haut."

        if action == "read":
            page = await browser.get_text()
            text = (page.get("text") or "").strip()
            title = page.get("title", "")
            if not text:
                return f"Page « {title} » — aucun texte lisible."
            extra = len(text) - 500
            excerpt = text[:500].replace("\n", " ")
            suite = f" … (+{extra} caractères)" if extra > 0 else ""
            return f"« {title} » — {excerpt}{suite}"

        if action == "page_info":
            st = await browser.status()
            if not st.get("running"):
                return "Le navigateur n'est pas encore ouvert."
            return f"On est sur « {st.get('title', '')} » — {st.get('url', '')}."

    except Exception as e:
        return f"Erreur navigateur : {str(e)[:200]}"
    return "Action navigateur inconnue."
