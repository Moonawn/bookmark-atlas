# Changelog

## 0.2.0 — 2026-09-07

- Interactive setup for collection, timezone, daily or interval scheduling, and organization preferences.
- Daily wall-clock scheduling with explicit timezone and daylight-saving handling.
- Agent Wiki queue, source-linked concept notes, content-hash validation and preserved personal notes.
- A portable Agent Skill for guided setup, scheduling and knowledge compilation.
- Chinese and English project homepages with shorter reading paths and dedicated usage guides.


## 0.1.0 — 2026-09-07

Initial release of Bookmark Atlas.

- X OAuth API and authenticated web-session adapters with conservative automatic fallback.
- PKCE OAuth login, refresh and private credential storage.
- Local SQLite archive with page transactions, raw response preservation, independent transport cursors, historical resume and owner isolation.
- Deduplicated membership tracking, stable content hashes and retryable incremental analysis.
- Local rule extraction and optional local Ollama Chinese summaries.
- Markdown topic indexes, per-item notes, JSON export and Chinese substring search.
- Manual sync and interval scheduler CLI; no automatic system startup changes.
- Synthetic automated tests and cross-platform CI.

See [compatibility and test coverage](docs/verification.md) for supported environments and integration requirements.
