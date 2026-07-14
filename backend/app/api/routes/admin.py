from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.auth import CARD_LINK_TTL_MINUTES, auth_manager, require_authenticated_user
from app.core.database import DATABASE_BACKEND, EXTERNAL_DATABASE_CONFIGURED, get_db
from app.core.runtime import get_runtime_paths
from app.core.share_link import read_public_share_url, request_public_base_url
from app.game_service import DIFFICULTY_CONFIG
from app.match_service import match_service
from app.models import AssistantCompetition, AssistantQuestionTemplate, Player
from app.player_catalog_service import (
    admin_lock_map,
    create_player,
    delete_player,
    get_last_refresh_report,
    refresh_players_from_source,
    to_admin_player_read,
    update_player,
)
from app.schemas import (
    AdminCatalogRefreshRead,
    AdminDeleteRead,
    AdminAssistantCompetitionMutationRead,
    AdminAssistantCompetitionRead,
    AdminAssistantCompetitionsRead,
    AdminAssistantCompetitionWrite,
    AdminAssistantDeleteRead,
    AdminAssistantQuestionMutationRead,
    AdminAssistantQuestionRead,
    AdminAssistantQuestionsRead,
    AdminAssistantQuestionWrite,
    AdminDifficultyStatRead,
    AdminMatchRead,
    AdminMatchSeatRead,
    AdminOverviewRead,
    AdminPlayerMutationRead,
    AdminPlayerRead,
    AdminPlayersPageRead,
    AdminPlayerWrite,
    AdminRuntimeRead,
)
from app.seed import BUNDLED_DATASET_PATH, DATASET_PATH

router = APIRouter(dependencies=[Depends(require_authenticated_user)])


def _serialize_match(match_state) -> AdminMatchRead:
    serialized = match_service.read_match(match_state)
    return AdminMatchRead(
        match_id=serialized.match_id,
        mode_key=serialized.mode_key,
        status=serialized.status,
        winner_seat=serialized.winner_seat,
        round_number=serialized.round.round_number,
        difficulty=serialized.round.difficulty,
        difficulty_label=serialized.round.difficulty_label,
        points_for_win=serialized.round.points_for_win,
        question_limit=serialized.round.question_limit,
        guess_limit=serialized.round.guess_limit,
        updated_at=serialized.updated_at,
        seats=[
            AdminMatchSeatRead(
                seat=seat.seat,
                player_name=seat.player_name,
                score=seat.score,
                rounds_won=seat.rounds_won,
                current_streak=seat.current_streak,
            )
            for seat in serialized.seats
        ],
    )


def _dataset_record_count() -> int:
    for candidate in (DATASET_PATH, BUNDLED_DATASET_PATH):
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, list):
            return len(payload)
    return 0


@router.get("/overview", response_model=AdminOverviewRead)
def get_admin_overview(request: Request, db: Session = Depends(get_db)) -> AdminOverviewRead:
    players = db.scalars(
        select(Player).where(Player.gender_key == "male").order_by(Player.fame_score.desc(), Player.name.asc())
    ).all()
    matches = match_service.list_matches()
    runtime_paths = get_runtime_paths()
    auth_material = auth_manager.get_material()
    public_base_url = read_public_share_url() or request_public_base_url(request)

    represented_countries = len(
        {
            country
            for player in players
            for country in (player.countries_ar or player.countries)
            if country
        }
    )
    represented_continents = len(
        {
            continent
            for player in players
            for continent in (player.continents_ar or player.continents)
            if continent
        }
    )

    difficulty_stats: list[AdminDifficultyStatRead] = []
    for level, config in sorted(DIFFICULTY_CONFIG.items()):
        level_players = [player for player in players if player.difficulty == level]
        fame_scores = [player.fame_score for player in level_players]
        difficulty_stats.append(
            AdminDifficultyStatRead(
                level=level,
                label=config["label"],
                description=config["description"],
                player_count=len(level_players),
                fame_min=min(fame_scores) if fame_scores else 0,
                fame_max=max(fame_scores) if fame_scores else 0,
            )
        )

    database_size_bytes = runtime_paths.database_path.stat().st_size if runtime_paths.database_path.exists() else 0
    active_matches = [match_state for match_state in matches if match_state.status == "active"]
    completed_matches = [match_state for match_state in matches if match_state.status == "completed"]
    last_refresh_report = get_last_refresh_report()

    return AdminOverviewRead(
        username=auth_material.username,
        total_players=len(players),
        active_players=sum(1 for player in players if player.is_active),
        retired_players=sum(1 for player in players if not player.is_active),
        represented_countries=represented_countries,
        represented_continents=represented_continents,
        players_with_images=sum(1 for player in players if player.image_url),
        players_with_arabic_names=sum(1 for player in players if player.name_ar),
        total_matches=len(matches),
        active_matches=len(active_matches),
        completed_matches=len(completed_matches),
        difficulty_stats=difficulty_stats,
        recent_matches=[_serialize_match(match_state) for match_state in matches[:12]],
        catalog_refresh=AdminCatalogRefreshRead.model_validate(last_refresh_report) if last_refresh_report else None,
        runtime=AdminRuntimeRead(
            public_base_url=public_base_url,
            runtime_root=str(runtime_paths.root),
            data_dir=str(runtime_paths.data_dir),
            database_path=str(runtime_paths.database_path),
            database_backend=DATABASE_BACKEND,
            external_database_configured=EXTERNAL_DATABASE_CONFIGURED,
            dataset_path=str(DATASET_PATH),
            dataset_record_count=_dataset_record_count(),
            credentials_file_path=str(auth_material.credentials_file_path),
            secret_file_path=str(auth_material.secret_file_path),
            session_cookie_name=auth_material.session_cookie_name,
            session_ttl_hours=max(1, auth_material.session_ttl_seconds // 3600),
            card_link_ttl_minutes=CARD_LINK_TTL_MINUTES,
            database_size_bytes=database_size_bytes,
        ),
    )


@router.get("/players", response_model=AdminPlayersPageRead)
def get_admin_players(
    q: str = Query(default="", max_length=120),
    difficulty: int | None = Query(default=None, ge=1, le=4),
    active: bool | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=24, ge=1, le=120),
    db: Session = Depends(get_db),
) -> AdminPlayersPageRead:
    lock_map = admin_lock_map(db)
    filters = [Player.gender_key == "male"]
    search_text = q.strip()
    if search_text:
        pattern = f"%{search_text}%"
        filters.append(
            or_(
                Player.name.ilike(pattern),
                Player.name_ar.ilike(pattern),
                Player.current_team.ilike(pattern),
                Player.current_team_ar.ilike(pattern),
                Player.wikidata_id.ilike(pattern),
            )
        )

    if difficulty is not None:
        filters.append(Player.difficulty == difficulty)

    if active is not None:
        filters.append(Player.is_active == active)

    total = db.scalar(select(func.count()).select_from(Player).where(*filters)) or 0
    items = db.scalars(
        select(Player)
        .where(*filters)
        .order_by(Player.fame_score.desc(), Player.name.asc())
        .offset(offset)
        .limit(limit)
    ).all()

    return AdminPlayersPageRead(
        total=total,
        offset=offset,
        limit=limit,
        items=[to_admin_player_read(player, lock_map.get(player.wikidata_id, False)) for player in items],
    )


@router.post("/catalog/refresh", response_model=AdminCatalogRefreshRead)
def post_admin_catalog_refresh(db: Session = Depends(get_db)) -> AdminCatalogRefreshRead:
    try:
        return AdminCatalogRefreshRead.model_validate(refresh_players_from_source(db))
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=503, detail="مصدر البيانات مشغول الحين، جرّب بعد شوي.") from error


@router.post("/players", response_model=AdminPlayerMutationRead)
def post_admin_player(payload: AdminPlayerWrite, db: Session = Depends(get_db)) -> AdminPlayerMutationRead:
    player, total_players = create_player(db, payload)
    lock_map = admin_lock_map(db)
    return AdminPlayerMutationRead(
        player=to_admin_player_read(player, lock_map.get(player.wikidata_id, False)),
        total_players=total_players,
    )


@router.patch("/players/{player_id}", response_model=AdminPlayerMutationRead)
def patch_admin_player(
    player_id: int,
    payload: AdminPlayerWrite,
    db: Session = Depends(get_db),
) -> AdminPlayerMutationRead:
    player, total_players = update_player(db, player_id, payload)
    lock_map = admin_lock_map(db)
    return AdminPlayerMutationRead(
        player=to_admin_player_read(player, lock_map.get(player.wikidata_id, False)),
        total_players=total_players,
    )


@router.delete("/players/{player_id}", response_model=AdminDeleteRead)
def delete_admin_player(player_id: int, db: Session = Depends(get_db)) -> AdminDeleteRead:
    total_players = delete_player(db, player_id)
    return AdminDeleteRead(
        deleted_id=player_id,
        total_players=total_players,
    )


@router.get("/assistant/questions", response_model=AdminAssistantQuestionsRead)
def get_admin_assistant_questions(db: Session = Depends(get_db)) -> AdminAssistantQuestionsRead:
    items = db.scalars(
        select(AssistantQuestionTemplate)
        .order_by(AssistantQuestionTemplate.created_at.desc(), AssistantQuestionTemplate.id.desc())
    ).all()
    return AdminAssistantQuestionsRead(
        total=len(items),
        items=[AdminAssistantQuestionRead.model_validate(item) for item in items],
    )


@router.post("/assistant/questions", response_model=AdminAssistantQuestionMutationRead)
def post_admin_assistant_question(
    payload: AdminAssistantQuestionWrite,
    db: Session = Depends(get_db),
) -> AdminAssistantQuestionMutationRead:
    existing = db.scalar(
        select(AssistantQuestionTemplate).where(AssistantQuestionTemplate.intent_key == payload.intent_key)
    )
    if existing is not None:
        raise HTTPException(status_code=400, detail="مفتاح السؤال مستخدم من قبل.")

    item = AssistantQuestionTemplate(
        intent_key=payload.intent_key,
        question_en=payload.question_en,
        question_ar=payload.question_ar,
        aliases_en=payload.aliases_en,
        aliases_ar=payload.aliases_ar,
        argument_kind=payload.argument_kind,
        enabled=payload.enabled,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    total_items = db.scalar(select(func.count()).select_from(AssistantQuestionTemplate)) or 0
    return AdminAssistantQuestionMutationRead(
        item=AdminAssistantQuestionRead.model_validate(item),
        total_items=total_items,
    )


@router.patch("/assistant/questions/{question_id}", response_model=AdminAssistantQuestionMutationRead)
def patch_admin_assistant_question(
    question_id: int,
    payload: AdminAssistantQuestionWrite,
    db: Session = Depends(get_db),
) -> AdminAssistantQuestionMutationRead:
    item = db.get(AssistantQuestionTemplate, question_id)
    if item is None:
        raise HTTPException(status_code=404, detail="سجل السؤال غير موجود.")

    duplicate = db.scalar(
        select(AssistantQuestionTemplate).where(
            AssistantQuestionTemplate.intent_key == payload.intent_key,
            AssistantQuestionTemplate.id != question_id,
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=400, detail="مفتاح السؤال مستخدم من قبل.")

    item.intent_key = payload.intent_key
    item.question_en = payload.question_en
    item.question_ar = payload.question_ar
    item.aliases_en = payload.aliases_en
    item.aliases_ar = payload.aliases_ar
    item.argument_kind = payload.argument_kind
    item.enabled = payload.enabled
    db.commit()
    db.refresh(item)

    total_items = db.scalar(select(func.count()).select_from(AssistantQuestionTemplate)) or 0
    return AdminAssistantQuestionMutationRead(
        item=AdminAssistantQuestionRead.model_validate(item),
        total_items=total_items,
    )


@router.delete("/assistant/questions/{question_id}", response_model=AdminAssistantDeleteRead)
def delete_admin_assistant_question(
    question_id: int,
    db: Session = Depends(get_db),
) -> AdminAssistantDeleteRead:
    item = db.get(AssistantQuestionTemplate, question_id)
    if item is None:
        raise HTTPException(status_code=404, detail="سجل السؤال غير موجود.")

    db.delete(item)
    db.commit()
    total_items = db.scalar(select(func.count()).select_from(AssistantQuestionTemplate)) or 0
    return AdminAssistantDeleteRead(
        deleted_id=question_id,
        total_items=total_items,
    )


@router.get("/assistant/competitions", response_model=AdminAssistantCompetitionsRead)
def get_admin_assistant_competitions(db: Session = Depends(get_db)) -> AdminAssistantCompetitionsRead:
    items = db.scalars(
        select(AssistantCompetition)
        .order_by(AssistantCompetition.created_at.desc(), AssistantCompetition.id.desc())
    ).all()
    return AdminAssistantCompetitionsRead(
        total=len(items),
        items=[AdminAssistantCompetitionRead.model_validate(item) for item in items],
    )


@router.post("/assistant/competitions", response_model=AdminAssistantCompetitionMutationRead)
def post_admin_assistant_competition(
    payload: AdminAssistantCompetitionWrite,
    db: Session = Depends(get_db),
) -> AdminAssistantCompetitionMutationRead:
    existing = db.scalar(
        select(AssistantCompetition).where(AssistantCompetition.key == payload.key)
    )
    if existing is not None:
        raise HTTPException(status_code=400, detail="مفتاح الدوري مستخدم من قبل.")

    item = AssistantCompetition(
        key=payload.key,
        wikidata_id=payload.wikidata_id,
        name_en=payload.name_en,
        name_ar=payload.name_ar,
        aliases_en=payload.aliases_en,
        aliases_ar=payload.aliases_ar,
        enabled=payload.enabled,
    )
    db.add(item)
    db.commit()
    db.refresh(item)

    total_items = db.scalar(select(func.count()).select_from(AssistantCompetition)) or 0
    return AdminAssistantCompetitionMutationRead(
        item=AdminAssistantCompetitionRead.model_validate(item),
        total_items=total_items,
    )


@router.patch("/assistant/competitions/{competition_id}", response_model=AdminAssistantCompetitionMutationRead)
def patch_admin_assistant_competition(
    competition_id: int,
    payload: AdminAssistantCompetitionWrite,
    db: Session = Depends(get_db),
) -> AdminAssistantCompetitionMutationRead:
    item = db.get(AssistantCompetition, competition_id)
    if item is None:
        raise HTTPException(status_code=404, detail="سجل الدوري غير موجود.")

    duplicate = db.scalar(
        select(AssistantCompetition).where(
            AssistantCompetition.key == payload.key,
            AssistantCompetition.id != competition_id,
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=400, detail="مفتاح الدوري مستخدم من قبل.")

    item.key = payload.key
    item.wikidata_id = payload.wikidata_id
    item.name_en = payload.name_en
    item.name_ar = payload.name_ar
    item.aliases_en = payload.aliases_en
    item.aliases_ar = payload.aliases_ar
    item.enabled = payload.enabled
    db.commit()
    db.refresh(item)

    total_items = db.scalar(select(func.count()).select_from(AssistantCompetition)) or 0
    return AdminAssistantCompetitionMutationRead(
        item=AdminAssistantCompetitionRead.model_validate(item),
        total_items=total_items,
    )


@router.delete("/assistant/competitions/{competition_id}", response_model=AdminAssistantDeleteRead)
def delete_admin_assistant_competition(
    competition_id: int,
    db: Session = Depends(get_db),
) -> AdminAssistantDeleteRead:
    item = db.get(AssistantCompetition, competition_id)
    if item is None:
        raise HTTPException(status_code=404, detail="سجل الدوري غير موجود.")

    db.delete(item)
    db.commit()
    total_items = db.scalar(select(func.count()).select_from(AssistantCompetition)) or 0
    return AdminAssistantDeleteRead(
        deleted_id=competition_id,
        total_items=total_items,
    )
