# Bookmark Atlas

**把收藏变成可以持续积累、检索和整理的本地资料库。**

Bookmark Atlas 支持 X Bookmarks，提供 **官方 OAuth API** 和 **网页登录会话** 两个入口；后续通过站点适配器扩展其他平台。

[![CI](https://github.com/Moonawn/bookmark-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/Moonawn/bookmark-atlas/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## 已有能力

- **双入口**：`api`、`web`、`auto`，同一账号统一归档。
- **增量保存**：按站点和推文 ID 去重，保留首次发现时间；今天收藏的旧推文也能识别为新增。
- **断点续传**：每页原始响应、数据和游标一起提交；支持历史补抓、全量复查和页数上限。
- **原文保留**：文本、作者、链接、媒体元数据；不会因为一次没抓到就删除历史收藏。
- **本地整理**：规则摘录、中文主题分类、GitHub 项目链接提取、关联主题索引。
- **可选模型分析**：连接本机 Ollama，生成中文摘要、要点和行动建议；失败独立重试。
- **增量分析**：相同内容不重复分析；内容变化后更新结果。
- **导出和检索**：JSON、Markdown、中文子串搜索；代码和私人数据分开存放。
- **手动或周期运行**：单次 CLI、常驻间隔调度、供系统调度使用的单轮模式。

## 安装

支持 **macOS / Linux，Python 3.11+**。v0.1 使用 POSIX 文件锁，暂不支持原生 Windows。

```sh
git clone https://github.com/Moonawn/bookmark-atlas.git
cd bookmark-atlas
uv sync --extra browser
uv run bookmark-atlas --version
```

也可以从 [GitHub Releases](https://github.com/Moonawn/bookmark-atlas/releases) 下载 wheel 后通过 `pip` 安装。

## 使用 Chrome 登录态

在 Chrome 中登录自己的 X 账号，并确认能打开收藏页。Cookie 仅用于访问 X，不写入归档、不上传 GitHub。

```sh
# 验证当前账号，不抓取收藏
uv run bookmark-atlas auth check --mode web --browser chrome

# 首次小批量验证：最多两页，并自动整理
uv run bookmark-atlas sync --mode web --browser chrome --max-pages 2

# 后续同步自动检查新增，再继续未完成的历史抓取
uv run bookmark-atlas sync --mode web --browser chrome
```

支持 Chrome、Firefox、Brave、Edge、Chromium；Quark 入口仅提供 macOS Cookie 读取适配，尚未完成真实验证。多浏览器配置用 `--cookie-file /path/to/Cookies` 指定。macOS 可能需要允许系统密钥访问，具体取决于浏览器和系统设置。

程序会读取已有系统 HTTPS 代理或标准代理环境变量；也可设置 `ATLAS_PROXY`。不会修改系统代理。

## 使用官方 API

官方 API 需要自己的 X Developer App 和可用额度。网页登录成功并不等于拥有 API 授权。接口说明见 [X 官方 Bookmarks 文档](https://docs.x.com/x-api/posts/bookmarks/quickstart/bookmarks-lookup) 和 [OAuth PKCE 文档](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)。

在 Developer App 配置回调 URL `http://127.0.0.1:8765/callback`，然后：

```sh
uv run bookmark-atlas auth login --client-id YOUR_CLIENT_ID
uv run bookmark-atlas auth check --mode api
uv run bookmark-atlas sync --mode api --max-pages 1
```

`auth login` 显示授权链接，等待本机回调，申请 `tweet.read users.read bookmark.read offline.access`，并保存可刷新的 OAuth Token。对于 confidential client，用环境变量 `ATLAS_X_CLIENT_SECRET` 提供 secret。不要把 secret 放进命令参数或仓库。

已有 OAuth 用户 Access Token 时，可通过 `ATLAS_X_ACCESS_TOKEN` 注入。该环境变量方式不自动刷新；需要自动刷新时使用 `auth login`。应用级 Bearer Token 不能代替用户授权。

```sh
# 默认优先 API，未配置或入口不可用时在写入前切换 web
uv run bookmark-atlas sync --mode auto

# 也可以优先网页入口
uv run bookmark-atlas sync --mode auto --prefer web
```

两个入口必须属于同一账号。遇到限流时按站点保存冷却时间，不切换入口绕过限制。遇到解析变化、账号不一致或已经开始写入后的失败，停止并保留数据和续传点。

## 本地整理与分析

默认不需要模型，也不会将收藏发给外部服务：使用规则做原文摘录、主题归类和项目链接提取。**规则摘录不等同于生成式中文摘要或事实核查。**

如果已经运行本地 Ollama 并安装了合适的模型：

```sh
uv run bookmark-atlas analyze --ollama-model YOUR_LOCAL_MODEL --limit 100
uv run bookmark-atlas export markdown --ollama-model YOUR_LOCAL_MODEL

# 抓取后直接调用本地模型
uv run bookmark-atlas sync --mode web --ollama-model YOUR_LOCAL_MODEL
```

仅连接本机 `127.0.0.1:11434`；不自动下载模型。模型摘要保留原文链接，仍需对照原文核实。失败记录可再次运行 `analyze` 重试，不需要重抓。

## 查看、导出和定时

```sh
uv run bookmark-atlas status
uv run bookmark-atlas search "智能体"
uv run bookmark-atlas export markdown
uv run bookmark-atlas export json --out ~/Documents/x-bookmarks.json

# 默认每 6 小时同步一次，启动时先执行一轮
uv run bookmark-atlas serve --mode auto --interval 21600

# 单轮调度测试；可以由你已有的系统任务调度器调用
uv run bookmark-atlas serve --mode web --once

# 定期深度复查：不按重复页提前结束，保留旧归档
uv run bookmark-atlas sync --mode web --full --max-pages 100
```

`serve` 是前台进程，关闭终端或停止进程会停止调度；安装程序不修改系统启动项。无新增、无更新且无分析失败时，周期模式保持安静。锁定、认证失败等错误写入 stderr。

默认每轮上限 50 页。`partial` 表示达到页数上限，历史未抓完；下次继续。`complete` 表示本次遍历到当前接口末尾，`incremental` 表示已覆盖重复页边界；这不保证 X 能提供所有已删除或不可访问的历史内容。

默认目录：

```text
~/.local/share/bookmark-atlas/
├── atlas.sqlite3        # 收藏、原始响应、来源、进度、分析和运行记录
├── credentials.json    # 仅官方 OAuth 登录后出现，权限 0600
├── reports/
│   ├── index.md         # 按主题连接相关收藏
│   ├── recent.md        # 按首次发现时间排列
│   └── items/           # 每条收藏的原文与分析
└── exports/
```

用全局 `--home`（放在子命令前）或 `ATLAS_HOME` 更改目录。不同 X 账号使用独立目录。

## 测试与当前边界

自动化测试使用合成数据与 Mock HTTP，覆盖官方 API、OAuth 刷新、网页登录解析、分页、重复与变更、事务回滚、历史续传、限流、账号隔离、分析重试和安全导出。

```sh
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv build
```

支持范围和测试覆盖见 [兼容性说明](docs/verification.md)。网页接口会随 X 更新变化；遇到结构变化会报错，避免将错误响应当作空收藏。首版保存媒体元数据，不下载图片/视频二进制，不展开完整线程或外链正文；暂不支持收藏夹结构同步和其他站点。

[架构与站点扩展](docs/architecture.md) · [贡献指南](CONTRIBUTING.md) · [安全与隐私](SECURITY.md) · [MIT License](LICENSE)
