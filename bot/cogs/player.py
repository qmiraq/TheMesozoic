import csv
import io

import discord
from discord.ext import commands

from bot.services.admin_log_service import send_admin_log, steam_link_embed
from bot.services.dino_storage_service import DinoStorageError, run_self_slay
from bot.services.economy_service import (
    award_due_milestones,
    get_points,
    get_total_playtime,
    milestone_reward_points,
    milestone_threshold_hours,
    preview_milestone_backfill,
)
from bot.services.player_panel_service import PRIME_TASKS, get_prime_status
from bot.services.player_service import link_player, get_profile
from bot.utils.validation import validate_steam_id


MILESTONE_REWARD_CHANNEL_ID = 1538159689412378684


def player_panel_embed() -> discord.Embed:
    return discord.Embed(
        title="PLAYER PANEL",
        description=(
            "Manage your player features through the buttons below.\n\n"
            "**Features**\n"
            "- **Milestones** - Complete additional tasks for more points.\n"
            "- **Prime Status** - View your Prime Tasks and their progress.\n"
            "- **Slay** - Slay your current dinosaur in-game.\n\n"
            "**In-Game Commands**\n"
            "- **!slay** - Slays your dino.\n"
            "- **!hp** - Shows your current health.\n"
            "- **!prime** - Shows amount of completed prime tasks.\n"
            "- **!primelist** - Shows list of prime tasks in local chat along with progress.\n\n"
            "*Currently milestones track only your playtime, in the future you "
            "can expect more stats being tracked as well as them getting rewarded.*"
        ),
        color=discord.Color.green(),
    )


def _format_playtime(total_seconds: int) -> str:
    total_minutes = max(0, int(total_seconds)) // 60
    days, remaining_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remaining_minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours or days:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return ", ".join(parts)


def _roman_number(value: int) -> str:
    value = max(1, int(value))
    numerals = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    result = []
    for amount, numeral in numerals:
        count, value = divmod(value, amount)
        result.append(numeral * count)
    return "".join(result)


def _playtime_threshold_hours(milestone_number: int) -> int:
    return milestone_threshold_hours(milestone_number)


def _playtime_milestone_progress(total_seconds: int) -> dict:
    played_seconds = max(0, int(total_seconds))
    milestone_number = 1
    while played_seconds >= _playtime_threshold_hours(milestone_number) * 3600:
        milestone_number += 1
    next_hours = _playtime_threshold_hours(milestone_number)
    previous_hours = (
        _playtime_threshold_hours(milestone_number - 1)
        if milestone_number > 1
        else 0
    )
    interval_seconds = max(1, (next_hours - previous_hours) * 3600)
    interval_played = max(0, played_seconds - (previous_hours * 3600))
    percentage = min(100.0, max(0.0, interval_played / interval_seconds * 100.0))
    remaining_seconds = max(0, (next_hours * 3600) - played_seconds)
    return {
        "milestone_number": milestone_number,
        "next_hours": next_hours,
        "percentage": percentage,
        "remaining_seconds": remaining_seconds,
        "played_hours": played_seconds / 3600.0,
    }


def _milestone_progress_bar(percentage: float) -> str:
    gradient = ("🟥", "🟥", "🟧", "🟧", "🟨", "🟨", "🟩", "🟩", "🟩", "🟩")
    filled = min(10, max(0, int(float(percentage) // 10)))
    return "".join(gradient[index] if index < filled else "⬛" for index in range(10))


def _format_remaining_time(total_seconds: int) -> str:
    total_minutes = max(0, (int(total_seconds) + 59) // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} hour{'s' if hours != 1 else ''}, {minutes} minute{'s' if minutes != 1 else ''}"
    if hours:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def milestones_embed(total_seconds: int) -> discord.Embed:
    progress = _playtime_milestone_progress(total_seconds)
    percentage = progress["percentage"]
    played_hours = progress["played_hours"]
    played_display = f"{played_hours:.1f}".rstrip("0").rstrip(".")
    embed = discord.Embed(
        title="🏆MILESTONES",
        description=(
            "For now the only milestone to work towards is playtime, if you got "
            "any ideas for other ones, be sure to give us feedback!\n\n"
            "**PLAYTIME**\n"
            "Playtime rewards are rewarded at 1, 2, 6, 12, 18 and every "
            "subsequent 6 hours of playtime!\n\n"
            "**Current progress:**\n"
            f"**Playtime {_roman_number(progress['milestone_number'])}**\n"
            f"{_milestone_progress_bar(percentage)} **{percentage:.0f}%**\n"
            f"**{played_display}/{progress['next_hours']} hours**\n"
            f"**{_format_remaining_time(progress['remaining_seconds'])} until next reward** "
            f"**of {milestone_reward_points(progress['milestone_number']):,} points**"
        ),
        color=discord.Color.gold(),
    )
    return embed


def milestone_reward_embed(
    player_mention: str,
    milestone_number: int,
    reached_hours: int,
    reward_points: int,
    new_balance: int,
    *,
    preview: bool = False,
) -> discord.Embed:
    next_hours = _playtime_threshold_hours(milestone_number + 1)
    embed = discord.Embed(
        title="🏆 PLAYTIME MILESTONE COMPLETED",
        description=(
            f"{player_mention}, you completed "
            f"**Playtime {_roman_number(milestone_number)}** by reaching "
            f"**{reached_hours} hours** of playtime!\n\n"
            "**Reward**\n"
            f"💰 **{reward_points:,} points**\n\n"
            "**New balance**\n"
            f"**{new_balance - reward_points:,} points ---> {new_balance:,} points**\n\n"
            f"Your next milestone is **Playtime {_roman_number(milestone_number + 1)}** "
            f"at **{next_hours} hours**.\n\n"
            "*Thank you for playing on The Mesozoic!*"
        ),
        color=discord.Color.gold(),
    )
    if preview:
        embed.set_footer(text="Milestone reward preview • No points were awarded")
    return embed


def prime_status_embed(status: dict) -> discord.Embed:
    completed = {int(value) for value in status.get("completed_tasks", [])}
    migration_progress = min(2, int(status.get("migration_progress") or 0))
    patrol_progress = min(4, int(status.get("patrol_progress") or 0))
    lines = []
    for number, name in PRIME_TASKS:
        marker = "✅" if number in completed else "❌"
        progress = ""
        if number == 5:
            progress = f" ({migration_progress}/2)"
        elif number == 6:
            progress = f" ({patrol_progress}/4)"
        lines.append(f"{marker} **{number}.** {name}{progress}")

    completed_count = len(completed)
    eligibility = "✅ Achieved" if completed_count >= 5 else "❌ Not achieved"
    embed = discord.Embed(
        title="📑 PRIME STATUS",
        description=(
            f"**Dino:** {status.get('species') or 'Unknown'}\n"
            f"**Growth:** {float(status.get('growth') or 0) * 100:.1f}%\n"
            f"**Prime eligibility:** {eligibility}\n"
            f"**Tasks complete:** {completed_count}/10\n\n"
            + "\n".join(lines)
        ),
        color=discord.Color.gold() if completed_count >= 5 else discord.Color.blue(),
    )
    return embed


async def _linked_player(interaction: discord.Interaction):
    player = await get_profile(str(interaction.user.id))
    if player is None:
        await interaction.response.send_message(
            "❌ You must link your SteamID before using the Player Panel.",
            ephemeral=True,
        )
    return player


class Player(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(
        name="linkingpanel",
        description="Create the SteamID linking panel",
    )
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def linking_panel(self, interaction: discord.Interaction):
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
            color=discord.Color.green(),
        )

        await interaction.response.send_message(
            "✅ Linking panel created.",
            ephemeral=True,
        )
        await interaction.channel.send(embed=embed, view=PlayerLinkView())

    @discord.app_commands.command(
        name="playerpanel",
        description="Post the persistent player panel",
    )
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def player_panel(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Player panel posted.",
            ephemeral=True,
        )
        await interaction.channel.send(
            embed=player_panel_embed(),
            view=PlayerPanelView(),
        )

    @discord.app_commands.command(
        name="milestonerewardtest",
        description="Post a public preview of the playtime milestone reward message.",
    )
    @discord.app_commands.default_permissions(administrator=True)
    @discord.app_commands.guild_only()
    async def milestone_reward_test(self, interaction: discord.Interaction):
        player = await get_profile(str(interaction.user.id))
        if player is None:
            await interaction.response.send_message(
                "❌ You must link your SteamID before testing this message.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        current_balance = await get_points(str(interaction.user.id), str(player.steam_id))
        channel = self.bot.get_channel(MILESTONE_REWARD_CHANNEL_ID)
        if channel is None:
            channel = await self.bot.fetch_channel(MILESTONE_REWARD_CHANNEL_ID)
        if not isinstance(channel, discord.abc.Messageable):
            await interaction.followup.send(
                "❌ The milestone reward channel is not messageable.",
                ephemeral=True,
            )
            return
        await channel.send(
            embed=milestone_reward_embed(
                interaction.user.mention,
                milestone_number=6,
                reached_hours=24,
                reward_points=300,
                new_balance=current_balance + 300,
                preview=True,
            ),
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False,
            ),
        )
        await interaction.followup.send(
            f"✅ Reward-message preview posted in <#{MILESTONE_REWARD_CHANNEL_ID}>. No points were awarded.",
            ephemeral=True,
        )

    @discord.app_commands.command(
        name="milestonebackfillpreview",
        description="Preview historical milestone rewards for every tracked player.",
    )
    @discord.app_commands.default_permissions(administrator=True)
    @discord.app_commands.guild_only()
    async def milestone_backfill_preview(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await preview_milestone_backfill()
        eligible = [row for row in rows if int(row["points"]) > 0]
        total_points = sum(int(row["points"]) for row in eligible)
        total_milestones = sum(int(row["unpaid_milestones"]) for row in eligible)

        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            (
                "Discord name",
                "Discord ID",
                "SteamID",
                "Total playtime",
                "Completed milestones",
                "Unpaid milestones",
                "Points owed",
                "Current balance",
                "Projected balance",
            )
        )
        guild = interaction.guild
        for row in rows:
            member = None
            if guild is not None and str(row["discord_id"]).isdigit():
                member = guild.get_member(int(row["discord_id"]))
            writer.writerow(
                (
                    member.display_name if member is not None else "Unknown",
                    row["discord_id"],
                    row["steam_id"],
                    _format_playtime(row["total_seconds"]),
                    row["completed_milestones"],
                    row["unpaid_milestones"],
                    row["points"],
                    row["current_balance"],
                    row["projected_balance"],
                )
            )
        report = output.getvalue().encode("utf-8-sig")
        embed = discord.Embed(
            title="🏆 MILESTONE BACKFILL PREVIEW",
            description=(
                "This is a read-only preview. **No points were awarded.**\n\n"
                f"**Tracked linked players:** {len(rows):,}\n"
                f"**Players receiving points:** {len(eligible):,}\n"
                f"**Completed milestones:** {total_milestones:,}\n"
                f"**Total points to backtrack:** {total_points:,}"
            ),
            color=discord.Color.gold(),
        )
        await interaction.followup.send(
            embed=embed,
            file=discord.File(io.BytesIO(report), filename="milestone-backfill-preview.csv"),
            ephemeral=True,
        )

    @discord.app_commands.command(
        name="milestonebackfillapply",
        description="Apply all previewed historical milestone rewards exactly once.",
    )
    @discord.app_commands.describe(confirmation="Type APPLY to confirm the backfill")
    @discord.app_commands.default_permissions(administrator=True)
    @discord.app_commands.guild_only()
    async def milestone_backfill_apply(
        self,
        interaction: discord.Interaction,
        confirmation: str,
    ):
        if confirmation.strip().upper() != "APPLY":
            await interaction.response.send_message(
                "❌ Backfill cancelled. The confirmation must be exactly `APPLY`.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        awards = await award_due_milestones(source="backfill")
        channel = self.bot.get_channel(MILESTONE_REWARD_CHANNEL_ID)
        if channel is None:
            channel = await self.bot.fetch_channel(MILESTONE_REWARD_CHANNEL_ID)
        posted = 0
        for award in awards:
            await channel.send(
                embed=milestone_reward_embed(
                    f"<@{award['discord_id']}>",
                    milestone_number=int(award["highest_milestone"]),
                    reached_hours=int(award["reached_hours"]),
                    reward_points=int(award["points"]),
                    new_balance=int(award["new_balance"]),
                ),
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                ),
            )
            posted += 1
        await interaction.followup.send(
            (
                f"✅ Backfill complete: **{len(awards):,} players** received "
                f"**{sum(int(item['points']) for item in awards):,} points**. "
                f"Posted **{posted:,}** silent reward messages. Running this command "
                "again cannot repay recorded milestones."
            ),
            ephemeral=True,
        )

    @discord.app_commands.command(
        name="profile",
        description="View your linked player profile",
    )
    async def profile(self, interaction: discord.Interaction):
        player = await get_profile(str(interaction.user.id))
        if not player:
            await interaction.response.send_message(
                "❌ You do not have a linked SteamID yet.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🦖 miniEniac Player Profile",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Discord",
            value=interaction.user.mention,
            inline=False,
        )
        embed.add_field(
            name="SteamID",
            value=f"`{player.steam_id}`",
            inline=False,
        )
        embed.add_field(
            name="Evrima Name",
            value=player.evrima_name,
            inline=False,
        )
        embed.add_field(name="Status", value="Linked ✅", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class PlayerLinkView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Link Steam ID",
        emoji="🔗",
        style=discord.ButtonStyle.green,
        custom_id="link_steam",
    )
    async def link_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        await interaction.response.send_modal(SteamLinkModal())


class SelfSlayConfirmView(discord.ui.View):
    def __init__(self, discord_id: int, steam_id: str):
        super().__init__(timeout=60)
        self.discord_id = int(discord_id)
        self.steam_id = str(steam_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.discord_id:
            return True
        await interaction.response.send_message(
            "❌ This confirmation belongs to another player.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="Slay my dino",
        emoji="☠️",
        style=discord.ButtonStyle.red,
    )
    async def confirm_slay(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            await run_self_slay(self.steam_id)
        except DinoStorageError as error:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
            return
        except Exception:
            await interaction.followup.send(
                "❌ The game server did not complete the Slay request.",
                ephemeral=True,
            )
            return
        self.stop()
        await interaction.followup.send(
            "✅ Your current dino has been slayed.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.gray,
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        self.stop()
        await interaction.response.edit_message(
            content="Slay cancelled.",
            view=None,
        )


class PlayerPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Milestones",
        emoji="🏆",
        style=discord.ButtonStyle.green,
        custom_id="player-panel:milestones",
    )
    async def milestones(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        player = await _linked_player(interaction)
        if player is None:
            return
        total_seconds = await get_total_playtime(str(player.steam_id))
        await interaction.response.send_message(
            embed=milestones_embed(total_seconds),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Prime Status",
        emoji="📑",
        style=discord.ButtonStyle.blurple,
        custom_id="player-panel:prime-status",
    )
    async def prime_status(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        player = await _linked_player(interaction)
        if player is None:
            return
        status = await get_prime_status(str(player.steam_id))
        if status is None:
            await interaction.response.send_message(
                "❌ No Prime status was found for your current dino.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=prime_status_embed(status),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Slay",
        emoji="☠️",
        style=discord.ButtonStyle.red,
        custom_id="player-panel:slay",
    )
    async def slay(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        player = await _linked_player(interaction)
        if player is None:
            return
        await interaction.response.send_message(
            (
                "Are you sure you want to slay your current in-game dino? "
                "This cannot be undone."
            ),
            view=SelfSlayConfirmView(
                interaction.user.id,
                str(player.steam_id),
            ),
            ephemeral=True,
        )


class SteamLinkModal(discord.ui.Modal, title="Link Steam Account"):
    steam_id = discord.ui.TextInput(
        label="SteamID64",
        placeholder="7656119xxxxxxxxxx",
        required=True,
        min_length=17,
        max_length=17,
    )

    async def on_submit(self, interaction: discord.Interaction):
        steam_id = self.steam_id.value.strip()
        if not validate_steam_id(steam_id):
            await interaction.response.send_message(
                "❌ That doesn't look like a valid SteamID64.",
                ephemeral=True,
            )
            return

        success, message = await link_player(
            discord_id=str(interaction.user.id),
            steam_id=steam_id,
        )
        if success:
            await interaction.response.send_message(
                f"✅ Your SteamID **{steam_id}** has been linked successfully!",
                ephemeral=True,
            )
            await send_admin_log(
                interaction.client,
                "steam_link",
                embed=steam_link_embed(interaction.user, steam_id),
            )
        else:
            await interaction.response.send_message(
                f"❌ {message}",
                ephemeral=True,
            )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
    ):
        import traceback

        traceback.print_exception(type(error), error, error.__traceback__)
        if interaction.response.is_done():
            await interaction.followup.send(
                "❌ An unexpected error occurred.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "❌ An unexpected error occurred.",
                ephemeral=True,
            )


async def setup(bot):
    bot.add_view(PlayerLinkView())
    bot.add_view(PlayerPanelView())
    await bot.add_cog(Player(bot))
