# WeChat articles and subscriptions

Archive selected WeChat accounts through an existing RSS discovery service, or import individual article links. Atlas saves original article HTML, text, metadata and image references, then prepares its ordinary source-linked Wiki queue.

## Set up

Log in to your existing [WeRSS service](https://github.com/rachelos/we-mp-rss), complete the required WeChat authorization and add your chosen accounts. Atlas does not configure the service's login or copy its credentials.

```sh
bookmark-atlas wechat add my-reading --label 'Account name' --biz 'account-__biz' --seed 'https://mp.weixin.qq.com/s/article-id'
bookmark-atlas wechat connect --rss-base http://127.0.0.1:8001
bookmark-atlas wechat list
bookmark-atlas wechat sync my-reading --limit 20
```

Get the stable `__biz` from an account's long article URL. The catalogue matches account labels; every article is then checked against `__biz`. Ambiguous names need an explicit feed:

```sh
bookmark-atlas wechat add my-reading --feed-url 'http://127.0.0.1:8001/rss/feed-id'
bookmark-atlas wechat add my-reading --keywords 'Agent,Harness' --exclude 'advertisement' --since 2026-09-01
bookmark-atlas wechat import 'https://mp.weixin.qq.com/s/article-id'
bookmark-atlas sync --site wechat
bookmark-atlas wechat disable my-reading
```

RSS / Atom entries must link to original WeChat articles. Include keywords match the title and body with OR semantics; excludes take priority. `--since` uses UTC publication dates. Changing filters reconsiders seen links without creating duplicate articles.

Default `sync` includes X and enabled WeChat rules. `--site x` and `--site wechat` restrict collection. Reuse an existing scheduler. The RSS service must update its own feed; starting Atlas alone does not authorize or start upstream discovery. A rule without a working feed reports `needs_setup`, even if its seed article was saved.

## Reliability and limits

Articles deduplicate by `__biz + mid + idx`. Failed URLs survive RSS rollover in a persistent queue. Successful URLs are not downloaded every run; use `wechat import` to check a specific article for edits. A real body change, including a shorter revision, refreshes the Wiki source hash. Saved `media: images` also downloads article images with the proper Referer and original URL parameters.

One run processes at most 20 articles by default. WeRSS feeds are read for up to 10 pages; increase `--max-pages` when needed. `coverage_limited` means the page bound was reached. Ordinary RSS only exposes the entries currently supplied by the source and does not guarantee a complete history.

Verification challenges stop further WeChat requests for that run. Login expiry, inaccessible paid content, deleted articles, missing bodies, dynamic-only articles and media-only posts remain unresolved. Embedded audio/video is not transcribed. RSS descriptions are never treated as complete articles.

Review newly captured sources before compiling Wiki notes. Distinguish author claims from verified evidence, reuse existing topics and related concepts, and preserve unresolved questions. Saving an article is not the same as compiling it into knowledge.
