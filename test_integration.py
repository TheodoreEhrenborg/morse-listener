"""Integration test: real HTTP server + client sending actual POSTs."""
import threading
import time
from http.server import HTTPServer

import pytest

import server as srv
from server import MorseConfig, MorseDecoder, _Handler
from client import send_text

# Decoder config: fast enough for tests, slow enough that client timing works.
CFG = MorseConfig(dash_gap_ms=50, letter_gap_ms=400)

# Client timing compatible with CFG.
CLIENT_KW = dict(
    dash_gap_s=0.025,   # 25 ms between dash presses  (<50 ms dash_gap)
    symbol_gap_s=0.15,  # 150 ms between symbols       (50–400 ms)
    letter_gap_s=0.6,   # 600 ms between letters       (>400 ms)
)

FLUSH_WAIT_S = (CFG.letter_gap_ms + 100) / 1000.0


@pytest.fixture(scope="module")
def server_url():
    decoder = MorseDecoder(CFG)
    srv._decoder = decoder

    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}/", decoder

    httpd.shutdown()


def test_single_letter(server_url):
    url, decoder = server_url
    decoder.reset()
    send_text("E", url, **CLIENT_KW)
    time.sleep(FLUSH_WAIT_S)
    assert decoder.get_text() == "E"


def test_sos(server_url):
    url, decoder = server_url
    decoder.reset()
    send_text("SOS", url, **CLIENT_KW)
    time.sleep(FLUSH_WAIT_S)
    assert decoder.get_text() == "SOS"


def test_hi(server_url):
    url, decoder = server_url
    decoder.reset()
    send_text("HI", url, **CLIENT_KW)
    time.sleep(FLUSH_WAIT_S)
    assert decoder.get_text() == "HI"
