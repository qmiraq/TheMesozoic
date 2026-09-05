from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from bot.cogs.dino_storage import (
    InventoryOverviewView,
    dinosaur_detail_embed,
    inventory_embed,
)
from bot.cogs.player import MILESTONE_REWARD_CHANNEL_ID, milestone_reward_embed
from bot.services.admin_log_service import (
    points_embed,
    replacement_embed,
    send_admin_log,
)
from bot.services.database_service import get_player_by_discord
from bot.services.dinosaur_render_service import dinosaur_render_file, render_message_id
from bot.services.dino_storage_service import list_inventory
from bot.services.economy_service import (
    ADMIN_MUTATION_SLOTS,
    EconomyError,
    MAXIMUM_SALE_HOURS,
    MINIMUM_PRICE,
    MINIMUM_SALE_HOURS,
    PLAYABLE_SPECIES,
    adjust_points,
    attach_market_message,
    award_due_milestones,
    award_online_playtime,
    break_playtime_sessions,
    cancel_market_sale,
    create_market_sale,
    create_shop_listing,
    deactivate_shop_listing,
    expire_market_sales,
    get_points,
    grant_admin_dinosaur,
    init_economy_database,
    list_active_market_sales,
    list_mutation_catalog,
    list_seller_sales,
    list_shop_listings,
    purchase_market_sale,
    purchase_shop_listing,
    resolve_linked_player,
)
from bot.services.rcon_api_service import get_players


MARKETPLACE_CHANNEL_ID = 1529641782563045556
LOGGER = logging.getLogger(__name__)


async def _post_milestone_award(bot: commands.Bot, award: dict) -> None:
    channel = bot.get_channel(MILESTONE_REWARD_CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(MILESTONE_REWARD_CHANNEL_ID)
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


def _player_list(value) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, (dict, str))]
    if isinstance(value, dict):
        for key in ("players", "Players", "data", "result"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, (dict, str))]
    return []


def economy_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="THE MESOZOIC ECONOMY",
        description=(
            "Earn **10 points for every 5 minutes** played on the server. "
            "Spend your points on shop dinos or trade parked dinos with other players. "
            "Be sure to link your account here <#1527247945798516786> to unlock all of "
            "these features!"
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="Features",
        value=(
            "- **Check Points** - Check your current amount of points.\n"
            "- **Dino Shop** - Purchase dinos from the server shop.\n"
            "- **Create Sale** - List one of your parked dinos for other players to buy.\n"
            "- **Cancel Sale** - Return a listed dino back to your garage."
        ),
        inline=False,
    )
    return embed


def admin_panel_embed() -> discord.Embed:
    return discord.Embed(
        title="ECONOMY ADMIN PANEL",
        description="Manage player points and the permanent dinosaur shop.",
        color=discord.Color.dark_gold(),
    )


def _snapshot(dinosaur: dict) -> dict:
    try:
        value = json.loads(dinosaur.get("serialized_player_data") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sale_dino_description(dinosaur: dict) -> str:
    snapshot = _snapshot(dinosaur)
    sex = str(dinosaur.get("sex") or "").strip().lower()
    if sex not in {"male", "female"}:
        sex = "female" if snapshot.get("isFemale") is True else "male"
    entombments = max(0, min(3, int(snapshot.get("elderStacks") or 0)))
    return f"{sex.title()} • Entombed {entombments}/3"


def _mutations_from_listing(listing: dict) -> list[str]:
    return [str(listing.get(f"mutation{index}") or "") for index in range(1, 5)]


def _shop_listing_mutation_summary(listing: dict) -> str:
    return "Mutations chosen when the Shop Token is unparked."


def dino_shop_embed(listings: list[dict]) -> discord.Embed:
    ordered = sorted(
        listings,
        key=lambda item: (
            int(item.get("price") or 0),
            str(item.get("species") or "").casefold(),
            int(item.get("id") or 0),
        ),
    )
    visible = ordered[:25]
    species_width = max(
        22,
        max((len(str(item["species"])) for item in visible), default=1),
    )
    price_width = max((len(f"{int(item['price']):,}") for item in visible), default=1)
    lines = [
        f"{str(item['species']):<{species_width}} - {int(item['price']):>{price_width},} points"
        for item in visible
    ]
    embed = discord.Embed(
        title="DINO SHOP",
        description=(
            "- Bought dinos cannot be refunded so make sure to spend your points wisely.\n"
            "- Keep in mind that bought dinos can only be unparked at your current location.\n"
            "- The dinos keep your current skin after unparking."
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="**LISTED DINOS**",
        value="```text\n" + "\n".join(lines) + "\n```",
        inline=False,
    )
    if len(ordered) > len(visible):
        embed.set_footer(text=f"Showing the 25 cheapest of {len(ordered)} shop listings")
    return embed


def shop_listing_embed(listing: dict, current_points: int) -> discord.Embed:
    price = int(listing["price"])
    embed = discord.Embed(
        title=f"👑 {listing['species']}",
        description=(
            f"**Price:** {price:,} points\n"
            f"**Points after transaction:** {int(current_points):,} → "
            f"{int(current_points) - price:,}"
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="Listed Dino Info",
        value=(
            "**Growth:** 75%\n"
            "**Status:** Prime Elder 👑\n"
            "**Gender:** Male\n"
            "**Entombments:** 0/3\n"
            "**Prime Tasks:** 10/10"
        ),
        inline=False,
    )
    embed.add_field(name="🧬 Mutations", value="Choose four mutations when unparking this Shop Token.", inline=False)
    return embed


def market_listing_embed(sale: dict) -> discord.Embed:
    embed = dinosaur_detail_embed(sale)
    expires_at = datetime.fromisoformat(str(sale["expires_at"]))
    expires_timestamp = int(expires_at.timestamp())
    embed.description = (
        f"**Listed by:** <@{sale['seller_discord_id']}>\n"
        f"**Price:** {int(sale['price']):,} points\n"
        f"**Expires:** <t:{expires_timestamp}:R>"
    )
    return embed


async def _market_render_file(
    bot: commands.Bot,
    species: str,
) -> discord.File | None:
    return await dinosaur_render_file(bot, species)


async def _send_market_listing_message(
    bot: commands.Bot,
    channel,
    sale: dict,
) -> discord.Message:
    embed = market_listing_embed(sale)
    render_file = await _market_render_file(bot, str(sale.get("species") or ""))
    if render_file is not None:
        embed.set_image(url=f"attachment://{render_file.filename}")
        try:
            return await channel.send(
                embed=embed,
                file=render_file,
                view=MarketSaleView(int(sale["sale_id"])),
            )
        except (discord.HTTPException, discord.Forbidden):
            LOGGER.exception(
                "Could not attach the %s render to marketplace sale %s",
                sale.get("species"),
                sale.get("sale_id"),
            )
            render_file.close()
    return await channel.send(
        embed=market_listing_embed(sale),
        view=MarketSaleView(int(sale["sale_id"])),
    )


async def _linked_player(interaction: discord.Interaction):
    player = await get_player_by_discord(str(interaction.user.id))
    if player is None:
        raise EconomyError("Link your SteamID before using the economy.")
    return player


def _cancel_scheduled_expiration(bot: commands.Bot, sale_id: int) -> None:
    economy_cog = bot.get_cog("Economy")
    if economy_cog is not None:
        economy_cog.cancel_market_expiration(int(sale_id))


async def _delete_market_message(bot: commands.Bot, sale: dict) -> None:
    if not sale.get("message_id") or not sale.get("channel_id"):
        return
    try:
        channel = bot.get_channel(int(sale["channel_id"]))
        if channel is None:
            channel = await bot.fetch_channel(int(sale["channel_id"]))
        message = await channel.fetch_message(int(sale["message_id"]))
        await message.delete()
    except (discord.HTTPException, discord.NotFound, discord.Forbidden):
        return


async def _refresh_market_message_view(bot: commands.Bot, sale: dict) -> None:
    if not sale.get("message_id") or not sale.get("channel_id"):
        return
    try:
        channel = bot.get_channel(int(sale["channel_id"]))
        if channel is None:
            channel = await bot.fetch_channel(int(sale["channel_id"]))
        message = await channel.fetch_message(int(sale["message_id"]))
        if not message.attachments and render_message_id(str(sale.get("species") or "")):
            render_file = await _market_render_file(
                bot,
                str(sale.get("species") or ""),
            )
            if render_file is not None:
                embed = market_listing_embed(sale)
                embed.set_image(url=f"attachment://{render_file.filename}")
                await message.edit(
                    embed=embed,
                    attachments=[render_file],
                    view=MarketSaleView(int(sale["sale_id"])),
                )
                return
        await message.edit(view=MarketSaleView(int(sale["sale_id"])))
    except (discord.HTTPException, discord.NotFound, discord.Forbidden):
        return


class ShopPurchaseView(discord.ui.View):
    def __init__(self, listing: dict):
        super().__init__(timeout=120)
        self.listing = listing

    @discord.ui.button(label="Buy Dino", style=discord.ButtonStyle.success)
    async def buy(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            player = await _linked_player(interaction)
            result = await purchase_shop_listing(
                int(self.listing["id"]),
                str(interaction.user.id),
                str(player.steam_id),
            )
        except EconomyError as error:
            await interaction.edit_original_response(
                content=f"❌ {error}",
                embed=None,
                view=None,
                attachments=[],
            )
            return
        await interaction.edit_original_response(
            content=(
                f"✅ Purchased **{result['species']}** for **{result['price']:,} points**.\n"
                f"It is now in your Dino Garage. Balance: **{result['balance']:,} points**."
            ),
            embed=None,
            view=None,
            attachments=[],
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Purchase cancelled.",
            embed=None,
            view=None,
            attachments=[],
        )


class ShopListingSelect(discord.ui.Select):
    def __init__(self, listings: list[dict]):
        self.listings = {str(item["id"]): item for item in listings[:25]}
        options = [
            discord.SelectOption(
                label=f"{item['species']} — {int(item['price']):,} points"[:100],
                value=str(item["id"]),
                description="75% Prime • Male • 10/10 Prime tasks"[:100],
                emoji="👑",
            )
            for item in listings[:25]
        ]
        super().__init__(placeholder="Choose a dino to buy...", options=options)

    async def callback(self, interaction: discord.Interaction):
        listing = self.listings[self.values[0]]
        try:
            player = await _linked_player(interaction)
            current_points = await get_points(
                str(interaction.user.id), str(player.steam_id)
            )
        except EconomyError as error:
            await interaction.response.edit_message(
                content=f"❌ {error}", embed=None, view=None
            )
            return
        embed = shop_listing_embed(listing, current_points)
        render_file = await _market_render_file(
            interaction.client,
            str(listing.get("species") or ""),
        )
        if render_file is not None:
            embed.set_image(url=f"attachment://{render_file.filename}")
            await interaction.response.edit_message(
                content=None,
                embed=embed,
                view=ShopPurchaseView(listing),
                attachments=[render_file],
            )
            return
        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=ShopPurchaseView(listing),
            attachments=[],
        )


class ShopListingSelectView(discord.ui.View):
    def __init__(self, listings: list[dict]):
        super().__init__(timeout=120)
        self.add_item(ShopListingSelect(listings))


class SaleDetailsModal(discord.ui.Modal, title="Create Dino Sale"):
    price = discord.ui.TextInput(
        label="Price in points",
        placeholder="Minimum 10",
        min_length=1,
        max_length=10,
    )
    duration = discord.ui.TextInput(
        label="Duration in hours",
        placeholder="1 to 24",
        min_length=1,
        max_length=2,
    )

    def __init__(self, bot: commands.Bot, dinosaur: dict, steam_id: str):
        super().__init__()
        self.bot = bot
        self.dinosaur = dinosaur
        self.steam_id = str(steam_id)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price = int(str(self.price.value).strip())
            duration = int(str(self.duration.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "Price and duration must be whole numbers.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            sale = await create_market_sale(
                str(interaction.user.id),
                self.steam_id,
                int(self.dinosaur["id"]),
                price,
                duration,
            )
            channel = self.bot.get_channel(MARKETPLACE_CHANNEL_ID)
            if channel is None:
                channel = await self.bot.fetch_channel(MARKETPLACE_CHANNEL_ID)
            if not hasattr(channel, "send"):
                raise EconomyError("The marketplace channel is unavailable.")
            message = await _send_market_listing_message(self.bot, channel, sale)
            await attach_market_message(int(sale["sale_id"]), channel.id, message.id)
            sale["channel_id"] = str(channel.id)
            sale["message_id"] = str(message.id)
            economy_cog = self.bot.get_cog("Economy")
            if economy_cog is not None:
                economy_cog.schedule_market_expiration(sale)
        except Exception as error:
            if "sale" in locals():
                try:
                    await cancel_market_sale(int(sale["sale_id"]), str(interaction.user.id))
                except EconomyError:
                    pass
            await interaction.edit_original_response(content=f"❌ Could not create sale: {error}")
            return
        await interaction.edit_original_response(
            content=(
                f"✅ Listed **{sale['species']}** for **{price:,} points** "
                f"for **{duration} hour{'s' if duration != 1 else ''}** in <#{MARKETPLACE_CHANNEL_ID}>."
            )
        )


class SaleDinosaurSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, dinosaurs: list[dict], steam_id: str):
        self.bot = bot
        self.steam_id = str(steam_id)
        self.dinosaurs = {str(item["id"]): item for item in dinosaurs[:25]}
        options = [
            discord.SelectOption(
                label=f"{item['species']} — {float(item.get('growth') or 0) * 100:.1f}%"[:100],
                value=str(item["id"]),
                description=_sale_dino_description(item),
                emoji="👑" if json_prime(item) else "🐢",
            )
            for item in dinosaurs[:25]
        ]
        species = str(dinosaurs[0].get("species") or "dino") if dinosaurs else "dino"
        super().__init__(placeholder=f"Choose a parked {species}..."[:150], options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            SaleDetailsModal(self.bot, self.dinosaurs[self.values[0]], self.steam_id)
        )


def json_prime(dinosaur: dict) -> bool:
    return _snapshot(dinosaur).get("isPrime") is True


class SaleDinosaurSelectView(discord.ui.View):
    def __init__(self, bot: commands.Bot, dinosaurs: list[dict], steam_id: str):
        super().__init__(timeout=120)
        self.add_item(SaleDinosaurSelect(bot, dinosaurs, steam_id))


class SaleSpeciesSelect(discord.ui.Select):
    def __init__(self, bot: commands.Bot, dinosaurs: list[dict], steam_id: str):
        self.bot = bot
        self.dinosaurs = dinosaurs
        self.steam_id = str(steam_id)
        species = sorted(
            {str(item.get("species") or "Unknown") for item in dinosaurs},
            key=str.casefold,
        )
        options = [
            discord.SelectOption(
                label=name,
                value=name,
                description=(
                    f"{sum(1 for item in dinosaurs if str(item.get('species') or 'Unknown') == name)} "
                    "parked"
                ),
            )
            for name in species[:25]
        ]
        super().__init__(placeholder="Choose a dino species...", options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_species = self.values[0]
        dinosaurs = [
            item
            for item in self.dinosaurs
            if str(item.get("species") or "Unknown") == selected_species
        ]
        await interaction.response.edit_message(
            content=f"Choose a parked **{selected_species}** to list for sale:",
            view=SaleDinosaurSelectView(self.bot, dinosaurs, self.steam_id),
        )


class SaleSpeciesSelectView(discord.ui.View):
    def __init__(self, bot: commands.Bot, dinosaurs: list[dict], steam_id: str):
        super().__init__(timeout=120)
        self.add_item(SaleSpeciesSelect(bot, dinosaurs, steam_id))


class CancelSaleSelect(discord.ui.Select):
    def __init__(self, sales: list[dict]):
        self.sales = {str(item["sale_id"]): item for item in sales[:25]}
        options = [
            discord.SelectOption(
                label=f"{item['species']} — {int(item['price']):,} points"[:100],
                value=str(item["sale_id"]),
                description=_sale_dino_description(item),
            )
            for item in sales[:25]
        ]
        super().__init__(placeholder="Choose a sale to cancel...", options=options)

    async def callback(self, interaction: discord.Interaction):
        sale = self.sales[self.values[0]]
        try:
            await cancel_market_sale(int(sale["sale_id"]), str(interaction.user.id))
        except EconomyError as error:
            await interaction.response.edit_message(content=f"❌ {error}", view=None)
            return
        await interaction.response.edit_message(
            content=f"✅ Cancelled the **{sale['species']}** sale. The dino is back in your Garage.",
            view=None,
        )
        _cancel_scheduled_expiration(interaction.client, int(sale["sale_id"]))
        await _delete_market_message(interaction.client, sale)


class CancelSaleSelectView(discord.ui.View):
    def __init__(self, sales: list[dict]):
        super().__init__(timeout=120)
        self.add_item(CancelSaleSelect(sales))


class MarketSaleView(discord.ui.View):
    def __init__(self, sale_id: int):
        super().__init__(timeout=None)
        self.sale_id = int(sale_id)
        button = discord.ui.Button(
            label="Buy Dino",
            emoji="💶",
            style=discord.ButtonStyle.success,
            custom_id=f"economy:market-buy:{self.sale_id}",
        )
        button.callback = self.buy
        self.add_item(button)

    async def buy(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            player = await _linked_player(interaction)
            result = await purchase_market_sale(
                self.sale_id,
                str(interaction.user.id),
                str(player.steam_id),
            )
        except EconomyError as error:
            await interaction.edit_original_response(content=f"❌ {error}")
            return
        await interaction.edit_original_response(
            content=(
                f"✅ Dino purchased for **{result['price']:,} points** and added to your Garage. "
                f"Balance: **{result['buyer_balance']:,} points**."
            )
        )
        _cancel_scheduled_expiration(interaction.client, self.sale_id)
        try:
            await interaction.message.delete()
        except (discord.HTTPException, discord.NotFound):
            pass


class EconomyPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Check Points",
        emoji="💲",
        style=discord.ButtonStyle.primary,
        custom_id="economy:view-points",
    )
    async def check_balance(self, interaction: discord.Interaction, _button: discord.ui.Button):
        try:
            player = await _linked_player(interaction)
            balance = await get_points(str(interaction.user.id), str(player.steam_id))
        except EconomyError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"💲 You currently have **{balance:,} points**.", ephemeral=True
        )

    @discord.ui.button(
        label="Dino Shop",
        emoji="💶",
        style=discord.ButtonStyle.success,
        custom_id="economy:buy-dinos",
    )
    async def buy_dinos(self, interaction: discord.Interaction, _button: discord.ui.Button):
        try:
            await _linked_player(interaction)
        except EconomyError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        listings = sorted(
            await list_shop_listings(),
            key=lambda item: (
                int(item.get("price") or 0),
                str(item.get("species") or "").casefold(),
                int(item.get("id") or 0),
            ),
        )
        if not listings:
            await interaction.response.send_message(
                "The dinosaur shop currently has no listings.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=dino_shop_embed(listings),
            view=ShopListingSelectView(listings),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Create Sale",
        emoji="🏷️",
        style=discord.ButtonStyle.secondary,
        custom_id="economy:create-sale",
    )
    async def create_sale(self, interaction: discord.Interaction, _button: discord.ui.Button):
        try:
            player = await _linked_player(interaction)
        except EconomyError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        dinosaurs = await list_inventory(str(interaction.user.id))
        if not dinosaurs:
            await interaction.response.send_message(
                "You have no parked dinos available to sell.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Choose a dino species to list for sale:",
            view=SaleSpeciesSelectView(interaction.client, dinosaurs, str(player.steam_id)),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Cancel Sale",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="economy:cancel-sale",
    )
    async def cancel_sale(self, interaction: discord.Interaction, _button: discord.ui.Button):
        sales = await list_seller_sales(str(interaction.user.id))
        if not sales:
            await interaction.response.send_message(
                "You have no active dinosaur sales.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Choose the sale you want to cancel:",
            view=CancelSaleSelectView(sales),
            ephemeral=True,
        )


class PointAdjustmentModal(discord.ui.Modal, title="Adjust Player Points"):
    discord_user = discord.ui.TextInput(
        label="Discord ID (use this or SteamID)",
        placeholder="Leave empty when using SteamID",
        required=False,
        max_length=22,
    )
    steam_user = discord.ui.TextInput(
        label="SteamID (use this or Discord ID)",
        placeholder="Leave empty when using Discord ID",
        required=False,
        max_length=17,
    )
    amount = discord.ui.TextInput(
        label="Point adjustment",
        placeholder="Example: 100 or -100",
        min_length=1,
        max_length=11,
    )

    async def on_submit(self, interaction: discord.Interaction):
        discord_id = str(self.discord_user.value or "").strip().strip("<@!>")
        steam_id = str(self.steam_user.value or "").strip()
        if bool(discord_id) == bool(steam_id):
            await interaction.response.send_message(
                "Enter exactly one Discord ID or SteamID.", ephemeral=True
            )
            return
        try:
            delta = int(str(self.amount.value).strip())
        except ValueError:
            await interaction.response.send_message("Adjustment must be a whole number.", ephemeral=True)
            return
        try:
            player = await resolve_linked_player(discord_id=discord_id, steam_id=steam_id)
        except EconomyError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        if player is None:
            await interaction.response.send_message(
                "No linked player was found for that ID.", ephemeral=True
            )
            return
        target_id = str(player["discord_id"])
        target_steam_id = str(player["steam_id"])
        try:
            balance = await adjust_points(
                target_id,
                target_steam_id,
                delta,
                str(interaction.user.id),
            )
        except EconomyError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ Adjusted <@{target_id}> (`{target_steam_id}`) by **{delta:+,} points**. "
            f"New balance: **{balance:,}**.",
            ephemeral=True,
        )
        await send_admin_log(
            interaction.client,
            "points",
            embed=points_embed(
                interaction.user,
                target_id,
                target_steam_id,
                delta,
                balance,
            ),
        )


class SpeciesSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=species, value=species) for species in PLAYABLE_SPECIES]
        super().__init__(placeholder="Choose a dinosaur class...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ShopPriceModal(self.values[0], []))


class SpeciesSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(SpeciesSelect())


class ShopPriceModal(discord.ui.Modal, title="Create Shop Listing"):
    price = discord.ui.TextInput(
        label="Price in points",
        placeholder="Minimum 10",
        min_length=1,
        max_length=10,
    )

    def __init__(self, species: str, mutations: list[str]):
        super().__init__()
        self.species = species
        self.mutations = mutations

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price = int(str(self.price.value).strip())
            await create_shop_listing(
                self.species,
                self.mutations,
                price,
                str(interaction.user.id),
            )
        except (ValueError, EconomyError) as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ Created **{self.species} Shop Token** listing for **{price:,} points**.",
            ephemeral=True,
        )


def _mutation_summary(species: str, selected: list[str]) -> str:
    lines = [
        f"**Slot{index}:** {value or 'Empty'}"
        for index, value in enumerate(selected, start=1)
    ]
    return f"**{species}**\n" + "\n".join(lines)


class MutationRangeSelect(discord.ui.Select):
    def __init__(
        self,
        parent_view: "MutationListingView",
        range_label: str,
        choices: list[str],
        row: int,
        include_empty: bool = False,
    ):
        self.parent_view = parent_view
        options = []
        if include_empty:
            options.append(
                discord.SelectOption(
                    label=f"Leave Slot{parent_view.slot} empty",
                    value="__empty__",
                )
            )
        options.extend(
            discord.SelectOption(label=name[:100], value=name)
            for name in choices
        )
        super().__init__(
            placeholder=f"Choose a Slot{parent_view.slot} mutation {range_label}",
            options=options,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        mutation = "" if self.values[0] == "__empty__" else self.values[0]
        if mutation and mutation in self.parent_view.selected:
            await interaction.response.send_message(
                "The same mutation cannot be used in more than one active slot.",
                ephemeral=True,
            )
            return
        await self.parent_view.advance(interaction, mutation)


class MutationListingView(discord.ui.View):
    def __init__(
        self,
        species: str,
        mutations: list[str],
        slot: int = 1,
        selected: list[str] | None = None,
    ):
        super().__init__(timeout=240)
        self.species = species
        self.mutations = sorted(set(mutations), key=str.casefold)
        self.slot = slot
        self.selected = list(selected or ["", "", "", ""])
        a_to_m = [
            name for name in self.mutations
            if name and "A" <= name[0].upper() <= "M"
        ]
        n_to_z = [name for name in self.mutations if name not in a_to_m]
        self.add_item(MutationRangeSelect(self, "A-M", a_to_m, 0, include_empty=True))
        self.add_item(MutationRangeSelect(self, "N-Z", n_to_z, 1))

    async def advance(self, interaction: discord.Interaction, mutation: str):
        selected = list(self.selected)
        selected[self.slot - 1] = mutation
        if self.slot < 4:
            await interaction.response.edit_message(
                content=(
                    _mutation_summary(self.species, selected)
                    + f"\n\nChoose a Slot{self.slot + 1} mutation:"
                ),
                view=MutationListingView(
                    self.species, self.mutations, self.slot + 1, selected
                ),
            )
            return
        await interaction.response.edit_message(
            content=_mutation_summary(self.species, selected),
            view=MutationSummaryView(self.species, selected),
        )


class MutationSummaryView(discord.ui.View):
    def __init__(self, species: str, selected: list[str]):
        super().__init__(timeout=240)
        self.species = species
        self.selected = list(selected)

    @discord.ui.button(label="Set Price", style=discord.ButtonStyle.success)
    async def set_price(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(ShopPriceModal(self.species, self.selected))


class RemoveShopSelect(discord.ui.Select):
    def __init__(self, listings: list[dict]):
        self.listings = {str(item["id"]): item for item in listings[:25]}
        options = [
            discord.SelectOption(
                label=f"{item['species']} — {int(item['price']):,} points"[:100],
                value=str(item["id"]),
                description=_shop_listing_mutation_summary(item),
            )
            for item in listings[:25]
        ]
        super().__init__(placeholder="Choose a shop listing to remove...", options=options)

    async def callback(self, interaction: discord.Interaction):
        listing = self.listings[self.values[0]]
        removed = await deactivate_shop_listing(int(listing["id"]))
        await interaction.response.edit_message(
            content=(
                f"✅ Removed **{listing['species']}** shop listing."
                if removed else "That shop listing was already removed."
            ),
            view=None,
        )


class RemoveShopView(discord.ui.View):
    def __init__(self, listings: list[dict]):
        super().__init__(timeout=120)
        self.add_item(RemoveShopSelect(listings))


def dino_storage_admin_panel_embed() -> discord.Embed:
    return discord.Embed(
        title="DINO STORAGE ADMIN PANEL",
        description=(
            "Create replacement dinos for players who lost them to rulebreaks or bugs. "
            "Created dinos will go straight to the player's inventory"
        ),
        color=discord.Color.dark_green(),
    )


def _admin_grant_mutation_limit(config: dict) -> int:
    return max(
        0,
        min(
            len(ADMIN_MUTATION_SLOTS),
            int(config.get("mutation_count", 4)),
        ),
    )


def _grant_mutation_lines(mutations: dict[str, str], slot_limit: int) -> str:
    groups = []
    for start in range(0, slot_limit, 4):
        lines = [
            f"**{label}:** {mutations.get(field) or 'Empty'}"
            for label, field in ADMIN_MUTATION_SLOTS[start : min(start + 4, slot_limit)]
        ]
        groups.append("\n".join(lines))
    return "\n\n".join(groups)


def _admin_grant_summary(config: dict, include_mutations: bool = False) -> str:
    status = "Prime Elder 👑" if config.get("is_prime") else "Frail Elder 🐢"
    gender = "Female" if config.get("is_female") else "Male"
    lines = [
        f"**Recipient:** <@{config['discord_id']}> (`{config['steam_id']}`)",
        f"**Class:** {config.get('species') or 'Not selected'}",
        f"**Growth:** {float(config.get('growth_percent', 100)):.1f}%",
        f"**Status:** {status}",
        f"**Gender:** {gender}",
        f"**Entombments:** {int(config.get('elder_stacks', 0))}/3",
        f"**Mutations:** {_admin_grant_mutation_limit(config)}/16",
        f"**Hunger:** {float(config.get('hunger_percent', 100)):.1f}%",
        f"**Thirst:** {float(config.get('thirst_percent', 100)):.1f}%",
        (
            "**Nutrients:** "
            f"Carb {float(config.get('carb_percent', 0)):.1f}% • "
            f"Protein {float(config.get('protein_percent', 0)):.1f}% • "
            f"Lipid {float(config.get('lipid_percent', 0)):.1f}%"
        ),
    ]
    if include_mutations:
        lines.extend([
            "",
            "**Mutations**",
            _grant_mutation_lines(
                config.get("mutations", {}),
                _admin_grant_mutation_limit(config),
            ),
        ])
    return "\n".join(lines)


def _parse_admin_percent(value: str, name: str) -> float:
    try:
        number = float(str(value).strip())
    except ValueError as error:
        raise EconomyError(f"{name} must be a number from 0 to 100.") from error
    if not math.isfinite(number) or not 0 <= number <= 100:
        raise EconomyError(f"{name} must be between 0 and 100.")
    return number


class AdminGrantRecipientModal(discord.ui.Modal, title="Choose Dino Recipient"):
    discord_user = discord.ui.TextInput(
        label="Discord ID (use this or SteamID)",
        placeholder="Leave empty when using SteamID",
        required=False,
        max_length=22,
    )
    steam_user = discord.ui.TextInput(
        label="SteamID (use this or Discord ID)",
        placeholder="Leave empty when using Discord ID",
        required=False,
        max_length=17,
    )

    async def on_submit(self, interaction: discord.Interaction):
        discord_id = str(self.discord_user.value or "").strip().strip("<@!>")
        steam_id = str(self.steam_user.value or "").strip()
        if bool(discord_id) == bool(steam_id):
            await interaction.response.send_message(
                "Enter exactly one Discord ID or SteamID.", ephemeral=True
            )
            return
        player = await resolve_linked_player(discord_id=discord_id, steam_id=steam_id)
        if player is None:
            await interaction.response.send_message(
                "No linked player was found for that ID.", ephemeral=True
            )
            return
        config = {
            "discord_id": str(player["discord_id"]),
            "steam_id": str(player["steam_id"]),
            "is_prime": False,
            "is_female": False,
            "elder_stacks": 0,
            "mutation_count": 4,
            "growth_percent": 100.0,
            "hunger_percent": 100.0,
            "thirst_percent": 100.0,
            "carb_percent": 0.0,
            "protein_percent": 0.0,
            "lipid_percent": 0.0,
            "mutations": {field: "" for _label, field in ADMIN_MUTATION_SLOTS},
        }
        await interaction.response.send_message(
            "Choose the replacement dino class:",
            view=AdminGrantSpeciesView(config),
            ephemeral=True,
        )


class AdminInventoryRecipientModal(discord.ui.Modal, title="Open Dino Inventory"):
    discord_user = discord.ui.TextInput(
        label="Discord ID (use this or SteamID)",
        placeholder="Leave empty when using SteamID",
        required=False,
        max_length=22,
    )
    steam_user = discord.ui.TextInput(
        label="SteamID (use this or Discord ID)",
        placeholder="Leave empty when using Discord ID",
        required=False,
        max_length=17,
    )

    async def on_submit(self, interaction: discord.Interaction):
        discord_id = str(self.discord_user.value or "").strip().strip("<@!>")
        steam_id = str(self.steam_user.value or "").strip()
        if bool(discord_id) == bool(steam_id):
            await interaction.response.send_message(
                "Enter exactly one Discord ID or SteamID.",
                ephemeral=True,
            )
            return
        player = await resolve_linked_player(discord_id=discord_id, steam_id=steam_id)
        if player is None:
            await interaction.response.send_message(
                "No linked player was found for that ID.",
                ephemeral=True,
            )
            return
        owner_discord_id = str(player["discord_id"])
        dinosaurs = await list_inventory(owner_discord_id)
        if not dinosaurs:
            await interaction.response.send_message(
                "That player has no parked dinos.",
                ephemeral=True,
            )
            return
        member = (
            interaction.guild.get_member(int(owner_discord_id))
            if interaction.guild is not None
            else None
        )
        player_name = (
            member.display_name
            if member is not None
            else f"Player {owner_discord_id}"
        )
        await interaction.response.send_message(
            embed=inventory_embed(player_name, dinosaurs),
            view=InventoryOverviewView(
                owner_discord_id,
                player_name,
                dinosaurs,
                admin_mode=True,
            ),
            ephemeral=True,
        )


class AdminGrantSpeciesSelect(discord.ui.Select):
    def __init__(self, config: dict):
        self.config = config
        super().__init__(
            placeholder="Choose a dino class...",
            options=[discord.SelectOption(label=species, value=species) for species in PLAYABLE_SPECIES],
        )

    async def callback(self, interaction: discord.Interaction):
        self.config["species"] = self.values[0]
        await interaction.response.edit_message(
            content=(
                _admin_grant_summary(self.config)
                + "\n\nChoose status, gender, entombments, and mutation count:"
            ),
            view=AdminGrantProfileView(self.config),
        )


class AdminGrantSpeciesView(discord.ui.View):
    def __init__(self, config: dict):
        super().__init__(timeout=300)
        self.add_item(AdminGrantSpeciesSelect(config))


class AdminGrantProfileSelect(discord.ui.Select):
    def __init__(
        self,
        parent_view: "AdminGrantProfileView",
        setting: str,
        placeholder: str,
        options: list[discord.SelectOption],
        row: int,
    ):
        self.parent_view = parent_view
        self.setting = setting
        super().__init__(placeholder=placeholder, options=options, row=row)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if self.setting == "is_prime":
            self.parent_view.config[self.setting] = value == "prime"
        elif self.setting == "is_female":
            self.parent_view.config[self.setting] = value == "female"
        else:
            self.parent_view.config[self.setting] = int(value)
        await interaction.response.defer()


class AdminGrantProfileView(discord.ui.View):
    def __init__(self, config: dict):
        super().__init__(timeout=300)
        self.config = config
        self.add_item(AdminGrantProfileSelect(
            self, "is_prime", "Choose status...",
            [
                discord.SelectOption(label="Frail Elder", value="frail", default=True, emoji="🐢"),
                discord.SelectOption(label="Prime Elder", value="prime", emoji="👑"),
            ], 0,
        ))
        self.add_item(AdminGrantProfileSelect(
            self, "is_female", "Choose gender...",
            [
                discord.SelectOption(label="Male", value="male", default=True),
                discord.SelectOption(label="Female", value="female"),
            ], 1,
        ))
        self.add_item(AdminGrantProfileSelect(
            self, "elder_stacks", "Choose entombments...",
            [
                discord.SelectOption(
                    label=f"{value}/3 Entombs",
                    value=str(value),
                    default=value == 0,
                )
                for value in range(4)
            ], 2,
        ))
        self.add_item(AdminGrantProfileSelect(
            self, "mutation_count", "Choose mutation count...",
            [
                discord.SelectOption(
                    label=f"{value}/16 Mutations",
                    value=str(value),
                    default=value == 4,
                )
                for value in range(17)
            ], 3,
        ))

    @discord.ui.button(label="Set Growth and Needs", style=discord.ButtonStyle.success, row=4)
    async def set_stats(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(AdminGrantCoreStatsModal(self.config))


class AdminGrantCoreStatsModal(discord.ui.Modal, title="Replacement Dino Stats"):
    growth = discord.ui.TextInput(label="Growth %", default="100", max_length=6)
    hunger = discord.ui.TextInput(label="Hunger %", default="100", max_length=6)
    thirst = discord.ui.TextInput(label="Thirst %", default="100", max_length=6)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config

    async def on_submit(self, interaction: discord.Interaction):
        try:
            self.config.update({
                "growth_percent": _parse_admin_percent(self.growth.value, "Growth"),
                "hunger_percent": _parse_admin_percent(self.hunger.value, "Hunger"),
                "thirst_percent": _parse_admin_percent(self.thirst.value, "Thirst"),
            })
        except EconomyError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        await interaction.response.edit_message(
            content=_admin_grant_summary(self.config) + "\n\nSet all three nutrient values:",
            view=AdminGrantNutrientsView(self.config),
        )


class AdminGrantNutrientsModal(discord.ui.Modal, title="Replacement Dino Nutrients"):
    carb = discord.ui.TextInput(label="Carbohydrate nutrient %", default="0", max_length=6)
    protein = discord.ui.TextInput(label="Protein nutrient %", default="0", max_length=6)
    lipid = discord.ui.TextInput(label="Lipid nutrient %", default="0", max_length=6)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config

    async def on_submit(self, interaction: discord.Interaction):
        try:
            self.config.update({
                "carb_percent": _parse_admin_percent(self.carb.value, "Carbohydrate"),
                "protein_percent": _parse_admin_percent(self.protein.value, "Protein"),
                "lipid_percent": _parse_admin_percent(self.lipid.value, "Lipid"),
            })
        except EconomyError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        slot_limit = _admin_grant_mutation_limit(self.config)
        if slot_limit == 0:
            await interaction.response.edit_message(
                content=_admin_grant_summary(self.config, include_mutations=True),
                view=AdminGrantConfirmationView(self.config),
            )
            return
        catalog = await list_mutation_catalog(species=str(self.config["species"]))
        await interaction.response.edit_message(
            content=_admin_grant_summary(self.config) + "\n\nChoose a Slot1 mutation:",
            view=AdminGrantMutationView(self.config, catalog),
        )


class AdminGrantNutrientsView(discord.ui.View):
    def __init__(self, config: dict):
        super().__init__(timeout=300)
        self.config = config

    @discord.ui.button(label="Set Nutrients and Mutations", style=discord.ButtonStyle.success)
    async def continue_setup(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(AdminGrantNutrientsModal(self.config))


class AdminGrantMutationSelect(discord.ui.Select):
    def __init__(
        self,
        parent_view: "AdminGrantMutationView",
        range_label: str,
        choices: list[str],
        row: int,
        include_empty: bool = False,
    ):
        self.parent_view = parent_view
        label, _field = ADMIN_MUTATION_SLOTS[parent_view.slot_index]
        options = []
        if include_empty:
            options.append(discord.SelectOption(label=f"Leave {label} empty", value="__empty__"))
        options.extend(discord.SelectOption(label=name[:100], value=name) for name in choices)
        super().__init__(
            placeholder=f"Choose a {label} mutation {range_label}",
            options=options,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        mutation = "" if self.values[0] == "__empty__" else self.values[0]
        await self.parent_view.advance(interaction, mutation)


class AdminGrantMutationView(discord.ui.View):
    def __init__(self, config: dict, catalog: list[str], slot_index: int = 0):
        super().__init__(timeout=600)
        self.config = config
        self.catalog = sorted(set(catalog), key=str.casefold)
        self.slot_index = slot_index
        self.slot_limit = _admin_grant_mutation_limit(config)
        if not 0 <= self.slot_index < self.slot_limit:
            raise ValueError("Mutation slot is outside the selected mutation count")
        a_to_m = [name for name in self.catalog if name and "A" <= name[0].upper() <= "M"]
        n_to_z = [name for name in self.catalog if name not in a_to_m]
        self.add_item(AdminGrantMutationSelect(self, "A-M", a_to_m, 0, include_empty=True))
        self.add_item(AdminGrantMutationSelect(self, "N-Z", n_to_z, 1))

    async def advance(self, interaction: discord.Interaction, mutation: str):
        _label, field = ADMIN_MUTATION_SLOTS[self.slot_index]
        self.config["mutations"][field] = mutation
        next_index = self.slot_index + 1
        if next_index >= self.slot_limit:
            await interaction.response.edit_message(
                content=_admin_grant_summary(self.config, include_mutations=True),
                view=AdminGrantConfirmationView(self.config),
            )
            return
        next_label, _next_field = ADMIN_MUTATION_SLOTS[next_index]
        await interaction.response.edit_message(
            content=(
                _admin_grant_summary(self.config, include_mutations=True)
                + f"\n\nChoose a {next_label} mutation:"
            ),
            view=AdminGrantMutationView(self.config, self.catalog, next_index),
        )

    @discord.ui.button(
        label="Finish With Remaining Empty",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def finish_empty(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(
            content=_admin_grant_summary(self.config, include_mutations=True),
            view=AdminGrantConfirmationView(self.config),
        )


class AdminGrantConfirmationView(discord.ui.View):
    def __init__(self, config: dict):
        super().__init__(timeout=300)
        self.config = config

    @discord.ui.button(label="Give Dino", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await grant_admin_dinosaur(
                self.config["discord_id"], self.config["steam_id"], self.config["species"],
                self.config["growth_percent"], self.config["is_prime"],
                self.config["is_female"], self.config["elder_stacks"],
                self.config["mutation_count"],
                self.config["hunger_percent"], self.config["thirst_percent"],
                self.config["carb_percent"], self.config["protein_percent"],
                self.config["lipid_percent"], self.config["mutations"],
                str(interaction.user.id),
            )
        except EconomyError as error:
            await interaction.edit_original_response(content=f"❌ {error}", view=None)
            return
        await interaction.edit_original_response(
            content=(
                f"✅ Gave **{result['species']}** to <@{result['discord_id']}>. "
                "It is now in their Dino Garage."
            ),
            view=None,
        )
        await send_admin_log(
            interaction.client,
            "replacement",
            embed=replacement_embed(interaction.user, result),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(content="Dino grant cancelled.", view=None)


class DinoStorageAdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message("Administrator permission is required.", ephemeral=True)
        return False

    @discord.ui.button(
        label="Give Dino",
        emoji="🦖",
        style=discord.ButtonStyle.success,
        custom_id="dino-storage-admin:give-dino",
    )
    async def give_dino(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(AdminGrantRecipientModal())

    @discord.ui.button(
        label="Manage Inventory",
        emoji="📦",
        style=discord.ButtonStyle.primary,
        custom_id="dino-storage-admin:manage-inventory",
    )
    async def manage_inventory(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        await interaction.response.send_modal(AdminInventoryRecipientModal())


class EconomyAdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message("Administrator permission is required.", ephemeral=True)
        return False

    @discord.ui.button(
        label="Give Points",
        emoji="💲",
        style=discord.ButtonStyle.primary,
        custom_id="economy-admin:points",
    )
    async def give_points(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(PointAdjustmentModal())

    @discord.ui.button(
        label="Create a Shop Listing",
        emoji="➕",
        style=discord.ButtonStyle.success,
        custom_id="economy-admin:create-shop",
    )
    async def create_shop(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(
            "Choose the class for the new permanent shop listing:",
            view=SpeciesSelectView(),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Remove Shop Listing",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="economy-admin:remove-shop",
    )
    async def remove_shop(self, interaction: discord.Interaction, _button: discord.ui.Button):
        listings = await list_shop_listings()
        if not listings:
            await interaction.response.send_message("There are no active shop listings.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Choose a shop listing to remove:", view=RemoveShopView(listings), ephemeral=True
        )


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.market_expiration_tasks: dict[int, asyncio.Task] = {}
        self.market_view_refresh_task: asyncio.Task | None = None

    async def cog_load(self):
        # A bot restart must never reconnect a persisted playtime timestamp to a
        # later game-server session. The first authoritative RCON poll starts a
        # fresh interval for every player who is genuinely online.
        await break_playtime_sessions()
        for sale in await list_active_market_sales():
            if sale.get("message_id"):
                self.bot.add_view(
                    MarketSaleView(int(sale["sale_id"])),
                    message_id=int(sale["message_id"]),
                )
            self.schedule_market_expiration(sale)
        self.market_view_refresh_task = asyncio.create_task(
            self._refresh_active_market_views()
        )
        if not self.playtime_rewards.is_running():
            self.playtime_rewards.start()
        if not self.sale_expiration.is_running():
            self.sale_expiration.start()

    def cog_unload(self):
        self.playtime_rewards.cancel()
        self.sale_expiration.cancel()
        if self.market_view_refresh_task is not None:
            self.market_view_refresh_task.cancel()
            self.market_view_refresh_task = None
        for task in self.market_expiration_tasks.values():
            task.cancel()
        self.market_expiration_tasks.clear()

    async def _refresh_active_market_views(self) -> None:
        try:
            await self.bot.wait_until_ready()
            for sale in await list_active_market_sales():
                await _refresh_market_message_view(self.bot, sale)
        except asyncio.CancelledError:
            return

    def cancel_market_expiration(self, sale_id: int) -> None:
        task = self.market_expiration_tasks.pop(int(sale_id), None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def schedule_market_expiration(self, sale: dict) -> None:
        sale_id = int(sale["sale_id"])
        self.cancel_market_expiration(sale_id)
        expires_at = datetime.fromisoformat(str(sale["expires_at"]))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        delay = max(0.0, (expires_at - datetime.now(timezone.utc)).total_seconds())
        self.market_expiration_tasks[sale_id] = asyncio.create_task(
            self._expire_market_sale_after(sale_id, delay)
        )

    async def _expire_market_sale_after(self, sale_id: int, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            await self._expire_due_market_sales()
        except asyncio.CancelledError:
            return
        finally:
            current = self.market_expiration_tasks.get(int(sale_id))
            if current is asyncio.current_task():
                self.market_expiration_tasks.pop(int(sale_id), None)

    async def _expire_due_market_sales(self) -> None:
        for sale in await expire_market_sales():
            self.cancel_market_expiration(int(sale["sale_id"]))
            await _delete_market_message(self.bot, sale)

    @tasks.loop(seconds=30)
    async def playtime_rewards(self):
        try:
            players = _player_list(await get_players())
        except Exception:
            LOGGER.exception("RCON player lookup failed during playtime rewards")
            players = []

        presence = getattr(self.bot, "dino_online_players", {})
        combined: dict[str, dict] = {}
        for player in players:
            if isinstance(player, str):
                steam_id = player.strip()
                name = "Unknown"
            else:
                steam_id = str(
                    player.get("steamId")
                    or player.get("SteamId")
                    or player.get("steamID")
                    or player.get("steam_id")
                    or player.get("playerId")
                    or player.get("PlayerID")
                    or ""
                ).strip()
                name = str(
                    player.get("name")
                    or player.get("Name")
                    or player.get("playerName")
                    or "Unknown"
                )
            if steam_id.isdigit():
                if name == "Unknown" and isinstance(presence, dict):
                    name = str(presence.get(steam_id) or name)
                combined[steam_id] = {"steamId": steam_id, "name": name}

        # Lua presence events are the authoritative live-player source. RCON's
        # player-list endpoint can legitimately return an empty list while
        # players are online, so use it only to supplement names/identities.
        if isinstance(presence, dict):
            for steam_id, name in presence.items():
                steam_id = str(steam_id).strip()
                if steam_id.isdigit() and steam_id not in combined:
                    combined[steam_id] = {
                        "steamId": steam_id,
                        "name": str(name or "Unknown"),
                    }

        if not combined:
            broken = await break_playtime_sessions()
            if broken:
                LOGGER.info(
                    "Closed %d playtime session(s); live presence is empty",
                    broken,
                )
            return
        try:
            discord_role_ids: dict[str, set[str]] = {}
            for steam_id in combined:
                link = await resolve_linked_player(steam_id=steam_id)
                if link is None:
                    continue
                discord_id = str(link.get("discord_id") or "").strip()
                if not discord_id.isdigit():
                    continue
                roles: set[str] = set()
                for guild in self.bot.guilds:
                    member = guild.get_member(int(discord_id))
                    if member is None:
                        try:
                            member = await guild.fetch_member(int(discord_id))
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            continue
                    roles.update(str(role.id) for role in member.roles)
                    break
                discord_role_ids[discord_id] = roles
            awarded = await award_online_playtime(
                list(combined.values()),
                discord_role_ids=discord_role_ids,
            )
            if awarded:
                LOGGER.info("Awarded playtime points to %d player(s)", len(awarded))
            milestone_awards = await award_due_milestones(
                set(combined),
                source="live",
            )
            for milestone_award in milestone_awards:
                try:
                    await _post_milestone_award(self.bot, milestone_award)
                except Exception:
                    LOGGER.exception(
                        "Milestone reward message failed for SteamID %s",
                        milestone_award.get("steam_id"),
                    )
            if milestone_awards:
                LOGGER.info(
                    "Awarded %d live playtime milestone(s) to %d player(s)",
                    sum(len(item["milestones"]) for item in milestone_awards),
                    len(milestone_awards),
                )
        except Exception:
            LOGGER.exception("Playtime point award failed")

    @playtime_rewards.before_loop
    async def before_playtime_rewards(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def sale_expiration(self):
        await self._expire_due_market_sales()

    @sale_expiration.before_loop
    async def before_sale_expiration(self):
        await self.bot.wait_until_ready()

    @discord.app_commands.command(
        name="economypanel",
        description="Post the persistent player economy panel",
    )
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def economy_panel(self, interaction: discord.Interaction):
        await interaction.response.send_message("Economy panel posted.", ephemeral=True)
        await interaction.channel.send(embed=economy_panel_embed(), view=EconomyPanelView())

    @discord.app_commands.command(
        name="economyadminpanel",
        description="Post the persistent economy administrator panel",
    )
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def economy_admin_panel(self, interaction: discord.Interaction):
        await interaction.response.send_message("Economy administrator panel posted.", ephemeral=True)
        await interaction.channel.send(embed=admin_panel_embed(), view=EconomyAdminPanelView())

    @discord.app_commands.command(
        name="dinostorageadminpanel",
        description="Post the persistent Dino Storage administrator panel",
    )
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def dino_storage_admin_panel(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Dino Storage administrator panel posted.", ephemeral=True
        )
        await interaction.channel.send(
            embed=dino_storage_admin_panel_embed(),
            view=DinoStorageAdminPanelView(),
        )


async def setup(bot: commands.Bot):
    await init_economy_database()
    bot.add_view(EconomyPanelView())
    bot.add_view(EconomyAdminPanelView())
    bot.add_view(DinoStorageAdminPanelView())
    await bot.add_cog(Economy(bot))
