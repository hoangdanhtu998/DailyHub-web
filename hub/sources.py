"""Fetchers for official, public sources: GitHub REST API and RSS/Atom feeds.

Rules: timeouts on every request, limited retries with backoff for 429/5xx/network
errors, a per-run request budget, and no scraping of pages that need login.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urljoin, urlparse

from .normalize import canonical_url, clean_text, safe_url

USER_AGENT = "DailyHub/1.0 (personal resource hub; +https://github.com)"
GITHUB_API = os.environ.get("DAILYHUB_GITHUB_API", "https://api.github.com").rstrip("/")
MAX_BYTES = 2_000_000
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ARTICLE_IMAGE_HOSTS = ("vnexpress.net", "tuoitre.vn", "thanhnien.vn", "dantri.com.vn", "vietnamnet.vn",
                       "techcrunch.com", "theverge.com", "dev.to", "news.ycombinator.com", "mgg.cx", "deal1k.vn")


def _open_url(req, timeout: float):
    # Codex's restricted Windows shell may inject a sink proxy at 127.0.0.1:9.
    # It is not a usable HTTP proxy; bypass only that exact loopback endpoint.
    for scheme, proxy in urllib.request.getproxies().items():
        if scheme not in ("http", "https", "all"):
            continue
        try:
            parsed = urlparse(proxy if "://" in proxy else "http://" + proxy)
            if parsed.hostname in ("127.0.0.1", "::1", "localhost") and parsed.port == 9:
                return _DIRECT_OPENER.open(req, timeout=timeout)
        except ValueError:
            continue
    return urllib.request.urlopen(req, timeout=timeout)


class SourceError(Exception):
    """A source failed; the message is safe to show in the UI (no secrets)."""


class RateLimited(SourceError):
    pass


class BudgetExhausted(SourceError):
    pass


def _header(headers, name: str) -> str | None:
    try:
        return headers.get(name)
    except AttributeError:
        return None


def http_get(url: str, headers: dict | None = None, *, timeout: float = 15, retries: int = 2,
             backoff: float = 2.0, sleep=time.sleep):
    """GET with timeout and bounded retries. Returns (status, headers, body_bytes).
    A 404 is returned (not raised) so callers can treat it as 'not found'."""
    attempt = 0
    while True:
        attempt += 1
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5", **(headers or {})})
        try:
            with _open_url(req, timeout=timeout) as resp:
                body = resp.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise SourceError("phản hồi quá lớn (vượt 2 MB)")
                return resp.status, resp.headers, body
        except urllib.error.HTTPError as err:
            status, hdrs = err.code, err.headers
            if status == 404:
                return 404, hdrs, b""
            if status == 403 and _header(hdrs, "X-RateLimit-Remaining") == "0":
                reset = _header(hdrs, "X-RateLimit-Reset")
                raise RateLimited(f"giới hạn lượt gọi API (reset lúc {reset or 'không rõ'})")
            if status in (429, 500, 502, 503, 504) and attempt <= retries:
                wait = _header(hdrs, "Retry-After")
                delay = min(float(wait), 60) if wait and wait.isdigit() else min(backoff * (2 ** (attempt - 1)), 60)
                sleep(delay)
                continue
            raise SourceError(f"HTTP {status}")
        except (urllib.error.URLError, socket.timeout, TimeoutError):
            if attempt <= retries:
                sleep(min(backoff * (2 ** (attempt - 1)), 60))
                continue
            raise SourceError("không kết nối được (timeout hoặc lỗi mạng)")


class GitHub:
    """Thin GitHub REST client with a per-run request budget."""

    def __init__(self, token: str, budget: int, retries: int = 2, backoff: float = 2.0, sleep=time.sleep):
        self.token = token
        self.budget = budget
        self.calls = 0
        self.retries = retries
        self.backoff = backoff
        self.sleep = sleep

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def get(self, path: str, params: dict | None = None):
        """Returns parsed JSON, or None on 404. Raises SourceError/RateLimited/BudgetExhausted."""
        if self.calls >= self.budget:
            raise BudgetExhausted("đã dùng hết hạn mức request của lần chạy này")
        url = GITHUB_API + path + (("?" + urlencode(params)) if params else "")
        self.calls += 1
        status, _hdrs, body = http_get(url, self._headers(), retries=self.retries, backoff=self.backoff, sleep=self.sleep)
        if status == 404:
            return None
        return json.loads(body.decode("utf-8"))

    def search_topic(self, topic: str, lookback_days: int, limit: int) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        data = self.get("/search/repositories", {"q": f"topic:{topic} pushed:>={since}", "sort": "stars", "order": "desc", "per_page": min(limit, 100)})
        return (data or {}).get("items", [])

    def owner_repos(self, login: str, limit: int) -> list[dict]:
        data = self.get(f"/users/{quote(login)}/repos", {"sort": "pushed", "per_page": min(limit, 100), "type": "owner"})
        if data is None:
            data = self.get(f"/orgs/{quote(login)}/repos", {"sort": "pushed", "per_page": min(limit, 100), "type": "public"}) or []
        return data

    def repo(self, full_name: str):
        return self.get(f"/repos/{full_name}")

    def latest_release(self, full_name: str):
        data = self.get(f"/repos/{full_name}/releases/latest")
        if not data:
            return None
        return {"tag": clean_text(data.get("tag_name") or data.get("name") or "", 60),
                "published_at": data.get("published_at") or data.get("created_at") or ""}


def normalize_repo(item: dict, *, source_id: str, topic_kinds: dict, classify) -> dict | None:
    """Map a GitHub repo object to our resource fields. Returns None if unusable."""
    url = safe_url(item.get("html_url"))
    full = clean_text(item.get("full_name"), 200)
    if not url or not full:
        return None
    topics = [clean_text(t, 50) for t in (item.get("topics") or []) if isinstance(t, str)][:20]
    lic = item.get("license") or {}
    homepage = safe_url(item.get("homepage") or "")
    stars = int(item.get("stargazers_count") or 0)
    return {
        "url": canonical_url(url), "name": full, "kind": classify(topics, topic_kinds),
        "author": clean_text((item.get("owner") or {}).get("login"), 80),
        "description": clean_text(item.get("description"), 400), "topics": topics,
        "source_id": source_id, "pushed_at": item.get("pushed_at") or "",
        "archived": bool(item.get("archived")), "license": clean_text(lic.get("spdx_id") or lic.get("name") or "", 60) if lic.get("spdx_id") != "NOASSERTION" else "",
        "homepage": homepage, "stars": stars, "forks": int(item.get("forks_count") or 0),
        "open_issues": int(item.get("open_issues_count") or 0),
        "status": "archived" if item.get("archived") else "active",
        "install_hint": "",
    }


def fetch_github_topic(gh: GitHub, *, topic: str, source_id: str, topic_kinds: dict, classify,
                       lookback_days: int, limit: int, release_checks: int, now_iso: str) -> list[dict]:
    items = gh.search_topic(topic, lookback_days, limit)
    out = []
    for i, item in enumerate(items):
        repo = normalize_repo(item, source_id=source_id, topic_kinds=topic_kinds, classify=classify)
        if not repo:
            continue
        repo["release_checked"] = False
        if i < release_checks:
            try:
                repo["latest_release"] = gh.latest_release(item["full_name"])
                repo["release_checked"] = True
            except BudgetExhausted:
                break
            except SourceError:
                pass  # release unknown; scored as "chưa xác minh"
        repo["metrics_at"] = now_iso
        out.append(repo)
    return out


def fetch_github_owner(gh: GitHub, *, login: str, source_id: str, topic_kinds: dict, classify,
                       limit: int, now_iso: str) -> list[dict]:
    out = []
    for item in gh.owner_repos(login, limit):
        if item.get("fork"):
            continue
        repo = normalize_repo(item, source_id=source_id, topic_kinds=topic_kinds, classify=classify)
        if repo:
            repo["release_checked"] = False
            repo["metrics_at"] = now_iso
            out.append(repo)
    return out


def parse_feed(xml_bytes: bytes, source_id: str, source_name: str, limit: int = 50) -> list[dict]:
    """RSS 2.0 or Atom. Returns article records (kind 'article', no score)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as err:
        raise SourceError("feed không phải RSS/Atom hợp lệ") from err
    tag = root.tag.split("}")[-1].lower()
    items = []
    if tag == "rss":
        entries = root.findall("./channel/item")
    elif tag == "feed":
        entries = root.findall("{http://www.w3.org/2005/Atom}entry") or root.findall("entry")
    else:
        raise SourceError("feed không có phần tử item/entry")

    def child_text(el, *names):
        for name in names:
            for c in el:
                if c.tag.split("}")[-1].lower() == name and c.text:
                    return c.text
        return ""

    for el in entries[:limit]:
        link = child_text(el, "link")
        if not link:
            for c in el:
                if c.tag.split("}")[-1].lower() == "link" and c.get("href"):
                    link = c.get("href")
                    break
        url = safe_url(link)
        title = clean_text(child_text(el, "title"), 200)
        if not url or not title:
            continue
        image = ""
        description_html = child_text(el, "description", "summary", "content")
        for element in el.iter():
            tag_name = element.tag.split("}")[-1].lower()
            candidate = element.get("url") or element.get("href") if tag_name in ("enclosure", "thumbnail", "content") else ""
            if candidate and safe_url(candidate):
                image = safe_url(candidate)
                break
        if not image and description_html:
            parser = _ArticleImageParser(url)
            try:
                parser.feed(description_html)
                image = next((candidate for _score, candidate in sorted(parser.candidates, reverse=True)), "")
            except (ValueError, AssertionError):
                pass
        items.append({
            "url": canonical_url(url), "name": title, "kind": "article",
            "author": clean_text(child_text(el, "creator", "author", "name") or source_name, 80),
            "description": clean_text(description_html, 1600),
            "topics": [], "source_id": source_id, "pushed_at": clean_text(child_text(el, "pubdate", "published", "updated"), 60),
            "archived": False, "license": "", "homepage": "", "stars": None, "forks": None, "open_issues": None,
            "status": "active", "install_hint": "", "release_checked": False, "latest_release": None, "image": image or None,
        })
    return items


def check_public_host(url: str) -> None:
    """Refuse feeds that resolve to loopback/private/link-local addresses (SSRF guard).
    Tests can set DAILYHUB_ALLOW_LOCAL_FEEDS=1 to use a local fixture server."""
    if os.environ.get("DAILYHUB_ALLOW_LOCAL_FEEDS") == "1":
        return
    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as err:
        raise SourceError("không phân giải được tên miền của nguồn") from err
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise SourceError("nguồn trỏ tới địa chỉ nội bộ hoặc localhost, đã bị chặn")


def _validate_article_page(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        raise SourceError("URL bài viết không hợp lệ") from None
    if (parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443)
            or not any(host == domain or host.endswith("." + domain) for domain in ARTICLE_IMAGE_HOSTS)):
        raise SourceError("chỉ hỗ trợ bài viết từ các nguồn tin của DailyHub")
    check_public_host(url)


class _ArticleRedirectGuard(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        _validate_article_page(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


class _ArticleImageParser(HTMLParser):
    _skip = re.compile(r"logo|avatar|icon|sprite|placeholder|tracking|pixel|favicon|badge|emoji", re.I)

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.container_depth = 0
        self.candidates: list[tuple[int, str]] = []

    def _add(self, raw: str, score: int, hint: str = "", width: str = "", height: str = ""):
        raw = (raw or "").strip()
        if not raw or raw.startswith(("data:", "javascript:")) or self._skip.search(hint):
            return
        try:
            w, h = int(width or 0), int(height or 0)
        except ValueError:
            w = h = 0
        if (w and w < 120) or (h and h < 80):
            return
        resolved = safe_url(urljoin(self.base_url, raw))
        if resolved:
            self.candidates.append((score + (15 if w >= 400 and h >= 220 else 0), resolved))

    def handle_starttag(self, tag, attrs):
        attrs = {key.lower(): (value or "") for key, value in attrs}
        if tag in ("article", "main"):
            self.container_depth += 1
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
                self._add(attrs.get("content", ""), 220 if key.startswith("og:") else 205,
                          attrs.get("content", ""), attrs.get("width", ""), attrs.get("height", ""))
        if tag == "link" and "image_src" in attrs.get("rel", "").lower():
            self._add(attrs.get("href", ""), 180, attrs.get("rel", ""))
        if tag == "img":
            hint = " ".join(attrs.get(k, "") for k in ("alt", "title", "class", "id", "role"))
            score = 70 + (85 if self.container_depth else 0)
            if re.search(r"hero|featured|cover|article|content|main", hint, re.I):
                score += 25
            for key in ("data-src", "data-original", "data-lazy-src", "data-original-src", "src"):
                if attrs.get(key):
                    self._add(attrs[key], score, hint, attrs.get("width", ""), attrs.get("height", ""))
                    break
            srcset = attrs.get("data-srcset") or attrs.get("srcset", "")
            if srcset:
                choices = []
                for part in srcset.split(","):
                    bits = part.strip().split()
                    if bits:
                        amount = int(re.sub(r"\D", "", bits[1])) if len(bits) > 1 and re.search(r"\d", bits[1]) else 0
                        choices.append((amount, bits[0]))
                if choices:
                    self._add(max(choices)[1], score + 5, hint)

    def handle_endtag(self, tag):
        if tag in ("article", "main") and self.container_depth:
            self.container_depth -= 1


def fetch_article_image(url: str) -> str | None:
    """Fetch one allowlisted article page and choose a likely editorial image."""
    if not safe_url(url):
        raise SourceError("URL bài viết không hợp lệ")
    _validate_article_page(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _ArticleRedirectGuard())
    try:
        with opener.open(request, timeout=4) as response:
            final_url = response.geturl()
            _validate_article_page(final_url)
            if "html" not in response.headers.get("Content-Type", "text/html").lower():
                return None
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                return None
    except (urllib.error.URLError, TimeoutError, socket.timeout, SourceError):
        return None
    parser = _ArticleImageParser(final_url)
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
    except (ValueError, AssertionError):
        return None
    return next((image for _score, image in sorted(parser.candidates, reverse=True)), None)


def fetch_feed(url: str, *, source_id: str, source_name: str, retries: int = 2, backoff: float = 2.0, sleep=time.sleep) -> list[dict]:
    if not safe_url(url):
        raise SourceError("URL nguồn không hợp lệ (chỉ chấp nhận http/https)")
    check_public_host(url)
    status, _h, body = http_get(url, retries=retries, backoff=backoff, sleep=sleep)
    if status == 404:
        raise SourceError("feed trả về 404 (nguồn có thể đã ngừng hoạt động)")
    return parse_feed(body, source_id, source_name)
