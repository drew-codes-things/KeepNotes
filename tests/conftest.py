import pytest

START_TIME = 1_800_000_000.0
OWNER = "demo@example.com"
SECRET = b"k" * 32


class FakeClock:
    def __init__(self, start: float = START_TIME):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def database(tmp_path):
    from keepnotes.db import Database
    return Database(tmp_path / "notes.db")


@pytest.fixture
def store(database, clock):
    from keepnotes.notes import NoteStore
    return NoteStore(database, clock=clock)


@pytest.fixture
def app(store):
    from keepnotes.auth import ProxyAuth
    from keepnotes.web import create_app
    auth = ProxyAuth(trusted_proxies=("127.0.0.1", "::1"), user_header="X-Forwarded-User")
    flask_app = create_app(store, auth, SECRET)
    flask_app.testing = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


class SignedInClient:
    def __init__(self, client):
        self.client = client
        self.headers = {"X-Forwarded-User": OWNER}
        session = client.get("/api/session", headers=self.headers).get_json()
        self.write_headers = {**self.headers, "X-CSRF-Token": session["csrfToken"]}

    def get(self, path, **kwargs):
        return self.client.get(path, headers=self.headers, **kwargs)

    def post(self, path, json=None, **kwargs):
        return self.client.post(path, json=json, headers=self.write_headers, **kwargs)

    def patch(self, path, json=None, **kwargs):
        return self.client.patch(path, json=json, headers=self.write_headers, **kwargs)

    def delete(self, path, **kwargs):
        return self.client.delete(path, headers=self.write_headers, **kwargs)


@pytest.fixture
def owner(client):
    return SignedInClient(client)
