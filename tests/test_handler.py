"""Test quy tắc ack / reply / retry / DLQ của MessageHandler bằng channel giả."""
import base64
import json
import unittest
from types import SimpleNamespace

from common.config import load_settings
from common.messages import (Q_FACE, Q_ORDERS, Status, build_customer_update, build_order_request,
                             build_recognize_request, encode)
from tests.fakes import FakeEngine, FakeMysql, FakePhotos, FakeVectorRepo
from worker.handler import MessageHandler, delivery_attempt
from worker.service import RecognitionService


class Unroutable(Exception):
    pass


class FakeChannel:
    def __init__(self, unroutable=False):
        self.published, self.acked, self.rejected, self.nacked = [], [], [], []
        self.events = []                                   # MỌI hành động, đúng thứ tự gọi (để kiểm reply trước ack)
        self.unroutable = unroutable

    def basic_publish(self, exchange, routing_key, body, properties, mandatory=False):
        if self.unroutable:
            raise Unroutable()
        self.events.append("publish")
        self.published.append((exchange, routing_key, json.loads(body), properties))

    def basic_ack(self, tag):
        self.events.append("ack")
        self.acked.append(tag)

    def basic_reject(self, tag, requeue):
        self.events.append("reject")
        self.rejected.append((tag, requeue))

    def basic_nack(self, tag, requeue):
        self.events.append("nack")
        self.nacked.append((tag, requeue))


def props(corr="c1", reply_to="reply.A.x", headers=None):
    return SimpleNamespace(correlation_id=corr, reply_to=reply_to, headers=headers)


def method(tag=1, redelivered=False):
    return SimpleNamespace(delivery_tag=tag, redelivered=redelivered)


def make_handler(settings=None, **kw):
    s = settings or load_settings({"MAX_DELIVERIES": "3", "RETRY_DELAY_S": "0"})
    svc = RecognitionService(FakeEngine(), FakeMysql(), FakeVectorRepo(), FakePhotos(), s, "w-test")
    sleeps = []
    h = MessageHandler(svc, s, "w-test", make_props=lambda correlation_id=None: SimpleNamespace(correlation_id=correlation_id),
                       unroutable=(Unroutable,), sleep=sleeps.append, **kw)
    h.sleeps = sleeps
    return h, svc


class HandlerTests(unittest.TestCase):
    def test_success_replies_before_ack_with_correlation_id(self):
        h, _ = make_handler()
        ch = FakeChannel()
        msg = build_recognize_request("A", b"p:1", request_id="rq1")
        h.handle(ch, method(5), props(corr="rq1"), encode(msg), Q_FACE)
        self.assertEqual(len(ch.published), 1)
        exchange, key, body, p = ch.published[0]
        self.assertEqual((exchange, key), ("", "reply.A.x"))                # về đúng reply queue của chi nhánh
        self.assertEqual(p.correlation_id, "rq1")
        self.assertEqual(body["status"], Status.NEW_CUSTOMER)
        self.assertEqual(body["worker"], "w-test")
        self.assertEqual(ch.acked, [5])
        self.assertEqual(ch.rejected, [])

    def test_reply_is_published_before_ack_or_reject(self):
        # Ack trước reply mà worker chết ở giữa: message đã bị xoá khỏi hàng đợi nhưng client không nhận được gì,
        # phải chờ hết timeout. Test cũ chỉ kiểm "có reply" và "có ack", không kiểm thứ tự (đột biến đổi chỗ hai dòng
        # vẫn qua 84 test, xem giáo trình Chương 20 mục 20.4 và Chương 26 bài B2-B3).
        cases = [
            ("thành công", Q_FACE, encode(build_recognize_request("A", b"p:1", request_id="rq1")), ["publish", "ack"]),
            ("lỗi nghiệp vụ", Q_ORDERS,
             encode(build_order_request("A", 999, [{"item_name": "x", "quantity": 1, "unit_price": 1}], request_id="o1")),
             ["publish", "ack"]),
            ("message hỏng", Q_FACE, b"{not json", ["publish", "reject"]),
        ]
        for name, queue, body, expected in cases:
            with self.subTest(name):
                h, _ = make_handler()
                ch = FakeChannel()
                h.handle(ch, method(5), props(corr="rq1"), body, queue)
                self.assertEqual(ch.events, expected)

    def test_malformed_json_goes_to_dlq(self):
        h, _ = make_handler()
        ch = FakeChannel()
        h.handle(ch, method(2), props(), b"{not json", Q_FACE)
        self.assertEqual(ch.rejected, [(2, False)])                          # requeue=False -> dead-letter
        self.assertEqual(ch.acked, [])
        self.assertEqual(ch.published[0][2]["status"], Status.ERROR)         # client vẫn nhận ERROR, không phải chờ timeout

    def test_wrong_type_for_queue_goes_to_dlq(self):
        h, _ = make_handler()
        ch = FakeChannel()
        h.handle(ch, method(3), props(), encode({"type": "order.create", "request_id": "x"}), Q_FACE)
        self.assertEqual(ch.rejected, [(3, False)])

    def test_invalid_image_base64_goes_to_dlq(self):
        h, _ = make_handler()
        ch = FakeChannel()
        bad = {"type": "face.recognize", "request_id": "r9", "branch_id": "A", "image_b64": "!!!not-base64!!!"}
        h.handle(ch, method(4), props(corr="r9"), encode(bad), Q_FACE)
        self.assertEqual(ch.rejected, [(4, False)])

    def test_business_error_replies_error_and_acks(self):
        h, _ = make_handler()
        ch = FakeChannel()
        msg = build_order_request("A", 999, [{"item_name": "x", "quantity": 1, "unit_price": 1}], request_id="o1")
        h.handle(ch, method(7), props(corr="o1"), encode(msg), Q_ORDERS)
        self.assertEqual(ch.acked, [7])                                      # thử lại cũng vô ích -> ack
        self.assertEqual(ch.published[0][2]["status"], Status.ERROR)
        self.assertEqual(ch.rejected, [])

    def test_transient_error_requeues_then_dead_letters_on_last_attempt(self):
        h, svc = make_handler()
        svc.mysql.fail_on = "get_log"
        svc.mysql.get_log = lambda rid: (_ for _ in ()).throw(__import__("common.errors", fromlist=["x"]).TransientError("db down"))
        msg = encode(build_recognize_request("A", b"p:1", request_id="rq2"))

        ch = FakeChannel()                                                   # lần 1: chưa có header -> thử lại
        h.handle(ch, method(1), props(corr="rq2"), msg, Q_FACE)
        self.assertEqual(ch.nacked, [(1, True)])
        self.assertEqual(ch.published, [])                                   # chưa trả lời, chờ lần thử sau

        ch = FakeChannel()                                                   # lần 2
        h.handle(ch, method(1, redelivered=True), props(corr="rq2", headers={"x-delivery-count": 1}), msg, Q_FACE)
        self.assertEqual(ch.nacked, [(1, True)])

        ch = FakeChannel()                                                   # lần 3 = MAX_DELIVERIES -> DLQ
        h.handle(ch, method(1, redelivered=True), props(corr="rq2", headers={"x-delivery-count": 2}), msg, Q_FACE)
        self.assertEqual(ch.rejected, [(1, False)])
        self.assertEqual(ch.published[0][2]["status"], Status.ERROR)

    def test_unroutable_reply_still_acks(self):
        """Client đã ngắt (reply queue không còn): vẫn ack; client retry cùng request_id sẽ nhận lại từ log."""
        h, svc = make_handler()
        ch = FakeChannel(unroutable=True)
        msg = encode(build_recognize_request("A", b"p:1", request_id="rq3"))
        h.handle(ch, method(8), props(corr="rq3"), msg, Q_FACE)
        self.assertEqual(ch.acked, [8])
        self.assertEqual(len(svc.mysql.customers), 1)                        # việc đã làm xong

        ch2 = FakeChannel()                                                  # client retry cùng request_id
        h.handle(ch2, method(9), props(corr="rq3"), msg, Q_FACE)
        self.assertTrue(ch2.published[0][2]["duplicate"])
        self.assertEqual(len(svc.mysql.customers), 1)                        # không tạo khách trùng

    def test_no_reply_to_means_no_publish(self):
        h, _ = make_handler()
        ch = FakeChannel()
        h.handle(ch, method(1), props(reply_to=None), encode(build_recognize_request("A", b"p:1")), Q_FACE)
        self.assertEqual(ch.published, [])
        self.assertEqual(ch.acked, [1])

    def test_orders_queue_handles_order_and_update(self):
        h, svc = make_handler()
        ch = FakeChannel()
        h.handle(ch, method(1), props(), encode(build_recognize_request("A", b"p:1", request_id="r1")), Q_FACE)
        cid = ch.published[0][2]["customer"]["customer_id"]

        ch = FakeChannel()
        order = build_order_request("A", cid, [{"item_name": "Latte", "quantity": 2, "unit_price": 45000}], request_id="o1")
        h.handle(ch, method(2), props(corr="o1"), encode(order), Q_ORDERS)
        self.assertEqual(ch.published[0][2]["status"], Status.ORDER_SAVED)
        self.assertEqual(ch.published[0][2]["total_amount"], 90000.0)

        ch = FakeChannel()
        upd = build_customer_update("A", cid, full_name="Cường", phone="0912345678", request_id="u1")
        h.handle(ch, method(3), props(corr="u1"), encode(upd), Q_ORDERS)
        self.assertEqual(ch.published[0][2]["status"], Status.CUSTOMER_UPDATED)
        self.assertEqual(svc.mysql.customers[cid]["full_name"], "Cường")

    def test_demo_delay_sleeps_before_reply(self):
        s = load_settings({"DEMO_DELAY_MS": "3000"})
        h, _ = make_handler(settings=s)
        ch = FakeChannel()
        h.handle(ch, method(1), props(), encode(build_recognize_request("A", b"p:1")), Q_FACE)
        self.assertIn(3.0, h.sleeps)


class DeliveryAttemptTests(unittest.TestCase):
    def test_attempt_numbers(self):
        self.assertEqual(delivery_attempt(props(headers=None), method()), 1)
        self.assertEqual(delivery_attempt(props(headers=None), method(redelivered=True)), 2)
        self.assertEqual(delivery_attempt(props(headers={"x-delivery-count": 2}), method(redelivered=True)), 3)


if __name__ == "__main__":
    unittest.main()
