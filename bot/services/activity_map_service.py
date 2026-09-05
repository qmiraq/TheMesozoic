from __future__ import annotations

import json
import math
import os
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import discord
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFont

from bot.services.dino_storage_service import MOD_ROOT


ACTIVITY_MAP_CHANNEL_ID = 1534857082300268594
ACTIVITY_SNAPSHOT_PATH = Path(
    os.getenv("DINO_ACTIVITY_SNAPSHOT_PATH", str(MOD_ROOT / "activity_snapshot.json"))
)
ACTIVITY_HISTORY_PATH = Path(
    os.getenv("DINO_ACTIVITY_HISTORY_PATH", str(MOD_ROOT / "activity_history.json"))
)
ACTIVITY_MESSAGE_PATH = Path(
    os.getenv("DINO_ACTIVITY_MESSAGE_PATH", str(MOD_ROOT / "activity_message.json"))
)
ACTIVITY_SEGMENTED_MESSAGE_PATH = Path(
    os.getenv(
        "DINO_ACTIVITY_SEGMENTED_MESSAGE_PATH",
        str(MOD_ROOT / "activity_segmented_message.json"),
    )
)
ACTIVITY_MAP_ASSET = (
    Path(__file__).resolve().parents[1] / "assets" / "V8-0-Gateway1600BW.png"
)
ACTIVITY_REGION_MASK = (
    Path(__file__).resolve().parents[1] / "assets" / "Gateway-region-mask.png"
)
ACTIVITY_CALIBRATION_PATH = Path(
    os.getenv(
        "MESOZOIC_GATEWAY_CALIBRATION_PATH",
        str(MOD_ROOT.parent / "MesozoicAIProbe" / "Saved" / "gateway-calibration.json"),
    )
)

HISTORY_SECONDS = 15 * 60
SNAPSHOT_STALE_SECONDS = 120
RENDER_SIZE = 1024
SEGMENTED_MIN_ONLINE_PLAYERS = 10

# Gateway calibration. RaidAtlas' complete square map represents these game-world
# bounds. The axes are intentionally crossed: world Y runs left-to-right and
# world X runs top-to-bottom on the supplied map.
WORLD_Y_MIN = -505_000.0
WORLD_Y_SPAN = 1_112_000.0
WORLD_X_MIN = -607_000.0
WORLD_X_SPAN = 1_116_000.0

# Live pawn locations use Unreal actor axes, while the Gateway map bounds above
# use the game's HUD/map coordinate system.  The HUD X axis is actor Y with the
# Gateway world-origin correction; HUD Y is actor X.
ACTOR_TO_HUD_X_OFFSET = -40_000.0
ACTOR_TO_HUD_Y_OFFSET = 0.0
MAP_PIXEL_X_OFFSET = 5.0
MAP_PIXEL_Y_OFFSET = 5.0
CALIBRATION_IMAGE_SIZE = 1600

_DEFAULT_GATEWAY_TRANSFORM = {
    "pixelXFromWorldX": 0.0,
    "pixelXFromWorldY": (CALIBRATION_IMAGE_SIZE - 1) / WORLD_Y_SPAN,
    "pixelXOffset": 5.0
    + (ACTOR_TO_HUD_X_OFFSET - WORLD_Y_MIN)
    * ((CALIBRATION_IMAGE_SIZE - 1) / WORLD_Y_SPAN),
    "pixelYFromWorldX": (CALIBRATION_IMAGE_SIZE - 1) / WORLD_X_SPAN,
    "pixelYFromWorldY": 0.0,
    "pixelYOffset": 5.0
    + (ACTOR_TO_HUD_Y_OFFSET - WORLD_X_MIN)
    * ((CALIBRATION_IMAGE_SIZE - 1) / WORLD_X_SPAN),
}
_calibration_cache_mtime_ns: int | None = None
_calibration_cache_transform: dict[str, float] = dict(_DEFAULT_GATEWAY_TRANSFORM)

CARNIVORES = {
    "allosaurus",
    "austroraptor",
    "carnotaurus",
    "ceratosaurus",
    "deinosuchus",
    "dilophosaurus",
    "herrerasaurus",
    "omniraptor",
    "pteranodon",
    "troodon",
    "tyrannosaurus",
}
HERBIVORES = {
    "diabloceratops",
    "dryosaurus",
    "hypsilophodon",
    "kentrosaurus",
    "maiasaura",
    "pachycephalosaurus",
    "stegosaurus",
    "tenontosaurus",
    "triceratops",
}
OMNIVORES = {"beipiaosaurus", "gallimimus"}

REGIONS = (
    ("South Plains", (255, 0, 0)),
    ("Mangrove", (0, 202, 255)),
    ("Swamps", (33, 209, 96)),
    ("West Rail Jungle", (209, 33, 193)),
    ("West Rail", (43, 11, 246)),
    ("The Dome", (255, 243, 0)),
    ("Highlands", (255, 111, 0)),
    ("Central Jungle", (30, 65, 23)),
    ("River Delta", (18, 91, 182)),
    ("Water Access/Northern Jungle", (130, 19, 245)),
    ("North Lake", (159, 206, 29)),
    ("East Lake", (76, 19, 19)),
    ("Delta Jungle", (89, 74, 147)),
)
REGION_BY_COLOR = {color: name for name, color in REGIONS}

# Counts are interpolated between these stops, so every added player changes
# the shade instead of painting an entire range with one flat color.
SEGMENTED_HEAT_STOPS = (
    (0, (68, 70, 76, 0)),
    (1, (22, 55, 180, 88)),
    (3, (30, 95, 255, 100)),
    (6, (0, 210, 235, 108)),
    (8, (35, 220, 85, 115)),
    (10, (245, 220, 0, 122)),
    (12, (255, 115, 0, 130)),
    (15, (255, 35, 0, 140)),
)


def _atomic_json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def _clean_player(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    species = str(value.get("species") or "").strip()
    try:
        x = float(value.get("x"))
        y = float(value.get("y"))
    except (TypeError, ValueError):
        return None
    if not species or not math.isfinite(x) or not math.isfinite(y):
        return None
    return {"species": species, "x": x, "y": y}


def load_live_snapshot() -> dict[str, Any] | None:
    value = _read_json(ACTIVITY_SNAPSHOT_PATH)
    if not isinstance(value, dict):
        return None
    try:
        updated_at = int(value.get("updatedAt"))
    except (TypeError, ValueError):
        return None
    players = []
    for item in value.get("players") or []:
        player = _clean_player(item)
        if player is not None:
            players.append(player)
    return {"updatedAt": updated_at, "players": players}


def load_history(now: float) -> list[dict[str, Any]]:
    value = _read_json(ACTIVITY_HISTORY_PATH)
    if not isinstance(value, list):
        return []
    cutoff = now - HISTORY_SECONDS
    history: list[dict[str, Any]] = []
    for sample in value:
        if not isinstance(sample, dict):
            continue
        try:
            timestamp = float(sample.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if timestamp < cutoff or timestamp > now + 60:
            continue
        players = []
        for item in sample.get("players") or []:
            player = _clean_player(item)
            if player is not None:
                players.append(player)
        history.append({"timestamp": timestamp, "players": players})
    history.sort(key=lambda item: item["timestamp"])
    return history


def save_history(history: list[dict[str, Any]]) -> None:
    _atomic_json_write(ACTIVITY_HISTORY_PATH, history)


def load_message_id() -> int | None:
    value = _read_json(ACTIVITY_MESSAGE_PATH)
    if not isinstance(value, dict):
        return None
    try:
        message_id = int(value.get("messageId"))
    except (TypeError, ValueError):
        return None
    return message_id if message_id > 0 else None


def save_message_id(message_id: int) -> None:
    _atomic_json_write(
        ACTIVITY_MESSAGE_PATH,
        {"channelId": ACTIVITY_MAP_CHANNEL_ID, "messageId": int(message_id)},
    )


def load_segmented_message_id() -> int | None:
    value = _read_json(ACTIVITY_SEGMENTED_MESSAGE_PATH)
    if not isinstance(value, dict):
        return None
    try:
        message_id = int(value.get("messageId"))
    except (TypeError, ValueError):
        return None
    return message_id if message_id > 0 else None


def save_segmented_message_id(message_id: int) -> None:
    _atomic_json_write(
        ACTIVITY_SEGMENTED_MESSAGE_PATH,
        {"channelId": ACTIVITY_MAP_CHANNEL_ID, "messageId": int(message_id)},
    )


def record_snapshot(
    history: list[dict[str, Any]], snapshot: dict[str, Any], now: float
) -> list[dict[str, Any]]:
    source_time = float(snapshot.get("updatedAt") or 0)
    if source_time <= 0:
        return history
    if history and float(history[-1].get("sourceTime") or 0) == source_time:
        return history
    cutoff = now - HISTORY_SECONDS
    kept = [item for item in history if float(item.get("timestamp") or 0) >= cutoff]
    kept.append(
        {
            "timestamp": now,
            "sourceTime": source_time,
            "players": list(snapshot.get("players") or []),
        }
    )
    return kept


def _gateway_transform() -> dict[str, float]:
    global _calibration_cache_mtime_ns, _calibration_cache_transform
    try:
        modified = ACTIVITY_CALIBRATION_PATH.stat().st_mtime_ns
    except OSError:
        _calibration_cache_mtime_ns = None
        _calibration_cache_transform = dict(_DEFAULT_GATEWAY_TRANSFORM)
        return _calibration_cache_transform

    if modified == _calibration_cache_mtime_ns:
        return _calibration_cache_transform

    try:
        payload = json.loads(ACTIVITY_CALIBRATION_PATH.read_text(encoding="utf-8-sig"))
        raw = payload.get("transform") or {}
        transform = {
            key: float(raw[key]) for key in _DEFAULT_GATEWAY_TRANSFORM
        }
        determinant = (
            transform["pixelXFromWorldX"] * transform["pixelYFromWorldY"]
            - transform["pixelXFromWorldY"] * transform["pixelYFromWorldX"]
        )
        if not all(math.isfinite(value) for value in transform.values()):
            raise ValueError("non-finite Gateway calibration coefficient")
        if abs(determinant) <= 1e-10:
            raise ValueError("non-invertible Gateway calibration")
        _calibration_cache_transform = transform
        _calibration_cache_mtime_ns = modified
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        _calibration_cache_transform = dict(_DEFAULT_GATEWAY_TRANSFORM)
        _calibration_cache_mtime_ns = modified
    return _calibration_cache_transform


def _world_to_pixel(x: float, y: float, size: int) -> tuple[int, int] | None:
    transform = _gateway_transform()
    source_x = (
        transform["pixelXFromWorldX"] * x
        + transform["pixelXFromWorldY"] * y
        + transform["pixelXOffset"]
    )
    source_y = (
        transform["pixelYFromWorldX"] * x
        + transform["pixelYFromWorldY"] * y
        + transform["pixelYOffset"]
    )
    maximum = CALIBRATION_IMAGE_SIZE - 1
    left = source_x / maximum
    top = source_y / maximum
    if left < 0.0 or left > 1.0 or top < 0.0 or top > 1.0:
        return None
    return (
        min(size - 1, max(0, int(round(left * (size - 1))))),
        min(size - 1, max(0, int(round(top * (size - 1))))),
    )


def _gradient_lut() -> tuple[list[int], list[int], list[int], list[int]]:
    stops = (
        (0, (30, 55, 255, 0)),
        (35, (35, 50, 255, 80)),
        (85, (0, 220, 255, 125)),
        (135, (0, 255, 105, 160)),
        (185, (255, 235, 0, 195)),
        (225, (255, 90, 0, 220)),
        (255, (255, 20, 0, 235)),
    )
    channels = [[], [], [], []]
    for value in range(256):
        lower = stops[0]
        upper = stops[-1]
        for index in range(1, len(stops)):
            if value <= stops[index][0]:
                lower = stops[index - 1]
                upper = stops[index]
                break
        span = max(1, upper[0] - lower[0])
        ratio = (value - lower[0]) / span
        for channel in range(4):
            result = round(
                lower[1][channel]
                + (upper[1][channel] - lower[1][channel]) * ratio
            )
            channels[channel].append(max(0, min(255, result)))
    for value in range(10):
        channels[3][value] = 0
    return channels[0], channels[1], channels[2], channels[3]


HEAT_LUT = _gradient_lut()


def render_heatmap(samples: Iterable[dict[str, Any]]) -> bytes:
    base = Image.open(ACTIVITY_MAP_ASSET).convert("RGB")
    base = base.resize((RENDER_SIZE, RENDER_SIZE), Image.Resampling.LANCZOS)
    base = ImageEnhance.Contrast(base).enhance(1.12)
    base = ImageEnhance.Brightness(base).enhance(0.62).convert("RGBA")

    frequencies: dict[tuple[int, int], int] = {}
    for sample in samples:
        for player in sample.get("players") or []:
            point = _world_to_pixel(
                float(player.get("x") or 0),
                float(player.get("y") or 0),
                RENDER_SIZE,
            )
            if point is None:
                continue
            frequencies[point] = frequencies.get(point, 0) + 1

    if frequencies:
        density = Image.new("L", (RENDER_SIZE, RENDER_SIZE), 0)
        radius = 50
        diameter = radius * 2 + 1
        sigma = radius / 2.25
        radial = []
        for row in range(diameter):
            dy = row - radius
            for column in range(diameter):
                dx = column - radius
                distance_squared = dx * dx + dy * dy
                if distance_squared > radius * radius:
                    radial.append(0)
                else:
                    radial.append(
                        round(90 * math.exp(-(distance_squared / (2 * sigma * sigma))))
                    )
        base_stamp = Image.frombytes("L", (diameter, diameter), bytes(radial))
        stamp_cache: dict[int, Image.Image] = {}

        for (pixel_x, pixel_y), count in frequencies.items():
            strength = 1 + min(3, int(math.log2(max(1, count))))
            stamp = stamp_cache.get(strength)
            if stamp is None:
                stamp = base_stamp.point(
                    [min(255, value * strength) for value in range(256)]
                )
                stamp_cache[strength] = stamp

            left = max(0, pixel_x - radius)
            top = max(0, pixel_y - radius)
            right = min(RENDER_SIZE, pixel_x + radius + 1)
            bottom = min(RENDER_SIZE, pixel_y + radius + 1)
            stamp_left = left - (pixel_x - radius)
            stamp_top = top - (pixel_y - radius)
            stamp_right = stamp_left + (right - left)
            stamp_bottom = stamp_top + (bottom - top)
            region = density.crop((left, top, right, bottom))
            addition = stamp.crop((stamp_left, stamp_top, stamp_right, stamp_bottom))
            density.paste(
                ImageChops.add(region, addition),
                (left, top, right, bottom),
            )

        overlay = Image.merge(
            "RGBA",
            tuple(density.point(channel) for channel in HEAT_LUT),
        )
        base = Image.alpha_composite(base, overlay)

    output = BytesIO()
    base.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def _load_region_mask(size: int) -> Image.Image:
    mask = Image.open(ACTIVITY_REGION_MASK).convert("RGB")
    if mask.size != (size, size):
        mask = mask.resize((size, size), Image.Resampling.NEAREST)
    return mask


def _region_at_point(
    mask: Image.Image,
    point: tuple[int, int],
    search_radius: int = 6,
) -> str | None:
    pixels = mask.load()
    width, height = mask.size
    pixel_x, pixel_y = point
    region = REGION_BY_COLOR.get(pixels[pixel_x, pixel_y])
    if region is not None:
        return region

    # A player can land on a one-pixel boundary after the source mask is scaled.
    # Only a very small nearest-pixel search is allowed so genuinely unassigned
    # areas are not pulled into a distant region.
    for radius in range(1, search_radius + 1):
        candidates = []
        for offset in range(-radius, radius + 1):
            candidates.extend(
                (
                    (pixel_x + offset, pixel_y - radius),
                    (pixel_x + offset, pixel_y + radius),
                    (pixel_x - radius, pixel_y + offset),
                    (pixel_x + radius, pixel_y + offset),
                )
            )
        for candidate_x, candidate_y in candidates:
            if 0 <= candidate_x < width and 0 <= candidate_y < height:
                region = REGION_BY_COLOR.get(pixels[candidate_x, candidate_y])
                if region is not None:
                    return region
    return None


def region_for_world_location(x: float, y: float) -> str | None:
    """Resolve one saved world location through the calibrated heatmap mask."""
    point = _world_to_pixel(float(x), float(y), CALIBRATION_IMAGE_SIZE)
    if point is None:
        return None
    return _region_at_point(_load_region_mask(CALIBRATION_IMAGE_SIZE), point)


def count_players_by_region(players: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name, _color in REGIONS}
    mask = _load_region_mask(RENDER_SIZE)
    for player in players:
        point = _world_to_pixel(
            float(player.get("x") or 0),
            float(player.get("y") or 0),
            RENDER_SIZE,
        )
        if point is None:
            continue
        region = _region_at_point(mask, point)
        if region is not None:
            counts[region] += 1
    return counts


def _segmented_color(count: float) -> tuple[int, int, int, int]:
    safe_count = max(0, count)
    if safe_count <= SEGMENTED_HEAT_STOPS[0][0]:
        return SEGMENTED_HEAT_STOPS[0][1]
    if safe_count >= SEGMENTED_HEAT_STOPS[-1][0]:
        return SEGMENTED_HEAT_STOPS[-1][1]

    for index in range(1, len(SEGMENTED_HEAT_STOPS)):
        upper_count, upper_color = SEGMENTED_HEAT_STOPS[index]
        if safe_count > upper_count:
            continue
        lower_count, lower_color = SEGMENTED_HEAT_STOPS[index - 1]
        span = max(1, upper_count - lower_count)
        ratio = (safe_count - lower_count) / span
        return tuple(
            round(lower + (upper - lower) * ratio)
            for lower, upper in zip(lower_color, upper_color)
        )
    return SEGMENTED_HEAT_STOPS[-1][1]


def _draw_segmented_legend(image: Image.Image) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    title_font = ImageFont.load_default(size=18)
    label_font = ImageFont.load_default(size=16)
    panel_width = 390
    panel_height = 92
    margin = 22
    panel_left = RENDER_SIZE - panel_width - margin
    panel_top = RENDER_SIZE - panel_height - margin
    panel_right = panel_left + panel_width
    panel_bottom = panel_top + panel_height
    draw.rounded_rectangle(
        (panel_left, panel_top, panel_right, panel_bottom),
        radius=12,
        fill=(48, 50, 56, 220),
        outline=(225, 230, 245, 105),
        width=1,
    )
    draw.text(
        (panel_left + 18, panel_top + 11),
        "PLAYERS PER REGION",
        fill=(245, 247, 255, 245),
        font=title_font,
    )

    bar_left = panel_left + 18
    bar_top = panel_top + 42
    bar_width = panel_width - 36
    bar_height = 18
    for offset in range(bar_width):
        count = 15.0 * offset / max(1, bar_width - 1)
        red, green, blue, _alpha = _segmented_color(count)
        draw.line(
            (bar_left + offset, bar_top, bar_left + offset, bar_top + bar_height),
            fill=(red, green, blue, 255),
        )
    draw.rectangle(
        (bar_left, bar_top, bar_left + bar_width, bar_top + bar_height),
        outline=(255, 255, 255, 150),
        width=1,
    )
    draw.text(
        (bar_left, bar_top + bar_height + 4),
        "0",
        fill=(245, 247, 255, 235),
        font=label_font,
    )
    right_label = "15+"
    right_box = draw.textbbox((0, 0), right_label, font=label_font)
    right_width = right_box[2] - right_box[0]
    draw.text(
        (bar_left + bar_width - right_width, bar_top + bar_height + 4),
        right_label,
        fill=(245, 247, 255, 235),
        font=label_font,
    )


def _render_segmented_heatmap(current_players: list[dict[str, Any]]) -> bytes:
    base = Image.open(ACTIVITY_MAP_ASSET).convert("RGB")
    base = base.resize((RENDER_SIZE, RENDER_SIZE), Image.Resampling.LANCZOS)
    base = ImageEnhance.Contrast(base).enhance(1.08)
    base = ImageEnhance.Brightness(base).enhance(0.72).convert("RGBA")

    counts = count_players_by_region(current_players)
    colors_by_region = {
        name: _segmented_color(count) for name, count in counts.items()
    }
    mask = _load_region_mask(RENDER_SIZE)
    overlay_data = bytearray(RENDER_SIZE * RENDER_SIZE * 4)
    for index, mask_color in enumerate(mask.getdata()):
        region = REGION_BY_COLOR.get(mask_color)
        heat_color = colors_by_region.get(region) if region is not None else None
        if heat_color is None:
            continue
        offset = index * 4
        overlay_data[offset : offset + 4] = bytes(heat_color)

    overlay = Image.frombytes(
        "RGBA", (RENDER_SIZE, RENDER_SIZE), bytes(overlay_data)
    )
    base = Image.alpha_composite(base, overlay)
    _draw_segmented_legend(base)

    output = BytesIO()
    base.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_segmented_heatmap(players: Iterable[dict[str, Any]]) -> bytes:
    current_players = list(players)
    if len(current_players) < SEGMENTED_MIN_ONLINE_PLAYERS:
        raise ValueError(
            "The segmented heatmap requires at least "
            f"{SEGMENTED_MIN_ONLINE_PLAYERS} online players"
        )
    return _render_segmented_heatmap(current_players)


def render_empty_segmented_heatmap() -> bytes:
    return _render_segmented_heatmap([])


def _species_group(species: str) -> str | None:
    normalized = "".join(character for character in species.lower() if character.isalpha())
    if normalized in CARNIVORES:
        return "carnivore"
    if normalized in HERBIVORES:
        return "herbivore"
    if normalized in OMNIVORES:
        return "omnivore"
    return None


def activity_embed(snapshot: dict[str, Any] | None, now: float) -> discord.Embed:
    current_players = []
    if snapshot is not None and now - float(snapshot.get("updatedAt") or 0) <= SNAPSHOT_STALE_SECONDS:
        current_players = list(snapshot.get("players") or [])

    counts = {"carnivore": 0, "herbivore": 0, "omnivore": 0}
    for player in current_players:
        group = _species_group(str(player.get("species") or ""))
        if group is not None:
            counts[group] += 1

    total = len(current_players)

    def line(label: str, key: str) -> str:
        amount = counts[key]
        percentage = round(amount * 100 / total) if total else 0
        return f"**{label}:** {amount} ({percentage:.0f}%)"

    updated = datetime.fromtimestamp(now, ZoneInfo("Europe/Paris"))
    description = "\n".join(
        (
            f"**Online players:** {total}",
            line("Carnivores", "carnivore"),
            line("Herbivores", "herbivore"),
            line("Omnivores", "omnivore"),
            "",
            f"**Updated:** {updated:%d.%m.%Y - %H:%M:%S} (CEST)",
        )
    )
    embed = discord.Embed(
        title="ACTIVITY HEATMAP",
        description=description,
        color=discord.Color.from_rgb(49, 132, 209),
    )
    embed.set_image(url="attachment://activity-heatmap.png")
    embed.set_footer(text="Map has been provided by RaidAtlas")
    return embed


def segmented_activity_embed(
    snapshot: dict[str, Any] | None, now: float
) -> discord.Embed:
    embed = activity_embed(snapshot, now)
    updated = datetime.fromtimestamp(now, ZoneInfo("Europe/Paris"))
    embed.description = f"**Updated:** {updated:%d.%m.%Y - %H:%M:%S} (CEST)"
    embed.set_image(url="attachment://segmented-activity-heatmap.png")
    return embed


def segmented_activity_inactive_embed(now: float) -> discord.Embed:
    updated = datetime.fromtimestamp(now, ZoneInfo("Europe/Paris"))
    description = "\n".join(
        (
            "❗ Regional activity is hidden while fewer than "
            f"{SEGMENTED_MIN_ONLINE_PLAYERS} players are online to protect player anonymity. ❗",
            "",
            f"**Updated:** {updated:%d.%m.%Y - %H:%M:%S} (CEST)",
        )
    )
    embed = discord.Embed(
        title="ACTIVITY HEATMAP",
        description=description,
        color=discord.Color.from_rgb(49, 132, 209),
    )
    embed.set_image(url="attachment://segmented-activity-heatmap.png")
    embed.set_footer(text="Map has been provided by RaidAtlas")
    return embed
