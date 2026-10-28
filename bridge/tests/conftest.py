import pytest

from bridge import config, db, media, push


@pytest.fixture(autouse=True)
def _journal_sandbox(tmp_path, monkeypatch):
    """Journal files live under tmp_path, no loops start, and no real binary or push can run."""
    monkeypatch.setattr(config, "JOURNAL_DIR", tmp_path / "journal")
    monkeypatch.setattr(config, "JOURNAL_TMP", tmp_path / "journal" / "tmp")
    monkeypatch.setattr(config, "VAPID_KEY_PATH", tmp_path / "vapid_private.pem")
    monkeypatch.setattr(config, "BACKGROUND", False)
    monkeypatch.setenv("HOME", str(tmp_path))  # the default WHISPER_MODEL path must not see the real home

    def forbidden(*a, **k):
        raise AssertionError("real binary/push called")

    monkeypatch.setattr(media, "_run", forbidden)
    monkeypatch.setattr(push, "_send", forbidden)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    # Modules read config.X at call time, so repoint the module attributes too.
    monkeypatch.setenv("BM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "app.sqlite3")
    monkeypatch.setattr(config, "PHOTOS_DIR", tmp_path / "photos")
    monkeypatch.setattr(config, "LOGS_DIR", tmp_path / "logs")
    c = db.connect()
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def client(conn):
    try:
        from fastapi.testclient import TestClient

        from bridge.app import app
    except Exception as e:
        pytest.skip(f"bridge.app not importable: {e}")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def tools(monkeypatch):
    from journal_helpers import FakeTools

    t = FakeTools()
    monkeypatch.setattr(media, "_run", t)
    return t


@pytest.fixture
def clock(monkeypatch):
    from journal_helpers import Clock, wib

    c = Clock(wib(26, 10, 0))
    monkeypatch.setattr(config, "now", c)
    return c


@pytest.fixture
def rng(monkeypatch):
    from journal_helpers import FixedRng

    from bridge import moments

    r = FixedRng(0)
    monkeypatch.setattr(moments, "_rng", r)
    return r


@pytest.fixture
def whisper_env(tmp_path, monkeypatch):
    (tmp_path / "whisper-cli").write_bytes(b"")
    (tmp_path / "model.bin").write_bytes(b"")
    monkeypatch.setenv("WHISPER_BIN", str(tmp_path / "whisper-cli"))
    monkeypatch.setenv("WHISPER_MODEL", str(tmp_path / "model.bin"))
