from __future__ import annotations

import json
import io
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import discord
from PIL import Image, ImageDraw, ImageFont

from bot.services.dino_storage_service import BOT_ROOT, MOD_ROOT


POPULATION_CHANNEL_ID = 1535471597014483025
POPULATION_SNAPSHOT_PATH = MOD_ROOT / "population_snapshot.json"
POPULATION_MESSAGE_PATH = MOD_ROOT / "population_control_message.json"
POPULATION_DESIGNED_MESSAGE_PATH = MOD_ROOT / "population_designed_message.json"
POPULATION_SNAPSHOT_STALE_SECONDS = 15
POPULATION_DESIGN_VERSION = "population-designed-v1-transparent"

SPECIES_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Herbivores",
        (
            "Diabloceratops",
            "Dryosaurus",
            "Hypsilophodon",
            "Kentrosaurus",
            "Maiasaura",
            "Pachycephalosaurus",
            "Stegosaurus",
            "Tenontosaurus",
            "Triceratops",
        ),
    ),
    (
        "Carnivores",
        (
            "Allosaurus",
            "Austroraptor",
            "Carnotaurus",
            "Ceratosaurus",
            "Deinosuchus",
            "Dilophosaurus",
            "Herrerasaurus",
            "Omniraptor",
            "Pteranodon",
            "Troodon",
            "Tyrannosaurus",
        ),
    ),
    (
        "Omnivores",
        (
            "Beipiaosaurus",
            "Gallimimus",
        ),
    ),
)

SPECIES_LIMITS = {
    "Allosaurus": 15,
    "Austroraptor": 30,
    "Beipiaosaurus": 30,
    "Carnotaurus": 22,
    "Ceratosaurus": 22,
    "Deinosuchus": 25,
    "Diabloceratops": 25,
    "Dilophosaurus": 25,
    "Dryosaurus": 30,
    "Gallimimus": 35,
    "Herrerasaurus": 30,
    "Hypsilophodon": 30,
    "Kentrosaurus": 25,
    "Maiasaura": 25,
    "Omniraptor": 30,
    "Pachycephalosaurus": 30,
    "Pteranodon": 30,
    "Stegosaurus": 15,
    "Tenontosaurus": 25,
    "Triceratops": 15,
    "Troodon": 30,
    "Tyrannosaurus": 7,
}

# Smallest to largest adult body size. The renderer deliberately consumes this
# order column-first: the left column is the smaller half and the right column
# continues through the largest species.
SPECIES_SIZE_ORDER: dict[str, tuple[str, ...]] = {
    "Herbivores": (
        "Hypsilophodon", "Dryosaurus", "Pachycephalosaurus",
        "Kentrosaurus", "Tenontosaurus", "Diabloceratops", "Maiasaura",
        "Stegosaurus", "Triceratops",
    ),
    "Carnivores": (
        "Troodon", "Herrerasaurus", "Pteranodon", "Austroraptor",
        "Omniraptor", "Dilophosaurus", "Ceratosaurus", "Carnotaurus",
        "Allosaurus", "Deinosuchus", "Tyrannosaurus",
    ),
    "Omnivores": ("Beipiaosaurus", "Gallimimus"),
}

POPULATION_TEST_WIDTH = 2048
POPULATION_TEST_BACKGROUND = (15, 14, 18, 255)
POPULATION_TEST_FRAME = (32, 30, 44, 255)
POPULATION_TEST_PANEL = (24, 22, 27, 255)
POPULATION_TEST_CYAN = (91, 199, 211, 255)
POPULATION_TEST_YELLOW = (244, 204, 61, 255)
POPULATION_TEST_ORANGE = (220, 132, 49, 255)
POPULATION_TEST_RED = (192, 68, 46, 255)

MANAGED_BY_KEY = {
    "".join(character for character in species.casefold() if character.isalpha()): species
    for species in SPECIES_LIMITS
}

GAME_INI_CANDIDATES = (
    Path(r"C:\TheMesozoic\TheIsle\Saved\Config\WindowsServer\Game.ini"),
    Path(r"D:\TheMesozoic\TheIsle\Saved\Config\WindowsServer\Game.ini"),
)
INITIAL_GAME_INI_BACKUP = BOT_ROOT / "population_control_initial_Game.ini"

SECTION_PATTERN = re.compile(
    r"^\s*\[\s*/script/theisle\.tigamestatebase\s*\]\s*$",
    re.IGNORECASE,
)
ANY_SECTION_PATTERN = re.compile(r"^\s*\[[^]]+\]\s*$")
ALLOWED_CLASS_PATTERN = re.compile(
    r"^\s*AllowedClasses\s*=\s*([^;#\r\n]+?)\s*(?:[;#].*)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GameIniChange:
    path: Path
    previous_bytes: bytes


def _normalize_species(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalpha())


def _atomic_bytes_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".population-control.tmp")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def _atomic_json_write(path: Path, value: Any) -> None:
    _atomic_bytes_write(
        path,
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def load_population_snapshot() -> dict[str, Any] | None:
    value = _read_json(POPULATION_SNAPSHOT_PATH)
    if not isinstance(value, dict) or not isinstance(value.get("counts"), dict):
        return None
    try:
        updated_at = int(value.get("updatedAt"))
    except (TypeError, ValueError):
        return None

    counts = {species: 0 for species in SPECIES_LIMITS}
    for raw_species, raw_count in value["counts"].items():
        species = MANAGED_BY_KEY.get(_normalize_species(raw_species))
        if species is None:
            continue
        try:
            counts[species] = max(0, int(raw_count))
        except (TypeError, ValueError):
            continue
    return {"updatedAt": updated_at, "counts": counts}


def snapshot_is_current(snapshot: dict[str, Any] | None, now: float | None = None) -> bool:
    if snapshot is None:
        return False
    current = time.time() if now is None else now
    try:
        age = current - float(snapshot.get("updatedAt") or 0)
    except (TypeError, ValueError):
        return False
    return -60 <= age <= POPULATION_SNAPSHOT_STALE_SECONDS


def population_signature(counts: dict[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple((species, int(counts.get(species, 0))) for species in sorted(SPECIES_LIMITS))


def locked_species(counts: dict[str, int]) -> frozenset[str]:
    return frozenset(
        species
        for species, limit in SPECIES_LIMITS.items()
        if int(counts.get(species, 0)) >= limit
    )


def population_embed(counts: dict[str, int]) -> discord.Embed:
    embed = discord.Embed(
        title="POPULATION CONTROL",
        color=discord.Color.from_rgb(66, 180, 92),
    )
    for group_name, species_names in SPECIES_GROUPS:
        lines = []
        for species in sorted(species_names, key=str.casefold):
            amount = max(0, int(counts.get(species, 0)))
            limit = SPECIES_LIMITS[species]
            indicator = "🔴" if amount >= limit else "🟢"
            lines.append(f"{indicator} {species} — **{amount}/{limit}**")
        embed.add_field(name=group_name, value="\n".join(lines), inline=False)
    return embed


def _population_font(size: int, *, bold: bool = True):
    candidates = (
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _mix_color(left: tuple[int, ...], right: tuple[int, ...], amount: float) -> tuple[int, ...]:
    amount = max(0.0, min(1.0, float(amount)))
    return tuple(round(left[index] + (right[index] - left[index]) * amount) for index in range(4))


def _population_level_color(ratio: float) -> tuple[int, ...]:
    value = max(0.0, min(1.0, ratio))
    # Low populations stay close to the reference cyan; pressure then moves
    # smoothly through yellow and orange before becoming red at the limit.
    stops = (
        (0.00, POPULATION_TEST_CYAN),
        (0.28, POPULATION_TEST_CYAN),
        (0.40, POPULATION_TEST_YELLOW),
        (0.70, POPULATION_TEST_ORANGE),
        (1.00, POPULATION_TEST_RED),
    )
    for index in range(1, len(stops)):
        start_at, start_color = stops[index - 1]
        end_at, end_color = stops[index]
        if value <= end_at:
            return _mix_color(start_color, end_color, (value - start_at) / (end_at - start_at))
    return POPULATION_TEST_RED


def _fit_render(raw: bytes, width: int, height: int) -> Image.Image | None:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            render = source.convert("RGBA")
    except (OSError, ValueError):
        return None
    bounds = render.getbbox()
    if bounds:
        render = render.crop(bounds)
    render.thumbnail((width, height), Image.Resampling.LANCZOS)
    return render


def _draw_population_bar(
    canvas: Image.Image,
    bounds: tuple[int, int, int, int],
    ratio: float,
) -> None:
    x1, y1, x2, y2 = bounds
    slant = 34
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.polygon(((x1 + slant, y1), (x2, y1), (x2 - slant, y2), (x1, y2)), fill=(2, 2, 3, 255))
    fill_ratio = max(0.0, min(1.0, ratio))
    if fill_ratio <= 0:
        return
    total_width = x2 - x1
    fill_right = x1 + max(slant + 2, round(total_width * fill_ratio))
    fill_right = min(x2, fill_right)
    color = _population_level_color(fill_ratio)
    draw.polygon(
        ((x1 + slant, y1), (fill_right, y1), (max(x1, fill_right - slant), y2), (x1, y2)),
        fill=color,
    )


def _draw_population_entry(
    canvas: Image.Image,
    species: str,
    amount: int,
    render_bytes: bytes | None,
    x: int,
    y: int,
    width: int,
    row_height: int,
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    render_width = 245
    render_height = row_height - 22
    if render_bytes:
        render = _fit_render(render_bytes, render_width, render_height)
        if render is not None:
            canvas.alpha_composite(
                render,
                (x + (render_width - render.width) // 2, y + (row_height - render.height) // 2),
            )

    limit = SPECIES_LIMITS[species]
    ratio = amount / limit if limit > 0 else 1.0
    color = _population_level_color(ratio)
    name_font = _population_font(43)
    count_font = _population_font(39)
    text_x = x + 250
    draw.text((text_x, y + 25), species, font=name_font, fill=(248, 247, 250, 255))
    count_text = f"{amount}/{limit}"
    count_box = draw.textbbox((0, 0), count_text, font=count_font)
    draw.text(
        (text_x + 120 - (count_box[2] - count_box[0]) // 2, y + 79),
        count_text,
        font=count_font,
        fill=color,
    )
    _draw_population_bar(
        canvas,
        (x + 475, y + 78, x + width - 22, y + 127),
        ratio,
    )


def render_population_test_panel(
    counts: dict[str, int],
    attacker_renders: dict[str, bytes],
) -> bytes:
    width = POPULATION_TEST_WIDTH
    margin = 32
    frame_inset = 48
    heading_height = 104
    row_height = 158
    panel_padding = 28
    section_gap = 18
    group_rows = {
        name: (len(SPECIES_SIZE_ORDER[name]) + 1) // 2
        for name in ("Herbivores", "Carnivores", "Omnivores")
    }
    height = (
        frame_inset * 2
        + sum(heading_height + panel_padding * 2 + group_rows[name] * row_height for name in group_rows)
        + section_gap * 2
    )
    canvas = Image.new("RGBA", (width, height), POPULATION_TEST_BACKGROUND)
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle(
        (margin, margin, width - margin - 1, height - margin - 1),
        radius=62,
        fill=POPULATION_TEST_FRAME,
        outline=POPULATION_TEST_CYAN,
        width=12,
    )

    cursor_y = frame_inset
    title_font = _population_font(58)
    inner_x = frame_inset + 18
    inner_width = width - 2 * inner_x
    column_gap = 24
    column_width = (inner_width - column_gap) // 2

    for group_name in ("Herbivores", "Carnivores", "Omnivores"):
        title_box = draw.textbbox((0, 0), group_name, font=title_font)
        draw.text(
            ((width - (title_box[2] - title_box[0])) // 2, cursor_y + 13),
            group_name,
            font=title_font,
            fill=(250, 249, 252, 255),
        )
        cursor_y += heading_height
        rows = group_rows[group_name]
        panel_bottom = cursor_y + panel_padding * 2 + rows * row_height
        draw.rounded_rectangle(
            (inner_x, cursor_y, width - inner_x, panel_bottom),
            radius=42,
            fill=POPULATION_TEST_PANEL,
        )
        ordered = SPECIES_SIZE_ORDER[group_name]
        split_at = (len(ordered) + 1) // 2
        columns = (ordered[:split_at], ordered[split_at:])
        for column_index, species_names in enumerate(columns):
            entry_x = inner_x + column_index * (column_width + column_gap)
            for row_index, species in enumerate(species_names):
                _draw_population_entry(
                    canvas,
                    species,
                    max(0, int(counts.get(species, 0))),
                    attacker_renders.get(species),
                    entry_x,
                    cursor_y + panel_padding + row_index * row_height,
                    column_width,
                    row_height,
                )
        cursor_y = panel_bottom + section_gap

    output = io.BytesIO()
    canvas.convert("RGB").save(output, "PNG", optimize=True, compress_level=9)
    return output.getvalue()


def _draw_population_entry_wide(
    canvas: Image.Image,
    species: str,
    amount: int,
    render_bytes: bytes | None,
    x: int,
    y: int,
    width: int,
    row_height: int,
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    render_width = 350
    render_height = row_height - 18
    if render_bytes:
        render = _fit_render(render_bytes, render_width, render_height)
        if render is not None:
            canvas.alpha_composite(
                render,
                (x + (render_width - render.width) // 2, y + (row_height - render.height) // 2),
            )

    limit = SPECIES_LIMITS[species]
    ratio = amount / limit if limit > 0 else 1.0
    color = _population_level_color(ratio)
    name_font = _population_font(48)
    count_font = _population_font(43)
    text_x = x + 390
    draw.text((text_x, y + 32), species, font=name_font, fill=(248, 247, 250, 255))
    count_text = f"{amount}/{limit}"
    count_box = draw.textbbox((0, 0), count_text, font=count_font)
    draw.text(
        (text_x + 132 - (count_box[2] - count_box[0]) // 2, y + 98),
        count_text,
        font=count_font,
        fill=color,
    )
    _draw_population_bar(
        canvas,
        (x + 700, y + 101, x + width - 28, y + 158),
        ratio,
    )


def _draw_connected_frame(draw: ImageDraw.ImageDraw, width: int, height: int, position: str) -> None:
    border = 13
    inset = 30
    radius = 64
    cyan = POPULATION_TEST_CYAN
    if position == "top":
        draw.line((inset + radius, inset, width - inset - radius, inset), fill=cyan, width=border)
        draw.arc((inset, inset, inset + radius * 2, inset + radius * 2), 180, 270, fill=cyan, width=border)
        draw.arc((width - inset - radius * 2, inset, width - inset, inset + radius * 2), 270, 360, fill=cyan, width=border)
        draw.line((inset, inset + radius, inset, height), fill=cyan, width=border)
        draw.line((width - inset, inset + radius, width - inset, height), fill=cyan, width=border)
    elif position == "middle":
        draw.line((inset, 0, inset, height), fill=cyan, width=border)
        draw.line((width - inset, 0, width - inset, height), fill=cyan, width=border)
    else:
        draw.line((inset, 0, inset, height - inset - radius), fill=cyan, width=border)
        draw.line((width - inset, 0, width - inset, height - inset - radius), fill=cyan, width=border)
        draw.arc((inset, height - inset - radius * 2, inset + radius * 2, height - inset), 90, 180, fill=cyan, width=border)
        draw.arc((width - inset - radius * 2, height - inset - radius * 2, width - inset, height - inset), 0, 90, fill=cyan, width=border)
        draw.line((inset + radius, height - inset, width - inset - radius, height - inset), fill=cyan, width=border)


def render_population_test_sections(
    counts: dict[str, int],
    attacker_renders: dict[str, bytes],
) -> dict[str, bytes]:
    width = 2800
    heading_height = 132
    row_height = 190
    panel_padding = 32
    outer_side = 70
    column_gap = 62
    title_font = _population_font(68)
    results: dict[str, bytes] = {}
    groups = (("Herbivores", "top"), ("Carnivores", "middle"), ("Omnivores", "bottom"))

    for group_name, frame_position in groups:
        ordered = SPECIES_SIZE_ORDER[group_name]
        rows = (len(ordered) + 1) // 2
        bottom_padding = 48 if frame_position == "bottom" else 18
        top_padding = 48 if frame_position == "top" else 18
        height = top_padding + heading_height + panel_padding * 2 + rows * row_height + bottom_padding
        canvas = Image.new("RGBA", (width, height), POPULATION_TEST_FRAME)
        draw = ImageDraw.Draw(canvas, "RGBA")
        _draw_connected_frame(draw, width, height, frame_position)

        title_y = top_padding + 8
        title_box = draw.textbbox((0, 0), group_name, font=title_font)
        draw.text(
            ((width - (title_box[2] - title_box[0])) // 2, title_y),
            group_name,
            font=title_font,
            fill=(250, 249, 252, 255),
        )
        panel_top = top_padding + heading_height
        panel_bottom = panel_top + panel_padding * 2 + rows * row_height
        draw.rounded_rectangle(
            (outer_side, panel_top, width - outer_side, panel_bottom),
            radius=45,
            fill=POPULATION_TEST_PANEL,
        )
        inner_width = width - outer_side * 2
        column_width = (inner_width - column_gap) // 2
        split_at = (len(ordered) + 1) // 2
        columns = (ordered[:split_at], ordered[split_at:])
        for column_index, species_names in enumerate(columns):
            entry_x = outer_side + column_index * (column_width + column_gap)
            for row_index, species in enumerate(species_names):
                _draw_population_entry_wide(
                    canvas,
                    species,
                    max(0, int(counts.get(species, 0))),
                    attacker_renders.get(species),
                    entry_x,
                    panel_top + panel_padding + row_index * row_height,
                    column_width,
                    row_height,
                )

        output = io.BytesIO()
        canvas.convert("RGB").save(output, "PNG", optimize=False, compress_level=6)
        results[group_name] = output.getvalue()
    return results


def render_population_test_panel_wide(
    counts: dict[str, int],
    attacker_renders: dict[str, bytes],
) -> bytes:
    sections = render_population_test_sections(counts, attacker_renders)
    images: list[Image.Image] = []
    try:
        for group_name in ("Herbivores", "Carnivores", "Omnivores"):
            with Image.open(io.BytesIO(sections[group_name])) as source:
                images.append(source.convert("RGB"))
        width = max(image.width for image in images)
        height = sum(image.height for image in images)
        canvas = Image.new("RGB", (width, height), POPULATION_TEST_FRAME[:3])
        cursor_y = 0
        for image in images:
            canvas.paste(image, (0, cursor_y))
            cursor_y += image.height
        output = io.BytesIO()
        canvas.save(output, "PNG", optimize=False, compress_level=6)
        return output.getvalue()
    finally:
        for image in images:
            image.close()


def render_population_test_panel_balanced(
    counts: dict[str, int],
    attacker_renders: dict[str, bytes],
    alphabetical: bool = False,
) -> bytes:
    """Render the clean wide design as one near-square, vertically balanced panel."""
    width = 2800
    outer_margin = 24
    inner_x = 70
    heading_height = 110
    row_height = 190
    panel_padding = 20
    section_gap = 16
    bottom_clearance = 18
    column_gap = 62
    groups = ("Herbivores", "Carnivores", "Omnivores")
    row_counts = {
        group_name: (len(SPECIES_SIZE_ORDER[group_name]) + 1) // 2
        for group_name in groups
    }
    height = (
        outer_margin * 2
        + sum(
            heading_height + panel_padding * 2 + row_counts[group_name] * row_height
            for group_name in groups
        )
        + section_gap * (len(groups) - 1)
        + bottom_clearance
    )
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle(
        (outer_margin, outer_margin, width - outer_margin - 1, height - outer_margin - 1),
        radius=64,
        fill=POPULATION_TEST_FRAME,
        outline=POPULATION_TEST_CYAN,
        width=13,
    )
    title_font = _population_font(68)
    inner_width = width - inner_x * 2
    column_width = (inner_width - column_gap) // 2
    cursor_y = outer_margin

    for group_name in groups:
        title_box = draw.textbbox((0, 0), group_name, font=title_font)
        draw.text(
            ((width - (title_box[2] - title_box[0])) // 2, cursor_y + 8),
            group_name,
            font=title_font,
            fill=(250, 249, 252, 255),
        )
        cursor_y += heading_height
        rows = row_counts[group_name]
        panel_bottom = cursor_y + panel_padding * 2 + rows * row_height
        draw.rounded_rectangle(
            (inner_x, cursor_y, width - inner_x, panel_bottom),
            radius=45,
            fill=POPULATION_TEST_PANEL,
        )
        ordered = SPECIES_SIZE_ORDER[group_name]
        if alphabetical:
            ordered = tuple(sorted(ordered, key=str.casefold))
        split_at = (len(ordered) + 1) // 2
        columns = (ordered[:split_at], ordered[split_at:])
        for column_index, species_names in enumerate(columns):
            entry_x = inner_x + column_index * (column_width + column_gap)
            balance_offset = ((rows - len(species_names)) * row_height) // 2
            for row_index, species in enumerate(species_names):
                _draw_population_entry_wide(
                    canvas,
                    species,
                    max(0, int(counts.get(species, 0))),
                    attacker_renders.get(species),
                    entry_x,
                    cursor_y + panel_padding + balance_offset + row_index * row_height,
                    column_width,
                    row_height,
                )
        cursor_y = panel_bottom + section_gap

    output = io.BytesIO()
    canvas.save(output, "PNG", optimize=False, compress_level=6)
    return output.getvalue()


def render_population_test_panel_alphabetical(
    counts: dict[str, int],
    attacker_renders: dict[str, bytes],
) -> bytes:
    return render_population_test_panel_balanced(
        counts,
        attacker_renders,
        alphabetical=True,
    )


def _fit_population_name_font(draw: ImageDraw.ImageDraw, text: str, max_width: int):
    for size in range(104, 61, -2):
        font = _population_font(size)
        bounds = draw.textbbox((0, 0), text, font=font)
        if bounds[2] - bounds[0] <= max_width:
            return font
    return _population_font(62)


def _draw_population_entry_big(
    canvas: Image.Image,
    species: str,
    amount: int,
    render_bytes: bytes | None,
    x: int,
    y: int,
    width: int,
    row_height: int,
) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    render_width = 620
    render_height = row_height - 12
    if render_bytes:
        render = _fit_render(render_bytes, render_width, render_height)
        if render is not None:
            canvas.alpha_composite(
                render,
                (x + (render_width - render.width) // 2, y + (row_height - render.height) // 2),
            )

    limit = SPECIES_LIMITS[species]
    ratio = amount / limit if limit > 0 else 1.0
    color = _population_level_color(ratio)
    text_x = x + 680
    bar_x = x + 1130
    name_font = _fit_population_name_font(draw, species, width - 700)
    count_font = _population_font(78)
    draw.text((text_x, y + 16), species, font=name_font, fill=(248, 247, 250, 255))
    count_text = f"{amount}/{limit}"
    count_box = draw.textbbox((0, 0), count_text, font=count_font)
    draw.text(
        (text_x + 190 - (count_box[2] - count_box[0]) // 2, y + 132),
        count_text,
        font=count_font,
        fill=color,
    )
    _draw_population_bar(
        canvas,
        (bar_x, y + 145, x + width - 30, y + 220),
        ratio,
    )


def render_population_test_panel_big(
    counts: dict[str, int],
    attacker_renders: dict[str, bytes],
) -> bytes:
    width = 3900
    outer_margin = 34
    inner_x = 74
    heading_height = 168
    row_height = 240
    panel_padding = 34
    section_gap = 14
    column_gap = 86
    groups = ("Herbivores", "Carnivores", "Omnivores")
    row_counts = {name: (len(SPECIES_SIZE_ORDER[name]) + 1) // 2 for name in groups}
    height = (
        outer_margin * 2
        + sum(heading_height + panel_padding * 2 + row_counts[name] * row_height for name in groups)
        + section_gap * 2
    )
    canvas = Image.new("RGBA", (width, height), POPULATION_TEST_BACKGROUND)
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle(
        (outer_margin, outer_margin, width - outer_margin - 1, height - outer_margin - 1),
        radius=76,
        fill=POPULATION_TEST_FRAME,
        outline=POPULATION_TEST_CYAN,
        width=15,
    )
    title_font = _population_font(102)
    inner_width = width - inner_x * 2
    column_width = (inner_width - column_gap) // 2
    cursor_y = outer_margin

    for group_name in groups:
        title_box = draw.textbbox((0, 0), group_name, font=title_font)
        draw.text(
            ((width - (title_box[2] - title_box[0])) // 2, cursor_y + 22),
            group_name,
            font=title_font,
            fill=(250, 249, 252, 255),
        )
        cursor_y += heading_height
        rows = row_counts[group_name]
        panel_bottom = cursor_y + panel_padding * 2 + rows * row_height
        draw.rounded_rectangle(
            (inner_x, cursor_y, width - inner_x, panel_bottom),
            radius=54,
            fill=POPULATION_TEST_PANEL,
        )
        ordered = SPECIES_SIZE_ORDER[group_name]
        split_at = (len(ordered) + 1) // 2
        columns = (ordered[:split_at], ordered[split_at:])
        for column_index, species_names in enumerate(columns):
            entry_x = inner_x + column_index * (column_width + column_gap)
            balance_offset = ((rows - len(species_names)) * row_height) // 2
            for row_index, species in enumerate(species_names):
                _draw_population_entry_big(
                    canvas,
                    species,
                    max(0, int(counts.get(species, 0))),
                    attacker_renders.get(species),
                    entry_x,
                    cursor_y + panel_padding + balance_offset + row_index * row_height,
                    column_width,
                    row_height,
                )
        cursor_y = panel_bottom + section_gap

    output = io.BytesIO()
    canvas.convert("RGB").save(output, "PNG", optimize=False, compress_level=6)
    return output.getvalue()


def load_message_reference() -> tuple[int | None, int | None]:
    value = _read_json(POPULATION_MESSAGE_PATH)
    if not isinstance(value, dict):
        return None, None
    try:
        channel_id = int(value.get("channelId"))
        message_id = int(value.get("messageId"))
    except (TypeError, ValueError):
        return None, None
    return (
        channel_id if channel_id > 0 else None,
        message_id if message_id > 0 else None,
    )


def save_message_id(message_id: int) -> None:
    _atomic_json_write(
        POPULATION_MESSAGE_PATH,
        {"channelId": POPULATION_CHANNEL_ID, "messageId": int(message_id)},
    )


def load_designed_message_reference() -> tuple[int | None, int | None]:
    value = _read_json(POPULATION_DESIGNED_MESSAGE_PATH)
    if not isinstance(value, dict):
        return None, None
    try:
        channel_id = int(value.get("channelId"))
        message_id = int(value.get("messageId"))
    except (TypeError, ValueError):
        return None, None
    return (
        channel_id if channel_id > 0 else None,
        message_id if message_id > 0 else None,
    )


def save_designed_message_id(message_id: int) -> None:
    _atomic_json_write(
        POPULATION_DESIGNED_MESSAGE_PATH,
        {"channelId": POPULATION_CHANNEL_ID, "messageId": int(message_id)},
    )


def find_game_ini() -> Path:
    configured = os.getenv("MESOZOIC_GAME_INI", "").strip()
    if configured:
        path = Path(configured)
        if path.is_file():
            return path
        raise FileNotFoundError(f"Configured Game.ini was not found: {path}")
    for path in GAME_INI_CANDIDATES:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "Game.ini was not found in the C: or D: TheMesozoic installation."
    )


def _decode_game_ini(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    return raw.decode("utf-8"), "utf-8"


def _rewrite_allowed_classes(text: str, locked: Iterable[str]) -> str:
    locked_keys = {_normalize_species(species) for species in locked}
    newline = "\r\n" if "\r\n" in text else "\n"
    had_final_newline = text.endswith(("\r", "\n"))
    lines = text.splitlines()

    section_start = next(
        (index for index, line in enumerate(lines) if SECTION_PATTERN.match(line)),
        None,
    )
    if section_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        section_start = len(lines)
        lines.append("[/Script/TheIsle.TIGameStateBase]")
        section_end = len(lines)
    else:
        section_end = len(lines)
        for index in range(section_start + 1, len(lines)):
            if ANY_SECTION_PATTERN.match(lines[index]):
                section_end = index
                break

    body = lines[section_start + 1 : section_end]
    filtered: list[str] = []
    insertion_index: int | None = None
    for line in body:
        match = ALLOWED_CLASS_PATTERN.match(line)
        if match is not None and _normalize_species(match.group(1)) in MANAGED_BY_KEY:
            if insertion_index is None:
                insertion_index = len(filtered)
            continue
        filtered.append(line)
    if insertion_index is None:
        insertion_index = 0
        while insertion_index < len(filtered) and not filtered[insertion_index].strip():
            insertion_index += 1

    allowed_lines = [
        f"AllowedClasses={species}"
        for species in sorted(SPECIES_LIMITS, key=str.casefold)
        if _normalize_species(species) not in locked_keys
    ]
    filtered[insertion_index:insertion_index] = allowed_lines
    lines[section_start + 1 : section_end] = filtered

    rewritten = newline.join(lines)
    if had_final_newline or rewritten:
        rewritten += newline
    return rewritten


def prepare_game_ini_change(locked: Iterable[str]) -> GameIniChange | None:
    path = find_game_ini()
    previous = path.read_bytes()
    text, encoding = _decode_game_ini(previous)
    rewritten = _rewrite_allowed_classes(text, locked)
    encoded = rewritten.encode(encoding)
    if encoded == previous:
        return None
    if not INITIAL_GAME_INI_BACKUP.exists():
        _atomic_bytes_write(INITIAL_GAME_INI_BACKUP, previous)
    _atomic_bytes_write(path, encoded)
    return GameIniChange(path=path, previous_bytes=previous)


def rollback_game_ini(change: GameIniChange) -> None:
    _atomic_bytes_write(change.path, change.previous_bytes)
