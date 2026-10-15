"""Flask application factory and entry point."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from importlib import import_module
from pathlib import Path

from flask import Flask, render_template, request
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

from config import get_config
from database import db
from database.db import DatabaseError
from extensions import csrf, limiter, login_manager


class Module(ABC):
    """Abstract contract for app modules that register Flask blueprints."""

    @property
    @abstractmethod
    def module_name(self) -> str:
        """The Python module that exposes the blueprint."""

    @property
    @abstractmethod
    def blueprint_name(self) -> str:
        """The blueprint object exported by the module."""

    @property
    @abstractmethod
    def url_prefix(self) -> str | None:
        """Optional URL prefix used when registering the blueprint."""

    @abstractmethod
    def get_blueprint(self):
        """Return the blueprint object defined by this module."""

    @abstractmethod
    def register(self, app: Flask) -> None:
        """Register this module's routes/templates with the application."""


class BlueprintModule(Module):
    """Concrete module that loads and registers a Flask blueprint."""

    def __init__(self, module_name: str, blueprint_name: str, url_prefix: str | None = None):
        self._module_name = module_name
        self._blueprint_name = blueprint_name
        self._url_prefix = url_prefix

    @property
    def module_name(self) -> str:
        return self._module_name

    @property
    def blueprint_name(self) -> str:
        return self._blueprint_name

    @property
    def url_prefix(self) -> str | None:
        return self._url_prefix

    def get_blueprint(self):
        module = import_module(self.module_name)
        return getattr(module, self.blueprint_name)

    def register(self, app: Flask) -> None:
        blueprint = self.get_blueprint()
        if self.url_prefix is None:
            app.register_blueprint(blueprint)
        else:
            app.register_blueprint(blueprint, url_prefix=self.url_prefix)


def create_app(config_class=None) -> Flask:
    app = Flask(__name__)

    config_class = config_class or get_config()
    config_class.validate()
    app.config.from_object(config_class)

    logging.basicConfig(level=logging.DEBUG if app.debug else logging.INFO)

    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    # Extensions
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to continue."
    login_manager.login_message_category = "warning"
    login_manager.session_protection = "strong"
    db.init_app(app)

    _register_blueprints(app)
    _register_error_handlers(app)
    _register_hooks(app)
    return app


def _register_blueprints(app: Flask) -> None:
    # Imported here (not at module top) to avoid circular imports.
    # Importing auth.routes also imports auth.models, which registers the
    # Flask-Login user_loader.
    from analyzer.improve_routes import improve_bp
    from analyzer.routes import analyzer_bp
    from applications.routes import applications_bp
    from auth.routes import auth_bp
    from jobs.routes import jobs_bp
    from main.routes import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(analyzer_bp, url_prefix="/resume")
    app.register_blueprint(improve_bp, url_prefix="/resume")  # Part 5: same prefix, different routes
    app.register_blueprint(jobs_bp, url_prefix="/jobs")
    app.register_blueprint(applications_bp, url_prefix="/applications")


def _error_page(status: int, title: str, message: str):
    template = "404.html" if status == 404 else "500.html"
    return render_template(template, error_title=title, error_message=message), status


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_exc):
        return _error_page(404, "Page not found", "The page you are looking for does not exist.")

    @app.errorhandler(413)
    def too_large(_exc):
        limit = app.config["MAX_UPLOAD_MB"]
        return _error_page(413, "File too large", f"Uploads are limited to {limit} MB.")

    @app.errorhandler(429)
    def rate_limited(_exc):
        return _error_page(429, "Too many requests", "Please wait a moment and try again.")

    @app.errorhandler(CSRFError)
    def csrf_failed(_exc):
        return _error_page(400, "Session expired", "Your form session expired. Please go back, refresh the page and try again.")

    @app.errorhandler(DatabaseError)
    def database_failed(exc):
        cause = exc.__cause__
        code = cause.args[0] if getattr(cause, "args", None) else None
        if code == 1045:
            message = "MySQL rejected the configured login. Update MYSQL_USER and MYSQL_PASSWORD in your .env file, restart the app, then try again."
        elif code == 1049:
            message = "The resume_analyzer database does not exist yet. From the project folder, run: python init_db.py"
        elif code in {2003, 2006, 2013}:
            message = "The MySQL server is not reachable. Start the MySQL service, check MYSQL_HOST and MYSQL_PORT in .env, then try again."
        else:
            message = "The database is unavailable. Check the MySQL settings in .env and run python init_db.py if the database has not been created."
        return _error_page(503, "Database setup needed", message)

    @app.errorhandler(Exception)
    def unexpected(exc):
        if isinstance(exc, HTTPException):
            return exc  # let Flask handle other HTTP errors normally
        app.logger.exception("Unhandled error on %s %s", request.method, request.path)
        return _error_page(500, "Something went wrong", "An unexpected error occurred. Please try again.")


def _register_hooks(app: Flask) -> None:
    @app.context_processor
    def inject_globals():
        return {"current_year": datetime.now().year, "ai_enabled": app.config["AI_ENABLED"]}

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        if app.config["SESSION_COOKIE_SECURE"] and request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


if __name__ == "__main__":
    application = create_app()
    application.run(host="127.0.0.1", port=5000, debug=application.config["DEBUG"])

BASE_DIR = Path(__file__).resolve().parent

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"
))
