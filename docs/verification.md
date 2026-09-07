# v0.1.0 verification

Date: 2026-09-07. The project was independently implemented in this repository. No source was copied from the bookmark projects discussed during initial research.

## Verification matrix

| Area | Verification | Status |
|---|---|---|
| Core archive and adapters | 51 synthetic automated tests | Passed |
| Code quality | Ruff lint and format checks | Passed |
| Distribution | wheel + source distribution build | Passed |
| Installation | wheel installed into a separate clean Python environment; version and database initialization | Passed |
| X web authentication | Current Chrome login; server-provided bootstrap account identity | Passed live |
| X web collection | Bounded first capture; historical backfill; terminal-page handling | Passed live |
| X repeat sync / scheduler one-shot | After a full scan: zero added, zero updated, zero reanalyzed | Passed live |
| Auto selection | No API credentials → web adapter, explicit fallback reason | Passed live |
| Local analysis/export | Rule-based extraction, topic index and per-item Markdown | Passed live |
| Official X API | Mock transport: identity, paging, expanded authors/media, long posts, errors, PKCE primitives and refresh | Passed offline |
| Official X API live OAuth | No Developer App credentials supplied during this release | Not live-tested |
| Ollama analysis | Mock transport, output validation, retry behavior and loopback restriction | Passed offline; no local model was configured |
| Other browsers | Cookie adapter code only; live test used Chrome on macOS | Not live-tested |

The unit test count includes parameterized scenarios. Test results do not assert that an external service will remain compatible. See GitHub Actions for the platform matrix on the published commit.

## Issues found and fixed during live testing

1. X lazy-loads the Bookmarks GraphQL definition into a separate webpack chunk. Discovery now reads the authenticated page's name/hash maps and fetches relevant public script chunks without executing remote JavaScript.
2. The old v1.1 account settings route returned 404. Identity now comes from the authenticated page's server-provided initial state, rather than that route or an assumed local cookie ID.
3. An empty terminal bookmark page can return the same cursor. This now ends the scan; a repeated cursor with actual items still fails safely.
4. Volatile video metadata changed between captures and caused redundant analysis. Media records now merge by identity, while content hashing excludes transient metadata. Raw responses remain preserved.

## Privacy boundary

All live captures, account identifiers, browser data, credentials and generated personal reports remain outside this repository. Public tests contain synthetic data only. Release notes must not include private bookmark content or credentials.
