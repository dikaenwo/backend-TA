-- ============================================================
--  B-Glow Database Setup Script
--  Run this once to create the database and table manually
--  (The backend also runs init_db() automatically on startup)
-- ============================================================

CREATE DATABASE IF NOT EXISTS bglow_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE bglow_db;

CREATE TABLE IF NOT EXISTS users (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    name         VARCHAR(100)        NOT NULL,
    email        VARCHAR(191)        NOT NULL UNIQUE,
    password     VARCHAR(255)        NOT NULL,
    gender       VARCHAR(20)         DEFAULT NULL,
    age          SMALLINT UNSIGNED   DEFAULT NULL,
    skin_type    VARCHAR(50)         DEFAULT NULL,
    skin_concern VARCHAR(100)        DEFAULT NULL,
    created_at   DATETIME            DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME            DEFAULT CURRENT_TIMESTAMP
                                     ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
