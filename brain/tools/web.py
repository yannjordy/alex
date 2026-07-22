import asyncio
import re
import urllib.parse
import httpx
from . import tool

SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

STOP_WORDS = frozenset(
    "le la les de des du un une est et a au sur dans pour par avec ce se que qui "
    "quoi dont où es tu il elle on nous vous ils elles son sa ses mon ma mes "
    "ton ta ses notre nos votre vos leur leurs je tu il elle on nous vous ils "
    "elles me te se lui y en ne pas plus tres bien mal aussi mais donc or ni car "
    "si comme quand lorsque puisque parce que cependant toute tous toutes "
    "plusieurs certains certaines chaque aucun aucune quelques soit soit".split())


def _clean_html(html: str) -> str:
    text = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL)
    text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL)
    text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL)
    text = re.sub(r'<form[^>]*>.*?</form>', '', text, flags=re.DOTALL)
    text = re.sub(r'<aside[^>]*>.*?</aside>', '', text, flags=re.DOTALL)
    text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL)
    text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL)
    text = re.sub(r'<form[^>]*>.*?</form>', '', text, flags=re.DOTALL)
    text = re.sub(r'<aside[^>]*>.*?</aside>', '', text, flags=re.DOTALL)
    text = re.sub(r'<svg[^>]*>.*?</svg>', '', text, flags=re.DOTALL)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r'\{\{[^}]*\}\}', ' ', text)
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'\[\[[^\]]*\]\]', ' ', text)
    texts = []
    for tag in ('h1', 'h2', 'h3', 'h4', 'p', 'li', 'td', 'th', 'blockquote', 'figcaption'):
        texts.extend(re.findall(f'<{tag}[^>]*>(.*?)</{tag}>', text, flags=re.DOTALL))
    if not texts:
        texts = [text]
    combined = ' '.join(texts)
    combined = re.sub(r'<[^>]+>', ' ', combined)
    combined = re.sub(r'&[a-z]+;', ' ', combined)
    combined = re.sub(r'&#\d+;', ' ', combined)
    combined = re.sub(r'\s+', ' ', combined).strip()
    return combined


def _extract_sentences(text: str) -> list[str]:
    sentences = re.split(r'(?<=[.!?])\s+', text)
    noise = ('cookie', 'accepter', 'refuser', 'mot de passe', 'password',
             'inscrire', 'connexion', "s'inscrire", 'abonnez-vous', 'menu',
             'navigation', 'newsletter', 'recevoir', 'partager', 'twitter',
             'facebook', 'linkedin', 'instagram', 'tiktok')
    clean = []
    for s in sentences:
        s = s.strip()
        if len(s) < 25:
            continue
        lower = s.lower()
        if any(kw in lower for kw in noise):
            continue
        clean.append(s)
    return clean


def _query_keywords(query: str) -> set[str]:
    words = set(re.findall(r'\w+', query.lower()))
    return words - STOP_WORDS


def _relevance_score(text: str, keywords: set[str]) -> float:
    if not keywords:
        return 0
    words = set(re.findall(r'\w+', text.lower()))
    overlap = len(words & keywords)
    return overlap / max(len(keywords), 1)


def _detect_question_type(query: str) -> str:
    q = query.lower().strip()
    if q.startswith(("qui", "quel", "quelle", "quels", "quelles", "qu'est-ce que", "qu'est-ce qu'")):
        return "factual"
    if q.startswith(("comment", "de quelle façon", "de quelle manière")):
        return "howto"
    if q.startswith(("pourquoi")):
        return "why"
    if any(w in q for w in ("combien", "quel âge", "quelle taille", "quel poids", "prix", "coût", "tarif")):
        return "measure"
    if q.startswith(("où")):
        return "where"
    if q.startswith(("quand")):
        return "when"
    if any(w in q for w in ("liste", "quels sont", "quelles sont", "types de", "catégories")):
        return "list"
    return "general"


async def _fetch_page(url: str, client: httpx.AsyncClient) -> tuple[str, str]:
    domain_match = re.match(r'https?://([^/]+)', url)
    domain = domain_match.group(1).replace('www.', '') if domain_match else url
    try:
        resp = await client.get(url, headers=SEARCH_HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        raw = _clean_html(resp.text)
        sentences = _extract_sentences(raw)
        if not sentences:
            return domain, ""
        return domain, '. '.join(sentences[:30])
    except Exception:
        return domain, ""


@tool("recherche_web", "Effectue une recherche approfondie sur le web. Paramètre : requete (la question posée).")
async def recherche_web(requete: str) -> str:
    if not requete or not requete.strip():
        return "Il me faut une requête pour chercher sur le web."

    query = requete.strip()
    encoded = urllib.parse.quote_plus(query)
    keywords = _query_keywords(query)
    qtype = _detect_question_type(query)

    async with httpx.AsyncClient(verify=False) as client:
        search_url = f"https://html.duckduckgo.com/html/?q={encoded}"
        try:
            resp = await client.get(search_url, headers=SEARCH_HEADERS, timeout=20, follow_redirects=True)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            return f"Je n'ai pas pu effectuer la recherche : {e}"

        urls = []
        seen_domains = set()
        for m in re.finditer(r'uddg=([^"&]+)', html):
            u = urllib.parse.unquote(m.group(1))
            if not u.startswith("http"):
                continue
            domain_match = re.match(r'https?://([^/]+)', u)
            domain = domain_match.group(1).replace('www.', '') if domain_match else u
            if domain in seen_domains:
                continue
            seen_domains.add(domain)
            urls.append(u)
            if len(urls) >= 4:
                break

        if not urls:
            for m in re.finditer(r'<a[^>]*href="(https?://[^"]+)"', html):
                u = m.group(1)
                if any(s in u for s in ("duckduckgo.com", "yahoo.com", "bing.com", "google.")):
                    continue
                if len(urls) >= 5:
                    break
                urls.append(u)

        if not urls:
            return f"Je n'ai pas trouvé de résultats pour « {query} »."

        results = await asyncio.gather(*[_fetch_page(u, client) for u in urls], return_exceptions=True)

    sources = []
    for i, r in enumerate(results):
        if isinstance(r, Exception) or not r[1]:
            continue
        sources.append(r)

    if not sources:
        return f"Je n'ai pas pu extraire le contenu des résultats pour « {query} »."

    all_sentences = []
    for domain, text in sources:
        sents = [s.strip() for s in text.split('. ') if len(s.strip()) > 25]
        for s in sents:
            score = _relevance_score(s, keywords)
            all_sentences.append((score, s, domain))

    all_sentences.sort(key=lambda x: -x[0])

    seen = set()
    final = []
    for score, sent, domain in all_sentences:
        key = re.sub(r'\W+', '', sent[:80].lower())
        if key in seen:
            continue
        seen.add(key)
        final.append((score, sent, domain))

    top = [s for s in final if s[0] > 0]
    if not top:
        top = final[:6]
    else:
        top = top[:8]

    if qtype == "factual":
        answer = [f"{top[0][1]}."]
        for _, sent, _ in top[1:5]:
            if sent not in answer[0]:
                answer.append(sent + ".")
        body = ' '.join(answer)
    elif qtype == "list":
        items = [f"  - {s.split('.')[0]}" for _, s, _ in top[:8]]
        body = "Voici ce que j'ai trouvé :\n" + '\n'.join(items)
    elif qtype == "measure":
        body = ' '.join(f"{s}." for _, s, _ in top[:5])
    else:
        body = ' '.join(s for _, s, _ in top[:6])

    # Deduplicate and trim
    body = re.sub(r'\s*\.\s*\.', '.', body)
    body = re.sub(r'\s{2,}', ' ', body)

    if len(body) > 2000:
        body = body[:2000] + "..."

    if len(body) < 40:
        body = ' '.join(s[1] for s in final[:4])

    # Append sources
    domains_used = set(d for _, _, d in top)
    src_line = " — Sources: " + ', '.join(sorted(domains_used)[:3])

    return body + src_line


@tool("recherche_image", "Cherche des images sur le web. Paramètre : requete (ce qu'il faut chercher).")
async def recherche_image(requete: str) -> str:
    if not requete or not requete.strip():
        return "Que veux-tu que je cherche comme image ?"
    query = requete.strip()
    encoded = urllib.parse.quote_plus(query)
    async with httpx.AsyncClient(verify=False) as client:
        try:
            resp = await client.get(
                f"https://html.duckduckgo.com/html/?q={encoded}+site:pinterest.com+OR+site:imgur.com+OR+site:flickr.com&iar=images&iax=images&ia=images",
                headers=SEARCH_HEADERS, timeout=15, follow_redirects=True
            )
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            return f"Je n'ai pas pu chercher des images : {e}"

        urls = []
        for m in re.finditer(r'<a[^>]*href="(https?://[^"]*\.(?:jpe?g|png|gif|webp))"', html, re.IGNORECASE):
            u = m.group(1)
            if u not in urls:
                urls.append(u)
            if len(urls) >= 4:
                break
        if not urls:
            for m in re.finditer(r'uddg=([^"&]+)', html):
                u = urllib.parse.unquote(m.group(1))
                if u.startswith("http") and any(ext in u.lower() for ext in ('.jpg','.jpeg','.png','.gif','.webp')):
                    if u not in urls:
                        urls.append(u)
                    if len(urls) >= 4:
                        break

        if not urls:
            return f"Je n'ai pas trouvé d'images pour « {query} »."

        result = f"Voici des images pour « {query} » :\n"
        for u in urls:
            result += u + "\n"
        return result.strip()


@tool("recherche_video", "Cherche des vidéos sur YouTube. Paramètre : requete (ce qu'il faut chercher).")
async def recherche_video(requete: str) -> str:
    if not requete or not requete.strip():
        return "Quelle vidéo veux-tu que je cherche ?"
    query = requete.strip()
    encoded = urllib.parse.quote_plus(query)
    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        try:
            resp = await client.get(
                f"https://html.duckduckgo.com/html/?q={encoded}+site:youtube.com",
                headers=SEARCH_HEADERS, follow_redirects=True
            )
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            return f"Je n'ai pas pu chercher des vidéos : {e}"

        results = []
        for m in re.finditer(r'uddg=([^"&]+)', html):
            u = urllib.parse.unquote(m.group(1))
            if not u.startswith("http"):
                continue
            if "youtube.com/watch" in u or "youtu.be/" in u:
                if u not in results:
                    results.append(u)
            if len(results) >= 5:
                break

        if not results:
            # Chercher les liens directs YouTube
            for m in re.finditer(r'<a[^>]*href="(https?://(?:www\.)?youtube\.com/watch\?v=[^"]+)"', html, re.IGNORECASE):
                u = m.group(1)
                if u not in results:
                    results.append(u)
                if len(results) >= 5:
                    break

        if not results:
            return f"Je n'ai pas trouvé de vidéos pour « {query} »."

        result = f"Voici des vidéos pour « {query} » :\n"
        for u in results:
            result += u + "\n"
        return result.strip()
