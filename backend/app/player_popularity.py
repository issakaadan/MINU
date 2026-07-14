from __future__ import annotations

from collections.abc import Iterable

LEVEL_ONE_MIN_FAME = 80
LEVEL_TWO_MIN_FAME = 35
LEVEL_THREE_MIN_FAME = 20
SUPERSTAR_MIN_FAME = 100

# These groups represent the global prominence of each country's football
# tradition, not the strength of its current national team. Historical labels
# are kept because they occur in the Wikidata-backed player catalog.
NATIONALITY_PROMINENCE: dict[int, frozenset[str]] = {
    1: frozenset(
        {
            "Argentina", "Brazil", "England", "France", "Germany", "Italy",
            "Kingdom of Italy", "Kingdom of the Netherlands", "Netherlands",
            "Portugal", "Spain", "United Kingdom", "West Germany",
        }
    ),
    2: frozenset(
        {
            "Belgium", "Cameroon", "Colombia", "Croatia", "German Democratic Republic",
            "Japan", "Mexico", "Morocco", "Nigeria", "Senegal", "South Korea",
            "Turkey", "Uruguay",
        }
    ),
    3: frozenset(
        {
            "Algeria", "Australia", "Austria", "Bosnia and Herzegovina", "Canada",
            "Chile", "Costa Rica", "Czech Republic", "Czechoslovakia", "Denmark",
            "Egypt", "Gabon", "Ghana", "Greece", "Hungary", "Iceland", "Iran",
            "Ireland", "Ivory Coast", "Norway", "Paraguay", "Peru", "Poland",
            "Romania", "Russia", "Serbia", "Slovakia", "Slovenia", "South Africa",
            "Soviet Union", "Sweden", "Switzerland", "Tunisia", "Ukraine",
            "United States", "Yugoslavia",
        }
    ),
}


def player_fame_level(fame_score: int) -> int:
    if fame_score >= LEVEL_ONE_MIN_FAME:
        return 1
    if fame_score >= LEVEL_TWO_MIN_FAME:
        return 2
    if fame_score >= LEVEL_THREE_MIN_FAME:
        return 3
    return 4


def nationality_popularity_level(countries: Iterable[str]) -> int:
    country_values = {str(country).strip() for country in countries if str(country).strip()}
    return min(
        (level for level, names in NATIONALITY_PROMINENCE.items() if country_values & names),
        default=4,
    )


def difficulty_from_popularity(fame_score: int, countries: Iterable[str]) -> int:
    """Combine player fame (75%) with football-nationality prominence (25%)."""
    normalized_fame = int(fame_score or 0)
    if normalized_fame >= SUPERSTAR_MIN_FAME:
        return 1

    fame_level = player_fame_level(normalized_fame)
    nationality_level = nationality_popularity_level(countries)
    return max(1, min(4, (3 * fame_level + nationality_level + 2) // 4))
