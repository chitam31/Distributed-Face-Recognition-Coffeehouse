"""Điểm vào của recognition worker.

    python -m worker.worker

Mỗi worker là một process độc lập, tất cả cùng đọc hàng đợi face.requests (mẫu "competing consumers").
Chạy nhiều bản (docker compose --scale worker=N) để tăng thông lượng; RabbitMQ chia việc với prefetch=1
nên worker nhanh nhận nhiều việc hơn, worker bận không bị dồn thêm.
"""
from __future__ import annotations

import functools
import logging
import os
import signal
import socket
import sys
import time

from common.config import Settings, load_settings
from common.messages import Q_FACE, Q_ORDERS
from common.rabbit import build_connection_params, declare_topology
from worker.face_engine import FaceEngine
from worker.handler import MessageHandler, pika_unroutable_exceptions
from worker.photo_store import PhotoStore
from worker.repo_mysql import MySqlRepo
from worker.repo_vector import PgVectorRepo
from worker.service import RecognitionService

log = logging.getLogger("worker")


def build_service(s: Settings, worker_name: str) -> RecognitionService:
    engine = FaceEngine(s)
    mysql = MySqlRepo(**s.mysql_kwargs)
    vectors = PgVectorRepo(s.pg_dsn)
    photos = PhotoStore(s.photo_dir)
    return RecognitionService(engine, mysql, vectors, photos, s, worker_name)


def run(s: Settings) -> None:
    import pika

    worker_name = f"{socket.gethostname()}-{os.getpid()}"
    service = build_service(s, worker_name)
    log.info("worker %s khởi động, đang nạp model...", worker_name)
    service.engine.warm_up()
    log.info("model sẵn sàng")

    handler = MessageHandler(service, s, worker_name, unroutable=pika_unroutable_exceptions())
    stopping = {"flag": False}
    state = {"conn": None, "ch": None}

    def request_stop(signum, _frame):
        # SIGTERM (docker stop): ngừng nhận việc mới, xử lý nốt message đang làm rồi thoát.
        log.warning("nhận tín hiệu %s, đang dừng nhẹ nhàng...", signum)
        stopping["flag"] = True
        conn, ch = state["conn"], state["ch"]
        if conn is not None and ch is not None:
            try:
                conn.add_callback_threadsafe(ch.stop_consuming)
            except Exception:                                      # pragma: no cover
                pass

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    backoff = 1.0
    while not stopping["flag"]:
        try:
            conn = pika.BlockingConnection(build_connection_params(s, f"worker-{worker_name}"))
            ch = conn.channel()
            ch.confirm_delivery()                    # biết reply có được broker nhận/định tuyến không
            declare_topology(ch, s)
            ch.basic_qos(prefetch_count=1)           # mỗi consumer giữ tối đa 1 message chưa ack
            handler.set_sleep(conn.sleep)            # chờ mà vẫn giữ heartbeat
            state["conn"], state["ch"] = conn, ch

            for queue in (Q_FACE, Q_ORDERS):
                ch.basic_consume(queue=queue, on_message_callback=functools.partial(_on_message, handler, queue))
            log.info("đang chờ message trên %s và %s (prefetch=1)", Q_FACE, Q_ORDERS)
            backoff = 1.0
            ch.start_consuming()
            if stopping["flag"]:
                break
        except pika.exceptions.AMQPConnectionError as exc:
            log.error("mất kết nối RabbitMQ: %s; thử lại sau %.0fs", exc, backoff)
        except pika.exceptions.AMQPError as exc:
            log.error("lỗi AMQP: %s; kết nối lại sau %.0fs", exc, backoff)
        finally:
            _close_quietly(state["conn"])
            state["conn"] = state["ch"] = None
        if not stopping["flag"]:
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
    log.info("worker %s đã dừng", worker_name)


def _on_message(handler: MessageHandler, queue: str, ch, method, props, body) -> None:
    started = time.perf_counter()
    handler.handle(ch, method, props, body, queue)
    log.info("xong message queue=%s tag=%s trong %.0f ms", queue, method.delivery_tag,
             (time.perf_counter() - started) * 1000)


def _close_quietly(conn) -> None:
    if conn is not None and conn.is_open:
        try:
            conn.close()
        except Exception:                                          # pragma: no cover
            pass


def main() -> int:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("pika").setLevel(logging.WARNING)
    run(load_settings())
    return 0


if __name__ == "__main__":
    sys.exit(main())
