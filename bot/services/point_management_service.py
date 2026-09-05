from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


BASE_POINTS_PER_FIVE_MINUTES = 10
CONFIG_PATH = Path(
    os.getenv(
        "MESOZOIC_POINT_MANAGEMENT_CONFIG",
        str(Path(__file__).resolve().parents[2] / "Config" / "point-management.json"),
    )
)


def _default_config() -> dict:
    return {
        "schemaVersion": 1,
        "basePointsPerFiveMinutes": BASE_POINTS_PER_FIVE_MINUTES,
        "boost": None,
        "supporterRoles": [],
    }


def load_point_management_config() -> dict:
    if not CONFIG_PATH.is_file():
        return _default_config()
    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return _default_config()
    if not isinstance(loaded, dict):
        return _default_config()
    config = _default_config()
    config.update(loaded)
    return config


def points_per_five_minutes(
    discord_role_ids: set[str] | None = None,
    now: datetime | None = None,
) -> int:
    config = load_point_management_config()
    base = max(1, int(config.get("basePointsPerFiveMinutes") or BASE_POINTS_PER_FIVE_MINUTES))
    role_ids = {str(value) for value in (discord_role_ids or set())}
    configured_rates = [base]
    for entry in config.get("supporterRoles") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("roleId") or "") not in role_ids:
            continue
        try:
            configured_rates.append(max(1, int(entry.get("pointsPerFiveMinutes"))))
        except (TypeError, ValueError):
            continue
    reward = max(configured_rates)

    boost = config.get("boost")
    if isinstance(boost, dict):
        try:
            ends_at = datetime.fromisoformat(str(boost.get("endsAtUtc") or "").replace("Z", "+00:00"))
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            if current < ends_at.astimezone(timezone.utc):
                reward = round(reward * float(boost.get("multiplier") or 1))
        except (TypeError, ValueError, OverflowError):
            pass
    return max(1, int(reward))
