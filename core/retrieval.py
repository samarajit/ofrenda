"""Find the cards a question is reaching for, without a second model.

Cards are short, so a small TF-IDF over the family's own words beats anything
cleverer, and it runs in microseconds. Two jobs live here: ranking cards for a
question, and noticing when two people remember the same moment differently.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

STOPWORDS = {
    # English
    "a", "about", "after", "all", "an", "and", "any", "are", "as", "at", "be",
    "because", "been", "before", "but", "by", "can", "could", "did", "do",
    "does", "for", "from", "had", "has", "have", "he", "her", "here", "him",
    "his", "how", "i", "if", "in", "into", "is", "it", "its", "just", "me",
    "my", "of", "on", "once", "or", "our", "out", "she", "so", "some", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "to", "too", "up", "was", "we", "were", "what", "when", "where", "which",
    "while", "who", "why", "will", "with", "would", "you", "your", "one",
    "remembers", "remember", "remembered", "says", "said", "told", "always",
    "never", "very", "much", "still", "only", "also", "himself", "herself",
    # Spanish
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al",
    "y", "o", "que", "en", "con", "por", "para", "su", "sus", "se", "lo", "le",
    "les", "como", "mas", "pero", "si", "no", "ni", "ya", "muy", "cuando",
    "donde", "porque", "este", "esta", "estos", "estas", "era", "fue", "son",
    "recuerda", "recuerdan", "dice", "dijo", "siempre", "nunca", "tambien",
    # particles that carry no memory of their own
    "until", "till", "away", "down", "back", "again", "together", "without",
    "around", "past", "over", "under", "along", "across", "behind", "beside",
    "already", "almost", "nearly", "sometimes", "often", "rather", "quite",
    "even", "else", "though", "although", "because", "since", "unless",
    "above", "across", "against", "among", "behind", "below", "beneath",
    "beside", "between", "beyond", "inside", "outside", "through", "toward",
    "towards", "upon", "within", "without", "during", "onto",
}

IRREGULAR = {
    "threw": "throw", "kept": "keep", "sang": "sing", "sung": "sing", "said": "say",
    "told": "tell", "gave": "give", "went": "go", "gone": "go", "made": "make",
    "slept": "sleep", "swept": "sweep", "taught": "teach", "bought": "buy",
    "brought": "bring", "thought": "think", "wore": "wear", "worn": "wear",
    "wrote": "write", "written": "write", "sold": "sell", "held": "hold",
    "left": "leave", "felt": "feel", "heard": "hear", "ran": "run", "came": "come",
    "saw": "see", "seen": "see", "ate": "eat", "drunk": "drink", "drove": "drive",
    "rode": "ride", "rose": "rise", "stood": "stand", "spoke": "speak",
    "broke": "break", "broken": "break", "sat": "sit", "stood": "stand",
    "wrapped": "wrap", "learned": "learn", "learnt": "learn",
}

EXTREMES = {
    "never", "always", "every", "nothing", "nobody", "none", "only",
    "nunca", "siempre", "nada", "nadie", "jamas", "solo", "tampoco",
}

WORD = re.compile(r"[a-z0-9']+")


def fold(text: str) -> str:
    """Strip accents so recuerdos survives being typed without them."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> list[str]:
    words = WORD.findall(fold(text))
    return [stem(IRREGULAR.get(w, w)) for w in words if w not in STOPWORDS and len(w) > 1]


def content_words(text: str) -> list[str]:
    return [w for w in WORD.findall(fold(text)) if len(w) > 2]


class Index:
    """TF-IDF over the altar's cards."""

    def __init__(self, cards: list[dict]):
        self.cards = cards
        self.doc_tokens = [tokens(card.get("text", "")) for card in cards]
        self.df: Counter[str] = Counter()
        for doc in self.doc_tokens:
            self.df.update(set(doc))
        self.n = max(1, len(cards))

    def _idf(self, term: str) -> float:
        return math.log((1 + self.n) / (1 + self.df.get(term, 0))) + 1.0

    def rank(self, question: str, limit: int = 4) -> list[tuple[dict, float]]:
        query = tokens(question)
        scores: list[tuple[dict, float]] = []
        for card, doc in zip(self.cards, self.doc_tokens):
            if not doc:
                continue
            counts = Counter(doc)
            score = 0.0
            for term in set(query):
                if term in counts:
                    score += self._idf(term) * (1.0 + 0.25 * counts[term])
            length_penalty = 1.0 + 0.04 * max(0, len(doc) - 12)
            scores.append((card, score / length_penalty))
        scored = [pair for pair in scores if pair[1] > 0]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    def distinctive_terms(self, limit: int = 6, skip: set[str] | None = None) -> list[str]:
        """Surface words that live on one card only. Good bait for a question chip."""
        skip = skip or set()
        for name in self._name_words():
            skip.add(fold(name))
        seen: dict[str, int] = {}
        for card in self.cards:
            words = {
                fold(w)
                for w in WORD.findall(card.get("text", ""))
                if len(w) > 3 and fold(w) not in STOPWORDS
            }
            for word in words:
                seen[word] = seen.get(word, 0) + 1
        candidates = [
            word
            for word, count in seen.items()
            if count == 1 and word not in skip and not word.endswith(("ed", "ing"))
        ]
        candidates.sort(key=lambda word: (-len(word), word))
        return candidates[:limit]

    def distinctive_phrases(self, limit: int = 3, skip: set[str] | None = None) -> list[str]:
        """Two or three word handles a person could actually ask about.

        Finds words that live on exactly one card, then keeps the neighbour that
        makes them a phrase ("tamarind candies", "the barber pole") so the chips
        read like a question and not like a search box.
        """
        skip = skip or set()
        skip |= {fold(name) for name in self._name_words()}
        seen: dict[str, int] = {}
        for card in self.cards:
            words = {
                fold(w)
                for w in WORD.findall(card.get("text", ""))
                if len(w) > 3 and fold(w) not in STOPWORDS
            }
            for word in words:
                seen[word] = seen.get(word, 0) + 1

        picked: list[tuple[int, str]] = []
        taken: set[str] = set()
        for card in self.cards:
            best: tuple[int, str] | None = None
            raw = WORD.findall(fold(card.get("text", "")))
            for i, word in enumerate(raw):
                if seen.get(word, 0) != 1 or word in taken or word in skip or len(word) < 5:
                    continue
                if word.endswith(("ed", "ing")):
                    continue
                before = raw[i - 1] if i else ""
                after = raw[i + 1] if i + 1 < len(raw) else ""
                phrase = word
                if (
                    _is_content(after)
                    and after not in NOT_HEADS
                    and after not in DETERMINERS
                    and not after.isdigit()
                    and not after.endswith(("ed", "ing"))
                ):
                    phrase = f"{word} {after}"
                    taken |= {word, after}
                elif before in DETERMINERS and before not in NOT_HEADS and before not in {"a", "an"}:
                    phrase = f"{before} {word}"
                    taken.add(word)
                else:
                    taken.add(word)
                best = (2 if " " in phrase else 1, phrase)
                break
            if best:
                picked.append(best)
        picked.sort(key=lambda pair: (-pair[0], -len(pair[1])))
        chosen = [phrase for _, phrase in picked[:limit]]
        if len(chosen) < limit:
            blocked = skip | {fold(w) for w in tokens(" ".join(chosen))}
            for word in self.distinctive_terms(limit=limit, skip=blocked):
                if word not in chosen and len(word) > 3:
                    chosen.append(word)
                if len(chosen) >= limit:
                    break
        return chosen[:limit]

    def _name_words(self) -> list[str]:
        names: list[str] = []
        for card in self.cards:
            names += WORD.findall(fold(card.get("by", "")))
        return names


DETERMINERS = {"the", "a", "an", "his", "her", "their", "this", "that", "every", "each"}
NOT_HEADS = {
    "mostly", "almost", "about", "really", "very", "always", "never", "only",
    "over", "under", "just", "quite", "rather", "half", "more", "less", "most",
    "every", "each", "another", "again", "still", "even", "both", "back",
}


def _is_content(word: str) -> bool:
    return bool(word) and len(word) > 2 and word not in STOPWORDS


def trigrams(text: str) -> set[str]:
    clean = " ".join(content_words(text))
    padded = f" {clean} "
    return {padded[i : i + 3] for i in range(max(1, len(padded) - 2))}


def overlap(a: str, b: str) -> float:
    ga, gb = trigrams(a), trigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def find_disagreements(cards: list[dict], floor: float = 0.34) -> list[dict]:
    """Pairs of cards that cover the same ground and pull in opposite directions.

    The rule is crude on purpose: two people talking about the same thing
    (character trigram overlap) where one of them uses a word like never or
    always and the other does not. It is a hint for the reader, not a verdict.
    """
    found: list[dict] = []
    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            a, b = cards[i], cards[j]
            similarity = overlap(a.get("text", ""), b.get("text", ""))
            if similarity < floor:
                continue
            words_a = set(content_words(a.get("text", "")))
            words_b = set(content_words(b.get("text", "")))
            conflict = (words_a & EXTREMES) ^ (words_b & EXTREMES)
            if not conflict:
                continue
            found.append(
                {
                    "a": a["id"],
                    "b": b["id"],
                    "similarity": round(similarity, 2),
                    "word": sorted(conflict)[0],
                }
            )
    return found
