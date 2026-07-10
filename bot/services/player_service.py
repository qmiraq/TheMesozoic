from bot.services.database_service import (
    create_player_link,
    get_player_by_discord
)


async def link_player(
    discord_id: str,
    steam_id: str
):
    return await create_player_link(
        discord_id,
        steam_id
    )


async def get_profile(
    discord_id: str
):
    return await get_player_by_discord(
        discord_id
    )