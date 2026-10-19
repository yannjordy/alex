"""Navigateur Alex — contrôle via CDP (Chrome DevTools Protocol).

Instance unique VISIBLE (plus de headless) : chromium/chrome lancé avec
fenêtre X11 (--ozone-platform=x11), port CDP 9224, profil persistant
~/.alex/browser. Toutes les opérations passent par websocket CDP :
onglets, navigation, remplissage par libellé, clic par texte, lecture.
"""

import asyncio
import base64
import glob
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from typing import Optional

import websockets

CDP_PORT = 9224
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
HOME = os.path.expanduser("~")
_user_data = os.path.join(HOME, ".alex", "browser-thorium")  # l'ancien ~/.alex/browser (ère chromium) crash Thorium en SIGTRAP
_proc: Optional[subprocess.Popen] = None
_ws = None
_ws_tab: Optional[str] = None
_ws_lock = asyncio.Lock()
_mid = 0

_DESKTOP_TO_BIN = {
    "google-chrome.desktop": "google-chrome",
    "google-chrome-stable.desktop": "google-chrome-stable",
    "chromium.desktop": "chromium",
    "chromium-browser.desktop": "chromium-browser",
    "thorium-browser.desktop": "thorium-browser",
    "thorium.desktop": "thorium-browser",
    "firefox.desktop": "firefox",
}


def _session_env():
    """Environnement graphique complet (DISPLAY, XAUTHORITY variable, DBus…)."""
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/run/user/1000/bus"
    if not env.get("XAUTHORITY"):
        auths = sorted(glob.glob("/run/user/1000/.mutter-Xwaylandauth.*"))
        if auths:
            env["XAUTHORITY"] = auths[0]
    return env


def _find_browser():
    """Navigateur d'Alex : ALEX_BROWSER > thorium > chromium > défaut système > chrome."""
    try:
        out = subprocess.run(
            ["xdg-mime", "query", "default", "x-scheme-handler/https"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception:
        out = ""
    preferred = _DESKTOP_TO_BIN.get(out)
    candidates = [b for b in (
        os.environ.get("ALEX_BROWSER", ""),
        "thorium-browser",
        "chromium", "chromium-browser", "/snap/bin/chromium",
        preferred or "",
        "google-chrome", "google-chrome-stable", "firefox",
    ) if b]
    seen = set()
    for bin_ in candidates:
        if bin_ in seen:
            continue
        seen.add(bin_)
        if bin_.startswith("/"):
            path = bin_ if os.path.isfile(bin_) and os.access(bin_, os.X_OK) else None
        else:
            path = shutil.which(bin_)
        if path:
            return path
    for p in ("/snap/bin/chromium", "/usr/bin/chromium", "/usr/bin/chromium-browser"):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


async def start() -> bool:
    """Lance le navigateur d'Alex (visible) avec CDP. True si OK."""
    global _proc
    if _proc and _proc.poll() is None:
        return True
    # Un ancien process peut tenir le port
    try:
        urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1)
        _proc = type("P", (), {"poll": lambda s: None, "pid": None})()
        return True
    except Exception:
        pass
    try:
        subprocess.run(["fuser", "-k", f"{CDP_PORT}/tcp"],
                       capture_output=True, timeout=3)
        await asyncio.sleep(0.5)
    except Exception:
        pass
    binary = _find_browser()
    if not binary:
        print("[browser] aucun navigateur trouvé", flush=True)
        return False
    os.makedirs(_user_data, exist_ok=True)
    args = [
        binary,
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-allow-origins=*",
        f"--user-data-dir={_user_data}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--window-size=1280,800",
    ]
    _bname = os.path.basename(binary).lower()
    if any(k in _bname for k in ("chrom", "thor")):
        # Xwayland : le mode Wayland natif ne mappe pas la fenêtre en lancement service
        args.append("--ozone-platform=x11")
    args.append("about:blank")
    _proc = subprocess.Popen(
        args,
        env=_session_env(),
        stdout=open("/tmp/alex-browser.log", "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(50):
        await asyncio.sleep(0.3)
        try:
            urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1)
            print(f"[browser] démarré (pid={_proc.pid}, port={CDP_PORT}, {binary})", flush=True)
            await asyncio.sleep(1.0)
            return True
        except Exception:
            if _proc.poll() is not None:
                print(f"[browser] mort (exit={_proc.returncode}) — voir /tmp/alex-browser.log", flush=True)
                return False
    print("[browser] timeout démarrage", flush=True)
    return False


async def stop():
    global _proc, _ws, _ws_tab
    if _ws:
        try:
            await _ws.close()
        except Exception:
            pass
        _ws, _ws_tab = None, None
    if _proc and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except Exception:
            _proc.kill()
    _proc = None
    print("[browser] arrêté", flush=True)


def _http(method: str, path: str, timeout: float = 5.0):
    req = urllib.request.Request(f"{CDP_URL}{path}", method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


async def _get_ws(tab_id: Optional[str] = None):
    """Connexion CDP vers un onglet (ou l'actif si None)."""
    global _ws, _ws_tab, _mid
    if _ws is not None and (tab_id is None or tab_id == _ws_tab):
        return _ws
    if _ws is not None:
        try:
            await _ws.close()
        except Exception:
            pass
        _ws, _ws_tab = None, None
    tabs = json.loads(_http("GET", "/json", timeout=3).decode())
    pages = [t for t in tabs if t.get("type") == "page"]
    page = next((t for t in pages if t.get("id") == tab_id), None) if tab_id else next(
        (t for t in pages if "about:blank" not in t.get("url", "")), None)
    if page is None:
        page = pages[0] if pages else None
    if page is None:
        _http("PUT", "/json/new?about:blank")
        await asyncio.sleep(0.5)
        tabs = json.loads(_http("GET", "/json").decode())
        page = next((t for t in tabs if t.get("type") == "page"), None)
    if page is None:
        raise RuntimeError("aucun onglet")
    _ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024)
    _ws_tab = page.get("id")
    return _ws


async def _send(method: str, params: dict = None, timeout: float = 30.0,
                tab_id: Optional[str] = None) -> dict:
    global _mid
    async with _ws_lock:
        ws = await _get_ws(tab_id)
        _mid += 1
        mid = _mid
        await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.5, deadline - time.time()))
            except asyncio.TimeoutError:
                continue
            except Exception:
                _ws, _ws_tab = None, None
                ws = await _get_ws(tab_id)
                await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
                continue
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error'].get('message', 'erreur')}")
                return msg.get("result", {})
    raise TimeoutError(f"CDP {method} timeout {timeout}s")


async def _eval_js(expression: str, timeout: float = 15.0, tab_id: Optional[str] = None):
    r = await _send(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        timeout=timeout, tab_id=tab_id,
    )
    if "exceptionDetails" in r:
        desc = (r["exceptionDetails"].get("exception") or {}).get("description", "")
        raise RuntimeError(f"JS: {desc[:300]}")
    return r.get("result", {}).get("value")


async def navigate(url: str) -> dict:
    """Ouvre une URL dans l'onglet actif."""
    await start()
    await _send("Page.enable")
    await _send("Page.navigate", {"url": url}, timeout=25)
    await asyncio.sleep(1.5)
    title = await _eval_js("document.title")
    cur = await _eval_js("location.href")
    return {"url": cur, "title": title}


async def open_url_smart(url: str) -> dict:
    """URL dans l'onglet actif si vide, sinon nouvel onglet."""
    await start()
    try:
        cur = await _eval_js("location.href", timeout=5)
    except Exception:
        cur = "about:blank"
    if not cur or "about:blank" in cur:
        return await navigate(url)
    return await new_tab(url)


async def screenshot(full_page: bool = False) -> str:
    await start()
    r = await _send("Page.captureScreenshot", {"format": "png", "fullPage": full_page}, timeout=20)
    data = base64.b64decode(r["data"])
    path = os.path.join(tempfile.gettempdir(), f"alex-browser-{int(time.time())}.png")
    with open(path, "wb") as f:
        f.write(data)
    return path


async def get_text() -> dict:
    """Texte lisible de la page + liens + champs de formulaire."""
    await start()
    js = """(() => {
      const text = document.body ? document.body.innerText : '';
      const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 50).map(a => ({
        text: (a.innerText || '').trim().slice(0, 80), href: a.href
      })).filter(l => l.text);
      const inputs = Array.from(document.querySelectorAll('input, textarea, select, button')).map((el, i) => {
        let label = '';
        if (el.labels && el.labels.length) label = Array.from(el.labels).map(l => l.innerText).join(' ').trim();
        return {
          i, tag: el.tagName.toLowerCase(), type: el.type || '',
          name: el.name || '', id: el.id || '',
          label: (label || '').slice(0, 60),
          placeholder: el.placeholder || '',
          aria: el.getAttribute('aria-label') || '',
          value: (el.value || '').slice(0, 50),
          text: (el.innerText || '').trim().slice(0, 50),
        };
      });
      return { title: document.title, url: location.href, text: text.slice(0, 4000), links, inputs };
    })()"""
    return await _eval_js(js)


async def click(selector: str) -> dict:
    """Clique sur un élément (CSS selector)."""
    await start()
    js = f"""(() => {{
      const el = document.querySelector({json.dumps(selector)});
      if (!el) return {{ok: false, error: 'introuvable: ' + {json.dumps(selector)}}};
      el.scrollIntoView({{block: 'center'}});
      el.click();
      return {{ok: true, tag: el.tagName.toLowerCase()}};
    }})()"""
    r = await _eval_js(js)
    await asyncio.sleep(0.8)
    return r


async def click_text(text: str) -> dict:
    """Clique sur un bouton/lien/champ dont le texte correspond (langage naturel)."""
    await start()
    js = f"""(() => {{
      const want = {json.dumps(text)}.trim().toLowerCase().replace(/\\s+/g, ' ');
      const norm = s => (s || '').trim().toLowerCase().replace(/\\s+/g, ' ');
      const pool = Array.from(document.querySelectorAll(
        'a, button, [role=button], [role=link], [role=tab], input[type=submit], input[type=button], summary, label, li[onclick], div[onclick], span[onclick]'));
      let el = pool.find(e => norm(e.innerText || e.value || e.textContent) === want);
      if (!el) el = pool.find(e => norm(e.innerText || e.value || e.textContent).includes(want));
      if (!el) {{
        // mots-clés : « bouton »/« lien » retirés côté appelant, sinon tous mots présents
        const words = want.split(' ').filter(w => w.length > 2);
        if (words.length) el = pool.find(e => {{
          const t = norm(e.innerText || e.value || e.textContent);
          return words.every(w => t.includes(w));
        }});
      }}
      if (!el) return {{ok: false, error: 'introuvable: ' + {json.dumps(text)}}};
      el.scrollIntoView({{block: 'center'}});
      el.click();
      return {{ok: true, tag: el.tagName.toLowerCase(), text: (el.innerText || el.value || '').trim().slice(0, 60)}};
    }})()"""
    r = await _eval_js(js)
    await asyncio.sleep(1.0)
    return r


async def fill(selector: str, value: str) -> dict:
    """Remplit un champ (CSS selector) avec une valeur."""
    await start()
    js = f"""(() => {{
      const el = document.querySelector({json.dumps(selector)});
      if (!el) return {{ok: false, error: 'introuvable: ' + {json.dumps(selector)}}};
      el.scrollIntoView({{block: 'center'}});
      if (el.tagName === 'SELECT') {{
        el.value = {json.dumps(value)};
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
      }} else {{
        el.focus();
        el.value = {json.dumps(value)};
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
      }}
      return {{ok: true}};
    }})()"""
    return await _eval_js(js)


async def fill_field(label: str, value: str) -> dict:
    """Remplit un champ identifié par libellé/placeholder/nom (langage naturel)."""
    await start()
    js = f"""(() => {{
      const want = {json.dumps(label)}.trim().toLowerCase().replace(/\\s+/g, ' ');
      const norm = s => (s || '').trim().toLowerCase().replace(/\\s+/g, ' ');
      const val = {json.dumps(value)};
      const els = Array.from(document.querySelectorAll(
        'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image]), textarea, select'));
      const hay = e => {{
        let lab = '';
        if (e.labels && e.labels.length) lab = Array.from(e.labels).map(l => l.innerText).join(' ');
        return norm([e.getAttribute('aria-label'), e.placeholder, e.name, e.id,
                     e.getAttribute('title'), lab].join(' '));
      }};
      let el = els.find(e => hay(e) === want) || els.find(e => hay(e).includes(want));
      if (!el) {{
        const L = Array.from(document.querySelectorAll('label')).find(l => norm(l.innerText).includes(want));
        if (L) el = document.getElementById(L.htmlFor) || L.querySelector('input, textarea, select');
      }}
      if (!el) {{
        const words = want.split(' ').filter(w => w.length > 2);
        if (words.length) el = els.find(e => words.every(w => hay(e).includes(w)));
      }}
      if (!el) {{
        const toks = want.split(' ').filter(t => t.length > 2);
        if (toks.length) el = els.find(e => {{
          const ht = hay(e).split(/[\\s.,;:()\\[\\]«»'"\\/-]+/).filter(t => t.length > 2);
          return toks.some(t => ht.some(x => x.startsWith(t) || t.startsWith(x)));
        }});
      }}
      if (!el) return {{ok: false, error: 'champ introuvable: ' + {json.dumps(label)}}};
      el.scrollIntoView({{block: 'center'}});
      el.focus();
      if (el.tagName === 'SELECT') {{
        const opt = Array.from(el.options).find(o => norm(o.text).includes(norm(val)) || norm(o.value) === norm(val));
        if (!opt) return {{ok: false, error: 'option introuvable: ' + val}};
        el.value = opt.value;
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
      }} else if (el.type === 'checkbox' || el.type === 'radio') {{
        const on = /^(1|true|oui|on|coche|case)$/i.test(val.trim());
        if (el.checked !== on) el.click();
      }} else {{
        const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
                   : el.tagName === 'SELECT' ? window.HTMLSelectElement.prototype
                   : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
        setter.call(el, val);
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
      }}
      el.blur();
      return {{ok: true, field: el.name || el.id || el.placeholder || el.getAttribute('aria-label') || ''}};
    }})()"""
    r = await _eval_js(js)
    await asyncio.sleep(0.3)
    return r


async def press(selector: str, key: str) -> dict:
    await start()
    js = f"""(() => {{
      const el = document.querySelector({json.dumps(selector)});
      if (!el) return {{ok: false, error: 'introuvable'}};
      el.focus();
      return {{ok: true}};
    }})()"""
    await _eval_js(js)
    key_map = {
        "Enter": {"windowsVirtualKeyCode": 13, "code": "Enter", "key": "Enter"},
        "Tab": {"windowsVirtualKeyCode": 9, "code": "Tab", "key": "Tab"},
        "Escape": {"windowsVirtualKeyCode": 27, "code": "Escape", "key": "Escape"},
    }
    kv = key_map.get(key, {"windowsVirtualKeyCode": 0, "code": key, "key": key})
    await _send("Input.dispatchKeyEvent", {"type": "keyDown", **kv})
    await _send("Input.dispatchKeyEvent", {"type": "keyUp", **kv})
    await asyncio.sleep(0.5)
    return {"ok": True, "key": key}


async def press_key(key: str = "Enter") -> dict:
    """Touche globale (Enter, Tab, Escape…) dans la page active."""
    key_map = {
        "Enter": {"windowsVirtualKeyCode": 13, "code": "Enter", "key": "Enter"},
        "Tab": {"windowsVirtualKeyCode": 9, "code": "Tab", "key": "Tab"},
        "Escape": {"windowsVirtualKeyCode": 27, "code": "Escape", "key": "Escape"},
        "ArrowDown": {"windowsVirtualKeyCode": 40, "code": "ArrowDown", "key": "ArrowDown"},
        "ArrowUp": {"windowsVirtualKeyCode": 38, "code": "ArrowUp", "key": "ArrowUp"},
    }
    kv = key_map.get(key, {"windowsVirtualKeyCode": 0, "code": key, "key": key})
    await start()
    await _send("Input.dispatchKeyEvent", {"type": "keyDown", **kv})
    await _send("Input.dispatchKeyEvent", {"type": "keyUp", **kv})
    await asyncio.sleep(0.4)
    return {"ok": True, "key": key}


async def scroll(direction: str = "down", amount: int = 400) -> dict:
    await start()
    y = amount if direction in ("down", "bas") else -amount
    await _eval_js(f"window.scrollBy(0, {int(y)})")
    await asyncio.sleep(0.3)
    return {"ok": True, "direction": direction}


async def go_back() -> dict:
    await start()
    await _eval_js("history.back()")
    await asyncio.sleep(1)
    return {"url": await _eval_js("location.href"), "title": await _eval_js("document.title")}


async def go_forward() -> dict:
    await start()
    await _eval_js("history.forward()")
    await asyncio.sleep(1)
    return {"url": await _eval_js("location.href"), "title": await _eval_js("document.title")}


async def reload() -> dict:
    await start()
    await _send("Page.reload", {}, timeout=15)
    await asyncio.sleep(1)
    return {"url": await _eval_js("location.href")}


async def list_tabs() -> list:
    """Liste des onglets : [{index, id, title, url, active}]."""
    await start()
    tabs = json.loads(_http("GET", "/json", timeout=3).decode())
    pages = [t for t in tabs if t.get("type") == "page"]
    out = []
    for i, t in enumerate(pages):
        out.append({
            "index": i, "id": t.get("id"),
            "title": t.get("title", ""), "url": t.get("url", ""),
            "active": t.get("id") == _ws_tab,
        })
    if out and not any(o["active"] for o in out):
        out[0]["active"] = True
    return out


async def new_tab(url: str = "about:blank") -> dict:
    """Nouvel onglet (devient l'onglet actif)."""
    await start()
    try:
        _http("PUT", f"/json/new?{urllib.parse.quote(url, safe=':/?&=%#')}", timeout=5)
    except Exception:
        _http("GET", f"/json/new?{urllib.parse.quote(url, safe=':/?&=%#')}", timeout=5)
    global _ws, _ws_tab
    if _ws:
        try:
            await _ws.close()
        except Exception:
            pass
        _ws, _ws_tab = None, None
    await asyncio.sleep(1.2)
    tabs = await list_tabs()
    active = next((t for t in tabs if t["active"]), tabs[-1] if tabs else None)
    return {"tabs": len(tabs), "title": active["title"] if active else "",
            "url": active["url"] if active else url}


async def close_tab(index: int = None) -> dict:
    """Ferme l'onglet actif (ou celui de l'index donné)."""
    await start()
    tabs = await list_tabs()
    if not tabs:
        return {"ok": False, "error": "aucun onglet"}
    target = None
    if index is not None:
        target = next((t for t in tabs if t["index"] == index), None)
        if not target:
            return {"ok": False, "error": f"onglet {index} introuvable", "tabs": len(tabs)}
    else:
        target = next((t for t in tabs if t["active"]), tabs[-1])
    global _ws, _ws_tab
    if _ws_tab == target["id"] and _ws:
        try:
            await _ws.close()
        except Exception:
            pass
        _ws, _ws_tab = None, None
    try:
        _http("GET", f"/json/close/{target['id']}", timeout=5)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    await asyncio.sleep(0.5)
    remaining = await list_tabs()
    if remaining and _ws_tab is None:
        nxt = next((t for t in remaining if t["active"]), remaining[0])
        _ws_tab = nxt["id"]
    return {"ok": True, "closed": target["title"] or target["url"], "tabs": len(remaining)}


async def activate_tab(index: int = None) -> dict:
    """Passe à l'onglet d'index donné (ou suivant si None)."""
    global _ws, _ws_tab
    await start()
    tabs = await list_tabs()
    if not tabs:
        return {"ok": False, "error": "aucun onglet"}
    if index is None:
        cur = next((t["index"] for t in tabs if t["active"]), 0)
        index = (cur + 1) % len(tabs)
    target = next((t for t in tabs if t["index"] == index), None)
    if not target:
        return {"ok": False, "error": f"onglet {index} introuvable", "tabs": len(tabs)}
    if _ws:
        try:
            await _ws.close()
        except Exception:
            pass
        _ws, _ws_tab = None, None
    _ws_tab = target["id"]
    await _get_ws(target["id"])
    try:
        await _send("Page.bringToFront", {}, timeout=5, tab_id=target["id"])
    except Exception:
        pass
    return {"ok": True, "index": index, "title": target["title"], "url": target["url"]}


async def status() -> dict:
    running = _proc is not None and _proc.poll() is None
    info = {"running": running, "pid": _proc.pid if (_proc and getattr(_proc, "pid", None)) else None,
            "port": CDP_PORT}
    if running or _proc is None:
        try:
            tabs = json.loads(_http("GET", "/json", timeout=2).decode())
            pages = [t for t in tabs if t.get("type") == "page"]
            info["running"] = True
            info["tabs"] = len(pages)
            active = next((t for t in pages if t.get("id") == _ws_tab), pages[0] if pages else None)
            if active:
                info["url"] = active.get("url", "")
                info["title"] = active.get("title", "")
        except Exception:
            pass
    return info
