import discord
from discord.ext import commands

from bot.services.rcon_service import rcon


class Server(commands.Cog):

    def __init__(self, bot):
        self.bot = bot


    @discord.app_commands.command(
        name="rcontest",
        description="Test RCON connection"
    )
    @discord.app_commands.checks.has_permissions(
        administrator=True
    )
    async def rcontest(
    self,
    interaction: discord.Interaction
):

        await interaction.response.defer(
        ephemeral=True
    )

        try:

            await rcon.connect()

            await interaction.followup.send(
            "🟢 RCON connection successful.",
            ephemeral=True
        )

        except Exception as e:

            await interaction.followup.send(
            f"🔴 RCON failed:\n`{type(e).__name__}: {e}`",
            ephemeral=True
        )


    @discord.app_commands.command(
        name="rconcommand",
        description="Run an RCON command"
    )
    @discord.app_commands.checks.has_permissions(
        administrator=True
    )
    async def rconcommand(
        self,
        interaction: discord.Interaction,
        command: str
    ):

        try:

            response = await rcon.send_command(
                command
            )

            await interaction.response.send_message(
                f"```{response}```",
                ephemeral=True
            )

        except Exception as e:

            await interaction.response.send_message(
                f"❌ {e}",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(Server(bot))