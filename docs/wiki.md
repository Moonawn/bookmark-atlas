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

让你使用的 Agent 读取仓库中的 `skills/bookmark-atlas/SKILL.md`。若 Agent 支持文件夹式 Skill 安装，将整个 `skills/bookmark-atlas/` 文件夹复制到该 Agent 的 Skill 目录。CLI 需要先安装；Skill 不会自带另一个运行环境。

Ask your Agent to read `skills/bookmark-atlas/SKILL.md` in this repository. For hosts supporting folder-based Skills, copy the entire `skills/bookmark-atlas/` directory into the host's Skills directory. Install the CLI first; the Skill does not bundle a separate runtime.

Agent 应先问清同步时间、时区、整理方式和模型选择。已有偏好可以复用。选择 Agent 意味着所选内容进入该 Agent 服务的上下文；选择 Ollama 使用本机模型。两者都需要各自的运行环境。

The Agent asks for schedule, timezone, organization and model preferences, reusing existing choices. Agent mode sends selected source material into that Agent service's context; Ollama uses a local model. Each requires its own running environment.

## 定时 / Scheduling

`setup` 保存偏好，`serve` 是前台调度。Agent 总结由 Agent 宿主的定时功能运行完整 Skill 流程。请只选一套调度；本机离线、会话过期或 Agent 服务不可用时需要恢复运行环境。

`setup` saves preferences, while `serve` provides foreground scheduling. For Agent summaries, use the Agent host's automation to run the full Skill workflow. Use one scheduler. Local execution depends on the host, authenticated session and Agent service being available.

[Agent 批次格式与目录约定 / Batch protocol and file layout →](../skills/bookmark-atlas/references/wiki.md)
