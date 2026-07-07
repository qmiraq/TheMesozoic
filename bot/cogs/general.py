import discord
from discord.ext import commands


class General(commands.Cog):

    def __init__(self, bot):
        self.bot = bot


    @discord.app_commands.command(
        name="ping",
        description="Check bot latency"
    )
    async def ping(self, interaction: discord.Interaction):

        latency = round(self.bot.latency * 1000)

        await interaction.response.send_message(
            f"🏓 Pong! `{latency}ms`"
        )


async def setup(bot):
    await bot.add_cog(General(bot))