"""HTTP API for the hub, mounted under /api/hub by the local server.

Every response is JSON. Input is validated; text is stored as plain text and
never as HTML. Write requests must carry the header X-DailyHub: 1 (checked by
the server), which blocks cross-site form posts.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone

from . import pipeline, scheduler, store
from .normalize import clean_text, safe_url
from .sources import SourceError

MEMORY_KINDS = {"fact", "preference", "process", "decision", "lesson", "note"}
CONFIDENCE = {"low", "medium", "high"}
RESOURCE_KINDS = {"repo", "skill", "toolkit", "article"}
SORTS = {
    "score": "COALESCE(r.score, -1) DESC, r.last_seen DESC",
    "updated": "r.last_updated DESC, r.last_seen DESC",
    "stars": "CAST(json_extract(r.metrics, '$.stars') AS INTEGER) DESC",
    "new": "r.first_seen DESC",
    "name": "r.name COLLATE NOCASE ASC",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now_ms() -> int:
    return store.now_ms()


def _json_list(value, max_items: int = 30, max_len: int = 60) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ApiError(400, "trường danh sách phải là mảng")
    out = []
    for item in value[:max_items]:
        text = clean_text(item, max_len)
        if text and text not in out:
            out.append(text)
    return out


def _body(body: bytes) -> dict:
    if not body:
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ApiError(400, "JSON không hợp lệ") from err
    if not isinstance(data, dict):
        raise ApiError(400, "body phải là object JSON")
    return data


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ---------- Resources ----------
def _resource_out(row: dict) -> dict:
    metrics = json.loads(row.get("metrics") or "{}")
    return {
        "id": row["id"], "kind": row["kind"], "name": row["name"], "url": row["url"],
        "author": row["author"], "description": row["description"], "topics": json.loads(row["topics"] or "[]"),
        "status": row["status"], "license": row["license"], "last_updated": row["last_updated"],
        "first_seen": row["first_seen"], "last_seen": row["last_seen"],
        "metrics": metrics, "metrics_at": row["metrics_at"], "score": row["score"],
        "score_breakdown": json.loads(row["score_breakdown"] or "{}"),
        "verified": {"github_metrics": row["metrics_at"] is not None},
        "saved": bool(row.get("saved") or 0), "seen": bool(row.get("seen") or 0),
        "rating": row.get("rating"), "note": row.get("note") or "",
    }


def list_resources(q: dict) -> dict:
    where, args = ["1=1"], []
    kind = q.get("kind", [""])[0]
    if kind:
        if kind not in RESOURCE_KINDS:
            raise ApiError(400, "kind không hợp lệ")
        where.append("r.kind = ?"); args.append(kind)
    status = q.get("status", ["active"])[0]
    if status != "all":
        where.append("r.status = ?"); args.append(status)
    term = clean_text(q.get("q", [""])[0], 100)
    if term:
        where.append("(r.name LIKE ? ESCAPE '\\' OR r.description LIKE ? ESCAPE '\\' OR r.author LIKE ? ESCAPE '\\' OR r.topics LIKE ? ESCAPE '\\')")
        args += [_like(term)] * 4
    author = clean_text(q.get("author", [""])[0], 80)
    if author:
        where.append("r.author = ?"); args.append(author)
    if q.get("saved", [""])[0] == "1":
        where.append("COALESCE(s.saved,0) = 1")
    if q.get("unseen", [""])[0] == "1":
        where.append("COALESCE(s.seen,0) = 0")
    min_score = q.get("min_score", [""])[0]
    if min_score.isdigit():
        where.append("r.score >= ?"); args.append(int(min_score))
    sort = q.get("sort", ["score"])[0]
    order = SORTS.get(sort)
    if order is None:
        raise ApiError(400, "sort không hợp lệ")
    limit = min(max(int(q.get("limit", ["50"])[0] or 50), 1), 200)
    offset = max(int(q.get("offset", ["0"])[0] or 0), 0)
    base = "FROM resources r LEFT JOIN resource_state s ON s.resource_id = r.id WHERE " + " AND ".join(where)
    total = store.query_one(f"SELECT COUNT(*) AS n {base}", tuple(args))["n"]
    rows = store.query(f"SELECT r.*, s.saved, s.seen, s.rating, s.note {base} ORDER BY {order} LIMIT ? OFFSET ?",
                       tuple(args) + (limit, offset))
    authors = [r["author"] for r in store.query("SELECT DISTINCT author FROM resources WHERE author != '' ORDER BY author LIMIT 200")]
    return {"total": total, "items": [_resource_out(r) for r in rows], "authors": authors}


def get_resource(rid: str) -> dict:
    row = store.query_one("""SELECT r.*, s.saved, s.seen, s.rating, s.note FROM resources r
                             LEFT JOIN resource_state s ON s.resource_id = r.id WHERE r.id=?""", (rid,))
    if not row:
        raise ApiError(404, "không tìm thấy tài nguyên")
    return _resource_out(row)


def patch_resource_state(rid: str, data: dict) -> dict:
    if not store.query_one("SELECT 1 FROM resources WHERE id=?", (rid,)):
        raise ApiError(404, "không tìm thấy tài nguyên")
    cur = store.query_one("SELECT * FROM resource_state WHERE resource_id=?", (rid,)) or {
        "saved": 0, "seen": 0, "rating": None, "note": ""}
    if "saved" in data:
        cur["saved"] = 1 if data["saved"] else 0
    if "seen" in data:
        cur["seen"] = 1 if data["seen"] else 0
    if "rating" in data:
        if data["rating"] is None:
            cur["rating"] = None
        elif isinstance(data["rating"], int) and 1 <= data["rating"] <= 5:
            cur["rating"] = data["rating"]
        else:
            raise ApiError(400, "rating phải là số nguyên 1-5 hoặc null")
    if "note" in data:
        if not isinstance(data["note"], str):
            raise ApiError(400, "note phải là chuỗi")
        cur["note"] = data["note"][:2000]
    with store.tx() as c:
        c.execute("""INSERT INTO resource_state(resource_id, saved, seen, rating, note, updated) VALUES(?,?,?,?,?,?)
                     ON CONFLICT(resource_id) DO UPDATE SET saved=excluded.saved, seen=excluded.seen,
                     rating=excluded.rating, note=excluded.note, updated=excluded.updated""",
                  (rid, cur["saved"], cur["seen"], cur["rating"], cur["note"], _now_ms()))
    return get_resource(rid)


# ---------- Sources ----------
def list_sources() -> dict:
    rows = store.query("SELECT * FROM sources ORDER BY type, name")
    return {"items": [dict(r, enabled=bool(r["enabled"])) for r in rows]}


def create_source(data: dict) -> dict:
    typ = data.get("type")
    name = clean_text(data.get("name", ""), 80)
    if typ == "rss":
        url = safe_url(data.get("url", ""))
        if not url:
            raise ApiError(400, "RSS cần URL http/https hợp lệ")
    elif typ == "github_owner":
        login = clean_text(data.get("login", ""), 39)
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", login or ""):
            raise ApiError(400, "tên tài khoản GitHub không hợp lệ")
        url, name = f"owner:{login}", name or f"GitHub: {login}"
    else:
        raise ApiError(400, "type phải là 'rss' hoặc 'github_owner'")
    if store.query_one("SELECT 1 FROM sources WHERE url=?", (url,)):
        raise ApiError(409, "nguồn này đã tồn tại")
    sid = store.new_id()
    with store.tx() as c:
        c.execute("INSERT INTO sources(id, name, type, url, enabled) VALUES(?,?,?,?,1)", (sid, name or url, typ, url))
    return dict(store.query_one("SELECT * FROM sources WHERE id=?", (sid,)), enabled=True)


def patch_source(sid: str, data: dict) -> dict:
    row = store.query_one("SELECT * FROM sources WHERE id=?", (sid,))
    if not row:
        raise ApiError(404, "không tìm thấy nguồn")
    enabled = bool(data["enabled"]) if "enabled" in data else bool(row["enabled"])
    name = clean_text(data["name"], 80) if "name" in data and data["name"] else row["name"]
    with store.tx() as c:
        c.execute("UPDATE sources SET enabled=?, name=? WHERE id=?", (1 if enabled else 0, name, sid))
    return dict(store.query_one("SELECT * FROM sources WHERE id=?", (sid,)), enabled=enabled)


def delete_source(sid: str) -> dict:
    with store.tx() as c:
        cur = c.execute("DELETE FROM sources WHERE id=?", (sid,))
    if cur.rowcount == 0:
        raise ApiError(404, "không tìm thấy nguồn")
    return {"deleted": sid}


# ---------- Status, sync, events ----------
def status() -> dict:
    last_run = store.query_one("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    if last_run:
        last_run["stats"] = json.loads(last_run["stats"] or "{}")
    counts = store.query_one("""SELECT
        (SELECT COUNT(*) FROM resources) AS resources,
        (SELECT COUNT(*) FROM resources WHERE kind='skill') AS skills,
        (SELECT COUNT(*) FROM resources WHERE kind='toolkit') AS toolkit,
        (SELECT COUNT(*) FROM resources WHERE kind='repo') AS repos,
        (SELECT COUNT(*) FROM resources WHERE kind='article') AS articles,
        (SELECT COUNT(*) FROM resource_state WHERE saved=1) AS saved,
        (SELECT COUNT(*) FROM memories) AS memories,
        (SELECT COUNT(*) FROM prompts) AS prompts""")
    sources = store.query("SELECT id, name, type, enabled, last_ok, last_error, ok_count, fail_count, last_count FROM sources WHERE enabled=1 ORDER BY name")
    return {
        "scheduler": scheduler.current().status() if scheduler.current() else {"running": False},
        "sync_running": pipeline._run_lock.locked(),
        "github_token_configured": bool(pipeline.github_token()),
        "last_run": last_run, "counts": counts, "sources": sources,
        "events": events(limit=12)["items"],
    }


def events(limit: int = 50) -> dict:
    rows = store.query("""SELECT e.id, e.type, e.detail, e.created, r.name AS resource_name, r.url AS resource_url
                          FROM events e LEFT JOIN resources r ON r.id = e.resource_id ORDER BY e.id DESC LIMIT ?""",
                       (min(max(limit, 1), 200),))
    return {"items": rows}


def run_now() -> dict:
    if pipeline._run_lock.locked():
        raise ApiError(409, "đang có lần đồng bộ chạy")
    threading.Thread(target=pipeline.run_sync, args=("manual",), daemon=True, name="dailyhub-manual-sync").start()
    return {"status": "started"}


# ---------- Prompts ----------
def _prompt_out(row: dict, with_versions: bool = False) -> dict:
    out = {"id": row["id"], "title": row["title"], "goal": row["goal"], "body": row["body"],
           "variables": json.loads(row["variables"] or "[]"), "models": json.loads(row["models"] or "[]"),
           "version": row["version"], "tags": json.loads(row["tags"] or "[]"),
           "result_note": row["result_note"], "rating": row["rating"],
           "created": row["created"], "updated": row["updated"]}
    if with_versions:
        out["versions"] = store.query("SELECT version, body, created FROM prompt_versions WHERE prompt_id=? ORDER BY version DESC", (row["id"],))
    return out


def _prompt_fields(data: dict, partial: bool) -> dict:
    out = {}
    if not partial or "title" in data:
        title = clean_text(data.get("title", ""), 120)
        if not title:
            raise ApiError(400, "prompt cần tiêu đề")
        out["title"] = title
    if not partial or "body" in data:
        body = data.get("body", "")
        if not isinstance(body, str) or not body.strip():
            raise ApiError(400, "prompt cần nội dung")
        out["body"] = body[:20000]
    if "goal" in data:
        out["goal"] = clean_text(data["goal"], 300)
    for key, limit in (("variables", 40), ("models", 20), ("tags", 30)):
        if key in data:
            out[key] = _json_list(data[key], limit)
    if "result_note" in data:
        out["result_note"] = str(data["result_note"] or "")[:2000]
    if "rating" in data:
        r = data["rating"]
        if r is not None and not (isinstance(r, int) and 1 <= r <= 5):
            raise ApiError(400, "rating phải là 1-5 hoặc null")
        out["rating"] = r
    return out


def list_prompts(q: dict) -> dict:
    where, args = ["1=1"], []
    term = clean_text(q.get("q", [""])[0], 100)
    if term:
        where.append("(title LIKE ? ESCAPE '\\' OR goal LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\')")
        args += [_like(term)] * 4
    tag = clean_text(q.get("tag", [""])[0], 30)
    if tag:
        where.append("tags LIKE ? ESCAPE '\\'"); args.append('%"' + tag.replace("%", "").replace("_", "") + '"%')
    rows = store.query(f"SELECT * FROM prompts WHERE {' AND '.join(where)} ORDER BY updated DESC LIMIT 300", tuple(args))
    return {"items": [_prompt_out(r) for r in rows]}


def create_prompt(data: dict) -> dict:
    fields = _prompt_fields(data, partial=False)
    pid, now = store.new_id(), _now_ms()
    with store.tx() as c:
        c.execute("""INSERT INTO prompts(id, title, goal, body, variables, models, version, tags, result_note, rating, created, updated)
                     VALUES(?,?,?,?,?,?,1,?,?,?,?,?)""",
                  (pid, fields["title"], fields.get("goal", ""), fields["body"],
                   json.dumps(fields.get("variables", []), ensure_ascii=False), json.dumps(fields.get("models", []), ensure_ascii=False),
                   json.dumps(fields.get("tags", []), ensure_ascii=False), fields.get("result_note", ""), fields.get("rating"), now, now))
        c.execute("INSERT INTO prompt_versions(prompt_id, version, body, created) VALUES(?,?,?,?)", (pid, 1, fields["body"], now))
    return get_prompt(pid)


def get_prompt(pid: str) -> dict:
    row = store.query_one("SELECT * FROM prompts WHERE id=?", (pid,))
    if not row:
        raise ApiError(404, "không tìm thấy prompt")
    return _prompt_out(row, with_versions=True)


def update_prompt(pid: str, data: dict) -> dict:
    row = store.query_one("SELECT * FROM prompts WHERE id=?", (pid,))
    if not row:
        raise ApiError(404, "không tìm thấy prompt")
    fields = _prompt_fields(data, partial=True)
    now = _now_ms()
    with store.tx() as c:
        version = row["version"]
        if "body" in fields and fields["body"] != row["body"]:
            c.execute("INSERT INTO prompt_versions(prompt_id, version, body, created) VALUES(?,?,?,?)", (pid, version, row["body"], now))
            version += 1
        sets, args = [], []
        for key in ("title", "goal", "body", "result_note", "rating"):
            if key in fields:
                sets.append(f"{key}=?"); args.append(fields[key])
        for key in ("variables", "models", "tags"):
            if key in fields:
                sets.append(f"{key}=?"); args.append(json.dumps(fields[key], ensure_ascii=False))
        sets += ["version=?", "updated=?"]; args += [version, now, pid]
        c.execute(f"UPDATE prompts SET {', '.join(sets)} WHERE id=?", tuple(args))
    return get_prompt(pid)


def delete_prompt(pid: str) -> dict:
    with store.tx() as c:
        cur = c.execute("DELETE FROM prompts WHERE id=?", (pid,))
    if cur.rowcount == 0:
        raise ApiError(404, "không tìm thấy prompt")
    return {"deleted": pid}


# ---------- Memories ----------
def _memory_out(row: dict) -> dict:
    return {"id": row["id"], "kind": row["kind"], "title": row["title"], "body": row["body"],
            "tags": json.loads(row["tags"] or "[]"), "source": row["source"], "confidence": row["confidence"],
            "valid_from": row["valid_from"], "review_at": row["review_at"], "pinned": bool(row["pinned"]),
            "stale": bool(row["stale"]), "created": row["created"], "updated": row["updated"]}


def _date(value, field: str) -> str:
    if value in (None, ""):
        return ""
    text = str(value)[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as err:
        raise ApiError(400, f"{field} phải có dạng YYYY-MM-DD") from err
    return text


def _memory_fields(data: dict, partial: bool) -> dict:
    out = {}
    if not partial or "title" in data:
        title = clean_text(data.get("title", ""), 160)
        if not title:
            raise ApiError(400, "memory cần tiêu đề")
        out["title"] = title
    if not partial or "kind" in data:
        kind = data.get("kind", "note")
        if kind not in MEMORY_KINDS:
            raise ApiError(400, "kind không hợp lệ")
        out["kind"] = kind
    if "body" in data:
        out["body"] = str(data["body"] or "")[:10000]
    if "tags" in data:
        out["tags"] = _json_list(data["tags"], 30, 40)
    if "source" in data:
        out["source"] = clean_text(data["source"], 300)
    if "confidence" in data:
        if data["confidence"] not in CONFIDENCE:
            raise ApiError(400, "confidence phải là low, medium hoặc high")
        out["confidence"] = data["confidence"]
    if "valid_from" in data:
        out["valid_from"] = _date(data["valid_from"], "valid_from")
    if "review_at" in data:
        out["review_at"] = _date(data["review_at"], "review_at")
    for flag in ("pinned", "stale"):
        if flag in data:
            out[flag] = 1 if data[flag] else 0
    return out


def list_memories(q: dict) -> dict:
    where, args = ["1=1"], []
    term = clean_text(q.get("q", [""])[0], 100)
    if term:
        where.append("(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\')")
        args += [_like(term)] * 4
    kind = q.get("kind", [""])[0]
    if kind:
        if kind not in MEMORY_KINDS:
            raise ApiError(400, "kind không hợp lệ")
        where.append("kind=?"); args.append(kind)
    tag = clean_text(q.get("tag", [""])[0], 40)
    if tag:
        where.append("tags LIKE ? ESCAPE '\\'"); args.append('%"' + tag.replace("%", "").replace("_", "") + '"%')
    if q.get("stale", [""])[0] == "1":
        where.append("stale=1")
    if q.get("pinned", [""])[0] == "1":
        where.append("pinned=1")
    rows = store.query(f"SELECT * FROM memories WHERE {' AND '.join(where)} ORDER BY pinned DESC, updated DESC LIMIT 500", tuple(args))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    items = [_memory_out(r) for r in rows]
    for m in items:
        m["review_due"] = bool(m["review_at"] and m["review_at"] <= today)
    return {"items": items}


def create_memory(data: dict) -> dict:
    f = _memory_fields(data, partial=False)
    mid, now = store.new_id(), _now_ms()
    with store.tx() as c:
        c.execute("""INSERT INTO memories(id, kind, title, body, tags, source, confidence, valid_from, review_at, pinned, stale, created, updated)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (mid, f["kind"], f["title"], f.get("body", ""), json.dumps(f.get("tags", []), ensure_ascii=False),
                   f.get("source", ""), f.get("confidence", "medium"), f.get("valid_from", ""), f.get("review_at", ""),
                   f.get("pinned", 0), f.get("stale", 0), now, now))
    return _memory_out(store.query_one("SELECT * FROM memories WHERE id=?", (mid,)))


def update_memory(mid: str, data: dict) -> dict:
    row = store.query_one("SELECT * FROM memories WHERE id=?", (mid,))
    if not row:
        raise ApiError(404, "không tìm thấy memory")
    f = _memory_fields(data, partial=True)
    if not f:
        return _memory_out(row)
    sets = [f"{k}=?" for k in f]
    args = [json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v for v in f.values()]
    with store.tx() as c:
        c.execute(f"UPDATE memories SET {', '.join(sets)}, updated=? WHERE id=?", tuple(args) + (_now_ms(), mid))
    return _memory_out(store.query_one("SELECT * FROM memories WHERE id=?", (mid,)))


def delete_memory(mid: str) -> dict:
    with store.tx() as c:
        cur = c.execute("DELETE FROM memories WHERE id=?", (mid,))
    if cur.rowcount == 0:
        raise ApiError(404, "không tìm thấy memory")
    return {"deleted": mid}


def _norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", t.strip().lower())


def memory_duplicates() -> dict:
    rows = store.query("SELECT * FROM memories ORDER BY created")
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(_norm_title(r["title"]), []).append(_memory_out(r))
    return {"groups": [g for g in groups.values() if len(g) > 1]}


def merge_memories(data: dict) -> dict:
    """Merge duplicates into one memory. Needs confirm=true (the UI asks the user first)."""
    if data.get("confirm") is not True:
        raise ApiError(400, "cần xác nhận (confirm: true) trước khi gộp")
    keep, remove = data.get("keep"), data.get("remove")
    if not isinstance(keep, str) or not isinstance(remove, list) or not remove or keep in remove:
        raise ApiError(400, "keep và remove không hợp lệ")
    with store.tx() as c:
        target = c.execute("SELECT * FROM memories WHERE id=?", (keep,)).fetchone()
        if not target:
            raise ApiError(404, "không tìm thấy memory giữ lại")
        body, tags = target["body"], json.loads(target["tags"] or "[]")
        for rid in remove:
            r = c.execute("SELECT * FROM memories WHERE id=?", (str(rid),)).fetchone()
            if not r:
                continue
            if r["body"].strip():
                body += f"\n\n---\nGộp từ bản trùng ({r['created']}):\n{r['body']}"
            for t in json.loads(r["tags"] or "[]"):
                if t not in tags:
                    tags.append(t)
            c.execute("DELETE FROM memories WHERE id=?", (str(rid),))
        c.execute("UPDATE memories SET body=?, tags=?, updated=? WHERE id=?",
                  (body[:10000], json.dumps(tags[:30], ensure_ascii=False), _now_ms(), keep))
    return _memory_out(store.query_one("SELECT * FROM memories WHERE id=?", (keep,)))


# ---------- Markdown / JSON import ----------
def parse_markdown_items(text: str, kind: str) -> list[dict]:
    """Split by '## Heading'. Each section becomes one memory or prompt. Plain text only."""
    text = text[:300_000]
    items, current = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            if current:
                items.append(current)
            current = {"title": clean_text(line[3:], 160), "lines": []}
        elif current is not None:
            current["lines"].append(line)
    if current:
        items.append(current)
    out = []
    for it in items[:500]:
        body = "\n".join(it["lines"]).strip()
        if not it["title"]:
            continue
        if kind == "prompt":
            if body:
                out.append({"title": it["title"], "body": body[:20000]})
        else:
            out.append({"title": it["title"], "body": body[:10000], "kind": "note"})
    return out


def import_markdown(data: dict) -> dict:
    kind = data.get("kind")
    text = data.get("text")
    if kind not in ("memory", "prompt") or not isinstance(text, str):
        raise ApiError(400, "cần kind (memory|prompt) và text")
    items = parse_markdown_items(text, "prompt" if kind == "prompt" else "memory")
    created = 0
    for it in items:
        if kind == "prompt":
            create_prompt({"title": it["title"], "body": it["body"]})
        else:
            create_memory({"title": it["title"], "body": it["body"], "kind": "note", "source": "Nhập từ Markdown"})
        created += 1
    return {"created": created}


# ---------- Export / import bundle ----------
def export_bundle() -> dict:
    memories = [_memory_out(r) for r in store.query("SELECT * FROM memories ORDER BY created")]
    prompts = [get_prompt(r["id"]) for r in store.query("SELECT id FROM prompts ORDER BY created")]
    states = store.query("""SELECT r.url, s.saved, s.seen, s.rating, s.note FROM resource_state s
                            JOIN resources r ON r.id = s.resource_id""")
    sources = store.query("SELECT name, type, url, enabled FROM sources WHERE type IN ('rss','github_owner')")
    return {"app": "DailyHub", "bundle_version": 1, "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "memories": memories, "prompts": prompts, "resource_states": states, "sources": sources,
            "settings": pipeline.get_settings()}


def import_bundle(data: dict) -> dict:
    if data.get("app") != "DailyHub" or data.get("bundle_version") != 1:
        raise ApiError(400, "tệp không phải bản sao lưu DailyHub phiên bản 1")
    for key in ("memories", "prompts", "resource_states", "sources"):
        if not isinstance(data.get(key, []), list):
            raise ApiError(400, f"trường {key} phải là mảng")
    if len(json.dumps(data, ensure_ascii=False)) > 20_000_000:
        raise ApiError(413, "bản sao lưu quá lớn")
    summary = {"memories_added": 0, "memories_skipped": 0, "prompts_added": 0, "prompts_skipped": 0,
               "states_applied": 0, "sources_added": 0}
    with store.tx() as c:
        for m in data.get("memories", [])[:20000]:
            if not isinstance(m, dict) or not clean_text(m.get("title", ""), 160):
                continue
            mid = str(m.get("id") or store.new_id())[:40]
            if c.execute("SELECT 1 FROM memories WHERE id=?", (mid,)).fetchone():
                summary["memories_skipped"] += 1
                continue
            f = _memory_fields(dict(m, kind=m.get("kind") if m.get("kind") in MEMORY_KINDS else "note",
                                    confidence=m.get("confidence") if m.get("confidence") in CONFIDENCE else "medium",
                                    valid_from=m.get("valid_from") or "", review_at=m.get("review_at") or ""), partial=False)
            c.execute("""INSERT INTO memories(id, kind, title, body, tags, source, confidence, valid_from, review_at, pinned, stale, created, updated)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (mid, f["kind"], f["title"], f.get("body", ""), json.dumps(f.get("tags", []), ensure_ascii=False),
                       f.get("source", ""), f["confidence"] if "confidence" in f else "medium", f.get("valid_from", ""),
                       f.get("review_at", ""), f.get("pinned", 0), f.get("stale", 0),
                       int(m.get("created") or _now_ms()), int(m.get("updated") or _now_ms())))
            summary["memories_added"] += 1
        for p in data.get("prompts", [])[:5000]:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or store.new_id())[:40]
            if c.execute("SELECT 1 FROM prompts WHERE id=?", (pid,)).fetchone():
                summary["prompts_skipped"] += 1
                continue
            try:
                f = _prompt_fields(p, partial=False)
            except ApiError:
                continue
            now = _now_ms()
            c.execute("""INSERT INTO prompts(id, title, goal, body, variables, models, version, tags, result_note, rating, created, updated)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (pid, f["title"], f.get("goal", ""), f["body"], json.dumps(f.get("variables", []), ensure_ascii=False),
                       json.dumps(f.get("models", []), ensure_ascii=False), 1, json.dumps(f.get("tags", []), ensure_ascii=False),
                       f.get("result_note", ""), f.get("rating"), now, now))
            c.execute("INSERT INTO prompt_versions(prompt_id, version, body, created) VALUES(?,?,?,?)", (pid, 1, f["body"], now))
            summary["prompts_added"] += 1
        for st in data.get("resource_states", [])[:50000]:
            if not isinstance(st, dict):
                continue
            row = c.execute("SELECT id FROM resources WHERE url=?", (str(st.get("url", ""))[:500],)).fetchone()
            if not row:
                continue
            c.execute("""INSERT INTO resource_state(resource_id, saved, seen, rating, note, updated) VALUES(?,?,?,?,?,?)
                         ON CONFLICT(resource_id) DO UPDATE SET saved=excluded.saved, seen=excluded.seen,
                         rating=excluded.rating, note=excluded.note, updated=excluded.updated""",
                      (row["id"], 1 if st.get("saved") else 0, 1 if st.get("seen") else 0,
                       st.get("rating") if st.get("rating") in (1, 2, 3, 4, 5) else None,
                       str(st.get("note") or "")[:2000], _now_ms()))
            summary["states_applied"] += 1
        for s in data.get("sources", [])[:200]:
            if not isinstance(s, dict) or s.get("type") not in ("rss", "github_owner"):
                continue
            url = str(s.get("url", ""))[:500]
            if s["type"] == "rss" and not safe_url(url):
                continue
            if c.execute("SELECT 1 FROM sources WHERE url=?", (url,)).fetchone():
                continue
            c.execute("INSERT INTO sources(id, name, type, url, enabled) VALUES(?,?,?,?,?)",
                      (store.new_id(), clean_text(s.get("name", url), 80) or url, s["type"], url, 1 if s.get("enabled", True) else 0))
            summary["sources_added"] += 1
    if isinstance(data.get("settings"), dict):
        allowed = {k: data["settings"][k] for k in ("schedule", "github", "retry", "max_requests_per_run", "weather", "backup") if k in data["settings"]}
        validate_settings(allowed)
        pipeline.save_settings(allowed)
    return summary


# ---------- Settings & data management ----------
def validate_settings(new: dict) -> None:
    b = new.get("backup")
    if b is not None:
        if not isinstance(b, dict):
            raise ApiError(400, "backup phải là object")
        if "enabled" in b and not isinstance(b["enabled"], bool):
            raise ApiError(400, "backup.enabled phải là true/false")
        if "keep" in b and not (isinstance(b["keep"], int) and 1 <= b["keep"] <= 60):
            raise ApiError(400, "backup.keep phải là số nguyên trong [1, 60]")
    w = new.get("weather")
    if w is not None:
        if not isinstance(w, dict):
            raise ApiError(400, "weather phải là object")
        try:
            lat, lon = float(w.get("latitude")), float(w.get("longitude"))
        except (TypeError, ValueError) as err:
            raise ApiError(400, "vĩ độ/kinh độ phải là số") from err
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ApiError(400, "vĩ độ phải trong [-90, 90] và kinh độ trong [-180, 180]")
        if "timezone" in w and not scheduler.is_valid_timezone(str(w["timezone"])):
            raise ApiError(400, "múi giờ thời tiết không hợp lệ")
        if "name" in w:
            w["name"] = clean_text(w["name"], 60) or "Vị trí"
    sch = new.get("schedule", {})
    if "time" in sch and not scheduler.TIME_RE.match(str(sch["time"])):
        raise ApiError(400, "giờ chạy phải có dạng HH:MM")
    if "timezone" in sch and not scheduler.is_valid_timezone(str(sch["timezone"])):
        raise ApiError(400, "múi giờ không hợp lệ (ví dụ Asia/Ho_Chi_Minh)")
    if "enabled" in sch and not isinstance(sch["enabled"], bool):
        raise ApiError(400, "schedule.enabled phải là true/false")
    gh = new.get("github", {})
    if "topics" in gh:
        topics = _json_list(gh["topics"], 30, 40)
        if any(not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", t) for t in topics):
            raise ApiError(400, "topic GitHub chỉ gồm chữ thường, số và dấu gạch ngang")
        gh["topics"] = topics
    for key, lo, hi in (("per_topic_limit", 1, 100), ("release_checks", 0, 100), ("refresh_limit", 0, 200),
                        ("owner_limit", 1, 100), ("lookback_days", 7, 3650)):
        if key in gh and not (isinstance(gh[key], int) and lo <= gh[key] <= hi):
            raise ApiError(400, f"{key} phải là số nguyên trong [{lo}, {hi}]")
    if "max_requests_per_run" in new and not (isinstance(new["max_requests_per_run"], int) and 1 <= new["max_requests_per_run"] <= 1000):
        raise ApiError(400, "max_requests_per_run phải là số nguyên 1-1000")


def get_settings_view() -> dict:
    return dict(pipeline.get_settings(), github_token_configured=bool(pipeline.github_token()))


def put_settings(data: dict) -> dict:
    allowed = {k: data[k] for k in ("schedule", "github", "retry", "max_requests_per_run", "weather", "backup") if k in data}
    validate_settings(allowed)
    pipeline.save_settings(allowed)
    return get_settings_view()


def _snippet(text: str, term: str, width: int = 90) -> str:
    text = text or ""
    i = text.lower().find(term.lower())
    if i < 0:
        return text[:width]
    start = max(0, i - width // 3)
    chunk = text[start:start + width]
    return ("…" if start > 0 else "") + chunk + ("…" if start + width < len(text) else "")


def search(raw_q: str, limit: int = 10) -> dict:
    """Search resources, memories, prompts and resource notes. Plain text matching (LIKE), case-insensitive for ASCII."""
    q = clean_text(raw_q, 100)
    if len(q) < 2:
        raise ApiError(400, "từ khóa cần ít nhất 2 ký tự")
    pat = _like(q)
    lim = min(max(limit, 1), 50)
    resources = store.query(
        """SELECT id, kind, name, url, author, description, score FROM resources
           WHERE name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR author LIKE ? ESCAPE '\\' OR topics LIKE ? ESCAPE '\\'
           ORDER BY (name LIKE ? ESCAPE '\\') DESC, COALESCE(score, -1) DESC LIMIT ?""",
        (pat, pat, pat, pat, pat, lim))
    memories = store.query(
        """SELECT id, kind, title, body, tags, source FROM memories
           WHERE title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\'
           ORDER BY (title LIKE ? ESCAPE '\\') DESC, updated DESC LIMIT ?""",
        (pat, pat, pat, pat, pat, lim))
    prompts = store.query(
        """SELECT id, title, goal, body, tags FROM prompts
           WHERE title LIKE ? ESCAPE '\\' OR goal LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\'
           ORDER BY (title LIKE ? ESCAPE '\\') DESC, updated DESC LIMIT ?""",
        (pat, pat, pat, pat, pat, lim))
    notes = store.query(
        """SELECT r.id AS resource_id, r.name, s.note FROM resource_state s JOIN resources r ON r.id = s.resource_id
           WHERE s.note LIKE ? ESCAPE '\\' ORDER BY s.updated DESC LIMIT ?""", (pat, lim))
    return {
        "query": q,
        "resources": [{"id": r["id"], "kind": r["kind"], "name": r["name"], "url": r["url"], "author": r["author"],
                       "snippet": _snippet(r["description"], q)} for r in resources],
        "memories": [{"id": m["id"], "kind": m["kind"], "title": m["title"],
                      "snippet": _snippet(m["body"] or m["tags"] or m["source"], q)} for m in memories],
        "prompts": [{"id": p["id"], "title": p["title"], "snippet": _snippet(p["goal"] or p["body"], q)} for p in prompts],
        "notes": [{"resource_id": n["resource_id"], "name": n["name"], "snippet": _snippet(n["note"], q)} for n in notes],
        "total": len(resources) + len(memories) + len(prompts) + len(notes),
    }


def get_memory(mid: str) -> dict:
    row = store.query_one("SELECT * FROM memories WHERE id=?", (mid,))
    if not row:
        raise ApiError(404, "không tìm thấy memory")
    return _memory_out(row)


# ---------- Backups ----------
def backups_list() -> dict:
    from . import backup
    return {"items": backup.list_backups(), "settings": pipeline.get_settings()["backup"]}


def backups_create() -> dict:
    from . import backup
    return backup.create_backup("manual")


def backups_read(name: str) -> dict:
    from . import backup
    try:
        return backup.read_backup(name)
    except ValueError as err:
        raise ApiError(400, str(err)) from err
    except FileNotFoundError as err:
        raise ApiError(404, "không tìm thấy bản sao lưu") from err


def backups_delete(name: str) -> dict:
    from . import backup
    try:
        backup.delete_backup(name)
    except ValueError as err:
        raise ApiError(400, str(err)) from err
    except FileNotFoundError as err:
        raise ApiError(404, "không tìm thấy bản sao lưu") from err
    return {"deleted": name}


def weather_view() -> dict:
    from . import weather
    loc = pipeline.get_settings()["weather"]
    try:
        return dict(weather.get_weather(loc), ok=True)
    except SourceError as err:
        return {"ok": False, "error": str(err), "location": loc}


def reset_data(data: dict) -> dict:
    if data.get("confirm") != "XOA":
        raise ApiError(400, "gõ XOA để xác nhận")
    scope = data.get("scope")
    if scope not in ("personal", "all"):
        raise ApiError(400, "scope phải là personal hoặc all")
    with store.tx() as c:
        c.execute("DELETE FROM memories")
        c.execute("DELETE FROM prompt_versions")
        c.execute("DELETE FROM prompts")
        c.execute("DELETE FROM resource_state")
        if scope == "all":
            c.execute("DELETE FROM resources")
            c.execute("DELETE FROM events")
            c.execute("DELETE FROM runs")
            c.execute("DELETE FROM sources")
    return {"reset": scope}


# ---------- Router ----------
ROUTES = []


def route(method: str, pattern: str):
    rx = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", pattern) + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


@route("GET", "/status")
def _r_status(q, b, p): return status()


@route("GET", "/resources")
def _r_resources(q, b, p): return list_resources(q)


@route("GET", "/resources/{rid}")
def _r_resource(q, b, p): return get_resource(p["rid"])


@route("PATCH", "/resources/{rid}/state")
def _r_resource_state(q, b, p): return patch_resource_state(p["rid"], b)


@route("GET", "/events")
def _r_events(q, b, p): return events(int(q.get("limit", ["50"])[0] or 50))


@route("POST", "/sync")
def _r_sync(q, b, p): return (202, run_now())


@route("GET", "/sources")
def _r_sources(q, b, p): return list_sources()


@route("POST", "/sources")
def _r_source_create(q, b, p): return (201, create_source(b))


@route("PATCH", "/sources/{sid}")
def _r_source_patch(q, b, p): return patch_source(p["sid"], b)


@route("DELETE", "/sources/{sid}")
def _r_source_delete(q, b, p): return delete_source(p["sid"])


@route("GET", "/prompts")
def _r_prompts(q, b, p): return list_prompts(q)


@route("POST", "/prompts")
def _r_prompt_create(q, b, p): return (201, create_prompt(b))


@route("GET", "/prompts/{pid}")
def _r_prompt(q, b, p): return get_prompt(p["pid"])


@route("PATCH", "/prompts/{pid}")
def _r_prompt_patch(q, b, p): return update_prompt(p["pid"], b)


@route("DELETE", "/prompts/{pid}")
def _r_prompt_delete(q, b, p): return delete_prompt(p["pid"])


@route("GET", "/memories")
def _r_memories(q, b, p): return list_memories(q)


@route("POST", "/memories")
def _r_memory_create(q, b, p): return (201, create_memory(b))


@route("GET", "/memories/duplicates")
def _r_memory_dups(q, b, p): return memory_duplicates()


@route("POST", "/memories/merge")
def _r_memory_merge(q, b, p): return merge_memories(b)


@route("PATCH", "/memories/{mid}")
def _r_memory_patch(q, b, p): return update_memory(p["mid"], b)


@route("DELETE", "/memories/{mid}")
def _r_memory_delete(q, b, p): return delete_memory(p["mid"])


@route("POST", "/import/markdown")
def _r_import_md(q, b, p): return import_markdown(b)


@route("GET", "/export")
def _r_export(q, b, p): return export_bundle()


@route("POST", "/import")
def _r_import(q, b, p): return import_bundle(b)


@route("GET", "/search")
def _r_search(q, b, p): return search(q.get("q", [""])[0])


@route("GET", "/memories/{mid}")
def _r_memory_get(q, b, p): return get_memory(p["mid"])


@route("GET", "/backups")
def _r_backups(q, b, p): return backups_list()


@route("POST", "/backups")
def _r_backup_create(q, b, p): return (201, backups_create())


@route("GET", "/backups/{name}")
def _r_backup_read(q, b, p): return backups_read(p["name"])


@route("DELETE", "/backups/{name}")
def _r_backup_delete(q, b, p): return backups_delete(p["name"])


@route("GET", "/weather")
def _r_weather(q, b, p): return weather_view()


@route("GET", "/settings")
def _r_settings_get(q, b, p): return get_settings_view()


@route("PUT", "/settings")
def _r_settings_put(q, b, p): return put_settings(b)


@route("POST", "/data/reset")
def _r_reset(q, b, p): return reset_data(b)


def dispatch(method: str, path: str, query: dict, body: bytes):
    """Returns (status, payload). Raises nothing: errors are returned as JSON."""
    sub = path[len("/api/hub"):] or "/"
    for m, rx, fn in ROUTES:
        match = rx.match(sub)
        if match and m == method:
            try:
                payload = fn(query, _body(body) if method in ("POST", "PUT", "PATCH") else {}, match.groupdict())
                if isinstance(payload, tuple):
                    return payload
                return 200, payload
            except ApiError as err:
                return err.status, {"error": err.message}
            except (ValueError, TypeError):
                return 400, {"error": "dữ liệu không hợp lệ"}
    if any(rx.match(sub) for _m, rx, _f in ROUTES):
        return 405, {"error": "phương thức không được hỗ trợ"}
    return 404, {"error": "không tìm thấy API"}
