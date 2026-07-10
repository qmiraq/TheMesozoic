from datetime import datetime

from sqlalchemy import String, Integer, Boolean, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column



class Base(DeclarativeBase):
    pass


class BotSettings(Base):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    key: Mapped[str] = mapped_column(
        String(100),
        unique=True
    )

    value: Mapped[str] = mapped_column(
        String(500)
    )

class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    discord_id: Mapped[str] = mapped_column(
        String(20),
        unique=True
    )

    steam_id: Mapped[str] = mapped_column(
        String(30),
        unique=True
    )

    evrima_name: Mapped[str] = mapped_column(
        String(100),
        default="Unknown"
    )

    first_join: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True
    )

    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True
    )

    total_joins: Mapped[int] = mapped_column(
        Integer,
        default=0
    )

    is_online: Mapped[bool] = mapped_column(
        Boolean,
        default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )
    playtime_minutes: Mapped[int] = mapped_column(
    Integer,
    default=0
    )
    points: Mapped[int] = mapped_column(
    Integer,
    default=0
    ) 
