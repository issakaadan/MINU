from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    wikidata_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    name_ar: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    image_url: Mapped[str] = mapped_column(String(500), nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    fame_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    birth_year: Mapped[int] = mapped_column(Integer, nullable=False)
    gender_key: Mapped[str] = mapped_column(String(16), default="male", nullable=False)
    position_group: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    countries: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    countries_ar: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    continents: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    continents_ar: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    positions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    positions_ar: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    current_team: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    current_team_ar: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AssistantQuestionTemplate(Base):
    __tablename__ = "assistant_question_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    intent_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    question_en: Mapped[str] = mapped_column(String(220), default="", nullable=False)
    question_ar: Mapped[str] = mapped_column(String(220), default="", nullable=False)
    aliases_en: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    aliases_ar: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    argument_kind: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AssistantCompetition(Base):
    __tablename__ = "assistant_competitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    wikidata_id: Mapped[str] = mapped_column(String(32), default="", nullable=False, index=True)
    name_en: Mapped[str] = mapped_column(String(220), default="", nullable=False)
    name_ar: Mapped[str] = mapped_column(String(220), default="", nullable=False)
    aliases_en: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    aliases_ar: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
