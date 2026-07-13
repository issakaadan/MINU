from __future__ import annotations

import random

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.match_service import MatchService, PLAYER_SELECTION_ATTEMPTS
from app.models import Player


def _patched_assert_match_state_consistency(
    self: MatchService,
    state,
    fallback,
):
    if state.match_id != fallback.match_id:
        raise HTTPException(status_code=404, detail="المباراة مو موجودة.")

    return fallback


def _patched_pick_players_v2(
    self: MatchService,
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
        raise HTTPException(status_code=400, detail="ما فيه لاعبين كفاية لهاللفل.")

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


MatchService._assert_match_state_consistency = _patched_assert_match_state_consistency
MatchService._pick_players_v2 = _patched_pick_players_v2
