"""Create the MySQL database and all tables.

Usage (from the project folder, with the virtual environment active):
    python init_db.py

Safe to run more than once. It reads credentials from .env, never from source code.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pymysql # type: ignore

from config import get_config

SCHEMA_FILE = Path(__file__).resolve().parent / "database" / "schema.sql"
VALID_DB_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def load_statements(sql_text: str):
    """Yield executable statements from schema.sql.

    Full-line "--" comments are dropped. CREATE DATABASE and USE are skipped
    because this script creates/selects the database named in .env instead.
    """
    lines = [line for line in sql_text.splitlines() if not line.strip().startswith("--")]
    for chunk in "\n".join(lines).split(";"):
        statement = chunk.strip()
        if not statement:
            continue
        upper = statement.upper()
        if upper.startswith("CREATE DATABASE") or upper.startswith("USE "):
            continue
        yield statement


def _columns(cur, table: str) -> set[str]:
    """Return existing column names for a known, hard-coded table."""
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    return {row[0] for row in cur.fetchall()}


def apply_compat_migrations(connection) -> int:
    """Safely add columns expected by the current app to older project tables.

    Existing data stays in place. Legacy resume text/timestamps and score fields
    are copied into the current columns so previous uploads remain visible.
    """
    changes = 0
    migrations = {
        "resumes": {
            "file_size": "INT UNSIGNED NOT NULL DEFAULT 0",
            "extracted_text": "MEDIUMTEXT NULL",
            "parsed_data": "JSON NULL",
            "uploaded_at": "DATETIME NULL",
        },
        "resume_analysis": {
            "user_id": "INT UNSIGNED NULL",
            "ats_score": "DECIMAL(5,2) NULL",
            "analysis_type": "ENUM('general','job_specific') NOT NULL DEFAULT 'general'",
            "target_job_title": "VARCHAR(200) NULL",
            "general_score": "DECIMAL(5,2) NOT NULL DEFAULT 0",
            "matched_score": "DECIMAL(5,2) NULL",
            "category_scores": "JSON NULL",
            "matched_keywords": "JSON NULL",
            "missing_keywords": "JSON NULL",
            "feedback": "JSON NULL",
        },
    }
    with connection.cursor() as cur:
        for table, definitions in migrations.items():
            existing = _columns(cur, table)
            for name, definition in definitions.items():
                if name not in existing:
                    cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{name}` {definition}")
                    changes += 1
                    existing.add(name)

        resume_columns = _columns(cur, "resumes")
        if "raw_text" in resume_columns:
            cur.execute("UPDATE resumes SET extracted_text = raw_text WHERE extracted_text IS NULL AND raw_text IS NOT NULL")
        if "created_at" in resume_columns:
            cur.execute("UPDATE resumes SET uploaded_at = created_at WHERE uploaded_at IS NULL")

        analysis_columns = _columns(cur, "resume_analysis")
        if "ats_score" in analysis_columns:
            cur.execute("UPDATE resume_analysis SET general_score = ats_score WHERE general_score = 0 AND ats_score IS NOT NULL")
        if "user_id" in analysis_columns:
            cur.execute("UPDATE resume_analysis ra JOIN resumes r ON r.id = ra.resume_id SET ra.user_id = r.user_id WHERE ra.user_id IS NULL")
        if "target_role" in analysis_columns:
            cur.execute("UPDATE resume_analysis SET target_job_title = target_role WHERE target_job_title IS NULL AND target_role IS NOT NULL")
        if "match_percentage" in analysis_columns:
            cur.execute("UPDATE resume_analysis SET matched_score = match_percentage WHERE matched_score IS NULL AND match_percentage IS NOT NULL")
        if "score_breakdown" in analysis_columns:
            cur.execute("UPDATE resume_analysis SET category_scores = score_breakdown WHERE category_scores IS NULL AND score_breakdown IS NOT NULL")
    connection.commit()
    return changes


def main() -> int:
    cfg = get_config()
    db_name = cfg.MYSQL_DATABASE

    if not VALID_DB_NAME.match(db_name):
        print(f"ERROR: MYSQL_DATABASE {db_name!r} may only contain letters, digits and underscores.")
        return 1
    if not SCHEMA_FILE.exists():
        print(f"ERROR: Schema file not found: {SCHEMA_FILE}")
        return 1

    try:
        connection = pymysql.connect(
            host=cfg.MYSQL_HOST,
            port=cfg.MYSQL_PORT,
            user=cfg.MYSQL_USER,
            password=cfg.MYSQL_PASSWORD,
            charset="utf8mb4",
            connect_timeout=5,
        )
    except pymysql.MySQLError as exc:
        print("ERROR: Could not connect to MySQL. Check that the MySQL service is running")
        print("       and that MYSQL_HOST / MYSQL_USER / MYSQL_PASSWORD in .env are correct.")
        print(f"       Details: {exc}")
        return 1

    try:
        with connection.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cur.execute(f"USE `{db_name}`")
            count = 0
            for statement in load_statements(SCHEMA_FILE.read_text(encoding="utf-8")):
                cur.execute(statement)
                count += 1
        migrated = apply_compat_migrations(connection)
        connection.commit()
    except pymysql.MySQLError as exc:
        print(f"ERROR: Database setup failed: {exc}")
        return 1
    finally:
        connection.close()

    print(f"Database '{db_name}' is ready ({count} schema statements applied, {migrated} compatibility columns added).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
