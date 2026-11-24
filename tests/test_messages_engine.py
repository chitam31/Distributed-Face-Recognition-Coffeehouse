"""Test hợp đồng message, cấu hình và logic chọn mặt / lọc ảnh mờ (không cần model)."""
import base64
import unittest

import cv2
import numpy as np

from common.config import load_settings
from common.messages import (MessageError, Status, build_customer_update, build_order_request,
                             build_recognize_request, decode, encode, validate_customer_update, validate_order,
                             validate_recognize)
from tests.fakes import unit
from worker.face_engine import FaceCandidate, decode_jpeg, evaluate_faces, laplacian_variance


def textured_image(size=400):
    rng = np.random.RandomState(0)
    img = rng.randint(0, 255, (size, size, 3), dtype=np.uint8)           # nhiễu mạnh = rất nét
    return img


def face(x1=100, y1=100, x2=300, y2=300, score=0.99, seed=1):
    return FaceCandidate((x1, y1, x2, y2), score, unit(seed))


class MessageTests(unittest.TestCase):
    def test_roundtrip_recognize(self):
        m = build_recognize_request("A", b"\xff\xd8jpegbytes", request_id="abc123")
        out = validate_recognize(decode(encode(m)))
        self.assertEqual(out["jpeg"], b"\xff\xd8jpegbytes")
        self.assertEqual(out["request_id"], "abc123")
        self.assertTrue(out["auto_enroll"])

    def test_recognize_rejects_bad_fields(self):
        base = build_recognize_request("A", b"x", request_id="id1")
        for patch in ({"request_id": "bad id!"}, {"branch_id": ""}, {"image_b64": ""}, {"image_b64": "@@@"},
                      {"full_name": "x" * 101}):
            with self.assertRaises(MessageError, msg=str(patch)):
                validate_recognize({**base, **patch})

    def test_decode_rejects_non_object(self):
        for body in (b"[1,2]", b"\xff\xfe", b"nope"):
            with self.assertRaises(MessageError):
                decode(body)

    def test_order_validation(self):
        ok = build_order_request("A", 3, [{"item_name": " Latte ", "quantity": 2, "unit_price": 45000, "options": "ít đá"}],
                                 request_id="o1")
        v = validate_order(ok)
        self.assertEqual(v["items"][0]["item_name"], "Latte")
        for bad_items in ([], "x", [{"item_name": "", "quantity": 1, "unit_price": 1}],
                          [{"item_name": "a", "quantity": 0, "unit_price": 1}],
                          [{"item_name": "a", "quantity": 1, "unit_price": -5}],
                          [{"item_name": "a", "quantity": True, "unit_price": 1}]):
            with self.assertRaises(MessageError):
                validate_order({**ok, "items": bad_items})
        with self.assertRaises(MessageError):
            validate_order({**ok, "customer_id": 0})

    def test_customer_update_validation(self):
        v = validate_customer_update(build_customer_update("A", 5, full_name="  Dũng ", request_id="u1"))
        self.assertEqual(v["full_name"], "Dũng")
        with self.assertRaises(MessageError):
            validate_customer_update(build_customer_update("A", 5, request_id="u2"))
        with self.assertRaises(MessageError):
            validate_customer_update(build_customer_update("A", 5, phone="abc", request_id="u3"))


class ConfigTests(unittest.TestCase):
    def test_defaults_and_overrides(self):
        s = load_settings({})
        self.assertEqual(s.queue_type, "quorum")
        self.assertEqual(s.rabbit_port, 5672)
        s = load_settings({"RABBIT_HOST": "10.0.0.5", "SIM_THRESHOLD": "0.5", "RABBIT_TLS": "true", "DEMO_DELAY_MS": "250"})
        self.assertEqual((s.rabbit_host, s.sim_threshold, s.rabbit_tls, s.demo_delay_ms), ("10.0.0.5", 0.5, True, 250))
        self.assertIn("host=localhost", s.pg_dsn)


class EngineLogicTests(unittest.TestCase):
    def setUp(self):
        self.s = load_settings({})
        self.img = textured_image()

    def test_single_good_face_returns_unit_embedding(self):
        a = evaluate_faces(self.img, [face()], self.s)
        self.assertIsNone(a.status)
        self.assertAlmostEqual(float(np.linalg.norm(a.embedding)), 1.0, places=5)

    def test_no_faces(self):
        a = evaluate_faces(self.img, [], self.s)
        self.assertEqual(a.status, Status.NO_FACE)

    def test_too_small_or_low_score_face_is_no_face(self):
        self.assertEqual(evaluate_faces(self.img, [face(100, 100, 130, 130)], self.s).status, Status.NO_FACE)
        self.assertEqual(evaluate_faces(self.img, [face(score=0.2)], self.s).status, Status.NO_FACE)

    def test_blurry_face_is_no_face(self):
        blurry = cv2.GaussianBlur(self.img, (0, 0), 25)
        a = evaluate_faces(blurry, [face()], self.s)
        self.assertEqual(a.status, Status.NO_FACE)
        self.assertIn("mờ", a.reason)
        self.assertLess(laplacian_variance(blurry, (100, 100, 300, 300)), laplacian_variance(self.img, (100, 100, 300, 300)))

    def test_multiple_faces_policies(self):
        faces = [face(50, 50, 150, 150, seed=1), face(200, 100, 380, 300, seed=2)]
        a = evaluate_faces(self.img, faces, load_settings({"MULTI_FACE_POLICY": "largest"}))
        self.assertIsNone(a.status)
        self.assertEqual(a.n_faces, 2)
        np.testing.assert_allclose(a.embedding, unit(2), atol=1e-6)         # chọn mặt lớn nhất
        b = evaluate_faces(self.img, faces, load_settings({"MULTI_FACE_POLICY": "reject"}))
        self.assertEqual(b.status, Status.MULTIPLE_FACES)

    def test_decode_jpeg(self):
        ok, buf = cv2.imencode(".jpg", self.img)
        self.assertEqual(decode_jpeg(buf.tobytes()).shape, self.img.shape)
        with self.assertRaises(MessageError):
            decode_jpeg(b"not a jpeg")


if __name__ == "__main__":
    unittest.main()
