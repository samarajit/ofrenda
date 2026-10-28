"""A séance in the terminal, for people who prefer the dark.

    python3 ofrenda.py seance --sample abram

Same altar, same cards, same local model. No server, no browser.
"""
from __future__ import annotations

import sys
import time

from .ground import Ghost
from .retrieval import Index

DIM = "\033[2m"
ITALIC = "\033[3m"
WARM = "\033[38;5;179m"
CLEAR = "\033[0m"
BOLD = "\033[1m"


def _writeline(text: str, delay: float = 0.012) -> None:
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay)
    sys.stdout.write("\n")


def _card_note(turn) -> str:
    if not turn.echo or turn.source == "verbatim":
        return ""
    who = turn.by or "unnamed"
    year = f", {turn.year}" if turn.year else ""
    return f'  {DIM}{ITALIC}"{turn.echo}"  {who}{year}{CLEAR}'


def run(altar, backend, delay: float = 0.012, limit: int = 12) -> int:
    ghost = Ghost(altar, backend)
    person = altar.person
    name = person.get("name") or "someone"
    dates = " to ".join(filter(None, [person.get("born"), person.get("died")]))

    print()
    print(f"  {WARM}ofrenda{CLEAR} {DIM}the altar of {name}{f', {dates}' if dates else ''}{CLEAR}")
    print(f"  {DIM}{len(altar.cards)} cards. ask, or press ctrl-d to put the candles out.{CLEAR}")
    print()

    turn = ghost.open_night()
    _writeline(f"  {ITALIC}{turn.say}{CLEAR}", delay)
    if turn.echo:
        print(_card_note(turn))
    print()

    asked = 0
    while asked < limit:
        try:
            question = input(f"  {WARM}>{CLEAR} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question in ("/quit", "/exit", "/end"):
            break
        if question == "/cards":
            for i, card in enumerate(altar.cards, 1):
                who = card.get("by") or "unnamed"
                print(f"  {DIM}{i:02d}  {card['text']}  ({who}){CLEAR}")
            print()
            continue
        if question == "/ask":
            index = Index(altar.cards)
            print(f"  {DIM}try: {', '.join(index.distinctive_terms(3))}{CLEAR}\n")
            continue
        asked += 1
        token = sys.stdout.write(f"  {DIM}the candle leans...{CLEAR}")
        sys.stdout.flush()
        turn = ghost.ask(question)
        sys.stdout.write("\r" + " " * 40 + "\r")
        _writeline(f"  {ITALIC}{turn.say}{CLEAR}", delay)
        if turn.echo:
            print(_card_note(turn))
        print()

    turn = ghost.close_night()
    _writeline(f"  {ITALIC}{turn.say}{CLEAR}", delay)
    if turn.echo:
        print(_card_note(turn))
    print(f"\n  {DIM}the candles are out. {len(altar.cards)} cards stay on the altar.{CLEAR}\n")
    return 0
