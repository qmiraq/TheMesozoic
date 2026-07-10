import aiohttp


class RCONService:

    def __init__(self):
        self.base_url = "http://localhost:5000/Rcon"


    async def get_player_data(self):

        async with aiohttp.ClientSession() as session:

            async with session.get(
                f"{self.base_url}/playerdata"
            ) as response:

                if response.status != 200:
                    raise Exception(
                        f"RCON API error: {response.status}"
                    )

                return await response.json()


    async def get_server_details(self):

        async with aiohttp.ClientSession() as session:

            async with session.get(
                f"{self.base_url}/server"
            ) as response:

                if response.status != 200:
                    raise Exception(
                        f"RCON API error: {response.status}"
                    )

                return await response.json()


rcon = RCONService()