import discord
from discord.ext import commands, tasks

from bot.services.admin_command_tree import AdminCommandTree

from bot.database.database import init_database
from bot.services.database_service import create_default_settings
from bot.config.settings import DISCORD_TOKEN
from bot.utils.logger import setup_logger


logger = setup_logger()


class EvrimaBot(commands.Bot):

    def __init__(self):
        intents = discord.Intents.default()

        intents.members = True
        intents.message_content = True
        intents.presences = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            tree_cls=AdminCommandTree
        )


    async def setup_hook(self):

        await init_database()
        await create_default_settings()

        await self.load_extension(
            "bot.cogs.general"
        )

        await self.load_extension(
            "bot.cogs.settings"
        )

        await self.load_extension(
            "bot.cogs.player"
        )

        await self.load_extension(
            "bot.cogs.server"
        )

        await self.load_extension(
            "bot.cogs.dino_storage"
        )
        await self.load_extension(
            "bot.cogs.economy"
        )
        await self.load_extension(
            "bot.cogs.admin_logs"
        )
        await self.load_extension(
            "bot.cogs.auto_restart"
        )
        await self.load_extension(
            "bot.cogs.weather_forecast"
        )
        await self.load_extension(
            "bot.cogs.population_control"
        )
        synced = await self.tree.sync()

        logger.info(
            f"Synced commands: {[command.name for command in synced]}"
        )


    async def update_status(self):

        from bot.services.live_server_service import get_player_count

        try:

            current, maximum = await get_player_count()

            await self.change_presence(
                activity=discord.Game(
                    name=f"🦖 {current}/{maximum} players online!"
                )
            )

            logger.info(
                f"Updated Discord status: {current}/{maximum} players"
            )


        except Exception as e:

            logger.error(
                f"Status update failed: {e}"
            )


    @tasks.loop(minutes=1)
    async def status_loop(self):

        await self.update_status()


    async def on_ready(self):

        logger.info(
            f"Connected as {self.user}"
        )

        if not self.status_loop.is_running():
            self.status_loop.start()


bot = EvrimaBot()

bot.run(DISCORD_TOKEN)