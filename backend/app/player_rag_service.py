from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAYER_RAG_INDEX_PATH = PROJECT_ROOT / "backend" / "data" / "player_rag_index.json"
FILLER_WORDS = {
    "en": {
        "the", "a", "an", "player", "footballer", "club", "clubs", "team", "teams",
        "did", "does", "do", "has", "have", "had", "he", "his", "him", "is", "was", "were",
        "in", "for", "with", "at", "to", "from", "of", "ever", "any", "this", "that",
        "play", "played", "score", "scored", "goal", "goals", "tell", "about",
    },
    "ar": {
        "اللاعب", "النادي", "الأندية", "الانديه", "الفريق", "الفرق", "هل", "كم", "وش", "شنو",
        "ايش", "إيش", "اي", "أي", "في", "مع", "له", "على", "من", "الى", "إلى", "ل",
        "لعب", "يلعب", "سجل", "هدف", "أهداف", "اهداف", "عن", "هذا", "ذلك",
    },
}
YOUTH_TEAM_PATTERN = re.compile(r"\bU(?:17|18|19|20|21|23)\b|under-\d+|\bolympic\b|\bamateur\b|\bB\b|\bC\b|الأولمبي|الشباب", re.IGNORECASE)


@dataclass(frozen=True)
class PlayerRagChunk:
    kind: str
    language: Literal["en", "ar", "multi"]
    title: str
    content: str
    lines: tuple[str, ...]
    normalized_text: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class PlayerRagDocument:
    wikidata_id: str
    name_en: str
    name_ar: str
    metadata: dict[str, str]
    descriptions: dict[str, str]
    pages: dict[str, dict[str, Any]]
    summaries: dict[str, str]
    achievements: dict[str, tuple[str, ...]]
    career_totals: dict[str, int | None]
    stat_rows: dict[str, dict[str, tuple[dict[str, Any], ...]]]
    chunks: tuple[PlayerRagChunk, ...]


@dataclass(frozen=True)
class PlayerRagHit:
    chunk: PlayerRagChunk
    score: int


@dataclass(frozen=True)
class PlayerRagAnswer:
    answer: str
    source_kind: str
    source_title: str
    source_language: str
    source_excerpt: str
    score: int | None = None


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ").strip())


def normalize_text(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[\u064b-\u065f\u0670]", "", normalized)
    normalized = normalized.replace("\u0640", "")
    normalized = normalized.translate(
        str.maketrans(
            {
                "أ": "ا",
                "إ": "ا",
                "آ": "ا",
                "ٱ": "ا",
                "ى": "ي",
                "ؤ": "و",
                "ئ": "ي",
                "ة": "ه",
            }
        )
    )
    normalized = re.sub(r"[.,!?\u061f\u060c\u061b\u2026(){}\[\]:\"'`~*#\\/=|+<>_-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def tokenize(value: str) -> list[str]:
    return [token for token in value.split() if token]


def content_tokens(value: str, language: Literal["en", "ar"]) -> list[str]:
    return [token for token in tokenize(value) if token not in FILLER_WORDS[language]]


def fuzzy_token_match(left: str, right: str) -> bool:
    if left == right:
        return True
    if len(left) >= 4 and len(right) >= 4 and (left in right or right in left):
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.8


def token_overlap(query_tokens: list[str], candidate_tokens: tuple[str, ...]) -> int:
    matched = 0
    for query_token in query_tokens:
        if any(fuzzy_token_match(query_token, token) for token in candidate_tokens):
            matched += 1
    return matched


@lru_cache(maxsize=1)
def _load_rag_documents() -> dict[str, PlayerRagDocument]:
    if not PLAYER_RAG_INDEX_PATH.exists():
        return {}

    payload = json.loads(PLAYER_RAG_INDEX_PATH.read_text(encoding="utf-8"))
    players = payload.get("players", [])
    if not isinstance(players, list):
        return {}

    documents: dict[str, PlayerRagDocument] = {}
    for record in players:
        if not isinstance(record, dict):
            continue

        wikidata_id = clean_text(str(record.get("wikidata_id", "") or ""))
        if not wikidata_id:
            continue

        raw_chunks = record.get("chunks", [])
        chunks: list[PlayerRagChunk] = []
        if isinstance(raw_chunks, list):
            for raw_chunk in raw_chunks:
                if not isinstance(raw_chunk, dict):
                    continue
                title = clean_text(str(raw_chunk.get("title", "") or ""))
                content = clean_text(str(raw_chunk.get("content", "") or ""))
                lines = tuple(
                    clean_text(str(line or ""))
                    for line in (raw_chunk.get("lines") or [])
                    if clean_text(str(line or ""))
                )
                search_text = clean_text(" ".join([title, content, *lines]))
                normalized_search_text = normalize_text(search_text)
                chunk_language = str(raw_chunk.get("language", "multi") or "multi")
                if chunk_language not in {"en", "ar", "multi"}:
                    chunk_language = "multi"
                chunks.append(
                    PlayerRagChunk(
                        kind=clean_text(str(raw_chunk.get("kind", "") or "")),
                        language=chunk_language,  # type: ignore[arg-type]
                        title=title,
                        content=content,
                        lines=lines,
                        normalized_text=normalized_search_text,
                        tokens=tuple(tokenize(normalized_search_text)),
                    )
                )

        stat_rows_payload = record.get("stat_rows", {})
        stat_rows: dict[str, dict[str, tuple[dict[str, Any], ...]]] = {
            "en": {"club": tuple(), "national": tuple()},
            "ar": {"club": tuple(), "national": tuple()},
        }
        if isinstance(stat_rows_payload, dict):
            for language in ("en", "ar"):
                language_payload = stat_rows_payload.get(language, {})
                if not isinstance(language_payload, dict):
                    continue
                for section in ("club", "national"):
                    rows = language_payload.get(section, [])
                    if isinstance(rows, list):
                        stat_rows[language][section] = tuple(row for row in rows if isinstance(row, dict))

        achievements_payload = record.get("achievements", {})
        achievements = {
            "en": tuple(clean_text(str(item or "")) for item in (achievements_payload.get("en") or []) if clean_text(str(item or ""))),
            "ar": tuple(clean_text(str(item or "")) for item in (achievements_payload.get("ar") or []) if clean_text(str(item or ""))),
        }
        summaries_payload = record.get("summaries", {})
        career_totals_payload = record.get("career_totals", {})

        documents[wikidata_id] = PlayerRagDocument(
            wikidata_id=wikidata_id,
            name_en=clean_text(str(record.get("name_en", "") or "")),
            name_ar=clean_text(str(record.get("name_ar", "") or "")),
            metadata={clean_text(str(key or "")): clean_text(str(value or "")) for key, value in (record.get("metadata") or {}).items()},
            descriptions={
                "en": clean_text(str((record.get("descriptions") or {}).get("en", "") or "")),
                "ar": clean_text(str((record.get("descriptions") or {}).get("ar", "") or "")),
            },
            pages={
                "en": dict((record.get("pages") or {}).get("en") or {}),
                "ar": dict((record.get("pages") or {}).get("ar") or {}),
            },
            summaries={
                "en": clean_text(str(summaries_payload.get("en", "") or "")),
                "ar": clean_text(str(summaries_payload.get("ar", "") or "")),
            },
            achievements=achievements,
            career_totals={
                "club_goals_total": _safe_int(career_totals_payload.get("club_goals_total")),
                "national_team_goals_total": _safe_int(career_totals_payload.get("national_team_goals_total")),
                "career_goals_total": _safe_int(career_totals_payload.get("career_goals_total")),
            },
            stat_rows=stat_rows,
            chunks=tuple(chunks),
        )

    return documents


def _safe_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_player_rag_document(
    wikidata_id: str,
    name_en: str = "",
    name_ar: str = "",
) -> PlayerRagDocument | None:
    documents = _load_rag_documents()
    normalized_id = clean_text(wikidata_id)
    if normalized_id and normalized_id in documents:
        return documents[normalized_id]

    normalized_names = {
        normalize_text(name_en),
        normalize_text(name_ar),
    }
    normalized_names.discard("")

    if not normalized_names:
        return None

    for document in documents.values():
        if normalize_text(document.name_en) in normalized_names or normalize_text(document.name_ar) in normalized_names:
            return document
    return None


def answer_player_rag_question(
    document: PlayerRagDocument,
    question: str,
    language: Literal["en", "ar"],
) -> PlayerRagAnswer | None:
    normalized_question = normalize_text(question)
    if not normalized_question:
        return None

    direct_answer = _answer_direct(document, normalized_question, language)
    if direct_answer:
        return direct_answer

    if not _should_use_search_fallback(normalized_question):
        return None

    hits = search_player_rag(document, normalized_question, language)
    if not hits or hits[0].score < 58:
        return None
    return _format_hit_answer(hits[0], normalized_question, language)


def search_player_rag(
    document: PlayerRagDocument,
    normalized_question: str,
    language: Literal["en", "ar"],
    *,
    top_k: int = 3,
) -> list[PlayerRagHit]:
    query_tokens = content_tokens(normalized_question, language)
    if not query_tokens:
        query_tokens = tokenize(normalized_question)
    if not query_tokens:
        return []

    hits: list[PlayerRagHit] = []
    for chunk in document.chunks:
        overlap = token_overlap(query_tokens, chunk.tokens)
        similarity = SequenceMatcher(None, normalized_question, chunk.normalized_text).ratio()
        if overlap == 0 and similarity < 0.24:
            continue

        score = overlap * 24 + int(similarity * 35)
        if chunk.language == language:
            score += 14
        elif chunk.language == "multi":
            score += 8

        if _looks_like_achievement_query(normalized_question) and chunk.kind == "achievements":
            score += 18
        if _looks_like_summary_query(normalized_question) and chunk.kind == "summary":
            score += 18
        if _looks_like_goal_query(normalized_question) and chunk.kind == "career_totals":
            score += 18
        if _looks_like_team_query(normalized_question) and chunk.kind == "club_stats":
            score += 14
        if _looks_like_national_query(normalized_question) and chunk.kind == "national_stats":
            score += 14

        hits.append(PlayerRagHit(chunk=chunk, score=score))

    hits.sort(key=lambda item: item.score, reverse=True)
    return hits[:top_k]


def get_player_rag_summary(document: PlayerRagDocument, language: Literal["en", "ar"]) -> str:
    preferred = clean_text(document.summaries.get(language, ""))
    if preferred:
        return _shorten_summary(preferred)
    fallback_language = "en" if language == "ar" else "ar"
    return _shorten_summary(clean_text(document.summaries.get(fallback_language, "")))


def get_player_rag_achievements(
    document: PlayerRagDocument,
    language: Literal["en", "ar"],
) -> list[str]:
    preferred = [item for item in document.achievements.get(language, ()) if clean_text(item)]
    if preferred:
        return preferred
    fallback_language = "en" if language == "ar" else "ar"
    fallback_items = [item for item in document.achievements.get(fallback_language, ()) if clean_text(item)]
    if language == "ar":
        return [_localize_achievement_item(item) for item in fallback_items]
    return fallback_items


def get_player_rag_goal_totals(document: PlayerRagDocument) -> dict[str, int | None]:
    return dict(document.career_totals)


def _answer_direct(
    document: PlayerRagDocument,
    normalized_question: str,
    language: Literal["en", "ar"],
) -> PlayerRagAnswer | None:
    if _looks_like_summary_query(normalized_question):
        summary = get_player_rag_summary(document, language)
        if summary:
            return PlayerRagAnswer(
                answer=summary,
                source_kind="summary",
                source_title="Arabic Introduction" if language == "ar" else "English Introduction",
                source_language=language,
                source_excerpt=summary,
            )

    if _looks_like_achievement_query(normalized_question):
        achievements = get_player_rag_achievements(document, language)
        if achievements:
            prefix = "أبرز الإنجازات" if language == "ar" else "Top achievements"
            answer = f"{prefix}: {' - '.join(achievements[:8])}."
            return PlayerRagAnswer(
                answer=answer,
                source_kind="achievements",
                source_title="Arabic Achievements" if language == "ar" else "English Achievements",
                source_language=language,
                source_excerpt=" - ".join(achievements[:4]),
            )

    if _looks_like_goal_query(normalized_question):
        team_goals_answer = _answer_team_specific_goals(document, normalized_question, language)
        if team_goals_answer:
            return team_goals_answer
        if (
            _looks_like_team_query(normalized_question)
            and not _looks_like_club_goals_query(normalized_question)
            and not _looks_like_national_goals_query(normalized_question)
        ):
            return None

        goal_totals = get_player_rag_goal_totals(document)
        club_goals = goal_totals.get("club_goals_total")
        national_goals = goal_totals.get("national_team_goals_total")
        career_goals = goal_totals.get("career_goals_total")

        if _looks_like_national_goals_query(normalized_question) and national_goals is not None:
            answer = (
                f"سجل {national_goals} هدفاً مع المنتخب الأول."
                if language == "ar"
                else f"He scored {national_goals} goals for the senior national team."
            )
            return PlayerRagAnswer(
                answer=answer,
                source_kind="career_totals",
                source_title="Arabic Career Totals" if language == "ar" else "English Career Totals",
                source_language=language,
                source_excerpt=answer,
            )

        if _looks_like_club_goals_query(normalized_question) and club_goals is not None:
            answer = (
                f"سجل {club_goals} هدفاً مع الأندية."
                if language == "ar"
                else f"He scored {club_goals} goals for clubs."
            )
            return PlayerRagAnswer(
                answer=answer,
                source_kind="career_totals",
                source_title="Arabic Career Totals" if language == "ar" else "English Career Totals",
                source_language=language,
                source_excerpt=answer,
            )

        if career_goals is not None:
            if language == "ar":
                club_value = club_goals if club_goals is not None else 0
                national_value = national_goals if national_goals is not None else 0
                answer = (
                    f"إجمالي أهدافه على مستوى المسيرة الاحترافية هو {career_goals}، "
                    f"منها {club_value} مع الأندية و{national_value} مع المنتخب الأول."
                )
            else:
                club_text = club_goals if club_goals is not None else 0
                national_text = national_goals if national_goals is not None else 0
                answer = (
                    f"His senior-career goal total is {career_goals}, "
                    f"with {club_text} for clubs and {national_text} for the senior national team."
                )
            return PlayerRagAnswer(
                answer=answer,
                source_kind="career_totals",
                source_title="Arabic Career Totals" if language == "ar" else "English Career Totals",
                source_language=language,
                source_excerpt=answer,
            )

    return None


def _answer_team_specific_goals(
    document: PlayerRagDocument,
    normalized_question: str,
    language: Literal["en", "ar"],
) -> PlayerRagAnswer | None:
    if not _looks_like_goal_query(normalized_question):
        return None
    if not _looks_like_team_query(normalized_question):
        return None
    best_match: tuple[str, dict[str, Any], float] | None = None
    for section in ("club", "national"):
        rows = _load_stat_rows(document, language, section)
        if not rows:
            continue
        best_row, best_score = _best_stat_row_match(normalized_question, rows, section)
        if best_row is None:
            continue
        if best_match is None or best_score > best_match[2]:
            best_match = (section, best_row, best_score)

    if best_match is None or best_match[2] < 0.72:
        return None

    section, best_row, best_score = best_match
    team_name = clean_text(str(best_row.get("team", "") or ""))
    goals = _safe_int(best_row.get("goals"))
    apps = _safe_int(best_row.get("apps"))
    if goals is None:
        return None

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
        source_title=_stat_source_title(section, language),
        source_language=language,
        source_excerpt=answer,
        score=int(best_score * 100),
    )


def _load_stat_rows(
    document: PlayerRagDocument,
    language: Literal["en", "ar"],
    section: str,
) -> list[dict[str, Any]]:
    rows = list(document.stat_rows.get(language, {}).get(section, ()))
    if language == "ar" and not rows:
        rows = list(document.stat_rows.get("en", {}).get(section, ()))
    if language == "en" and not rows:
        rows = list(document.stat_rows.get("ar", {}).get(section, ()))
    return rows


def _best_stat_row_match(
    normalized_question: str,
    rows: list[dict[str, Any]],
    section: str,
) -> tuple[dict[str, Any] | None, float]:
    best_row: dict[str, Any] | None = None
    best_score = 0.0
    mentions_youth = _question_mentions_youth_team(normalized_question)

    for row in rows:
        team_label = clean_text(str(row.get("team", "") or ""))
        normalized_team = normalize_text(team_label)
        if not normalized_team or normalized_team == "total":
            continue

        score = _name_match_score(normalized_question, normalized_team)
        if not score:
            continue

        if _row_is_youth_team(team_label):
            score += 0.08 if mentions_youth else -0.18
        else:
            score += 0.05

        if section == "national":
            score += 0.08 if _looks_like_national_query(normalized_question) else 0.02
        elif _looks_like_national_query(normalized_question):
            score -= 0.12

        if normalized_team in normalized_question:
            score += 0.08

        if score > best_score:
            best_score = score
            best_row = row

    return best_row, best_score


def _row_is_youth_team(team_label: str) -> bool:
    return bool(YOUTH_TEAM_PATTERN.search(team_label))


def _question_mentions_youth_team(normalized_question: str) -> bool:
    return bool(YOUTH_TEAM_PATTERN.search(normalized_question))


def _stat_source_title(section: str, language: Literal["en", "ar"]) -> str:
    if section == "national":
        return "Arabic National Team Stats" if language == "ar" else "English National Team Stats"
    return "Arabic Club Career Stats" if language == "ar" else "English Club Career Stats"


def _name_match_score(question: str, team_name: str) -> float:
    if team_name in question or question in team_name:
        return 1.0

    question_tokens = tokenize(question)
    team_tokens = tokenize(team_name)
    overlap = token_overlap(question_tokens, tuple(team_tokens))
    if team_tokens:
        coverage = overlap / len(team_tokens)
    else:
        coverage = 0.0
    similarity = SequenceMatcher(None, question, team_name).ratio()
    return max(coverage, similarity)


def _format_hit_answer(
    hit: PlayerRagHit,
    normalized_question: str,
    language: Literal["en", "ar"],
) -> PlayerRagAnswer | None:
    chunk = hit.chunk
    if chunk.kind == "achievements":
        prefix = "أبرز الإنجازات" if language == "ar" else "Top achievements"
        answer = f"{prefix}: {' - '.join(chunk.lines[:8])}."
        return PlayerRagAnswer(
            answer=answer,
            source_kind=chunk.kind,
            source_title=chunk.title,
            source_language=chunk.language,
            source_excerpt=" - ".join(chunk.lines[:4]),
            score=hit.score,
        )

    if chunk.kind == "career_totals":
        answer = " ".join(chunk.lines[:3])
        return PlayerRagAnswer(
            answer=answer,
            source_kind=chunk.kind,
            source_title=chunk.title,
            source_language=chunk.language,
            source_excerpt=answer,
            score=hit.score,
        )

    if chunk.kind in {"club_stats", "national_stats"}:
        relevant_lines = [
            line for line in chunk.lines
            if _name_match_score(normalized_question, normalize_text(line)) >= 0.6
        ]
        if not relevant_lines:
            relevant_lines = list(chunk.lines[:3])
        if not relevant_lines:
            return None
        answer = " ".join(relevant_lines)
        return PlayerRagAnswer(
            answer=answer,
            source_kind=chunk.kind,
            source_title=chunk.title,
            source_language=chunk.language,
            source_excerpt=answer,
            score=hit.score,
        )

    if chunk.kind in {"summary", "description", "metadata"}:
        return PlayerRagAnswer(
            answer=chunk.content,
            source_kind=chunk.kind,
            source_title=chunk.title,
            source_language=chunk.language,
            source_excerpt=chunk.content[:240],
            score=hit.score,
        )

    if not chunk.content:
        return None
    return PlayerRagAnswer(
        answer=chunk.content,
        source_kind=chunk.kind,
        source_title=chunk.title,
        source_language=chunk.language,
        source_excerpt=chunk.content[:240],
        score=hit.score,
    )


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


def _looks_like_goal_query(normalized_question: str) -> bool:
    return any(token in normalized_question for token in ("goal", "goals", "scor", "هدف", "اهداف", "أهداف", "سجل"))


def _looks_like_club_goals_query(normalized_question: str) -> bool:
    return any(
        marker in normalized_question
        for marker in ("club", "clubs", "with clubs", "for clubs", "النادي", "الأندية", "الانديه", "النوادي", "مع الأندية", "مع الاندية")
    )


def _looks_like_national_goals_query(normalized_question: str) -> bool:
    return any(
        marker in normalized_question
        for marker in ("national team", "international", "for the national team", "المنتخب", "منتخب", "دولي")
    )


def _looks_like_summary_query(normalized_question: str) -> bool:
    return any(
        marker in normalized_question
        for marker in ("about him", "about this player", "summary", "biograph", "نبذه", "نبذة", "احكي عنه", "قول لي عنه", "عرفني عليه", "معلومات عنه")
    )


def _looks_like_achievement_query(normalized_question: str) -> bool:
    return any(
        marker in normalized_question
        for marker in (
            "achievement", "achievements", "honour", "honors", "honours", "award", "awards", "ballon",
            "record", "records", "milestone", "لقب", "القاب", "ألقاب", "بطول", "انجاز", "إنجاز",
            "جائزة", "جوائز", "رقم", "ارقام", "أرقام", "قياسي", "قياسية",
        )
    )


def _looks_like_team_query(normalized_question: str) -> bool:
    return any(marker in normalized_question for marker in ("for ", "with ", "at ", "مع ", "للنادي", "لـ", "لنادي"))


def _looks_like_national_query(normalized_question: str) -> bool:
    return any(marker in normalized_question for marker in ("national team", "international", "منتخب", "المنتخب", "دولي"))


def _should_use_search_fallback(normalized_question: str) -> bool:
    return any(
        marker in normalized_question
        for marker in (
            "about", "summary", "career", "goals", "goal", "award", "awards", "honour", "honors", "honours",
            "achievement", "achievements", "played", "play", "club", "clubs", "team", "teams", "retir", "alive",
            "born", "country", "position", "stats", "record", "records", "مسيره", "مسير", "نبذه", "نبذة",
            "هدف", "اهداف", "أهداف", "انجاز", "إنجاز", "جائزة", "جوائز", "لعب", "نادي", "أندية", "المنتخب",
            "منتخب", "اعتزل", "حي", "مواليد", "مركز", "احصائيات", "إحصائيات", "رقم قياسي", "أرقام قياسية",
        )
    )


def _shorten_summary(value: str) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        return ""

    sentences = [entry.strip() for entry in re.findall(r"[^.!?\u061f]+[.!?\u061f]?", cleaned) if entry.strip()]
    if not sentences:
        return cleaned

    summary = ""
    for sentence in sentences[:3]:
        candidate = f"{summary} {sentence}".strip()
        if len(candidate) > 360 and summary:
            break
        summary = candidate
        if len(summary) >= 160 and re.search(r"[.!?\u061f]$", sentence):
            break
    return summary or cleaned
