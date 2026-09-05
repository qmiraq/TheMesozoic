from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import unquote

from bot.services.dino_storage_service import MOD_ROOT


PRIME_STATE_PATH = MOD_ROOT.parent / "miniEniac" / "prime_states_v1.tsv"

PRIME_TASKS = (
    (1, "Visit a Sanctuary as a Juvenile"),
    (2, "Get Nested In"),
    (3, "Get Perfect Diet"),
    (4, "Visit Mass Migration Zone"),
    (5, "Visit 2 Migration Zones"),
    (6, "Visit 4 Patrol Zones"),
    (7, "Never be infertile"),
    (8, "Never get muscle spasms"),
    (9, "Raise children to subadult"),
    (10, "Species task"),
)


def _decode(value: str) -> str:
    return unquote(str(value or ""))


def _csv_values(value: str) -> list[str]:
    return [
        decoded
        for token in str(value or "").split(",")
        if (decoded := _decode(token))
    ]


def _read_prime_status(path: Path, steam_id: str) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None

    if not lines or lines[0] != "miniEniacPrimeState\tv1":
        return None

    target = str(steam_id)
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) < 14 or _decode(fields[0]) != target:
            continue
        completed = {
            int(token)
            for token in fields[10].split(",")
            if token.isdigit() and 1 <= int(token) <= 10
        }
        return {
            "steam_id": target,
            "species": _decode(fields[1]) or "Unknown",
            "growth": max(0.0, float(fields[2] or 0)),
            "life_serial": max(1, int(float(fields[3] or 1))),
            "frozen": fields[4] == "1",
            "completed_tasks": sorted(completed),
            "sanctuary_progress": len(_csv_values(fields[11])),
            "migration_progress": len(_csv_values(fields[12])),
            "patrol_progress": len(_csv_values(fields[13])),
        }
    return None


async def get_prime_status(steam_id: str) -> dict | None:
    return await asyncio.to_thread(
        _read_prime_status,
        PRIME_STATE_PATH,
        str(steam_id),
    )
