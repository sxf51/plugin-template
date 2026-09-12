"""Hooks - one handler at each of the ten lifecycle points, in execution order.

The contract is the same everywhere:

    async def handler(context: dict) -> dict | None

Return a new context to apply your changes, or None to pass through untouched.
Copy the dict rather than mutating the one you were given: other plugins'
handlers run on the same object, and an in-place edit makes the order they
happen to be registered in part of your behaviour.

Handlers run in ascending priority, so a lower number runs earlier. The house
convention: 10-40 enrich inbound data, 50-80 shape execution, 90+ observe and
decorate results. Keep the work small - every inbound message pays for your
`before_route` handler, and the runtime times each plugin's handlers separately
so a slow one is attributable to you by name.

Unregistering is automatic: the host clears everything this plugin registered
when the plugin is disabled, reloaded or uninstalled.
"""

from __future__ import annotations

import logging
from typing import Any

from extension.hook import HookPoint

logger = logging.getLogger(__name__)

# Every tool this plugin owns. Hooks fire for the whole runtime, so the first
# thing most handlers do is check whether this event is even theirs.
TEMPLATE_TOOLS = {
    "template_note_tool",
    "template_notes_tool",
    "template_format_tool",
    "template_status_tool",
    "template_llm_tool",
}


def _node_target(context: dict[str, Any]) -> str:
    """Which tool or subagent a DAG node points at."""
    node = context.get("node")
    runtime_config = node.get("runtime_config", {}) if isinstance(node, dict) else {}
    return str(runtime_config.get("target", "")) if isinstance(runtime_config, dict) else ""


def _message_text(context: dict[str, Any]) -> str:
    message = context.get("message")
    return str(message.get("text", "") if isinstance(message, dict) else "").strip()


def register_hooks(hook_manager: Any, plugin: Any, runtime_context: dict[str, Any]) -> None:  # noqa: PLR0915
    """Register one handler per lifecycle point.

    A real plugin registers the two or three it needs. All ten are here so the
    signature, the context keys and the return shape of each one are visible in
    a single file.
    """
    _ = runtime_context
    name = str(getattr(plugin, "name", "plugin-template"))

    # -- 1. before_route ----------------------------------------------------
    # First thing after a message is normalised, before command dispatch and
    # routing. context: {"message": {...}, ...}. This is the only point that
    # can end the turn on its own, through `route_outcome.action`:
    #   continue - carry on (the default; just leave route_outcome alone)
    #   respond  - reply with `response` and stop
    #   drop     - persist the turn and stop, with nothing shown
    # The first respond or drop short-circuits the remaining before_route
    # handlers of every plugin, so claim a message only when it is yours.
    async def before_route(context: dict[str, Any]) -> dict[str, Any]:
        updated = dict(context)
        text = _message_text(updated)
        lowered = text.lower()

        if lowered.startswith("/template-ping"):
            # Answered here and nowhere else: no command entry in plugin.yaml,
            # no tool, no LLM turn. The host persists the turn and delivers it.
            updated["route_outcome"] = {
                "action": "respond",
                "response": f"pong from {name}",
            }
            return updated

        if lowered.startswith("/template-ignore"):
            # Swallow the message: recorded in history, nothing sent back.
            # Useful for acknowledgement traffic from a chat platform.
            updated["route_outcome"] = {"action": "drop"}
            return updated

        if lowered.startswith("/template-outline"):
            # The recommended way to influence routing: a soft hint the planner
            # may weigh, instead of hard-coding the decision in this handler.
            updated["router_hints"] = {
                "plugin": name,
                "preferred_route": "subagent",
                "preferred_subagent": "template_note_curator",
                "fallback_route": "tool",
                "fallback_tool": "template_notes_tool",
            }
        return updated

    # -- 2. before_plan -----------------------------------------------------
    # The task is about to be planned. context: {"task": {...}, "hints": {...}}.
    # Enrich the task or the planner hints; do not plan here yourself.
    async def before_plan(context: dict[str, Any]) -> dict[str, Any]:
        updated = dict(context)
        task = updated.get("task")
        if not isinstance(task, dict) or not str(task.get("query", "")).lower().startswith("/template-"):
            return updated
        hints = dict(updated.get("hints") or {})
        hints.setdefault("preferred_domain", "notes")
        updated["hints"] = hints
        return updated

    # -- 3. after_plan_graph ------------------------------------------------
    # A complete DAG, before compilation and validation. context: {"graph": {...}}.
    # Safe place to fill in defaults the planner had no way to know. Do not add
    # or remove nodes unless you are prepared to keep the edges consistent.
    async def after_plan_graph(context: dict[str, Any]) -> dict[str, Any]:
        graph = context.get("graph")
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
            return context
        updated_nodes = []
        for node in graph["nodes"]:
            runtime_config = node.get("runtime_config") if isinstance(node, dict) else None
            if not isinstance(runtime_config, dict) or runtime_config.get("target") != "template_notes_tool":
                updated_nodes.append(node)
                continue
            parameters = dict(runtime_config.get("parameters") or {})
            parameters.setdefault("limit", 20)
            updated_nodes.append({**node, "runtime_config": {**runtime_config, "parameters": parameters}})
        return {**context, "graph": {**graph, "nodes": updated_nodes}}

    # -- 4. before_node_execute ---------------------------------------------
    # One node's payload, immediately before it runs.
    # context: {"node": {...}, "payload": {...}, "task": {...}}.
    # This is where caller identity gets attached: the planner builds payloads
    # from what the model produced, and the model must never be the source of
    # an actor id. Take it from the task instead.
    async def before_node_execute(context: dict[str, Any]) -> dict[str, Any]:
        if _node_target(context) not in TEMPLATE_TOOLS:
            return context
        payload = dict(context.get("payload") or {})
        task = context.get("task")
        if isinstance(task, dict):
            actor_id = str(task.get("actor_id") or task.get("user_id") or "").strip()
            if actor_id:
                payload.setdefault("actor_id", actor_id)
        return {**context, "payload": payload}

    # -- 5. before_tool_call ------------------------------------------------
    # Inside the tool executor, after authorisation and the approval gate but
    # before the tool runs. context: {"tool": str, "payload": {...}}.
    # Setting context["denied"] = True refuses the call, and the executor raises
    # with whatever `reason` says - so set both, or the caller gets a generic
    # message. `payload` must survive as a dict either way. This is the last
    # place a plugin can stop a call, so keep the rule narrow and specific.
    async def before_tool_call(context: dict[str, Any]) -> dict[str, Any]:
        if str(context.get("tool", "")) != "template_llm_tool":
            return context
        payload = context.get("payload")
        if isinstance(payload, dict) and payload.get("live") and not str(payload.get("query", "")).strip():
            logger.info("%s refused an empty live LLM call", name)
            return {**context, "denied": True, "reason": "empty_live_query"}
        return context

    # -- 6. after_tool_call -------------------------------------------------
    # The tool returned. context: {"tool": str, "result": Any}.
    # Redact here rather than in the tool when the rule is cross-cutting.
    async def after_tool_call(context: dict[str, Any]) -> dict[str, Any]:
        result = context.get("result")
        if str(context.get("tool", "")) not in TEMPLATE_TOOLS or not isinstance(result, dict):
            return context
        redacted = {key: value for key, value in result.items() if key not in {"api_key", "token"}}
        return {**context, "result": redacted}

    # -- 7. after_node_execute ----------------------------------------------
    # One node's result. context: {"node": {...}, "result": Any}.
    # Tag provenance so a downstream reader can tell where a field came from.
    async def after_node_execute(context: dict[str, Any]) -> dict[str, Any]:
        result = context.get("result")
        if _node_target(context) not in TEMPLATE_TOOLS or not isinstance(result, dict):
            return context
        enriched = dict(result)
        enriched.setdefault("plugin", name)
        enriched.setdefault("extension_method", "after_node_execute_hook")
        return {**context, "result": enriched}

    # -- 8. on_node_error ---------------------------------------------------
    # One node failed. context: {"node": {...}, "error": Exception | str}.
    # Returning a `result` recovers the node; returning the context unchanged
    # lets the failure propagate. Only recover a failure you actually
    # understand - a blanket fallback hides real breakage.
    async def on_node_error(context: dict[str, Any]) -> dict[str, Any]:
        if _node_target(context) != "template_notes_tool":
            return context
        logger.warning("%s recovering a notes listing failure: %s", name, context.get("error"))
        return {
            **context,
            "result": {
                "status": "success",
                "tool": "template_notes_tool",
                "notes": [],
                "degraded": True,
                "report": "Notes are temporarily unavailable.",
            },
        }

    # -- 9. after_workflow --------------------------------------------------
    # The aggregate result of a whole workflow. context: {"result": {...}}.
    async def after_workflow(context: dict[str, Any]) -> dict[str, Any]:
        result = context.get("result")
        if not isinstance(result, dict) or not _mentions_template_tool(result):
            return context
        return {**context, "result": {**result, "contributing_plugins": [name]}}

    # -- 10. on_workflow_error ----------------------------------------------
    # The workflow failed. context: {"error": ...}. Observe and record; do not
    # swallow it. Whatever the host does with a failure is not a plugin's call.
    async def on_workflow_error(context: dict[str, Any]) -> dict[str, Any]:
        logger.warning("%s observed a workflow failure: %s", name, context.get("error"))
        return context

    hook_manager.register(name, HookPoint.before_route, before_route, priority=30)
    hook_manager.register(name, HookPoint.before_plan, before_plan, priority=35)
    hook_manager.register(name, HookPoint.after_plan_graph, after_plan_graph, priority=50)
    hook_manager.register(name, HookPoint.before_node_execute, before_node_execute, priority=40)
    hook_manager.register(name, HookPoint.before_tool_call, before_tool_call, priority=60)
    hook_manager.register(name, HookPoint.after_tool_call, after_tool_call, priority=70)
    hook_manager.register(name, HookPoint.after_node_execute, after_node_execute, priority=90)
    hook_manager.register(name, HookPoint.on_node_error, on_node_error, priority=80)
    hook_manager.register(name, HookPoint.after_workflow, after_workflow, priority=95)
    hook_manager.register(name, HookPoint.on_workflow_error, on_workflow_error, priority=95)


def _mentions_template_tool(value: Any, depth: int = 3) -> bool:
    """Did any nested node result come from one of this plugin's tools?

    A workflow result aggregates every node, so this is how a plugin decides
    whether the aggregate is any of its business.
    """
    if depth <= 0:
        return False
    if isinstance(value, dict):
        if str(value.get("tool", "")) in TEMPLATE_TOOLS:
            return True
        return any(_mentions_template_tool(item, depth - 1) for item in value.values())
    if isinstance(value, list):
        return any(_mentions_template_tool(item, depth - 1) for item in value)
    return False
