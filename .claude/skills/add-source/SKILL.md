---
name: add-source
description: Add support for a new source or format to the KB (e.g. Reddit, LinkedIn, Substack, Spotify, newsletters, EPUB). Use when the user wants the KB to handle a specific site or file type better.
---

New source: $ARGUMENTS

1. **Routing**: if the source is recognizable by its URL, add a pattern in `backend/app/urls.py` (`classify()`:
   `kind`, canonical URL without tracking parameters, useful identifiers in `ids`). Add the cases to the
   parametrized `test_classify` test.
2. **Extractor**: create `backend/app/extractors/<source>.py` returning a complete `Extracted`:
   `kind`, `content` (Markdown text), `title`, canonical `source_url`, `author`, `author_url`, `published_at`,
   `thumbnail_url`, `metadata` (ids, metrics). Raise `ExtractionError` for a permanent failure (not found,
   private), a plain exception for a failure worth retrying.
3. **Wire it** in `extractors/__init__.py` (`extract_url` or `extract_file`), falling back to `web.extract` when
   the dedicated API fails.
4. **New `kind`**: add it to `KIND_LABELS` (`pipeline.py`), to `KINDS`/`FILTER_ORDER` (`web/src/kinds.ts`), to the
   `--b-*`/`--dot-*` colors (`web/src/styles.css`, light and dark) and to the `search_kb` docstring (`mcp_server.py`).
5. **Tests**: an extractor test with faked responses (see `FakeX` in `tests/test_extractors.py`), then
   `pytest -q` and `npm run build`.
6. Update the format list in `README.md` and `README.fr.md`.
