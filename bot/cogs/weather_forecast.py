from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from bot.services.weather_forecast_service import (
    get_panel_location,
    get_weather_forecast,
    save_panel_location,
)


LOGGER = logging.getLogger(__name__)

WEATHER_COLORS = {
    "clear": 0xF1C40F,
    "cloudy": 0x95A5A6,
    "foggy": 0xBDC3C7,
    "light_rain": 0x5DADE2,
    "rain": 0x3498DB,
    "rain_extension": 0x2471A3,
}


def weather_forecast_embed(forecast: dict) -> discord.Embed:
    if not forecast.get("available"):
        return discord.Embed(
            title="WEATHER FORECAST",
            description=(
                "**Current weather:** Unavailable\n\n"
                "Waiting for the weather controller to report its current state."
            ),
            color=discord.Color.dark_gray(),
        )

    option_lines = [
        f"- {name} — **{chance}**"
        for name, chance in forecast.get("options", ())
    ]
    if not option_lines:
        option_lines.append("- Waiting for the next scheduled roll.")

    deadline = int(forecast.get("next_action_at") or 0)
    timer = f"<t:{deadline}:R>" if deadline > 0 else "Scheduling..."
    description = (
        f"**Current weather:** {forecast['weather']}\n\n"
        "**Next weather rolls**\n"
        + "\n".join(option_lines)
        + f"\n\n**Next roll:** {timer}"
    )
    return discord.Embed(
        title="WEATHER FORECAST",
        description=description,
        color=WEATHER_COLORS.get(forecast.get("weather_key"), 0x5865F2),
    )


class WeatherForecast(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_panel: tuple[int, int] | None = None
        self._panel_message: discord.Message | None = None
        self.forecast_updater.start()

    def cog_unload(self):
        self.forecast_updater.cancel()

    async def _resolve_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        return channel

    async def _update_saved_panel(self) -> None:
        panel = await get_panel_location()
        if panel is None:
            return

        forecast = await get_weather_forecast()
        channel_id, message_id = panel
        try:
            if self._panel_message is None or panel != self._last_panel:
                channel = await self._resolve_channel(channel_id)
                self._panel_message = await channel.fetch_message(message_id)

            # Deliberately edit every cycle, even when the controller status did
            # not change. This keeps the persistent panel actively refreshed at
            # the requested ten-second cadence.
            await self._panel_message.edit(embed=weather_forecast_embed(forecast))
        except discord.NotFound:
            LOGGER.warning("Weather forecast panel message no longer exists")
            self._panel_message = None
            self._last_panel = None
            return
        except (discord.Forbidden, discord.HTTPException):
            LOGGER.exception("Could not update the weather forecast panel")
            return

        self._last_panel = panel

    @tasks.loop(seconds=10)
    async def forecast_updater(self):
        await self._update_saved_panel()

    @forecast_updater.before_loop
    async def before_forecast_updater(self):
        await self.bot.wait_until_ready()

    @discord.app_commands.command(
        name="weatherforecastpanel",
        description="Post the persistent weather forecast panel",
    )
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def weather_forecast_panel(self, interaction: discord.Interaction):
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message(
                "❌ This command must be used inside a server channel.",
                ephemeral=True,
            )
            return

        forecast = await get_weather_forecast()
        await interaction.response.send_message(
            "Weather forecast panel posted.",
            ephemeral=True,
        )
        message = await channel.send(embed=weather_forecast_embed(forecast))
        await save_panel_location(channel.id, message.id)
        self._last_panel = (channel.id, message.id)
        self._panel_message = message


async def setup(bot: commands.Bot):
    await bot.add_cog(WeatherForecast(bot))
