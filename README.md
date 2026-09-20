<div align="center">

# Bookmark Atlas

### 让收藏不再止于收藏。

**简体中文** · [English](README.en.md)

[快速开始](#快速开始) · [交给 Agent](#交给-agent) · [使用指南](docs/usage.zh-CN.md) · [下载](https://github.com/Moonawn/bookmark-atlas/releases) · [安装 Skill](#交给-agent)

[![CI](https://github.com/Moonawn/bookmark-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/Moonawn/bookmark-atlas/actions/workflows/ci.yml) [![许可证：MIT](https://img.shields.io/badge/License-MIT-blue.svg)](许可证)

</div>

**支持 X 收藏、指定作者和关注列表；新增公众号文章收录与 RSS 条件订阅。其他平台开发中。**

公众号支持文章链接、RSS 订阅，也可复用本地 WeRSS 中的微信读书授权（每号最新一篇），详见[公众号使用指南](docs/wechat.zh-CN.md)。

每天刷过很多内容，想用时却找不到；收藏夹越来越满，整理总留给以后。

**一次设置，收藏不止。** Bookmark Atlas 按你的节奏，把值得留下的内容存到本地。无需反复复制链接：保留原文、整理主题，也可以交给 Agent，逐步形成有来源、有关联的知识笔记。

今天留下的内容，下次遇到问题时能找回、能对照、能接着用。

![书签图谱：月亮、书页与本地知识地图](assets/hero.png)

## 从一个收藏开始

| 你做的事 | Atlas 接着做 |
| :--- | :--- |
| 读到值得留下的内容，点击收藏 | 同步新增，按 ID 去重，保留原文、作者和来源链接 |
| 选好每天几点，或多久同步一次 | 按计划运行；中断后从已保存的进度继续 |
| 选择主题归档或 LLM Wiki | 生成可检索的资料库；由 Agent 提炼知识，或用本地模型生成摘要 |
| 想起一个主题、一位作者或一段话 | 在本地检索，打开笔记，对照原文继续思考 |

## 快速开始

需要 **macOS / Linux、Python 3.11+ 和 uv**。使用网页入口时，先在 Chrome 登录 X，并确认可以打开自己的收藏页。

```sh
git clone https://github.com/Moonawn/bookmark-atlas.git
cd bookmark-atlas
uv sync --extra browser
uv run bookmark-atlas setup
uv run bookmark-atlas sync
```

首次设置会问你：**从哪里采集、何时同步、如何整理、由谁总结。** 后续直接运行 `sync`，无需重复输入配置。首次历史归档可能分多轮完成。

```sh
uv run bookmark-atlas search "智能体"
uv run bookmark-atlas export markdown

# 把图片收到本地；视频默认只存首帧截图和原始地址
uv run bookmark-atlas fetch-media
```

资料默认保存在 `~/.local/share/bookmark-atlas/`，打开 `reports/index.md` 即可阅读。使用 `--home` 或 `ATLAS_HOME` 自定义目录。

<details>
<summary><strong>开启定时同步</strong></summary>

```sh
# 沿用首次设置保存的每日时间或间隔
uv run bookmark-atlas serve

# 或直接指定每日时间
uv run bookmark-atlas serve --at 21:00 --timezone Asia/Shanghai
```

`serve` 在当前终端运行，关闭进程后停止。每日模式等待指定时间；间隔模式启动时先同步一轮。选择 Agent 整理时，需要 Agent 的定时任务继续处理 Wiki；可让 Agent 按下面的 Skill 配置。不要同时运行两套重复调度。

</details>

<details>
<summary><strong>改用官方 API</strong></summary>

支持官方 OAuth API 与网页登录会话两种入口，也支持自动选择。API 需要你自己的 X Developer App、用户授权和可用额度。

```sh
uv run bookmark-atlas auth login --client-id YOUR_CLIENT_ID
uv run bookmark-atlas sync --mode api
```

[授权、浏览器配置与代理说明 →](docs/usage.zh-CN.md)

</details>

## 交给 Agent

[**下载 Skill 安装包（v0.5.0）**](https://github.com/Moonawn/bookmark-atlas/releases/download/v0.5.0/bookmark-atlas-skill-0.5.0.zip) · [查看 Skill](skills/bookmark-atlas/SKILL.md) · [安装说明](docs/wiki.md#使用-skill--use-the-skill)

解压后，将整个 `bookmark-atlas/` 文件夹放入所用 Agent 支持的 Skills 目录；也可以让 Agent 直接读取仓库中的 Skill。先按上面的步骤安装 CLI。


Bookmark Atlas 提供可独立运行的 CLI，也附带 [Agent Skill](skills/bookmark-atlas/SKILL.md)。让 Agent 读取这个 Skill，即可引导设置、执行同步并整理 Wiki。

> 帮我设置 Bookmark Atlas。先问我同步时间、时区和整理方式；如果选择 LLM Wiki，就保留原文，把相关收藏提炼成有来源的知识笔记。

三种整理方式可以按需要选择：

- **原文摘录**：不需要模型，自动分类并提取项目链接。
- **本地模型**：使用已安装的 Ollama 模型生成摘要、要点和行动建议。
- **Agent + LLM Wiki**：由你选择的 Agent 阅读待整理来源，形成概念与方法笔记；内容变化后重新整理，保留来源与待核对事项。

Wiki 将原始资料、生成笔记和人工笔记分开保存。生成内容带来源，人工笔记不会被导出过程覆盖。知识库保存在你的本地；选择 Agent 时，资料会进入你所使用的 Agent 服务上下文。

Wiki 工作流受 [Andrej Karpathy 的 LLM Wiki 模式](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)启发：保留来源，持续整理、连接和修订。分类采用可配置的“主题＋笔记类型”，无法确定的内容先放入待分类。

[了解 Wiki 整理与 Skill 使用 →](docs/wiki.md)

## 持续关注作者

收藏是手动选择，定向采集适合持续跟进。指定作者，或从自己的关注列表中选择范围，再按时间、关键词、语言与原创／引用类型筛选。

```sh
# 示例：替换为你要跟进的作者
uv run bookmark-atlas watch add research --authors example_author --days 7 --keywords "Agent,Wiki" --kinds post
uv run bookmark-atlas watch sync research
```

启用的规则会随后续 `sync` 和已有定时任务执行。收藏与各条规则分别记录采集进度，同一篇内容只存一份。关注人数较多时分批轮转，每轮是否完成会在结果中显示。

[关注列表、筛选规则与暂停方法 →](docs/watch.md)

## 你始终掌握自己的资料

SQLite 保存归档，Markdown 方便阅读，JSON 方便迁移。重复同步不重复添加；未出现在新一轮列表中的旧收藏仍会保留。模型整理失败时，原文和采集进度仍在，可以单独重试。

图片可由 `fetch-media` 下载到本地，视频默认只保留首帧截图和原始地址，两者都记下来源。响应中包含的 X 长文与引用推文可一并保存；完整讨论串和任意外链正文暂不采集。网页接口随 X 更新可能变化；更多环境与测试范围见 [兼容性说明](docs/verification.md)。

---

[使用指南](docs/usage.zh-CN.md) · [架构与扩展](docs/architecture.md) · [贡献](CONTRIBUTING.md) · [安全与隐私](SECURITY.md) · [MIT License](LICENSE)
