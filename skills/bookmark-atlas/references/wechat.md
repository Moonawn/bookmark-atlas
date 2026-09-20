# WeChat intake

Read `wechat list` before adding subscriptions. Resolve the selected accounts from the user's stated preferences or a maintained watchlist; do not turn every previously shared author into a permanent subscription. Use `__biz` as the account identity and `label` only for display / matching an existing WeRSS catalogue.

Commands:

- `wechat add <name> --biz <biz> --label <account> --seed <article-url>` registers an account.
- `wechat connect --weread-container <container>` reuses existing WeRead authorization inside a local WeRSS container. Credentials stay in that container. Requires its existing WeRead support, saved `weread_data`, and Python runtime; see the WeChat guide. Latest article only, not historical discovery; a known short `--seed` can also be collected.
- `wechat connect --rss-base <existing-service-url>` connects an already installed WeRSS catalogue. User login and WeChat authorization happen in that service's own UI. Do not ask for passwords, cookies or tokens in chat.
- `wechat add <name> --feed-url <rss-url>` sets the feed directly. RSS / Atom must contain original `mp.weixin.qq.com/s` article URLs.
- `wechat add <name> --keywords <comma-separated> --exclude <comma-separated> --since YYYY-MM-DD` changes filters; the date uses UTC.
- `wechat sync [name] --limit 20 --max-pages 10` captures selected subscriptions and prepares exports.
- `wechat import <url>` collects or refreshes one article.
- `wechat disable <name>` / `enable <name>` preserves history.

Default `sync` includes configured WeChat subscriptions; `--site x` and `--site wechat` explicitly restrict scope. Reuse the existing daily Agent task. A saved RSS endpoint is not proof of working authorization or new-article discovery. Verify nonempty real entries and a successful account-checked article fetch before reporting it active. `needs_setup`, `verification_required`, `failed` and `coverage_limited` are material status signals. Empty queues must not silence these failures.

The bridge reports `coverage: latest-only`: multiple publications between polls can be missed. Explicit RSS rules take precedence. Do not claim complete history, automatically change the schedule, or mark an empty service as active.

WeRSS discovers links, Atlas saves the original HTML and prepares the ordinary Wiki queue. Do not treat RSS descriptions as complete articles. Missing body, deletion, dynamic-only body, inaccessible paid content and environmental verification stay unresolved. A verification response stops all further WeChat requests in that run; preserve pending URLs and hand off the required login / verification. Never mark such sources compiled merely to empty the queue.

For `sync`, inspect X, watch and WeChat results separately. A partial failure can still leave newly captured material ready for Wiki compilation. Media failures also do not block usable text. Do not duplicate a media pass already performed by sync.

Use the same Wiki batch protocol and saved taxonomy as other Atlas sources. The raw article is evidence of what its author wrote, not independent proof of its claims. Reuse existing concepts, verify cited primary sources when needed, and report what remains uncertain. Preserve original author, publication date, source URL and exact source hash.
