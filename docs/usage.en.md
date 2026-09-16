# User guide

[English home](../README.en.md) · [中文指南](usage.zh-CN.md)

## Configuration

Install with `uv sync --extra browser`, then run `uv run bookmark-atlas setup`. The interactive prompt saves private `settings.json`. Choose `daily`, `interval` or `manual`; an IANA timezone; `topics` or `wiki`; and `rules`, `agent` or `ollama`. Agent processing requires Wiki mode. Ollama requires an installed local model. Inspect your saved configuration with `bookmark-atlas settings`. Explicit command-line options override saved settings.

The default private directory is `~/.local/share/bookmark-atlas`. Set `ATLAS_HOME` or place `--home /your/directory` before the subcommand. Keep separate directories for different X accounts.

## Browser and API access

Sign in to X in Chrome and open your bookmarks. Then run:

```sh
uv run bookmark-atlas auth check --mode web --browser chrome
uv run bookmark-atlas sync --mode web --max-pages 2
```

Other browser choices: Firefox, Brave, Edge, Chromium and Quark. Chrome on macOS has live coverage; verify other profiles before relying on them. `--cookie-file` selects a Cookies database. macOS may request access to the system keychain. Cookies are used for X requests, not copied into exports.

For the official API, create an X Developer App with callback `http://127.0.0.1:8765/callback`:

```sh
uv run bookmark-atlas auth login --client-id YOUR_CLIENT_ID
uv run bookmark-atlas auth check --mode api
uv run bookmark-atlas sync --mode api
```

OAuth uses `tweet.read users.read bookmark.read follows.read offline.access`. Use `ATLAS_X_CLIENT_SECRET` for a confidential client. `auth login` stores refreshable credentials privately. `ATLAS_X_ACCESS_TOKEN` also accepts an existing user token, without automatic refresh. App-only bearer tokens cannot replace user authorization. See [X OAuth documentation](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code).

`--mode auto` tries API first, with a conservative pre-write fallback to web; use `--prefer web` to reverse the order. It never switches to bypass a rate limit or an account mismatch. Standard proxy environment variables, system HTTPS proxy settings and `ATLAS_PROXY` are supported.

## Scheduling

```sh
uv run bookmark-atlas serve --at 21:00 --timezone Asia/Shanghai
uv run bookmark-atlas serve --interval 21600
uv run bookmark-atlas serve --once
```

Daily mode waits for the next scheduled wall-clock time. DST gaps are skipped, and a repeated local time runs once. Interval mode runs immediately, then waits. `--once` runs immediately and exits. `serve` remains a foreground process; setup does not install a daemon. For Agent processing, schedule the [Skill workflow](../skills/bookmark-atlas/SKILL.md) through your Agent host instead of running a second scheduler.

## Analysis and export

```sh
uv run bookmark-atlas analyze --ollama-model YOUR_LOCAL_MODEL
uv run bookmark-atlas export markdown --ollama-model YOUR_LOCAL_MODEL
uv run bookmark-atlas export wiki
uv run bookmark-atlas export json --out ~/Documents/bookmarks.json
uv run bookmark-atlas status
```

Rule processing produces excerpts, not generated summaries or fact checks. Ollama runs on loopback and models are not downloaded automatically. Agent Wiki compilation is described in [Wiki workflow](wiki.md). Current built-in classifications, report headings and Ollama prompts are Chinese.

## Local media and replaying stored responses

Collection saves media metadata only. To keep the images themselves — so a deleted post still shows its pictures — run the downloader on its own:

```sh
# Default: download images, keep a single frame for videos
uv run bookmark-atlas fetch-media

# See what would be fetched, writing nothing
uv run bookmark-atlas fetch-media --dry-run

# Bring video binaries down too (one video can outweigh every photo)
uv run bookmark-atlas fetch-media --with-videos

# Work in batches
uv run bookmark-atlas fetch-media --limit 50
```

Limits default to 5 MB for images and 100 MB for videos, and videos are framed rather than downloaded unless asked for. Size is uneven between the two: a measured 62-minute video came to 501 MB against a 0.19 MB average for photos. A file over its limit keeps a thumbnail and records why it was skipped along with its full URL, so nothing is silently dropped.

Media fetching runs separately from sync, so a long download never stalls collection. Every file is named after the post it came from, and `media/index.json` records its original URL, owning post and fetch time.

Archived captures hold the **raw responses** exactly as returned. After the parser improves, `replay` re-reads them without touching the network — which matters once a post has been deleted:

```sh
uv run bookmark-atlas replay            # report differences only
uv run bookmark-atlas replay --apply    # merge them back
uv run bookmark-atlas replay --since 2026-09-01
```

Replay writes only the `items` table. Memberships, origins, checkpoints and the captures themselves are left alone, so it is safe to repeat, and a response the current parser cannot read is counted rather than aborting the run.

## Incremental behavior

Each sync checks the newest bookmarks before resuming unfinished history. A newly bookmarked old post counts as new. Two entirely known pages form the normal overlap boundary. `--full` disables that early stop; `--max-pages` bounds a run. The default limit is 50 pages.

`partial` means the page budget was reached; run again to continue. `complete` means the current endpoint ended, not that deleted or inaccessible posts were recovered. `incremental` means the known overlap boundary was reached. Missing posts remain in the archive. Media metadata is saved during collection, images can be downloaded with `fetch-media`, and videos keep a frame plus their original address. Whole threads and linked-page bodies remain outside the current scope.

[定向作者与关注列表采集 / Directed authors and following lists](watch.md)
