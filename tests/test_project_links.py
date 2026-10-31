import html
from html.parser import HTMLParser
import re
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
