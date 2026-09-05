from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path


BOT_ROOT = Path(__file__).resolve().parents[2]
PANEL_STATE_PATH = BOT_ROOT / "weather_forecast_panel.json"

WEATHER_LABELS = {
    "clear": "Clear Sky",
    "cloudy": "Cloudy",
    "foggy": "Foggy",
    "light_rain": "Light Rain",
    "rain": "Rain",
    "rain_extension": "Rain Extension",
}

NEXT_ROLLS = {
    "clear": (("Clear Sky", "50%"), ("Cloudy", "50%")),
    "cloudy_decision": (
        ("Clear Sky", "33.3%"),
        ("Foggy", "33.3%"),
        ("Light Rain", "33.3%"),
    ),
    "foggy": (("Cloudy", "100%"),),
    "cloudy_recovery": (("Clear Sky", "100%"),),
    "light_decision": (("Cloudy", "33.3%"), ("Rain", "66.7%")),
    "rain_decision": (("Rain Extension", "50%"), ("Light Rain", "50%")),
    "rain_extension": (("Light Rain", "100%"),),
    "light_recovery": (("Cloudy", "100%"),),
}


def _default_status_path() -> Path:
    configured = os.getenv("MESOZOIC_WEATHER_STATUS_PATH")
    if configured:
        return Path(configured).expanduser()

    candidates = (
        Path(
            r"C:\TheMesozoic\TheIsle\Binaries\Win64\Mods"
            r"\MesozoicWeatherController\Saved\weather_controller_status.json"
        ),
        Path(
            r"D:\TheMesozoic\TheIsle\Binaries\Win64\Mods"
            r"\MesozoicWeatherController\Saved\weather_controller_status.json"
        ),
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


WEATHER_STATUS_PATH = _default_status_path()


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_panel_state(channel_id: int, message_id: int) -> None:
    payload = json.dumps(
        {"channel_id": int(channel_id), "message_id": int(message_id)},
        separators=(",", ":"),
    )
    temporary = PANEL_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(PANEL_STATE_PATH)


def forecast_from_status(status: dict | None) -> dict:
    if not status:
        return {
            "available": False,
            "weather_key": "unknown",
            "weather": "Unavailable",
            "phase": "startup",
            "next_action_at": 0,
            "options": (),
        }

    weather_key = str(status.get("weather") or "unknown")
    phase = str(status.get("phase") or "startup")
    try:
        next_action_at = max(0, int(float(status.get("nextActionAt") or 0)))
    except (TypeError, ValueError):
        next_action_at = 0

    return {
        "available": True,
        "weather_key": weather_key,
        "weather": WEATHER_LABELS.get(weather_key, weather_key.replace("_", " ").title()),
        "phase": phase,
        "next_action_at": next_action_at,
        "options": NEXT_ROLLS.get(phase, ()),
    }


async def get_weather_forecast() -> dict:
    status = await asyncio.to_thread(_read_json, WEATHER_STATUS_PATH)
    return forecast_from_status(status)


async def get_panel_location() -> tuple[int, int] | None:
    state = await asyncio.to_thread(_read_json, PANEL_STATE_PATH)
    if not state:
        return None
    try:
        return int(state["channel_id"]), int(state["message_id"])
    except (KeyError, TypeError, ValueError):
        return None


async def save_panel_location(channel_id: int, message_id: int) -> None:
    await asyncio.to_thread(_write_panel_state, channel_id, message_id)
