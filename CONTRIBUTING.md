# Contributing

This is an independently implemented project, not a fork of an existing bookmark scraper.

Use Python 3.11+ on macOS or Linux:

```sh
uv sync --extra browser
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest -q
uv build
```

Tests must use synthetic fixtures and mocked transports. Never commit a real account's cookies, OAuth responses, HTML bootstrap, bookmarks, screenshots, HAR files, or archive database.

For a new site adapter, implement `identity()`, `page(cursor)`, and `close()` and normalize into `Identity`, `Item`, and `Page`. Add tests for identity, paging, empty collections, rate limits, schema changes, and mixed duplicate/new pages. See [architecture](docs/architecture.md).

Do not imply a live integration was tested when only fixtures were used. Update the release verification matrix whenever integration status changes.
