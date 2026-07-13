from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Player
from app.schemas import (
    DifficultyLevel,
    GameOverview,
    PlayerReveal,
    QuestionCategory,
    QuestionHistoryItem,
    QuestionOption,
    RoundStateRead,
    TwistCard,
)

POSITION_LABELS = {
    "goalkeeper": "حارس مرمى",
    "defender": "مدافع",
    "midfielder": "لاعب وسط",
    "forward": "مهاجم",
    "unknown": "غير محدد",
}

ACTIVITY_OPTIONS = [
    QuestionOption(value="active", label="نشط"),
    QuestionOption(value="retired", label="معتزل"),
]

ERA_OPTIONS = [
    QuestionOption(value="before1980", label="قبل 1980"),
    QuestionOption(value="1980s", label="الثمانينيات"),
    QuestionOption(value="1990s", label="التسعينيات"),
    QuestionOption(value="2000plus", label="2000 أو بعده"),
]

POSITION_OPTIONS = [
    QuestionOption(value="goalkeeper", label=POSITION_LABELS["goalkeeper"]),
    QuestionOption(value="defender", label=POSITION_LABELS["defender"]),
    QuestionOption(value="midfielder", label=POSITION_LABELS["midfielder"]),
    QuestionOption(value="forward", label=POSITION_LABELS["forward"]),
]

DIFFICULTY_CONFIG = {
    1: {
        "label": "لفل 1",
        "description": "الأشهر بالعالم.",
        "base_points": 18,
        "max_questions_range": (6, 6),
        "guess_limit": 3,
        "image_mode": "clear",
        "prohibited_count": 1,
        "question_penalty": 1,
    },
    2: {
        "label": "لفل 2",
        "description": "معروفين بس أقل.",
        "base_points": 38,
        "max_questions_range": (6, 6),
        "guess_limit": 3,
        "image_mode": "clear",
        "prohibited_count": 1,
        "question_penalty": 2,
    },
    3: {
        "label": "لفل 3",
        "description": "الأصعب والأقل شهرة بالعالم.",
        "base_points": 62,
        "max_questions_range": (6, 6),
        "guess_limit": 3,
        "image_mode": "clear",
        "prohibited_count": 2,
        "question_penalty": 3,
    },
}

CATEGORY_METADATA = {
    "country": {
        "label": "البلد",
        "description": "اسأل عن بلد اللاعب.",
    },
    "continent": {
        "label": "القارة",
        "description": "اسأل عن القارة.",
    },
    "position": {
        "label": "المركز",
        "description": "اسأل عن مركزه.",
    },
    "activity": {
        "label": "يلعب/اعتزل",
        "description": "اسأل إذا للحين يلعب أو اعتزل.",
    },
    "birth_range": {
        "label": "العمر",
        "description": "اسأل عن عمره بشكل تقريبي.",
    },
}


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    value = value.casefold()
    value = re.sub(r"[^\w\s\u0600-\u06FF]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def birth_range_for_year(year: int) -> str:
    if year < 1980:
        return "before1980"
    if year < 1990:
        return "1980s"
    if year < 2000:
        return "1990s"
    return "2000plus"


def render_question_prompt(category: str, value: str) -> str:
    if category == "country":
        return f"هل يحمل اللاعب جنسية {value}؟"
    if category == "continent":
        return f"هل ينتمي اللاعب إلى قارة {value}؟"
    if category == "position":
        return f"هل مركز اللاعب الأساسي هو {POSITION_LABELS.get(value, value)}؟"
    if category == "activity":
        return "هل اللاعب ما زال نشطًا في كرة القدم؟" if value == "active" else "هل اللاعب معتزل؟"
    if category == "birth_range":
        labels = {option.value: option.label for option in ERA_OPTIONS}
        return f"هل وُلِد اللاعب في {labels.get(value, value)}؟"
    return value


def player_to_reveal(player: Player) -> PlayerReveal:
    return PlayerReveal(
        id=player.id,
        wikidata_id=player.wikidata_id,
        name=player.name,
        name_ar=player.name_ar,
        image_url=player.image_url,
        primary_country=player.countries[0] if player.countries else "",
        primary_country_ar=player.countries_ar[0] if player.countries_ar else "",
        continents=player.continents,
        continents_ar=player.continents_ar,
        birth_year=player.birth_year,
        gender_key=player.gender_key,
        position_group=player.position_group,
        is_active=player.is_active,
        current_team=player.current_team,
        current_team_ar=player.current_team_ar,
        difficulty=player.difficulty,
        fame_score=player.fame_score,
        positions=player.positions,
        positions_ar=player.positions_ar,
    )


def build_question_categories(players: list[Player]) -> list[QuestionCategory]:
    country_options = sorted(
        {
            (label or fallback)
            for player in players
            for label, fallback in zip(player.countries_ar or [], player.countries or [], strict=False)
            if label or fallback
        }
    )
    fallback_country_options = {
        country
        for player in players
        for country in player.countries
        if country
    }
    country_options = country_options or sorted(fallback_country_options)
    if country_options and len(country_options) < len(fallback_country_options):
        country_options = sorted(set(country_options).union(fallback_country_options))

    continent_options = sorted(
        {
            (label or fallback)
            for player in players
            for label, fallback in zip(player.continents_ar or [], player.continents or [], strict=False)
            if label or fallback
        }
    )
    if not continent_options:
        continent_options = sorted(
            {
                continent
                for player in players
                for continent in player.continents
                if continent
            }
        )

    return [
        QuestionCategory(
            key="country",
            label=CATEGORY_METADATA["country"]["label"],
            description=CATEGORY_METADATA["country"]["description"],
            options=[QuestionOption(value=option, label=option) for option in country_options],
        ),
        QuestionCategory(
            key="continent",
            label=CATEGORY_METADATA["continent"]["label"],
            description=CATEGORY_METADATA["continent"]["description"],
            options=[QuestionOption(value=option, label=option) for option in continent_options],
        ),
        QuestionCategory(
            key="position",
            label=CATEGORY_METADATA["position"]["label"],
            description=CATEGORY_METADATA["position"]["description"],
            options=POSITION_OPTIONS,
        ),
        QuestionCategory(
            key="activity",
            label=CATEGORY_METADATA["activity"]["label"],
            description=CATEGORY_METADATA["activity"]["description"],
            options=ACTIVITY_OPTIONS,
        ),
        QuestionCategory(
            key="birth_range",
            label=CATEGORY_METADATA["birth_range"]["label"],
            description=CATEGORY_METADATA["birth_range"]["description"],
            options=ERA_OPTIONS,
        ),
    ]


@dataclass
class RoundState:
    round_id: str
    player_id: int
    difficulty: int
    image_mode: str
    prohibited_categories: list[str]
    max_questions: int
    guess_limit: int
    max_points: int
    question_penalty: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    question_history: list[QuestionHistoryItem] = field(default_factory=list)
    guesses_used: int = 0
    finished: bool = False
    awarded_points: int = 0
    revealed_at: datetime | None = None

    @property
    def questions_remaining(self) -> int:
        return max(self.max_questions - len(self.question_history), 0)

    @property
    def guesses_remaining(self) -> int:
        return max(self.guess_limit - self.guesses_used, 0)

    @property
    def current_points(self) -> int:
        floor_points = max(int(self.max_points * 0.4), 4)
        deducted = len(self.question_history) * self.question_penalty
        return max(self.max_points - deducted, floor_points)


class GameService:
    def __init__(self) -> None:
        self._rounds: dict[str, RoundState] = {}
        self._lock = Lock()

    def get_overview(self, db: Session) -> GameOverview:
        players = db.scalars(select(Player).where(Player.gender_key == "male")).all()
        total_players = len(players)
        active_players = sum(1 for player in players if player.is_active)
        retired_players = total_players - active_players
        represented_countries = len(
            {
                country
                for player in players
                for country in (player.countries_ar or player.countries)
                if country
            }
        )

        difficulty_levels = [
            DifficultyLevel(
                level=level,
                label=config["label"],
                description=config["description"],
                base_points=config["base_points"],
                typical_question_limit=config["max_questions_range"][0],
                typical_guess_limit=config["guess_limit"],
                image_mode=config["image_mode"],
            )
            for level, config in DIFFICULTY_CONFIG.items()
        ]

        return GameOverview(
            total_players=total_players,
            active_players=active_players,
            retired_players=retired_players,
            represented_countries=represented_countries,
            question_categories=build_question_categories(players),
            difficulty_levels=difficulty_levels,
        )

    def create_round(self, db: Session, difficulty: int, recent_player_ids: list[int]) -> tuple[RoundState, Player]:
        config = DIFFICULTY_CONFIG[difficulty]
        candidates = db.scalars(
            select(Player).where(Player.difficulty == difficulty, Player.gender_key == "male")
        ).all()
        filtered_candidates = [player for player in candidates if player.id not in set(recent_player_ids)]
        player = random.choice(filtered_candidates or candidates)

        all_categories = [key for key in CATEGORY_METADATA if self._player_supports_category(player, key)]
        if len(all_categories) <= config["prohibited_count"]:
            prohibited_categories = all_categories[:-1]
        else:
            prohibited_categories = random.sample(all_categories, config["prohibited_count"])

        max_questions = random.randint(*config["max_questions_range"])
        twist_bonus = len(prohibited_categories) * 3 + max(0, 4 - max_questions) * 2
        round_state = RoundState(
            round_id=uuid4().hex,
            player_id=player.id,
            difficulty=difficulty,
            image_mode=config["image_mode"],
            prohibited_categories=prohibited_categories,
            max_questions=max_questions,
            guess_limit=config["guess_limit"],
            max_points=config["base_points"] + twist_bonus,
            question_penalty=config["question_penalty"],
        )

        with self._lock:
            self._rounds[round_state.round_id] = round_state

        return round_state, player

    def get_round(self, round_id: str) -> RoundState:
        round_state = self._rounds.get(round_id)
        if round_state is None:
            raise HTTPException(status_code=404, detail="الجولة مو موجودة.")
        return round_state

    def read_round(self, round_state: RoundState, player: Player) -> RoundStateRead:
        config = DIFFICULTY_CONFIG[round_state.difficulty]
        twist_cards = [
            TwistCard(
                key="yes-no-only",
                label="نعم / لا فقط",
                description="كل الأسئلة تُجاب فقط بنعم أو لا.",
            ),
            TwistCard(
                key="question-cap",
                label=f"{round_state.max_questions} أسئلة فقط",
                description="إذا خلصت الأسئلة، يبقى عليك التخمين.",
            ),
            TwistCard(
                key="guess-cap",
                label=f"{round_state.guess_limit} محاولة تخمين",
                description="هذا عدد التخمينات لك بهالجولة.",
            ),
            TwistCard(
                key="prohibited-categories",
                label="فئات محظورة",
                description="لا يمكنك السؤال في الفئات المحظورة لهذه الجولة.",
            ),
            TwistCard(
                key="image-mode",
                label="الصورة",
                description="الصورة دايم واضحة.",
            ),
        ]

        prohibited_labels = [CATEGORY_METADATA[key]["label"] for key in round_state.prohibited_categories]
        return RoundStateRead(
            round_id=round_state.round_id,
            difficulty=round_state.difficulty,
            difficulty_label=config["label"],
            image_url=player.image_url,
            image_mode=round_state.image_mode,
            twists=twist_cards,
            max_points=round_state.max_points,
            current_points=round_state.current_points,
            questions_remaining=round_state.questions_remaining,
            guesses_remaining=round_state.guesses_remaining,
            prohibited_categories=prohibited_labels,
            prohibited_category_keys=round_state.prohibited_categories,
            question_history=round_state.question_history,
            player_revealed=round_state.finished,
        )

    def ask_question(self, db: Session, round_state: RoundState, category: str, value: str) -> tuple[bool, str]:
        if round_state.finished:
            raise HTTPException(status_code=400, detail="الجولة خلصت.")
        if round_state.questions_remaining <= 0:
            raise HTTPException(status_code=400, detail="ما بقى أسئلة.")
        if category in round_state.prohibited_categories:
            raise HTTPException(status_code=400, detail="هالنوع من الأسئلة مقفول بهالجولة.")

        player = db.get(Player, round_state.player_id)
        if player is None:
            raise HTTPException(status_code=404, detail="اللاعب مو موجود.")
        if not self._player_supports_category(player, category):
            raise HTTPException(status_code=400, detail="هالسؤال ما ينفع لهاللاعب.")

        answer = self._evaluate_question(player, category, value)
        prompt = render_question_prompt(category, value)
        history_item = QuestionHistoryItem(category=category, value=value, prompt=prompt, answer=answer)
        round_state.question_history.append(history_item)
        return answer, prompt

    def submit_guess(self, db: Session, round_state: RoundState, guess: str) -> tuple[bool, str, Player | None]:
        if round_state.finished:
            raise HTTPException(status_code=400, detail="الجولة خلصت.")
        if round_state.guesses_remaining <= 0:
            raise HTTPException(status_code=400, detail="ما بقى تخمينات.")

        player = db.get(Player, round_state.player_id)
        if player is None:
            raise HTTPException(status_code=404, detail="اللاعب مو موجود.")

        round_state.guesses_used += 1
        if self._matches_player(player, guess):
            round_state.finished = True
            round_state.awarded_points = round_state.current_points
            round_state.revealed_at = datetime.now(timezone.utc)
            return True, "إجابة صحيحة!", player

        if round_state.guesses_remaining == 0:
            round_state.finished = True
            round_state.revealed_at = datetime.now(timezone.utc)
            return False, "انتهت محاولات التخمين.", player

        return False, "ليست هذه الإجابة الصحيحة. ما زالت لديك محاولة أخرى.", None

    def reveal(self, db: Session, round_state: RoundState) -> Player:
        player = db.get(Player, round_state.player_id)
        if player is None:
            raise HTTPException(status_code=404, detail="اللاعب مو موجود.")
        round_state.finished = True
        round_state.awarded_points = 0
        round_state.revealed_at = datetime.now(timezone.utc)
        return player

    def _matches_player(self, player: Player, guess: str) -> bool:
        normalized_guess = normalize_text(guess)
        alias_values = {normalize_text(alias) for alias in player.aliases if alias}
        alias_values.add(normalize_text(player.name))
        if player.name_ar:
            alias_values.add(normalize_text(player.name_ar))

        if normalized_guess in alias_values:
            return True

        if len(normalized_guess) >= 4:
            for alias in alias_values:
                parts = [part for part in alias.split(" ") if part]
                if parts and normalized_guess == parts[-1]:
                    return True
                if normalized_guess in alias:
                    return True
        return False

    def _player_supports_category(self, player: Player, category: str) -> bool:
        if category == "country":
            return bool(player.countries or player.countries_ar)
        if category == "continent":
            return bool(player.continents or player.continents_ar)
        if category == "position":
            return player.position_group != "unknown"
        if category == "activity":
            return True
        if category == "birth_range":
            return player.birth_year > 0
        return False

    def _evaluate_question(self, player: Player, category: str, value: str) -> bool:
        normalized_value = normalize_text(value)

        if category == "country":
            player_values = {normalize_text(item) for item in [*player.countries, *player.countries_ar] if item}
            return normalized_value in player_values
        if category == "continent":
            player_values = {normalize_text(item) for item in [*player.continents, *player.continents_ar] if item}
            return normalized_value in player_values
        if category == "position":
            return player.position_group == value
        if category == "activity":
            return player.is_active if value == "active" else not player.is_active
        if category == "birth_range":
            return birth_range_for_year(player.birth_year) == value
        return False


game_service = GameService()
