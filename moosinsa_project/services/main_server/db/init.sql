CREATE DATABASE IF NOT EXISTS mss_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE mss_db;

CREATE TABLE IF NOT EXISTS shoes (
    SSID        VARCHAR(36)     PRIMARY KEY COMMENT '신발 고유 ID',
    name        VARCHAR(100)    NOT NULL    COMMENT '신발명',
    brand       VARCHAR(100)    NOT NULL    COMMENT '브랜드',
    size        DECIMAL(4,1)    NOT NULL    COMMENT '사이즈',
    color       VARCHAR(50)                 COMMENT '색상',
    location    VARCHAR(50)                 COMMENT '창고 위치 (선반 코드)',
    stock       INT             DEFAULT 0   COMMENT '재고 수량',
    created_at  DATETIME        DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME        DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB;
