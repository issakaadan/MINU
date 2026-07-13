from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

QUESTION_CATEGORY_KEYS = {
    "country",
    "continent",
    "position",
    "activity",
    "birth_range",
}
ANSWER_RULE_KEYS = {
    "yes-no-only",
    "no-spelling",
    "one-word-answer",
    "five-second-reply",
    "no-club-hints",
    "single-pass",
}


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class QuestionOption(BaseModel):
    value: str
    label: str


class QuestionCategory(BaseModel):
    key: str
    label: str
    description: str
    options: list[QuestionOption]


class DifficultyLevel(BaseModel):
    level: int
    label: str
    description: str
    base_points: int
    typical_question_limit: int
    typical_guess_limit: int
    image_mode: str


class GameOverview(BaseModel):
    total_players: int
    active_players: int
    retired_players: int
    represented_countries: int
    question_categories: list[QuestionCategory]
    difficulty_levels: list[DifficultyLevel]


class ShareLinkRead(BaseModel):
    public_url: str | None = None


class AuthLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=160)

    @field_validator("username", "password")
    @classmethod
    def normalize_auth_field(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("الحقل مطلوب.")
        return normalized


class AuthSessionRead(BaseModel):
    authenticated: bool
    username: str | None = None


class AdminDifficultyStatRead(BaseModel):
    level: int
    label: str
    description: str
    player_count: int
    fame_min: int
    fame_max: int


class AdminMatchSeatRead(BaseModel):
    seat: int
    player_name: str
    score: int
    rounds_won: int
    current_streak: int


class AdminMatchRead(BaseModel):
    match_id: str
    mode_key: str
    status: str
    winner_seat: int | None = None
    round_number: int
    difficulty: int
    difficulty_label: str
    points_for_win: int
    question_limit: int
    guess_limit: int
    updated_at: datetime
    seats: list[AdminMatchSeatRead]


class AdminRuntimeRead(BaseModel):
    public_base_url: str | None = None
    runtime_root: str
    data_dir: str
    database_path: str
    database_backend: str
    external_database_configured: bool
    dataset_path: str
    dataset_record_count: int
    credentials_file_path: str
    secret_file_path: str
    session_cookie_name: str
    session_ttl_hours: int
    card_link_ttl_hours: int
    database_size_bytes: int


class AdminOverviewRead(BaseModel):
    username: str
    total_players: int
    active_players: int
    retired_players: int
    represented_countries: int
    represented_continents: int
    players_with_images: int
    players_with_arabic_names: int
    total_matches: int
    active_matches: int
    completed_matches: int
    difficulty_stats: list[AdminDifficultyStatRead]
    recent_matches: list[AdminMatchRead]
    catalog_refresh: "AdminCatalogRefreshRead | None" = None
    runtime: AdminRuntimeRead


class AdminPlayerRead(ORMModel):
    id: int
    wikidata_id: str
    name: str
    name_ar: str
    image_url: str
    difficulty: int
    fame_score: int
    birth_year: int
    gender_key: str
    position_group: str
    is_active: bool
    countries: list[str]
    countries_ar: list[str]
    continents: list[str]
    continents_ar: list[str]
    positions: list[str]
    positions_ar: list[str]
    aliases: list[str]
    current_team: str
    current_team_ar: str
    admin_locked: bool = False
    created_at: datetime


class AdminPlayersPageRead(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[AdminPlayerRead]


class AdminPlayerWrite(BaseModel):
    wikidata_id: str = Field(default="", max_length=64)
    name: str = Field(min_length=1, max_length=160)
    name_ar: str = Field(default="", max_length=160)
    image_url: str = Field(min_length=1, max_length=500)
    difficulty: int = Field(ge=1, le=3)
    fame_score: int = Field(ge=0, le=10000)
    birth_year: int = Field(ge=1860, le=2100)
    gender_key: str = Field(default="male", max_length=16)
    position_group: Literal["goalkeeper", "defender", "midfielder", "forward"]
    is_active: bool
    countries: list[str] = Field(default_factory=list, max_length=12)
    countries_ar: list[str] = Field(default_factory=list, max_length=12)
    continents: list[str] = Field(default_factory=list, max_length=8)
    continents_ar: list[str] = Field(default_factory=list, max_length=8)
    positions: list[str] = Field(default_factory=list, max_length=12)
    positions_ar: list[str] = Field(default_factory=list, max_length=12)
    aliases: list[str] = Field(default_factory=list, max_length=24)
    current_team: str = Field(default="", max_length=160)
    current_team_ar: str = Field(default="", max_length=160)
    admin_locked: bool = True

    @field_validator("wikidata_id", "name", "name_ar", "image_url", "gender_key", "current_team", "current_team_ar")
    @classmethod
    def normalize_string_field(cls, value: str) -> str:
        return value.strip()

    @field_validator("countries", "countries_ar", "continents", "continents_ar", "positions", "positions_ar", "aliases")
    @classmethod
    def normalize_string_list(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = value.strip()
            if not cleaned:
                continue
            lowered = cleaned.casefold()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(cleaned)
        return normalized


class AdminPlayerMutationRead(BaseModel):
    player: AdminPlayerRead
    total_players: int


class AdminDeleteRead(BaseModel):
    deleted_id: int
    total_players: int


class AdminCatalogRefreshRead(BaseModel):
    refreshed_at: datetime | None = None
    scanned_players: int
    updated_players: int
    removed_players: int
    locked_players: int
    total_players: int


class AdminAssistantQuestionRead(ORMModel):
    id: int
    intent_key: str
    question_en: str
    question_ar: str
    aliases_en: list[str]
    aliases_ar: list[str]
    argument_kind: str
    enabled: bool
    created_at: datetime


class AdminAssistantQuestionWrite(BaseModel):
    intent_key: str = Field(min_length=1, max_length=64)
    question_en: str = Field(default="", max_length=220)
    question_ar: str = Field(min_length=1, max_length=220)
    aliases_en: list[str] = Field(default_factory=list, max_length=40)
    aliases_ar: list[str] = Field(default_factory=list, max_length=40)
    argument_kind: Literal["", "competition", "team"] = ""
    enabled: bool = True

    @field_validator("intent_key", "question_en", "question_ar")
    @classmethod
    def normalize_assistant_question_field(cls, value: str) -> str:
        return value.strip()

    @field_validator("aliases_en", "aliases_ar")
    @classmethod
    def normalize_assistant_question_aliases(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = value.strip()
            if not cleaned:
                continue
            lowered = cleaned.casefold()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(cleaned)
        return normalized


class AdminAssistantQuestionMutationRead(BaseModel):
    item: AdminAssistantQuestionRead
    total_items: int


class AdminAssistantQuestionsRead(BaseModel):
    total: int
    items: list[AdminAssistantQuestionRead]


class AdminAssistantCompetitionRead(ORMModel):
    id: int
    key: str
    wikidata_id: str
    name_en: str
    name_ar: str
    aliases_en: list[str]
    aliases_ar: list[str]
    enabled: bool
    created_at: datetime


class AdminAssistantCompetitionWrite(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    wikidata_id: str = Field(default="", max_length=32)
    name_en: str = Field(default="", max_length=220)
    name_ar: str = Field(min_length=1, max_length=220)
    aliases_en: list[str] = Field(default_factory=list, max_length=40)
    aliases_ar: list[str] = Field(default_factory=list, max_length=40)
    enabled: bool = True

    @field_validator("key", "wikidata_id", "name_en", "name_ar")
    @classmethod
    def normalize_assistant_competition_field(cls, value: str) -> str:
        return value.strip()

    @field_validator("aliases_en", "aliases_ar")
    @classmethod
    def normalize_assistant_competition_aliases(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = value.strip()
            if not cleaned:
                continue
            lowered = cleaned.casefold()
            if lowered in seen:
                continue
            seen.add(lowered)
            normalized.append(cleaned)
        return normalized


class AdminAssistantCompetitionMutationRead(BaseModel):
    item: AdminAssistantCompetitionRead
    total_items: int


class AdminAssistantCompetitionsRead(BaseModel):
    total: int
    items: list[AdminAssistantCompetitionRead]


class AdminAssistantDeleteRead(BaseModel):
    deleted_id: int
    total_items: int


class StartRoundRequest(BaseModel):
    difficulty: int = Field(ge=1, le=3)
    recent_player_ids: list[int] = Field(default_factory=list, max_length=24)


class TwistCard(BaseModel):
    key: str
    label: str
    description: str


class QuestionHistoryItem(BaseModel):
    category: str
    value: str
    prompt: str
    answer: bool


class PlayerReveal(BaseModel):
    id: int
    wikidata_id: str
    name: str
    name_ar: str
    image_url: str
    primary_country: str
    primary_country_ar: str
    continents: list[str]
    continents_ar: list[str]
    birth_year: int
    gender_key: str
    position_group: str
    is_active: bool
    current_team: str
    current_team_ar: str
    difficulty: int
    fame_score: int
    positions: list[str]
    positions_ar: list[str]


class RoundStateRead(BaseModel):
    round_id: str
    difficulty: int
    difficulty_label: str
    image_url: str
    image_mode: str
    twists: list[TwistCard]
    max_points: int
    current_points: int
    questions_remaining: int
    guesses_remaining: int
    prohibited_categories: list[str]
    prohibited_category_keys: list[str]
    question_history: list[QuestionHistoryItem]
    player_revealed: bool


class AskQuestionRequest(BaseModel):
    category: Literal["country", "continent", "position", "activity", "birth_range"]
    value: str = Field(min_length=1, max_length=120)

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Question value is required.")
        return normalized


class AskQuestionResponse(BaseModel):
    answer: bool
    answer_label: str
    prompt: str
    questions_remaining: int
    current_points: int
    question_history: list[QuestionHistoryItem]


class GuessRequest(BaseModel):
    guess: str = Field(min_length=1, max_length=160)

    @field_validator("guess")
    @classmethod
    def normalize_guess(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A guess is required.")
        return normalized


class GuessResponse(BaseModel):
    correct: bool
    message: str
    awarded_points: int
    current_points: int
    guesses_remaining: int
    round_finished: bool
    player: PlayerReveal | None = None


class RevealResponse(BaseModel):
    message: str
    player: PlayerReveal
    question_history: list[QuestionHistoryItem]
    created_at: datetime


class MatchCreateRequest(BaseModel):
    difficulty: int = Field(ge=1, le=3)
    mode_key: str = Field(min_length=1, max_length=64)
    player_names: list[str] = Field(
        default_factory=lambda: ["اللاعب 1", "اللاعب 2"],
        min_length=2,
        max_length=2,
    )
    recent_player_ids: list[int] = Field(default_factory=list, max_length=600)
    selected_answer_rule_keys: list[str] = Field(default_factory=list, max_length=6)
    selected_prohibited_category_keys: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("mode_key")
    @classmethod
    def normalize_mode_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Mode key is required.")
        return normalized

    @field_validator("player_names")
    @classmethod
    def normalize_player_names(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for index, value in enumerate(values[:2], start=1):
            cleaned = value.strip()
            normalized.append(cleaned or f"اللاعب {index}")
        if len(normalized) != 2:
            raise ValueError("لازم اسمين.")
        return normalized

    @field_validator("selected_answer_rule_keys")
    @classmethod
    def normalize_answer_rule_keys(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            cleaned = value.strip()
            if cleaned not in ANSWER_RULE_KEYS:
                raise ValueError("فيه تويست جواب مو معروف.")
            if cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    @field_validator("selected_prohibited_category_keys")
    @classmethod
    def normalize_prohibited_category_keys(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            cleaned = value.strip()
            if cleaned not in QUESTION_CATEGORY_KEYS:
                raise ValueError("فيه نوع سؤال مو معروف.")
            if cleaned not in normalized:
                normalized.append(cleaned)
        return normalized


class MatchSeatRead(BaseModel):
    seat: int
    player_id: int
    player_name: str
    score: int
    rounds_won: int
    current_streak: int


class MatchRoundRead(BaseModel):
    round_number: int
    difficulty: int
    difficulty_label: str
    starting_seat: int
    image_mode: str
    points_for_win: int
    question_limit: int
    guess_limit: int
    twist_keys: list[str]
    answer_rule_keys: list[str]
    prohibited_category_keys: list[str]
    allowed_category_keys: list[str]
    awarded_to: int | None = None
    resolved: bool = False


class MatchRead(BaseModel):
    match_id: str
    match_token: str
    recent_player_ids: list[int] = []
    mode_key: str
    status: str
    winner_seat: int | None = None
    seats: list[MatchSeatRead]
    round: MatchRoundRead
    updated_at: datetime


class AwardRoundRequest(BaseModel):
    seat: int = Field(ge=1, le=2)


class PlayerSecretRead(BaseModel):
    match_id: str
    mode_key: str
    status: str
    winner_seat: int | None = None
    seat: int
    player_name: str
    opponent_name: str
    round: MatchRoundRead
    player: PlayerReveal
    wikipedia_url: str
    updated_at: datetime


class PlayerCardTokenRead(BaseModel):
    token: str


class CardAssistantQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    language: Literal["ar", "en"] = "ar"

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Question is required.")
        return normalized


class CardAssistantTraceRead(BaseModel):
    source_kind: str
    source_title: str
    source_language: str
    source_excerpt: str
    score: int | None = None


class CardAssistantAnswerRead(BaseModel):
    answer: str
    intent_key: str | None = None
    used_sources: list[str] = Field(default_factory=list)
    matched_argument: str | None = None
    trace: CardAssistantTraceRead | None = None


class SharedPlayerCardRead(BaseModel):
    m: str
    r: int
    s: int
    pn: str
    on: str
    mk: str
    n: str
    na: str
    i: str
    c: str
    ce: str
    p: str
    y: int
    a: int
    ct: str
    cta: str
    wd: str = ""
