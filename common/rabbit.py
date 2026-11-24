"""Kết nối RabbitMQ và khai báo topology dùng chung (pika được import muộn).

Topology:

    chi nhánh --publish--> [coffee.requests] --face.requests--> (Q face.requests) --> worker x N
                                             --orders---------> (Q orders)        --> worker x N
    worker ----reply------> default exchange, routing_key = reply_to (reply.<branch>.<uuid>)
    message hỏng / quá số lần giao --> [coffee.dlx] --> face.requests.dlq / orders.dlq

Cả worker và client khai báo bằng đúng một hàm này để tham số queue luôn khớp nhau
(RabbitMQ báo PRECONDITION_FAILED nếu khai báo lại queue với tham số khác).
"""
from __future__ import annotations

import ssl
from typing import Any, Dict

from .config import Settings
from .messages import (DLX, EXCHANGE, Q_FACE, Q_FACE_DLQ, Q_ORDERS, Q_ORDERS_DLQ)


def build_connection_params(s: Settings, client_name: str = "coffee-client"):
    import pika

    ssl_options = None
    if s.rabbit_tls:
        ctx = ssl.create_default_context(cafile=s.rabbit_ca_file)
        ssl_options = pika.SSLOptions(ctx, s.rabbit_host)
    return pika.ConnectionParameters(
        host=s.rabbit_host,
        port=s.rabbit_port,
        virtual_host=s.rabbit_vhost,
        credentials=pika.PlainCredentials(s.rabbit_user, s.rabbit_pass),
        ssl_options=ssl_options,
        heartbeat=30,
        blocked_connection_timeout=60,
        connection_attempts=1,
        socket_timeout=5,
        client_properties={"connection_name": client_name},
    )


def queue_arguments(s: Settings, dead_letter_key: str) -> Dict[str, Any]:
    args: Dict[str, Any] = {
        "x-dead-letter-exchange": DLX,
        "x-dead-letter-routing-key": dead_letter_key,
    }
    if s.queue_type == "quorum":
        # Quorum queue: bền vững, có đếm số lần giao (x-delivery-count) và giới hạn giao.
        args["x-queue-type"] = "quorum"
        args["x-delivery-limit"] = s.max_deliveries
    return args


def declare_topology(channel, s: Settings) -> None:
    """Khai báo exchange, queue, binding. An toàn khi gọi lặp lại (idempotent)."""
    channel.exchange_declare(exchange=DLX, exchange_type="direct", durable=True)
    for dlq in (Q_FACE_DLQ, Q_ORDERS_DLQ):
        channel.queue_declare(queue=dlq, durable=True)
        channel.queue_bind(queue=dlq, exchange=DLX, routing_key=dlq)

    channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)
    for queue, dlq in ((Q_FACE, Q_FACE_DLQ), (Q_ORDERS, Q_ORDERS_DLQ)):
        channel.queue_declare(queue=queue, durable=True, arguments=queue_arguments(s, dlq))
        channel.queue_bind(queue=queue, exchange=EXCHANGE, routing_key=queue)
