<div align="center">

# Bookmark Atlas

### Let collection be more than mere saving.

[简体中文](README.md) · **English**

[Quick start](#quick-start) · [Use with an Agent](#use-with-an-agent) · [User guide](docs/usage.en.md) · [Download](https://github.com/Moonawn/bookmark-atlas/releases) · [Install Skill](#use-with-an-agent)

[![CI](https://github.com/Moonawn/bookmark-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/Moonawn/bookmark-atlas/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

</div>

**The current stable release is v0.4.0. WeChat integration remains experimental and is not included in the stable feature set.**

**Currently supports X: bookmarks and conditional collection from selected authors or your following list. Other platforms are in development.**

You read plenty of posts, then struggle to find the useful ones when you need them. Your bookmarks grow; organizing them stays on the to-do list.

**Set once, save beyond.** Bookmark Atlas saves useful material to your computer on your schedule. Preserve the original posts, organize topics, or ask your Agent to build connected notes with sources—without repeatedly copying batches of links.

Find what you saved, check its context, and use it to work through your next question.

![Bookmark Atlas: moon, pages and a local knowledge map](assets/hero.png)

## Start with a bookmark

| What you do | What Atlas does next |
| :--- | :--- |
| Bookmark something worth keeping | Sync new items, deduplicate by ID, and preserve text, authors and sources |
| Choose a daily time or an interval | Run on schedule and resume from saved progress after interruptions |
| Choose a topic archive or LLM Wiki | Prepare searchable material; let an Agent synthesize knowledge or a local model summarize it |
| Recall a topic, an author or a phrase | Search locally, open your notes and return to the source |

## Quick start

Requires **macOS / Linux, Python 3.11+ and uv**. For browser access, sign in to X in Chrome and check that your bookmarks page opens.

```sh
git clone https://github.com/Moonawn/bookmark-atlas.git
cd bookmark-atlas
uv sync --extra browser
uv run bookmark-atlas setup
uv run bookmark-atlas sync
```

Setup asks about **access, schedule, organization and summarization**. The current interactive prompts are in Chinese; option values are shown in English. Later runs reuse your choices. The initial historical archive may take several runs.

```sh
uv run bookmark-atlas search "agents"
uv run bookmark-atlas export markdown
```

Data lives in `~/.local/share/bookmark-atlas/` by default. Open `reports/index.md` to read it, or choose a directory with `--home` or `ATLAS_HOME`.

<details>
<summary><strong>Run on a schedule</strong></summary>

```sh
# Use your saved daily time or interval
uv run bookmark-atlas serve

# Or choose a daily time explicitly
uv run bookmark-atlas serve --at 21:00 --timezone Asia/Shanghai
```

`serve` runs in the foreground and stops when the process closes. Daily mode waits for the scheduled time; interval mode runs once immediately. Agent summaries need an Agent automation to continue the Wiki workflow. Ask your Agent to configure this using the Skill below. Avoid duplicate schedulers.

</details>

<details>
<summary><strong>Use the official API</strong></summary>

Choose official OAuth API access, an authenticated browser session, or automatic selection. API access requires your own X Developer App, user authorization and available credits.

```sh
uv run bookmark-atlas auth login --client-id YOUR_CLIENT_ID
uv run bookmark-atlas sync --mode api
```

[Authentication, browsers and proxy configuration →](docs/usage.en.md)

</details>

## Use with an Agent

[**Download the Skill (v0.4.0)**](https://github.com/Moonawn/bookmark-atlas/releases/download/v0.4.0/bookmark-atlas-skill-0.4.0.zip) · [Read the Skill](skills/bookmark-atlas/SKILL.md) · [Installation](docs/wiki.md#使用-skill--use-the-skill)

Extract the archive and copy the entire `bookmark-atlas/` folder into your Agent host's supported Skills directory, or ask your Agent to read the repository Skill directly. Install the CLI first using the steps above.


Bookmark Atlas runs as a CLI and includes an [Agent Skill](skills/bookmark-atlas/SKILL.md). Ask your Agent to read it to guide setup, sync your bookmarks and compile the Wiki.

> Set up Bookmark Atlas for me. Ask about my schedule, timezone and organization preferences first. If I choose LLM Wiki, preserve the original posts and turn related sources into traceable knowledge notes.

Choose how much processing you want:

- **Excerpts**: no model needed; classify posts and extract project links.
- **Local model**: use an installed Ollama model for summaries, key points and suggested actions.
- **Agent + LLM Wiki**: your selected Agent reads pending sources and develops concept or method notes, preserving citations and open questions. Changed sources return to the queue.

The Wiki separates original sources, generated notes and personal writing. Generated notes cite their sources; exports preserve your personal notes. Files stay on your computer. If you select an Agent, source material enters that Agent service's context.

The Wiki workflow is inspired by [Andrej Karpathy’s LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): preserve sources, then synthesize, connect and revise. Configurable topics and note types organize the Wiki; uncertain classifications go to the inbox.

[Wiki workflow and Skill usage →](docs/wiki.md)

## Follow authors over time

Choose authors or a subset of your following list, then filter by age, keywords, language and original/quote posts.

```sh
# Replace the example author with someone you want to follow
uv run bookmark-atlas watch add research --authors example_author --days 7 --keywords "Agent,Wiki" --kinds post
uv run bookmark-atlas watch sync research
```

Enabled rules run during future `sync` calls and existing schedules. Bookmarks and rules keep separate progress, while each post is stored once. Large author lists rotate through bounded batches, with completion information in each result.

[Following lists, filters and pausing rules →](docs/watch.md)

## Keep control of your archive

SQLite stores the archive, Markdown makes it readable, and JSON makes it portable. Repeated syncs do not duplicate unchanged items. Previously saved bookmarks remain even when missing from a later response. If analysis fails, the original material and sync progress remain available for a separate retry.

Images can be downloaded locally with `fetch-media`; videos keep a first frame and their original address, and both record their source. Embedded X Articles and quoted posts are captured when returned by X. Complete threads and arbitrary external page bodies remain outside the current scope. X website changes can affect the web adapter. See [compatibility and test coverage](docs/verification.md).

---

[User guide](docs/usage.en.md) · [Architecture](docs/architecture.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [MIT License](LICENSE)
