"""Test lớp web của chi nhánh (client/web.py) bằng RPC giả: server HTTP thật trên cổng ngẫu nhiên, KHÔNG cần RabbitMQ.

Kiểm: chuyển JSON trình duyệt -> message đúng hợp đồng, kiểm tra đầu vào, ánh xạ lỗi -> mã HTTP, chặn yêu cầu từ trang
web khác (CSRF/DNS rebinding), xếp hàng RPC (pika không an toàn đa luồng), làm mới kết nối rảnh, hiển thị 'món hay gọi'.
"""
import base64
import http.client
import json
import sys
import threading
import time
import types
import unittest
from http.server import ThreadingHTTPServer

import cv2
import numpy as np

try:
    import pika  # noqa: F401
except ImportError:                       # máy chạy test không cài client deps: stub tối thiểu để import rpc_client
    _pk, _pe = types.ModuleType("pika"), types.ModuleType("pika.exceptions")
    _pk.exceptions = _pe
    sys.modules["pika"], sys.modules["pika.exceptions"] = _pk, _pe

from client import web                                  # noqa: E402
from client.presenter import describe                   # noqa: E402
from client.rpc_client import RpcError, RpcTimeout      # noqa: E402

MENU = [{"name": "Latte", "price": 49000}, {"name": "Bạc xỉu", "price": 32000}]


class FakeRpc:
    def __init__(self):
        self.sent = []
        self.connects = 0
        self.fail_connect = False
        self.raises = None
        self.delay = 0.0
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def connect(self):
        self.connects += 1
        if self.fail_connect:
            raise OSError("broker tắt")

    def _answer(self, kind, msg):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.raises:
                raise self.raises
            self.sent.append((kind, msg))
            return {"request_id": msg["request_id"], "status": "RECOGNIZED" if kind == "recognize" else "ORDER_SAVED",
                    "worker": "w-test", "latency_ms": 12}
        finally:
            with self._lock:
                self.active -= 1

    def recognize(self, msg):
        return self._answer("recognize", msg)

    def send_command(self, msg):
        return self._answer("command", msg)


def png_data_url(w=1600, h=1200):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (30, 120, 200)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode()


class WebTestCase(unittest.TestCase):
    allowed = {"localhost", "127.0.0.1", "::1"}

    def setUp(self):
        self.rpc = FakeRpc()
        self.now = [100.0]
        self.app = web.BranchWebApp(self.rpc, "A", MENU, clock=lambda: self.now[0])
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.app, self.allowed))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = dict(headers or {})
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        try:
            parsed = json.loads(payload)
        except ValueError:
            parsed = payload
        return resp.status, parsed, resp


class PageAndConfigTests(WebTestCase):
    def test_index_is_served_with_security_headers(self):
        status, body, resp = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Chụp &amp; nhận diện", body.decode("utf-8"))
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")
        self.assertIn("frame-ancestors 'none'", resp.getheader("Content-Security-Policy"))
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")

    def test_config_has_branch_menu_and_status_maps_from_presenter(self):
        status, cfg, _ = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(cfg["branch"], "A")
        self.assertEqual(cfg["menu"], MENU)
        self.assertEqual(cfg["status_text"]["RECOGNIZED"], "KHÁCH QUEN")
        self.assertEqual(cfg["status_color"]["ERROR"], "#b71c1c")

    def test_unknown_paths_are_404(self):
        self.assertEqual(self.request("GET", "/nope")[0], 404)
        self.assertEqual(self.request("POST", "/api/nope", {})[0], 404)

    def test_shipped_menu_is_valid(self):
        self.assertGreaterEqual(len(web.load_menu()), 1)


class RecognizeTests(WebTestCase):
    def test_builds_contract_message_and_resizes_image(self):
        status, resp, _ = self.request("POST", "/api/recognize", {"image": png_data_url(), "request_id": "rid-1"})
        self.assertEqual(status, 200)
        self.assertEqual(resp["worker"], "w-test")
        kind, msg = self.rpc.sent[0]
        self.assertEqual(kind, "recognize")
        self.assertEqual((msg["type"], msg["branch_id"], msg["request_id"], msg["auto_enroll"]),
                         ("face.recognize", "A", "rid-1", True))
        jpeg = base64.b64decode(msg["image_b64"])
        self.assertEqual(jpeg[:2], b"\xff\xd8")                                  # JPEG thật
        h, w = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape[:2]
        self.assertEqual(max(h, w), 640)                                         # co về 640 như CLI/GUI

    def test_options_are_passed_through(self):
        self.request("POST", "/api/recognize", {"image": png_data_url(200, 200), "auto_enroll": False, "full_name": "Cường"})
        msg = self.rpc.sent[0][1]
        self.assertFalse(msg["auto_enroll"])
        self.assertEqual(msg["full_name"], "Cường")

    def test_request_id_is_generated_when_missing(self):
        self.request("POST", "/api/recognize", {"image": png_data_url(200, 200)})
        self.assertRegex(self.rpc.sent[0][1]["request_id"], r"^[0-9a-f]{32}$")

    def test_bad_input_is_400_and_never_reaches_rabbitmq(self):
        good = png_data_url(200, 200)
        bad_bodies = [
            {},                                                                  # thiếu ảnh
            {"image": "!!!not-base64!!!"},
            {"image": "data:image/png;base64," + base64.b64encode(b"hello").decode()},   # base64 hợp lệ nhưng không phải ảnh
            {"image": good, "auto_enroll": "yes"},
            {"image": good, "request_id": "có dấu cách"},
            {"image": good, "full_name": "x" * 101},
            [1, 2, 3],
        ]
        for body in bad_bodies:
            status, resp, _ = self.request("POST", "/api/recognize", body)
            self.assertEqual(status, 400, body if not isinstance(body, dict) else list(body))
            self.assertIn("error", resp)
        self.assertEqual(self.rpc.sent, [])


class OrderAndCustomerTests(WebTestCase):
    ITEMS = [{"item_name": "Latte", "quantity": 2, "unit_price": 49000}]

    def test_order_message(self):
        status, resp, _ = self.request("POST", "/api/order", {"customer_id": 7, "items": self.ITEMS, "request_id": "o-1", "note": "ít đá"})
        self.assertEqual(status, 200)
        kind, msg = self.rpc.sent[0]
        self.assertEqual((kind, msg["type"], msg["customer_id"], msg["request_id"], msg["note"]),
                         ("command", "order.create", 7, "o-1", "ít đá"))
        self.assertEqual(msg["items"], self.ITEMS)

    def test_order_validation(self):
        for body in ({"customer_id": 7, "items": []},
                     {"customer_id": "7", "items": self.ITEMS},
                     {"customer_id": 0, "items": self.ITEMS},
                     {"customer_id": 7, "items": [{"item_name": "Latte", "quantity": 0, "unit_price": 1}]},
                     {"customer_id": 7, "items": [{"item_name": "Latte", "quantity": 1, "unit_price": -5}]}):
            self.assertEqual(self.request("POST", "/api/order", body)[0], 400, body)
        self.assertEqual(self.rpc.sent, [])

    def test_customer_update(self):
        status, _, _ = self.request("POST", "/api/customer", {"customer_id": 7, "full_name": "Cường", "phone": "0912345678"})
        self.assertEqual(status, 200)
        msg = self.rpc.sent[0][1]
        self.assertEqual((msg["type"], msg["full_name"], msg["phone"]), ("customer.update", "Cường", "0912345678"))
        self.assertEqual(self.request("POST", "/api/customer", {"customer_id": 7})[0], 400)                 # không có gì để cập nhật
        self.assertEqual(self.request("POST", "/api/customer", {"customer_id": 7, "phone": "abc"})[0], 400)


class ErrorMappingTests(WebTestCase):
    def test_rpc_errors_map_to_gateway_codes(self):
        body = {"image": png_data_url(200, 200)}
        self.rpc.raises = RpcError("RabbitMQ từ chối")
        self.assertEqual(self.request("POST", "/api/recognize", body)[0], 502)
        self.rpc.raises = RpcTimeout("không nhận được trả lời")
        self.assertEqual(self.request("POST", "/api/recognize", body)[0], 504)

    def test_unexpected_error_is_500_and_hides_details(self):
        self.rpc.raises = RuntimeError("mật-khẩu-bí-mật")
        with self.assertLogs("client.web", level="ERROR"):                    # chi tiết chỉ nằm trong log, không ra trình duyệt
            status, resp, _ = self.request("POST", "/api/recognize", {"image": png_data_url(200, 200)})
        self.assertEqual(status, 500)
        self.assertNotIn("bí-mật", json.dumps(resp, ensure_ascii=False))


class HardeningTests(WebTestCase):
    def test_foreign_host_header_is_rejected(self):
        """DNS rebinding: trang của kẻ xấu trỏ tên miền của họ vào 127.0.0.1 -> Host không phải localhost."""
        self.assertEqual(self.request("GET", "/api/config", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("GET", "/", headers={"Host": "evil.example:8080"})[0], 403)
        self.assertEqual(self.request("GET", "/api/config", headers={"Host": f"localhost:{self.port}"})[0], 200)
        self.assertEqual(self.request("GET", "/api/config", headers={"Host": f"127.0.0.1:{self.port}"})[0], 200)

    def test_cross_origin_and_non_json_posts_are_rejected(self):
        body = {"customer_id": 7, "items": [{"item_name": "Latte", "quantity": 1, "unit_price": 1}]}
        status, _, _ = self.request("POST", "/api/order", body, headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/order", headers={"Content-Type": "text/plain"}, raw=json.dumps(body).encode())
        self.assertEqual(status, 415)
        self.assertEqual(self.rpc.sent, [])

    def test_same_origin_post_is_accepted(self):
        body = {"customer_id": 7, "items": [{"item_name": "Latte", "quantity": 1, "unit_price": 1}]}
        status, _, _ = self.request("POST", "/api/order", body, headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)

    def test_oversized_and_malformed_bodies(self):
        # Chỉ khai báo Content-Length quá lớn (không gửi thật): server phải từ chối ngay, không đọc/đệm 3 MiB vào RAM.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("POST", "/api/recognize")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(web.MAX_BODY_BYTES + 1))
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()
        status, _, _ = self.request("POST", "/api/recognize", headers={"Content-Type": "application/json"}, raw=b"{not json")
        self.assertEqual(status, 400)

    def test_non_loopback_bind_does_not_filter_host(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.app, None))
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("GET", "/api/config", headers={"Host": "192.168.1.5:8080"})
            self.assertEqual(conn.getresponse().status, 200)
        finally:
            server.shutdown()
            server.server_close()


class ConnectionHandlingTests(WebTestCase):
    def call(self):
        return self.request("POST", "/api/recognize", {"image": png_data_url(200, 200)})[0]

    def test_rpc_calls_are_serialized(self):
        """pika không an toàn đa luồng: dù 4 trình duyệt bấm cùng lúc, chỉ một RPC chạy tại một thời điểm."""
        self.rpc.delay = 0.05
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.call())) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(results, [200] * 4)
        self.assertEqual(self.rpc.max_active, 1)

    def test_idle_connection_is_refreshed_before_use(self):
        self.assertEqual(self.call(), 200)
        self.assertEqual(self.rpc.connects, 0)                       # vừa dùng xong: không làm mới
        self.now[0] += web.IDLE_RECONNECT_S + 1
        self.assertEqual(self.call(), 200)
        self.assertEqual(self.rpc.connects, 1)                       # rảnh quá lâu: làm mới đúng một lần
        self.assertEqual(self.call(), 200)
        self.assertEqual(self.rpc.connects, 1)

    def test_failed_refresh_does_not_block_the_call(self):
        self.rpc.fail_connect = True
        self.now[0] += web.IDLE_RECONNECT_S + 1
        self.assertEqual(self.call(), 200)                           # call() tự nối lại/thử lại ở tầng RPC
        self.assertEqual(len(self.rpc.sent), 1)


class MenuAndPresenterTests(unittest.TestCase):
    def test_load_menu_rejects_bad_files(self):
        import os
        import tempfile
        for content in ('{"a":1}', "[]", '[{"name":"x"}]', '[{"name":"x","price":-1}]', '[{"name":" ","price":1}]'):
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
                fh.write(content)
            try:
                with self.assertRaises(ValueError):
                    web.load_menu(fh.name)
            finally:
                os.unlink(fh.name)

    def test_presenter_shows_frequent_items_only_when_present(self):
        resp = {"status": "RECOGNIZED", "customer": {"customer_id": 1, "full_name": "An"}, "last_order": None,
                "frequent_items": [{"item_name": "Latte", "total_qty": 5, "n_orders": 3},
                                   {"item_name": "Bạc xỉu", "total_qty": 2, "n_orders": 2}]}
        self.assertIn("Hay gọi: Latte ×5, Bạc xỉu ×2", describe(resp))
        del resp["frequent_items"]                                    # worker bản cũ không gửi trường này
        self.assertFalse([l for l in describe(resp) if l.startswith("Hay gọi")])


if __name__ == "__main__":
    unittest.main()
