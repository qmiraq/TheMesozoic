from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks


LOGGER = logging.getLogger(__name__)
BOT_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = BOT_ROOT / "auto_restart_state.json"
SERVER_STATUS_MESSAGE_PATH = BOT_ROOT / "server_status_message.json"
NESTING_ROLE_ID = 1542939551566270484
_MOD_ROOT_CANDIDATES = (
    Path(r"C:\TheMesozoic\TheIsle\Binaries\Win64\Mods\DinoStorage"),
    Path(r"D:\TheMesozoic\TheIsle\Binaries\Win64\Mods\DinoStorage"),
)
MOD_ROOT = Path(
    os.getenv(
        "DINO_STORAGE_MOD_ROOT",
        str(next((path for path in _MOD_ROOT_CANDIDATES if path.is_dir()), _MOD_ROOT_CANDIDATES[0])),
    )
)
POPULATION_SNAPSHOT_PATH = Path(
    os.getenv("DINO_POPULATION_SNAPSHOT", str(MOD_ROOT / "population_snapshot.json"))
)
ADMIN_EVENT_PATH = Path(
    os.getenv("DINO_ADMIN_EVENT_PATH", str(MOD_ROOT / "admin_events.ndjson"))
)
SERVER_HEARTBEAT_STALE_SECONDS = 45
SERVER_STATUS_LOOKUP_TIMEOUT_SECONDS = 2.0
SERVER_SESSION_SCAN_LIMIT_BYTES = 8 * 1024 * 1024
RCON_BASE_URLS = (
    "http://127.0.0.1:5064/Rcon",
    "http://127.0.0.1:5000/Rcon",
)

WARNINGS = {
    (23, 45): "Server will restart in 15 minutes, make sure to safelog before the restart!",
    (11, 45): "Server will restart in 15 minutes, make sure to safelog before the restart!",
    (23, 50): "Server will restart in 10 minutes, make sure to safelog before the restart!",
    (11, 50): "Server will restart in 10 minutes, make sure to safelog before the restart!",
    (23, 55): "Server will restart in 5 minutes, make sure to safelog before the restart!",
    (11, 55): "Server will restart in 5 minutes, make sure to safelog before the restart!",
    (
        23,
        58,
    ): "Server will restart in 2 minutes, stop all fights and safelog to save all your progress!",
    (
        11,
        58,
    ): "Server will restart in 2 minutes, stop all fights and safelog to save all your progress!",
    (
        23,
        59,
    ): "Server will restart in 1 minute, stop all fights and safelog to save all your progress!",
    (
        11,
        59,
    ): "Server will restart in 1 minute, stop all fights and safelog to save all your progress!",
}
RESTART_TIMES = {(0, 0), (12, 0)}


def _paris_offset_for_local(value: datetime) -> int:
    """Return the UTC offset for an unambiguous Europe/Paris wall time."""
    march_switch = _last_sunday(value.year, 3)
    october_switch = _last_sunday(value.year, 10)
    if value.month < 3 or value.month > 10:
        return 1
    if 3 < value.month < 10:
        return 2
    if value.month == 3:
        return 2 if value.day > march_switch or (value.day == march_switch and value.hour >= 2) else 1
    return 2 if value.day < october_switch or (value.day == october_switch and value.hour < 3) else 1


def next_restart_timestamp(utc_now: datetime | None = None) -> int:
    current_utc = utc_now or datetime.now(timezone.utc)
    current_paris = paris_now(current_utc).replace(tzinfo=None)
    candidates: list[datetime] = []
    for day_offset in (0, 1):
        day = current_paris.date() + timedelta(days=day_offset)
        for hour, minute in sorted(RESTART_TIMES):
            candidate = datetime(day.year, day.month, day.day, hour, minute)
            if candidate > current_paris:
                candidates.append(candidate)
    target = min(candidates)
    offset = _paris_offset_for_local(target)
    target_utc = target.replace(tzinfo=timezone.utc) - timedelta(hours=offset)
    return int(target_utc.timestamp())


def _read_population_snapshot() -> dict:
    try:
        value = json.loads(POPULATION_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _server_is_online(snapshot: dict, now: float | None = None) -> bool:
    try:
        updated_at = float(snapshot.get("updatedAt"))
    except (TypeError, ValueError):
        return False
    age = (now if now is not None else time.time()) - updated_at
    return -5 <= age <= SERVER_HEARTBEAT_STALE_SECONDS


def _server_start_from_line(raw_line: bytes) -> int | None:
    if b'"server_session"' not in raw_line:
        return None
    try:
        event = json.loads(raw_line.decode("utf-8"))
        if event.get("type") != "server_session" or event.get("event") != "started":
            return None
        return int(event.get("ts") or event.get("session"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _latest_server_start() -> int | None:
    """Find a recent server-session event from a bounded tail of the log."""
    try:
        with ADMIN_EVENT_PATH.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            position = stream.tell()
            scan_floor = max(0, position - SERVER_SESSION_SCAN_LIMIT_BYTES)
            carry = b""
            while position > scan_floor:
                start = max(scan_floor, position - 65536)
                stream.seek(start)
                parts = (stream.read(position - start) + carry).split(b"\n")
                carry = parts.pop(0)
                for raw_line in reversed(parts):
                    timestamp = _server_start_from_line(raw_line)
                    if timestamp is not None:
                        return timestamp
                position = start
            return _server_start_from_line(carry) if scan_floor == 0 else None
    except OSError:
        return None


def _last_sunday(year: int, month: int) -> int:
    last_day = monthrange(year, month)[1]
    weekday = datetime(year, month, last_day).weekday()
    return last_day - ((weekday + 1) % 7)


def paris_now(utc_now: datetime | None = None) -> datetime:
    """Return Europe/Paris wall time without requiring Windows tzdata."""
    current = utc_now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    year = current.year
    dst_start = datetime(
        year,
        3,
        _last_sunday(year, 3),
        1,
        tzinfo=timezone.utc,
    )
    dst_end = datetime(
        year,
        10,
        _last_sunday(year, 10),
        1,
        tzinfo=timezone.utc,
    )
    offset = 2 if dst_start <= current < dst_end else 1
    return current + timedelta(hours=offset)


def event_for(paris_time: datetime) -> tuple[str, str | None] | None:
    clock = (paris_time.hour, paris_time.minute)
    key = paris_time.strftime("%Y-%m-%dT%H:%M")
    if clock in WARNINGS:
        return key, WARNINGS[clock]
    if clock in RESTART_TIMES:
        return key, None
    return None


def _read_last_event() -> str:
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""
    return str(payload.get("lastEvent") or "")


def _write_last_event(event_key: str) -> None:
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"lastEvent": event_key}, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, STATE_PATH)


def _read_status_message() -> tuple[int, int] | None:
    try:
        value = json.loads(SERVER_STATUS_MESSAGE_PATH.read_text(encoding="utf-8"))
        return int(value["channelId"]), int(value["messageId"])
    except (FileNotFoundError, OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _write_status_message(channel_id: int, message_id: int) -> None:
    temporary = SERVER_STATUS_MESSAGE_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"channelId": int(channel_id), "messageId": int(message_id)},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    os.replace(temporary, SERVER_STATUS_MESSAGE_PATH)


def _clear_status_message() -> None:
    try:
        SERVER_STATUS_MESSAGE_PATH.unlink()
    except FileNotFoundError:
        pass


class NestingRoleView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Nesting Ping",
        emoji="🥚",
        style=discord.ButtonStyle.success,
        custom_id="mesozoic:role:nesting",
    )
    async def nesting_role(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "❌ This role can only be changed inside the server.",
                ephemeral=True,
            )
            return
        role = interaction.guild.get_role(NESTING_ROLE_ID)
        if role is None:
            await interaction.response.send_message(
                "❌ The Nesting role could not be found.",
                ephemeral=True,
            )
            return
        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(
                    role,
                    reason="Self-service Nesting Ping role button",
                )
                message = "❌ Role Nesting has been unassigned!"
            else:
                await interaction.user.add_roles(
                    role,
                    reason="Self-service Nesting Ping role button",
                )
                message = "✅ Role Nesting has been assigned!"
        except discord.Forbidden:
            LOGGER.exception("Nesting role button lacks permission or role hierarchy")
            message = "❌ miniEniac cannot manage the Nesting role."
        except discord.HTTPException:
            LOGGER.exception("Discord rejected a Nesting role button update")
            message = "❌ The Nesting role could not be updated. Please try again."
        await interaction.response.send_message(message, ephemeral=True)


def nesting_role_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Select your role!",
        colour=discord.Colour.from_rgb(0, 153, 255),
    )
    embed.add_field(
        name="🥚 Nesting Ping",
        value="- Get pinged by players when they have a nest available.",
        inline=False,
    )
    embed.set_footer(text="Click already assigned role to unassign it!")
    return embed


class AutoRestart(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_event = _read_last_event()
        self.rcon_base_url: str | None = None
        self._server_status_signature: tuple[bool, int | None, int] | None = None
        self.bot.add_view(NestingRoleView())
        self.restart_clock.start()
        self.update_server_status.start()

    def cog_unload(self) -> None:
        self.restart_clock.cancel()
        self.update_server_status.cancel()

    async def _post_rcon(self, path: str, payload: dict | None = None) -> dict:
        candidates = list(RCON_BASE_URLS)
        if self.rcon_base_url in candidates:
            candidates.remove(self.rcon_base_url)
            candidates.insert(0, self.rcon_base_url)
        errors: list[str] = []
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for base_url in candidates:
                try:
                    async with session.post(
                        f"{base_url}/{path.lstrip('/')}",
                        json=payload,
                    ) as response:
                        body = await response.text()
                        if response.status < 200 or response.status >= 300:
                            errors.append(f"{base_url}: HTTP {response.status} {body[:160]}")
                            continue
                        self.rcon_base_url = base_url
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

    async def _announce(self, message: str) -> None:
        result = await self._post_rcon("announce", {"message": message})
        if result.get("ok") is False:
            raise RuntimeError(str(result.get("error") or "RCON announcement failed"))
        LOGGER.info("Sent automatic restart announcement: %s", message)

    async def _request_server_save(self) -> None:
        try:
            result = await self._post_rcon("save")
            if result.get("ok") is False:
                raise RuntimeError(str(result.get("error") or "RCON save failed"))
            LOGGER.info("Requested a server save before the automatic restart.")
        except Exception:
            LOGGER.exception(
                "The pre-restart RCON save failed; continuing with the scheduled restart."
            )

    @staticmethod
    def _stop_game_processes() -> bool:
        stopped = False
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for image_name in (
            "TheIsleServer-Win64-Shipping.exe",
            "TheIsleServer.exe",
        ):
            result = subprocess.run(
                ["taskkill.exe", "/F", "/IM", image_name],
                check=False,
                capture_output=True,
                text=True,
                creationflags=flags,
            )
            if result.returncode == 0:
                stopped = True
        return stopped

    async def _restart_game(self) -> None:
        await self._request_server_save()
        await asyncio.sleep(2)
        stopped = await asyncio.to_thread(self._stop_game_processes)
        if stopped:
            LOGGER.warning(
                "Automatic game-server restart triggered. "
                "The existing crash-restart launcher will validate and start the server."
            )
        else:
            LOGGER.error(
                "Automatic restart time was reached, but no game-server process was stopped."
            )

    @app_commands.command(
        name="serverstatus",
        description="Show whether the game server is online and its restart times.",
    )
    @app_commands.guild_only()
    async def server_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.channel is None:
            await interaction.edit_original_response(
                content="❌ The server-status channel is unavailable."
            )
            return
        try:
            embed, signature = await self._build_server_status()
            message = None
            saved = await asyncio.to_thread(_read_status_message)
            if saved is not None and saved[0] == interaction.channel.id:
                try:
                    message = await interaction.channel.fetch_message(saved[1])
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = None
            if message is None:
                message = await interaction.channel.send(embed=embed)
            else:
                await message.edit(embed=embed)
            await asyncio.to_thread(
                _write_status_message,
                interaction.channel.id,
                message.id,
            )
            self._server_status_signature = signature
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not post the public /serverstatus embed")
            await interaction.edit_original_response(
                content="❌ miniEniac could not post in this channel.",
                embed=None,
            )
            return
        except Exception:
            LOGGER.exception("Could not build or persist the /serverstatus message")
            await interaction.edit_original_response(
                content="❌ Server status data is temporarily unavailable.",
                embed=None,
            )
            return
        await interaction.edit_original_response(
            content="✅ Server status is active and will update automatically.",
            embed=None,
        )

    async def _build_server_status(
        self,
    ) -> tuple[discord.Embed, tuple[bool, int | None, int]]:
        snapshot = await asyncio.wait_for(
            asyncio.to_thread(_read_population_snapshot),
            timeout=SERVER_STATUS_LOOKUP_TIMEOUT_SECONDS,
        )
        try:
            last_restart = await asyncio.wait_for(
                asyncio.to_thread(_latest_server_start),
                timeout=SERVER_STATUS_LOOKUP_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, OSError):
            LOGGER.warning("Server-status restart-history lookup timed out or failed")
            last_restart = None
        online = _server_is_online(snapshot)
        next_restart = next_restart_timestamp()
        colour = (
            discord.Colour.from_rgb(65, 190, 105)
            if online
            else discord.Colour.from_rgb(205, 67, 67)
        )
        embed = discord.Embed(title="SERVER STATUS", colour=colour)
        embed.add_field(
            name="Status",
            value="🟢 **ONLINE**" if online else "🔴 **OFFLINE**",
            inline=False,
        )
        embed.add_field(
            name="Last restart",
            value=(
                f"<t:{last_restart}:F>\n<t:{last_restart}:R>"
                if last_restart is not None
                else "Temporarily unavailable"
            ),
            inline=True,
        )
        embed.add_field(
            name="Next restart",
            value=f"<t:{next_restart}:F>\n<t:{next_restart}:R>",
            inline=True,
        )
        embed.set_footer(text="Scheduled restarts: 00:00 and 12:00 Europe/Berlin")
        return embed, (online, last_restart, next_restart)

    @app_commands.command(
        name="rolepanel",
        description="Post the self-service server role panel.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def role_panel(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or not (
            interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                "❌ This command is restricted to administrators.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.channel is None:
            await interaction.edit_original_response(
                content="❌ The role-panel channel is unavailable."
            )
            return
        try:
            await interaction.channel.send(
                embed=nesting_role_embed(),
                view=NestingRoleView(),
            )
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not post the Nesting role panel")
            await interaction.edit_original_response(
                content="❌ miniEniac could not post the role panel in this channel."
            )
            return
        await interaction.edit_original_response(content="✅ Role panel posted.")

    @tasks.loop(seconds=15)
    async def update_server_status(self) -> None:
        saved = await asyncio.to_thread(_read_status_message)
        if saved is None:
            return
        try:
            embed, signature = await self._build_server_status()
            if signature == self._server_status_signature:
                return
            channel = self.bot.get_channel(saved[0])
            if channel is None:
                channel = await self.bot.fetch_channel(saved[0])
            if not hasattr(channel, "fetch_message"):
                raise RuntimeError("Saved server-status channel cannot contain messages")
            message = await channel.fetch_message(saved[1])
            await message.edit(embed=embed)
            self._server_status_signature = signature
        except discord.NotFound:
            LOGGER.warning(
                "The saved server-status message no longer exists; run /serverstatus to recreate it"
            )
            await asyncio.to_thread(_clear_status_message)
            self._server_status_signature = None
        except Exception:
            LOGGER.exception("Failed to update the persistent server-status message")

    @update_server_status.before_loop
    async def before_update_server_status(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=10)
    async def restart_clock(self) -> None:
        scheduled = event_for(paris_now())
        if scheduled is None:
            return
        event_key, warning = scheduled
        if event_key == self.last_event:
            return

        # Persist before performing the action so a bot restart during this
        # minute cannot duplicate an announcement or restart.
        self.last_event = event_key
        try:
            await asyncio.to_thread(_write_last_event, event_key)
        except OSError:
            LOGGER.exception("Could not persist the automatic-restart event marker.")

        if warning is not None:
            try:
                await self._announce(warning)
            except Exception:
                LOGGER.exception("Automatic restart announcement failed.")
            return
        await self._restart_game()

    @restart_clock.before_loop
    async def before_restart_clock(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoRestart(bot))
