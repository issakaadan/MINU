from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.lib import colors
from sqlalchemy.orm import Session

from app.core.auth import auth_manager, require_authenticated_user
from app.core.database import get_db
from app.core.share_link import read_public_share_url, request_public_base_url
from app.game_service import game_service, player_to_reveal
from app import match_service_patch  # noqa: F401
from app.match_service import match_service
from app.schemas import (
    AskQuestionRequest,
    AskQuestionResponse,
    AwardRoundRequest,
    GameOverview,
    GuessRequest,
    GuessResponse,
    MatchCreateRequest,
    MatchRead,
    PlayerCardTokenRead,
    PlayerSecretRead,
    RevealResponse,
    RoundStateRead,
    ShareLinkRead,
    SharedPlayerCardRead,
    StartRoundRequest,
)

router = APIRouter(dependencies=[Depends(require_authenticated_user)])
public_router = APIRouter()


def _match_token_from_request(request: Request) -> str | None:
    token = request.headers.get("x-minu-match", "").strip()
    return token or None


def _build_shared_card_payload(secret: PlayerSecretRead) -> SharedPlayerCardRead:
    return SharedPlayerCardRead(
        m=secret.match_id,
        r=secret.round.round_number,
        s=secret.seat,
        pn=secret.player_name,
        on=secret.opponent_name,
        mk=secret.mode_key,
        n=secret.player.name,
        na=secret.player.name_ar,
        i=secret.player.image_url,
        c=secret.player.primary_country_ar or secret.player.primary_country,
        ce=secret.player.primary_country,
        p=secret.player.position_group,
        y=secret.player.birth_year,
        a=1 if secret.player.is_active else 0,
        ct=secret.player.current_team,
        cta=secret.player.current_team_ar,
        wd=secret.player.wikidata_id,
    )


@router.get("/overview", response_model=GameOverview)
def get_overview(db: Session = Depends(get_db)) -> GameOverview:
    return game_service.get_overview(db)


@router.get("/share-link", response_model=ShareLinkRead)
def get_share_link(request: Request) -> ShareLinkRead:
    return ShareLinkRead(public_url=read_public_share_url() or request_public_base_url(request))


@router.get("/qr", response_class=Response)
def get_qr_code(value: str = Query(min_length=1, max_length=3000)) -> Response:
    qr_widget = qr.QrCodeWidget(value)
    left, bottom, right, top = qr_widget.getBounds()
    width = right - left
    height = top - bottom
    drawing = Drawing(width, height)
    drawing.add(Rect(0, 0, width, height, fillColor=colors.white, strokeColor=colors.white))
    drawing.add(qr_widget)
    return Response(content=renderSVG.drawToString(drawing), media_type="image/svg+xml")


@router.post("/rounds", response_model=RoundStateRead)
def create_round(payload: StartRoundRequest, db: Session = Depends(get_db)) -> RoundStateRead:
    round_state, player = game_service.create_round(db, payload.difficulty, payload.recent_player_ids)
    return game_service.read_round(round_state, player)


@router.post("/rounds/{round_id}/questions", response_model=AskQuestionResponse)
def ask_question(round_id: str, payload: AskQuestionRequest, db: Session = Depends(get_db)) -> AskQuestionResponse:
    round_state = game_service.get_round(round_id)
    answer, prompt = game_service.ask_question(db, round_state, payload.category, payload.value)
    return AskQuestionResponse(
        answer=answer,
        answer_label="نعم" if answer else "لا",
        prompt=prompt,
        questions_remaining=round_state.questions_remaining,
        current_points=round_state.current_points,
        question_history=round_state.question_history,
    )


@router.post("/rounds/{round_id}/guess", response_model=GuessResponse)
def submit_guess(round_id: str, payload: GuessRequest, db: Session = Depends(get_db)) -> GuessResponse:
    round_state = game_service.get_round(round_id)
    correct, message, player = game_service.submit_guess(db, round_state, payload.guess)
    return GuessResponse(
        correct=correct,
        message=message,
        awarded_points=round_state.awarded_points,
        current_points=round_state.current_points,
        guesses_remaining=round_state.guesses_remaining,
        round_finished=round_state.finished,
        player=player_to_reveal(player) if player and round_state.finished else None,
    )


@router.post("/rounds/{round_id}/reveal", response_model=RevealResponse)
def reveal_round(round_id: str, db: Session = Depends(get_db)) -> RevealResponse:
    round_state = game_service.get_round(round_id)
    player = game_service.reveal(db, round_state)
    return RevealResponse(
        message="تم كشف اللاعب. لا نقاط في هذه الجولة.",
        player=player_to_reveal(player),
        question_history=round_state.question_history,
        created_at=round_state.revealed_at or datetime.now(timezone.utc),
    )


@router.post("/matches", response_model=MatchRead)
def create_match(payload: MatchCreateRequest, db: Session = Depends(get_db)) -> MatchRead:
    match_state = match_service.create_match(
        db=db,
        difficulty=payload.difficulty,
        mode_key=payload.mode_key,
        player_names=payload.player_names,
        recent_player_ids=payload.recent_player_ids,
        recent_player_keys=[],
        selected_answer_rule_keys=payload.selected_answer_rule_keys,
        selected_prohibited_category_keys=payload.selected_prohibited_category_keys,
    )
    return match_service.read_match(match_state)


@router.get("/matches/{match_id}", response_model=MatchRead)
def get_match(match_id: str, request: Request) -> MatchRead:
    match_state = match_service.get_match(match_id, _match_token_from_request(request))
    return match_service.read_match(match_state)


@router.post("/matches/{match_id}/award", response_model=MatchRead)
def award_round(match_id: str, payload: AwardRoundRequest, request: Request) -> MatchRead:
    with match_service._lock:
        match_state = match_service.get_match(match_id, _match_token_from_request(request))
        match_service.award_round(match_state, payload.seat)
    return match_service.read_match(match_state)


@router.post("/matches/{match_id}/next-round", response_model=MatchRead)
def next_round(match_id: str, request: Request, db: Session = Depends(get_db)) -> MatchRead:
    with match_service._lock:
        match_state = match_service.get_match(match_id, _match_token_from_request(request))
        match_service.next_round(db, match_state)
    return match_service.read_match(match_state)


@router.post("/matches/{match_id}/no-answer", response_model=MatchRead)
def mark_round_unanswered(match_id: str, request: Request) -> MatchRead:
    with match_service._lock:
        match_state = match_service.get_match(match_id, _match_token_from_request(request))
        match_service.mark_round_unanswered(match_state)
    return match_service.read_match(match_state)


@router.post("/matches/{match_id}/end", response_model=MatchRead)
def end_match(match_id: str, request: Request) -> MatchRead:
    with match_service._lock:
        match_state = match_service.get_match(match_id, _match_token_from_request(request))
        match_service.end_match(match_state)
    return match_service.read_match(match_state)


@router.get("/matches/{match_id}/players/{seat}", response_model=PlayerSecretRead)
def get_player_secret(
    match_id: str,
    seat: int,
    request: Request,
    db: Session = Depends(get_db),
) -> PlayerSecretRead:
    match_state = match_service.get_match(match_id, _match_token_from_request(request))
    return match_service.read_secret(db, match_state, seat)


@router.get("/matches/{match_id}/players/{seat}/share-token", response_model=PlayerCardTokenRead)
def get_player_share_token(
    match_id: str,
    seat: int,
    request: Request,
    db: Session = Depends(get_db),
) -> PlayerCardTokenRead:
    match_state = match_service.get_match(match_id, _match_token_from_request(request))
    secret = match_service.read_secret(db, match_state, seat)
    payload = _build_shared_card_payload(secret)
    return PlayerCardTokenRead(token=auth_manager.create_card_token(payload.model_dump()))


@public_router.get("/card/{token}", response_model=SharedPlayerCardRead)
def get_public_player_card(token: str) -> SharedPlayerCardRead:
    payload = auth_manager.read_card_token(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="البطاقة مو موجودة")
    return SharedPlayerCardRead.model_validate(payload)
