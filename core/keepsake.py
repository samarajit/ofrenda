"""The recuerdo: a printable card made from the family's own sentences.

Nothing is invented here. The quotes are the cards, the dedication is the last
thing the altar said, and the file is written on the machine that made it.
"""
from __future__ import annotations

import html
import platform
from datetime import datetime


def _name(person: dict) -> str:
    name = person.get("name") or "Someone"
    called = person.get("called")
    return f'{name} ("{called}")' if called and called.lower() not in name.lower() else name


def _dates(person: dict) -> str:
    born, died = person.get("born", ""), person.get("died", "")
    if born and died:
        return f"{born} to {died}"
    return born or died or ""


DEDICATION = "These are the sentences the family kept."


def as_text(altar, dedication: str = DEDICATION, model_label: str = "a local model",
            closing: str = "") -> str:
    person = altar.person
    lines = [
        "RECUERDO",
        "",
        _name(person),
        _dates(person),
        "",
    ]
    if person.get("relation"):
        lines += [person["relation"], ""]
    if dedication:
        lines += [dedication, ""]
    lines.append("What the family left on the cards")
    lines.append("")
    for i, card in enumerate(altar.cards, 1):
        by = card.get("by") or "unnamed"
        year = f', {card["year"]}' if card.get("year") else ""
        lines.append(f"{i}. {card['text']}")
        lines.append(f"   {by}{year}")
        lines.append("")
    if closing:
        lines += ["", "The last thing said at the altar:", closing, ""]
    lines += [
        "Kept on " + platform.node() + " on " + datetime.now().strftime("%d %B %Y") + ".",
        f"Written at home with {model_label}. No copy of this page was sent anywhere.",
    ]
    return "\n".join(lines)


def as_html(altar, dedication: str = DEDICATION, model_label: str = "a local model",
            closing: str = "") -> str:
    person = altar.person
    esc = html.escape
    rows = []
    for i, card in enumerate(altar.cards, 1):
        by = card.get("by") or "unnamed"
        year = f', {card["year"]}' if card.get("year") else ""
        rows.append(
            f'<li><span class="num">{i:02d}</span>'
            f'<span class="quote">{esc(card["text"])}</span>'
            f'<span class="by">{esc(by)}{esc(year)}</span></li>'
        )
    today = datetime.now().strftime("%d %B %Y")
    closing_block = (
        f'<p class="closing">{esc(closing)}</p>' if closing else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recuerdo: {esc(_name(person))}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 16px; background: #17110e;
    font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
    color: #2a1d16;
  }}
  .card {{
    width: 148mm; min-height: 210mm; margin: 0 auto; padding: 16mm 14mm;
    background: #f4ead6;
    background-image:
      radial-gradient(120% 90% at 50% -10%, #fffaf0 0%, #f4ead6 55%, #ecdfc6 100%);
    box-shadow: 0 22px 60px rgba(0,0,0,.55);
    position: relative;
  }}
  .card::before {{
    content: ""; position: absolute; inset: 6mm; pointer-events: none;
    border: 1px solid rgba(150, 90, 40, .28);
  }}
  .kicker {{
    font-size: 9pt; letter-spacing: .34em; text-transform: uppercase;
    color: #9a5f22; text-align: center; margin: 0 0 6mm;
  }}
  h1 {{ font-size: 21pt; font-weight: 400; margin: 0; text-align: center; letter-spacing: .01em; }}
  .dates {{ text-align: center; font-size: 9.5pt; color: #7a6650; margin: 2mm 0 0; font-variant: small-caps; letter-spacing: .12em; }}
  .relation {{ text-align: center; font-size: 10pt; color: #5f4a38; margin: 3mm 0 0; }}
  .flourish {{ display: block; width: 40mm; margin: 6mm auto; }}
  .dedication {{
    font-size: 11.5pt; line-height: 1.55; font-style: italic; text-align: center;
    margin: 0 auto 9mm; max-width: 108mm; color: #3a281d;
  }}
  .label {{
    font-size: 8.5pt; letter-spacing: .26em; text-transform: uppercase;
    color: #9a5f22; text-align: center; margin: 0 0 4mm;
  }}
  ol {{ list-style: none; margin: 0; padding: 0; counter-reset: none; }}
  li {{ display: grid; grid-template-columns: 8mm 1fr; column-gap: 3mm; margin: 0 0 4.5mm; }}
  .num {{ font-size: 8.5pt; color: #b08a5a; padding-top: 1.2mm; letter-spacing: .1em; }}
  .quote {{ font-size: 10.5pt; line-height: 1.45; }}
  .by {{ grid-column: 2; font-size: 8.5pt; color: #8a7358; font-variant: small-caps; letter-spacing: .08em; margin-top: .8mm; }}
  footer {{
    margin-top: 10mm; padding-top: 4mm; border-top: 1px solid rgba(150, 90, 40, .22);
    font-size: 8pt; line-height: 1.6; color: #8a7358; text-align: center;
  }}
  .closing {{
    margin: 9mm 0 0; padding-top: 5mm; border-top: 1px solid rgba(150, 90, 40, .22);
    font-size: 10.5pt; line-height: 1.55; font-style: italic; color: #4a3526; text-align: center;
  }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .card {{ box-shadow: none; width: auto; min-height: auto; }}
  }}
</style>
</head>
<body>
  <article class="card">
    <p class="kicker">Recuerdo</p>
    <h1>{esc(_name(person))}</h1>
    <p class="dates">{esc(_dates(person))}</p>
    <p class="relation">{esc(person.get("relation", ""))}</p>
    <svg class="flourish" viewBox="0 0 120 12" aria-hidden="true">
      <path d="M2 6h44" stroke="#c98a2e" stroke-width="1"/>
      <path d="M74 6h44" stroke="#c98a2e" stroke-width="1"/>
      <circle cx="60" cy="6" r="3.2" fill="#e0a33a"/>
      <circle cx="52" cy="6" r="1.2" fill="#c98a2e"/>
      <circle cx="68" cy="6" r="1.2" fill="#c98a2e"/>
    </svg>
    <p class="dedication">{esc(dedication)}</p>
    <p class="label">What the family left on the cards</p>
    <ol>{"".join(rows)}</ol>
    {closing_block}
    <footer>
      Kept on {esc(platform.node())} on {today}.<br>
      Written at home with {esc(model_label)}. No copy of this page was sent anywhere.
    </footer>
  </article>
</body>
</html>
"""
