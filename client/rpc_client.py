"""Client RPC trên RabbitMQ cho một chi nhánh.

Mẫu RPC của RabbitMQ:  publish (correlation_id = request_id, reply_to = queue riêng của chi nhánh)
rồi chờ reply có correlation_id khớp.

Chịu lỗi phía client:
  * timeout (mặc định 10s) -> GỬI LẠI với CÙNG request_id; worker tra recognition_logs nên không tạo khách
    trùng (idempotent). Tối đa `max_attempts` lần.
  * mất kết nối -> nối lại với back-off rồi gửi lại.
  * publisher confirms + mandatory: biết chắc broker đã nhận message vào queue; nếu không có queue
    nào nhận (chưa khởi tạo topology) thì báo lỗi rõ ràng thay vì chờ timeout.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Optional

import pika
import pika.exceptions as pe

from common.config import Settings
from common.messages import EXCHANGE, MessageError, Q_FACE, Q_ORDERS, REPLY_PREFIX, decode, encode
from common.rabbit import build_connection_params

log = logging.getLogger("rpc_client")


class RpcError(Exception):
    """Lỗi giao tiếp không thể khắc phục bằng cách thử lại."""


class RpcTimeout(RpcError):
    """Hết số lần thử mà vẫn không có reply."""


class BranchRpcClient:
    def __init__(self, settings: Settings, branch_id: str, timeout_s: float = 10.0, max_attempts: int = 3):
        self.s = settings
        self.branch_id = branch_id
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self._conn: Optional[pika.BlockingConnection] = None
        self._ch = None
        self._reply_queue: Optional[str] = None
        self._replies: Dict[str, Dict[str, Any]] = {}
        self.last_attempts = 0               # số lần gửi của lần call() gần nhất (để hiển thị/đo đạc)

    # ------------------------------------------------------------------ kết nối
    def connect(self) -> None:
        self.close()
        self._conn = pika.BlockingConnection(build_connection_params(self.s, f"branch-{self.branch_id}"))
        self._ch = self._conn.channel()
        self._ch.confirm_delivery()
        # Queue trả lời riêng của chi nhánh: exclusive (chỉ kết nối này dùng) + auto_delete (tự xoá khi ngắt).
        self._reply_queue = f"{REPLY_PREFIX}{self.branch_id}.{uuid.uuid4().hex[:12]}"
        self._ch.queue_declare(queue=self._reply_queue, exclusive=True, auto_delete=True)
        self._ch.basic_consume(queue=self._reply_queue, on_message_callback=self._on_reply, auto_ack=True)
        self._replies.clear()
        log.info("chi nhánh %s đã kết nối, reply queue = %s", self.branch_id, self._reply_queue)

    def close(self) -> None:
        if self._conn is not None and self._conn.is_open:
            try:
                self._conn.close()
            except Exception:                                      # pragma: no cover
                pass
        self._conn = self._ch = None

    def _is_connected(self) -> bool:
        return self._conn is not None and self._conn.is_open and self._ch is not None and self._ch.is_open

    def _on_reply(self, _ch, _method, props, body) -> None:
        try:
            self._replies[props.correlation_id] = decode(body)
        except MessageError:
            log.warning("bỏ qua reply không hợp lệ")

    # ------------------------------------------------------------------ gọi
    def recognize(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return self.call(message, Q_FACE)

    def send_command(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Đơn hàng / cập nhật khách đi qua queue 'orders'."""
        return self.call(message, Q_ORDERS)

    def call(self, message: Dict[str, Any], routing_key: str) -> Dict[str, Any]:
        request_id = message["request_id"]
        body = encode(message)
        backoff = 1.0
        for attempt in range(1, self.max_attempts + 1):
            self.last_attempts = attempt
            try:
                if not self._is_connected():
                    self.connect()
                props = pika.BasicProperties(correlation_id=request_id, reply_to=self._reply_queue,
                                             delivery_mode=2, content_type="application/json")
                self._ch.basic_publish(exchange=EXCHANGE, routing_key=routing_key, body=body,
                                       properties=props, mandatory=True)
                reply = self._wait(request_id, self.timeout_s)
                if reply is not None:
                    return reply
                log.warning("timeout %.0fs (lần %d/%d) request=%s; gửi lại cùng request_id",
                            self.timeout_s, attempt, self.max_attempts, request_id)
            except pe.UnroutableError as exc:
                raise RpcError("RabbitMQ không có queue nhận yêu cầu (worker/topology chưa được khởi tạo?)") from exc
            except pe.ProbableAccessDeniedError as exc:
                raise RpcError(f"không đủ quyền trên RabbitMQ (sai tài khoản chi nhánh?): {exc}") from exc
            except pe.ProbableAuthenticationError as exc:
                raise RpcError("sai tên đăng nhập/mật khẩu RabbitMQ") from exc
            except pe.ChannelClosedByBroker as exc:
                self.close()
                if exc.reply_code in (403, 404):                  # không đủ quyền / thiếu exchange: thử lại vô ích
                    raise RpcError(f"RabbitMQ từ chối ({exc.reply_code}): {exc.reply_text}. "
                                   "Kiểm tra quyền tài khoản chi nhánh hoặc chạy tools.init_topology") from exc
                log.warning("kênh bị broker đóng (lần %d/%d): %s", attempt, self.max_attempts, exc)
            except (pe.AMQPError, OSError) as exc:
                log.warning("lỗi kết nối (lần %d/%d): %s", attempt, self.max_attempts, exc)
                self.close()
                if attempt < self.max_attempts:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 8.0)
        raise RpcTimeout(f"không nhận được trả lời sau {self.max_attempts} lần thử (request {request_id})")

    def _wait(self, request_id: str, timeout_s: float) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + timeout_s
        while True:
            if request_id in self._replies:
                return self._replies.pop(request_id)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._conn.process_data_events(time_limit=min(0.5, remaining))
