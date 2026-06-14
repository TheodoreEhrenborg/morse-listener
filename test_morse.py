"""Tests for MorseDecoder. Uses a 10x faster config to keep wall-clock time short."""
import time

import pytest

from server import MorseConfig, MorseDecoder

# Test config: 10x faster than production defaults.
CFG = MorseConfig(dash_gap_ms=50, letter_gap_ms=200)

# Timing constants for building press sequences (all in ms, compatible with CFG).
DASH_MS = 20      # gap between the two presses that form a dash  (<50)
SYMBOL_MS = 100   # gap between symbols in the same letter        (50–200)
LETTER_MS = 350   # gap that separates letters                    (>200)

FLUSH_WAIT_S = (CFG.letter_gap_ms + 50) / 1000.0  # real seconds to wait for final letter


def make_times(pattern: str) -> list[float]:
    """Turn a pattern string into a list of press timestamps (ms).

    Characters:
      '.'  → single press (dot)
      '-'  → two quick presses (dash)
      ' '  → letter boundary gap
    """
    t = 0.0
    result: list[float] = []
    need_symbol_gap = False
    for ch in pattern:
        if ch == " ":
            t += LETTER_MS
            need_symbol_gap = False
        elif ch == ".":
            if need_symbol_gap:
                t += SYMBOL_MS
            result.append(t)
            need_symbol_gap = True
        elif ch == "-":
            if need_symbol_gap:
                t += SYMBOL_MS
            result.append(t)
            t += DASH_MS
            result.append(t)
            need_symbol_gap = True
    return result


def decode(pattern: str) -> str:
    d = MorseDecoder(CFG)
    for t in make_times(pattern):
        d.press(t)
    time.sleep(FLUSH_WAIT_S)
    return d.get_text()


# --- single-symbol letters ---

def test_e():
    assert decode(".") == "E"   # E = .


def test_t():
    assert decode("-") == "T"   # T = -


# --- multi-symbol letters ---

def test_s():
    assert decode("...") == "S"   # S = ...


def test_o():
    assert decode("---") == "O"   # O = ---


def test_a():
    assert decode(".-") == "A"   # A = .-


def test_n():
    assert decode("-.") == "N"   # N = -.


def test_h():
    assert decode("....") == "H"   # H = ....


# --- multi-letter words ---

def test_sos():
    assert decode("... --- ...") == "SOS"


def test_hi():
    assert decode(".... ..") == "HI"


def test_run():
    # R=.-. U=..- N=-.
    assert decode(".-. ..- -.") == "RUN"
