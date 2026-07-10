import discord
from discord.ext import commands


class General(commands.Cog):

    def __init__(self, bot):
        self.bot = bot


    @discord.app_commands.command(
        name="ping",
        description="Check bot latency"
    )
    async def ping(
        self,
        interaction: discord.Interaction
    ):

        latency = round(self.bot.latency * 1000)

        await interaction.response.send_message(
            f"🏓 Pong! `{latency}ms`"
        )


    @discord.app_commands.command(
        name="about",
        description="Information about EvrimaBot"
    )
    async def about(
        self,
        interaction: discord.Interaction
    ):

        embed = discord.Embed(
            title="miniEniac",
            description="The Mesozoic certified bot",
            color=discord.Color.green()
        )

        embed.add_field(
            name="Version",
            value="0.1.0",
            inline=True
        )

        embed.add_field(
            name="Status",
            value="Online",
            inline=True
        )

        embed.add_field(
            name="Features",
            value=(
                "• Discord commands\n"
                "• Very basic logging\n"
                "• Database base"
            ),
            inline=False
        )

        await interaction.response.send_message(
            embed=embed
        )


async def setup(bot):
    await bot.add_cog(General(bot))