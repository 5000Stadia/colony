"""Kokoro, kept loaded: one JSON line in ({"text", "voice", "out"}), one line out (the WAV written, or an error). Run
by the board with the Python of colony's voice folder, which has kokoro-onnx; it imports nothing of colony's."""
import json
import sys

import soundfile
from kokoro_onnx import Kokoro

folder = sys.argv[1]
kokoro = Kokoro(f"{folder}/kokoro-v1.0.onnx", f"{folder}/voices-v1.0.bin")
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    try:
        ask = json.loads(line)
        samples, rate = kokoro.create(ask["text"], voice=ask.get("voice") or "af_heart", speed=1.0,
                                      lang="en-gb" if str(ask.get("voice", "")).startswith("b") else "en-us")
        soundfile.write(ask["out"], samples, rate)
        print(json.dumps({"out": ask["out"]}), flush=True)
    except Exception as err:                            # one bad line never ends the worker
        print(json.dumps({"error": f"{err.__class__.__name__}: {err}"}), flush=True)
