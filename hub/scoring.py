"""Transparent quality score for repositories.

Every component has a fixed maximum and a human-readable reason, so the score
can be explained in the UI. Stars are only one of six components. Values that
were not verified are scored 0 and labelled "chưa xác minh" instead of guessed.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

WEIGHTS = {"activity": 30, "release": 15, "adoption": 20, "community": 10, "health": 10, "docs": 10, "license": 5}
# Sum of WEIGHTS = 100.


def _days_since(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 86400


def score_repo(repo: dict, now: datetime | None = None) -> tuple[int | None, dict]:
    """repo: normalized fields (stars, forks, pushed_at, archived, license, homepage,
    description, latest_release {tag, published_at} or None, release_checked bool).
    Returns (score, breakdown). score is None when the repo is missing."""
    now = now or datetime.now(timezone.utc)
    breakdown: dict[str, dict] = {}

    if repo.get("status") == "missing":
        return None, {"status": {"points": None, "reason": "Repo không còn truy cập được (404)."}}

    # Activity: time since last push.
    age = _days_since(repo.get("pushed_at"), now)
    if age is None:
        pts, why = 0, "chưa xác minh ngày cập nhật"
    elif age <= 30:
        pts, why = WEIGHTS["activity"], f"cập nhật {int(age)} ngày trước"
    elif age <= 90:
        pts, why = 20, f"cập nhật {int(age)} ngày trước"
    elif age <= 365:
        pts, why = 8, f"cập nhật {int(age // 30)} tháng trước"
    else:
        pts, why = 0, "không cập nhật hơn 1 năm"
    breakdown["activity"] = {"points": pts, "max": WEIGHTS["activity"], "reason": why}

    # Release: existence and recency of the latest release.
    rel = repo.get("latest_release")
    if not repo.get("release_checked"):
        pts, why = 0, "chưa xác minh release"
    elif not rel:
        pts, why = 0, "chưa có release chính thức"
    else:
        rage = _days_since(rel.get("published_at"), now)
        if rage is not None and rage <= 180:
            pts, why = WEIGHTS["release"], f"release {rel.get('tag','')} trong 6 tháng qua"
        else:
            pts, why = 7, f"release {rel.get('tag','')} đã cũ"
    breakdown["release"] = {"points": pts, "max": WEIGHTS["release"], "reason": why}

    # Adoption: log scale so 10k stars does not dominate everything.
    stars = int(repo.get("stars") or 0)
    pts = min(WEIGHTS["adoption"], round(math.log10(stars + 1) * 3.3))  # ~1k stars ≈ 10, ~1M ≈ 20
    breakdown["adoption"] = {"points": pts, "max": WEIGHTS["adoption"], "reason": f"{stars:,} stars (thang log)"}

    # Community: forks, log scale.
    forks = int(repo.get("forks") or 0)
    pts = min(WEIGHTS["community"], round(math.log10(forks + 1) * 2.5))  # ~100 forks ≈ 5, ~10k ≈ 10
    breakdown["community"] = {"points": pts, "max": WEIGHTS["community"], "reason": f"{forks:,} forks (thang log)"}

    # Health: not archived.
    if repo.get("archived"):
        pts, why = 0, "đã lưu trữ (archived), không còn bảo trì"
    else:
        pts, why = WEIGHTS["health"], "chưa lưu trữ"
    breakdown["health"] = {"points": pts, "max": WEIGHTS["health"], "reason": why}

    # Docs: description and homepage present (README is not fetched, so this is a partial signal).
    docs = 0
    if (repo.get("description") or "").strip():
        docs += 5
    if (repo.get("homepage") or "").strip():
        docs += 5
    breakdown["docs"] = {"points": docs, "max": WEIGHTS["docs"], "reason": "có mô tả/homepage (chưa đọc README)"}

    lic = (repo.get("license") or "").strip()
    breakdown["license"] = {"points": WEIGHTS["license"] if lic else 0, "max": WEIGHTS["license"],
                            "reason": f"giấy phép {lic}" if lic else "không có giấy phép rõ ràng"}

    total = sum(v["points"] for v in breakdown.values())
    if repo.get("archived"):
        total = min(total, 20)  # an archived repo cannot rank as "hot"
    return int(total), breakdown
