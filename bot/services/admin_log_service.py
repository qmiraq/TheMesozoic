import json
import logging
from typing import Any

import discord


LOGGER = logging.getLogger(__name__)

ADMIN_LOG_THREADS = {
    "presence": 1529859131647987923,
    "steam_link": 1529859243497488464,
    "park": 1529859427811852290,
    "unpark": 1529859461492113498,
    "delete": 1529859615745904720,
    "points": 1529859677670608926,
    "replacement": 1529859708070924369,
    "admin_action": 1529860075257073794,
    "global_chat": 1529860133759357119,
    "death": 1529860243939659856,
    "kill_feed": 1535257257284210759,
}

MUTATION_GROUPS = (
    (
        "Slot mutations",
        (
            ("Slot1", "MutationSlot1"),
            ("Slot2", "MutationSlot2"),
            ("Slot3", "MutationSlot3"),
            ("Slot4", "MutationSlot4"),
        ),
    ),
    (
        "Baby mutations",
        (
            ("Baby1", "ParentMutationSlot1"),
            ("Baby2", "ParentMutationSlot2"),
            ("Baby3", "ParentMutationSlot3"),
            ("Baby4", "ParentMutationSlot4"),
        ),
    ),
    (
        "Entomb mutations 1–4",
        (
            ("Entomb1", "ElderMutationSlot1A"),
            ("Entomb2", "ElderMutationSlot2A"),
            ("Entomb3", "ElderMutationSlot3A"),
            ("Entomb4", "ElderMutationSlot4A"),
        ),
    ),
    (
        "Entomb mutations 5–8",
        (
            ("Entomb5", "ElderMutationSlot1B"),
            ("Entomb6", "ElderMutationSlot2B"),
            ("Entomb7", "ElderMutationSlot3B"),
            ("Entomb8", "ElderMutationSlot4B"),
        ),
    ),
)


async def send_admin_log(
    bot: discord.Client,
    route: str,
    *,
    embed: discord.Embed | None = None,
    content: str | None = None,
) -> bool:
    thread_id = ADMIN_LOG_THREADS.get(route)
    if thread_id is None:
        LOGGER.warning("Unknown admin log route: %s", route)
        return False

    try:
        destination = bot.get_channel(thread_id)
        if destination is None:
            destination = await bot.fetch_channel(thread_id)
        if not hasattr(destination, "send"):
            LOGGER.warning("Admin log destination %s is not sendable", thread_id)
            return False
        await destination.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as error:
        LOGGER.warning("Could not send %s admin log to %s: %s", route, thread_id, error)
    except Exception:
        LOGGER.exception("Unexpected failure while sending %s admin log", route)
    return False


def parse_snapshot(dinosaur: dict[str, Any] | None) -> dict[str, Any]:
    if not dinosaur:
        return {}
    serialized = dinosaur.get("serialized_player_data")
    if isinstance(serialized, str):
        try:
            parsed = json.loads(serialized)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return dinosaur if isinstance(dinosaur, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _percentage(snapshot: dict[str, Any], value_key: str, maximum_key: str) -> str:
    value = _number(snapshot.get(value_key))
    maximum = _number(snapshot.get(maximum_key))
    if value is None or maximum is None or maximum <= 0:
        return "Unknown"
    return f"{max(0.0, min(100.0, value / maximum * 100.0)):.1f}%"


def _nutrient_percentage(snapshot: dict[str, Any], nutrient_name: str) -> str:
    generated = snapshot.get("generatedNutrientPercents")
    if isinstance(generated, dict):
        value = _number(generated.get(nutrient_name))
        if value is not None:
            return f"{max(0.0, min(100.0, value)):.1f}%"
    captured = snapshot.get("nutrientPercentages")
    if isinstance(captured, dict):
        value = _number(captured.get(nutrient_name))
        if value is not None:
            return f"{max(0.0, min(100.0, value)):.1f}%"
    if snapshot.get("generatedShop") is True:
        nutrients = snapshot.get("nutrients")
        nutrients = nutrients if isinstance(nutrients, dict) else {}
        field = {
            "carb": "carbValue",
            "protein": "proteinValue",
            "lipid": "lipidValue",
        }[nutrient_name]
        value = _number(nutrients.get(field))
        if value is not None:
            return f"{max(0.0, min(100.0, value * 100.0)):.1f}%"
    return "Unknown"


def _growth(snapshot: dict[str, Any], fallback: Any = None) -> str:
    value = _number(snapshot.get("growth"))
    if value is None:
        value = _number(fallback)
    return "Unknown" if value is None else f"{max(0.0, value * 100.0):.1f}%"


def _status(snapshot: dict[str, Any]) -> str:
    is_prime = snapshot.get("isPrime") is True or snapshot.get("primeEligible") is True
    return "Prime Elder 👑" if is_prime else "Frail Elder 🐢"


def _sex(snapshot: dict[str, Any], fallback: Any = None) -> str:
    if snapshot.get("isFemale") is True:
        return "Female"
    if snapshot.get("isFemale") is False:
        return "Male"
    text = str(fallback or "").strip().lower()
    return "Female" if text == "female" else "Male"


def _species(snapshot: dict[str, Any], fallback: Any = None) -> str:
    return str(snapshot.get("species") or fallback or "Unknown")


def add_dinosaur_fields(
    embed: discord.Embed,
    snapshot: dict[str, Any],
    *,
    species: Any = None,
    growth: Any = None,
    sex: Any = None,
    include_full_stats: bool = False,
    include_mutations: bool = False,
) -> None:
    elder_stacks = max(0, min(3, int(_number(snapshot.get("elderStacks")) or 0)))
    embed.add_field(
        name="Dino",
        value=(
            f"**Class:** {_species(snapshot, species)}\n"
            f"**Growth:** {_growth(snapshot, growth)}\n"
            f"**Status:** {_status(snapshot)}\n"
            f"**Gender:** {_sex(snapshot, sex)}\n"
            f"**Entombments:** {elder_stacks}/3"
        ),
        inline=False,
    )
    if not include_full_stats:
        return

    embed.add_field(
        name="Core Stats",
        value=(
            f"**Health:** {_percentage(snapshot, 'health', 'maxHealth')}\n"
            f"**Blood:** {_percentage(snapshot, 'blood', 'maxBlood')}\n"
            f"**Stamina:** {_percentage(snapshot, 'stamina', 'maxStamina')}"
        ),
        inline=True,
    )
    embed.add_field(
        name="Needs",
        value=(
            f"**Food:** {_percentage(snapshot, 'hunger', 'maxHunger')}\n"
            f"**Water:** {_percentage(snapshot, 'thirst', 'maxThirst')}"
        ),
        inline=True,
    )
    embed.add_field(
        name="Nutrients",
        value=(
            f"**Carb:** {_nutrient_percentage(snapshot, 'carb')}\n"
            f"**Protein:** {_nutrient_percentage(snapshot, 'protein')}\n"
            f"**Lipid:** {_nutrient_percentage(snapshot, 'lipid')}"
        ),
        inline=True,
    )
    prime_data = snapshot.get("primeData")
    prime_data = prime_data if isinstance(prime_data, dict) else {}
    completed = int(_number(prime_data.get("completedCount")) or 0)
    embed.add_field(name="Prime Tasks", value=f"**Completed:** {completed}/10", inline=True)

    location = snapshot.get("location")
    if isinstance(location, dict):
        x = _number(location.get("x"))
        y = _number(location.get("y"))
        z = _number(location.get("z"))
        if x is not None and y is not None and z is not None:
            embed.add_field(
                name="Last Location",
                value=f"`X {x:.0f} • Y {y:.0f} • Z {z:.0f}`",
                inline=False,
            )

    if include_mutations:
        mutations = snapshot.get("mutations")
        mutations = mutations if isinstance(mutations, dict) else {}
        for group_name, fields in MUTATION_GROUPS:
            lines = [
                f"**{label}:** {str(mutations.get(field) or 'Empty')}"
                for label, field in fields
            ]
            embed.add_field(name=group_name, value="\n".join(lines), inline=False)


def dino_action_embed(
    action: str,
    user: discord.abc.User,
    steam_id: str,
    *,
    dinosaur: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    success: bool,
    reason: str = "",
    location_mode: str = "",
) -> discord.Embed:
    snapshot = parse_snapshot(dinosaur)
    result = result or {}
    if not snapshot and result:
        snapshot = result
    color = discord.Color.green() if success else discord.Color.red()
    embed = discord.Embed(
        title=f"{'✅' if success else '❌'} Dino {action.title()}",
        color=color,
    )
    embed.add_field(
        name="Player",
        value=(
            f"**Discord:** {user} (`{user.id}`)\n"
            f"**SteamID:** `{steam_id}`"
        ),
        inline=False,
    )
    add_dinosaur_fields(
        embed,
        snapshot,
        species=(dinosaur or {}).get("species") or result.get("species"),
        growth=(dinosaur or {}).get("growth") or result.get("growth"),
        sex=(dinosaur or {}).get("sex"),
    )
    embed.add_field(name="Result", value="Successful" if success else "Failed", inline=True)
    if location_mode:
        embed.add_field(
            name="Location",
            value="Saved location" if location_mode == "saved" else "Current location",
            inline=True,
        )
    if reason:
        embed.add_field(name="Details", value=str(reason)[:1024], inline=False)
    return embed


def steam_link_embed(user: discord.abc.User, steam_id: str) -> discord.Embed:
    return discord.Embed(
        title="🔑 Steam Linked",
        description=(
            f"**Discord:** {user.mention}\n"
            f"**SteamID:** `{steam_id}`"
        ),
        color=discord.Color.orange(),
    )


def points_embed(
    admin: discord.abc.User,
    target_discord_id: str,
    target_steam_id: str,
    amount: int,
    balance: int,
) -> discord.Embed:
    embed = discord.Embed(title="💲 Player Points Adjusted", color=discord.Color.gold())
    embed.add_field(name="Admin", value=f"{admin} (`{admin.id}`)", inline=False)
    embed.add_field(
        name="Player",
        value=f"Discord: <@{target_discord_id}> (`{target_discord_id}`)\nSteamID: `{target_steam_id}`",
        inline=False,
    )
    embed.add_field(name="Adjustment", value=f"{amount:+,} points", inline=True)
    embed.add_field(name="New Balance", value=f"{balance:,} points", inline=True)
    return embed


def replacement_embed(
    admin: discord.abc.User,
    result: dict[str, Any],
) -> discord.Embed:
    snapshot = parse_snapshot(result)
    embed = discord.Embed(title="🦖 Replacement Dino Granted", color=discord.Color.orange())
    embed.add_field(name="Admin", value=f"{admin} (`{admin.id}`)", inline=False)
    embed.add_field(
        name="Recipient",
        value=(
            f"Discord: <@{result.get('discord_id')}> (`{result.get('discord_id')}`)\n"
            f"SteamID: `{result.get('steam_id')}`"
        ),
        inline=False,
    )
    add_dinosaur_fields(
        embed,
        snapshot,
        species=result.get("species"),
        growth=result.get("growth"),
        sex=result.get("sex"),
        include_full_stats=True,
        include_mutations=True,
    )
    return embed


def presence_embed(event: dict[str, Any]) -> discord.Embed:
    joined = event.get("event") == "login"
    embed = discord.Embed(
        title="🟢 Player Joined" if joined else "🔴 Player Left",
        color=discord.Color.green() if joined else discord.Color.red(),
    )
    name = str(event.get("name") or "Unknown")
    steam = str(event.get("steam") or "Unknown")
    embed.add_field(name="Player", value=f"**Name:** {name}\n**SteamID:** `{steam}`", inline=False)
    species = str(event.get("species") or "")
    if species:
        try:
            growth = float(event.get("growth") or 0) * 100
            growth_text = f"{growth:.1f}%"
        except (TypeError, ValueError):
            growth_text = "Unknown"
        sex = str(event.get("sex") or "Unknown")
        embed.add_field(
            name="Last Dino",
            value=f"**Class:** {species}\n**Gender:** {sex}\n**Growth:** {growth_text}",
            inline=False,
        )
    if not joined:
        logout_type = str(event.get("logoutType") or "hard/combat logout")
        embed.add_field(name="Logout", value=logout_type, inline=False)
    return embed


def presence_log_line(event: dict[str, Any]) -> str:
    def clean(value: Any, fallback: str = "Unknown") -> str:
        text = str(value or fallback)
        return (
            text.replace("\x1b", "")
            .replace("`", "'")
            .replace("\r", " ")
            .replace("\n", " ")
        )[:120]

    joined = event.get("event") == "login"
    name = clean(event.get("name"))
    steam = clean(event.get("steam"))
    species = clean(event.get("species"), "")
    sex = clean(event.get("sex"), "")
    try:
        growth = f"{float(event.get('growth')) * 100:.1f}%"
    except (TypeError, ValueError):
        growth = ""
    dino = ", ".join(part for part in (species, sex, growth) if part)

    identity_color = "32" if joined else "31"
    identity = f"\u001b[2;{identity_color}m{name} [{steam}]\u001b[0m"
    if joined:
        save_found = event.get("saveFound") is True or bool(species)
        if save_found:
            result = (
                "\u001b[1;35mSave Found:\u001b[0m "
                f"\u001b[2;33m{dino or 'Unknown'}\u001b[0m"
            )
        else:
            result = "\u001b[1;35mNo Save Found\u001b[0m"
        line = f"{identity} \u001b[1;37mjoined\u001b[0m - {result}"
    else:
        logout_type = clean(event.get("logoutType"), "Hardlogged")
        result = f"\u001b[1;37m{logout_type}\u001b[0m"
        if dino:
            result += f": \u001b[2;33m{dino}\u001b[0m"
        line = f"{identity} \u001b[1;37mleft\u001b[0m - {result}"
    return f"```ansi\n{line}\n```"


def kill_feed_log_line(event: dict[str, Any]) -> str:
    """Build an anonymous, bold ANSI kill-feed line."""

    def clean_class(value: Any) -> str:
        text = str(value or "Unknown")
        return (
            text.replace("\x1b", "")
            .replace("`", "'")
            .replace("\r", " ")
            .replace("\n", " ")
        )[:120]

    def growth(value: Any) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "Unknown"
        percentage = number * 100.0 if abs(number) <= 1.0 else number
        return f"{max(0.0, min(100.0, percentage)):.1f}%"

    victim = (
        f"{clean_class(event.get('species'))} "
        f"({growth(event.get('growth'))})"
    )
    if str(event.get("cause") or "natural") == "player":
        killer = (
            f"{clean_class(event.get('killerSpecies'))} "
            f"({growth(event.get('killerGrowth'))})"
        )
        line = (
            f"\u001b[1;32m{killer}\u001b[0m "
            f"\u001b[1;37mkilled\u001b[0m "
            f"\u001b[1;31m{victim}\u001b[0m"
        )
    else:
        line = (
            f"\u001b[1;31m{victim}\u001b[0m "
            f"\u001b[1;37mdied of natural causes.\u001b[0m"
        )
    return f"```ansi\n{line}\n```"


def admin_action_embed(event: dict[str, Any]) -> discord.Embed:
    labels = {
        "enter_specmode": "Entered Spectator Mode",
        "exit_specmode": "Exited Spectator Mode",
        "teleport_to_player": "Teleported to Player",
        "bring_player": "Brought Player",
        "slay": "Slayed Player",
        "heal": "Healed Player",
        "grow": "Changed Player Growth",
    }
    action = str(event.get("action") or "unknown")
    embed = discord.Embed(
        title=f"🛡️ {labels.get(action, action.replace('_', ' ').title())}",
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="Admin",
        value=f"**Name:** {event.get('adminName') or 'Unknown'}\n**SteamID:** `{event.get('adminSteam') or 'Unknown'}`",
        inline=False,
    )
    if event.get("targetSteam") or event.get("targetName"):
        embed.add_field(
            name="Target",
            value=f"**Name:** {event.get('targetName') or 'Unknown'}\n**SteamID:** `{event.get('targetSteam') or 'Unknown'}`",
            inline=False,
        )
    if event.get("percent") not in (None, ""):
        embed.add_field(name="Growth Value", value=str(event["percent"]), inline=False)
    return embed


def admin_action_log_line(event: dict[str, Any]) -> str:
    def clean(value: Any, fallback: str = "Unknown") -> str:
        text = str(value or fallback)
        return (
            text.replace("\x1b", "")
            .replace("`", "'")
            .replace("\r", " ")
            .replace("\n", " ")
        )[:120]

    action = str(event.get("action") or "unknown")
    action_labels = {
        "enter_specmode": ("Entered Spec Mode", "32"),
        "exit_specmode": ("Exited Spec Mode", "33"),
        "teleport_to_player": ("Teleported to", "36"),
        "bring_player": ("Brought", "36"),
        "heal": ("Healed", "31"),
        "grow": ("Grew", "33"),
        "slay": ("Slayed", "36"),
    }
    label, action_color = action_labels.get(
        action,
        (clean(action.replace("_", " ").title()), "37"),
    )
    admin_name = clean(event.get("adminName"))
    admin_steam = clean(event.get("adminSteam"))
    target_name = clean(event.get("targetName"))
    target_steam = clean(event.get("targetSteam"))

    identity = (
        f"\u001b[1;36m{admin_name}\u001b[0m "
        f"\u001b[1;35m({admin_steam})\u001b[0m"
    )
    line = f"{identity} \u001b[1;{action_color}m{label}\u001b[0m"
    if action not in {"enter_specmode", "exit_specmode"}:
        target = (
            f"\u001b[1;35m{target_name}\u001b[0m "
            f"\u001b[1;35m({target_steam})\u001b[0m"
        )
        line += f" {target}"
    return f"```ansi\n{line}\n```"


def global_chat_log_line(event: dict[str, Any]) -> str:
    def clean(value: Any, fallback: str = "") -> str:
        text = str(value or fallback)
        return (
            text.replace("\x1b", "")
            .replace("`", "'")
            .replace("\r", " ")
            .replace("\n", " ")
        )

    name = clean(event.get("name"), "Unknown")[:120]
    steam = clean(event.get("steam"), "Unknown")[:120]
    message = clean(event.get("message")).strip()[:1700]
    line = (
        f"\u001b[1;36m{name}\u001b[0m "
        f"\u001b[1;35m({steam})\u001b[0m"
        f"\u001b[1;37m: {message}\u001b[0m"
    )
    return f"```ansi\n{line}\n```"


def death_embed(event: dict[str, Any], snapshot: dict[str, Any]) -> discord.Embed:
    cause = str(event.get("cause") or "natural")
    title = "⚔️ Player-Caused Death" if cause == "player" else "☠️ Player Death"
    if cause == "admin-slay":
        title = "🛡️ Admin Slay Death"
    elif cause == "self-slay":
        title = "☠️ Player Self-Slay"
    embed = discord.Embed(title=title, color=discord.Color.dark_red())
    embed.add_field(
        name="Victim",
        value=f"**Name:** {event.get('victimName') or 'Unknown'}\n**SteamID:** `{event.get('victimSteam') or 'Unknown'}`",
        inline=False,
    )
    if event.get("killerSteam"):
        embed.add_field(
            name="Responsible Player",
            value=f"**Name:** {event.get('killerName') or 'Unknown'}\n**SteamID:** `{event.get('killerSteam')}`",
            inline=False,
        )
    cause_labels = {
        "player": "Player-caused (recent direct hit)",
        "admin-slay": "Admin slay",
        "self-slay": "Player used the Discord Slay button",
        "natural": "Natural/environmental",
    }
    embed.add_field(name="Cause", value=cause_labels.get(cause, cause), inline=False)
    add_dinosaur_fields(
        embed,
        snapshot,
        species=event.get("species"),
        growth=event.get("growth"),
        sex=event.get("sex"),
        include_full_stats=True,
        include_mutations=True,
    )
    return embed
