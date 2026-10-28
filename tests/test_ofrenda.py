"""Tests for the parts that must never make something up.

Run them with:  python3 -m unittest discover -s tests -v
None of these need a model, and none of them open a port.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import keepsake  # noqa: E402
from core.ground import Ghost, extract_phrase, fit_echo  # noqa: E402
from core.retrieval import Index, find_disagreements, tokens  # noqa: E402
from core.store import MAX_CARDS, Altar  # noqa: E402


class FakeBackend:
    """Replies in order, so a test can watch the harness correct one."""

    name = "fake"
    model_label = "fake-model"

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        text = self.replies.pop(0) if self.replies else "{}"
        from core.llm import ChatResult

        return ChatResult(text=text, tokens=12, seconds=0.5, prompt_tokens=100)


def make_altar(cards: list[dict] | None = None) -> Altar:
    data = {
        "person": {"name": "Abram Flores", "born": "1931", "died": "2011", "relation": "grandfather"},
        "cards": cards
        or [
            {"id": "a1", "text": "He kept a bowl of tamarind candies by the register.", "by": "Rosa", "year": ""},
            {"id": "b2", "text": "Once a boy fell asleep in the chair and Abram let him sleep an hour past closing.", "by": "Elena", "year": "2001"},
            {"id": "c3", "text": "Rosa remembers that he never made a sound while he swept.", "by": "Rosa", "year": ""},
            {"id": "d4", "text": "Elena remembers that he hummed while he swept, the same three notes.", "by": "Elena", "year": ""},
            {"id": "e5", "text": "He never learned to drive.", "by": "Delia", "year": ""},
        ],
        "nights": [],
    }
    tmp = Path(tempfile.mkdtemp()) / "altar.json"
    altar = Altar(tmp, data)
    altar.save()
    return altar


class StoreTests(unittest.TestCase):
    def test_round_trip(self):
        altar = make_altar()
        loaded = Altar.load(altar.path)
        self.assertEqual(len(loaded.cards), 5)
        self.assertEqual(loaded.person["name"], "Abram Flores")

    def test_card_validation(self):
        altar = make_altar()
        with self.assertRaises(ValueError):
            altar.add_card("  ")
        with self.assertRaises(ValueError):
            altar.add_card("x" * 500)
        card = altar.add_card("He kept his own hair short.", by="Nesto")
        self.assertIn("id", card)
        self.assertTrue(altar.remove_card(card["id"]))
        self.assertFalse(altar.remove_card("nope"))

    def test_card_ceiling(self):
        many = [{"id": f"x{i}", "text": f"Memory number {i} about the shop.", "by": "Rosa"} for i in range(MAX_CARDS)]
        altar = make_altar(many)
        with self.assertRaises(ValueError):
            altar.add_card("One more memory that will not fit.")

    def test_sample_loads_and_gets_ids(self):
        path = Path(tempfile.mkdtemp()) / "altar.json"
        altar = Altar.from_sample(ROOT / "samples" / "abram.json", path)
        self.assertTrue(all(card.get("id") for card in altar.cards))
        self.assertEqual(altar.data["nights"], [])


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.cards = make_altar().cards

    def test_ranking_finds_the_right_card(self):
        index = Index(self.cards)
        ranked = index.rank("What happened when the boy fell asleep?", limit=1)
        self.assertEqual(ranked[0][0]["id"], "b2")

    def test_ranking_finds_nothing_for_unrelated_questions(self):
        index = Index(self.cards)
        self.assertEqual(index.rank("What did he think of the moon landing?", limit=2), [])

    def test_disagreement_is_spotted(self):
        pairs = find_disagreements(self.cards, floor=0.28)
        found = {tuple(sorted((pair["a"], pair["b"]))) for pair in pairs}
        self.assertIn(("c3", "d4"), found)

    def test_phrases_are_short_and_present(self):
        index = Index(self.cards)
        for phrase in index.distinctive_phrases(limit=4):
            self.assertLessEqual(len(phrase.split()), 3)
            self.assertTrue(any(phrase in card["text"].lower() for card in self.cards))

    def test_irregular_verbs_fold(self):
        self.assertIn("throw", tokens("he threw it out"))
        self.assertIn("keep", tokens("he kept it"))


class PhraseTests(unittest.TestCase):
    def test_extract_stays_inside_the_card(self):
        text = "He kept the barber pole in the garage after 2009, wrapped in a bedsheet, and would not throw it out."
        phrase = extract_phrase(text)
        self.assertTrue(3 <= len(phrase.split()) <= 6, phrase)
        self.assertIn(phrase.lower().rstrip(".,"), text.lower())

    def test_fit_echo_trims_a_whole_card(self):
        text = "Once a boy fell asleep in the chair and Abram let him sleep an hour past closing."
        self.assertIsNotNone(fit_echo(text, text))

    def test_fit_echo_rejects_words_that_are_not_there(self):
        self.assertIsNone(fit_echo("he sold shoes in Topeka", "He kept a bowl of candies."))


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.altar = make_altar()
        self.ghost = Ghost(self.altar, backend=None)

    def payload(self, card=2, echo="fell asleep in the chair", say=None):
        card_text = self.altar.cards[card - 1]["text"]
        return {
            "card": card,
            "echo": echo,
            "say": say
            or "A boy fell asleep in the chair, and Abram let him sleep an hour past closing.",
        }

    def test_accepts_a_faithful_answer(self):
        ok, reason = self.ghost._check(self.payload(), [2, 4])
        self.assertTrue(ok, reason)

    def test_rejects_a_card_outside_the_choice(self):
        ok, reason = self.ghost._check(self.payload(card=1, echo="bowl of tamarind candies",
                                                   say="He kept a bowl of tamarind candies by the register."), [2])
        self.assertFalse(ok)
        self.assertIn("card must be", reason)

    def test_rejects_invented_numbers(self):
        ok, reason = self.ghost._check(
            self.payload(say="A boy fell asleep in the chair for 3 hours past closing."), [2]
        )
        self.assertFalse(ok)

    def test_rejects_a_bare_quotation_that_is_not_on_a_card(self):
        ok, reason = self.ghost._check(
            self.payload(say='He said "sit down, mijo" while the boy fell asleep in the chair.'), [2]
        )
        self.assertFalse(ok)

    def test_rejects_wrong_author(self):
        ok, reason = self.ghost._check(
            self.payload(say="Rosa remembers that a boy fell asleep in the chair and slept past closing."),
            [2],
        )
        self.assertFalse(ok)
        self.assertIn("left by", reason)

    def test_rejects_too_many_sentences(self):
        ok, reason = self.ghost._check(
            self.payload(say="A boy fell asleep in the chair. Abram let him sleep. The shop was warm."), [2]
        )
        self.assertFalse(ok)

    def test_rejects_drift_onto_another_card(self):
        ok, reason = self.ghost._check(
            self.payload(say="Rosa remembers that he never made a sound while he swept the floor."), [2, 3]
        )
        self.assertFalse(ok)

    def test_refuses_questions_the_cards_do_not_touch(self):
        chosen, refuse = self.ghost._pick("What did he think about the war in Korea?", "question")
        self.assertTrue(refuse)
        self.assertEqual(chosen, [])

    def test_answers_questions_the_cards_do_touch(self):
        chosen, refuse = self.ghost._pick("What happened when the boy fell asleep?", "question")
        self.assertFalse(refuse)
        self.assertIn(2, chosen)

    def test_repaired_answer_is_used(self):
        good = json.dumps(
            {
                "card": 2,
                "echo": "fell asleep in the chair",
                "say": "A boy fell asleep in the chair, and Abram let him sleep an hour past closing.",
            }
        )
        bad = json.dumps({"card": 2, "echo": "sold shoes in Topeka", "say": "He sold shoes."})
        ghost = Ghost(self.altar, backend=FakeBackend([bad, good]))
        turn = ghost.ask("What happened when the boy fell asleep?")
        self.assertEqual(turn.source, "repaired")
        self.assertEqual(turn.echo, "fell asleep in the chair")

    def test_the_card_is_read_when_the_model_will_not_behave(self):
        bad = json.dumps({"card": 2, "echo": "a morning in April", "say": "He woke the boy at dawn."})
        ghost = Ghost(self.altar, backend=FakeBackend([bad, bad]))
        turn = ghost.ask("What happened when the boy fell asleep?")
        self.assertEqual(turn.source, "verbatim")
        self.assertIn(self.altar.cards[1]["text"], turn.say)
        self.assertGreater(ghost.stats.as_dict()["verbatim"], 0)

    def test_the_opening_is_the_card_itself(self):
        ghost = Ghost(self.altar, backend=FakeBackend(['{"card": 1, "echo": "a", "say": "b"}']))
        turn = ghost.open_night()
        self.assertEqual(turn.source, "verbatim")
        self.assertIn(turn.say, {f'The candles are lit. The first thing left on the altar is this: "{card["text"]}"' for card in self.altar.cards})
        self.assertEqual(ghost.backend.calls, [])

    def test_no_model_still_speaks_from_the_cards(self):
        turn = self.ghost.ask("What happened when the boy fell asleep?")
        self.assertEqual(turn.source, "verbatim")
        self.assertIn("boy fell asleep", turn.say)

    def test_transcript_is_written_down(self):
        self.ghost.ask("Did he ever learn to drive?")
        saved = json.loads(self.altar.path.read_text("utf-8"))
        roles = [turn["role"] for turn in saved["nights"][-1]["turns"]]
        self.assertEqual(roles, ["family", "altar"])


class PrivacyTests(unittest.TestCase):
    """The promise on the box, checked by a machine instead of by me."""

    def test_the_app_never_learns_to_phone_home(self):
        import re

        offenders = []
        for path in sorted((ROOT / "core").glob("*.py")) + [ROOT / "ofrenda.py"]:
            text = path.read_text("utf-8")
            # doctor prints a link for a human to read; it never fetches it
            text = text.replace("https://github.com/ggml-org/llama.cpp", "")
            for url in re.findall(r"https?://[^\s\"'\\)]+", text):
                if not re.match(r"https?://(127\.0\.0\.1|localhost)", url):
                    offenders.append(f"{path.name}: {url}")
        self.assertEqual(offenders, [], "outbound URL found in the running app")

    def test_the_model_server_binds_to_loopback(self):
        text = (ROOT / "core" / "llm.py").read_text("utf-8")
        self.assertIn('"--host", "127.0.0.1"', text)

    def test_the_record_is_a_file_you_can_delete(self):
        altar = make_altar()
        altar.path.unlink()
        self.assertFalse(altar.path.exists())
        self.assertEqual(Altar.load(altar.path).cards, [])


class KeepsakeTests(unittest.TestCase):
    def test_html_escapes_the_family_text(self):
        altar = make_altar(
            [{"id": "z", "text": "<script>alert(1)</script> he kept a shop.", "by": "Rosa & co"}]
        )
        page = keepsake.as_html(altar, "A line for the card.")
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("Rosa &amp; co", page)

    def test_text_keeps_every_card(self):
        altar = make_altar()
        text = keepsake.as_text(altar, "A line for the card.")
        for card in altar.cards:
            self.assertIn(card["text"], text)


if __name__ == "__main__":
    unittest.main()
