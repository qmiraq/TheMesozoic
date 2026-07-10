from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from bot.database.models import Base


DATABASE_URL = "sqlite+aiosqlite:///evrimabot.db"


engine = create_async_engine(
    DATABASE_URL,
    echo=False
)


Session = async_sessionmaker(
    engine,
    expire_on_commit=False
)


async def init_database():
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all
        )