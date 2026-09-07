# Security and privacy

- Archive only accounts you own or are authorized to access.
- The X integration only reads bookmarks. It never creates or deletes X bookmarks.
- Cookies are read from one selected browser profile, filtered to the exact `x.com` domain, and retained in memory. They are not written to the archive or logs.
- Public JavaScript assets are fetched using a separate HTTP client without session headers or cookies.
- OAuth credentials are stored locally with mode 0600 inside a 0700 data directory. They are not encrypted at rest: protect your device and backups.
- SQLite, raw collection response pages, and Markdown exports contain private information. Keep them outside the source repository; they are excluded by `.gitignore` as an additional precaution.
- Model analysis is optional. v0.1 supports a loopback Ollama endpoint only. No cloud model receives your bookmarks by default.
- Archived text and model output are untrusted data. Markdown export escapes active HTML and Markdown syntax; the analyzer never executes source instructions, follows arbitrary commands, or invokes tools.
- HTTP 429 creates a site-wide cooldown. Automatic mode never switches routes to work around a rate limit or account mismatch.
- Report vulnerabilities through the repository's private security reporting facility if enabled; do not post credentials or private captures in public issues.
