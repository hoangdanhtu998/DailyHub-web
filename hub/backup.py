"""Automatic backups of the personal library (memories, prompts, resource state, sources, settings).

Files go to <data>/backups/dailyhub-YYYYMMDD-HHMMSS.json. Writes are atomic
(temp file + rename). Only the newest N files are kept (setting backup.keep).
The GitHub token is never part of a backup.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import pipeline, store

NAME_RE = re.compile(r"^dailyhub-\d{8}-\d{6}(?:-\d{2})?\.json$")
MAX_BACKUP_BYTES = 50 * 1024 * 1024


def backup_dir() -> Path:
    path = store.DATA_DIR / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _valid_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise ValueError("tên bản sao lưu không hợp lệ")
    return name


def create_backup(reason: str = "manual") -> dict:
    from . import api  # imported here to avoid a circular import at module load
    bundle = api.export_bundle()
    bundle["backup_reason"] = reason
    text = json.dumps(bundle, ensure_ascii=False, indent=2)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    folder = backup_dir()
    name = f"dailyhub-{stamp}.json"
    counter = 1
    while (folder / name).exists():  # two backups in the same second
        name = f"dailyhub-{stamp}-{counter:02d}.json"
        counter += 1
    tmp = folder / (name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, folder / name)
    prune(keep=pipeline.get_settings()["backup"]["keep"])
    return _info(folder / name)


def _info(path: Path) -> dict:
    st = path.stat()
    return {"name": path.name, "size": st.st_size,
            "created": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")}


def list_backups() -> list[dict]:
    folder = backup_dir()
    files = sorted((p for p in folder.glob("dailyhub-*.json") if NAME_RE.match(p.name)), key=lambda p: p.name, reverse=True)
    return [_info(p) for p in files]


def read_backup(name: str) -> dict:
    name = _valid_name(name)
    path = backup_dir() / name
    if not path.is_file() or path.stat().st_size > MAX_BACKUP_BYTES:
        raise FileNotFoundError(name)
    return json.loads(path.read_text(encoding="utf-8"))


def delete_backup(name: str) -> None:
    name = _valid_name(name)
    path = backup_dir() / name
    if not path.is_file():
        raise FileNotFoundError(name)
    path.unlink()


def prune(keep: int) -> int:
    """Delete the oldest backups beyond `keep`. Returns how many were removed."""
    files = list_backups()
    removed = 0
    for info in files[max(keep, 1):]:
        (backup_dir() / info["name"]).unlink(missing_ok=True)
        removed += 1
    return removed


def after_auto_sync() -> dict | None:
    """Called by the scheduler after each automatic daily sync. Never raises."""
    try:
        if not pipeline.get_settings()["backup"]["enabled"]:
            return None
        return create_backup("auto")
    except Exception:  # noqa: BLE001 - a failed backup must not break the scheduler
        return None
