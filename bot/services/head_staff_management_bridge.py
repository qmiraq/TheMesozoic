from __future__ import annotations
import asyncio, json, sys
from pathlib import Path

# This bridge is executed by its file path from miniEniac-RCON. Python would
# otherwise expose only bot/services on sys.path and `import bot` would fail.
APPLICATION_ROOT = Path(__file__).resolve().parents[2]
if str(APPLICATION_ROOT) not in sys.path:
    sys.path.insert(0, str(APPLICATION_ROOT))
from bot.services.economy_service import (
    adjust_points, create_shop_listing, deactivate_shop_listing,
    grant_admin_dinosaur, list_shop_listings, resolve_linked_player, update_shop_listing, list_mutation_catalog,
)
from bot.services.dino_storage_service import MUTATION_FIELDS

async def run(req: dict):
    action=req.get("action")
    # Match the Discord shop exactly: only active permanent listings are shown.
    if action == "shop.list": return {"listings": await list_shop_listings(True)}
    if action == "mutation.list": return {"mutations": await list_mutation_catalog(species=req.get("species"))}
    if action == "shop.create":
        return {"id": await create_shop_listing(req["species"], [], int(req["price"]), req["actor"])}
    if action == "shop.disable": return {"ok": await deactivate_shop_listing(int(req["id"]))}
    if action == "shop.update": return {"ok": await update_shop_listing(int(req["id"]),req["species"],int(req["price"]),bool(req.get("active",True)))}
    if action == "points.give":
        player=await resolve_linked_player(discord_id=req.get("discordId"),steam_id=req.get("steamId"))
        if not player: raise RuntimeError("No linked player matched that identifier.")
        balance=await adjust_points(str(player["discord_id"]),str(player["steam_id"]),int(req["amount"]),req["actor"])
        return {"ok":True,"balance":balance,"player":player}
    if action == "dino.give":
        player=await resolve_linked_player(discord_id=req.get("discordId"),steam_id=req.get("steamId"))
        if not player: raise RuntimeError("No linked player matched that identifier.")
        muts={field:str((req.get("mutations") or {}).get(field) or "") for field in MUTATION_FIELDS}
        return await grant_admin_dinosaur(str(player["discord_id"]),str(player["steam_id"]),req["species"],req["growth"],req["isPrime"],req["isFemale"],req["entombments"],req["mutationCount"],req["hunger"],req["thirst"],req["carb"],req["protein"],req["lipid"],muts,req["actor"])
    raise RuntimeError("Unknown management action.")

async def main():
    try: print(json.dumps({"ok":True,"result":await run(json.loads(sys.stdin.read()))},default=str))
    except Exception as exc: print(json.dumps({"ok":False,"error":str(exc)}))
if __name__ == "__main__": asyncio.run(main())
