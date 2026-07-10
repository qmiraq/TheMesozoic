import asyncio
from bot.services.rcon_api_service import get_server_status, get_players


async def main():
    server = await get_server_status()
    players = await get_players()

    print(server)
    print(players)


asyncio.run(main())