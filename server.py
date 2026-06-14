import argparse
import logging
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
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
    dash_gap_ms: float = 100.0   # two presses closer than this → dash
    letter_gap_ms: float = 1500.0  # silence ≥ this → end of letter


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
        self._timer = threading.Timer(
            self._cfg.letter_gap_ms / 1000.0, self._on_timer
        )
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_timer(self) -> None:
        with self._lock:
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
        char = MORSE.get(code, "?")
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


_decoder = MorseDecoder()


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        t_ms = time.monotonic() * 1000.0
        length = min(int(self.headers.get("Content-Length", 0)), 1024)
        self.rfile.read(length)
        logger.info("POST received  t=%.0f ms", t_ms)
        _decoder.press(t_ms)
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # suppress default access log


def run(host: str = "0.0.0.0", port: int = 8765, config: Optional[MorseConfig] = None) -> None:
    global _decoder
    _decoder = MorseDecoder(config)
    server = HTTPServer((host, port), _Handler)
    logger.info("Listening on %s:%d  dash_gap=%.0f ms  letter_gap=%.0f ms",
                host, port, _decoder._cfg.dash_gap_ms, _decoder._cfg.letter_gap_ms)
    server.serve_forever()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Morse code listener")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--dash-gap-ms", type=float, default=100.0,
                   help="Two presses within this interval form a dash (default: 100)")
    p.add_argument("--letter-gap-ms", type=float, default=1500.0,
                   help="Silence ≥ this interval ends the current letter (default: 1500)")
    args = p.parse_args()
    run(args.host, args.port, MorseConfig(args.dash_gap_ms, args.letter_gap_ms))
