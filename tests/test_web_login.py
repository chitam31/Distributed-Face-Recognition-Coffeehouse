"""Test đăng nhập theo chi nhánh của web chi nhánh (client/web.py, BranchLogin) bằng RPC giả, KHÔNG cần RabbitMQ.

Kiểm: chưa đăng nhập thì mọi API nghiệp vụ trả 401 và không gì tới RabbitMQ; chi nhánh lấy từ tên tài khoản và message
mang đúng mã đó; máy bị ghim một chi nhánh thì tài khoản chi nhánh khác bị từ chối (không thử nối broker); cookie
HttpOnly + SameSite=Strict, tên theo cổng; chặn đăng nhập từ trang web khác; khoá tạm sau nhiều lần sai; một máy một
phiên; đăng xuất và hết hạn đều đóng kết nối RabbitMQ của phiên.
"""
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from tests.test_web import MENU, FakeRpc, png_data_url    # cũng lo stub pika khi máy test không cài
from client import web


class ClosableRpc(FakeRpc):
    def __init__(self, user):
        super().__init__()
        self.user = user
        self.closed = False

    def close(self):
        self.closed = True


class LoginTestCase(unittest.TestCase):
    pinned = "B"
    passwords = {"branch_a": "branch_a_pass", "branch_b": "branch_b_pass"}

    def setUp(self):
        self.now = [1000.0]
        self.made = []                                    # (user, branch) mỗi lần make_app thật sự "nối broker"
        self.clients = []

        def make_app(user, password, branch):
            self.made.append((user, branch))
            if self.passwords.get(user) != password:
                raise web.HttpError(401, "sai tài khoản hoặc mật khẩu")
            rpc = ClosableRpc(user)
            self.clients.append(rpc)
            return web.BranchWebApp(rpc, branch, MENU, clock=lambda: self.now[0])

        self.auth = web.BranchLogin(make_app, pinned_branch=self.pinned, clock=lambda: self.now[0])
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                          web.make_handler(None, {"localhost", "127.0.0.1", "::1"}, self.auth))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.cookie_name = f"cb_session_{self.port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, token=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        if token:
            hdrs["Cookie"] = f"{self.cookie_name}={token}"
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        try:
            parsed = json.loads(payload)
        except ValueError:
            parsed = payload
        return resp.status, parsed, resp

    def login(self, user="branch_b", password="branch_b_pass", **kw):
        status, body, resp = self.request("POST", "/api/login", {"username": user, "password": password}, **kw)
        token = None
        cookie = resp.getheader("Set-Cookie") or ""
        if cookie.startswith(self.cookie_name + "="):
            token = cookie.split(";", 1)[0].split("=", 1)[1] or None
        return status, body, token, cookie


class AccessTests(LoginTestCase):
    def test_page_is_public_but_business_api_needs_login(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        status, ses, _ = self.request("GET", "/api/session")
        self.assertEqual((status, ses["login_required"], ses["logged_in"], ses["pinned_branch"]), (200, True, False, "B"))
        self.assertEqual(self.request("GET", "/api/config")[0], 401)
        for path, body in (("/api/recognize", {"image": png_data_url(200, 200)}),
                           ("/api/order", {"customer_id": 7, "items": [{"item_name": "Latte", "quantity": 1, "unit_price": 1}]}),
                           ("/api/customer", {"customer_id": 7, "full_name": "An"})):
            self.assertEqual(self.request("POST", path, body)[0], 401, path)
        self.assertEqual(self.request("GET", "/api/config", token="token-bia-dat")[0], 401)
        self.assertEqual(self.made, [])                               # chưa từng thử nối broker

    def test_login_sets_strict_httponly_cookie_and_branch_comes_from_account(self):
        status, body, token, cookie = self.login()
        self.assertEqual((status, body), (200, {"user": "branch_b", "branch": "B"}))
        self.assertTrue(token)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Path=/", cookie)
        status, cfg, _ = self.request("GET", "/api/config", token=token)
        self.assertEqual((status, cfg["branch"], cfg["user"]), (200, "B", "branch_b"))
        status, _, _ = self.request("POST", "/api/recognize", {"image": png_data_url(200, 200)}, token=token)
        self.assertEqual(status, 200)
        kind, msg = self.clients[0].sent[0]
        self.assertEqual((kind, msg["branch_id"]), ("recognize", "B"))   # trình duyệt không chọn được chi nhánh
        status, ses, _ = self.request("GET", "/api/session", token=token)
        self.assertEqual((ses["logged_in"], ses["branch"], ses["user"]), (True, "B", "branch_b"))

    def test_username_is_normalised(self):
        status, body, token, _ = self.login(user="  Branch_B ")
        self.assertEqual((status, body["user"]), (200, "branch_b"))


class RejectionTests(LoginTestCase):
    def test_wrong_password_is_401_without_cookie(self):
        status, body, token, cookie = self.login(password="sai")
        self.assertEqual(status, 401)
        self.assertIn("error", body)
        self.assertEqual(cookie, "")
        self.assertIsNone(token)

    def test_account_of_another_branch_is_refused_on_a_pinned_machine(self):
        status, body, token, _ = self.login(user="branch_a", password="branch_a_pass")
        self.assertEqual(status, 403)
        self.assertIn("chi nhánh B", body["error"])
        self.assertIsNone(token)
        self.assertEqual(self.made, [])                               # bị chặn trước khi thử nối broker

    def test_malformed_login_bodies_are_400(self):
        for body in ({}, {"username": "branch_b"}, {"username": "admin", "password": "x"},
                     {"username": "branch_bb", "password": "x"}, {"username": "branch_b", "password": ""},
                     {"username": "branch_b", "password": "x" * 129}, {"username": 5, "password": "x"}, [1]):
            status, _, _ = self.request("POST", "/api/login", body)
            self.assertEqual(status, 400, body)
        self.assertEqual(self.made, [])

    def test_cross_origin_or_non_json_login_is_rejected(self):
        status, _, token, _ = self.login(headers={"Origin": "http://evil.example"})
        self.assertEqual((status, token), (403, None))
        status, _, _ = self.request("POST", "/api/login", headers={"Content-Type": "text/plain"},
                                    raw=json.dumps({"username": "branch_b", "password": "branch_b_pass"}).encode())
        self.assertEqual(status, 415)
        self.assertEqual(self.made, [])

    def test_lockout_after_repeated_wrong_passwords(self):
        for _ in range(web.LOGIN_MAX_FAILS):
            self.assertEqual(self.login(password="sai")[0], 401)
        tries = len(self.made)
        status, body, token, _ = self.login()                        # đúng mật khẩu nhưng đang bị khoá
        self.assertEqual((status, token), (429, None))
        self.assertEqual(len(self.made), tries)                       # bị khoá thì không thử nối broker
        self.now[0] += web.LOGIN_LOCK_S + 1
        self.assertEqual(self.login()[0], 200)


class SessionLifecycleTests(LoginTestCase):
    def test_logout_clears_cookie_closes_connection_and_blocks_api(self):
        _, _, token, _ = self.login()
        status, _, resp = self.request("POST", "/api/logout", {}, token=token)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", resp.getheader("Set-Cookie"))
        self.assertTrue(self.clients[0].closed)
        self.assertEqual(self.request("GET", "/api/config", token=token)[0], 401)

    def test_new_login_replaces_the_previous_session(self):
        _, _, first, _ = self.login()
        _, _, second, _ = self.login()
        self.assertNotEqual(first, second)
        self.assertTrue(self.clients[0].closed)                       # kết nối RabbitMQ của phiên cũ được đóng
        self.assertEqual(self.request("GET", "/api/config", token=first)[0], 401)
        self.assertEqual(self.request("GET", "/api/config", token=second)[0], 200)

    def test_idle_session_expires_and_closes_connection(self):
        _, _, token, _ = self.login()
        self.now[0] += web.SESSION_IDLE_S - 10
        self.assertEqual(self.request("GET", "/api/config", token=token)[0], 200)   # dùng là được gia hạn
        self.now[0] += web.SESSION_IDLE_S + 1
        self.assertEqual(self.request("GET", "/api/config", token=token)[0], 401)
        self.assertTrue(self.clients[0].closed)


class UnpinnedAndHelpersTests(LoginTestCase):
    pinned = None

    def test_unpinned_machine_takes_branch_from_account(self):
        status, body, token, _ = self.login(user="branch_a", password="branch_a_pass")
        self.assertEqual((status, body["branch"]), (200, "A"))
        self.request("POST", "/api/recognize", {"image": png_data_url(200, 200)}, token=token)
        self.assertEqual(self.clients[0].sent[0][1]["branch_id"], "A")

    def test_classify_connect_error_by_pika_class_name(self):
        def err(name, text=""):
            return type(name, (Exception,), {})(text)
        self.assertEqual(web.classify_connect_error(err("ProbableAuthenticationError"))[0], 401)
        self.assertEqual(web.classify_connect_error(err("AMQPConnectionError", "ConnectionClosedByBroker: (403) 'ACCESS_REFUSED'"))[0], 401)
        self.assertEqual(web.classify_connect_error(err("ProbableAccessDeniedError"))[0], 403)
        self.assertEqual(web.classify_connect_error(err("AMQPConnectionError"))[0], 503)
        self.assertEqual(web.classify_connect_error(OSError("timed out"))[0], 503)

    def test_no_login_mode_reports_login_not_required(self):
        app = web.BranchWebApp(FakeRpc(), "C", MENU)
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(app, None))
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", "/api/session")
            body = json.loads(conn.getresponse().read())
            self.assertEqual((body["login_required"], body["branch"]), (False, "C"))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
