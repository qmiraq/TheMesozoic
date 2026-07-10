import discord
from discord.ext import commands

from bot.services.database_service import (
    set_setting,
    get_setting,
    get_all_settings
)

class Settings(commands.Cog):

    def __init__(self, bot):
        self.bot = bot


    @discord.app_commands.command(
        name="setsetting",
        description="Save a bot setting"
    )
    @discord.app_commands.checks.has_permissions(
        administrator=True
    )
    async def setsetting(
        self,
        interaction: discord.Interaction,
        key: str,
        value: str
    ):

        await set_setting(key, value)

        await interaction.response.send_message(
            f"✅ Saved setting `{key}`"
        )
    @discord.app_commands.command(
        name="settings",
        description="Show current miniEniac configuration"
    )
    @discord.app_commands.checks.has_permissions(
        administrator=True
    )
    async def settings(
        self,
        interaction: discord.Interaction
    ):

        settings = await get_all_settings()

        if not settings:
            await interaction.response.send_message(
                "⚙️ No settings configured yet.",
                ephemeral=True
            )
            return


        embed = discord.Embed(
            title="⚙️ miniEniac Configuration",
            color=discord.Color.blue()
        )

        for setting in settings:
            embed.add_field(
                name=setting.key,
                value=setting.value,
                inline=False
            )


        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    @discord.app_commands.command(
        name="getsetting",
        description="View a bot setting"
    )
    @discord.app_commands.checks.has_permissions(
        administrator=True
    )
    async def getsetting(
        self,
        interaction: discord.Interaction,
        key: str
    ):

        result = await get_setting(key)

        if result:
            await interaction.response.send_message(
                f"⚙️ `{key}` = `{result.value}`"
            )
        else:
            await interaction.response.send_message(
                f"❌ No setting found for `{key}`"
            )


    @setsetting.error
    async def setsetting_error(
        self,
        interaction: discord.Interaction,
        error
    ):

        if isinstance(
            error,
            discord.app_commands.errors.MissingPermissions
        ):
            await interaction.response.send_message(
                "❌ You need administrator permissions to use this command.",
                ephemeral=True
            )


    @getsetting.error
    async def getsetting_error(
        self,
        interaction: discord.Interaction,
        error
    ):

        if isinstance(
            error,
            discord.app_commands.errors.MissingPermissions
        ):
            await interaction.response.send_message(
                "❌ You need administrator permissions to use this command.",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(Settings(bot))