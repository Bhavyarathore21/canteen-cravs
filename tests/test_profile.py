"""
Tests for editing a profile, changing a password, and creating the real admin.

The FastAPI app is built in the test with a fake "who is logged in", so no tokens are needed.
"""
import os
import sqlite3

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-the-test-suite-only-0123456789")

from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import demo_seed
import profile_routes
from auth import get_current_user, hash_password, verify_password


def add_user(conn, name, email, password, role="user"):
    conn.execute("INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                 (name, email, hash_password(password), role))


@pytest.fixture
def env(tmp_path):
    """A fresh database with Asha (student), Ravi (student), a real admin and a demo student."""
    database.DB_NAME = str(tmp_path / "profile.db")
    database.create_tables()
    conn = sqlite3.connect(database.DB_NAME)
    add_user(conn, "Asha", "asha@x.com", "oldpass1")                          # id 1
    add_user(conn, "Ravi", "ravi@x.com", "ravipass1")                         # id 2
    add_user(conn, "Chef", "chef@x.com", "chefpass1", role="admin")           # id 3
    add_user(conn, "Demo Student", "student@canteen.demo", "student12345")    # id 4
    conn.commit()
    conn.close()

    who = {"id": 1}

    def current_user():
        c = sqlite3.connect(database.DB_NAME)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT id, name, email, role FROM users WHERE id = ?", (who["id"],)).fetchone()
        c.close()
        return dict(row)

    app = FastAPI()
    app.include_router(profile_routes.router)
    app.dependency_overrides[get_current_user] = current_user
    return TestClient(app), who


def row(user_id):
    c = sqlite3.connect(database.DB_NAME)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    c.close()
    return r


# ------------------------------------------------------------------ editing the profile
def test_a_user_can_change_their_name(env):
    client, _ = env
    r = client.patch("/me", json={"name": "  Asha Rao  "})
    assert r.status_code == 200
    assert r.json() == {"id": 1, "name": "Asha Rao", "email": "asha@x.com", "role": "user"}
    assert row(1)["name"] == "Asha Rao"


def test_a_user_can_change_their_email_and_it_is_lowercased(env):
    client, _ = env
    r = client.patch("/me", json={"email": "Asha.New@Example.com"})
    assert r.status_code == 200 and r.json()["email"] == "asha.new@example.com"


def test_changing_only_the_name_keeps_the_email(env):
    client, _ = env
    client.patch("/me", json={"name": "Asha R"})
    assert row(1)["email"] == "asha@x.com"


def test_an_email_used_by_someone_else_is_refused(env):
    client, _ = env
    r = client.patch("/me", json={"email": "ravi@x.com"})
    assert r.status_code == 400 and "already used" in r.json()["detail"]
    assert row(1)["email"] == "asha@x.com"


def test_bad_input_is_refused(env):
    client, _ = env
    assert client.patch("/me", json={"name": "A"}).status_code == 422          # too short
    assert client.patch("/me", json={"name": "   "}).status_code in (400, 422)  # only spaces
    assert client.patch("/me", json={"email": "not-an-email"}).status_code == 422
    assert client.patch("/me", json={}).status_code == 400                      # nothing to change


def test_an_admin_can_edit_their_own_profile_too(env):
    client, who = env
    who["id"] = 3
    r = client.patch("/me", json={"name": "Head Chef"})
    assert r.status_code == 200 and r.json()["role"] == "admin" and r.json()["name"] == "Head Chef"


def test_editing_never_changes_the_role(env):
    client, _ = env
    client.patch("/me", json={"name": "Asha", "role": "admin"})
    assert row(1)["role"] == "user"


def test_a_user_only_changes_their_own_row(env):
    client, _ = env
    client.patch("/me", json={"name": "Changed"})
    assert row(2)["name"] == "Ravi"


def test_demo_accounts_cannot_be_edited(env):
    client, who = env
    who["id"] = 4
    r = client.patch("/me", json={"name": "Hacked"})
    assert r.status_code == 403 and "Demo accounts" in r.json()["detail"]
    assert row(4)["name"] == "Demo Student"


# ------------------------------------------------------------------ changing the password
def test_changing_the_password_works_and_the_old_one_stops_working(env):
    client, _ = env
    r = client.post("/me/password", json={"current_password": "oldpass1", "new_password": "newpass99"})
    assert r.status_code == 200
    assert verify_password("newpass99", row(1)["password_hash"])
    assert not verify_password("oldpass1", row(1)["password_hash"])


def test_the_password_is_stored_hashed(env):
    client, _ = env
    client.post("/me/password", json={"current_password": "oldpass1", "new_password": "newpass99"})
    assert "newpass99" not in row(1)["password_hash"]


def test_a_wrong_current_password_is_refused(env):
    client, _ = env
    r = client.post("/me/password", json={"current_password": "WRONG", "new_password": "newpass99"})
    assert r.status_code == 400
    assert verify_password("oldpass1", row(1)["password_hash"])


def test_a_weak_or_unchanged_new_password_is_refused(env):
    client, _ = env
    assert client.post("/me/password", json={"current_password": "oldpass1", "new_password": "123"}).status_code == 422
    assert client.post("/me/password", json={"current_password": "oldpass1", "new_password": "x" * 100}).status_code == 422
    assert client.post("/me/password", json={"current_password": "oldpass1", "new_password": "oldpass1"}).status_code == 400


def test_the_demo_password_cannot_be_changed(env):
    client, who = env
    who["id"] = 4
    r = client.post("/me/password", json={"current_password": "student12345", "new_password": "locked-out"})
    assert r.status_code == 403
    assert verify_password("student12345", row(4)["password_hash"])


# ------------------------------------------------------------------ the real admin
@pytest.fixture
def fresh_db(tmp_path):
    database.DB_NAME = str(tmp_path / "seed.db")
    database.create_tables()
    database.seed_menu()


def admins():
    c = sqlite3.connect(database.DB_NAME)
    rows = c.execute("SELECT email FROM users WHERE role = 'admin' ORDER BY id").fetchall()
    c.close()
    return [r[0] for r in rows]


def test_without_admin_settings_only_the_demo_accounts_exist(fresh_db, monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    demo_seed.seed_demo()
    assert admins() == ["admin@canteen.demo"]


def test_the_real_admin_is_created_from_environment_variables(fresh_db, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "  Owner@Example.com ")
    monkeypatch.setenv("ADMIN_PASSWORD", "a-strong-pass-1")
    monkeypatch.setenv("ADMIN_NAME", "Bhavya")
    demo_seed.seed_demo()
    assert admins() == ["admin@canteen.demo", "owner@example.com"]
    c = sqlite3.connect(database.DB_NAME)
    c.row_factory = sqlite3.Row
    me = c.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()
    assert me["name"] == "Bhavya" and verify_password("a-strong-pass-1", me["password_hash"])
    assert "a-strong-pass-1" not in me["password_hash"]


def test_starting_twice_does_not_duplicate_the_admin(fresh_db, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "owner@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "a-strong-pass-1")
    demo_seed.seed_demo()
    demo_seed.seed_demo()
    assert admins().count("owner@example.com") == 1


def test_an_existing_account_is_promoted_not_overwritten(fresh_db, monkeypatch):
    conn = sqlite3.connect(database.DB_NAME)
    add_user(conn, "Real Owner", "owner@example.com", "my-own-password")
    conn.commit()
    conn.close()
    monkeypatch.setenv("ADMIN_EMAIL", "owner@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "different-password")
    demo_seed.seed_demo()
    c = sqlite3.connect(database.DB_NAME)
    c.row_factory = sqlite3.Row
    me = c.execute("SELECT * FROM users WHERE email = 'owner@example.com'").fetchone()
    assert me["role"] == "admin"
    assert verify_password("my-own-password", me["password_hash"])   # the password was left alone


def test_a_too_short_admin_password_creates_nothing(fresh_db, monkeypatch, capsys):
    monkeypatch.setenv("ADMIN_EMAIL", "owner@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "abc")
    demo_seed.seed_demo()
    assert "owner@example.com" not in admins()
    assert "not created" in capsys.readouterr().out


def test_registration_still_cannot_create_an_admin():
    """The /register endpoint in main.py always inserts role 'user'. This guards that line."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "backend", "main.py"), encoding="utf-8").read() \
        if os.path.exists(os.path.join(os.path.dirname(__file__), "..", "backend", "main.py")) else None
    if src is None:
        pytest.skip("main.py is not part of this test environment")
    assert "VALUES (?, ?, ?, 'user')" in src
