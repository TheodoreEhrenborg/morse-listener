"""Mock client: sends POST requests to the morse server encoding text."""
import json
import sys
import time
import urllib.request
from datetime import datetime

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".",
    "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---",
    "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---",
    "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-",
    "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--",
    "Z": "--..", "0": "-----", "1": ".----", "2": "..---",
    "3": "...--", "4": "....-", "5": ".....", "6": "-....",
    "7": "--...", "8": "---..", "9": "----.",
}


def send_press(url: str) -> None:
    now = datetime.now()
    # Server expects {"date": "HH:MM:SS mmm"} with millisecond precision.
    date_str = now.strftime("%H:%M:%S ") + f"{now.microsecond // 1000:03d}"
    body = json.dumps({"date": date_str}).encode()
    urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=5)


def send_text(
    text: str,
    url: str = "http://localhost:8765/",
    dash_gap_s: float = 0.05,
    symbol_gap_s: float = 6.0,
    letter_gap_s: float = 16.0,
) -> None:
    """Encode text as morse and send POST requests to the server."""
    first_char = True
    for ch in text.upper():
        if ch == " ":
            time.sleep(letter_gap_s)
            first_char = True
            continue
        code = MORSE.get(ch)
        if code is None:
            continue
        if not first_char:
            time.sleep(letter_gap_s)
        first_char = False
        for i, sym in enumerate(code):
            if i > 0:
                time.sleep(symbol_gap_s)
            if sym == ".":
                send_press(url)
            else:  # dash: two quick presses
                send_press(url)
                time.sleep(dash_gap_s / 2)
                send_press(url)


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else "SOS"
    url = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8765/"
    print(f"Sending {text!r} to {url}")
    send_text(text, url)
