"""Test lõi nghiệp vụ và các cơ chế phân tán bằng repo giả.  Chạy:  python -m unittest discover -s tests -v"""
import base64
import threading
import unittest

from common.config import load_settings
from common.errors import BusinessError, TransientError
from common.messages import Status
from tests.fakes import FakeEngine, FakeMysql, FakePhotos, FakeVectorRepo
from worker.service import RecognitionService


def recognize_req(rid, jpeg, branch="A", auto_enroll=True, name=None):
    return {"request_id": rid, "branch_id": branch, "captured_at": None, "auto_enroll": auto_enroll,
            "full_name": name, "jpeg": jpeg}


def make_service(engine=None, settings=None, name="w1", mysql=None, vectors=None, photos=None):
    s = settings or load_settings({})
    svc = RecognitionService(engine or FakeEngine(), mysql or FakeMysql(), vectors or FakeVectorRepo(),
                             photos or FakePhotos(), s, name)
    return svc


class FlowTests(unittest.TestCase):
    def test_new_customer_then_recognized_at_other_branch(self):
        """Kịch bản demo 1+2: người mới ở chi nhánh A, sau đó được nhận ra ở chi nhánh B kèm đơn lần trước."""
        svc = make_service()
        r1 = svc.recognize(recognize_req("r1", b"p:1", branch="A", name="An"))
        self.assertEqual(r1["status"], Status.NEW_CUSTOMER)
        cid = r1["customer"]["customer_id"]
        self.assertEqual(r1["customer"]["full_name"], "An")
        self.assertIsNone(r1["last_order"])

        order = svc.create_order({"request_id": "o1", "branch_id": "A", "customer_id": cid, "note": None,
                                  "items": [{"item_name": "Cà phê sữa đá", "quantity": 2, "unit_price": 35000.0,
                                             "options": "ít đá"}]})
        self.assertEqual(order["status"], Status.ORDER_SAVED)
        self.assertEqual(order["total_amount"], 70000.0)

        r2 = svc.recognize(recognize_req("r2", b"p:1:7", branch="B"))      # cùng người, ảnh hơi khác
        self.assertEqual(r2["status"], Status.RECOGNIZED)
        self.assertEqual(r2["customer"]["customer_id"], cid)
        self.assertEqual(r2["last_order"]["items"][0]["item_name"], "Cà phê sữa đá")
        self.assertGreaterEqual(r2["similarity"], 0.9)

    def test_stranger_is_new_customer(self):
        svc = make_service()
        svc.recognize(recognize_req("r1", b"p:1"))
        r = svc.recognize(recognize_req("r2", b"p:2"))
        self.assertEqual(r["status"], Status.NEW_CUSTOMER)
        self.assertEqual(len(svc.mysql.customers), 2)

    def test_no_face_and_multiple_faces_create_nothing(self):
        """Kịch bản demo 4: che mặt/ảnh mờ -> NO_FACE, không tạo khách rác."""
        svc = make_service()
        self.assertEqual(svc.recognize(recognize_req("r1", b"none"))["status"], Status.NO_FACE)
        self.assertEqual(svc.recognize(recognize_req("r2", b"multi"))["status"], Status.MULTIPLE_FACES)
        self.assertEqual(svc.mysql.customers, {})
        self.assertEqual(svc.vectors.customer_ids(), [])
        self.assertEqual(len(svc.mysql.logs), 2)                          # vẫn có log để đo đạc

    def test_auto_enroll_false_returns_unknown_without_creating(self):
        svc = make_service()
        r = svc.recognize(recognize_req("r1", b"p:5", auto_enroll=False))
        self.assertEqual(r["status"], Status.UNKNOWN)
        self.assertEqual(svc.mysql.customers, {})

    def test_extra_embedding_added_for_recognized_customer_up_to_limit(self):
        s = load_settings({"ADD_EMB_MIN_SIM": "0.5", "ADD_EMB_MAX_SIM": "0.999999", "MAX_EMBEDDINGS": "3"})
        svc = make_service(settings=s)
        cid = svc.recognize(recognize_req("r0", b"p:1"))["customer"]["customer_id"]
        for i in range(1, 8):
            r = svc.recognize(recognize_req(f"r{i}", f"p:1:{i}".encode()))
            self.assertEqual(r["status"], Status.RECOGNIZED)
        self.assertEqual(svc.vectors.count_for_customer(cid), 3)           # chặn ở tối đa 3


class IdempotencyTests(unittest.TestCase):
    def test_same_request_id_twice_creates_one_customer(self):
        """Client timeout rồi retry cùng request_id: không được tạo khách trùng."""
        svc = make_service()
        r1 = svc.recognize(recognize_req("same", b"p:9"))
        r2 = svc.recognize(recognize_req("same", b"p:9"))
        self.assertEqual(r1["status"], Status.NEW_CUSTOMER)
        self.assertEqual(r2["status"], Status.NEW_CUSTOMER)                # kết quả nhất quán với lần đầu
        self.assertTrue(r2["duplicate"])
        self.assertEqual(r1["customer"]["customer_id"], r2["customer"]["customer_id"])
        self.assertEqual(len(svc.mysql.customers), 1)
        self.assertEqual(svc.engine.calls, 1)                              # lần hai không chạy lại model

    def test_redelivery_after_worker_crash_is_safe(self):
        """Worker 1 ghi xong rồi chết trước khi ack; worker 2 nhận lại cùng message."""
        mysql, vectors, photos = FakeMysql(), FakeVectorRepo(), FakePhotos()
        w1 = make_service(mysql=mysql, vectors=vectors, photos=photos, name="w1")
        w2 = make_service(mysql=mysql, vectors=vectors, photos=photos, name="w2")
        w1.recognize(recognize_req("m1", b"p:3"))                          # (ack không bao giờ xảy ra)
        r = w2.recognize(recognize_req("m1", b"p:3"))
        self.assertTrue(r["duplicate"])
        self.assertEqual(len(mysql.customers), 1)

    def test_order_is_idempotent(self):
        svc = make_service()
        cid = svc.recognize(recognize_req("r1", b"p:1"))["customer"]["customer_id"]
        req = {"request_id": "ord-1", "branch_id": "A", "customer_id": cid, "note": None,
               "items": [{"item_name": "Bạc xỉu", "quantity": 1, "unit_price": 30000.0, "options": None}]}
        a, b = svc.create_order(req), svc.create_order(req)
        self.assertEqual(a["order_id"], b["order_id"])
        self.assertFalse(a["duplicate"])
        self.assertTrue(b["duplicate"])
        self.assertEqual(len(svc.mysql.orders), 1)

    def test_order_for_missing_customer_is_business_error(self):
        svc = make_service()
        with self.assertRaises(BusinessError):
            svc.create_order({"request_id": "o", "branch_id": "A", "customer_id": 999, "note": None,
                              "items": [{"item_name": "x", "quantity": 1, "unit_price": 1.0, "options": None}]})

    def test_update_customer(self):
        svc = make_service()
        cid = svc.recognize(recognize_req("r1", b"p:1"))["customer"]["customer_id"]
        r = svc.update_customer({"request_id": "u1", "branch_id": "A", "customer_id": cid,
                                 "full_name": "Bình", "phone": "0901234567"})
        self.assertEqual(r["status"], Status.CUSTOMER_UPDATED)
        self.assertEqual(r["customer"]["full_name"], "Bình")
        with self.assertRaises(BusinessError):
            svc.update_customer({"request_id": "u2", "branch_id": "A", "customer_id": 12345, "full_name": "X",
                                 "phone": None})


class ConcurrencyTests(unittest.TestCase):
    def test_many_workers_same_new_person_creates_exactly_one_customer(self):
        """Ba chi nhánh cùng lúc gặp một người mới (kịch bản race trong báo cáo): nhờ khoá chỉ có 1 khách."""
        mysql, vectors, photos = FakeMysql(), FakeVectorRepo(), FakePhotos()
        mysql.write_delay = 0.03
        workers = [make_service(engine=FakeEngine(delay=0.02), mysql=mysql, vectors=vectors, photos=photos,
                                name=f"w{i}") for i in range(8)]
        results, barrier = [], threading.Barrier(8)

        def run(i):
            barrier.wait()
            results.append(workers[i].recognize(recognize_req(f"req-{i}", f"p:42:{i}".encode(), branch="ABC"[i % 3])))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]

        self.assertEqual(len(mysql.customers), 1, "khoá phải ngăn tạo khách trùng")
        statuses = sorted(r["status"] for r in results)
        self.assertEqual(statuses.count(Status.NEW_CUSTOMER), 1)
        self.assertEqual(statuses.count(Status.RECOGNIZED), 7)
        self.assertEqual(vectors.max_concurrent_in_lock, 1, "tại một thời điểm chỉ một worker trong vùng găng")

    def test_without_lock_race_would_create_duplicates(self):
        """Đối chứng: bỏ khoá thì cùng kịch bản tạo ra nhiều khách (chứng minh khoá có tác dụng)."""
        from contextlib import contextmanager

        class NoLockVectors(FakeVectorRepo):
            @contextmanager
            def exclusive(self):
                yield self

        mysql, vectors, photos = FakeMysql(), NoLockVectors(), FakePhotos()
        mysql.write_delay = 0.03
        workers = [make_service(engine=FakeEngine(delay=0.05), mysql=mysql, vectors=vectors, photos=photos)
                   for _ in range(6)]
        barrier = threading.Barrier(6)

        def run(i):
            barrier.wait()
            workers[i].recognize(recognize_req(f"req-{i}", f"p:42:{i}".encode()))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertGreater(len(mysql.customers), 1)

    def test_different_people_concurrently_get_distinct_customers(self):
        mysql, vectors, photos = FakeMysql(), FakeVectorRepo(), FakePhotos()
        workers = [make_service(mysql=mysql, vectors=vectors, photos=photos) for _ in range(5)]
        threads = [threading.Thread(target=lambda i=i: workers[i].recognize(recognize_req(f"q{i}", f"p:{100+i}".encode())))
                   for i in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(mysql.customers), 5)


class SagaTests(unittest.TestCase):
    def test_vector_failure_compensates_mysql_and_photo(self):
        """pgvector lỗi sau khi MySQL đã ghi -> xoá khách + ảnh vừa tạo (saga), không để dữ liệu mồ côi."""
        svc = make_service()
        svc.vectors.fail_on_add = True
        with self.assertRaises(TransientError):
            svc.recognize(recognize_req("r1", b"p:1"))
        self.assertEqual(svc.mysql.customers, {})
        self.assertEqual(svc.photos.files, {})
        self.assertEqual(svc.mysql.logs, {})                               # chưa ghi log -> retry sẽ chạy lại

        svc.vectors.fail_on_add = False                                    # hệ thống hồi phục
        r = svc.recognize(recognize_req("r1", b"p:1"))                     # message được giao lại
        self.assertEqual(r["status"], Status.NEW_CUSTOMER)
        self.assertEqual(len(svc.mysql.customers), 1)

    def test_mysql_failure_before_any_write_leaves_nothing(self):
        svc = make_service()
        svc.mysql.fail_on = "create_customer"
        with self.assertRaises(TransientError):
            svc.recognize(recognize_req("r1", b"p:1"))
        self.assertEqual(svc.vectors.customer_ids(), [])

    def test_orphan_embedding_is_cleaned_and_person_reenrolled(self):
        svc = make_service()
        cid = svc.recognize(recognize_req("r1", b"p:1"))["customer"]["customer_id"]
        svc.mysql.delete_customer(cid)                                     # khách biến mất khỏi MySQL
        r = svc.recognize(recognize_req("r2", b"p:1:3"))
        self.assertEqual(r["status"], Status.NEW_CUSTOMER)
        self.assertEqual(svc.vectors.customer_ids(), [r["customer"]["customer_id"]])

    def test_enroll_person_skips_bad_photos(self):
        svc = make_service()
        out = svc.enroll_person("Chi", [b"p:7", b"none", b"p:7:1"], "A")
        self.assertEqual(out["added"], 2)
        self.assertEqual(len(out["skipped"]), 1)
        self.assertEqual(svc.vectors.count_for_customer(out["customer_id"]), 2)


class FrequentItemsTests(unittest.TestCase):
    """Gợi ý "món hay gọi" kèm kết quả khách quen (không đổi hợp đồng cũ: chỉ thêm trường `frequent_items`)."""

    @staticmethod
    def _order(svc, cid, rid, items):
        svc.create_order({"request_id": rid, "branch_id": "A", "customer_id": cid, "note": None,
                          "items": [{"item_name": n, "quantity": q, "unit_price": 30000.0, "options": None}
                                    for n, q in items]})

    def test_ranked_by_total_quantity_across_orders(self):
        svc = make_service()
        cid = svc.recognize(recognize_req("r1", b"p:1"))["customer"]["customer_id"]
        self._order(svc, cid, "o1", [("Latte", 1), ("Cà phê sữa đá", 2)])
        self._order(svc, cid, "o2", [("Cà phê sữa đá", 1), ("Bạc xỉu", 1)])
        r = svc.recognize(recognize_req("r2", b"p:1:3", branch="B"))
        self.assertEqual(r["status"], Status.RECOGNIZED)
        self.assertEqual(r["frequent_items"][0], {"item_name": "Cà phê sữa đá", "total_qty": 3, "n_orders": 2})
        # hoà nhau (1 ly mỗi món): món gọi gần đây hơn (Bạc xỉu ở đơn o2) đứng trước Latte
        self.assertEqual([i["item_name"] for i in r["frequent_items"]], ["Cà phê sữa đá", "Bạc xỉu", "Latte"])

    def test_limited_to_three_items(self):
        svc = make_service()
        cid = svc.recognize(recognize_req("r1", b"p:1"))["customer"]["customer_id"]
        self._order(svc, cid, "o1", [("A", 5), ("B", 4), ("C", 3), ("D", 2), ("E", 1)])
        r = svc.recognize(recognize_req("r2", b"p:1:3"))
        self.assertEqual([i["item_name"] for i in r["frequent_items"]], ["A", "B", "C"])

    def test_new_customer_and_customer_without_orders_get_empty_list(self):
        svc = make_service()
        r1 = svc.recognize(recognize_req("r1", b"p:1"))
        self.assertEqual(r1["frequent_items"], [])
        r2 = svc.recognize(recognize_req("r2", b"p:1:3"))
        self.assertEqual(r2["status"], Status.RECOGNIZED)
        self.assertEqual(r2["frequent_items"], [])

    def test_other_customers_orders_are_not_mixed_in(self):
        svc = make_service()
        c1 = svc.recognize(recognize_req("r1", b"p:1"))["customer"]["customer_id"]
        c2 = svc.recognize(recognize_req("r2", b"p:2"))["customer"]["customer_id"]
        self._order(svc, c1, "o1", [("Latte", 2)])
        self._order(svc, c2, "o2", [("Trà vải", 9)])
        r = svc.recognize(recognize_req("r3", b"p:1:3"))
        self.assertEqual([i["item_name"] for i in r["frequent_items"]], ["Latte"])

    def test_replayed_result_also_carries_frequent_items(self):
        svc = make_service()
        cid = svc.recognize(recognize_req("r1", b"p:1"))["customer"]["customer_id"]
        self._order(svc, cid, "o1", [("Latte", 2)])
        first = svc.recognize(recognize_req("r2", b"p:1:3"))
        again = svc.recognize(recognize_req("r2", b"p:1:3"))               # cùng request_id -> phát lại
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["frequent_items"], first["frequent_items"])

    def test_failure_of_optional_lookup_does_not_break_recognition(self):
        """Tính năng phụ lỗi (vd. SQL sai) tuyệt đối không được làm hỏng luồng nhận diện chính."""
        svc = make_service()
        cid = svc.recognize(recognize_req("r1", b"p:1"))["customer"]["customer_id"]
        self._order(svc, cid, "o1", [("Latte", 2)])
        svc.mysql.fail_on = "get_frequent_items"
        r = svc.recognize(recognize_req("r2", b"p:1:3"))
        self.assertEqual(r["status"], Status.RECOGNIZED)
        self.assertEqual(r["frequent_items"], [])
        self.assertEqual(r["last_order"]["items"][0]["item_name"], "Latte")


if __name__ == "__main__":
    unittest.main()
