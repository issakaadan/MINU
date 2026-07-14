from __future__ import annotations

import sys
import unittest
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.assistant_service import answer_card_question, seed_assistant_catalog
from app.core.database import SessionLocal
from app.models import Player
from app.schemas import SharedPlayerCardRead
from app.seed import seed_database
from app.game_service import DIFFICULTY_CONFIG
from app.player_popularity import difficulty_from_popularity, nationality_popularity_level
from app.core.auth import CARD_LINK_TTL_MINUTES, _urlsafe_b64decode, auth_manager


def build_payload(player: Player) -> SharedPlayerCardRead:
    return SharedPlayerCardRead(
        m="single",
        r=1,
        s=1,
        pn="A",
        on="B",
        mk="test",
        n=player.name,
        na=player.name_ar,
        i=player.image_url,
        c=player.countries[0] if player.countries else "",
        ce=player.continents[0] if player.continents else "",
        p=player.positions[0] if player.positions else player.position_group,
        y=player.birth_year,
        a=1 if player.is_active else 0,
        ct=player.current_team,
        cta=player.current_team_ar,
        wd=player.wikidata_id,
    )


class AssistantArabicRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db = SessionLocal()
        seed_database(cls.db)
        seed_assistant_catalog(cls.db)
        player = cls.db.scalar(select(Player).where(Player.name == "Samuel Eto'o"))
        if player is None:
            raise RuntimeError("Samuel Eto'o is missing from the seeded catalog.")
        cls.payload = build_payload(player)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def ask(self, question: str):
        return answer_card_question(self.db, self.payload, question, "ar")

    def test_arabic_career_goals(self) -> None:
        answer = self.ask("كم هدف سجل في مسيرته؟")
        self.assertIn("427", answer.answer)

    def test_arabic_team_specific_goals(self) -> None:
        answer = self.ask("كم سجل لبرشلونة؟")
        self.assertIn("130", answer.answer)
        self.assertIn("برشلونة", answer.answer)

    def test_arabic_club_history(self) -> None:
        answer = self.ask("وش الفرق اللي مر عليها؟")
        self.assertIn("برشلونة", answer.answer)
        self.assertIn("مايوركا", answer.answer)
        self.assertNotIn("على قيد الحياة", answer.answer)

    def test_arabic_achievements_prompt(self) -> None:
        answer = self.ask("ايش اهم شي حققه؟")
        self.assertIn("أبرز الإنجازات", answer.answer)

    def test_arabic_team_membership(self) -> None:
        answer = self.ask("هل احترف ببرشلونة؟")
        self.assertTrue(answer.answer.startswith("نعم"))

    def test_arabic_competition_membership(self) -> None:
        answer = self.ask("هل مر على الدوري الإسباني؟")
        self.assertTrue(answer.answer.startswith("نعم"))

    def test_arabic_semantic_position_paraphrase(self) -> None:
        answer = self.ask("وش وظيفته بالملعب؟")
        self.assertEqual("position", answer.intent_key)
        self.assertIn("مهاجم", answer.answer)

    def test_arabic_semantic_club_history_paraphrase(self) -> None:
        answer = self.ask("وين لعب قبل ما يعتزل؟")
        self.assertEqual("club_history", answer.intent_key)
        self.assertIn("برشلونة", answer.answer)

    def test_unrelated_arabic_question_is_refused(self) -> None:
        answer = self.ask("ما عاصمة فرنسا؟")
        self.assertIsNone(answer.intent_key)
        self.assertEqual("لا أستطيع الإجابة عن هذا السؤال.", answer.answer)

    def test_unrelated_english_question_uses_required_refusal(self) -> None:
        answer = answer_card_question(self.db, self.payload, "What is the capital of France?", "en")
        self.assertIsNone(answer.intent_key)
        self.assertEqual("I can't answer this question.", answer.answer)


class PlayerPopularityLevelTests(unittest.TestCase):
    def test_four_popularity_levels_are_configured(self) -> None:
        self.assertEqual([1, 2, 3, 4], sorted(DIFFICULTY_CONFIG))

    def test_popularity_combines_player_fame_and_nationality(self) -> None:
        cases = [
            (220, ["Brazil"], 1),
            (220, ["Liberia"], 1),
            (90, ["Liberia"], 2),
            (34, ["Spain"], 3),
            (19, ["Brazil"], 3),
            (19, ["Liberia"], 4),
        ]
        for fame_score, countries, expected_level in cases:
            with self.subTest(fame_score=fame_score, countries=countries):
                self.assertEqual(expected_level, difficulty_from_popularity(fame_score, countries))

    def test_uses_most_prominent_nationality_for_dual_nationals(self) -> None:
        self.assertEqual(1, nationality_popularity_level(["Cape Verde", "Portugal"]))


class PlayerCardLifetimeTests(unittest.TestCase):
    def test_player_card_token_expires_after_fifteen_minutes(self) -> None:
        issued_at = int(datetime.now(timezone.utc).timestamp())
        token = auth_manager.create_card_token({"player": "test"})
        payload_token = token.split(".", 1)[0]
        token_payload = json.loads(_urlsafe_b64decode(payload_token).decode("utf-8"))
        self.assertEqual(15, CARD_LINK_TTL_MINUTES)
        self.assertGreaterEqual(token_payload["exp"] - issued_at, 899)
        self.assertLessEqual(token_payload["exp"] - issued_at, 900)


if __name__ == "__main__":
    unittest.main()
