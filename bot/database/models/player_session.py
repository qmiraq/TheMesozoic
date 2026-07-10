from datetime import datetime

from sqlalchemy import String, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.models import Base


class PlayerSession(Base):

    __tablename__ = "player_sessions"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    steam_id: Mapped[str] = mapped_column(
        String(30)
    )

    discord_id: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow
    )

    left_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True
    )

    duration_minutes: Mapped[int] = mapped_column(
        Integer,
        default=0
    )