"""Trang quản trị CHỈ ĐỌC của máy chủ (máy lab): nhìn trực tiếp hệ phân tán đang chạy.

    docker compose -f docker-compose.yml -f docker-compose.admin.yml up -d admin    # trên máy lab
    # rồi mở http://127.0.0.1:8090 (trên máy lab, hoặc qua đường hầm SSH) và đăng nhập tài khoản admin

Trang hiện, làm mới mỗi 2 giây:
  * yêu cầu đến từ chi nhánh nào, worker nào xử lý, mất bao nhiêu ms (bảng recognition_logs, một đồng hồ: MySQL);
  * độ sâu từng hàng đợi, số consumer, các kết nối đang mở và TÀI KHOẢN đã được RabbitMQ xác thực (management API);
  * số khách, số đơn; khi bấm nút: đối chiếu MySQL với pgvector (cùng phép tính với `python -m tools.reconcile`).

Vì sao CHỈ ĐỌC, và chỉ đọc đến đâu:
  * Không có nút nào sửa dữ liệu, không có đường dẫn POST nào ngoài đăng nhập/đăng xuất.
  * Phiên MySQL đặt `SET SESSION TRANSACTION READ ONLY`, phiên PostgreSQL đặt `default_transaction_read_only=on`:
    nếu mã có lỗi lỡ gửi lệnh ghi thì CSDL từ chối. Tài khoản `coffee` vẫn có quyền ghi (dùng chung với worker); muốn
    chặn ở mức quyền thì tạo user chỉ có SELECT và đặt ADMIN_MYSQL_USER/ADMIN_PG_USER (chưa làm, xem hướng dẫn).
  * Gọi management API của RabbitMQ chỉ bằng GET.
  * Không hiện tên, số điện thoại hay ảnh khách: trang này để xem vận hành, chỉ cần mã khách.

Hai lưu ý thiết kế có thể bị hỏi:
  * Kết nối MySQL dùng autocommit=True. Nếu để autocommit=False, InnoDB (REPEATABLE READ) giữ nguyên ảnh chụp của
    transaction đầu tiên, và trang sẽ hiện mãi số liệu cũ dù worker ghi thêm.
  * Mỗi nguồn (MySQL, RabbitMQ, PostgreSQL) hỏng độc lập: một nguồn lỗi thì phần đó báo lỗi, phần khác vẫn hiện.
    Đó chính là "lỗi cục bộ" (partial failure) của hệ phân tán, nhìn thấy được trên trang.
  * `branch_id` trong nhật ký là chi nhánh do máy gửi tự khai trong message; bảng kết nối mới là tài khoản đã được
    broker kiểm mật khẩu. Hai cột này nên khớp nhau (branch_b gửi B).
"""
from __future__ import annotations

import argparse
import base64
import hmac
import json
import logging
import math
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set
from urllib.parse import parse_qs, quote, urlsplit

log = logging.getLogger("admin.web")

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(HERE, "web_static", "index.html")

DEFAULT_PORT = 8090
WINDOWS_MIN = (5, 15, 60, 360, 1440)          # các cửa sổ thời gian được chọn trên trang (phút)
DEFAULT_WINDOW_MIN = 15
BUCKETS_S = (10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)
MAX_BARS = 60                                 # biểu đồ theo thời gian có tối đa chừng này cột
MAX_WINDOW_ROWS = 20000                       # trần số dòng nhật ký đọc cho một cửa sổ
RECENT_ROWS = 30
RECENT_ORDERS = 8
BASE_BRANCHES = ("A", "B", "C", "D")          # luôn hiện bốn chi nhánh, kể cả khi chưa có yêu cầu nào
SUMMARY_CACHE_S = 1.0                         # nhiều tab cùng mở: gộp các lần hỏi trong 1 giây
RECONCILE_CACHE_S = 30.0                      # đối chiếu nặng hơn: tối đa một lần mỗi 30 giây
SESSION_IDLE_S = 2 * 3600
MAX_SESSIONS = 8
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW_S = 300.0
LOGIN_LOCK_S = 60.0
MAX_BODY_BYTES = 4096
MIN_PASSWORD_LEN = 10

CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ====================================================================== tính toán thuần (có unit test)
def iso_utc(value: Any) -> Any:
    """datetime (MySQL trả naive, đã là UTC vì phiên đặt time_zone='+00:00') -> chuỗi ISO có 'Z'."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(timespec="milliseconds") + "Z"
    return value


def _num(value: Any) -> Any:
    """Decimal/float -> float, giữ None; để json.dumps không vấp kiểu Decimal của PyMySQL."""
    if value is None or isinstance(value, (int, str, bool)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def pick_bucket_s(minutes: int) -> int:
    """Độ rộng cột nhỏ nhất trong BUCKETS_S sao cho cửa sổ có không quá MAX_BARS cột."""
    need = minutes * 60 / MAX_BARS
    for b in BUCKETS_S:
        if b >= need:
            return b
    return BUCKETS_S[-1]


def _stats(latencies: List[float]) -> Dict[str, Any]:
    from tools.metrics import percentile                   # cùng công thức với báo cáo và Chương 22
    if not latencies:
        return {"lat_avg": None, "lat_p50": None, "lat_p95": None}
    return {"lat_avg": round(sum(latencies) / len(latencies), 1),
            "lat_p50": round(percentile(latencies, 50), 1),
            "lat_p95": round(percentile(latencies, 95), 1)}


def aggregate(rows: Sequence[Dict[str, Any]], key: str, minutes: int,
              always: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """Gom các dòng recognition_logs theo `key` (branch_id hoặc worker): số yêu cầu, theo trạng thái, độ trễ, lần cuối."""
    groups: Dict[str, Dict[str, Any]] = {k: {"n": 0, "status": {}, "lat": [], "last": None} for k in always}
    for r in rows:
        k = r.get(key) or "?"
        g = groups.setdefault(k, {"n": 0, "status": {}, "lat": [], "last": None})
        g["n"] += 1
        st = r.get("status") or "?"
        g["status"][st] = g["status"].get(st, 0) + 1
        if r.get("latency_ms") is not None:
            g["lat"].append(float(r["latency_ms"]))
        t = r.get("created_at")
        if t is not None and (g["last"] is None or t > g["last"]):
            g["last"] = t
    out = []
    for k in sorted(groups):
        g = groups[k]
        item = {"key": k, "n": g["n"], "status": g["status"], "per_min": round(g["n"] / minutes, 2) if minutes else None,
                "last": iso_utc(g["last"])}
        item.update(_stats(g["lat"]))
        out.append(item)
    return out


def timeline(rows: Sequence[Dict[str, Any]], now: datetime, minutes: int,
             branches: Sequence[str]) -> Dict[str, Any]:
    """Số yêu cầu theo từng khoảng thời gian và từng chi nhánh (cột chồng). Mốc cột tính từ giờ CSDL `now`."""
    bucket = pick_bucket_s(minutes)
    n_bars = math.ceil(minutes * 60 / bucket)
    start = now - timedelta(seconds=n_bars * bucket)
    bars = [{"t": iso_utc(start + timedelta(seconds=i * bucket)), "counts": {b: 0 for b in branches}}
            for i in range(n_bars)]
    for r in rows:
        t = r.get("created_at")
        if t is None:
            continue
        i = int((t - start).total_seconds() // bucket)
        if i >= n_bars:                 # dòng ghi sau lúc đọc giờ CSDL (hai câu SELECT cách nhau vài ms): vào cột cuối
            i = n_bars - 1
        if i >= 0:
            b = r.get("branch_id") or "?"
            bars[i]["counts"][b] = bars[i]["counts"].get(b, 0) + 1
    return {"bucket_s": bucket, "bars": bars}


def branch_order(rows: Sequence[Dict[str, Any]]) -> List[str]:
    seen = {r.get("branch_id") or "?" for r in rows}
    return list(BASE_BRANCHES) + sorted(seen - set(BASE_BRANCHES))


def build_db_section(snap: Dict[str, Any], minutes: int) -> Dict[str, Any]:
    rows = snap["window_rows"]
    branches = branch_order(rows)
    now = snap["now_utc"]
    orders_window = {r["branch_id"]: {"n": int(r["n"]), "amount": _num(r["amount"])} for r in snap["orders_window"]}
    by_branch = aggregate(rows, "branch_id", minutes, always=BASE_BRANCHES)
    for item in by_branch:
        item["orders"] = orders_window.get(item["key"], {"n": 0, "amount": 0.0})
    return {
        "now_utc": iso_utc(now),
        "totals": {k: _num(v) for k, v in snap["totals"].items()},
        "window_rows": len(rows),
        "truncated": len(rows) >= MAX_WINDOW_ROWS,
        "branches": branches,
        "by_branch": by_branch,
        "by_worker": aggregate(rows, "worker", minutes),
        "timeline": timeline(rows, now, minutes, branches),
        "recent": [{"log_id": r["log_id"], "request_id": (r.get("request_id") or "")[:8],
                    "branch_id": r["branch_id"], "worker": r.get("worker"), "status": r["status"],
                    "customer_id": r.get("customer_id"), "similarity": _num(r.get("similarity")),
                    "latency_ms": r.get("latency_ms"), "created_at": iso_utc(r["created_at"])}
                   for r in snap["recent"]],
        "orders_recent": [{"order_id": r["order_id"], "branch_id": r["branch_id"], "customer_id": r["customer_id"],
                           "total_amount": _num(r["total_amount"]), "items": int(r["items"]),
                           "ordered_at": iso_utc(r["ordered_at"])} for r in snap["orders_recent"]],
    }


def summarize_queues(raw: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for q in raw:
        ms = q.get("message_stats") or {}
        out.append({
            "name": q.get("name"), "type": q.get("type"),
            "ready": q.get("messages_ready", 0) or 0, "unacked": q.get("messages_unacknowledged", 0) or 0,
            "consumers": q.get("consumers", 0) or 0,
            "publish_rate": round(float((ms.get("publish_details") or {}).get("rate", 0.0)), 2),
            "deliver_rate": round(float((ms.get("deliver_get_details") or {}).get("rate", 0.0)), 2),
            "is_dlq": str(q.get("name", "")).endswith(".dlq"),
        })
    return sorted(out, key=lambda q: (q["is_dlq"], q["name"] or ""))


def summarize_connections(raw: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for c in raw:
        user = c.get("user") or ""
        branch = user[7:].upper() if user.startswith("branch_") and len(user) == 8 else None
        props = c.get("client_properties") or {}
        connected = c.get("connected_at")
        out.append({
            "user": user, "branch": branch, "peer_host": c.get("peer_host"),
            "client_name": props.get("connection_name") or c.get("user_provided_name"),
            "state": c.get("state"), "channels": c.get("channels"),
            "connected_at": (datetime.fromtimestamp(connected / 1000, tz=timezone.utc).replace(tzinfo=None)
                             .isoformat(timespec="seconds") + "Z") if isinstance(connected, (int, float)) else None,
        })
    return sorted(out, key=lambda c: (c["branch"] is None, c["branch"] or "", c["user"], c["client_name"] or ""))


def build_reconcile(mysql_ids: Iterable[int], emb_counts: Dict[int, int], dups: Sequence[Sequence[Any]],
                    threshold: float) -> Dict[str, Any]:
    from tools.metrics import diff_ids                        # đúng hàm mà tools.reconcile dùng
    mysql_ids = list(mysql_ids)
    no_embedding, no_customer = diff_ids(mysql_ids, emb_counts.keys())
    pairs = [{"a": int(a), "b": int(b), "sim": round(float(s), 3)} for a, b, s in dups]
    return {
        "customers": len(mysql_ids), "embeddings": int(sum(emb_counts.values())),
        "per_customer": [{"customer_id": int(c), "n": int(n)} for c, n in sorted(emb_counts.items())],
        "no_embedding": sorted(no_embedding), "no_customer": sorted(no_customer),
        "duplicates": pairs, "threshold": threshold,
        "clean": not no_embedding and not no_customer and not pairs,
    }


def short_error(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {text[:200]}" if text else type(exc).__name__


# ====================================================================== nguồn dữ liệu (chỉ đọc)
def connect_mysql_readonly(kw: Dict[str, Any]):
    import pymysql
    from pymysql.cursors import DictCursor
    conn = pymysql.connect(charset="utf8mb4", cursorclass=DictCursor, autocommit=True,
                           connect_timeout=5, read_timeout=10, write_timeout=10,
                           init_command="SET time_zone='+00:00'", **kw)
    with conn.cursor() as cur:
        cur.execute("SET SESSION TRANSACTION READ ONLY")
    return conn


def connect_pg_readonly(dsn: str):
    import psycopg
    return psycopg.connect(dsn, autocommit=True, connect_timeout=5,
                           options="-c default_transaction_read_only=on -c statement_timeout=10000")


class _Reconnecting:
    """Một kết nối dùng chung, có khoá. Lỗi trên kết nối CŨ (có thể server đã đóng vì rảnh) -> mở lại và thử một lần."""

    def __init__(self, connect: Callable[[], Any]):
        self._connect = connect
        self._conn = None
        self._lock = threading.Lock()

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:                                  # noqa: BLE001
                pass

    def run(self, fn: Callable[[Any], Any]) -> Any:
        with self._lock:
            fresh = self._conn is None
            try:
                if self._conn is None:
                    self._conn = self._connect()
                return fn(self._conn)
            except Exception:
                self._drop()
                if fresh:
                    raise
            self._conn = self._connect()
            try:
                return fn(self._conn)
            except Exception:
                self._drop()
                raise

    def close(self) -> None:
        with self._lock:
            self._drop()


class MySqlStats:
    TOTALS_SQL = ("SELECT UTC_TIMESTAMP(3) AS now_utc, "
                  "(SELECT COUNT(*) FROM customers) AS customers, "
                  "(SELECT COALESCE(MAX(customer_id), 0) FROM customers) AS max_customer_id, "
                  "(SELECT COUNT(*) FROM orders) AS orders, "
                  "(SELECT COUNT(*) FROM customer_photos) AS photos, "
                  "(SELECT COUNT(*) FROM recognition_logs) AS logs, "
                  "(SELECT COALESCE(MAX(log_id), 0) FROM recognition_logs) AS max_log_id")
    WINDOW_SQL = ("SELECT branch_id, worker, status, latency_ms, created_at FROM recognition_logs "
                  "WHERE created_at >= UTC_TIMESTAMP(3) - INTERVAL %s MINUTE ORDER BY log_id DESC LIMIT %s")
    RECENT_SQL = ("SELECT log_id, request_id, branch_id, worker, status, customer_id, similarity, latency_ms, "
                  "created_at FROM recognition_logs ORDER BY log_id DESC LIMIT %s")
    ORDERS_RECENT_SQL = ("SELECT o.order_id, o.branch_id, o.customer_id, o.total_amount, o.ordered_at, "
                         "COUNT(i.item_id) AS items FROM orders o LEFT JOIN order_items i ON i.order_id = o.order_id "
                         "GROUP BY o.order_id, o.branch_id, o.customer_id, o.total_amount, o.ordered_at "
                         "ORDER BY o.order_id DESC LIMIT %s")
    ORDERS_WINDOW_SQL = ("SELECT branch_id, COUNT(*) AS n, COALESCE(SUM(total_amount), 0) AS amount FROM orders "
                         "WHERE ordered_at >= UTC_TIMESTAMP(3) - INTERVAL %s MINUTE GROUP BY branch_id")
    IDS_SQL = "SELECT customer_id FROM customers ORDER BY customer_id"

    def __init__(self, connect: Callable[[], Any]):
        self._db = _Reconnecting(connect)

    @staticmethod
    def _all(conn, sql: str, args: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        cur = conn.cursor()
        try:
            cur.execute(sql, tuple(args))
            return list(cur.fetchall())
        finally:
            cur.close()

    def snapshot(self, minutes: int) -> Dict[str, Any]:
        def work(conn):
            head = self._all(conn, self.TOTALS_SQL)[0]
            now = head.pop("now_utc")
            return {"now_utc": now, "totals": head,
                    "window_rows": self._all(conn, self.WINDOW_SQL, (minutes, MAX_WINDOW_ROWS)),
                    "recent": self._all(conn, self.RECENT_SQL, (RECENT_ROWS,)),
                    "orders_recent": self._all(conn, self.ORDERS_RECENT_SQL, (RECENT_ORDERS,)),
                    "orders_window": self._all(conn, self.ORDERS_WINDOW_SQL, (minutes,))}
        return self._db.run(work)

    def customer_ids(self) -> List[int]:
        return self._db.run(lambda conn: [int(r["customer_id"]) for r in self._all(conn, self.IDS_SQL)])

    def close(self) -> None:
        self._db.close()


class PgStats:
    COUNTS_SQL = "SELECT customer_id, COUNT(*) FROM face_embeddings GROUP BY customer_id ORDER BY customer_id"

    def __init__(self, connect: Callable[[], Any]):
        self._db = _Reconnecting(connect)

    def embedding_counts(self) -> Dict[int, int]:
        return self._db.run(lambda conn: {int(c): int(n) for c, n in conn.execute(self.COUNTS_SQL).fetchall()})

    def duplicate_pairs(self, threshold: float) -> List[Sequence[Any]]:
        from tools.reconcile import DUP_SQL                    # một câu SQL duy nhất cho cả CLI và trang này
        return self._db.run(lambda conn: list(conn.execute(DUP_SQL, (threshold,)).fetchall()))

    def close(self) -> None:
        self._db.close()


class RabbitMgmt:
    """Management HTTP API của RabbitMQ (cổng 15672, trong mạng Docker). Chỉ GET."""

    def __init__(self, base_url: str, user: str, password: str, vhost: str = "/",
                 opener: Optional[Callable[..., Any]] = None, timeout: float = 5.0):
        self.base = base_url.rstrip("/")
        self.vhost = vhost
        self._auth = "Basic " + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        self._open = opener or urllib.request.urlopen
        self.timeout = timeout

    def _get(self, path: str) -> Any:
        req = urllib.request.Request(self.base + path, headers={"Authorization": self._auth,
                                                                "Accept": "application/json"}, method="GET")
        try:
            with self._open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"management API trả HTTP {exc.code} cho {path}") from None

    def snapshot(self) -> Dict[str, Any]:
        vh = quote(self.vhost, safe="")
        return {"queues": summarize_queues(self._get(f"/api/queues/{vh}")),
                "connections": summarize_connections(self._get("/api/connections"))}


# ====================================================================== logic của trang (không biết HTTP)
class AdminApp:
    def __init__(self, mysql: MySqlStats, rabbit: RabbitMgmt, pg: PgStats, sim_threshold: float,
                 clock: Callable[[], float] = time.monotonic):
        self.mysql, self.rabbit, self.pg = mysql, rabbit, pg
        self.sim_threshold = sim_threshold
        self._clock = clock
        self._lock = threading.Lock()
        self._summary_cache: Dict[int, tuple] = {}
        self._reconcile_lock = threading.Lock()
        self._reconcile_cache: Optional[tuple] = None

    @staticmethod
    def parse_minutes(raw: Optional[str]) -> int:
        if raw in (None, ""):
            return DEFAULT_WINDOW_MIN
        try:
            m = int(raw)
        except ValueError:
            raise HttpError(400, "minutes phải là số nguyên")
        if m not in WINDOWS_MIN:
            raise HttpError(400, f"minutes phải là một trong {list(WINDOWS_MIN)}")
        return m

    def summary(self, minutes: int) -> Dict[str, Any]:
        now = self._clock()
        with self._lock:
            hit = self._summary_cache.get(minutes)
            if hit and now - hit[0] < SUMMARY_CACHE_S:
                return hit[1]
        out: Dict[str, Any] = {"minutes": minutes, "windows": list(WINDOWS_MIN), "errors": {},
                               "server_utc": iso_utc(datetime.now(timezone.utc)), "db": None, "rabbit": None,
                               "sim_threshold": self.sim_threshold}
        try:
            out["db"] = build_db_section(self.mysql.snapshot(minutes), minutes)
        except Exception as exc:                               # noqa: BLE001 - lỗi cục bộ: báo, không sập cả trang
            log.warning("đọc MySQL lỗi: %r", exc)
            out["errors"]["mysql"] = short_error(exc)
        try:
            out["rabbit"] = self.rabbit.snapshot()
        except Exception as exc:                               # noqa: BLE001
            log.warning("đọc RabbitMQ management lỗi: %r", exc)
            out["errors"]["rabbitmq"] = short_error(exc)
        with self._lock:
            self._summary_cache[minutes] = (self._clock(), out)
        return out

    def reconcile(self) -> Dict[str, Any]:
        with self._reconcile_lock:                             # hai người bấm cùng lúc: chỉ chạy một lần
            now = self._clock()
            if self._reconcile_cache and now - self._reconcile_cache[0] < RECONCILE_CACHE_S:
                res = dict(self._reconcile_cache[1])
                res["cached_s"] = round(now - self._reconcile_cache[0], 1)
                return res
            res: Dict[str, Any] = {"checked_utc": iso_utc(datetime.now(timezone.utc)), "cached_s": 0.0}
            try:
                ids = self.mysql.customer_ids()
            except Exception as exc:                           # noqa: BLE001
                raise HttpError(503, f"không đọc được MySQL: {short_error(exc)}")
            try:
                counts = self.pg.embedding_counts()
                dups = self.pg.duplicate_pairs(self.sim_threshold)
            except Exception as exc:                           # noqa: BLE001
                raise HttpError(503, f"không đọc được PostgreSQL: {short_error(exc)}")
            res.update(build_reconcile(ids, counts, dups, self.sim_threshold))
            self._reconcile_cache = (now, res)
            return dict(res)

    def close(self) -> None:
        for src in (self.mysql, self.pg):
            try:
                src.close()
            except Exception:                                  # noqa: BLE001
                pass


# ====================================================================== đăng nhập admin
class AdminAuth:
    """Một tài khoản admin (ADMIN_USER/ADMIN_PASSWORD). So sánh hằng thời gian, khoá tạm khi thử sai nhiều."""

    def __init__(self, user: str, password: str, clock: Callable[[], float] = time.monotonic,
                 idle_s: float = SESSION_IDLE_S):
        self._user = user.encode("utf-8")
        self._password = password.encode("utf-8")
        self._clock = clock
        self.idle_s = idle_s
        self._lock = threading.Lock()
        self._sessions: Dict[str, float] = {}                # token -> lần dùng cuối
        self._fails: List[float] = []
        self._locked_until = 0.0

    def login(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            raise HttpError(400, "body phải là một JSON object")
        user, password = payload.get("username"), payload.get("password")
        if not isinstance(user, str) or not isinstance(password, str) or len(user) > 64 or len(password) > 256:
            raise HttpError(400, "cần 'username' và 'password' dạng chuỗi")
        now = self._clock()
        with self._lock:
            if now < self._locked_until:
                raise HttpError(429, f"thử sai quá nhiều lần, chờ {int(self._locked_until - now) + 1} giây")
            ok_user = hmac.compare_digest(user.strip().encode("utf-8"), self._user)
            ok_pass = hmac.compare_digest(password.encode("utf-8"), self._password)
            if not (ok_user and ok_pass):
                self._fails = [t for t in self._fails if now - t < LOGIN_WINDOW_S] + [now]
                if len(self._fails) >= LOGIN_MAX_FAILS:
                    self._locked_until, self._fails = now + LOGIN_LOCK_S, []
                log.warning("đăng nhập admin sai")
                raise HttpError(401, "sai tài khoản hoặc mật khẩu")
            self._fails = []
            token = secrets.token_urlsafe(32)
            self._sessions[token] = now
            while len(self._sessions) > MAX_SESSIONS:          # bỏ phiên dùng lâu nhất
                del self._sessions[min(self._sessions, key=self._sessions.get)]
        log.info("admin đăng nhập")
        return token

    def logout(self, token: Optional[str]) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)

    def valid(self, token: Optional[str]) -> bool:
        now = self._clock()
        with self._lock:
            last = self._sessions.get(token or "")
            if last is None:
                return False
            if now - last > self.idle_s:
                del self._sessions[token]
                return False
            self._sessions[token] = now
            return True

    @property
    def user(self) -> str:
        return self._user.decode("utf-8")


# ====================================================================== HTTP
def _hostname(host_header: str) -> str:
    return (urlsplit("//" + host_header).hostname or "").lower()


def make_handler(app: AdminApp, auth: AdminAuth, allowed_hosts: Optional[Set[str]]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "CoffeeAdminWeb/1.0"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, status: int, body: bytes, ctype: str, extra: Optional[List[tuple]] = None) -> None:
            self.send_response(status)
            for k, v in extra or ():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: Any, extra: Optional[List[tuple]] = None) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", extra)

        def _cookie_name(self) -> str:
            return f"cb_admin_{self.server.server_address[1]}"

        def _token(self) -> Optional[str]:
            try:
                morsel = SimpleCookie(self.headers.get("Cookie", "")).get(self._cookie_name())
            except CookieError:
                return None
            return morsel.value if morsel else None

        def _guard(self, post: bool) -> None:
            host = self.headers.get("Host", "")
            if allowed_hosts is not None and _hostname(host) not in allowed_hosts:
                raise HttpError(403, "Host không được phép")
            if post:
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc != host:
                    raise HttpError(403, "Origin không khớp (chặn yêu cầu từ trang web khác)")
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    raise HttpError(415, "chỉ nhận application/json")

        def _require_login(self) -> None:
            if not auth.valid(self._token()):
                raise HttpError(401, "chưa đăng nhập hoặc phiên đã hết hạn")

        def _read_json(self) -> Any:
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                raise HttpError(411, "thiếu Content-Length")
            if length < 0 or length > MAX_BODY_BYTES:
                raise HttpError(413, "body không hợp lệ hoặc quá lớn")
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise HttpError(400, "JSON không hợp lệ")

        def _run(self, work: Callable[[], Any], extra: Optional[List[tuple]] = None) -> None:
            try:
                result = work()
                self._json(200, result, extra)
            except HttpError as exc:
                self._json(exc.status, {"error": exc.message})
            except Exception:                                  # noqa: BLE001
                log.exception("lỗi không lường trước")
                self._json(500, {"error": "lỗi nội bộ của trang quản trị (xem log)"})

        def do_GET(self) -> None:                             # noqa: N802
            parts = urlsplit(self.path)
            if parts.path in ("/", "/index.html"):
                try:
                    self._guard(post=False)
                    with open(INDEX_PATH, "rb") as fh:
                        self._send(200, fh.read(), "text/html; charset=utf-8")
                except HttpError as exc:
                    self._json(exc.status, {"error": exc.message})
                return

            def work():
                self._guard(post=False)
                if parts.path == "/api/session":
                    ok = auth.valid(self._token())
                    return {"logged_in": ok, "user": auth.user if ok else None}
                if parts.path == "/api/summary":
                    self._require_login()
                    raw = parse_qs(parts.query).get("minutes", [None])[0]
                    return app.summary(app.parse_minutes(raw))
                if parts.path == "/api/reconcile":
                    self._require_login()
                    return app.reconcile()
                raise HttpError(404, "không có đường dẫn này")

            self._run(work)

        def do_POST(self) -> None:                            # noqa: N802
            path = urlsplit(self.path).path
            base = f"{self._cookie_name()}=%s; Path=/; HttpOnly; SameSite=Strict"
            try:
                self._guard(post=True)
                if path == "/api/login":
                    token = auth.login(self._read_json())
                    self._json(200, {"user": auth.user}, [("Set-Cookie", base % token)])
                elif path == "/api/logout":
                    auth.logout(self._token())
                    self._json(200, {"ok": True}, [("Set-Cookie", (base % "") + "; Max-Age=0")])
                else:
                    raise HttpError(404, "không có đường dẫn này (trang quản trị chỉ đọc)")
            except HttpError as exc:
                self._json(exc.status, {"error": exc.message})
            except Exception:                                  # noqa: BLE001
                log.exception("lỗi không lường trước")
                self._json(500, {"error": "lỗi nội bộ của trang quản trị (xem log)"})

    return Handler


# ====================================================================== dòng lệnh
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Trang quản trị chỉ đọc của máy chủ Coffeehouse")
    p.add_argument("--bind", default="127.0.0.1",
                   help="địa chỉ nghe; trong container dùng 0.0.0.0 và chỉ publish ra 127.0.0.1 của máy lab")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--allow-host", action="append", default=None,
                   help="tên Host được phép trong yêu cầu (mặc định localhost, 127.0.0.1, ::1); lặp lại để thêm")
    p.add_argument("--mgmt-url", default=os.environ.get("RABBIT_MGMT_URL"),
                   help="management API, mặc định http://<RABBIT_HOST>:15672")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from common.config import load_settings

    password = os.environ.get("ADMIN_PASSWORD", "")
    if len(password) < MIN_PASSWORD_LEN:
        print(f"LỖI: đặt biến ADMIN_PASSWORD (ít nhất {MIN_PASSWORD_LEN} ký tự) trước khi chạy trang quản trị.",
              file=sys.stderr)
        return 2
    s = load_settings()
    mysql_kw = dict(s.mysql_kwargs)
    mysql_kw["user"] = os.environ.get("ADMIN_MYSQL_USER") or mysql_kw["user"]
    mysql_kw["password"] = os.environ.get("ADMIN_MYSQL_PASSWORD") or mysql_kw["password"]
    pg_dsn = (f"host={s.pg_host} port={s.pg_port} dbname={s.pg_db} "
              f"user={os.environ.get('ADMIN_PG_USER') or s.pg_user} "
              f"password={os.environ.get('ADMIN_PG_PASSWORD') or s.pg_password}")
    mgmt_url = args.mgmt_url or f"http://{s.rabbit_host}:15672"
    app = AdminApp(MySqlStats(lambda: connect_mysql_readonly(mysql_kw)),
                   RabbitMgmt(mgmt_url, os.environ.get("ADMIN_RABBIT_USER") or s.rabbit_user,
                              os.environ.get("ADMIN_RABBIT_PASS") or s.rabbit_pass, s.rabbit_vhost),
                   PgStats(lambda: connect_pg_readonly(pg_dsn)), s.sim_threshold)
    auth = AdminAuth(os.environ.get("ADMIN_USER") or "admin", password)
    allowed = set(h.lower() for h in (args.allow_host or ["localhost", "127.0.0.1", "::1"]))
    try:
        server = ThreadingHTTPServer((args.bind, args.port), make_handler(app, auth, allowed))
    except OSError as exc:
        print(f"LỖI: không nghe được {args.bind}:{args.port} ({exc}).", file=sys.stderr)
        return 2
    server.daemon_threads = True
    print(f"Trang quản trị (chỉ đọc) nghe {args.bind}:{args.port}; Host được phép: {sorted(allowed)}; "
          f"MySQL {mysql_kw['host']}:{mysql_kw['port']}, PostgreSQL {s.pg_host}:{s.pg_port}, management {mgmt_url}",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
