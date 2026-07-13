from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from urllib.parse import quote_plus
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import auth_manager
from app.game_service import DIFFICULTY_CONFIG, player_to_reveal
from app.models import Player
from app.schemas import MatchRead, MatchRoundRead, MatchSeatRead, PlayerSecretRead

MATCH_MODES = {
    "race-to-100": {
        "required_score": 100,
        "required_round_wins": None,
        "required_streak": None,
        "bonus_points": 0,
    },
    "lightning-rush": {
        "required_score": None,
        "required_round_wins": 3,
        "required_streak": None,
        "bonus_points": 5,
    },
    "hot-streak": {
        "required_score": None,
        "required_round_wins": None,
        "required_streak": 3,
        "bonus_points": 8,
    },
    "best-of-five": {
        "required_score": None,
        "required_round_wins": 3,
        "required_streak": None,
        "bonus_points": 0,
    },
    "marathon-180": {
        "required_score": 180,
        "required_round_wins": None,
        "required_streak": None,
        "bonus_points": 0,
    },
}

CATEGORY_KEYS = ("country", "continent", "position", "activity", "birth_range")
CAREER_START_AGE = 18
CAREER_LENGTH_YEARS = 18
MIN_SHARED_ERA_YEARS = 4
MAX_BIRTH_YEAR_GAP = 12
PLAYER_SELECTION_ATTEMPTS = 120


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MatchSeatState:
    seat: int
    player_name: str
    score: int = 0
    rounds_won: int = 0
    current_streak: int = 0


@dataclass
class MatchRoundState:
    round_number: int
    difficulty: int
    player_ids: dict[int, int]
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


@dataclass
class MatchState:
    match_id: str
    mode_key: str
    seats: dict[int, MatchSeatState]
    round_state: MatchRoundState
    selected_answer_rule_keys: list[str] = field(default_factory=list)
    selected_prohibited_category_keys: list[str] = field(default_factory=list)
    recent_player_ids: list[int] = field(default_factory=list)
    recent_player_keys: list[str] = field(default_factory=list)
    status: str = "active"
    winner_seat: int | None = None
    updated_at: datetime = field(default_factory=utc_now)


class MatchService:
    def __init__(self) -> None:
        self._matches: dict[str, MatchState] = {}
        self._lock = RLock()

    def _resolve_active_state(self, match_state: MatchState) -> MatchState:
        cached = self._matches.get(match_state.match_id)
        return cached if cached is not None else match_state

    def _assert_match_state_consistency(self, state: MatchState, fallback: MatchState) -> MatchState:
        if state.match_id != fallback.match_id:
            raise HTTPException(status_code=404, detail="Ù„Ù…Ø¨Ø§Ø±Ø§Ø© Ù…Ùˆ Ù…ÙˆØ¬ÙˆØ¯Ø©.")

        return fallback

    def _cache_match(self, match_state: MatchState) -> MatchState:
        with self._lock:
            self._matches[match_state.match_id] = match_state
        return match_state

    def create_match(
        self,
        db: Session,
        difficulty: int,
        mode_key: str,
        player_names: list[str],
        recent_player_ids: list[int],
        recent_player_keys: list[str],
        selected_answer_rule_keys: list[str],
        selected_prohibited_category_keys: list[str],
    ) -> MatchState:
        if mode_key not in MATCH_MODES:
            raise HTTPException(status_code=400, detail="المود مو معروف.")

        round_state, used_player_ids, used_player_keys = self._build_round(
            db=db,
            difficulty=difficulty,
            mode_key=mode_key,
            recent_player_ids=recent_player_ids,
            recent_player_keys=recent_player_keys,
            round_number=1,
            selected_answer_rule_keys=selected_answer_rule_keys,
            selected_prohibited_category_keys=selected_prohibited_category_keys,
        )
        match_state = MatchState(
            match_id=uuid4().hex,
            mode_key=mode_key,
            seats={
                1: MatchSeatState(seat=1, player_name=player_names[0]),
                2: MatchSeatState(seat=2, player_name=player_names[1]),
            },
            round_state=round_state,
            selected_answer_rule_keys=selected_answer_rule_keys,
            selected_prohibited_category_keys=selected_prohibited_category_keys,
            recent_player_ids=used_player_ids,
            recent_player_keys=used_player_keys,
        )
        return self._cache_match(match_state)

    def get_match(self, match_id: str, match_token: str | None = None) -> MatchState:
        with self._lock:
            cached = self._matches.get(match_id)
        if cached is not None:
            return cached

        if match_token:
            restored = self._read_token_match(match_id, match_token)
            if restored is not None:
                return self._cache_match(restored)

        raise HTTPException(status_code=404, detail="المباراة مو موجودة.")

    def list_matches(self) -> list[MatchState]:
        with self._lock:
            return sorted(
                self._matches.values(),
                key=lambda match_state: match_state.updated_at,
                reverse=True,
            )

    def read_match(self, match_state: MatchState) -> MatchRead:
        return MatchRead(
            match_id=match_state.match_id,
            match_token=self._create_match_token(match_state),
            recent_player_ids=match_state.recent_player_ids,
            mode_key=match_state.mode_key,
            status=match_state.status,
            winner_seat=match_state.winner_seat,
            seats=[
                MatchSeatRead(
                    seat=seat_state.seat,
                    player_id=match_state.round_state.player_ids[seat_state.seat],
                    player_name=seat_state.player_name,
                    score=seat_state.score,
                    rounds_won=seat_state.rounds_won,
                    current_streak=seat_state.current_streak,
                )
                for seat_state in match_state.seats.values()
            ],
            round=self._serialize_round(match_state.round_state),
            updated_at=match_state.updated_at,
        )

    def award_round(self, match_state: MatchState, seat: int) -> MatchState:
        if match_state.status != "active":
            raise HTTPException(status_code=400, detail="المباراة خلصت.")
        if match_state.round_state.resolved:
            raise HTTPException(status_code=400, detail="الجولة محسوبة من قبل.")
        if seat not in match_state.seats:
            raise HTTPException(status_code=404, detail="مكان اللاعب مو موجود.")

        winner_state = match_state.seats[seat]
        loser_state = match_state.seats[1 if seat == 2 else 2]

        winner_state.score += match_state.round_state.points_for_win
        winner_state.rounds_won += 1
        winner_state.current_streak += 1
        loser_state.current_streak = 0

        match_state.round_state.awarded_to = seat
        match_state.round_state.resolved = True
        match_state.updated_at = utc_now()
        self._update_match_outcome(match_state)
        return self._cache_match(match_state)

    def mark_round_unanswered(self, match_state: MatchState) -> MatchState:
        if match_state.status != "active":
            raise HTTPException(status_code=400, detail="المباراة خلصت.")
        if match_state.round_state.resolved:
            raise HTTPException(status_code=400, detail="الجولة محسوبة من قبل.")

        for seat_state in match_state.seats.values():
            seat_state.current_streak = 0

        match_state.round_state.awarded_to = None
        match_state.round_state.resolved = True
        match_state.updated_at = utc_now()
        self._update_match_outcome(match_state)
        return self._cache_match(match_state)

    def next_round(self, db: Session, match_state: MatchState) -> MatchState:
        if match_state.status != "active":
            raise HTTPException(status_code=400, detail="المباراة خلصت.")

        if not match_state.round_state.resolved:
            raise HTTPException(status_code=400, detail="حدد مين خذها أول أو اختر ولا حد جاوب.")

        current_round_player_ids = [*match_state.round_state.player_ids.values()]
        current_round_players = db.scalars(
            select(Player).where(Player.id.in_(current_round_player_ids))
        ).all()
        current_round_player_keys: list[str] = []
        for current_player in current_round_players:
            current_round_player_keys.extend(self._tracking_keys(current_player))

        next_round_recent_ids = self._merge_used_player_ids(
            match_state.recent_player_ids,
            current_round_player_ids,
        )
        next_round_recent_keys = self._merge_used_player_keys(
            match_state.recent_player_keys,
            current_round_player_keys,
        )

        next_round_number = match_state.round_state.round_number + 1
        round_state, used_player_ids, used_player_keys = self._build_round(
            db=db,
            difficulty=match_state.round_state.difficulty,
            mode_key=match_state.mode_key,
            recent_player_ids=next_round_recent_ids,
            recent_player_keys=next_round_recent_keys,
            round_number=next_round_number,
            selected_answer_rule_keys=match_state.selected_answer_rule_keys,
            selected_prohibited_category_keys=match_state.selected_prohibited_category_keys,
        )
        match_state.round_state = round_state
        match_state.recent_player_ids = used_player_ids
        match_state.recent_player_keys = used_player_keys
        match_state.updated_at = utc_now()
        return self._cache_match(match_state)

    def end_match(self, match_state: MatchState) -> MatchState:
        if match_state.status != "active":
            return self._cache_match(match_state)

        match_state.status = "completed"
        match_state.winner_seat = None
        match_state.updated_at = utc_now()
        return self._cache_match(match_state)

    def read_secret(self, db: Session, match_state: MatchState, seat: int) -> PlayerSecretRead:
        if seat not in match_state.seats:
            raise HTTPException(status_code=404, detail="مكان اللاعب مو موجود.")

        player_id = match_state.round_state.player_ids[seat]
        player = db.get(Player, player_id)
        if player is None:
            raise HTTPException(status_code=404, detail="اللاعب مو موجود.")

        seat_state = match_state.seats[seat]
        opponent_state = match_state.seats[1 if seat == 2 else 2]
        wikipedia_url = f"https://en.wikipedia.org/wiki/Special:Search?search={quote_plus(player.name)}"

        return PlayerSecretRead(
            match_id=match_state.match_id,
            mode_key=match_state.mode_key,
            status=match_state.status,
            winner_seat=match_state.winner_seat,
            seat=seat,
            player_name=seat_state.player_name,
            opponent_name=opponent_state.player_name,
            round=self._serialize_round(match_state.round_state),
            player=player_to_reveal(player),
            wikipedia_url=wikipedia_url,
            updated_at=match_state.updated_at,
        )

    def _create_match_token(self, match_state: MatchState) -> str:
        return auth_manager.create_match_token(self._serialize_match_state(match_state))

    def _read_token_match(self, match_id: str, match_token: str) -> MatchState | None:
        payload = auth_manager.read_match_token(match_token)
        if payload is None:
            return None

        token_match_id = str(payload.get("match_id") or "")
        if token_match_id != match_id:
            return None

        try:
            return self._deserialize_match_state(payload)
        except (KeyError, TypeError, ValueError):
            return None

    def _serialize_match_state(self, match_state: MatchState) -> dict[str, object]:
        return {
            "match_id": match_state.match_id,
            "mode_key": match_state.mode_key,
            "selected_answer_rule_keys": match_state.selected_answer_rule_keys,
            "selected_prohibited_category_keys": match_state.selected_prohibited_category_keys,
            "recent_player_ids": match_state.recent_player_ids,
            "recent_player_keys": match_state.recent_player_keys,
            "status": match_state.status,
            "winner_seat": match_state.winner_seat,
            "updated_at": match_state.updated_at.isoformat(),
            "seats": [
                {
                    "seat": seat_state.seat,
                    "player_name": seat_state.player_name,
                    "score": seat_state.score,
                    "rounds_won": seat_state.rounds_won,
                    "current_streak": seat_state.current_streak,
                }
                for seat_state in match_state.seats.values()
            ],
            "round_state": {
                "round_number": match_state.round_state.round_number,
                "difficulty": match_state.round_state.difficulty,
                "player_ids": match_state.round_state.player_ids,
                "starting_seat": match_state.round_state.starting_seat,
                "image_mode": match_state.round_state.image_mode,
                "points_for_win": match_state.round_state.points_for_win,
                "question_limit": match_state.round_state.question_limit,
                "guess_limit": match_state.round_state.guess_limit,
                "twist_keys": match_state.round_state.twist_keys,
                "answer_rule_keys": match_state.round_state.answer_rule_keys,
                "prohibited_category_keys": match_state.round_state.prohibited_category_keys,
                "allowed_category_keys": match_state.round_state.allowed_category_keys,
                "awarded_to": match_state.round_state.awarded_to,
                "resolved": match_state.round_state.resolved,
            },
        }

    def _deserialize_match_state(self, payload: dict[str, object]) -> MatchState:
        seats_payload = payload["seats"]
        round_payload = payload["round_state"]
        if not isinstance(seats_payload, list) or not isinstance(round_payload, dict):
            raise ValueError("bad match payload")

        seats: dict[int, MatchSeatState] = {}
        for seat_payload in seats_payload:
            if not isinstance(seat_payload, dict):
                continue
            seat_number = int(seat_payload["seat"])
            seats[seat_number] = MatchSeatState(
                seat=seat_number,
                player_name=str(seat_payload["player_name"]),
                score=int(seat_payload.get("score", 0)),
                rounds_won=int(seat_payload.get("rounds_won", 0)),
                current_streak=int(seat_payload.get("current_streak", 0)),
            )

        player_ids_payload = round_payload["player_ids"]
        if not isinstance(player_ids_payload, dict):
            raise ValueError("bad player ids")

        return MatchState(
            match_id=str(payload["match_id"]),
            mode_key=str(payload["mode_key"]),
            seats=seats,
            round_state=MatchRoundState(
                round_number=int(round_payload["round_number"]),
                difficulty=int(round_payload["difficulty"]),
                player_ids={int(key): int(value) for key, value in player_ids_payload.items()},
                starting_seat=int(round_payload["starting_seat"]),
                image_mode=str(round_payload["image_mode"]),
                points_for_win=int(round_payload["points_for_win"]),
                question_limit=int(round_payload["question_limit"]),
                guess_limit=int(round_payload["guess_limit"]),
                twist_keys=[str(value) for value in round_payload.get("twist_keys", [])],
                answer_rule_keys=[str(value) for value in round_payload.get("answer_rule_keys", [])],
                prohibited_category_keys=[
                    str(value) for value in round_payload.get("prohibited_category_keys", [])
                ],
                allowed_category_keys=[
                    str(value) for value in round_payload.get("allowed_category_keys", [])
                ],
                awarded_to=(
                    int(round_payload["awarded_to"])
                    if round_payload.get("awarded_to") is not None
                    else None
                ),
                resolved=bool(
                    round_payload.get(
                        "resolved",
                        round_payload.get("awarded_to") is not None,
                    )
                ),
            ),
            recent_player_ids=self._normalize_and_unique_ids(
                [int(value) for value in payload.get("recent_player_ids", [])]
                or [int(value) for value in player_ids_payload.values()]
            ),
            recent_player_keys=[str(value) for value in payload.get("recent_player_keys", [])],
            selected_answer_rule_keys=[
                str(value) for value in payload.get("selected_answer_rule_keys", [])
            ],
            selected_prohibited_category_keys=[
                str(value) for value in payload.get("selected_prohibited_category_keys", [])
            ],
            status=str(payload.get("status", "active")),
            winner_seat=int(payload["winner_seat"]) if payload.get("winner_seat") is not None else None,
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
        )

    def _normalize_and_unique_ids(self, ids: list[int]) -> list[int]:
        seen: set[int] = set()
        normalized: list[int] = []
        for player_id in ids:
            try:
                player_id_value = int(player_id)
            except (TypeError, ValueError):
                continue
            if player_id_value in seen:
                continue
            seen.add(player_id_value)
            normalized.append(player_id_value)
        return normalized

    def _normalize_and_unique_values(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for value in values:
            normalized_value = self._normalize_signature(str(value))
            if not normalized_value or normalized_value in seen:
                continue
            seen.add(normalized_value)
            normalized.append(normalized_value)
        return normalized

    def _refresh_recent_player_keys(self, db: Session, player_ids: list[int], player_keys: list[str]) -> list[str]:
        missing_keys = self._normalize_and_unique_values(player_keys)
        if missing_keys:
            return missing_keys

        if not player_ids:
            return []

        players = db.scalars(select(Player).where(Player.id.in_(player_ids))).all()
        if not players:
            return missing_keys

        return self._normalize_and_unique_values(
            [self._normalize_signature(key) for player in players for key in self._tracking_keys(player)]
        )

    def _build_round(
        self,
        db: Session,
        difficulty: int,
        mode_key: str,
        recent_player_ids: list[int],
        recent_player_keys: list[str],
        round_number: int,
        selected_answer_rule_keys: list[str],
        selected_prohibited_category_keys: list[str],
    ) -> tuple[MatchRoundState, list[int], list[str]]:
        config = DIFFICULTY_CONFIG[difficulty]
        recent_player_keys = self._refresh_recent_player_keys(
            db,
            player_ids=recent_player_ids,
            player_keys=recent_player_keys,
        )
        players = self._pick_players_v2(
            db=db,
            difficulty=difficulty,
            recent_player_ids=recent_player_ids,
            recent_player_keys=recent_player_keys,
        )
        self._assert_pair_not_recycled(players, recent_player_ids)
        self._validate_round_players(players, recent_player_ids, recent_player_keys)
        shared_category_keys = self._shared_category_keys(players)

        prohibited_category_keys = [
            key for key in selected_prohibited_category_keys if key in shared_category_keys
        ]
        allowed_category_keys = [
            key for key in shared_category_keys if key not in prohibited_category_keys
        ]
        question_limit = random.randint(*config["max_questions_range"])
        guess_limit = config["guess_limit"]
        answer_rule_keys = selected_answer_rule_keys[:]
        twist_keys = self._build_twist_keys(answer_rule_keys, prohibited_category_keys)

        points_for_win = (
            config["base_points"]
            + len(answer_rule_keys) * 4
            + len(prohibited_category_keys) * 3
            + max(0, 5 - question_limit) * 2
            + MATCH_MODES[mode_key]["bonus_points"]
        )

        round_state = MatchRoundState(
            round_number=round_number,
            difficulty=difficulty,
            player_ids={1: players[0].id, 2: players[1].id},
            starting_seat=random.choice([1, 2]),
            image_mode=config["image_mode"],
            points_for_win=points_for_win,
            question_limit=question_limit,
            guess_limit=guess_limit,
            twist_keys=twist_keys,
            answer_rule_keys=answer_rule_keys,
            prohibited_category_keys=prohibited_category_keys,
            allowed_category_keys=allowed_category_keys,
        )

        used_player_ids = self._merge_used_player_ids(recent_player_ids, [players[0].id, players[1].id])
        used_player_keys = self._merge_used_player_keys(
            recent_player_keys,
            [*self._tracking_keys(players[0]), *self._tracking_keys(players[1])],
        )
        return round_state, used_player_ids, used_player_keys

    def _player_signature(self, player: Player) -> str:
        if player.wikidata_id:
            return self._normalize_signature(f"wikidata:{player.wikidata_id}")

        normalized_name = self._normalize_signature(player.name)
        normalized_birth = str(player.birth_year or 0)
        return self._normalize_signature(
            f"{normalized_name}|{normalized_birth}|{player.position_group}"
        )

    def _tracking_keys(self, player: Player) -> set[str]:
        return {
            key
            for key in (
                self._normalize_signature(self._player_signature(player)),
                *self._identity_keys(player),
            )
            if key
        }

    def _identity_keys(self, player: Player) -> set[str]:
        keys: set[str] = {
            self._normalize_signature(player.name),
            self._normalize_signature(player.name_ar),
        }
        if player.wikidata_id:
            keys.add(self._normalize_signature(f"wikidata:{player.wikidata_id}"))
        if player.birth_year:
            keys.add(self._normalize_signature(f"birth:{player.birth_year}"))
        keys.update(
            self._normalize_signature(f"alias:{alias}")
            for alias in [*player.aliases, player.name_ar]
            if self._normalize_signature(alias)
        )
        return {key for key in keys if key}

    def _is_disallowed_player(
        self,
        player: Player,
        used_player_ids: set[int],
        used_player_keys: set[str],
    ) -> bool:
        if player.id in used_player_ids:
            return True

        if self._normalize_signature(self._player_signature(player)) in used_player_keys:
            return True

        if self._identity_keys(player) & used_player_keys:
            return True

        return False

    def _is_valid_pair(self, players: list[Player]) -> bool:
        if len(players) != 2:
            return False

        first, second = players
        if first.id == second.id:
            return False

        if self._player_signature(first) == self._player_signature(second):
            return False

        if self._normalize_signature(first.image_url) == self._normalize_signature(second.image_url):
            return False

        if first.name == second.name and first.position_group == second.position_group:
            return False

        return True

    def _dedupe_players(self, players: list[Player]) -> list[Player]:
        seen: set[str] = set()
        deduped: list[Player] = []
        for player in players:
            signature = self._player_signature(player)
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(player)
        return deduped

    def _build_close_pairs(self, players: list[Player]) -> list[tuple[Player, Player]]:
        sorted_by_birth = sorted(players, key=lambda player: player.birth_year if player.birth_year > 0 else 9999)
        pairs: list[tuple[Player, Player]] = []
        for index, left in enumerate(sorted_by_birth[:-1]):
            for right in sorted_by_birth[index + 1 :]:
                pairs.append((left, right))
        pairs.sort(key=lambda pair: abs((pair[0].birth_year or 0) - (pair[1].birth_year or 0)))
        return pairs

    def _validate_round_players(
        self,
        players: list[Player],
        recent_player_ids: list[int],
        recent_player_keys: list[str],
    ) -> None:
        if not self._is_valid_pair(players):
            raise HTTPException(status_code=400, detail="Could not find two distinct players in this match.")

        used_player_ids = set(recent_player_ids)
        used_player_keys = {self._normalize_signature(value) for value in recent_player_keys}
        if players[0].id in used_player_ids or players[1].id in used_player_ids:
            raise HTTPException(
                status_code=400,
                detail="Could not generate a unique player pair. Please start a new game.",
            )
        if self._player_signature(players[0]) in used_player_keys or self._player_signature(players[1]) in used_player_keys:
            raise HTTPException(
                status_code=400,
                detail="Could not generate a unique player pair. Please start a new game.",
            )
        if self._identity_keys(players[0]) & used_player_keys or self._identity_keys(players[1]) & used_player_keys:
            raise HTTPException(
                status_code=400,
                detail="Could not generate a unique player pair. Please start a new game.",
            )

    def _pick_players(
        self,
        db: Session,
        difficulty: int,
        recent_player_ids: list[int],
        recent_player_keys: list[str],
    ) -> list[Player]:
        candidates = db.scalars(
            select(Player).where(Player.difficulty == difficulty, Player.gender_key == "male")
        ).all()
        used_player_ids = set(recent_player_ids)
        used_player_keys = {self._normalize_signature(value) for value in recent_player_keys}
        source = self._require_enough_players_for_match(
            [
                player
                for player in candidates
                if not self._is_disallowed_player(
                    player,
                    used_player_ids=used_player_ids,
                    used_player_keys=used_player_keys,
                )
            ]
        )
        if len(source) < 2:
            raise HTTPException(status_code=400, detail="ما فيه لاعبين كفاية لهاللفل.")

        shuffled_players = source[:]
        random.shuffle(shuffled_players)
        for anchor in shuffled_players:
            same_era_players = [
                candidate
                for candidate in shuffled_players
                if candidate.id != anchor.id and self._players_share_era(anchor, candidate)
            ]
            if same_era_players:
                pair = [anchor, random.choice(same_era_players)]
                if pair[0].id == pair[1].id or self._player_signature(pair[0]) == self._player_signature(pair[1]):
                    continue
                return pair

        sorted_by_birth = sorted(
            shuffled_players,
            key=lambda player: player.birth_year if player.birth_year > 0 else 9999,
        )
        closest_pair = min(
            (
                (left, right)
                for index, left in enumerate(sorted_by_birth[:-1])
                for right in sorted_by_birth[index + 1 :]
            ),
            key=lambda pair: abs((pair[0].birth_year or 0) - (pair[1].birth_year or 0)),
        )
        if (
            closest_pair[0].id == closest_pair[1].id
            or self._player_signature(closest_pair[0]) == self._player_signature(closest_pair[1])
        ):
            raise HTTPException(status_code=400, detail="Could not find two distinct players in this match.")

        return [closest_pair[0], closest_pair[1]]

    def _pick_players_v2(
        self,
        db: Session,
        difficulty: int,
        recent_player_ids: list[int],
        recent_player_keys: list[str],
    ) -> list[Player]:
        candidates = db.scalars(
            select(Player).where(Player.difficulty == difficulty, Player.gender_key == "male")
        ).all()
        used_player_ids = set(recent_player_ids)
        used_player_keys = {self._normalize_signature(value) for value in recent_player_keys}
        source = self._dedupe_players(
            self._require_enough_players_for_match(
                [
                    player
                    for player in candidates
                    if not self._is_disallowed_player(
                        player,
                        used_player_ids=used_player_ids,
                        used_player_keys=used_player_keys,
                    )
                ]
            )
        )
        if len(source) < 2:
            raise HTTPException(status_code=400, detail="Ù…Ø§ ÙÙŠÙ‡ Ù„Ø§Ø¹Ø¨ÙŠÙ† ÙƒÙØ§ÙŠØ© Ù„Ù‡Ø§Ù„Ù„ÙÙ„.")

        shuffled_players = source[:]
        for _ in range(PLAYER_SELECTION_ATTEMPTS):
            random.shuffle(shuffled_players)

            for anchor in shuffled_players:
                same_era_players = [
                    candidate
                    for candidate in shuffled_players
                    if self._players_share_era(anchor, candidate)
                ]
                for candidate in same_era_players:
                    if self._is_valid_pair([anchor, candidate]):
                        return [anchor, candidate]

            for first, second in self._build_close_pairs(shuffled_players):
                if self._is_valid_pair([first, second]):
                    return [first, second]

        raise HTTPException(status_code=400, detail="Could not find two distinct players in this match.")

    def _merge_used_player_ids(self, existing_ids: list[int], new_ids: list[int]) -> list[int]:
        merged: list[int] = []
        seen_ids: set[int] = set()
        for player_id in [*existing_ids, *new_ids]:
            if player_id in seen_ids:
                continue
            seen_ids.add(player_id)
            merged.append(player_id)
        return merged

    def _normalize_signature(self, value: str) -> str:
        normalized = (value or "").strip().lower()
        normalized = re.sub(r"[^\w\d]+", " ", normalized, flags=re.UNICODE)
        return re.sub(r"\s+", " ", normalized).strip()

    def _merge_used_player_keys(self, existing_keys: list[str], new_keys: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for key in [*existing_keys, *new_keys]:
            normalized_key = self._normalize_signature(key)
            if not normalized_key or normalized_key in seen:
                continue
            seen.add(normalized_key)
            merged.append(normalized_key)
        return merged

    def _require_enough_players_for_match(self, candidates: list[Player]) -> list[Player]:
        if len(candidates) < 2:
            raise HTTPException(
                status_code=400,
                detail="خلصوا لاعبين هاللفل بهالمباراة. غيّر اللفل أو ابدأ مباراة جديدة.",
            )
        return candidates

    def _shared_category_keys(self, players: list[Player]) -> list[str]:
        shared: list[str] = []
        for key in CATEGORY_KEYS:
            if all(self._player_supports_category(player, key) for player in players):
                shared.append(key)

        if len(shared) >= 2:
            return shared
        return list(CATEGORY_KEYS)

    def _build_twist_keys(
        self,
        answer_rule_keys: list[str],
        prohibited_category_keys: list[str],
    ) -> list[str]:
        twist_keys = ["question-cap", "guess-cap", *answer_rule_keys]
        if prohibited_category_keys:
            twist_keys.append("blocked-categories")
        twist_keys.append("notes-open")
        deduped: list[str] = []
        for key in twist_keys:
            if key not in deduped:
                deduped.append(key)
        return deduped

    def _serialize_round(self, round_state: MatchRoundState) -> MatchRoundRead:
        difficulty_label = DIFFICULTY_CONFIG[round_state.difficulty]["label"]
        return MatchRoundRead(
            round_number=round_state.round_number,
            difficulty=round_state.difficulty,
            difficulty_label=difficulty_label,
            starting_seat=round_state.starting_seat,
            image_mode=round_state.image_mode,
            points_for_win=round_state.points_for_win,
            question_limit=round_state.question_limit,
            guess_limit=round_state.guess_limit,
            twist_keys=round_state.twist_keys,
            answer_rule_keys=round_state.answer_rule_keys,
            prohibited_category_keys=round_state.prohibited_category_keys,
            allowed_category_keys=round_state.allowed_category_keys,
            awarded_to=round_state.awarded_to,
            resolved=round_state.resolved,
        )

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

    def _career_window(self, player: Player) -> tuple[int, int]:
        if player.birth_year <= 0:
            current_year = utc_now().year
            return current_year - 8, current_year + 8

        start_year = player.birth_year + CAREER_START_AGE
        end_year = player.birth_year + CAREER_START_AGE + CAREER_LENGTH_YEARS
        if player.is_active:
            end_year = max(end_year, utc_now().year)
        return start_year, end_year

    def _assert_pair_not_recycled(self, players: list[Player], recent_player_ids: list[int]) -> None:
        if len(players) != 2:
            return

        recent_id_set = set(recent_player_ids)
        if players[0].id in recent_id_set or players[1].id in recent_id_set:
            raise HTTPException(
                status_code=400,
                detail="Could not generate a unique player pair. Starting new selection cycle.",
            )

    def _players_share_era(self, left: Player, right: Player) -> bool:
        left_start, left_end = self._career_window(left)
        right_start, right_end = self._career_window(right)
        shared_years = min(left_end, right_end) - max(left_start, right_start)
        birth_year_gap = abs((left.birth_year or 0) - (right.birth_year or 0))
        return shared_years >= MIN_SHARED_ERA_YEARS and birth_year_gap <= MAX_BIRTH_YEAR_GAP

    def _update_match_outcome(self, match_state: MatchState) -> None:
        mode = MATCH_MODES[match_state.mode_key]
        for seat_state in match_state.seats.values():
            if mode["required_score"] is not None and seat_state.score >= mode["required_score"]:
                match_state.status = "completed"
                match_state.winner_seat = seat_state.seat
                return
            if mode["required_round_wins"] is not None and seat_state.rounds_won >= mode["required_round_wins"]:
                match_state.status = "completed"
                match_state.winner_seat = seat_state.seat
                return
            if mode["required_streak"] is not None and seat_state.current_streak >= mode["required_streak"]:
                match_state.status = "completed"
                match_state.winner_seat = seat_state.seat
                return


match_service = MatchService()
