"""Hợp đồng message giữa chi nhánh (client) và worker.

Mọi message là JSON UTF-8. Ảnh JPEG được mã hoá base64 trong trường `image_b64`
(đã resize cạnh dài tối đa 640px nên chỉ vài chục KB).
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- topology
EXCHANGE = "coffee.requests"        # chi nhánh CHỈ được publish vào exchange này
DLX = "coffee.dlx"                  # dead-letter exchange
Q_FACE = "face.requests"            # yêu cầu nhận diện (competing consumers)
Q_FACE_DLQ = "face.requests.dlq"
Q_ORDERS = "orders"                 # lệnh ghi: tạo đơn, cập nhật khách
Q_ORDERS_DLQ = "orders.dlq"
REPLY_PREFIX = "reply."             # reply.<branch>.<uuid>: exclusive, auto-delete

# ---------------------------------------------------------------- message types
T_RECOGNIZE = "face.recognize"
T_ORDER = "order.create"
T_CUSTOMER_UPDATE = "customer.update"


class Status:
    RECOGNIZED = "RECOGNIZED"
    NEW_CUSTOMER = "NEW_CUSTOMER"
    UNKNOWN = "UNKNOWN"              # chỉ khi auto_enroll=false (đo đạc, không tạo khách)
    NO_FACE = "NO_FACE"
    MULTIPLE_FACES = "MULTIPLE_FACES"
    ERROR = "ERROR"
    ORDER_SAVED = "ORDER_SAVED"
    CUSTOMER_UPDATED = "CUSTOMER_UPDATED"


class MessageError(ValueError):
    """Message sai định dạng. Đây là lỗi vĩnh viễn: thử lại cũng không hết."""


_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_PHONE_RE = re.compile(r"^[0-9+\-\s().]{6,20}$")


def new_request_id() -> str:
    return uuid.uuid4().hex


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def encode(obj: Dict[str, Any]) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode(body: bytes) -> Dict[str, Any]:
    try:
        obj = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MessageError(f"body không phải JSON hợp lệ: {exc}") from exc
    if not isinstance(obj, dict):
        raise MessageError("body phải là một JSON object")
    return obj


# ---------------------------------------------------------------- builders (client)
def build_recognize_request(branch_id: str, jpeg: bytes, *, request_id: Optional[str] = None,
                            auto_enroll: bool = True, full_name: Optional[str] = None,
                            captured_at: Optional[str] = None) -> Dict[str, Any]:
    return {
        "type": T_RECOGNIZE,
        "request_id": request_id or new_request_id(),
        "branch_id": branch_id,
        "captured_at": captured_at or utc_now_iso(),
        "auto_enroll": auto_enroll,
        "full_name": full_name,
        "image_b64": base64.b64encode(jpeg).decode("ascii"),
    }


def build_order_request(branch_id: str, customer_id: int, items: List[Dict[str, Any]], *,
                        request_id: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
    return {
        "type": T_ORDER,
        "request_id": request_id or new_request_id(),
        "branch_id": branch_id,
        "customer_id": customer_id,
        "items": items,
        "note": note,
    }


def build_customer_update(branch_id: str, customer_id: int, *, full_name: Optional[str] = None,
                          phone: Optional[str] = None, request_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "type": T_CUSTOMER_UPDATE,
        "request_id": request_id or new_request_id(),
        "branch_id": branch_id,
        "customer_id": customer_id,
        "full_name": full_name,
        "phone": phone,
    }


# ---------------------------------------------------------------- validators (worker)
def _require_id(msg: Dict[str, Any], key: str) -> str:
    value = msg.get(key)
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise MessageError(f"trường '{key}' thiếu hoặc không hợp lệ")
    return value


def validate_recognize(msg: Dict[str, Any]) -> Dict[str, Any]:
    _require_id(msg, "request_id")
    _require_id(msg, "branch_id")
    b64 = msg.get("image_b64")
    if not isinstance(b64, str) or not b64:
        raise MessageError("thiếu 'image_b64'")
    try:
        jpeg = base64.b64decode(b64, validate=True)
    except Exception as exc:  # binascii.Error
        raise MessageError("'image_b64' không phải base64 hợp lệ") from exc
    if not jpeg:
        raise MessageError("ảnh rỗng")
    name = msg.get("full_name")
    if name is not None and (not isinstance(name, str) or len(name) > 100):
        raise MessageError("'full_name' không hợp lệ")
    return {
        "request_id": msg["request_id"],
        "branch_id": msg["branch_id"],
        "captured_at": msg.get("captured_at"),
        "auto_enroll": bool(msg.get("auto_enroll", True)),
        "full_name": (name or "").strip() or None,
        "jpeg": jpeg,
    }


def validate_order(msg: Dict[str, Any]) -> Dict[str, Any]:
    _require_id(msg, "request_id")
    _require_id(msg, "branch_id")
    cid = msg.get("customer_id")
    if not isinstance(cid, int) or isinstance(cid, bool) or cid <= 0:
        raise MessageError("'customer_id' phải là số nguyên dương")
    raw_items = msg.get("items")
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 50:
        raise MessageError("'items' phải là danh sách 1..50 món")
    items = []
    for it in raw_items:
        if not isinstance(it, dict):
            raise MessageError("mỗi món phải là object")
        name = it.get("item_name")
        qty = it.get("quantity", 1)
        price = it.get("unit_price", 0)
        options = it.get("options")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise MessageError("'item_name' không hợp lệ")
        if not isinstance(qty, int) or isinstance(qty, bool) or not (1 <= qty <= 99):
            raise MessageError("'quantity' phải nằm trong 1..99")
        if not isinstance(price, (int, float)) or isinstance(price, bool) or price < 0:
            raise MessageError("'unit_price' không hợp lệ")
        if options is not None and (not isinstance(options, str) or len(options) > 255):
            raise MessageError("'options' không hợp lệ")
        items.append({"item_name": name.strip(), "quantity": qty,
                      "unit_price": round(float(price), 2), "options": (options or "").strip() or None})
    note = msg.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 255):
        raise MessageError("'note' không hợp lệ")
    return {"request_id": msg["request_id"], "branch_id": msg["branch_id"], "customer_id": cid,
            "items": items, "note": note}


def validate_customer_update(msg: Dict[str, Any]) -> Dict[str, Any]:
    _require_id(msg, "request_id")
    _require_id(msg, "branch_id")
    cid = msg.get("customer_id")
    if not isinstance(cid, int) or isinstance(cid, bool) or cid <= 0:
        raise MessageError("'customer_id' phải là số nguyên dương")
    name = msg.get("full_name")
    phone = msg.get("phone")
    if name is not None and (not isinstance(name, str) or len(name.strip()) > 100):
        raise MessageError("'full_name' không hợp lệ")
    if phone is not None and (not isinstance(phone, str) or not _PHONE_RE.match(phone.strip())):
        raise MessageError("'phone' không hợp lệ")
    name = (name or "").strip() or None
    phone = (phone or "").strip() or None
    if name is None and phone is None:
        raise MessageError("cần ít nhất 'full_name' hoặc 'phone'")
    return {"request_id": msg["request_id"], "branch_id": msg["branch_id"], "customer_id": cid,
            "full_name": name, "phone": phone}
