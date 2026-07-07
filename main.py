import discord
from discord.ext import commands

from bot.config.settings import DISCORD_TOKEN
from bot.utils.logger import setup_logger


logger = setup_logger()


class EvrimaBot(commands.Bot):

    def __init__(self):
        intents = discord.Intents.default()

        # These match the intents we enabled in Discord
        intents.members = True
        intents.message_content = True
        intents.presences = True

        super().__init__(
            command_prefix="!",
            intents=intents
        )


    async def setup_hook(self):
        # Load our command modules
        await self.load_extension(
            "bot.cogs.general"
        )

        # Register slash commands with Discord
        await self.tree.sync()

        logger.info("Commands synchronized")


    async def on_ready(self):
        logger.info(
            f"Connected as {self.user}"
        )


bot = EvrimaBot()

bot.run(DISCORD_TOKEN)