from pathlib import Path

import pytest
from conftest import OWNER, SECRET


def test_health_needs_no_sign_in(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_health_reports_a_broken_database(client, database):
    database.path.unlink()
    database.path.mkdir()
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.get_json()["ok"] is False


def test_api_without_the_user_header_is_refused(client):
    assert client.get("/api/notes").status_code == 401


def test_user_header_sent_from_outside_the_proxy_is_refused(client):
    response = client.get("/api/notes", headers={"X-Forwarded-User": OWNER}, environ_base={"REMOTE_ADDR": "172.16.1.77"})
    assert response.status_code == 401


def test_pages_need_a_user_too(client, owner):
    assert client.get("/").status_code == 401
    page = owner.get("/")
    assert page.status_code == 200
    assert b"<title>Starfire Notes</title>" in page.data
    assert b'aria-label="Starfire Notes home"' in page.data
    assert b'Starfire <span class="brand-accent">Notes</span>' in page.data


def test_session_names_the_user_and_hands_out_a_csrf_token(owner):
    session = owner.get("/api/session").get_json()
    assert session["user"] == OWNER
    assert len(session["csrfToken"]) >= 32


def test_writes_without_a_csrf_token_are_refused(client, owner):
    response = client.post("/api/notes", json={"title": "x"}, headers=owner.headers)
    assert response.status_code == 403
    wrong = client.post("/api/notes", json={"title": "x"}, headers={**owner.headers, "X-CSRF-Token": "0" * 64})
    assert wrong.status_code == 403
    assert owner.get("/api/notes").get_json()["notes"] == []


def test_writes_from_another_origin_are_refused(owner):
    response = owner.client.post("/api/notes", json={"title": "x"}, headers={**owner.write_headers, "Origin": "https://evil.example"})
    assert response.status_code == 403


def test_writes_from_the_same_origin_are_accepted(owner):
    response = owner.client.post("/api/notes", json={"title": "x"}, headers={**owner.write_headers, "Origin": "http://localhost"})
    assert response.status_code == 201


def test_note_life_cycle(owner):
    created = owner.post("/api/notes", json={"kind": "checklist", "title": "Trip", "items": [{"text": "tickets"}, {"text": "bags"}]})
    assert created.status_code == 201
    note = created.get_json()
    note_id = note["id"]

    listed = owner.get("/api/notes").get_json()
    assert [entry["id"] for entry in listed["notes"]] == [note_id]
    assert listed["dueReminders"] == 0

    ticked = owner.patch(f"/api/notes/{note_id}/items/{note['items'][0]['id']}", json={"checked": True})
    assert ticked.status_code == 200
    assert ticked.get_json()["items"][0]["checked"] is True

    edited = owner.patch(f"/api/notes/{note_id}", json={"color": "green", "pinned": True, "tags": ["travel"]})
    assert edited.get_json()["color"] == "green"
    assert owner.get(f"/api/notes/{note_id}").get_json()["tags"] == ["travel"]
    assert owner.get("/api/tags").get_json() == {"tags": [{"name": "travel", "count": 1}]}

    assert owner.post(f"/api/notes/{note_id}/trash").get_json()["trashed"] is True
    assert owner.get("/api/notes?view=bin").get_json()["notes"][0]["id"] == note_id
    assert owner.post(f"/api/notes/{note_id}/restore").get_json()["trashed"] is False
    assert owner.delete(f"/api/notes/{note_id}").status_code == 409
    owner.post(f"/api/notes/{note_id}/trash")
    assert owner.delete(f"/api/notes/{note_id}").status_code == 204
    assert owner.get(f"/api/notes/{note_id}").status_code == 404


def test_search_and_tag_filters_reach_the_store(owner):
    owner.post("/api/notes", json={"title": "Bread", "tags": ["food"]})
    owner.post("/api/notes", json={"title": "Car"})
    assert [note["title"] for note in owner.get("/api/notes?q=bre").get_json()["notes"]] == ["Bread"]
    assert [note["title"] for note in owner.get("/api/notes?tag=food").get_json()["notes"]] == ["Bread"]


def test_unknown_view_is_a_bad_request(owner):
    assert owner.get("/api/notes?view=all").status_code == 400


def test_overlong_search_is_refused(owner):
    assert owner.get("/api/notes?q=" + "x" * 201).status_code == 400


def test_body_html_in_the_api_is_escaped(owner):
    note = owner.post("/api/notes", json={"body": "<img src=x onerror=alert(1)>"}).get_json()
    assert "<img" not in note["bodyHtml"]


def test_reorder_and_empty_bin(owner):
    first = owner.post("/api/notes", json={"title": "1"}).get_json()["id"]
    second = owner.post("/api/notes", json={"title": "2"}).get_json()["id"]
    assert owner.post("/api/notes/reorder", json={"ids": [first, second]}).status_code == 204
    assert [note["id"] for note in owner.get("/api/notes").get_json()["notes"]] == [first, second]
    owner.post(f"/api/notes/{first}/trash")
    emptied = owner.post("/api/bin/empty")
    assert emptied.get_json() == {"removed": 1}


def test_bad_json_is_a_bad_request(owner):
    response = owner.client.post("/api/notes", data="not json", headers={**owner.write_headers, "Content-Type": "application/json"})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_invalid_fields_come_back_as_json_errors(owner):
    response = owner.post("/api/notes", json={"title": "x", "color": "chartreuse"})
    assert response.status_code == 400
    assert "colour" in response.get_json()["error"]


def test_oversized_requests_are_refused(owner):
    response = owner.post("/api/import", json={"notes": [], "padding": "x" * (11 * 1024 * 1024)})
    assert response.status_code == 413


def test_security_headers(owner):
    response = owner.get("/api/notes")
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/static/app.js", "/static/app.css", "/static/icon.svg", "/static/fonts/ibm-plex-sans-latin-400-normal.woff2"])
def test_static_files_are_served(owner, path):
    assert owner.get(path).status_code == 200


def test_static_files_cannot_climb_out(owner):
    assert owner.get("/static/../web.py").status_code == 404


def test_an_import_with_a_year_10000_date_is_refused_and_the_list_still_loads(owner):
    payload = {"app": "keep-notes", "version": 1, "notes": [{"title": "x", "createdAt": "9999-12-31T23:59:59-23:59"}]}
    assert owner.post("/api/import", json=payload).status_code == 400
    assert owner.get("/api/notes").status_code == 200
    assert owner.get("/api/export").status_code == 200


def test_a_reminder_that_cannot_be_shown_is_refused(owner):
    response = owner.post("/api/notes", json={"title": "x", "reminderAt": "9999-12-31T23:59:59-12:00"})
    assert response.status_code == 400
    assert "reminderAt" in response.get_json()["error"]


PORT = 8097


@pytest.fixture
def local_client(store):
    from keepnotes.auth import LocalAuth
    from keepnotes.web import create_app
    local_app = create_app(store, LocalAuth("me", port=PORT), SECRET)
    local_app.testing = True
    return local_app.test_client()


def test_local_mode_refuses_a_rebound_host_name(local_client):
    rebound = f"evil.example:{PORT}"
    assert local_client.get("/api/session", headers={"Host": rebound}).status_code == 421
    assert local_client.get("/", headers={"Host": rebound}).status_code == 421
    posted = local_client.post("/api/session", headers={"Host": rebound, "Origin": f"http://{rebound}"})
    assert posted.status_code == 421


@pytest.mark.parametrize("host", [f"localhost:{PORT}", f"127.0.0.1:{PORT}", f"[::1]:{PORT}"])
def test_local_mode_answers_on_the_loopback_names(local_client, host):
    response = local_client.get("/api/session", headers={"Host": host})
    assert response.status_code == 200
    assert response.get_json()["user"] == "me"


HUGE_ID = "999999999999999999999999999999"
DEEP_JSON = ("[" * 100_000).encode()


@pytest.mark.parametrize("method, path", [("get", f"/api/notes/{HUGE_ID}"), ("patch", f"/api/notes/{HUGE_ID}"),
                                          ("delete", f"/api/notes/{HUGE_ID}"), ("post", f"/api/notes/{HUGE_ID}/trash"),
                                          ("patch", f"/api/notes/1/items/{HUGE_ID}")])
def test_ids_too_large_for_the_database_are_not_found(owner, method, path):
    response = getattr(owner, method)(path, json={}) if method != "get" else owner.get(path)
    assert response.status_code == 404


def test_reordering_with_an_id_too_large_for_the_database_is_refused(owner):
    assert owner.post("/api/notes/reorder", json={"ids": [10 ** 30]}).status_code == 400


@pytest.mark.parametrize("path", ["/api/import", "/api/notes"])
def test_json_nested_too_deeply_is_refused(owner, path):
    response = owner.client.post(path, data=DEEP_JSON, headers={**owner.write_headers, "Content-Type": "application/json"})
    assert response.status_code == 400


def test_the_import_error_in_the_page_names_starfire_notes():
    script = (Path(__file__).resolve().parent.parent / "keepnotes" / "static" / "app.js").read_text()
    assert "That file is not a Starfire Notes export" in script
