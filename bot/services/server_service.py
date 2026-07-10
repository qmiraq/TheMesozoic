from bot.services.database_service import get_setting


async def get_server_config():

    host = await get_setting(
        "server_host"
    )

    port = await get_setting(
        "server_rcon_port"
    )

    password = await get_setting(
        "server_rcon_password"
    )

    return {
        "host": host.value if host else None,
        "port": int(port.value) if port else None,
        "password": password.value if password else None
    }