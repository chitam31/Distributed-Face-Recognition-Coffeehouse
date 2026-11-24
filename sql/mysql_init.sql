-- MySQL 8: hồ sơ khách, ảnh, đơn hàng, nhật ký nhận diện.
-- Chạy tự động lần đầu khi container mysql khởi tạo (docker-entrypoint-initdb.d).
SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS coffeehouse CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE coffeehouse;

-- Tài khoản ứng dụng cho worker (không dùng root)
CREATE USER IF NOT EXISTS 'coffee'@'%' IDENTIFIED BY 'coffee123';
GRANT SELECT, INSERT, UPDATE, DELETE ON coffeehouse.* TO 'coffee'@'%';
FLUSH PRIVILEGES;

CREATE TABLE IF NOT EXISTS branches (
    branch_id   VARCHAR(32)  NOT NULL,
    name        VARCHAR(100) NOT NULL,
    address     VARCHAR(255) NULL,
    created_at  DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (branch_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS customers (
    customer_id     BIGINT       NOT NULL AUTO_INCREMENT,
    full_name       VARCHAR(100) NULL,
    phone           VARCHAR(20)  NULL,
    first_branch_id VARCHAR(32)  NOT NULL,
    created_at      DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    last_seen_at    DATETIME(3)  NULL,
    PRIMARY KEY (customer_id),
    CONSTRAINT fk_customers_branch FOREIGN KEY (first_branch_id) REFERENCES branches (branch_id)
) ENGINE=InnoDB;

-- Ảnh lưu thành file; DB chỉ giữ đường dẫn tương đối so với PHOTO_DIR.
CREATE TABLE IF NOT EXISTS customer_photos (
    photo_id     BIGINT       NOT NULL AUTO_INCREMENT,
    customer_id  BIGINT       NOT NULL,
    file_path    VARCHAR(255) NOT NULL,
    branch_id    VARCHAR(32)  NOT NULL,
    captured_at  DATETIME(3)  NOT NULL,
    PRIMARY KEY (photo_id),
    KEY idx_photos_customer (customer_id),
    CONSTRAINT fk_photos_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id) ON DELETE CASCADE,
    CONSTRAINT fk_photos_branch   FOREIGN KEY (branch_id)   REFERENCES branches (branch_id)
) ENGINE=InnoDB;

-- request_id UNIQUE: gửi lại cùng một lệnh tạo đơn không tạo đơn thứ hai (idempotent).
CREATE TABLE IF NOT EXISTS orders (
    order_id      BIGINT        NOT NULL AUTO_INCREMENT,
    request_id    VARCHAR(64)   NOT NULL,
    customer_id   BIGINT        NOT NULL,
    branch_id     VARCHAR(32)   NOT NULL,
    ordered_at    DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    total_amount  DECIMAL(12,2) NOT NULL DEFAULT 0,
    note          VARCHAR(255)  NULL,
    PRIMARY KEY (order_id),
    UNIQUE KEY uq_orders_request (request_id),
    KEY idx_orders_customer_time (customer_id, ordered_at DESC),   -- lấy đơn gần nhất nhanh
    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
    CONSTRAINT fk_orders_branch   FOREIGN KEY (branch_id)   REFERENCES branches (branch_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS order_items (
    item_id     BIGINT        NOT NULL AUTO_INCREMENT,
    order_id    BIGINT        NOT NULL,
    item_name   VARCHAR(100)  NOT NULL,
    quantity    INT           NOT NULL DEFAULT 1,
    unit_price  DECIMAL(10,2) NOT NULL DEFAULT 0,
    options     VARCHAR(255)  NULL,                    -- ví dụ: ít đá, 50% đường
    PRIMARY KEY (item_id),
    KEY idx_items_order (order_id),
    CONSTRAINT fk_items_order FOREIGN KEY (order_id) REFERENCES orders (order_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- Vừa phục vụ idempotency (request_id UNIQUE), vừa là dữ liệu đo đạc.
-- customer_id không có FK: nhật ký phải sống sót khi customer bị xoá do bù trừ (saga).
CREATE TABLE IF NOT EXISTS recognition_logs (
    log_id       BIGINT       NOT NULL AUTO_INCREMENT,
    request_id   VARCHAR(64)  NOT NULL,
    branch_id    VARCHAR(32)  NOT NULL,
    customer_id  BIGINT       NULL,
    status       VARCHAR(20)  NOT NULL,
    similarity   FLOAT        NULL,
    latency_ms   INT          NULL,
    worker       VARCHAR(64)  NULL,
    created_at   DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (log_id),
    UNIQUE KEY uq_logs_request (request_id),
    KEY idx_logs_created (created_at)
) ENGINE=InnoDB;

INSERT IGNORE INTO branches (branch_id, name, address) VALUES
    ('A', 'Chi nhánh A', 'Quận 1, TP.HCM'),
    ('B', 'Chi nhánh B', 'Quận 7, TP.HCM'),
    ('C', 'Chi nhánh C', 'Thủ Đức, TP.HCM');
