"""Web endpoints - everything the dashboard page and the declarative panel call.

Each endpoint is published under this plugin's own namespace:

    /api/v1/plugins/extensions/plugin-template/<endpoint>

You never write that prefix and you never mount a FastAPI route. Routes are
resolved at request time, which is what lets a hot-reloaded plugin change its
endpoints without a restart. Authentication is the host's; every handler
already knows who is calling through `request.username`.

Two registration styles, both used below:

* `web.register_web_api(endpoint, handler, methods, description)` inside
  `register_web_apis` - needed when the handler closes over `plugin`,
  `runtime_context` or shared state.
* `@plugin_web_api(endpoint, methods=...)` on a module-level function - shorter,
  but the function gets nothing except its request.

A handler may return a dict (serialised as JSON), or one of the response
helpers for anything else. Sync and async handlers both work.

Failures return a stable snake_case code, never a sentence: the page renders
the text, in whichever language its reader is using, and a message written
here would be stuck in one language. Provider error text is logged, never
returned - it can carry server paths or key fragments.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from extension.plugin_web import (
    bytes_response,
    error_response,
    file_response,
    json_response,
    plugin_web_api,
    stream_response,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import ModuleType

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 1 * 1024 * 1024
STREAM_TICKS = 30


def _load_local_module(filename: str) -> ModuleType:
    """Import a sibling module by path, exactly as tools.py does; see the note there."""
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"plugin_template_web_{path.stem}", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable for a shipped file
        msg = f"cannot load {filename}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_store = _load_local_module("store.py")


# ---------------------------------------------------------------------------
# Decorator style: no closure, so nothing but the request is available.
# ---------------------------------------------------------------------------


@plugin_web_api("ping", methods=("GET",), description="Liveness probe for the console page")
def ping(request: Any) -> dict[str, Any]:
    """Answer a liveness probe. Synchronous: the host awaits only what is awaitable."""
    return {
        "status": "success",
        "pong": True,
        # Who is calling, established by the host's auth, not by the page.
        "user": request.username,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Closure style: everything that needs plugin config or storage.
# ---------------------------------------------------------------------------


def register_web_apis(web: Any, plugin: Any, runtime_context: dict[str, Any]) -> None:  # noqa: PLR0915
    """Publish this plugin's HTTP surface.

    `web` is a facade bound to this plugin: it can only register under this
    plugin's namespace and cannot reach the global route table.
    """
    storage = runtime_context.get("storage")
    # The host's tool executor. Running a tool through it from a page gets the
    # authorisation checks, the approval gate, retries and tracing - and costs
    # no LLM turn. Calling a tool object directly skips all of that.
    executor = runtime_context.get("tool_executor")

    def settings() -> dict[str, Any]:
        section = (plugin.config or {}).get("notes")
        return section if isinstance(section, dict) else {}

    def store() -> Any:
        return _store.NoteStore(storage)

    def unavailable() -> Any:
        return error_response("storage_unavailable", 503)

    # -- panel sources: shapes the widgets expect ---------------------------

    # `stat` widgets read one field out of this body via source.field.
    async def stats(request: Any) -> Any:
        if storage is None:
            return unavailable()
        ledger = store()
        return {
            "notes": ledger.count(request.username),
            "attachments": attachments().count(request.username),
            "hint": f"retaining {settings().get('keep_last', 50)}",
            "backend": ledger.backend,
            "version": plugin.version,
        }

    # `key-value` renders every entry of a flat object.
    async def summary(request: Any) -> Any:
        _ = request
        return {
            "plugin": plugin.name,
            "version": plugin.version,
            "runtime_context": ", ".join(sorted(runtime_context)),
            "storage_backend": store().backend,
            "tool_executor": "available" if executor is not None else "absent",
        }

    # `table` wants an array of objects whose keys match the declared columns.
    async def recent(request: Any) -> Any:
        if storage is None:
            return unavailable()
        limit = int(request.query.get("limit") or 10)
        return [
            {"title": note["title"], "tag": note["tag"], "created": note["created"], "id": note["id"]}
            for note in store().recent(request.username, limit)
        ]

    # Both chart widgets read the same array: one x_field plus one series.
    async def series(request: Any) -> Any:
        if storage is None:
            return unavailable()
        return store().daily(request.username, int(request.query.get("days") or 7))

    # -- CRUD, one HTTP method each ----------------------------------------

    async def create_note(request: Any) -> Any:
        if storage is None:
            return unavailable()
        body = await request.json({})
        title = str(body.get("title") or "").strip()
        text = str(body.get("body") or "").strip()
        if not title and not text:
            return error_response("missing_text", 400)
        note = store().add(
            request.username,
            title or text,
            text or title,
            str(body.get("tag") or settings().get("default_tag", "general")),
        )
        return json_response({"status": "success", "note": note}, 201)

    async def update_note(request: Any) -> Any:
        if storage is None:
            return unavailable()
        body = await request.json({})
        note_id = str(body.get("id") or "").strip()
        if not note_id:
            return error_response("missing_id", 400)
        # Scoped to the caller, so one user cannot edit another's note by id.
        updated = store().update(request.username, note_id, body)
        if updated is None:
            return error_response("note_not_found", 404)
        return {"status": "success", "note": updated}

    async def delete_note(request: Any) -> Any:
        if storage is None:
            return unavailable()
        note_id = str(request.query.get("id") or "").strip()
        if not note_id:
            return error_response("missing_id", 400)
        if not store().delete(request.username, note_id):
            return error_response("note_not_found", 404)
        return {"status": "success", "id": note_id}

    # -- attachments: upload, list, download, delete -------------------------

    def attachments() -> Any:
        return _store.AttachmentStore(storage)

    async def attach(request: Any) -> Any:
        if storage is None:
            return unavailable()
        # The page bridge's upload() sends the file under the field `file`.
        files = await request.files()
        upload = files.get("file")
        if upload is None:
            return error_response("missing_file", 400)
        # Read one byte past the cap rather than the whole body: a plain read()
        # of an oversized upload has already cost the memory by the time you
        # check its length. The page checks the size too, but the page is
        # attacker-controlled code, so this check is the one that counts.
        raw = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            return error_response("file_too_large", 413)
        if not raw:
            return error_response("empty_file", 400)
        form = await request.form()
        # The file keeps its original name and format, but neither is taken as
        # sent: the extension is vetted against an allow-list and the stem is
        # sanitised - see extension_for() and safe_stem() in store.py.
        record = attachments().add(
            request.username,
            getattr(upload, "filename", "") or str(form.get("filename") or ""),
            raw,
            getattr(upload, "content_type", "") or "",
        )
        return json_response({"status": "success", "attachment": record, "note": str(form.get("note") or "")}, 201)

    async def list_attachments(request: Any) -> Any:
        if storage is None:
            return unavailable()
        return attachments().recent(request.username, int(request.query.get("limit") or 20))

    async def attachment_file(request: Any) -> Any:
        """Serve one attachment back, under its original name and type."""
        if storage is None:
            return unavailable()
        ledger = attachments()
        record = ledger.get(request.username, str(request.query.get("id") or ""))
        if record is None:
            return error_response("attachment_not_found", 404)
        path = ledger.path_for(request.username, record)
        if path is None:
            # Indexed but missing on disk. Report it rather than 500ing.
            return error_response("attachment_file_missing", 410)
        # file_response sets the download name. Take it from the index, not from
        # the path: old records still on disk carry a generated stored name.
        return file_response(path, filename=str(record["name"]), media_type=str(record["content_type"]))

    async def delete_attachment(request: Any) -> Any:
        """Delete one attachment, index record and file together."""
        if storage is None:
            return unavailable()
        record_id = str(request.query.get("id") or "").strip()
        if not record_id:
            return error_response("missing_id", 400)
        # Scoped to the caller, so an id guessed from someone else's upload
        # finds nothing rather than deleting their file.
        removed = attachments().remove(request.username, record_id)
        if removed is None:
            return error_response("attachment_not_found", 404)
        return {"status": "success", "id": record_id, "name": removed["name"]}

    # -- binaries: the page cannot fetch these itself -----------------------

    async def badge(request: Any) -> Any:
        """Render an SVG the page shows via bridge.dataUrl().

        The page's CSP sets `connect-src 'none'` and an <img> cannot carry a
        bearer token, so the host fetches the bytes and hands the page back a
        data: URL. Prefer a thumbnail: the bytes cross the bridge as a string.
        """
        if storage is None:
            return unavailable()
        count = store().count(request.username)
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="72">'
            '<rect width="240" height="72" rx="10" fill="#2f6feb"/>'
            f'<text x="120" y="44" font-family="sans-serif" font-size="26" fill="#fff" '
            f'text-anchor="middle">{count} notes</text></svg>'
        )
        return bytes_response(svg.encode("utf-8"), "image/svg+xml")

    async def export_notes(request: Any) -> Any:
        """Serve a file the page saves via bridge.download().

        The sandbox blocks any download the page starts itself, so the host has
        to be the one to save it.
        """
        if storage is None:
            return unavailable()
        payload = json.dumps(store().all_for(request.username), ensure_ascii=False, indent=2)
        return bytes_response(payload.encode("utf-8"), "application/json")

    # -- SSE: the log-stream widget and bridge.subscribeSSE() ---------------

    async def events(request: Any) -> Any:
        """Emit `data: <line>` frames.

        Every frame must end with a blank line. Bound the stream: an endless
        generator holds a worker for as long as the tab stays open.
        """

        async def generate() -> AsyncIterator[str]:
            for tick in range(STREAM_TICKS):
                count = store().count(request.username) if storage is not None else 0
                stamp = datetime.now(UTC).strftime("%H:%M:%S")
                yield f"data: [{stamp}] tick {tick + 1}/{STREAM_TICKS} - {count} note(s)\n\n"
                await asyncio.sleep(1)

        return stream_response(generate())

    # -- action and form widgets -------------------------------------------

    async def prune(request: Any) -> Any:
        """Backs the `action` widget. The host POSTs with no body."""
        if storage is None:
            return unavailable()
        keep_last = int(settings().get("keep_last", 50))
        dropped_notes = store().prune(request.username, keep_last)
        # Attachments have a file behind each record, so pruning the index is
        # only half the job - the bytes have to go too, or the plugin's
        # directory grows forever with files nothing can reach.
        ledger = attachments()
        dropped_files = ledger.prune(request.username, keep_last)
        ledger.discard_files(request.username, dropped_files)
        return {
            "status": "success",
            "removed": len(dropped_notes),
            "removed_attachments": len(dropped_files),
        }

    # -- running a tool from the page ---------------------------------------

    async def run_tool(request: Any) -> Any:
        if executor is None:
            return error_response("executor_unavailable", 503)
        body = await request.json({})
        tool = str(body.get("tool") or "template_status_tool")
        if not tool.startswith("template_"):
            # Only ever dispatch to your own tools. A page must not become a
            # way to call arbitrary tools by name.
            return error_response("tool_not_allowed", 403)
        try:
            result = await asyncio.wait_for(
                executor.execute(
                    tool,
                    {"actor_id": request.username, "text": str(body.get("text") or "")},
                    trace_id=f"plugin-page:{plugin.name}:{request.username}",
                ),
                timeout=60,
            )
        except TimeoutError:
            return error_response("tool_timeout", 504)
        except RuntimeError as exc:
            logger.warning("template plugin tool call failed: %s", exc)
            return error_response("tool_failed", 502)
        return {"status": "success", "result": result if isinstance(result, dict) else {"value": str(result)}}

    # -- publication --------------------------------------------------------
    # Endpoints are relative. Methods default to ("GET",) when omitted.

    web.register_web_api("stats", stats, ("GET",), "Panel stat tiles")
    web.register_web_api("summary", summary, ("GET",), "Runtime context key-value panel")
    web.register_web_api("recent", recent, ("GET",), "Recent notes table")
    web.register_web_api("series", series, ("GET",), "Daily note counts for the charts")
    web.register_web_api("notes", create_note, ("POST",), "Create one note")
    web.register_web_api("notes/update", update_note, ("PUT",), "Update one note")
    web.register_web_api("notes/delete", delete_note, ("DELETE",), "Delete one note")
    web.register_web_api("attachments", attach, ("POST",), "Upload one file, keeping its format")
    web.register_web_api("attachments/list", list_attachments, ("GET",), "List this user's uploads")
    web.register_web_api("attachments/file", attachment_file, ("GET",), "Download one upload")
    web.register_web_api("attachments/delete", delete_attachment, ("DELETE",), "Delete one upload")
    web.register_web_api("badge", badge, ("GET",), "SVG badge for bridge.dataUrl")
    web.register_web_api("export", export_notes, ("GET",), "JSON export for bridge.download")
    web.register_web_api("events", events, ("GET",), "Server-sent activity stream")
    web.register_web_api("actions/prune", prune, ("POST",), "Prune notes beyond the retention limit")
    web.register_web_api("run-tool", run_tool, ("POST",), "Run one of this plugin's tools")
