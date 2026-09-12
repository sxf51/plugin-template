"""Tools - the three ways to register one, and what the host injects into each.

Registration paths, all equivalent to the runtime:

1. `@plugin_tool` on a class      -> `TemplateNoteTool` below
2. `@plugin_tool` on a function   -> `template_list_notes` below
3. `register_tools(...)` by hand  -> `TemplateStatusTool` at the bottom

A module may use all three at once, as this one does. When `register_tools` is
present the host calls it first and then still scans the module for decorated
members, so nothing is lost by mixing them.

What every tool must get right:

* `execute(payload) -> dict`. Async or sync both work.
* Return a dict, never raise for an expected failure. `status`, a stable
  snake_case `error_code`, and a human `report` are the shape the rest of the
  runtime reads.
* Declare `description` and `parameters` (a JSON Schema object). The registry
  copies them into the tool's `input_schema`, which is what the planner shows
  the model. A tool with no schema gets guessed at, and guesses route badly.
* Declare `consequential = True` if the call has a real side effect. That flag
  is the only thing the approval gate looks at.
"""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from urllib import error, request
from urllib.parse import urlparse

from extension.plugin import plugin_tool

if TYPE_CHECKING:
    from types import ModuleType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Loading a module the host does not import for you
# ---------------------------------------------------------------------------


def _load_local_module(filename: str) -> ModuleType:
    """Import a sibling module by path.

    The host imports only `hooks.py`, `tools.py`, `subagents.py` and `web.py`,
    and it does so under a synthetic module name without putting the plugin
    directory on `sys.path`. A plain `import store` therefore fails. This is
    how you split a plugin across more files than the four fixed ones.
    """
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"plugin_template_{path.stem}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable for a shipped file
        msg = f"cannot load {filename}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_store = _load_local_module("store.py")


# ---------------------------------------------------------------------------
# Small helpers shared by the tools below
# ---------------------------------------------------------------------------


def _config(plugin: Any | None) -> dict[str, Any]:
    """`plugin.config` merges plugin.yaml's `config:` with the saved schema values."""
    config = getattr(plugin, "config", None)
    return config if isinstance(config, dict) else {}


def _notes_settings(plugin: Any | None) -> dict[str, Any]:
    section = _config(plugin).get("notes")
    return section if isinstance(section, dict) else {}


def _actor(payload: dict[str, Any]) -> str:
    """Who is calling.

    The host puts the caller identity in the payload; a `before_node_execute`
    hook in hooks.py backfills it when the planner leaves it out. Notes are
    keyed by it, so getting this wrong leaks one user's data to another.
    """
    return str(payload.get("actor_id") or payload.get("user_id") or "unknown").strip() or "unknown"


def _query(payload: dict[str, Any]) -> str:
    """Pull the user's text out of wherever this particular route put it."""
    for key in ("text", "query", "content", "prompt"):
        value = str(payload.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _strip_command(text: str) -> str:
    """Drop a leading slash command so `/template-note hello` saves `hello`."""
    if text.startswith("/"):
        _, _, rest = text.partition(" ")
        return rest.strip()
    return text


def _failure(tool: str, code: str, report: str, trace_id: str) -> dict[str, Any]:
    """One shape for every expected failure, so callers can branch on a code."""
    return {"status": "error", "tool": tool, "trace_id": trace_id, "error_code": code, "report": report}


# ---------------------------------------------------------------------------
# 1. Decorated class tool - has side effects, uses injected storage
# ---------------------------------------------------------------------------


@plugin_tool(
    "template_note_tool",
    tags=("template", "notes", "write", "command"),
    # A second allow-list, next to config.tool_access in plugin.yaml. The two
    # are intersected, so declaring it here keeps the constraint with the code
    # even if someone edits the manifest.
    allowed_subagents=("template_note_curator",),
)
class TemplateNoteTool:
    """Save one note for the calling user."""

    # Read by the registry when no metadata= is passed at registration time.
    description = "Save a short note for the calling user and return the stored record."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Note body; the first line becomes the title."},
            "tag": {"type": "string", "description": "Optional label, defaults to notes.default_tag."},
            "attachment_path": {
                "type": "string",
                "description": "Optional path to a file the host stored for this user.",
            },
        },
        "required": ["text"],
    }
    # This tool writes. The approval gate reads exactly this attribute.
    consequential = True

    def __init__(self, plugin: Any | None = None, runtime_context: dict[str, Any] | None = None, **_: Any) -> None:
        # The host instantiates decorated classes with whichever of these your
        # __init__ actually accepts: plugin, runtime_context, tool_registry,
        # subagent_registry. Declaring a parameter is how you opt in.
        self.plugin = plugin
        # Already scoped to this plugin. Nothing below passes a plugin name.
        self.storage = (runtime_context or {}).get("storage")

    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        trace_id = str(payload.get("trace_id") or "trace-template-note")
        actor = _actor(payload)
        text = _strip_command(str(payload.get("text") or _query(payload)))
        if not text:
            return _failure("template_note_tool", "missing_text", "Nothing to save. Use /template-note <text>.", trace_id)
        if self.storage is None:
            return _failure("template_note_tool", "storage_unavailable", "Storage was not injected.", trace_id)

        title, _, body = text.partition("\n")
        settings = _notes_settings(self.plugin)
        store = _store.NoteStore(self.storage)
        note = store.add(actor, title, body or title, str(payload.get("tag") or settings.get("default_tag", "general")))

        attachment = self._describe_attachment(payload)
        return {
            "status": "success",
            "tool": "template_note_tool",
            "trace_id": trace_id,
            "note": note,
            "total": store.count(actor),
            "backend": store.backend,
            **({"attachment": attachment} if attachment else {}),
            "report": f"Saved note {note['id']} ({note['tag']}).",
        }

    def _describe_attachment(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Resolve a path the caller handed us - the only safe way to touch one.

        Two different questions, two different methods:

        * `storage.resolve()` - is this one of *my own* files?
        * `storage.user_file()` - is this a file the host stored for a user,
          such as a chat attachment the model cannot see the real path of?

        Both return None for anything outside, symlinks included. A plugin that
        opens a caller-supplied path without asking one of them is a traversal
        bug waiting to happen.
        """
        raw = str(payload.get("attachment_path") or "").strip()
        if not raw or self.storage is None:
            return None
        resolved = self.storage.resolve(raw) or self.storage.user_file(raw)
        if resolved is None:
            return {"accepted": False, "reason": "path_outside_plugin_scope"}
        return {"accepted": True, "name": resolved.name, "bytes": resolved.stat().st_size}


# ---------------------------------------------------------------------------
# 2. Decorated function tool - read-only, no class needed
# ---------------------------------------------------------------------------


@plugin_tool(
    "template_format_tool",
    tags=("template", "notes", "offline", "function"),
    allowed_subagents=("template_note_curator", "template_note_reviewer"),
    # A bare function has nowhere to hang class attributes, so its description
    # and schema are declared here instead. The host wraps it so it still
    # satisfies the execute(payload) protocol.
    metadata={
        "description": "Split free text into a note title, body and tag without touching storage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "tag": {"type": "string"},
            },
            "required": ["text"],
        },
    },
)
async def template_format_note(payload: dict[str, Any]) -> dict[str, Any]:
    """Function-style tool. The decorator's name, not this function's name, is the tool id.

    A function tool receives no injected dependencies - no plugin, no storage,
    no runtime context - so it can only work from its payload. That makes it
    the right shape for pure transformations and the wrong shape for anything
    that needs configuration or state. Reach for a class when you need those.
    """
    text = _strip_command(str(payload.get("text") or _query(payload)))
    if not text:
        return _failure("template_format_tool", "missing_text", "Nothing to format.", str(payload.get("trace_id", "")))
    title, _, body = text.partition("\n")
    return {
        "status": "success",
        "tool": "template_format_tool",
        "trace_id": str(payload.get("trace_id") or "trace-template-format"),
        "title": " ".join(title.split())[:120],
        "body": " ".join((body or title).split())[:4000],
        "tag": " ".join(str(payload.get("tag") or "general").split())[:32],
        "offline": True,
        "report": "Note text normalised.",
    }


@plugin_tool(
    "template_notes_tool",
    tags=("template", "notes", "read", "command"),
    allowed_subagents=("template_note_curator", "template_note_reviewer"),
)
class TemplateNotesTool:
    """List the calling user's notes. Read-only, so not consequential."""

    description = "List the calling user's saved notes, newest first."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}},
    }
    consequential = False

    def __init__(self, runtime_context: dict[str, Any] | None = None, **_: Any) -> None:
        # Only the dependency this tool actually uses is declared. The host
        # passes whatever the signature accepts and nothing more.
        self.storage = (runtime_context or {}).get("storage")

    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        trace_id = str(payload.get("trace_id") or "trace-template-notes")
        if self.storage is None:
            return _failure("template_notes_tool", "storage_unavailable", "Storage was not injected.", trace_id)
        store = _store.NoteStore(self.storage)
        # Scoped to the caller. Never list every actor's notes from one call.
        notes = store.recent(_actor(payload), int(payload.get("limit") or 20))
        return {
            "status": "success",
            "tool": "template_notes_tool",
            "trace_id": trace_id,
            "notes": notes,
            "count": len(notes),
            "backend": store.backend,
            "report": f"{len(notes)} note(s)." if notes else "No notes yet. Use /template-note <text>.",
        }


# ---------------------------------------------------------------------------
# 3. Explicitly registered tools - instantiated by you, with whatever you like
# ---------------------------------------------------------------------------


class TemplateStatusTool:
    """Report non-secret runtime state, so authors can see what was injected."""

    description = "Report which runtime context keys, storage backend and LLM provider this plugin resolved."
    parameters: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}
    consequential = False

    def __init__(self, plugin: Any, runtime_context: dict[str, Any]) -> None:
        self.plugin = plugin
        self.runtime_context = runtime_context

    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        storage = self.runtime_context.get("storage")
        llm = self.runtime_context.get("llm_config")
        return {
            "status": "success",
            "tool": "template_status_tool",
            "trace_id": str(payload.get("trace_id") or "trace-template-status"),
            "plugin": self.plugin.name,
            "version": self.plugin.version,
            # Exactly what the host handed this plugin. Useful once, when you
            # are working out what you are allowed to reach for.
            "runtime_context_keys": sorted(self.runtime_context),
            "storage_backend": _store.NoteStore(storage).backend,
            "llm_provider": getattr(llm, "provider_name", "") or getattr(llm, "provider", ""),
            "llm_model": getattr(llm, "model", ""),
            "config_keys": sorted(_config(self.plugin)),
            # Never echo a key, not even truncated. Report the fact, not the value.
            "api_key_exposed": False,
            "report": "Template plugin is loaded.",
        }


class TemplateLlmTool:
    """Show - and optionally use - the LLM config the host resolved for this plugin."""

    description = "Return the plugin-scoped LLM configuration; optionally send one chat completion."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Question to send when live is true."},
            "live": {"type": "boolean", "description": "Send a real request. Defaults to false."},
        },
    }
    # A live call spends money and leaves the process, so it is consequential
    # even though nothing local changes.
    consequential = True

    def __init__(self, plugin: Any | None = None, runtime_context: dict[str, Any] | None = None, **_: Any) -> None:
        self.plugin = plugin
        # Resolved per plugin: config.llm here overrides the chosen provider,
        # which overrides the global default, field by field.
        self.llm = (runtime_context or {}).get("llm_config")

    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        trace_id = str(payload.get("trace_id") or "trace-template-llm")
        if self.llm is None:
            return _failure("template_llm_tool", "llm_unconfigured", "No LLM config was injected.", trace_id)

        resolved = {
            "provider": getattr(self.llm, "provider_name", "") or getattr(self.llm, "provider", ""),
            "model": getattr(self.llm, "model", ""),
            "base_url": getattr(self.llm, "base_url", "") or "",
            "temperature": getattr(self.llm, "temperature", 0.0),
            "max_tokens": getattr(self.llm, "max_tokens", 0),
            "timeout_sec": getattr(self.llm, "timeout_sec", 0.0),
            "api_key_present": bool(getattr(self.llm, "api_key", "")),
        }
        query = _strip_command(_query(payload))
        if not payload.get("live"):
            # Default path: describe the call instead of making it, so the
            # template runs offline and costs nothing.
            return {
                "status": "success",
                "tool": "template_llm_tool",
                "trace_id": trace_id,
                "dry_run": True,
                "resolved": resolved,
                "would_send": {"model": resolved["model"], "prompt": query},
                "report": f"Would call {resolved['provider']}/{resolved['model']}. Pass live=true to send it.",
            }
        return self._chat(query, resolved, trace_id)

    def _chat(self, query: str, resolved: dict[str, Any], trace_id: str) -> dict[str, Any]:
        if not resolved["api_key_present"] or not resolved["base_url"]:
            return _failure("template_llm_tool", "llm_unconfigured", "No API key or base URL resolved.", trace_id)
        prompt = str(_notes_settings(self.plugin).get("summary_prompt") or "")
        body = {
            "model": resolved["model"],
            "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": query}],
            "temperature": resolved["temperature"],
        }
        try:
            answer = _post_json(
                f"{str(resolved['base_url']).rstrip('/')}/chat/completions",
                body,
                {
                    "Authorization": f"Bearer {self.llm.api_key}",
                    "Content-Type": "application/json",
                },
                float(resolved["timeout_sec"] or 60.0),
            )
        except (OSError, ValueError, error.HTTPError) as exc:
            # Catch what this call can actually raise. A bare `except Exception`
            # would swallow programming bugs in the lines above too.
            logger.warning("template plugin chat completion failed: %s", type(exc).__name__)
            # The provider's own error text can carry server paths or key
            # fragments, so it is logged, not returned.
            return _failure("template_llm_tool", "provider_error", "The provider call failed.", trace_id)
        choices = answer.get("choices") or [{}]
        return {
            "status": "success",
            "tool": "template_llm_tool",
            "trace_id": trace_id,
            "dry_run": False,
            "resolved": resolved,
            "answer": str(choices[0].get("message", {}).get("content", "")),
            "report": "Chat completion returned.",
        }


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    """POST JSON over http(s) only."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        msg = "LLM base URL must use http or https and include a hostname"
        raise ValueError(msg)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = request.Request(url=url, data=payload, headers=headers, method="POST")
    # Scheme and hostname are validated directly above.
    with request.urlopen(req, timeout=timeout) as response:  # nosec B310
        parsed_body = json.loads(response.read().decode("utf-8"))
    if not isinstance(parsed_body, dict):
        msg = "provider response is not a JSON object"
        raise ValueError(msg)
    return parsed_body


# ---------------------------------------------------------------------------
# The explicit entry point. Called before decorated members are scanned.
# ---------------------------------------------------------------------------


def register_tools(tool_registry: Any, plugin: Any, runtime_context: dict[str, Any]) -> None:
    """Register the tools that need constructor arguments of their own.

    `tool_registry` is not the host's registry: it is a guard that scopes
    registrations to this plugin and refuses names reserved by builtin tools.
    Prefix every tool name with your plugin to avoid a startup-fatal clash.
    """
    tool_registry.register(
        TemplateStatusTool(plugin, runtime_context),
        name="template_status_tool",
        tags=("template", "status", "explicit-registration"),
        # allowed_roles defaults to ("user", "admin"); narrow it for anything
        # only an operator should be able to invoke.
        allowed_roles=("user", "admin"),
    )
    tool_registry.register(
        TemplateLlmTool(plugin=plugin, runtime_context=runtime_context),
        name="template_llm_tool",
        tags=("template", "llm", "explicit-registration"),
    )
