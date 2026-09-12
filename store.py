"""The template's tiny notes store - and a worked example of `runtime_context["storage"]`.

This is NOT one of the four module names the host imports (`hooks.py`,
`tools.py`, `subagents.py`, `web.py`). Extra modules like this one are loaded
by file path - see `_load_local_module` in tools.py - because the plugin
directory is never added to `sys.path`, so `import store` would raise.

Everything about *where* data lives is the host's business. The plugin is
handed a storage object whose scope was already bound to this plugin's
identity, so no method here passes a plugin name, a directory constant or a
connection string. Two backends are used:

* Redis, when `storage.client()` returns a client. Keys are namespaced by
  `storage.key(...)`, so one plugin cannot read another's keys.
* A JSON file under `storage.dir(...)`, as the fallback when Redis is down.
  A real plugin may prefer to fail loudly instead; the template degrades so it
  stays runnable on a machine with no Redis.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

MAX_TITLE = 120
MAX_BODY = 4000
MAX_FILENAME = 120
MAX_EXTENSION = 12

# Extensions this plugin is willing to keep, mapped to the type it will serve
# them back as. An allow-list, not a deny-list: the stored name ends up in a
# Content-Disposition header and, on Windows, in a real filename, so ".ps1",
# ".lnk" and friends should never survive a round trip through a plugin.
# Widen this for your own plugin - but widen it deliberately, one entry at a
# time, and never by accepting whatever the client sent.
ALLOWED_EXTENSIONS = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".log": "text/plain",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".zip": "application/zip",
}
# Used when the client's name carries no extension we recognise.
FALLBACK_EXTENSION = ".bin"
FALLBACK_TYPE = "application/octet-stream"
# The reverse map, for a client that sent a usable MIME type but a bare name.
_TYPE_TO_EXTENSION = {
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "application/zip": ".zip",
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ ()\-一-鿿]+")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def extension_for(client_name: Any, content_type: Any) -> str:
    """Decide the extension to keep, from an untrusted name and MIME type.

    "Preserve the original format" cannot mean "use whatever the client said".
    The client is a web page, so both inputs are attacker-controlled; a name
    like `report.pdf.exe`, or one carrying path separators, must not survive.
    Only a known extension is kept, and only the last one.
    """
    suffix = PurePosixPath(str(client_name or "").replace("\\", "/")).suffix.lower()[:MAX_EXTENSION]
    if suffix in ALLOWED_EXTENSIONS:
        return suffix
    declared = str(content_type or "").split(";")[0].strip().lower()
    return _TYPE_TO_EXTENSION.get(declared, FALLBACK_EXTENSION)


def content_type_for(suffix: str, declared: Any) -> str:
    """Serve the type the extension implies, not the one the client claimed.

    Echoing a client-supplied Content-Type back on download is how a text file
    gets served as HTML and runs as script; the extension is the thing that was
    actually vetted, so that is what decides.
    """
    _ = declared
    return ALLOWED_EXTENSIONS.get(suffix, FALLBACK_TYPE)


def display_name(client_name: Any, suffix: str) -> str:
    """Build a readable original name, safe for a header or a download prompt.

    Kept as data next to the file, never used as the file's own name on disk.
    """
    raw = str(client_name or "").replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    stem = _SAFE_NAME.sub("_", PurePosixPath(raw).stem).strip(" .")[:MAX_FILENAME]
    return f"{stem or 'upload'}{suffix}"


class _UserList:
    """One named, per-user list of records, on whichever backend is up.

    Notes and attachments are both this shape, so the persistence lives here
    once and each store below just says which collection it wants.
    """

    def __init__(self, storage: Any | None, collection: str) -> None:
        self._storage = storage
        self._collection = collection

    # -- backend selection ---------------------------------------------------

    @property
    def backend(self) -> str:
        """Which backend a call would use right now: redis, file, or none."""
        if self._storage is None:
            return "none"
        # `available()` lazily opens the shared client and pings it; the host
        # owns the retry and idle-health-check policy, so there is nothing to
        # catch here.
        return "redis" if self._storage.available() else "file"

    def _client(self) -> Any | None:
        return self._storage.client() if self._storage is not None else None

    def _redis_key(self, actor: str) -> str:
        # `storage.key(...)` prefixes with this plugin's private namespace.
        # Segments are sanitised, so a hostile actor id cannot escape it.
        return self._storage.key(self._collection, actor or "unknown")

    def _file_path(self) -> Any:
        # `storage.path(...)` is the file twin of `storage.dir(...)`: parents
        # are created, the file itself is not.
        return self._storage.path(f"{self._collection}.json")

    # -- reads ---------------------------------------------------------------

    def all_for(self, actor: str) -> list[dict[str, Any]]:
        """Every record this user owns, newest first."""
        if self._storage is None:
            return []
        client = self._client()
        if client is not None:
            raw = client.lrange(self._redis_key(actor), 0, -1)
            return [json.loads(item) for item in raw]
        return self._read_file().get(actor or "unknown", [])

    def recent(self, actor: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.all_for(actor)[: max(1, min(int(limit), 200))]

    def count(self, actor: str) -> int:
        return len(self.all_for(actor))

    def get(self, actor: str, record_id: str) -> dict[str, Any] | None:
        return next((item for item in self.all_for(actor) if item["id"] == record_id), None)

    # -- writes --------------------------------------------------------------

    def delete(self, actor: str, record_id: str) -> bool:
        before = self.count(actor)
        self._mutate(actor, lambda items: [item for item in items if item["id"] != record_id])
        return self.count(actor) < before

    def prune(self, actor: str, keep_last: int) -> list[dict[str, Any]]:
        """Trim to the newest `keep_last`, returning what was removed.

        The removed records come back rather than a count, because an
        attachment also has a file on disk that has to go with it.
        """
        keep = max(0, int(keep_last))
        current = self.all_for(actor)
        if len(current) <= keep:
            return []
        self._mutate(actor, lambda items: items[:keep])
        return current[keep:]

    # -- one place that knows how a write reaches each backend ----------------

    def _mutate(self, actor: str, transform: Any) -> None:
        if self._storage is None:
            return
        client = self._client()
        if client is not None:
            key = self._redis_key(actor)
            # A pipeline keeps the rewrite atomic against concurrent readers.
            notes = transform([json.loads(item) for item in client.lrange(key, 0, -1)])
            pipeline = client.pipeline()
            pipeline.delete(key)
            if notes:
                pipeline.rpush(key, *[json.dumps(note, ensure_ascii=False) for note in notes])
            pipeline.execute()
            return
        data = self._read_file()
        data[actor or "unknown"] = transform(data.get(actor or "unknown", []))
        self._write_file(data)

    def _read_file(self) -> dict[str, list[dict[str, Any]]]:
        path = self._file_path()
        if not path.is_file():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt fallback file must not take the plugin down; the
            # authoritative copy is Redis whenever Redis is up.
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _write_file(self, data: dict[str, list[dict[str, Any]]]) -> None:
        path = self._file_path()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)


class NoteStore(_UserList):
    """The notes a user has written."""

    def __init__(self, storage: Any | None) -> None:
        super().__init__(storage, "notes")

    def daily(self, actor: str, days: int = 7) -> list[dict[str, Any]]:
        """Count notes per UTC day, zero-filled, for the chart widgets."""
        span = max(1, min(int(days), 90))
        tally = Counter(str(note.get("created", ""))[:10] for note in self.all_for(actor))
        today = datetime.now(UTC).date()
        return [
            {"day": str(day), "notes": tally.get(str(day), 0)}
            for day in (today - timedelta(days=offset) for offset in range(span - 1, -1, -1))
        ]

    def add(self, actor: str, title: str, body: str, tag: str) -> dict[str, Any]:
        note = {
            "id": uuid.uuid4().hex[:12],
            "title": _clean(title, MAX_TITLE) or "untitled",
            "body": _clean(body, MAX_BODY),
            "tag": _clean(tag, 32) or "general",
            "created": _now(),
        }
        self._mutate(actor, lambda notes: [note, *notes])
        return note

    def update(self, actor: str, note_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        updated: dict[str, Any] | None = None

        def apply(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
            nonlocal updated
            result = []
            for note in notes:
                if note["id"] != note_id:
                    result.append(note)
                    continue
                updated = {
                    **note,
                    "title": _clean(fields.get("title", note["title"]), MAX_TITLE) or note["title"],
                    "body": _clean(fields.get("body", note["body"]), MAX_BODY),
                    "tag": _clean(fields.get("tag", note["tag"]), 32) or note["tag"],
                }
                result.append(updated)
            return result

        self._mutate(actor, apply)
        return updated


class AttachmentStore(_UserList):
    """Files a user uploaded: bytes on disk, one index record each.

    Two separate things have to stay in step - a file under the plugin's own
    directory and a record in this index. Every write below does both, because
    an orphaned file is invisible and undeletable through the UI, and an
    orphaned record is a download that 404s.
    """

    def __init__(self, storage: Any | None) -> None:
        super().__init__(storage, "attachments")

    def directory(self, actor: str) -> Any:
        # One directory per user. `storage.dir()` sanitises each segment, so a
        # hostile actor id cannot climb out of the plugin's own tree.
        return self._storage.dir("uploads", actor or "unknown")

    def add(self, actor: str, client_name: str, payload: bytes, content_type: str) -> dict[str, Any]:
        """Store the bytes and index them, keeping the file's original format."""
        record_id = uuid.uuid4().hex[:12]
        suffix = extension_for(client_name, content_type)
        # The stored name is generated here; only the extension is taken from
        # the caller, and only after `extension_for` has vetted it. The name the
        # user typed is kept as data, for display and for the download filename.
        stored_name = f"{record_id}{suffix}"
        (self.directory(actor) / stored_name).write_bytes(payload)
        record = {
            "id": record_id,
            "name": display_name(client_name, suffix),
            "stored_name": stored_name,
            "content_type": content_type_for(suffix, content_type),
            "bytes": len(payload),
            "created": _now(),
        }
        self._mutate(actor, lambda items: [record, *items])
        return record

    def path_for(self, actor: str, record: dict[str, Any]) -> Any | None:
        """Resolve a record's file, confirming it is really one of ours."""
        if self._storage is None:
            return None
        # Ask the host rather than trusting the index: a record could be stale,
        # hand-edited in Redis, or point outside the plugin's tree.
        return self._storage.resolve(self.directory(actor) / str(record.get("stored_name", "")))

    def remove(self, actor: str, record_id: str) -> dict[str, Any] | None:
        """Delete one attachment: the index record and the file behind it."""
        record = self.get(actor, record_id)
        if record is None:
            return None
        self.discard_files(actor, [record])
        self.delete(actor, record_id)
        return record

    def discard_files(self, actor: str, records: list[dict[str, Any]]) -> int:
        """Unlink the files of records that are being removed from the index."""
        removed = 0
        for record in records:
            path = self.path_for(actor, record)
            if path is None:
                continue
            try:
                path.unlink()
            except OSError:
                # A file already gone is the desired end state; anything else
                # here is a disk problem the index should not be held back by.
                continue
            removed += 1
        return removed
