# playerInfo

Bilingual Wikipedia/Wikidata markdown corpus for the MINU player catalog.

## Summary
- Generated At (UTC): 2026-07-13T12:13:33+00:00
- Source Dataset: `backend/data/players.seed.json`
- Output Directory: `playerInfo`
- Player Files: 506
- Missing English Wikipedia Pages: 2
- Missing Arabic Wikipedia Pages: 2
- Worker Count Used: 4

## File Naming

Each file is named with an ASCII slug plus the player's Wikidata ID, for example `lionel-messi-q615.md`.

## Content Included Per File

- core seed metadata from the game catalog
- English and Arabic Wikidata descriptions when available
- English and Arabic Wikipedia page titles and URLs
- English and Arabic introduction text parsed from the page
- honours/achievements bullets when a relevant section exists
- club and national-team career-stat tables parsed from the infobox when available

## Regeneration

Run the exporter again from the repo root:

```powershell
python scripts/export_player_info_markdown.py
```
