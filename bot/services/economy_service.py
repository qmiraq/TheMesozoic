from __future__ import annotations

import json
import math
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from bot.services.dino_storage_service import (
    DATABASE_PATH,
    DinoStorageError,
    MUTATION_FIELDS,
    build_admin_grant_snapshot,
    payload_digest,
)
from bot.services.point_management_service import points_per_five_minutes


PLAYER_DATABASE_PATH = Path(os.getenv("EVRIMABOT_DATABASE", "evrimabot.db"))
PLAYTIME_REWARD_SECONDS = 300
PLAYTIME_REWARD_POINTS = 10
ONLINE_GAP_TOLERANCE_SECONDS = 120
MILESTONE_FIXED_HOURS = (1, 2, 6, 12, 18)
MINIMUM_PRICE = 10
MINIMUM_SALE_HOURS = 1
MAXIMUM_SALE_HOURS = 24

PLAYABLE_SPECIES = (
    "Allosaurus", "Austroraptor", "Beipiaosaurus", "Carnotaurus", "Ceratosaurus",
    "Deinosuchus", "Diabloceratops", "Dilophosaurus", "Dryosaurus",
    "Gallimimus", "Herrerasaurus", "Hypsilophodon", "Kentrosaurus",
    "Maiasaura", "Omniraptor", "Pachycephalosaurus", "Pteranodon",
    "Stegosaurus", "Tenontosaurus", "Triceratops", "Troodon",
    "Tyrannosaurus",
)

HERBIVORE_OMNIVORE_MUTATIONS = tuple(sorted({
    "Xerocole Adaptation", "Hypervigilance", "Truculency", "Photosynthetic Regeneration",
    "Cellular Regeneration", "Advanced Gestation", "Sustained Hydration", "Enlarged meniscus",
    "Efficient Digestion", "Featherweight", "Osteosclerosis", "Wader", "Epidermal Fibrosis",
    "Congenital Hypoalgesia", "Photosynthetic Tissue", "Nocturnal", "Hydroregenerative",
    "Increased Inspiratory Capacity", "Hydrodynamic", "Submerged Optical Retention",
    "Reabsorption", "Enhanced Digestion", "Reinforced Tendons", "Reniculate Kidneys",
    "Multichambered Lungs", "Infrasound Communication", "Sequential Hermaphroditism",
    "Barometric Sensitivity", "Tactile Endurance", "Gastronomic Regeneration",
    "Heightened Ghrelin", "Prolific Reproduction", "Parthenogenesis",
}, key=str.casefold))
CARNIVORE_MUTATIONS = tuple(sorted({
    "Photosynthetic Regeneration", "Cellular Regeneration", "Advanced Gestation",
    "Sustained Hydration", "Enlarged meniscus", "Efficient Digestion", "Featherweight",
    "Osteosclerosis", "Wader", "Epidermal Fibrosis", "Congenital Hypoalgesia",
    "Photosynthetic Tissue", "Nocturnal", "Hydroregenerative", "Increased Inspiratory Capacity",
    "Hydrodynamic", "Submerged Optical Retention", "Reabsorption", "Enhanced Digestion",
    "Reinforced Tendons", "Reniculate Kidneys", "Multichambered Lungs",
    "Infrasound Communication", "Sequential Hermaphroditism", "Augmented Tapetum",
    "Tactile Endurance", "Gastronomic Regeneration", "Heightened Ghrelin",
    "Prolific Reproduction", "Parthenogenesis", "Hemomania", "Hematophagy",
    "Accelerated Prey Drive", "Osteophagic", "Cannibalistic", "Hypermetabolic Inanition",
}, key=str.casefold))
CARNIVORE_SPECIES = frozenset({
    "Allosaurus", "Austroraptor", "Carnotaurus", "Ceratosaurus", "Deinosuchus",
    "Dilophosaurus", "Herrerasaurus", "Omniraptor", "Pteranodon", "Troodon", "Tyrannosaurus",
})
DEFAULT_MUTATIONS = tuple(sorted(set(HERBIVORE_OMNIVORE_MUTATIONS) | set(CARNIVORE_MUTATIONS), key=str.casefold))

# Shop Tokens intentionally exclude progression mutations that must be earned
# in-game.  A second group is valid only in the game's even mutation slots.
# These restrictions are Shop Token-only; admin grants and captured dinosaurs
# continue to use the complete species catalog above.
SHOP_TOKEN_EXCLUDED_MUTATIONS = frozenset({
    "Reniculate Kidneys", "Osteophagic", "Reinforced Tendons",
})
SHOP_TOKEN_EVEN_SLOT_MUTATIONS = frozenset({
    "Tactile Endurance", "Gastronomic Regeneration",
    "Hypermetabolic Inanition", "Augmented Tapetum",
    "Prolific Reproduction", "Enhanced Digestion", "Heightened Ghrelin",
    "Parthenogenesis", "Cannibalistic",
})

def mutations_for_species(species: str) -> tuple[str, ...]:
    if species not in PLAYABLE_SPECIES:
        raise EconomyError("That dinosaur class is not in the playable-species list.")
    return CARNIVORE_MUTATIONS if species in CARNIVORE_SPECIES else HERBIVORE_OMNIVORE_MUTATIONS


def shop_token_mutations_for_slot(species: str, slot: int) -> tuple[str, ...]:
    slot = int(slot)
    if slot not in (1, 2, 3, 4):
        raise EconomyError("Shop Token mutation slot must be between 1 and 4.")
    values = (
        value for value in mutations_for_species(species)
        if value not in SHOP_TOKEN_EXCLUDED_MUTATIONS
        and (slot in (2, 4) or value not in SHOP_TOKEN_EVEN_SLOT_MUTATIONS)
    )
    return tuple(values)

PRIME_TASK_NAMES = (
    "Visit a Sanctuary as a Juvenile", "Get Nested In", "Get Perfect Diet",
    "Visit Mass Migration Zone", "Visit 2 Migration Zones",
    "Visit 4 Patrol Zones", "Never be infertile", "Never get muscle spasms",
    "Raise children to subadult", "Species task",
)

ADMIN_MUTATION_SLOTS = (
    ("Slot1", "MutationSlot1"), ("Slot2", "MutationSlot2"),
    ("Slot3", "MutationSlot3"), ("Slot4", "MutationSlot4"),
    ("Baby1", "ParentMutationSlot1"), ("Baby2", "ParentMutationSlot2"),
    ("Baby3", "ParentMutationSlot3"), ("Baby4", "ParentMutationSlot4"),
    ("Entomb1", "ElderMutationSlot1A"), ("Entomb2", "ElderMutationSlot2A"),
    ("Entomb3", "ElderMutationSlot3A"), ("Entomb4", "ElderMutationSlot4A"),
    ("Entomb5", "ElderMutationSlot1B"), ("Entomb6", "ElderMutationSlot2B"),
    ("Entomb7", "ElderMutationSlot3B"), ("Entomb8", "ElderMutationSlot4B"),
)


class EconomyError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def current_playtime_reward_points(
    now: datetime | None = None,
    discord_role_ids: set[str] | None = None,
) -> int:
    """Return the configured five-minute rate, including roles and promotions."""
    return points_per_five_minutes(discord_role_ids, now)


def milestone_threshold_hours(milestone_number: int) -> int:
    milestone_number = max(1, int(milestone_number))
    if milestone_number <= len(MILESTONE_FIXED_HOURS):
        return MILESTONE_FIXED_HOURS[milestone_number - 1]
    return 18 + ((milestone_number - len(MILESTONE_FIXED_HOURS)) * 6)


def milestone_reward_points(milestone_number: int) -> int:
    milestone_number = max(1, int(milestone_number))
    if milestone_number == 1:
        return 100
    if milestone_number == 2:
        return 200
    return 300


def milestone_entitlement(total_seconds: int | float) -> dict:
    played_seconds = max(0, int(float(total_seconds or 0)))
    completed = 0
    points = 0
    while played_seconds >= milestone_threshold_hours(completed + 1) * 3600:
        completed += 1
        points += milestone_reward_points(completed)
    return {"completed_milestones": completed, "points": points}


def _row_dict(row: aiosqlite.Row | None) -> dict | None:
    return dict(row) if row is not None else None


async def init_economy_database() -> None:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA journal_mode=WAL;")
        await database.execute("PRAGMA busy_timeout=5000;")
        await database.executescript(
            """
            CREATE TABLE IF NOT EXISTS economy_accounts
            (
                discord_id TEXT PRIMARY KEY,
                steam_id TEXT NOT NULL UNIQUE,
                points INTEGER NOT NULL DEFAULT 0 CHECK(points >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS economy_ledger
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id TEXT NOT NULL,
                steam_id TEXT,
                delta INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                reason TEXT NOT NULL,
                reference_id TEXT NOT NULL UNIQUE,
                actor_discord_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS ix_economy_ledger_owner
            ON economy_ledger(discord_id, id DESC);

            CREATE TABLE IF NOT EXISTS economy_playtime_state
            (
                steam_id TEXT PRIMARY KEY,
                discord_id TEXT NOT NULL,
                accumulated_seconds REAL NOT NULL DEFAULT 0,
                total_seconds REAL NOT NULL DEFAULT 0,
                last_seen_epoch REAL NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS economy_playtime_milestones
            (
                steam_id TEXT NOT NULL,
                discord_id TEXT NOT NULL,
                milestone_number INTEGER NOT NULL,
                threshold_hours INTEGER NOT NULL,
                points INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                source TEXT NOT NULL,
                awarded_at TEXT NOT NULL,
                PRIMARY KEY (steam_id, milestone_number)
            );

            CREATE INDEX IF NOT EXISTS ix_economy_milestones_owner
            ON economy_playtime_milestones(discord_id, milestone_number);

            CREATE TABLE IF NOT EXISTS economy_mutation_catalog
            (
                name TEXT PRIMARY KEY,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS economy_shop_listings
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                species TEXT NOT NULL,
                mutation1 TEXT,
                mutation2 TEXT,
                mutation3 TEXT,
                mutation4 TEXT,
                price INTEGER NOT NULL CHECK(price >= 10),
                active INTEGER NOT NULL DEFAULT 1,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS ix_economy_shop_active
            ON economy_shop_listings(active, species, id);

            CREATE TABLE IF NOT EXISTS economy_market_sales
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dinosaur_id INTEGER NOT NULL,
                seller_discord_id TEXT NOT NULL,
                seller_steam_id TEXT NOT NULL,
                buyer_discord_id TEXT,
                price INTEGER NOT NULL CHECK(price >= 10),
                status TEXT NOT NULL DEFAULT 'active',
                channel_id TEXT,
                message_id TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS ix_economy_sales_seller_status
            ON economy_market_sales(seller_discord_id, status, id DESC);

            CREATE UNIQUE INDEX IF NOT EXISTS ux_economy_sales_active_dinosaur
            ON economy_market_sales(dinosaur_id) WHERE status = 'active';
            """
        )
        playtime_columns = {
            str(row[1])
            for row in await (
                await database.execute(
                    "PRAGMA table_info(economy_playtime_state);"
                )
            ).fetchall()
        }
        if "total_seconds" not in playtime_columns:
            await database.execute(
                """
                ALTER TABLE economy_playtime_state
                ADD COLUMN total_seconds REAL NOT NULL DEFAULT 0;
                """
            )
        cursor = await database.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'economy_market_sales';"
        )
        schema_row = await cursor.fetchone()
        compact_schema = " ".join(str(schema_row[0] if schema_row else "").lower().split())
        if "dinosaur_id integer not null unique" in compact_schema:
            await database.executescript(
                """
                ALTER TABLE economy_market_sales RENAME TO economy_market_sales_legacy;

                CREATE TABLE economy_market_sales
                (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dinosaur_id INTEGER NOT NULL,
                    seller_discord_id TEXT NOT NULL,
                    seller_steam_id TEXT NOT NULL,
                    buyer_discord_id TEXT,
                    price INTEGER NOT NULL CHECK(price >= 10),
                    status TEXT NOT NULL DEFAULT 'active',
                    channel_id TEXT,
                    message_id TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    completed_at TEXT
                );

                INSERT INTO economy_market_sales
                    (id, dinosaur_id, seller_discord_id, seller_steam_id,
                     buyer_discord_id, price, status, channel_id, message_id,
                     created_at, expires_at, completed_at)
                SELECT id, dinosaur_id, seller_discord_id, seller_steam_id,
                       buyer_discord_id, price, status, channel_id, message_id,
                       created_at, expires_at, completed_at
                FROM economy_market_sales_legacy;

                DROP TABLE economy_market_sales_legacy;

                CREATE INDEX ix_economy_sales_seller_status
                ON economy_market_sales(seller_discord_id, status, id DESC);

                CREATE UNIQUE INDEX ux_economy_sales_active_dinosaur
                ON economy_market_sales(dinosaur_id) WHERE status = 'active';
                """
            )
        now = _utc_now()
        await database.executemany(
            """
            INSERT INTO economy_mutation_catalog(name, last_seen_at)
            VALUES (?, ?)
            ON CONFLICT(name) DO NOTHING;
            """,
            [(name, now) for name in DEFAULT_MUTATIONS],
        )
        # v0.12 Shop Tokens: permanent listings no longer prescribe mutations.
        await database.execute(
            "UPDATE economy_shop_listings SET mutation1 = NULL, mutation2 = NULL, mutation3 = NULL, mutation4 = NULL, updated_at = ? "
            "WHERE mutation1 IS NOT NULL OR mutation2 IS NOT NULL OR mutation3 IS NOT NULL OR mutation4 IS NOT NULL;",
            (now,),
        )
        cursor = await database.execute(
            "SELECT id, serialized_player_data FROM stored_dinosaurs WHERE status = 'parked' AND source = 'shop_purchase';"
        )
        for row in await cursor.fetchall():
            try:
                snapshot = json.loads(str(row[1] or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(snapshot, dict):
                continue
            mutation_map = snapshot.get("mutations")
            if not isinstance(mutation_map, dict):
                mutation_map = {field: "" for field in MUTATION_FIELDS}
            for field in MUTATION_FIELDS:
                mutation_map[field] = ""
            snapshot["mutations"] = mutation_map
            snapshot["unlockRequiredMutations"] = []
            snapshot["generatedShop"] = True
            snapshot["shopToken"] = True
            snapshot["shopTokenMutationsSelected"] = False
            serialized = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
            await database.execute(
                "UPDATE stored_dinosaurs SET source = 'shop_token', serialized_player_data = ?, payload_sha256 = ?, updated_at = ? WHERE id = ?;",
                (serialized, payload_digest(serialized), now, int(row[0])),
            )
        await database.commit()
    await refresh_mutation_catalog()


async def _ensure_account(
    database: aiosqlite.Connection,
    discord_id: str,
    steam_id: str,
) -> None:
    now = _utc_now()
    await database.execute(
        """
        INSERT INTO economy_accounts(discord_id, steam_id, points, created_at, updated_at)
        VALUES (?, ?, 0, ?, ?)
        ON CONFLICT(discord_id) DO UPDATE SET
            steam_id = excluded.steam_id,
            updated_at = excluded.updated_at;
        """,
        (str(discord_id), str(steam_id), now, now),
    )


async def get_points(discord_id: str, steam_id: str) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA busy_timeout=5000;")
        await database.execute("BEGIN IMMEDIATE;")
        await _ensure_account(database, discord_id, steam_id)
        cursor = await database.execute(
            "SELECT points FROM economy_accounts WHERE discord_id = ?;",
            (str(discord_id),),
        )
        row = await cursor.fetchone()
        await database.commit()
        return int(row[0]) if row else 0


async def adjust_points(
    discord_id: str,
    steam_id: str,
    delta: int,
    actor_discord_id: str,
    reason: str = "admin-adjustment",
) -> int:
    delta = int(delta)
    if delta == 0:
        raise EconomyError("The point adjustment cannot be zero.")
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA busy_timeout=5000;")
        await database.execute("BEGIN IMMEDIATE;")
        await _ensure_account(database, discord_id, steam_id)
        cursor = await database.execute(
            "SELECT points FROM economy_accounts WHERE discord_id = ?;",
            (str(discord_id),),
        )
        current = int((await cursor.fetchone())[0])
        updated = current + delta
        if updated < 0:
            await database.rollback()
            raise EconomyError("That adjustment would make the player's balance negative.")
        now = _utc_now()
        await database.execute(
            "UPDATE economy_accounts SET points = ?, updated_at = ? WHERE discord_id = ?;",
            (updated, now, str(discord_id)),
        )
        await database.execute(
            """
            INSERT INTO economy_ledger
                (discord_id, steam_id, delta, balance_after, reason,
                 reference_id, actor_discord_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                str(discord_id), str(steam_id), delta, updated, reason,
                f"admin:{uuid.uuid4().hex}", str(actor_discord_id), now,
            ),
        )
        await database.commit()
        return updated


async def _linked_players(steam_ids: set[str]) -> dict[str, dict]:
    if not steam_ids or not PLAYER_DATABASE_PATH.is_file():
        return {}
    placeholders = ",".join("?" for _ in steam_ids)
    async with aiosqlite.connect(PLAYER_DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        cursor = await database.execute(
            f"SELECT discord_id, steam_id, evrima_name FROM players WHERE steam_id IN ({placeholders});",
            tuple(sorted(steam_ids)),
        )
        return {str(row["steam_id"]): dict(row) for row in await cursor.fetchall()}


async def resolve_linked_player(
    discord_id: str | None = None,
    steam_id: str | None = None,
) -> dict | None:
    discord_id = str(discord_id or "").strip()
    steam_id = str(steam_id or "").strip()
    if bool(discord_id) == bool(steam_id):
        raise EconomyError("Provide exactly one Discord ID or SteamID.")
    if not PLAYER_DATABASE_PATH.is_file():
        return None
    column = "discord_id" if discord_id else "steam_id"
    value = discord_id or steam_id
    async with aiosqlite.connect(PLAYER_DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        cursor = await database.execute(
            f"SELECT discord_id, steam_id, evrima_name FROM players WHERE {column} = ?;",
            (value,),
        )
        return _row_dict(await cursor.fetchone())


async def break_playtime_sessions() -> int:
    """Prevent the next online poll from bridging an offline/restart interval."""
    now = _utc_now()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA busy_timeout=5000;")
        cursor = await database.execute(
            """
            UPDATE economy_playtime_state
            SET last_seen_epoch = 0,
                updated_at = ?
            WHERE last_seen_epoch != 0;
            """,
            (now,),
        )
        await database.commit()
        return max(0, int(cursor.rowcount or 0))


async def award_online_playtime(
    players: list[dict],
    discord_role_ids: dict[str, set[str]] | None = None,
) -> list[dict]:
    online: dict[str, str] = {}
    for player in players:
        if isinstance(player, str):
            steam_id = player.strip()
            if steam_id.isdigit():
                online[steam_id] = "Unknown"
            continue
        if not isinstance(player, dict):
            continue
        steam_id = str(
            player.get("steamId")
            or player.get("SteamId")
            or player.get("steamID")
            or player.get("steam_id")
            or player.get("playerId")
            or player.get("PlayerID")
            or ""
        ).strip()
        if steam_id.isdigit():
            online[steam_id] = str(
                player.get("name")
                or player.get("Name")
                or player.get("playerName")
                or "Unknown"
            )
    linked = await _linked_players(set(online))

    now_epoch = time.time()
    now = _utc_now()
    awarded: list[dict] = []
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA busy_timeout=5000;")
        await database.execute("BEGIN IMMEDIATE;")
        for steam_id in sorted(online):
            link = linked.get(steam_id)
            cursor = await database.execute(
                """
                SELECT discord_id, accumulated_seconds, total_seconds,
                       last_seen_epoch
                FROM economy_playtime_state
                WHERE steam_id = ?;
                """,
                (steam_id,),
            )
            state = await cursor.fetchone()
            accumulated = 0.0
            total_seconds = 0.0
            previous_discord_id = ""
            if state is not None:
                previous_discord_id = str(state["discord_id"] or "")
                accumulated = float(state["accumulated_seconds"])
                total_seconds = float(state["total_seconds"])
                gap = max(0.0, now_epoch - float(state["last_seen_epoch"]))
                if gap <= ONLINE_GAP_TOLERANCE_SECONDS:
                    total_seconds += gap
                    if link is not None:
                        accumulated += gap
                elif link is None:
                    accumulated = 0.0

            discord_id = (
                str(link["discord_id"])
                if link is not None
                else previous_discord_id
            )
            reward_count = (
                int(accumulated // PLAYTIME_REWARD_SECONDS)
                if link is not None
                else 0
            )
            if link is not None:
                accumulated %= PLAYTIME_REWARD_SECONDS
                await _ensure_account(database, discord_id, steam_id)
            await database.execute(
                """
                INSERT INTO economy_playtime_state
                    (steam_id, discord_id, accumulated_seconds, total_seconds,
                     last_seen_epoch, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(steam_id) DO UPDATE SET
                    discord_id = excluded.discord_id,
                    accumulated_seconds = excluded.accumulated_seconds,
                    total_seconds = excluded.total_seconds,
                    last_seen_epoch = excluded.last_seen_epoch,
                    updated_at = excluded.updated_at;
                """,
                (
                    steam_id,
                    discord_id,
                    accumulated,
                    total_seconds,
                    now_epoch,
                    now,
                ),
            )
            if reward_count <= 0:
                continue
            role_ids = (discord_role_ids or {}).get(discord_id, set())
            delta = reward_count * current_playtime_reward_points(
                discord_role_ids=role_ids,
            )
            cursor = await database.execute(
                "SELECT points FROM economy_accounts WHERE discord_id = ?;",
                (discord_id,),
            )
            balance = int((await cursor.fetchone())[0]) + delta
            await database.execute(
                "UPDATE economy_accounts SET points = ?, updated_at = ? WHERE discord_id = ?;",
                (balance, now, discord_id),
            )
            reference = f"playtime:{steam_id}:{int(now_epoch)}:{uuid.uuid4().hex[:8]}"
            await database.execute(
                """
                INSERT INTO economy_ledger
                    (discord_id, steam_id, delta, balance_after, reason,
                     reference_id, actor_discord_id, created_at)
                VALUES (?, ?, ?, ?, 'playtime', ?, NULL, ?);
                """,
                (discord_id, steam_id, delta, balance, reference, now),
            )
            awarded.append({"discord_id": discord_id, "points": delta, "balance": balance})
        await database.commit()
    return awarded


async def get_total_playtime(steam_id: str) -> int:
    """Return persisted lifetime online playtime, rounded down to seconds."""
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA busy_timeout=5000;")
        cursor = await database.execute(
            """
            SELECT total_seconds
            FROM economy_playtime_state
            WHERE steam_id = ?;
            """,
            (str(steam_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            return 0
        return max(0, int(float(row[0] or 0)))


async def preview_milestone_backfill() -> list[dict]:
    """Return projected historical milestone awards without writing anything."""
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA busy_timeout=5000;")
        cursor = await database.execute(
            """
            SELECT state.discord_id, state.steam_id, state.total_seconds,
                   COALESCE(accounts.points, 0) AS current_balance
            FROM economy_playtime_state AS state
            LEFT JOIN economy_accounts AS accounts
              ON accounts.discord_id = state.discord_id
            WHERE state.discord_id <> ''
            ORDER BY state.total_seconds DESC, state.discord_id ASC;
            """
        )
        rows = await cursor.fetchall()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            "SELECT steam_id, milestone_number FROM economy_playtime_milestones;"
        )
        awarded = {
            (str(row[0]), int(row[1]))
            for row in await cursor.fetchall()
        }
    preview = []
    for row in rows:
        entitlement = milestone_entitlement(row["total_seconds"])
        completed = int(entitlement["completed_milestones"])
        missing = [
            number
            for number in range(1, completed + 1)
            if (str(row["steam_id"]), number) not in awarded
        ]
        points = sum(milestone_reward_points(number) for number in missing)
        balance = int(row["current_balance"] or 0)
        preview.append(
            {
                "discord_id": str(row["discord_id"]),
                "steam_id": str(row["steam_id"]),
                "total_seconds": max(0, int(float(row["total_seconds"] or 0))),
                "completed_milestones": completed,
                "unpaid_milestones": len(missing),
                "points": points,
                "current_balance": balance,
                "projected_balance": balance + points,
            }
        )
    return preview


async def award_due_milestones(
    steam_ids: set[str] | None = None,
    *,
    source: str,
) -> list[dict]:
    """Atomically award every unpaid reached milestone, grouped by player."""
    if source not in {"live", "backfill"}:
        raise EconomyError("Invalid milestone award source.")
    normalized = {
        str(value).strip()
        for value in (steam_ids or set())
        if str(value).strip().isdigit()
    }
    where = "WHERE state.discord_id <> ''"
    parameters: tuple = ()
    if steam_ids is not None:
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        where += f" AND state.steam_id IN ({placeholders})"
        parameters = tuple(sorted(normalized))

    now = _utc_now()
    results: list[dict] = []
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA busy_timeout=5000;")
        await database.execute("BEGIN IMMEDIATE;")
        cursor = await database.execute(
            f"""
            SELECT state.discord_id, state.steam_id, state.total_seconds
            FROM economy_playtime_state AS state
            {where}
            ORDER BY state.total_seconds ASC, state.steam_id ASC;
            """,
            parameters,
        )
        states = await cursor.fetchall()
        for state in states:
            discord_id = str(state["discord_id"] or "").strip()
            steam_id = str(state["steam_id"] or "").strip()
            if not discord_id.isdigit() or not steam_id.isdigit():
                continue
            entitlement = milestone_entitlement(state["total_seconds"])
            completed = int(entitlement["completed_milestones"])
            if completed <= 0:
                continue
            cursor = await database.execute(
                """
                SELECT milestone_number
                FROM economy_playtime_milestones
                WHERE steam_id = ?;
                """,
                (steam_id,),
            )
            already_awarded = {int(row[0]) for row in await cursor.fetchall()}
            due = [
                number
                for number in range(1, completed + 1)
                if number not in already_awarded
            ]
            if not due:
                continue
            await _ensure_account(database, discord_id, steam_id)
            cursor = await database.execute(
                "SELECT points FROM economy_accounts WHERE discord_id = ?;",
                (discord_id,),
            )
            old_balance = int((await cursor.fetchone())[0])
            balance = old_balance
            for milestone_number in due:
                points = milestone_reward_points(milestone_number)
                balance += points
                threshold_hours = milestone_threshold_hours(milestone_number)
                await database.execute(
                    """
                    INSERT INTO economy_playtime_milestones
                        (steam_id, discord_id, milestone_number, threshold_hours,
                         points, balance_after, source, awarded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        steam_id,
                        discord_id,
                        milestone_number,
                        threshold_hours,
                        points,
                        balance,
                        source,
                        now,
                    ),
                )
                await database.execute(
                    """
                    INSERT INTO economy_ledger
                        (discord_id, steam_id, delta, balance_after, reason,
                         reference_id, actor_discord_id, created_at)
                    VALUES (?, ?, ?, ?, 'playtime-milestone', ?, NULL, ?);
                    """,
                    (
                        discord_id,
                        steam_id,
                        points,
                        balance,
                        f"milestone:{steam_id}:{milestone_number}",
                        now,
                    ),
                )
            await database.execute(
                "UPDATE economy_accounts SET points = ?, updated_at = ? WHERE discord_id = ?;",
                (balance, now, discord_id),
            )
            results.append(
                {
                    "discord_id": discord_id,
                    "steam_id": steam_id,
                    "milestones": due,
                    "highest_milestone": max(due),
                    "reached_hours": milestone_threshold_hours(max(due)),
                    "points": balance - old_balance,
                    "old_balance": old_balance,
                    "new_balance": balance,
                }
            )
        await database.commit()
    return results


async def refresh_mutation_catalog() -> None:
    now = _utc_now()
    discovered: set[str] = set()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            "SELECT serialized_player_data FROM stored_dinosaurs;"
        )
        for row in await cursor.fetchall():
            try:
                snapshot = json.loads(row[0])
            except (TypeError, json.JSONDecodeError):
                continue
            mutations = snapshot.get("mutations") if isinstance(snapshot, dict) else None
            if not isinstance(mutations, dict):
                continue
            for field in ("MutationSlot1", "MutationSlot2", "MutationSlot3", "MutationSlot4"):
                value = mutations.get(field)
                if isinstance(value, str) and value and value != "None" and len(value) <= 100:
                    discovered.add(value)
        if discovered:
            await database.executemany(
                """
                INSERT INTO economy_mutation_catalog(name, last_seen_at)
                VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET last_seen_at = excluded.last_seen_at;
                """,
                [(name, now) for name in sorted(discovered)],
            )
        await database.commit()


async def list_mutation_catalog(limit: int = 100, species: str | None = None) -> list[str]:
    values = mutations_for_species(species) if species else DEFAULT_MUTATIONS
    return list(values[:max(1, min(100, int(limit)))])


async def create_shop_listing(
    species: str,
    mutations: list[str] | None,
    price: int,
    created_by: str,
) -> int:
    if species not in PLAYABLE_SPECIES:
        raise EconomyError("That dinosaur class is not in the playable-species list.")
    price = int(price)
    if price < MINIMUM_PRICE:
        raise EconomyError(f"Shop prices must be at least {MINIMUM_PRICE} points.")
    selected = ["", "", "", ""]
    now = _utc_now()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            """
            INSERT INTO economy_shop_listings
                (species, mutation1, mutation2, mutation3, mutation4,
                 price, active, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?);
            """,
            (species, *selected, price, str(created_by), now, now),
        )
        await database.commit()
        return int(cursor.lastrowid)


async def list_shop_listings(active_only: bool = True) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        query = "SELECT * FROM economy_shop_listings"
        params: tuple = ()
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY species COLLATE NOCASE, price, id;"
        cursor = await database.execute(query, params)
        return [dict(row) for row in await cursor.fetchall()]


async def deactivate_shop_listing(listing_id: int) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            "UPDATE economy_shop_listings SET active = 0, updated_at = ? WHERE id = ? AND active = 1;",
            (_utc_now(), int(listing_id)),
        )
        await database.commit()
        return cursor.rowcount == 1


async def update_shop_listing(listing_id: int, species: str, price: int, active: bool) -> bool:
    if species not in PLAYABLE_SPECIES: raise EconomyError("That dinosaur class is not playable.")
    if int(price) < MINIMUM_PRICE: raise EconomyError(f"Shop prices must be at least {MINIMUM_PRICE} points.")
    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor=await database.execute("UPDATE economy_shop_listings SET species=?, price=?, active=?, mutation1=NULL, mutation2=NULL, mutation3=NULL, mutation4=NULL, updated_at=? WHERE id=?;",(species,int(price),1 if active else 0,_utc_now(),int(listing_id)))
        await database.commit(); return cursor.rowcount == 1


def _shop_snapshot(species: str, mutations: list[str] | None, steam_id: str) -> dict:
    mutation_map = {field: "" for field in MUTATION_FIELDS}
    selected: list[str] = []
    conditions = {f"cond{index}": True for index in range(1, 11)}
    tasks = [
        {"number": index, "name": name, "complete": True}
        for index, name in enumerate(PRIME_TASK_NAMES, start=1)
    ]
    return {
        "version": 1,
        "captureMode": "shop-generated",
        "capturedAt": int(time.time()),
        "steam": str(steam_id),
        "species": species,
        "classPath": (
            f"/Game/TheIsle/Core/Characters/Dinosaurs/{species}/"
            f"BP_{species}.BP_{species}_C"
        ),
        "growth": 0.75,
        "health": 1.0,
        "stamina": 1.0,
        "hunger": 1.0,
        "thirst": 1.0,
        "oxygen": 1000.0,
        "blood": 1.0,
        "lockedDamage": 0.0,
        "food": 1.0,
        "waterLevel": 1.0,
        "rottenValue": 1800.0,
        "maxHealth": 1.0,
        "maxBlood": 1.0,
        "maxHunger": 1.0,
        "maxFoodValue": 1.0,
        "maxThirst": 1.0,
        "maxStamina": 1.0,
        "isFemale": False,
        "isPrime": True,
        "generatedShop": True,
        "shopToken": True,
        "shopTokenMutationsSelected": False,
        "percentageBackedSnapshot": True,
        "requestedHungerPercent": 100.0,
        "requestedThirstPercent": 100.0,
        "requestedCarbPercent": 100.0,
        "requestedProteinPercent": 100.0,
        "requestedLipidPercent": 100.0,
        "currentLocationOnly": True,
        "preserveCurrentSkin": True,
        "forceMale": True,
        "forceFemale": False,
        "generatedHungerFraction": 1.0,
        "generatedThirstFraction": 1.0,
        "generatedNutrientPercents": {
            "carb": 100.0,
            "protein": 100.0,
            "lipid": 100.0,
        },
        "elderStacks": 0,
        "unlockRequiredMutations": selected,
        "nutrients": {"carbValue": 1.0, "proteinValue": 1.0, "lipidValue": 1.0},
        "skin": {"skinCaptured": False},
        "primeData": {
            "eligible": True,
            "completedCount": 10,
            "completedTaskNumbers": list(range(1, 11)),
            "conditions": conditions,
            "tasks": tasks,
        },
        "mutations": mutation_map,
        "location": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
    }


def _percentage(value: float | int, name: str) -> float:
    try:
        percentage = float(value)
    except (TypeError, ValueError) as error:
        raise EconomyError(f"{name} must be a number from 0 to 100.") from error
    if not math.isfinite(percentage) or not 0 <= percentage <= 100:
        raise EconomyError(f"{name} must be between 0 and 100.")
    return percentage


async def grant_admin_dinosaur(
    target_discord_id: str,
    target_steam_id: str,
    species: str,
    growth_percent: float,
    is_prime: bool,
    is_female: bool,
    elder_stacks: int,
    mutation_count: int,
    hunger_percent: float,
    thirst_percent: float,
    carb_percent: float,
    protein_percent: float,
    lipid_percent: float,
    mutations: dict[str, str],
    granted_by: str,
) -> dict:
    if species not in PLAYABLE_SPECIES:
        raise EconomyError("That dinosaur class is not in the playable-species list.")
    growth = _percentage(growth_percent, "Growth") / 100.0
    hunger = _percentage(hunger_percent, "Hunger") / 100.0
    thirst = _percentage(thirst_percent, "Thirst") / 100.0
    carb = _percentage(carb_percent, "Carbohydrate")
    protein = _percentage(protein_percent, "Protein")
    lipid = _percentage(lipid_percent, "Lipid")
    elder_stacks = int(elder_stacks)
    if not 0 <= elder_stacks <= 3:
        raise EconomyError("Entombments must be between 0 and 3.")
    mutation_count = int(mutation_count)
    if not 0 <= mutation_count <= len(ADMIN_MUTATION_SLOTS):
        raise EconomyError("Mutation count must be between 0 and 16.")
    if set(mutations) != MUTATION_FIELDS:
        raise EconomyError("The administrator mutation map is incomplete.")
    cleaned_mutations = {
        field: str(mutations[field] or "").strip()
        for field in MUTATION_FIELDS
    }
    allowed_mutation_count = mutation_count
    unavailable_fields = {
        field for _label, field in ADMIN_MUTATION_SLOTS[allowed_mutation_count:]
    }
    if any(cleaned_mutations[field] for field in unavailable_fields):
        raise EconomyError(
            f"Only the first {allowed_mutation_count} selected mutation slots are available."
        )
    catalog = set(await list_mutation_catalog(species=species))
    if any(value and value not in catalog for value in cleaned_mutations.values()):
        raise EconomyError("One or more selected mutations are not in the verified catalog.")

    selected_mutations = list(dict.fromkeys(
        value for value in cleaned_mutations.values() if value
    ))
    conditions = {f"cond{index}": bool(is_prime) for index in range(1, 11)}
    tasks = [
        {"number": index, "name": name, "complete": bool(is_prime)}
        for index, name in enumerate(PRIME_TASK_NAMES, start=1)
    ]
    requested_snapshot = {
        "version": 1,
        "captureMode": "admin-grant-request",
        "capturedAt": int(time.time()),
        "steam": str(target_steam_id),
        "species": species,
        "classPath": (
            f"/Game/TheIsle/Core/Characters/Dinosaurs/{species}/"
            f"BP_{species}.BP_{species}_C"
        ),
        "growth": growth,
        "health": 1.0,
        "stamina": 1.0,
        "hunger": hunger,
        "thirst": thirst,
        "oxygen": 1000.0,
        "blood": 1.0,
        "lockedDamage": 0.0,
        "food": hunger,
        "waterLevel": thirst,
        "rottenValue": 1800.0,
        "maxHealth": 1.0,
        "maxBlood": 1.0,
        "maxHunger": 1.0,
        "maxFoodValue": 1.0,
        "maxThirst": 1.0,
        "maxStamina": 1.0,
        "isFemale": bool(is_female),
        "isPrime": bool(is_prime),
        "generatedShop": False,
        "currentLocationOnly": False,
        "preserveCurrentSkin": False,
        "forceMale": False,
        "forceFemale": False,
        "requestedHungerFraction": hunger,
        "requestedThirstFraction": thirst,
        "requestedNutrientPercents": {
            "carb": carb,
            "protein": protein,
            "lipid": lipid,
        },
        "elderStacks": elder_stacks,
        "unlockRequiredMutations": selected_mutations,
        "nutrients": {
            "carbValue": 0.0,
            "proteinValue": 0.0,
            "lipidValue": 0.0,
        },
        "skin": {"skinCaptured": False},
        "primeData": {
            "eligible": bool(is_prime),
            "completedCount": 10 if is_prime else 0,
            "completedTaskNumbers": list(range(1, 11)) if is_prime else [],
            "conditions": conditions,
            "tasks": tasks,
        },
        "mutations": cleaned_mutations,
        "location": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
    }
    try:
        snapshot, serialized = await build_admin_grant_snapshot(
            str(target_steam_id),
            requested_snapshot,
        )
    except DinoStorageError as error:
        raise EconomyError(str(error)) from error

    # Admin replacements are persisted as ordinary parked dinos. `granted_by`
    # remains audit metadata only; neither inventory nor unparking branches on
    # it, and the payload uses the same normal park-pending database schema.
    materialized_growth = float(snapshot["growth"])
    materialized_is_female = snapshot.get("isFemale") is True
    now = _utc_now()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA busy_timeout=5000;")
        cursor = await database.execute(
            """
            INSERT INTO stored_dinosaurs
                (discord_id, steam_id, species, growth, sex,
                 serialized_player_data, payload_sha256, source, granted_by,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'player_park', ?, 'parked', ?, ?);
            """,
            (
                str(target_discord_id), str(target_steam_id), species,
                materialized_growth,
                "female" if materialized_is_female else "male", serialized,
                payload_digest(serialized), str(granted_by), now, now,
            ),
        )
        await database.commit()
        dinosaur_id = int(cursor.lastrowid)
    return {
        "dinosaur_id": dinosaur_id,
        "species": species,
        "discord_id": str(target_discord_id),
        "steam_id": str(target_steam_id),
        "growth": materialized_growth,
        "sex": "female" if materialized_is_female else "male",
        "serialized_player_data": serialized,
    }


async def purchase_shop_listing(
    listing_id: int,
    buyer_discord_id: str,
    buyer_steam_id: str,
) -> dict:
    now = _utc_now()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA busy_timeout=5000;")
        await database.execute("BEGIN IMMEDIATE;")
        cursor = await database.execute(
            "SELECT * FROM economy_shop_listings WHERE id = ? AND active = 1;",
            (int(listing_id),),
        )
        listing = await cursor.fetchone()
        if listing is None:
            await database.rollback()
            raise EconomyError("That shop listing is no longer available.")
        await _ensure_account(database, buyer_discord_id, buyer_steam_id)
        cursor = await database.execute(
            "SELECT points FROM economy_accounts WHERE discord_id = ?;",
            (str(buyer_discord_id),),
        )
        balance = int((await cursor.fetchone())[0])
        price = int(listing["price"])
        if balance < price:
            await database.rollback()
            raise EconomyError(f"You need {price - balance} more points for this dino.")
        snapshot = _shop_snapshot(str(listing["species"]), [], buyer_steam_id)
        serialized = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
        cursor = await database.execute(
            """
            INSERT INTO stored_dinosaurs
                (discord_id, steam_id, species, growth, sex,
                 serialized_player_data, payload_sha256, source, granted_by,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, 0.75, 'male', ?, ?, 'shop_token', ?, 'parked', ?, ?);
            """,
            (
                str(buyer_discord_id), str(buyer_steam_id), str(listing["species"]),
                serialized, payload_digest(serialized), f"shop:{int(listing['id'])}", now, now,
            ),
        )
        dinosaur_id = int(cursor.lastrowid)
        updated_balance = balance - price
        await database.execute(
            "UPDATE economy_accounts SET points = ?, updated_at = ? WHERE discord_id = ?;",
            (updated_balance, now, str(buyer_discord_id)),
        )
        await database.execute(
            """
            INSERT INTO economy_ledger
                (discord_id, steam_id, delta, balance_after, reason,
                 reference_id, actor_discord_id, created_at)
            VALUES (?, ?, ?, ?, 'shop-purchase', ?, NULL, ?);
            """,
            (
                str(buyer_discord_id), str(buyer_steam_id), -price, updated_balance,
                f"shop-purchase:{uuid.uuid4().hex}", now,
            ),
        )
        await database.commit()
        return {
            "dinosaur_id": dinosaur_id,
            "species": str(listing["species"]),
            "price": price,
            "balance": updated_balance,
        }


async def set_shop_token_mutations(
    discord_id: str,
    dinosaur_id: int,
    mutations: list[str],
) -> dict:
    selected = [str(value or "").strip() for value in mutations]
    if len(selected) != 4 or any(not value for value in selected):
        raise EconomyError("Shop Tokens require exactly four mutations.")
    if len({value.casefold() for value in selected}) != 4:
        raise EconomyError("Each Shop Token mutation must be unique.")
    now = _utc_now()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("BEGIN IMMEDIATE;")
        cursor = await database.execute(
            "SELECT * FROM stored_dinosaurs WHERE id = ? AND discord_id = ? AND status = 'parked' AND source = 'shop_token';",
            (int(dinosaur_id), str(discord_id)),
        )
        row = await cursor.fetchone()
        if row is None:
            await database.rollback()
            raise EconomyError("That Shop Token is no longer available.")
        species = str(row["species"])
        for slot, value in enumerate(selected, start=1):
            if value not in set(shop_token_mutations_for_slot(species, slot)):
                await database.rollback()
                raise EconomyError(
                    f"{value} is not available for {species} in Shop Token Slot{slot}."
                )
        try:
            snapshot = json.loads(str(row["serialized_player_data"] or "{}"))
        except json.JSONDecodeError as error:
            await database.rollback()
            raise EconomyError("That Shop Token snapshot is invalid.") from error
        mutation_map = snapshot.get("mutations")
        if not isinstance(mutation_map, dict):
            mutation_map = {field: "" for field in MUTATION_FIELDS}
        for field in MUTATION_FIELDS:
            mutation_map[field] = ""
        for index, value in enumerate(selected, start=1):
            mutation_map[f"MutationSlot{index}"] = value
        snapshot["mutations"] = mutation_map
        snapshot["unlockRequiredMutations"] = selected
        snapshot["shopToken"] = True
        snapshot["shopTokenMutationsSelected"] = True
        serialized = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
        await database.execute(
            "UPDATE stored_dinosaurs SET serialized_player_data = ?, payload_sha256 = ?, updated_at = ? WHERE id = ?;",
            (serialized, payload_digest(serialized), now, int(dinosaur_id)),
        )
        await database.commit()
        result = dict(row)
        result["serialized_player_data"] = serialized
        result["payload_sha256"] = payload_digest(serialized)
        result["source"] = "shop_token"
        return result


async def create_market_sale(
    seller_discord_id: str,
    seller_steam_id: str,
    dinosaur_id: int,
    price: int,
    duration_hours: int,
) -> dict:
    price = int(price)
    duration_hours = int(duration_hours)
    if price < MINIMUM_PRICE:
        raise EconomyError(f"Sale prices must be at least {MINIMUM_PRICE} points.")
    if not MINIMUM_SALE_HOURS <= duration_hours <= MAXIMUM_SALE_HOURS:
        raise EconomyError("Sale duration must be between 1 and 24 hours.")
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds")
    expires = (now_dt + timedelta(hours=duration_hours)).isoformat(timespec="seconds")
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA busy_timeout=5000;")
        await database.execute("BEGIN IMMEDIATE;")
        await _ensure_account(database, seller_discord_id, seller_steam_id)
        cursor = await database.execute(
            """
            SELECT * FROM stored_dinosaurs
            WHERE id = ? AND discord_id = ? AND steam_id = ? AND status = 'parked';
            """,
            (int(dinosaur_id), str(seller_discord_id), str(seller_steam_id)),
        )
        dinosaur = await cursor.fetchone()
        if dinosaur is None:
            await database.rollback()
            raise EconomyError("That parked dinosaur is no longer available to sell.")
        cursor = await database.execute(
            "UPDATE stored_dinosaurs SET status = 'listed', updated_at = ? WHERE id = ? AND status = 'parked';",
            (now, int(dinosaur_id)),
        )
        if cursor.rowcount != 1:
            await database.rollback()
            raise EconomyError("That dinosaur was already changed by another operation.")
        cursor = await database.execute(
            """
            INSERT INTO economy_market_sales
                (dinosaur_id, seller_discord_id, seller_steam_id, price,
                 status, created_at, expires_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?);
            """,
            (
                int(dinosaur_id), str(seller_discord_id), str(seller_steam_id),
                price, now, expires,
            ),
        )
        sale_id = int(cursor.lastrowid)
        await database.commit()
        result = dict(dinosaur)
        result.update({
            "sale_id": sale_id,
            "price": price,
            "duration_hours": duration_hours,
            "expires_at": expires,
            "seller_discord_id": str(seller_discord_id),
        })
        return result


async def attach_market_message(sale_id: int, channel_id: int, message_id: int) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute(
            """
            UPDATE economy_market_sales SET channel_id = ?, message_id = ?
            WHERE id = ? AND status = 'active';
            """,
            (str(channel_id), str(message_id), int(sale_id)),
        )
        await database.commit()


async def _sales_query(where: str, params: tuple) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        cursor = await database.execute(
            f"""
            SELECT s.id AS sale_id, s.*, d.discord_id, d.steam_id, d.species,
                   d.growth, d.sex, d.nickname, d.serialized_player_data,
                   d.payload_sha256, d.source, d.granted_by, d.status AS dinosaur_status,
                   d.created_at AS dinosaur_created_at, d.updated_at AS dinosaur_updated_at
            FROM economy_market_sales s
            JOIN stored_dinosaurs d ON d.id = s.dinosaur_id
            WHERE {where}
            ORDER BY s.id DESC;
            """,
            params,
        )
        return [dict(row) for row in await cursor.fetchall()]


async def list_seller_sales(seller_discord_id: str) -> list[dict]:
    return await _sales_query(
        "s.seller_discord_id = ? AND s.status = 'active'",
        (str(seller_discord_id),),
    )


async def list_active_market_sales() -> list[dict]:
    return await _sales_query("s.status = 'active'", ())


async def cancel_market_sale(sale_id: int, seller_discord_id: str | None = None) -> dict:
    now = _utc_now()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA busy_timeout=5000;")
        await database.execute("BEGIN IMMEDIATE;")
        query = "SELECT * FROM economy_market_sales WHERE id = ? AND status = 'active'"
        params: list[object] = [int(sale_id)]
        if seller_discord_id is not None:
            query += " AND seller_discord_id = ?"
            params.append(str(seller_discord_id))
        cursor = await database.execute(query + ";", tuple(params))
        sale = await cursor.fetchone()
        if sale is None:
            await database.rollback()
            raise EconomyError("That sale is no longer active.")
        await database.execute(
            "UPDATE economy_market_sales SET status = 'cancelled', completed_at = ? WHERE id = ?;",
            (now, int(sale_id)),
        )
        await database.execute(
            "UPDATE stored_dinosaurs SET status = 'parked', updated_at = ? WHERE id = ? AND status = 'listed';",
            (now, int(sale["dinosaur_id"])),
        )
        await database.commit()
        return dict(sale)


async def expire_market_sales() -> list[dict]:
    now = _utc_now()
    expired = await _sales_query(
        "s.status = 'active' AND s.expires_at <= ?",
        (now,),
    )
    for sale in expired:
        try:
            await cancel_market_sale(int(sale["sale_id"]), None)
        except EconomyError:
            continue
    return expired


async def purchase_market_sale(
    sale_id: int,
    buyer_discord_id: str,
    buyer_steam_id: str,
) -> dict:
    now = _utc_now()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA busy_timeout=5000;")
        await database.execute("BEGIN IMMEDIATE;")
        cursor = await database.execute(
            """
            SELECT s.*, d.serialized_player_data
            FROM economy_market_sales s
            JOIN stored_dinosaurs d ON d.id = s.dinosaur_id
            WHERE s.id = ? AND s.status = 'active' AND d.status = 'listed';
            """,
            (int(sale_id),),
        )
        sale = await cursor.fetchone()
        if sale is None:
            await database.rollback()
            raise EconomyError("That sale is no longer available.")
        if str(sale["seller_discord_id"]) == str(buyer_discord_id):
            await database.rollback()
            raise EconomyError("You cannot buy your own sale.")
        if str(sale["expires_at"]) <= now:
            await database.rollback()
            raise EconomyError("That sale has expired.")
        await _ensure_account(database, buyer_discord_id, buyer_steam_id)
        await _ensure_account(database, str(sale["seller_discord_id"]), str(sale["seller_steam_id"]))
        cursor = await database.execute(
            "SELECT points FROM economy_accounts WHERE discord_id = ?;",
            (str(buyer_discord_id),),
        )
        buyer_balance = int((await cursor.fetchone())[0])
        price = int(sale["price"])
        if buyer_balance < price:
            await database.rollback()
            raise EconomyError(f"You need {price - buyer_balance} more points for this dino.")
        cursor = await database.execute(
            "SELECT points FROM economy_accounts WHERE discord_id = ?;",
            (str(sale["seller_discord_id"]),),
        )
        seller_balance = int((await cursor.fetchone())[0])
        new_buyer_balance = buyer_balance - price
        new_seller_balance = seller_balance + price
        await database.execute(
            "UPDATE economy_accounts SET points = ?, updated_at = ? WHERE discord_id = ?;",
            (new_buyer_balance, now, str(buyer_discord_id)),
        )
        await database.execute(
            "UPDATE economy_accounts SET points = ?, updated_at = ? WHERE discord_id = ?;",
            (new_seller_balance, now, str(sale["seller_discord_id"])),
        )
        try:
            snapshot = json.loads(str(sale["serialized_player_data"]))
            snapshot["steam"] = str(buyer_steam_id)
            serialized = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, json.JSONDecodeError) as error:
            await database.rollback()
            raise EconomyError("The listed dinosaur snapshot is invalid.") from error
        await database.execute(
            """
            UPDATE stored_dinosaurs
            SET discord_id = ?, steam_id = ?, serialized_player_data = ?,
                payload_sha256 = ?, status = 'parked', updated_at = ?
            WHERE id = ? AND status = 'listed';
            """,
            (
                str(buyer_discord_id), str(buyer_steam_id), serialized,
                payload_digest(serialized), now, int(sale["dinosaur_id"]),
            ),
        )
        await database.execute(
            """
            UPDATE economy_market_sales
            SET buyer_discord_id = ?, status = 'sold', completed_at = ?
            WHERE id = ? AND status = 'active';
            """,
            (str(buyer_discord_id), now, int(sale_id)),
        )
        reference = f"market:{int(sale_id)}:{uuid.uuid4().hex[:12]}"
        await database.execute(
            """
            INSERT INTO economy_ledger
                (discord_id, steam_id, delta, balance_after, reason,
                 reference_id, actor_discord_id, created_at)
            VALUES (?, ?, ?, ?, 'market-purchase', ?, NULL, ?);
            """,
            (
                str(buyer_discord_id), str(buyer_steam_id), -price,
                new_buyer_balance, reference + ":buyer", now,
            ),
        )
        await database.execute(
            """
            INSERT INTO economy_ledger
                (discord_id, steam_id, delta, balance_after, reason,
                 reference_id, actor_discord_id, created_at)
            VALUES (?, ?, ?, ?, 'market-sale', ?, NULL, ?);
            """,
            (
                str(sale["seller_discord_id"]), str(sale["seller_steam_id"]), price,
                new_seller_balance, reference + ":seller", now,
            ),
        )
        await database.commit()
        return {
            "sale_id": int(sale_id),
            "dinosaur_id": int(sale["dinosaur_id"]),
            "price": price,
            "buyer_balance": new_buyer_balance,
            "seller_discord_id": str(sale["seller_discord_id"]),
        }
