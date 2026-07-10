from bot.services.rcon_api_service import get_server_status


async def get_live_server():

    return await get_server_status()


async def get_player_count():

    server = await get_live_server()

    return (
        server["currentPlayers"],
        server["maxPlayers"]
    )