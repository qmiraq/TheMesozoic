from sqlalchemy import select

from bot.database.database import Session
from bot.database.models import BotSettings, Player
from bot.config.defaults import DEFAULT_SETTINGS


async def set_setting(key: str, value: str):

    async with Session() as session:

        result = await session.execute(
            select(BotSettings).where(
                BotSettings.key == key
            )
        )

        setting = result.scalar_one_or_none()

        if setting:
            setting.value = value

        else:
            setting = BotSettings(
                key=key,
                value=value
            )

            session.add(setting)

        await session.commit()


async def get_setting(key: str):

    async with Session() as session:

        result = await session.execute(
            select(BotSettings).where(
                BotSettings.key == key
            )
        )

        return result.scalar_one_or_none()


async def get_all_settings():

    async with Session() as session:

        result = await session.execute(
            select(BotSettings)
        )

        return result.scalars().all()
    
async def create_default_settings():

    async with Session() as session:

        for key, value in DEFAULT_SETTINGS.items():

            result = await session.execute(
                select(BotSettings).where(
                    BotSettings.key == key
                )
            )

            existing = result.scalar_one_or_none()

            if not existing:

                session.add(
                    BotSettings(
                        key=key,
                        value=value
                    )
                )

        await session.commit()


async def create_player_link(
    discord_id: str,
    steam_id: str
):

    async with Session() as session:

        existing_discord = await session.execute(
            select(Player).where(
                Player.discord_id == discord_id
            )
        )

        if existing_discord.scalar_one_or_none():
            return False, "Discord account already linked."


        existing_steam = await session.execute(
            select(Player).where(
                Player.steam_id == steam_id
            )
        )

        if existing_steam.scalar_one_or_none():
            return False, "SteamID already linked."


        player = Player(
            discord_id=discord_id,
            steam_id=steam_id,
            evrima_name="Unknown"
        )

        session.add(player)

        await session.commit()

        return True, "Account linked successfully."
async def get_player_by_discord(
    discord_id: str
):

    async with Session() as session:

        result = await session.execute(
            select(Player).where(
                Player.discord_id == discord_id
            )
        )

        return result.scalar_one_or_none()