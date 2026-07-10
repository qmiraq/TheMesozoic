import asyncio

from bot.services.rcon_service import rcon


async def main():

    players = await rcon.get_player_data()

    print(players)


asyncio.run(main())