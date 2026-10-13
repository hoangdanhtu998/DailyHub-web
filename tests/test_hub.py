"""Tests for the AI Personal Growth Hub backend.

Run:  python -m unittest discover -s tests -v
Uses a local fake GitHub/RSS server, so no real network calls are made.
"""
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Local fake GitHub API. Must be set before hub.sources is imported.
FAKE_STATE = {"release_tag": "v1.0.0", "gone": False, "fail503": 0, "ratelimit": False, "feed_fail": False,
              "weather_calls": 0, "weather_fail": False}
NOW = datetime.now(timezone.utc)


def iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


REPO = {"full_name": "acme/mcp-tools", "html_url": "https://github.com/acme/mcp-tools", "description": "MCP tools <b>for</b> agents",
        "topics": ["mcp", "llm"], "stargazers_count": 1200, "forks_count": 80, "open_issues_count": 5,
        "pushed_at": iso(3), "archived": False, "license": {"spdx_id": "MIT"}, "homepage": "https://acme.dev",
        "owner": {"login": "acme"}, "fork": False}
OLD = {"full_name": "old/archived-tool", "html_url": "https://github.com/old/archived-tool", "description": "old",
       "topics": ["mcp"], "stargazers_count": 300, "forks_count": 2, "open_issues_count": 0,
       "pushed_at": "2020-01-01T00:00:00Z", "archived": True, "license": None, "homepage": "",
       "owner": {"login": "old"}, "fork": False}
_HOURS = [f"2026-10-09T{h:02d}:00" for h in range(12, 24)]
WEATHER_FIXTURE = {
    "timezone": "Asia/Ho_Chi_Minh",
    "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
    "current": {"time": "2026-10-09T15:00", "temperature_2m": 29.4, "apparent_temperature": 33.1,
                "relative_humidity_2m": 70, "precipitation": 0.0, "weather_code": 2, "wind_speed_10m": 9.5},
    "hourly": {"time": _HOURS, "temperature_2m": [25 + i for i in range(len(_HOURS))],
               "precipitation_probability": [10 * (i % 5) for i in range(len(_HOURS))],
               "weather_code": [0, 1, 2, 3, 61, 63, 80, 95, 0, 1, 2, 3][:len(_HOURS)]},
}
RSS_OK = b"""<?xml version="1.0"?><rss><channel><item><title>Ban phat hanh moi</title>
<link>https://example.org/release-notes</link><description>&lt;p&gt;Ghi chu&lt;/p&gt;</description></item></channel></rss>"""


class FakeGitHub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep test output quiet
        pass

    def _send(self, status, body=b"", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/feed.xml":
            return self._send(200, RSS_OK) if not FAKE_STATE["feed_fail"] else self._send(500, b"boom")
        if path == "/v1/forecast":
            FAKE_STATE["weather_calls"] += 1
            if FAKE_STATE["weather_fail"]:
                return self._send(500, b"{}")
            return self._send(200, json.dumps(WEATHER_FIXTURE).encode())
        if FAKE_STATE["ratelimit"]:
            return self._send(403, b"{}", {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "123"})
        if path == "/search/repositories":
            if FAKE_STATE["fail503"] > 0:
                FAKE_STATE["fail503"] -= 1
                return self._send(503, b"{}")
            items = [] if FAKE_STATE["gone"] else [REPO]
            if "topic:mcp" in self.path or "topic%3Amcp" in self.path:
                items = items + [OLD]
            return self._send(200, json.dumps({"items": items}).encode())
        if path == "/repos/acme/mcp-tools/releases/latest":
            return self._send(200, json.dumps({"tag_name": FAKE_STATE["release_tag"], "published_at": iso(10)}).encode())
        if path == "/repos/acme/mcp-tools":
            if FAKE_STATE["gone"]:
                return self._send(404, b"{}")
            return self._send(200, json.dumps(REPO).encode())
        if path == "/repos/old/archived-tool/releases/latest":
            return self._send(404, b"{}")
        if path == "/repos/old/archived-tool":
            return self._send(200, json.dumps(OLD).encode())
        if path == "/users/acme/repos":
            return self._send(200, json.dumps([REPO]).encode())
        return self._send(404, b"{}")


_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
threading.Thread(target=_server.serve_forever, daemon=True).start()
FAKE_BASE = f"http://127.0.0.1:{_server.server_address[1]}"
os.environ["DAILYHUB_GITHUB_API"] = FAKE_BASE
os.environ["DAILYHUB_DATA"] = tempfile.mkdtemp(prefix="dh-test-")
os.environ["DAILYHUB_ALLOW_LOCAL_FEEDS"] = "1"  # fixture feed is on 127.0.0.1
os.environ["DAILYHUB_WEATHER_API"] = FAKE_BASE + "/v1/forecast"

from hub import api, backup, pipeline, scheduler, store, weather  # noqa: E402
from hub.normalize import canonical_url, clean_text, classify, safe_url  # noqa: E402
from hub.scoring import score_repo  # noqa: E402
from hub.sources import SourceError, http_get, parse_feed  # noqa: E402


def fresh_db():
    store.open_db(Path(tempfile.mkdtemp()) / "t.db")


def no_sleep(_s):
    return None


class TestStorage(unittest.TestCase):
    def test_migrations_and_persistence(self):
        path = Path(tempfile.mkdtemp()) / "m.db"
        store.open_db(path)
        self.assertEqual(store.schema_version(), 1)
        store.set_meta("probe", "hello")
        store.open_db(path)  # reopen: migration must not wipe data
        self.assertEqual(store.get_meta("probe"), "hello")
        self.assertEqual(store.schema_version(), 1)


class TestScoring(unittest.TestCase):
    def test_breakdown_sums_to_score_and_is_bounded(self):
        score, br = score_repo({"stars": 10**6, "forks": 10**5, "pushed_at": iso(1), "archived": False,
                                "license": "MIT", "description": "d", "homepage": "h",
                                "latest_release": {"tag": "v1", "published_at": iso(5)}, "release_checked": True}, NOW)
        self.assertEqual(score, sum(v["points"] for v in br.values()))
        self.assertLessEqual(score, 100)

    def test_stars_are_not_the_only_signal(self):
        low_stars_active, _ = score_repo({"stars": 50, "forks": 5, "pushed_at": iso(2), "archived": False, "license": "MIT",
                                          "description": "d", "release_checked": True, "latest_release": {"tag": "v2", "published_at": iso(20)}}, NOW)
        high_stars_dead, _ = score_repo({"stars": 50000, "forks": 900, "pushed_at": "2019-01-01T00:00:00Z", "archived": True,
                                         "license": "", "description": "", "release_checked": False}, NOW)
        self.assertGreater(low_stars_active, high_stars_dead)

    def test_scores_differentiate_popular_repos(self):
        base = {"pushed_at": iso(2), "archived": False, "license": "MIT", "description": "d", "release_checked": False}
        small, _ = score_repo(dict(base, stars=1000, forks=50), NOW)
        big, _ = score_repo(dict(base, stars=100000, forks=5000), NOW)
        huge, _ = score_repo(dict(base, stars=1000000, forks=50000), NOW)
        self.assertLess(small, big)
        self.assertLess(big, huge)  # not all top repos saturate at 100

    def test_archived_capped_and_unknown_is_labelled(self):
        s, br = score_repo({"stars": 99999, "forks": 9999, "pushed_at": iso(1), "archived": True, "release_checked": False}, NOW)
        self.assertLessEqual(s, 20)
        self.assertIn("chưa xác minh", br["release"]["reason"])

    def test_missing_repo_has_no_score(self):
        self.assertIsNone(score_repo({"status": "missing"}, NOW)[0])


class TestNormalize(unittest.TestCase):
    def test_classify_priority(self):
        self.assertEqual(classify(["mcp", "agent-skills"]), "skill")
        self.assertEqual(classify(["llm", "rag"]), "toolkit")
        self.assertEqual(classify(["llm"]), "repo")
        self.assertEqual(classify([]), "repo")

    def test_clean_text_removes_html_and_scripts(self):
        self.assertEqual(clean_text("<script>x()</script>Xin &lt;b&gt; <img onerror=y> ok"), "Xin <b> ok")

    def test_unsafe_urls_rejected(self):
        self.assertEqual(safe_url("javascript:alert(1)"), "")
        self.assertEqual(safe_url("file:///etc/passwd"), "")
        self.assertEqual(safe_url("https://example.com/x"), "https://example.com/x")

    def test_canonical_url(self):
        self.assertEqual(canonical_url("HTTPS://GitHub.com/A/B/"), "https://github.com/A/B")


class TestFeeds(unittest.TestCase):
    def test_rss_and_atom(self):
        rss = parse_feed(RSS_OK, "s", "Demo")
        self.assertEqual(rss[0]["kind"], "article")
        self.assertEqual(rss[0]["description"], "Ghi chu")
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title><link href="https://e.org/x"/></entry></feed>'
        self.assertEqual(parse_feed(atom, "s", "A")[0]["url"], "https://e.org/x")

    def test_invalid_feed_rejected(self):
        with self.assertRaises(SourceError):
            parse_feed(b"<<<nope", "s", "x")
        with self.assertRaises(SourceError):
            parse_feed(b"<html/>", "s", "x")

    def test_retry_then_success(self):
        FAKE_STATE["fail503"] = 2
        delays = []
        status, _h, body = http_get(FAKE_BASE + "/search/repositories?q=x", retries=2, backoff=1, sleep=delays.append)
        self.assertEqual(status, 200)
        self.assertEqual(delays, [1, 2])
        FAKE_STATE["fail503"] = 0

    def test_bypasses_unavailable_loopback_sink_proxy(self):
        class Response:
            status = 200
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def read(self, _limit): return b'{"ok":true}'

        with patch("hub.sources.urllib.request.getproxies", return_value={"https": "http://127.0.0.1:9"}), \
             patch("hub.sources._DIRECT_OPENER.open", return_value=Response()) as direct_open, \
             patch("hub.sources.urllib.request.urlopen", side_effect=urllib.error.URLError("proxy unavailable")):
            status, _headers, body = http_get("https://api.github.com/zen", retries=0)
        self.assertEqual(status, 200)
        self.assertEqual(body, b'{"ok":true}')
        direct_open.assert_called_once()


class TestPipeline(unittest.TestCase):
    def setUp(self):
        fresh_db()
        FAKE_STATE.update({"release_tag": "v1.0.0", "gone": False, "fail503": 0, "ratelimit": False, "feed_fail": False})
        settings = pipeline.get_settings()
        settings["github"]["topics"] = ["mcp"]
        pipeline.save_settings({"github": {"topics": ["mcp"]}})

    def test_first_run_creates_resources_and_events(self):
        out = pipeline.run_sync("manual", sleep=no_sleep, token="")
        self.assertIn(out["status"], ("ok", "partial"))
        rows = store.query("SELECT name, kind, status, score FROM resources ORDER BY name")
        names = {r["name"]: r for r in rows}
        self.assertIn("acme/mcp-tools", names)
        self.assertEqual(names["acme/mcp-tools"]["kind"], "toolkit")
        self.assertEqual(names["old/archived-tool"]["status"], "archived")
        self.assertIsNotNone(names["acme/mcp-tools"]["score"])
        types = {e["type"] for e in store.query("SELECT type FROM events")}
        self.assertIn("new", types)

    def test_user_state_survives_resync_and_release_event(self):
        pipeline.run_sync("manual", sleep=no_sleep, token="")
        rid = store.query_one("SELECT id FROM resources WHERE name='acme/mcp-tools'")["id"]
        api.patch_resource_state(rid, {"saved": True, "seen": True, "rating": 4, "note": "dùng tốt"})
        FAKE_STATE["release_tag"] = "v1.1.0"
        pipeline.run_sync("manual", sleep=no_sleep, token="")
        state = api.get_resource(rid)
        self.assertTrue(state["saved"]); self.assertEqual(state["note"], "dùng tốt"); self.assertEqual(state["rating"], 4)
        rel = store.query("SELECT detail FROM events WHERE type='release'")
        self.assertEqual(rel[0]["detail"], "v1.0.0 → v1.1.0")

    def test_missing_repo_detected_then_recovered(self):
        pipeline.run_sync("manual", sleep=no_sleep, token="")
        FAKE_STATE["gone"] = True
        pipeline.run_sync("manual", sleep=no_sleep, token="")
        self.assertEqual(store.query_one("SELECT status FROM resources WHERE name='acme/mcp-tools'")["status"], "missing")
        self.assertIn("missing", {e["type"] for e in store.query("SELECT type FROM events")})
        FAKE_STATE["gone"] = False
        pipeline.run_sync("manual", sleep=no_sleep, token="")
        self.assertEqual(store.query_one("SELECT status FROM resources WHERE name='acme/mcp-tools'")["status"], "active")
        self.assertIn("recovered", {e["type"] for e in store.query("SELECT type FROM events")})

    def test_rate_limit_is_recorded_and_secret_not_leaked(self):
        FAKE_STATE["ratelimit"] = True
        secret = "ghp_TESTSECRET123"
        out = pipeline.run_sync("manual", sleep=no_sleep, token=secret)
        FAKE_STATE["ratelimit"] = False
        self.assertEqual(out["status"], "failed")
        src = store.query_one("SELECT last_error, fail_count FROM sources WHERE url='topic:mcp'")
        self.assertIn("giới hạn", src["last_error"])
        self.assertEqual(src["fail_count"], 1)
        self.assertNotIn(secret, json.dumps(out, ensure_ascii=False))
        self.assertNotIn(secret, src["last_error"])

    def test_one_failing_source_does_not_stop_others(self):
        api.create_source({"type": "rss", "name": "Bad feed", "url": FAKE_BASE + "/broken"})
        api.create_source({"type": "rss", "name": "Good feed", "url": FAKE_BASE + "/feed.xml"})
        out = pipeline.run_sync("manual", sleep=no_sleep, token="")
        self.assertFalse(out["stats"]["sources"]["Bad feed"]["ok"])
        self.assertTrue(out["stats"]["sources"]["Good feed"]["ok"])
        self.assertEqual(out["status"], "partial")
        self.assertEqual(store.query_one("SELECT COUNT(*) AS n FROM resources WHERE kind='article'")["n"], 1)

    def test_run_is_recorded(self):
        pipeline.run_sync("manual", sleep=no_sleep, token="")
        run = store.query_one("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
        self.assertIn(run["status"], ("ok", "partial", "failed"))
        self.assertIsNotNone(run["finished"])


class TestWeather(unittest.TestCase):
    LOC = {"name": "Hà Nội", "latitude": 21.0285, "longitude": 105.8542, "timezone": "Asia/Ho_Chi_Minh"}

    def setUp(self):
        fresh_db()
        weather.clear_cache()
        FAKE_STATE.update({"weather_calls": 0, "weather_fail": False})

    def test_parse_current_and_upcoming_hours(self):
        data = weather.parse_forecast(WEATHER_FIXTURE, "2026-10-09T15:00")
        self.assertEqual(data["current"]["temperature"], 29.4)
        self.assertEqual(data["current"]["description"], "Có mây rải rác")
        self.assertEqual(len(data["upcoming"]), weather.HOURS_AHEAD)
        self.assertEqual(data["upcoming"][0]["time"], "2026-10-09T15:00")
        self.assertIn("Open-Meteo", data["attribution"])

    def test_missing_fields_do_not_crash(self):
        data = weather.parse_forecast({"current": {}}, None)
        self.assertIsNone(data["current"]["temperature"])
        self.assertEqual(data["upcoming"], [])
        self.assertEqual(weather.describe(9999), "Không xác định")

    def test_cache_avoids_repeat_calls(self):
        weather.get_weather(self.LOC, sleep=no_sleep)
        weather.get_weather(self.LOC, sleep=no_sleep)
        self.assertEqual(FAKE_STATE["weather_calls"], 1)

    def test_upstream_failure_without_cache_is_an_error_view(self):
        FAKE_STATE["weather_fail"] = True
        view = api.weather_view()
        self.assertFalse(view["ok"])
        self.assertIn("500", view["error"])
        self.assertIn("location", view)

    def test_upstream_failure_with_cache_serves_stale(self):
        weather.get_weather(self.LOC, sleep=no_sleep)
        weather._cache = {k: (v[0] - 10_000, v[1]) for k, v in weather._cache.items()}  # expire it
        FAKE_STATE["weather_fail"] = True
        out = weather.get_weather(self.LOC, sleep=no_sleep)
        self.assertTrue(out["stale"])
        self.assertIsNotNone(out["current"]["temperature"])

    def test_location_validation(self):
        with self.assertRaises(api.ApiError):
            api.put_settings({"weather": {"latitude": 200, "longitude": 10}})
        with self.assertRaises(api.ApiError):
            api.put_settings({"weather": {"latitude": "abc", "longitude": 10}})
        with self.assertRaises(api.ApiError):
            api.put_settings({"weather": {"latitude": 10, "longitude": 10, "timezone": "Mars/Base"}})
        out = api.put_settings({"weather": {"name": "Huế", "latitude": 16.46, "longitude": 107.59, "timezone": "Asia/Ho_Chi_Minh"}})
        self.assertEqual(out["weather"]["name"], "Huế")

    def test_default_location_is_hanoi(self):
        self.assertEqual(pipeline.get_settings()["weather"]["name"], "Hà Nội")


class TestSearch(unittest.TestCase):
    def setUp(self):
        fresh_db()

    def test_rejects_too_short_query(self):
        with self.assertRaises(api.ApiError):
            api.search("a")

    def test_finds_across_types_and_ranks_title_first(self):
        api.create_memory({"title": "Quy tắc trả lời", "body": "luôn tiếng Việt"})
        api.create_memory({"title": "Khác", "body": "có chữ quy tắc trong nội dung"})
        api.create_prompt({"title": "Tóm tắt", "body": "quy tắc: ngắn gọn"})
        out = api.search("quy tắc")
        self.assertEqual(out["memories"][0]["title"], "Quy tắc trả lời")
        self.assertEqual(len(out["memories"]), 2)
        self.assertEqual(len(out["prompts"]), 1)
        self.assertGreaterEqual(out["total"], 3)

    def test_wildcards_are_literal(self):
        api.create_memory({"title": "Tỉ lệ 100% đúng", "body": ""})
        api.create_memory({"title": "không có", "body": "tỉ lệ 1000 đúng"})
        out = api.search("100%")
        self.assertEqual([m["title"] for m in out["memories"]], ["Tỉ lệ 100% đúng"])
        self.assertEqual(api.search("tỉ lệ 1")["total"], 2)  # prefix-free: matches both memories

    def test_searches_resource_notes(self):
        pipeline.save_settings({"github": {"topics": ["mcp"]}})
        pipeline.run_sync("manual", sleep=no_sleep, token="")
        rid = store.query_one("SELECT id FROM resources WHERE name='acme/mcp-tools'")["id"]
        api.patch_resource_state(rid, {"note": "dùng cho dự án chatbot nội bộ"})
        out = api.search("chatbot")
        self.assertEqual(out["notes"][0]["name"], "acme/mcp-tools")
        self.assertIn("chatbot", out["notes"][0]["snippet"])

    def test_get_memory_by_id(self):
        m = api.create_memory({"title": "Một mục"})
        self.assertEqual(api.get_memory(m["id"])["title"], "Một mục")
        with self.assertRaises(api.ApiError):
            api.get_memory("missing")


class TestBackup(unittest.TestCase):
    def setUp(self):
        fresh_db()
        api.create_memory({"title": "Ghi nhớ quan trọng", "body": "x"})

    def test_create_writes_file_and_excludes_secrets(self):
        info = backup.create_backup("manual")
        self.assertTrue(backup.NAME_RE.match(info["name"]))
        data = backup.read_backup(info["name"])
        self.assertEqual(data["app"], "DailyHub")
        self.assertEqual(len(data["memories"]), 1)
        self.assertNotIn("GITHUB_TOKEN", json.dumps(data))

    def test_prune_keeps_newest_n(self):
        for i in range(5):
            backup.create_backup("manual")
        self.assertGreaterEqual(len(backup.list_backups()), 5)
        removed = backup.prune(keep=2)
        self.assertGreaterEqual(removed, 3)
        self.assertEqual(len(backup.list_backups()), 2)

    def test_names_are_validated_against_path_traversal(self):
        for bad in ("../secret.json", "dailyhub-x.json", "/etc/passwd", "dailyhub-20260101-000000.json/../..", ""):
            with self.assertRaises(ValueError):
                backup.read_backup(bad)
        with self.assertRaises(api.ApiError):
            api.backups_read("../../etc/passwd")

    def test_auto_backup_runs_after_daily_sync_when_enabled(self):
        sch = scheduler.Scheduler(interval=3600)
        orig = pipeline.run_sync
        pipeline.run_sync = lambda trigger="manual", **kw: {"status": "ok"}
        try:
            before = len(backup.list_backups())
            sch.tick(datetime(2026, 10, 9, 1, tzinfo=timezone.utc))
            self.assertEqual(len(backup.list_backups()), before + 1)
            pipeline.save_settings({"backup": {"enabled": False}})
            sch.tick(datetime(2026, 10, 10, 1, tzinfo=timezone.utc))
            self.assertEqual(len(backup.list_backups()), before + 1)  # disabled: no new file
        finally:
            pipeline.run_sync = orig

    def test_backup_settings_validation(self):
        with self.assertRaises(api.ApiError):
            api.put_settings({"backup": {"keep": 0}})
        with self.assertRaises(api.ApiError):
            api.put_settings({"backup": {"enabled": "yes"}})
        self.assertEqual(api.put_settings({"backup": {"keep": 7}})["backup"]["keep"], 7)

    def test_restore_from_backup_is_merge_not_duplicate(self):
        info = backup.create_backup("manual")
        bundle = backup.read_backup(info["name"])
        summary = api.import_bundle(bundle)
        self.assertEqual(summary["memories_added"], 0)
        self.assertEqual(summary["memories_skipped"], 1)


class TestSearchAndBackupHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TestHttpLayer.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        TestHttpLayer.tearDownClass.__func__(cls)

    def _req(self, *a, **kw):
        return TestHttpLayer._req(self, *a, **kw)

    def test_search_endpoint_and_short_query(self):
        self._req("POST", "/api/hub/memories", {"title": "Kiểm thử tìm kiếm"}, {"X-DailyHub": "1"})
        from urllib.parse import quote
        status, body = self._req("GET", "/api/hub/search?q=" + quote("kiểm thử"))
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(body["memories"]), 1)
        status, _ = self._req("GET", "/api/hub/search?q=x")
        self.assertEqual(status, 400)

    def test_backup_create_list_read_delete_over_http(self):
        status, info = self._req("POST", "/api/hub/backups", None, {"X-DailyHub": "1"})
        self.assertEqual(status, 201)
        status, listing = self._req("GET", "/api/hub/backups")
        self.assertTrue(any(i["name"] == info["name"] for i in listing["items"]))
        status, data = self._req("GET", "/api/hub/backups/" + info["name"])
        self.assertEqual(status, 200)
        self.assertEqual(data["app"], "DailyHub")
        status, _ = self._req("GET", "/api/hub/backups/..%2F..%2Fetc%2Fpasswd")
        self.assertEqual(status, 400)
        status, _ = self._req("DELETE", "/api/hub/backups/" + info["name"], None, {"X-DailyHub": "1"})
        self.assertEqual(status, 200)


class TestSsrfGuard(unittest.TestCase):
    def test_private_targets_blocked_when_guard_enabled(self):
        from hub import sources
        os.environ.pop("DAILYHUB_ALLOW_LOCAL_FEEDS", None)
        try:
            with self.assertRaises(SourceError):
                sources.fetch_feed("http://127.0.0.1:9/feed.xml", source_id="s", source_name="x", sleep=no_sleep)
            with self.assertRaises(SourceError):
                sources.check_public_host("http://192.168.1.10/rss")
        finally:
            os.environ["DAILYHUB_ALLOW_LOCAL_FEEDS"] = "1"


class TestScheduler(unittest.TestCase):
    def setUp(self):
        fresh_db()
        pipeline.save_settings({"schedule": {"enabled": True, "time": "07:00", "timezone": "Asia/Ho_Chi_Minh"}})

    def test_due_after_time_once_per_day(self):
        s = pipeline.get_settings()
        before = datetime(2026, 10, 9, 0, 30, tzinfo=timezone.utc)   # 07:30 in UTC+7 -> due
        self.assertTrue(scheduler.is_due(s, before))
        store.set_meta("last_auto_run_date", "2026-10-09")
        self.assertFalse(scheduler.is_due(s, before))
        early = datetime(2026, 10, 8, 22, 0, tzinfo=timezone.utc)    # 05:00 local -> not yet
        store.set_meta("last_auto_run_date", "2026-10-07")
        self.assertFalse(scheduler.is_due(s, early))

    def test_disabled_never_due(self):
        s = pipeline.get_settings(); s["schedule"]["enabled"] = False
        self.assertFalse(scheduler.is_due(s, datetime(2026, 10, 9, 5, tzinfo=timezone.utc)))

    def test_tick_runs_sync_via_pipeline(self):
        calls = []
        orig = pipeline.run_sync
        pipeline.run_sync = lambda trigger="manual", **kw: calls.append(trigger) or {"status": "ok"}
        try:
            sch = scheduler.Scheduler(interval=3600)
            self.assertTrue(sch.tick(datetime(2026, 10, 9, 1, tzinfo=timezone.utc)))
            self.assertFalse(sch.tick(datetime(2026, 10, 9, 2, tzinfo=timezone.utc)))
            self.assertEqual(calls, ["auto"])
        finally:
            pipeline.run_sync = orig

    def test_timezone_validation(self):
        self.assertTrue(scheduler.is_valid_timezone("Asia/Ho_Chi_Minh"))
        self.assertFalse(scheduler.is_valid_timezone("Mars/Olympus"))
        with self.assertRaises(api.ApiError):
            api.put_settings({"schedule": {"time": "25:99"}})
        with self.assertRaises(api.ApiError):
            api.put_settings({"schedule": {"timezone": "Mars/Olympus"}})

    def test_fixed_timezone_fallbacks_match_supported_choices_without_warning(self):
        for zone, hours in (("Asia/Ho_Chi_Minh", 7), ("Asia/Bangkok", 7), ("Asia/Tokyo", 9), ("Asia/Singapore", 8), ("UTC", 0)):
            with self.subTest(zone=zone):
                tz, note = scheduler.resolve_timezone(zone)
                self.assertEqual(datetime(2026, 1, 1, tzinfo=tz).utcoffset(), timedelta(hours=hours))
                self.assertEqual(note, "")


class TestApi(unittest.TestCase):
    def setUp(self):
        fresh_db()

    def test_prompt_versioning(self):
        p = api.create_prompt({"title": "Tóm tắt", "body": "Tóm tắt {{text}}", "tags": ["viết"], "models": ["gpt-x"]})
        self.assertEqual(p["version"], 1)
        p2 = api.update_prompt(p["id"], {"body": "Tóm tắt ngắn {{text}}"})
        self.assertEqual(p2["version"], 2)
        self.assertEqual(len(p2["versions"]), 2)
        p3 = api.update_prompt(p["id"], {"result_note": "tốt", "rating": 5})
        self.assertEqual(p3["version"], 2)  # no body change, no new version
        self.assertEqual(api.list_prompts({"tag": ["viết"]})["items"][0]["id"], p["id"])

    def test_memory_crud_search_and_flags(self):
        m = api.create_memory({"kind": "preference", "title": "Trả lời ngắn", "body": "Ưu tiên tiếng Việt", "tags": ["style"],
                               "source": "chat 2026-10-01", "confidence": "high", "review_at": "2026-01-01"})
        self.assertEqual(m["kind"], "preference")
        updated = api.update_memory(m["id"], {"pinned": True, "stale": True})
        self.assertTrue(updated["pinned"]); self.assertTrue(updated["stale"])
        found = api.list_memories({"q": ["tiếng việt"]})["items"]
        self.assertEqual(len(found), 1)
        self.assertTrue(api.list_memories({})["items"][0]["review_due"])
        api.delete_memory(m["id"])
        self.assertEqual(api.list_memories({})["items"], [])

    def test_search_wildcards_are_literal(self):
        api.create_memory({"title": "100% chắc chắn", "body": ""})
        api.create_memory({"title": "khác hẳn", "body": ""})
        self.assertEqual(len(api.list_memories({"q": ["100%"]})["items"]), 1)

    def test_invalid_input_rejected(self):
        with self.assertRaises(api.ApiError):
            api.create_memory({"title": "x", "kind": "hack"})
        with self.assertRaises(api.ApiError):
            api.create_source({"type": "rss", "url": "javascript:alert(1)"})
        with self.assertRaises(api.ApiError):
            api.create_source({"type": "github_owner", "login": "bad name;rm"})
        with self.assertRaises(api.ApiError):
            api.create_prompt({"title": "", "body": "x"})

    def test_duplicate_merge_requires_confirmation(self):
        a = api.create_memory({"title": "Quy trình deploy", "body": "bước 1", "tags": ["ops"]})
        b = api.create_memory({"title": "quy trình  DEPLOY", "body": "bước 2", "tags": ["ci"]})
        self.assertEqual(len(api.memory_duplicates()["groups"]), 1)
        with self.assertRaises(api.ApiError):
            api.merge_memories({"keep": a["id"], "remove": [b["id"]]})
        merged = api.merge_memories({"keep": a["id"], "remove": [b["id"]], "confirm": True})
        self.assertIn("bước 2", merged["body"])
        self.assertEqual(set(merged["tags"]), {"ops", "ci"})
        self.assertEqual(len(api.list_memories({})["items"]), 1)

    def test_markdown_import(self):
        out = api.import_markdown({"kind": "memory", "text": "## Sở thích\nTrả lời ngắn\n## Quy tắc\nLuôn kiểm tra"})
        self.assertEqual(out["created"], 2)
        out = api.import_markdown({"kind": "prompt", "text": "## Dịch\nDịch sang tiếng Việt: {{text}}"})
        self.assertEqual(out["created"], 1)

    def test_export_import_roundtrip_into_fresh_db(self):
        m = api.create_memory({"title": "Ghi nhớ", "body": "nội dung", "tags": ["a"]})
        p = api.create_prompt({"title": "P", "body": "B", "models": ["m1"]})
        bundle = api.export_bundle()
        self.assertNotIn("github_token", json.dumps(bundle))
        fresh_db()
        summary = api.import_bundle(json.loads(json.dumps(bundle)))
        self.assertEqual(summary["memories_added"], 1)
        self.assertEqual(summary["prompts_added"], 1)
        self.assertEqual(api.list_memories({})["items"][0]["id"], m["id"])
        self.assertEqual(api.get_prompt(p["id"])["models"], ["m1"])
        again = api.import_bundle(json.loads(json.dumps(bundle)))
        self.assertEqual(again["memories_added"], 0)  # idempotent
        self.assertEqual(again["memories_skipped"], 1)

    def test_import_rejects_foreign_bundle(self):
        with self.assertRaises(api.ApiError):
            api.import_bundle({"app": "Other", "bundle_version": 1})

    def test_reset_needs_confirmation(self):
        api.create_memory({"title": "x"})
        with self.assertRaises(api.ApiError):
            api.reset_data({"scope": "personal", "confirm": "yes"})
        api.reset_data({"scope": "personal", "confirm": "XOA"})
        self.assertEqual(api.list_memories({})["items"], [])

    def test_dispatch_status_codes(self):
        self.assertEqual(api.dispatch("GET", "/api/hub/nope", {}, b"")[0], 404)
        self.assertEqual(api.dispatch("DELETE", "/api/hub/status", {}, b"")[0], 405)
        self.assertEqual(api.dispatch("POST", "/api/hub/prompts", {}, b"{bad")[0], 400)
        status, body = api.dispatch("POST", "/api/hub/prompts", {}, json.dumps({"title": "T", "body": "B"}).encode())
        self.assertEqual(status, 201)

    def test_resource_state_patch_validation(self):
        pipeline.save_settings({"github": {"topics": ["mcp"]}})
        pipeline.run_sync("manual", sleep=no_sleep, token="")
        rid = store.query_one("SELECT id FROM resources LIMIT 1")["id"]
        with self.assertRaises(api.ApiError):
            api.patch_resource_state(rid, {"rating": 9})
        self.assertFalse(api.get_resource(rid)["saved"])  # rejected patch must not change state


class TestHttpLayer(unittest.TestCase):
    """End-to-end through the real bridge Handler: CSRF header, Origin check, body limits."""

    @classmethod
    def setUpClass(cls):
        fresh_db()
        import importlib.util
        spec = importlib.util.spec_from_file_location("bridge", ROOT / "freellmapi_spider_bridge.py")
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _req(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_write_without_csrf_header_is_rejected(self):
        status, _ = self._req("POST", "/api/hub/prompts", {"title": "T", "body": "B"})
        self.assertEqual(status, 403)

    def test_write_with_header_succeeds(self):
        status, body = self._req("POST", "/api/hub/prompts", {"title": "T", "body": "B"}, {"X-DailyHub": "1"})
        self.assertEqual(status, 201)
        self.assertEqual(body["title"], "T")

    def test_foreign_origin_rejected(self):
        status, _ = self._req("POST", "/api/hub/memories", {"title": "x"}, {"X-DailyHub": "1", "Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_oversized_body_rejected(self):
        status, _ = self._req("POST", "/api/hub/memories", {"title": "x", "body": "y" * (3 * 1024 * 1024)}, {"X-DailyHub": "1"})
        self.assertEqual(status, 413)

    def test_import_allows_larger_backup_but_has_a_cap(self):
        status, body = self._req("POST", "/api/hub/import", {"app": "DailyHub", "bundle_version": 1, "memories": ["x" * (3 * 1024 * 1024)]}, {"X-DailyHub": "1"})
        self.assertEqual(status, 200)  # passes the size check; invalid items are skipped, none added
        self.assertEqual(body.get("memories_added"), 0)
        status, _ = self._req("POST", "/api/hub/import", {"app": "DailyHub", "bundle_version": 1, "memories": ["x" * (21 * 1024 * 1024)]}, {"X-DailyHub": "1"})
        self.assertEqual(status, 413)

    def test_status_endpoint_reports_scheduler_and_no_secrets(self):
        status, body = self._req("GET", "/api/hub/status")
        self.assertEqual(status, 200)
        self.assertIn("scheduler", body)
        self.assertIn("github_token_configured", body)
        self.assertNotIn("token", json.dumps(body).lower().replace("github_token_configured", ""))

    def test_settings_put_validates(self):
        status, _ = self._req("PUT", "/api/hub/settings", {"schedule": {"time": "99:00"}}, {"X-DailyHub": "1"})
        self.assertEqual(status, 400)

    def test_weather_endpoint(self):
        status, body = self._req("GET", "/api/hub/weather")
        self.assertEqual(status, 200)
        self.assertIn("ok", body)
        self.assertIn("location", body)

    def test_static_page_still_served(self):
        with urllib.request.urlopen(self.base + "/index.html", timeout=10) as r:
            self.assertEqual(r.status, 200)
            self.assertIn(b"DailyHub", r.read())


if __name__ == "__main__":
    unittest.main()
