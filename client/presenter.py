"""Chuyển kết quả của worker thành văn bản dễ đọc cho nhân viên quán (dùng chung cho CLI và GUI)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from common.messages import Status

STATUS_TEXT = {
    Status.RECOGNIZED: "KHÁCH QUEN",
    Status.NEW_CUSTOMER: "KHÁCH MỚI",
    Status.UNKNOWN: "CHƯA NHẬN RA",
    Status.NO_FACE: "KHÔNG THẤY MẶT",
    Status.MULTIPLE_FACES: "NHIỀU KHUÔN MẶT",
    Status.ERROR: "LỖI",
    Status.ORDER_SAVED: "ĐÃ LƯU ĐƠN",
    Status.CUSTOMER_UPDATED: "ĐÃ CẬP NHẬT",
}

STATUS_COLOR = {
    Status.RECOGNIZED: "#1b7f3b",
    Status.NEW_CUSTOMER: "#1565c0",
    Status.UNKNOWN: "#6d4c41",
    Status.NO_FACE: "#c77700",
    Status.MULTIPLE_FACES: "#c77700",
    Status.ERROR: "#b71c1c",
    Status.ORDER_SAVED: "#1b7f3b",
    Status.CUSTOMER_UPDATED: "#1b7f3b",
}


def money(value: Any) -> str:
    try:
        return f"{float(value):,.0f}đ".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)


def local_time(iso: Optional[str]) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso


def order_lines(order: Optional[Dict[str, Any]]) -> List[str]:
    if not order:
        return ["(chưa có đơn nào)"]
    lines = [f"Đơn #{order['order_id']} · chi nhánh {order.get('branch_id', '?')} · {local_time(order.get('ordered_at'))}"]
    for it in order.get("items", []):
        opt = f" ({it['options']})" if it.get("options") else ""
        lines.append(f"  • {it['quantity']} x {it['item_name']}{opt}  {money(it.get('unit_price', 0))}")
    lines.append(f"  Tổng: {money(order.get('total_amount', 0))}")
    return lines


def frequent_line(items: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    """"Hay gọi: Cà phê sữa đá ×5, Latte ×2" (None nếu chưa có dữ liệu; worker cũ không gửi trường này)."""
    if not items:
        return None
    return "Hay gọi: " + ", ".join(f"{i['item_name']} ×{i['total_qty']}" for i in items)


def describe(resp: Dict[str, Any]) -> List[str]:
    status = resp.get("status", "?")
    lines = [f"[{STATUS_TEXT.get(status, status)}]"]
    cust = resp.get("customer")
    if cust:
        name = cust.get("full_name") or "(chưa có tên)"
        lines.append(f"Khách #{cust['customer_id']}: {name}" + (f" · {cust['phone']}" if cust.get("phone") else ""))
    if status == Status.RECOGNIZED:
        lines.append("Đơn gần nhất - hỏi khách có dùng lại không:")
        lines += order_lines(resp.get("last_order"))
        freq = frequent_line(resp.get("frequent_items"))
        if freq:
            lines.append(freq)
    if status == Status.NEW_CUSTOMER:
        lines.append("Khách đã được lưu cho lần sau. Có thể hỏi tên / số điện thoại.")
    if status == Status.ORDER_SAVED:
        lines.append(f"Đơn #{resp.get('order_id')} · tổng {money(resp.get('total_amount', 0))}")
    if resp.get("message"):
        lines.append(f"Ghi chú: {resp['message']}")
    meta = []
    if resp.get("similarity") is not None:
        meta.append(f"độ giống {resp['similarity']:.3f}")
    if resp.get("latency_ms") is not None:
        meta.append(f"worker xử lý {resp['latency_ms']} ms")
    if resp.get("worker"):
        meta.append(f"worker {resp['worker']}")
    if resp.get("duplicate"):
        meta.append("kết quả phát lại (yêu cầu trùng)")
    if meta:
        lines.append(" · ".join(meta))
    return lines
