import mysql.connector
from mysql.connector import Error
import os

# ─── Database Configuration ───────────────────────────────────────────────────
_DB_NAME = os.getenv('DB_NAME', 'bglow_TA')

_BASE_CONFIG = {
    'host':       os.getenv('DB_HOST', '127.0.0.1'),
    'port':       int(os.getenv('DB_PORT', 3306)),
    'user':       os.getenv('DB_USER', 'bglow'),
    'password':   os.getenv('DB_PASSWORD', 'Bglow@2026'),
    'charset':    'utf8mb4',
    'autocommit': False,
}

DB_CONFIG = {**_BASE_CONFIG, 'database': _DB_NAME}


def get_connection():
    """Return a new MySQL connection to bglow_db. Caller must close it."""
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Error as e:
        raise RuntimeError(f"Database connection failed: {e}")


def init_db():
    """
    Create the bglow_db database and all tables if they don't already exist.
    Connects without selecting a DB first so it works on a fresh MySQL.
    """
    try:
        conn = mysql.connector.connect(**_BASE_CONFIG)
        cursor = conn.cursor()

        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{_DB_NAME}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        )
        cursor.execute(f"USE `{_DB_NAME}`;")

        # ── users ──────────────────────────────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                name         VARCHAR(100)       NOT NULL,
                email        VARCHAR(191)       NOT NULL UNIQUE,
                password     VARCHAR(255)       NOT NULL,
                gender       VARCHAR(20)        DEFAULT NULL,
                age          SMALLINT UNSIGNED  DEFAULT NULL,
                skin_type    VARCHAR(50)        DEFAULT NULL,
                skin_concern VARCHAR(100)       DEFAULT NULL,
                created_at   DATETIME           DEFAULT CURRENT_TIMESTAMP,
                updated_at   DATETIME           DEFAULT CURRENT_TIMESTAMP
                                                ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # ── routine_steps ──────────────────────────────────────────────────────
        # Stores each routine step per user.
        # is_special=0 → base (shown every day)
        # is_special=1 → special (shown on specific day_index only)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS routine_steps (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                user_id      INT           NOT NULL,
                time_of_day  ENUM('morning','night') NOT NULL,
                label        VARCHAR(100)  NOT NULL,
                product      VARCHAR(200)  NOT NULL,
                brand        VARCHAR(100)  DEFAULT '',
                emoji        VARCHAR(10)   DEFAULT '💧',
                bg           VARCHAR(20)   DEFAULT '#E1F5FE',
                is_special   TINYINT(1)    DEFAULT 0,
                day_index    TINYINT       DEFAULT NULL,
                insert_after VARCHAR(100)  DEFAULT NULL,
                sort_order   INT           DEFAULT 0,
                created_at   DATETIME      DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # ── routine_logs ───────────────────────────────────────────────────────
        # One row per (user, step, date). Represents a checked step on that day.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS routine_logs (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                user_id     INT  NOT NULL,
                step_id     INT  NOT NULL,
                log_date    DATE NOT NULL,
                checked_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_log (user_id, step_id, log_date),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (step_id) REFERENCES routine_steps(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # ── routine_streak ─────────────────────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS routine_streak (
                id                  INT AUTO_INCREMENT PRIMARY KEY,
                user_id             INT  NOT NULL UNIQUE,
                current_streak      INT  DEFAULT 0,
                best_streak         INT  DEFAULT 0,
                last_completed_date DATE DEFAULT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        conn.commit()
        cursor.close()
        conn.close()
        print(f"[DB] Database `{_DB_NAME}` and tables initialised successfully.")

    except Error as e:
        raise RuntimeError(f"DB init error: {e}")
