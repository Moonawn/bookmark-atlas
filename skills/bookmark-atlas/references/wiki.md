# Agent Wiki batch protocol

`export wiki` writes the private queue to `<home>/wiki/queue.json`. Each source includes an exact `key` and current content `hash`, original text, author, URL, links and local source file. The queue contains sources without a complete, current compilation receipt. Read the text and existing notes before composing a batch.

```json
{
  "notes": [
    {
      "id": "stable-concept-slug",
      "title": "A specific concept or method",
      "body": "Explain the core idea in your own words.\n\nConnect evidence, application and limitations. Distinguish claims from observations.",
      "topics": ["ai-agents"],
      "type": "method",
      "related": [],
      "sources": [
        {"key": "EXACT_KEY_FROM_QUEUE", "hash": "EXACT_HASH_FROM_QUEUE"}
      ]
    }
  ]
}
```

- `id`: 1–80 lowercase letters, digits or hyphens; begins with a letter or digit. Preserve an existing concept ID when extending it.
- `body`: plain text with paragraph breaks. The writer escapes Markdown/HTML from both source and generated content. Source links are rendered separately from validated references.
- `topics`: one or more IDs from private `taxonomy.json`; default `inbox` for new notes.
- `type`: a note type from that file; default `insight` for new notes.
- `related`: existing or same-batch note IDs; self-links and missing targets are rejected.
- Omitted classification fields preserve existing note metadata on updates.
- `sources`: nonempty exact source references. A concept update must retain all prior sources. When a changed source is used by multiple notes, update all affected notes in the same batch.
- Do not insert invented source IDs, mark unread sources as compiled, or edit the manifest directly.

Save the batch privately and run:

```sh
bookmark-atlas --home /your/private/archive wiki apply /your/private/batch.json
```

Validation covers the complete batch before writing notes. Source hashes must match the current database. Each note is written atomically; the manifest advances after all notes are saved. Reapply after an interrupted write. Stale-source errors require regenerating the queue and revising the batch. These checks verify provenance and write consistency, not factual accuracy.

The private layout is:

```text
wiki/
  taxonomy.json     # editable topic and note-type dictionary
  log.md            # compilation history
  index.md          # generated reading index
  sources/          # generated original-source pages and topic index
  notes/            # source-linked notes plus .meta.json classifications
  personal/         # user writing; exports leave it untouched
  queue.json        # pending Agent work
  manifest.json     # source hashes and generated-note references
  last-compile.json # latest successful batch receipt
```
