from bot.services.rcon_service import rcon


async def connect_to_server():
    await rcon.connect()


async def disconnect_from_server():
    await rcon.disconnect()