# Wiki workflow / Wiki 整理

[简体中文](../README.md) · [English](../README.en.md)

Bookmark Atlas 提供采集与存储引擎，也提供 [Agent Skill](../skills/bookmark-atlas/SKILL.md)。CLI 可以独立运行；Skill 让 Agent 按用户偏好完成配置、调度和知识整理。

Bookmark Atlas includes both a standalone collection engine and an Agent Skill. The CLI archives sources; the Skill guides setup, scheduling and knowledge compilation.

## 整理方法 / How it works

原始资料进入来源层。Agent 阅读新增或变更来源，结合已有笔记，提炼具体概念、方法或项目观察，保留证据、适用条件和待核对的问题。相同内容不会反复进入队列；变化后的来源需要重新编译。仅做摘录与分类不等于完成 LLM Wiki 编译。

The source layer preserves original material. The Agent reads new or changed sources, relates them to existing notes, and writes focused concepts, methods or project observations with evidence and open questions. Unchanged compiled sources leave the queue; changed sources require a new pass. Rule-based excerpts alone are not Wiki synthesis.

```sh
bookmark-atlas setup
bookmark-atlas sync
bookmark-atlas export wiki
```

## 使用 Skill / Use the Skill

[下载 Skill 安装包 / Download Skill v0.4.0](https://github.com/Moonawn/bookmark-atlas/releases/download/v0.4.0/bookmark-atlas-skill-0.4.0.zip)

让你使用的 Agent 读取仓库中的 `skills/bookmark-atlas/SKILL.md`。若 Agent 支持文件夹式 Skill 安装，将整个 `skills/bookmark-atlas/` 文件夹复制到该 Agent 的 Skill 目录。CLI 需要先安装；Skill 不会自带另一个运行环境。

Ask your Agent to read `skills/bookmark-atlas/SKILL.md` in this repository. For hosts supporting folder-based Skills, copy the entire `skills/bookmark-atlas/` directory into the host's Skills directory. Install the CLI first; the Skill does not bundle a separate runtime.

Agent 应先问清同步时间、时区、整理方式和模型选择。已有偏好可以复用。选择 Agent 意味着所选内容进入该 Agent 服务的上下文；选择 Ollama 使用本机模型。两者都需要各自的运行环境。

The Agent asks for schedule, timezone, organization and model preferences, reusing existing choices. Agent mode sends selected source material into that Agent service's context; Ollama uses a local model. Each requires its own running environment.

## 定时 / Scheduling

`setup` 保存偏好，`serve` 是前台调度。Agent 总结由 Agent 宿主的定时功能运行完整 Skill 流程。请只选一套调度；本机离线、会话过期或 Agent 服务不可用时需要恢复运行环境。

`setup` saves preferences, while `serve` provides foreground scheduling. For Agent summaries, use the Agent host's automation to run the full Skill workflow. Use one scheduler. Local execution depends on the host, authenticated session and Agent service being available.

[Agent 批次格式与目录约定 / Batch protocol and file layout →](../skills/bookmark-atlas/references/wiki.md)

## 方法来源 / Inspiration

Wiki 工作流受 [Andrej Karpathy 的 LLM Wiki 模式](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)启发：原始来源保持可追溯，LLM 持续整理、关联和修订知识。Bookmark Atlas 中的队列、校验、存储和导出由项目自身实现；没有依赖一个名为 LLM Wiki 的第三方运行包。上述链接说明方法来源。

The workflow is inspired by [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). Bookmark Atlas implements its queue, validation, storage and export pipeline; LLM Wiki is a workflow pattern here, not an installed third-party runtime package.

## 分类逻辑 / Classification

分类采用两个维度，不按每条推文各建一个知识目录：

| 维度 / Dimension | 默认内容 / Defaults |
|---|---|
| 主题 / Topic | AI 与 Agent、开发与开源、研究与论文、产品与商业、设计与创作、学习与方法、待分类 |
| 笔记类型 / Note type | 概念、方法、项目、人物与组织、观察与判断、资料索引 |

Agent 先读取 `wiki/taxonomy.json`，优先扩充已有概念。一篇笔记可以属于多个主题，但只保留一份正文；相关笔记通过 `related` 互链。证据不足时进入待分类，明确待核对的问题。来源与作者保留在来源层，不能代替知识分类。

修改 `taxonomy.json` 可增添主题或改显示名。保留 `inbox` 和 `insight` 默认项；删除已被使用的分类前，需要先迁移对应的 `notes/*.meta.json`。正文更新未提供分类字段时会保留原分类。`index.md` 按主题索引，`log.md` 记录知识更新，人工写作放在 `personal/`。

Classification has two axes: topic and note type. Read private `taxonomy.json` before compiling and prefer extending existing concepts. A note can appear under several topics while keeping one body. `related` links validated note IDs. Keep uncertainty explicit and use the inbox when classification is unclear.

Add categories or change display labels in `taxonomy.json`; preserve the default `inbox` and `insight` keys. Migrate note metadata before removing a category in use. Content-only updates preserve existing classifications. The topic index and update log are generated; personal writing belongs in `personal/`.
