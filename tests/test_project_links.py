import html
from html.parser import HTMLParser
import json
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch
import urllib.parse

from colony import board, pins
from tests import test_board as fixtures


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.hrefs = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.hrefs += [value for key, value in attrs if key == "href"]


class ProjectLinksTest(fixtures.BoardBase):
    serve = fixtures.ReaderTest.serve
    fetch = fixtures.ReaderTest.fetch
    post = fixtures.ReaderTest.post

    def rename(self, name):
        target = self.root.with_name(name)
        self.root.rename(target)
        self.root = target

    def test_saved_redirect_keeps_its_project_after_an_earlier_project_is_discovered(self):
        self.rename("reading-list")
        board.track(self.root, register=False)
        board.save_registry({"roots": [str(self.root.parent)], "projects": [],
                             "hidden": [str(board.home())], "settings": {"messaging": False, "active_hours": "off"}})
        self.serve()
        status, headers, _ = self.fetch("/?p=0&view=chat", follow=False)
        saved = headers["Location"]
        self.assertEqual((status, saved), (303, "/?n=reading-list&view=chat"))
        earlier = self.root.parent / "Weekend recipes"
        earlier.mkdir()
        (earlier / "ROADMAP.md").write_text("# Weekend recipes\n")
        self.assertEqual(board.projects()[0], earlier)
        status, _, page = self.fetch(saved)
        self.assertEqual(status, 200)
        self.assertIn("reading-list", page)
        self.assertRegex(page, r"<a class='proj on'[^>]*href='/\?n=reading-list'")
        self.assertIn("data-name='reading-list'", page)
        status, headers, _ = self.fetch("/?p=1&view=roadmap&all=M1", follow=False)
        self.assertEqual((status, headers["Location"]), (303, "/?n=reading-list&view=roadmap&all=M1"))

    def test_names_with_spaces_and_url_characters_round_trip(self):
        self.rename("Weekend recipes & tea+#'é")
        board.track(self.root)
        self.serve()
        address = "/?n=" + urllib.parse.quote(self.root.name, safe="")
        status, headers, _ = self.fetch("/?p=0", follow=False)
        self.assertEqual((status, headers["Location"]), (303, address))
        status, _, page = self.fetch(address)
        self.assertEqual(status, 200)
        self.assertIn(address, Links(page).hrefs)
        self.assertIn(html.escape(self.root.name), page)
        status, headers, _ = self.fetch(urllib.parse.quote(str(self.root), safe="/"), follow=False)
        self.assertEqual((status, headers["Location"]), (303, address))

    def test_display_name_changes_do_not_change_the_project_address(self):
        from colony import project_names
        board.track(self.root)
        project_names.rename(self.root, "My garden & plants", changed_by="agent")
        self.serve()
        status, headers, _ = self.fetch("/?p=0", follow=False)
        self.assertEqual((status, headers["Location"]), (303, "/?n=plants"))
        status, _, page = self.fetch("/?n=plants")
        self.assertEqual(status, 200)
        self.assertIn("My garden &amp; plants", page)
        self.assertIn("/?n=plants", Links(page).hrefs)
        self.assertNotIn("/?n=My%20garden%20%26%20plants", Links(page).hrefs)

    def test_main_pages_and_chat_generate_named_project_links(self):
        board.track(self.root)
        (self.root / "brief.md").write_text("# Brief\n")
        pin = pins.add(self.root, "brief.md", "The brief", by="agent")
        board.append(self.root, "gates.jsonl", {"type": "gate", "id": "g1", "at": board.now(),
                                               "question": "Keep the old export?"})
        self.serve()
        pages = []
        for path in ("/", "/projects", "/needs-you", "/?n=plants&view=chat", "/?n=plants&view=library",
                     "/?n=plants&view=roadmap", "/open?n=plants&f=brief.md&pin=" + pin["id"]):
            status, _, page = self.fetch(path)
            self.assertEqual(status, 200, path)
            pages.append((path, page))
        pages.append(("Chat reply", board.chat_prose(f"Read [the brief]({self.root}/brief.md).", 0, self.root)))
        seen = set()
        routes = {"/", "/item", "/open", "/pins/add", "/pin/edit"}
        for source, page in pages:
            for href in Links(page).hrefs:
                url = urllib.parse.urlsplit(href)
                query = urllib.parse.parse_qs(url.query)
                if url.path not in routes or not ({"p", "n"} & query.keys()):
                    continue
                if query.get("p") == ["steward"]:
                    continue
                self.assertNotIn("p", query, (source, href))
                self.assertEqual(query.get("n"), ["plants"], (source, href))
                seen.add(url.path)
        self.assertEqual(seen, routes, "exercise the project page, items, files, pin creation and pin editing")

    def test_folder_and_document_links_open_the_project_and_reader(self):
        board.track(self.root)
        folder = self.root / "docs"
        folder.mkdir()
        document = folder / "weekend plan.md"
        document.write_text("# Weekend plan\n\nA day outside.\n")
        self.serve()
        for path in (str(self.root), str(self.root) + "/"):
            status, headers, _ = self.fetch(path, follow=False)
            self.assertEqual((status, headers["Location"]), (303, "/?n=plants"))
        status, headers, _ = self.fetch(urllib.parse.quote(str(document), safe="/"), follow=False)
        self.assertEqual(status, 303)
        destination = urllib.parse.urlsplit(headers["Location"])
        self.assertEqual(destination.path, "/open")
        self.assertEqual(urllib.parse.parse_qs(destination.query), {"n": ["plants"], "f": ["docs/weekend plan.md"]})
        status, _, page = self.fetch(headers["Location"])
        self.assertEqual(status, 200)
        self.assertIn("<article class='reader'>", page)
        self.assertIn("<h1 id='weekend-plan'>Weekend plan</h1>", page)

    def test_raw_paths_outside_projects_and_symlink_escapes_stay_not_found(self):
        board.track(self.root)
        outside = self.root.parent / "private.md"
        outside.write_text("Private outside the project")
        (self.root / "escape.md").symlink_to(outside)
        (self.root / "escape").symlink_to(self.root.parent, target_is_directory=True)
        self.serve()
        for path in (str(outside), str(self.root / "missing.md"), str(self.root / "escape.md"),
                     str(self.root / "escape" / "private.md"), str(self.root) + "/../private.md",
                     str(self.root) + "/%2e%2e/private.md", "/unregistered/path", "/open?n=plants&f=../private.md"):
            self.assertEqual(self.fetch(path, follow=False)[0], 404, path)

    def test_existing_numeric_post_fields_still_reach_the_project(self):
        board.track(self.root)
        self.serve()
        status, _ = self.post("/note", p=0, kind="item", ref="R2", text="Remember the watering can.",
                              back="/?n=plants&view=roadmap")
        self.assertEqual(status, 200)
        self.assertTrue(any(n.get("text") == "Remember the watering can." and n.get("anchor") == {"item": "R2"}
                            for n in board.notes(self.root)))

    def test_a_file_opened_from_the_inbox_goes_back_to_the_inbox(self):
        board.track(self.root)
        (self.root / "brief.md").write_text("# Garden brief\n")
        self.serve()
        status, _, page = self.fetch("/open?n=plants&f=brief.md", Referer=self.base + "/inbox")
        self.assertEqual(status, 200)
        self.assertIn("class='back' href='/inbox'>‹ Back to Inbox</a>", page)
        status, _, page = self.fetch("/open?n=plants&f=brief.md", Referer="https://elsewhere.example/inbox")
        self.assertNotIn("Back to Inbox", page, "only the board's own Inbox is a way back")
        status, _, page = self.fetch("/open?n=plants&f=brief.md", Referer=self.base + "/?n=plants&view=chat")
        self.assertIn("Back to Chat", page)

    def test_a_page_the_phone_opens_is_recorded_with_what_asked_for_it(self):
        """Temporary, with the measure it checks (out at the Nov 18 friction reading)."""
        board.track(self.root)
        self.serve()
        phone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        asked = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none"}
        self.fetch("/inbox", **{"User-Agent": phone, "Referer": self.base + "/?n=plants", **asked})
        log = board.home() / "chat-measure.log"
        line = log.read_text().splitlines()[-1]
        for part in ('"when": "nav"', '"path": "/inbox"', '"site": "none"', "/?n=plants", "Mobile/15E148"):
            self.assertIn(part, line)
        lines = len(log.read_text().splitlines())
        self.fetch("/inbox", **{"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)", **asked})
        self.fetch("/inbox", **{"User-Agent": phone})                    # a page keeping itself current
        self.assertEqual(len(log.read_text().splitlines()), lines, "only pages the phone itself opens")

    def test_named_referrer_returns_to_the_same_project_view_after_reordering(self):
        board.track(self.root)
        (self.root / "brief.md").write_text("# Garden brief\n")
        other = self.root.parent / "Weekend recipes"
        other.mkdir()
        (other / "ROADMAP.md").write_text("# Weekend recipes\n")
        board.track(other)
        self.serve()
        status, _, page = self.fetch("/open?n=plants&f=brief.md", Referer=self.base + "/?n=plants&view=chat")
        self.assertEqual(status, 200)
        self.assertIn("Back to Chat", page)
        back = html.unescape(re.search(r"class='back' href='([^']+)'", page)[1])
        reg = board.registry()
        reg["projects"].reverse()
        board.save_registry(reg)
        status, _, returned = self.fetch(back)
        self.assertEqual(status, 200)
        self.assertIn("<body data-project='plants'>", returned)
        self.assertIn("data-name='plants'", returned)
        status, _, page = self.fetch("/open?n=plants&f=brief.md",
                                     Referer=self.base + "/?n=Weekend%20recipes&p=1&view=chat")
        self.assertEqual(status, 200)
        back = html.unescape(re.search(r"class='back' href='([^']+)'", page)[1])
        self.assertIn("view=overview", back, "another project's named referrer cannot choose this project's view")

    def test_unknown_names_never_fall_back_to_another_project(self):
        board.track(self.root)
        self.serve()
        for path in ("/?n=gone", "/?n=", "/?n=&p=0", "/?n=gone&p=0", "/open?n=gone&f=ROADMAP.md", "/item?n=gone&id=R2",
                     "/pins/add?n=gone", "/pin/edit?n=gone&id=missing"):
            self.assertEqual(self.fetch(path)[0], 404, path)

    def test_named_document_audio_returns_the_current_key_for_voice_controls(self):
        board.track(self.root)
        other = self.root.parent / "Weekend recipes"
        other.mkdir()
        board.track(other)
        (self.root / "brief.md").write_text("# Garden brief\n\nKeep the plants watered.\n")
        (other / "brief.md").write_text("# Recipe brief\n\nCook a weekend meal.\n")
        board.choose_voice("0", self.root, "af_heart")
        board.choose_voice("1", other, "bm_fable")
        self.serve()
        with patch("colony.voice.ready", return_value=False):
            status, _, body = self.fetch("/open?n=plants&f=brief.md&audio=1")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["key"], "0")
            reg = board.registry()
            reg["projects"].reverse()
            board.save_registry(reg)
            status, _, body = self.fetch("/open?n=plants&f=brief.md&audio=1")
            self.assertEqual(status, 200)
            data = json.loads(body)
            self.assertEqual((data["key"], data["project"], data["voice"]), ("1", "plants", "af_heart"))
            self.assertIn("Keep the plants watered.", body)
            self.assertNotIn("Cook a weekend meal.", body)
            status, _, voice = self.fetch("/chat/voice?p=" + data["key"])
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(voice)["voice"], "af_heart")


class LastProjectPageTest(unittest.TestCase):
    def test_saved_project_tabs_keep_named_identity_and_respect_the_named_referrer(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is needed to exercise the page navigation script")
        script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(0, 'utf8');
function visit({saved, referer = '', from = '', nav = false}) {
  const redirects = [], values = new Map([['page:plants', saved], ['page:other', '/?n=other&view=console']]);
  const storage = (map) => ({getItem: (key) => map.get(key) ?? null, setItem: (key, value) => map.set(key, value)});
  const location = new URL('http://board.invalid/?n=plants');
  location.replace = (url) => redirects.push({method: 'location', url});
  const window = {colonyFrom: from};
  const context = {URL, URLSearchParams, location, window,
    document: {body: {dataset: {project: 'plants'}}, referrer: referer},
    sessionStorage: storage(new Map()), localStorage: storage(values)};
  if (nav) context.colonyNav = window.colonyNav = {replace: (url) => redirects.push({method: 'colonyNav', url})};
  vm.runInNewContext(source, context);
  return {redirects, values};
}
function restored(result, view, method) {
  assert.equal(result.redirects.length, 1);
  assert.equal(result.redirects[0].method, method);
  const destination = new URL(result.redirects[0].url, 'http://board.invalid');
  assert.equal(destination.pathname, '/');
  assert.equal(destination.searchParams.get('n'), 'plants');
  assert.equal(destination.searchParams.get('view'), view);
  assert.equal(destination.searchParams.has('p'), false);
}
restored(visit({saved: '/?n=plants&view=library'}), 'library', 'location');
restored(visit({saved: '/?p=0&view=roadmap', nav: true}), 'roadmap', 'colonyNav');
const inside = visit({saved: '/?n=plants&view=chat', referer: 'http://board.invalid/?n=plants&view=roadmap'});
assert.equal(inside.redirects.length, 0);
assert.equal(inside.values.get('page:plants'), '/?n=plants');
assert.equal(inside.values.get('lastpage'), '/?n=plants');
restored(visit({saved: '/?n=plants&view=library', referer: 'http://board.invalid/?n=other&view=chat'}), 'library', 'location');
const inPlace = visit({saved: '/?p=0&view=chat', referer: 'http://board.invalid/?n=other',
  from: 'http://board.invalid/?n=plants&view=roadmap', nav: true});
assert.equal(inPlace.redirects.length, 0);
console.log('PASS named project tab navigation');
"""
        result = subprocess.run([node, "-e", script], input=board.LASTPAGE, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS named project tab navigation", result.stdout)
