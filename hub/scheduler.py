"""Daily scheduler that runs inside the local server process.

It is a background thread of the Python server, not a timer in the browser tab.
Limitation: it only runs while the server process is alive. If the machine was
off at the scheduled time, the run happens once when the server next starts
(catch-up), as long as today's run has not happened yet.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone

from . import pipeline, store

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

FIXED_OFFSETS = {
    "Asia/Ho_Chi_Minh": timezone(timedelta(hours=7)),
    "Asia/Bangkok": timezone(timedelta(hours=7)),
    "Asia/Tokyo": timezone(timedelta(hours=9)),
    "Asia/Singapore": timezone(timedelta(hours=8)),
    "UTC": timezone.utc,
}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def resolve_timezone(name: str) -> tuple[timezone, str]:
    """Return (tzinfo, note). Falls back to a fixed offset when system tz data is missing."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name), ""
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    if name in FIXED_OFFSETS:
        # These zones currently use fixed offsets without seasonal DST changes.
        # The fallback is accurate for scheduling on Windows without tzdata.
        return FIXED_OFFSETS[name], ""
    return timezone.utc, f"Múi giờ '{name}' không xác định được; đang dùng UTC."


def is_valid_timezone(name: str) -> bool:
    if ZoneInfo is not None:
        try:
            ZoneInfo(name)
            return True
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    return name in FIXED_OFFSETS


def scheduled_today(local_now: datetime, hhmm: str) -> datetime:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def is_due(settings: dict, now_utc: datetime) -> bool:
    sch = settings["schedule"]
    if not sch.get("enabled"):
        return False
    tz, _note = resolve_timezone(sch["timezone"])
    local = now_utc.astimezone(tz)
    if local < scheduled_today(local, sch["time"]):
        return False
    return store.get_meta("last_auto_run_date") != local.date().isoformat()


def next_run(settings: dict, now_utc: datetime) -> datetime | None:
    sch = settings["schedule"]
    if not sch.get("enabled"):
        return None
    tz, _ = resolve_timezone(sch["timezone"])
    local = now_utc.astimezone(tz)
    target = scheduled_today(local, sch["time"])
    if local >= target:
        if store.get_meta("last_auto_run_date") == local.date().isoformat():
            target = target + timedelta(days=1)
        # else: due now, next_run is "now" (catch-up)
        else:
            return local
    return target


class Scheduler(threading.Thread):
    def __init__(self, interval: float = 30.0):
        super().__init__(name="dailyhub-scheduler", daemon=True)
        self.interval = interval
        self._stop_event = threading.Event()

    def tick(self, now_utc: datetime | None = None) -> bool:
        """Run one sync if due. Returns True when a run was started."""
        now_utc = now_utc or datetime.now(timezone.utc)
        settings = pipeline.get_settings()
        if not is_due(settings, now_utc):
            return False
        tz, _ = resolve_timezone(settings["schedule"]["timezone"])
        # Mark first so a failing run does not retry in a tight loop; per-request retries live in the pipeline.
        store.set_meta("last_auto_run_date", now_utc.astimezone(tz).date().isoformat())
        pipeline.run_sync("auto")
        from . import backup  # local import keeps module load order simple
        backup.after_auto_sync()
        return True

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - keep the scheduler alive
                pass
            self._stop_event.wait(self.interval)

    def stop(self) -> None:
        self._stop_event.set()

    def status(self) -> dict:
        settings = pipeline.get_settings()
        sch = settings["schedule"]
        tz, note = resolve_timezone(sch["timezone"])
        nxt = next_run(settings, datetime.now(timezone.utc))
        return {
            "enabled": bool(sch.get("enabled")), "ai_enabled": bool(sch.get("ai_enabled", True)),
            "time": sch["time"], "timezone": sch["timezone"],
            "timezone_note": note, "running": self.is_alive(),
            "next_run": nxt.isoformat(timespec="minutes") if nxt else None,
            "last_auto_run_date": store.get_meta("last_auto_run_date"),
            "limitation": "Lịch chỉ chạy khi tiến trình server (freellmapi_spider_bridge.py) đang mở. Nếu máy tắt đúng giờ, lần chạy sẽ diễn ra khi server khởi động lại trong ngày.",
        }


_instance: Scheduler | None = None


def start_default() -> Scheduler:
    """Start the process-wide scheduler once (called from the server's main block)."""
    global _instance
    if _instance is None:
        _instance = Scheduler()
        _instance.start()
    return _instance


def current() -> Scheduler | None:
    return _instance
