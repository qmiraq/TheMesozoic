import aiohttp

RCON_API_URL = "http://localhost:5000"


async def get_server_status():
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{RCON_API_URL}/Rcon/server"
        ) as response:
            return await response.json()


async def get_players():
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{RCON_API_URL}/Rcon/players"
        ) as response:
            return await response.json()
