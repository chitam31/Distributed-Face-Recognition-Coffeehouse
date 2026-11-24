"""Cấu hình dùng chung cho worker, client và tools.

Mọi tham số đọc từ biến môi trường để cùng một mã nguồn chạy được trên
nhiều máy (laptop chi nhánh, máy chủ) mà không phải sửa code.
Hàm load_settings() nhận một mapping tuỳ chọn để test không phụ thuộc os.environ.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional


def _get(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name)
    return default if value is None or value == "" else value


def _get_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class Settings:
    # ---- RabbitMQ ----
    rabbit_host: str = "localhost"
    rabbit_port: int = 5672
    rabbit_user: str = "guest"
    rabbit_pass: str = "guest"
    rabbit_vhost: str = "/"
    rabbit_tls: bool = False                 # amqps
    rabbit_ca_file: Optional[str] = None     # CA để client xác thực broker khi dùng TLS
    queue_type: str = "quorum"               # quorum | classic
    max_deliveries: int = 3                  # số lần giao tối đa trước khi chuyển DLQ

    # ---- MySQL (hồ sơ khách, ảnh, đơn hàng) ----
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "coffee"
    mysql_password: str = "coffee123"
    mysql_db: str = "coffeehouse"

    # ---- PostgreSQL + pgvector (embedding khuôn mặt) ----
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "coffee"
    pg_password: str = "coffee123"
    pg_db: str = "faces"

    # ---- Lưu ảnh ----
    photo_dir: str = "./data/photos"

    # ---- Nhận diện ----
    sim_threshold: float = 0.45              # ngưỡng cosine để coi là cùng một người
    max_embeddings_per_customer: int = 5
    add_embedding_min_sim: float = 0.55      # chỉ bổ sung embedding khi nhận ra khá chắc...
    add_embedding_max_sim: float = 0.92      # ...và ảnh đủ khác ảnh cũ (tránh lưu trùng)
    multi_face_policy: str = "largest"       # largest | reject
    min_det_score: float = 0.5
    min_face_px: int = 60
    min_blur_var: float = 30.0               # phương sai Laplacian của mặt (112x112); thấp hơn = quá mờ -> NO_FACE
    model_name: str = "buffalo_l"
    use_cuda: bool = False
    ort_threads: int = 0                     # số luồng ONNX Runtime MỖI worker; 0 = tự chọn (dùng hết nhân CPU)

    # ---- Vận hành / demo ----
    demo_delay_ms: int = 0                   # làm chậm worker để quan sát hàng đợi và kill worker
    retry_delay_s: float = 1.0               # chờ trước khi trả message về hàng đợi

    @property
    def mysql_kwargs(self) -> dict:
        return dict(host=self.mysql_host, port=self.mysql_port, user=self.mysql_user,
                    password=self.mysql_password, database=self.mysql_db)

    @property
    def pg_dsn(self) -> str:
        return (f"host={self.pg_host} port={self.pg_port} dbname={self.pg_db} "
                f"user={self.pg_user} password={self.pg_password}")


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    e = os.environ if env is None else env
    d = Settings()
    return Settings(
        rabbit_host=_get(e, "RABBIT_HOST", d.rabbit_host),
        rabbit_port=int(_get(e, "RABBIT_PORT", str(d.rabbit_port))),
        rabbit_user=_get(e, "RABBIT_USER", d.rabbit_user),
        rabbit_pass=_get(e, "RABBIT_PASS", d.rabbit_pass),
        rabbit_vhost=_get(e, "RABBIT_VHOST", d.rabbit_vhost),
        rabbit_tls=_get_bool(e, "RABBIT_TLS", d.rabbit_tls),
        rabbit_ca_file=e.get("RABBIT_CA_FILE") or None,
        queue_type=_get(e, "QUEUE_TYPE", d.queue_type).lower(),
        max_deliveries=int(_get(e, "MAX_DELIVERIES", str(d.max_deliveries))),
        mysql_host=_get(e, "MYSQL_HOST", d.mysql_host),
        mysql_port=int(_get(e, "MYSQL_PORT", str(d.mysql_port))),
        mysql_user=_get(e, "MYSQL_USER", d.mysql_user),
        mysql_password=_get(e, "MYSQL_PASSWORD", d.mysql_password),
        mysql_db=_get(e, "MYSQL_DB", d.mysql_db),
        pg_host=_get(e, "PG_HOST", d.pg_host),
        pg_port=int(_get(e, "PG_PORT", str(d.pg_port))),
        pg_user=_get(e, "PG_USER", d.pg_user),
        pg_password=_get(e, "PG_PASSWORD", d.pg_password),
        pg_db=_get(e, "PG_DB", d.pg_db),
        photo_dir=_get(e, "PHOTO_DIR", d.photo_dir),
        sim_threshold=float(_get(e, "SIM_THRESHOLD", str(d.sim_threshold))),
        max_embeddings_per_customer=int(_get(e, "MAX_EMBEDDINGS", str(d.max_embeddings_per_customer))),
        add_embedding_min_sim=float(_get(e, "ADD_EMB_MIN_SIM", str(d.add_embedding_min_sim))),
        add_embedding_max_sim=float(_get(e, "ADD_EMB_MAX_SIM", str(d.add_embedding_max_sim))),
        multi_face_policy=_get(e, "MULTI_FACE_POLICY", d.multi_face_policy).lower(),
        min_det_score=float(_get(e, "MIN_DET_SCORE", str(d.min_det_score))),
        min_face_px=int(_get(e, "MIN_FACE_PX", str(d.min_face_px))),
        min_blur_var=float(_get(e, "MIN_BLUR_VAR", str(d.min_blur_var))),
        model_name=_get(e, "MODEL_NAME", d.model_name),
        use_cuda=_get_bool(e, "USE_CUDA", d.use_cuda),
        ort_threads=int(_get(e, "ORT_THREADS", str(d.ort_threads))),
        demo_delay_ms=int(_get(e, "DEMO_DELAY_MS", str(d.demo_delay_ms))),
        retry_delay_s=float(_get(e, "RETRY_DELAY_S", str(d.retry_delay_s))),
    )
