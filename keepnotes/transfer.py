from .notes import NoteError, NoteStore, clean_fields, parse_iso, to_iso

APP_NAME = "keep-notes"
EXPORT_VERSION = 1
MAX_IMPORT_NOTES = 10_000
EXPORTED_FIELDS = ("kind", "title", "body", "color", "pinned", "archived", "reminderAt", "tags", "trashedAt", "createdAt", "updatedAt")
TIMESTAMP_FIELDS = ("trashedAt", "createdAt", "updatedAt")


def _exported_note(note: dict) -> dict:
    exported = {field: note[field] for field in EXPORTED_FIELDS}
    exported["items"] = [{"text": item["text"], "checked": item["checked"]} for item in note["items"]]
    return exported


def export_notes(store: NoteStore) -> dict:
    return {
        "app": APP_NAME,
        "version": EXPORT_VERSION,
        "exportedAt": to_iso(store.clock()),
        "notes": [_exported_note(note) for note in store.all_notes()],
    }


def _optional_timestamp(note: dict, field: str) -> float | None:
    value = note.pop(field, None)
    return None if value is None else parse_iso(value, field)


def _imported_note(raw, position: int) -> dict:
    if not isinstance(raw, dict):
        raise NoteError(f"note {position} is not an object")
    note = dict(raw)
    try:
        timestamps = {field: _optional_timestamp(note, field) for field in TIMESTAMP_FIELDS}
        return {"fields": clean_fields(note), **timestamps}
    except NoteError as error:
        raise NoteError(f"note {position}: {error}") from error


def import_notes(store: NoteStore, payload) -> int:
    if not isinstance(payload, dict) or payload.get("app") != APP_NAME:
        raise NoteError("this is not a Keep Notes export")
    if payload.get("version") != EXPORT_VERSION:
        raise NoteError(f"this export is version {payload.get('version')!r}, only version {EXPORT_VERSION} can be read")
    notes = payload.get("notes")
    if not isinstance(notes, list):
        raise NoteError("the export has no list of notes")
    if len(notes) > MAX_IMPORT_NOTES:
        raise NoteError(f"an import can hold at most {MAX_IMPORT_NOTES} notes")
    cleaned = [_imported_note(raw, position) for position, raw in enumerate(notes, start=1)]
    return store.insert_imported(cleaned)
