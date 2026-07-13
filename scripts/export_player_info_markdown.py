from __future__ import annotations

import argparse
import json
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_PATH = PROJECT_ROOT / "backend" / "data" / "players.seed.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "playerInfo"
USER_AGENT = "MINUPlayerInfoExporter/1.0 (https://minu-theta.vercel.app)"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API_URLS = {
    "en": "https://en.wikipedia.org/w/api.php",
    "ar": "https://ar.wikipedia.org/w/api.php",
}
TOTAL_ROW_PATTERN = re.compile(r"^(total|totals|المجموع|الإجمالي|الاجمالي)$", re.IGNORECASE)
YOUTH_TEAM_PATTERN = re.compile(
    r"\bU(?:17|18|19|20|21|23)\b|under-\d+|youth|juvenil|school|schools|منتخب الشباب",
    re.IGNORECASE,
)
REQUEST_LOCK = threading.Lock()
LAST_REQUEST_AT = 0.0
REQUEST_INTERVAL_SECONDS = 0.12
WIKIDATA_BATCH_SIZE = 40


@dataclass
class CareerStatLine:
    years_label: str
    team_name: str
    apps: int | None
    goals: int | None
    section: Literal["club", "national"]
    is_total: bool = False
    is_youth: bool = False


@dataclass
class WikipediaPageExport:
    language: Literal["en", "ar"]
    title: str
    url: str
    intro: str
    achievements: list[str]
    club_rows: list[CareerStatLine]
    national_rows: list[CareerStatLine]
    club_goals_total: int | None
    national_team_goals_total: int | None
    career_goals_total: int | None
    available: bool


@dataclass
class PlayerExportResult:
    file_path: Path
    player_name: str
    wikidata_id: str
    english_available: bool
    arabic_available: bool


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


def slugify_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return normalized or "player"


def request_json_url(url: str, *, attempts: int = 5, timeout: int = 45) -> Any:
    global LAST_REQUEST_AT
    for attempt in range(1, attempts + 1):
        try:
            with REQUEST_LOCK:
                now = time.monotonic()
                delay = REQUEST_INTERVAL_SECONDS - (now - LAST_REQUEST_AT)
                if delay > 0:
                    time.sleep(delay)
                LAST_REQUEST_AT = time.monotonic()

            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(charset))
        except HTTPError as error:
            if attempt == attempts:
                return {}
            retry_after = error.headers.get("Retry-After", "").strip()
            try:
                wait_seconds = max(2, int(retry_after))
            except ValueError:
                wait_seconds = attempt * 2
            time.sleep(wait_seconds)
        except (URLError, TimeoutError, json.JSONDecodeError):
            if attempt == attempts:
                return {}
            time.sleep(attempt)
    return {}


def request_json(base_url: str, params: dict[str, str], *, attempts: int = 5, timeout: int = 45) -> Any:
    query = urlencode({key: value for key, value in params.items() if value})
    return request_json_url(f"{base_url}?{query}", attempts=attempts, timeout=timeout)


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def fetch_wikidata_entities(ids: list[str]) -> dict[str, dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    for batch in chunked(ids, WIKIDATA_BATCH_SIZE):
        payload = request_json(
            WIKIDATA_API_URL,
            {
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": "labels|descriptions|sitelinks",
                "languages": "en|ar",
                "languagefallback": "1",
                "format": "json",
            },
            timeout=60,
        )
        if not isinstance(payload, dict):
            continue
        raw_entities = payload.get("entities", {})
        if not isinstance(raw_entities, dict):
            continue
        for entity_id, entity in raw_entities.items():
            if isinstance(entity, dict):
                entities[entity_id] = entity
    return entities


def label_for(entity: dict[str, Any], language: Literal["en", "ar"]) -> str:
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


def description_for(entity: dict[str, Any], language: Literal["en", "ar"]) -> str:
    descriptions = entity.get("descriptions", {})
    if not isinstance(descriptions, dict):
        return ""

    preferred = descriptions.get(language, {})
    english = descriptions.get("en", {})
    arabic = descriptions.get("ar", {})
    for candidate in (preferred, english, arabic):
        if isinstance(candidate, dict):
            value = clean_text(str(candidate.get("value", "") or ""))
            if value:
                return value
    return ""


def wikipedia_title_for(entity: dict[str, Any], language: Literal["en", "ar"], fallback_name: str) -> str:
    sitelinks = entity.get("sitelinks", {})
    if isinstance(sitelinks, dict):
        site_key = f"{language}wiki"
        site = sitelinks.get(site_key, {})
        if isinstance(site, dict):
            title = clean_text(str(site.get("title", "") or ""))
            if title:
                return title
    return clean_text(fallback_name)


def fetch_wikipedia_page(title: str, language: Literal["en", "ar"]) -> WikipediaPageExport:
    normalized_title = clean_text(title)
    if not normalized_title:
        return WikipediaPageExport(
            language=language,
            title="",
            url="",
            intro="",
            achievements=[],
            club_rows=[],
            national_rows=[],
            club_goals_total=None,
            national_team_goals_total=None,
            career_goals_total=None,
            available=False,
        )

    payload = request_json(
        WIKIPEDIA_API_URLS[language],
        {
            "action": "parse",
            "page": normalized_title,
            "prop": "text",
            "formatversion": "2",
            "format": "json",
        },
        timeout=60,
    )
    if not isinstance(payload, dict):
        return WikipediaPageExport(
            language=language,
            title=normalized_title,
            url=f"https://{language}.wikipedia.org/wiki/{quote(normalized_title.replace(' ', '_'), safe='')}",
            intro="",
            achievements=[],
            club_rows=[],
            national_rows=[],
            club_goals_total=None,
            national_team_goals_total=None,
            career_goals_total=None,
            available=False,
        )

    parse_block = payload.get("parse", {})
    html = parse_block.get("text", "") if isinstance(parse_block, dict) else ""
    if not isinstance(html, str) or not html.strip():
        return WikipediaPageExport(
            language=language,
            title=normalized_title,
            url=f"https://{language}.wikipedia.org/wiki/{quote(normalized_title.replace(' ', '_'), safe='')}",
            intro="",
            achievements=[],
            club_rows=[],
            national_rows=[],
            club_goals_total=None,
            national_team_goals_total=None,
            career_goals_total=None,
            available=False,
        )

    intro = extract_intro_from_html(html)
    achievements = extract_achievements_from_html(html, language)
    stats = parse_wikipedia_career_stats(html)

    return WikipediaPageExport(
        language=language,
        title=normalized_title,
        url=f"https://{language}.wikipedia.org/wiki/{quote(normalized_title.replace(' ', '_'), safe='')}",
        intro=intro,
        achievements=achievements,
        club_rows=stats["club_rows"] if stats else [],
        national_rows=stats["national_rows"] if stats else [],
        club_goals_total=stats["club_goals_total"] if stats else None,
        national_team_goals_total=stats["national_team_goals_total"] if stats else None,
        career_goals_total=stats["career_goals_total"] if stats else None,
        available=True,
    )


def extract_intro_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".mw-parser-output") or soup
    paragraphs: list[str] = []

    for node in root.children:
        name = getattr(node, "name", None)
        classes = getattr(node, "get", lambda *_: [])("class", []) or []

        if name in {"h2"} or "mw-heading2" in classes:
            break
        if name != "p":
            continue
        if "mw-empty-elt" in classes:
            continue

        text = clean_text(node.get_text(" ", strip=True))
        if not text:
            continue

        paragraphs.append(text)
        if len(paragraphs) >= 3 or len(" ".join(paragraphs)) >= 1000:
            break

    return "\n\n".join(paragraphs)


def parse_wikipedia_career_stats(html: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "html.parser")
    infobox = soup.select_one("table.infobox")
    if infobox is None:
        return None

    current_section: Literal["club", "national"] | None = None
    club_rows: list[CareerStatLine] = []
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

        th_cells = row.find_all("th", recursive=False)
        td_cells = row.find_all("td", recursive=False)
        if len(th_cells) != 1 or len(td_cells) < 3:
            continue

        years_label = clean_text(th_cells[0].get_text(" ", strip=True))
        team_name = extract_career_team_name(td_cells[0])
        apps = parse_stat_number(td_cells[1].get_text(" ", strip=True))
        goals = parse_stat_number(td_cells[2].get_text(" ", strip=True))
        is_total = bool(TOTAL_ROW_PATTERN.search(team_name) or TOTAL_ROW_PATTERN.search(years_label))

        if is_total and not team_name:
            team_name = "Total"
        if not team_name or goals is None or team_name.lower() == "team":
            continue

        line = CareerStatLine(
            years_label=years_label,
            team_name=team_name,
            apps=apps,
            goals=goals,
            section=current_section,
            is_total=is_total,
            is_youth=is_non_senior_team_label(team_name),
        )
        if current_section == "club":
            club_rows.append(line)
        else:
            national_rows.append(line)

    if not club_rows and not national_rows:
        return None

    club_goals_total = club_goals_total_for_rows(club_rows)
    detailed_club_goals_total = parse_detailed_club_goals_total(soup)
    if detailed_club_goals_total is not None and (
        club_goals_total is None or detailed_club_goals_total >= club_goals_total
    ):
        club_goals_total = detailed_club_goals_total
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


def is_senior_career_header(value: str) -> bool:
    return bool(
        re.search(r"^(senior career|club career)\*?$", value, flags=re.IGNORECASE)
        or re.search(r"(المسيرة|المشوار).*(الأندية|الاحترافية|الكروية)", value)
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


def parse_detailed_club_goals_total(soup: BeautifulSoup) -> int | None:
    best_total: int | None = None
    best_score = -1

    for table in soup.select("table.wikitable"):
        if not is_detailed_club_career_table(table):
            continue

        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) < 3:
                continue

            cell_texts = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
            label = normalize_question(cell_texts[0] if cell_texts else "")
            if not is_detailed_total_row_label(label):
                continue

            numeric_values = [
                parse_stat_number(text)
                for text in cell_texts[1:]
            ]
            numeric_values = [value for value in numeric_values if value is not None]
            if not numeric_values:
                continue

            score = len(numeric_values) + (10 if "career total" in label else 0)
            if score > best_score:
                best_total = numeric_values[-1]
                best_score = score

    return best_total


def is_detailed_club_career_table(table) -> bool:
    caption = clean_text(table.caption.get_text(" ", strip=True) if table.caption else "")
    normalized_caption = normalize_question(caption)
    if any(
        marker in normalized_caption
        for marker in (
            "appearances and goals by club",
            "career statistics",
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
    return (
        any(marker in normalized_headers for marker in ("club", "season", "النادي", "الفريق", "الموسم"))
        and "total" in normalized_headers
    )


def is_detailed_total_row_label(value: str) -> bool:
    return bool(
        re.search(
            r"^(career total|total|totals|overall total|grand total|المجموع|الاجمالي|الإجمالي|اجمالي المسيره|إجمالي المسيرة)$",
            value,
            flags=re.IGNORECASE,
        )
    )


def club_goals_total_for_rows(rows: list[CareerStatLine]) -> int | None:
    explicit_total = next((row.goals for row in rows if row.is_total and row.goals is not None), None)
    if explicit_total is not None:
        return explicit_total
    values = [row.goals for row in rows if row.goals is not None and not row.is_total]
    return sum(values) if values else None


def national_team_goals_total_for_rows(rows: list[CareerStatLine]) -> int | None:
    values = [
        row.goals
        for row in rows
        if row.goals is not None and not row.is_total and not row.is_youth
    ]
    return sum(values) if values else None


def is_non_senior_team_label(value: str) -> bool:
    return bool(
        YOUTH_TEAM_PATTERN.search(value)
        or re.search(r"\bB\b|\bolympic\b|\bamateur\b", value, flags=re.IGNORECASE)
    )


def extract_achievements_from_html(html: str, language: Literal["en", "ar"]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".mw-parser-output") or soup
    headings = root.select(".mw-heading2, .mw-heading3, h2, h3, .mw-headline")
    section_start = next(
        (
            node
            for node in headings
            if is_achievement_heading(clean_text(node.get_text(" ", strip=True)))
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
            normalize_achievement_label(clean_text(item.get_text(" ", strip=True)))
            for item in node.find_all("li", recursive=False)
        ]
        items = [item for item in items if item]
        if items:
            groups.append((current_group, items))
        node = node.next_sibling

    filtered_groups = [entry for entry in groups if not should_skip_group(entry[0])]
    source_groups = filtered_groups if len(filtered_groups) >= 3 else groups
    seen: set[str] = set()
    achievements: list[str] = []
    max_items = 8

    for group, items in source_groups:
        if not items:
            continue
        formatted = format_achievement_display(group, items[0], language)
        key = normalize_question(formatted)
        if formatted and key not in seen and len(achievements) < max_items:
            seen.add(key)
            achievements.append(formatted)

    for group, items in source_groups:
        for item in items:
            formatted = format_achievement_display(group, item, language)
            key = normalize_question(formatted)
            if formatted and key not in seen and len(achievements) < max_items:
                seen.add(key)
                achievements.append(formatted)

    return achievements


def normalize_achievement_label(value: str) -> str:
    cleaned = clean_text(re.sub(r"\[[^\]]*]", " ", value))
    if not cleaned:
        return ""
    before_colon = cleaned.split(":")[0].strip()
    return before_colon or cleaned


def is_achievement_heading(value: str) -> bool:
    return bool(
        re.search(r"^(honours|honors)$", value, flags=re.IGNORECASE)
        or re.search(r"(الإنجازات|الانجازات|الألقاب|الالقاب|البطولات)", value)
    )


def should_skip_group(group: str) -> bool:
    return bool(re.search(r"\bU(?:17|18|19|20|21|23)\b|under-\d+", group, flags=re.IGNORECASE))


def is_generic_achievement_group(group: str, language: Literal["en", "ar"]) -> bool:
    if language == "ar":
        return bool(re.search(r"(فردية|جوائز|اوسمة|أوسمة|تكريم|سجلات)", group))
    return bool(re.search(r"\b(individual|awards?|orders?|special awards?|records?|distinctions?)\b", group, flags=re.IGNORECASE))


def format_achievement(group: str, item: str) -> str:
    group_label = clean_text(group)
    achievement_label = normalize_achievement_label(item)
    if not achievement_label:
        return ""
    if not group_label:
        return achievement_label
    return f"{group_label}: {achievement_label}"


def format_achievement_display(group: str, item: str, language: Literal["en", "ar"]) -> str:
    group_label = clean_text(group)
    formatted = format_achievement(group, item)
    if not formatted or not group_label:
        return formatted

    if language == "ar":
        if group_label == "فردية" or is_generic_achievement_group(group_label, language):
            return formatted
        return f"مع {group_label}: {normalize_achievement_label(item)}"

    if group_label.lower() == "individual" or is_generic_achievement_group(group_label, language):
        return formatted
    return f"With {group_label}: {normalize_achievement_label(item)}"


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|")


def format_stat_value(value: int | None) -> str:
    return "-" if value is None else str(value)


def format_string_list(values: list[str]) -> str:
    cleaned = [clean_text(value) for value in values if clean_text(value)]
    return ", ".join(cleaned) if cleaned else "None"


def render_stats_table(rows: list[CareerStatLine]) -> str:
    if not rows:
        return "_No rows found._"

    lines = [
        "| Years | Team | Apps | Goals |",
        "| --- | --- | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_escape(row.years_label or "-"),
                    markdown_escape(row.team_name or "-"),
                    format_stat_value(row.apps),
                    format_stat_value(row.goals),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def render_achievements(items: list[str]) -> str:
    if not items:
        return "_No honours section found._"
    return "\n".join(f"- {item}" for item in items)


def render_intro(text: str) -> str:
    return text if text else "_No introduction found._"


def build_markdown(
    player: dict[str, Any],
    entity: dict[str, Any],
    english_page: WikipediaPageExport,
    arabic_page: WikipediaPageExport,
) -> str:
    english_name = clean_text(str(player.get("name", "") or "")) or label_for(entity, "en") or player["wikidata_id"]
    arabic_name = clean_text(str(player.get("name_ar", "") or "")) or label_for(entity, "ar")
    countries_en = [clean_text(value) for value in (player.get("countries") or []) if clean_text(value)]
    countries_ar = [clean_text(value) for value in (player.get("countries_ar") or []) if clean_text(value)]
    positions_en = [clean_text(value) for value in (player.get("positions") or []) if clean_text(value)]
    positions_ar = [clean_text(value) for value in (player.get("positions_ar") or []) if clean_text(value)]
    aliases = [clean_text(value) for value in (player.get("aliases") or []) if clean_text(value)]
    current_team_en = clean_text(str(player.get("current_team", "") or ""))
    current_team_ar = clean_text(str(player.get("current_team_ar", "") or ""))
    description_en = description_for(entity, "en")
    description_ar = description_for(entity, "ar")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    lines = [
        f"# {english_name}" + (f" | {arabic_name}" if arabic_name else ""),
        "",
        "## Metadata",
        f"- Wikidata ID: {player['wikidata_id']}",
        f"- Generated At (UTC): {generated_at}",
        f"- English Name: {english_name or 'Unknown'}",
        f"- Arabic Name: {arabic_name or 'Unavailable'}",
        f"- Birth Year: {player.get('birth_year') or 'Unknown'}",
        f"- Active: {'Yes' if player.get('is_active') else 'No'}",
        f"- Position Group: {clean_text(str(player.get('position_group', '') or 'unknown')) or 'unknown'}",
        f"- Positions (EN): {format_string_list(positions_en)}",
        f"- Positions (AR): {format_string_list(positions_ar)}",
        f"- Countries (EN): {format_string_list(countries_en)}",
        f"- Countries (AR): {format_string_list(countries_ar)}",
        f"- Current Team (EN): {current_team_en or 'None'}",
        f"- Current Team (AR): {current_team_ar or 'None'}",
        f"- Fame Score: {player.get('fame_score') or 0}",
        f"- Difficulty: {player.get('difficulty') or 0}",
        f"- Aliases: {format_string_list(aliases)}",
        f"- Image URL: {clean_text(str(player.get('image_url', '') or '')) or 'None'}",
        "",
        "## Wikidata Descriptions",
        f"- English: {description_en or 'None'}",
        f"- Arabic: {description_ar or 'None'}",
        "",
        "## English Wikipedia",
        f"- Page Available: {'Yes' if english_page.available else 'No'}",
        f"- Title: {english_page.title or 'Unavailable'}",
        f"- URL: {english_page.url or 'Unavailable'}",
        "",
        "### English Introduction",
        render_intro(english_page.intro),
        "",
        "### English Achievements",
        render_achievements(english_page.achievements),
        "",
        "### English Career Totals",
        f"- Club goals total: {english_page.club_goals_total if english_page.club_goals_total is not None else 'Unavailable'}",
        f"- Senior national team goals total: {english_page.national_team_goals_total if english_page.national_team_goals_total is not None else 'Unavailable'}",
        f"- Senior career goals total: {english_page.career_goals_total if english_page.career_goals_total is not None else 'Unavailable'}",
        "",
        "### English Club Career Stats",
        render_stats_table(english_page.club_rows),
        "",
        "### English National Team Stats",
        render_stats_table(english_page.national_rows),
        "",
        "## Arabic Wikipedia",
        f"- Page Available: {'Yes' if arabic_page.available else 'No'}",
        f"- Title: {arabic_page.title or 'Unavailable'}",
        f"- URL: {arabic_page.url or 'Unavailable'}",
        "",
        "### Arabic Introduction",
        render_intro(arabic_page.intro),
        "",
        "### Arabic Achievements",
        render_achievements(arabic_page.achievements),
        "",
        "### Arabic Career Totals",
        f"- Club goals total: {arabic_page.club_goals_total if arabic_page.club_goals_total is not None else 'Unavailable'}",
        f"- Senior national team goals total: {arabic_page.national_team_goals_total if arabic_page.national_team_goals_total is not None else 'Unavailable'}",
        f"- Senior career goals total: {arabic_page.career_goals_total if arabic_page.career_goals_total is not None else 'Unavailable'}",
        "",
        "### Arabic Club Career Stats",
        render_stats_table(arabic_page.club_rows),
        "",
        "### Arabic National Team Stats",
        render_stats_table(arabic_page.national_rows),
        "",
    ]
    return "\n".join(lines).strip() + "\n"


def export_player(
    player: dict[str, Any],
    entity: dict[str, Any],
    output_dir: Path,
) -> PlayerExportResult:
    english_name = clean_text(str(player.get("name", "") or "")) or label_for(entity, "en") or player["wikidata_id"]
    arabic_name = clean_text(str(player.get("name_ar", "") or "")) or label_for(entity, "ar")
    title_en = wikipedia_title_for(entity, "en", english_name)
    title_ar = wikipedia_title_for(entity, "ar", arabic_name or english_name)

    english_page = fetch_wikipedia_page(title_en, "en")
    arabic_page = fetch_wikipedia_page(title_ar, "ar") if title_ar else WikipediaPageExport(
        language="ar",
        title="",
        url="",
        intro="",
        achievements=[],
        club_rows=[],
        national_rows=[],
        club_goals_total=None,
        national_team_goals_total=None,
        career_goals_total=None,
        available=False,
    )

    file_slug = slugify_filename(english_name)
    file_path = output_dir / f"{file_slug}-{player['wikidata_id'].lower()}.md"
    file_path.write_text(
        build_markdown(player, entity, english_page, arabic_page),
        encoding="utf-8",
    )
    return PlayerExportResult(
        file_path=file_path,
        player_name=english_name,
        wikidata_id=player["wikidata_id"],
        english_available=english_page.available,
        arabic_available=arabic_page.available,
    )


def load_players(dataset_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError(f"Expected a list in {dataset_path}")
    players = [item for item in payload if isinstance(item, dict) and str(item.get("wikidata_id", "")).strip()]
    if not players:
        raise RuntimeError(f"No players found in {dataset_path}")
    return players


def build_readme(
    dataset_path: Path,
    output_dir: Path,
    results: list[PlayerExportResult],
    *,
    workers: int,
) -> str:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    english_missing = len([result for result in results if not result.english_available])
    arabic_missing = len([result for result in results if not result.arabic_available])

    lines = [
        "# playerInfo",
        "",
        "Bilingual Wikipedia/Wikidata markdown corpus for the MINU player catalog.",
        "",
        "## Summary",
        f"- Generated At (UTC): {generated_at}",
        f"- Source Dataset: `{dataset_path.relative_to(PROJECT_ROOT).as_posix()}`",
        f"- Output Directory: `{output_dir.relative_to(PROJECT_ROOT).as_posix()}`",
        f"- Player Files: {len(results)}",
        f"- Missing English Wikipedia Pages: {english_missing}",
        f"- Missing Arabic Wikipedia Pages: {arabic_missing}",
        f"- Worker Count Used: {workers}",
        "",
        "## File Naming",
        "",
        "Each file is named with an ASCII slug plus the player's Wikidata ID, for example `lionel-messi-q615.md`.",
        "",
        "## Content Included Per File",
        "",
        "- core seed metadata from the game catalog",
        "- English and Arabic Wikidata descriptions when available",
        "- English and Arabic Wikipedia page titles and URLs",
        "- English and Arabic introduction text parsed from the page",
        "- honours/achievements bullets when a relevant section exists",
        "- club and national-team career-stat tables parsed from the infobox when available",
        "",
        "## Regeneration",
        "",
        "Run the exporter again from the repo root:",
        "",
        "```powershell",
        "python scripts/export_player_info_markdown.py",
        "```",
        "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export bilingual player markdown files from Wikipedia and Wikidata.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="Optional limit for testing.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_path = Path(args.dataset).resolve()
    output_dir = Path(args.output).resolve()
    workers = max(1, int(args.workers))
    limit = max(0, int(args.limit))

    players = load_players(dataset_path)
    if limit:
        players = players[:limit]

    output_dir.mkdir(parents=True, exist_ok=True)
    for existing_file in output_dir.glob("*.md"):
        existing_file.unlink()

    wikidata_ids = [str(player["wikidata_id"]).strip() for player in players]
    entities = fetch_wikidata_entities(wikidata_ids)

    results: list[PlayerExportResult] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(export_player, player, entities.get(player["wikidata_id"], {}), output_dir): player
            for player in players
        }
        for index, future in enumerate(as_completed(futures), start=1):
            player = futures[future]
            result = future.result()
            results.append(result)
            if index % 25 == 0 or index == len(futures):
                safe_name = result.player_name.encode("ascii", "backslashreplace").decode("ascii")
                print(f"[{index}/{len(futures)}] Exported {safe_name} ({player['wikidata_id']})")

    results.sort(key=lambda item: item.file_path.name)
    readme_path = output_dir / "README.md"
    readme_path.write_text(
        build_readme(dataset_path, output_dir, results, workers=workers),
        encoding="utf-8",
    )

    print(f"Completed export for {len(results)} players into {output_dir}")
    print(f"README: {readme_path}")


if __name__ == "__main__":
    main()
