"""Test trang quản trị chỉ đọc (admin/web.py) bằng nguồn dữ liệu giả, KHÔNG cần MySQL, PostgreSQL hay RabbitMQ.

Kiểm: các phép gom (theo chi nhánh, theo worker, theo thời gian) và p50/p95 dùng đúng công thức của tools.metrics;
mọi câu SQL là SELECT; phiên MySQL autocommit + READ ONLY, phiên PostgreSQL read-only; management API chỉ GET;
một nguồn lỗi thì phần đó báo lỗi còn phần khác vẫn hiện; bộ nhớ đệm 1 giây và 30 giây; đăng nhập so sánh hằng
thời gian, khoá tạm, hết hạn; HTTP: chưa đăng nhập thì 401 và không đụng CSDL, chặn Host lạ, Origin lạ, không có
đường dẫn ghi nào.
"""
import http.client
import json
import logging
import os
import sys
import threading
import types
import unittest
import urllib.error
from datetime import datetime, timedelta
from decimal import Decimal
from http.server import ThreadingHTTPServer
from unittest import mock

from admin import web
from tools.metrics import percentile

NOW = datetime(2026, 11, 12, 3, 0, 0)
_QUIET = logging.NullHandler()


def setUpModule():                                        # các cảnh báo cố ý gây ra (sai mật khẩu, nguồn lỗi) không in ra
    logging.getLogger("admin.web").addHandler(_QUIET)


def tearDownModule():
    logging.getLogger("admin.web").removeHandler(_QUIET)


def log_row(branch, worker, status, ms, sec_ago, **extra):
    row = {"branch_id": branch, "worker": worker, "status": status, "latency_ms": ms,
           "created_at": NOW - timedelta(seconds=sec_ago)}
    row.update(extra)
    return row


def snapshot(rows=None):
    rows = rows if rows is not None else [
        log_row("A", "w1", "RECOGNIZED", 300, 10), log_row("A", "w2", "RECOGNIZED", 500, 20),
        log_row("B", "w1", "NEW_CUSTOMER", 900, 30), log_row("C", "w2", "NO_FACE", 100, 40),
        log_row("E", "w2", "UNKNOWN", None, 50)]
    return {
        "now_utc": NOW,
        "totals": {"customers": 7, "max_customer_id": 9, "orders": 9, "photos": 19, "logs": 4000, "max_log_id": 4100},
        "window_rows": rows,
        "recent": [{"log_id": 4100, "request_id": "abcdef0123456789", "branch_id": "A", "worker": "w1",
                    "status": "RECOGNIZED", "customer_id": 1, "similarity": 0.7021, "latency_ms": 300,
                    "created_at": NOW}],
        "orders_recent": [{"order_id": 9, "branch_id": "A", "customer_id": 1, "total_amount": Decimal("64000.00"),
                           "items": 2, "ordered_at": NOW}],
        "orders_window": [{"branch_id": "B", "n": 1, "amount": Decimal("29000.00")}],
    }


class FakeMysql:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def snapshot(self, minutes):
        self.calls += 1
        if self.fail:
            raise ConnectionRefusedError("MySQL không trả lời")
        return snapshot()

    def customer_ids(self):
        self.calls += 1
        return [1, 2, 4, 5, 6, 7, 9]

    def close(self):
        pass


class FakeRabbit:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def snapshot(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("management API trả HTTP 401 cho /api/queues/%2F")
        return {"queues": web.summarize_queues([{"name": "face.requests", "type": "quorum", "messages_ready": 2,
                                                 "messages_unacknowledged": 3, "consumers": 3}]),
                "connections": web.summarize_connections([{"user": "branch_b", "peer_host": "10.234.18.129",
                                                           "client_properties": {"connection_name": "branch-B"}}])}


class FakePg:
    def __init__(self, counts=None, dups=(), fail=False):
        self.counts = counts if counts is not None else {1: 5, 2: 1, 4: 1, 5: 5, 6: 4, 7: 1, 9: 1}
        self.dups = list(dups)
        self.fail = fail
        self.calls = 0

    def embedding_counts(self):
        self.calls += 1
        if self.fail:
            raise OSError("PostgreSQL không trả lời")
        return dict(self.counts)

    def duplicate_pairs(self, threshold):
        return list(self.dups)

    def close(self):
        pass


# ====================================================================== tính toán thuần
class PureFunctionTests(unittest.TestCase):
    def test_bucket_width_keeps_at_most_60_bars(self):
        self.assertEqual([web.pick_bucket_s(m) for m in web.WINDOWS_MIN], [10, 15, 60, 600, 1800])
        for m in web.WINDOWS_MIN:
            self.assertLessEqual(m * 60 / web.pick_bucket_s(m), web.MAX_BARS)

    def test_aggregate_by_branch_counts_statuses_and_percentiles(self):
        rows = snapshot()["window_rows"]
        out = {g["key"]: g for g in web.aggregate(rows, "branch_id", 10, always=web.BASE_BRANCHES)}
        self.assertEqual(sorted(out), ["A", "B", "C", "D", "E"])
        self.assertEqual(out["A"]["n"], 2)
        self.assertEqual(out["A"]["status"], {"RECOGNIZED": 2})
        self.assertEqual(out["A"]["lat_p50"], round(percentile([300.0, 500.0], 50), 1))     # 400.0, nội suy
        self.assertEqual(out["A"]["lat_p95"], round(percentile([300.0, 500.0], 95), 1))
        self.assertEqual(out["A"]["last"], "2026-11-12T02:59:50.000Z")                       # dòng mới nhất của A
        self.assertEqual(out["A"]["per_min"], 0.2)
        self.assertEqual(out["D"]["n"], 0)                                                   # luôn có, dù chưa có yêu cầu
        self.assertIsNone(out["D"]["lat_p50"])
        self.assertIsNone(out["E"]["lat_avg"])                                               # latency NULL bị bỏ qua

    def test_aggregate_by_worker(self):
        out = {g["key"]: g for g in web.aggregate(snapshot()["window_rows"], "worker", 10)}
        self.assertEqual((out["w1"]["n"], out["w2"]["n"]), (2, 3))
        self.assertEqual(out["w2"]["lat_avg"], 300.0)                                       # (500 + 100) / 2

    def test_timeline_places_rows_by_db_clock_and_drops_old_rows(self):
        rows = [log_row("A", "w", "RECOGNIZED", 1, 1), log_row("B", "w", "RECOGNIZED", 1, 1),
                log_row("A", "w", "RECOGNIZED", 1, 299), log_row("A", "w", "RECOGNIZED", 1, 301),
                log_row("Z", "w", "RECOGNIZED", 1, 5), log_row("D", "w", "RECOGNIZED", 1, -0.4)]   # ghi sau lúc đọc giờ
        tl = web.timeline(rows, NOW, 5, web.branch_order(rows))
        self.assertEqual((tl["bucket_s"], len(tl["bars"])), (10, 30))
        self.assertEqual(tl["bars"][-1]["counts"], {"A": 1, "B": 1, "C": 0, "D": 1, "Z": 1})
        self.assertEqual(tl["bars"][0]["counts"]["A"], 1)                                   # 299 giây trước: cột đầu
        self.assertEqual(sum(sum(b["counts"].values()) for b in tl["bars"]), 5)              # 301 giây: ngoài cửa sổ
        self.assertEqual(tl["bars"][0]["t"], "2026-11-12T02:55:00.000Z")

    def test_db_section_is_json_and_hides_full_request_id(self):
        sec = web.build_db_section(snapshot(), 15)
        json.dumps(sec)                                                                      # Decimal, datetime đã đổi
        self.assertEqual(sec["recent"][0]["request_id"], "abcdef01")
        self.assertEqual(sec["recent"][0]["created_at"], "2026-11-12T03:00:00.000Z")
        self.assertEqual(sec["orders_recent"][0]["total_amount"], 64000.0)
        by = {b["key"]: b for b in sec["by_branch"]}
        self.assertEqual(by["B"]["orders"], {"n": 1, "amount": 29000.0})
        self.assertEqual(by["A"]["orders"], {"n": 0, "amount": 0.0})
        self.assertEqual(sec["branches"], ["A", "B", "C", "D", "E"])
        self.assertFalse(sec["truncated"])

    def test_summarize_queues_puts_dlq_last_and_tolerates_missing_stats(self):
        out = web.summarize_queues([
            {"name": "face.requests.dlq", "messages_ready": 3, "consumers": 0},
            {"name": "orders", "type": "quorum", "messages_ready": 0, "messages_unacknowledged": 0, "consumers": 3,
             "message_stats": {"publish_details": {"rate": 1.234}, "deliver_get_details": {"rate": 0.5}}},
            {"name": "face.requests", "messages_ready": 7, "messages_unacknowledged": 3, "consumers": 3}])
        self.assertEqual([q["name"] for q in out], ["face.requests", "orders", "face.requests.dlq"])
        self.assertEqual((out[0]["ready"], out[0]["unacked"], out[0]["publish_rate"]), (7, 3, 0.0))
        self.assertEqual(out[1]["publish_rate"], 1.23)
        self.assertTrue(out[2]["is_dlq"])
        self.assertEqual(out[2]["unacked"], 0)

    def test_summarize_connections_maps_only_branch_accounts(self):
        out = web.summarize_connections([
            {"user": "coffee_admin", "peer_host": "172.18.0.5", "client_properties": {"connection_name": "worker-x"},
             "connected_at": 1762916400000},
            {"user": "branch_c", "peer_host": "10.234.18.61", "client_properties": {}, "user_provided_name": "branch-C"},
            {"user": "branch_cc", "peer_host": "1.2.3.4"}])
        self.assertEqual([(c["user"], c["branch"]) for c in out],
                         [("branch_c", "C"), ("branch_cc", None), ("coffee_admin", None)])
        self.assertEqual(out[0]["client_name"], "branch-C")
        self.assertEqual(out[2]["connected_at"], "2025-11-12T03:00:00Z")

    def test_reconcile_clean_and_dirty(self):
        clean = web.build_reconcile([1, 2], {1: 5, 2: 1}, [], 0.46)
        self.assertTrue(clean["clean"])
        self.assertEqual(clean["embeddings"], 6)
        dirty = web.build_reconcile([1, 2, 3], {1: 5, 8: 1}, [(1, 2, 0.4981)], 0.46)
        self.assertFalse(dirty["clean"])
        self.assertEqual((dirty["no_embedding"], dirty["no_customer"]), ([2, 3], [8]))
        self.assertEqual(dirty["duplicates"], [{"a": 1, "b": 2, "sim": 0.498}])


# ====================================================================== nguồn dữ liệu: chỉ đọc
class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []

    def execute(self, sql, args=()):
        self.conn.log.append((sql, args))
        if self.conn.broken:
            raise ConnectionResetError("server đã đóng kết nối")
        if sql.startswith("SELECT UTC_TIMESTAMP"):
            self._rows = [{"now_utc": NOW, "customers": 7, "max_customer_id": 9, "orders": 9, "photos": 19,
                           "logs": 1, "max_log_id": 1}]
        elif "FROM customers ORDER BY" in sql:
            self._rows = [{"customer_id": 1}, {"customer_id": 2}]
        else:
            self._rows = []

    def fetchall(self):
        return self._rows

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, broken=False):
        self.log = []
        self.broken = broken
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class SourceTests(unittest.TestCase):
    def test_every_sql_is_a_select(self):
        from tools.reconcile import DUP_SQL
        sqls = [v for k, v in vars(web.MySqlStats).items() if k.endswith("_SQL")]
        sqls += [web.PgStats.COUNTS_SQL, DUP_SQL]
        self.assertGreaterEqual(len(sqls), 8)
        for sql in sqls:
            self.assertTrue(sql.lstrip().upper().startswith("SELECT"), sql)

    def test_mysql_snapshot_issues_only_selects_with_window_and_limit(self):
        conn = FakeConn()
        stats = web.MySqlStats(lambda: conn)
        snap = stats.snapshot(60)
        self.assertEqual(snap["now_utc"], NOW)
        self.assertNotIn("now_utc", snap["totals"])
        self.assertTrue(all(sql.startswith("SELECT") for sql, _ in conn.log))
        args = {sql: a for sql, a in conn.log}
        self.assertEqual(args[web.MySqlStats.WINDOW_SQL], (60, web.MAX_WINDOW_ROWS))
        self.assertEqual(args[web.MySqlStats.ORDERS_WINDOW_SQL], (60,))
        self.assertEqual(stats.customer_ids(), [1, 2])

    def test_stale_connection_is_reopened_once(self):
        conns = [FakeConn(broken=True), FakeConn()]
        opened = []

        def connect():
            opened.append(conns[len(opened)])
            return opened[-1]

        stats = web.MySqlStats(connect)
        stats._db._conn = connect()                     # giả lập kết nối cũ đã bị server đóng
        self.assertEqual(stats.customer_ids(), [1, 2])
        self.assertEqual(len(opened), 2)
        self.assertTrue(conns[0].closed)

    def test_fresh_connection_failure_is_reported_not_retried(self):
        attempts = []

        def connect():
            attempts.append(1)
            raise ConnectionRefusedError("không tới được mysql:3306")

        with self.assertRaises(ConnectionRefusedError):
            web.MySqlStats(connect).customer_ids()
        self.assertEqual(len(attempts), 1)

    def test_mysql_session_is_autocommit_and_read_only(self):
        seen = {}
        conn = FakeConn()
        fake = types.ModuleType("pymysql")
        fake.connect = lambda **kw: (seen.update(kw), conn)[1]
        cursors = types.ModuleType("pymysql.cursors")
        cursors.DictCursor = object
        with mock.patch.dict(sys.modules, {"pymysql": fake, "pymysql.cursors": cursors}):
            web.connect_mysql_readonly({"host": "mysql", "port": 3306, "user": "coffee", "password": "x",
                                        "database": "coffeehouse"})
        self.assertIs(seen["autocommit"], True)          # không giữ ảnh chụp cũ của InnoDB giữa các lần hỏi
        self.assertEqual(seen["init_command"], "SET time_zone='+00:00'")
        self.assertEqual(conn.log[0][0], "SET SESSION TRANSACTION READ ONLY")

    def test_pg_session_is_read_only(self):
        seen = {}
        fake = types.ModuleType("psycopg")
        fake.connect = lambda dsn, **kw: (seen.update(kw, dsn=dsn), object())[1]
        with mock.patch.dict(sys.modules, {"psycopg": fake}):
            web.connect_pg_readonly("host=postgres dbname=faces")
        self.assertIs(seen["autocommit"], True)
        self.assertIn("default_transaction_read_only=on", seen["options"])

    def test_rabbit_mgmt_uses_get_basic_auth_and_encoded_vhost(self):
        seen = []

        class Resp:
            def __init__(self, body):
                self.body = body

            def read(self):
                return json.dumps(self.body).encode()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(req, timeout):
            seen.append((req.get_method(), req.full_url, req.get_header("Authorization"), timeout))
            return Resp([{"name": "face.requests", "messages_ready": 1}] if "/queues/" in req.full_url else [])

        snap = web.RabbitMgmt("http://rabbitmq:15672/", "coffee_admin", "pw", "/", opener=opener).snapshot()
        self.assertEqual([s[0] for s in seen], ["GET", "GET"])
        self.assertEqual(seen[0][1], "http://rabbitmq:15672/api/queues/%2F")
        self.assertEqual(seen[1][1], "http://rabbitmq:15672/api/connections")
        self.assertEqual(seen[0][2], "Basic Y29mZmVlX2FkbWluOnB3")          # base64("coffee_admin:pw")
        self.assertEqual(snap["queues"][0]["ready"], 1)

    def test_rabbit_mgmt_http_error_is_short(self):
        def opener(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            web.RabbitMgmt("http://rabbitmq:15672", "u", "p", opener=opener).snapshot()


# ====================================================================== logic trang
class AdminAppTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.mysql, self.rabbit, self.pg = FakeMysql(), FakeRabbit(), FakePg()

    def app(self):
        return web.AdminApp(self.mysql, self.rabbit, self.pg, 0.46, clock=lambda: self.now[0])

    def test_one_source_down_does_not_hide_the_other(self):
        self.mysql.fail = True
        out = self.app().summary(15)
        self.assertIsNone(out["db"])
        self.assertIn("ConnectionRefusedError", out["errors"]["mysql"])
        self.assertEqual(out["rabbit"]["queues"][0]["consumers"], 3)
        self.mysql.fail, self.rabbit.fail = False, True
        out = self.app().summary(15)
        self.assertIsNone(out["rabbit"])
        self.assertIn("HTTP 401", out["errors"]["rabbitmq"])
        self.assertEqual(out["db"]["totals"]["customers"], 7)

    def test_summary_is_cached_for_one_second_per_window(self):
        app = self.app()
        app.summary(15)
        app.summary(15)
        self.assertEqual(self.mysql.calls, 1)
        app.summary(60)                                     # cửa sổ khác: hỏi riêng
        self.assertEqual(self.mysql.calls, 2)
        self.now[0] += 1.5
        app.summary(15)
        self.assertEqual(self.mysql.calls, 3)

    def test_parse_minutes(self):
        self.assertEqual(web.AdminApp.parse_minutes(None), web.DEFAULT_WINDOW_MIN)
        self.assertEqual(web.AdminApp.parse_minutes("60"), 60)
        for bad in ("7", "abc", "-5", "100000"):
            with self.assertRaises(web.HttpError) as ctx:
                web.AdminApp.parse_minutes(bad)
            self.assertEqual(ctx.exception.status, 400)

    def test_reconcile_is_cached_for_30_seconds(self):
        app = self.app()
        first = app.reconcile()
        self.assertTrue(first["clean"])
        self.assertEqual(first["customers"], 7)
        self.now[0] += 10
        again = app.reconcile()
        self.assertEqual((self.pg.calls, again["cached_s"]), (1, 10.0))
        self.now[0] += 25
        app.reconcile()
        self.assertEqual(self.pg.calls, 2)

    def test_reconcile_reports_orphans_and_db_errors(self):
        self.pg.counts = {1: 5, 2: 1, 15: 1}
        res = self.app().reconcile()
        self.assertEqual((res["no_embedding"], res["no_customer"]), ([4, 5, 6, 7, 9], [15]))
        self.pg.fail = True
        self.now[0] += 60
        with self.assertRaises(web.HttpError) as ctx:
            self.app().reconcile()
        self.assertEqual(ctx.exception.status, 503)


# ====================================================================== đăng nhập
class AuthTests(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.auth = web.AdminAuth("admin", "mat-khau-dai-123", clock=lambda: self.now[0], idle_s=100)

    def login(self, user="admin", password="mat-khau-dai-123"):
        return self.auth.login({"username": user, "password": password})

    def test_good_and_bad_password(self):
        token = self.login()
        self.assertTrue(self.auth.valid(token))
        for user, pw in (("admin", "sai"), ("root", "mat-khau-dai-123"), ("admin", "")):
            with self.assertRaises(web.HttpError) as ctx:
                self.login(user, pw)
            self.assertEqual(ctx.exception.status, 401)
        with self.assertRaises(web.HttpError) as ctx:
            self.auth.login(["admin"])
        self.assertEqual(ctx.exception.status, 400)

    def test_lockout_blocks_even_the_right_password_then_clears(self):
        for _ in range(web.LOGIN_MAX_FAILS):
            with self.assertRaises(web.HttpError):
                self.login(password="sai")
        with self.assertRaises(web.HttpError) as ctx:
            self.login()
        self.assertEqual(ctx.exception.status, 429)
        self.now[0] += web.LOGIN_LOCK_S + 1
        self.assertTrue(self.auth.valid(self.login()))

    def test_idle_expiry_logout_and_session_cap(self):
        token = self.login()
        self.now[0] += 50
        self.assertTrue(self.auth.valid(token))             # dùng lại thì gia hạn
        self.now[0] += 101
        self.assertFalse(self.auth.valid(token))
        t2 = self.login()
        self.auth.logout(t2)
        self.assertFalse(self.auth.valid(t2))
        tokens = []
        for _ in range(web.MAX_SESSIONS + 2):
            self.now[0] += 1
            tokens.append(self.login())
        self.assertFalse(self.auth.valid(tokens[0]))        # phiên cũ nhất bị bỏ
        self.assertTrue(self.auth.valid(tokens[-1]))


# ====================================================================== HTTP
class HttpTests(unittest.TestCase):
    def setUp(self):
        self.mysql, self.rabbit, self.pg = FakeMysql(), FakeRabbit(), FakePg()
        self.app = web.AdminApp(self.mysql, self.rabbit, self.pg, 0.46)
        self.auth = web.AdminAuth("admin", "mat-khau-dai-123")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                          web.make_handler(self.app, self.auth, {"localhost", "127.0.0.1", "::1"}))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.cookie = f"cb_admin_{self.port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, token=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        if token:
            hdrs["Cookie"] = f"{self.cookie}={token}"
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            return resp.status, json.loads(raw), resp
        except ValueError:
            return resp.status, raw, resp

    def login(self):
        status, body, resp = self.request("POST", "/api/login", {"username": "admin", "password": "mat-khau-dai-123"})
        self.assertEqual(status, 200, body)
        cookie = resp.getheader("Set-Cookie")
        self.assertTrue(cookie.startswith(self.cookie + "="), cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        return cookie.split(";")[0].split("=", 1)[1]

    def test_page_is_public_but_data_needs_login(self):
        status, raw, resp = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Qu\xe1\xba\xa3n tr\xe1\xbb\x8b", raw)              # "Quản trị"
        self.assertIn("frame-ancestors 'none'", resp.getheader("Content-Security-Policy"))
        for path in ("/api/summary", "/api/reconcile"):
            status, body, _ = self.request("GET", path)
            self.assertEqual(status, 401, path)
        self.assertEqual((self.mysql.calls, self.rabbit.calls, self.pg.calls), (0, 0, 0))
        status, body, _ = self.request("GET", "/api/session")
        self.assertEqual(body, {"logged_in": False, "user": None})

    def test_logged_in_admin_reads_summary_and_reconcile(self):
        token = self.login()
        status, body, _ = self.request("GET", "/api/summary?minutes=60", token=token)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["minutes"], 60)
        self.assertEqual(body["db"]["totals"]["customers"], 7)
        self.assertEqual(body["rabbit"]["connections"][0]["branch"], "B")
        status, body, _ = self.request("GET", "/api/summary?minutes=7", token=token)
        self.assertEqual(status, 400)
        status, body, _ = self.request("GET", "/api/reconcile", token=token)
        self.assertEqual((status, body["clean"]), (200, True))
        status, body, _ = self.request("GET", "/api/session", token=token)
        self.assertEqual(body, {"logged_in": True, "user": "admin"})

    def test_logout_ends_the_session(self):
        token = self.login()
        status, _, resp = self.request("POST", "/api/logout", {}, token=token)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", resp.getheader("Set-Cookie"))
        status, _, _ = self.request("GET", "/api/summary", token=token)
        self.assertEqual(status, 401)

    def test_foreign_host_origin_and_content_type_are_refused(self):
        status, _, _ = self.request("GET", "/", headers={"Host": "evil.example:8090"})
        self.assertEqual(status, 403)                                       # chống DNS rebinding
        status, _, _ = self.request("POST", "/api/login", {"username": "admin", "password": "mat-khau-dai-123"},
                                    headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/login", body=b"username=admin", headers={"Content-Type": "text/plain"})
        self.assertEqual(conn.getresponse().status, 415)
        conn.close()

    def test_there_is_no_write_endpoint(self):
        token = self.login()
        for path in ("/api/reconcile", "/api/fix", "/api/purge", "/api/summary"):
            status, _, _ = self.request("POST", path, {}, token=token)
            self.assertEqual(status, 404, path)
        for method in ("PUT", "DELETE"):
            status, _, _ = self.request(method, "/api/summary", token=token)
            self.assertEqual(status, 501, method)                          # http.server: không có do_PUT/do_DELETE


class MainTests(unittest.TestCase):
    def test_refuses_to_start_without_a_long_password(self):
        for value in (None, "ngan"):
            env = {k: v for k, v in os.environ.items() if k != "ADMIN_PASSWORD"}
            if value:
                env["ADMIN_PASSWORD"] = value
            with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.stderr"):
                self.assertEqual(web.main(["--port", "0"]), 2)


if __name__ == "__main__":
    unittest.main()
