from __future__ import annotations

import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
