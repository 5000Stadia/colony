"""The lab's lock-and-play page (/lab/lockplay): its ways, its clips, the server stream and the reports it keeps."""
from http.server import ThreadingHTTPServer
import http.client
import json
import re
import threading
import time
from unittest.mock import patch

from colony import board, lab, voice
from tests.test_board import BoardBase

KEYS = [k for k, _, _ in lab.WAYS]


def mp3(n):
    """A stand-in MP3 as an encoder writes one: an ID3v2 tag, a Xing summary frame, then frames of MPEG-2 Layer III at
    24 kHz and 32 kbit/s (96 bytes each) whose sound bytes all say n, and an ID3v1 tag at the end."""
    head = bytes([0xFF, 0xF3, 0x44, 0xC4])
    xing = head + b"\0" * 9 + b"Xing" + b"\0" * (96 - 17)
    frames = (head + bytes([n]) * 92) * 5
    return b"ID3\x03\x00\x00\x00\x00\x00\x14" + b"\x01" * 20 + xing + frames + b"TAG" + b"t" * 125


class LockPlayTest(BoardBase):
    def setUp(self):
        super().setUp()
        made = self.root.parent / "voice-made"
        made.mkdir()

        def speak(text, voice_name=None):
            n = int(re.search(r"clip (\d+) of", text).group(1))
            out = made / f"{abs(hash(text))}.mp3"
            out.write_bytes(mp3(n))
            return out
        for p in (patch.object(voice, "ready", return_value=True), patch.object(voice, "speak", side_effect=speak),
                  patch.object(lab, "LEAD", 10 ** 6)):
            p.start()
            self.addCleanup(p.stop)
        lab._making.update(thread=None, error="")
        lab._held.clear()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]

    def ask(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        got = conn.getresponse()
        out = got.status, dict(got.getheaders()), got.read()
        conn.close()
        return out

    def made(self):
        lab.make()
        if lab._making["thread"]:
            lab._making["thread"].join(10)
        self.assertEqual(lab.status()["made"], 12 * len(KEYS))

    def test_the_page_offers_every_way_and_the_clips_are_made_once_in_the_background(self):
        status, _, body = self.ask("GET", "/lab/lockplay")
        self.assertEqual(status, 200)
        page = body.decode()
        for key, name, _ in lab.WAYS:
            self.assertIn(f"data-way='{key}'", page, name)
        self.assertEqual(len(KEYS), 7, "A to F, and G: the silent loop underneath")
        self.assertIn("lock the phone, and wait two minutes", page)
        self.assertIn("navigator.audioSession", page)
        self.assertIn("isTypeSupported", page)
        self.assertIn("Battery and CPU can't be read from Safari", page)
        for key in KEYS:
            self.assertIn(board.e(lab.COST[key]), page, "a cost line per way")
        self.made()
        clips = sorted((board.home() / "lab" / "lockplay" / "clips").glob("*.mp3"))
        self.assertEqual(len(clips), 12 * len(KEYS), "kept under the board's home, apart from the voice's own cache")
        self.assertEqual(lab.text("B", 3).split(".")[0], "Loop trick, clip 3 of 12")
        status, _, body = self.ask("GET", "/lab/lockplay/status")
        got = json.loads(body)
        self.assertEqual((got["made"], got["total"]), (84, 84))
        self.assertAlmostEqual(got["durations"]["D"][0], 5 * 0.024, places=3, msg="the summary frame isn't counted")

    def test_the_stream_sends_every_clip_back_to_back_without_tags_in_order(self):
        self.made()
        status, headers, body = self.ask("GET", "/lab/lockplay/stream?run=D-test")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Transfer-Encoding"), "chunked")
        self.assertEqual(headers.get("Content-Type"), "audio/mpeg")
        self.assertNotIn(b"ID3", body)
        self.assertNotIn(b"TAG", body)
        self.assertNotIn(b"Xing", body)
        self.assertEqual(len(body), 12 * 5 * 96, "every frame of every clip, nothing else")
        order = [body[i + 4] for i in range(0, len(body), 96)]
        self.assertEqual(order, [n for n in range(1, 13) for _ in range(5)], "clip 1 to 12, in order")
        sent = [r for r in lab.rows() if r.get("source") == "server" and r["event"] == "sent"]
        self.assertEqual([r["clip"] for r in sent], list(range(1, 13)), "each clip it streams, logged with its time")
        self.assertTrue(all(r["at"] for r in sent))
        for _ in range(100):                            # the board logs the stream's end just after its last byte
            if "held" in lab.rows()[-1]:
                break
            time.sleep(0.02)
        run = lab.summary()[0]
        self.assertEqual(run["sent"], 12)
        last = lab.rows()[-1]
        self.assertEqual((last["event"], last["bytes"]), ("sent all", len(body)), "the stream's bytes, logged by the board")
        self.assertIsInstance(last["held"], float, "and how long it held the connection open")
        self.assertEqual((run["server_bytes"], run["server_requests"], run["held"]), (len(body), 1, last["held"]))

    def test_a_clip_comes_in_byte_ranges_and_bare_without_its_tags(self):
        self.made()
        status, headers, body = self.ask("GET", "/lab/lockplay/clip?a=A&n=2", headers={"Range": "bytes=0-1"})
        self.assertEqual((status, body, headers["Content-Range"]), (206, b"ID", f"bytes 0-1/{len(mp3(2))}"))
        status, _, body = self.ask("GET", "/lab/lockplay/clip?a=C&n=2&bare=1")
        self.assertEqual((status, len(body)), (200, 5 * 96))
        self.assertEqual(self.ask("GET", "/lab/lockplay/clip?a=Z&n=2")[0], 404)
        self.assertEqual(lab.rows(), [], "a clip asked outside a run isn't counted")
        self.ask("GET", "/lab/lockplay/clip?a=A&n=2&run=A-9", headers={"Range": "bytes=0-1"})
        self.ask("GET", "/lab/lockplay/clip?a=A&n=2&run=A-9")
        for _ in range(100):                        # each logged just after its bytes go
            if len(lab.rows()) == 2:
                break
            time.sleep(0.02)
        served = lab.summary()[0]
        self.assertEqual((served["run"], served["server_requests"], served["server_bytes"]), ("A-9", 2, 2 + len(mp3(2))),
                         "each request of a run's and the bytes it sent, counted for its cost")

    def test_the_reports_are_kept_and_the_results_count_them(self):
        def row(run, way, clip, event, vis="visible", **more):
            return dict(approach=way, run=run, clip=clip, event=event, t=f"2026-10-31T10:00:{clip:02d}Z", vis=vis, **more)
        rows = [row("A-1", "A", 0, "run start")]
        rows += [row("A-1", "A", n, "start", "hidden" if n > 1 else "visible") for n in (1, 2)]
        rows += [row("A-1", "A", 1, "end"), row("A-1", "A", 2, "stall", "hidden"), row("A-1", "A", 5, "visibility")]
        rows += [row("B-1", "B", n, "start", "hidden" if n > 1 else "visible") for n in range(1, 13)]
        rows += [row("B-1", "B", 12, "end", "hidden"), row("B-1", "B", 12, "finished", "hidden")]
        rows += [row("G-1", "G", 1, "start", main="playing", silent="playing", bytes=1000, requests=2, calls=5,
                     hidden_calls=0, playing=2),
                 row("G-1", "G", 2, "start", "hidden", main="playing", silent="paused", bytes=3000, requests=4, calls=9,
                     hidden_calls=3, playing=1)]
        rows += [row("", "", 0, "visibility")]          # a visibility change outside a run belongs to none
        status, _, body = self.ask("POST", "/lab/lockplay/log", body=json.dumps(rows),
                                   headers={"Content-Type": "text/plain;charset=UTF-8", "Origin": f"http://127.0.0.1:{self.port}",
                                            "Host": f"127.0.0.1:{self.port}"})
        self.assertEqual((status, json.loads(body)["kept"]), (200, len(rows)))
        kept = lab.rows()
        self.assertEqual(len(kept), len(rows))
        self.assertTrue(all(r["source"] == "page" and r["at"] for r in kept), "each row with the time the board got it")
        status, _, body = self.ask("GET", "/lab/lockplay/results")
        runs = {r["run"]: r for r in json.loads(body)}
        self.assertEqual(set(runs), {"A-1", "B-1", "G-1"})
        a, b, g = runs["A-1"], runs["B-1"], runs["G-1"]
        self.assertEqual((a["reached"], a["hidden"], a["finished"], a["stalls"], a["last"]),
                         (2, 1, False, 1, "2026-10-31T10:00:02Z"), "stopped at its latest clip")
        self.assertEqual((b["reached"], b["hidden"], b["finished"], b["name"]), (12, 11, True, "Loop trick"))
        self.assertEqual((g["reached"], g["silent_paused"]), (2, True), "G says when its silent element was paused")
        self.assertEqual({k: g[k] for k in lab.MEASURES}, {"bytes": 3000, "requests": 4, "calls": 9, "hidden_calls": 3,
                                                           "playing": 2}, "its cost: the most each measure reached")
        self.assertEqual((a["bytes"], a["held"]), (0, None))
        self.assertEqual([r["run"] for r in json.loads(body)], ["G-1", "B-1", "A-1"], "newest first")

    def test_the_lab_needs_only_the_boards_usual_access(self):
        self.assertEqual(self.ask("GET", "/lab/lockplay")[0], 200, "no token, no sign-in: the board's own page access")
        self.assertEqual(self.ask("GET", "/lab/lockplay", headers={"Host": "evil.example"})[0], 403)
        status, _, _ = self.ask("POST", "/lab/lockplay/log", body="[]", headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403, "a report from another website is refused")
        self.assertEqual(lab.rows(), [])
        self.assertEqual(self.ask("POST", "/lab/lockplay/log", body="[]")[0], 200)
        self.assertEqual(self.ask("GET", "/lab/elsewhere")[0], 404)

    def test_the_ramp_holds_each_next_clip_back_on_the_board(self):
        self.made()
        page = self.ask("GET", "/lab/lockplay")[2].decode()
        self.assertIn("Wait before each next clip: 2 s, then 4 s, 6 s", page)
        self.assertIn("buffer may hide the gap", page)
        self.assertEqual([lab.gap(n) for n in (1, 2, 3, 12)], [0, 2, 4, 22], "2 s before clip 2, 22 s before clip 12")
        began = time.monotonic()
        status, _, body = self.ask("GET", "/lab/lockplay/clip?a=A&n=2&wait=1&run=A-ramp-1", headers={"Range": "bytes=0-1"})
        self.assertEqual(status, 206)
        self.assertGreaterEqual(time.monotonic() - began, 1, "the board answers only after the wait: the clip is late")
        began = time.monotonic()
        self.ask("GET", "/lab/lockplay/clip?a=A&n=2&wait=1&run=A-ramp-1")
        self.assertLess(time.monotonic() - began, 0.5, "held back once per clip, though Safari asks for it in parts")

    def test_the_ramp_quiets_the_stream_for_each_gap(self):
        self.made()
        with patch.object(lab, "GAP", 0.02):
            status, _, body = self.ask("GET", "/lab/lockplay/stream?run=D-ramp-1&ramp=1")
            for _ in range(100):
                if "held" in lab.rows()[-1]:
                    break
                time.sleep(0.02)
        self.assertEqual((status, len(body)), (200, 12 * 5 * 96))
        sent = [r for r in lab.rows() if r.get("event") == "sent"]
        self.assertEqual([r["gap"] for r in sent], [0.02 * (n - 1) for n in range(1, 13)])
        playing, gaps = 11 * 5 * 0.024, 0.02 * sum(range(12))
        self.assertGreaterEqual(lab.rows()[-1]["held"], round(playing + gaps, 1) - 0.1,
                                "each clip sent only once due: the sound before it, and every gap so far")

    def test_the_longest_gap_survived_while_hidden(self):
        def start(run, n, vis):
            return dict(approach=run[0], run=run, clip=n, event="start", t=f"2026-10-31T10:00:{n:02d}Z", vis=vis,
                        gap=2 * (n - 1) if "-ramp-" in run else 0)
        rows = [start("A-ramp-1", 1, "visible"), start("A-ramp-1", 2, "hidden"), start("A-ramp-1", 3, "hidden"),
                start("A-ramp-1", 4, "visible"), start("F-1", 1, "visible"), start("F-1", 2, "hidden")]
        self.assertEqual(self.ask("POST", "/lab/lockplay/log", body=json.dumps(rows))[0], 200)
        runs = {r["run"]: r for r in lab.summary()}
        self.assertEqual((runs["A-ramp-1"]["ramp"], runs["A-ramp-1"]["longest_gap_hidden"]), (True, 4),
                         "clip 3 started hidden after 4 s; clip 4's 6 s came with the screen on")
        self.assertEqual((runs["F-1"]["ramp"], runs["F-1"]["longest_gap_hidden"]), (False, None), "no ramp, no gaps")
        self.assertIn("Longest gap survived while hidden", lab.SCRIPT)

    def test_a_clip_the_phone_held_until_unlock_did_not_survive_its_gap(self):
        # Kabe's A run with the ramp (Oct 31): clip 4 was sent on time after its 6 s gap but started only when the
        # phone was unlocked, 44 ms before the page showed again, while still reported hidden
        def row(n, event, at, vis="hidden"):
            return dict(approach="A", run="A-ramp-2", clip=n, event=event, t=f"2026-10-31T22:08:{at}Z", vis=vis,
                        gap=2 * (n - 1) if event == "start" else 0)
        rows = [row(1, "start", "00.000", "visible"), row(1, "end", "10.000"), row(2, "start", "12.400"),
                row(2, "end", "25.000"), row(3, "start", "29.650"), row(3, "end", "42.000"),
                row(4, "start", "58.293"), row(4, "visibility", "58.337", "visible")]
        self.assertEqual(self.ask("POST", "/lab/lockplay/log", body=json.dumps(rows))[0], 200)
        run = {r["run"]: r for r in lab.summary()}["A-ramp-2"]
        self.assertEqual((run["reached"], run["hidden"], run["longest_gap_hidden"]), (4, 2, 4),
                         "clip 4 started with the unlock: not hidden, and its 6 s gap not survived")
        late = [dict(r) for r in rows[:6]] + [row(4, "start", "56.000")]     # started hidden, but 8 s past its gap
        for r in late:
            r["run"] = "A-ramp-3"
        self.assertEqual(self.ask("POST", "/lab/lockplay/log", body=json.dumps(late))[0], 200)
        run = {r["run"]: r for r in lab.summary()}["A-ramp-3"]
        self.assertEqual((run["hidden"], run["longest_gap_hidden"]), (3, 4), "held up past its gap: not survived")
