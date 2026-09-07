# Compatibility and test coverage

## Supported environments

- Python 3.11–3.13 on macOS and Linux.
- X Bookmarks through an OAuth user token or an authenticated browser session.
- Chrome on macOS has live integration coverage. Firefox, Brave, Edge, Chromium and Quark adapters require verification against the selected browser profile.
- Official API access requires an X Developer App, the appropriate OAuth scopes and available API credits.
- Model summaries require a running local Ollama service and an installed model.

## Validation coverage

| Component | Coverage |
|---|---|
| Archive and adapters | 69 automated cases covering normalization, paging, merging, transactions, recovery, setup, scheduling and Wiki provenance |
| X web adapter | Live authentication, historical collection, repeat sync and automatic adapter selection |
| Official X API | Mock HTTP coverage for identity, paging, expansions, long posts, errors and token refresh; live OAuth validation remains pending |
| Ollama | Mock HTTP coverage for output validation, retries and local endpoint restrictions; live model validation remains pending |
| Distribution | wheel and source builds; clean-environment installation and CLI execution |
| CI | macOS and Linux with Python 3.11, 3.12 and 3.13 |

## Archive behavior

- Each page and its progress marker commit together. A failed page does not advance the checkpoint.
- Repeated collection of unchanged posts does not add duplicate records or repeat analysis.
- Missing posts are retained in the local archive.
- Account mismatch stops the sync. Rate limits create a cooldown shared by both X entry points.
- Unrecognized timeline structures produce an error rather than an empty collection.
- Media metadata is stored; image and video binaries, complete threads and linked-page bodies are outside the current collection scope.

See [GitHub Actions](https://github.com/Moonawn/bookmark-atlas/actions) for current checks and [architecture](architecture.md) for the data flow.
