# Compatibility and test coverage

## Supported environments

- Python 3.11–3.13 on macOS and Linux.
- X bookmarks and directed author collection through an OAuth user token or an authenticated browser session.
- Chrome on macOS has live integration coverage. Firefox, Brave, Edge, Chromium and Quark adapters require verification against the selected browser profile.
- Official API access requires an X Developer App, the appropriate OAuth scopes and available API credits.
- Built-in Ollama summaries require a running local Ollama service and an installed model. Agent Wiki compilation can instead run in the connected Agent.
- WeChat supports accessible article links, RSS/Atom discovery, and an optional local WeRSS container bridge with existing WeRead authorization. The bridge discovers only the latest article per account.

## Validation coverage

| Component | Coverage |
|---|---|
| Archive and adapters | 154 automated cases covering normalization, paging, merging, transactions, recovery, setup, scheduling, Wiki provenance, media fetching, capture replay, WeChat parsing/discovery and the WeRead bridge protocol |
| X web adapter | Live authentication, historical collection, repeat sync, Following and UserTweets access, and automatic adapter selection |
| Official X API | Mock HTTP coverage for identity, paging, expansions, long posts, author/following routes, errors and token refresh; live OAuth validation remains pending |
| Ollama | Mock HTTP coverage for output validation, retries and local endpoint restrictions; live model validation remains pending |
| WeChat | Live authorized collection from three accounts: five full articles and 41 images; repeat sync produced no duplicates. A platform-restricted older article stayed unresolved. RSS/Atom discovery, queue rollover, filtering and parser failure paths have automated coverage. |
| Distribution | wheel and source builds; clean-environment installation and CLI execution |
| CI | macOS and Linux with Python 3.11, 3.12 and 3.13 |

## Archive behavior

- Each page and its progress marker commit together. A failed page does not advance the checkpoint.
- Repeated collection of unchanged posts does not add duplicate records or repeat analysis.
- Missing posts are retained in the local archive.
- Account mismatch stops the sync. Rate limits create a cooldown shared by both X entry points.
- Unrecognized timeline structures produce an error rather than an empty collection.
- Media metadata is stored during collection. `fetch-media` downloads images; videos are framed rather than downloaded unless `--with-videos` is given. Embedded X Articles and quoted posts are read when present in the response; complete threads and arbitrary external page bodies remain outside the collection scope.

See [GitHub Actions](https://github.com/Moonawn/bookmark-atlas/actions) for current checks and [architecture](architecture.md) for the data flow.
