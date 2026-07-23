import argparse
import json
import logging
import os
import ssl
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

import certifi

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

LOG_FILE = os.environ.get("MORSE_LOG_FILE", "morse.log")

_log_formatter = logging.Formatter(
    "%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_formatter)
_file_handler = logging.FileHandler(LOG_FILE)
_file_handler.setFormatter(_log_formatter)
logging.basicConfig(level=logging.INFO, handlers=[_console_handler, _file_handler])
logger = logging.getLogger(__name__)

MORSE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E",
    "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O",
    ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y",
    "--..": "Z", "-----": "0", ".----": "1", "..---": "2",
    "...--": "3", "....-": "4", ".....": "5", "-....": "6",
    "--...": "7", "---..": "8", "----.": "9",
}


@dataclass
class MorseConfig:
    dash_gap_ms: float = 4000.0  # two presses closer than this → dash
    letter_gap_ms: float = 8000.0  # silence ≥ this → end of letter


class MorseDecoder:
    """Stateful morse decoder driven by press() calls. Thread-safe."""

    def __init__(self, config: Optional[MorseConfig] = None) -> None:
        self._cfg = config or MorseConfig()
        self._lock = threading.Lock()
        self._state = "IDLE"  # "IDLE" | "ONE_PRESS"
        self._pending_ms: Optional[float] = None
        self._last_press_ms: Optional[float] = None
        self._symbols: list[str] = []
        self._chars: list[str] = []
        self._timer: Optional[threading.Timer] = None
        self._timer_gen = 0  # bumped whenever the timer is (re)armed or cancelled

    def press(self, t_ms: Optional[float] = None) -> None:
        if t_ms is None:
            t_ms = time.monotonic() * 1000.0
        with self._lock:
            self._handle_press(t_ms)

    def _handle_press(self, t_ms: float) -> None:
        self._cancel_timer()

        if self._state == "IDLE":
            # Flush buffered symbols if the gap since the last press clears the letter boundary.
            # This handles multi-letter sequences when timestamps are injected faster than real time.
            if self._last_press_ms is not None and self._symbols:
                if t_ms - self._last_press_ms >= self._cfg.letter_gap_ms:
                    self._flush_letter()
            self._state = "ONE_PRESS"
            self._pending_ms = t_ms
            self._last_press_ms = t_ms
            self._arm_timer()
            return

        gap = t_ms - self._pending_ms  # type: ignore[operator]
        self._last_press_ms = t_ms

        if gap < self._cfg.dash_gap_ms:
            self._add_symbol("-", gap)
            self._pending_ms = None
            self._state = "IDLE"
        elif gap < self._cfg.letter_gap_ms:
            self._add_symbol(".", gap)
            self._pending_ms = t_ms
        else:
            self._add_symbol(".", gap)
            self._flush_letter()
            self._pending_ms = t_ms

        self._arm_timer()

    def _add_symbol(self, sym: str, gap: float) -> None:
        self._symbols.append(sym)
        logger.info("symbol %r  gap=%.0f ms  so_far=%s", sym, gap, "".join(self._symbols))

    def _arm_timer(self) -> None:
        self._timer_gen += 1
        gen = self._timer_gen
        self._timer = threading.Timer(
            self._cfg.letter_gap_ms / 1000.0, self._on_timer, args=(gen,)
        )
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer(self) -> None:
        self._timer_gen += 1  # invalidate any timer that already fired but is blocked on the lock
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_timer(self, gen: int) -> None:
        with self._lock:
            if gen != self._timer_gen:
                # A press arrived (and re-armed/cancelled) between this timer firing
                # and acquiring the lock; the state is stale, so do nothing.
                return
            if self._state == "ONE_PRESS":
                logger.debug("timer: pending press → dot")
                self._symbols.append(".")
                self._state = "IDLE"
                self._pending_ms = None
            self._flush_letter()

    def _flush_letter(self) -> None:
        if not self._symbols:
            return
        code = "".join(self._symbols)
        char = MORSE.get(code) or f"undecipherable({code})"
        logger.info("letter  %s → %r", code, char)
        self._chars.append(char)
        self._symbols.clear()

    def get_text(self) -> str:
        with self._lock:
            return "".join(self._chars)

    def reset(self) -> None:
        with self._lock:
            self._cancel_timer()
            self._state = "IDLE"
            self._pending_ms = None
            self._last_press_ms = None
            self._symbols.clear()
            self._chars.clear()


class NtfyPoster:
    def __init__(self, topic: str, interval_s: float, decoder: MorseDecoder) -> None:
        self._topic = topic
        self._interval_s = interval_s
        self._decoder = decoder
        self._last_len = 0
        self._timer: Optional[threading.Timer] = None

    def start(self) -> None:
        self._schedule()

    def _schedule(self) -> None:
        self._timer = threading.Timer(self._interval_s, self._post)
        self._timer.daemon = True
        self._timer.start()

    def _post(self) -> None:
        text = self._decoder.get_text()
        new_text = text[self._last_len:]
        if new_text:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            message = f"[{ts}] {new_text}"
            try:
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"https://ntfy.sh/{self._topic}",
                        data=message.encode(),
                    ),
                    context=_SSL_CTX,
                    timeout=10,
                )
                self._last_len = len(text)
                logger.info("ntfy posted: %r", message)
            except Exception as exc:
                logger.error("ntfy failed: %s", exc)
        else:
            logger.debug("ntfy: nothing new to post")
        self._schedule()


def _load_dotenv(path: str = ".env") -> None:
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def _parse_client_ts(body: bytes) -> Optional[float]:
    """Parse client timestamp from JSON body. Returns ms since midnight, or None."""
    try:
        date_str = json.loads(body)["date"]  # "HH:MM:SS mmm"
        time_part, ms_str = date_str.rsplit(" ", 1)
        h, m, s = time_part.split(":")
        return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000.0 + int(ms_str)
    except Exception:
        return None


def _now_ms_since_midnight() -> float:
    n = datetime.now()
    return (n.hour * 3600 + n.minute * 60 + n.second) * 1000.0 + n.microsecond / 1000.0


_decoder = MorseDecoder()


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        server_t_ms = time.monotonic() * 1000.0
        length = min(int(self.headers.get("Content-Length", 0)), 1024)
        body = self.rfile.read(length)
        client_t_ms = _parse_client_ts(body)
        if client_t_ms is None:
            logger.warning("POST rejected: missing/invalid timestamp  body=%s", body)
            self.send_response(400)
            self.end_headers()
            return
        latency_ms = _now_ms_since_midnight() - client_t_ms
        logger.info("POST received  client_t=%.0f ms  latency=%.0f ms", client_t_ms, latency_ms)
        t_ms = client_t_ms
        _decoder.press(t_ms)
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # suppress default access log


def run(
    host: str = "0.0.0.0",
    port: int = 8765,
    config: Optional[MorseConfig] = None,
    ntfy_topic: Optional[str] = None,
    ntfy_interval_s: float = 600.0,
) -> None:
    global _decoder
    _decoder = MorseDecoder(config)
    if ntfy_topic:
        NtfyPoster(ntfy_topic, ntfy_interval_s, _decoder).start()
        logger.info("ntfy topic=%s  interval=%.0f s", ntfy_topic, ntfy_interval_s)
    server = HTTPServer((host, port), _Handler)
    logger.info("Listening on %s:%d  dash_gap=%.0f ms  letter_gap=%.0f ms",
                host, port, _decoder._cfg.dash_gap_ms, _decoder._cfg.letter_gap_ms)
    server.serve_forever()


if __name__ == "__main__":
    _load_dotenv()
    p = argparse.ArgumentParser(description="Morse code listener")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--dash-gap", type=float, default=4000.0,
                   help="Two presses within this interval form a dash (default: 4000 ms)")
    p.add_argument("--letter-gap", type=float, default=8000.0,
                   help="Silence ≥ this interval ends the current letter (default: 8000 ms)")
    p.add_argument("--ntfy-interval", type=float, default=10.0,
                   help="Minutes between ntfy.sh posts (default: 10)")
    p.add_argument("--no-ntfy", action="store_true",
                   help="Disable ntfy.sh posting")
    args = p.parse_args()

    ntfy_topic: Optional[str] = None
    if not args.no_ntfy:
        ntfy_topic = os.environ.get("NTFY_SH_TOPIC")
        if not ntfy_topic:
            p.error("NTFY_SH_TOPIC not set in environment or .env — pass --no-ntfy to skip")

    run(args.host, args.port,
        MorseConfig(args.dash_gap, args.letter_gap),
        ntfy_topic=ntfy_topic,
        ntfy_interval_s=args.ntfy_interval * 60)
