"""Kho quan hệ MySQL 8 (PyMySQL): khách, ảnh, đơn hàng, nhật ký nhận diện.

Quy ước:
  * Mọi thời gian lưu UTC (SET time_zone='+00:00'); client tự đổi sang giờ địa phương khi hiển thị.
  * Lỗi kết nối/timeout -> TransientError (worker sẽ cho RabbitMQ giao lại message).
  * Vi phạm ràng buộc xử lý riêng từng chỗ (trùng request_id = yêu cầu lặp, FK = lỗi nghiệp vụ).
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from common.errors import BusinessError, TransientError

log = logging.getLogger("repo_mysql")

ER_DUP_ENTRY = 1062
ER_NO_REFERENCED_ROW = 1452


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds")
    if isinstance(value, Decimal):
        return float(value)
    return value


def _row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return None if row is None else {k: _iso(v) for k, v in row.items()}


def parse_iso_utc(text: Optional[str]) -> datetime:
    """ISO-8601 -> datetime UTC không tzinfo (đúng định dạng DATETIME của MySQL). Sai định dạng -> bây giờ."""
    if text:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            pass
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MySqlRepo:
    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        self._kw = dict(host=host, port=port, user=user, password=password, database=database)
        self._conn = None

    # ------------------------------------------------------------- kết nối
    def _connection(self):
        import pymysql
        from pymysql.cursors import DictCursor
        try:
            if self._conn is None:
                self._conn = pymysql.connect(
                    charset="utf8mb4", cursorclass=DictCursor, autocommit=False,
                    connect_timeout=5, read_timeout=15, write_timeout=15,
                    init_command="SET time_zone='+00:00'", **self._kw)
            else:
                self._conn.ping(reconnect=True)
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError, OSError) as exc:
            self._conn = None
            raise TransientError(f"MySQL không khả dụng: {exc}") from exc
        return self._conn

    @contextmanager
    def _tx(self):
        """Một transaction: commit khi thoát bình thường, rollback khi có lỗi. Lỗi kết nối -> TransientError."""
        import pymysql
        conn = self._connection()
        try:
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as exc:
            self._conn = None
            raise TransientError(f"MySQL lỗi giữa chừng: {exc}") from exc
        except BaseException:
            try:
                conn.rollback()
            except Exception:                                  # pragma: no cover
                self._conn = None
            raise

    # ------------------------------------------------------------- chi nhánh
    def ensure_branch(self, branch_id: str) -> None:
        with self._tx() as cur:
            cur.execute("INSERT IGNORE INTO branches (branch_id, name) VALUES (%s, %s)",
                        (branch_id, f"Chi nhánh {branch_id}"))

    # ------------------------------------------------------------- nhật ký nhận diện (idempotency)
    def get_log(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self._tx() as cur:
            cur.execute("SELECT request_id, branch_id, customer_id, status, similarity, latency_ms "
                        "FROM recognition_logs WHERE request_id = %s", (request_id,))
            return _row(cur.fetchone())

    def insert_log(self, request_id: str, branch_id: str, customer_id: Optional[int], status: str,
                   similarity: Optional[float], latency_ms: int, worker: str) -> bool:
        """True nếu ghi mới; False nếu request_id đã có (worker khác xử lý trước) - không phải lỗi."""
        import pymysql
        try:
            with self._tx() as cur:
                cur.execute(
                    "INSERT INTO recognition_logs (request_id, branch_id, customer_id, status, similarity, "
                    "latency_ms, worker) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (request_id, branch_id, customer_id, status, similarity, latency_ms, worker))
            return True
        except pymysql.err.IntegrityError as exc:
            if exc.args and exc.args[0] == ER_DUP_ENTRY:
                return False
            raise

    # ------------------------------------------------------------- khách
    def create_customer(self, branch_id: str, full_name: Optional[str]) -> int:
        self.ensure_branch(branch_id)
        with self._tx() as cur:
            cur.execute("INSERT INTO customers (full_name, first_branch_id, last_seen_at) "
                        "VALUES (%s, %s, NOW(3))", (full_name, branch_id))
            return int(cur.lastrowid)

    def delete_customer(self, customer_id: int) -> None:
        """Dùng cho bù trừ (saga) và reconcile. Ảnh bị xoá theo ON DELETE CASCADE."""
        with self._tx() as cur:
            cur.execute("DELETE FROM customers WHERE customer_id = %s", (customer_id,))

    def get_customer(self, customer_id: int) -> Optional[Dict[str, Any]]:
        with self._tx() as cur:
            cur.execute("SELECT customer_id, full_name, phone, first_branch_id, created_at, last_seen_at "
                        "FROM customers WHERE customer_id = %s", (customer_id,))
            return _row(cur.fetchone())

    def touch_customer(self, customer_id: int) -> None:
        with self._tx() as cur:
            cur.execute("UPDATE customers SET last_seen_at = NOW(3) WHERE customer_id = %s", (customer_id,))

    def update_customer(self, customer_id: int, full_name: Optional[str], phone: Optional[str]) -> bool:
        sets, args = [], []
        if full_name is not None:
            sets.append("full_name = %s"); args.append(full_name)
        if phone is not None:
            sets.append("phone = %s"); args.append(phone)
        if not sets:
            return False
        args.append(customer_id)
        with self._tx() as cur:
            cur.execute(f"UPDATE customers SET {', '.join(sets)} WHERE customer_id = %s", args)
            return cur.rowcount > 0 or self._exists(cur, customer_id)

    @staticmethod
    def _exists(cur, customer_id: int) -> bool:
        cur.execute("SELECT 1 FROM customers WHERE customer_id = %s", (customer_id,))
        return cur.fetchone() is not None

    def customer_ids(self) -> List[int]:
        with self._tx() as cur:
            cur.execute("SELECT customer_id FROM customers ORDER BY customer_id")
            return [int(r["customer_id"]) for r in cur.fetchall()]

    # ------------------------------------------------------------- ảnh
    def add_photo(self, customer_id: int, file_path: str, branch_id: str, captured_at: Optional[str]) -> int:
        with self._tx() as cur:
            cur.execute("INSERT INTO customer_photos (customer_id, file_path, branch_id, captured_at) "
                        "VALUES (%s,%s,%s,%s)", (customer_id, file_path, branch_id, parse_iso_utc(captured_at)))
            return int(cur.lastrowid)

    # ------------------------------------------------------------- đơn hàng
    def get_last_order(self, customer_id: int) -> Optional[Dict[str, Any]]:
        """Đơn gần nhất (kèm các món). Dùng INDEX (customer_id, ordered_at DESC)."""
        with self._tx() as cur:
            cur.execute("SELECT order_id, branch_id, ordered_at, total_amount, note FROM orders "
                        "WHERE customer_id = %s ORDER BY ordered_at DESC, order_id DESC LIMIT 1", (customer_id,))
            order = _row(cur.fetchone())
            if order is None:
                return None
            cur.execute("SELECT item_name, quantity, unit_price, options FROM order_items "
                        "WHERE order_id = %s ORDER BY item_id", (order["order_id"],))
            order["items"] = [_row(r) for r in cur.fetchall()]
            return order

    def get_frequent_items(self, customer_id: int, limit: int = 3) -> List[Dict[str, Any]]:
        """Món khách gọi nhiều nhất (tổng số lượng trên MỌI đơn, MỌI chi nhánh) để gợi ý "như mọi khi?".
        Bằng nhau thì món được gọi gần đây hơn xếp trước, rồi theo tên cho kết quả ổn định.
        Chỉ đọc; dùng INDEX (customer_id, ordered_at) của orders rồi nối order_items theo order_id."""
        with self._tx() as cur:
            cur.execute("SELECT oi.item_name AS item_name, CAST(SUM(oi.quantity) AS SIGNED) AS total_qty, "
                        "COUNT(DISTINCT oi.order_id) AS n_orders "
                        "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id "
                        "WHERE o.customer_id = %s GROUP BY oi.item_name "
                        "ORDER BY total_qty DESC, MAX(o.ordered_at) DESC, oi.item_name LIMIT %s",
                        (customer_id, int(limit)))
            return [_row(r) for r in cur.fetchall()]

    def create_order(self, request_id: str, branch_id: str, customer_id: int,
                     items: List[Dict[str, Any]], note: Optional[str]) -> Tuple[int, float, bool]:
        """Ghi orders + order_items trong MỘT transaction.
        Trả về (order_id, total, created). created=False nghĩa là request_id này đã được ghi trước đó
        (client retry / message giao lại) -> trả lại đơn cũ, không tạo đơn thứ hai."""
        import pymysql
        self.ensure_branch(branch_id)
        total = round(sum(i["quantity"] * i["unit_price"] for i in items), 2)
        try:
            with self._tx() as cur:
                cur.execute("INSERT INTO orders (request_id, customer_id, branch_id, total_amount, note) "
                            "VALUES (%s,%s,%s,%s,%s)", (request_id, customer_id, branch_id, total, note))
                order_id = int(cur.lastrowid)
                cur.executemany(
                    "INSERT INTO order_items (order_id, item_name, quantity, unit_price, options) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    [(order_id, i["item_name"], i["quantity"], i["unit_price"], i["options"]) for i in items])
            return order_id, total, True
        except pymysql.err.IntegrityError as exc:
            code = exc.args[0] if exc.args else None
            if code == ER_DUP_ENTRY:
                with self._tx() as cur:
                    cur.execute("SELECT order_id, total_amount FROM orders WHERE request_id = %s", (request_id,))
                    row = cur.fetchone()
                return int(row["order_id"]), float(row["total_amount"]), False
            if code == ER_NO_REFERENCED_ROW:
                raise BusinessError(f"khách hàng {customer_id} không tồn tại") from exc
            raise

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
