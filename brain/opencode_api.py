"""ALEX — passerelle REST complète vers opencode (sessions, modèles, agents,
permissions, questions, skills, fichiers, VCS…).

Expose des routes /api-oc/* consommées par l'interface Alex.
Tout est un proxy fin vers le serveur opencode local (port 4199).
"""

import os
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

OC = os.environ.get("ALEX_OPENCODE_URL", "http://127.0.0.1:4199")
SID_FILE = os.path.expanduser("~/Documents/alex/.alex-opencode-sid")
TIMEOUT = httpx.Timeout(20.0, connect=5.0)

router = APIRouter(prefix="/api-oc", tags=["opencode"])


def _sid() -> str:
    try:
        s = open(SID_FILE).read().strip()
        if s:
            return s
    except OSError:
        pass
    raise HTTPException(503, "Aucune session opencode (fichier SID absent).")


async def _get(path: str, params: Optional[dict] = None):
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{OC}{path}", params=params)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text[:300])
        return r.json()


async def _send(method: str, path: str, json_body: Optional[dict] = None, params: Optional[dict] = None):
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.request(method, f"{OC}{path}", json=json_body, params=params)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text[:300])
        if not r.content:
            return {"ok": True}
        try:
            return r.json()
        except ValueError:
            return {"ok": True, "raw": r.text[:300]}


def _unwrap(payload):
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


# ---------------------------------------------------------------- statut

@router.get("/status")
async def status():
    try:
        health = await _get("/api/health")
    except HTTPException:
        health = {"healthy": False}
    model = agent = None
    try:
        sid = _sid()
        sess = _unwrap(await _get(f"/api/session/{sid}"))
        model = sess.get("model")
        agent = sess.get("agent")
    except (HTTPException, Exception):
        sid = None
    return {
        "healthy": bool(health.get("healthy")),
        "session_id": sid,
        "model": model,
        "agent": agent,
    }


# ---------------------------------------------------------------- sessions

@router.get("/sessions")
async def sessions():
    data = _unwrap(await _get("/api/session"))
    try:
        cur = _sid()
    except HTTPException:
        cur = None
    out = []
    for s in data:
        if s.get("parentID"):
            continue
        out.append({
            "id": s.get("id"),
            "title": s.get("title") or "Session sans titre",
            "agent": s.get("agent"),
            "model": (s.get("model") or {}).get("id"),
            "tokens": (s.get("tokens") or {}).get("input", 0) + (s.get("tokens") or {}).get("output", 0),
            "time": (s.get("time") or {}),
            "current": s.get("id") == cur,
        })
    out.sort(key=lambda s: s["time"].get("updated", 0), reverse=True)
    return {"data": out}


class SessionCreate(BaseModel):
    title: Optional[str] = None


@router.post("/sessions")
async def create_session(body: SessionCreate = None):
    payload = {}
    if body and body.title:
        payload["title"] = body.title
    sess = _unwrap(await _send("POST", "/api/session", json_body=payload or None))
    sid = sess.get("id") if isinstance(sess, dict) else None
    if not sid:
        raise HTTPException(502, "Session non créée.")
    open(SID_FILE, "w").write(sid)
    return {"id": sid}


class SessionSelect(BaseModel):
    id: str


@router.post("/sessions/select")
async def select_session(body: SessionSelect):
    sess = _unwrap(await _get(f"/api/session/{body.id}"))
    open(SID_FILE, "w").write(body.id)
    return {"id": sess.get("id", body.id), "title": sess.get("title"), "agent": sess.get("agent"), "model": sess.get("model")}


@router.delete("/sessions/{sid}")
async def delete_session(sid: str):
    await _send("DELETE", f"/session/{sid}")
    return {"ok": True}


@router.get("/sessions/{sid}/messages")
async def session_messages(sid: str, limit: int = 200):
    data = _unwrap(await _get(f"/api/session/{sid}/message", params={"limit": limit}))
    out = []
    for m in data:
        role = m.get("type")
        if role not in ("user", "assistant"):
            continue
        texts, tools = [], []
        for part in m.get("content") or []:
            pt = part.get("type")
            if pt == "text":
                texts.append(part.get("text", ""))
            elif pt == "tool":
                state = part.get("state") or {}
                tools.append({
                    "id": part.get("id"),
                    "tool": part.get("tool") or part.get("name"),
                    "status": state.get("status"),
                    "input": state.get("input"),
                    "output": (state.get("output") or "")[:1200] if isinstance(state.get("output"), str) else state.get("output"),
                })
        text = "\n".join(t for t in texts if t).strip()
        if not text and not tools:
            continue
        out.append({
            "id": m.get("id"),
            "role": role,
            "text": text,
            "tools": tools,
            "time": (m.get("time") or {}).get("created"),
        })
    return {"data": out}


# ---------------------------------------------------------------- modèles / agents

@router.get("/models")
async def models():
    data = _unwrap(await _get("/api/model"))
    return {"data": [
        {
            "id": m.get("id"),
            "providerID": m.get("providerID"),
            "name": m.get("name") or m.get("id"),
            "capabilities": m.get("capabilities"),
        }
        for m in data
    ]}


class ModelSwitch(BaseModel):
    providerID: str
    id: str


@router.post("/model")
async def switch_model(body: ModelSwitch):
    await _send("POST", f"/api/session/{_sid()}/model",
                json_body={"model": {"providerID": body.providerID, "id": body.id}})
    return {"ok": True, "model": body.id}


@router.get("/agents")
async def agents():
    data = _unwrap(await _get("/api/agent"))
    try:
        cur = (_unwrap(await _get(f"/api/session/{_sid()}")) or {}).get("agent")
    except Exception:
        cur = None
    return {"data": [
        {
            "id": a.get("id"),
            "description": (a.get("description") or "")[:200],
            "mode": a.get("mode"),
            "current": a.get("id") == cur,
        }
        for a in data if not a.get("hidden")
    ]}


class AgentSwitch(BaseModel):
    id: str


@router.post("/agent")
async def switch_agent(body: AgentSwitch):
    await _send("POST", f"/api/session/{_sid()}/agent", json_body={"agentID": body.id})
    return {"ok": True, "agent": body.id}


# ---------------------------------------------------------------- skills / commands / tools

@router.get("/skills")
async def skills():
    data = _unwrap(await _get("/api/skill"))
    return {"data": [{"name": s.get("name"), "description": (s.get("description") or "")[:240]} for s in data]}


@router.get("/commands")
async def commands():
    data = _unwrap(await _get("/api/command"))
    return {"data": [{"name": c.get("name"), "description": (c.get("description") or c.get("template", ""))[:200]} for c in data]}


@router.get("/tools")
async def tools():
    try:
        ids = await _get("/experimental/tool/ids")
    except HTTPException:
        ids = []
    return {"data": ids if isinstance(ids, list) else []}


@router.get("/todos")
async def todos():
    data = await _get(f"/session/{_sid()}/todo")
    return {"data": data if isinstance(data, list) else []}


# ---------------------------------------------------------------- questions / permissions

@router.get("/questions")
async def questions():
    data = _unwrap(await _get("/api/question/request"))
    return {"data": data}


class Answer(BaseModel):
    answers: Optional[list] = None
    answer: Optional[str] = None


@router.post("/questions/{qid}/reply")
async def question_reply(qid: str, body: Answer):
    answers = body.answers
    if answers is None:
        answers = [[body.answer or ""]]
    await _send("POST", f"/api/session/{_sid()}/question/{qid}/reply",
                json_body={"answers": answers})
    return {"ok": True}


@router.post("/questions/{qid}/reject")
async def question_reject(qid: str):
    await _send("POST", f"/api/session/{_sid()}/question/{qid}/reject")
    return {"ok": True}


@router.get("/permissions")
async def permissions():
    data = _unwrap(await _get("/api/permission/request"))
    return {"data": data}


class PermissionReply(BaseModel):
    approved: bool = True
    session_id: Optional[str] = None


@router.post("/permissions/{pid}/reply")
async def permission_reply(pid: str, body: PermissionReply):
    sid = body.session_id or _sid()
    await _send("POST", f"/api/session/{sid}/permission/{pid}/reply",
                json_body={"reply": "once" if body.approved else "reject"})
    return {"ok": True}


# ---------------------------------------------------------------- actions session

@router.post("/compact")
async def compact():
    await _send("POST", f"/session/{_sid()}/summarize",
                json_body={"providerID": "opencode", "modelID": os.environ.get("ALEX_OPENCODE_MODEL", "mimo-v2.6-flash-free")})
    return {"ok": True}


@router.post("/interrupt")
async def interrupt():
    await _send("POST", f"/api/session/{_sid()}/interrupt")
    return {"ok": True}


# ---------------------------------------------------------------- fichiers / VCS

@router.get("/fs")
async def fs_list(directory: str = os.path.expanduser("~")):
    data = _unwrap(await _get("/api/fs/list", params={"directory": directory}))
    return {"data": data, "directory": directory}


@router.get("/file")
async def file_read(path: str):
    data = await _get("/api/fs/read", params={"path": path})
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict) and "content" in data:
        data = data["content"]
    return {"path": path, "content": data if isinstance(data, str) else str(data)}


@router.get("/vcs")
async def vcs_status(directory: str = os.path.expanduser("~/Documents/alex")):
    try:
        st = await _get("/vcs/status", params={"directory": directory})
        if isinstance(st, dict) and "data" in st:
            st = st["data"]
        return {"data": st}
    except HTTPException:
        return {"data": []}


# ---------------------------------------------------------------- commandes / messages / session info

class CommandRun(BaseModel):
    command: str
    arguments: Optional[str] = None


@router.post("/command")
async def run_command(body: CommandRun):
    payload = {"command": body.command}
    if body.arguments:
        payload["arguments"] = body.arguments
    data = _unwrap(await _send("POST", f"/session/{_sid()}/command", json_body=payload))
    info = (data or {}).get("info", {})
    text = info.get("text") or ""
    if not text:
        parts = (data or {}).get("parts") or []
        text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text"))
    return {"text": text, "info": info}


class SessionRename(BaseModel):
    title: str


@router.patch("/sessions/{sid}")
async def rename_session(sid: str, body: SessionRename):
    data = _unwrap(await _send("PATCH", f"/session/{sid}", json_body={"title": body.title}))
    return {"id": (data or {}).get("id", sid), "title": (data or {}).get("title", body.title)}


@router.delete("/sessions/{sid}/messages/{mid}")
async def delete_message(sid: str, mid: str):
    await _send("DELETE", f"/session/{sid}/message/{mid}")
    return {"ok": True}


@router.get("/session-info")
async def session_info():
    data = _unwrap(await _get(f"/session/{_sid()}"))
    return {
        "id": (data or {}).get("id"),
        "title": (data or {}).get("title"),
        "cost": (data or {}).get("cost", 0),
        "tokens": (data or {}).get("tokens", {}),
        "summary": (data or {}).get("summary", {}),
    }


# ---------------------------------------------------------------- diff brut (texte)

@router.get("/vcs/raw")
async def vcs_raw(directory: str = os.path.expanduser("~/Documents/alex")):
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{OC}/vcs/diff/raw", params={"directory": directory})
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text[:300])
        return Response(content=r.text, media_type="text/plain; charset=utf-8")
