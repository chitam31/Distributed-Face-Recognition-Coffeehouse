"""Công cụ phục vụ demo chịu lỗi và quá tải (dùng tài khoản quản trị RabbitMQ).

    python -m tools.chaos poison              # gửi message hỏng -> worker reject -> vào face.requests.dlq
    python -m tools.chaos dlq                 # xem số message và nội dung trong DLQ (không lấy mất)
    python -m tools.chaos purge-dlq           # xoá sạch DLQ
    python -m tools.chaos flood --image a.jpg --count 100    # dồn 100 request vào queue để thấy "overload"
    python -m tools.chaos status              # độ sâu các queue
    python -m tools.chaos watch --out results/queue_depth_w3.csv   # ghi độ sâu face.requests theo thời gian (CSV)

flood không đặt reply_to và dùng auto_enroll=false: worker vẫn xử lý đầy đủ nhưng không tạo khách rác.
flood --enroll (auto_enroll=true) dùng để thử tranh chấp thật: gửi N lần CÙNG ảnh của một người CHƯA enroll; nhờ khoá
advisory chỉ được tạo đúng 1 khách (kiểm bằng cách đếm bảng customers trước và sau).
Mở RabbitMQ UI (cổng 15672 -> Queues) để xem queue face.requests tăng rồi giảm khi worker xử lý dần.
"""
from __future__ import annotations

import argparse
import json
import sys

import pika

from client.imaging import load_jpeg_for_sending
from common.config import load_settings
from common.messages import EXCHANGE, Q_FACE, Q_FACE_DLQ, Q_ORDERS, Q_ORDERS_DLQ, build_recognize_request, encode
from common.rabbit import build_connection_params, declare_topology
from tools.amqp_peek import peek_queue


def connect(s):
    conn = pika.BlockingConnection(build_connection_params(s, "chaos-tool"))
    ch = conn.channel()
    ch.confirm_delivery()
    declare_topology(ch, s)
    return conn, ch


def depth(ch, queue_name: str) -> int:
    return ch.queue_declare(queue=queue_name, passive=True).method.message_count


def cmd_status(ch, _args) -> None:
    for q in (Q_FACE, Q_ORDERS, Q_FACE_DLQ, Q_ORDERS_DLQ):
        print(f"{q:<22} {depth(ch, q):>6} message")


def cmd_watch(ch, args) -> None:
    """Ghi độ sâu hàng đợi (số message đang CHỜ, không tính message worker đang cầm chưa ack) theo thời gian.

    Chạy ở một terminal TRƯỚC, rồi dồn tải ở terminal khác; tự dừng khi queue đã về 0 liên tục `--idle-stop` giây
    sau khi từng có message, hoặc khi hết `--duration`. Ra file CSV: t_s,ready,consumers."""
    import csv
    import os
    import time
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    t0 = time.perf_counter()
    peak, empty_since = 0, None
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "ready", "consumers"])
        print(f"theo dõi {args.queue} mỗi {args.interval}s -> {args.out}  (Ctrl+C để dừng sớm)")
        while True:
            t = time.perf_counter() - t0
            m = ch.queue_declare(queue=args.queue, passive=True).method
            w.writerow([f"{t:.2f}", m.message_count, m.consumer_count])
            fh.flush()
            print(f"{t:7.1f}s  ready={m.message_count:<5} consumers={m.consumer_count}")
            peak = max(peak, m.message_count)
            if m.message_count == 0 and peak > 0:
                empty_since = t if empty_since is None else empty_since
                if t - empty_since >= args.idle_stop:
                    break
            else:
                empty_since = None
            if t >= args.duration:
                break
            time.sleep(args.interval)
    print(f"xong: đỉnh {peak} message chờ; đã ghi {args.out}")


def cmd_poison(ch, _args) -> None:
    bad = [b"{ day khong phai json", encode({"type": "face.recognize", "request_id": "poison-1", "branch_id": "A"}),
           encode({"type": "face.recognize", "request_id": "poison-2", "branch_id": "A", "image_b64": "@@@@"})]
    for body in bad:
        ch.basic_publish(exchange=EXCHANGE, routing_key=Q_FACE, body=body,
                         properties=pika.BasicProperties(delivery_mode=2), mandatory=True)
    print(f"đã gửi {len(bad)} message hỏng vào {Q_FACE}; xem log worker và `python -m tools.chaos dlq`")


def cmd_dlq(ch, _args) -> None:
    for q in (Q_FACE_DLQ, Q_ORDERS_DLQ):
        n = depth(ch, q)
        print(f"{q}: {n} message")
        for props, body in peek_queue(ch, q, min(n, 10)):
            reason = ""
            deaths = (getattr(props, "headers", None) or {}).get("x-death")
            if deaths:
                reason = f" lý do={deaths[0].get('reason')} từ={deaths[0].get('queue')}"
            print(f"  - {len(body)} byte{reason}: {body[:90]!r}")


def cmd_purge(ch, _args) -> None:
    for q in (Q_FACE_DLQ, Q_ORDERS_DLQ):
        ch.queue_purge(q)
    print("đã xoá sạch các DLQ")


def cmd_flood(ch, args) -> None:
    jpeg = load_jpeg_for_sending(args.image)
    for _ in range(args.count):
        msg = build_recognize_request(args.branch, jpeg, auto_enroll=bool(args.enroll))
        ch.basic_publish(exchange=EXCHANGE, routing_key=Q_FACE, body=encode(msg),
                         properties=pika.BasicProperties(delivery_mode=2, correlation_id=msg["request_id"]),
                         mandatory=True)
    print(f"đã dồn {args.count} request; độ sâu hiện tại của {Q_FACE}: {depth(ch, Q_FACE)}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host"), p.add_argument("--user"), p.add_argument("--password")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("status", "poison", "dlq", "purge-dlq"):
        sub.add_parser(name)
    wt = sub.add_parser("watch")
    wt.add_argument("--queue", default=Q_FACE)
    wt.add_argument("--interval", type=float, default=0.5)
    wt.add_argument("--duration", type=float, default=180.0)
    wt.add_argument("--idle-stop", type=float, default=3.0, dest="idle_stop")
    wt.add_argument("--out", default="results/queue_depth.csv")
    f = sub.add_parser("flood")
    f.add_argument("--image", required=True)
    f.add_argument("--count", type=int, default=100)
    f.add_argument("--branch", default="A")
    f.add_argument("--enroll", action="store_true", help="auto_enroll=true (thử tranh chấp khách mới)")
    args = p.parse_args()

    from dataclasses import replace
    s = load_settings()
    ov = {k: v for k, v in (("rabbit_host", args.host), ("rabbit_user", args.user), ("rabbit_pass", args.password)) if v}
    s = replace(s, **ov) if ov else s
    conn, ch = connect(s)
    try:
        {"status": cmd_status, "poison": cmd_poison, "dlq": cmd_dlq, "purge-dlq": cmd_purge, "flood": cmd_flood, "watch": cmd_watch}[args.cmd](ch, args)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
