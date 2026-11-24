-- PostgreSQL + pgvector: vector khuôn mặt (ArcFace 512 chiều, đã chuẩn hoá L2).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS face_embeddings (
    embedding_id BIGSERIAL PRIMARY KEY,
    customer_id  BIGINT      NOT NULL,   -- khoá logic trỏ sang MySQL, không có FK thật (hai CSDL khác nhau)
    embedding    vector(512) NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- Tìm láng giềng gần nhất theo cosine bằng HNSW
CREATE INDEX IF NOT EXISTS idx_face_embeddings_hnsw
    ON face_embeddings USING hnsw (embedding vector_cosine_ops);

-- Đếm / xoá embedding theo khách
CREATE INDEX IF NOT EXISTS idx_face_embeddings_customer ON face_embeddings (customer_id);

-- Truy vấn nhận diện (do worker chạy):
--   SELECT customer_id, 1 - (embedding <=> %s::vector) AS similarity
--   FROM face_embeddings ORDER BY embedding <=> %s::vector LIMIT 1;
