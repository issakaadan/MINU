from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from bs4 import BeautifulSoup

TOTAL_ROW_PATTERN = re.compile(r"^(total|totals|المجموع|الإجمالي|الاجمالي)$", re.IGNORECASE)
YOUTH_TEAM_PATTERN = re.compile(
    r"\bU(?:17|18|19|20|21|23)\b|under[- ]?\d+|youth|juvenil|school|schools|reserve|reserves|b team|c team|منتخب الشباب|الأولمبي|تحت\s*\d+|(?:\s|^)[بج](?:\s|$)",
    re.IGNORECASE,
)
TOTAL_LABELS = {
    "total",
    "totals",
    "المجموع",
    "الاجمالي",
    "الإجمالي",
    "اجمالي المسيره",
    "اجمالي المسيرة",
    "إجمالي المسيره",
    "إجمالي المسيرة",
}
CAREER_TOTAL_LABELS = {
    "career total",
    "overall total",
    "grand total",
    "total career",
    "career totals",
    "الاجمالي",
    "الإجمالي",
    "اجمالي المسيره",
    "اجمالي المسيرة",
    "إجمالي المسيره",
    "إجمالي المسيرة",
}


@dataclass
class CareerStatLine:
    years_label: str
    team_name: str
    apps: int | None
    goals: int | None
    section: Literal["club", "national"]
    is_total: bool = False
    is_youth: bool = False


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ").strip())


def normalize_question(value: str) -> str:
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


def parse_wikipedia_career_stats(html: str) -> dict[str, object] | None:
    soup = BeautifulSoup(html, "html.parser")
    infobox = soup.select_one("table.infobox")
    if infobox is None:
        return None

    current_section: Literal["club", "national"] | None = None
    infobox_club_rows: list[CareerStatLine] = []
    national_rows: list[CareerStatLine] = []

    for row in infobox.select("tr"):
        section_header = extract_infobox_section_header(row)
        if section_header:
            if is_senior_career_header(section_header):
                current_section = "club"
            elif is_national_career_header(section_header):
                current_section = "national"
            elif is_managerial_career_header(section_header):
                current_section = None
            continue

        if current_section not in {"club", "national"}:
            continue

        parsed_line = parse_infobox_career_stat_line(row, current_section)
        if parsed_line is None:
            continue
        if current_section == "club":
            infobox_club_rows.append(parsed_line)
        else:
            national_rows.append(parsed_line)

    if not infobox_club_rows and not national_rows:
        return None

    detailed_club_rows, detailed_club_goals_total = parse_detailed_club_stats(soup)
    club_rows = detailed_club_rows or infobox_club_rows
    club_goals_total = detailed_club_goals_total
    if club_goals_total is None:
        club_goals_total = club_goals_total_for_rows(club_rows)
    national_team_goals_total = national_team_goals_total_for_rows(national_rows)

    career_goals_total = None
    if club_goals_total is not None or national_team_goals_total is not None:
        career_goals_total = (club_goals_total or 0) + (national_team_goals_total or 0)

    return {
        "club_rows": club_rows,
        "national_rows": national_rows,
        "club_goals_total": club_goals_total,
        "national_team_goals_total": national_team_goals_total,
        "career_goals_total": career_goals_total,
    }


def extract_infobox_section_header(row) -> str:
    header = row.select_one(".infobox-header")
    if header is not None:
        return clean_text(header.get_text(" ", strip=True))

    if row.find("td", recursive=False) is not None:
        return ""

    first_cell = row.find(["th", "td"], recursive=False)
    if first_cell is None:
        return ""

    colspan_value = int(first_cell.get("colspan", "1") or "1")
    if colspan_value <= 1 and "infobox-header" not in (first_cell.get("class") or []):
        return ""
    return clean_text(first_cell.get_text(" ", strip=True))


def parse_infobox_career_stat_line(
    row,
    section: Literal["club", "national"],
) -> CareerStatLine | None:
    th_cells = row.find_all("th", recursive=False)
    td_cells = row.find_all("td", recursive=False)
    if not th_cells and not td_cells:
        return None

    years_label = ""
    team_name = ""
    apps: int | None = None
    goals: int | None = None

    if len(th_cells) == 1 and len(td_cells) >= 3:
        years_label = clean_text(th_cells[0].get_text(" ", strip=True))
        team_name = extract_career_team_name(td_cells[0])
        apps = parse_stat_number(td_cells[1].get_text(" ", strip=True))
        goals = parse_stat_number(td_cells[2].get_text(" ", strip=True))
    elif not th_cells and len(td_cells) >= 4:
        cell_texts = [clean_text(cell.get_text(" ", strip=True)) for cell in td_cells]
        if looks_like_compact_stat_header_row(cell_texts):
            return None
        years_label = cell_texts[0]
        team_name = extract_career_team_name(td_cells[1]) if len(td_cells) > 1 else ""
        apps = parse_stat_number(cell_texts[-2])
        goals = parse_stat_number(cell_texts[-1])
    else:
        return None

    normalized_team_name = normalize_question(team_name)
    normalized_years_label = normalize_question(years_label)
    is_total = bool(
        TOTAL_ROW_PATTERN.search(team_name)
        or TOTAL_ROW_PATTERN.search(years_label)
        or normalized_team_name in TOTAL_LABELS
        or normalized_years_label in TOTAL_LABELS
    )
    if is_total and not team_name:
        team_name = "Total"
    if not team_name or goals is None or team_name.lower() == "team":
        return None

    return CareerStatLine(
        years_label=years_label,
        team_name=team_name,
        apps=apps,
        goals=goals,
        section=section,
        is_total=is_total,
        is_youth=is_non_senior_team_label(team_name),
    )


def looks_like_compact_stat_header_row(cell_texts: list[str]) -> bool:
    normalized_cells = [normalize_question(text) for text in cell_texts if clean_text(text)]
    if len(normalized_cells) < 3:
        return False
    return (
        any(text in {"سنوات", "السنه", "السنة", "years", "year"} for text in normalized_cells)
        and any(text in {"فريق", "الفريق", "النادي", "team", "club"} for text in normalized_cells)
        and any(text in {"م", "مباريات", "apps", "ه", "اهداف", "أهداف", "goals"} for text in normalized_cells)
    )


def is_senior_career_header(value: str) -> bool:
    return bool(
        re.search(r"^(senior career|club career)\*?$", value, flags=re.IGNORECASE)
        or re.search(r"(المسيرة|المشوار).*(الأندية|الاحترافية|الاحترافيه|الكروية|الكرويه)", value)
        or re.search(r"الأندية التي لعب لها", value)
    )


def is_national_career_header(value: str) -> bool:
    return bool(
        re.search(r"(international career|national team)", value, flags=re.IGNORECASE)
        or re.search(r"(المسيرة|المشوار).*(الدولية|المنتخب)", value)
        or re.search(r"(المنتخب|الدولي)", value)
    )


def is_managerial_career_header(value: str) -> bool:
    return bool(
        re.search(r"(managerial career|teams managed)", value, flags=re.IGNORECASE)
        or re.search(r"(المسيرة التدريبية|التدريب|الفرق التي دربها)", value)
    )


def extract_career_team_name(team_cell) -> str:
    linked_names = [
        clean_text(anchor.get_text(" ", strip=True))
        for anchor in team_cell.select("a")
        if clean_text(anchor.get_text(" ", strip=True))
    ]
    team_name = clean_text(" ".join(linked_names)) or clean_text(team_cell.get_text(" ", strip=True))
    team_name = re.sub(r"^(?:→|->)\s*", "", team_name)
    return team_name


def parse_stat_number(value: str) -> int | None:
    matched = re.search(r"-?\d+", value.replace(",", ""))
    return int(matched.group(0)) if matched else None


def parse_detailed_club_stats(soup: BeautifulSoup) -> tuple[list[CareerStatLine], int | None]:
    best_rows: list[CareerStatLine] = []
    best_total: int | None = None
    best_score = -1

    for table in soup.select("table.wikitable"):
        if not is_detailed_club_career_table(table):
            continue

        rows, grand_total = parse_single_detailed_club_table(table)
        if not rows and grand_total is None:
            continue

        computed_total = club_goals_total_for_rows(rows)
        has_youth_rows = any(row.is_youth for row in rows)
        score = len(rows) * 10
        if grand_total is not None:
            score += 50
        if computed_total is not None:
            score += 15
        if grand_total is not None and computed_total is not None and grand_total == computed_total:
            score += 40

        if score > best_score:
            best_rows = rows
            best_total = computed_total if has_youth_rows and computed_total is not None else (grand_total if grand_total is not None else computed_total)
            best_score = score

    return best_rows, best_total


def parse_single_detailed_club_table(table) -> tuple[list[CareerStatLine], int | None]:
    grouped_rows: dict[str, CareerStatLine] = {}
    team_order: list[str] = []
    current_team = ""
    current_year_labels: list[str] = []
    season_apps_total = 0
    season_goals_total = 0
    saw_current_season = False
    grand_total: int | None = None

    def reset_current() -> None:
        nonlocal current_team, current_year_labels, season_apps_total, season_goals_total, saw_current_season
        current_team = ""
        current_year_labels = []
        season_apps_total = 0
        season_goals_total = 0
        saw_current_season = False

    def append_row(team_name: str, years_label: str, apps: int | None, goals: int | None) -> None:
        normalized_team = normalize_question(team_name)
        if not normalized_team or goals is None:
            return

        existing = grouped_rows.get(normalized_team)
        if existing is None:
            grouped_rows[normalized_team] = CareerStatLine(
                years_label=years_label,
                team_name=team_name,
                apps=apps,
                goals=goals,
                section="club",
                is_total=False,
                is_youth=is_non_senior_team_label(team_name),
            )
            team_order.append(normalized_team)
            return

        if existing.apps is None:
            existing.apps = apps
        elif apps is not None:
            existing.apps += apps

        existing.goals = (existing.goals or 0) + goals
        existing.years_label = merge_year_ranges(existing.years_label, years_label)

    def flush_current(*, explicit_apps: int | None = None, explicit_goals: int | None = None) -> None:
        if not current_team:
            reset_current()
            return

        apps_value = explicit_apps if explicit_apps is not None else (season_apps_total if saw_current_season else None)
        goals_value = explicit_goals if explicit_goals is not None else (season_goals_total if saw_current_season else None)
        if goals_value is not None:
            append_row(current_team, summarize_year_labels(current_year_labels), apps_value, goals_value)
        reset_current()

    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 3:
            continue

        cell_texts = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        if not any(cell_texts):
            continue

        normalized_label = normalize_question(cell_texts[0])
        apps_value, goals_value = extract_apps_and_goals_from_detailed_row(cell_texts)
        if apps_value is None and goals_value is None:
            continue

        if is_career_total_row_label(normalized_label):
            flush_current()
            grand_total = goals_value
            continue

        if is_total_row_label(normalized_label):
            flush_current(explicit_apps=apps_value, explicit_goals=goals_value)
            continue

        next_team, year_label = parse_detailed_team_and_year(cell_texts)
        if next_team:
            flush_current()
            current_team = next_team
        elif not current_team:
            continue

        if year_label:
            current_year_labels.append(year_label)
        if apps_value is not None:
            season_apps_total += apps_value
        if goals_value is not None:
            season_goals_total += goals_value
        saw_current_season = True

    flush_current()
    return [grouped_rows[key] for key in team_order], grand_total


def parse_detailed_team_and_year(cell_texts: list[str]) -> tuple[str, str]:
    first = cell_texts[0] if cell_texts else ""
    second = cell_texts[1] if len(cell_texts) > 1 else ""

    if looks_like_year_label(first):
        return "", first
    if is_total_row_label(normalize_question(first)):
        return "", ""
    if looks_like_year_label(second):
        return strip_footnotes(first), second
    return "", ""


def extract_apps_and_goals_from_detailed_row(cell_texts: list[str]) -> tuple[int | None, int | None]:
    numeric_values: list[int] = []
    for text in cell_texts:
        number = parse_stat_number(text)
        if number is not None:
            numeric_values.append(number)
    if len(numeric_values) < 2:
        return None, None
    return numeric_values[-2], numeric_values[-1]


def looks_like_year_label(value: str) -> bool:
    cleaned = strip_footnotes(value)
    return bool(re.match(r"^\d{4}(?:\s*[–—\-\/]\s*\d{2,4})?(?:\b|[\s\[(])", cleaned))


def strip_footnotes(value: str) -> str:
    return clean_text(re.sub(r"\[\s*[^\]]+\s*\]", "", value))


def summarize_year_labels(year_labels: list[str]) -> str:
    cleaned_labels = [strip_footnotes(label) for label in year_labels if strip_footnotes(label)]
    if not cleaned_labels:
        return ""

    years = [parse_year_bounds(label) for label in cleaned_labels]
    flattened = [year for pair in years for year in pair if year is not None]
    if not flattened:
        return cleaned_labels[0] if len(cleaned_labels) == 1 else f"{cleaned_labels[0]} - {cleaned_labels[-1]}"

    start_year = min(flattened)
    end_year = max(flattened)
    return str(start_year) if start_year == end_year else f"{start_year}–{end_year}"


def merge_year_ranges(left: str, right: str) -> str:
    values = [value for value in (left, right) if clean_text(value)]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return summarize_year_labels(values)


def parse_year_bounds(value: str) -> tuple[int | None, int | None]:
    matches = [int(match) for match in re.findall(r"\d{4}", value)]
    if not matches:
        return None, None
    return matches[0], matches[-1]


def is_detailed_club_career_table(table) -> bool:
    caption = clean_text(table.caption.get_text(" ", strip=True) if table.caption else "")
    normalized_caption = normalize_question(caption)
    if any(
        marker in normalized_caption
        for marker in (
            "appearances and goals by club",
            "career statistics",
            "المشاركات والاهداف حسب النادي",
            "الاهداف حسب النادي",
            "احصاءات المسيره",
            "احصائيات المسيره",
            "احصاءات النادي",
            "احصائيات النادي",
            "احصاءات الانديه",
            "احصائيات الانديه",
        )
    ):
        return True

    header_rows = table.find_all("tr", limit=3)
    header_text = " ".join(
        clean_text(row.get_text(" ", strip=True))
        for row in header_rows
    )
    normalized_headers = normalize_question(header_text)
    has_team_marker = any(marker in normalized_headers for marker in ("club", "النادي", "الدرجة", "division"))
    has_year_marker = any(marker in normalized_headers for marker in ("season", "year", "الموسم", "العام"))
    has_total_marker = any(marker in normalized_headers for marker in ("total", "career total", "المجموع", "الاجمالي", "الإجمالي"))
    has_goal_marker = any(marker in normalized_headers for marker in ("goal", "goals", "الاهداف", "أهداف"))
    return has_team_marker and has_year_marker and has_total_marker and has_goal_marker


def is_total_row_label(value: str) -> bool:
    return value in TOTAL_LABELS


def is_career_total_row_label(value: str) -> bool:
    return value in CAREER_TOTAL_LABELS


def club_goals_total_for_rows(rows: list[CareerStatLine]) -> int | None:
    explicit_total = next((row.goals for row in rows if row.is_total and row.goals is not None), None)
    if explicit_total is not None:
        return explicit_total

    goals = [row.goals for row in rows if row.goals is not None and not row.is_youth]
    return sum(goals) if goals else None


def national_team_goals_total_for_rows(rows: list[CareerStatLine]) -> int | None:
    explicit_total = next((row.goals for row in rows if row.is_total and row.goals is not None), None)
    if explicit_total is not None:
        return explicit_total

    goals = [row.goals for row in rows if row.goals is not None and not row.is_youth]
    return sum(goals) if goals else None


def is_non_senior_team_label(value: str) -> bool:
    cleaned = clean_text(value)
    if not cleaned:
        return False
    normalized = normalize_question(cleaned)
    return bool(
        YOUTH_TEAM_PATTERN.search(cleaned)
        or re.search(r"(?:^|\s)(?:b|c|ii|ب|ج)(?:$|\s)", normalized, flags=re.IGNORECASE)
    )
