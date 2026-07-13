from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib import error, request
import http.cookiejar

SYNC_FIELDS = (
    "wikidata_id",
    "name",
    "name_ar",
    "image_url",
    "difficulty",
    "fame_score",
    "birth_year",
    "gender_key",
    "position_group",
    "is_active",
    "countries",
    "countries_ar",
    "continents",
    "continents_ar",
    "positions",
    "positions_ar",
    "aliases",
    "current_team",
    "current_team_ar",
)


def _build_opener() -> request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return request.build_opener(request.HTTPCookieProcessor(jar))


def _json_request(
    opener: request.OpenerDirector,
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    body = None
    headers = {"accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"

    req = request.Request(url, data=body, headers=headers, method=method)
    with opener.open(req, timeout=60) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return json.loads(response.read().decode(charset))


def _export_players(base_url: str, username: str, password: str) -> list[dict[str, Any]]:
    opener = _build_opener()
    normalized_base = base_url.rstrip("/")
    _json_request(
        opener,
        "POST",
        f"{normalized_base}/api/auth/login",
        {"username": username, "password": password},
    )

    offset = 0
    limit = 120
    total = None
    items: list[dict[str, Any]] = []

    while total is None or offset < total:
        payload = _json_request(
            opener,
            "GET",
            f"{normalized_base}/api/admin/players?offset={offset}&limit={limit}",
        )
        total = int(payload.get("total") or 0)
        page_items = payload.get("items") or []
        if not isinstance(page_items, list):
            raise ValueError("Unexpected admin players response payload.")
        items.extend(page_items)
        offset += len(page_items)
        if not page_items:
            break

    records: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        record = {field: item.get(field) for field in SYNC_FIELDS}
        record["admin_locked"] = bool(item.get("admin_locked"))
        records.append(record)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Export live MINU admin players into a seed JSON file.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--output", default="backend/data/players.seed.json")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        records = _export_players(args.base_url, args.username, args.password)
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} while exporting players: {detail}") from exc
    except error.URLError as exc:
        raise SystemExit(f"Network error while exporting players: {exc.reason}") from exc

    output_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(records)} players to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
