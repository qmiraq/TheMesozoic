import io
import json

import discord
from discord.ext import commands

from bot.services.admin_log_service import dino_action_embed, send_admin_log
from bot.services.database_service import get_player_by_discord
from bot.services.dinosaur_render_service import dinosaur_render_file
from bot.services.dino_storage_service import (
    DinoStorageError,
    delete_inventory_item,
    get_live_dinosaur_status,
    init_dino_storage_database,
    list_inventory,
    normalize_legacy_admin_grant,
    run_park_dinosaur,
    run_live_needs_probe,
    run_live_player_data_probe,
    run_live_prime_probe,
    run_ue4ss_object_dump,
    run_unpark_dinosaur,
    set_inventory_nickname,
    set_inventory_diets_percent,
)
from bot.services.economy_service import (
    EconomyError,
    list_mutation_catalog,
    shop_token_mutations_for_slot,
    set_shop_token_mutations,
)
from bot.services.activity_map_service import region_for_world_location


PANEL_TITLE = "DINO GARAGE"
SELECT_PAGE_SIZE = 25


def _snapshot(dinosaur: dict) -> dict:
    try:
        value = json.loads(dinosaur.get("serialized_player_data") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _is_prime(dinosaur: dict) -> bool:
    return _snapshot(dinosaur).get("isPrime") is True


def _is_shop_token(dinosaur: dict) -> bool:
    return str(dinosaur.get("source") or "") == "shop_token" or _snapshot(dinosaur).get("shopToken") is True


def _sex_label(dinosaur: dict) -> str:
    sex = str(dinosaur.get("sex") or "").strip().lower()
    if sex == "female":
        return "Female"
    if sex == "male":
        return "Male"
    return "Female" if _snapshot(dinosaur).get("isFemale") is True else "Male"


def _status_label(dinosaur: dict) -> str:
    if _is_shop_token(dinosaur):
        return "Shop Token"
    return "Prime Elder" if _is_prime(dinosaur) else "Frail Elder"


def _parked_region_label(dinosaur: dict) -> str:
    snapshot = _snapshot(dinosaur)
    if snapshot.get("currentLocationOnly") is True:
        return "Current location only"
    location = snapshot.get("location")
    if not isinstance(location, dict):
        return "Unknown"
    try:
        region = region_for_world_location(
            float(location.get("x")),
            float(location.get("y")),
        )
    except (TypeError, ValueError):
        return "Unknown"
    return region or "Outside named regions"


def nutrient_percentage(snapshot: dict, nutrient_name: str) -> str:
    generated = snapshot.get("generatedNutrientPercents")
    if isinstance(generated, dict):
        value = generated.get(nutrient_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{max(0.0, min(100.0, float(value))):.1f}%"

    captured = snapshot.get("nutrientPercentages")
    if isinstance(captured, dict):
        value = captured.get(nutrient_name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{max(0.0, min(100.0, float(value))):.1f}%"

    # Compatibility for old admin/shop snapshots that incorrectly stored
    # percentage choices as 0..1 raw nutrient values.
    if snapshot.get("generatedShop") is True:
        nutrients = snapshot.get("nutrients")
        field = {
            "carb": "carbValue",
            "protein": "proteinValue",
            "lipid": "lipidValue",
        }.get(nutrient_name)
        value = nutrients.get(field) if isinstance(nutrients, dict) and field else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{max(0.0, min(100.0, float(value) * 100.0)):.1f}%"

    # EVRIMA stores each main diet against the live dinosaur's MaxHunger
    # capacity. This remains species/growth aware instead of assuming 1200.
    nutrients = snapshot.get("nutrients")
    field = {
        "carb": "carbValue",
        "protein": "proteinValue",
        "lipid": "lipidValue",
    }.get(nutrient_name)
    raw_value = nutrients.get(field) if isinstance(nutrients, dict) and field else None
    capacity = snapshot.get("maxHunger")
    if (
        isinstance(raw_value, (int, float))
        and not isinstance(raw_value, bool)
        and isinstance(capacity, (int, float))
        and not isinstance(capacity, bool)
        and float(capacity) > 0
    ):
        percent = float(raw_value) / float(capacity) * 100.0
        return f"{max(0.0, min(100.0, percent)):.1f}%"

    return "Unknown"


def _species_groups(dinosaurs: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for dinosaur in dinosaurs:
        species = str(dinosaur.get("species") or "Unknown")
        groups.setdefault(species, []).append(dinosaur)
    return sorted(groups.items(), key=lambda item: item[0].casefold())


def _page_count(item_count: int) -> int:
    return max(1, (item_count + SELECT_PAGE_SIZE - 1) // SELECT_PAGE_SIZE)


def storage_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="DINO GARAGE",
        description=(
            "Store your dinos for later and manage them all through our panel! "
            "You must first link your Steam here: <#1527247945798516786>."
        ),
        color=discord.Color.dark_green(),
    )
    embed.add_field(
        name="**Features**",
        value=(
            "- **Park Dino** - Store your current dino.\n"
            "- **Unpark Dino** - Take out a stored dino to play it again.\n"
            "- **View Inventory** - View all your stored dinos.\n"
            "- **Delete Dino** - Delete any stored dinos.\n"
            "Capacity is unlimited so park as many dinos as you want!"
        ),
        inline=False,
    )
    embed.add_field(
        name="**Parking requirements**",
        value=(
            "- Your dino must be at least **75% growth.**\n"
            "- Your dino must have **100% health, stamina and blood.**\n"
            "- Your dino must have atleast **25% food and water.**"
        ),
        inline=False,
    )
    embed.add_field(
        name="**Unparking rules**",
        value=(
            "- Must be online and play the same species as the one being unparked.\n"
            "- Growth must be below 50%.\n"
            "- Keep in mind that your current dinosaur will be deleted upon "
            "unparking.\n"
            "- Choose whether to unpark at its saved location or your current location.\n\n"
            "**Happy playing Islander!**"
        ),
        inline=False,
    )
    return embed


def inventory_embed(player_name: str, dinosaurs: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title=f"📦 {player_name}'s Dino Inventory",
        color=discord.Color.blurple(),
    )

    frail_counts: dict[str, int] = {}
    prime_counts: dict[str, int] = {}
    shop_counts: dict[str, int] = {}
    for dinosaur in dinosaurs:
        species = str(dinosaur.get("species") or "Unknown")
        snapshot = _snapshot(dinosaur)
        counts = shop_counts if _is_shop_token(dinosaur) else (prime_counts if snapshot.get("isPrime") is True else frail_counts)
        counts[species] = counts.get(species, 0) + 1

    def section_text(counts: dict[str, int]) -> str:
        if not counts:
            return "No parked dinos."
        return "\n".join(
            f"{species} - {count}x"
            for species, count in sorted(counts.items(), key=lambda item: item[0].casefold())
        )

    embed.add_field(
        name=f"🐢 Frail Elders - {sum(frail_counts.values())} total",
        value=section_text(frail_counts),
        inline=False,
    )
    embed.add_field(
        name=f"👑 Prime Elders - {sum(prime_counts.values())} total",
        value=section_text(prime_counts),
        inline=False,
    )
    embed.add_field(
        name=f"🎟️ Shop Tokens - {sum(shop_counts.values())} total",
        value=section_text(shop_counts),
        inline=False,
    )
    return embed


def species_inventory_embed(
    player_name: str,
    species: str,
    dinosaurs: list[dict],
    page: int,
) -> discord.Embed:
    ordered = sorted(
        dinosaurs,
        key=lambda item: (-float(item.get("growth") or 0.0), int(item.get("id") or 0)),
    )
    pages = _page_count(len(ordered))
    page = max(0, min(page, pages - 1))
    start = page * SELECT_PAGE_SIZE
    visible = ordered[start : start + SELECT_PAGE_SIZE]
    lines = []
    for dinosaur in visible:
        growth = max(0.0, float(dinosaur.get("growth") or 0.0)) * 100
        icon = "👑" if _is_prime(dinosaur) else "🐢"
        lines.append(f"{icon} {growth:.1f}% — {_sex_label(dinosaur)}")

    embed = discord.Embed(
        title=f"📦 {player_name}'s {species} Inventory",
        description=(
            f"You have **{len(ordered)}** parked **{species}**.\n"
            "Select one below to view its captured details.\n\n"
            + ("\n".join(lines) if lines else "No parked dinos.")
        ),
        color=discord.Color.blurple(),
    )
    if pages > 1:
        embed.set_footer(text=f"Page {page + 1} of {pages}")
    return embed


def species_status_embed(player_name: str, species: str, dinosaurs: list[dict]) -> discord.Embed:
    shop_count = sum(1 for dinosaur in dinosaurs if _is_shop_token(dinosaur))
    frail_count = sum(1 for dinosaur in dinosaurs if not _is_shop_token(dinosaur) and not _is_prime(dinosaur))
    prime_count = len(dinosaurs) - frail_count - shop_count
    return discord.Embed(
        title=f"📦 {player_name}'s {species} Inventory",
        description=(
            f"Choose which type of **{species}** you want to view.\n\n"
            f"🐢 **Frail:** {frail_count}\n"
            f"👑 **Prime:** {prime_count}\n"
            f"🎟️ **Shop Tokens:** {shop_count}"
        ),
        color=discord.Color.blurple(),
    )


def dinosaur_detail_embed(dinosaur: dict) -> discord.Embed:
    snapshot = _snapshot(dinosaur)
    species = str(dinosaur.get("species") or snapshot.get("species") or "Unknown")
    icon = "🎟️" if _is_shop_token(dinosaur) else ("👑" if snapshot.get("isPrime") is True else "🐢")
    elder_stacks = int(snapshot.get("elderStacks") or 0)
    growth = max(0.0, float(snapshot.get("growth", dinosaur.get("growth") or 0.0))) * 100

    embed = discord.Embed(
        title=f"{icon} {species}",
        color=discord.Color.gold() if snapshot.get("isPrime") is True else discord.Color.green(),
    )
    embed.add_field(
        name="🦕 Parked Dino Info",
        value=(
            f"**Name:** {str(dinosaur.get('nickname') or '').strip() or 'Unnamed'}\n"
            f"**Class:** {species}\n"
            f"**Parked at:** {_parked_region_label(dinosaur)}\n"
            f"**Gender:** {_sex_label(dinosaur)}\n"
            f"**Status:** {_status_label(dinosaur)} {icon}\n"
            f"**Entombments:** {max(0, min(3, elder_stacks))}/3"
        ),
        inline=False,
    )

    def percentage(name: str, maximum_name: str, legacy_maximum: float | None = None) -> str:
        value = snapshot.get(name)
        maximum = snapshot.get(maximum_name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "Unknown"
        if isinstance(maximum, bool) or not isinstance(maximum, (int, float)):
            maximum = legacy_maximum
        if maximum is None or maximum <= 0:
            return "Unknown"
        percent = max(0.0, min(100.0, float(value) / float(maximum) * 100.0))
        return f"{percent:.1f}%"

    health = snapshot.get("health")
    blood = snapshot.get("blood")
    legacy_health_blood_max = max(
        (
            float(value)
            for value in (health, blood)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ),
        default=0.0,
    )

    embed.add_field(
        name="📈 Core Stats",
        value=(
            f"**Growth:** {growth:.1f}%\n"
            f"**Health:** {percentage('health', 'maxHealth', legacy_health_blood_max)}\n"
            f"**Blood:** {percentage('blood', 'maxBlood', legacy_health_blood_max)}\n"
            f"**Stamina:** {percentage('stamina', 'maxStamina')}"
        ),
        inline=True,
    )
    embed.add_field(
        name="🍖 Needs",
        value=(
            f"**Hunger:** {percentage('hunger', 'maxHunger')}\n"
            f"**Thirst:** {percentage('thirst', 'maxThirst')}"
        ),
        inline=True,
    )
    embed.add_field(
        name="🍽️ Nutrients",
        value=(
            f"**Carb:** {nutrient_percentage(snapshot, 'carb')}\n"
            f"**Protein:** {nutrient_percentage(snapshot, 'protein')}\n"
            f"**Lipid:** {nutrient_percentage(snapshot, 'lipid')}"
        ),
        inline=True,
    )

    prime = snapshot.get("primeData") if isinstance(snapshot.get("primeData"), dict) else {}
    tasks = prime.get("tasks") if isinstance(prime.get("tasks"), list) else []
    task_lines = []
    for index, task in enumerate(tasks[:10], start=1):
        if not isinstance(task, dict):
            continue
        number_value = int(task.get("number") or index)
        complete = task.get("complete") is True
        name = str(task.get("name") or f"Prime condition {number_value}")
        task_lines.append(f"{'✅' if complete else '❌'} **{number_value}.** {name}")
    completed_count = sum(
        1
        for index, task in enumerate(tasks[:10], start=1)
        if isinstance(task, dict)
        and task.get("complete") is True
    )
    embed.add_field(
        name=f"📑 Prime Tasks ({completed_count}/10)",
        value="\n".join(task_lines) if task_lines else "No Prime-task data captured.",
        inline=False,
    )

    mutations = snapshot.get("mutations") if isinstance(snapshot.get("mutations"), dict) else {}
    mutation_groups = [
        [
            (f"Slot{index}", f"MutationSlot{index}")
            for index in range(1, 5)
        ],
        [
            (f"Baby{index}", f"ParentMutationSlot{index}")
            for index in range(1, 5)
        ],
        [
            ("Entomb1", "ElderMutationSlot1A"),
            ("Entomb2", "ElderMutationSlot2A"),
            ("Entomb3", "ElderMutationSlot3A"),
            ("Entomb4", "ElderMutationSlot4A"),
        ],
        [
            ("Entomb5", "ElderMutationSlot1B"),
            ("Entomb6", "ElderMutationSlot2B"),
            ("Entomb7", "ElderMutationSlot3B"),
            ("Entomb8", "ElderMutationSlot4B"),
        ],
    ]
    populated_groups = []
    for group in mutation_groups:
        group_lines = [
            f"• **{label}:** {mutations[field]}"
            for label, field in group
            if isinstance(mutations.get(field), str) and mutations[field]
        ]
        if group_lines:
            populated_groups.append("\n".join(group_lines))
    embed.add_field(
        name="🧬 Mutations",
        value="\n\n".join(populated_groups) if populated_groups else "No populated mutations.",
        inline=False,
    )
    return embed


async def rendered_dinosaur_detail(
    bot: commands.Bot,
    dinosaur: dict,
) -> tuple[discord.Embed, discord.File | None]:
    embed = dinosaur_detail_embed(dinosaur)
    species = str(dinosaur.get("species") or _snapshot(dinosaur).get("species") or "")
    render_file = await dinosaur_render_file(bot, species)
    if render_file is not None:
        embed.set_image(url=f"attachment://{render_file.filename}")
    return embed, render_file


class OwnedInventoryView(discord.ui.View):
    def __init__(self, discord_id: str, admin_mode: bool = False):
        super().__init__(timeout=300)
        self.discord_id = str(discord_id)
        self.admin_mode = bool(admin_mode)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.admin_mode and interaction.user.guild_permissions.administrator:
            return True
        if str(interaction.user.id) == self.discord_id:
            return True
        await interaction.response.send_message(
            "You cannot manage this dinosaur inventory.",
            ephemeral=True,
        )
        return False


class InventoryStatusSelect(discord.ui.Select):
    def __init__(self, parent_view: "InventorySpeciesStatusView"):
        self.parent_view = parent_view
        shop_count = sum(1 for dinosaur in parent_view.species_dinosaurs if _is_shop_token(dinosaur))
        frail_count = sum(1 for dinosaur in parent_view.species_dinosaurs if not _is_shop_token(dinosaur) and not _is_prime(dinosaur))
        prime_count = len(parent_view.species_dinosaurs) - frail_count - shop_count
        options = []
        if frail_count:
            options.append(discord.SelectOption(
                label="Frail",
                value="frail",
                description=f"{frail_count} parked {parent_view.species}",
                emoji="🐢",
            ))
        if prime_count:
            options.append(discord.SelectOption(
                label="Prime",
                value="prime",
                description=f"{prime_count} parked {parent_view.species}",
                emoji="👑",
            ))
        if shop_count:
            options.append(discord.SelectOption(
                label="Shop Tokens",
                value="shop",
                description=f"{shop_count} {parent_view.species} Shop Tokens",
                emoji="🎟️",
            ))
        super().__init__(
            placeholder=f"Choose Prime or Frail {parent_view.species}..."[:150],
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        status_filter = self.values[0]
        filtered = [dinosaur for dinosaur in self.parent_view.species_dinosaurs if (
            _is_shop_token(dinosaur) if status_filter == "shop" else
            (not _is_shop_token(dinosaur) and _is_prime(dinosaur) == (status_filter == "prime"))
        )]
        await interaction.response.edit_message(
            content=None,
            embed=species_inventory_embed(
                self.parent_view.player_name,
                self.parent_view.species,
                filtered,
                0,
            ),
            view=InventorySpeciesView(
                self.parent_view.discord_id,
                self.parent_view.player_name,
                self.parent_view.dinosaurs,
                self.parent_view.species,
                0,
                status_filter=status_filter,
                admin_mode=self.parent_view.admin_mode,
            ),
        )


class InventorySpeciesSelect(discord.ui.Select):
    def __init__(self, parent_view: "InventoryOverviewView", groups: list[tuple[str, list[dict]]]):
        self.parent_view = parent_view
        self.groups_by_value: dict[str, tuple[str, list[dict]]] = {}
        options = []
        for index, (species, dinosaurs) in enumerate(groups):
            value = str(index)
            self.groups_by_value[value] = (species, dinosaurs)
            shop_count = sum(1 for dinosaur in dinosaurs if _is_shop_token(dinosaur))
            prime_count = sum(1 for dinosaur in dinosaurs if not _is_shop_token(dinosaur) and _is_prime(dinosaur))
            frail_count = len(dinosaurs) - prime_count - shop_count
            options.append(
                discord.SelectOption(
                    label=f"{species} ({len(dinosaurs)}x)"[:100],
                    value=value,
                    description=f"{frail_count} Frail • {prime_count} Prime • {shop_count} Shop"[:100],
                )
            )
        super().__init__(
            placeholder="📦 Choose a class...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        species, dinosaurs = self.groups_by_value[self.values[0]]
        await interaction.response.edit_message(
            content=None,
            embed=species_status_embed(
                self.parent_view.player_name,
                species,
                dinosaurs,
            ),
            view=InventorySpeciesStatusView(
                self.parent_view.discord_id,
                self.parent_view.player_name,
                self.parent_view.dinosaurs,
                species,
                admin_mode=self.parent_view.admin_mode,
            ),
        )


class InventoryDinosaurSelect(discord.ui.Select):
    def __init__(self, parent_view: "InventorySpeciesView", dinosaurs: list[dict]):
        self.parent_view = parent_view
        self.dinosaurs_by_id = {str(dinosaur["id"]): dinosaur for dinosaur in dinosaurs}
        options = []
        for dinosaur in dinosaurs:
            growth = max(0.0, float(dinosaur.get("growth") or 0.0)) * 100
            status = "Shop Token" if _is_shop_token(dinosaur) else ("Prime" if _is_prime(dinosaur) else "Frail")
            options.append(
                discord.SelectOption(
                    label=(
                        f"{self.parent_view.species} • {growth:.1f}% • "
                        f"{_sex_label(dinosaur)}"
                    )[:100],
                    value=str(dinosaur["id"]),
                    description=f"{status} Elder"[:100],
                )
            )
        super().__init__(
            placeholder="🎟️ Choose a token...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        dinosaur = self.dinosaurs_by_id[self.values[0]]
        embed, render_file = await rendered_dinosaur_detail(
            interaction.client,
            dinosaur,
        )
        await interaction.response.edit_message(
            content=None,
            embed=embed,
            attachments=[render_file] if render_file is not None else [],
            view=InventoryDetailView(
                self.parent_view.discord_id,
                self.parent_view.player_name,
                self.parent_view.dinosaurs,
                self.parent_view.species,
                self.parent_view.page,
                dinosaur,
                status_filter=self.parent_view.status_filter,
                admin_mode=self.parent_view.admin_mode,
            ),
        )


class InventoryOverviewView(OwnedInventoryView):
    def __init__(
        self,
        discord_id: str,
        player_name: str,
        dinosaurs: list[dict],
        page: int = 0,
        admin_mode: bool = False,
    ):
        super().__init__(discord_id, admin_mode)
        self.player_name = player_name
        self.dinosaurs = dinosaurs
        self.filtered_dinosaurs = dinosaurs
        self.groups = _species_groups(dinosaurs)
        self.pages = _page_count(len(self.groups))
        self.page = max(0, min(page, self.pages - 1))
        start = self.page * SELECT_PAGE_SIZE
        visible_groups = self.groups[start : start + SELECT_PAGE_SIZE]
        if visible_groups:
            self.add_item(InventorySpeciesSelect(self, visible_groups))
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.pages - 1
        if self.pages == 1:
            self.remove_item(self.previous_page)
            self.remove_item(self.next_page)

    async def _show_page(self, interaction: discord.Interaction, page: int) -> None:
        view = InventoryOverviewView(
            self.discord_id,
            self.player_name,
            self.dinosaurs,
            page,
            admin_mode=self.admin_mode,
        )
        embed = inventory_embed(self.player_name, view.filtered_dinosaurs)
        if view.pages > 1:
            embed.set_footer(text=f"Class selector page {view.page + 1} of {view.pages}")
        await interaction.response.edit_message(content=None, embed=embed, view=view)

    @discord.ui.button(label="← Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._show_page(interaction, self.page - 1)

    @discord.ui.button(label="Next →", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._show_page(interaction, self.page + 1)


class InventorySpeciesStatusView(OwnedInventoryView):
    def __init__(
        self,
        discord_id: str,
        player_name: str,
        dinosaurs: list[dict],
        species: str,
        admin_mode: bool = False,
    ):
        super().__init__(discord_id, admin_mode)
        self.player_name = player_name
        self.dinosaurs = dinosaurs
        self.species = species
        self.species_dinosaurs = [
            dinosaur for dinosaur in dinosaurs
            if str(dinosaur.get("species") or "Unknown") == species
        ]
        self.add_item(InventoryStatusSelect(self))

    @discord.ui.button(label="← Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None,
            embed=inventory_embed(self.player_name, self.dinosaurs),
            view=InventoryOverviewView(
                self.discord_id,
                self.player_name,
                self.dinosaurs,
                admin_mode=self.admin_mode,
            ),
        )


class InventorySpeciesView(OwnedInventoryView):
    def __init__(
        self,
        discord_id: str,
        player_name: str,
        dinosaurs: list[dict],
        species: str,
        page: int = 0,
        status_filter: str | None = None,
        admin_mode: bool = False,
    ):
        super().__init__(discord_id, admin_mode)
        self.player_name = player_name
        self.dinosaurs = dinosaurs
        self.species = species
        self.status_filter = status_filter
        self.species_dinosaurs = sorted(
            [
                dinosaur
                for dinosaur in dinosaurs
                if str(dinosaur.get("species") or "Unknown") == species
                and (
                    status_filter not in {"frail", "prime", "shop"}
                    or (_is_shop_token(dinosaur) if status_filter == "shop" else
                        (not _is_shop_token(dinosaur) and _is_prime(dinosaur) == (status_filter == "prime")))
                )
            ],
            key=lambda item: (-float(item.get("growth") or 0.0), int(item.get("id") or 0)),
        )
        self.pages = _page_count(len(self.species_dinosaurs))
        self.page = max(0, min(page, self.pages - 1))
        start = self.page * SELECT_PAGE_SIZE
        visible = self.species_dinosaurs[start : start + SELECT_PAGE_SIZE]
        if visible:
            self.add_item(InventoryDinosaurSelect(self, visible))
        self.previous_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.pages - 1
        if self.pages == 1:
            self.remove_item(self.previous_page)
            self.remove_item(self.next_page)

    async def _show_page(self, interaction: discord.Interaction, page: int) -> None:
        view = InventorySpeciesView(
            self.discord_id,
            self.player_name,
            self.dinosaurs,
            self.species,
            page,
            status_filter=self.status_filter,
            admin_mode=self.admin_mode,
        )
        await interaction.response.edit_message(
            content=None,
            embed=species_inventory_embed(
                self.player_name,
                self.species,
                view.species_dinosaurs,
                view.page,
            ),
            view=view,
        )

    @discord.ui.button(label="← Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._show_page(interaction, self.page - 1)

    @discord.ui.button(label="Next →", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._show_page(interaction, self.page + 1)

    @discord.ui.button(label="← Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None,
            embed=species_status_embed(
                self.player_name,
                self.species,
                [
                    dinosaur for dinosaur in self.dinosaurs
                    if str(dinosaur.get("species") or "Unknown") == self.species
                ],
            ),
            view=InventorySpeciesStatusView(
                self.discord_id,
                self.player_name,
                self.dinosaurs,
                self.species,
                admin_mode=self.admin_mode,
            ),
        )


class InventoryDetailView(OwnedInventoryView):
    def __init__(
        self,
        discord_id: str,
        player_name: str,
        dinosaurs: list[dict],
        species: str,
        species_page: int,
        dinosaur: dict,
        status_filter: str | None = None,
        admin_mode: bool = False,
    ):
        super().__init__(discord_id, admin_mode)
        self.player_name = player_name
        self.dinosaurs = dinosaurs
        self.species = species
        self.species_page = species_page
        self.dinosaur = dinosaur
        self.status_filter = status_filter
        if not (
            self.admin_mode
            and _snapshot(dinosaur).get("generatedShop") is True
        ):
            self.remove_item(self.repair_diets)

    @discord.ui.button(
        label="Name Token",
        emoji="✏️",
        style=discord.ButtonStyle.primary,
    )
    async def name_token(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        await interaction.response.send_modal(TokenNameModal(self))

    @discord.ui.button(
        label="Set Diets to 100%",
        emoji="🍽️",
        style=discord.ButtonStyle.success,
    )
    async def repair_diets(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        await interaction.response.defer()
        try:
            updated = await set_inventory_diets_percent(
                self.discord_id,
                int(self.dinosaur["id"]),
            )
        except DinoStorageError as error:
            await interaction.edit_original_response(content=f"❌ {error}")
            return
        self.dinosaur.update(updated)
        for item in self.dinosaurs:
            if int(item.get("id") or 0) == int(self.dinosaur["id"]):
                item.update(updated)
                break
        embed, render_file = await rendered_dinosaur_detail(
            interaction.client,
            self.dinosaur,
        )
        await interaction.edit_original_response(
            content=(
                f"✅ **{self.species}** diets are now set to **100%**. "
                "Its live species capacity will be resolved when it is unparked."
            ),
            embed=embed,
            attachments=[render_file] if render_file is not None else [],
            view=InventoryDetailView(
                self.discord_id,
                self.player_name,
                self.dinosaurs,
                self.species,
                self.species_page,
                self.dinosaur,
                status_filter=self.status_filter,
                admin_mode=True,
            ),
        )

    @discord.ui.button(label="← Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _button: discord.ui.Button):
        species_dinosaurs = [
            dinosaur
            for dinosaur in self.dinosaurs
            if str(dinosaur.get("species") or "Unknown") == self.species
            and (
                self.status_filter not in {"frail", "prime"}
                or _is_prime(dinosaur) == (self.status_filter == "prime")
            )
        ]
        await interaction.response.edit_message(
            content=None,
            embed=species_inventory_embed(
                self.player_name,
                self.species,
                species_dinosaurs,
                self.species_page,
            ),
            attachments=[],
            view=InventorySpeciesView(
                self.discord_id,
                self.player_name,
                self.dinosaurs,
                self.species,
                self.species_page,
                status_filter=self.status_filter,
                admin_mode=self.admin_mode,
            ),
        )

class TokenNameModal(discord.ui.Modal, title="Name Token"):
    token_name = discord.ui.TextInput(
        label="Token name",
        placeholder="Leave blank to return it to Unnamed",
        required=False,
        max_length=40,
    )

    def __init__(self, owner: InventoryDetailView):
        super().__init__()
        self.owner = owner
        self.token_name.default = str(owner.dinosaur.get("nickname") or "")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            updated = await set_inventory_nickname(
                self.owner.discord_id,
                int(self.owner.dinosaur["id"]),
                str(self.token_name.value or ""),
            )
        except DinoStorageError as error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return
        self.owner.dinosaur.update(updated)
        for item in self.owner.dinosaurs:
            if int(item.get("id") or 0) == int(self.owner.dinosaur["id"]):
                item.update(updated)
                break
        embed, render_file = await rendered_dinosaur_detail(
            interaction.client,
            self.owner.dinosaur,
        )
        await interaction.response.edit_message(
            content=None,
            embed=embed,
            attachments=[render_file] if render_file is not None else [],
            view=InventoryDetailView(
                self.owner.discord_id,
                self.owner.player_name,
                self.owner.dinosaurs,
                self.owner.species,
                self.owner.species_page,
                self.owner.dinosaur,
                status_filter=self.owner.status_filter,
                admin_mode=self.owner.admin_mode,
            ),
        )


class DeleteConfirmationView(discord.ui.View):
    def __init__(self, discord_id: str, dinosaur: dict):
        super().__init__(timeout=90)
        self.discord_id = discord_id
        self.dinosaur = dinosaur

    @discord.ui.button(label="Permanently Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.discord_id:
            await interaction.response.send_message(
                "This confirmation is not yours.", ephemeral=True
            )
            return
        deleted = await delete_inventory_item(self.discord_id, int(self.dinosaur["id"]))
        player = await get_player_by_discord(self.discord_id)
        await send_admin_log(
            interaction.client,
            "delete",
            embed=dino_action_embed(
                "delete",
                interaction.user,
                str(player.steam_id) if player is not None else "Unknown",
                dinosaur=self.dinosaur,
                success=deleted,
                reason="" if deleted else "Dino was already removed or no longer parked.",
            ),
        )
        for child in self.children:
            child.disabled = True
        message = (
            f"🗑️ Deleted **{self.dinosaur['species']} #{self.dinosaur['id']}**."
            if deleted
            else "That dinosaur was already removed or is no longer parked."
        )
        await interaction.response.edit_message(content=message, view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.discord_id:
            await interaction.response.send_message(
                "This confirmation is not yours.", ephemeral=True
            )
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Deletion cancelled.", view=self)


class ShopTokenMutationSelect(discord.ui.Select):
    def __init__(self, owner: "ShopTokenMutationView", label: str, choices: list[str], row: int):
        self.owner = owner
        super().__init__(
            placeholder=f"Choose Slot{owner.slot} mutation {label}",
            options=[discord.SelectOption(label=value[:100], value=value) for value in choices[:25]],
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value in self.owner.selected:
            await interaction.response.send_message("Each mutation must be unique.", ephemeral=True)
            return
        selected = self.owner.selected + [value]
        if self.owner.slot < 4:
            await interaction.response.edit_message(
                content=f"Choose four mutations for your **{self.owner.dinosaur['species']} Shop Token**.\n\n" + "\n".join(f"**Slot{i}:** {v}" for i, v in enumerate(selected, 1)),
                view=ShopTokenMutationView(self.owner.discord_id, self.owner.steam_id, self.owner.dinosaur, self.owner.base_catalog, selected),
            )
            return
        try:
            dinosaur = await set_shop_token_mutations(self.owner.discord_id, int(self.owner.dinosaur["id"]), selected)
        except EconomyError as error:
            await interaction.response.edit_message(content=f"❌ {error}", view=None)
            return
        embed, render_file = await rendered_dinosaur_detail(
            interaction.client,
            dinosaur,
        )
        await interaction.response.edit_message(
            content=(f"Your **{dinosaur['species']} Shop Token** now has four selected mutations. "
                     "Choose where to unpark it. Shop Tokens can only use your current location."),
            embed=embed,
            attachments=[render_file] if render_file is not None else [],
            view=UnparkConfirmationView(self.owner.discord_id, self.owner.steam_id, dinosaur),
        )


class ShopTokenMutationView(discord.ui.View):
    def __init__(self, discord_id: str, steam_id: str, dinosaur: dict, catalog: list[str], selected: list[str] | None = None):
        super().__init__(timeout=300)
        self.discord_id, self.steam_id, self.dinosaur = str(discord_id), str(steam_id), dinosaur
        self.selected = list(selected or [])
        self.base_catalog = sorted(set(catalog), key=str.casefold)
        self.slot = len(self.selected) + 1
        species = str(dinosaur["species"])
        allowed_for_slot = set(shop_token_mutations_for_slot(species, self.slot))
        self.catalog = sorted(
            (
                value for value in self.base_catalog
                if value in allowed_for_slot
                and value.casefold() not in {chosen.casefold() for chosen in self.selected}
            ),
            key=str.casefold,
        )
        a_m = [v for v in self.catalog if v and "A" <= v[0].upper() <= "M"]
        n_z = [v for v in self.catalog if v not in a_m]
        self.add_item(ShopTokenMutationSelect(self, "A-M", a_m, 0))
        self.add_item(ShopTokenMutationSelect(self, "N-Z", n_z, 1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) == self.discord_id:
            return True
        await interaction.response.send_message("This Shop Token is not yours.", ephemeral=True)
        return False


class UnparkConfirmationView(discord.ui.View):
    def __init__(self, discord_id: str, steam_id: str, dinosaur: dict):
        super().__init__(timeout=90)
        self.discord_id = str(discord_id)
        self.steam_id = str(steam_id)
        self.dinosaur = dinosaur
        if _snapshot(dinosaur).get("currentLocationOnly") is True:
            for child in list(self.children):
                if getattr(child, "label", None) == "Unpark at saved location":
                    self.remove_item(child)

    async def _unpark(self, interaction: discord.Interaction, location_mode: str) -> None:
        if str(interaction.user.id) != self.discord_id:
            await interaction.response.send_message(
                "This confirmation is not yours.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await run_unpark_dinosaur(
                self.discord_id,
                self.steam_id,
                int(self.dinosaur["id"]),
                location_mode,
            )
        except DinoStorageError as error:
            await send_admin_log(
                interaction.client,
                "unpark",
                embed=dino_action_embed(
                    "unpark",
                    interaction.user,
                    self.steam_id,
                    dinosaur=self.dinosaur,
                    success=False,
                    reason=str(error),
                    location_mode=location_mode,
                ),
            )
            await interaction.edit_original_response(content=f"❌ {error}", view=None)
            return
        except Exception:
            await send_admin_log(
                interaction.client,
                "unpark",
                embed=dino_action_embed(
                    "unpark",
                    interaction.user,
                    self.steam_id,
                    dinosaur=self.dinosaur,
                    success=False,
                    reason="Unexpected bot error.",
                    location_mode=location_mode,
                ),
            )
            await interaction.edit_original_response(
                content=(
                    "❌ Unparking failed unexpectedly. Your inventory entry remains "
                    "available; wait for the attempt to stop, then try again."
                ),
                view=None,
            )
            return

        if result.get("ok") is not True or result.get("restored") is not True:
            reason = result.get("reason") or "The game rejected the restore."
            await send_admin_log(
                interaction.client,
                "unpark",
                embed=dino_action_embed(
                    "unpark",
                    interaction.user,
                    self.steam_id,
                    dinosaur=self.dinosaur,
                    result=result,
                    success=False,
                    reason=str(reason),
                    location_mode=location_mode,
                ),
            )
            await interaction.edit_original_response(
                content=f"❌ Unparking failed: `{reason}`\nYour stored dinosaur was preserved.",
                view=None,
            )
            return
        await send_admin_log(
            interaction.client,
            "unpark",
            embed=dino_action_embed(
                "unpark",
                interaction.user,
                self.steam_id,
                dinosaur=self.dinosaur,
                result=result,
                success=True,
                location_mode=location_mode,
            ),
        )
        await interaction.edit_original_response(
            content=f"✅ **{self.dinosaur['species']} unparked successfully.**",
            view=None,
        )

    @discord.ui.button(
        label="Unpark at saved location",
        style=discord.ButtonStyle.success,
    )
    async def saved_location(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        await self._unpark(interaction, "saved")

    @discord.ui.button(
        label="Unpark at current location",
        style=discord.ButtonStyle.primary,
    )
    async def current_location(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ):
        await self._unpark(interaction, "current")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if str(interaction.user.id) != self.discord_id:
            await interaction.response.send_message(
                "This confirmation is not yours.", ephemeral=True
            )
            return
        await interaction.response.edit_message(content="Unparking cancelled.", view=None)


class DinoSelection(discord.ui.Select):
    def __init__(
        self,
        discord_id: str,
        dinosaurs: list[dict],
        action: str,
        steam_id: str | None = None,
    ):
        self.discord_id = discord_id
        self.dinosaurs = {str(item["id"]): item for item in dinosaurs[:25]}
        self.action = action
        self.steam_id = steam_id
        options = []
        for item in dinosaurs[:25]:
            growth = max(0.0, float(item.get("growth") or 0.0)) * 100
            snapshot = _snapshot(item)
            entombments = max(0, min(3, int(snapshot.get("elderStacks") or 0)))
            options.append(
                discord.SelectOption(
                    label=f"{item['species']} • {growth:.1f}%",
                    value=str(item["id"]),
                    description=f"{_sex_label(item)} • Entombed {entombments}/3",
                )
            )
        species = str(dinosaurs[0].get("species") or "dino") if dinosaurs else "dino"
        super().__init__(
            placeholder=(
                f"Choose a stored {species}" if action == "unpark"
                else "Choose a stored dinosaur"
            )[:150],
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.discord_id:
            await interaction.response.send_message(
                "This inventory menu is not yours.", ephemeral=True
            )
            return

        dinosaur = self.dinosaurs[self.values[0]]
        if self.action == "delete":
            await interaction.response.send_message(
                f"Delete **{dinosaur['species']} #{dinosaur['id']}** permanently?",
                view=DeleteConfirmationView(self.discord_id, dinosaur),
                ephemeral=True,
            )
            return

        legacy_admin_grant = (
            str(dinosaur.get("source") or "") == "admin_grant"
            or _snapshot(dinosaur).get("generatedAdmin") is True
        )
        if legacy_admin_grant:
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                dinosaur = await normalize_legacy_admin_grant(dinosaur)
            except DinoStorageError as error:
                await interaction.edit_original_response(
                    content=f"❌ {error}",
                    view=None,
                )
                return
            self.dinosaurs[str(dinosaur["id"])] = dinosaur

        if _is_shop_token(dinosaur):
            snapshot = _snapshot(dinosaur)
            if snapshot.get("shopTokenMutationsSelected") is not True:
                catalog = await list_mutation_catalog(species=str(dinosaur["species"]))
                await interaction.response.send_message(
                    f"Choose four mutations for your **{dinosaur['species']} Shop Token**.\n\nChoose a Slot1 mutation:",
                    view=ShopTokenMutationView(self.discord_id, str(self.steam_id or ""), dinosaur, catalog),
                    ephemeral=True,
                )
                return

        current_only = _snapshot(dinosaur).get("currentLocationOnly") is True
        prompt = (
            f"If you're sure you want to unpark your {dinosaur['species']}, "
            "it will be restored at your current location."
            if current_only
            else (
                f"If you're sure you want to unpark your {dinosaur['species']}, "
                "please choose if you'd like to either spawn where your "
                f"{dinosaur['species']} was parked or stay at your current location."
            )
        )
        view = UnparkConfirmationView(
            self.discord_id,
            str(self.steam_id or ""),
            dinosaur,
        )
        embed, render_file = await rendered_dinosaur_detail(
            interaction.client,
            dinosaur,
        )
        if interaction.response.is_done():
            await interaction.edit_original_response(
                content=prompt,
                embed=embed,
                attachments=[render_file] if render_file is not None else [],
                view=view,
            )
        else:
            send_options = {
                "embed": embed,
                "view": view,
                "ephemeral": True,
            }
            if render_file is not None:
                send_options["file"] = render_file
            await interaction.response.send_message(prompt, **send_options)


class DinoSelectionView(discord.ui.View):
    def __init__(
        self,
        discord_id: str,
        dinosaurs: list[dict],
        action: str,
        steam_id: str | None = None,
    ):
        super().__init__(timeout=120)
        self.add_item(DinoSelection(discord_id, dinosaurs, action, steam_id))


class ActionStatusSelect(discord.ui.Select):
    def __init__(self, discord_id: str, dinosaurs: list[dict], action: str, steam_id: str | None):
        self.discord_id, self.dinosaurs, self.action, self.steam_id = discord_id, dinosaurs, action, steam_id
        groups = {
            "frail": [d for d in dinosaurs if not _is_shop_token(d) and not _is_prime(d)],
            "prime": [d for d in dinosaurs if not _is_shop_token(d) and _is_prime(d)],
            "shop": [d for d in dinosaurs if _is_shop_token(d)],
        }
        self.groups = {key: value for key, value in groups.items() if value}
        labels = {"frail": ("Frail", "🐢"), "prime": ("Prime", "👑"), "shop": ("Shop Tokens", "🎟️")}
        species = str(dinosaurs[0].get("species") or "dino")
        options = [discord.SelectOption(label=labels[key][0], value=key, emoji=labels[key][1], description=f"{len(value)} {species}") for key, value in self.groups.items()]
        super().__init__(placeholder=f"Choose Frail, Prime or Shop Tokens...", options=options)

    async def callback(self, interaction: discord.Interaction):
        selected = self.groups[self.values[0]]
        await interaction.response.edit_message(
            content=f"Choose a stored **{selected[0]['species']}**:",
            view=DinoSelectionView(self.discord_id, selected, self.action, self.steam_id),
        )


class ActionStatusView(discord.ui.View):
    def __init__(self, discord_id: str, dinosaurs: list[dict], action: str, steam_id: str | None = None):
        super().__init__(timeout=120)
        self.add_item(ActionStatusSelect(discord_id, dinosaurs, action, steam_id))


class DinoStoragePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Park Dino",
        emoji="📥",
        style=discord.ButtonStyle.success,
        custom_id="dino_storage:park",
    )
    async def park(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        discord_id = str(interaction.user.id)
        player = await get_player_by_discord(discord_id)
        if player is None:
            await interaction.followup.send(
                "Link your SteamID before using dinosaur storage.", ephemeral=True
            )
            return

        try:
            result = await run_park_dinosaur(discord_id, player.steam_id)
        except DinoStorageError as error:
            await send_admin_log(
                interaction.client,
                "park",
                embed=dino_action_embed(
                    "park",
                    interaction.user,
                    player.steam_id,
                    success=False,
                    reason=str(error),
                ),
            )
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
            return
        except Exception:
            await send_admin_log(
                interaction.client,
                "park",
                embed=dino_action_embed(
                    "park",
                    interaction.user,
                    player.steam_id,
                    success=False,
                    reason="Unexpected bot error.",
                ),
            )
            await interaction.followup.send(
                "❌ Parking failed unexpectedly. Contact an administrator before "
                "spawning another dinosaur.",
                ephemeral=True,
            )
            return

        if result.get("ok") is not True:
            reason = result.get("reason") or "The game rejected parking."
            message = str(result.get("message") or "").strip()
            await send_admin_log(
                interaction.client,
                "park",
                embed=dino_action_embed(
                    "park",
                    interaction.user,
                    player.steam_id,
                    result=result,
                    success=False,
                    reason=message or str(reason),
                ),
            )
            if message:
                changed_note = (
                    "\nYour dinosaur may have been modified; contact an administrator."
                    if result.get("dinosaurModified") is True
                    else "\nYour dinosaur was not changed."
                )
                await interaction.followup.send(
                    f"❌ {message}{changed_note}",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"❌ Parking failed: `{reason}`\nYour dinosaur was not changed.",
                ephemeral=True,
            )
            return

        await send_admin_log(
            interaction.client,
            "park",
            embed=dino_action_embed(
                "park",
                interaction.user,
                player.steam_id,
                result=result,
                success=True,
            ),
        )
        await interaction.followup.send(
            "✅ Dino parked successfully.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Unpark Dino",
        emoji="📤",
        style=discord.ButtonStyle.primary,
        custom_id="dino_storage:unpark",
    )
    async def unpark(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        player = await get_player_by_discord(str(interaction.user.id))
        if player is None:
            await interaction.edit_original_response(
                content="Link your SteamID before using dinosaur storage."
            )
            return
        dinosaurs = await list_inventory(str(interaction.user.id))
        if not dinosaurs:
            await interaction.edit_original_response(
                content="Your dinosaur inventory is empty."
            )
            return

        try:
            live = await get_live_dinosaur_status(player.steam_id)
        except DinoStorageError as error:
            await interaction.edit_original_response(content=f"❌ {error}")
            return
        except Exception:
            await interaction.edit_original_response(
                content="❌ The game server could not check your current dino."
            )
            return
        if live.get("ok") is not True:
            reason = live.get("reason") or "live-dino-check-failed"
            await interaction.edit_original_response(
                content=f"❌ Current dino check failed: `{reason}`"
            )
            return

        species = str(live.get("species") or "").strip()
        if not species:
            await interaction.edit_original_response(
                content="❌ The game server could not identify your current dino species."
            )
            return
        matching = [
            dinosaur
            for dinosaur in dinosaurs
            if str(dinosaur.get("species") or "").casefold() == species.casefold()
        ]
        if not matching:
            await interaction.edit_original_response(
                content=f"You have no parked **{species}** dinos."
            )
            return

        await interaction.edit_original_response(
            content=f"Choose which type of **{species}** you want to unpark:",
            view=ActionStatusView(
                str(interaction.user.id),
                matching,
                "unpark",
                player.steam_id,
            ),
        )

    @discord.ui.button(
        label="View Inventory",
        emoji="📦",
        style=discord.ButtonStyle.secondary,
        custom_id="dino_storage:inventory",
    )
    async def inventory(self, interaction: discord.Interaction, _button: discord.ui.Button):
        dinosaurs = await list_inventory(str(interaction.user.id))
        await interaction.response.send_message(
            embed=inventory_embed(interaction.user.display_name, dinosaurs),
            view=InventoryOverviewView(
                str(interaction.user.id),
                interaction.user.display_name,
                dinosaurs,
            ),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Delete Dino",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dino_storage:delete",
    )
    async def delete(self, interaction: discord.Interaction, _button: discord.ui.Button):
        dinosaurs = await list_inventory(str(interaction.user.id))
        if not dinosaurs:
            await interaction.response.send_message(
                "Your dinosaur inventory is empty.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Choose the dinosaur you want to permanently delete:",
            view=DinoSelectionView(str(interaction.user.id), dinosaurs, "delete"),
            ephemeral=True,
        )


class DinoStorage(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.app_commands.command(
        name="dinostoragepanel",
        description="Post the persistent dinosaur storage panel",
    )
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def storage_panel(self, interaction: discord.Interaction):
        await interaction.response.send_message("Dinosaur storage panel posted.", ephemeral=True)
        await interaction.channel.send(embed=storage_panel_embed(), view=DinoStoragePanelView())

    @discord.app_commands.command(
        name="dinoneedsprobe",
        description="Test the game's live nutrient setter on your current dino",
    )
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def dino_needs_probe(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        player = await get_player_by_discord(str(interaction.user.id))
        if player is None:
            await interaction.edit_original_response(
                content="❌ Link your SteamID before running the needs probe."
            )
            return

        try:
            result = await run_live_needs_probe(player.steam_id)
        except DinoStorageError as error:
            await interaction.edit_original_response(content=f"❌ {error}")
            return
        except Exception:
            await interaction.edit_original_response(
                content="❌ The game server could not complete the needs probe."
            )
            return

        if result.get("ok") is not True:
            reason = result.get("reason") or "needs-probe-failed"
            await interaction.edit_original_response(
                content=f"❌ Needs probe failed: `{reason}`"
            )
            return

        def number(name: str) -> float:
            try:
                return float(result.get(name))
            except (TypeError, ValueError):
                return 0.0

        embed = discord.Embed(
            title="LIVE DINO NEEDS PROBE",
            description=(
                "The game was asked to set each nutrient slot to **100**. "
                "The original live values were restored immediately afterward."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Dino",
            value=(
                f"Species: **{result.get('species') or 'Unknown'}**\n"
                f"Growth: **{number('growth') * 100:.2f}%**\n"
                f"Max hunger: **{number('maxHunger'):.3f}**\n"
                f"Max food: **{number('maxFoodValue'):.3f}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Raw nutrient values",
            value=(
                "```text\n"
                "             Carb       Protein      Lipid\n"
                f"Before   {number('beforeCarb'):10.3f}"
                f" {number('beforeProtein'):12.3f}"
                f" {number('beforeLipid'):10.3f}\n"
                f"Set 100  {number('nativeCarb'):10.3f}"
                f" {number('nativeProtein'):12.3f}"
                f" {number('nativeLipid'):10.3f}\n"
                f"Restored {number('restoredCarb'):10.3f}"
                f" {number('restoredProtein'):12.3f}"
                f" {number('restoredLipid'):10.3f}\n"
                "```"
            ),
            inline=False,
        )
        if result.get("originalValuesRestored") is not True:
            embed.add_field(
                name="Restoration warning",
                value=(
                    "The probe collected its measurements, but exact restoration "
                    "could not be confirmed. Do not run it again on this dino."
                ),
                inline=False,
            )
        await interaction.edit_original_response(embed=embed, content=None)

    @discord.app_commands.command(
        name="dinoprimeprobe",
        description="Read Prime/Elder state and derived stats from an online player's dino",
    )
    @discord.app_commands.describe(member="Linked online player to inspect; defaults to you")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def dino_prime_probe(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        target = member or interaction.user
        player = await get_player_by_discord(str(target.id))
        if player is None:
            await interaction.edit_original_response(
                content=f"❌ {target.mention} does not have a linked SteamID."
            )
            return

        try:
            result = await run_live_prime_probe(player.steam_id)
        except DinoStorageError as error:
            await interaction.edit_original_response(content=f"❌ {error}")
            return
        except Exception:
            await interaction.edit_original_response(
                content="❌ The game server could not complete the Prime probe."
            )
            return

        if result.get("ok") is not True:
            reason = result.get("reason") or "prime-probe-failed"
            await interaction.edit_original_response(
                content=f"❌ Prime probe failed: `{reason}`"
            )
            return

        def number(name: str) -> float:
            try:
                return float(result.get(name))
            except (TypeError, ValueError):
                return 0.0

        conditions = result.get("primeConditions")
        if not isinstance(conditions, list):
            conditions = []
        condition_text = " ".join(
            f"{index}:{'✅' if value is True else '❌'}"
            for index, value in enumerate(conditions, start=1)
        ) or "Unavailable"
        embed = discord.Embed(
            title="🔬 PRIME RESTORE PROBE",
            description=(
                "Read-only snapshot of the live dinosaur. No values were set, "
                "restored, or recalculated."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Identity and Prime state",
            value=(
                f"Player: **{target.display_name}**\n"
                f"Species: **{result.get('species') or 'Unknown'}**\n"
                f"Growth: **{number('growth') * 100:.3f}%**\n"
                f"Cached Prime flag: **{result.get('isPrime') is True}**\n"
                f"Prime struct eligible: **{result.get('primeEligible') is True}**\n"
                f"Completed: **{int(number('primeCompletedCount'))}/10**\n"
                f"Elder stacks: **{int(number('elderStacks'))}**"
            ),
            inline=False,
        )
        embed.add_field(name="Prime conditions", value=condition_text, inline=False)
        embed.add_field(
            name="Derived live values",
            value=(
                "```text\n"
                f"Weight             {number('weight'):.6f}\n"
                f"Base adult weight  {number('baseAdultWeight'):.6f}\n"
                f"Max health         {number('maxHealth'):.6f}\n"
                f"Max blood          {number('maxBlood'):.6f}\n"
                f"Max hunger         {number('maxHunger'):.6f}\n"
                f"Max food           {number('maxFoodValue'):.6f}\n"
                f"Max thirst         {number('maxThirst'):.6f}\n"
                f"Max stamina        {number('maxStamina'):.6f}\n"
                "```"
            ),
            inline=False,
        )
        embed.set_footer(text="READ ONLY • dinosaurModified=false")
        payload = json.dumps(result, indent=2, sort_keys=True).encode("utf-8")
        attachment = discord.File(
            io.BytesIO(payload),
            filename=f"prime-probe-{player.steam_id}.json",
        )
        await interaction.edit_original_response(
            embed=embed,
            content=None,
            attachments=[attachment],
        )

    @discord.app_commands.command(
        name="dinovenomdump",
        description="Generate one read-only UE4SS object dump for venom diagnosis",
    )
    @discord.app_commands.describe(
        confirm="Must be True; run only while the incorrect venom is active"
    )
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def dino_venom_dump(
        self,
        interaction: discord.Interaction,
        confirm: bool,
    ):
        if confirm is not True:
            await interaction.response.send_message(
                "❌ Nothing was dumped. Set `confirm` to **True** after reproducing the venom bug.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await run_ue4ss_object_dump()
        except Exception:
            await interaction.edit_original_response(
                content="❌ UE4SS did not finish the object dump within three minutes. Check `UE4SS.log`."
            )
            return
        if result.get("ok") is not True:
            reason = result.get("reason") or "object-dump-failed"
            await interaction.edit_original_response(
                content=f"❌ Object dump failed: `{reason}`"
            )
            return
        await interaction.edit_original_response(
            content=(
                "✅ Read-only object dump completed. Find **UE4SS_ObjectDump.txt** "
                "beside the active UE4SS installation, zip it, and send it privately here. "
                "It may contain player identifiers. The command is now locked until the next server restart."
            )
        )

    @discord.app_commands.command(
        name="dinoplayerdataprobe",
        description="Record a read-only live venom timeline sample",
    )
    @discord.app_commands.describe(member="Linked online player to inspect; defaults to you")
    @discord.app_commands.checks.has_permissions(administrator=True)
    async def dino_player_data_probe(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        target = member or interaction.user
        player = await get_player_by_discord(str(target.id))
        if player is None:
            await interaction.edit_original_response(
                content=f"❌ {target.mention} does not have a linked SteamID."
            )
            return
        try:
            result = await run_live_player_data_probe(player.steam_id)
        except DinoStorageError as error:
            await interaction.edit_original_response(content=f"❌ {error}")
            return
        except Exception:
            await interaction.edit_original_response(
                content="❌ The game server could not record the venom timeline sample."
            )
            return
        if result.get("ok") is not True:
            reason = result.get("reason") or "player-data-probe-failed"
            await interaction.edit_original_response(
                content=f"❌ Venom timeline sample failed: `{reason}`"
            )
            return

        live = result.get("livePawn") if isinstance(result.get("livePawn"), dict) else {}
        embed = discord.Embed(
            title="LIVE VENOM TIMELINE SAMPLE",
            description=(
                "Read-only live-pawn snapshot. Run it before venom, during venom, after the "
                "delayed effect, and after unpark to build a timeline."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Live pawn",
            value=(
                f"Player: **{target.display_name}**\n"
                f"Species: **{live.get('species') or 'Unknown'}**\n"
                f"Venom property: **{live.get('venomStatusText') or live.get('venomStatus')}**\n"
                f"GetVenomStatus: **{live.get('getVenomStatusText') or live.get('getVenomStatus')}**\n"
                f"Has any venom stack: **{live.get('hasAnyVenomStack')}**\n"
                f"Last venom pouncer: **{live.get('lastVenomPouncer') or 'None'}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Timeline",
            value=(
                f"Server file: **{result.get('timelineFile') or 'Unavailable'}**\n"
                "Native save calls: **None**\n"
                "Save-manager calls: **None**\n"
                "Dinosaur modified: **False**"
            ),
            inline=False,
        )
        embed.set_footer(text="READ ONLY • saved automatically • dinosaurModified=false")
        payload = json.dumps(result, indent=2, sort_keys=True).encode("utf-8")
        attachment = discord.File(
            io.BytesIO(payload),
            filename=f"venom-timeline-sample-{player.steam_id}.json",
        )
        await interaction.edit_original_response(
            embed=embed,
            content=None,
            attachments=[attachment],
        )


async def setup(bot: commands.Bot):
    await init_dino_storage_database()
    bot.add_view(DinoStoragePanelView())
    await bot.add_cog(DinoStorage(bot))
