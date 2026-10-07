"""The site in a real browser: the built app (web/dist) served by the API on a test database, driven by Playwright.

Run after `npm run build` in web/:  pytest -q tests/test_site.py
Skipped when Playwright or the build is missing (the backend CI job); the `site` CI job runs it on every push and PR.
Any JavaScript error or failed request on a page fails the test.
"""

import re
import socket
import threading
import time
from datetime import datetime, timedelta
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
    monkeypatch.setattr(extractors, "extract_url", lambda url: Extracted(
        kind="article", title="Mesurer un agent sur de vraies tâches", source_url=url, author="Jane Doe",
        content="Un benchmark d'agents doit utiliser de vraies tâches et mesurer le coût. " * 30))
    contexts, problems = [], []

    def open_page(path: str = "/", viewport: dict = PHONE, lang: str = "fr", signed_in: bool = True,
                  expected_status: int | None = None, touch: bool = False) -> Page:
        """`expected_status`: an HTTP error the test causes on purpose (the browser logs it as a console error).
        `touch`: a touch screen, as on an iPhone (see _swipe)."""
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
            expected_status and f"status of {expected_status}" in m.text) else None)
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
    expect(page.get_by_placeholder("Chercher un sujet, une idée, une personne…")).to_be_visible()


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
        ("/add", page.get_by_role("heading", name="Ajouter à ta KB")),
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
    summary = page.locator(".sheet .ruled")
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

    page.get_by_role("button", name="Aujourd'hui").first.click()
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


def test_journal_from_the_menus(site):
    page = site("/perso")
    page.get_by_role("link", name="Journal").click()
    expect(page.get_by_role("heading", name="Journal", exact=True)).to_be_visible()
    desk = site("/", viewport=DESKTOP)
    desk.locator(".rail").get_by_role("link", name="Journal").click()
    expect(desk.get_by_role("heading", name="Journal", exact=True)).to_be_visible()


def test_english(site):
    page = site("/")
    _seed(page)
    page.locator(".topbar .lang").get_by_role("button", name="EN").click()
    expect(page.locator(".tabbar").get_by_role("link", name="Feed")).to_be_visible()
    expect(page.get_by_placeholder("Search for a topic, an idea, a person…")).to_be_visible()
    expect(page.locator(".fiche .ruled").first).to_contain_text("Summary of the article")   # the English card
    page.goto("/journal")
    expect(page.locator(".cal-wd").first).to_have_text("Mon")
    expect(page.get_by_role("button", name="Add to the journal")).to_be_visible()
    page.locator(".topbar .lang").get_by_role("button", name="FR").click()
    expect(page.get_by_role("button", name="Ajouter au journal")).to_be_visible()


def test_add_a_link(site):
    page = site("/add")
    page.get_by_label("Lien ou note").fill("https://blog.ex.com/agents")
    page.get_by_role("button", name="Ajouter à la KB").click()
    expect(page.get_by_role("status")).to_contain_text("Ajouté à ta KB ✓")
    drain()
    page.goto("/")
    expect(page.get_by_role("heading", name="Mesurer un agent sur de vraies tâches")).to_be_visible()


def test_upload_a_file(site, tmp_path):
    page = site("/add")
    pdf = tmp_path / "notes.txt"
    pdf.write_text("Des notes de lecture sur les agents.")
    page.locator("input[type=file]").set_input_files(str(pdf))
    page.get_by_role("button", name="Ajouter à la KB").click()
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


def test_mouse_clicks_still_open_cards(site):
    page = site("/", viewport=DESKTOP)
    ids = _seed(page)
    page.reload()
    page.locator(f"a[href='/item/{ids['article']}']").click()
    expect(page.get_by_role("button", name="Archiver")).to_be_visible()
