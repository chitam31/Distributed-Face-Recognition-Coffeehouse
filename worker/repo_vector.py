"""Kho vector khuôn mặt: PostgreSQL + pgvector (psycopg 3).

Điểm phân tán quan trọng: exclusive() giữ pg_advisory_xact_lock trong MỘT transaction trên MỘT kết nối,
để chuỗi "tìm lại -> tạo khách -> ghi embedding" của các worker khác nhau (kể cả ở nhiều máy)
chạy tuần tự. Khoá nằm trong PostgreSQL nên mọi worker đều thấy cùng một khoá.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, List, Sequence, Tuple

import numpy as np

from common.errors import TransientError

log = logging.getLogger("repo_vector")

ENROLL_LOCK_KEY = 4020001          # số bất kỳ, dùng chung cho mọi worker


def to_literal(embedding: Sequence[float]) -> str:
    """Chuyển vector sang literal của pgvector: '[0.1,0.2,...]' (không cần thư viện pgvector cho Python)."""
    arr = np.asarray(embedding, dtype=np.float32).ravel()
    return "[" + ",".join(f"{float(x):.7g}" for x in arr) + "]"


class _Ops:
    """Các thao tác trên một kết nối psycopg đã mở (dùng chung cho chế độ autocommit và chế độ khoá)."""

    def __init__(self, conn):
        self._conn = conn

    def search(self, embedding, k: int = 1) -> List[Tuple[int, float]]:
        lit = to_literal(embedding)
        rows = self._conn.execute(
            "SELECT customer_id, 1 - (embedding <=> %s::vector) AS similarity "
            "FROM face_embeddings ORDER BY embedding <=> %s::vector LIMIT %s",
            (lit, lit, k)).fetchall()
        return [(int(r[0]), float(r[1])) for r in rows]

    def add_embedding(self, customer_id: int, embedding) -> int:
        row = self._conn.execute(
            "INSERT INTO face_embeddings (customer_id, embedding) VALUES (%s, %s::vector) RETURNING embedding_id",
            (int(customer_id), to_literal(embedding))).fetchone()
        return int(row[0])

    def count_for_customer(self, customer_id: int) -> int:
        row = self._conn.execute("SELECT count(*) FROM face_embeddings WHERE customer_id = %s",
                                 (int(customer_id),)).fetchone()
        return int(row[0])

    def delete_customer(self, customer_id: int) -> int:
        cur = self._conn.execute("DELETE FROM face_embeddings WHERE customer_id = %s", (int(customer_id),))
        return cur.rowcount


def _translate(exc: Exception) -> Exception:
    import psycopg
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return TransientError(f"PostgreSQL không khả dụng: {exc}")
    return exc


class PgVectorRepo:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._conn = None

    # ------------------------------------------------------------- kết nối tự phục hồi
    def _connection(self):
        import psycopg
        if self._conn is None or self._conn.closed or self._conn.broken:
            try:
                self._conn = psycopg.connect(self._dsn, autocommit=True, connect_timeout=5)
            except psycopg.Error as exc:
                self._conn = None
                raise _translate(exc)
        return self._conn

    def _run(self, fn):
        import psycopg
        try:
            return fn(_Ops(self._connection()))
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            self._conn = None                      # lần sau nối lại
            raise _translate(exc)

    # ------------------------------------------------------------- API (chế độ autocommit)
    def search(self, embedding, k: int = 1):
        return self._run(lambda ops: ops.search(embedding, k))

    def add_embedding(self, customer_id: int, embedding) -> int:
        return self._run(lambda ops: ops.add_embedding(customer_id, embedding))

    def count_for_customer(self, customer_id: int) -> int:
        return self._run(lambda ops: ops.count_for_customer(customer_id))

    def delete_customer(self, customer_id: int) -> int:
        return self._run(lambda ops: ops.delete_customer(customer_id))

    def customer_ids(self) -> List[int]:
        return self._run(lambda ops: [int(r[0]) for r in ops._conn.execute(
            "SELECT DISTINCT customer_id FROM face_embeddings ORDER BY 1").fetchall()])

    # ------------------------------------------------------------- khoá phân tán
    @contextmanager
    def exclusive(self) -> Iterator[_Ops]:
        """Vào vùng găng toàn hệ thống. Trong khối `with`, mọi thao tác dùng chung một transaction;
        thoát bình thường -> commit (nhả khoá), có lỗi -> rollback (embedding chưa commit biến mất)."""
        import psycopg
        try:
            conn = psycopg.connect(self._dsn, connect_timeout=5)       # autocommit=False
        except psycopg.Error as exc:
            raise _translate(exc)
        try:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (ENROLL_LOCK_KEY,))
            yield _Ops(conn)
            conn.commit()
        except psycopg.Error as exc:
            _safe_rollback(conn)
            raise _translate(exc)
        except BaseException:
            _safe_rollback(conn)
            raise
        finally:
            try:
                conn.close()
            except Exception:                      # pragma: no cover
                pass

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None


def _safe_rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:                              # pragma: no cover
        pass
