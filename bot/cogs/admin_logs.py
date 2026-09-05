import asyncio
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from bot.services.admin_log_service import (
    admin_action_log_line,
    death_embed,
    global_chat_log_line,
    kill_feed_log_line,
    presence_log_line,
    send_admin_log,
)
from bot.services.dino_storage_service import MOD_ROOT, SAVED_ROOT
from bot.services.economy_service import break_playtime_sessions
from bot.services.activity_map_service import (
    ACTIVITY_MAP_CHANNEL_ID,
    SEGMENTED_MIN_ONLINE_PLAYERS,
    SNAPSHOT_STALE_SECONDS,
    activity_embed,
    load_segmented_message_id,
    load_live_snapshot,
    load_message_id,
    render_empty_segmented_heatmap,
    render_heatmap,
    render_segmented_heatmap,
    save_message_id,
    save_segmented_message_id,
    segmented_activity_embed,
    segmented_activity_inactive_embed,
)


LOGGER = logging.getLogger(__name__)
EVENT_PATH = Path(os.getenv("DINO_ADMIN_EVENT_PATH", str(MOD_ROOT / "admin_events.ndjson")))
CURSOR_PATH = Path(
    os.getenv("DINO_ADMIN_EVENT_CURSOR", str(MOD_ROOT / "admin_events.bot.offset"))
)
KILL_FEED_TEST_IMAGE = Path(__file__).resolve().parents[1] / "assets" / "killfeed-test.png"
KILL_FEED_RENDER_CHANNEL_ID = 1527443013884973248
KILL_FEED_TRICERATOPS_MESSAGE_ID = 1537452337629765782
LIVE_KILL_FEED_CHANNEL_ID = 1535257257284210759
KILL_FEED_RENDER_MESSAGES = {
    "Triceratops": 1537452337629765782,
    "Herrerasaurus": 1537473695964995788,
    "Allosaurus": 1537598644079829103,
    "Austroraptor": 1537829096204279899,
    "Tyrannosaurus Rex": 1538201423756664843,
    "Ceratosaurus": 1538246797158383727,
    "Diabloceratops": 1543264496926064752,
    "Deinosuchus": 1540137635367096371,
    "Tenontosaurus": 1541743743106027571,
    "Dilophosaurus": 1541755713636929598,
    "Pteranodon": 1541790965017219114,
    "Kentrosaurus": 1541827691534225498,
    "Carnotaurus": 1541838457679122452,
    "Maiasaura": 1542186993658896486,
    "Beipiaosaurus": 1542195883360264253,
    "Stegosaurus": 1542299533126410250,
    "Omniraptor": 1542313648989544568,
    "Gallimimus": 1542323767634829462,
    "Hypsilophodon": 1542334754173747210,
    "Dryosaurus": 1542341378393120779,
    "Troodon": 1542348395614707732,
    "Pachycephalosaurus": 1542355505698185268,
}
KILL_FEED_ATTACKER_RENDER_MESSAGES = {
    "Chicken": 1543241634794438747,
    "Deer": 1543241663059599411,
    "Boar": 1543241695427170304,
    "Fish": 1543241756584452096,
}


def _normalized_species(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


_KILL_FEED_SPECIES_ALIASES = {
    _normalized_species(name): name for name in KILL_FEED_RENDER_MESSAGES
}
_KILL_FEED_SPECIES_ALIASES.update(
    {
        "tyrannosaurus": "Tyrannosaurus Rex",
        "tyrannosaurusrex": "Tyrannosaurus Rex",
        "trex": "Tyrannosaurus Rex",
        "utahraptor": "Omniraptor",
    }
)
_KILL_FEED_ATTACKER_ALIASES = {
    "chicken": "Chicken",
    "deer": "Deer",
    "boar": "Boar",
    "fish": "Fish",
}


def kill_feed_species(value: Any, *, attacker: bool = False) -> tuple[str, int]:
    normalized = _normalized_species(value)
    if attacker:
        attacker_display = _KILL_FEED_ATTACKER_ALIASES.get(normalized)
        if attacker_display is None:
            for alias, candidate in _KILL_FEED_ATTACKER_ALIASES.items():
                if alias in normalized:
                    attacker_display = candidate
                    break
        if attacker_display is not None:
            return (
                attacker_display,
                KILL_FEED_ATTACKER_RENDER_MESSAGES[attacker_display],
            )
    display = _KILL_FEED_SPECIES_ALIASES.get(normalized)
    if display is None:
        for alias in sorted(_KILL_FEED_SPECIES_ALIASES, key=len, reverse=True):
            if alias and alias in normalized:
                display = _KILL_FEED_SPECIES_ALIASES[alias]
                break
    if display is None:
        display = "Triceratops"
    return display, KILL_FEED_RENDER_MESSAGES[display]


def _growth_text(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "Unknown"
    percentage = number * 100.0 if abs(number) <= 1.0 else number
    return f"{max(0, min(100, round(percentage)))}%"


def _killfeed_font(size: int):
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _killfeed_title_font(draw: ImageDraw.ImageDraw, text: str, max_width: int):
    for size in range(48, 17, -1):
        font = _killfeed_font(size)
        box = draw.textbbox((0, 0), text, font=font, stroke_width=2)
        if box[2] - box[0] <= max_width:
            return font
    return _killfeed_font(18)


def _killfeed_emoji_font(size: int):
    for name in ("C:/Windows/Fonts/seguiemj.ttf", "seguiemj.ttf", "Segoe UI Emoji"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return _killfeed_font(size)


def render_killfeed_test(
    killer_bytes: bytes,
    victim_bytes: bytes,
    killer_species: str = "Triceratops",
    victim_species: str = "Triceratops",
    killer_growth: Any = 93,
    victim_growth: Any = 78,
) -> bytes:
    # Earlier green kill-feed direction, with a darker render stage than surround.
    canvas = Image.new("RGBA", (1200, 500), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle((18, 18, 1181, 481), radius=46, fill=(31, 29, 42, 255), outline=(183, 63, 48, 255), width=8)
    draw.rounded_rectangle((45, 108, 1155, 462), radius=28, fill=(24, 22, 27, 255))
    title = f"{killer_species} defeated {victim_species}"
    title_font, growth_font = _killfeed_title_font(draw, title, 1100), _killfeed_font(43)
    box = draw.textbbox((0, 0), title, font=title_font, stroke_width=2)
    draw.text(((1200-(box[2]-box[0]))/2, 31), title, font=title_font, fill="white", stroke_width=2, stroke_fill=(0,0,0,230))
    fauna_without_brightness_filter = set(KILL_FEED_ATTACKER_RENDER_MESSAGES)
    killer_brightness = 1.0 if killer_species in fauna_without_brightness_filter else 1.45
    for raw, bounds, brightness in (
        (killer_bytes, (65, 125, 500, 392), killer_brightness),
        (victim_bytes, (700, 125, 1135, 392), 1.45),
    ):
        with Image.open(io.BytesIO(raw)) as source:
            render = source.convert("RGBA")
        if brightness != 1.0:
            alpha = render.getchannel("A")
            render = ImageEnhance.Brightness(render.convert("RGB")).enhance(brightness).convert("RGBA")
            render.putalpha(alpha)
        render.thumbnail((bounds[2]-bounds[0], bounds[3]-bounds[1]), Image.Resampling.LANCZOS)
        x = bounds[0] + (bounds[2]-bounds[0]-render.width)//2
        y = bounds[1] + (bounds[3]-bounds[1]-render.height)//2
        canvas.alpha_composite(render, (x, y))
    emoji = "⚔️"
    emoji_font = _killfeed_emoji_font(62)
    emoji_layer = Image.new("RGBA", (180, 140), (0, 0, 0, 0))
    emoji_draw = ImageDraw.Draw(emoji_layer)
    try:
        emoji_draw.text((20, 10), emoji, font=emoji_font, embedded_color=True)
    except (TypeError, ValueError):
        emoji_draw.text((20, 10), emoji, font=emoji_font, fill="white")
    visible_bounds = emoji_layer.getbbox()
    if visible_bounds:
        emoji_layer = emoji_layer.crop(visible_bounds)
        canvas.alpha_composite(
            emoji_layer,
            (600 - emoji_layer.width // 2, 263 - emoji_layer.height // 2),
        )
    for text, center in (
        (_growth_text(killer_growth), 282),
        (_growth_text(victim_growth), 918),
    ):
        box = draw.textbbox((0, 0), text, font=growth_font, stroke_width=2)
        draw.text((center-(box[2]-box[0])/2, 401), text, font=growth_font, fill="white", stroke_width=2, stroke_fill=(0,0,0,230))
    output = io.BytesIO()
    canvas.save(output, "PNG", optimize=True)
    return output.getvalue()


def render_natural_causes_test(
    victim_bytes: bytes,
    victim_species: str = "Triceratops",
    victim_growth: Any = 78,
    cause: str = "natural",
) -> bytes:
    canvas = Image.new("RGBA", (1200, 500), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas, "RGBA")
    draw.rounded_rectangle(
        (18, 18, 1181, 481),
        radius=46,
        fill=(31, 29, 42, 255),
        outline=(44, 192, 197, 255),
        width=8,
    )
    draw.rounded_rectangle((45, 108, 1155, 462), radius=28, fill=(24, 22, 27, 255))

    title_suffix = {
        "admin-slay": "was slain by an admin",
        "self-slay": "died after using Slay",
        "natural": "died of natural causes",
    }.get(str(cause), "died")
    title = f"{victim_species} {title_suffix}"
    title_font = _killfeed_title_font(draw, title, 1100)
    growth_font = _killfeed_font(43)
    title_box = draw.textbbox((0, 0), title, font=title_font, stroke_width=2)
    draw.text(
        ((1200 - (title_box[2] - title_box[0])) / 2, 31),
        title,
        font=title_font,
        fill="white",
        stroke_width=2,
        stroke_fill=(0, 0, 0, 230),
    )

    with Image.open(io.BytesIO(victim_bytes)) as source:
        victim = source.convert("RGBA")
    alpha = victim.getchannel("A")
    victim = ImageEnhance.Brightness(victim.convert("RGB")).enhance(1.45).convert("RGBA")
    victim.putalpha(alpha)
    victim.thumbnail((600, 260), Image.Resampling.LANCZOS)
    canvas.alpha_composite(victim, (600 - victim.width // 2, 245 - victim.height // 2))

    skull_font = _killfeed_emoji_font(54)
    skull_layer = Image.new("RGBA", (140, 120), (0, 0, 0, 0))
    skull_draw = ImageDraw.Draw(skull_layer)
    try:
        skull_draw.text((15, 8), "💀", font=skull_font, embedded_color=True)
    except (TypeError, ValueError):
        skull_draw.text((15, 8), "💀", font=skull_font, fill="white")
    visible_bounds = skull_layer.getbbox()
    if visible_bounds:
        skull_layer = skull_layer.crop(visible_bounds)
        canvas.alpha_composite(skull_layer, (600 - skull_layer.width // 2, 352 - skull_layer.height // 2))

    growth = _growth_text(victim_growth)
    growth_box = draw.textbbox((0, 0), growth, font=growth_font, stroke_width=2)
    draw.text(
        (600 - (growth_box[2] - growth_box[0]) / 2, 401),
        growth,
        font=growth_font,
        fill="white",
        stroke_width=2,
        stroke_fill=(0, 0, 0, 230),
    )
    output = io.BytesIO()
    canvas.save(output, "PNG", optimize=True)
    return output.getvalue()


class AdminLogs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not isinstance(getattr(self.bot, "dino_online_players", None), dict):
            self.bot.dino_online_players = {}
        self.offset = 0
        self._loaded_cursor = False
        self._activity_lock = asyncio.Lock()
        self._kill_feed_render_lock = asyncio.Lock()
        self._kill_feed_render_cache: dict[str, tuple[bytes, bytes]] = {}
        self.poll_events.start()
        self.update_activity_map.start()
        self.update_segmented_activity_map.start()

    def cog_unload(self) -> None:
        self.poll_events.cancel()
        self.update_activity_map.cancel()
        self.update_segmented_activity_map.cancel()

    async def _activity_channel(self):
        channel = self.bot.get_channel(ACTIVITY_MAP_CHANNEL_ID)
        if channel is None:
            channel = await self.bot.fetch_channel(ACTIVITY_MAP_CHANNEL_ID)
        return channel

    async def _kill_feed_render_channel(self):
        channel = self.bot.get_channel(KILL_FEED_RENDER_CHANNEL_ID)
        if channel is None:
            channel = await self.bot.fetch_channel(KILL_FEED_RENDER_CHANNEL_ID)
        return channel

    async def _kill_feed_assets(
        self,
        species: Any,
        *,
        attacker: bool = False,
    ) -> tuple[str, bytes, bytes]:
        display, message_id = kill_feed_species(species, attacker=attacker)
        cache_key = f"attacker:{display}" if attacker else f"dinosaur:{display}"
        cached = self._kill_feed_render_cache.get(cache_key)
        if cached is not None:
            return display, cached[0], cached[1]
        async with self._kill_feed_render_lock:
            cached = self._kill_feed_render_cache.get(cache_key)
            if cached is None:
                channel = await self._kill_feed_render_channel()
                message = await channel.fetch_message(message_id)
                required_attachments = 1 if attacker and display in KILL_FEED_ATTACKER_RENDER_MESSAGES else 2
                if len(message.attachments) < required_attachments:
                    raise RuntimeError(
                        f"{display} render message {message_id} needs "
                        f"{required_attachments} attachment(s)"
                    )
                if required_attachments == 1:
                    attacker_render = await message.attachments[0].read()
                    cached = (attacker_render, attacker_render)
                else:
                    downloads = await asyncio.gather(
                        message.attachments[0].read(),
                        message.attachments[1].read(),
                    )
                    cached = (downloads[0], downloads[1])
                self._kill_feed_render_cache[cache_key] = cached
        return display, cached[0], cached[1]

    async def _render_live_kill_feed(self, event: dict[str, Any]) -> bytes:
        victim_species, _victim_killer, victim_render = await self._kill_feed_assets(
            event.get("species")
        )
        cause = str(event.get("cause") or "natural")
        if cause != "player":
            return await asyncio.to_thread(
                render_natural_causes_test,
                victim_render,
                victim_species,
                event.get("growth"),
                cause,
            )
        killer_species, killer_render, _killer_victim = await self._kill_feed_assets(
            event.get("killerSpecies"),
            attacker=True,
        )
        return await asyncio.to_thread(
            render_killfeed_test,
            killer_render,
            victim_render,
            killer_species,
            victim_species,
            event.get("killerGrowth"),
            event.get("growth"),
        )

    async def _send_live_kill_feed(self, event: dict[str, Any]) -> bool:
        try:
            image_bytes = await self._render_live_kill_feed(event)
            destination = self.bot.get_channel(LIVE_KILL_FEED_CHANNEL_ID)
            if destination is None:
                destination = await self.bot.fetch_channel(LIVE_KILL_FEED_CHANNEL_ID)
            if not hasattr(destination, "send"):
                raise RuntimeError("Live kill-feed destination is not sendable")
            await destination.send(
                file=discord.File(io.BytesIO(image_bytes), filename="killfeed.png"),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except Exception:
            LOGGER.exception("Failed to render or send the live visual kill feed")
            return False

    async def _upsert_activity_message(
        self,
        embed: discord.Embed,
        image_bytes: bytes,
        *,
        allow_create: bool = False,
    ) -> bool:
        channel = await self._activity_channel()
        message = None
        message_id = await asyncio.to_thread(load_message_id)
        if message_id is not None and hasattr(channel, "fetch_message"):
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                message = None

        if message is None and not allow_create:
            return False

        upload = discord.File(
            io.BytesIO(image_bytes), filename="activity-heatmap.png"
        )
        if message is None:
            message = await channel.send(embed=embed, file=upload)
            await asyncio.to_thread(save_message_id, message.id)
            return True
        await message.edit(embed=embed, attachments=[upload])
        return True

    async def _render_activity_payload(
        self,
    ) -> tuple[discord.Embed, bytes]:
        now = time.time()
        snapshot = await asyncio.to_thread(load_live_snapshot)
        current_players = []
        if snapshot is not None and (
            now - float(snapshot.get("updatedAt") or 0) <= SNAPSHOT_STALE_SECONDS
        ):
            current_players = list(snapshot.get("players") or [])
        image_bytes = await asyncio.to_thread(
            render_heatmap,
            [{"timestamp": now, "players": current_players}],
        )
        return activity_embed(snapshot, now), image_bytes

    async def _upsert_segmented_activity_message(
        self,
        embed: discord.Embed,
        image_bytes: bytes | None,
        *,
        allow_create: bool = False,
    ) -> bool:
        channel = await self._activity_channel()
        message = None
        message_id = await asyncio.to_thread(load_segmented_message_id)
        if message_id is not None and hasattr(channel, "fetch_message"):
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                message = None

        if message is None and not allow_create:
            return False

        upload = (
            discord.File(
                io.BytesIO(image_bytes), filename="segmented-activity-heatmap.png"
            )
            if image_bytes is not None
            else None
        )
        if message is None:
            if upload is None:
                message = await channel.send(embed=embed)
            else:
                message = await channel.send(embed=embed, file=upload)
            await asyncio.to_thread(save_segmented_message_id, message.id)
            return True
        await message.edit(
            embed=embed,
            attachments=[upload] if upload is not None else [],
        )
        return True

    async def _render_segmented_activity_payload(
        self,
    ) -> tuple[discord.Embed, bytes, bool]:
        now = time.time()
        snapshot = await asyncio.to_thread(load_live_snapshot)
        current_players = []
        if snapshot is not None and (
            now - float(snapshot.get("updatedAt") or 0) <= SNAPSHOT_STALE_SECONDS
        ):
            current_players = list(snapshot.get("players") or [])
        if len(current_players) < SEGMENTED_MIN_ONLINE_PLAYERS:
            image_bytes = await asyncio.to_thread(render_empty_segmented_heatmap)
            return segmented_activity_inactive_embed(now), image_bytes, False
        image_bytes = await asyncio.to_thread(
            render_segmented_heatmap,
            current_players,
        )
        return segmented_activity_embed(snapshot, now), image_bytes, True

    @app_commands.command(
        name="killfeedtest",
        description="Preview the proposed visual kill-feed design.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def killfeedtest(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ This command is restricted to administrators.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            channel = self.bot.get_channel(KILL_FEED_RENDER_CHANNEL_ID) or await self.bot.fetch_channel(KILL_FEED_RENDER_CHANNEL_ID)
            message = await channel.fetch_message(KILL_FEED_TRICERATOPS_MESSAGE_ID)
            if len(message.attachments) < 2:
                raise RuntimeError("The render message must contain killer and victim attachments.")
            killer_bytes, victim_bytes = await asyncio.gather(
                message.attachments[0].read(),
                message.attachments[1].read(),
            )
            image_bytes = await asyncio.to_thread(render_killfeed_test, killer_bytes, victim_bytes)
            await interaction.followup.send(file=discord.File(io.BytesIO(image_bytes), filename="killfeed-test.png"))
        except Exception as error:
            LOGGER.exception("Failed to render /killfeedtest")
            await interaction.followup.send(f"❌ Kill-feed preview failed: `{error}`", ephemeral=True)

    @app_commands.command(
        name="naturalcausestest",
        description="Preview the proposed natural-causes kill-feed design.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def naturalcausestest(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ This command is restricted to administrators.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            channel = self.bot.get_channel(KILL_FEED_RENDER_CHANNEL_ID) or await self.bot.fetch_channel(KILL_FEED_RENDER_CHANNEL_ID)
            victim_message = await channel.fetch_message(KILL_FEED_TRICERATOPS_MESSAGE_ID)
            if len(victim_message.attachments) < 2:
                raise RuntimeError("The victim render message is missing its second attachment.")
            victim_bytes = await victim_message.attachments[1].read()
            image_bytes = await asyncio.to_thread(render_natural_causes_test, victim_bytes)
            await interaction.followup.send(file=discord.File(io.BytesIO(image_bytes), filename="natural-causes-test.png"))
        except Exception as error:
            LOGGER.exception("Failed to render /naturalcausestest")
            await interaction.followup.send(f"❌ Natural-causes preview failed: `{error}`", ephemeral=True)

    @app_commands.command(
        name="heatmap",
        description="Create or refresh the server activity heatmap.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def heatmap(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not (
            interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                "❌ This command is restricted to administrators.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with self._activity_lock:
                embed, image_bytes = await self._render_activity_payload()
                await self._upsert_activity_message(
                    embed,
                    image_bytes,
                    allow_create=True,
                )
        except Exception:
            LOGGER.exception("Failed to deploy the Discord activity heatmap")
            await interaction.followup.send(
                "❌ The activity heatmap could not be created.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"✅ The activity heatmap is active in <#{ACTIVITY_MAP_CHANNEL_ID}> and "
            "will update every 15 minutes.",
            ephemeral=True,
        )

    @app_commands.command(
        name="heatmapsegmented",
        description="Create or refresh the privacy-safe regional activity heatmap.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def heatmapsegmented(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not (
            interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                "❌ This command is restricted to administrators.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        is_active = False
        try:
            async with self._activity_lock:
                embed, image_bytes, is_active = (
                    await self._render_segmented_activity_payload()
                )
                await self._upsert_segmented_activity_message(
                    embed,
                    image_bytes,
                    allow_create=True,
                )
        except Exception:
            LOGGER.exception("Failed to deploy the segmented activity heatmap")
            await interaction.followup.send(
                "❌ The segmented activity heatmap could not be created.",
                ephemeral=True,
            )
            return

        if is_active:
            response = (
                f"✅ The segmented activity heatmap is active in "
                f"<#{ACTIVITY_MAP_CHANNEL_ID}> and will update every 15 minutes."
            )
        else:
            response = (
                f"✅ The segmented activity heatmap is configured in "
                f"<#{ACTIVITY_MAP_CHANNEL_ID}> and will become active once at least "
                f"{SEGMENTED_MIN_ONLINE_PLAYERS} players are online."
            )
        await interaction.followup.send(response, ephemeral=True)

    @staticmethod
    def _load_cursor() -> int:
        try:
            return max(0, int(CURSOR_PATH.read_text(encoding="utf-8").strip()))
        except (FileNotFoundError, ValueError, OSError):
            return 0

    @staticmethod
    def _save_cursor(offset: int) -> None:
        CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = CURSOR_PATH.with_name(CURSOR_PATH.name + ".tmp")
        temporary.write_text(str(max(0, offset)), encoding="utf-8")
        os.replace(temporary, CURSOR_PATH)

    @staticmethod
    def _read_events(offset: int) -> tuple[list[dict[str, Any]], int]:
        if not EVENT_PATH.is_file():
            return [], offset
        size = EVENT_PATH.stat().st_size
        if size < offset:
            offset = 0
        with EVENT_PATH.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read()
        if not chunk:
            return [], offset
        last_newline = chunk.rfind(b"\n")
        if last_newline < 0:
            return [], offset
        complete = chunk[: last_newline + 1]
        events: list[dict[str, Any]] = []
        for raw_line in complete.splitlines():
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                LOGGER.warning("Skipped malformed DinoStorage admin event line")
                continue
            if isinstance(value, dict):
                events.append(value)
        return events, offset + len(complete)

    @staticmethod
    def _load_death_snapshot(filename: str) -> dict[str, Any]:
        safe_name = Path(filename).name
        if not safe_name or safe_name != filename:
            return {}
        path = SAVED_ROOT / safe_name
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    async def _dispatch(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "server_session":
            self.bot.dino_online_players.clear()
            broken = await break_playtime_sessions()
            LOGGER.info(
                "Started game-server presence session %s; closed %d stale playtime session(s)",
                event.get("session") or "unknown",
                broken,
            )
            return
        if event_type == "presence":
            steam = str(event.get("steam") or "").strip()
            if steam:
                if event.get("event") == "login":
                    self.bot.dino_online_players[steam] = str(
                        event.get("name") or "Unknown"
                    )
                elif event.get("event") == "logout":
                    self.bot.dino_online_players.pop(steam, None)
            await send_admin_log(
                self.bot,
                "presence",
                content=presence_log_line(event),
            )
            return
        if event_type == "global_chat":
            await send_admin_log(
                self.bot,
                "global_chat",
                content=global_chat_log_line(event),
            )
            return
        if event_type == "admin_action":
            await send_admin_log(
                self.bot,
                "admin_action",
                content=admin_action_log_line(event),
            )
            return
        if event_type == "death":
            snapshot = await asyncio.to_thread(
                self._load_death_snapshot,
                str(event.get("snapshotFile") or ""),
            )
            await send_admin_log(
                self.bot,
                "death",
                embed=death_embed(event, snapshot),
            )
            visual_sent = await self._send_live_kill_feed(event)
            if not visual_sent:
                await send_admin_log(
                    self.bot,
                    "kill_feed",
                    content=kill_feed_log_line(event),
                )

    @tasks.loop(seconds=1)
    async def poll_events(self) -> None:
        if not self._loaded_cursor:
            self.offset = await asyncio.to_thread(self._load_cursor)
            self._loaded_cursor = True
        try:
            events, new_offset = await asyncio.to_thread(self._read_events, self.offset)
        except OSError as error:
            LOGGER.warning("Could not read DinoStorage admin events: %s", error)
            return
        for event in events:
            try:
                await self._dispatch(event)
            except Exception:
                LOGGER.exception("Failed to dispatch DinoStorage admin event")
        if new_offset != self.offset:
            self.offset = new_offset
            await asyncio.to_thread(self._save_cursor, self.offset)

    @poll_events.before_loop
    async def before_poll_events(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=15)
    async def update_activity_map(self) -> None:
        if await asyncio.to_thread(load_message_id) is None:
            return
        async with self._activity_lock:
            try:
                embed, image_bytes = await self._render_activity_payload()
                await self._upsert_activity_message(embed, image_bytes)
            except Exception:
                LOGGER.exception("Failed to update the Discord activity heatmap")

    @update_activity_map.before_loop
    async def before_update_activity_map(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=15)
    async def update_segmented_activity_map(self) -> None:
        if await asyncio.to_thread(load_segmented_message_id) is None:
            return
        async with self._activity_lock:
            try:
                embed, image_bytes, _ = (
                    await self._render_segmented_activity_payload()
                )
                await self._upsert_segmented_activity_message(embed, image_bytes)
            except Exception:
                LOGGER.exception("Failed to update the segmented activity heatmap")

    @update_segmented_activity_map.before_loop
    async def before_update_segmented_activity_map(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminLogs(bot))
