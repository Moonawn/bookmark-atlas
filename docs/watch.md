# 定向采集 / Directed collection

[中文主页](../README.md) · [English homepage](../README.en.md)

目前支持 X 的指定作者，以及登录账号的关注列表。所有规则都需要已授权的官方 API 或网页登录会话。只读取该账号可见的内容。

Collect from specified X authors or the signed-in account's following list using official API access or an authenticated web session.

## 设置规则 / Configure a rule

```sh
# 替换示例作者名；任意关键词命中即可 / Replace author names; keywords match with OR
bookmark-atlas watch add research --authors alice,bob --days 7 --keywords "Agent,Wiki" --kinds post

# 从自己的关注列表中筛选 / Filter your following list
bookmark-atlas watch add following-ai --following --days 1 --keywords "Agent,LLM" --max-authors 20

# 只采集关注列表中的指定作者（交集）/ Intersection with your following list
bookmark-atlas watch add selected --following --authors alice,bob --days 7 --language en

bookmark-atlas watch list
bookmark-atlas watch sync research
bookmark-atlas watch disable research
bookmark-atlas watch enable research
```

`watch add` 保存并启用规则；同名规则会被替换。下一次 `sync`、`serve` 或已有 Agent 定时任务就会执行启用的规则。`watch sync` 只运行作者采集；不指定名称时运行全部启用规则。停止某条规则用 `watch disable`，本地已有资料保留。

`watch add` saves and enables a rule, replacing an existing rule with the same name. Enabled rules run during `sync`, `serve` and existing Agent schedules. `watch sync` runs author collection only; omit the name to run all enabled rules. Disable a rule to stop future collection without deleting saved material.

| 参数 / Option | 行为 / Behavior |
|---|---|
| `--authors` | 逗号分隔 X 用户名 / Comma-separated X usernames |
| `--following` | 每轮刷新自己的关注名单；与 `--authors` 同用取交集 / Refresh following list; intersect with explicit authors |
| `--days` | 过去 N×24 小时，默认 7；不保证完整历史 / Rolling N×24-hour window, default 7; not a full-history guarantee |
| `--keywords` | 正文与展开链接中的字面匹配，任一词命中，不区分大小写 / Case-insensitive literal OR matching over text and expanded links |
| `--kinds` | `post` 原创、`quote` 引用，默认两者；暂不采集回复和转发 / Original or quote posts, both by default; replies and reposts are excluded |
| `--language` | X 标注的语言代码，如 `en`、`zh`；省略不限 / X-provided language code; omitted means any |
| `--max-authors` | 每条规则每轮最多作者数，默认 20；下一轮继续轮转 / Authors per rule per run, default 20; rotate across later runs |
| `--max-pages` | 每位作者每轮页数，默认 5；未结束保留进度 / Pages per author per run, default 5; incomplete progress is retained |

时间、类型、关键词和语言条件同时满足才入库。规则保存在私有目录的 `watchlists.json`。原始响应会在本地保留用于恢复和排查，可能包含未通过筛选的内容；筛选控制的是正常归档条目。

All filters must pass. Rules live in private `watchlists.json`. Private raw responses may contain unmatched posts for recovery and diagnostics; filters control normalized archive entries.

## 运行边界 / Run limits

- 收藏与每条规则分别记进度；同一推文跨规则出现时，正文只存一份。新增计数表示加入集合，不一定是全库新推文。
- 大名单按批轮转。`remaining_authors` 表示本次遍历尚未处理的作者；`partial` 表示作者时间线触及页数上限。短时间窗口配合很大的关注名单可能漏过尚未轮到作者的内容，应缩小范围或增加频率。
- X API 和网页仅返回当前可访问的时间线；置顶、删除、受限内容及服务返回上限都可能影响覆盖范围。不是全量镜像。
- API 关注名单需要 `follows.read`，旧授权需要重新登录授权，并满足账号额度。网页适配器跟随 X 接口变化维护。
- 自动入口切换发生在身份和作者名单读取阶段；时间线失败会停止并保留已提交进度。限流不会通过切换入口绕过。

Bookmarks and each rule keep separate progress. A shared post has one document; added counts describe collection membership, not necessarily a new post globally. Large lists rotate across runs, so a short lookback window can miss posts before their author is visited. Narrow the roster or run more frequently when this matters.

The API and web routes expose accessible timelines, not a complete mirror. Following-list API access needs `follows.read`, refreshed user authorization when upgrading old scopes, and usable credits. Auto selection can fall back while resolving identity and author rosters; timeline failures stop the run with committed progress preserved. Rate limits are never bypassed by switching routes.

## 升级 / Upgrade

v0.3 会自动将 SQLite 升级到 schema v2。升级前停止同步并备份私有资料目录；回退到 v0.2 需要恢复升级前的备份。不要把私有归档提交进代码仓库。

Version 0.3 migrates SQLite to schema v2. Stop sync and back up your private data directory before upgrading. Returning to v0.2 requires the pre-upgrade backup.
