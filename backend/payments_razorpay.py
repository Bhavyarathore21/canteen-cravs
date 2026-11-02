"""
payments_razorpay.py - let a student pay for an order online with Razorpay.

Works in Razorpay TEST mode (rzp_test_ keys): the checkout popup looks real,
but no money moves.

How it works
  1. POST /orders/{id}/razorpay          the server asks Razorpay to create a payment
                                         for the order's total and returns what the popup needs
  2. (the student pays inside the Razorpay popup)
  3. POST /orders/{id}/razorpay/verify   the popup sends back 3 values. The server re-computes
                                         the signature with the SECRET key, and only if it matches
                                         does it mark the order Paid.

The secret key lives only on the server (RAZORPAY_KEY_SECRET). The amount is always
read from the database, never from the browser.
"""
import hashlib
import hmac
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from database import get_connection

router = APIRouter()

RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"


class RazorpayResult(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


def _keys():
    key_id = os.getenv("RAZORPAY_KEY_ID")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret:
        raise HTTPException(status_code=503, detail="Online payments are not set up on this server yet")
    return key_id, key_secret


def _log(conn, order_id, event, amount, reference):
    conn.execute(
        "INSERT INTO payment_log (order_id, event, amount, reference) VALUES (?, ?, ?, ?)",
        (order_id, event, amount, reference),
    )


def _my_order(conn, order_id, user_id):
    order = conn.execute(
        "SELECT * FROM orders WHERE id = ? AND user_id = ?", (order_id, user_id)
    ).fetchone()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _stored_razorpay_order(conn, order_id):
    row = conn.execute(
        "SELECT reference FROM payment_log WHERE order_id = ? AND event = 'Razorpay order created' "
        "ORDER BY id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    return row["reference"] if row else None


@router.post("/orders/{order_id}/razorpay")
def create_razorpay_order(order_id: int, user: dict = Depends(get_current_user)):
    key_id, key_secret = _keys()
    conn = get_connection()
    try:
        order = _my_order(conn, order_id, user["id"])
        if order["payment_method"] != "UPI":
            raise HTTPException(status_code=400, detail="Online payment is only for UPI orders")
        if order["status"] != "Placed":
            raise HTTPException(status_code=400, detail=f"Order is already {order['status']}")
        if order["payment_status"] not in ("Pending", "Submitted"):
            raise HTTPException(status_code=400, detail=f"Payment is already {order['payment_status']}")

        amount_paise = int(round(order["total"] * 100))  # Razorpay counts in paise
        razorpay_order_id = _stored_razorpay_order(conn, order_id)  # reuse it on a retry

        if razorpay_order_id is None:
            try:
                resp = httpx.post(
                    RAZORPAY_ORDERS_URL,
                    auth=(key_id, key_secret),
                    json={"amount": amount_paise, "currency": "INR", "receipt": order["order_code"]},
                    timeout=20,
                )
            except httpx.HTTPError:
                raise HTTPException(status_code=502, detail="Could not reach the payment service. Please try again.")
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Could not start the online payment. Please try again.")
            razorpay_order_id = resp.json()["id"]
            _log(conn, order_id, "Razorpay order created", order["total"], razorpay_order_id)
            conn.commit()

        return {
            "key_id": key_id,
            "razorpay_order_id": razorpay_order_id,
            "amount": amount_paise,
            "currency": "INR",
            "order_code": order["order_code"],
        }
    finally:
        conn.close()


@router.post("/orders/{order_id}/razorpay/verify")
def verify_razorpay_payment(
    order_id: int, data: RazorpayResult, user: dict = Depends(get_current_user)
):
    _, key_secret = _keys()
    conn = get_connection()
    try:
        order = _my_order(conn, order_id, user["id"])

        if _stored_razorpay_order(conn, order_id) != data.razorpay_order_id:
            raise HTTPException(status_code=400, detail="This payment does not belong to this order")

        expected = hmac.new(
            key_secret.encode(),
            f"{data.razorpay_order_id}|{data.razorpay_payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, data.razorpay_signature):
            _log(conn, order_id, "Razorpay signature rejected", order["total"], data.razorpay_payment_id)
            conn.commit()
            raise HTTPException(status_code=400, detail="Payment could not be verified")

        cur = conn.execute(
            "UPDATE orders SET payment_status = 'Paid' "
            "WHERE id = ? AND payment_status IN ('Pending', 'Submitted')",
            (order_id,),
        )
        if cur.rowcount == 0:
            if order["payment_status"] == "Paid":
                return {"message": "Payment was already confirmed"}
            raise HTTPException(status_code=400, detail=f"Payment is {order['payment_status']}")

        # Same event name as a manual UPI confirmation, so the analytics pipeline treats it as paid
        _log(conn, order_id, "UPI payment confirmed", order["total"], data.razorpay_payment_id)
        conn.commit()
        return {"message": "Payment received"}
    finally:
        conn.close()
