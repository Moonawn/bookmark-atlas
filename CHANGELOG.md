# Changelog

## 0.1.0 — 2026-09-07

Initial independent release of Bookmark Atlas.

- X OAuth API and authenticated web-session adapters with conservative automatic fallback.
- PKCE OAuth login, refresh and private credential storage.
- Local SQLite archive with page transactions, raw response preservation, independent transport cursors, historical resume and owner isolation.
- Deduplicated membership tracking, stable content hashes and retryable incremental analysis.
- Local rule extraction and optional local Ollama Chinese summaries.
- Markdown topic indexes, per-item notes, JSON export and Chinese substring search.
- Manual sync and interval scheduler CLI; no automatic system startup changes.
- Synthetic automated tests and cross-platform CI.

X web collection was live-tested. Official API and Ollama integrations passed mock tests but await live credentials/model configuration; see [verification](docs/verification.md).
