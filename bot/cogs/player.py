import discord
from discord.ext import commands

from bot.services.player_service import (
    link_player,
    get_profile
)

from bot.utils.validation import validate_steam_id


class Player(commands.Cog):

    def __init__(self, bot):
        self.bot = bot


    @discord.app_commands.command(
        name="linkingpanel",
        description="Create the SteamID linking panel"
    )
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def playerpanel(
        self,
        interaction: discord.Interaction
    ):

        embed = discord.Embed(
            title="🔑 LINK YOUR STEAM ID 🔑",
            description=(
                "Make sure you link your SteamID so you get access to all our features!\n\n"

                "**Where to get your SteamID:**\n"
                "1. Open Steam.\n"
                "2. Click your username in the top-right corner.\n"
                "3. Click **Account details**.\n"
                "4. Copy your SteamID64 (it usually starts with **7656**).\n"
                "5. Click the button below and paste your SteamID.\n\n"

                "**Happy playing Islander!**"
            ),
            color=discord.Color.green()
        )

        await interaction.response.send_message(
    "✅ Linking panel created.",
    ephemeral=True
)

        await interaction.channel.send(
    embed=embed,
    view=PlayerLinkView()
)


    @discord.app_commands.command(
        name="profile",
        description="View your linked player profile"
    )
    async def profile(
        self,
        interaction: discord.Interaction
    ):

        player = await get_profile(
            str(interaction.user.id)
        )

        if not player:

            await interaction.response.send_message(
                "❌ You do not have a linked SteamID yet.",
                ephemeral=True
            )

            return


        embed = discord.Embed(
            title="🦖 miniEniac Player Profile",
            color=discord.Color.green()
        )

        embed.add_field(
            name="Discord",
            value=interaction.user.mention,
            inline=False
        )

        embed.add_field(
            name="SteamID",
            value=f"`{player.steam_id}`",
            inline=False
        )

        embed.add_field(
            name="Evrima Name",
            value=player.evrima_name,
            inline=False
        )

        embed.add_field(
            name="Status",
            value="Linked ✅",
            inline=False
        )


        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )


class PlayerLinkView(discord.ui.View):

    def __init__(self):
        super().__init__(
            timeout=None
        )


    @discord.ui.button(
        label="Link Steam ID",
        emoji="🔗",
        style=discord.ButtonStyle.green,
        custom_id="link_steam"
    )
    async def link_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.send_modal(
            SteamLinkModal()
        )


class SteamLinkModal(discord.ui.Modal, title="Link Steam Account"):

    steam_id = discord.ui.TextInput(
        label="SteamID64",
        placeholder="7656119xxxxxxxxxx",
        required=True,
        min_length=17,
        max_length=17
    )


    async def on_submit(
        self,
        interaction: discord.Interaction
    ):

        steam_id = self.steam_id.value.strip()


        if not validate_steam_id(steam_id):

            await interaction.response.send_message(
                "❌ That doesn't look like a valid SteamID64.",
                ephemeral=True
            )

            return


        success, message = await link_player(
            discord_id=str(interaction.user.id),
            steam_id=steam_id
        )


        if success:

            await interaction.response.send_message(
                f"✅ Your SteamID **{steam_id}** has been linked successfully!",
                ephemeral=True
            )

        else:

            await interaction.response.send_message(
                f"❌ {message}",
                ephemeral=True
            )


    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception
    ):

        import traceback

        traceback.print_exception(
            type(error),
            error,
            error.__traceback__
        )

        if interaction.response.is_done():

            await interaction.followup.send(
                "❌ An unexpected error occurred.",
                ephemeral=True
            )

        else:

            await interaction.response.send_message(
                "❌ An unexpected error occurred.",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(Player(bot))