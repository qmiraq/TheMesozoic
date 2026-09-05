from __future__ import annotations

import discord
from discord import app_commands


class AdminCommandTree(app_commands.CommandTree):
    """Command tree that exposes miniEniac slash commands to administrators only."""

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        permissions = getattr(interaction.user, "guild_permissions", None)
        allowed = bool(
            interaction.guild is not None
            and permissions is not None
            and permissions.administrator
        )
        if not allowed and not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ miniEniac slash commands are restricted to server administrators.",
                ephemeral=True,
            )
        return allowed

    async def sync(self, *, guild=None) -> list[app_commands.AppCommand]:
        administrator = discord.Permissions(administrator=True)
        for command in self.get_commands(guild=guild):
            command.default_permissions = administrator
        return await super().sync(guild=guild)
