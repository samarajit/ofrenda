#!/usr/bin/env python3
"""Prove that Ofrenda does not need a network.

Runs the altar inside a network namespace that has nothing but loopback, asks it
five questions, tries to reach two well known APIs from the same namespace, and
writes the whole thing down.

    scripts/prove-offline.py                 # write the report to stdout
    scripts/prove-offline.py --out proof.txt # and to a file

Needs root for `unshare`, or Linux with user namespaces enabled. On a machine
that is already airgapped, run it without --no-isolate and it will just measure.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 7788
QUESTIONS = [
    "Why is there no barber pole on the altar?",
    "What happened the day the boy fell asleep in the chair?",
    "Did he make any sound while he swept?",
    "What did he think about the war in Korea?",
    "Did he ever learn to drive?",
]


def http(path: str, payload: dict | None = None, timeout: float = 300) -> dict:
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def run_report(write) -> int:
    sys.path.insert(0, str(ROOT))
    from core import llm

    binary, model = llm.find_binary(), llm.find_model()
    subprocess.run(["ip", "link", "set", "lo", "up"], check=False)
    interfaces = subprocess.run(
        ["ip", "-brief", "addr"], capture_output=True, text=True
    ).stdout.strip()
    write("interfaces visible to the app:")
    write(interfaces or "  (ip not installed)")
    write("")
    write("model:")
    if binary and model:
        write(f"  runtime: {binary}")
        write(f"  weights: {model.name}, {model.stat().st_size / 1e9:.2f} GB")
    else:
        write("  no model found, so the altar will read the cards out loud and nothing more")
        write("  set OFRENDA_MODEL and OFRENDA_LLAMA_SERVER, or run scripts/fetch-model.sh")
    write("")

    app = subprocess.Popen(
        [sys.executable, "-u", "ofrenda.py", "serve", "--port", str(PORT), "--sample", "abram"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        for _ in range(150):
            time.sleep(2)
            try:
                if http("/api/health", timeout=3).get("ok"):
                    break
            except Exception:
                pass
        else:
            write("the altar did not come up. is the model in ./models?")
            return 1

        state = http("/api/state", timeout=10)
        person = state["person"]
        write("state:")
        write(f"  person: {person.get('name')} {person.get('born')} to {person.get('died')}")
        write(f"  cards: {len(state['cards'])}")
        write(f"  backend: {state['backend']}")
        write(
            "  disagreements: "
            + str([(pair["a"]["by"], pair["b"]["by"]) for pair in state["disagreements"]])
        )
        write("")

        for question in QUESTIONS:
            started = time.time()
            turn = http("/api/ask", {"question": question})["turn"]
            write(f"Q: {question}")
            write(
                f"  [{turn['source']}, try {turn['attempts']}, {turn['seconds']}s, "
                f"{turn['tokens']} tokens, wall {time.time() - started:.1f}s]"
            )
            write(f"  echo: {turn['echo'] or '-'}")
            write(f"  say: {turn['say']}")
            if turn["note"]:
                write(f"  earlier attempt refused: {turn['note']}")
            write("")

        write("outbound reachability from inside the app's namespace:")
        for host in ("huggingface.co", "api.openai.com"):
            try:
                socket.create_connection((host, 443), timeout=6).close()
                write(f"  {host} -> reachable (this machine is not isolated)")
            except OSError as exc:
                write(f"  {host} -> {exc}")
        write("")

        try:
            lines = subprocess.run(
                ["ps", "-o", "rss=,args=", "-C", "llama-server"],
                capture_output=True,
                text=True,
            ).stdout.strip().splitlines()
            write("memory held by the model process:")
            for line in lines:
                rss, _, args = line.strip().partition(" ")
                if rss.isdigit():
                    write(f"  {int(rss) / 1024:.0f} MB  {args.split()[0] if args else ''}")
        except Exception as exc:
            write(f"  could not read memory: {exc}")
        return 0
    finally:
        app.terminate()
        try:
            app.wait(timeout=20)
        except subprocess.TimeoutExpired:
            app.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="prove the altar runs with no network")
    parser.add_argument("--no-isolate", action="store_true", help="do not enter a network namespace")
    parser.add_argument("--out", help="also write the report to this file")
    args = parser.parse_args()

    if args.no_isolate:
        collected: list[str] = []
        code = run_report(lambda line="": (print(line, flush=True), collected.append(line)))
        if args.out:
            Path(args.out).write_text("\n".join(collected) + "\n", encoding="utf-8")
        return code

    inner = [sys.executable, str(Path(__file__).resolve()), "--no-isolate"]
    if args.out:
        inner += ["--out", args.out]
    if os.geteuid() == 0:
        prefix = ["unshare", "--net"]
    else:
        keep = "OFRENDA_MODEL,OFRENDA_LLAMA_SERVER,OFRENDA_BACKEND,OFRENDA_MODEL_NAME"
        prefix = ["sudo", f"--preserve-env={keep}", "unshare", "--net"]
    try:
        return subprocess.call(prefix + inner)
    except FileNotFoundError:
        print("unshare is not available here. run again with --no-isolate.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
