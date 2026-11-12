"""The Hacker News space: the day's best stories, kept apart from what the user saves."""

import pytest

from app import db, migrate
from app.taxonomy import normalize_space, scope_spaces


def test_hn_is_a_space():
    assert normalize_space("HN") == normalize_space("hacker news") == normalize_space("hackernews") == "hn"
    assert scope_spaces("mine") == ["main", "perso"]
    assert scope_spaces("hn") == ["hn"]
    assert scope_spaces("both") is None and scope_spaces(None) is None
    with pytest.raises(ValueError):
        scope_spaces("everything")


def test_items_accept_the_hn_space(clean_db):
    migrate.run()  # the schema is applied once by the session fixture: running it again must change nothing
    db.execute("insert into items (input_url, space) values ('https://a.example.com', 'hn')")
    with pytest.raises(Exception):
        db.execute("insert into items (input_url, space) values ('https://b.example.com', 'nope')")
