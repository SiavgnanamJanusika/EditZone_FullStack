import asyncio
import hashlib
import hmac
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse

import httpx

from app.config import settings

_token_lock = asyncio.Lock()
_token_cache = {"value": None, "expires_at": 0.0}


class PayHereAPIError(Exception):
    pass


def format_amount(value) -> str:
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid payment amount") from exc


def amount_to_minor(value) -> int:
    """Convert an LKR amount to integer cents without binary floating point."""
    return int(Decimal(format_amount(value)) * 100)


def minor_to_amount(value: int) -> str:
    return format_amount(Decimal(int(value)) / 100)


def split_amount(value, commission_percent) -> tuple[int, int, int]:
    gross = amount_to_minor(value)
    rate = Decimal(str(commission_percent)) / Decimal("100")
    fee = int((Decimal(gross) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return gross, fee, gross - fee


def split_project_amount(value) -> tuple[int, int, int, int, int]:
    """Return project gross, editor commission, client fee, revenue and editor net.

    Both percentages use the original project amount.  The client fee is added
    to checkout and is never deducted from editor earnings.
    """
    gross = amount_to_minor(value)
    platform = int((Decimal(gross) * Decimal(str(settings.EDITOR_COMMISSION_PERCENT)) / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    service = int((Decimal(gross) * Decimal(str(settings.CLIENT_SERVICE_FEE_PERCENT)) / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    admin_total = platform + service
    return gross, platform, service, admin_total, gross - platform


def _md5_upper(value: str) -> str:
    # PayHere defines this legacy digest in its wire protocol; it is not used
    # for password hashing or as an application-selected cryptographic primitive.
    return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest().upper()


def payhere_checkout_config() -> dict[str, str]:
    """Return normalized sandbox checkout configuration or fail safely."""
    merchant_id = str(settings.PAYHERE_MERCHANT_ID or "").strip()
    merchant_secret = str(settings.PAYHERE_MERCHANT_SECRET or "").strip()
    mode = str(settings.PAYHERE_MODE or "").strip().lower()
    currency = str(settings.PAYHERE_CURRENCY or "").strip().upper()
    checkout_url = str(settings.PAYHERE_SANDBOX_URL or "").strip()
    notify_url = str(settings.PAYHERE_NOTIFY_URL or "").strip()
    parsed_checkout = urlparse(checkout_url)
    if mode != "sandbox" or not settings.PAYHERE_SANDBOX:
        raise PayHereAPIError("PayHere sandbox mode is required")
    if not merchant_id or not merchant_secret:
        raise PayHereAPIError("PayHere Merchant ID and Secret are not configured")
    if currency != "LKR":
        raise PayHereAPIError("PayHere currency must be LKR")
    if parsed_checkout.scheme != "https" or parsed_checkout.netloc != "sandbox.payhere.lk" or parsed_checkout.path != "/pay/checkout":
        raise PayHereAPIError("PayHere sandbox checkout URL is invalid")
    if not notify_url:
        raise PayHereAPIError("PayHere notify URL is not configured")
    return {
        "merchant_id": merchant_id, "merchant_secret": merchant_secret,
        "mode": mode, "currency": currency, "checkout_url": checkout_url,
        "notify_url": notify_url,
    }


def checkout_hash(order_id: str, amount, currency: str = "LKR", *, merchant_id: str | None = None, merchant_secret: str | None = None) -> str:
    """Generate PayHere's server-side Checkout API hash from canonical strings."""
    config = payhere_checkout_config() if merchant_id is None or merchant_secret is None else None
    normalized_merchant = str(merchant_id if merchant_id is not None else config["merchant_id"]).strip()
    normalized_secret = str(merchant_secret if merchant_secret is not None else config["merchant_secret"]).strip()
    normalized_order = str(order_id or "").strip()
    normalized_currency = str(currency or "").strip().upper()
    normalized_amount = format_amount(amount)
    if not normalized_merchant or not normalized_secret or not normalized_order:
        raise ValueError("Merchant ID, Merchant Secret, and order ID are required")
    if normalized_currency != "LKR" or Decimal(normalized_amount) <= 0:
        raise ValueError("PayHere checkout requires a positive LKR amount")
    secret_hash = _md5_upper(normalized_secret)
    return _md5_upper(f"{normalized_merchant}{normalized_order}{normalized_amount}{normalized_currency}{secret_hash}")


def notification_is_valid(
    merchant_id: str,
    order_id: str,
    amount: str,
    currency: str,
    status_code: str,
    signature: str,
) -> bool:
    configured_merchant = str(settings.PAYHERE_MERCHANT_ID or "").strip()
    configured_secret = str(settings.PAYHERE_MERCHANT_SECRET or "").strip()
    if not configured_secret or merchant_id.strip() != configured_merchant:
        return False
    secret_hash = _md5_upper(configured_secret)
    expected = _md5_upper(
        f"{merchant_id}{order_id}{amount}{currency}{status_code}{secret_hash}"
    )
    return hmac.compare_digest(expected, (signature or "").upper())


def _base_url() -> str:
    # Live mode is deliberately unavailable until sandbox testing is complete.
    if not settings.PAYHERE_SANDBOX:
        raise PayHereAPIError("Live PayHere mode is locked; complete sandbox testing first")
    return "https://sandbox.payhere.lk"


async def _access_token() -> str:
    if _token_cache["value"] and _token_cache["expires_at"] > time.monotonic() + 30:
        return _token_cache["value"]
    async with _token_lock:
        if _token_cache["value"] and _token_cache["expires_at"] > time.monotonic() + 30:
            return _token_cache["value"]
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    f"{_base_url()}/merchant/v1/oauth/token",
                    data={"grant_type": "client_credentials"},
                    auth=(settings.PAYHERE_APP_ID, settings.PAYHERE_APP_SECRET),
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PayHereAPIError("Could not authenticate with PayHere") from exc
        token = payload.get("access_token")
        if not token:
            raise PayHereAPIError("PayHere did not return an access token")
        _token_cache.update({
            "value": token,
            "expires_at": time.monotonic() + int(payload.get("expires_in", 599)),
        })
        return token


async def capture_authorization(authorization_token: str, amount: str) -> dict:
    token = await _access_token()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{_base_url()}/merchant/v1/payment/capture",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "authorization_token": authorization_token,
                    "amount": float(format_amount(amount)),
                    "deduction_details": "EditZone protected project release",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PayHereAPIError("PayHere capture request failed") from exc
    if payload.get("status") != 1 or str(payload.get("data", {}).get("status_code")) != "2":
        raise PayHereAPIError(payload.get("msg") or "PayHere declined the capture")
    return payload["data"]


async def refund_payment(
    *,
    reason: str,
    payment_id: str | None = None,
    authorization_token: str | None = None,
) -> dict:
    token = await _access_token()
    body = {"description": reason}
    if payment_id:
        body["payment_id"] = payment_id
    else:
        body["authorization_token"] = authorization_token
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{_base_url()}/merchant/v1/payment/refund",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PayHereAPIError("PayHere refund request failed") from exc
    if payload.get("status") != 1:
        raise PayHereAPIError(payload.get("msg") or "PayHere declined the refund")
    return payload


async def retrieve_payment(order_id: str) -> list[dict]:
    """Retrieve successful PayHere records for reconciliation; never browser-facing."""
    token = await _access_token()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{_base_url()}/merchant/v1/payment/search",
                headers={"Authorization": f"Bearer {token}"},
                params={"order_id": order_id},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise PayHereAPIError("PayHere retrieval request failed") from exc
    if payload.get("status") == -1:
        return []
    if payload.get("status") != 1:
        raise PayHereAPIError(payload.get("msg") or "PayHere retrieval was rejected")
    return payload.get("data") or []
