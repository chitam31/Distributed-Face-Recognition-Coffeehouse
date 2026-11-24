"""Khai báo exchange / queue / binding trên RabbitMQ bằng tài khoản quản trị.

Worker cũng tự khai báo khi khởi động, nhưng chạy công cụ này trước giúp client chi nhánh (chỉ có quyền
publish, không có quyền tạo queue) hoạt động ngay cả khi chưa có worker nào chạy.

    python -m tools.init_topology
"""
from __future__ import annotations

import sys

import pika

from common.config import load_settings
from common.rabbit import build_connection_params, declare_topology


def main() -> int:
    s = load_settings()
    conn = pika.BlockingConnection(build_connection_params(s, "init-topology"))
    try:
        declare_topology(conn.channel(), s)
    finally:
        conn.close()
    print(f"đã khai báo topology trên {s.rabbit_host} (loại queue: {s.queue_type})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
