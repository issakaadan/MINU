from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AssistantCompetition, AssistantQuestionTemplate, Player
from app.player_rag_service import (
    PlayerRagAnswer,
    PlayerRagDocument,
    answer_player_rag_question,
    get_player_rag_achievements,
    get_player_rag_document,
    get_player_rag_goal_totals,
    get_player_rag_summary,
)
from app.schemas import CardAssistantAnswerRead, CardAssistantTraceRead, SharedPlayerCardRead
from app.wikipedia_career_stats import CareerStatLine, parse_wikipedia_career_stats

PROJECT_ROOT = Path(__file__).resolve().parents[2]
QUESTION_TEMPLATE_PATH = PROJECT_ROOT / "backend" / "data" / "assistant_question_templates.json"
COMPETITION_PATH = PROJECT_ROOT / "backend" / "data" / "assistant_competitions.json"
USER_AGENT = "MINUAssistant/1.0 (https://minu-theta.vercel.app)"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API_URLS = {
    "en": "https://en.wikipedia.org/w/api.php",
    "ar": "https://ar.wikipedia.org/w/api.php",
}
WIKIPEDIA_SUMMARY_URLS = {
    "en": "https://en.wikipedia.org/api/rest_v1/page/summary",
    "ar": "https://ar.wikipedia.org/api/rest_v1/page/summary",
}
QUESTION_FILLER_WORDS = {
    "en": {
        "the", "a", "an", "player", "footballer", "club", "clubs", "team", "league", "competition",
        "did", "does", "do", "has", "have", "had", "he", "his", "him", "is", "was", "were",
        "in", "for", "with", "at", "to", "from", "of", "ever", "any", "this", "that",
        "what", "which", "who", "where", "when", "how", "play", "played", "score", "scored", "goal", "goals",
    },
    "ar": {
        "اللاعب", "النادي", "الأندية", "الانديه", "الفريق", "الفرق", "الدوري", "المسابقة",
        "المسابقه", "في", "مع", "هل", "سبق", "له", "على", "من", "الى", "إلى", "كم",
        "وش", "شنو", "ايش", "إيش", "اي", "أي", "اللي", "التي", "شي", "لعب", "يلعب", "سجل", "هدف", "أهداف", "اهداف",
    },
}
MATCH_FILLER_WORDS = QUESTION_FILLER_WORDS["en"] | QUESTION_FILLER_WORDS["ar"]
TEAM_NAME_MATCH_FILLER_WORDS = {
    "fc", "cf", "sc", "ac", "afc", "rcd", "cd", "ud", "uc", "club", "football", "team", "men", "mens",
    "national", "de", "futbol", "futebol", "s", "نادي", "منتخب", "لكرة", "لكره", "كرة", "كره",
    "القدم", "قدم", "للرجال", "الرجال",
}
YOUTH_TEAM_PATTERN = re.compile(
    r"\bU(?:17|18|19|20|21|23)\b|under[- ]?\d+|youth|juvenil|school|schools|reserve|reserves|b team|c team|olympic|منتخب الشباب|الأولمبي|تحت\s*\d+|(?:\s|^)[بج](?:\s|$)",
    re.IGNORECASE,
)


@dataclass
class TeamStint:
    entity_id: str
    name_en: str
    name_ar: str
    start_year: int | None
    end_year: int | None
    is_national_team: bool
    league_ids: list[str] = field(default_factory=list)


@dataclass
class AssistantProfile:
    player: Player | None
    wikidata_id: str
    rag_document: PlayerRagDocument | None = None
    rag_loaded: bool = False
    trace: CardAssistantTraceRead | None = None
    summary_en: str = ""
    summary_ar: str = ""
    achievements_en: list[str] = field(default_factory=list)
    achievements_ar: list[str] = field(default_factory=list)
    club_stints: list[TeamStint] = field(default_factory=list)
    national_team_stints: list[TeamStint] = field(default_factory=list)
    club_retired_year: int | None = None
    national_team_retired_year: int | None = None
    is_alive: bool | None = None
    deceased_year: int | None = None
    career_stats_loaded: bool = False
    club_stat_lines: list[CareerStatLine] = field(default_factory=list)
    national_stat_lines: list[CareerStatLine] = field(default_factory=list)
    club_goals_total: int | None = None
    national_team_goals_total: int | None = None
    career_goals_total: int | None = None
    used_sources: set[str] = field(default_factory=lambda: {"database"})


def seed_assistant_catalog(db: Session) -> None:
    template_payload = json.loads(QUESTION_TEMPLATE_PATH.read_text(encoding="utf-8"))
    competition_payload = json.loads(COMPETITION_PATH.read_text(encoding="utf-8"))

    existing_templates = {
        entry.intent_key: entry
        for entry in db.scalars(select(AssistantQuestionTemplate)).all()
    }
    for record in template_payload:
        intent_key = str(record.get("intent_key", "")).strip()
        if not intent_key:
            continue

        entry = existing_templates.get(intent_key)
        if entry is not None:
            entry.aliases_en = _normalize_string_list([*(entry.aliases_en or []), *(record.get("aliases_en") or [])])
            entry.aliases_ar = _normalize_string_list([*(entry.aliases_ar or []), *(record.get("aliases_ar") or [])])
            continue

        entry = AssistantQuestionTemplate(
            intent_key=intent_key,
            question_en=str(record.get("question_en", "")).strip(),
            question_ar=str(record.get("question_ar", "")).strip(),
            aliases_en=_normalize_string_list(record.get("aliases_en") or []),
            aliases_ar=_normalize_string_list(record.get("aliases_ar") or []),
            argument_kind=str(record.get("argument_kind", "")).strip(),
            enabled=bool(record.get("enabled", True)),
        )
        db.add(entry)

    existing_competitions = {
        entry.key: entry
        for entry in db.scalars(select(AssistantCompetition)).all()
    }
    for record in competition_payload:
        key = str(record.get("key", "")).strip()
        if not key:
            continue

        entry = existing_competitions.get(key)
        if entry is not None:
            entry.aliases_en = _normalize_string_list([*(entry.aliases_en or []), *(record.get("aliases_en") or [])])
            entry.aliases_ar = _normalize_string_list([*(entry.aliases_ar or []), *(record.get("aliases_ar") or [])])
            continue

        entry = AssistantCompetition(
            key=key,
            wikidata_id=str(record.get("wikidata_id", "")).strip(),
            name_en=str(record.get("name_en", "")).strip(),
            name_ar=str(record.get("name_ar", "")).strip(),
            aliases_en=_normalize_string_list(record.get("aliases_en") or []),
            aliases_ar=_normalize_string_list(record.get("aliases_ar") or []),
            enabled=bool(record.get("enabled", True)),
        )
        db.add(entry)

    db.commit()


def answer_card_question(
    db: Session,
    payload: SharedPlayerCardRead,
    question: str,
    language: Literal["ar", "en"],
) -> CardAssistantAnswerRead:
    normalized_question = _normalize_question(question)
    if not normalized_question:
        return CardAssistantAnswerRead(
            answer="اكتب سؤالك أولاً." if language == "ar" else "Write your question first.",
            intent_key=None,
            used_sources=["database"],
            matched_argument=None,
            trace=None,
        )

    templates = db.scalars(
        select(AssistantQuestionTemplate)
        .where(AssistantQuestionTemplate.enabled.is_(True))
        .order_by(AssistantQuestionTemplate.id.asc())
    ).all()
    competitions = db.scalars(
        select(AssistantCompetition)
        .where(AssistantCompetition.enabled.is_(True))
        .order_by(AssistantCompetition.id.asc())
    ).all()

    match = _match_question_template(normalized_question, templates)
    player = _resolve_player(db, payload)
    if match is None:
        profile = _build_base_profile(player, payload)
        rag_answer = _answer_from_rag(profile, payload, question, language)
        if rag_answer:
            return CardAssistantAnswerRead(
                answer=rag_answer,
                intent_key=None,
                used_sources=sorted(profile.used_sources),
                matched_argument=None,
                trace=profile.trace,
            )
        return CardAssistantAnswerRead(
            answer=_answer_not_found(language),
            intent_key=None,
            used_sources=["database"],
            matched_argument=None,
            trace=None,
        )

    profile = _build_base_profile(player, payload)
    matched_argument: str | None = None

    if match.intent_key == "summary":
        summary = _load_summary(profile, payload, language)
        answer = summary or _missing_summary(language)
    elif match.intent_key == "achievements":
        achievements = _load_achievements(profile, payload, language)
        answer = _answer_achievements(profile, payload, achievements, language)
    elif match.intent_key == "played_in_competition":
        competition = _match_competition(normalized_question, competitions, match.argument_tail or "")
        if competition is None:
            answer = _missing_competition(language)
        else:
            matched_argument = competition.name_ar if language == "ar" and competition.name_ar else competition.name_en
            answer = _answer_competition(profile, payload, competition, language)
    elif match.intent_key == "played_for_team":
        team_name = _extract_dynamic_argument(normalized_question, match.argument_tail or "", language)
        if not team_name:
            answer = _missing_team(language)
        else:
            matched_argument = team_name
            answer = _answer_team_membership(profile, payload, team_name, language)
    else:
        answer = _answer_from_profile(profile, payload, match.intent_key, language)

    return CardAssistantAnswerRead(
        answer=answer,
        intent_key=match.intent_key,
        used_sources=sorted(profile.used_sources),
        matched_argument=matched_argument,
        trace=profile.trace,
    )


def _resolve_player(db: Session, payload: SharedPlayerCardRead) -> Player | None:
    wikidata_id = payload.wd.strip()
    if wikidata_id:
        player = db.scalar(select(Player).where(Player.wikidata_id == wikidata_id))
        if player is not None:
            return player

    name = payload.n.strip()
    name_ar = payload.na.strip()
    if name:
        player = db.scalar(select(Player).where(Player.name == name))
        if player is not None:
            return player
    if name_ar:
        return db.scalar(select(Player).where(Player.name_ar == name_ar))

    return None


def _load_player_rag_document(
    profile: AssistantProfile,
    payload: SharedPlayerCardRead,
) -> PlayerRagDocument | None:
    if profile.rag_loaded:
        return profile.rag_document

    profile.rag_loaded = True
    profile.rag_document = get_player_rag_document(
        profile.wikidata_id,
        payload.n,
        payload.na,
    )
    return profile.rag_document


def _answer_from_rag(
    profile: AssistantProfile,
    payload: SharedPlayerCardRead,
    question: str,
    language: Literal["ar", "en"],
) -> str | None:
    document = _load_player_rag_document(profile, payload)
    if document is None:
        return None

    aliased_team_answer = _answer_team_specific_goals_with_profile_aliases(
        profile,
        document,
        question,
        language,
    )
    if aliased_team_answer:
        profile.used_sources.add("player_rag")
        _set_trace_from_rag_answer(profile, aliased_team_answer)
        return aliased_team_answer.answer

    rag_answer = answer_player_rag_question(document, question, language)
    if rag_answer:
        profile.used_sources.add("player_rag")
        _set_trace_from_rag_answer(profile, rag_answer)
        return rag_answer.answer
    return None


def _answer_team_specific_goals_with_profile_aliases(
    profile: AssistantProfile,
    document: PlayerRagDocument,
    question: str,
    language: Literal["ar", "en"],
) -> PlayerRagAnswer | None:
    normalized_question = _normalize_question(question)
    if not normalized_question or not _looks_like_goal_query(normalized_question):
        return None
    if not _looks_like_specific_team_goals_query(normalized_question):
        return None

    mentions_youth = bool(YOUTH_TEAM_PATTERN.search(normalized_question))
    best_stint: TeamStint | None = None
    best_score = 0.0
    for stint in [*profile.club_stints, *profile.national_team_stints]:
        score = _team_stint_query_score(normalized_question, stint, mentions_youth)
        if score > best_score:
            best_score = score
            best_stint = stint

    if best_stint is None or best_score < 0.72:
        return None

    section = "national" if best_stint.is_national_team else "club"
    row = _match_rag_stat_row_to_stint(document, language, section, best_stint)
    if row is None:
        return None

    goals = _safe_int(row.get("goals"))
    apps = _safe_int(row.get("apps"))
    if goals is None:
        return None

    team_name = best_stint.name_ar if language == "ar" and best_stint.name_ar else best_stint.name_en
    if language == "ar":
        if apps is not None:
            answer = f"سجل {goals} هدفاً مع {team_name} في {apps} مباراة."
        else:
            answer = f"سجل {goals} هدفاً مع {team_name}."
    elif apps is not None:
        answer = f"He scored {goals} goals for {team_name} in {apps} appearances."
    else:
        answer = f"He scored {goals} goals for {team_name}."

    return PlayerRagAnswer(
        answer=answer,
        source_kind="national_stats" if section == "national" else "club_stats",
        source_title=(
            "Arabic National Team Stats"
            if section == "national" and language == "ar"
            else "English National Team Stats"
            if section == "national"
            else "Arabic Club Career Stats"
            if language == "ar"
            else "English Club Career Stats"
        ),
        source_language=language,
        source_excerpt=answer,
        score=int(best_score * 100),
    )


def _team_stint_query_score(
    normalized_question: str,
    stint: TeamStint,
    mentions_youth: bool,
) -> float:
    score = max(
        _team_name_match_score(normalized_question, stint.name_en),
        _team_name_match_score(normalized_question, stint.name_ar),
    )
    if stint.is_national_team:
        score += 0.08 if _looks_like_national_goals_query(normalized_question) else 0.02
    if _is_non_senior_team_label(stint.name_en) or _is_non_senior_team_label(stint.name_ar):
        score += 0.08 if mentions_youth else -0.18
    else:
        score += 0.05
    return score


def _team_name_match_score(normalized_question: str, team_name: str) -> float:
    best = 0.0
    question_tokens = _content_tokens_for_matching(normalized_question) or _tokenize_for_matching(normalized_question)
    for variant in _team_name_variants(team_name):
        if variant in normalized_question:
            return 1.0
        variant_tokens = _content_tokens_for_matching(variant) or _tokenize_for_matching(variant)
        if not variant_tokens:
            continue
        overlap = _count_matching_tokens(question_tokens, variant_tokens)
        coverage = overlap / len(variant_tokens)
        similarity = _partial_similarity(normalized_question, variant)
        best = max(best, coverage, similarity)
    return best


def _team_name_variants(team_name: str) -> list[str]:
    normalized_team = _normalize_question(team_name)
    if not normalized_team:
        return []

    variants = {normalized_team}
    tokens = [token for token in _tokenize_for_matching(normalized_team) if token not in TEAM_NAME_MATCH_FILLER_WORDS]
    if tokens:
        variants.add(" ".join(tokens))
        if len(tokens) > 1 and len(tokens[0]) <= 3:
            variants.add(" ".join(tokens[1:]))
    return [variant for variant in variants if variant]


def _match_rag_stat_row_to_stint(
    document: PlayerRagDocument,
    language: Literal["ar", "en"],
    section: Literal["club", "national"],
    stint: TeamStint,
) -> dict[str, object] | None:
    rows = list(document.stat_rows.get(language, {}).get(section, ()))
    if language == "ar" and not rows:
        rows = list(document.stat_rows.get("en", {}).get(section, ()))
    if language == "en" and not rows:
        rows = list(document.stat_rows.get("ar", {}).get(section, ()))
    if not rows:
        return None

    best_row: dict[str, object] | None = None
    best_score = 0.0
    stint_is_youth = _is_non_senior_team_label(stint.name_en) or _is_non_senior_team_label(stint.name_ar)
    for row in rows:
        team_name = clean_text(str(row.get("team", "") or ""))
        if not team_name or _normalize_question(team_name) in {"total", "totals", "المجموع", "الاجمالي", "الإجمالي"}:
            continue
        score = max(
            _team_label_alignment_score(team_name, stint.name_en),
            _team_label_alignment_score(team_name, stint.name_ar),
        )
        row_is_youth = _is_non_senior_team_label(team_name)
        if row_is_youth == stint_is_youth:
            score += 0.08
        else:
            score -= 0.28
        if score > best_score:
            best_score = score
            best_row = row

    if best_row is None or best_score < 0.72:
        return None
    return best_row


def _team_label_alignment_score(left: str, right: str) -> float:
    best = 0.0
    for left_variant in _team_name_variants(left):
        left_tokens = _content_tokens_for_matching(left_variant) or _tokenize_for_matching(left_variant)
        for right_variant in _team_name_variants(right):
            if left_variant == right_variant:
                return 1.0
            if left_variant and right_variant and (left_variant in right_variant or right_variant in left_variant):
                best = max(best, 0.92)
            right_tokens = _content_tokens_for_matching(right_variant) or _tokenize_for_matching(right_variant)
            if right_tokens:
                overlap = _count_matching_tokens(left_tokens, right_tokens)
                coverage = overlap / len(right_tokens)
            else:
                coverage = 0.0
            similarity = _partial_similarity(left_variant, right_variant)
            best = max(best, coverage, similarity)
    return best


def _set_trace_from_rag_answer(profile: AssistantProfile, rag_answer: PlayerRagAnswer) -> None:
    profile.trace = CardAssistantTraceRead(
        source_kind=rag_answer.source_kind,
        source_title=rag_answer.source_title,
        source_language=rag_answer.source_language,
        source_excerpt=rag_answer.source_excerpt,
        score=rag_answer.score,
    )


def _set_player_rag_trace(
    profile: AssistantProfile,
    *,
    source_kind: str,
    source_title: str,
    source_language: str,
    source_excerpt: str,
    score: int | None = None,
) -> None:
    profile.trace = CardAssistantTraceRead(
        source_kind=source_kind,
        source_title=source_title,
        source_language=source_language,
        source_excerpt=source_excerpt,
        score=score,
    )


@dataclass
class TemplateMatch:
    intent_key: str
    score: tuple[int, int]
    argument_kind: str
    argument_tail: str


def _match_question_template(
    normalized_question: str,
    templates: list[AssistantQuestionTemplate],
) -> TemplateMatch | None:
    template_map = {template.intent_key: template for template in templates}
    direct_intent = _guess_intent_key(normalized_question)
    if direct_intent and direct_intent in template_map:
        template = template_map[direct_intent]
        return TemplateMatch(
            intent_key=template.intent_key,
            score=(995, 995),
            argument_kind=template.argument_kind,
            argument_tail="",
        )

    best: TemplateMatch | None = None
    question_tokens = _tokenize_for_matching(normalized_question)
    question_content_tokens = _content_tokens_for_matching(normalized_question)
    goal_scope = _goal_query_scope(normalized_question)
    if goal_scope == "unsupported":
        return None

    for template in templates:
        if template.intent_key in {"career_goals", "club_goals"}:
            if goal_scope in {None, "unsupported"}:
                continue
            if goal_scope == "career" and template.intent_key != "career_goals":
                continue
            if goal_scope == "club" and template.intent_key != "club_goals":
                continue

        aliases = [
            template.question_en,
            template.question_ar,
            *(template.aliases_en or []),
            *(template.aliases_ar or []),
        ]
        for alias in aliases:
            normalized_alias = _normalize_question(alias)
            if not normalized_alias:
                continue

            alias_tokens = _tokenize_for_matching(normalized_alias)
            if not alias_tokens:
                continue

            if f" {normalized_alias} " in f" {normalized_question} ":
                alias_length = len(alias_tokens)
                start_index = normalized_question.find(normalized_alias)
                argument_tail = normalized_question[start_index + len(normalized_alias):].strip()
                candidate = TemplateMatch(
                    intent_key=template.intent_key,
                    score=(130 + alias_length, alias_length),
                    argument_kind=template.argument_kind,
                    argument_tail=argument_tail,
                )
            else:
                overlap = _count_matching_tokens(question_tokens, alias_tokens)
                if overlap == 0:
                    continue

                alias_content_tokens = _content_tokens_for_matching(normalized_alias)
                content_overlap = 0
                if alias_content_tokens and question_content_tokens:
                    exact_question_tokens = {_simplify_token(token) for token in question_content_tokens}
                    exact_alias_tokens = {_simplify_token(token) for token in alias_content_tokens}
                    if not (exact_question_tokens & exact_alias_tokens):
                        continue
                    # A one-word alias must match an actual content token. Allowing fuzzy
                    # matching here made unrelated Arabic words such as "عاصمة" select
                    # "مركزه", and "لون" select the nationality alias "وين".
                    if len(alias_content_tokens) == 1:
                        alias_token = _simplify_token(alias_content_tokens[0])
                        content_overlap = int(
                            bool(alias_token)
                            and alias_token in exact_question_tokens
                        )
                    else:
                        content_overlap = _count_matching_tokens(question_content_tokens, alias_content_tokens)
                    if content_overlap == 0:
                        continue
                    if len(alias_content_tokens) > 1 and content_overlap < 2:
                        continue

                coverage_numerator = content_overlap or overlap
                coverage_denominator = len(alias_content_tokens) or len(alias_tokens)
                coverage = int((coverage_numerator / max(1, coverage_denominator)) * 100)
                similarity = int(_partial_similarity(normalized_question, normalized_alias) * 100)
                candidate = TemplateMatch(
                    intent_key=template.intent_key,
                    score=(coverage + (similarity // 3) + (8 if content_overlap else 0), content_overlap or overlap),
                    argument_kind=template.argument_kind,
                    argument_tail="",
                )

            if best is None or candidate.score > best.score:
                best = candidate

    # Scores below 80 are typically a single vaguely similar word. Accepting
    # them caused general-knowledge questions to be answered with player data.
    if best is not None and best.score[0] >= 80:
        return best
    return None


def _guess_intent_key(normalized_question: str) -> str | None:
    if _looks_like_national_before_retirement_query(normalized_question):
        return "national_retired_before_club_retired"
    goal_scope = _goal_query_scope(normalized_question)
    if goal_scope == "career":
        return "career_goals"
    if goal_scope == "club":
        return "club_goals"
    if _looks_like_achievement_query(normalized_question):
        return "achievements"
    if _looks_like_club_history_query(normalized_question):
        return "club_history"
    if _contains_marker_phrase(
        normalized_question,
        ("وظيفته بالملعب", "دوره بالملعب", "يلعب وين بالملعب", "وين لعب", "أين لعب"),
    ):
        if _contains_marker_phrase(normalized_question, ("وظيفته بالملعب", "دوره بالملعب", "يلعب وين بالملعب")):
            return "position"
        return "club_history"
    if _looks_like_alive_query(normalized_question):
        return "alive_status"
    if _looks_like_summary_query(normalized_question):
        return "summary"
    return None


def _tokenize_for_matching(value: str) -> list[str]:
    return [token for token in value.split() if token]


def _content_tokens_for_matching(value: str) -> list[str]:
    return [
        token
        for token in _tokenize_for_matching(value)
        if token and token not in MATCH_FILLER_WORDS
    ]


def _question_token_set(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in _tokenize_for_matching(value):
        simplified = _simplify_token(token)
        if simplified:
            tokens.add(simplified)
    return tokens


def _contains_marker_token(normalized_question: str, markers: tuple[str, ...]) -> bool:
    question_tokens = _question_token_set(normalized_question)
    if not question_tokens:
        return False

    for marker in markers:
        normalized_marker = _normalize_question(marker)
        if not normalized_marker or " " in normalized_marker:
            continue
        simplified_marker = _simplify_token(normalized_marker)
        if simplified_marker and simplified_marker in question_tokens:
            return True
    return False


def _contains_marker_phrase(normalized_question: str, markers: tuple[str, ...]) -> bool:
    return any(
        (normalized_marker := _normalize_question(marker)) and " " in normalized_marker and normalized_marker in normalized_question
        for marker in markers
    )


def _count_matching_tokens(question_tokens: list[str], alias_tokens: list[str]) -> int:
    matched = 0
    for alias_token in alias_tokens:
        if any(_fuzzy_token_match(alias_token, question_token) for question_token in question_tokens):
            matched += 1
    return matched


def _fuzzy_token_match(left: str, right: str) -> bool:
    if left == right:
        return True

    left_simple = _simplify_token(left)
    right_simple = _simplify_token(right)
    if not left_simple or not right_simple:
        return False
    if left_simple == right_simple:
        return True
    if len(left_simple) >= 4 and len(right_simple) >= 4 and (left_simple in right_simple or right_simple in left_simple):
        return True
    minimum_length = min(len(left_simple), len(right_simple))
    if minimum_length < 3:
        return False

    ratio = SequenceMatcher(None, left_simple, right_simple).ratio()
    if minimum_length == 3:
        return ratio >= 0.9
    return ratio >= 0.84


def _simplify_token(value: str) -> str:
    simplified = value.strip().lower()
    if not simplified:
        return ""

    for prefix in ("وال", "بال", "كال", "فال", "لل", "ال", "و", "ف", "ب", "ك", "ل"):
        if simplified.startswith(prefix) and len(simplified) - len(prefix) >= 3:
            simplified = simplified[len(prefix):]
            break

    for suffix in ("يات", "يون", "يين", "ات", "ون", "ين", "ها", "هم", "هن", "كما", "كم", "نا", "ه", "ة", "ي", "s", "es", "ed", "ing"):
        if simplified.endswith(suffix) and len(simplified) - len(suffix) >= 3:
            simplified = simplified[:-len(suffix)]
            break

    return simplified


def _partial_similarity(normalized_question: str, normalized_alias: str) -> float:
    if not normalized_question or not normalized_alias:
        return 0.0
    if normalized_alias in normalized_question or normalized_question in normalized_alias:
        return 1.0
    return SequenceMatcher(None, normalized_question, normalized_alias).ratio()


def _looks_like_goal_query(normalized_question: str) -> bool:
    return any(token in normalized_question for token in ("goal", "goals", "scor", "هدف", "اهداف", "أهداف", "سجل"))


def _goal_query_scope(normalized_question: str) -> Literal["career", "club", "unsupported"] | None:
    if not _looks_like_goal_query(normalized_question):
        return None
    if _looks_like_national_goals_query(normalized_question):
        return "unsupported"
    if _looks_like_competition_goals_query(normalized_question):
        return "unsupported"
    if _looks_like_club_goals_query(normalized_question):
        return "club"
    if _looks_like_specific_team_goals_query(normalized_question):
        return "unsupported"
    if _looks_like_career_goals_query(normalized_question):
        return "career"
    return None


def _looks_like_career_goals_query(normalized_question: str) -> bool:
    if not _looks_like_goal_query(normalized_question):
        return False
    career_markers = (
        "career", "overall", "total", "all time", "whole career", "entire career",
        "مسير", "اجمالي", "إجمالي", "طول مسير", "كل مسير",
    )
    club_markers = ("club", "clubs", "النادي", "الأندية", "الانديه", "النوادي")
    return any(marker in normalized_question for marker in career_markers) or not any(
        marker in normalized_question for marker in club_markers
    )


def _looks_like_club_goals_query(normalized_question: str) -> bool:
    if not _looks_like_goal_query(normalized_question):
        return False
    return any(
        marker in normalized_question
        for marker in (
            "club", "clubs", "with clubs", "for clubs", "for his clubs", "for their clubs",
            "النادي", "الأندية", "الانديه", "النوادي", "مع الاندية", "مع الأندية",
        )
    )


def _looks_like_national_goals_query(normalized_question: str) -> bool:
    return any(
        marker in normalized_question
        for marker in (
            "national team", "international", "for the national team", "for his country",
            "for the country", "منتخب", "المنتخب", "دولي",
        )
    )


def _looks_like_competition_goals_query(normalized_question: str) -> bool:
    return any(
        marker in normalized_question
        for marker in (
            "world cup", "champions league", "premier league", "la liga", "serie a",
            "bundesliga", "league", "cup", "tournament", "euro", "copa", "champions",
            "دوري", "كاس", "كأس", "بطوله", "بطولة", "ابطال", "أبطال", "يورو", "كوبا", "مونديال",
        )
    )


def _looks_like_specific_team_goals_query(normalized_question: str) -> bool:
    if any(marker in normalized_question for marker in ("with clubs", "for clubs", "مع الأندية", "مع الاندية")):
        return False
    if any(marker in normalized_question for marker in ("national team", "international", "المنتخب", "منتخب")):
        return False
    if re.search(r"\b(for|with|at)\s+[a-z]", normalized_question):
        return True
    if "مع " in normalized_question and "مع الأندية" not in normalized_question and "مع الاندية" not in normalized_question:
        return True
    if re.search(r"(?:^|\s)[لب][اأإآء-ي]{3,}", normalized_question):
        return True
    return False


def _looks_like_achievement_query(normalized_question: str) -> bool:
    return any(
        marker in normalized_question
        for marker in (
            "achievement", "achievements", "honour", "honors", "honours", "troph", "award", "awards",
            "ballon", "record", "records", "milestone", "لقب", "القاب", "ألقاب", "بطول", "انجاز",
            "إنجاز", "جائزه", "جائزة", "جوائز", "رقم", "ارقام", "أرقام", "قياسي", "قياسية",
            "حقق", "حققه", "ابرز", "أبرز", "اهم انجاز", "أهم إنجاز", "اهم شي حققه", "أهم شي حققه",
        )
    )


def _looks_like_club_history_query(normalized_question: str) -> bool:
    if _contains_marker_phrase(
        normalized_question,
        (
            "club history", "team history", "clubs did he play for", "teams did he play for",
            "which clubs did he play for", "which teams did he play for", "where has he played",
            "الانديه التي لعب لها", "الأندية التي لعب لها", "الفرق التي لعب لها",
            "الفرق اللي لعب لها", "التيمات التي لعب لها", "التيمات اللي لعب لها",
            "كل الاندية", "كل الأندية", "كل الفرق", "كل التيمات",
            "تسلسل الاندية", "تسلسل الأندية", "تسلسل الفرق", "تسلسل التيمات",
            "الفرق اللي مر عليها", "وش الفرق اللي مر عليها", "وش الأندية اللي مر عليها",
        ),
    ):
        return True

    team_markers = (
        "clubs", "teams", "اندية", "أندية", "الاندية", "الأندية", "نوادي", "فرق", "التيمات", "تيمات",
    )
    play_markers = ("play", "played", "لعب", "يلعب", "مر", "تنقل", "مثل")
    return _contains_marker_token(normalized_question, team_markers) and _contains_marker_token(
        normalized_question,
        play_markers,
    )


def _looks_like_full_retirement_query(normalized_question: str) -> bool:
    return any(
        marker in normalized_question
        for marker in (
            "full retirement", "fully retir", "complete retir", "completely retir", "retire from football",
            "retiring from football", "club football", "retire from club football", "final retirement",
            "الاعتزال النهائي", "اعتزل نهائي", "اعتزل نهائيا", "اعتزل نهائيًا", "اعتزل كرة القدم",
            "اعتزاله النهائي",
        )
    )


def _looks_like_alive_query(normalized_question: str) -> bool:
    return _contains_marker_phrase(normalized_question, ("passed away", "على قيد الحياة")) or _contains_marker_token(
        normalized_question,
        ("alive", "dead", "deceased", "حي", "مات", "متوف", "متوفي", "متوفى", "توفي", "توفى"),
    )


def _looks_like_summary_query(normalized_question: str) -> bool:
    return any(
        marker in normalized_question
        for marker in ("about him", "about this player", "biograph", "summary", "نبذه", "نبذة", "احكي عنه", "قول لي عنه", "معلومات عنه", "عرفني عليه")
    )


def _looks_like_national_before_retirement_query(normalized_question: str) -> bool:
    explicit_national_markers = ("national team", "international", "دولي", "المنتخب", "منتخب")

    if "before" in normalized_question and "retir" in normalized_question:
        if any(marker in normalized_question for marker in explicit_national_markers):
            return True
        if _looks_like_full_retirement_query(normalized_question) and re.search(r"\b(for|with|at)\s+[a-z]", normalized_question):
            return True

    if "قبل" in normalized_question and "اعتزل" in normalized_question:
        if any(marker in normalized_question for marker in explicit_national_markers):
            return True
        if _looks_like_full_retirement_query(normalized_question) and any(
            marker in normalized_question
            for marker in ("مع ", "للمنتخب", "لمنتخب", "للنادي", "للفريق", "لنادي", "لفريق")
        ):
            return True

    return False


def _build_base_profile(player: Player | None, payload: SharedPlayerCardRead) -> AssistantProfile:
    wikidata_id = (player.wikidata_id if player is not None else payload.wd or "").strip()
    profile = AssistantProfile(player=player, wikidata_id=wikidata_id)
    if not wikidata_id:
        return profile

    entity_payload = _fetch_wikidata_entity_payload(wikidata_id)
    if not entity_payload:
        return profile

    profile.used_sources.add("wikipedia")
    claims = entity_payload.get("claims", {}) if isinstance(entity_payload, dict) else {}
    if "P570" in claims:
        profile.is_alive = False
        profile.deceased_year = _read_claim_time_year(claims["P570"][0], "self")
    else:
        profile.is_alive = True

    stints = _extract_team_stints(claims.get("P54", []))
    if not stints:
        return profile

    team_ids = [stint["id"] for stint in stints if stint.get("id")]
    team_payload = _fetch_wikidata_entities(team_ids, props="labels|claims")
    ordered_stints: list[TeamStint] = []

    for stint in stints:
        entity_id = stint["id"]
        team_entity = team_payload.get(entity_id, {})
        name_en = _label_for(team_entity, "en")
        name_ar = _label_for(team_entity, "ar")
        is_national_team = _is_national_team_label(name_en) or _is_national_team_label(name_ar)
        league_ids = _extract_entity_ids_from_claims(team_entity.get("claims", {}), "P118")
        ordered_stints.append(
            TeamStint(
                entity_id=entity_id,
                name_en=name_en or entity_id,
                name_ar=name_ar or name_en or entity_id,
                start_year=stint["start_year"],
                end_year=stint["end_year"],
                is_national_team=is_national_team,
                league_ids=league_ids,
            )
        )

    profile.club_stints = [entry for entry in ordered_stints if not entry.is_national_team]
    profile.national_team_stints = [entry for entry in ordered_stints if entry.is_national_team]
    profile.club_retired_year = _latest_end_year(profile.club_stints)
    profile.national_team_retired_year = _latest_end_year(profile.national_team_stints)
    return profile


def _extract_team_stints(raw_claims: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for index, claim in enumerate(raw_claims):
        entity_id = (
            claim.get("mainsnak", {})
            .get("datavalue", {})
            .get("value", {})
            .get("id", "")
        )
        if not isinstance(entity_id, str) or not entity_id.strip():
            continue

        normalized.append(
            {
                "id": entity_id.strip(),
                "start_year": _read_claim_time_year(claim, "P580"),
                "end_year": _read_claim_time_year(claim, "P582"),
                "rank": str(claim.get("rank", "normal")),
                "index": index,
            }
        )

    normalized.sort(
        key=lambda item: (
            item["start_year"] if item["start_year"] is not None else 999999,
            item["end_year"] if item["end_year"] is not None else 999999,
            0 if item["rank"] == "preferred" else 1,
            item["index"],
        )
    )
    return normalized


def _answer_from_profile(
    profile: AssistantProfile,
    payload: SharedPlayerCardRead,
    intent_key: str,
    language: Literal["ar", "en"],
) -> str:
    player_name = (payload.na or payload.n) if language == "ar" else payload.n
    country = (payload.c or payload.ce) if language == "ar" else (payload.ce or payload.c)
    club_name = (payload.cta or payload.ct) if language == "ar" else (payload.ct or payload.cta)
    birth_year = payload.y
    age = max(0, datetime.now(timezone.utc).year - birth_year) if birth_year else None
    position = _position_label(payload.p, language)

    if intent_key == "player_name":
        return f"اسم اللاعب هو {player_name}." if language == "ar" else f"This player is {player_name}."
    if intent_key == "nationality":
        return f"جنسيته {country}." if language == "ar" else f"His nationality is {country}."
    if intent_key == "position":
        return f"يلعب في مركز {position}." if language == "ar" else f"He plays as a {position}."
    if intent_key == "age":
        if not age:
            return _answer_not_found(language)
        return f"عمره تقريباً {age} سنة." if language == "ar" else f"He is about {age} years old."
    if intent_key == "birth_year":
        return f"ولد في {birth_year}." if language == "ar" else f"He was born in {birth_year}."
    if intent_key == "current_club":
        if club_name:
            return f"ناديه الحالي {club_name}." if language == "ar" else f"His current club is {club_name}."
        return _answer_not_found(language)
    if intent_key == "club_history":
        clubs = _unique_team_names(profile.club_stints, language)
        if clubs:
            prefix = "تسلسل الأندية" if language == "ar" else "Club history"
            return f"{prefix}: {' - '.join(clubs)}."
        if club_name:
            return f"آخر نادي ظاهر عندي هو {club_name}." if language == "ar" else f"The latest club I can confirm is {club_name}."
        return _answer_not_found(language)
    if intent_key == "active_status":
        if payload.a == 1:
            return "نعم، اللاعب ما زال يلعب." if language == "ar" else "Yes, he is still active."
        if profile.club_retired_year:
            return (
                f"اللاعب معتزل، وآخر سنة اعتزال أندية ظهرت لي هي {profile.club_retired_year}."
                if language == "ar"
                else f"He is retired. The latest club-retirement year I found is {profile.club_retired_year}."
            )
        return "اللاعب معتزل." if language == "ar" else "He is retired."
    if intent_key == "alive_status":
        if profile.is_alive is True:
            return "نعم، اللاعب على قيد الحياة." if language == "ar" else "Yes, the player is alive."
        if profile.is_alive is False and profile.deceased_year:
            return (
                f"لا، اللاعب متوفى وتاريخ الوفاة الظاهر هو {profile.deceased_year}."
                if language == "ar"
                else f"No. The player is deceased, and the year I found is {profile.deceased_year}."
            )
        if profile.is_alive is False:
            return "لا، اللاعب متوفى." if language == "ar" else "No, the player is deceased."
        return _answer_not_found(language)
    if intent_key == "national_team_retirement_year":
        if profile.national_team_retired_year:
            return (
                f"آخر سنة اعتزال دولي وجدتها هي {profile.national_team_retired_year}."
                if language == "ar"
                else f"The latest international-retirement year I found is {profile.national_team_retired_year}."
            )
        return _answer_not_found(language)
    if intent_key == "club_retirement_year":
        if payload.a == 1:
            return "اللاعب ما زال نشطاً، لذلك لا يوجد اعتزال نهائي حتى الآن." if language == "ar" else "He is still active, so there is no full retirement year yet."
        if profile.club_retired_year:
            return (
                f"آخر سنة اعتزال نهائي وجدتها هي {profile.club_retired_year}."
                if language == "ar"
                else f"The latest full-retirement year I found is {profile.club_retired_year}."
            )
        return _answer_not_found(language)
    if intent_key == "career_goals":
        _load_career_goal_totals(profile, payload, language)
        if profile.career_goals_total is not None:
            club_goals = profile.club_goals_total or 0
            national_goals = profile.national_team_goals_total or 0
            if language == "ar":
                return (
                    f"إجمالي أهدافه على مستوى المسيرة الاحترافية هو {profile.career_goals_total}، "
                    f"منها {club_goals} مع الأندية و{national_goals} مع المنتخب الأول."
                )
            return (
                f"His full senior-career goal total is {profile.career_goals_total}, "
                f"with {club_goals} for clubs and {national_goals} for the senior national team."
            )
        return _answer_not_found(language)
    if intent_key == "club_goals":
        _load_career_goal_totals(profile, payload, language)
        if profile.club_goals_total is not None:
            return (
                f"إجمالي أهدافه مع الأندية هو {profile.club_goals_total}."
                if language == "ar"
                else f"His club-goal total is {profile.club_goals_total}."
            )
        return _answer_not_found(language)
    if intent_key == "national_retired_before_club_retired":
        return _answer_national_before_club(profile, payload, language)

    return _answer_not_found(language)


def _answer_competition(
    profile: AssistantProfile,
    payload: SharedPlayerCardRead,
    competition: AssistantCompetition,
    language: Literal["ar", "en"],
) -> str:
    matching_clubs = [
        stint.name_ar if language == "ar" and stint.name_ar else stint.name_en
        for stint in profile.club_stints
        if competition.wikidata_id and competition.wikidata_id in stint.league_ids
    ]
    player_name = (payload.na or payload.n) if language == "ar" else payload.n
    competition_name = competition.name_ar if language == "ar" and competition.name_ar else competition.name_en

    if matching_clubs:
        club_list = " - ".join(dict.fromkeys(matching_clubs))
        return (
            f"نعم، {player_name} لعب في {competition_name}. الأندية المطابقة: {club_list}."
            if language == "ar"
            else f"Yes. {player_name} played in {competition_name}. Matching clubs: {club_list}."
        )

    if profile.club_stints:
        return (
            f"لا، لم يلعب في {competition_name}."
            if language == "ar"
            else f"No. He did not play in {competition_name}."
        )

    return _answer_not_found(language)


def _answer_team_membership(
    profile: AssistantProfile,
    payload: SharedPlayerCardRead,
    team_name: str,
    language: Literal["ar", "en"],
) -> str:
    normalized_team = _normalize_question(team_name)
    if not normalized_team:
        return _missing_team(language)

    matching_clubs = [
        stint for stint in profile.club_stints
        if _name_matches_query(normalized_team, stint.name_en) or _name_matches_query(normalized_team, stint.name_ar)
    ]
    player_name = (payload.na or payload.n) if language == "ar" else payload.n
    display_team = team_name.strip()

    if matching_clubs:
        return (
            f"نعم، {player_name} لعب مع {display_team}."
            if language == "ar"
            else f"Yes. {player_name} played for {display_team}."
        )

    if profile.club_stints:
        return (
            f"لا، لم يلعب مع {display_team}."
            if language == "ar"
            else f"No. {player_name} did not play for {display_team}."
        )

    return _answer_not_found(language)


def _answer_national_before_club(
    profile: AssistantProfile,
    payload: SharedPlayerCardRead,
    language: Literal["ar", "en"],
) -> str:
    national_year = profile.national_team_retired_year
    club_year = profile.club_retired_year

    if national_year is None:
        return _answer_not_found(language)

    if payload.a == 1:
        return (
            f"نعم، اعتزل دولياً في {national_year} بينما اللاعب ما زال يلعب على مستوى الأندية."
            if language == "ar"
            else f"Yes. He retired internationally in {national_year} while still playing club football."
        )

    if club_year is None:
        return _answer_not_found(language)

    if national_year < club_year:
        return (
            f"نعم، اعتزل دولياً في {national_year} قبل اعتزاله النهائي في {club_year}."
            if language == "ar"
            else f"Yes. He retired internationally in {national_year} before fully retiring in {club_year}."
        )
    if national_year == club_year:
        return (
            f"لا، الاعتزال الدولي والاعتزال النهائي ظهرا في نفس السنة {club_year}."
            if language == "ar"
            else f"No. The international and full retirement years both came out as {club_year}."
        )

    return (
        f"لا، سنة الاعتزال الدولي التي وجدتها هي {national_year} وهي بعد سنة الاعتزال النهائي {club_year}."
        if language == "ar"
        else f"No. The international-retirement year I found is {national_year}, which is after the full-retirement year {club_year}."
    )


def _load_career_goal_totals(
    profile: AssistantProfile,
    payload: SharedPlayerCardRead,
    language: Literal["ar", "en"],
) -> None:
    if profile.career_stats_loaded:
        return

    rag_document = _load_player_rag_document(profile, payload)
    if rag_document is not None:
        goal_totals = get_player_rag_goal_totals(rag_document)
        profile.club_goals_total = goal_totals.get("club_goals_total")
        profile.national_team_goals_total = goal_totals.get("national_team_goals_total")
        profile.career_goals_total = goal_totals.get("career_goals_total")
        if (
            profile.club_goals_total is not None
            or profile.national_team_goals_total is not None
            or profile.career_goals_total is not None
        ):
            profile.career_stats_loaded = True
            profile.used_sources.add("player_rag")
            _set_player_rag_trace(
                profile,
                source_kind="career_totals",
                source_title="Arabic Career Totals" if language == "ar" else "English Career Totals",
                source_language=language,
                source_excerpt=(
                    f"club_goals_total={profile.club_goals_total}, "
                    f"national_team_goals_total={profile.national_team_goals_total}, "
                    f"career_goals_total={profile.career_goals_total}"
                ),
            )
            return

    profile.career_stats_loaded = True
    for page_language in ("en", "ar"):
        title = _resolve_wikipedia_title(profile.wikidata_id, page_language, payload)
        if not title:
            continue

        stats = _fetch_wikipedia_career_stats(title, page_language)
        if not stats:
            continue

        profile.club_stat_lines = stats["club_rows"]
        profile.national_stat_lines = stats["national_rows"]
        profile.club_goals_total = stats["club_goals_total"]
        profile.national_team_goals_total = stats["national_team_goals_total"]
        profile.career_goals_total = stats["career_goals_total"]
        if (
            profile.club_goals_total is not None
            or profile.national_team_goals_total is not None
            or profile.career_goals_total is not None
        ):
            profile.used_sources.add("wikipedia")
            return


def _fetch_wikipedia_career_stats(
    title: str,
    language: Literal["ar", "en"],
) -> dict[str, object] | None:
    normalized_title = clean_text(title)
    if not normalized_title:
        return None

    payload = _fetch_json(
        WIKIPEDIA_API_URLS[language],
        {
            "action": "parse",
            "page": normalized_title,
            "prop": "text",
            "formatversion": "2",
            "format": "json",
        },
    )
    if not isinstance(payload, dict):
        return None

    parse_block = payload.get("parse", {})
    html = parse_block.get("text", "") if isinstance(parse_block, dict) else ""
    if not isinstance(html, str) or not html.strip():
        return None

    return parse_wikipedia_career_stats(html)


def _load_summary(
    profile: AssistantProfile,
    payload: SharedPlayerCardRead,
    language: Literal["ar", "en"],
) -> str:
    existing = profile.summary_ar if language == "ar" else profile.summary_en
    if existing:
        return existing

    rag_document = _load_player_rag_document(profile, payload)
    if rag_document is not None:
        rag_summary = get_player_rag_summary(rag_document, language)
        if rag_summary:
            if language == "ar":
                profile.summary_ar = rag_summary
            else:
                profile.summary_en = rag_summary
            profile.used_sources.add("player_rag")
            _set_player_rag_trace(
                profile,
                source_kind="summary",
                source_title="Arabic Introduction" if language == "ar" else "English Introduction",
                source_language=language,
                source_excerpt=rag_summary,
            )
            return rag_summary

    title = _resolve_wikipedia_title(profile.wikidata_id, language, payload)
    if not title:
        return ""

    summary = _fetch_wikipedia_summary(title, language) or ""
    if language == "ar":
        profile.summary_ar = summary
    else:
        profile.summary_en = summary

    if summary:
        profile.used_sources.add("wikipedia")
    return summary


def _load_achievements(
    profile: AssistantProfile,
    payload: SharedPlayerCardRead,
    language: Literal["ar", "en"],
) -> list[str]:
    existing = profile.achievements_ar if language == "ar" else profile.achievements_en
    if existing:
        return existing

    rag_document = _load_player_rag_document(profile, payload)
    if rag_document is not None:
        rag_achievements = get_player_rag_achievements(rag_document, language)
        if rag_achievements:
            if language == "ar":
                profile.achievements_ar = rag_achievements
            else:
                profile.achievements_en = rag_achievements
            profile.used_sources.add("player_rag")
            _set_player_rag_trace(
                profile,
                source_kind="achievements",
                source_title="Arabic Achievements" if language == "ar" else "English Achievements",
                source_language=language,
                source_excerpt=" - ".join(rag_achievements[:4]),
            )
            return rag_achievements

    title = _resolve_wikipedia_title(profile.wikidata_id, language, payload)
    if not title:
        return []

    achievements = _fetch_wikipedia_achievements(title, language)
    if not achievements and language == "ar":
        english_title = _resolve_wikipedia_title(profile.wikidata_id, "en", payload)
        if english_title:
            achievements = _localize_achievement_items(
                _fetch_wikipedia_achievements(english_title, "en"),
                language,
            )
    if not achievements:
        summary = _load_summary(profile, payload, language)
        achievements = _extract_achievements_from_summary(summary, language)
    if not achievements and language == "ar":
        if not achievements:
            english_summary = _load_summary(profile, payload, "en")
            achievements = _localize_achievement_items(
                _extract_achievements_from_summary(english_summary, "en"),
                language,
            )
    if language == "ar":
        profile.achievements_ar = achievements
    else:
        profile.achievements_en = achievements
    if achievements:
        profile.used_sources.add("wikipedia")
    return achievements


def _resolve_wikipedia_title(
    wikidata_id: str,
    language: Literal["ar", "en"],
    payload: SharedPlayerCardRead,
) -> str:
    if wikidata_id:
        entity_payload = _fetch_wikidata_entity_payload(wikidata_id)
        if entity_payload:
            sitelinks = entity_payload.get("sitelinks", {})
            site_key = f"{language}wiki"
            title = clean_text(sitelinks.get(site_key, {}).get("title", "")) if isinstance(sitelinks, dict) else ""
            if title:
                return title

    candidate = payload.na if language == "ar" else payload.n
    return clean_text(candidate)


def _fetch_wikidata_entity_payload(wikidata_id: str) -> dict[str, object]:
    normalized_id = wikidata_id.strip()
    if not normalized_id:
        return {}

    payload = _fetch_json(
        WIKIDATA_API_URL,
        {
            "action": "wbgetentities",
            "ids": normalized_id,
            "props": "claims|sitelinks",
            "format": "json",
        },
    )
    if not isinstance(payload, dict):
        return {}
    entity = payload.get("entities", {}).get(normalized_id, {})
    return entity if isinstance(entity, dict) else {}


def _fetch_wikidata_entities(ids: list[str], props: str) -> dict[str, dict[str, object]]:
    unique_ids = [entry for entry in dict.fromkeys(id_.strip() for id_ in ids if id_.strip())]
    if not unique_ids:
        return {}

    payload = _fetch_json(
        WIKIDATA_API_URL,
        {
            "action": "wbgetentities",
            "ids": "|".join(unique_ids),
            "props": props,
            "languages": "en|ar",
            "languagefallback": "1",
            "format": "json",
        },
    )
    if not isinstance(payload, dict):
        return {}

    entities = payload.get("entities", {})
    if not isinstance(entities, dict):
        return {}

    return {
        entity_id: entity
        for entity_id, entity in entities.items()
        if isinstance(entity, dict)
    }


def _fetch_wikipedia_summary(title: str, language: Literal["ar", "en"]) -> str:
    normalized_title = clean_text(title)
    if not normalized_title:
        return ""

    payload = _fetch_json_url(
        f"{WIKIPEDIA_SUMMARY_URLS[language]}/{_url_quote(normalized_title)}"
    )
    if not isinstance(payload, dict):
        return ""

    extract = clean_text(str(payload.get("extract", "") or ""))
    if extract:
        return _shorten_summary(extract)

    description = clean_text(str(payload.get("description", "") or ""))
    return description


def _fetch_wikipedia_achievements(title: str, language: Literal["ar", "en"]) -> list[str]:
    normalized_title = clean_text(title)
    if not normalized_title:
        return []

    payload = _fetch_json(
        WIKIPEDIA_API_URLS[language],
        {
            "action": "parse",
            "page": normalized_title,
            "prop": "text",
            "formatversion": "2",
            "format": "json",
        },
    )
    if not isinstance(payload, dict):
        return []

    parse_block = payload.get("parse", {})
    html = parse_block.get("text", "") if isinstance(parse_block, dict) else ""
    if not isinstance(html, str) or not html.strip():
        return []

    return _extract_achievements_from_html(html, language)


def _extract_achievements_from_html(html: str, language: Literal["ar", "en"]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".mw-parser-output") or soup
    headings = root.select(".mw-heading2, .mw-heading3, h2, h3, .mw-headline")
    section_start = next(
        (
            node
            for node in headings
            if _is_achievement_heading(clean_text(node.get_text(" ", strip=True)))
        ),
        None,
    )
    if section_start is None:
        return []

    start_container = section_start
    if hasattr(section_start, "parent") and section_start.parent is not None:
        parent_name = getattr(section_start.parent, "name", "") or ""
        if parent_name in {"div", "h2", "h3", "h4"}:
            start_container = section_start.parent

    groups: list[tuple[str, list[str]]] = []
    current_group = ""
    node = getattr(start_container, "next_sibling", None)
    while node is not None:
        name = getattr(node, "name", None)
        if name in {"h2"} or ("mw-heading2" in getattr(node, "get", lambda *_: [])("class", [])):
            break

        node_classes = getattr(node, "get", lambda *_: [])("class", []) or []
        if "hatnote" in node_classes:
            node = node.next_sibling
            continue

        if name in {"h3", "h4"} or any(class_name in {"mw-heading3", "mw-heading4"} for class_name in node_classes):
            heading_text = clean_text(node.get_text(" ", strip=True))
            if heading_text:
                current_group = heading_text
            node = node.next_sibling
            continue

        if name == "p":
            bold = node.find("b")
            bold_label = clean_text(bold.get_text(" ", strip=True) if bold else "")
            if bold_label and len(bold_label) <= 60:
                current_group = bold_label
            node = node.next_sibling
            continue

        if name != "ul":
            node = node.next_sibling
            continue

        items = [
            _normalize_achievement_label(clean_text(item.get_text(" ", strip=True)))
            for item in node.find_all("li", recursive=False)
        ]
        items = [item for item in items if item]
        if items:
            groups.append((current_group, items))
        node = node.next_sibling

    filtered_groups = [entry for entry in groups if not _should_skip_group(entry[0])]
    source_groups = filtered_groups if len(filtered_groups) >= 3 else groups
    seen: set[str] = set()
    achievements: list[str] = []
    max_items = 8

    for group, items in source_groups:
        if not items:
            continue
        formatted = _format_achievement_display(group, items[0], language)
        key = _normalize_question(formatted)
        if formatted and key not in seen and len(achievements) < max_items:
            seen.add(key)
            achievements.append(formatted)

    for group, items in source_groups:
        for item in items:
            formatted = _format_achievement_display(group, item, language)
            key = _normalize_question(formatted)
            if formatted and key not in seen and len(achievements) < max_items:
                seen.add(key)
                achievements.append(formatted)

    return achievements


def _extract_achievements_from_summary(summary: str, language: Literal["ar", "en"]) -> list[str]:
    cleaned = clean_text(summary)
    if not cleaned:
        return []

    markers = (
        ("world cup", "champion", "won", "winner", "ballon", "award", "title", "titles", "captain")
        if language == "en"
        else ("كاس", "كأس", "فاز", "بطل", "بطولة", "الكرة الذهبية", "جائزة", "لقب", "القاب", "ألقاب")
    )
    achievements: list[str] = []
    for sentence in _split_sentences(cleaned):
        normalized_sentence = _normalize_question(sentence)
        if any(marker in normalized_sentence for marker in markers):
            achievements.append(sentence.rstrip(" .،؛;:"))
        if len(achievements) >= 3:
            break
    return achievements


def _localize_achievement_items(items: list[str], language: Literal["ar", "en"]) -> list[str]:
    if language != "ar":
        return items
    return [_localize_achievement_item(item) for item in items]


def _localize_achievement_item(value: str) -> str:
    cleaned = clean_text(value)
    replacements = {
        "With Player: ": "فردية: ",
        "With Manager: ": "كمدرب: ",
        "With ": "مع ",
        "Individual: ": "فردية: ",
        "Awards: ": "جوائز: ",
        "Award: ": "جائزة: ",
        "Records: ": "أرقام قياسية: ",
        "Orders: ": "أوسمة: ",
        "Special awards: ": "جوائز خاصة: ",
    }
    for source, target in replacements.items():
        if cleaned.startswith(source):
            return f"{target}{cleaned.removeprefix(source)}"
    return cleaned


def _read_claim_time_year(claim: dict[str, object], property_key: str) -> int | None:
    if property_key == "self":
        time_value = (
            claim.get("mainsnak", {})
            .get("datavalue", {})
            .get("value", {})
            .get("time", "")
        )
    else:
        time_value = (
            claim.get("qualifiers", {})
            .get(property_key, [{}])[0]
            .get("datavalue", {})
            .get("value", {})
            .get("time", "")
        )
    if not isinstance(time_value, str):
        return None
    matched = re.search(r"([+-]?\d{4})", time_value)
    return int(matched.group(1).replace("+", "")) if matched else None


def _extract_entity_ids_from_claims(claims: dict[str, object], property_key: str) -> list[str]:
    values = claims.get(property_key, [])
    if not isinstance(values, list):
        return []

    entity_ids: list[str] = []
    for claim in values:
        entity_id = (
            claim.get("mainsnak", {})
            .get("datavalue", {})
            .get("value", {})
            .get("id", "")
        )
        if isinstance(entity_id, str) and entity_id and entity_id not in entity_ids:
            entity_ids.append(entity_id)
    return entity_ids


def _label_for(entity: dict[str, object], language: Literal["ar", "en"]) -> str:
    labels = entity.get("labels", {})
    if not isinstance(labels, dict):
        return ""

    preferred = labels.get(language, {})
    english = labels.get("en", {})
    arabic = labels.get("ar", {})
    for candidate in (preferred, english, arabic):
        if isinstance(candidate, dict):
            value = clean_text(str(candidate.get("value", "") or ""))
            if value:
                return value
    return ""


def _latest_end_year(stints: list[TeamStint]) -> int | None:
    years = [stint.end_year for stint in stints if stint.end_year]
    return max(years) if years else None


def _unique_team_names(stints: list[TeamStint], language: Literal["ar", "en"]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for stint in stints:
        label = stint.name_ar if language == "ar" and stint.name_ar else stint.name_en
        key = _normalize_question(label)
        if key and key not in seen:
            seen.add(key)
            values.append(label)
    return values


def _match_competition(
    normalized_question: str,
    competitions: list[AssistantCompetition],
    argument_tail: str,
) -> AssistantCompetition | None:
    search_space = [candidate for candidate in (argument_tail, normalized_question) if candidate]
    best: tuple[int, AssistantCompetition] | None = None
    for competition in competitions:
        aliases = [competition.name_en, competition.name_ar, *(competition.aliases_en or []), *(competition.aliases_ar or [])]
        for alias in aliases:
            normalized_alias = _normalize_question(alias)
            if not normalized_alias:
                continue
            alias_tokens = _tokenize_for_matching(normalized_alias)
            if not alias_tokens:
                continue

            for candidate in search_space:
                candidate_tokens = _tokenize_for_matching(candidate)
                if normalized_alias in candidate:
                    score = 130 + len(alias_tokens)
                else:
                    overlap = _count_matching_tokens(candidate_tokens, alias_tokens)
                    if overlap == 0:
                        continue
                    coverage = int((overlap / max(1, len(alias_tokens))) * 100)
                    similarity = int(_partial_similarity(candidate, normalized_alias) * 100)
                    score = coverage + (similarity // 3)

                if best is None or score > best[0]:
                    best = (score, competition)
    return best[1] if best else None


def _extract_dynamic_argument(
    normalized_question: str,
    argument_tail: str,
    language: Literal["ar", "en"],
) -> str:
    candidate = argument_tail or normalized_question
    if language == "ar":
        candidate = re.sub(r"^(?:هل\s+)?(?:سبق\s+له\s+)?(?:اللعب|لعب|يلعب|احترف|مر|مثل)\s+", "", candidate).strip()
        candidate = re.sub(r"^(?:في|مع|على)\s+", "", candidate).strip()
    else:
        candidate = re.sub(r"^(?:did|does|has|have|was|is)\s+", "", candidate).strip()
        candidate = re.sub(r"^(?:play(?:ed)?|score(?:d)?|played|with|for|at|in)\s+", "", candidate).strip()
    words = [word for word in candidate.split() if word not in QUESTION_FILLER_WORDS[language]]
    return " ".join(words).strip()


def _name_matches_query(query: str, candidate_name: str) -> bool:
    normalized_candidate = _normalize_question(candidate_name)
    if not query or not normalized_candidate:
        return False
    if query in normalized_candidate or normalized_candidate in query:
        return True

    query_tokens = _tokenize_for_matching(query)
    candidate_tokens = _tokenize_for_matching(normalized_candidate)
    overlap = _count_matching_tokens(query_tokens, candidate_tokens)
    if overlap and overlap >= min(len(query_tokens), len(candidate_tokens)):
        return True

    return _partial_similarity(query, normalized_candidate) >= 0.72


def _answer_achievements(
    profile: AssistantProfile,
    payload: SharedPlayerCardRead,
    achievements: list[str],
    language: Literal["ar", "en"],
) -> str:
    if not achievements:
        return _answer_not_found(language)
    prefix = "أبرز الإنجازات" if language == "ar" else "Top achievements"
    return f"{prefix}: {' - '.join(achievements)}."


def _position_label(position_group: str, language: Literal["ar", "en"]) -> str:
    labels_en = {
        "goalkeeper": "goalkeeper",
        "defender": "defender",
        "midfielder": "midfielder",
        "forward": "forward",
    }
    labels_ar = {
        "goalkeeper": "حارس مرمى",
        "defender": "مدافع",
        "midfielder": "لاعب وسط",
        "forward": "مهاجم",
    }
    return (labels_ar if language == "ar" else labels_en).get(position_group, "غير معروف" if language == "ar" else "unknown")


def _answer_not_found(language: Literal["ar", "en"]) -> str:
    return "لا أستطيع الإجابة عن هذا السؤال." if language == "ar" else "I can't answer this question."


def _missing_summary(language: Literal["ar", "en"]) -> str:
    return _answer_not_found(language)


def _missing_competition(language: Literal["ar", "en"]) -> str:
    return _answer_not_found(language)


def _missing_team(language: Literal["ar", "en"]) -> str:
    return _answer_not_found(language)


def _is_non_senior_team_label(value: str) -> bool:
    cleaned = clean_text(value)
    if not cleaned:
        return False
    normalized = _normalize_question(cleaned)
    return bool(
        YOUTH_TEAM_PATTERN.search(cleaned)
        or re.search(r"(?:^|\s)(?:b|c|ii|ب|ج)(?:$|\s)", normalized, flags=re.IGNORECASE)
    )


def _normalize_string_list(values: list[object]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        value = clean_text(str(raw_value or ""))
        lowered = value.casefold()
        if value and lowered not in seen:
            seen.add(lowered)
            normalized.append(value)
    return normalized


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ").strip())


def _safe_int(value: object) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_question(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[\u064b-\u065f\u0670]", "", normalized)
    normalized = normalized.replace("\u0640", "")
    normalized = normalized.translate(str.maketrans({
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ؤ": "و",
        "ئ": "ي",
        "ة": "ه",
    }))
    normalized = re.sub(r"[.,!?؟،؛…(){}\[\]:\"'`~*#\\/=|+<>_-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _shorten_summary(value: str) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        return ""

    sentences = [entry.strip() for entry in re.findall(r"[^.!?؟]+[.!?؟]?", cleaned) if entry.strip()]
    if not sentences:
        return cleaned

    summary = ""
    for sentence in sentences[:3]:
        candidate = f"{summary} {sentence}".strip()
        if len(candidate) > 280 and summary:
            break
        summary = candidate
        if len(summary) >= 110 and re.search(r"[.!?؟]$", sentence):
            break
    return (summary or cleaned).strip()


def _split_sentences(value: str) -> list[str]:
    return [entry.strip() for entry in re.findall(r"[^.!?؟]+[.!?؟]?", value) if entry.strip()]


def _normalize_achievement_label(value: str) -> str:
    cleaned = clean_text(re.sub(r"\[[^\]]*]", " ", value))
    if not cleaned:
        return ""
    before_colon = cleaned.split(":")[0].strip()
    return before_colon or cleaned


def _is_achievement_heading(value: str) -> bool:
    return bool(
        re.search(r"^(honours|honors)$", value, flags=re.IGNORECASE)
        or re.search(r"(الإنجازات|الانجازات|الألقاب|الالقاب|البطولات)", value)
    )


def _should_skip_group(group: str) -> bool:
    return bool(re.search(r"\bU(?:17|18|19|20|21|23)\b|under-\d+", group, flags=re.IGNORECASE))


def _is_generic_achievement_group(group: str, language: Literal["ar", "en"]) -> bool:
    if language == "ar":
        return bool(re.search(r"(فردية|جوائز|اوسمة|أوسمة|تكريم|سجلات)", group))
    return bool(re.search(r"\b(individual|awards?|orders?|special awards?|records?|distinctions?)\b", group, flags=re.IGNORECASE))


def _format_achievement(group: str, item: str, language: Literal["ar", "en"]) -> str:
    group_label = clean_text(group)
    achievement_label = _normalize_achievement_label(item)
    if not achievement_label:
        return ""
    if not group_label:
        return achievement_label
    if language == "ar":
        return f"{group_label}: {achievement_label}"
    return f"{group_label}: {achievement_label}"


def _format_achievement_display(group: str, item: str, language: Literal["ar", "en"]) -> str:
    group_label = clean_text(group)
    formatted = _format_achievement(group, item, language)
    if not formatted or not group_label:
        return formatted

    if language == "ar":
        if group_label == "فردية" or _is_generic_achievement_group(group_label, language):
            return formatted
        return f"مع {group_label}: {_normalize_achievement_label(item)}"

    if group_label.lower() == "individual" or _is_generic_achievement_group(group_label, language):
        return formatted
    return f"With {group_label}: {_normalize_achievement_label(item)}"


def _is_national_team_label(value: str) -> bool:
    return bool(re.search(r"\bnational\b.*\bteam\b|\bU(?:17|18|19|20|21|23)\b|under-\d+|منتخب", value, flags=re.IGNORECASE))


@lru_cache(maxsize=512)
def _fetch_json_url(url: str) -> object:
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        with urlopen(request, timeout=20) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset))
    except (HTTPError, URLError, json.JSONDecodeError, TimeoutError):
        return {}


def _fetch_json(base_url: str, params: dict[str, str]) -> object:
    query = urlencode({key: value for key, value in params.items() if value})
    return _fetch_json_url(f"{base_url}?{query}")


def _url_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
