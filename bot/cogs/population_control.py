from __future__ import annotations

import asyncio
import io
import json
import logging
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.services.population_control_service import (
    POPULATION_CHANNEL_ID,
    SPECIES_LIMITS,
    load_designed_message_reference,
    load_message_reference,
    load_population_snapshot,
    locked_species,
    population_embed,
    render_population_test_panel_balanced,
    population_signature,
    prepare_game_ini_change,
    rollback_game_ini,
    save_message_id,
    save_designed_message_id,
    snapshot_is_current,
)


LOGGER = logging.getLogger(__name__)
POPULATION_DESIGNED_BUILD_MARKER = "POPULATION_DESIGNED_V1_TRANSPARENT"
KILL_FEED_RENDER_CHANNEL_ID = 1527443013884973248
POPULATION_ATTACKER_RENDER_MESSAGES = {
    "Triceratops": 1537452337629765782,
    "Herrerasaurus": 1537473695964995788,
    "Allosaurus": 1537598644079829103,
    "Austroraptor": 1537829096204279899,
    "Tyrannosaurus": 1538201423756664843,
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
RCON_BASE_URLS = (
    "http://127.0.0.1:5064/Rcon",
    "http://127.0.0.1:5000/Rcon",
)


class PopulationControl(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self._last_counts_signature: tuple[tuple[str, int], ...] | None = None
        self._last_designed_counts_signature: tuple[tuple[str, int], ...] | None = None
        self._last_locked: frozenset[str] | None = None
        self._rcon_base_url: str | None = None
        self._retry_after = 0.0
        self._population_render_cache: dict[str, bytes] = {}
        self.watch_population.start()

    def cog_unload(self) -> None:
        self.watch_population.cancel()

    async def _channel(self):
        channel = self.bot.get_channel(POPULATION_CHANNEL_ID)
        if channel is None:
            channel = await self.bot.fetch_channel(POPULATION_CHANNEL_ID)
        return channel

    async def _upsert(self, embed: discord.Embed, *, allow_create: bool) -> bool:
        channel = await self._channel()
        message = None
        saved_channel_id, message_id = await asyncio.to_thread(load_message_reference)
        if (
            message_id is not None
            and saved_channel_id == POPULATION_CHANNEL_ID
            and hasattr(channel, "fetch_message")
        ):
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                message = None
        if message is None and not allow_create:
            return False
        if message is None:
            message = await channel.send(embed=embed)
            await asyncio.to_thread(save_message_id, message.id)
            if (
                saved_channel_id is not None
                and message_id is not None
                and saved_channel_id != POPULATION_CHANNEL_ID
            ):
                try:
                    old_channel = self.bot.get_channel(saved_channel_id)
                    if old_channel is None:
                        old_channel = await self.bot.fetch_channel(saved_channel_id)
                    if hasattr(old_channel, "fetch_message"):
                        old_message = await old_channel.fetch_message(message_id)
                        await old_message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    LOGGER.warning(
                        "The previous population-control panel could not be removed",
                        exc_info=True,
                    )
            return True
        await message.edit(embed=embed)
        return True

    async def _attacker_renders(self) -> dict[str, bytes]:
        channel = self.bot.get_channel(KILL_FEED_RENDER_CHANNEL_ID)
        if channel is None:
            channel = await self.bot.fetch_channel(KILL_FEED_RENDER_CHANNEL_ID)
        errors: list[str] = []
        for species in SPECIES_LIMITS:
            if species in self._population_render_cache:
                continue
            message_id = POPULATION_ATTACKER_RENDER_MESSAGES[species]
            try:
                message = await channel.fetch_message(message_id)
                if not message.attachments:
                    raise RuntimeError("message has no attachments")
                self._population_render_cache[species] = await message.attachments[0].read()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, RuntimeError) as error:
                errors.append(f"{species}: {error}")
        if errors:
            raise RuntimeError("Attacker renders unavailable: " + " | ".join(errors))
        return dict(self._population_render_cache)

    async def _upsert_designed(self, image: bytes, *, allow_create: bool) -> bool:
        channel = await self._channel()
        message = None
        saved_channel_id, message_id = await asyncio.to_thread(
            load_designed_message_reference
        )
        if (
            message_id is not None
            and saved_channel_id == POPULATION_CHANNEL_ID
            and hasattr(channel, "fetch_message")
        ):
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                message = None
        if message is None and not allow_create:
            return False
        file = discord.File(io.BytesIO(image), filename="population-designed.png")
        if message is None:
            message = await channel.send(file=file)
            await asyncio.to_thread(save_designed_message_id, message.id)
            return True
        await message.edit(content=None, embed=None, attachments=[file])
        return True

    async def _post_update_playables(self) -> dict:
        candidates = list(RCON_BASE_URLS)
        if self._rcon_base_url in candidates:
            candidates.remove(self._rcon_base_url)
            candidates.insert(0, self._rcon_base_url)
        errors: list[str] = []
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for base_url in candidates:
                try:
                    async with session.post(f"{base_url}/update-playables") as response:
                        body = await response.text()
                        if response.status < 200 or response.status >= 300:
                            errors.append(f"{base_url}: HTTP {response.status} {body[:160]}")
                            continue
                        self._rcon_base_url = base_url
                        if not body:
                            return {"ok": True}
                        try:
                            value = json.loads(body)
                        except json.JSONDecodeError:
                            return {"ok": True, "response": body}
                        return value if isinstance(value, dict) else {"ok": True}
                except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                    errors.append(f"{base_url}: {error}")
        raise RuntimeError("RCON API unavailable: " + " | ".join(errors))

    async def _reconcile_locks(self, counts: dict[str, int]) -> None:
        desired = locked_species(counts)
        if desired == self._last_locked:
            return
        now = time.monotonic()
        if now < self._retry_after:
            return

        change = await asyncio.to_thread(prepare_game_ini_change, desired)
        if change is not None:
            try:
                result = await self._post_update_playables()
                if result.get("ok") is False:
                    raise RuntimeError(str(result.get("error") or "UpdatePlayables failed"))
            except Exception:
                await asyncio.to_thread(rollback_game_ini, change)
                try:
                    await self._post_update_playables()
                except Exception:
                    LOGGER.exception("Population-control RCON rollback refresh failed")
                self._retry_after = time.monotonic() + 10
                raise

        self._last_locked = desired
        self._retry_after = 0.0
        LOGGER.info(
            "Population-control roster reconciled; locked=%s",
            ", ".join(sorted(desired)) or "none",
        )

    async def _process(self, *, allow_create: bool = False, force: bool = False) -> bool:
        snapshot = await asyncio.to_thread(load_population_snapshot)
        if not snapshot_is_current(snapshot):
            return False
        counts = dict(snapshot["counts"])
        signature = population_signature(counts)
        try:
            await self._reconcile_locks(counts)
        except Exception:
            LOGGER.exception("Population-control species lock reconciliation failed")

        if not force and signature == self._last_counts_signature:
            return True
        updated = await self._upsert(population_embed(counts), allow_create=allow_create)
        if updated:
            self._last_counts_signature = signature
        return updated

    async def _process_designed(
        self, *, allow_create: bool = False, force: bool = False
    ) -> bool:
        if not allow_create:
            saved_channel_id, message_id = await asyncio.to_thread(
                load_designed_message_reference
            )
            if saved_channel_id != POPULATION_CHANNEL_ID or message_id is None:
                return False
        snapshot = await asyncio.to_thread(load_population_snapshot)
        if not snapshot_is_current(snapshot):
            return False
        counts = dict(snapshot["counts"])
        signature = population_signature(counts)
        if not force and signature == self._last_designed_counts_signature:
            return True
        renders = await self._attacker_renders()
        image = await asyncio.to_thread(
            render_population_test_panel_balanced,
            counts,
            renders,
        )
        updated = await self._upsert_designed(image, allow_create=allow_create)
        if updated:
            self._last_designed_counts_signature = signature
        return updated

    @app_commands.command(
        name="populationcontrol",
        description="Create or refresh the live species population panel.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def populationcontrol(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not (
            interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                "❌ This command is restricted to administrators.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with self._lock:
                updated = await self._process(allow_create=True, force=True)
        except Exception:
            LOGGER.exception("Failed to create the population-control panel")
            await interaction.followup.send(
                "❌ The population-control panel could not be created.", ephemeral=True
            )
            return
        if not updated:
            await interaction.followup.send(
                "❌ Live population data is not ready. Restart the game server once, "
                "wait a few seconds, and try again.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ Population control is active in <#{POPULATION_CHANNEL_ID}>.",
            ephemeral=True,
        )

    @app_commands.command(
        name="populationdesigned",
        description="Create or refresh the designed live population panel.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def populationdesigned(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not (
            interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                "❌ This command is restricted to administrators.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with self._lock:
                updated = await self._process_designed(allow_create=True, force=True)
        except Exception:
            LOGGER.exception("Failed to create the designed population panel")
            await interaction.followup.send(
                "❌ The designed population panel could not be created. Check the bot log for details.",
                ephemeral=True,
            )
            return
        if not updated:
            await interaction.followup.send(
                "❌ Live population data is not ready. Restart the game server once, wait a few seconds, and try again.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ Designed population control is active in <#{POPULATION_CHANNEL_ID}>.",
            ephemeral=True,
        )

    @tasks.loop(seconds=1)
    async def watch_population(self) -> None:
        async with self._lock:
            try:
                await self._process()
            except Exception:
                LOGGER.exception("Population-control watch cycle failed")
            try:
                await self._process_designed()
            except Exception:
                LOGGER.exception("Designed population-control watch cycle failed")

    @watch_population.before_loop
    async def before_watch_population(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PopulationControl(bot))
