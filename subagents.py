"""SubAgents - a decorated one and an explicitly registered one.

A subagent is a class with one method:

    async def run(task: dict, context: dict, decision: dict) -> dict

`domain` and `capabilities` are what the planner matches a task against, so
they are routing metadata, not documentation. Be specific: a subagent claiming
`domain="general"` competes with everything.

Tool visibility is layered. A subagent sees a tool only if every layer allows
it: the tool's own `allowed_subagents`, the subagent's `tools=(...)`,
`config.tool_access` in plugin.yaml, and `runtime.subagent_tool_access` in the
host's config.yaml. The host can tighten what you declare, never widen it.
"""

from __future__ import annotations

from typing import Any

from extension.plugin import plugin_subagent

# Vote answers the runtime understands. Anything unparseable counts as abstain.
APPROVE = "approve"
REJECT = "reject"


def _vote(spec: dict[str, Any], threshold: float) -> dict[str, Any]:
    """Answer a `vote` DAG node.

    Any registered subagent can be listed in a vote node's `voters`. When it is
    delegated a vote, the task carries `_vote_spec` with `topic`, `proposal`,
    `objective` and `choices`. Return a `choice` plus a short `reason`, either
    at the top level or one level down under result/output/answer.
    """
    proposal = str(spec.get("proposal") or spec.get("topic") or "")
    # A real voter reasons about the proposal. This one uses a length heuristic
    # so the template has no LLM dependency and a deterministic test.
    score = min(1.0, len(proposal.split()) / 20.0)
    choice = APPROVE if score >= threshold else REJECT
    return {
        "choice": choice,
        "reason": f"Proposal detail score {score:.2f} against threshold {threshold:.2f}.",
        "score": score,
    }


@plugin_subagent(
    "template_note_curator",
    # The planner matches on these two. Keep them narrow and honest.
    domain="notes",
    capabilities=("note-taking", "outline-drafting", "note-search"),
    # The subagent-side allow-list. Intersected with each tool's own.
    tools=("template_notes_tool", "template_format_tool", "template_note_tool"),
)
class TemplateNoteCurator:
    """Turn a request into an outline built from the caller's own notes."""

    def __init__(
        self,
        domain: str,
        capabilities: tuple[str, ...],
        plugin: Any | None = None,
        runtime_context: dict[str, Any] | None = None,
        tools: Any | None = None,
        **_: Any,
    ) -> None:
        # The host injects by parameter name, so declare only what you use.
        # `tools` arrives already filtered to what this subagent may see - it
        # is not the full registry, and it should not be swapped for one.
        self.domain = domain
        self.capabilities = capabilities
        self.plugin = plugin
        self.runtime_context = runtime_context or {}
        self.tools = tools

    async def run(self, task: dict[str, Any], context: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
        _ = (context, decision)
        trace_id = str(task.get("trace_id") or "trace-template-curator")

        vote_spec = task.get("_vote_spec")
        if isinstance(vote_spec, dict):
            threshold = float(_settings(self.plugin).get("relevance_threshold", 0.5))
            return {"status": "success", "subagent": "template_note_curator", "trace_id": trace_id, **_vote(vote_spec, threshold)}

        registry = self.tools or self.runtime_context.get("tool_registry")
        notes_tool = registry.get("template_notes_tool") if registry is not None else None
        if notes_tool is None:
            # Getting None here usually means an allow-list excluded the tool,
            # not that it failed to register.
            return {
                "status": "error",
                "subagent": "template_note_curator",
                "trace_id": trace_id,
                "error_code": "tool_unavailable",
                "report": "template_notes_tool is not visible to this subagent.",
            }

        listing = await notes_tool.execute({"actor_id": task.get("actor_id"), "limit": 20, "trace_id": trace_id})
        notes = listing.get("notes", []) if isinstance(listing, dict) else []
        query = str(task.get("query", "")).strip()
        return {
            "status": "success",
            "subagent": "template_note_curator",
            "trace_id": trace_id,
            "domain": self.domain,
            "capabilities": list(self.capabilities),
            "outline": [f"{index}. {note.get('title', '')}" for index, note in enumerate(notes[:10], start=1)],
            "source_count": len(notes),
            "report": f"Drafted an outline for {query!r} from {len(notes)} note(s).",
        }


class TemplateNoteReviewer:
    """Explicitly registered subagent, built as a dedicated vote-node voter."""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        # Declared so the registry guard can replace it with the filtered view
        # it builds from metadata["allowed_tools"]. Without the attribute there
        # is nothing for it to attach to, and this agent would see every tool.
        self.tools: Any | None = None

    async def run(self, task: dict[str, Any], context: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
        _ = (context, decision)
        threshold = float(_settings(self.plugin).get("relevance_threshold", 0.5))
        spec = task.get("_vote_spec")
        if not isinstance(spec, dict):
            return {
                "status": "success",
                "subagent": "template_note_reviewer",
                "choice": "abstain",
                "reason": "This subagent only answers vote nodes.",
            }
        return {"status": "success", "subagent": "template_note_reviewer", **_vote(spec, threshold)}


def _settings(plugin: Any | None) -> dict[str, Any]:
    config = getattr(plugin, "config", None)
    section = config.get("notes") if isinstance(config, dict) else None
    return section if isinstance(section, dict) else {}


def register_subagents(subagent_registry: Any, plugin: Any, runtime_context: dict[str, Any]) -> None:
    """Register a subagent that needs constructor arguments of its own.

    Decorated classes in this module are still discovered afterwards, so the
    two styles coexist.
    """
    # Note the argument order: name first, then the instance.
    subagent_registry.register(
        "template_note_reviewer",
        TemplateNoteReviewer(plugin),
        domain="notes",
        capabilities=("note-review", "voting"),
        metadata={
            "description": "Reviews a note proposal and votes approve or reject.",
            # The registry guard reads this, builds the filtered tool view and
            # assigns it to the instance's `tools` attribute for you.
            "allowed_tools": ("template_notes_tool", "template_format_tool"),
        },
    )

    # `runtime_context["tool_registry_factory"]` builds that same filtered view
    # on demand, for anything the guard does not touch - a worker object, a
    # background task, a helper you hand to several agents:
    #
    #   factory = runtime_context.get("tool_registry_factory")
    #   view = factory("template_note_reviewer", ("template_notes_tool",))
    #
    # It is absent when the host's registry cannot produce filtered views, so
    # always check `callable(factory)` before using it.
