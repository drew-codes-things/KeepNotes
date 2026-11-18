import pytest

from keepnotes.notes import BIN_DAYS, NoteError

DAY = 24 * 3600


def ids(notes):
    return [note["id"] for note in notes]


def test_new_text_note_has_sensible_defaults(store):
    note = store.create({"title": "Groceries", "body": "milk"})
    assert note["kind"] == "text"
    assert note["title"] == "Groceries"
    assert note["body"] == "milk"
    assert note["color"] == "default"
    assert note["pinned"] is False
    assert note["archived"] is False
    assert note["trashed"] is False
    assert note["tags"] == []
    assert note["items"] == []
    assert note["reminderAt"] is None
    assert note["createdAt"].endswith("Z")


def test_note_body_is_rendered_and_escaped(store):
    note = store.create({"body": "**hi** <script>x</script>"})
    assert "<strong>hi</strong>" in note["bodyHtml"]
    assert "<script>" not in note["bodyHtml"]


def test_an_empty_note_is_refused(store):
    with pytest.raises(NoteError) as error:
        store.create({"title": "  ", "body": ""})
    assert error.value.status == 400


def test_checklist_keeps_item_order_and_ticks(store):
    note = store.create({"kind": "checklist", "title": "Packing", "items": [
        {"text": "passport", "checked": True}, {"text": "charger"}, {"text": "socks"}]})
    assert [item["text"] for item in note["items"]] == ["passport", "charger", "socks"]
    assert [item["checked"] for item in note["items"]] == [True, False, False]


def test_ticking_one_item_leaves_the_others(store):
    note = store.create({"kind": "checklist", "items": [{"text": "a"}, {"text": "b"}]})
    first, second = note["items"]
    updated = store.set_item_checked(note["id"], second["id"], True)
    assert [item["checked"] for item in updated["items"]] == [False, True]
    assert store.set_item_checked(note["id"], second["id"], False)["items"][1]["checked"] is False


def test_ticking_an_item_of_another_note_is_not_found(store):
    one = store.create({"kind": "checklist", "items": [{"text": "a"}]})
    two = store.create({"kind": "checklist", "items": [{"text": "b"}]})
    with pytest.raises(NoteError) as error:
        store.set_item_checked(one["id"], two["items"][0]["id"], True)
    assert error.value.status == 404


@pytest.mark.parametrize("data", [
    {"title": "x" * 501},
    {"body": "x" * 100_001},
    {"title": "x", "color": "chartreuse"},
    {"title": "x", "kind": "drawing"},
    {"title": 5},
    {"title": "x", "pinned": "yes"},
    {"kind": "checklist", "items": [{"text": ""}]},
    {"kind": "checklist", "items": [{"text": "x" * 1001}]},
    {"kind": "checklist", "items": "milk"},
    {"title": "x", "tags": ["ok", "bad,tag"]},
    {"title": "x", "tags": ["x" * 41]},
    {"title": "x", "tags": [f"t{number}" for number in range(21)]},
    {"title": "x", "reminderAt": "tomorrow"},
    {"title": "x", "reminderAt": "2027-01-01T09:00:00"},
    {"title": "x", "surprise": 1},
])
def test_bad_fields_are_refused(store, data):
    with pytest.raises(NoteError) as error:
        store.create(data)
    assert error.value.status == 400


def test_update_changes_only_what_is_sent(store, clock):
    note = store.create({"title": "Old", "body": "keep me", "color": "blue"})
    clock.advance(60)
    updated = store.update(note["id"], {"title": "New"})
    assert updated["title"] == "New"
    assert updated["body"] == "keep me"
    assert updated["color"] == "blue"
    assert updated["updatedAt"] != note["updatedAt"]


def test_update_replaces_checklist_items(store):
    note = store.create({"kind": "checklist", "items": [{"text": "a"}]})
    updated = store.update(note["id"], {"items": [{"text": "b", "checked": True}, {"text": "c"}]})
    assert [(item["text"], item["checked"]) for item in updated["items"]] == [("b", True), ("c", False)]


def test_missing_note_is_not_found(store):
    with pytest.raises(NoteError) as error:
        store.get(999)
    assert error.value.status == 404


def test_pinned_notes_come_first(store):
    first = store.create({"title": "first"})
    second = store.create({"title": "second"})
    store.update(first["id"], {"pinned": True})
    assert ids(store.list_notes())[0] == first["id"]
    assert second["id"] in ids(store.list_notes())


def test_new_notes_go_to_the_top(store):
    older = store.create({"title": "older"})
    newer = store.create({"title": "newer"})
    assert ids(store.list_notes()) == [newer["id"], older["id"]]


def test_archived_notes_leave_the_main_list_and_lose_their_pin(store):
    note = store.create({"title": "x", "pinned": True})
    archived = store.update(note["id"], {"archived": True})
    assert archived["pinned"] is False
    assert ids(store.list_notes()) == []
    assert ids(store.list_notes("archive")) == [note["id"]]


def test_pinning_an_archived_note_brings_it_back(store):
    note = store.create({"title": "x", "archived": True})
    pinned = store.update(note["id"], {"pinned": True})
    assert pinned["archived"] is False
    assert ids(store.list_notes()) == [note["id"]]


def test_binned_note_moves_to_the_bin_and_cannot_be_edited(store):
    note = store.create({"title": "x"})
    binned = store.trash(note["id"])
    assert binned["trashed"] is True
    assert binned["trashedAt"] is not None
    assert ids(store.list_notes()) == []
    assert ids(store.list_notes("bin")) == [note["id"]]
    with pytest.raises(NoteError) as error:
        store.update(note["id"], {"title": "y"})
    assert error.value.status == 409


def test_restore_takes_a_note_out_of_the_bin(store):
    note = store.create({"title": "x"})
    store.trash(note["id"])
    restored = store.restore(note["id"])
    assert restored["trashed"] is False
    assert ids(store.list_notes()) == [note["id"]]


def test_delete_forever_only_works_from_the_bin(store, database):
    note = store.create({"kind": "checklist", "items": [{"text": "a"}]})
    with pytest.raises(NoteError) as error:
        store.delete_forever(note["id"])
    assert error.value.status == 409
    store.trash(note["id"])
    store.delete_forever(note["id"])
    with pytest.raises(NoteError):
        store.get(note["id"])
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM checklist_items").fetchone()[0] == 0


def test_bin_empties_itself_after_thirty_days(store, clock):
    old = store.create({"title": "old"})
    store.trash(old["id"])
    clock.advance(2 * DAY)
    recent = store.create({"title": "recent"})
    store.trash(recent["id"])
    clock.advance((BIN_DAYS - 1) * DAY)
    assert ids(store.list_notes("bin")) == [recent["id"]]
    clock.advance(2 * DAY)
    assert ids(store.list_notes("bin")) == []
    assert BIN_DAYS == 30


def test_purge_reports_how_many_were_removed(store, clock):
    for title in ("a", "b"):
        store.trash(store.create({"title": title})["id"])
    store.create({"title": "kept"})
    clock.advance(BIN_DAYS * DAY + 1)
    assert store.purge_expired() == 2
    assert len(store.list_notes()) == 1


def test_empty_bin_removes_every_binned_note(store):
    for title in ("a", "b"):
        store.trash(store.create({"title": title})["id"])
    kept = store.create({"title": "kept"})
    assert store.empty_bin() == 2
    assert ids(store.list_notes("bin")) == []
    assert ids(store.list_notes()) == [kept["id"]]


def test_tags_are_trimmed_and_deduplicated(store):
    note = store.create({"title": "x", "tags": [" Work ", "work", "home"]})
    assert note["tags"] == ["home", "Work"]


def test_tag_filter_and_counts(store):
    work = store.create({"title": "a", "tags": ["work"]})
    store.create({"title": "b", "tags": ["home"]})
    binned = store.create({"title": "c", "tags": ["work", "old"]})
    store.trash(binned["id"])
    assert ids(store.list_notes(tag="WORK")) == [work["id"]]
    assert store.tags() == [{"name": "home", "count": 1}, {"name": "work", "count": 1}]


def test_unused_tags_are_dropped(store):
    note = store.create({"title": "a", "tags": ["temporary"]})
    store.update(note["id"], {"tags": []})
    assert store.tags() == []


@pytest.mark.parametrize("query", ["BREAD", "rye", "flour", "baking"])
def test_search_matches_title_body_items_and_tags(store, query):
    store.create({"title": "Bread", "body": "rye", "tags": ["baking"]})
    store.create({"kind": "checklist", "title": "Shop", "items": [{"text": "flour"}]})
    store.create({"title": "Unrelated"})
    found = store.list_notes(query=query)
    assert len(found) == 1


def test_search_treats_wildcards_as_plain_text(store):
    store.create({"title": "100% done"})
    store.create({"title": "1000 done"})
    store.create({"title": "snake_case"})
    store.create({"title": "snakeXcase"})
    assert [note["title"] for note in store.list_notes(query="0%")] == ["100% done"]
    assert [note["title"] for note in store.list_notes(query="e_c")] == ["snake_case"]


def test_search_skips_the_bin_unless_looking_in_it(store):
    note = store.create({"title": "findme"})
    store.trash(note["id"])
    assert store.list_notes(query="findme") == []
    assert ids(store.list_notes("bin", query="findme")) == [note["id"]]


def test_reminders_are_listed_soonest_first_and_marked_due(store, clock):
    later = store.create({"title": "later", "reminderAt": "2027-01-20T09:00:00Z"})
    sooner = store.create({"title": "sooner", "reminderAt": "2027-01-16T09:00:00+00:00"})
    store.create({"title": "no reminder"})
    reminders = store.list_notes("reminders")
    assert ids(reminders) == [sooner["id"], later["id"]]
    assert sooner["reminderAt"] == "2027-01-16T09:00:00Z"
    assert all(note["reminderDue"] is False for note in reminders)
    assert store.due_reminder_count() == 0
    clock.advance(2 * DAY)
    assert store.get(sooner["id"])["reminderDue"] is True
    assert store.due_reminder_count() == 1


def test_reminder_can_be_cleared(store):
    note = store.create({"title": "x", "reminderAt": "2027-01-16T09:00:00Z"})
    assert store.update(note["id"], {"reminderAt": None})["reminderAt"] is None
    assert store.list_notes("reminders") == []


def test_binned_reminders_are_not_due(store, clock):
    note = store.create({"title": "x", "reminderAt": "2027-01-01T09:00:00Z"})
    assert store.due_reminder_count() == 1
    store.trash(note["id"])
    assert store.due_reminder_count() == 0


def test_reorder_keeps_the_given_order(store):
    first = store.create({"title": "1"})
    second = store.create({"title": "2"})
    third = store.create({"title": "3"})
    store.reorder([first["id"], third["id"], second["id"]])
    assert ids(store.list_notes()) == [first["id"], third["id"], second["id"]]


@pytest.mark.parametrize("bad, status", [([1, 1], 400), (["1"], 400), ("1,2", 400), ([999], 404), ([True], 400)])
def test_reorder_refuses_bad_lists(store, bad, status):
    store.create({"title": "1"})
    with pytest.raises(NoteError) as error:
        store.reorder(bad)
    assert error.value.status == status


def test_unknown_view_is_refused(store):
    with pytest.raises(NoteError):
        store.list_notes("everything")
