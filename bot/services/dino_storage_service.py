import asyncio
import copy
import hashlib
import json
import logging
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


BOT_ROOT = Path(__file__).resolve().parents[2]
_configured_database_path = Path(
    os.getenv("DINO_STORAGE_DATABASE", "dino_storage.db")
).expanduser()
DATABASE_PATH = (
    _configured_database_path
    if _configured_database_path.is_absolute()
    else BOT_ROOT / _configured_database_path
)
INTEGRITY_LOG_PATH = BOT_ROOT / "dino_storage_integrity.log"
BUILD_VERSION = "0.12.20"
LOGGER = logging.getLogger(__name__)


def _default_mod_root() -> Path:
    configured = os.getenv("DINO_STORAGE_MOD_ROOT")
    if configured:
        return Path(configured)

    candidates = (
        Path(r"C:\TheMesozoic\TheIsle\Binaries\Win64\Mods\DinoStorage"),
        Path(r"D:\TheMesozoic\TheIsle\Binaries\Win64\Mods\DinoStorage"),
    )
    return next((candidate for candidate in candidates if candidate.is_dir()), candidates[0])


MOD_ROOT = _default_mod_root()
COMMAND_PATH = MOD_ROOT / "dino_storage_commands.ndjson"
RESULT_PATH = MOD_ROOT / "dino_storage_results.ndjson"
SAVED_ROOT = MOD_ROOT / "Saved"
BRIDGE_TIMEOUT_SECONDS = float(os.getenv("DINO_STORAGE_BRIDGE_TIMEOUT", "25"))

_append_lock = asyncio.Lock()
_park_lock = asyncio.Lock()
_unpark_lock = asyncio.Lock()
_self_slay_lock = asyncio.Lock()
_normalization_lock = asyncio.Lock()

MUTATION_FIELDS = {
    "MutationSlot1", "MutationSlot2", "MutationSlot3", "MutationSlot4",
    "ParentMutationSlot1", "ParentMutationSlot2", "ParentMutationSlot3",
    "ParentMutationSlot4", "ElderMutationSlot1A", "ElderMutationSlot1B",
    "ElderMutationSlot2A", "ElderMutationSlot2B", "ElderMutationSlot3A",
    "ElderMutationSlot3B", "ElderMutationSlot4A", "ElderMutationSlot4B",
}
SKIN_COLOR_FIELDS = {
    "BodyColor", "MarkingsColor", "FlankColor", "UnderbellyColor",
    "Detail1Color", "EyesColor", "MaleDisplayColor", "TeethColor",
    "MouthColor", "ClawsColor",
}


class DinoStorageError(RuntimeError):
    pass


class DinoStorageUnavailable(DinoStorageError):
    pass


class DinoStorageTimeout(DinoStorageError):
    pass


def _snapshot_integrity_valid(
    serialized: str,
    expected_digest: str,
    *,
    stage: str,
    dinosaur_id: object = None,
) -> bool:
    actual_digest = payload_digest(serialized) if serialized else ""
    if serialized and actual_digest == expected_digest:
        return True

    diagnostic = {
        "timestamp": _utc_now(),
        "build": BUILD_VERSION,
        "stage": str(stage),
        "dinosaurId": dinosaur_id,
        "database": str(DATABASE_PATH.resolve()),
        "serializedCharacters": len(serialized),
        "expectedSha256": str(expected_digest),
        "actualSha256": actual_digest,
    }
    line = json.dumps(diagnostic, separators=(",", ":"), ensure_ascii=False)
    try:
        INTEGRITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with INTEGRITY_LOG_PATH.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")
    except OSError:
        LOGGER.exception("Could not write DinoStorage integrity diagnostics")
    LOGGER.error("DinoStorage snapshot integrity failure: %s", line)
    return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def init_dino_storage_database() -> None:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("PRAGMA journal_mode=WAL;")
        await database.execute("PRAGMA busy_timeout=5000;")
        await database.executescript(
            """
            CREATE TABLE IF NOT EXISTS stored_dinosaurs
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_id TEXT NOT NULL,
                steam_id TEXT NOT NULL,
                species TEXT NOT NULL,
                growth REAL NOT NULL DEFAULT 0,
                sex TEXT,
                nickname TEXT,
                serialized_player_data TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'player_park',
                granted_by TEXT,
                status TEXT NOT NULL DEFAULT 'parked',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS ix_stored_dinosaurs_owner_status
            ON stored_dinosaurs(discord_id, status, id DESC);

            CREATE TABLE IF NOT EXISTS dino_storage_operations
            (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                discord_id TEXT NOT NULL,
                steam_id TEXT NOT NULL,
                stored_dinosaur_id INTEGER,
                status TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS ix_dino_storage_operations_owner
            ON dino_storage_operations(discord_id, created_at DESC);
            """
        )
        # Restore attempts are transform-in-place and do not create a second
        # dinosaur. Older builds marked any partial/unknown result as requiring
        # administrator recovery; make those inventory rows retryable again.
        await database.execute(
            """
            UPDATE stored_dinosaurs
            SET status = 'parked', updated_at = ?
            WHERE status = 'restore_uncertain';
            """,
            (_utc_now(),),
        )
        await database.commit()


async def list_inventory(discord_id: str) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        cursor = await database.execute(
            """
            SELECT id, discord_id, steam_id, species, growth, sex, nickname,
                   source, granted_by, status, serialized_player_data, payload_sha256,
                   created_at, updated_at
            FROM stored_dinosaurs
            WHERE discord_id = ? AND status = 'parked'
            ORDER BY id DESC;
            """,
            (str(discord_id),),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def delete_inventory_item(discord_id: str, dinosaur_id: int) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            """
            DELETE FROM stored_dinosaurs
            WHERE id = ? AND discord_id = ? AND status = 'parked';
            """,
            (int(dinosaur_id), str(discord_id)),
        )
        await database.commit()
        return cursor.rowcount == 1


async def get_inventory_item(discord_id: str, dinosaur_id: int) -> dict | None:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        cursor = await database.execute(
            """
            SELECT id, discord_id, steam_id, species, growth, sex, nickname,
                   serialized_player_data, payload_sha256, source, granted_by, status,
                   created_at, updated_at
            FROM stored_dinosaurs
            WHERE id = ? AND discord_id = ? AND status = 'parked';
            """,
            (int(dinosaur_id), str(discord_id)),
        )
        row = await cursor.fetchone()
        return dict(row) if row is not None else None


async def set_inventory_nickname(
    discord_id: str,
    dinosaur_id: int,
    nickname: str,
) -> dict:
    cleaned = " ".join(str(nickname or "").strip().split())
    if len(cleaned) > 40:
        raise DinoStorageError("Token names can contain at most 40 characters.")
    now = _utc_now()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA busy_timeout=5000;")
        await database.execute("BEGIN IMMEDIATE;")
        cursor = await database.execute(
            """
            SELECT id, discord_id, steam_id, species, growth, sex, nickname,
                   serialized_player_data, payload_sha256, source, granted_by,
                   status, created_at, updated_at
            FROM stored_dinosaurs
            WHERE id = ? AND discord_id = ? AND status = 'parked';
            """,
            (int(dinosaur_id), str(discord_id)),
        )
        row = await cursor.fetchone()
        if row is None:
            await database.rollback()
            raise DinoStorageError("That parked dinosaur is no longer available.")
        dinosaur = dict(row)
        try:
            snapshot = json.loads(str(dinosaur.get("serialized_player_data") or "{}"))
        except json.JSONDecodeError as error:
            await database.rollback()
            raise DinoStorageError("That parked token snapshot is invalid.") from error
        cursor = await database.execute(
            """
            UPDATE stored_dinosaurs
            SET nickname = ?, updated_at = ?
            WHERE id = ? AND discord_id = ? AND status = 'parked';
            """,
            (cleaned or None, now, int(dinosaur_id), str(discord_id)),
        )
        if cursor.rowcount != 1:
            await database.rollback()
            raise DinoStorageError("The parked token changed before it could be named.")
        await database.commit()
    dinosaur["nickname"] = cleaned or None
    dinosaur["updated_at"] = now
    return dinosaur


async def set_inventory_diets_percent(
    discord_id: str,
    dinosaur_id: int,
    carb_percent: float = 100.0,
    protein_percent: float = 100.0,
    lipid_percent: float = 100.0,
) -> dict:
    percentages = {
        "carb": float(carb_percent),
        "protein": float(protein_percent),
        "lipid": float(lipid_percent),
    }
    if any(
        not math.isfinite(value) or value < 0.0 or value > 100.0
        for value in percentages.values()
    ):
        raise DinoStorageError("Diet percentages must be between 0 and 100.")

    now = _utc_now()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("PRAGMA busy_timeout=5000;")
        await database.execute("BEGIN IMMEDIATE;")
        cursor = await database.execute(
            """
            SELECT id, discord_id, steam_id, species, growth, sex, nickname,
                   serialized_player_data, payload_sha256, source, granted_by,
                   status, created_at, updated_at
            FROM stored_dinosaurs
            WHERE id = ? AND discord_id = ? AND status = 'parked';
            """,
            (int(dinosaur_id), str(discord_id)),
        )
        row = await cursor.fetchone()
        if row is None:
            await database.rollback()
            raise DinoStorageError("That parked dinosaur is no longer available.")

        dinosaur = dict(row)
        serialized = str(dinosaur.get("serialized_player_data") or "")
        if not _snapshot_integrity_valid(
            serialized,
            str(dinosaur.get("payload_sha256") or ""),
            stage="admin-inventory-diet-update",
            dinosaur_id=dinosaur.get("id"),
        ):
            await database.rollback()
            raise DinoStorageError("The parked dinosaur snapshot failed its integrity check.")
        try:
            snapshot = json.loads(serialized)
        except json.JSONDecodeError as error:
            await database.rollback()
            raise DinoStorageError("The parked dinosaur snapshot is not valid JSON.") from error
        if not isinstance(snapshot, dict):
            await database.rollback()
            raise DinoStorageError("The parked dinosaur snapshot is incomplete.")

        if snapshot.get("generatedShop") is not True:
            await database.rollback()
            raise DinoStorageError(
                "Only admin-given and shop-generated dinos use percentage-based diet repair."
            )

        # These are percentages by intent, not raw FNutrientsValues. The game
        # resolves them against the live species when the dino is restored.
        snapshot["generatedNutrientPercents"] = percentages

        updated_serialized = json.dumps(
            snapshot,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        cursor = await database.execute(
            """
            UPDATE stored_dinosaurs
            SET serialized_player_data = ?, payload_sha256 = ?, updated_at = ?
            WHERE id = ? AND discord_id = ? AND status = 'parked';
            """,
            (
                updated_serialized,
                payload_digest(updated_serialized),
                now,
                int(dinosaur_id),
                str(discord_id),
            ),
        )
        if cursor.rowcount != 1:
            await database.rollback()
            raise DinoStorageError("The parked dinosaur changed before it could be updated.")
        await database.commit()

    dinosaur["serialized_player_data"] = updated_serialized
    dinosaur["payload_sha256"] = payload_digest(updated_serialized)
    dinosaur["updated_at"] = now
    return dinosaur


async def run_park_dinosaur(discord_id: str, steam_id: str) -> dict:
    async with _park_lock:
        return await _run_park_dinosaur(discord_id, steam_id)


async def get_live_dinosaur_status(steam_id: str) -> dict:
    return await _send_and_wait(
        {
            "id": uuid.uuid4().hex,
            "verb": "dino.storage.probe",
            "steam": str(steam_id),
        }
    )


async def run_live_needs_probe(steam_id: str) -> dict:
    return await _send_and_wait(
        {
            "id": uuid.uuid4().hex,
            "verb": "dino.storage.needs_probe",
            "steam": str(steam_id),
        }
    )


async def run_live_prime_probe(steam_id: str) -> dict:
    return await _send_and_wait(
        {
            "id": uuid.uuid4().hex,
            "verb": "dino.storage.prime_probe",
            "steam": str(steam_id),
        }
    )


async def run_live_player_data_probe(steam_id: str) -> dict:
    return await _send_and_wait(
        {
            "id": uuid.uuid4().hex,
            "verb": "dino.storage.player_data_probe",
            "steam": str(steam_id),
        }
    )


async def run_ue4ss_object_dump() -> dict:
    return await _send_and_wait(
        {
            "id": uuid.uuid4().hex,
            "verb": "dino.storage.object_dump",
            "confirm": True,
        },
        timeout_seconds=180,
    )


async def run_self_slay(steam_id: str) -> dict:
    async with _self_slay_lock:
        result = await _send_and_wait(
            {
                "id": uuid.uuid4().hex,
                "verb": "dino.player.self_slay",
                "steam": str(steam_id),
            }
        )
    if result.get("ok") is not True:
        reason = str(result.get("reason") or "self-slay-failed")
        if reason == "player-not-online":
            raise DinoStorageError("You must be online in-game to use Slay.")
        if reason in {
            "player-has-no-live-pawn",
            "spectator-pawn-not-allowed",
            "self-slay-dinosaur-not-alive",
        }:
            raise DinoStorageError("You do not currently have a live dino to slay.")
        if reason == "restore-already-in-progress":
            raise DinoStorageError("Wait for your current unpark operation to finish first.")
        raise DinoStorageError(f"Could not slay your dino: {reason}")
    return result


def _usable_player_baseline(snapshot: object, steam_id: str) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if str(snapshot.get("steam") or "") != str(steam_id):
        return False
    skin = snapshot.get("skin")
    location = snapshot.get("location")
    rotation = snapshot.get("rotation")
    if not isinstance(skin, dict) or skin.get("skinCaptured") is not True:
        return False
    if not isinstance(location, dict) or not isinstance(rotation, dict):
        return False
    values = (
        location.get("x"), location.get("y"), location.get("z"),
        rotation.get("pitch"), rotation.get("yaw"), rotation.get("roll"),
    )
    return all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        for value in values
    )


async def _load_player_baseline(steam_id: str) -> dict:
    candidates: list[dict] = []

    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        cursor = await database.execute(
            """
            SELECT serialized_player_data
            FROM stored_dinosaurs
            WHERE steam_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 100;
            """,
            (str(steam_id),),
        )
        for row in await cursor.fetchall():
            try:
                snapshot = json.loads(str(row["serialized_player_data"] or ""))
            except json.JSONDecodeError:
                continue
            if _usable_player_baseline(snapshot, steam_id):
                candidates.append(snapshot)

    def load_saved_candidates() -> list[dict]:
        loaded: list[dict] = []
        paths = list(SAVED_ROOT.glob(f"death_{steam_id}_*.json"))
        paths.append(SAVED_ROOT / f"last_known_{steam_id}.json")
        for path in paths:
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if _usable_player_baseline(snapshot, steam_id):
                loaded.append(snapshot)
        return loaded

    candidates.extend(await asyncio.to_thread(load_saved_candidates))
    if not candidates:
        raise DinoStorageError(
            "No saved player history is available for this SteamID yet. "
            "The player does not need to be online, but they must have joined "
            "the server at least once since player-history snapshots were enabled."
        )
    return max(
        candidates,
        key=lambda snapshot: float(snapshot.get("capturedAt") or 0),
    )


async def build_admin_grant_snapshot(
    steam_id: str,
    requested_snapshot: dict,
) -> tuple[dict, str]:
    baseline = await _load_player_baseline(str(steam_id))
    needs_fraction = float(
        requested_snapshot.get(
            "requestedHungerFraction",
            requested_snapshot.get("generatedHungerFraction", 0.0),
        )
        or 0.0
    )
    thirst_fraction = float(
        requested_snapshot.get(
            "requestedThirstFraction",
            requested_snapshot.get("generatedThirstFraction", 0.0),
        )
        or 0.0
    )
    nutrient_percentages = requested_snapshot.get("requestedNutrientPercents")
    if not isinstance(nutrient_percentages, dict):
        nutrient_percentages = requested_snapshot.get("generatedNutrientPercents")
    if not isinstance(nutrient_percentages, dict):
        raise DinoStorageError("The replacement nutrient percentages are missing.")

    snapshot = copy.deepcopy(baseline)
    snapshot.update(
        {
            "version": 1,
            "captureMode": "park-pending",
            "capturedAt": int(datetime.now(timezone.utc).timestamp()),
            "steam": str(steam_id),
            "species": str(requested_snapshot["species"]),
            "classPath": str(requested_snapshot["classPath"]),
            "growth": float(requested_snapshot["growth"]),
            "health": 1.0,
            "maxHealth": 1.0,
            "blood": 1.0,
            "maxBlood": 1.0,
            "stamina": 1.0,
            "maxStamina": 1.0,
            "hunger": needs_fraction,
            "maxHunger": 1.0,
            "food": needs_fraction,
            "maxFoodValue": 1.0,
            "thirst": thirst_fraction,
            "maxThirst": 1.0,
            "oxygen": 1000.0,
            "lockedDamage": 0.0,
            "rottenValue": 1800.0,
            "isFemale": requested_snapshot.get("isFemale") is True,
            "isPrime": requested_snapshot.get("isPrime") is True,
            "generatedShop": False,
            "percentageBackedSnapshot": True,
            "requestedHungerPercent": needs_fraction * 100.0,
            "requestedThirstPercent": thirst_fraction * 100.0,
            "requestedCarbPercent": float(nutrient_percentages.get("carb") or 0.0),
            "requestedProteinPercent": float(
                nutrient_percentages.get("protein") or 0.0
            ),
            "requestedLipidPercent": float(nutrient_percentages.get("lipid") or 0.0),
            "currentLocationOnly": False,
            "preserveCurrentSkin": False,
            "forceMale": False,
            "forceFemale": False,
            "elderStacks": int(requested_snapshot["elderStacks"]),
            "unlockRequiredMutations": copy.deepcopy(
                requested_snapshot["unlockRequiredMutations"]
            ),
            "primeData": copy.deepcopy(requested_snapshot["primeData"]),
            "mutations": copy.deepcopy(requested_snapshot["mutations"]),
        }
    )
    snapshot["nutrients"] = {
        "carbValue": float(nutrient_percentages.get("carb") or 0.0) / 100.0,
        "proteinValue": float(nutrient_percentages.get("protein") or 0.0) / 100.0,
        "lipidValue": float(nutrient_percentages.get("lipid") or 0.0) / 100.0,
    }
    snapshot["location"] = copy.deepcopy(baseline["location"])
    snapshot["rotation"] = copy.deepcopy(baseline["rotation"])
    snapshot["skin"] = copy.deepcopy(baseline["skin"])
    snapshot["skin"]["skinIsFemale"] = snapshot["isFemale"]

    serialized = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
    return snapshot, serialized


async def normalize_legacy_admin_grant(dinosaur: dict) -> dict:
    """Convert one old synthetic admin grant into an ordinary parked row."""
    serialized = str(dinosaur.get("serialized_player_data") or "")
    try:
        requested_snapshot = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise DinoStorageError("The parked dinosaur snapshot is not valid JSON.") from error

    is_legacy_grant = (
        str(dinosaur.get("source") or "") == "admin_grant"
        or (
            isinstance(requested_snapshot, dict)
            and requested_snapshot.get("generatedAdmin") is True
        )
    )
    if not is_legacy_grant:
        return dinosaur
    if not isinstance(requested_snapshot, dict):
        raise DinoStorageError("The parked dinosaur snapshot is incomplete.")

    async with _normalization_lock:
        # Inventory menus intentionally hold display-oriented dictionaries.
        # Always re-read the authoritative row before integrity validation.
        current = await get_inventory_item(
            str(dinosaur["discord_id"]),
            int(dinosaur["id"]),
        )
        if current is None:
            raise DinoStorageError("That parked dinosaur is no longer available.")
        current_serialized = str(current.get("serialized_player_data") or "")
        try:
            current_snapshot = json.loads(current_serialized)
        except json.JSONDecodeError as error:
            raise DinoStorageError("The parked dinosaur snapshot is not valid JSON.") from error
        if (
            str(current.get("source") or "") != "admin_grant"
            and not (
                isinstance(current_snapshot, dict)
                and current_snapshot.get("generatedAdmin") is True
            )
        ):
            return current
        if not _snapshot_integrity_valid(
            current_serialized,
            str(current.get("payload_sha256") or ""),
            stage="legacy-admin-grant-reread",
            dinosaur_id=current.get("id"),
        ):
            raise DinoStorageError("The parked dinosaur snapshot failed its integrity check.")

        snapshot, normalized_serialized = await build_admin_grant_snapshot(
            str(current["steam_id"]),
            current_snapshot,
        )
        now = _utc_now()
        normalized_digest = payload_digest(normalized_serialized)
        async with aiosqlite.connect(DATABASE_PATH) as database:
            await database.execute("PRAGMA busy_timeout=5000;")
            cursor = await database.execute(
                """
                UPDATE stored_dinosaurs
                SET species = ?, growth = ?, sex = ?,
                    serialized_player_data = ?, payload_sha256 = ?,
                    source = 'player_park', updated_at = ?
                WHERE id = ? AND discord_id = ? AND status = 'parked'
                  AND payload_sha256 = ?;
                """,
                (
                    str(snapshot["species"]),
                    float(snapshot["growth"]),
                    "female" if snapshot.get("isFemale") is True else "male",
                    normalized_serialized,
                    normalized_digest,
                    now,
                    int(current["id"]),
                    str(current["discord_id"]),
                    str(current["payload_sha256"]),
                ),
            )
            await database.commit()
        if cursor.rowcount != 1:
            raise DinoStorageError(
                "The parked dinosaur changed before its legacy entry could be converted."
            )
        normalized = await get_inventory_item(
            str(current["discord_id"]),
            int(current["id"]),
        )
        if normalized is None:
            raise DinoStorageError("The converted parked dinosaur could not be reloaded.")
        return normalized


async def _run_park_dinosaur(discord_id: str, steam_id: str) -> dict:
    operation_id = uuid.uuid4().hex
    created_at = _utc_now()
    snapshot_path: Path | None = None
    dinosaur_id: int | None = None

    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute(
            """
            INSERT INTO dino_storage_operations
                (id, action, discord_id, steam_id, status, created_at)
            VALUES (?, 'park', ?, ?, 'capturing', ?);
            """,
            (operation_id, str(discord_id), str(steam_id), created_at),
        )
        await database.commit()

    capture_command = {
        "id": operation_id,
        "verb": "dino.storage.capture_park",
        "steam": str(steam_id),
    }

    try:
        capture = await _send_and_wait(capture_command)
    except Exception as error:
        await _complete_operation(operation_id, "failed", str(error))
        raise

    if capture.get("ok") is not True:
        detail = json.dumps(capture, separators=(",", ":"), ensure_ascii=False)
        await _complete_operation(operation_id, "rejected", detail)
        return capture

    snapshot_path = SAVED_ROOT / f"park_pending_{steam_id}_{operation_id}.json"
    try:
        validated_path, snapshot, serialized = await asyncio.to_thread(
            _load_and_validate_snapshot,
            capture,
            str(steam_id),
            operation_id,
        )
        snapshot_path = validated_path
        dinosaur_id = await _create_pending_inventory_item(
            discord_id=str(discord_id),
            steam_id=str(steam_id),
            snapshot=snapshot,
            serialized=serialized,
        )
        await _set_operation_inventory_id(operation_id, dinosaur_id, "committing")
    except Exception as error:
        if dinosaur_id is not None:
            await _delete_pending_inventory_item(dinosaur_id)
        if snapshot_path is not None:
            await asyncio.to_thread(_remove_staging_snapshot, snapshot_path)
        await _complete_operation(operation_id, "failed", str(error))
        if isinstance(error, DinoStorageError):
            raise
        raise DinoStorageError(f"The captured dinosaur JSON was rejected: {error}") from error

    commit_command = {
        "id": uuid.uuid4().hex,
        "verb": "dino.storage.commit_park",
        "steam": str(steam_id),
        "captureId": operation_id,
    }

    try:
        commit = await _send_and_wait(commit_command)
    except Exception as error:
        await _complete_operation(
            operation_id,
            "recovery_required",
            "Commit response was interrupted; pending inventory was preserved. " + str(error),
        )
        raise DinoStorageError(
            "The server lost the parking confirmation. The snapshot was preserved "
            "for recovery; contact an administrator before spawning another dinosaur."
        ) from error

    if commit.get("ok") is not True or commit.get("parked") is not True:
        await _delete_pending_inventory_item(dinosaur_id)
        await asyncio.to_thread(_remove_staging_snapshot, snapshot_path)
        detail = json.dumps(commit, separators=(",", ":"), ensure_ascii=False)
        await _complete_operation(operation_id, "rejected", detail)
        return commit

    try:
        await _finalize_inventory_item(dinosaur_id, operation_id, capture, commit)
    except Exception as error:
        await _complete_operation(
            operation_id,
            "recovery_required",
            f"Dinosaur was removed but inventory finalization failed: {error}",
        )
        raise DinoStorageError(
            "Your dinosaur was removed and its snapshot is preserved, but the inventory "
            "entry needs administrator recovery."
        ) from error

    await asyncio.to_thread(_remove_staging_snapshot, snapshot_path)
    result = dict(capture)
    result.update(
        {
            "ok": True,
            "parked": True,
            "dinosaurModified": True,
            "dinosaurId": dinosaur_id,
            "reason": "dinosaur-parked-in-inventory",
        }
    )
    return result


async def run_unpark_dinosaur(
    discord_id: str,
    steam_id: str,
    dinosaur_id: int,
    location_mode: str,
) -> dict:
    if location_mode not in {"saved", "current"}:
        raise DinoStorageError("The selected unpark location mode is invalid.")
    async with _unpark_lock:
        return await _run_unpark_dinosaur(
            discord_id,
            steam_id,
            dinosaur_id,
            location_mode,
        )


async def _run_unpark_dinosaur(
    discord_id: str,
    steam_id: str,
    dinosaur_id: int,
    location_mode: str,
) -> dict:
    dinosaur = await get_inventory_item(discord_id, dinosaur_id)
    if dinosaur is None:
        raise DinoStorageError("That parked dinosaur is no longer available.")
    if str(dinosaur.get("steam_id")) != str(steam_id):
        raise DinoStorageError("The parked dinosaur does not belong to the linked SteamID.")
    dinosaur = await normalize_legacy_admin_grant(dinosaur)
    granted_by = str(dinosaur.get("granted_by") or "")
    is_admin_replacement = bool(granted_by) and not granted_by.startswith("shop:")
    if is_admin_replacement:
        # Replacement dinos do not own a genuine parked location. Keep both
        # familiar buttons in Discord, but make either choice preserve the
        # player's current in-game position.
        location_mode = "current"

    serialized = str(dinosaur.get("serialized_player_data") or "")
    expected_digest = str(dinosaur.get("payload_sha256") or "")
    if not _snapshot_integrity_valid(
        serialized,
        expected_digest,
        stage="unpark",
        dinosaur_id=dinosaur.get("id"),
    ):
        raise DinoStorageError("The parked dinosaur snapshot failed its integrity check.")
    try:
        snapshot = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise DinoStorageError("The parked dinosaur snapshot is not valid JSON.") from error
    if not isinstance(snapshot, dict) or snapshot.get("classPath") is None:
        raise DinoStorageError("The parked dinosaur snapshot is incomplete.")
    if (str(dinosaur.get("source") or "") == "shop_token" or snapshot.get("shopToken") is True):
        mutations = snapshot.get("mutations") if isinstance(snapshot.get("mutations"), dict) else {}
        selected = [str(mutations.get(f"MutationSlot{index}") or "").strip() for index in range(1, 5)]
        if snapshot.get("shopTokenMutationsSelected") is not True or any(not value for value in selected) or len(set(selected)) != 4:
            raise DinoStorageError("Choose four unique mutations for this Shop Token before unparking.")
        location_mode = "current"
    if snapshot.get("currentLocationOnly") is True and location_mode != "current":
        raise DinoStorageError("This dinosaur can only be unparked at your current location.")
    if is_admin_replacement:
        # Old admin rows predate the explicit marker. Reconstruct it only in
        # the restore staging copy; the integrity-protected inventory snapshot
        # remains untouched.
        def percentage(
            percent_key: str,
            fraction_keys: tuple[str, ...],
            value: object,
            maximum: object,
        ) -> float:
            explicit = snapshot.get(percent_key)
            if isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
                return max(0.0, min(100.0, float(explicit)))
            for key in fraction_keys:
                fraction = snapshot.get(key)
                if isinstance(fraction, (int, float)) and not isinstance(fraction, bool):
                    return max(0.0, min(100.0, float(fraction) * 100.0))
            try:
                raw = float(value)
                capacity = float(maximum)
            except (TypeError, ValueError):
                return 0.0
            if not math.isfinite(raw) or not math.isfinite(capacity) or capacity <= 0:
                return 0.0
            return max(0.0, min(100.0, raw / capacity * 100.0))

        nutrients = snapshot.get("nutrients")
        if not isinstance(nutrients, dict):
            nutrients = {}
        requested_nutrients = snapshot.get("requestedNutrientPercents")
        if not isinstance(requested_nutrients, dict):
            requested_nutrients = snapshot.get("generatedNutrientPercents")
        if not isinstance(requested_nutrients, dict):
            requested_nutrients = {}
        snapshot["percentageBackedSnapshot"] = True
        snapshot["requestedHungerPercent"] = percentage(
            "requestedHungerPercent",
            ("requestedHungerFraction", "generatedHungerFraction"),
            snapshot.get("hunger"),
            snapshot.get("maxHunger"),
        )
        snapshot["requestedThirstPercent"] = percentage(
            "requestedThirstPercent",
            ("requestedThirstFraction", "generatedThirstFraction"),
            snapshot.get("thirst"),
            snapshot.get("maxThirst"),
        )
        nutrient_capacity = snapshot.get("maxHunger")
        for friendly, field, target in (
            ("carb", "carbValue", "requestedCarbPercent"),
            ("protein", "proteinValue", "requestedProteinPercent"),
            ("lipid", "lipidValue", "requestedLipidPercent"),
        ):
            selected = requested_nutrients.get(friendly)
            if isinstance(selected, (int, float)) and not isinstance(selected, bool):
                snapshot[target] = max(0.0, min(100.0, float(selected)))
            else:
                snapshot[target] = percentage(
                    target,
                    (),
                    nutrients.get(field),
                    nutrient_capacity,
                )
        serialized = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)

    operation_id = uuid.uuid4().hex
    created_at = _utc_now()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute(
            """
            INSERT INTO dino_storage_operations
                (id, action, discord_id, steam_id, stored_dinosaur_id, status, created_at)
            VALUES (?, 'unpark', ?, ?, ?, 'restoring', ?);
            """,
            (
                operation_id,
                str(discord_id),
                str(steam_id),
                int(dinosaur_id),
                created_at,
            ),
        )
        await database.commit()

    restore_name = f"restore_pending_{steam_id}_{operation_id}.json"
    restore_path = SAVED_ROOT / restore_name
    try:
        await asyncio.to_thread(_write_restore_staging, restore_path, serialized)
    except Exception as error:
        await _complete_operation(operation_id, "failed", str(error))
        raise DinoStorageError("The restore staging file could not be created.") from error

    command = {
        "id": operation_id,
        "verb": "dino.storage.restore",
        "steam": str(steam_id),
        "restoreFile": restore_name,
        "locationMode": location_mode,
    }
    try:
        result = await _send_and_wait(command)
    except Exception as error:
        await _mark_restore_retryable(
            dinosaur_id,
            operation_id,
            "Restore response was interrupted: " + str(error),
        )
        raise DinoStorageError(
            "The server lost the unpark confirmation. Your inventory entry remains "
            "available; wait for the current attempt to stop, then try again."
        ) from error
    finally:
        await asyncio.to_thread(_remove_staging_snapshot, restore_path)

    detail = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
    if result.get("ok") is not True or result.get("restored") is not True:
        if result.get("dinosaurModified") is True:
            reason = str(result.get("reason") or "unknown-restore-error")
            await _mark_restore_retryable(
                dinosaur_id,
                operation_id,
                "The game reported a partially applied restore: " + detail,
            )
            raise DinoStorageError(
                f"The restore was only partially confirmed (`{reason}`). Your inventory "
                "entry remains available so you can safely retry the unpark."
            )
        await _complete_operation(operation_id, "rejected", detail)
        return result

    try:
        await _finalize_unpark(dinosaur_id, operation_id, detail)
    except Exception as error:
        await _mark_restore_retryable(
            dinosaur_id,
            operation_id,
            "Game restore succeeded but inventory finalization failed: " + str(error),
        )
        raise DinoStorageError(
            "Your dinosaur was restored, but inventory finalization failed. The entry "
            "remains available so the unpark can be retried."
        ) from error

    result["dinosaurId"] = int(dinosaur_id)
    return result


def _write_restore_staging(path: Path, serialized: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


async def _finalize_unpark(
    dinosaur_id: int,
    operation_id: str,
    detail: str,
) -> None:
    now = _utc_now()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("BEGIN IMMEDIATE;")
        cursor = await database.execute(
            """
            UPDATE stored_dinosaurs
            SET status = 'unparked', updated_at = ?
            WHERE id = ? AND status = 'parked';
            """,
            (now, int(dinosaur_id)),
        )
        if cursor.rowcount != 1:
            await database.rollback()
            raise DinoStorageError("The parked inventory row could not be finalized.")
        await database.execute(
            """
            UPDATE dino_storage_operations
            SET status = 'succeeded', detail = ?, completed_at = ?
            WHERE id = ?;
            """,
            (detail[:4000], now, operation_id),
        )
        await database.commit()


async def _mark_restore_retryable(
    dinosaur_id: int,
    operation_id: str,
    detail: str,
) -> None:
    now = _utc_now()
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute("BEGIN IMMEDIATE;")
        await database.execute(
            """
            UPDATE stored_dinosaurs
            SET status = 'parked', updated_at = ?
            WHERE id = ? AND status IN ('parked', 'restore_uncertain');
            """,
            (now, int(dinosaur_id)),
        )
        await database.execute(
            """
            UPDATE dino_storage_operations
            SET status = 'retryable_failed', detail = ?, completed_at = ?
            WHERE id = ?;
            """,
            (detail[:4000], now, operation_id),
        )
        await database.commit()


def _load_and_validate_snapshot(
    capture: dict,
    steam_id: str,
    operation_id: str,
) -> tuple[Path, dict, str]:
    expected_name = f"park_pending_{steam_id}_{operation_id}.json"
    if capture.get("snapshotFile") != expected_name:
        raise DinoStorageError("The game returned an unexpected snapshot filename.")

    path = SAVED_ROOT / expected_name
    if not path.is_file():
        raise DinoStorageError("The captured JSON file was not found on disk.")
    if path.stat().st_size > 1_000_000:
        raise DinoStorageError("The captured JSON file was unexpectedly large.")

    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DinoStorageError(f"The captured JSON file could not be read: {error}") from error

    required_top_level = {
        "version", "captureMode", "capturedAt", "steam", "species", "classPath",
        "growth", "health", "stamina", "hunger", "thirst", "blood",
        "elderStacks", "unlockRequiredMutations", "nutrients", "primeData",
        "mutations", "skin", "location", "rotation",
    }
    missing = sorted(required_top_level.difference(snapshot))
    if missing:
        raise DinoStorageError("Snapshot is missing: " + ", ".join(missing))
    if snapshot.get("version") != 1 or snapshot.get("captureMode") != "park-pending":
        raise DinoStorageError("Snapshot schema or capture mode is invalid.")
    if str(snapshot.get("steam")) != steam_id:
        raise DinoStorageError("Snapshot SteamID does not match the linked player.")
    if not isinstance(snapshot.get("species"), str) or not snapshot["species"]:
        raise DinoStorageError("Snapshot species is invalid.")

    mutations = snapshot.get("mutations")
    if not isinstance(mutations, dict) or set(mutations) != MUTATION_FIELDS:
        raise DinoStorageError("Snapshot does not contain the complete 16-slot mutation map.")
    if not all(isinstance(value, str) for value in mutations.values()):
        raise DinoStorageError("Snapshot mutation values are invalid.")

    skin = snapshot.get("skin")
    if not isinstance(skin, dict) or skin.get("skinCaptured") is not True:
        raise DinoStorageError("Snapshot does not contain a captured skin.")
    if not isinstance(skin.get("skinIsFemale"), bool):
        raise DinoStorageError("Snapshot skin gender is invalid.")
    for field in SKIN_COLOR_FIELDS:
        for channel in "RGBA":
            value = skin.get(f"{field}{channel}")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise DinoStorageError(f"Snapshot skin {field}{channel} is invalid.")

    nutrients = snapshot.get("nutrients")
    if not isinstance(nutrients, dict) or not {
        "carbValue", "proteinValue", "lipidValue"
    }.issubset(nutrients):
        raise DinoStorageError("Snapshot does not contain all three nutrients.")

    prime = snapshot.get("primeData")
    conditions = prime.get("conditions") if isinstance(prime, dict) else None
    tasks = prime.get("tasks") if isinstance(prime, dict) else None
    if not isinstance(conditions, dict) or len(conditions) != 10:
        raise DinoStorageError("Snapshot does not contain all ten Prime conditions.")
    if not isinstance(tasks, list) or len(tasks) != 10:
        raise DinoStorageError("Snapshot does not contain all ten Prime task records.")
    if set(conditions) != {f"cond{index}" for index in range(1, 11)}:
        raise DinoStorageError("Snapshot Prime condition numbers are invalid.")
    if not all(isinstance(value, bool) for value in conditions.values()):
        raise DinoStorageError("Snapshot Prime condition values are invalid.")
    if not all(
        isinstance(task, dict)
        and task.get("number") == index
        and isinstance(task.get("name"), str)
        and isinstance(task.get("complete"), bool)
        for index, task in enumerate(tasks, start=1)
    ):
        raise DinoStorageError("Snapshot Prime task records are invalid.")
    unlocks = snapshot.get("unlockRequiredMutations")
    if not isinstance(unlocks, list) or not all(isinstance(value, str) for value in unlocks):
        raise DinoStorageError("Snapshot quest-mutation unlocks are invalid.")
    location = snapshot.get("location")
    rotation = snapshot.get("rotation")
    if not isinstance(location, dict) or not isinstance(rotation, dict):
        raise DinoStorageError("Snapshot location or rotation is invalid.")

    numeric_paths = {
        "growth": snapshot.get("growth"), "health": snapshot.get("health"),
        "stamina": snapshot.get("stamina"), "hunger": snapshot.get("hunger"),
        "thirst": snapshot.get("thirst"), "blood": snapshot.get("blood"),
        "carbValue": nutrients.get("carbValue"),
        "proteinValue": nutrients.get("proteinValue"),
        "lipidValue": nutrients.get("lipidValue"),
        "location.x": location.get("x"),
        "location.y": location.get("y"),
        "location.z": location.get("z"),
        "rotation.pitch": rotation.get("pitch"),
        "rotation.yaw": rotation.get("yaw"),
        "rotation.roll": rotation.get("roll"),
    }
    for optional_maximum in ("maxHealth", "maxBlood"):
        if optional_maximum in snapshot:
            numeric_paths[optional_maximum] = snapshot[optional_maximum]
    for name, value in numeric_paths.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise DinoStorageError(f"Snapshot {name} is not a finite number.")

    serialized = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
    return path, snapshot, serialized


async def _create_pending_inventory_item(
    discord_id: str,
    steam_id: str,
    snapshot: dict,
    serialized: str,
) -> int:
    now = _utc_now()
    sex = "female" if snapshot.get("isFemale") is True else "male"
    async with aiosqlite.connect(DATABASE_PATH) as database:
        cursor = await database.execute(
            """
            INSERT INTO stored_dinosaurs
                (discord_id, steam_id, species, growth, sex,
                 serialized_player_data, payload_sha256, source, status,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'player_park', 'pending_park', ?, ?);
            """,
            (
                discord_id,
                steam_id,
                snapshot["species"],
                float(snapshot["growth"]),
                sex,
                serialized,
                payload_digest(serialized),
                now,
                now,
            ),
        )
        await database.commit()
        return int(cursor.lastrowid)


async def _set_operation_inventory_id(
    operation_id: str,
    dinosaur_id: int,
    status: str,
) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute(
            """
            UPDATE dino_storage_operations
            SET stored_dinosaur_id = ?, status = ?
            WHERE id = ?;
            """,
            (int(dinosaur_id), status, operation_id),
        )
        await database.commit()


async def _delete_pending_inventory_item(dinosaur_id: int) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute(
            "DELETE FROM stored_dinosaurs WHERE id = ? AND status = 'pending_park';",
            (int(dinosaur_id),),
        )
        await database.commit()


async def _finalize_inventory_item(
    dinosaur_id: int,
    operation_id: str,
    capture: dict,
    commit: dict,
) -> None:
    now = _utc_now()
    nutrient_percentages = {
        "carb": commit.get("nutrientCarbPercent"),
        "protein": commit.get("nutrientProteinPercent"),
        "lipid": commit.get("nutrientLipidPercent"),
    }
    has_nutrient_percentages = all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 100.0
        for value in nutrient_percentages.values()
    )
    detail = json.dumps(
        {"capture": capture, "commit": commit},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    async with aiosqlite.connect(DATABASE_PATH) as database:
        database.row_factory = aiosqlite.Row
        await database.execute("BEGIN IMMEDIATE;")
        cursor = await database.execute(
            """
            SELECT serialized_player_data, payload_sha256
            FROM stored_dinosaurs
            WHERE id = ? AND status = 'pending_park';
            """,
            (int(dinosaur_id),),
        )
        row = await cursor.fetchone()
        if row is None:
            await database.rollback()
            raise DinoStorageError("Pending inventory row was not available to finalize.")

        serialized = str(row["serialized_player_data"] or "")
        if not _snapshot_integrity_valid(
            serialized,
            str(row["payload_sha256"] or ""),
            stage="park-finalization",
            dinosaur_id=dinosaur_id,
        ):
            await database.rollback()
            raise DinoStorageError("Pending inventory snapshot failed its integrity check.")
        if has_nutrient_percentages:
            try:
                snapshot = json.loads(serialized)
            except json.JSONDecodeError as error:
                await database.rollback()
                raise DinoStorageError("Pending inventory snapshot is not valid JSON.") from error
            snapshot["nutrientPercentages"] = {
                name: float(value)
                for name, value in nutrient_percentages.items()
            }
            serialized = json.dumps(
                snapshot,
                separators=(",", ":"),
                ensure_ascii=False,
            )

        cursor = await database.execute(
            """
            UPDATE stored_dinosaurs
            SET serialized_player_data = ?, payload_sha256 = ?,
                status = 'parked', updated_at = ?
            WHERE id = ? AND status = 'pending_park';
            """,
            (
                serialized,
                payload_digest(serialized),
                now,
                int(dinosaur_id),
            ),
        )
        if cursor.rowcount != 1:
            await database.rollback()
            raise DinoStorageError("Pending inventory row was not available to finalize.")
        await database.execute(
            """
            UPDATE dino_storage_operations
            SET status = 'succeeded', detail = ?, completed_at = ?
            WHERE id = ?;
            """,
            (detail[:4000], now, operation_id),
        )
        await database.commit()


def _remove_staging_snapshot(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


async def _complete_operation(operation_id: str, status: str, detail: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as database:
        await database.execute(
            """
            UPDATE dino_storage_operations
            SET status = ?, detail = ?, completed_at = ?
            WHERE id = ?;
            """,
            (status, detail[:4000], _utc_now(), operation_id),
        )
        await database.commit()


async def _send_and_wait(
    command: dict,
    timeout_seconds: float | None = None,
) -> dict:
    if not MOD_ROOT.is_dir():
        raise DinoStorageUnavailable(
            f"DinoStorage UE4SS mod directory was not found at {MOD_ROOT}."
        )

    await asyncio.to_thread(MOD_ROOT.mkdir, parents=True, exist_ok=True)
    result_offset = await asyncio.to_thread(_file_size, RESULT_PATH)

    encoded = (
        json.dumps(command, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")

    async with _append_lock:
        await asyncio.to_thread(_append_bytes, COMMAND_PATH, encoded)

    effective_timeout = (
        BRIDGE_TIMEOUT_SECONDS if timeout_seconds is None else float(timeout_seconds)
    )
    deadline = asyncio.get_running_loop().time() + effective_timeout
    next_offset = result_offset

    while asyncio.get_running_loop().time() < deadline:
        next_offset, match = await asyncio.to_thread(
            _scan_results,
            RESULT_PATH,
            next_offset,
            command["id"],
        )
        if match is not None:
            return match
        await asyncio.sleep(0.25)

    raise DinoStorageTimeout(
        f"The game did not answer storage operation {command['id']} within "
        f"{effective_timeout:g} seconds."
    )


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _append_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _scan_results(path: Path, offset: int, operation_id: str) -> tuple[int, dict | None]:
    if not path.exists():
        return 0, None

    size = path.stat().st_size
    if offset > size:
        offset = 0

    with path.open("rb") as stream:
        stream.seek(offset)
        while True:
            line = stream.readline()
            if not line:
                return stream.tell(), None
            if not line.endswith(b"\n"):
                return offset, None
            offset = stream.tell()
            try:
                result = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if result.get("id") == operation_id:
                return offset, result


def payload_digest(serialized_player_data: str) -> str:
    return hashlib.sha256(serialized_player_data.encode("utf-8")).hexdigest()
