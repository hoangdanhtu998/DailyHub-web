"""Daily sync pipeline: fetch enabled sources, normalize, dedupe, score, store.

Guarantees:
- Resource data from sources is updated; user state (saved, seen, rating, note)
  lives in its own table and is never overwritten here.
- One run at a time. Each source is isolated: one failure does not stop the others.
- Source errors are recorded per source (ok/fail counts, last error without secrets).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from . import store
from .normalize import classify
from .scoring import score_repo
from .sources import (BudgetExhausted, GitHub, RateLimited, SourceError, fetch_feed,
                      fetch_github_owner, fetch_github_topic, normalize_repo)
from .sources import canonical_url  # re-exported for convenience

DEFAULT_SETTINGS = {
    "schedule": {"enabled": True, "time": "07:00", "timezone": "Asia/Ho_Chi_Minh", "ai_enabled": True},
    "github": {
        "topics": ["ai-agents", "llm", "mcp", "agent-skills", "rag", "local-llm", "context-engineering"],
        "topic_kinds": {},
        "lookback_days": 365,
        "per_topic_limit": 20,
        "release_checks": 15,
        "refresh_limit": 20,
        "owner_limit": 30,
    },
    "retry": {"retries": 2, "backoff_seconds": 2.0},
    "max_requests_per_run": 50,
    "backup": {"enabled": True, "keep": 14},
    "weather": {"name": "Hà Nội", "latitude": 21.0285, "longitude": 105.8542, "timezone": "Asia/Ho_Chi_Minh"},
}
DEFAULT_TOKEN_BUDGET = 400  # used when GITHUB_TOKEN is set (5000 req/h authenticated)

_run_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact(message: str, token: str) -> str:
    text = str(message)
    if token:
        text = text.replace(token, "***")
    return text[:200]


def get_settings() -> dict:
    raw = store.get_meta("settings")
    stored = json.loads(raw) if raw else {}
    return _merge(DEFAULT_SETTINGS, stored)


def _merge(base: dict, override: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def save_settings(new: dict) -> dict:
    merged = _merge(get_settings(), new)
    store.set_meta("settings", json.dumps(merged, ensure_ascii=False))
    ensure_default_sources()
    return merged


def github_token() -> str:
    """Read only from the environment. Never stored in the DB, never returned by the API."""
    return os.environ.get("GITHUB_TOKEN", "").strip()


def ensure_default_sources() -> None:
    """Create one GitHub topic source per configured topic (idempotent)."""
    settings = get_settings()
    with store.tx() as c:
        for topic in settings["github"]["topics"]:
            url = f"topic:{topic}"
            exists = c.execute("SELECT 1 FROM sources WHERE url=?", (url,)).fetchone()
            if not exists:
                c.execute("INSERT INTO sources(id, name, type, url, enabled) VALUES(?,?,?,?,1)",
                          (store.new_id(), f"GitHub topic: {topic}", "github_topic", url))


def _upsert_resource(c, repo: dict, now_ms: int, kind_changed_note: bool = True) -> str:
    """Insert or update a resource from a source record. Returns 'new', 'updated' or 'same'."""
    row = c.execute("SELECT * FROM resources WHERE url=?", (repo["url"],)).fetchone()
    score, breakdown = score_repo(repo)
    metrics = {k: repo.get(k) for k in ("stars", "forks", "open_issues")}
    if repo.get("latest_release") is not None:
        metrics["latest_release"] = repo["latest_release"]
    elif row is not None and repo.get("release_checked") is False:
        metrics["latest_release"] = json.loads(row["metrics"]).get("latest_release")
    if row is None:
        rid = store.new_id()
        c.execute("""INSERT INTO resources(id, kind, name, url, author, description, topics, source_id,
                     first_seen, last_seen, last_updated, status, metrics, metrics_at, score, score_breakdown,
                     license, install_hint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (rid, repo["kind"], repo["name"], repo["url"], repo.get("author", ""), repo.get("description", ""),
                   json.dumps(repo.get("topics", []), ensure_ascii=False), repo.get("source_id", ""),
                   now_ms, now_ms, repo.get("pushed_at", ""), repo.get("status", "active"),
                   json.dumps(metrics, ensure_ascii=False), now_ms if repo.get("stars") is not None else None,
                   score, json.dumps(breakdown, ensure_ascii=False), repo.get("license", ""), repo.get("install_hint", "")))
        c.execute("INSERT INTO events(resource_id, type, detail, created) VALUES(?,?,?,?)",
                  (rid, "new", repo["name"], now_ms))
        return "new"

    rid = row["id"]
    old_metrics = json.loads(row["metrics"] or "{}")
    old_tag = (old_metrics.get("latest_release") or {}).get("tag")
    new_tag = (metrics.get("latest_release") or {}).get("tag")
    if old_tag and new_tag and old_tag != new_tag:
        c.execute("INSERT INTO events(resource_id, type, detail, created) VALUES(?,?,?,?)",
                  (rid, "release", f"{old_tag} → {new_tag}", now_ms))
    new_status = repo.get("status", "active")
    if row["status"] != new_status:
        if row["status"] == "missing" and new_status == "active":
            event_type, detail = "recovered", repo["name"]
        else:
            event_type, detail = new_status, repo["name"]
        c.execute("INSERT INTO events(resource_id, type, detail, created) VALUES(?,?,?,?)",
                  (rid, event_type, detail, now_ms))
    description = repo.get("description") or row["description"]
    c.execute("""UPDATE resources SET kind=?, name=?, author=?, description=?, topics=?, last_seen=?,
                 last_updated=?, status=?, metrics=?, metrics_at=?, score=?, score_breakdown=?, license=?
                 WHERE id=?""",
              (repo["kind"], repo["name"], repo.get("author", row["author"]), description,
               json.dumps(repo.get("topics") or json.loads(row["topics"]), ensure_ascii=False), now_ms,
               repo.get("pushed_at") or row["last_updated"], repo.get("status", "active"),
               json.dumps(metrics, ensure_ascii=False), now_ms if repo.get("stars") is not None else row["metrics_at"],
               score, json.dumps(breakdown, ensure_ascii=False), repo.get("license") or row["license"], rid))
    return "updated"


def _refresh_tracked(c, gh: GitHub, limit: int, now_ms: int, release_checks: int) -> dict:
    """Re-check known GitHub repos: detect missing (404), archived, and new releases."""
    stats = {"checked": 0, "missing": 0, "archived": 0, "releases": 0}
    rows = c.execute("""SELECT r.*, s.id AS sid FROM resources r JOIN sources s ON s.id = r.source_id
                        WHERE r.url LIKE 'https://github.com/%' AND r.status != 'missing'
                        ORDER BY COALESCE(r.metrics_at, 0) ASC LIMIT ?""", (limit,)).fetchall()
    for row in rows:
        full = row["name"]
        try:
            data = gh.repo(full)
        except BudgetExhausted:
            break
        stats["checked"] += 1
        if data is None:
            c.execute("UPDATE resources SET status='missing' WHERE id=?", (row["id"],))
            c.execute("INSERT INTO events(resource_id, type, detail, created) VALUES(?,?,?,?)",
                      (row["id"], "missing", "repo trả về 404 (đã xóa, đổi tên hoặc chuyển riêng tư)", now_ms))
            stats["missing"] += 1
            continue
        repo = normalize_repo(data, source_id=row["source_id"], topic_kinds={}, classify=lambda *_: row["kind"])
        if repo is None:
            continue
        if stats["checked"] <= release_checks:
            try:
                repo["latest_release"] = gh.latest_release(full)
                repo["release_checked"] = True
            except (BudgetExhausted, SourceError):
                pass
        repo["kind"] = row["kind"]
        before = row["status"]
        if repo["status"] == "archived" and before != "archived":
            stats["archived"] += 1
        before_tag = (json.loads(row["metrics"] or "{}").get("latest_release") or {}).get("tag")
        if (repo.get("latest_release") or {}).get("tag") not in (None, before_tag):
            stats["releases"] += 1
        repo["metrics_at"] = now_iso()
        _upsert_resource(c, repo, now_ms)
    return stats


def _record_source(c, source_id: str, ok: bool, count: int = 0, error: str = "") -> None:
    if ok:
        c.execute("UPDATE sources SET ok_count=ok_count+1, last_ok=?, last_count=?, last_error='' WHERE id=?",
                  (store.now_ms(), count, source_id))
    else:
        c.execute("UPDATE sources SET fail_count=fail_count+1, last_error=?, last_count=0 WHERE id=?",
                  (error[:200], source_id))


def run_sync(trigger: str = "manual", *, sleep=None, token: str | None = None) -> dict:
    """Run every enabled source once. Returns a summary dict. Safe to call from any thread."""
    import time as _time
    sleep = sleep or _time.sleep
    if not _run_lock.acquire(blocking=False):
        return {"status": "busy", "message": "Đang có lần đồng bộ khác chạy."}
    try:
        settings = get_settings()
        token = github_token() if token is None else token
        budget = int(settings["max_requests_per_run"]) if not token else max(int(settings["max_requests_per_run"]), DEFAULT_TOKEN_BUDGET)
        gh_cfg, retry = settings["github"], settings["retry"]
        topic_kinds = gh_cfg.get("topic_kinds") or {}
        started = store.now_ms()
        with store.tx() as c:
            run_id = c.execute("INSERT INTO runs(trigger, started, status) VALUES(?,?,'running')", (trigger, started)).lastrowid
        ensure_default_sources()
        gh = GitHub(token, budget, retries=int(retry["retries"]), backoff=float(retry["backoff_seconds"]), sleep=sleep)
        # Least-recently-successful sources first, so a small request budget rotates across topics day by day.
        sources = store.query("SELECT * FROM sources WHERE enabled=1 ORDER BY COALESCE(last_ok, 0) ASC, name ASC")
        stats: dict = {"sources": {}, "new": 0, "updated": 0, "refresh": {}}
        now_ms = store.now_ms()
        for src in sources:
            name = src["name"]
            try:
                if src["type"] == "github_topic":
                    topic = src["url"].split(":", 1)[1]
                    repos = fetch_github_topic(gh, topic=topic, source_id=src["id"], topic_kinds=topic_kinds,
                                               classify=classify, lookback_days=gh_cfg["lookback_days"],
                                               limit=gh_cfg["per_topic_limit"], release_checks=gh_cfg["release_checks"],
                                               now_iso=now_iso())
                elif src["type"] == "github_owner":
                    login = src["url"].split(":", 1)[1]
                    repos = fetch_github_owner(gh, login=login, source_id=src["id"], topic_kinds=topic_kinds,
                                               classify=classify, limit=gh_cfg["owner_limit"], now_iso=now_iso())
                elif src["type"] == "rss":
                    repos = fetch_feed(src["url"], source_id=src["id"], source_name=name,
                                       retries=int(retry["retries"]), backoff=float(retry["backoff_seconds"]), sleep=sleep)
                else:
                    raise SourceError("loại nguồn không được hỗ trợ")
                with store.tx() as c:
                    counts = {"new": 0, "updated": 0}
                    for repo in repos:
                        result = _upsert_resource(c, repo, store.now_ms())
                        if result in counts:
                            counts[result] += 1
                    _record_source(c, src["id"], True, len(repos))
                stats["new"] += counts["new"]
                stats["updated"] += counts["updated"]
                stats["sources"][name] = {"ok": True, "found": len(repos), **counts}
            except (SourceError, BudgetExhausted, RateLimited) as err:
                msg = redact(err, token)
                with store.tx() as c:
                    _record_source(c, src["id"], False, error=msg)
                stats["sources"][name] = {"ok": False, "error": msg}
            except Exception:  # noqa: BLE001 - one broken source must not stop the run
                with store.tx() as c:
                    _record_source(c, src["id"], False, error="lỗi nội bộ khi xử lý nguồn (xem log)")
                stats["sources"][name] = {"ok": False, "error": "lỗi nội bộ khi xử lý nguồn"}

        if gh.calls and gh.calls < budget:
            with store.tx() as c:
                stats["refresh"] = _refresh_tracked(c, gh, gh_cfg["refresh_limit"], store.now_ms(), gh_cfg["release_checks"])
        if trigger == "auto" and settings["schedule"].get("ai_enabled", True):
            from . import ai
            ai_status = ai.status()
            if not ai_status["available"]:
                stats["ai_review"] = {"status": "unavailable", "message": ai_status.get("message", "Chưa cấu hình AI.")}
            else:
                try:
                    rows = store.query("""SELECT id, kind, name, description, topics, metrics, license, status
                                          FROM resources WHERE last_seen>=? ORDER BY last_seen DESC LIMIT 12""", (started,))
                    items = [{**row, "topics": json.loads(row["topics"] or "[]"),
                              "metrics": json.loads(row["metrics"] or "{}")} for row in rows]
                    ranked = ai.rank_resources(items)
                    names = {row["id"]: row["name"] for row in items}
                    stats["ai_review"] = {"status": "ok", "model": ai_status["model"], "analyzed": len(ranked),
                                           "top": [{"name": names.get(row["id"], ""), "score": row["score"],
                                                    "reason": row["reason"][:180]} for row in ranked[:3]]}
                except Exception as err:  # noqa: BLE001 - AI failure must not invalidate a successful sync
                    stats["ai_review"] = {"status": "error", "model": ai_status["model"], "message": str(err)[:200]}
        with store.tx() as c:
            c.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 500)")
        stats["github_requests"] = gh.calls
        oks = [v["ok"] for v in stats["sources"].values()]
        status = "empty" if not oks else ("ok" if all(oks) else ("failed" if not any(oks) else "partial"))
        with store.tx() as c:
            c.execute("UPDATE runs SET finished=?, status=?, stats=? WHERE id=?",
                      (store.now_ms(), status, json.dumps(stats, ensure_ascii=False), run_id))
        return {"status": status, "run_id": run_id, "stats": stats}
    finally:
        _run_lock.release()
