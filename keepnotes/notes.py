import math
import re
import sqlite3
import time
from datetime import datetime, timezone

from .db import Database
from .markdown_render import render_markdown

BIN_DAYS = 30
SECONDS_PER_DAY = 24 * 3600
KINDS = ("text", "checklist")
COLORS = ("default", "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink", "brown", "grey")
VIEWS = ("notes", "archive", "bin", "reminders")
MAX_TITLE_LENGTH = 500
MAX_BODY_LENGTH = 100_000
MAX_ITEMS = 500
MAX_ITEM_LENGTH = 1000
MAX_TAGS = 20
MAX_TAG_LENGTH = 40
MAX_QUERY_LENGTH = 200
MAX_REORDER_IDS = 5000
MAX_TIMESTAMP_LENGTH = 40
LARGEST_DATABASE_INTEGER = 2 ** 63 - 1
SMALLEST_DATABASE_INTEGER = -(2 ** 63)
EARLIEST_TIMESTAMP = datetime(1, 1, 2, tzinfo=timezone.utc).timestamp()
LATEST_TIMESTAMP = datetime(9999, 12, 30, tzinfo=timezone.utc).timestamp()
TAG_PATTERN = re.compile(r"^\w[\w .\-]*$")
ITEM_KEYS = frozenset({"id", "text", "checked"})
NOTE_DEFAULTS = {"kind": "text", "title": "", "body": "", "color": "default", "pinned": False, "archived": False,
                 "tags": [], "items": [], "reminderAt": None}
COLUMN_FOR_FIELD = {"kind": "kind", "title": "title", "body": "body", "color": "color", "pinned": "pinned",
                    "archived": "archived", "reminderAt": "reminder_at"}
LIKE_ESCAPE = "\\"
VIEW_FILTERS = {
    "notes": "n.trashed_at IS NULL AND n.archived = 0",
    "archive": "n.trashed_at IS NULL AND n.archived = 1",
    "bin": "n.trashed_at IS NOT NULL",
    "reminders": "n.trashed_at IS NULL AND n.reminder_at IS NOT NULL",
}
VIEW_ORDER = {
    "notes": "n.pinned DESC, n.position, n.id DESC",
    "archive": "n.pinned DESC, n.position, n.id DESC",
    "bin": "n.trashed_at DESC, n.id DESC",
    "reminders": "n.reminder_at, n.id",
}
SEARCH_FILTER = (
    "(n.title LIKE :pattern ESCAPE '\\' OR n.body LIKE :pattern ESCAPE '\\'"
    " OR EXISTS (SELECT 1 FROM checklist_items i WHERE i.note_id = n.id AND i.text LIKE :pattern ESCAPE '\\')"
    " OR EXISTS (SELECT 1 FROM note_tags nt JOIN tags t ON t.id = nt.tag_id WHERE nt.note_id = n.id AND t.name LIKE :pattern ESCAPE '\\'))"
)
TAG_FILTER = "EXISTS (SELECT 1 FROM note_tags nt JOIN tags t ON t.id = nt.tag_id WHERE nt.note_id = n.id AND t.name = :tag)"


class NoteError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def is_showable_timestamp(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and EARLIEST_TIMESTAMP <= value <= LATEST_TIMESTAMP


def to_iso(timestamp: float | None) -> str | None:
    if not is_showable_timestamp(timestamp):
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value, field: str) -> float:
    if not isinstance(value, str) or len(value) > MAX_TIMESTAMP_LENGTH:
        raise NoteError(f"{field} must be a date and time like 2027-01-31T09:00:00Z")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as error:
        raise NoteError(f"{field} must be a date and time like 2027-01-31T09:00:00Z") from error
    if moment.tzinfo is None:
        raise NoteError(f"{field} needs a time zone, like 2027-01-31T09:00:00Z")
    timestamp = moment.timestamp()
    if not is_showable_timestamp(timestamp):
        raise NoteError(f"{field} must fall between the years 1 and 9999 in UTC")
    return timestamp


def _clean_text(value, name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise NoteError(f"{name} must be text")
    if len(value) > limit:
        raise NoteError(f"{name} is too long (at most {limit} characters)")
    return value


def _clean_flag(value, name: str) -> bool:
    if not isinstance(value, bool):
        raise NoteError(f"{name} must be true or false")
    return value


def _clean_choice(value, name: str, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise NoteError(f"{name} must be one of {', '.join(choices)}")
    return value


def _clean_item(item) -> dict:
    if not isinstance(item, dict) or not set(item) <= ITEM_KEYS or "text" not in item:
        raise NoteError("each checklist item needs text and may say whether it is checked")
    text = _clean_text(item["text"], "a checklist item", MAX_ITEM_LENGTH).strip()
    if not text:
        raise NoteError("a checklist item cannot be empty")
    return {"text": text, "checked": _clean_flag(item.get("checked", False), "checked")}


def _clean_items(value) -> list[dict]:
    if not isinstance(value, list):
        raise NoteError("items must be a list")
    if len(value) > MAX_ITEMS:
        raise NoteError(f"a checklist can hold at most {MAX_ITEMS} items")
    return [_clean_item(item) for item in value]


def _clean_tag(value) -> str:
    name = _clean_text(value, "a tag", MAX_TAG_LENGTH).strip()
    if not TAG_PATTERN.match(name):
        raise NoteError("tags may use letters, numbers, spaces, dots, dashes and underscores")
    return name


def _clean_tags(value) -> list[str]:
    if not isinstance(value, list):
        raise NoteError("tags must be a list")
    unique: dict[str, str] = {}
    for raw in value:
        name = _clean_tag(raw)
        unique.setdefault(name.lower(), name)
    if len(unique) > MAX_TAGS:
        raise NoteError(f"a note can have at most {MAX_TAGS} tags")
    return list(unique.values())


def _clean_reminder(value) -> float | None:
    return None if value is None else parse_iso(value, "reminderAt")


FIELD_CLEANERS = {
    "kind": lambda value: _clean_choice(value, "kind", KINDS),
    "title": lambda value: _clean_text(value, "the title", MAX_TITLE_LENGTH),
    "body": lambda value: _clean_text(value, "the note", MAX_BODY_LENGTH),
    "color": lambda value: _clean_choice(value, "colour", COLORS),
    "pinned": lambda value: _clean_flag(value, "pinned"),
    "archived": lambda value: _clean_flag(value, "archived"),
    "tags": _clean_tags,
    "items": _clean_items,
    "reminderAt": _clean_reminder,
}


def clean_fields(data) -> dict:
    if not isinstance(data, dict):
        raise NoteError("send a JSON object")
    unknown = sorted(set(data) - set(FIELD_CLEANERS))
    if unknown:
        raise NoteError(f"unknown field: {', '.join(unknown)}")
    fields = {name: FIELD_CLEANERS[name](value) for name, value in data.items()}
    if fields.get("pinned") and fields.get("archived"):
        raise NoteError("a note cannot be pinned and archived at once")
    if fields.get("pinned"):
        fields["archived"] = False
    if fields.get("archived"):
        fields["pinned"] = False
    return fields


def _has_content(fields: dict) -> bool:
    return bool(fields["title"].strip() or fields["body"].strip() or fields["items"])


def _like_pattern(query: str) -> str:
    escaped = query.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2).replace("%", LIKE_ESCAPE + "%").replace("_", LIKE_ESCAPE + "_")
    return f"%{escaped}%"


def _is_note_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and SMALLEST_DATABASE_INTEGER <= value <= LARGEST_DATABASE_INTEGER


class NoteStore:
    def __init__(self, database: Database, clock=time.time, bin_days: int = BIN_DAYS):
        self.database = database
        self.clock = clock
        self.bin_seconds = bin_days * SECONDS_PER_DAY

    def create(self, data) -> dict:
        fields = {**NOTE_DEFAULTS, **clean_fields(data)}
        if not _has_content(fields):
            raise NoteError("a note needs a title, some text or a checklist item")
        with self.database.connect() as connection:
            current = self.clock()
            position = connection.execute("SELECT COALESCE(MIN(position), 0) - 1 FROM notes").fetchone()[0]
            note_id = self._insert(connection, fields, position, created_at=current, updated_at=current, trashed_at=None)
            return self._load(connection, note_id)

    def insert_imported(self, notes: list[dict]) -> int:
        with self.database.connect() as connection:
            start = connection.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM notes").fetchone()[0]
            current = self.clock()
            for offset, note in enumerate(notes):
                fields = {**NOTE_DEFAULTS, **note["fields"]}
                self._insert(connection, fields, start + offset, created_at=note["createdAt"] or current,
                             updated_at=note["updatedAt"] or current, trashed_at=note["trashedAt"])
        return len(notes)

    def get(self, note_id: int) -> dict:
        with self.database.connect() as connection:
            return self._load(connection, note_id)

    def update(self, note_id: int, data) -> dict:
        fields = clean_fields(data)
        with self.database.connect() as connection:
            self._editable_row(connection, note_id)
            columns = [name for name in COLUMN_FOR_FIELD if name in fields]
            assignments = "".join(f"{COLUMN_FOR_FIELD[name]} = ?, " for name in columns)
            values = [fields[name] for name in columns]
            connection.execute(f"UPDATE notes SET {assignments}updated_at = ? WHERE id = ?", (*values, self.clock(), note_id))
            if "items" in fields:
                connection.execute("DELETE FROM checklist_items WHERE note_id = ?", (note_id,))
                self._write_items(connection, note_id, fields["items"])
            if "tags" in fields:
                self._write_tags(connection, note_id, fields["tags"])
                self._prune_tags(connection)
            return self._load(connection, note_id)

    def set_item_checked(self, note_id: int, item_id: int, checked) -> dict:
        ticked = _clean_flag(checked, "checked")
        with self.database.connect() as connection:
            self._editable_row(connection, note_id)
            changed = connection.execute("UPDATE checklist_items SET checked = ? WHERE id = ? AND note_id = ?",
                                         (ticked, item_id, note_id)).rowcount
            if not changed:
                raise NoteError("no such checklist item", status=404)
            connection.execute("UPDATE notes SET updated_at = ? WHERE id = ?", (self.clock(), note_id))
            return self._load(connection, note_id)

    def trash(self, note_id: int) -> dict:
        with self.database.connect() as connection:
            self._row(connection, note_id)
            connection.execute("UPDATE notes SET trashed_at = COALESCE(trashed_at, ?) WHERE id = ?", (self.clock(), note_id))
            return self._load(connection, note_id)

    def restore(self, note_id: int) -> dict:
        with self.database.connect() as connection:
            self._row(connection, note_id)
            connection.execute("UPDATE notes SET trashed_at = NULL WHERE id = ?", (note_id,))
            return self._load(connection, note_id)

    def delete_forever(self, note_id: int) -> None:
        with self.database.connect() as connection:
            if self._row(connection, note_id)["trashed_at"] is None:
                raise NoteError("move the note to the bin first", status=409)
            connection.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            self._prune_tags(connection)

    def empty_bin(self) -> int:
        with self.database.connect() as connection:
            removed = connection.execute("DELETE FROM notes WHERE trashed_at IS NOT NULL").rowcount
            self._prune_tags(connection)
        return removed

    def purge_expired(self) -> int:
        cutoff = self.clock() - self.bin_seconds
        with self.database.connect() as connection:
            removed = connection.execute("DELETE FROM notes WHERE trashed_at IS NOT NULL AND trashed_at <= ?", (cutoff,)).rowcount
            if removed:
                self._prune_tags(connection)
        return removed

    def list_notes(self, view: str = "notes", query: str = "", tag: str = "") -> list[dict]:
        if view not in VIEWS:
            raise NoteError(f"view must be one of {', '.join(VIEWS)}")
        search = _clean_text(query, "the search", MAX_QUERY_LENGTH).strip()
        tag_name = _clean_text(tag, "the tag", MAX_TAG_LENGTH).strip()
        self.purge_expired()
        conditions = [VIEW_FILTERS[view]]
        parameters: dict[str, str] = {}
        if search:
            conditions.append(SEARCH_FILTER)
            parameters["pattern"] = _like_pattern(search)
        if tag_name:
            conditions.append(TAG_FILTER)
            parameters["tag"] = tag_name
        sql = f"SELECT n.id FROM notes n WHERE {' AND '.join(conditions)} ORDER BY {VIEW_ORDER[view]}"
        with self.database.connect() as connection:
            note_ids = [row["id"] for row in connection.execute(sql, parameters)]
            return [self._load(connection, note_id) for note_id in note_ids]

    def all_notes(self) -> list[dict]:
        self.purge_expired()
        with self.database.connect() as connection:
            note_ids = [row["id"] for row in connection.execute("SELECT id FROM notes ORDER BY position, id")]
            return [self._load(connection, note_id) for note_id in note_ids]

    def due_reminder_count(self) -> int:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM notes WHERE trashed_at IS NULL AND reminder_at IS NOT NULL AND reminder_at <= ?",
                (self.clock(),)).fetchone()[0]

    def reorder(self, note_ids) -> None:
        if not isinstance(note_ids, list) or not all(_is_note_id(note_id) for note_id in note_ids):
            raise NoteError("ids must be a list of note ids")
        if len(note_ids) > MAX_REORDER_IDS:
            raise NoteError(f"at most {MAX_REORDER_IDS} notes can be reordered at once")
        if len(set(note_ids)) != len(note_ids):
            raise NoteError("each note id may appear only once")
        placeholders = ", ".join("?" for _ in note_ids)
        with self.database.connect() as connection:
            found = connection.execute(f"SELECT COUNT(*) FROM notes WHERE id IN ({placeholders})", note_ids).fetchone()[0] if note_ids else 0
            if found != len(note_ids):
                raise NoteError("one of those notes does not exist", status=404)
            connection.executemany("UPDATE notes SET position = ? WHERE id = ?", [(index, note_id) for index, note_id in enumerate(note_ids)])

    def tags(self) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT t.name, COUNT(*) AS uses FROM tags t JOIN note_tags nt ON nt.tag_id = t.id JOIN notes n ON n.id = nt.note_id"
                " WHERE n.trashed_at IS NULL GROUP BY t.id ORDER BY t.name COLLATE NOCASE").fetchall()
        return [{"name": row["name"], "count": row["uses"]} for row in rows]

    def _insert(self, connection: sqlite3.Connection, fields: dict, position: int, created_at: float, updated_at: float,
                trashed_at: float | None) -> int:
        cursor = connection.execute(
            "INSERT INTO notes (kind, title, body, color, pinned, archived, trashed_at, position, reminder_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fields["kind"], fields["title"], fields["body"], fields["color"], fields["pinned"], fields["archived"], trashed_at,
             position, fields["reminderAt"], created_at, updated_at))
        self._write_items(connection, cursor.lastrowid, fields["items"])
        self._write_tags(connection, cursor.lastrowid, fields["tags"])
        return cursor.lastrowid

    def _write_items(self, connection: sqlite3.Connection, note_id: int, items: list[dict]) -> None:
        connection.executemany("INSERT INTO checklist_items (note_id, text, checked, position) VALUES (?, ?, ?, ?)",
                               [(note_id, item["text"], item["checked"], index) for index, item in enumerate(items)])

    def _write_tags(self, connection: sqlite3.Connection, note_id: int, tags: list[str]) -> None:
        connection.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
        for name in tags:
            connection.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
            tag_id = connection.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()[0]
            connection.execute("INSERT OR IGNORE INTO note_tags (note_id, tag_id) VALUES (?, ?)", (note_id, tag_id))

    def _prune_tags(self, connection: sqlite3.Connection) -> None:
        connection.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM note_tags)")

    def _row(self, connection: sqlite3.Connection, note_id: int) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            raise NoteError("no such note", status=404)
        return row

    def _editable_row(self, connection: sqlite3.Connection, note_id: int) -> sqlite3.Row:
        row = self._row(connection, note_id)
        if row["trashed_at"] is not None:
            raise NoteError("restore the note from the bin before changing it", status=409)
        return row

    def _load(self, connection: sqlite3.Connection, note_id: int) -> dict:
        row = self._row(connection, note_id)
        items = connection.execute("SELECT id, text, checked FROM checklist_items WHERE note_id = ? ORDER BY position, id", (note_id,)).fetchall()
        tags = connection.execute("SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id = t.id WHERE nt.note_id = ? ORDER BY t.name COLLATE NOCASE",
                                  (note_id,)).fetchall()
        is_trashed = row["trashed_at"] is not None
        return {
            "id": row["id"],
            "kind": row["kind"],
            "title": row["title"],
            "body": row["body"],
            "bodyHtml": render_markdown(row["body"]),
            "items": [{"id": item["id"], "text": item["text"], "checked": bool(item["checked"])} for item in items],
            "color": row["color"],
            "pinned": bool(row["pinned"]),
            "archived": bool(row["archived"]),
            "trashed": is_trashed,
            "trashedAt": to_iso(row["trashed_at"]),
            "reminderAt": to_iso(row["reminder_at"]),
            "reminderDue": row["reminder_at"] is not None and not is_trashed and row["reminder_at"] <= self.clock(),
            "tags": [tag["name"] for tag in tags],
            "position": row["position"],
            "createdAt": to_iso(row["created_at"]),
            "updatedAt": to_iso(row["updated_at"]),
        }
