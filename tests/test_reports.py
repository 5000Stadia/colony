"""Status reports (R150): written on the person's button or their schedule, every waiting item answerable in place."""
import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from colony import board, reports
from tests.test_board import BoardBase


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi).timestamp()


def sched(**kw):
    return dict(reports.DEFAULT, **kw)


class ScheduleTest(unittest.TestCase):
    """The slots a schedule fires in, by the machine's clock. Nov 9, 2026 is a Monday."""

    def test_weekdays_at_noon(self):
        s = sched(mode="daily", at="12:00", days=[0, 1, 2, 3, 4])
        got = reports.slots(s, at(2026, 11, 8, 0), at(2026, 11, 15, 23))
        self.assertEqual([datetime.fromtimestamp(t).strftime("%a %H:%M") for t in got],
                         ["Mon 12:00", "Tue 12:00", "Wed 12:00", "Thu 12:00", "Fri 12:00"])
        self.assertEqual(reports.describe(s), "Weekdays at 12:00 PM")

    def test_every_four_hours_from_eight_runs_to_the_days_end_on_the_days_ticked(self):
        s = sched(mode="hours", every=4, at="08:00", days=list(range(7)))
        got = reports.slots(s, at(2026, 11, 9, 0), at(2026, 11, 9, 23, 59))
        self.assertEqual([datetime.fromtimestamp(t).strftime("%H:%M") for t in got], ["08:00", "12:00", "16:00", "20:00"])
        self.assertEqual(reports.describe(s), "Every 4 hours from 8:00 AM, every day")
        s = sched(mode="hours", every=4, at="00:00", days=[5, 6])
        got = reports.slots(s, at(2026, 11, 9, 0), at(2026, 11, 15, 23, 59))
        self.assertEqual(len(got), 12, "six a day, on Saturday and Sunday only")
        self.assertEqual({datetime.fromtimestamp(t).strftime("%a") for t in got}, {"Sat", "Sun"})
        self.assertEqual(reports.describe(s), "Every 4 hours from 12:00 AM, weekends")

    def test_off_fires_never(self):
        self.assertEqual(reports.slots(sched(mode="off"), at(2026, 11, 9, 0), at(2026, 11, 20, 0)), [])
        self.assertEqual(reports.describe(sched(mode="off")), "Off")
        self.assertEqual(reports.describe(sched(mode="daily", at="09:30", days=[0, 2, 4])), "Mon, Wed, Fri at 9:30 AM")


class ParseTest(unittest.TestCase):
    def test_the_writers_reply_is_read_plain_or_fenced_and_a_broken_one_is_none(self):
        d = {"overview": "All calm.", "projects": [{"name": "plants", "status": "Watering."}],
             "needs": [{"ref": "n1", "title": "Pick a pot", "situation": "s", "obvious": True, "recommend": "Clay",
                        "why": "It breathes.", "options": [{"choice": "Clay", "pros": "breathes", "cons": "heavy"},
                                                           {"pros": "no choice"}]}]}
        for text in (json.dumps(d), "```json\n" + json.dumps(d) + "\n```", "Here it is:\n" + json.dumps(d)):
            got = reports.parse(text)
            self.assertEqual(got["overview"], "All calm.")
            self.assertEqual([o["choice"] for o in got["needs"][0]["options"]], ["Clay"], "an option without a choice is dropped")
            self.assertEqual((got["needs"][0]["recommend"], got["needs"][0]["obvious"], got["needs"][0]["why"]),
                             ("Clay", True, "It breathes."))
        for text in ("", "no json here", "{not json}", "[1, 2]", json.dumps({"other": 1})):
            self.assertIsNone(reports.parse(text))


class ReportTest(BoardBase):
    def setUp(self):
        super().setUp()
        board.track(self.root)
        self.gate = board.add_gate(self.root, "Which pot for the fern?", "R3", "The fern outgrew its pot.",
                                   options=("Clay", "Plastic"), recommended="Clay", default="Clay")
        self.other = board.add_gate(self.root, "Repot the cactus now?", None, "It can wait.", options=("Yes", "No"))
        self.briefs = []

    def writer(self, reply):
        def write(brief, here):
            self.briefs.append(brief)
            return {"text": reply, "cost": 0.04, "model": "test-model", "effort": "medium", "family": "claude"}
        return write

    def reply(self, needs):
        return json.dumps({"overview": "The plants are growing.", "projects": [{"name": "plants", "status": "Building the water log."}],
                           "needs": needs})

    def test_every_waiting_item_reaches_the_writer_with_a_ref_and_its_agents_words(self):
        facts = reports.gather()
        self.assertEqual([(n["ref"], n["gate"]) for n in facts["needs"]], [("n1", self.gate["id"]), ("n2", self.other["id"])])
        self.assertIn("[n1] plants · a decision", facts["text"])
        self.assertIn("Question: Which pot for the fern?", facts["text"])
        self.assertIn("Its agent recommends: Clay", facts["text"])
        self.assertIn("Its agent's default (it does not stand by itself; the decision waits for the person): Clay", facts["text"])
        self.assertIn("No default: it waits for your answer.", facts["text"])
        self.assertIn("## plants", facts["text"])
        self.assertIn("In progress: water log", facts["text"])
        self.assertEqual(facts["projects"][0]["waiting"], 2)

    def test_a_paused_project_needs_nothing_of_the_person_and_says_what_it_holds(self):
        """The person, 2026-11-10: a project they paused doesn't need them in that stasis (nine of a report's
        twenty-one items were one paused project's)."""
        reports.start("button", writer=self.writer("I could not do it."), wait=True)
        board.pause_project(self.root)
        facts = reports.gather()
        self.assertEqual(facts["needs"], [])
        self.assertIn("paused by you: it needs nothing of you until you resume it, and holds 2 for then", facts["text"])
        self.assertEqual((facts["projects"][0]["waiting"], facts["projects"][0]["held"]), (0, 2))
        html = reports.feed_html(board.registry())          # a report from before the pause: its items folded, held
        self.assertEqual((html.count("<div class='rneed'>"), html.count("Held while this project is paused.")), (0, 4))

    def test_the_report_shows_the_writers_words_with_each_items_live_answer(self):
        reply = self.reply([{"ref": "n2", "title": "Cactus", "situation": "It is root-bound.", "obvious": False,
                             "options": [{"choice": "Yes", "pros": "room to grow", "cons": "a shock"}],
                             "recommend": "Yes", "why": "Yes: it has no room left."},
                            {"ref": "n9", "title": "Invented", "situation": "not real", "options": [], "recommend": "x"}])
        self.assertTrue(reports.start("button", writer=self.writer(reply), wait=True))
        [rep] = reports.reports()
        html = reports.feed_html(board.registry())
        self.assertIn("The plants are growing.", html)
        self.assertLess(html.index("Cactus"), html.index("Which pot for the fern?"), "the writer's order first, the skipped after")
        self.assertIn("<b>Recommend:</b> Yes: it has no room left.", html)
        cactus = html[html.index("Cactus"):html.index("Which pot for the fern?")]
        self.assertIn("<span class='tag'>Recommended</span><b>Yes</b><span class='rpro'>+ room to grow</span>"
                      "<span class='rcon'>− a shock</span>", cactus, "each option once, as a tap with its pros and cons")
        self.assertLess(cactus.index("<b>Yes</b>"), cactus.index("<b>No</b>"))
        fern = html[html.index("Which pot for the fern?"):]
        self.assertIn("<span class='tag'>Recommended</span><b>Clay</b>", fern, "skipped by the writer: its agent's pick")
        self.assertIn("<span class='rstands'>the default</span>", fern, "the default is named on its own option, with no time it stands")
        self.assertNotIn("If you skip it", fern)
        self.assertIn("Something else, or a question", fern, "and a box for anything else")
        self.assertNotIn("Invented", html, "a ref the writer made up is dropped")
        self.assertIn(f"name='gate' value='{self.gate['id']}'", html, "the item the writer skipped can still be answered")
        self.assertIn(f"name='gate' value='{self.other['id']}'", html)
        self.assertEqual(html.count("<span class='num'>"), 2)
        self.assertIn("Building the water log.", html)
        self.assertIn("about 4¢", html)
        self.assertEqual(rep["trigger"], "button")

    def test_an_answered_item_shows_its_answer_and_an_unreadable_reply_still_lists_every_item(self):
        reports.start("button", writer=self.writer("I could not do it."), wait=True)
        board.answer_gate(self.root, self.gate["id"], "Plastic")
        html = reports.feed_html(board.registry())
        self.assertIn("Your answer: Plastic", html)
        # the person, 2026-11-10: answered questions fold away, each to its title and what was answered
        done = html[html.index("<details class='rneed done'"):]
        self.assertIn("<span class='rdone'>Your answer: Plastic</span></summary>", done[:done.index("</details>")])
        self.assertEqual(html.count("<div class='rneed'>"), 1, "the one still waiting stays open")
        # tapped again (a tap that showed nothing was tapped three times): one answer, one note; another is a change
        notes = lambda: [n["text"] for n in board.notes(self.root) if (n.get("anchor") or {}).get("gate") == self.gate["id"]]
        board.answer_gate(self.root, self.gate["id"], "Plastic")
        self.assertEqual(len(notes()), 1)
        board.answer_gate(self.root, self.gate["id"], "Clay")
        self.assertEqual(len(notes()), 2)
        self.assertIn("They changed their answer: it was \"Plastic\"", notes()[-1])
        self.assertIn("This one stands", notes()[-1])
        self.assertIn("Which pot for the fern?", html)
        self.assertIn(f"name='gate' value='{self.other['id']}'", html)
        self.assertIn("What the writer said", html)
        [rep] = reports.reports()
        self.assertEqual(rep["error"], "the writer's reply wasn't readable")

    def test_the_feed_is_newest_first_with_the_earlier_folded_and_the_next_report_says_lately_since_the_last(self):
        reports.start("button", writer=self.writer(self.reply([])), wait=True)
        first = reports.reports()[-1]
        reports.start("schedule", writer=self.writer(self.reply([])), wait=True)
        self.assertIn("since the last report", self.briefs[-1])
        html = reports.feed_html(board.registry())
        self.assertEqual(html.count("<article class='card report'>"), 1)
        self.assertIn(f"data-k='rep-{first['id']}'", html, "the earlier one folded")
        self.assertLess(html.index("on schedule"), html.index("from the button"))

    def test_a_second_press_while_one_is_written_starts_none(self):
        release = threading.Event()

        def slow(brief, here):
            release.wait(5)
            return {"text": self.reply([])}
        self.assertTrue(reports.start("button", writer=slow))
        self.assertIsNotNone(reports.writing())
        self.assertIn("Writing a report", reports.feed_html(board.registry()))
        self.assertFalse(reports.start("button", writer=slow))
        release.set()
        for _ in range(100):
            if not reports.writing() and reports.reports():
                break
            time.sleep(0.05)
        self.assertEqual(len(reports.reports()), 1)

    def test_a_schedule_counts_from_when_it_was_set_and_takes_each_slot_once(self):
        noon = at(2026, 11, 9, 12)
        with patch.object(board, "now", return_value=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(noon - 3 * 86400))):
            reports.set_schedule("daily", 4, "12:00", [0, 1, 2, 3, 4])
        w = self.writer(self.reply([]))
        self.assertFalse(reports.check(now=noon - 60, writer=w), "before the slot")
        self.assertTrue(reports.check(now=noon + 60, writer=w))
        for _ in range(100):
            if not reports.writing():
                break
            time.sleep(0.05)
        self.assertFalse(reports.check(now=noon + 120, writer=w), "each slot once")
        self.assertFalse(reports.check(now=at(2026, 11, 10, 12) + reports.GRACE + 60, writer=w), "missed by more than the grace: passed over")
        self.assertFalse(reports.check(now=at(2026, 11, 10, 12) + reports.GRACE + 120, writer=w))
        reports.set_schedule("off")
        self.assertFalse(reports.check(now=at(2026, 11, 11, 12) + 60, writer=w))

    def test_a_schedule_just_set_never_fires_for_a_slot_already_past(self):
        reports.set_schedule("hours", 1, "00:00", range(7))
        self.assertFalse(reports.check(now=time.time() + 5, writer=self.writer(self.reply([]))))

    def test_each_report_is_heard_by_parts_overview_then_each_item_then_each_project(self):
        reply = self.reply([{"ref": "n2", "title": "Cactus", "situation": "It is root-bound.\n\n## not a part", "obvious": False,
                             "options": [{"choice": "Yes", "pros": "room to grow", "cons": "a shock"}],
                             "recommend": "Yes", "why": "it has no room left."},
                            {"ref": "n1", "title": "Fern pot", "situation": "It outgrew its pot.",
                             "options": [{"choice": "Clay", "pros": "breathes", "cons": ""}], "recommend": "Clay", "why": "it breathes."}])
        reports.start("button", writer=self.writer(reply), wait=True)
        reports.start("button", writer=self.writer(self.reply([])), wait=True)
        first, newest = reports.reports()
        board.answer_gate(self.root, self.gate["id"], "Plastic")
        spoken = reports.audio(first, board.projects(board.registry()))
        titles = [s["title"] for s in spoken["sections"]]
        self.assertTrue(titles[0].startswith("Status report, "))
        self.assertEqual(titles[1:], ["1. Cactus", "2. Fern pot", "plants, " + first["projects"][0]["state"] + ", 2 waiting on you"])
        text = lambda i: " ".join(b["text"] for b in spoken["blocks"][spoken["sections"][i]["start"]:spoken["sections"][i]["end"]])
        self.assertIn("The plants are growing.", text(0))
        self.assertIn("2 things wait on you.", text(0))
        self.assertIn("Yes. For: room to grow. Against: a shock.", text(1), "an item still waiting: its options, read")
        self.assertIn("not a part", text(1), "the writer's words never start a section of their own")
        self.assertIn("Recommend: it has no room left.", text(1))
        self.assertIn("No longer waiting on you.", text(2), "answered since the report: said so")
        self.assertNotIn("For: breathes", text(2), "and its options are not read")
        self.assertIn("Building the water log.", text(3))
        html = reports.feed_html(board.registry())
        for rep in (first, newest):
            self.assertIn(f"class='repplay' data-url='/reports/audio?r={rep['id']}'", html, "a Listen on every report")

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        got = json.loads(urllib.request.urlopen(f"{base}/reports/audio?r={first['id']}").read())
        self.assertEqual([s["title"] for s in got["sections"]], titles)
        self.assertIn("voice", got)
        with self.assertRaises(urllib.error.HTTPError) as no:
            urllib.request.urlopen(f"{base}/reports/audio?r=nothing")
        self.assertEqual(no.exception.code, 404)
        self.assertIn("u.pathname === '/reports/audio'", urllib.request.urlopen(base + "/reports").read().decode(),
                      "the board's player takes a report as a document")

    def test_off_by_default_and_the_page_saves_a_schedule_and_starts_a_report(self):
        self.assertEqual(reports.schedule()["mode"], "off")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), board.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        page = urllib.request.urlopen(base + "/reports").read().decode()
        self.assertIn("Automatic: Off", page)
        self.assertIn("No reports yet", page)
        self.assertIn("href='/reports'", page, "the menu links it")
        form = {"mode": "daily", "at_daily": "12:00", "at_hours": "08:00", "every": "4"} | {f"day{d}": "1" for d in range(5)}
        urllib.request.urlopen(urllib.request.Request(base + "/reports/schedule", data=urllib.parse.urlencode(form).encode()))
        self.assertEqual((reports.schedule()["mode"], reports.schedule()["days"]), ("daily", [0, 1, 2, 3, 4]))
        self.assertIn("Automatic: Weekdays at 12:00 PM", urllib.request.urlopen(base + "/reports").read().decode())
        with patch.object(reports, "write", self.writer(self.reply([]))):
            urllib.request.urlopen(urllib.request.Request(base + "/reports/new", data=b"x=1"))
            for _ in range(100):
                if reports.reports():
                    break
                time.sleep(0.05)
        self.assertEqual(reports.reports()[-1]["trigger"], "button")


if __name__ == "__main__":
    unittest.main()
