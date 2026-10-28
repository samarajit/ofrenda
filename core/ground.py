"""The contract between a small model and somebody's dead.

A 1.7B model will wander. It will answer a question about a haircut with a
memory about a barber pole, and it will invent a morning nobody lived. So the
division of labour here is strict: the harness reads the question, chooses the
card, and enforces the facts. The model is only allowed to choose which words
of that card to hold up and how to say one sentence around them.

Every borrow is checked against the card it claims to come from. A failed check
is sent back once with the phrase fixed, and then replaced with the card read
word for word. A wrong memory never reaches the screen.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field

from .retrieval import Index, find_disagreements, tokens

VOICE = """You are the voice of an ofrenda, a family altar on the night of the dead.

You are not the person being remembered. You never pretend to be them, and you never claim to know anything that is not written on the cards. You are the shape a family made by remembering someone.

Every answer has two parts. First you hold up a few of the family's own words, copied exactly. Then you say, plainly, what those words hold.

Rules:
1. "echo" is three to six words copied exactly from the card you are given, without quotation marks.
2. "say" is one or two plain sentences, at most thirty words. Restate what the card says in the third person, in the card's own words where you can.
3. Add nothing that is not on the cards. No new event, time, weather, feeling, motive, cause, outcome, or name.
4. If two cards are named, they disagree. Name both people and leave it open. Do not settle it.
5. The cards in the example below are not on this altar. Never use their words, names, or details.
6. Do not open "say" with a name and remembers, says, or wrote. Begin with the memory itself.
7. No greetings, no exclamation marks, no lists, no stage directions, no mention of models or assistants.

Plain, quiet, specific. You are not selling anything."""

EXAMPLE_CARDS = """SHAPE EXAMPLE. These cards are not part of the altar.

CARDS
1. [by Delia] He kept four hives behind the church in San Marcos and sold honey in old coffee tins.
2. [by Nesto] He said the bees knew his name."""

EXAMPLE_ASK = """QUESTION
Why did he sell honey in coffee tins?

USE CARD 1."""

EXAMPLE_ANSWER = {
    "card": 1,
    "echo": "sold honey in old coffee tins",
    "say": "Delia put the old coffee tins beside four hives behind a church in San Marcos.",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "card": {"type": "integer", "description": "the number of the card you were given"},
        "echo": {"type": "string", "description": "three to six words copied exactly from that card"},
        "say": {"type": "string", "description": "one or two plain sentences"},
    },
    "required": ["card", "echo", "say"],
    "additionalProperties": False,
}

FORBIDDEN = re.compile(
    r"\b(as an ai|language model|i am an ai|chatbot|openai|assistant)\b", re.IGNORECASE
)
QUOTED = re.compile(r"[\"“”]([^\"“”]{6,90})[\"“”]")

TAIL_WORDS = {
    "for", "by", "with", "in", "on", "at", "to", "of", "from", "and", "but",
    "that", "which", "who", "the", "a", "an", "his", "her", "their", "as",
    "into", "over", "under", "after", "before", "about", "when", "while",
    "he", "she", "they", "it", "we", "you", "him", "them", "us", "i",
    "was", "is", "are", "were", "be", "been", "made", "make", "every",
    "each", "has", "have", "had", "do", "does", "did",
}
HEAD_WORDS = {"and", "but", "because", "while", "when", "that", "which", "who", "so", "then"}

REFUSAL = "That is not on the cards. Nothing the family wrote down touches it."

# words that carry a question but never anchor a memory on their own
MENTAL = {
    "think", "know", "feel", "want", "like", "mean", "believe", "wonder", "guess",
    "hope", "wish", "love", "hate", "care", "remember", "say", "tell", "consider",
    "suppose", "really", "about", "ever", "anything", "something", "much", "many",
}


@dataclass
class Turn:
    kind: str
    say: str
    echo: str = ""
    card_id: str = ""
    number: int = 0
    by: str = ""
    year: str = ""
    source: str = "model"
    attempts: int = 1
    seconds: float = 0.0
    tokens: int = 0
    note: str = ""

    def payload(self) -> dict:
        return asdict(self)


@dataclass
class Stats:
    asks: int = 0
    first_try: int = 0
    repaired: int = 0
    verbatim: int = 0
    refused: int = 0
    tokens: int = 0
    seconds: float = 0.0
    used: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "asks": self.asks,
            "first_try": self.first_try,
            "repaired": self.repaired,
            "verbatim": self.verbatim,
            "refused": self.refused,
            "tokens": self.tokens,
            "seconds": round(self.seconds, 2),
            "tokens_per_second": round(self.tokens / self.seconds, 1) if self.seconds else 0.0,
            "used": self.used[-12:],
        }


# -- words ------------------------------------------------------------------


OPENERS = re.compile(
    r"^(?:[A-ZÁÉÍÓÚÑ][\wáéíóúñ'’-]+(?:\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ'’-]+)?\s+)?"
    r"(?:remembers|remembered|recalls|recalled|says|said|writes|wrote|told us"
    r"|recuerda|recuerdan|dice|dijo)\s+that\s+|"
    r"(?:[A-ZÁÉÍÓÚÑ][\wáéíóúñ'’-]+\s+)?(?:recuerda|recuerdan|dice|dijo)\s+(?:que\s+)?",
    re.IGNORECASE,
)


def polish_say(say: str) -> str:
    """Small deterministic repairs, so a good answer is not sent back for style.

    A 1.7B model likes to open with "Rosa remembers that...". The interface
    already names the person who left the card, so the harness takes the phrase
    off instead of spending another fifteen seconds on a retry.
    """
    text = " ".join(say.split())
    for _ in range(2):
        stripped = OPENERS.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    text = re.sub(r"^(?:and|but|that)\s+", "", text, flags=re.IGNORECASE)
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text.strip()


def normalize(text: str) -> str:
    """Compare phrases without letting punctuation or case fake a match."""
    return " ".join(re.findall(r"[a-z0-9áéíóúñü']+", text.lower()))


def _tidy(words: list[str]) -> list[str]:
    while words and words[0].lower() in HEAD_WORDS:
        words = words[1:]
    while words and words[-1].lower().strip(".,;:") in TAIL_WORDS:
        words = words[:-1]
    return words


def extract_phrase(text: str, low: int = 3, high: int = 6) -> str:
    """Take a clause that can be said out loud, keeping the order of the words."""
    clean = text.strip().rstrip(".")
    chunks = re.split(r"[.;:,]\s+|\s+and\s+", clean)
    best: list[str] = []
    best_key = (-1, -1)
    for chunk in chunks:
        words = _tidy(chunk.split())
        if not low <= len(words) <= high:
            continue
        key = (len(set(tokens(" ".join(words)))), len(" ".join(words)))
        if key > best_key:
            best, best_key = words, key
    if best:
        return " ".join(best).strip(" ,.;:")

    words = _tidy(clean.split())
    if len(words) > high:
        windows = []
        for i in range(len(words) - high + 1):
            window = words[i : i + high]
            windows.append((len(set(tokens(" ".join(window)))), window))
        windows.sort(key=lambda pair: pair[0], reverse=True)
        words = _tidy(windows[0][1]) if windows else words[:high]
    if len(words) < low:
        words = _tidy(clean.split()[:high])
    return " ".join(words).strip(" ,.;:")


def fit_echo(echo: str, card_text: str, low: int = 3, high: int = 6) -> str | None:
    """Keep the model's choice of card and correct the size of its phrase.

    A small model usually copies a whole card instead of a phrase. When those
    words really are on the card, the harness keeps the card and takes a short
    phrase from it, instead of making the person wait through another try.
    """
    cleaned = echo.strip().strip('"“”')
    if not cleaned:
        return None
    haystack = normalize(card_text)
    needle = normalize(cleaned)
    if not needle or needle not in haystack:
        return None
    if low <= len(cleaned.split()) <= high:
        return cleaned
    phrase = extract_phrase(card_text, low=low, high=high)
    if not phrase or normalize(phrase) not in haystack:
        return None
    return phrase if low <= len(phrase.split()) <= high else None


# -- the ghost --------------------------------------------------------------


class Ghost:
    def __init__(self, altar, backend=None, history_limit: int = 2):
        self.altar = altar
        self.backend = backend
        self.history: list[dict] = []
        self.history_limit = history_limit
        self.stats = Stats()
        self.seen_terms: set[str] = set()

    # -- choosing ------------------------------------------------------------

    def _pick(self, question: str, kind: str) -> tuple[list[int], bool]:
        """Return the card numbers the answer may use, and whether to refuse."""
        cards = self.altar.cards
        if not cards:
            return [], True
        if kind == "opening":
            bait = self._opening_bait()
            for i, card in enumerate(cards, 1):
                if bait and normalize(bait) in normalize(card["text"]):
                    return [i], False
            return [1], False
        if kind == "closing":
            for i, card in enumerate(cards, 1):
                if card["id"] not in self.stats.used:
                    return [i], False
            return [len(cards)], False

        ranked = Index(cards).rank(question, limit=3)
        if not ranked or ranked[0][1] < 2.5:
            return [], True
        anchors = {word for word in tokens(question) if word not in MENTAL}
        top_card = ranked[0][0]
        matched = anchors & set(tokens(top_card["text"] + " " + top_card.get("by", "")))
        if not matched:
            return [], True
        chosen = [self._number_of(ranked[0][0]["id"])]
        top = ranked[0][0]["id"]
        for item in find_disagreements(cards, floor=0.28):
            if top in (item["a"], item["b"]):
                other = item["b"] if item["a"] == top else item["a"]
                chosen.append(self._number_of(other))
        return sorted(set(chosen)), False

    def _opening_bait(self) -> str:
        index = Index(self.altar.cards)
        phrases = index.distinctive_phrases(limit=1)
        return phrases[0] if phrases else (self.altar.cards[0]["text"] if self.altar.cards else "")

    def _number_of(self, card_id: str) -> int:
        for i, card in enumerate(self.altar.cards):
            if card["id"] == card_id:
                return i + 1
        return 1

    # -- prompt --------------------------------------------------------------

    def _person_line(self) -> str:
        person = self.altar.person
        bits = [person.get("name") or "someone"]
        if person.get("relation"):
            bits.append(person["relation"])
        if person.get("born") or person.get("died"):
            bits.append(f'{person.get("born", "?")} to {person.get("died", "?")}')
        return "THE PERSON\n" + ", ".join(bits)

    def _cards_block(self) -> str:
        lines = []
        for i, card in enumerate(self.altar.cards, 1):
            who = card.get("by") or "unnamed"
            stamp = f"{who}, {card['year']}" if card.get("year") else who
            lines.append(f"{i}. [by {stamp}] {card['text']}")
        return "CARDS\n" + "\n".join(lines)

    def _messages(self, question: str, kind: str, chosen: list[int]) -> list[dict]:
        """The record sits in the system turn so llama.cpp can keep it cached.

        Every question then costs a short tail instead of a full re-read of the
        altar, which is the difference between three seconds and twenty.
        """
        system = f"{VOICE}\n\n{self._person_line()}\n\n{self._cards_block()}"
        asks = {
            "opening": "The candles are lit. Hold up the first thing this family left here.",
            "closing": "The night is ending. Hold up the last thing they should carry out.",
            "question": question,
        }
        lines = [f"QUESTION\n{asks[kind]}", ""]
        for number in chosen:
            card = self.altar.cards[number - 1]
            who = card.get("by") or "unnamed"
            stamp = f"{who}, {card['year']}" if card.get("year") else who
            lines.append(f"THE CARD YOU ARE USING\n{number}. [by {stamp}] {card['text']}")
        lines.append("")
        if len(chosen) == 1:
            lines.append(f"USE CARD {chosen[0]}.")
        else:
            lines.append(
                f"CARDS {chosen[0]} AND {chosen[1]} DISAGREE. Use one of them for the echo "
                "and name both people in the sentence."
            )
        history = ""
        if self.history:
            recent = self.history[-self.history_limit :]
            history = "ALREADY ASKED TONIGHT, do not answer them again\n" + "\n".join(
                f"- {turn['q']}" for turn in recent
            ) + "\n\n"
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": EXAMPLE_CARDS + "\n\n" + EXAMPLE_ASK},
            {"role": "assistant", "content": json.dumps(EXAMPLE_ANSWER, ensure_ascii=False)},
            {"role": "user", "content": history + "\n".join(lines)},
        ]

    # -- checking ------------------------------------------------------------

    def _check(self, data: dict, allowed: list[int]) -> tuple[bool, str]:
        """Return (ok, reason). The reason goes back to the model on a retry."""
        cards = self.altar.cards
        try:
            number = int(data.get("card", -1))
        except (TypeError, ValueError):
            return False, "card must be a number"
        if number not in allowed:
            return False, f"card must be {allowed[0]}"
        echo = (data.get("echo") or "").strip()
        say = (data.get("say") or "").strip()
        if not say:
            return False, "say was empty"
        if FORBIDDEN.search(say):
            return False, "say mentioned being a model or an assistant"

        person = (self.altar.person.get("name") or "").split(" ")[0]
        if person and re.search(rf"\bI am {re.escape(person)}\b", say, re.IGNORECASE):
            return False, "say claimed to be the dead person"

        cited_card = cards[number - 1]
        authors = {
            (c.get("by") or "").strip().lower() for c in cards if c.get("by")
        }
        cited_author = (cited_card.get("by") or "").strip().lower()
        for author in authors - {cited_author}:
            if author and re.search(rf"\b{re.escape(author)}\b", say, re.IGNORECASE):
                return False, (
                    f"card {number} was left by {cited_author or 'someone else'}, "
                    f"not by {author}. Name the person who wrote the card, or no name at all"
                )

        if re.match(r"^[A-ZÁÉÍÓÚÑ][\wáéíóúñ'’-]+\s+(remembers|remembered|says|said|wrote|writes)\b", say):
            return False, "say began with who remembered it. Begin with the memory itself"

        sentences = [part for part in re.split(r"(?<=[.!?])\s+", say) if part.strip()]
        if len(sentences) > 2:
            return False, "say was more than two sentences"
        words = say.split()
        if len(words) < 6:
            return False, "say was shorter than six words"
        if len(words) > 32:
            return False, "say was longer than thirty-two words"

        card = cards[number - 1]
        if not echo:
            return False, "echo was empty"
        fitted = fit_echo(echo, card["text"])
        if fitted is None:
            return False, f"echo was not copied exactly from card {number}"
        data["echo"] = fitted

        others = []
        for i, other in enumerate(cards, 1):
            if i == number:
                continue
            score = self._overlap(say, other["text"] + " " + other.get("by", ""))
            others.append((score, i))
        others.sort(reverse=True)
        if others and others[0][0] > 0.6 and others[0][0] > self._overlap(
            say, card["text"] + " " + card.get("by", "")
        ):
            return False, f"that sentence belongs to card {others[0][1]}, not card {number}"

        if self._overlap(say, card["text"] + " " + card.get("by", "")) < 0.5:
            return False, (
                "say used too many words that are not on that card. "
                "Restate the card in its own words"
            )

        record = normalize(
            " ".join(c["text"] + " " + c.get("by", "") for c in cards)
            + " " + self.altar.person.get("name", "")
        )
        record_words = set(record.split())
        stray = [word for word in tokens(say) if word not in set(tokens(record))]
        if len(stray) > 1:
            return False, (
                "say used words that are not on the cards: " + ", ".join(sorted(set(stray))[:4])
            )

        for quoted in QUOTED.findall(say):
            if not any(normalize(quoted) in normalize(c["text"]) for c in cards):
                return False, "say contained a quotation that is not on any card"
        for number_word in re.findall(r"\b\d+\b", say):
            if number_word not in record_words and number_word not in {
                "1", "2",
            }:
                return False, f"say used the number {number_word}, which is not on the cards"
        for name in re.findall(r"(?<![.!?]\s)\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}", say):
            if normalize(name) not in record_words:
                return False, f"say used the name {name}, which is not on the cards"
        return True, ""

    @staticmethod
    def _overlap(say: str, card_text: str) -> float:
        said = set(tokens(say))
        if not said:
            return 0.0
        return len(said & set(tokens(card_text))) / len(said)

    # -- turns ---------------------------------------------------------------

    def _verbatim(self, kind: str, number: int, note: str = "") -> Turn:
        card = self.altar.cards[number - 1]
        by = card.get("by") or ""
        leads = {
            "opening": "The candles are lit. The first thing left on the altar is this:",
            "closing": "What stays lit is this:",
            "question": f"{by} left this on the card:" if by else "The card says:",
        }
        return Turn(
            kind=kind,
            say=f'{leads.get(kind, leads["question"])} "{card["text"]}"',
            echo=card["text"],
            card_id=card["id"],
            number=number,
            by=card.get("by", ""),
            year=card.get("year", ""),
            source="verbatim",
            note=note,
        )

    def _run(self, question: str, kind: str) -> Turn:
        chosen, refuse = self._pick(question, kind)
        if refuse or not chosen:
            self.stats.refused += 1
            return Turn(kind, REFUSAL, source="refusal")

        if self.backend is None:
            self.stats.verbatim += 1
            return self._verbatim(kind, chosen[0])

        messages = self._messages(question, kind, chosen)
        fixed = extract_phrase(self.altar.cards[chosen[0] - 1]["text"], low=3, high=6)
        started = time.time()
        total_tokens = 0
        attempts = 0
        reason = ""
        for attempts in range(1, 3):
            try:
                result = self.backend.chat(
                    messages,
                    max_tokens=80,
                    temperature=0.35 if attempts == 1 else 0.1,
                    json_schema=SCHEMA,
                )
            except Exception as exc:
                return Turn(kind, f"The voice is not here: {exc}", source="error")
            total_tokens += result.tokens
            raw = re.sub(r"^```(json)?|```$", "", result.text.strip(), flags=re.MULTILINE).strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                reason = "the reply was not valid JSON"
                messages = messages + [
                    {"role": "assistant", "content": raw[:200]},
                    {"role": "user", "content": "That was not JSON. Reply with the three keys only."},
                ]
                continue
            data["say"] = polish_say(data.get("say") or "")
            ok, reason = self._check(data, chosen)
            if ok:
                number = int(data["card"])
                card = self.altar.cards[number - 1]
                turn = Turn(
                    kind=kind,
                    say=data["say"].strip(),
                    echo=data["echo"].strip(),
                    card_id=card["id"],
                    number=number,
                    by=card.get("by", ""),
                    year=card.get("year", ""),
                    source="model" if attempts == 1 else "repaired",
                    attempts=attempts,
                    seconds=round(time.time() - started, 2),
                    tokens=total_tokens,
                )
                self.stats.asks += 1
                self.stats.tokens += total_tokens
                self.stats.seconds += turn.seconds
                self.stats.used.append(turn.card_id)
                if attempts == 1:
                    self.stats.first_try += 1
                else:
                    self.stats.repaired += 1
                return turn

            if os.environ.get("OFRENDA_DEBUG"):
                print(f"[ofrenda] attempt {attempts} refused: {reason}\n          {raw[:320]}")
            messages = messages + [
                {"role": "assistant", "content": raw[:200]},
                {
                    "role": "user",
                    "content": (
                        f"That broke a rule: {reason}. The phrase is chosen for you now. "
                        f'Use card {chosen[0]} and set "echo" to exactly: {fixed} '
                        'Then write one sentence in "say" that restates that card and adds nothing.'
                    ),
                },
            ]

        turn = self._verbatim(kind, chosen[0], note=reason)
        turn.attempts = attempts
        turn.seconds = round(time.time() - started, 2)
        turn.tokens = total_tokens
        self.stats.asks += 1
        self.stats.verbatim += 1
        self.stats.tokens += total_tokens
        self.stats.seconds += turn.seconds
        return turn

    def open_night(self) -> Turn:
        """The first thing in the room is the family's own sentence, not the model's.

        It is faster, it is honest, and it sets the rule for the night: the words
        on the altar come first.
        """
        chosen, _ = self._pick("", "opening")
        if not chosen:
            return Turn("empty", "There is nothing on the cards yet.")
        turn = self._verbatim("opening", chosen[0])
        self.altar.record_turn("altar", turn.say, echo=turn.echo, by=turn.by, source=turn.source)
        return turn

    def ask(self, question: str) -> Turn:
        question = " ".join((question or "").split())[:300]
        self.seen_terms.update(tokens(question))
        turn = self._run(question, "question")
        self.altar.record_turn("family", question)
        self.altar.record_turn("altar", turn.say, echo=turn.echo, by=turn.by, source=turn.source)
        if turn.source in ("model", "repaired"):
            self.history.append({"q": question, "a": {"say": turn.say}})
        return turn

    def close_night(self) -> Turn:
        turn = self._run("", "closing")
        self.altar.record_turn("altar", turn.say, echo=turn.echo, by=turn.by, source=turn.source)
        self.altar.close_night(turn.say)
        return turn

    # -- things the interface asks for ---------------------------------------

    def suggestions(self, limit: int = 3) -> list[str]:
        return Index(self.altar.cards).distinctive_phrases(limit=limit, skip=self.seen_terms)

    def disagreements(self) -> list[dict]:
        return find_disagreements(self.altar.cards)

    def stats_payload(self) -> dict:
        return self.stats.as_dict()
