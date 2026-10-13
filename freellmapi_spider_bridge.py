"""Read-only, loopback bridge from FreeLLMAPI Desktop analytics to Prompt Spider.

Run with: python freellmapi_spider_bridge.py
Open:     http://127.0.0.1:8766/index.html

It also serves the AI Personal Growth Hub API under /api/hub (see hub/api.py)
and runs the daily sync scheduler (see hub/scheduler.py) in a background thread.

Only aggregate request metrics, request metadata, and explicitly saved Playground
messages are served. Provider credentials and raw error strings are never read
or returned. The SQLite database is opened read-only.
"""

from __future__ import annotations

import json
import re
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
NEWS_ROOT = ROOT
SPIDER_PAGE = ROOT / "spider.html"
DB_PATH = Path(os.environ.get("FREELLMAPI_DB", Path(os.environ.get("APPDATA", "")) / "FreeLLMAPI" / "freeapi.db"))
HOST = "127.0.0.1"
PORT = int(os.environ.get("DAILYHUB_PORT", "8766"))
RANGES = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}
FALLBACK_INPUT_PER_M = 0.20
FALLBACK_OUTPUT_PER_M = 0.80


def db_connect() -> sqlite3.Connection:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"FreeLLMAPI database not found: {DB_PATH}")
    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=1.5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def cutoff(range_name: str) -> str:
    days = RANGES.get(range_name, 7)
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def percentile(values: list[int], p: float) -> int | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return values[max(0, math.ceil(p * len(values)) - 1)]


def snapshot(range_name: str) -> dict:
    since = cutoff(range_name)
    with db_connect() as con:
        summary = con.execute(
            """SELECT COUNT(*) AS requests,
                      SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
                      SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS failed,
                      SUM(CASE WHEN status='canceled' THEN 1 ELSE 0 END) AS canceled,
                      SUM(COALESCE(input_tokens,0)) AS input_tokens,
                      SUM(COALESCE(output_tokens,0)) AS output_tokens,
                      AVG(latency_ms) AS avg_latency_ms,
                      AVG(ttfb_ms) AS avg_ttfb_ms
                 FROM requests WHERE created_at >= ?""",
            (since,),
        ).fetchone()

        # Mirror FreeLLMAPI's Analytics "estimated savings" calculation:
        # price successful requests at their paid-equivalent model rates,
        # falling back to the app's modest default when a model has no price.
        model_columns = {row[1] for row in con.execute("PRAGMA table_info(models)")}
        if {"paid_input_per_m", "paid_output_per_m"}.issubset(model_columns):
            savings_row = con.execute(
                """SELECT COALESCE(SUM(
                           CASE WHEN r.status='success' THEN
                             COALESCE(r.input_tokens,0) * COALESCE(m.paid_input_per_m, ?) / 1000000.0 +
                             COALESCE(r.output_tokens,0) * COALESCE(m.paid_output_per_m, ?) / 1000000.0
                           ELSE 0 END
                         ),0) AS estimated_cost_savings
                   FROM requests r LEFT JOIN models m ON m.id=r.model_db_id
                   WHERE r.created_at >= ?""",
                (FALLBACK_INPUT_PER_M, FALLBACK_OUTPUT_PER_M, since),
            ).fetchone()
            estimated_cost_savings = round(savings_row["estimated_cost_savings"] or 0, 2)
        else:
            # Older FreeLLMAPI databases may not have the pricing migration.
            estimated_cost_savings = None

        latency_values = [r[0] for r in con.execute(
            "SELECT latency_ms FROM requests WHERE created_at >= ? AND latency_ms IS NOT NULL",
            (since,),
        )]
        provider_rows = con.execute(
            """SELECT platform,
                      COUNT(*) AS requests,
                      SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
                      SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS failed,
                      AVG(latency_ms) AS avg_latency_ms,
                      AVG(ttfb_ms) AS avg_ttfb_ms,
                      SUM(COALESCE(input_tokens,0)) AS input_tokens,
                      SUM(COALESCE(output_tokens,0)) AS output_tokens
                 FROM requests WHERE created_at >= ?
                 GROUP BY platform ORDER BY requests DESC""",
            (since,),
        ).fetchall()

        provider_latency = con.execute(
            """SELECT platform, latency_ms FROM requests
                 WHERE created_at >= ? AND latency_ms IS NOT NULL ORDER BY platform""",
            (since,),
        ).fetchall()

        model_rows = con.execute(
            """SELECT r.platform, r.model_id, COALESCE(m.display_name, r.model_id) AS display_name,
                      COUNT(*) AS requests,
                      SUM(CASE WHEN r.status='success' THEN 1 ELSE 0 END) AS success,
                      SUM(CASE WHEN r.status='error' THEN 1 ELSE 0 END) AS failed,
                      SUM(CASE WHEN r.requested_model IS NOT NULL
                                AND lower(r.requested_model) NOT IN ('auto','fusion') THEN 1 ELSE 0 END) AS pinned,
                      AVG(r.latency_ms) AS avg_latency_ms,
                      SUM(COALESCE(r.input_tokens,0)) AS input_tokens,
                      SUM(COALESCE(r.output_tokens,0)) AS output_tokens
                 FROM requests r LEFT JOIN models m ON m.id=r.model_db_id
                 WHERE r.created_at >= ?
                 GROUP BY r.platform, r.model_id, display_name
                 ORDER BY requests DESC LIMIT 100""",
            (since,),
        ).fetchall()

        recent_rows = con.execute(
            """SELECT r.id, r.platform, r.model_id, r.requested_model, r.status,
                      r.input_tokens, r.output_tokens, r.latency_ms, r.created_at,
                      (SELECT COUNT(*) FROM request_attempts a WHERE a.request_id=r.id) AS attempts,
                      (SELECT COUNT(*) FROM request_attempts a WHERE a.request_id=r.id AND a.outcome<>'ok') AS failed_attempts
                 FROM requests r WHERE r.created_at>=? ORDER BY r.id DESC LIMIT 100""",
            (since,),
        ).fetchall()

        error_rows = con.execute(
            """SELECT platform, error FROM requests
                 WHERE created_at >= ? AND status='error' AND error IS NOT NULL""",
            (since,),
        ).fetchall()
        attempt_rows = con.execute(
            """SELECT a.outcome, COUNT(*) AS n
                 FROM request_attempts a JOIN requests r ON r.id=a.request_id
                 WHERE r.created_at >= ? GROUP BY a.outcome""",
            (since,),
        ).fetchall()
        latest_id = con.execute("SELECT COALESCE(MAX(id),0) FROM requests").fetchone()[0]

    decided = (summary["success"] or 0) + (summary["failed"] or 0)
    categories = {"Rate limited (429)": 0, "Model unavailable (404/503)": 0, "Server/connection error": 0, "Other": 0}
    by_error_provider: dict[str, int] = {}
    for row in error_rows:
        # Classify messages in memory; never expose raw provider error text.
        msg = (row["error"] or "").lower()
        if "429" in msg or "rate limit" in msg or "quota" in msg:
            category = "Rate limited (429)"
        elif "404" in msg or "not found" in msg or "503" in msg or "unavailable" in msg:
            category = "Model unavailable (404/503)"
        elif "500" in msg or "timeout" in msg or "connection" in msg or "502" in msg:
            category = "Server/connection error"
        else:
            category = "Other"
        categories[category] += 1
        by_error_provider[row["platform"] or "unknown"] = by_error_provider.get(row["platform"] or "unknown", 0) + 1

    failed_attempts = sum(r["n"] for r in attempt_rows if r["outcome"] != "ok")
    latencies_by_provider: dict[str, list[int]] = {}
    for row in provider_latency:
        latencies_by_provider.setdefault(row["platform"] or "unknown", []).append(row["latency_ms"])
    providers = []
    for row in provider_rows:
        item = dict(row)
        item["p95LatencyMs"] = percentile(latencies_by_provider.get(item["platform"] or "unknown", []), .95)
        total_latency = item["avg_latency_ms"] or 0
        item["tokensPerSecond"] = round((item.get("output_tokens", 0) or 0) / (total_latency / 1000), 1) if total_latency else None
        item["successRate"] = round((item["success"] or 0) * 100 / item["requests"], 1) if item["requests"] else 0
        providers.append(item)

    models = []
    for row in model_rows:
        item = dict(row)
        item["successRate"] = round((item["success"] or 0) * 100 / item["requests"], 1) if item["requests"] else 0
        models.append(item)
    return {
        "range": range_name if range_name in RANGES else "7d",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "requests": summary["requests"] or 0,
        "successRate": round((summary["success"] or 0) * 100 / decided, 1) if decided else 0,
        "failedRequests": summary["failed"] or 0,
        "canceledRequests": summary["canceled"] or 0,
        "failedAttempts": failed_attempts,
        "inputTokens": summary["input_tokens"] or 0,
        "outputTokens": summary["output_tokens"] or 0,
        "avgLatencyMs": round(summary["avg_latency_ms"] or 0),
        "p95LatencyMs": percentile(latency_values, .95),
        "avgTtfbMs": round(summary["avg_ttfb_ms"] or 0),
        "estimatedCostSavings": estimated_cost_savings,
        "byProvider": providers,
        "byModel": models,
        "recentCalls": [dict(r) for r in recent_rows],
        "errorsByCategory": categories,
        "errorsByProvider": by_error_provider,
        "latestRequestId": latest_id,
    }


def request_events(after_id: int) -> list[dict]:
    with db_connect() as con:
        rows = con.execute(
            """SELECT r.id, r.platform, r.model_id, r.requested_model, r.status,
                      r.input_tokens, r.output_tokens, r.latency_ms, r.created_at,
                      (SELECT COUNT(*) FROM request_attempts a WHERE a.request_id=r.id) AS attempts,
                      (SELECT COUNT(*) FROM request_attempts a WHERE a.request_id=r.id AND a.outcome<>'ok') AS failed_attempts
                 FROM requests r WHERE r.id>? ORDER BY r.id ASC LIMIT 250""",
            (max(0, after_id),),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_chats() -> list[dict]:
    """Return recent saved Playground transcripts, excluding system prompts."""
    with db_connect() as con:
        rows = con.execute(
            """SELECT id, title, model, messages_json, updated_at_ms
                 FROM playground_conversations ORDER BY updated_at_ms DESC LIMIT 5"""
        ).fetchall()
    chats = []
    for row in rows:
        try:
            raw_messages = json.loads(row["messages_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            raw_messages = []
        messages = []
        for message in raw_messages[-12:]:
            if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
                continue
            meta = message.get("meta") if isinstance(message.get("meta"), dict) else {}
            panel = meta.get("fusionPanel") if isinstance(meta.get("fusionPanel"), list) else []
            messages.append({
                "role": message.get("role"),
                "content": str(message.get("content", ""))[:12000],
                "model": str(meta.get("model", row["model"] or ""))[:160],
                "platform": str(meta.get("platform", ""))[:100],
                "fusionPanel": [
                    {"model": str(item.get("model", ""))[:160],
                     "platform": str(item.get("platform", ""))[:100],
                     "status": str(item.get("status", ""))[:20],
                     "content": str(item.get("content", ""))[:4000]}
                    for item in panel[:8] if isinstance(item, dict)
                ],
            })
        chats.append({"id": row["id"], "title": row["title"], "model": row["model"],
                      "updatedAt": row["updated_at_ms"], "messages": messages})
    return chats


ALLOWED_ORIGINS = {"http://127.0.0.1:8766", "http://localhost:8766"}
MAX_BODY = 2 * 1024 * 1024
MAX_DRAIN = 32 * 1024 * 1024
MAX_IMPORT_BODY = 20 * 1024 * 1024


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(NEWS_ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, payload: object, status: int = 200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _hub(self, method: str) -> bool:
        """Route /api/hub requests. Returns False when the path is not a hub path."""
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/hub"):
            return False
        if method != "GET":
            # Blocks cross-site form posts: browsers cannot add this header to a simple cross-origin request.
            if self.headers.get("X-DailyHub") != "1":
                self.send_json({"error": "thiếu header X-DailyHub"}, 403)
                return True
            origin = self.headers.get("Origin")
            if origin and origin not in ALLOWED_ORIGINS:
                self.send_json({"error": "origin không được phép"}, 403)
                return True
        body = b""
        length = self.headers.get("Content-Length")
        if method != "GET" and length:
            try:
                size = int(length)
            except ValueError:
                self.send_json({"error": "Content-Length không hợp lệ"}, 400)
                return True
            limit = MAX_IMPORT_BODY if parsed.path == "/api/hub/import" else MAX_BODY
            if size < 0 or size > limit:
                # Drain (bounded) so the client finishes sending and can read the 413 response.
                remaining = size if 0 < size <= MAX_DRAIN else 0  # read in chunks, never all at once
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self.close_connection = True
                self.send_json({"error": "body quá lớn hoặc không hợp lệ"}, 413)
                return True
            body = self.rfile.read(size)
        try:
            status, payload = hub_api.dispatch(method, parsed.path, parse_qs(parsed.query), body)
        except Exception:  # noqa: BLE001 - never leak internals to the browser
            print("[hub] internal error", flush=True)
            status, payload = 500, {"error": "lỗi nội bộ, xem log server"}
        self.send_json(payload, status)
        return True

    def do_POST(self):
        if not self._hub("POST"):
            self.send_error(405)

    def do_PUT(self):
        if not self._hub("PUT"):
            self.send_error(405)

    def do_PATCH(self):
        if not self._hub("PATCH"):
            self.send_error(405)

    def do_DELETE(self):
        if not self._hub("DELETE"):
            self.send_error(405)

    def do_GET(self):
        if self._hub("GET"):
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/spider.html":
            try:
                data = SPIDER_PAGE.read_bytes()
            except OSError:
                self.send_error(404, "Prompt Spider page not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        try:
            if parsed.path == "/api/snapshot":
                self.send_json(snapshot(query.get("range", ["7d"])[0]))
                return
            if parsed.path == "/api/events":
                try:
                    after = int(query.get("after", ["0"])[0])
                except ValueError:
                    after = 0
                self.send_json(request_events(after))
                return
            if parsed.path == "/api/chats":
                self.send_json(recent_chats())
                return
        except FileNotFoundError as exc:
            self.send_json({"error": str(exc)}, 503)
            return
        except sqlite3.Error:
            self.send_json({"error": "Database busy; retrying shortly"}, 503)
            return
        return super().do_GET()

    def log_message(self, fmt, *args):
        # Do not log query strings or request payloads (search terms may be private).
        line = re.sub(r"\?\S*", "?…", fmt % args)
        print(f"[Spider bridge] {self.address_string()} {line}")


from hub import api as hub_api  # noqa: E402
from hub import scheduler as hub_scheduler  # noqa: E402

if __name__ == "__main__":
    print(f"FreeLLMAPI database: {DB_PATH}")
    print(f"News app: {NEWS_ROOT}")
    print(f"Open http://{HOST}:{PORT}/index.html")
    print("Read-only; loopback only. Press Ctrl+C to stop.")
    hub_scheduler.start_default()
    print("Daily sync scheduler: " + ("on" if hub_scheduler.current() else "off"))
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

