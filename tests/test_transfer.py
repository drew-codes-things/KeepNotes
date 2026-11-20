import json

import pytest

from keepnotes.db import Database
from keepnotes.notes import NoteError, NoteStore
from keepnotes.transfer import EXPORT_VERSION, MAX_IMPORT_NOTES, export_notes, import_notes

PORTABLE_FIELDS = ("kind", "title", "body", "color", "pinned", "archived", "trashedAt", "reminderAt", "tags", "createdAt", "updatedAt")


def portable(note):
    view = {field: note[field] for field in PORTABLE_FIELDS}
    view["items"] = [(item["text"], item["checked"]) for item in note["items"]]
    return view


def everything(store):
    return [portable(note) for view in ("notes", "archive", "bin") for note in store.list_notes(view)]


def fill(store):
    store.create({"title": "pinned", "body": "**b**", "pinned": True, "color": "red", "tags": ["a", "b"]})
    store.create({"kind": "checklist", "title": "list", "items": [{"text": "x", "checked": True}, {"text": "y"}],
                  "reminderAt": "2027-02-01T10:00:00Z"})
    store.create({"title": "archived", "archived": True})
    store.trash(store.create({"title": "binned"})["id"])


def test_export_holds_every_note_with_its_details(store):
    fill(store)
    exported = export_notes(store)
    assert exported["app"] == "keep-notes"
    assert exported["version"] == EXPORT_VERSION
    assert exported["exportedAt"].endswith("Z")
    assert sorted(note["title"] for note in exported["notes"]) == ["archived", "binned", "list", "pinned"]
    checklist = next(note for note in exported["notes"] if note["title"] == "list")
    assert checklist["items"] == [{"text": "x", "checked": True}, {"text": "y", "checked": False}]
    assert "id" not in checklist
    json.dumps(exported)


def test_export_then_import_gives_the_same_notes(store, tmp_path, clock):
    fill(store)
    fresh = NoteStore(Database(tmp_path / "fresh.db"), clock=clock)
    assert import_notes(fresh, export_notes(store)) == 4
    assert sorted(everything(fresh), key=str) == sorted(everything(store), key=str)


def test_an_import_from_another_app_is_refused_by_the_starfire_notes_name(store):
    with pytest.raises(NoteError, match="this is not a Starfire Notes export"):
        import_notes(store, {"app": "something-else"})


def test_import_adds_to_what_is_already_there(store):
    store.create({"title": "existing"})
    import_notes(store, {"app": "keep-notes", "version": EXPORT_VERSION, "notes": [{"title": "imported"}]})
    assert sorted(note["title"] for note in store.list_notes()) == ["existing", "imported"]


def test_imported_notes_go_after_the_existing_ones(store):
    existing = store.create({"title": "existing"})
    import_notes(store, {"app": "keep-notes", "version": EXPORT_VERSION, "notes": [{"title": "first"}, {"title": "second"}]})
    assert [note["title"] for note in store.list_notes()] == [existing["title"], "first", "second"]


@pytest.mark.parametrize("payload", [
    [],
    {"app": "other", "version": EXPORT_VERSION, "notes": []},
    {"app": "keep-notes", "version": 99, "notes": []},
    {"app": "keep-notes", "version": EXPORT_VERSION, "notes": "x"},
    {"app": "keep-notes", "version": EXPORT_VERSION, "notes": [{"title": "ok"}, {"title": "bad", "color": "nope"}]},
    {"app": "keep-notes", "version": EXPORT_VERSION, "notes": [{"title": "ok", "createdAt": "yesterday"}]},
    {"app": "keep-notes", "version": EXPORT_VERSION, "notes": [{"title": "ok", "id": 4}]},
    {"app": "keep-notes", "version": EXPORT_VERSION, "notes": [{"title": "ok", "items": [{"text": "a", "extra": 1}]}]},
])
def test_bad_imports_are_refused_and_write_nothing(store, payload):
    with pytest.raises(NoteError) as error:
        import_notes(store, payload)
    assert error.value.status == 400
    assert everything(store) == []


def test_too_many_notes_are_refused(store):
    payload = {"app": "keep-notes", "version": EXPORT_VERSION, "notes": [{"title": "n"}] * (MAX_IMPORT_NOTES + 1)}
    with pytest.raises(NoteError):
        import_notes(store, payload)


def test_export_download_and_import_over_http(owner):
    owner.post("/api/notes", json={"title": "carry me", "tags": ["t"]})
    download = owner.get("/api/export")
    assert download.status_code == 200
    assert download.headers["Content-Disposition"].startswith("attachment; filename=keep-notes-")
    payload = download.get_json()
    imported = owner.post("/api/import", json=payload)
    assert imported.status_code == 200
    assert imported.get_json() == {"imported": 1}
    assert [note["title"] for note in owner.get("/api/notes").get_json()["notes"]] == ["carry me", "carry me"]


def test_import_over_http_needs_the_csrf_token(owner):
    response = owner.client.post("/api/import", json={"app": "keep-notes", "version": EXPORT_VERSION, "notes": [{"title": "x"}]},
                                 headers=owner.headers)
    assert response.status_code == 403


def test_bad_import_over_http_is_a_bad_request(owner):
    response = owner.post("/api/import", json={"app": "keep-notes", "version": 99, "notes": []})
    assert response.status_code == 400


OUT_OF_RANGE_MOMENTS = ("9999-12-31T23:59:59-23:59", "9999-12-31T23:59:59-12:00", "0001-01-01T00:00:00+23:59",
                        "0001-01-01T00:00:00+12:00")


@pytest.mark.parametrize("moment", OUT_OF_RANGE_MOMENTS)
@pytest.mark.parametrize("field", ("createdAt", "updatedAt", "trashedAt", "reminderAt"))
def test_dates_that_cannot_be_shown_are_refused_on_import(store, field, moment):
    payload = {"app": "keep-notes", "version": EXPORT_VERSION, "notes": [{"title": "x", field: moment}]}
    with pytest.raises(NoteError) as caught:
        import_notes(store, payload)
    assert field in str(caught.value)
    assert store.all_notes() == []
    assert export_notes(store)["notes"] == []


def test_a_stored_date_that_cannot_be_shown_does_not_break_lists_or_the_export(store, database):
    store.create({"title": "fine"})
    odd = store.create({"title": "odd", "reminderAt": "2027-02-01T10:00:00Z"})
    with database.connect() as connection:
        connection.execute("UPDATE notes SET created_at = ?, reminder_at = ? WHERE id = ?", (253402344000.0, float("inf"), odd["id"]))
    titles = {note["title"]: note for note in store.list_notes("notes")}
    assert set(titles) == {"fine", "odd"}
    assert titles["odd"]["createdAt"] is None
    assert titles["odd"]["reminderAt"] is None
    assert len(export_notes(store)["notes"]) == 2


def test_an_import_does_not_sweep_unused_tags_once_per_note(store, monkeypatch):
    sweeps = []
    real_prune = NoteStore._prune_tags
    monkeypatch.setattr(NoteStore, "_prune_tags", lambda self, connection: sweeps.append(1) or real_prune(self, connection))
    notes = [{"title": f"n{number}", "tags": [f"t{number}-{tag}" for tag in range(5)]} for number in range(50)]
    assert import_notes(store, {"app": "keep-notes", "version": EXPORT_VERSION, "notes": notes}) == 50
    assert len(sweeps) <= 1
    assert len(store.tags()) == 250


def test_tags_dropped_from_a_note_still_leave_the_tag_table(store, database):
    note = store.create({"title": "x", "tags": ["keep", "drop"]})
    store.update(note["id"], {"tags": ["keep"]})
    with database.connect() as connection:
        assert [row["name"] for row in connection.execute("SELECT name FROM tags")] == ["keep"]
