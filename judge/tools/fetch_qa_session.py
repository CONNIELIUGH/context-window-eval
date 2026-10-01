import json
import re


def _enc(s):
    out = []
    for ch in str(s):
        if re.match(r"[A-Za-z0-9_.~-]", ch):
            out.append(ch)
        else:
            out.append("".join("%{:02X}".format(b) for b in ch.encode("utf-8")))
    return "".join(out)


def process(agent_key: str, session_key: str, api_base_url: str = "https://api.vectara.dev/v2") -> dict:
    agent_key = str(agent_key).strip()
    session_key = str(session_key).strip()
    if not re.match(r"^[A-Za-z0-9_.-]+$", agent_key) or not re.match(r"^[A-Za-z0-9_-]+$", session_key):
        return {"success": False, "error": "agent_key or session_key contains unexpected characters"}
    base = str(api_base_url).rstrip("/")
    events = []
    page_key = ""
    for _ in range(30):
        url = base + "/agents/" + agent_key + "/sessions/" + session_key + "/events?limit=100"
        if page_key:
            url += "&page_key=" + _enc(page_key)
        try:
            resp = tool.web_get(url=url)
        except Exception as e:
            return {"success": False, "error": "request failed: " + str(e)[:300]}
        if not isinstance(resp, dict):
            return {"success": False, "error": "unexpected response from web_get"}
        status = resp.get("status_code")
        body = resp.get("content", "")
        if status != 200:
            return {"success": False, "error": "sessions API returned HTTP " + str(status) + ": " + str(body)[:300]}
        if resp.get("truncated"):
            return {"success": False, "error": "the events response was truncated, so the answer cannot be extracted reliably"}
        try:
            data = json.loads(body)
        except Exception as e:
            return {"success": False, "error": "could not parse the events response: " + str(e)[:200]}
        events.extend(data.get("events", []))
        page_key = (data.get("metadata") or {}).get("page_key") or ""
        if not page_key:
            break
    events.sort(key=lambda e: e.get("created_at", ""))
    question = ""
    for e in events:
        if e.get("type") == "input_message":
            msgs = e.get("messages") or []
            question = " ".join(str(m.get("content", "")) for m in msgs if isinstance(m, dict) and m.get("type") == "text").strip()
            if question:
                break
    answer = ""
    for e in events:
        t = e.get("type")
        if t == "agent_output" and str(e.get("content") or "").strip():
            answer = str(e.get("content")).strip()
        elif t == "structured_output":
            answer = json.dumps(e.get("content"), ensure_ascii=False)
    user_messages = sum(1 for e in events if e.get("type") == "input_message")
    if not question:
        return {"success": False, "error": "no user question found in that session", "events_seen": len(events)}
    return {"success": True, "agent_key": agent_key, "session_key": session_key, "question": question,
            "answer": answer, "answer_found": bool(answer), "user_messages_in_session": user_messages}
