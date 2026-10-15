"""Central configuration. All secrets and environment-specific values come from
environment variables (loaded from .env in development); nothing sensitive is
hardcoded here."""

import os
import tempfile
from datetime import timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional in production/runtime images
    def load_dotenv(*args, **kwargs):
        return False

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"Environment variable {name} must be an integer, got {raw!r}.") from None


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"Environment variable {name} must be a number, got {raw!r}.") from None


class Config:
    # ----- Core -----
    APP_ENV = os.environ.get("APP_ENV", "development").lower()
    DEBUG = _get_bool("FLASK_DEBUG", False)
    TESTING = False
    SECRET_KEY = os.environ.get("SECRET_KEY", "")

    # ----- Sessions and cookies -----
    PERMANENT_SESSION_LIFETIME = timedelta(hours=_get_int("SESSION_LIFETIME_HOURS", 8))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _get_bool("SESSION_COOKIE_SECURE", APP_ENV == "production")
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    # ----- CSRF -----
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 7200  # seconds

    # ----- MySQL -----
    MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
    MYSQL_PORT = _get_int("MYSQL_PORT", 3306)
    MYSQL_USER = os.environ.get("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
    MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "resume_analyzer")

    # ----- Uploads (stored OUTSIDE static/ so files are never served directly) -----
    MAX_UPLOAD_MB = _get_int("MAX_UPLOAD_MB", 5)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024
    UPLOAD_FOLDER = str(BASE_DIR / "uploads")
    # extension -> MIME types browsers are expected to send for it
    ALLOWED_UPLOADS = {
        "pdf": {"application/pdf"},
        "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        "png": {"image/png"},
        "jpg": {"image/jpeg"},
        "jpeg": {"image/jpeg"},
    }

    # ----- OCR -----
    TESSERACT_CMD = os.environ.get("TESSERACT_CMD", "")

    # ----- Optional AI integration -----
    AI_API_KEY = os.environ.get("AI_API_KEY", "")
    AI_API_URL = os.environ.get("AI_API_URL", "https://api.anthropic.com/v1/messages")
    AI_MODEL = os.environ.get("AI_MODEL", "claude-sonnet-5-5")
    AI_ENABLED = bool(AI_API_KEY)

    # ----- Optional authorized job-data provider -----
    JOB_API_PROVIDER = os.environ.get("JOB_API_PROVIDER", "")
    JOB_API_KEY = os.environ.get("JOB_API_KEY", "")
    JOB_API_BASE_URL = os.environ.get("JOB_API_BASE_URL", "")

    # ----- Rate limiting -----
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_DEFAULT = "200 per hour"
    RATELIMIT_HEADERS_ENABLED = True
    AUTH_RATE_LIMIT = os.environ.get("AUTH_RATE_LIMIT", "10 per minute")
    ANALYSIS_RATE_LIMIT = os.environ.get("ANALYSIS_RATE_LIMIT", "20 per hour")

    # ----- Authentication policy -----
    MIN_PASSWORD_LENGTH = 8

    # ----- ATS weights: configurable illustrative defaults, NOT an official formula.
    # The scorer normalizes these so they always sum to 100.
    ATS_WEIGHTS = {
        "skills": _get_float("ATS_WEIGHT_SKILLS", 35),
        "keywords": _get_float("ATS_WEIGHT_KEYWORDS", 25),
        "experience": _get_float("ATS_WEIGHT_EXPERIENCE", 20),
        "education": _get_float("ATS_WEIGHT_EDUCATION", 10),
        "structure": _get_float("ATS_WEIGHT_STRUCTURE", 10),
    }

    @classmethod
    def validate(cls) -> None:
        """Raise a readable error if the configuration is unusable."""
        problems = []
        if not cls.SECRET_KEY or cls.SECRET_KEY.startswith("change-me"):
            problems.append(
                "SECRET_KEY is missing or still the placeholder. Generate one with: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if cls.MAX_UPLOAD_MB <= 0:
            problems.append("MAX_UPLOAD_MB must be greater than 0.")
        if any(weight < 0 for weight in cls.ATS_WEIGHTS.values()) or sum(cls.ATS_WEIGHTS.values()) <= 0:
            problems.append("ATS_WEIGHT_* values must be non-negative and not all zero.")
        if cls.APP_ENV == "production" and not cls.SESSION_COOKIE_SECURE:
            problems.append("SESSION_COOKIE_SECURE must be enabled in production (HTTPS).")
        if problems:
            raise RuntimeError("Configuration problems:\n - " + "\n - ".join(problems))


class DevelopmentConfig(Config):
    pass


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True


class TestingConfig(Config):
    TESTING = True
    DEBUG = False
    SECRET_KEY = "test-secret-key-not-for-production"
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    RATELIMIT_ENABLED = False
    MYSQL_DATABASE = os.environ.get("MYSQL_TEST_DATABASE", "resume_analyzer_test")
    UPLOAD_FOLDER = str(Path(tempfile.gettempdir()) / "resume_analyzer_test_uploads")


def get_config():
    """Pick the config class from APP_ENV (development, production, or testing)."""
    env = os.environ.get("APP_ENV", "development").lower()
    return {"production": ProductionConfig, "testing": TestingConfig}.get(env, DevelopmentConfig)