#!/usr/bin/env python3
"""Ofrenda: an altar that remembers out loud.

    python3 ofrenda.py serve          light the candles in a browser
    python3 ofrenda.py seance         the same altar, in the terminal
    python3 ofrenda.py doctor         check the model, the runtime, the record
    python3 ofrenda.py keepsake       write the recuerdo for the last night

Everything the program knows lives in ./data/altar.json on this machine.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import keepsake as keepsake_mod  # noqa: E402
from core import llm, seance, server  # noqa: E402
from core.store import Altar  # noqa: E402

DATA = ROOT / "data" / "altar.json"


def _banner(app) -> None:
    backend = app.backend.describe() if app.backend else {
        "name": "none", "model": "no model", "online": False
    }
    person = app.altar.person
    name = person.get("name") or "an empty altar"
    dates = " to ".join(filter(None, [person.get("born"), person.get("died")]))
    lines = [
        "",
        "  ofrenda",
        "  an altar that remembers out loud",
        "",
        f"  person    {name}{f', {dates}' if dates else ''}",
        f"  cards     {len(app.altar.cards)}",
        f"  model     {backend['model']} via {backend['name']}",
        f"  record    {app.data_path}",
        "",
    ]
    print("\n".join(lines))


def cmd_serve(args) -> int:
    backend = None
    if not args.no_model:
        print("  waking the local model...", end="", flush=True)
        try:
            backend = llm.connect(verbose=args.verbose)
            print(f" ready ({backend.describe()['model']})")
        except llm.BackendError as exc:
            print(" not available")
            print(f"\n  {exc}\n")
            print("  The altar still works. Every answer will be the card itself,"
                  " read out word for word.")
            if args.strict:
                return 2
    data_path = Path(args.data) if args.data else DATA
    app = server.AltarApp(data_path, backend, verbose=args.verbose, sample=args.sample)
    port = args.port or server.free_port()
    httpd = server.serve(app, host=args.host, port=port)
    _banner(app)
    address = f"http://localhost:{port}"
    print(f"  listening {address}\n")
    print("  nothing leaves this machine. ctrl-c puts the candles out.\n")
    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(address)).start()

    def put_out_candles(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, put_out_candles)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  the candles are out.")
    finally:
        httpd.server_close()
        if backend:
            backend.close()
    return 0


def cmd_seance(args) -> int:
    altar = _altar(args)
    backend = None
    if not args.no_model:
        try:
            backend = llm.connect(verbose=args.verbose)
        except llm.BackendError as exc:
            print(f"\n  no model: {exc}\n  reading the cards aloud instead.\n")
    try:
        return seance.run(altar, backend, delay=0.0 if args.fast else 0.012)
    finally:
        if backend:
            backend.close()


def cmd_doctor(args) -> int:
    print("\n  ofrenda doctor\n")
    ok = True

    def check(label: str, good: bool, detail: str) -> None:
        nonlocal ok
        ok = ok and good
        print(f"  [{'ok' if good else '--'}] {label:<18} {detail}")

    check("python", sys.version_info >= (3, 10), platform.python_version())
    binary = llm.find_binary()
    check("llama-server", binary is not None, str(binary) if binary else
          "missing. scripts/fetch-runtime.sh, or build llama.cpp and put it on PATH")
    model = llm.find_model()
    size = ""
    if model:
        size = f"{model.stat().st_size / 1e9:.2f} GB"
    check("model", model is not None, f"{model.name} {size}" if model else
          "missing. run scripts/fetch-model.sh, or set OFRENDA_MODEL")
    cards = 0
    if DATA.exists():
        try:
            cards = len(json.loads(DATA.read_text("utf-8")).get("cards", []))
        except json.JSONDecodeError:
            cards = 0
    check("record", True, f"{DATA} ({cards} cards)")
    print(f"  [--] {'network':<18} not used, by design")
    print()
    if binary and model:
        print("  Everything needed is here. Try: python3 ofrenda.py seance --sample abram\n")
    else:
        print("  Two ways to finish this:\n")
        print("    1. scripts/fetch-runtime.sh   prebuilt llama.cpp for this machine")
        print("    2. build it yourself: https://github.com/ggml-org/llama.cpp")
        print("       then export PATH=$PATH:./build/bin\n")
        print("  The model is a GGUF file. Any one you like works, from a 0.6B")
        print("  up to whatever your memory allows. scripts/fetch-model.sh gets")
        print("  Qwen3 1.7B, about 1.1 GB.\n")
    return 0 if ok else 1


def cmd_keepsake(args) -> int:
    altar = Altar.load(Path(args.data or DATA))
    if not altar.cards:
        print("  no cards, no recuerdo.")
        return 1
    dedication = args.dedication or keepsake_mod.DEDICATION
    base = Path(args.out) if args.out else ROOT / "recuerdo"
    html = keepsake_mod.as_html(altar, dedication, args.model_label)
    text = keepsake_mod.as_text(altar, dedication, args.model_label)
    Path(f"{base}.html").write_text(html, "utf-8")
    Path(f"{base}.txt").write_text(text, "utf-8")
    print(f"  wrote {base}.html and {base}.txt")
    return 0


def cmd_new(args) -> int:
    target = Path(args.data or DATA)
    if target.exists() and not args.force:
        answer = input(f"  {target} exists. replace it? [y/N] ").strip().lower()
        if answer != "y":
            print("  left alone.")
            return 0
    if args.sample:
        Altar.from_sample(ROOT / "samples" / f"{args.sample}.json", target)
        print(f"  a new altar, from samples/{args.sample}.json")
    else:
        Altar(target).save()
        print("  an empty altar. add cards in the browser.")
    return 0


def _altar(args) -> Altar:
    if getattr(args, "sample", None):
        path = ROOT / "samples" / f"{args.sample}.json"
        if not path.exists():
            raise SystemExit(f"no sample named {args.sample}")
        return Altar.from_sample(path, Path(args.data or "/tmp/ofrenda-session.json"))
    path = Path(args.data or DATA)
    return Altar.load(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ofrenda", description="an altar that remembers out loud"
    )
    parser.add_argument("--data", help="path to the record (default: data/altar.json)")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="open the altar in a browser")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--sample", help="start from a sample instead of the saved record")
    serve.add_argument("--open", action="store_true", help="open a browser when ready")
    serve.add_argument("--no-model", action="store_true", help="cards only, no model")
    serve.add_argument("--strict", action="store_true", help="exit if the model is missing")
    serve.add_argument("--verbose", action="store_true")

    seance_cmd = sub.add_parser("seance", help="talk to the altar in the terminal")
    seance_cmd.add_argument("--sample")
    seance_cmd.add_argument("--no-model", action="store_true")
    seance_cmd.add_argument("--fast", action="store_true", help="no typewriter")
    seance_cmd.add_argument("--verbose", action="store_true")

    doctor = sub.add_parser("doctor", help="check runtime, model, and record")
    doctor.set_defaults(verbose=False)

    keep = sub.add_parser("keepsake", help="write the recuerdo as html and text")
    keep.add_argument("--sample")
    keep.add_argument("--out", help="path without extension")
    keep.add_argument("--dedication", help="the line printed above the cards")
    keep.add_argument("--model-label", default="a local model")

    new = sub.add_parser("new", help="start a fresh record")
    new.add_argument("--sample")
    new.add_argument("--force", action="store_true")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    commands = {
        "serve": cmd_serve,
        "seance": cmd_seance,
        "doctor": cmd_doctor,
        "keepsake": cmd_keepsake,
        "new": cmd_new,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
