"""What *this* plugin does. Replace this file when you copy the plugin.

`test_plugin_contract.py` next to it holds the checks every plugin should pass;
those are generic and copy over unchanged. This file is the other half: the
behaviour only this plugin has, and it is the example to follow when writing
tests for your own.

The pattern worth copying: ask for the `bundle` fixture, reach for a tool or an
endpoint by name, and assert on what comes back. The bodies never ask which
backend is running, so the same tests cover the real PluginManager inside the
project and the stand-ins outside it.

No Redis and no network either way: storage falls back to a JSON file, which is
a path a plugin has to survive anyway.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

EXPECTED_TOOLS = {
    "template_note_tool",
    "template_notes_tool",
    "template_format_tool",
    "template_status_tool",
    "template_llm_tool",
}
EXPECTED_SUBAGENTS = {"template_note_curator", "template_note_reviewer"}
EXPECTED_COMMANDS = {
    "/template-note",
    "/template-notes",
    "/template-format",
    "/template-status",
    "/template-llm",
    "/template-outline",
}
HOOK_POINTS = {
    "before_route",
    "before_plan",
    "after_plan_graph",
    "before_node_execute",
    "after_node_execute",
    "before_tool_call",
    "after_tool_call",
    "on_node_error",
    "after_workflow",
    "on_workflow_error",
}
SUPPORTED_CONFIG_TYPES = {
    "string",
    "text",
    "int",
    "float",
    "bool",
    "object",
    "list",
    "dict",
    "template_list",
    "file",
}
PANEL_WIDGET_TYPES = {
    "stat",
    "key-value",
    "table",
    "line-chart",
    "bar-chart",
    "log-stream",
    "form",
    "action",
    "markdown",
}
CHART_DAYS = 7
UPLOAD_BYTES = 5
PRUNED_ATTACHMENTS = 2


def _store_module(plugin_dir: Path) -> Any:
    """Load this plugin's store.py the way the plugin itself loads it."""
    spec = importlib.util.spec_from_file_location("plugin_template_store_test", plugin_dir / "store.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _call(host: Any, bundle: Any, endpoint: str, method: str = "GET", **kwargs: Any) -> Any:
    return asyncio.run(host.call_web(bundle, endpoint, method, **kwargs))


def _body(response: Any) -> Any:
    """Read a helper response, whichever backend produced it."""
    if isinstance(response, dict | list):
        return response
    content = getattr(response, "content", None)
    if content is not None:
        return content
    return json.loads(bytes(response.body))


def _hook(bundle: Any, point: str, context: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(bundle.hooks.trigger(point, context))


# ---------------------------------------------------------------------------
# Manifest and schema
# ---------------------------------------------------------------------------


def test_template_ships_disabled(plugin_dir: Path) -> None:
    """Enabling it is the reader's choice; the committed copy stays inert.

    This fails while the plugin is switched on for local trials, which is when
    it should: set `enabled: false` again before committing.
    """
    manifest = yaml.safe_load((plugin_dir / "plugin.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == plugin_dir.name
    assert manifest["enabled"] is False, (
        "plugin.yaml is currently enabled. That is fine while you are trying it out; "
        "set enabled back to false before committing."
    )


def test_config_schema_covers_every_supported_type(host: Any, plugin_dir: Path) -> None:
    """The template demonstrates all ten field types; your plugin need not."""
    schema = host.validate_schema(json.loads((plugin_dir / "_conf_schema.json").read_text(encoding="utf-8")))

    def types_in(spec_map: dict[str, Any]) -> set[str]:
        found: set[str] = set()
        for spec in spec_map.values():
            found.add(spec["type"])
            if spec["type"] == "object":
                found |= types_in(spec.get("items", {}))
        return found

    assert types_in(schema) == SUPPORTED_CONFIG_TYPES


def test_requirements_file_parses_and_needs_nothing(host: Any, plugin_dir: Path) -> None:
    assert host.parse_requirements(plugin_dir) == (), "stay stdlib-only so the plugin loads anywhere"


def test_panel_widgets_cover_the_vocabulary(bundle: Any) -> None:
    """The template demonstrates every widget type; your plugin need not."""
    widgets = [widget for panel in bundle.plugin.ui.panels for widget in panel["widgets"]]
    assert {widget["type"] for widget in widgets} == PANEL_WIDGET_TYPES


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_every_documented_tool_registers(bundle: Any) -> None:
    assert set(bundle.tools.tools) >= EXPECTED_TOOLS


def test_the_right_tools_are_marked_as_having_side_effects(bundle: Any) -> None:
    """Which tools write is a claim about this plugin, not a generic rule.

    That every tool declares the flag at all is checked in the contract tests.
    """
    # Saving a note and spending money on a completion are the two with real
    # side effects; the approval gate reads exactly this flag.
    assert bundle.tools.tools["template_note_tool"].consequential is True
    assert bundle.tools.tools["template_llm_tool"].consequential is True
    assert bundle.tools.tools["template_notes_tool"].consequential is False


def test_every_documented_subagent_registers(bundle: Any) -> None:
    assert set(bundle.subagents.list_agents()) >= EXPECTED_SUBAGENTS


def test_all_ten_hook_points_have_a_handler(bundle: Any) -> None:
    points = {str(registration.point) for registration in bundle.hooks.list_hooks()}
    assert points == HOOK_POINTS


def test_web_endpoints_register(bundle: Any) -> None:
    routes = {route.endpoint for route in bundle.web.list_for(bundle.plugin.name)}
    # "ping" comes from the decorator, the rest from register_web_apis.
    assert "ping" in routes
    assert {"stats", "summary", "recent", "series", "notes", "events", "badge", "export"} <= routes


# ---------------------------------------------------------------------------
# Notes, on the file fallback
# ---------------------------------------------------------------------------


def test_notes_round_trip_without_redis(bundle: Any) -> None:
    write = bundle.tools.get("template_note_tool")
    read = bundle.tools.get("template_notes_tool")

    saved = asyncio.run(write.execute({"text": "first note\nwith a body", "actor_id": "alice"}))
    assert saved["status"] == "success"
    assert saved["backend"] == "file", "no Redis here, so the documented fallback must engage"

    listed = asyncio.run(read.execute({"actor_id": "alice"}))
    assert [note["title"] for note in listed["notes"]] == ["first note"]
    # Notes are keyed by caller, so another actor sees none of them.
    assert asyncio.run(read.execute({"actor_id": "bob"}))["notes"] == []


def test_attachment_path_outside_plugin_scope_is_refused(bundle: Any, tmp_path: Path) -> None:
    outside = tmp_path / "not-mine.txt"
    outside.write_text("x", encoding="utf-8")
    result = asyncio.run(
        bundle.tools.get("template_note_tool").execute(
            {"text": "note", "actor_id": "alice", "attachment_path": str(outside)}
        )
    )
    assert result["attachment"] == {"accepted": False, "reason": "path_outside_plugin_scope"}


def test_llm_tool_never_calls_out_by_default(bundle: Any) -> None:
    result = asyncio.run(bundle.tools.get("template_llm_tool").execute({"query": "hello"}))
    assert result["dry_run"] is True
    assert result["resolved"]["api_key_present"] is False


def test_status_tool_reports_without_leaking(bundle: Any) -> None:
    result = asyncio.run(bundle.tools.get("template_status_tool").execute({}))
    assert result["api_key_exposed"] is False
    assert "storage" in result["runtime_context_keys"]


def test_format_tool_works_with_nothing_injected(bundle: Any) -> None:
    result = asyncio.run(bundle.tools.get("template_format_tool").execute({"text": "  a title \n a body "}))
    assert result["title"] == "a title"
    assert result["offline"] is True


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def test_before_route_answers_ping_without_the_agent(bundle: Any) -> None:
    context = _hook(bundle, "before_route", {"message": {"text": "/template-ping"}})
    assert context["route_outcome"]["action"] == "respond"
    assert "pong" in context["route_outcome"]["response"]


def test_before_route_can_drop_a_message(bundle: Any) -> None:
    context = _hook(bundle, "before_route", {"message": {"text": "/template-ignore"}})
    assert context["route_outcome"]["action"] == "drop"


def test_before_route_hints_instead_of_hard_routing(bundle: Any) -> None:
    context = _hook(bundle, "before_route", {"message": {"text": "/template-outline ideas"}})
    assert "route_outcome" not in context
    assert context["router_hints"]["preferred_subagent"] == "template_note_curator"


def test_before_plan_adds_hints_only_for_its_own_tasks(bundle: Any) -> None:
    mine = _hook(bundle, "before_plan", {"task": {"query": "/template-outline ideas"}, "hints": {}})
    assert mine["hints"]["preferred_domain"] == "notes"

    other = _hook(bundle, "before_plan", {"task": {"query": "unrelated"}, "hints": {}})
    assert other["hints"] == {}


def test_after_plan_graph_fills_in_node_defaults(bundle: Any) -> None:
    graph = {
        "nodes": [
            {"id": "a", "runtime_config": {"target": "template_notes_tool", "parameters": {}}},
            {"id": "b", "runtime_config": {"target": "some_other_tool", "parameters": {}}},
        ],
        "edges": [],
    }
    result = _hook(bundle, "after_plan_graph", {"graph": graph})

    nodes = {node["id"]: node["runtime_config"]["parameters"] for node in result["graph"]["nodes"]}
    assert nodes["a"] == {"limit": 20}
    # Another plugin's node is left exactly as it was.
    assert nodes["b"] == {}
    assert result["graph"]["edges"] == []


def test_before_node_execute_backfills_the_actor(bundle: Any) -> None:
    context = _hook(
        bundle,
        "before_node_execute",
        {
            "node": {"runtime_config": {"target": "template_notes_tool"}},
            "payload": {},
            "task": {"actor_id": "alice"},
        },
    )
    assert context["payload"]["actor_id"] == "alice"


def test_after_node_execute_tags_only_this_plugins_results(bundle: Any) -> None:
    tagged = _hook(
        bundle,
        "after_node_execute",
        {"node": {"runtime_config": {"target": "template_note_tool"}}, "result": {"status": "success"}},
    )
    assert tagged["result"]["plugin"] == "plugin-template"

    other = _hook(
        bundle,
        "after_node_execute",
        {"node": {"runtime_config": {"target": "other_tool"}}, "result": {"status": "success"}},
    )
    assert "plugin" not in other["result"]


def test_on_node_error_recovers_only_the_node_it_understands(bundle: Any) -> None:
    recovered = _hook(
        bundle,
        "on_node_error",
        {"node": {"runtime_config": {"target": "template_notes_tool"}}, "error": RuntimeError("redis down")},
    )
    assert recovered["result"]["degraded"] is True

    untouched = _hook(
        bundle,
        "on_node_error",
        {"node": {"runtime_config": {"target": "some_other_tool"}}, "error": RuntimeError("boom")},
    )
    assert "result" not in untouched


def test_after_workflow_claims_only_workflows_it_took_part_in(bundle: Any) -> None:
    ours = _hook(
        bundle,
        "after_workflow",
        {"result": {"nodes": [{"tool": "template_note_tool", "status": "success"}]}},
    )
    assert ours["result"]["contributing_plugins"] == ["plugin-template"]

    theirs = _hook(bundle, "after_workflow", {"result": {"nodes": [{"tool": "other_tool"}]}})
    assert "contributing_plugins" not in theirs["result"]


def test_on_workflow_error_observes_without_swallowing(bundle: Any) -> None:
    failure = {"error": RuntimeError("workflow blew up"), "trace_id": "t-1"}
    observed = _hook(bundle, "on_workflow_error", failure)
    assert observed["error"] is failure["error"]


# ---------------------------------------------------------------------------
# The tool-call hooks, through a real execution path
# ---------------------------------------------------------------------------


def test_before_tool_call_denial_stops_the_execution(host: Any, bundle: Any) -> None:
    with pytest.raises(host.execution_error()) as refused:
        asyncio.run(bundle.executor.execute("template_llm_tool", {"live": True, "query": "   "}))
    # The executor raises with the hook's own `reason`, so a refusal has to set
    # that key and not one of its own.
    assert "empty_live_query" in str(refused.value)

    allowed = asyncio.run(bundle.executor.execute("template_llm_tool", {"query": "hello"}))
    assert allowed["status"] == "success"


def test_after_tool_call_redaction_reaches_the_caller(bundle: Any) -> None:
    result = asyncio.run(bundle.executor.execute("template_status_tool", {}))
    assert "api_key" not in result
    assert "token" not in result


# ---------------------------------------------------------------------------
# SubAgents
# ---------------------------------------------------------------------------


def test_subagent_builds_an_outline_from_its_own_tools(bundle: Any) -> None:
    asyncio.run(bundle.tools.get("template_note_tool").execute({"text": "alpha", "actor_id": "carol"}))
    curator = bundle.subagents.get("template_note_curator")
    result = asyncio.run(curator.run({"query": "recap", "actor_id": "carol"}, {}, {}))
    assert result["status"] == "success"
    assert result["outline"] == ["1. alpha"]


def test_subagent_answers_a_vote_node(bundle: Any) -> None:
    reviewer = bundle.subagents.get("template_note_reviewer")
    detailed = asyncio.run(
        reviewer.run({"_vote_spec": {"proposal": " ".join(["word"] * 20), "choices": ["approve", "reject"]}}, {}, {})
    )
    thin = asyncio.run(reviewer.run({"_vote_spec": {"proposal": "short"}}, {}, {}))
    assert detailed["choice"] == "approve"
    assert thin["choice"] == "reject"


# ---------------------------------------------------------------------------
# Web endpoints
# ---------------------------------------------------------------------------


def test_table_and_chart_endpoints_return_the_declared_columns(host: Any, bundle: Any) -> None:
    _call(host, bundle, "notes", "POST", username="alice", body={"title": "one", "body": "first"})

    rows = _body(_call(host, bundle, "recent", username="alice"))
    assert len(rows) == 1
    assert set(rows[0]) >= {"title", "tag", "created"}

    points = _body(_call(host, bundle, "series", username="alice"))
    assert len(points) == CHART_DAYS
    assert set(points[0]) == {"day", "notes"}


def test_crud_endpoints_are_scoped_to_the_caller(host: Any, bundle: Any) -> None:
    created = _body(_call(host, bundle, "notes", "POST", username="alice", body={"title": "mine"}))
    note_id = created["note"]["id"]

    # Bob cannot reach Alice's note by guessing its id.
    denied = _body(_call(host, bundle, "notes/update", "PUT", username="bob", body={"id": note_id, "title": "x"}))
    assert denied["message"] == "note_not_found"

    renamed = _call(host, bundle, "notes/update", "PUT", username="alice", body={"id": note_id, "title": "renamed"})
    assert _body(renamed)["note"]["title"] == "renamed"

    deleted = _body(_call(host, bundle, "notes/delete", "DELETE", username="alice", query={"id": note_id}))
    assert deleted["id"] == note_id


def test_binary_endpoints_serve_real_bytes(host: Any, bundle: Any) -> None:
    badge = _call(host, bundle, "badge", username="alice")
    assert badge.media_type == "image/svg+xml"
    assert bytes(badge.body).startswith(b"<svg")

    export = _call(host, bundle, "export", username="alice")
    assert json.loads(bytes(export.body)) == []


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


def _upload(host: Any, bundle: Any, user: str, payload: bytes, **kwargs: Any) -> dict[str, Any]:
    response = _call(host, bundle, "attachments", "POST", username=user, upload=payload, **kwargs)
    return _body(response)["attachment"]


def test_upload_rejects_what_it_should(host: Any, bundle: Any) -> None:
    too_big = _call(host, bundle, "attachments", "POST", username="alice", upload=b"x" * (2 * 1024 * 1024))
    assert _body(too_big)["message"] == "file_too_large"

    missing = _call(host, bundle, "attachments", "POST", username="alice")
    assert _body(missing)["message"] == "missing_file"

    empty = _call(host, bundle, "attachments", "POST", username="alice", upload=b"")
    assert _body(empty)["message"] == "empty_file"


def test_upload_keeps_the_original_format(host: Any, bundle: Any) -> None:
    record = _upload(host, bundle, "alice", b"hello", filename="Q3 report.PDF", content_type="application/pdf")
    assert record["name"] == "Q3 report.pdf"
    assert record["stored_name"].endswith(".pdf")
    assert record["content_type"] == "application/pdf"
    assert record["bytes"] == UPLOAD_BYTES
    # The name on disk is generated, so it never echoes the client's.
    assert "report" not in record["stored_name"]


def test_upload_refuses_to_keep_a_dangerous_extension(host: Any, bundle: Any, plugin_dir: Path) -> None:
    """An unrecognised extension falls back to the declared type, then to .bin."""
    allowed = set(_store_module(plugin_dir).ALLOWED_EXTENSIONS) | {".bin"}
    for client_name, content_type, refused in (
        ("payroll.pdf.exe", "application/pdf", ".exe"),
        ("../../startup.bat", "text/plain", ".bat"),
        ("script.ps1", "application/octet-stream", ".ps1"),
        ("macro.docm", "application/x-msdownload", ".docm"),
    ):
        record = _upload(host, bundle, "alice", b"x", filename=client_name, content_type=content_type)
        suffix = Path(record["stored_name"]).suffix
        assert suffix in allowed, client_name
        assert suffix != refused
        assert not set(record["name"]) & {"/", "\\"}
        assert not record["name"].endswith(refused)


def test_upload_falls_back_to_the_declared_type(host: Any, bundle: Any) -> None:
    record = _upload(host, bundle, "alice", b"\x89PNG", filename="clipboard", content_type="image/png")
    assert record["stored_name"].endswith(".png")
    assert record["name"] == "clipboard.png"


def test_attachments_list_download_and_delete(host: Any, bundle: Any) -> None:
    record = _upload(host, bundle, "alice", b"hello", filename="notes.txt", content_type="text/plain")

    listed = _body(_call(host, bundle, "attachments/list", username="alice"))
    assert [item["id"] for item in listed] == [record["id"]]
    # One user's uploads are invisible to another.
    assert _body(_call(host, bundle, "attachments/list", username="bob")) == []

    served = _call(host, bundle, "attachments/file", username="alice", query={"id": record["id"]})
    stored_path = Path(served.path)
    assert stored_path.read_bytes() == b"hello"
    assert served.media_type == "text/plain"

    missing = _call(host, bundle, "attachments/file", username="bob", query={"id": record["id"]})
    assert _body(missing)["message"] == "attachment_not_found"
    denied = _call(host, bundle, "attachments/delete", "DELETE", username="bob", query={"id": record["id"]})
    assert _body(denied)["message"] == "attachment_not_found"

    removed = _call(host, bundle, "attachments/delete", "DELETE", username="alice", query={"id": record["id"]})
    assert _body(removed)["name"] == "notes.txt"
    # Both halves go: the index record and the bytes behind it.
    assert _body(_call(host, bundle, "attachments/list", username="alice")) == []
    assert not stored_path.exists()

    gone = _call(host, bundle, "attachments/delete", "DELETE", username="alice", query={"id": record["id"]})
    assert _body(gone)["message"] == "attachment_not_found"


def test_prune_removes_attachment_files_too(host: Any, bundle: Any) -> None:
    """An index pruned without its files leaves bytes nothing can reach."""
    paths = []
    for index in range(3):
        record = _upload(host, bundle, "alice", f"file-{index}".encode(), filename=f"f{index}.txt")
        served = _call(host, bundle, "attachments/file", username="alice", query={"id": record["id"]})
        paths.append(Path(served.path))

    bundle.plugin.config["notes"]["keep_last"] = 1
    result = _body(_call(host, bundle, "actions/prune", "POST", username="alice"))

    assert result["removed_attachments"] == PRUNED_ATTACHMENTS
    # Records are newest-first, so keep_last=1 keeps the last upload.
    assert [path.exists() for path in paths] == [False, False, True]


def test_run_tool_refuses_tools_that_are_not_ours(host: Any, bundle: Any) -> None:
    """A page endpoint must not become a way to call arbitrary tools by name."""
    refused = _call(host, bundle, "run-tool", "POST", username="alice", body={"tool": "shell_tool"})
    assert _body(refused)["message"] == "tool_not_allowed"

    allowed = _call(host, bundle, "run-tool", "POST", username="alice", body={"tool": "template_status_tool"})
    assert _body(allowed)["result"]["plugin"] == "plugin-template"


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


