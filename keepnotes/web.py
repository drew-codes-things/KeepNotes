import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, abort, g, jsonify, redirect, request, send_from_directory
from werkzeug.exceptions import HTTPException

from . import __version__
from .auth import csrf_matches, csrf_token
from .notes import LARGEST_DATABASE_INTEGER, NoteError, NoteStore
from .signin import SIGN_IN_PATHS, PasswordAuth, add_sign_in_pages
from .transfer import export_notes, import_notes

STATIC_DIR = Path(__file__).resolve().parent / "static"
APP_NAME = "Starfire Notes"
MAX_REQUEST_BYTES = 10 * 1024 * 1024
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
PUBLIC_PATHS = ("/api/health",)
SIGNED_OUT_TEXT = "Sign in through the main sign-in page first."
NESTED_TOO_DEEPLY_TEXT = "that JSON is nested too deeply"
WRONG_HOST_TEXT = ("this app does not answer on that host name (AUTH_MODE=local answers on localhost, 127.0.0.1 or "
                   "[::1] at its own port; the own sign-in answers on the names in ALLOWED_HOSTS, or with it empty on "
                   "localhost, IP addresses and this machine's name)")
MISDIRECTED_REQUEST = 421
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "font-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
))

log = logging.getLogger("keepnotes.web")


def _error(message: str, status: int):
    return jsonify({"error": message}), status


def _body() -> dict:
    try:
        data = request.get_json(silent=True)
    except RecursionError as error:
        raise NoteError(NESTED_TOO_DEEPLY_TEXT) from error
    if not isinstance(data, dict):
        raise NoteError("send a JSON object")
    return data


def _is_same_origin() -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc == request.host


def _export_file_name(store: NoteStore) -> str:
    day = datetime.fromtimestamp(store.clock(), timezone.utc).strftime("%Y-%m-%d")
    return f"keep-notes-{day}.json"


def create_app(store: NoteStore, auth, secret: bytes) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
    has_own_sign_in = isinstance(auth, PasswordAuth)
    if has_own_sign_in:
        add_sign_in_pages(app, auth, APP_NAME)

    @app.url_value_preprocessor
    def refuse_ids_beyond_the_database(endpoint, values):
        if any(isinstance(value, int) and value > LARGEST_DATABASE_INTEGER for value in (values or {}).values()):
            abort(404)

    @app.before_request
    def identify_and_guard_writes():
        if request.path in PUBLIC_PATHS:
            return None
        if not auth.accepts_host(request.host):
            return _error(WRONG_HOST_TEXT, MISDIRECTED_REQUEST)
        if has_own_sign_in and request.path in SIGN_IN_PATHS:
            return None
        user = auth.user_for(request.remote_addr or "", request.headers)
        if user is None:
            if request.path.startswith("/api/"):
                return _error("sign in first", 401)
            if has_own_sign_in:
                return redirect(auth.entry_path(), code=303)
            return Response(SIGNED_OUT_TEXT, status=401, mimetype="text/plain")
        g.user = user
        if request.method in SAFE_METHODS:
            return None
        if not _is_same_origin():
            return _error("this request came from another site", 403)
        if not csrf_matches(secret, user, request.headers.get(CSRF_HEADER)):
            return _error("missing or wrong CSRF token, reload the page", 403)
        return None

    @app.after_request
    def add_security_headers(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(NoteError)
    def note_refused(error: NoteError):
        return _error(str(error), error.status)

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        if error.code == 413:
            return _error("that request is too large", 413)
        if request.path.startswith("/api/"):
            return _error(error.description or error.name, error.code or 500)
        return error

    @app.get("/")
    def index():
        response = send_from_directory(STATIC_DIR, "index.html", max_age=0)
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/static/<path:name>")
    def static_file(name: str):
        return send_from_directory(STATIC_DIR, name, max_age=0)

    @app.get("/api/health")
    def health():
        if not store.database.is_reachable():
            log.error("health check: the database could not be read at %s", store.database.path)
            return jsonify({"ok": False, "error": "database unavailable"}), 503
        return jsonify({"ok": True})

    @app.get("/api/session")
    def session_state():
        return jsonify({"user": g.user, "csrfToken": csrf_token(secret, g.user), "version": __version__})

    @app.get("/api/notes")
    def list_notes():
        notes = store.list_notes(request.args.get("view", "notes"), request.args.get("q", ""), request.args.get("tag", ""))
        return jsonify({"notes": notes, "dueReminders": store.due_reminder_count()})

    @app.post("/api/notes")
    def create_note():
        return jsonify(store.create(_body())), 201

    @app.post("/api/notes/reorder")
    def reorder_notes():
        store.reorder(_body().get("ids"))
        return "", 204

    @app.get("/api/notes/<int:note_id>")
    def read_note(note_id: int):
        return jsonify(store.get(note_id))

    @app.patch("/api/notes/<int:note_id>")
    def update_note(note_id: int):
        return jsonify(store.update(note_id, _body()))

    @app.delete("/api/notes/<int:note_id>")
    def delete_note(note_id: int):
        store.delete_forever(note_id)
        return "", 204

    @app.patch("/api/notes/<int:note_id>/items/<int:item_id>")
    def tick_item(note_id: int, item_id: int):
        return jsonify(store.set_item_checked(note_id, item_id, _body().get("checked")))

    @app.post("/api/notes/<int:note_id>/trash")
    def trash_note(note_id: int):
        return jsonify(store.trash(note_id))

    @app.post("/api/notes/<int:note_id>/restore")
    def restore_note(note_id: int):
        return jsonify(store.restore(note_id))

    @app.get("/api/tags")
    def list_tags():
        return jsonify({"tags": store.tags()})

    @app.post("/api/bin/empty")
    def empty_bin():
        return jsonify({"removed": store.empty_bin()})

    @app.get("/api/export")
    def export_download():
        body = json.dumps(export_notes(store), ensure_ascii=False, indent=2)
        response = Response(body, mimetype="application/json")
        response.headers["Content-Disposition"] = f"attachment; filename={_export_file_name(store)}"
        return response

    @app.post("/api/import")
    def import_upload():
        return jsonify({"imported": import_notes(store, _body())})

    return app
