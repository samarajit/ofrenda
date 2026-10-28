"""The altar itself: one person, a stack of remembered cards, and where it lives.

The whole record is a single JSON file on the machine that runs Ofrenda. There
is no account, no sync, and no copy anywhere else.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

MAX_CARDS = 24
MAX_CARD_CHARS = 420


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return slug or "altar"


class Altar:
    """A person, their cards, and the transcript of the nights so far."""

    def __init__(self, path: Path, data: dict | None = None):
        self.path = Path(path)
        self.data = data or {
            "person": {
                "name": "",
                "called": "",
                "born": "",
                "died": "",
                "relation": "",
            },
            "cards": [],
            "nights": [],
            "created": _now(),
        }

    # -- loading and saving -------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "Altar":
        path = Path(path)
        if path.exists():
            try:
                return cls(path, json.loads(path.read_text("utf-8")))
            except json.JSONDecodeError:
                broken = path.with_suffix(".broken.json")
                path.replace(broken)
        return cls(path)

    @classmethod
    def from_sample(cls, sample_path: Path, store_path: Path) -> "Altar":
        data = json.loads(Path(sample_path).read_text("utf-8"))
        data.setdefault("person", {})
        data.setdefault("cards", [])
        for card in data["cards"]:
            card.setdefault("id", hashlib.sha1(card["text"].encode()).hexdigest()[:8])
            card.setdefault("added", _now())
        data["nights"] = []
        data["created"] = _now()
        altar = cls(store_path, data)
        altar.save()
        return altar

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), "utf-8")

    # -- person -------------------------------------------------------------

    @property
    def person(self) -> dict:
        return self.data["person"]

    @property
    def cards(self) -> list[dict]:
        return self.data["cards"]

    def set_person(self, fields: dict) -> None:
        for key in ("name", "called", "born", "died", "relation"):
            if key in fields and isinstance(fields[key], str):
                self.person[key] = fields[key].strip()[:120]
        self.save()

    # -- cards --------------------------------------------------------------

    def add_card(self, text: str, by: str = "", year: str = "") -> dict:
        text = " ".join((text or "").split()).strip()
        if len(text) < 3:
            raise ValueError("a memory needs a little more than that")
        if len(text) > MAX_CARD_CHARS:
            raise ValueError(f"keep a card under {MAX_CARD_CHARS} characters")
        if len(self.cards) >= MAX_CARDS:
            raise ValueError(f"the altar holds {MAX_CARDS} cards; retire one first")
        digest = hashlib.sha1(f"{text}{by}{time_nonce()}".encode()).hexdigest()[:8]
        card = {
            "id": digest,
            "text": text,
            "by": (by or "").strip()[:60],
            "year": (year or "").strip()[:20],
            "added": _now(),
        }
        self.cards.append(card)
        self.save()
        return card

    def remove_card(self, card_id: str) -> bool:
        before = len(self.cards)
        self.data["cards"] = [c for c in self.cards if c["id"] != card_id]
        if len(self.data["cards"]) != before:
            self.save()
            return True
        return False

    def card_by_id(self, card_id: str) -> dict | None:
        for card in self.cards:
            if card["id"] == card_id:
                return card
        return None

    # -- nights -------------------------------------------------------------

    def open_night(self) -> dict:
        night = {"started": _now(), "turns": [], "ended": None}
        self.data["nights"].append(night)
        self.save()
        return night

    def close_night(self, keepsake: str = "") -> None:
        if self.data["nights"]:
            self.data["nights"][-1]["ended"] = _now()
            if keepsake:
                self.data["nights"][-1]["keepsake"] = keepsake
            self.save()

    def record_turn(self, role: str, text: str, **extra) -> None:
        if not self.data["nights"]:
            self.open_night()
        turn = {"role": role, "text": text, "at": _now()}
        turn.update({k: v for k, v in extra.items() if v not in (None, "")})
        self.data["nights"][-1]["turns"].append(turn)
        self.save()

    # -- shapes the browser needs -------------------------------------------

    def public(self) -> dict:
        return {
            "person": self.person,
            "cards": self.cards,
            "limits": {"max_cards": MAX_CARDS, "max_card_chars": MAX_CARD_CHARS},
        }


def time_nonce() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
