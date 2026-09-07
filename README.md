<div align="center">

# Bookmark Atlas

### 让收藏不止于收藏，让知识开始复利。

**简体中文** · [English](README.en.md)

[快速开始](#快速开始) · [交给 Agent](#交给-agent) · [使用指南](docs/usage.zh-CN.md) · [下载](https://github.com/Moonawn/bookmark-atlas/releases)

[![CI](https://github.com/Moonawn/bookmark-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/Moonawn/bookmark-atlas/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

</div>

每天刷了很多推，真正留下的却很少。消息更新太快，没时间梳理；遇到好内容，又要复制链接、攒成一批，再交给 Agent。

**一次设置，照常收藏。** Bookmark Atlas 按你设定的节奏，把 X 收藏同步到本地，保留原文与来源，再按你选择的方式整理。

收藏只是起点。让值得留下的内容被总结、关联和反复回看，让今天存下的一个想法，成为明天能用上的知识。

> 更新频率由你定，整理方式由你选。知识复利，来自每一次有依据的整理、连接与使用。

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

Bookmark Atlas 提供可独立运行的 CLI，也附带 [Agent Skill](skills/bookmark-atlas/SKILL.md)。让 Agent 读取这个 Skill，即可引导设置、执行同步并整理 Wiki。

> 帮我设置 Bookmark Atlas。先问我同步时间、时区和整理方式；如果选择 LLM Wiki，就保留原文，把相关收藏提炼成有来源的知识笔记。

三种整理方式可以按需要选择：

- **原文摘录**：不需要模型，自动分类并提取项目链接。
- **本地模型**：使用已安装的 Ollama 模型生成摘要、要点和行动建议。
- **Agent + LLM Wiki**：由你选择的 Agent 阅读待整理来源，形成概念与方法笔记；内容变化后重新整理，保留来源与待核对事项。

Wiki 将原始资料、生成笔记和人工笔记分开保存。生成内容带来源，人工笔记不会被导出过程覆盖。知识库保存在你的本地；选择 Agent 时，资料会进入你所使用的 Agent 服务上下文。

[了解 Wiki 整理与 Skill 使用 →](docs/wiki.md)

## 你始终掌握自己的资料

SQLite 保存归档，Markdown 方便阅读，JSON 方便迁移。重复同步不重复添加；未出现在新一轮列表中的旧收藏仍会保留。模型整理失败时，原文和采集进度仍在，可以单独重试。

目前支持 **当前账号的 X 收藏**。关注动态、指定作者条件采集、其他站点，以及图片视频文件下载尚未接入。网页接口随 X 更新可能变化；更多环境与测试范围见 [兼容性说明](docs/verification.md)。

---

[使用指南](docs/usage.zh-CN.md) · [架构与扩展](docs/architecture.md) · [贡献](CONTRIBUTING.md) · [安全与隐私](SECURITY.md) · [MIT License](LICENSE)
