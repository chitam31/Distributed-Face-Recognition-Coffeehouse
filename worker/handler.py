"""Xử lý MỘT message từ RabbitMQ: giải mã -> nghiệp vụ -> gửi reply -> ack.

Quy tắc ack (trái tim của khả năng chịu lỗi):

  Kết quả xử lý                 Hành động với message gốc               Client nhận
  ----------------------------  --------------------------------------  -----------------
  Thành công                    reply TRƯỚC, ack SAU                    kết quả thật
  Message hỏng (MessageError)   reject(requeue=False) -> DLQ            ERROR
  Lỗi nghiệp vụ                 ack (thử lại cũng vô ích)               ERROR
  Lỗi tạm thời, còn lượt thử    nack(requeue=True) -> giao lại          (chưa có)
  Lỗi tạm thời, hết lượt thử    reject(requeue=False) -> DLQ            ERROR
  Worker chết trước khi ack     RabbitMQ tự giao lại cho worker khác    kết quả thật

Vì "reply trước, ack sau", mỗi yêu cầu được xử lý ÍT NHẤT MỘT LẦN; idempotency (recognition_logs,
orders.request_id) biến nó thành TÁC DỤNG đúng một lần.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from common.config import Settings
from common.errors import BusinessError, TransientError
from common.messages import (MessageError, Q_FACE, Status, T_CUSTOMER_UPDATE, T_ORDER, T_RECOGNIZE, decode,
                             validate_customer_update, validate_order, validate_recognize)

log = logging.getLogger("handler")

# kết cục xử lý
OK, POISON, BUSINESS, RETRY, DEAD = "ok", "poison", "business", "retry", "dead"


@dataclass
class Outcome:
    kind: str
    response: Optional[Dict[str, Any]] = None
    error: str = ""


def delivery_attempt(props, method) -> int:
    """Đây là lần giao thứ mấy. Quorum queue gắn header x-delivery-count (số lần đã bị trả lại)."""
    headers = getattr(props, "headers", None) or {}
    count = headers.get("x-delivery-count")
    if count is not None:
        return int(count) + 1
    return 2 if getattr(method, "redelivered", False) else 1


class MessageHandler:
    def __init__(self, service, settings: Settings, worker_name: str,
                 make_props: Optional[Callable[..., Any]] = None,
                 unroutable: Tuple[type, ...] = (),
                 sleep: Optional[Callable[[float], None]] = None):
        self.service = service
        self.s = settings
        self.worker_name = worker_name
        self._make_props = make_props or _pika_props
        self._unroutable = unroutable
        self._sleep = sleep or __import__("time").sleep

    def set_sleep(self, fn: Callable[[float], None]) -> None:
        """Dùng connection.sleep của pika để heartbeat vẫn chạy trong lúc chờ."""
        self._sleep = fn

    # ------------------------------------------------------------------ điểm vào
    def handle(self, channel, method, props, body: bytes, queue: str) -> None:
        attempt = delivery_attempt(props, method)
        outcome, request_id = self._process(body, queue, attempt)

        if self.s.demo_delay_ms > 0 and outcome.kind == OK:
            # Cửa sổ để demo "kill worker khi đang xử lý": DB đã ghi nhưng chưa reply/ack.
            log.warning("DEMO: dừng %d ms trước khi reply/ack (request=%s)", self.s.demo_delay_ms, request_id)
            self._sleep(self.s.demo_delay_ms / 1000.0)

        corr = getattr(props, "correlation_id", None) or request_id
        reply_to = getattr(props, "reply_to", None)
        tag = method.delivery_tag

        if outcome.kind == OK:
            self._reply(channel, reply_to, corr, outcome.response)
            channel.basic_ack(tag)
        elif outcome.kind == BUSINESS:
            self._reply(channel, reply_to, corr, self._error(request_id, outcome.error))
            channel.basic_ack(tag)
        elif outcome.kind in (POISON, DEAD):
            self._reply(channel, reply_to, corr, self._error(request_id, outcome.error))
            channel.basic_reject(tag, requeue=False)               # -> dead-letter exchange -> *.dlq
        else:                                                       # RETRY
            self._sleep(self.s.retry_delay_s)
            channel.basic_nack(tag, requeue=True)

    # ------------------------------------------------------------------ xử lý (không I/O với broker)
    def _process(self, body: bytes, queue: str, attempt: int) -> Tuple[Outcome, Optional[str]]:
        request_id: Optional[str] = None
        try:
            msg = decode(body)
            rid = msg.get("request_id")
            request_id = rid if isinstance(rid, str) else None
            return Outcome(OK, self._dispatch(msg, queue)), request_id
        except MessageError as exc:
            log.error("message hỏng (queue=%s): %s", queue, exc)
            return Outcome(POISON, error=f"message không hợp lệ: {exc}"), request_id
        except BusinessError as exc:
            log.warning("lỗi nghiệp vụ request=%s: %s", request_id, exc)
            return Outcome(BUSINESS, error=str(exc)), request_id
        except Exception as exc:                                    # noqa: BLE001
            # TransientError và mọi lỗi không lường trước đều được thử lại có giới hạn.
            kind = "tạm thời" if isinstance(exc, TransientError) else "không lường trước"
            if attempt >= self.s.max_deliveries:
                log.error("lỗi %s request=%s, hết %d lượt thử -> DLQ: %s", kind, request_id, attempt, exc,
                          exc_info=not isinstance(exc, TransientError))
                return Outcome(DEAD, error=f"xử lý thất bại sau {attempt} lần thử: {exc}"), request_id
            log.warning("lỗi %s request=%s (lần %d/%d), sẽ thử lại: %s", kind, request_id, attempt,
                        self.s.max_deliveries, exc)
            return Outcome(RETRY, error=str(exc)), request_id

    def _dispatch(self, msg: Dict[str, Any], queue: str) -> Dict[str, Any]:
        mtype = msg.get("type")
        if queue == Q_FACE:
            if mtype != T_RECOGNIZE:
                raise MessageError(f"queue {queue} chỉ nhận type='{T_RECOGNIZE}', nhận '{mtype}'")
            result = self.service.recognize(validate_recognize(msg))
        elif mtype == T_ORDER:
            result = self.service.create_order(validate_order(msg))
        elif mtype == T_CUSTOMER_UPDATE:
            result = self.service.update_customer(validate_customer_update(msg))
        else:
            raise MessageError(f"type '{mtype}' không được hỗ trợ")
        result["worker"] = self.worker_name
        return result

    # ------------------------------------------------------------------ gửi reply
    def _error(self, request_id: Optional[str], text: str) -> Dict[str, Any]:
        return {"request_id": request_id, "status": Status.ERROR, "message": text,
                "customer": None, "last_order": None, "similarity": None, "latency_ms": 0,
                "worker": self.worker_name}

    def _reply(self, channel, reply_to: Optional[str], correlation_id: Optional[str],
               response: Dict[str, Any]) -> None:
        """Publish về queue reply của đúng chi nhánh (default exchange, routing_key = reply_to).
        Queue reply biến mất (client đã ngắt) -> bỏ qua: client sẽ retry cùng request_id và nhận lại
        kết quả từ nhật ký, nên mất reply không làm mất dữ liệu."""
        if not reply_to:
            return
        from common.messages import encode
        props = self._make_props(correlation_id=correlation_id)
        try:
            channel.basic_publish(exchange="", routing_key=reply_to, body=encode(response),
                                  properties=props, mandatory=True)
        except self._unroutable:
            log.warning("không gửi được reply tới %s (client đã ngắt?), vẫn ack; client sẽ retry", reply_to)


def _pika_props(correlation_id=None):
    import pika
    return pika.BasicProperties(correlation_id=correlation_id, content_type="application/json", delivery_mode=1)


def pika_unroutable_exceptions() -> Tuple[type, ...]:
    import pika.exceptions as pe
    return (pe.UnroutableError, pe.NackError)
