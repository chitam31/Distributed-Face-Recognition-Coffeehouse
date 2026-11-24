"""Nghiệp vụ của worker: nhận diện, đăng ký khách mới, lưu đơn, cập nhật khách.

Lớp này không biết RabbitMQ lẫn driver CSDL; nó chỉ gọi các "repo" qua giao diện nhỏ
(MySqlRepo, PgVectorRepo, PhotoStore, FaceEngine) nên có thể kiểm thử bằng repo giả.

Các cơ chế phân tán nằm ở đây:
  1. IDEMPOTENCY  - recognition_logs.request_id UNIQUE: yêu cầu lặp trả lại kết quả cũ, không tạo khách trùng.
  2. LOCK         - exclusive() = pg_advisory_xact_lock: "tìm lại rồi thêm" thành nguyên tử giữa các worker.
  3. SAGA         - không có transaction phân tán MySQL+pgvector: ghi MySQL trước, pgvector sau,
                    nếu bước sau lỗi thì bù trừ bằng cách xoá khách vừa tạo.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from common.config import Settings
from common.errors import BusinessError, TransientError
from common.messages import Status

log = logging.getLogger("service")

FREQUENT_ITEMS_LIMIT = 3        # số món "hay gọi" kèm trong kết quả khách quen


class RecognitionService:
    def __init__(self, engine, mysql, vectors, photos, settings: Settings, worker_name: str = "worker"):
        self.engine = engine
        self.mysql = mysql
        self.vectors = vectors
        self.photos = photos
        self.s = settings
        self.worker_name = worker_name

    # ================================================================== nhận diện
    def recognize(self, req: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.perf_counter()

        # (1) Idempotency: request_id đã xử lý xong -> trả lại kết quả cũ.
        prior = self.mysql.get_log(req["request_id"])
        if prior is not None:
            return self._replay(prior, t0)

        # (2) Phát hiện + trích đặc trưng. Ảnh hỏng/không có mặt -> không tạo khách rác.
        analysis = self.engine.analyze(req["jpeg"])
        if analysis.status is not None:
            return self._finish(req, analysis.status, None, None, t0, n_faces=analysis.n_faces,
                                message=analysis.reason, quality=analysis.quality)
        emb = analysis.embedding

        # (3) Tìm khách gần nhất trong pgvector.
        hits = self.vectors.search(emb, 1)
        best_id, best_sim = (hits[0] if hits else (None, 0.0))
        if best_id is not None and best_sim >= self.s.sim_threshold:
            customer = self.mysql.get_customer(best_id)
            if customer is not None:
                return self._recognized(req, analysis, customer, best_sim, t0)
            # Embedding mồ côi (khách đã bị xoá khỏi MySQL): dọn rồi coi như chưa nhận ra.
            log.warning("embedding mồ côi của customer_id=%s, đang dọn", best_id)
            self.vectors.delete_customer(best_id)

        if not req["auto_enroll"]:
            return self._finish(req, Status.UNKNOWN, None, best_sim, t0, n_faces=analysis.n_faces,
                                message="chưa nhận ra khách (không tự đăng ký vì auto_enroll=false)",
                                quality=analysis.quality)

        return self._enroll_new(req, analysis, best_sim, t0)

    # ------------------------------------------------------------------ khách quen
    def _recognized(self, req, analysis, customer, similarity, t0, extra_msg: str = "") -> Dict[str, Any]:
        cid = customer["customer_id"]
        last_order = self.mysql.get_last_order(cid)       # đọc trước khi cập nhật gì khác
        frequent = self._frequent_items(cid)
        self.mysql.touch_customer(cid)
        self._maybe_add_embedding(req, analysis, cid, similarity)
        return self._finish(req, Status.RECOGNIZED, customer, similarity, t0, last_order=last_order,
                            frequent_items=frequent, n_faces=analysis.n_faces, message=extra_msg,
                            quality=analysis.quality)

    def _frequent_items(self, customer_id: int):
        """Gợi ý "món hay gọi" (vd. "như mọi khi?"). Chỉ là thông tin thêm: lỗi ở đây không được
        làm hỏng kết quả nhận diện, nên chỉ ghi cảnh báo và trả danh sách rỗng."""
        try:
            return self.mysql.get_frequent_items(customer_id, FREQUENT_ITEMS_LIMIT)
        except Exception as exc:                          # noqa: BLE001 - cố ý nuốt lỗi
            log.warning("bỏ qua 'món hay gọi' của customer_id=%s: %s", customer_id, exc)
            return []

    def _maybe_add_embedding(self, req, analysis, cid: int, similarity: float) -> None:
        """Nhận ra chắc chắn nhưng ảnh khác ảnh cũ -> thêm embedding (tối đa N) để bền với ánh sáng/góc mặt.
        Đây chỉ là tối ưu: lỗi ở đây không được làm hỏng kết quả nhận diện."""
        if not (self.s.add_embedding_min_sim <= similarity <= self.s.add_embedding_max_sim):
            return
        photo = None
        try:
            if self.vectors.count_for_customer(cid) >= self.s.max_embeddings_per_customer:
                return
            photo = self.photos.save(cid, req["jpeg"], req["branch_id"])
            self.mysql.add_photo(cid, photo, req["branch_id"], req.get("captured_at"))
            self.vectors.add_embedding(cid, analysis.embedding)
            log.info("đã thêm embedding cho customer_id=%s (sim=%.3f)", cid, similarity)
        except Exception as exc:                          # noqa: BLE001 - cố ý nuốt lỗi
            self.photos.delete(photo)
            log.warning("bỏ qua việc thêm embedding cho customer_id=%s: %s", cid, exc)

    # ------------------------------------------------------------------ khách mới
    def _enroll_new(self, req, analysis, best_sim: float, t0) -> Dict[str, Any]:
        cid: Optional[int] = None
        photo: Optional[str] = None
        try:
            with self.vectors.exclusive() as vx:
                # Kiểm tra lại BÊN TRONG khoá: worker khác có thể vừa đăng ký chính người này.
                prior = self.mysql.get_log(req["request_id"])
                if prior is not None:
                    return self._replay(prior, t0)
                hits = vx.search(analysis.embedding, 1)
                if hits and hits[0][1] >= self.s.sim_threshold:
                    customer = self.mysql.get_customer(hits[0][0])
                    if customer is not None:
                        return self._recognized(req, analysis, customer, hits[0][1], t0,
                                                extra_msg="khách vừa được đăng ký bởi yêu cầu khác")

                # SAGA: MySQL trước, pgvector sau. Nếu có lỗi -> except bên dưới bù trừ.
                cid = self.mysql.create_customer(req["branch_id"], req.get("full_name"))
                photo = self.photos.save(cid, req["jpeg"], req["branch_id"])
                self.mysql.add_photo(cid, photo, req["branch_id"], req.get("captured_at"))
                vx.add_embedding(cid, analysis.embedding)
            # Thoát `with` = commit pgvector + nhả khoá. Nếu commit lỗi thì except bên dưới cũng bù trừ.
        except BaseException:
            if cid is not None:
                self._compensate(cid, [photo] if photo else [])
            raise

        customer = self.mysql.get_customer(cid)
        return self._finish(req, Status.NEW_CUSTOMER, customer, best_sim, t0, n_faces=analysis.n_faces,
                            message="khách mới đã được lưu", quality=analysis.quality)

    def _compensate(self, customer_id: int, photos) -> None:
        """Hoàn tác phần đã ghi của một lần đăng ký thất bại. Nếu chính việc bù trừ cũng lỗi
        (MySQL sập), tools/reconcile.py sẽ dọn dữ liệu mồ côi sau."""
        log.error("lỗi khi đăng ký khách mới -> bù trừ (xoá customer_id=%s)", customer_id)
        try:
            self.mysql.delete_customer(customer_id)
        except Exception as exc:                          # noqa: BLE001
            log.error("bù trừ MySQL thất bại cho customer_id=%s: %s (cần chạy tools/reconcile.py)",
                      customer_id, exc)
        for rel in photos:
            self.photos.delete(rel)

    # ------------------------------------------------------------------ phát lại kết quả cũ
    def _replay(self, prior: Dict[str, Any], t0) -> Dict[str, Any]:
        cid = prior.get("customer_id")
        customer = self.mysql.get_customer(cid) if cid else None
        last_order = self.mysql.get_last_order(cid) if cid else None
        return self._response(prior["request_id"], prior["status"], customer, prior.get("similarity"), t0,
                              last_order=last_order,
                              frequent_items=self._frequent_items(cid) if cid else None, duplicate=True,
                              message="yêu cầu trùng request_id: trả lại kết quả đã xử lý")

    # ------------------------------------------------------------------ dựng response + ghi log
    def _finish(self, req, status, customer, similarity, t0, *, last_order=None, frequent_items=None,
                n_faces=0, message="", quality=None) -> Dict[str, Any]:
        resp = self._response(req["request_id"], status, customer, similarity, t0, last_order=last_order,
                              frequent_items=frequent_items, n_faces=n_faces, message=message, quality=quality)
        cid = customer["customer_id"] if customer else None
        # Log để tra idempotency và đo đạc. Trùng request_id (worker khác ghi trước) không phải lỗi.
        self.mysql.insert_log(req["request_id"], req["branch_id"], cid, status, similarity,
                              resp["latency_ms"], self.worker_name)
        return resp

    def _response(self, request_id, status, customer, similarity, t0, *, last_order=None, frequent_items=None,
                  n_faces=0, message="", quality=None, duplicate=False) -> Dict[str, Any]:
        return {
            "request_id": request_id,
            "status": status,
            "customer": customer,
            "last_order": last_order,
            "frequent_items": frequent_items or [],
            "similarity": None if similarity is None else round(float(similarity), 4),
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "faces_detected": n_faces,
            "message": message,
            "quality": quality or {},
            "duplicate": duplicate,
        }

    # ================================================================== lưu đơn (luồng C)
    def create_order(self, req: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.perf_counter()
        if self.mysql.get_customer(req["customer_id"]) is None:
            raise BusinessError(f"khách hàng {req['customer_id']} không tồn tại")
        order_id, total, created = self.mysql.create_order(
            req["request_id"], req["branch_id"], req["customer_id"], req["items"], req.get("note"))
        return {
            "request_id": req["request_id"],
            "status": Status.ORDER_SAVED,
            "order_id": order_id,
            "total_amount": total,
            "duplicate": not created,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "message": "đơn đã lưu" if created else "đơn đã được lưu từ trước (yêu cầu trùng request_id)",
        }

    # ================================================================== cập nhật hồ sơ khách
    def update_customer(self, req: Dict[str, Any]) -> Dict[str, Any]:
        t0 = time.perf_counter()
        if not self.mysql.update_customer(req["customer_id"], req.get("full_name"), req.get("phone")):
            raise BusinessError(f"khách hàng {req['customer_id']} không tồn tại")
        return {
            "request_id": req["request_id"],
            "status": Status.CUSTOMER_UPDATED,
            "customer": self.mysql.get_customer(req["customer_id"]),
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "message": "đã cập nhật thông tin khách",
        }

    # ================================================================== đăng ký offline (tools/enroll.py)
    def enroll_person(self, full_name: str, jpegs, branch_id: str = "A") -> Dict[str, Any]:
        """Đăng ký một người từ nhiều ảnh (đường offline, không qua RabbitMQ). Bỏ qua ảnh không dùng được.
        Cùng cơ chế khoá + saga như luồng B."""
        added, skipped = 0, []
        cid: Optional[int] = None
        saved: list = []
        try:
            with self.vectors.exclusive() as vx:
                for idx, jpeg in enumerate(jpegs):
                    analysis = self.engine.analyze(jpeg)
                    if analysis.status is not None:
                        skipped.append((idx, analysis.reason))
                        continue
                    if cid is None:
                        cid = self.mysql.create_customer(branch_id, full_name)
                    photo = self.photos.save(cid, jpeg, branch_id)
                    saved.append(photo)
                    self.mysql.add_photo(cid, photo, branch_id, None)
                    vx.add_embedding(cid, analysis.embedding)
                    added += 1
        except BaseException:
            if cid is not None:
                self._compensate(cid, saved)
            raise
        return {"customer_id": cid, "added": added, "skipped": skipped}
