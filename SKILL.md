---
name: plugin-template
description: Reference plugin template - saves, lists and outlines short notes while exercising every host extension point once.
---

## Description

A minimal notes feature that exists so every plugin extension point has something
real to do. Copy this plugin as the starting point for a new one; the feature
itself is throwaway.

The host registers this file automatically when it finds `SKILL.md` (or the
legacy `skill.md`) at the plugin root. There is no `register_skills` function,
and a disabled plugin's skill is not registered at all.

## Capabilities

- Commands: `/template-note`, `/template-notes`, `/template-format`, `/template-status`, `/template-llm`, `/template-outline`
- Hook-only command: `/template-ping` answers from a `before_route` hook without reaching the agent
- Tools: `template_note_tool` (decorated class, writes), `template_notes_tool` (decorated class, read-only), `template_format_tool` (decorated function, offline), `template_status_tool` and `template_llm_tool` (explicitly registered)
- SubAgents: `template_note_curator` (decorated, plans an outline), `template_note_reviewer` (explicitly registered, usable as a `vote` node voter)
- Hooks: all ten lifecycle points, one handler each
- Web: a dashboard page under `pages/console/`, a declarative panel, and a dozen endpoints under this plugin's own namespace, including attachment upload, listing, download and deletion
- Storage: the host's per-plugin storage service, with a JSON-file fallback when Redis is down

## Usage Hints

- `/template-note <text>` saves a note. The first line becomes the title.
- `/template-notes` lists the current user's notes; notes are never shared between users.
- `/template-format <text>` splits free text into title, body and tag without writing anything. It is the offline function-tool example.
- `/template-status` reports non-secret runtime state: which runtime context keys arrived, whether storage is usable, which LLM provider was resolved.
- `/template-llm <question>` prints the LLM config the host resolved for this plugin. It performs no network call unless the payload sets `live: true` and a key is configured.
- `/template-outline <topic>` goes through the planner to `template_note_curator`, which calls back into this plugin's own tools through a restricted registry view.
- Open the Template Console page to exercise the page bridge: API calls, file upload, binary preview, download, a live event stream and toasts.
