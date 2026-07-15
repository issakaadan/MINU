from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "playerInfo"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "backend" / "data" / "player_rag_index.json"


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ").strip())


def is_placeholder_text(value: str) -> bool:
    normalized = clean_text(value).strip("_").casefold()
    return normalized in {
        "",
        "none",
        "unavailable",
        "no",
        "no introduction found.",
        "no honours section found.",
        "no rows found.",
        "غير متوفر",
        "غير متوفر.",
        "لا يوجد",
    }


def parse_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_title = ""
    current_lines: list[str] = []

    for raw_line in text.splitlines():
        heading_match = re.match(r"^(#{2,6})\s+(.*)$", raw_line)
        if heading_match:
            if current_title:
                sections[current_title] = "\n".join(current_lines).strip()
            current_title = clean_text(heading_match.group(2))
            current_lines = []
            continue

        if current_title:
            current_lines.append(raw_line.rstrip())

    if current_title:
        sections[current_title] = "\n".join(current_lines).strip()

    return sections


def parse_bullet_map(section_text: str) -> dict[str, str]:
    items: dict[str, str] = {}
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        body = clean_text(line[2:])
        if ":" not in body:
            continue
        key, value = body.split(":", 1)
        items[clean_text(key)] = clean_text(value)
    return items


def parse_list(section_text: str) -> list[str]:
    values: list[str] = []
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        value = clean_text(line[2:])
        if not is_placeholder_text(value):
            values.append(value)
    return [value for value in values if value]


def parse_numeric(value: str) -> int | None:
    cleaned = clean_text(value)
    if is_placeholder_text(cleaned):
        return None
    matched = re.search(r"-?\d+", cleaned.replace(",", ""))
    return int(matched.group(0)) if matched else None


def parse_markdown_table(section_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    table_lines = [line.strip() for line in section_text.splitlines() if line.strip().startswith("|")]
    if len(table_lines) < 3:
        return rows

    for line in table_lines[2:]:
        cells = [clean_text(cell) for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        years, team, apps, goals = cells[:4]
        if not team or team == "---":
            continue
        rows.append(
            {
                "years": years,
                "team": team,
                "apps": parse_numeric(apps),
                "goals": parse_numeric(goals),
            }
        )
    return rows


def format_stat_rows(rows: list[dict[str, Any]], language: str) -> list[str]:
    lines: list[str] = []
    for row in rows:
        years = clean_text(str(row.get("years", "") or ""))
        team = clean_text(str(row.get("team", "") or ""))
        apps = row.get("apps")
        goals = row.get("goals")
        if language == "ar":
            lines.append(
                f"الفترة: {years or '-'} . الفريق: {team or '-'} . المباريات: {apps if apps is not None else '-'} . الأهداف: {goals if goals is not None else '-'} ."
            )
        else:
            lines.append(
                f"Years: {years or '-'} . Team: {team or '-'} . Apps: {apps if apps is not None else '-'} . Goals: {goals if goals is not None else '-'} ."
            )
    return lines


def build_goal_lines(goal_totals: dict[str, int | None], language: str) -> list[str]:
    if not any(value is not None for value in goal_totals.values()):
        return []
    if language == "ar":
        return [
            f"مجموع أهداف الأندية: {goal_totals['club_goals_total'] if goal_totals['club_goals_total'] is not None else 'غير متوفر'}",
            f"مجموع أهداف المنتخب الأول: {goal_totals['national_team_goals_total'] if goal_totals['national_team_goals_total'] is not None else 'غير متوفر'}",
            f"مجموع أهداف المسيرة: {goal_totals['career_goals_total'] if goal_totals['career_goals_total'] is not None else 'غير متوفر'}",
        ]
    return [
        f"Club goals total: {goal_totals['club_goals_total'] if goal_totals['club_goals_total'] is not None else 'Unavailable'}",
        f"Senior national team goals total: {goal_totals['national_team_goals_total'] if goal_totals['national_team_goals_total'] is not None else 'Unavailable'}",
        f"Senior career goals total: {goal_totals['career_goals_total'] if goal_totals['career_goals_total'] is not None else 'Unavailable'}",
    ]


def build_metadata_lines(metadata: dict[str, str]) -> list[str]:
    preferred_keys = [
        "English Name",
        "Arabic Name",
        "Birth Year",
        "Active",
        "Position Group",
        "Positions (EN)",
        "Positions (AR)",
        "Countries (EN)",
        "Countries (AR)",
        "Current Team (EN)",
        "Current Team (AR)",
        "Aliases",
    ]
    lines: list[str] = []
    for key in preferred_keys:
        value = clean_text(metadata.get(key, ""))
        if value and not is_placeholder_text(value):
            lines.append(f"{key}: {value}")
    return lines


def build_description_lines(descriptions: dict[str, str], language: str) -> list[str]:
    lookup_key = "Arabic" if language == "ar" else "English"
    value = clean_text(descriptions.get(lookup_key, ""))
    if not value or is_placeholder_text(value):
        return []
    if language == "ar":
        return [f"الوصف: {value}"]
    return [f"Description: {value}"]


def make_chunk(kind: str, language: str, title: str, lines: list[str]) -> dict[str, Any] | None:
    cleaned_lines = [clean_text(line) for line in lines if clean_text(line)]
    if not cleaned_lines:
        return None
    return {
        "kind": kind,
        "language": language,
        "title": title,
        "content": "\n".join(cleaned_lines),
        "lines": cleaned_lines,
    }


def build_player_record(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    sections = parse_sections(text)

    metadata = parse_bullet_map(sections.get("Metadata", ""))
    descriptions = parse_bullet_map(sections.get("Wikidata Descriptions", ""))
    english_page = parse_bullet_map(sections.get("English Wikipedia", ""))
    arabic_page = parse_bullet_map(sections.get("Arabic Wikipedia", ""))
    english_totals = parse_bullet_map(sections.get("English Career Totals", ""))
    arabic_totals = parse_bullet_map(sections.get("Arabic Career Totals", ""))

    summary_en = clean_text(sections.get("English Introduction", ""))
    summary_ar = clean_text(sections.get("Arabic Introduction", ""))
    if is_placeholder_text(summary_en):
        summary_en = ""
    if is_placeholder_text(summary_ar):
        summary_ar = ""
    achievements_en = parse_list(sections.get("English Achievements", ""))
    achievements_ar = parse_list(sections.get("Arabic Achievements", ""))

    club_rows_en = parse_markdown_table(sections.get("English Club Career Stats", ""))
    national_rows_en = parse_markdown_table(sections.get("English National Team Stats", ""))
    club_rows_ar = parse_markdown_table(sections.get("Arabic Club Career Stats", ""))
    national_rows_ar = parse_markdown_table(sections.get("Arabic National Team Stats", ""))

    goal_totals = {
        "club_goals_total": parse_numeric(english_totals.get("Club goals total", "")),
        "national_team_goals_total": parse_numeric(english_totals.get("Senior national team goals total", "")),
        "career_goals_total": parse_numeric(english_totals.get("Senior career goals total", "")),
    }
    if goal_totals["club_goals_total"] is None:
        goal_totals["club_goals_total"] = parse_numeric(arabic_totals.get("Club goals total", ""))
    if goal_totals["national_team_goals_total"] is None:
        goal_totals["national_team_goals_total"] = parse_numeric(arabic_totals.get("Senior national team goals total", ""))
    if goal_totals["career_goals_total"] is None:
        goal_totals["career_goals_total"] = parse_numeric(arabic_totals.get("Senior career goals total", ""))

    chunks: list[dict[str, Any]] = []
    for maybe_chunk in (
        make_chunk("metadata", "multi", "Metadata", build_metadata_lines(metadata)),
        make_chunk("description", "en", "English Description", build_description_lines(descriptions, "en")),
        make_chunk("description", "ar", "Arabic Description", build_description_lines(descriptions, "ar")),
        make_chunk("summary", "en", "English Introduction", [summary_en]),
        make_chunk("summary", "ar", "Arabic Introduction", [summary_ar]),
        make_chunk("achievements", "en", "English Achievements", achievements_en),
        make_chunk("achievements", "ar", "Arabic Achievements", achievements_ar),
        make_chunk("career_totals", "en", "English Career Totals", build_goal_lines(goal_totals, "en")),
        make_chunk("career_totals", "ar", "Arabic Career Totals", build_goal_lines(goal_totals, "ar")),
        make_chunk("club_stats", "en", "English Club Career Stats", format_stat_rows(club_rows_en, "en")),
        make_chunk("national_stats", "en", "English National Team Stats", format_stat_rows(national_rows_en, "en")),
        make_chunk("club_stats", "ar", "Arabic Club Career Stats", format_stat_rows(club_rows_ar, "ar")),
        make_chunk("national_stats", "ar", "Arabic National Team Stats", format_stat_rows(national_rows_ar, "ar")),
    ):
        if maybe_chunk is not None:
            chunks.append(maybe_chunk)

    return {
        "wikidata_id": clean_text(metadata.get("Wikidata ID", "")),
        "file_name": path.name,
        "name_en": clean_text(metadata.get("English Name", "")),
        "name_ar": clean_text(metadata.get("Arabic Name", "")),
        "metadata": metadata,
        "descriptions": {
            "en": "" if is_placeholder_text(descriptions.get("English", "")) else clean_text(descriptions.get("English", "")),
            "ar": "" if is_placeholder_text(descriptions.get("Arabic", "")) else clean_text(descriptions.get("Arabic", "")),
        },
        "pages": {
            "en": {
                "available": clean_text(english_page.get("Page Available", "")).casefold() == "yes",
                "title": clean_text(english_page.get("Title", "")),
                "url": clean_text(english_page.get("URL", "")),
            },
            "ar": {
                "available": clean_text(arabic_page.get("Page Available", "")).casefold() == "yes",
                "title": clean_text(arabic_page.get("Title", "")),
                "url": clean_text(arabic_page.get("URL", "")),
            },
        },
        "summaries": {
            "en": summary_en,
            "ar": summary_ar,
        },
        "achievements": {
            "en": achievements_en,
            "ar": achievements_ar,
        },
        "career_totals": goal_totals,
        "stat_rows": {
            "en": {
                "club": club_rows_en,
                "national": national_rows_en,
            },
            "ar": {
                "club": club_rows_ar,
                "national": national_rows_ar,
            },
        },
        "chunks": chunks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a local player RAG index from playerInfo markdown files.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = Path(args.input).resolve()
    output_path = Path(args.output).resolve()

    markdown_files = sorted(
        path for path in input_dir.glob("*.md")
        if path.name.lower() != "readme.md"
    )
    if not markdown_files:
        raise RuntimeError(f"No player markdown files found in {input_dir}")

    players = [build_player_record(path) for path in markdown_files]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "player_count": len(players),
        "players": players,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Built player RAG index for {len(players)} players at {output_path}")


if __name__ == "__main__":
    main()
