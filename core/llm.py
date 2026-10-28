"""Talk to a local model.

There is no API key in this file and no hostname that leaves the machine.
The default backend spawns `llama-server` from llama.cpp on a loopback port and
shuts it down with the app. A second backend lets you point Ofrenda at any
OpenAI-compatible server you already run (llama.cpp, Ollama, LM Studio, vLLM).
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR_BIN = ROOT / "vendor" / "llama.cpp" / "llama-server"
MODELS_DIR = ROOT / "models"


class BackendError(RuntimeError):
    """Raised when the local model cannot be reached or refuses a request."""


@dataclass
class ChatResult:
    text: str
    tokens: int = 0
    seconds: float = 0.0
    prompt_tokens: int = 0
    cached_tokens: int = 0

    @property
    def tokens_per_second(self) -> float:
        if not self.tokens or not self.seconds:
            return 0.0
        return round(self.tokens / self.seconds, 1)


def find_binary() -> Path | None:
    """Look for llama-server in the places a person would actually put it."""
    explicit = os.environ.get("OFRENDA_LLAMA_SERVER")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates += [
        VENDOR_BIN,
        ROOT / "vendor" / "llama-server",
        Path.home() / ".local" / "bin" / "llama-server",
        Path("/usr/local/bin/llama-server"),
        Path("/opt/homebrew/bin/llama-server"),
    ]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    found = shutil.which("llama-server")
    return Path(found) if found else None


def find_model() -> Path | None:
    explicit = os.environ.get("OFRENDA_MODEL")
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    ggufs = sorted(MODELS_DIR.glob("*.gguf"), key=lambda p: p.stat().st_size)
    return ggufs[0] if ggufs else None


def _die_with_parent() -> None:
    """Ask the kernel to take llama-server down with us, even on a hard kill."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise BackendError(f"{url} returned {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BackendError(f"could not reach the model at {url}: {exc}") from exc


class Backend:
    """Shared request shape for every local engine."""

    name = "local"
    model_label = "unknown"

    def chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 160,
        temperature: float = 0.4,
        top_p: float = 0.9,
        json_schema: dict | None = None,
        timeout: float = 240.0,
    ) -> ChatResult:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"name": self.name, "model": self.model_label, "online": True}

    def close(self) -> None:
        pass


class LoopbackBackend(Backend):
    """An OpenAI-compatible server that is already running on this machine."""

    name = "server"

    def __init__(self, base_url: str, model_label: str = "local model"):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        self.model_label = model_label

    def chat(self, messages, *, max_tokens=160, temperature=0.4, top_p=0.9,
             json_schema=None, timeout=240.0) -> ChatResult:
        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": 1.08,
            "cache_prompt": True,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if json_schema:
            payload["response_format"] = {"type": "json_object", "schema": json_schema}
        started = time.time()
        data = _post_json(f"{self.base_url}/chat/completions", payload, timeout)
        elapsed = time.time() - started
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise BackendError(f"unexpected response from the model: {data}") from exc
        usage = data.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        return ChatResult(
            text=text.strip(),
            tokens=int(usage.get("completion_tokens") or 0),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            cached_tokens=int(prompt_details.get("cached_tokens") or 0),
            seconds=elapsed,
        )

    def health(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/models", timeout=3) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def close(self) -> None:
        pass


class SpawnedLlama(LoopbackBackend):
    """llama.cpp started as a child process, bound to 127.0.0.1 only."""

    name = "llama.cpp"

    def __init__(self, binary: Path, model: Path, context: int = 2048,
                 threads: int | None = None, verbose: bool = False):
        self.binary = binary
        self.model = model
        self.context = context
        self.threads = threads or max(1, (os.cpu_count() or 2) - 0)
        self.verbose = verbose
        self.process: subprocess.Popen | None = None
        self.port = _free_port()
        super().__init__(f"http://127.0.0.1:{self.port}", model.stem)

    def start(self, timeout: float = 180.0) -> None:
        command = [
            str(self.binary),
            "-m", str(self.model),
            "-c", str(self.context),
            "--parallel", "1",
            "-t", str(self.threads),
            "-b", "256",
            "-ub", "128",
            "--cache-type-k", "q8_0",
            "--cache-type-v", "q8_0",
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--no-webui",
            "--jinja",
            "--no-warmup",
        ]
        stdout = None if self.verbose else subprocess.DEVNULL
        stderr = None if self.verbose else subprocess.DEVNULL
        self.process = subprocess.Popen(
            command,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            preexec_fn=_die_with_parent,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise BackendError(
                    "llama-server exited while loading the model. "
                    "Run with --verbose to see why."
                )
            try:
                with urllib.request.urlopen(f"{self.base_url}/health", timeout=2) as resp:
                    if resp.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                time.sleep(0.3)
        raise BackendError("the model took too long to load")

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


def connect(verbose: bool = False) -> Backend:
    """Return a backend: an external local server if one is configured, else spawn."""
    remote = os.environ.get("OFRENDA_BACKEND")
    if remote:
        label = os.environ.get("OFRENDA_MODEL_NAME", "local model")
        backend = LoopbackBackend(remote, label)
        if not backend.health():
            raise BackendError(f"no model server answered at {remote}")
        return backend

    binary = find_binary()
    model = find_model()
    if binary is None:
        raise BackendError(
            "llama-server was not found. Run `python3 ofrenda.py doctor` for the two "
            "ways to install it."
        )
    if model is None:
        raise BackendError(
            "no .gguf model found in ./models. Run `scripts/fetch-model.sh` or point "
            "OFRENDA_MODEL at a file you already have."
        )
    backend = SpawnedLlama(binary, model, verbose=verbose)
    backend.start()
    return backend
