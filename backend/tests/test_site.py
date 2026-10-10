"""The site in a real browser: the built app (web/dist) served by the API on a test database, driven by Playwright.

Run after `npm run build` in web/:  pytest -q tests/test_site.py
Skipped when Playwright or the build is missing (the backend CI job); the `site` CI job runs it on every push and PR.
Any JavaScript error or failed request on a page fails the test.
"""

import re
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("playwright.sync_api")
DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
if not (DIST / "index.html").exists():
    pytest.skip("web/dist is missing: run `npm run build` in web/ first", allow_module_level=True)

from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402

from app import extractors  # noqa: E402
from app.extractors.base import Extracted  # noqa: E402

from .conftest import drain  # noqa: E402
from .test_e2e import AUTH  # noqa: E402

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1320, "height": 900}


@pytest.fixture(scope="module")
def server(database):
    import uvicorn

    from app import main

    previous, main.STATIC_DIR = main.STATIC_DIR, DIST
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started, "the test server did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(10)
    main.STATIC_DIR = previous


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def site(browser, server, clean_db, fake_llm, monkeypatch):
    """Opens pages as a signed-in user; fails the test on any page error, console error or failed request."""
    from app import folders

    folders.seed()          # as the app does when it starts
    monkeypatch.setattr(extractors, "extract_url", lambda url: Extracted(
        kind="article", title="Mesurer un agent sur de vraies tâches", source_url=url, author="Jane Doe",
        content="Un benchmark d'agents doit utiliser de vraies tâches et mesurer le coût. " * 30))
    contexts, problems = [], []

    def open_page(path: str = "/", viewport: dict = PHONE, lang: str = "fr", signed_in: bool = True,
                  expected_status: int | None = None, touch: bool = False, expected_errors: tuple = ()) -> Page:
        """`expected_status`: an HTTP error the test causes on purpose (the browser logs it as a console error).
        `touch`: a touch screen, as on an iPhone (see _swipe).
        `expected_errors`: console errors the test causes on purpose (a page made to crash), by a piece of their text."""
        ctx = browser.new_context(viewport=viewport, base_url=server, locale="fr-FR", timezone_id="Europe/Paris",
                                  has_touch=touch)
        # first visit only: switching the language in the app (which reloads it) must stick
        init = f"if (!localStorage.getItem('kb_lang')) localStorage.setItem('kb_lang', '{lang}');"
        if signed_in:
            init += "if (!localStorage.getItem('kb_token')) localStorage.setItem('kb_token', 'test-token');"
        ctx.add_init_script(init)
        contexts.append(ctx)
        page = ctx.new_page()
        page.on("pageerror", lambda e: problems.append(f"{page.url}: {e}"))
        page.on("console", lambda m: problems.append(f"{page.url}: {m.text}") if m.type == "error" and not (
            expected_status and f"status of {expected_status}" in m.text) and not any(
            e in m.text for e in expected_errors) else None)
        page.on("response", lambda r: problems.append(f"{r.status} {r.url}") if r.status >= 500 else None)
        page.goto(path)
        return page

    yield open_page
    for ctx in contexts:
        ctx.close()
    assert not problems, "\n".join(problems)


def _api(server_page: Page, method: str, path: str, **body):
    """Calls the API from the page, as the app does."""
    return server_page.evaluate(
        """async ([method, path, body, token]) => {
             const r = await fetch(path, {method, headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"},
                                         body: method === "GET" ? undefined : JSON.stringify(body)});
             return {status: r.status, body: await r.json()};
           }""", [method, path, body, AUTH["Authorization"].removeprefix("Bearer ")])


def _seed(client_page: Page) -> dict:
    """An article, a principle and a journal note, processed."""
    article = _api(client_page, "POST", "/api/ingest", url="https://blog.ex.com/agents", note="pour mon benchmark")
    principle = _api(client_page, "POST", "/api/notes", content="Je tiens mes promesses.", category="principe")
    journal = _api(client_page, "POST", "/api/notes", content="Bonne journée de travail.", category="journal")
    drain()
    return {"article": article["body"]["id"], "principle": principle["body"]["id"], "journal": journal["body"]["id"]}


def _swipe(page: Page, target, dx: float, steps: int = 14):
    """A finger dragged horizontally across `target` (real touch events, so the page sees pointerType "touch")."""
    target.wait_for(state="visible")                    # bounding_box() is None until the row is laid out
    box = target.bounding_box()
    x = box["x"] + (box["width"] - 24 if dx < 0 else 24)
    y = box["y"] + min(50, box["height"] / 2)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    for i in range(1, steps + 1):
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + dx * i / steps, "y": y + i * 0.3}]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    cdp.detach()


def test_sign_in(site):
    page = site("/", signed_in=False, expected_status=401)
    expect(page.get_by_role("heading", name="Ta knowledge base")).to_be_visible()
    page.get_by_label("Jeton d'accès").fill("mauvais")
    page.get_by_role("button", name="Se connecter").click()
    expect(page.get_by_text("Ce jeton ne correspond pas")).to_be_visible()
    page.get_by_label("Jeton d'accès").fill("test-token")
    page.get_by_role("button", name="Se connecter").click()
    expect(page.get_by_placeholder("Chercher un sujet, une personne…")).to_be_visible()
    expect(page.get_by_role("heading", name="Veille", exact=True)).to_be_visible()


@pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["phone", "desktop"])
def test_every_page_opens(site, viewport):
    page = site("/", viewport=viewport)
    ids = _seed(page)
    checks = [
        ("/", page.get_by_role("heading", name="Mesurer un agent sur de vraies tâches")),
        ("/perso", page.get_by_role("heading", name="Perso", exact=True)),
        ("/journal", page.get_by_role("heading", name="Journal", exact=True)),
        ("/digest", page.get_by_role("button", name="Générer maintenant").or_(page.get_by_text("Pas encore de digest"))),
        ("/digest/interets", page.get_by_role("heading", name="Ce qui t'intéresse")),
        ("/ask", page.get_by_role("heading", name="Demande à ta KB")),
        ("/add", page.get_by_role("heading", name="Qu'est-ce que tu gardes ?")),
        ("/folders", page.get_by_role("heading", name="Dossiers", exact=True)),
        ("/todo", page.get_by_text("Tester l'outil mentionné").first),
        ("/settings", page.get_by_role("heading", name="Réglages")),
        (f"/item/{ids['article']}", page.get_by_role("heading", name="Mesurer un agent sur de vraies tâches")),
        (f"/item/{ids['principle']}", page.get_by_text("Je tiens mes promesses.").first),
        ("/note/new", page.get_by_role("button", name="Enregistrer")),
    ]
    for path, visible in checks:
        page.goto(path)
        expect(visible.first).to_be_visible()


def test_item_page_shows_the_whole_summary(site):
    page = site("/")
    ids = _seed(page)
    page.goto(f"/item/{ids['article']}")
    summary = page.locator(".item-summary")
    expect(summary).to_contain_text("Résumé de article")
    assert summary.evaluate("el => getComputedStyle(el).webkitLineClamp") in ("none", "")


def test_journal_write_edit_delete(site):
    page = site("/journal")
    today = page.locator(".cal-day[aria-current='date']")
    expect(today).to_have_attribute("aria-pressed", "true")
    expect(page.get_by_text("Rien d'écrit ce jour-là.")).to_be_visible()
    expect(page.get_by_text("Aucune note ce mois-ci")).to_be_visible()

    page.get_by_label("Écrire pour ce jour").fill("Course le matin.\nPuis une longue journée au calme.")
    page.get_by_role("button", name="Ajouter au journal").click()
    entry = page.locator(".entry")
    expect(entry).to_have_count(1)
    expect(entry).to_contain_text("Puis une longue journée au calme.")
    expect(today.locator(".mark")).to_be_visible()
    expect(page.get_by_text("1 note ce mois-ci")).to_be_visible()

    page.reload()
    expect(page.locator(".entry")).to_have_count(1)

    page.get_by_role("button", name="Modifier").click()
    page.get_by_label("Modifier la note").fill("Course le matin, puis bibliothèque.")
    page.get_by_role("button", name="Enregistrer").click()
    expect(page.locator(".entry-text")).to_have_text("Course le matin, puis bibliothèque.")

    page.get_by_label("Ajouter une note à ce jour").fill("Le soir : appel avec ma sœur.")
    page.keyboard.press("Control+Enter")
    expect(page.locator(".entry")).to_have_count(2)
    expect(page.get_by_text("2 notes ce mois-ci")).to_be_visible()

    page.once("dialog", lambda d: d.accept())
    page.locator(".entry").first.get_by_role("button", name="Supprimer").click()
    expect(page.locator(".entry")).to_have_count(1)
    expect(page.locator(".entry-text")).to_have_text("Le soir : appel avec ma sœur.")


def test_journal_other_days(site):
    page = site("/journal")
    today = datetime.now(ZoneInfo("Europe/Paris")).date()       # the browser's day, not the test runner's (UTC)
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=14)
    page.get_by_role("button", name="Mois précédent").click()
    page.locator(f".cal-day[data-day='{last_month.isoformat()}']").click()
    expect(page).to_have_url(f"{page.url.split('/journal')[0]}/journal/{last_month.isoformat()}")
    page.get_by_label("Écrire pour ce jour").fill("Un souvenir de ce jour-là.")
    page.get_by_role("button", name="Ajouter au journal").click()
    expect(page.locator(".entry")).to_have_count(1)
    expect(page.locator(f".cal-day[data-day='{last_month.isoformat()}'] .mark")).to_be_visible()

    day = _api(page, "GET", f"/api/journal/{last_month.isoformat()}")["body"]
    assert [e["text"] for e in day["entries"]] == ["Un souvenir de ce jour-là."]

    page.get_by_role("button", name="Revenir à aujourd'hui").click()
    expect(page.locator(".cal-day[aria-current='date']")).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".entry")).to_have_count(0)

    # a deep link opens its month
    page.goto(f"/journal/{last_month.isoformat()}")
    expect(page.locator(".entry-text")).to_have_text("Un souvenir de ce jour-là.")
    # a day that doesn't exist shows today rather than a rolled-over date
    page.goto("/journal/2026-02-30")
    expect(page.locator(".cal-day[aria-current='date']")).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".cal-day[aria-pressed='true']")).to_have_count(1)
    # and the note is a Perso card with its day
    page.goto("/perso")
    expect(page.locator(".fiche .when").first).to_contain_text(str(last_month.year))


def test_phone_head_and_tab_bar(site):
    """On a phone, Veille's head carries the folders, the To do badge and the settings cog; the + in the tab bar opens
    Add."""
    page = site("/")
    _seed(page)
    page.reload()
    expect(page.get_by_role("heading", name="Veille", exact=True)).to_be_visible()
    expect(page.locator(".page-head .badge")).to_have_text(re.compile(r"^[1-9]\d*$"))   # one open action per seeded item
    page.locator(".page-head").get_by_role("link", name=re.compile("À faire")).click()
    expect(page.get_by_role("heading", name="À faire", exact=True)).to_be_visible()
    page.goto("/")
    page.locator(".page-head").get_by_role("link", name="Réglages").click()
    expect(page.get_by_role("heading", name="Réglages")).to_be_visible()
    page.go_back()
    page.locator(".page-head").get_by_role("link", name="Dossiers").click()
    expect(page.get_by_role("heading", name="Dossiers", exact=True)).to_be_visible()
    expect(page.locator(".tabbar a[aria-current='page']")).to_have_text("Veille")     # folders belong to Veille
    page.locator(".tabbar").get_by_role("link", name="Ajouter").click()
    expect(page.get_by_role("heading", name="Qu'est-ce que tu gardes ?")).to_be_visible()
    # the "Tout" chip is the default filter and clears a type filter
    page.goto("/?kind=article")
    expect(page.locator(".chip[aria-pressed='true']")).to_have_text("Articles")
    page.get_by_role("button", name="Tout", exact=True).click()
    expect(page).not_to_have_url(re.compile(r"kind="))


def test_journal_from_the_menus(site):
    page = site("/perso")
    page.get_by_role("link", name="Journal").click()
    expect(page.get_by_role("heading", name="Journal", exact=True)).to_be_visible()
    expect(page.locator(".tabbar a[aria-current='page']")).to_have_text("Perso")       # the journal belongs to Perso
    desk = site("/", viewport=DESKTOP)
    desk.locator(".rail").get_by_role("link", name="Journal").click()
    expect(desk.get_by_role("heading", name="Journal", exact=True)).to_be_visible()


def test_english(site):
    page = site("/")
    _seed(page)
    # the language lives in Settings: a row on a phone, a section on a computer
    page.get_by_role("link", name="Réglages").click()
    page.get_by_role("group", name="Langue").get_by_role("button", name="EN").click()
    page.goto("/")
    expect(page.locator(".tabbar").get_by_role("link", name="Feed")).to_be_visible()
    expect(page.get_by_placeholder("Search for a topic, a person…")).to_be_visible()
    expect(page.locator(".fiche .summary").first).to_contain_text("Summary of the article")   # the English card
    page.goto("/journal")
    expect(page.locator(".cal-wd").first).to_have_text("M")
    expect(page.get_by_role("button", name="Add to the journal")).to_be_visible()
    desk = site("/settings", viewport=DESKTOP, lang="en")
    desk.locator("#langue").get_by_role("button", name="Français").click()
    desk.goto("/")
    expect(desk.get_by_placeholder("Chercher un sujet, une idée, une personne…")).to_be_visible()


def test_add_a_link(site):
    page = site("/add")
    page.get_by_label("Lien, idée ou citation").fill("https://blog.ex.com/agents")
    page.get_by_role("button", name="Ajouter", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Ajouté à ta KB ✓")
    drain()
    page.goto("/")
    expect(page.get_by_role("heading", name="Mesurer un agent sur de vraies tâches")).to_be_visible()


def test_add_a_blocked_article_with_its_text(site, monkeypatch):
    """A site that refuses the server (Medium): pasting the link then the article's text in Ajouter still saves it."""
    from app import db
    from app.extractors import ExtractionError

    def blocked(url):
        raise ExtractionError("medium.com refuse l'accès aux serveurs (403).")

    monkeypatch.setattr(extractors, "extract_url", blocked)
    page = site("/add")
    article = "Cinq astuces pour écrire de meilleures instructions à Claude, une par paragraphe. " * 40
    page.get_by_label("Lien, idée ou citation").fill(f"https://medium.com/ex-publication/cinq-astuces-1a2b3c4d5e6f\n\n{article}")
    page.get_by_role("button", name="Ajouter", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Ajouté à ta KB ✓")
    drain()
    row = db.fetchone("select status, kind, source_url, content from items")
    assert row["status"] == "ready" and row["kind"] == "article"
    assert row["source_url"].startswith("https://medium.com/ex-publication/cinq-astuces") and "meilleures instructions" in row["content"]


def test_upload_a_file(site, tmp_path):
    page = site("/add")
    pdf = tmp_path / "notes.txt"
    pdf.write_text("Des notes de lecture sur les agents.")
    page.locator("input[type=file]").set_input_files(str(pdf))
    expect(page.get_by_text("notes.txt")).to_be_visible()
    page.get_by_role("button", name="Ajouter", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Ajouté à ta KB ✓")


def test_app_shell_files(site):
    page = site("/")
    for path in ("/sw.js", "/manifest.webmanifest", "/robots.txt"):
        assert page.request.get(path).ok, path
    assert page.request.get("/api/health").json()["ok"] is True


def _wait_for(condition, page: Page, seconds: float = 10):
    """For effects the page triggers in the background (an undo, a deletion): poll instead of guessing a delay."""
    for _ in range(int(seconds * 10)):
        if condition():
            return
        page.wait_for_timeout(100)
    assert condition()


def _exists(item_id: str) -> bool:
    from app import db

    return db.fetchone("select 1 from items where id = %s", (item_id,)) is not None


def test_swipe_to_pin_archive_and_delete(site):
    page = site("/", touch=True)
    ids = _seed(page)
    second = _api(page, "POST", "/api/ingest", url="https://blog.ex.com/evals")["body"]["id"]
    drain()
    page.reload()
    cards = page.locator(".swipe")
    expect(cards).to_have_count(2)
    card = cards.filter(has=page.locator(f"a[href='/item/{ids['article']}']"))
    toast = page.get_by_role("status")

    # a plain tap still opens the card
    card.locator(".fiche").tap()
    expect(page).to_have_url(f"{page.url.split('/item')[0]}/item/{ids['article']}")
    page.go_back()
    expect(cards).to_have_count(2)

    # long swipe right: pinned, with an undo
    _swipe(page, card, 280)
    expect(toast).to_contain_text("Épinglé")
    expect(card.locator(".fiche-head .pin")).to_be_visible()
    assert _api(page, "GET", f"/api/items/{ids['article']}")["body"]["pinned"] is True
    toast.get_by_role("button", name="Annuler").click()
    expect(card.locator(".fiche-head .pin")).to_have_count(0)
    _wait_for(lambda: _api(page, "GET", f"/api/items/{ids['article']}")["body"]["pinned"] is False, page)

    # short swipe left: the actions stay open; tapping the card closes them instead of opening it
    _swipe(page, card, -110)
    expect(card.get_by_role("button", name="Archiver")).to_be_visible()
    expect(card.get_by_role("button", name="Supprimer")).to_be_visible()
    card.locator(".fiche h3").tap()
    expect(card.get_by_role("button", name="Archiver")).to_be_hidden()
    expect(page).not_to_have_url(re.compile(r"/item/"))

    # long swipe left: archived, and found in the Archives
    _swipe(page, card, -300)
    expect(toast).to_contain_text("Archivé")
    expect(cards).to_have_count(1)
    assert _api(page, "GET", f"/api/items/{ids['article']}")["body"]["archived"] is True
    page.get_by_role("button", name="Archives").click()
    expect(page.get_by_role("heading", name="Archives")).to_be_visible()
    expect(cards).to_have_count(1)
    _swipe(page, cards.first, -110)
    cards.first.get_by_role("button", name="Ressortir").tap()
    expect(toast).to_contain_text("Sorti des archives")
    expect(page.get_by_text("Rien dans les archives.")).to_be_visible()
    page.get_by_role("button", name="Veille").click()
    expect(cards).to_have_count(2)

    # delete: undo puts it back and nothing is deleted
    other = cards.filter(has=page.locator(f"a[href='/item/{second}']"))
    _swipe(page, other, -110)
    other.get_by_role("button", name="Supprimer").tap()
    expect(toast).to_contain_text("Supprimé")
    expect(cards).to_have_count(1)
    toast.get_by_role("button", name="Annuler").click()
    expect(cards).to_have_count(2)
    page.wait_for_timeout(5500)
    assert _exists(second)

    # delete again and leave the page: the deletion happens right away
    _swipe(page, other, -110)
    other.get_by_role("button", name="Supprimer").tap()
    expect(cards).to_have_count(1)
    page.locator(".tabbar").get_by_role("link", name="Perso").click()
    expect(page.get_by_role("heading", name="Perso", exact=True)).to_be_visible()
    _wait_for(lambda: not _exists(second), page)


def test_a_card_whose_lists_were_saved_as_text(site):
    """Seen in production: a tweet whose key points were stored as one "<item>…</item>" text. Its page crashed and left
    the whole app blank, even after going back. It now opens like any other."""
    from app import db

    broken = "\n<item>Les questions comptent plus que le volume.</item>\n<item>Trois débats d'experts.</item>\n</item>\n</invoke>"
    row = db.fetchone(
        """insert into items (kind, title, status, summary, key_points, use_cases, translations)
           values ('tweet', 'Apprendre un domaine en 48 h', 'ready', 'Un étudiant prépare un examen.', %s, %s, %s)
           returning id""",
        (db.jsonb(broken), db.jsonb("- Utile pour réviser\n- Utile avant un examen"),
         db.jsonb({"en": {"title": "Learn a field in 48 h", "key_points": "<item>Questions matter.</item>"}})))
    page = site("/", touch=True)
    page.locator(f"a[href='/item/{row['id']}']").click()
    expect(page.get_by_role("heading", name="Apprendre un domaine en 48 h")).to_be_visible()
    points = page.locator("section", has=page.get_by_role("heading", name="Points clés")).locator("li")
    expect(points).to_have_text(["Les questions comptent plus que le volume.", "Trois débats d'experts."])
    expect(page.locator("section", has=page.get_by_role("heading", name="Utile pour")).locator(".use-for")).to_have_count(2)
    page.go_back()
    expect(page.locator(f"a[href='/item/{row['id']}']")).to_be_visible()


def test_a_page_that_crashes_shows_a_way_back(site):
    """Whatever breaks a page, the app around it stays up: a message, a way back, and the next page works."""
    page = site("/", touch=True, expected_errors=("React error #31", "Page crash"))
    ids = _seed(page)
    page.reload()

    def odd(route):
        body = route.fetch().json()
        route.fulfill(json={**body, "title": {"fr": "un titre rangé comme un objet"}})

    page.route(f"**/api/items/{ids['article']}", odd)
    page.locator(f"a[href='/item/{ids['article']}']").click()
    expect(page.get_by_role("heading", name="Cette page n'a pas pu s'afficher")).to_be_visible()
    expect(page.get_by_role("navigation").last).to_be_visible()             # the tab bar is still there
    page.get_by_role("button", name="Revenir en arrière").click()
    expect(page.locator(f"a[href='/item/{ids['principle']}']")).to_have_count(0)  # Veille: the article only
    expect(page.locator(f"a[href='/item/{ids['article']}']")).to_be_visible()
    page.unroute(f"**/api/items/{ids['article']}")
    page.locator(f"a[href='/item/{ids['article']}']").click()
    expect(page.get_by_role("heading", name="Mesurer un agent sur de vraies tâches")).to_be_visible()
    page.get_by_role("button", name="Plus d'actions").click()               # on a phone, the item's actions are in •••
    expect(page.get_by_role("menuitem", name="Archiver")).to_be_visible()


def test_mouse_clicks_still_open_cards(site):
    page = site("/", viewport=DESKTOP)
    ids = _seed(page)
    page.reload()
    page.locator(f"a[href='/item/{ids['article']}']").click()
    expect(page.get_by_role("button", name="Archiver")).to_be_visible()


_OVERFLOW = """() => {
  // elements reaching past the screen's right edge that no scrolling strip (chips, cards to rediscover) clips
  const vw = document.documentElement.clientWidth, out = [];
  const clipped = (el) => {
    for (let p = el.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
      if (getComputedStyle(p).overflowX !== "visible") return true;
    }
    return false;
  };
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    if (r.width && r.right > vw + 1 && !clipped(el) && getComputedStyle(el).position !== "fixed")
      out.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 30)}: ${(el.textContent || "").trim().slice(0, 40)}`);
  }
  return out;
}"""


def test_no_page_slides_sideways_on_a_phone(site):
    """Long names, links and errors stay inside the screen, and fields are big enough that iPhone doesn't zoom."""
    from app import db

    page = site("/", touch=True)
    _seed(page)
    long_url = "https://a-very-long-blog-name.example.com/feeds/all-posts-and-comments.atom?format=full&lang=fr"
    db.execute("""insert into watch (kind, name, feed_url, origin, last_error) values
                  ('feed', %s, %s, 'manual', %s)""", (long_url, long_url, f"HTTPError 404 for {long_url}"))
    db.execute("""insert into watch (kind, name, x_handle, origin, note) values
                  ('person', 'Quelqu''un au nom vraiment très long pour un téléphone', 'averyveryverylonghandle', 'manual',
                   'Écrit sur https://averyveryverylonghandle.substack.com/p/une-adresse-sans-espaces-qui-ne-finit-pas')""")
    entry = {"key": "hn:1", "section": "essentiel", "title": "Un titre", "summary": "Un résumé.", "why": "", "kind": "article",
             "url": long_url, "source": "Hacker News", "author": "averyveryverylongauthornamewithoutanyspaceatall_and_more",
             "person": None, "published_at": None, "in_kb": False, "links": {}}
    db.execute("""insert into digests (kind, period_start, period_end, status, headline, data)
                  values ('daily', current_date, current_date, 'ready', 'Une journée.', %s)""",
               (db.jsonb({"entries": [entry]}),))
    for path in ["/", "/perso", "/folders", "/journal", "/digest", "/digest/interets", "/ask", "/add", "/todo", "/settings",
                 "/settings/couts", "/settings/connecteur"]:
        page.goto(path)
        page.wait_for_load_state("networkidle")
        assert page.evaluate(_OVERFLOW) == [], path
    for path, field in [("/", ".search input"), ("/digest/interets", ".add-row .field"), ("/add", "select"),
                        ("/ask", ".model-pick select")]:
        page.goto(path)
        size = page.locator(field).first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)")
        assert size >= 16, (path, size)
    assert page.evaluate("getComputedStyle(document.documentElement).overscrollBehaviorY") == "none"


def test_link_x_account_and_its_follows(site, monkeypatch):
    from app.digest import following

    from .test_following import FakeX

    fake = FakeX()
    monkeypatch.setattr(following, "_get", fake)
    page = site("/digest/interets")
    page.get_by_label("Ton compte X").fill("solal_test")
    page.get_by_role("button", name="Relier").click()
    expect(page.get_by_text("Relié à @solal_test")).to_be_visible()
    expect(page.locator(".watch-row")).to_have_count(0)

    fake.follow("karpathy", "Andrej Karpathy")
    page.get_by_role("button", name="Vérifier maintenant").click()
    expect(page.locator(".watch-row", has_text="Andrej Karpathy")).to_contain_text("suivi sur X")
    expect(page.get_by_text("Ajouté la dernière fois : Andrej Karpathy")).to_be_visible()

    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name=re.compile("Importer ceux d'avant")).click()
    expect(page.locator(".watch-row")).to_have_count(13)
    page.get_by_role("button", name="Délier").click()
    expect(page.get_by_label("Ton compte X")).to_be_visible()


def test_settings_menu_and_costs(site, billing_apis, monkeypatch):
    from app import costs
    from app.config import get_settings

    costs.record("anthropic", 0.42, model="claude-haiku-5-5")
    costs.record("x", 0.05)
    billing_apis.answers[costs.X_CREDITS_URL] = {"data": {"total_balance": 4.2, "free_balance": 0}}
    # on a computer, a strip of sections takes you straight to one
    desk = site("/settings", viewport=DESKTOP)
    nav = desk.get_by_role("navigation", name="Sections des réglages")
    nav.get_by_role("link", name="Connecteur Claude").click()
    heading = desk.get_by_role("heading", name="Connecteur Claude")
    desk.wait_for_function("() => { const h = [...document.querySelectorAll('h2')].find(e => e.textContent === 'Connecteur Claude');"
                           " const r = h.getBoundingClientRect(); return r.top > 0 && r.top < 260; }")
    expect(heading).to_be_in_viewport()
    expect(nav.get_by_role("link", name="Connecteur Claude")).to_have_attribute("aria-current", "true")
    expect(desk.locator("#couts .cost-totals")).to_contain_text("Ce mois-ci")

    # on a phone, grouped rows, each opening its section
    page = site("/settings", touch=True)
    rows = page.get_by_role("navigation", name="Sections des réglages")
    expect(rows.get_by_role("link", name=re.compile("^Coûts"))).to_contain_text("5,47 $US ce mois-ci")   # with the Railway plan
    rows.get_by_role("link", name=re.compile("^Coûts")).click()
    expect(page).to_have_url(re.compile(r"/settings/couts$"))
    expect(page.get_by_role("heading", name="Coûts", exact=True)).to_be_visible()

    # costs in US dollars, by service, each marked synced or estimated
    section = page.locator("#couts")
    expect(section.locator(".cost-totals")).to_contain_text("Ce mois-ci")
    claude = section.locator(".cost-row[data-service='anthropic']")
    expect(claude).to_contain_text("0,42 $US")
    expect(claude.locator(".cost-tag")).to_have_text("estimé")
    expect(claude).to_contain_text("Seulement ce que la KB consomme")
    expect(section.locator(".cost-row[data-service='railway']")).to_contain_text("forfait 5,00 $US / mois")
    expect(section).to_contain_text("ANTHROPIC_ADMIN_KEY")

    # X: the balance comes from X itself, nothing to note by hand
    x = section.locator(".cost-row[data-service='x']")
    expect(x).to_contain_text("Reste 4,20 $US")
    expect(x).to_contain_text("solde du compte, synchronisé")
    x.get_by_role("button", name="Modifier X API").click()
    expect(x.get_by_label("Limite de dépenses mensuelle")).to_be_visible()
    expect(x.get_by_label("Solde affiché sur ton compte")).to_have_count(0)

    # Claude: a balance and the monthly limit noted from the Console
    claude.get_by_role("button", name="Modifier Claude (Anthropic)").click()
    claude.get_by_label("Solde affiché sur ton compte").fill("12,5")
    claude.get_by_label("Limite de dépenses mensuelle").fill("20")
    claude.get_by_role("button", name="Enregistrer").click()
    expect(claude).to_contain_text("Reste ≈ 12,50 $US")
    expect(claude).to_contain_text("Reste ce mois ≈ 19,58 $US sur 20,00 $US de limite")
    costs.record("anthropic", 0.5)
    page.reload()
    expect(page.locator(".cost-row[data-service='anthropic']")).to_contain_text("Reste ≈ 12,00 $US")

    # with an Admin key, Claude's figures are the Console's own
    monkeypatch.setattr(get_settings(), "anthropic_admin_key", "sk-ant-admin01-test")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    billing_apis.answers[costs.ANTHROPIC_COST_URL] = {
        "data": [{"starting_at": today, "results": [{"amount": "157", "currency": "USD"}]}], "has_more": False}
    costs._cache.clear()
    page.reload()
    claude = page.locator(".cost-row[data-service='anthropic']")
    expect(claude.locator(".cost-tag")).to_have_text("synchronisé")
    expect(claude.locator(".cost-nums b")).to_have_text("1,57 $US")
    expect(claude).to_contain_text("Dont la KB : 0,92 $US ce mois-ci")
    expect(claude).to_contain_text("Reste ce mois 18,43 $US sur 20,00 $US de limite")
    expect(page.locator("#couts")).not_to_contain_text("ANTHROPIC_ADMIN_KEY")


def test_thinking_can_be_turned_off_in_settings(site):
    from app import llm

    desk = site("/settings", viewport=DESKTOP)
    desk.get_by_role("navigation", name="Sections des réglages").get_by_role("link", name="Réflexion").click()
    expect(desk.get_by_label("Laisser Claude réfléchir avant de répondre")).to_be_checked()
    page = site("/settings")                             # on a phone, a switch in the first group
    box = page.get_by_label("Laisser Claude réfléchir avant de répondre")
    expect(box).to_be_checked()                          # on by default
    box.uncheck()
    _wait_for(lambda: llm.thinking_enabled() is False, page)
    page.reload()
    box = page.get_by_label("Laisser Claude réfléchir avant de répondre")
    expect(box).not_to_be_checked()
    box.check()
    _wait_for(llm.thinking_enabled, page)


def test_generate_button_on_the_digest(site):
    from app import db

    # today as the page sees it (Paris), not the database's UTC date: they differ between 22:00 and midnight UTC
    today = datetime.now(ZoneInfo("Europe/Paris")).date()
    db.execute("""insert into digests (kind, period_start, period_end, status, headline, data)
                  values ('daily', %s, %s, 'ready', 'Une journée.', '{"entries": []}')""", (today, today))
    page = site("/digest")
    button = page.get_by_role("button", name="Générer maintenant")
    expect(button).to_be_visible()
    asked = []
    page.once("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    button.click()
    expect(page.get_by_role("heading", name="Digest du", exact=False)).to_be_visible()
    assert asked and "existe déjà" in asked[0]
    assert db.fetchone("select status from digests")["status"] == "ready"      # dismissed: nothing rewritten


def test_folders(site):
    """The Dossiers page: the default folders, a new one, an item filed by hand from its page, and the folder's own feed."""
    page = site("/")
    ids = _seed(page)
    page.locator(".page-head").get_by_role("link", name="Dossiers").click()
    cards = page.locator(".folder-card")
    expect(cards.locator(".t")).to_have_text(["ML", "Claude", "Entretien", "Perso", "Sans dossier"])

    page.get_by_role("button", name="Nouveau dossier").click()
    page.get_by_label("Nom du dossier").fill("Lectures")
    page.get_by_label("Ce qui va dedans").fill("Romans et essais")
    page.get_by_role("button", name="Créer le dossier").click()
    expect(cards.filter(has_text="Lectures")).to_contain_text("Romans et essais")

    # filed by hand from the item's page
    page.goto(f"/item/{ids['article']}")
    page.get_by_label("Dossier").select_option(label="Lectures")
    _wait_for(lambda: _api(page, "GET", f"/api/items/{ids['article']}")["body"]["folder_id"] is not None, page)
    item = _api(page, "GET", f"/api/items/{ids['article']}")["body"]
    assert item["metadata"]["manual_folder"] is True

    # the folder's feed, then rename and delete it
    page.goto("/folders")
    lectures = cards.filter(has_text="Lectures")
    expect(lectures.locator(".n")).to_have_text("1")
    lectures.click()
    expect(page.get_by_role("heading", name="Lectures", exact=True)).to_be_visible()
    expect(page.locator(".fiche")).to_have_count(1)
    expect(page.get_by_role("heading", name="Mesurer un agent sur de vraies tâches")).to_be_visible()
    page.get_by_role("button", name="Modifier le dossier").click()
    page.get_by_label("Nom du dossier").fill("Lectures du soir")
    page.get_by_role("button", name="Enregistrer").click()
    expect(page.get_by_role("heading", name="Lectures du soir", exact=True)).to_be_visible()
    page.get_by_role("button", name="Modifier le dossier").click()
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Supprimer le dossier").click()
    expect(page.get_by_role("heading", name="Dossiers", exact=True)).to_be_visible()
    expect(cards.filter(has_text="Lectures")).to_have_count(0)
    assert _api(page, "GET", f"/api/items/{ids['article']}")["body"]["folder_id"] is None

    # the items in no folder, and Dossiers in the sidebar on a computer
    page.locator(".folder-card.unfiled").click()
    expect(page.get_by_role("heading", name="Sans dossier", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Mesurer un agent sur de vraies tâches")).to_be_visible()
    desk = site("/", viewport=DESKTOP)
    desk.locator(".rail").get_by_role("link", name="Dossiers").click()
    expect(desk.get_by_role("heading", name="Dossiers", exact=True)).to_be_visible()


def test_add_into_a_folder(site):
    page = site("/add")
    page.get_by_label("Lien, idée ou citation").fill("https://blog.ex.com/agents")
    page.get_by_label("Dossier").select_option(label="Entretien")
    page.get_by_role("button", name="Ajouter", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Ajouté à ta KB ✓")
    drain()
    folders = {f["name"]: f["id"] for f in _api(page, "GET", "/api/folders")["body"]["folders"]}
    items = _api(page, "GET", f"/api/items?folder={folders['Entretien']}")["body"]["items"]
    assert [i["title"] for i in items] == ["Mesurer un agent sur de vraies tâches"]


def test_feed_type_filters_group_kinds(site):
    """« Vidéos » shows YouTube videos and uploaded videos, « Papiers & PDF » papers and PDFs."""
    from app import db

    for kind, title in [("youtube", "Une vidéo YouTube"), ("video", "Une vidéo envoyée"), ("pdf", "Un PDF"),
                        ("paper", "Un papier"), ("tweet", "Un tweet")]:
        db.execute("insert into items (kind, title, status) values (%s, %s, 'ready')", (kind, title))
    page = site("/", viewport=DESKTOP)
    page.get_by_role("button", name="Vidéos").click()
    expect(page.locator(".fiche h3")).to_have_text(["Une vidéo envoyée", "Une vidéo YouTube"], ignore_case=False)
    page.get_by_role("button", name="Papiers & PDF").click()
    expect(page.locator(".fiche h3")).to_have_count(2)
    expect(page.locator(".feed-main")).not_to_contain_text("Un tweet")
