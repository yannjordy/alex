"""Navigateur Alex — contrôle Chromium via CDP (Chrome DevTools Protocol)."""

import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

import websockets

CDP_PORT = 9224
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
_user_data = os.path.join(tempfile.gettempdir(), "alex-browser-profile")
_proc: Optional[subprocess.Popen] = None
_ws = None
_ws_lock = asyncio.Lock()
_mid = 0


def _find_chromium():
    for p in [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ]:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


async def start() -> bool:
    """Lance Chromium headless avec CDP. Retourne True si OK."""
    global _proc
    if _proc and _proc.poll() is None:
        return True
    binary = _find_chromium()
    if not binary:
        print("[browser] aucun Chromium trouvé", flush=True)
        return False
    # Libérer le port si un ancien process le tient
    try:
        import subprocess as sp
        sp.run(["fuser", "-k", f"{CDP_PORT}/tcp"], capture_output=True, timeout=3)
        await asyncio.sleep(0.5)
    except Exception:
        pass
    os.makedirs(_user_data, exist_ok=True)
    _proc = subprocess.Popen(
        [
            binary,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={_user_data}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--headless=new",
            "--window-size=1280,800",
            "--no-sandbox",
            "--disable-gpu",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Attendre que le port réponde
    import urllib.request
    for i in range(40):
        await asyncio.sleep(0.3)
        try:
            urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=1)
            print(f"[browser] Chromium démarré (pid={_proc.pid}, port={CDP_PORT}, binary={binary})", flush=True)
            return True
        except Exception:
            if _proc.poll() is not None:
                print(f"[browser] Chromium est mort (exit={_proc.returncode})", flush=True)
                return False
            continue
    print("[browser] timeout démarrage Chromium", flush=True)
    return False


async def stop():
    global _proc, _ws
    if _ws:
        try:
            await _ws.close()
        except Exception:
            pass
        _ws = None
    if _proc:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except Exception:
            _proc.kill()
        _proc = None
    print("[browser] arrêté", flush=True)


async def _get_ws():
    """Connexion CDP vers la première page (onglet)."""
    global _ws, _mid
    if _ws:
        return _ws
    import urllib.request
    tabs = json.loads(urllib.request.urlopen(f"{CDP_URL}/json", timeout=3).read())
    page = next((t for t in tabs if t.get("type") == "page"), None)
    if not page:
        # créer un onglet
        urllib.request.urlopen(f"{CDP_URL}/json/new?about:blank", timeout=3)
        tabs = json.loads(urllib.request.urlopen(f"{CDP_URL}/json", timeout=3).read())
        page = next((t for t in tabs if t.get("type") == "page"), None)
    if not page:
        raise RuntimeError("aucun onglet page")
    _ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=50 * 1024 * 1024)
    return _ws


async def _send(method: str, params: dict = None, timeout: float = 30.0) -> dict:
    """Envoie une commande CDP et retourne le résultat."""
    global _mid
    async with _ws_lock:
        ws = await _get_ws()
        _mid += 1
        mid = _mid
        await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.5, deadline - time.time()))
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error'].get('message', 'erreur')}")
                return msg.get("result", {})
    raise TimeoutError(f"CDP {method} timeout {timeout}s")


async def _eval_js(expression: str, timeout: float = 15.0):
    """Évalue du JS dans la page et retourne la valeur."""
    r = await _send(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        timeout=timeout,
    )
    if "exceptionDetails" in r:
        desc = (r["exceptionDetails"].get("exception") or {}).get("description", "")
        raise RuntimeError(f"JS: {desc[:300]}")
    return r.get("result", {}).get("value")


async def navigate(url: str) -> dict:
    """Ouvre une URL. Retourne {url, title}."""
    await start()
    await _send("Page.enable")
    await _send("Page.navigate", {"url": url}, timeout=25)
    await asyncio.sleep(1.2)
    title = await _eval_js("document.title")
    cur = await _eval_js("location.href")
    return {"url": cur, "title": title}


async def screenshot(full_page: bool = False) -> str:
    """Capture d'écran → chemin PNG."""
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
      const inputs = Array.from(document.querySelectorAll('input, textarea, select, button')).map((el, i) => ({
        i, tag: el.tagName.toLowerCase(), type: el.type || '',
        name: el.name || '', id: el.id || '',
        placeholder: el.placeholder || '',
        value: (el.value || '').slice(0, 50),
        text: (el.innerText || '').trim().slice(0, 50),
      }));
      return { title: document.title, url: location.href, text: text.slice(0, 4000), links, inputs };
    })()"""
    return await _eval_js(js)


async def click(selector: str) -> dict:
    """Clique sur un élément (CSS selector)."""
    await start()
    # Scroll dans le viewport + click via JS (fiable headless)
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
    r = await _eval_js(js)
    return r


async def press(selector: str, key: str) -> dict:
    """Appuie sur une touche après focus d'un élément."""
    await start()
    js = f"""(() => {{
      const el = document.querySelector({json.dumps(selector)});
      if (!el) return {{ok: false, error: 'introuvable'}};
      el.focus();
      return {{ok: true}};
    }})()"""
    await _eval_js(js)
    # Utiliser CDP Input.dispatchKeyEvent pour Enter etc.
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


async def scroll(direction: str = "down", amount: int = 400) -> dict:
    """Scroll haut/bas."""
    await start()
    y = amount if direction == "down" else -amount
    await _eval_js(f"window.scrollBy(0, {int(y)})")
    await asyncio.sleep(0.3)
    return {"ok": True, "direction": direction}


async def go_back() -> dict:
    await start()
    await _eval_js("history.back()")
    await asyncio.sleep(1)
    return {"url": await _eval_js("location.href")}


async def go_forward() -> dict:
    await start()
    await _eval_js("history.forward()")
    await asyncio.sleep(1)
    return {"url": await _eval_js("location.href")}


async def reload() -> dict:
    await start()
    await _send("Page.reload", {}, timeout=15)
    await asyncio.sleep(1)
    return {"url": await _eval_js("location.href")}


async def new_tab(url: str = "about:blank") -> dict:
    """Ouvre un nouvel onglet."""
    import urllib.request
    urllib.request.urlopen(f"{CDP_URL}/json/new?{url}", timeout=5)
    global _ws
    if _ws:
        try:
            await _ws.close()
        except Exception:
            pass
        _ws = None
    await asyncio.sleep(0.5)
    await start()
    return {"url": url}


async def status() -> dict:
    """État du navigateur."""
    running = _proc is not None and _proc.poll() is None
    info = {"running": running, "pid": _proc.pid if _proc else None}
    if running:
        try:
            import urllib.request
            tabs = json.loads(urllib.request.urlopen(f"{CDP_URL}/json", timeout=2).read())
            pages = [t for t in tabs if t.get("type") == "page"]
            info["tabs"] = len(pages)
            if pages:
                info["url"] = pages[0].get("url", "")
                info["title"] = pages[0].get("title", "")
        except Exception:
            pass
    return info
