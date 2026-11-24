"""Repo / engine giả (trong bộ nhớ) có cùng giao diện với bản thật, để kiểm thử logic phân tán
(idempotency, khoá, saga, retry/DLQ) mà không cần RabbitMQ, MySQL, PostgreSQL hay model.

FakeVectorRepo.exclusive() dùng threading.Lock để mô phỏng pg_advisory_xact_lock: nhiều "worker"
(thread) cùng tranh một khoá toàn cục, giống nhiều process tranh khoá trong PostgreSQL.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

import numpy as np

from common.errors import BusinessError, TransientError
from worker.face_engine import FaceAnalysis
from common.messages import Status


def unit(seed: int, dim: int = 512) -> np.ndarray:
    v = np.random.RandomState(seed).randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def noisy(v: np.ndarray, noise: float, seed: int) -> np.ndarray:
    w = v + noise * np.random.RandomState(seed + 10_000).randn(v.shape[0]).astype(np.float32)   # seed tách khỏi unit()
    return w / np.linalg.norm(w)


class FakeEngine:
    """Ảnh "giả" là bytes: b'p:<seed>' -> khuôn mặt của người <seed>; b'p:<seed>:<n>' -> cùng người, hơi khác.
    b'none' -> NO_FACE; b'multi' -> MULTIPLE_FACES."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0

    def analyze(self, jpeg: bytes) -> FaceAnalysis:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if jpeg == b"none":
            return FaceAnalysis(status=Status.NO_FACE, reason="không phát hiện khuôn mặt")
        if jpeg == b"multi":
            return FaceAnalysis(status=Status.MULTIPLE_FACES, n_faces=2, reason="2 mặt")
        parts = jpeg.decode().split(":")
        seed = int(parts[1])
        emb = unit(seed)
        if len(parts) > 2:
            emb = noisy(emb, 0.015, int(parts[2]))
        return FaceAnalysis(status=None, embedding=emb, n_faces=1, det_score=0.99)


class FakePhotos:
    def __init__(self):
        self.files: Dict[str, bytes] = {}
        self._n = 0
        self._lock = threading.Lock()

    def save(self, customer_id, jpeg, branch_id, when=None) -> str:
        with self._lock:
            self._n += 1
            rel = f"{customer_id}/{self._n}.jpg"
            self.files[rel] = jpeg
            return rel

    def delete(self, rel):
        self.files.pop(rel, None)


class FakeMysql:
    def __init__(self):
        self._lock = threading.RLock()
        self.customers: Dict[int, Dict[str, Any]] = {}
        self.photos: List[Dict[str, Any]] = []
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.logs: Dict[str, Dict[str, Any]] = {}
        self._next_customer = 1
        self._next_order = 1
        self.fail_on: Optional[str] = None       # tên method sẽ ném TransientError (để thử lỗi)
        self.write_delay = 0.0                   # mô phỏng độ trễ mạng/đĩa của một lần ghi DB

    def _maybe_fail(self, name):
        if self.fail_on == name:
            raise TransientError(f"giả lập MySQL sập tại {name}")

    # logs
    def get_log(self, request_id):
        with self._lock:
            row = self.logs.get(request_id)
            return dict(row) if row else None

    def insert_log(self, request_id, branch_id, customer_id, status, similarity, latency_ms, worker):
        self._maybe_fail("insert_log")
        with self._lock:
            if request_id in self.logs:
                return False
            self.logs[request_id] = dict(request_id=request_id, branch_id=branch_id, customer_id=customer_id,
                                         status=status, similarity=similarity, latency_ms=latency_ms, worker=worker)
            return True

    # customers
    def create_customer(self, branch_id, full_name):
        self._maybe_fail("create_customer")
        if self.write_delay:
            time.sleep(self.write_delay)
        with self._lock:
            cid = self._next_customer
            self._next_customer += 1
            self.customers[cid] = dict(customer_id=cid, full_name=full_name, phone=None,
                                       first_branch_id=branch_id, created_at="t", last_seen_at="t")
            return cid

    def delete_customer(self, cid):
        with self._lock:
            self.customers.pop(cid, None)
            self.photos = [p for p in self.photos if p["customer_id"] != cid]

    def get_customer(self, cid):
        with self._lock:
            c = self.customers.get(cid)
            return dict(c) if c else None

    def touch_customer(self, cid):
        pass

    def update_customer(self, cid, full_name, phone):
        with self._lock:
            c = self.customers.get(cid)
            if not c:
                return False
            if full_name is not None:
                c["full_name"] = full_name
            if phone is not None:
                c["phone"] = phone
            return True

    def customer_ids(self):
        return sorted(self.customers)

    def add_photo(self, cid, path, branch_id, captured_at):
        self._maybe_fail("add_photo")
        with self._lock:
            self.photos.append(dict(customer_id=cid, file_path=path))
            return len(self.photos)

    # orders
    def get_last_order(self, cid):
        with self._lock:
            mine = [o for o in self.orders.values() if o["customer_id"] == cid]
            if not mine:
                return None
            o = max(mine, key=lambda x: x["order_id"])
            return {"order_id": o["order_id"], "branch_id": o["branch_id"], "total_amount": o["total"],
                    "ordered_at": "t", "note": o["note"], "items": o["items"]}

    def get_frequent_items(self, cid, limit=3):
        """Cùng quy tắc xếp hạng với MySqlRepo: tổng số lượng giảm dần, hoà thì món gọi gần đây hơn, rồi theo tên."""
        self._maybe_fail("get_frequent_items")
        with self._lock:
            agg: Dict[str, Dict[str, Any]] = {}
            for o in self.orders.values():
                if o["customer_id"] != cid:
                    continue
                for it in o["items"]:
                    a = agg.setdefault(it["item_name"], dict(item_name=it["item_name"], total_qty=0,
                                                             order_ids=set(), last=0))
                    a["total_qty"] += it["quantity"]
                    a["order_ids"].add(o["order_id"])
                    a["last"] = max(a["last"], o["order_id"])
            ranked = sorted(agg.values(), key=lambda a: (-a["total_qty"], -a["last"], a["item_name"]))
            return [dict(item_name=a["item_name"], total_qty=a["total_qty"], n_orders=len(a["order_ids"]))
                    for a in ranked[:limit]]

    def create_order(self, request_id, branch_id, customer_id, items, note):
        with self._lock:
            if request_id in self.orders:
                o = self.orders[request_id]
                return o["order_id"], o["total"], False
            if customer_id not in self.customers:
                raise BusinessError(f"khách hàng {customer_id} không tồn tại")
            oid = self._next_order
            self._next_order += 1
            total = round(sum(i["quantity"] * i["unit_price"] for i in items), 2)
            self.orders[request_id] = dict(order_id=oid, customer_id=customer_id, branch_id=branch_id,
                                           total=total, note=note, items=items)
            return oid, total, True


class FakeVectorRepo:
    def __init__(self):
        self._rows: List[tuple] = []             # (customer_id, embedding)
        self._data_lock = threading.Lock()
        self._global = threading.Lock()          # = pg_advisory_xact_lock
        self.fail_on_add = False
        self.max_concurrent_in_lock = 0
        self._inside = 0
        self.lock_acquisitions = 0

    def _search(self, emb, k):
        with self._data_lock:
            sims = sorted(((cid, float(np.dot(emb, e))) for cid, e in self._rows), key=lambda t: -t[1])
        return sims[:k]

    def search(self, emb, k=1):
        return self._search(emb, k)

    def add_embedding(self, cid, emb):
        if self.fail_on_add:
            raise TransientError("giả lập pgvector sập khi ghi embedding")
        with self._data_lock:
            self._rows.append((cid, np.asarray(emb)))
            return len(self._rows)

    def count_for_customer(self, cid):
        with self._data_lock:
            return sum(1 for c, _ in self._rows if c == cid)

    def delete_customer(self, cid):
        with self._data_lock:
            n = len(self._rows)
            self._rows = [r for r in self._rows if r[0] != cid]
            return n - len(self._rows)

    def customer_ids(self):
        with self._data_lock:
            return sorted({c for c, _ in self._rows})

    @contextmanager
    def exclusive(self):
        with self._global:
            self.lock_acquisitions += 1
            self._inside += 1
            self.max_concurrent_in_lock = max(self.max_concurrent_in_lock, self._inside)
            try:
                yield self
            finally:
                self._inside -= 1
