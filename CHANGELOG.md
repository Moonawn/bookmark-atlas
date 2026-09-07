# Changelog

## 0.3.0 — 2026-09-07

- Directed collection from selected X authors and following lists, with rolling time windows, keyword/language filters, original/quote selection and bounded author rotation.
- Separate collection memberships and progress, shared post deduplication, and integration into existing sync schedules.
- Editable Wiki topics and note types, validated related-note links and an update log. Existing note classifications survive content-only updates.
- Updated Chinese/English introductions, a directly downloadable Agent Skill, and the moon-themed project illustration.
- SQLite schema v2 migrates existing bookmark memberships automatically. Back up the private archive before upgrading; v0.2 cannot open a v2 database.

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
