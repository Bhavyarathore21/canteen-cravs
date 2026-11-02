"""
Tests for paying online with Razorpay (test mode).

The real Razorpay service is never contacted: the network call is replaced by a fake.
The most important tests are the ones about the signature, because that is what stops
anyone from marking an order as paid without paying.
"""
import hashlib
import hmac
import os
import sqlite3

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-the-test-suite-only-0123456789")

from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
import payments_razorpay
from auth import get_current_user

KEY_ID = "rzp_test_abc123"
KEY_SECRET = "razorpay-test-secret"


def sign(razorpay_order_id, payment_id, secret=KEY_SECRET):
    """What Razorpay sends back: HMAC-SHA256 of 'order_id|payment_id' with the secret key."""
    return hmac.new(secret.encode(), f"{razorpay_order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fresh database with 2 students and a few orders, plus a fake Razorpay."""
    database.DB_NAME = str(tmp_path / "rz.db")
    database.create_tables()
    conn = sqlite3.connect(database.DB_NAME)
    for name, email in [("Asha", "asha@x.com"), ("Ravi", "ravi@x.com")]:
        conn.execute("INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, 'h', 'user')", (name, email))
    rows = [  # id 1: Asha UPI 45.50, id 2: Asha Cash, id 3: Ravi UPI, id 4: Asha UPI already preparing
        ("A001", 1, 45.50, "Placed", "UPI", "Pending"),
        ("A002", 1, 20, "Placed", "Cash", "Pending"),
        ("A003", 2, 30, "Placed", "UPI", "Pending"),
        ("A004", 1, 15, "Preparing", "UPI", "Paid"),
    ]
    for code, uid, total, status, method, pay in rows:
        conn.execute(
            "INSERT INTO orders (order_code, user_id, total, status, payment_method, payment_status) "
            "VALUES (?, ?, ?, ?, ?, ?)", (code, uid, total, status, method, pay))
    conn.commit()
    conn.close()

    monkeypatch.setenv("RAZORPAY_KEY_ID", KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", KEY_SECRET)

    calls = []

    def fake_post(url, auth=None, json=None, timeout=None):
        calls.append({"url": url, "auth": auth, "json": json})
        return FakeResponse(200, {"id": f"order_TEST{len(calls)}"})

    monkeypatch.setattr(payments_razorpay.httpx, "post", fake_post)

    app = FastAPI()
    app.include_router(payments_razorpay.router)
    current = {"id": 1}  # who is logged in
    app.dependency_overrides[get_current_user] = lambda: {"id": current["id"], "name": "x", "email": "x", "role": "user"}
    return TestClient(app), calls, current


def db():
    conn = sqlite3.connect(database.DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def payment_status(order_id):
    return db().execute("SELECT payment_status FROM orders WHERE id = ?", (order_id,)).fetchone()[0]


def events(order_id):
    return [r["event"] for r in db().execute("SELECT event FROM payment_log WHERE order_id = ? ORDER BY id", (order_id,))]


# ------------------------------------------------------------------ starting a payment
def test_starting_a_payment_asks_razorpay_for_the_total_in_paise(env):
    client, calls, _ = env
    r = client.post("/orders/1/razorpay")
    assert r.status_code == 200
    body = r.json()
    assert body["amount"] == 4550 and body["currency"] == "INR"
    assert body["key_id"] == KEY_ID and body["razorpay_order_id"] == "order_TEST1"
    assert calls[0]["json"]["amount"] == 4550          # 45.50 rupees = 4550 paise
    assert calls[0]["auth"] == (KEY_ID, KEY_SECRET)
    assert "Razorpay order created" in events(1)


def test_the_secret_key_is_never_sent_to_the_browser(env):
    client, _, _ = env
    assert KEY_SECRET not in client.post("/orders/1/razorpay").text


def test_retrying_reuses_the_same_razorpay_order(env):
    client, calls, _ = env
    first = client.post("/orders/1/razorpay").json()
    second = client.post("/orders/1/razorpay").json()
    assert first["razorpay_order_id"] == second["razorpay_order_id"]
    assert len(calls) == 1                             # Razorpay was only asked once


def test_cannot_pay_for_someone_elses_order(env):
    client, calls, _ = env
    assert client.post("/orders/3/razorpay").status_code == 404   # order 3 belongs to Ravi
    assert calls == []


def test_cash_orders_cannot_use_online_payment(env):
    client, _, _ = env
    r = client.post("/orders/2/razorpay")
    assert r.status_code == 400 and "UPI" in r.json()["detail"]


def test_orders_already_being_prepared_cannot_start_a_payment(env):
    client, _, _ = env
    assert client.post("/orders/4/razorpay").status_code == 400


def test_server_without_keys_says_payments_are_not_set_up(env, monkeypatch):
    client, calls, _ = env
    monkeypatch.delenv("RAZORPAY_KEY_SECRET")
    r = client.post("/orders/1/razorpay")
    assert r.status_code == 503 and calls == []


def test_razorpay_being_down_gives_a_friendly_error(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setattr(payments_razorpay.httpx, "post", lambda *a, **k: FakeResponse(500))
    r = client.post("/orders/1/razorpay")
    assert r.status_code == 502 and "try again" in r.json()["detail"].lower()
    assert "Razorpay order created" not in events(1)


# ------------------------------------------------------------------ verifying a payment
def start(client, order_id=1):
    return client.post(f"/orders/{order_id}/razorpay").json()["razorpay_order_id"]


def test_a_valid_signature_marks_the_order_paid(env):
    client, _, _ = env
    rp_order = start(client)
    r = client.post("/orders/1/razorpay/verify", json={
        "razorpay_order_id": rp_order, "razorpay_payment_id": "pay_ABC",
        "razorpay_signature": sign(rp_order, "pay_ABC")})
    assert r.status_code == 200
    assert payment_status(1) == "Paid"
    assert events(1)[-1] == "UPI payment confirmed"    # same event name the analytics pipeline expects
    ref = db().execute("SELECT reference FROM payment_log WHERE order_id = 1 AND event = 'UPI payment confirmed'").fetchone()[0]
    assert ref == "pay_ABC"


def test_a_forged_signature_is_rejected_and_the_order_stays_unpaid(env):
    client, _, _ = env
    rp_order = start(client)
    r = client.post("/orders/1/razorpay/verify", json={
        "razorpay_order_id": rp_order, "razorpay_payment_id": "pay_ABC",
        "razorpay_signature": "0" * 64})
    assert r.status_code == 400
    assert payment_status(1) == "Pending"
    assert "Razorpay signature rejected" in events(1)


def test_a_signature_made_with_the_wrong_secret_is_rejected(env):
    client, _, _ = env
    rp_order = start(client)
    r = client.post("/orders/1/razorpay/verify", json={
        "razorpay_order_id": rp_order, "razorpay_payment_id": "pay_ABC",
        "razorpay_signature": sign(rp_order, "pay_ABC", secret="some-other-secret")})
    assert r.status_code == 400 and payment_status(1) == "Pending"


def test_a_signature_for_a_different_payment_id_is_rejected(env):
    client, _, _ = env
    rp_order = start(client)
    r = client.post("/orders/1/razorpay/verify", json={
        "razorpay_order_id": rp_order, "razorpay_payment_id": "pay_ABC",
        "razorpay_signature": sign(rp_order, "pay_SOMETHING_ELSE")})
    assert r.status_code == 400 and payment_status(1) == "Pending"


def test_a_payment_for_another_razorpay_order_cannot_be_reused(env):
    client, _, current = env
    start(client, 1)
    current["id"] = 2
    other = start(client, 3)                           # Ravi pays for his own order
    current["id"] = 1
    r = client.post("/orders/1/razorpay/verify", json={   # Asha tries to use Ravi's payment
        "razorpay_order_id": other, "razorpay_payment_id": "pay_R",
        "razorpay_signature": sign(other, "pay_R")})
    assert r.status_code == 400 and payment_status(1) == "Pending"


def test_verifying_without_starting_a_payment_is_rejected(env):
    client, _, _ = env
    r = client.post("/orders/1/razorpay/verify", json={
        "razorpay_order_id": "order_X", "razorpay_payment_id": "pay_X",
        "razorpay_signature": sign("order_X", "pay_X")})
    assert r.status_code == 400 and payment_status(1) == "Pending"


def test_verifying_twice_is_harmless(env):
    client, _, _ = env
    rp_order = start(client)
    body = {"razorpay_order_id": rp_order, "razorpay_payment_id": "pay_ABC",
            "razorpay_signature": sign(rp_order, "pay_ABC")}
    assert client.post("/orders/1/razorpay/verify", json=body).status_code == 200
    again = client.post("/orders/1/razorpay/verify", json=body)
    assert again.status_code == 200
    assert events(1).count("UPI payment confirmed") == 1   # logged only once


def test_a_paid_order_cannot_start_another_payment(env):
    client, _, _ = env
    rp_order = start(client)
    client.post("/orders/1/razorpay/verify", json={
        "razorpay_order_id": rp_order, "razorpay_payment_id": "pay_ABC",
        "razorpay_signature": sign(rp_order, "pay_ABC")})
    assert client.post("/orders/1/razorpay").status_code == 400
