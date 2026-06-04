import csv
import base64
import io
import json
import logging
import logging.handlers
import os
import random
import secrets
import sys
import time
import uuid
import hashlib
from contextlib import contextmanager
from pathlib import Path
from datetime import date, datetime, time as dtime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo
from werkzeug.exceptions import HTTPException


def load_env_file(path: str | Path = ".env") -> None:
    """Load simple KEY=value pairs before app services need environment values."""
    env_path = Path(path)
    if not env_path.is_absolute():
        env_path = Path(__file__).resolve().parent / env_path
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file()

import cv2
import firebase_admin
import numpy as np
import psycopg2
import psycopg2.extras
import requests
from firebase_admin import auth as fb_auth
from firebase_admin import credentials, db
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_mail import Mail, Message
from flask_socketio import SocketIO, emit, join_room, leave_room
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from detection.face_matching import detect_faces, get_ear_from_face, yaw_ratio_from_face
from services.face_service import compare_with_database, generate_embedding
from services.response_service import fail, ok
from utils.configuration import load_yaml
from utils.crypto import decrypt_embedding, encrypt_embedding

sys.modules.setdefault("app", sys.modules[__name__])

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _time_to_minutes(t: dtime) -> int:
    return t.hour * 60 + t.minute


def _minutes_to_time(m: int) -> dtime:
    m = max(0, min(m, 23 * 60 + 59))
    return dtime(m // 60, m % 60, 0)


def _add_minutes(t: dtime, mins: int) -> dtime:
    return _minutes_to_time(_time_to_minutes(t) + mins)


# ============================================================
# GENERAL HELPERS
# Shared redirects, role guards, formatting helpers.
# ============================================================
def redirect_with_msg(path: str, msg: str):
    sep = "&" if "?" in path else "?"
    return redirect(f"{path}{sep}msg={quote(msg)}")


try:
    APP_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "Asia/Manila"))
except Exception:
    APP_TZ = timezone(timedelta(hours=8))


def app_now() -> datetime:
    """Return app-local wall time for class/session windows."""
    return datetime.now(APP_TZ).replace(tzinfo=None)


def app_today() -> date:
    return app_now().date()


def student_required():
    if not session.get("logged_in") or session.get("role") != "student":
        return redirect_with_msg("/login", "Please log in as a student.")
    return None


def instructor_required():
    if not session.get("logged_in") or session.get("role") != "instructor":
        return redirect_with_msg("/login", "Instructor access only. Please log in.")
    return None


def admin_required():
    if not session.get("logged_in") or session.get("role") != "admin":
        return redirect_with_msg("/login", "Admin access only. Please log in.")
    return None


def _stud_home_url():
    return url_for("stud_class_home")


def _instr_home_url():
    return url_for("instructor_class_home")


def _admin_home_url():
    return url_for("admin_dashboard")


def _safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def _fmt_time_hhmm(v):
    if v is None:
        return ""
    try:
        return v.strftime("%H:%M")
    except Exception:
        return str(v)[:5]


# ============================================================
# SECURITY LOGGING - MASKED IDENTIFIERS
# Mask UIDs and emails to avoid exposing sensitive data in logs
# ============================================================
def _mask_uid(uid: str) -> str:
    """Mask UID to first 8 chars + ***, e.g., 'abc12345***'"""
    if not uid or len(uid) < 8:
        return "***"
    return uid[:8] + "***"


def _mask_email(email: str) -> str:
    """Mask email to show domain only, e.g., 'user...@example.com'"""
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    # Show first char + dots + @ + domain
    masked_local = local[0] + "..." if len(local) > 0 else "..."
    return f"{masked_local}@{domain}"


def _mask_identifier(identifier: str) -> str:
    """Generic masking - first 4 chars + ***"""
    if not identifier or len(identifier) < 4:
        return "***"
    return identifier[:4] + "***"


# ============================================================
# PASSWORD STRENGTH VALIDATION
# ============================================================
import re
try:
    from zxcvbn import zxcvbn
except ImportError:
    zxcvbn = None

def validate_password_strength(password: str) -> tuple:
    """
    Validate password meets Firebase authentication policy requirements:
    - Minimum 8 characters
    - At least 1 uppercase letter
    - At least 1 number
    - At least 1 special character
    
    Returns: (is_valid: bool, error_message: str, strength_score: int)
    """
    errors = []
    
    # Check minimum length
    if len(password) < 8:
        errors.append("Password must be at least 8 characters")
    
    # Check for uppercase letter
    if not re.search(r'[A-Z]', password):
        errors.append("Password must contain at least 1 uppercase letter")
    
    # Check for number
    if not re.search(r'[0-9]', password):
        errors.append("Password must contain at least 1 number")
    
    # Check for special character
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};:\'",.<>?/\\|`~]', password):
        errors.append("Password must contain at least 1 special character (!@#$%^&*)")
    
    # Calculate strength score using zxcvbn if available
    strength_score = 0
    if zxcvbn is not None:
        try:
            result = zxcvbn(password)
            strength_score = result.get('score', 0)  # 0-4 scale
        except Exception as e:
            logger = logging.getLogger("classiface")
            logger.debug(f"zxcvbn scoring error: {type(e).__name__}")
    
    is_valid = len(errors) == 0
    error_message = "; ".join(errors) if errors else ""
    
    return is_valid, error_message, strength_score


# ============================================================
# APPLICATION PATHS
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# CONFIGURATION
# ============================================================
config_path = os.path.join(BASE_DIR, "configs", "database.yaml")
if not os.path.isfile(config_path):
    config_path = os.path.join(BASE_DIR, "configs", "database.example.yaml")
config = load_yaml(config_path) or {}

# ============================================================
# EARLY LOGGING SETUP (before Firebase and app initialization)
# ============================================================
def _setup_early_logging():
    """Setup basic logging before app/Flask initialization."""
    logs_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    
    # Early logger for startup processes
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Simple formatter for early startup
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler
    log_file = os.path.join(logs_dir, "app.log")
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)
    
    return root_logger

_early_logger = _setup_early_logging()

def _resolve_service_account_path(firebase_config: dict) -> str:
    service_account_path = firebase_config.get("pathToServiceAccount") or ""
    if not os.path.isabs(service_account_path):
        service_account_path = os.path.join(BASE_DIR, service_account_path)
    return service_account_path


def init_firebase(firebase_config: dict):
    service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if service_account_json:
        try:
            service_account_json = service_account_json.strip()
            if len(service_account_json) >= 2 and service_account_json[0] == service_account_json[-1] and service_account_json[0] in ("'", '"'):
                service_account_json = service_account_json[1:-1].strip()
            cred = credentials.Certificate(json.loads(service_account_json))
        except json.JSONDecodeError as exc:
            raise RuntimeError("FIREBASE_SERVICE_ACCOUNT_JSON is not valid JSON") from exc

        if not firebase_admin._apps:
            firebase_admin.initialize_app(
                cred,
                {"databaseURL": os.environ.get("FIREBASE_DATABASE_URL", firebase_config["databaseURL"])},
            )
            _early_logger.info("Firebase Admin SDK initialized from environment")
        else:
            _early_logger.info("Firebase Admin SDK already initialized")
        return

    service_account_path = _resolve_service_account_path(firebase_config)

    _early_logger.info(f"Firebase service account path: {service_account_path}")
    if not os.path.isfile(service_account_path):
        _early_logger.error(f"Service account file not found at {service_account_path}")
        raise FileNotFoundError(f"Service account key file not found: {service_account_path}")

    cred = credentials.Certificate(service_account_path)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(
            cred,
            {"databaseURL": firebase_config["databaseURL"]},
        )
        _early_logger.info("Firebase Admin SDK initialized successfully")
    else:
        _early_logger.info("Firebase Admin SDK already initialized")


init_firebase(config["firebase"])


# ============================================================
# POSTGRESQL CONNECTION POOLING
# ============================================================
# POSTGRESQL CONNECTION
# ============================================================
_PG_POOL = None
_PG_POOL_KEY = None


def _pg_pool_settings() -> tuple[int, int]:
    minconn = max(1, int(os.environ.get("PG_POOL_MIN", "1")))
    maxconn = max(minconn, int(os.environ.get("PG_POOL_MAX", "8")))
    return minconn, maxconn


def _pg_connection_args():
    common_kwargs = {
        "cursor_factory": RealDictCursor,
        "connect_timeout": int(os.environ.get("PG_CONNECT_TIMEOUT", "10")),
        "keepalives": 1,
        "keepalives_idle": int(os.environ.get("PG_KEEPALIVES_IDLE", "30")),
        "keepalives_interval": int(os.environ.get("PG_KEEPALIVES_INTERVAL", "10")),
        "keepalives_count": int(os.environ.get("PG_KEEPALIVES_COUNT", "5")),
    }

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return (database_url,), common_kwargs, ("url", database_url)

    pg = config.get("postgres", {})
    host = os.environ.get("PGHOST", pg.get("host", "localhost"))
    port = int(os.environ.get("PGPORT", pg.get("port", 5432)))
    dbname = os.environ.get("PGDATABASE", pg.get("database"))
    user = os.environ.get("PGUSER", pg.get("user"))
    password = os.environ.get("PGPASSWORD", pg.get("password"))

    if not dbname or not user or not password:
        raise RuntimeError(
            "PostgreSQL config missing. Set configs/database.yaml postgres section "
            "or set env vars PGDATABASE/PGUSER/PGPASSWORD."
        )

    kwargs = {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": user,
        "password": password,
        **common_kwargs,
    }
    return (), kwargs, ("params", host, port, dbname, user)


def _get_pg_pool():
    global _PG_POOL, _PG_POOL_KEY

    args, kwargs, pool_key = _pg_connection_args()
    if _PG_POOL is None or _PG_POOL_KEY != pool_key:
        if _PG_POOL is not None:
            _PG_POOL.closeall()

        minconn, maxconn = _pg_pool_settings()
        _PG_POOL = ThreadedConnectionPool(minconn, maxconn, *args, **kwargs)
        _PG_POOL_KEY = pool_key
        _early_logger.info("PostgreSQL connection pool initialized: min=%s max=%s", minconn, maxconn)

    return _PG_POOL


@contextmanager
def pg_conn():
    """Borrow a pooled PostgreSQL connection and return it after the request work."""
    pool = _get_pg_pool()
    conn = None
    discard = False

    try:
        conn = pool.getconn()
        if conn.closed:
            pool.putconn(conn, close=True)
            conn = pool.getconn()

        yield conn
        conn.commit()
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                discard = True
        raise
    finally:
        if conn is not None:
            pool.putconn(conn, close=discard or bool(conn.closed))


# ============================================================
# FLASK APPLICATION SETUP
# ============================================================
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "template"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.secret_key = os.environ.get("FLASK_SECRET")
if not app.secret_key:
    raise RuntimeError("Set FLASK_SECRET before starting the application.")

_early_logger.info(
    "Production env check: DATABASE_URL=%s FIREBASE_SERVICE_ACCOUNT_JSON=%s FIREBASE_WEB_API_KEY=%s FLASK_SECRET=%s",
    "set" if os.environ.get("DATABASE_URL") else "missing",
    "set" if os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON") else "missing",
    "set" if os.environ.get("FIREBASE_WEB_API_KEY") else "fallback",
    "set" if os.environ.get("FLASK_SECRET") else "missing",
)


def firebase_web_api_key() -> str:
    key = os.environ.get("FIREBASE_WEB_API_KEY", "AIzaSyARe4SArCWGeAUeq8738oqv-PVAa6te3oU").strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in ("'", '"'):
        key = key[1:-1].strip()
    if not key:
        raise RuntimeError("Set FIREBASE_WEB_API_KEY before using Firebase Web Auth.")
    return key

# ============================================================
# EMAIL SETUP
# ============================================================
def configure_mail(flask_app: Flask, app_config: dict):
    mail_cfg = app_config.get("mail", {})
    flask_app.config["MAIL_SERVER"] = mail_cfg.get("server", "smtp.gmail.com")
    flask_app.config["MAIL_PORT"] = int(mail_cfg.get("port", 587))
    flask_app.config["MAIL_USE_TLS"] = bool(mail_cfg.get("use_tls", True))
    flask_app.config["MAIL_USERNAME"] = mail_cfg.get("username")
    flask_app.config["MAIL_PASSWORD"] = mail_cfg.get("password")
    flask_app.config["MAIL_DEFAULT_SENDER"] = (
        mail_cfg.get("default_sender") or mail_cfg.get("username")
    )


configure_mail(app, config)

mail = Mail(app)

socketio = SocketIO(
    app,
    # Session and cookie configuration for secure WebSocket communication
    manage_session=False,  # Let Flask manage sessions
    cookie='io_session',   # Cookie name for Socket.IO session tracking
    cors_allowed_origins='*',
    async_mode='threading',
    # Security parameters for Socket.IO cookies
    engineio_logger=False,
    logger=False,
)

app.config.update(
    WTF_CSRF_ENABLED=True,
    SESSION_COOKIE_HTTPONLY=True,  # Prevents JavaScript access to session cookie
    SESSION_COOKIE_SAMESITE="Strict",  # Only send cookie in same-site requests (prevents CSRF)
    SESSION_COOKIE_SECURE=True,  # Only send cookie over HTTPS (requires HTTPS in production)
    SESSION_COOKIE_NAME='classiface_session',  # Explicit session cookie name
    PREFERRED_URL_SCHEME='https',  # Use HTTPS for url_for() and redirects
)


# ============================================================
# STRUCTURED LOGGING CONFIGURATION
# ============================================================
def _configure_logging():
    """Configure structured logging with appropriate log levels and file rotation."""
    # Ensure logs directory exists
    logs_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    
    # Production mode: log WARNING and above; Development: log INFO and above
    is_production = os.environ.get("FLASK_ENV", "development") == "production"
    log_level = logging.WARNING if is_production else logging.INFO
    
    # Create logger
    logger = logging.getLogger("classiface")
    logger.setLevel(log_level)
    
    # Remove any existing handlers
    logger.handlers.clear()
    
    # Formatter for structured logs
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler with rotation (max 10MB per file, keep 5 backups)
    log_file = os.path.join(logs_dir, "app.log")
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Console handler for development (less verbose in production)
    if not is_production:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # Configure Flask's logger
    app.logger.setLevel(log_level)
    app.logger.handlers.clear()
    app.logger.addHandler(file_handler)
    if not is_production:
        app.logger.addHandler(console_handler)
    
    return logger


# Initialize structured logging
logger = _configure_logging()
logger.info("=" * 80)
logger.info("ClassiFace Application Starting")
logger.info(f"Environment: {os.environ.get('FLASK_ENV', 'development')}")
logger.info("=" * 80)


# ============================================================
# API TIMING LOGGING
# Measures request response time for GET, POST, PATCH, and DELETE.
# This is observability only and does not affect face recognition logic.
# ============================================================
# ============================================================
# HTTPS ENFORCEMENT
# Redirect HTTP requests to HTTPS in production mode
# ============================================================
@app.before_request
def enforce_https():
    """Redirect HTTP to HTTPS in production environment."""
    # Skip HTTPS enforcement in development (localhost/127.0.0.1)
    if request.remote_addr in ('127.0.0.1', 'localhost'):
        return
    
    # Check if request came through HTTP (not HTTPS)
    # X-Forwarded-Proto is set by reverse proxies (nginx, Apache)
    if os.environ.get('FLASK_ENV') == 'production':
        # Trust the X-Forwarded-Proto header from reverse proxy
        scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
        if scheme == 'http':
            url = request.url.replace('http://', 'https://', 1)
            logger.info(f"HTTPS redirect from {request.remote_addr}: {request.path}")
            return redirect(url, code=301)


@app.before_request
def start_api_timer():
    request._api_start_time = time.perf_counter()


# ============================================================
# REQUEST SIZE VALIDATION
# Prevents large payloads from overwhelming the server
# ============================================================
@app.before_request
def validate_request_size():
    """Reject requests larger than 10000 bytes to prevent resource exhaustion."""
    max_size = 10000  # 10KB limit
    if request.content_length and request.content_length > max_size:
        app.logger.warning(f"Request rejected: size {request.content_length} exceeds {max_size} bytes")
        return jsonify({'error': 'Request too large'}), 400


@app.after_request
def log_api_timing(response):
    try:
        if request.method in ("GET", "POST", "PATCH", "DELETE"):
            start_time = getattr(request, "_api_start_time", None)
            if start_time is not None:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                status_text = "OK" if response.status_code < 400 else "ERROR"
                print(
                    f"[API-TIMING] {request.method} {request.path} -> "
                    f"{elapsed_ms:.2f}ms | Status:{response.status_code} | {status_text}",
                    flush=True,
                )
    except Exception as e:
        print(f"[API-TIMING] logging error: {e}", flush=True)

    return response


# ============================================================
# SECURITY HEADERS
# Adds security headers to all responses to protect against
# common web vulnerabilities and attacks.
# ============================================================
@app.after_request
def set_headers(response):
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.socket.io https://www.gstatic.com https://www.googleapis.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https:; "
        "media-src 'self' blob:; "
        "connect-src 'self' https://cdn.jsdelivr.net https://cdn.socket.io https://www.googleapis.com https://www.gstatic.com https://identitytoolkit.googleapis.com https://firebaseappcheck.googleapis.com https://*.firebaseio.com https://*.firebaseapp.com; "
        "font-src 'self' https://cdn.jsdelivr.net data:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "upgrade-insecure-requests; "  # Upgrade HTTP requests to HTTPS
    )
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    # HSTS header: ONLY enable after SSL certificate is properly configured
    # max-age=31536000 is 1 year; includeSubDomains applies policy to all subdomains; preload enables HSTS preload list
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(self), microphone=(), geolocation=(self)'
    response.headers['Server'] = 'ClassiFace'
    return response


# ============================================================
# TEMPLATE FILTERS
# ============================================================
# Custom Jinja2 filter to format time to 12-hour format with AM/PM
def format_time_12h(time_obj):
    if not time_obj:
        return "—"
    if isinstance(time_obj, str):
        try:
            time_obj = datetime.strptime(time_obj, "%H:%M:%S").time()
        except Exception:
            try:
                time_obj = datetime.strptime(time_obj, "%H:%M").time()
            except Exception:
                return str(time_obj)

    if isinstance(time_obj, dtime):
        hour = time_obj.hour
        minute = time_obj.minute
        ampm = "AM" if hour < 12 else "PM"
        hour_12 = hour % 12
        if hour_12 == 0:
            hour_12 = 12
        return f"{hour_12}:{minute:02d} {ampm}"

    return str(time_obj)


# Custom Jinja2 filter to format date to MM/DD/YYYY format
def format_date_us(date_obj):
    if not date_obj:
        return "—"
    if isinstance(date_obj, str):
        try:
            date_obj = datetime.strptime(date_obj, "%Y-%m-%d").date()
        except Exception:
            return str(date_obj)

    if isinstance(date_obj, date):
        return f"{date_obj.month:02d}/{date_obj.day:02d}/{date_obj.year}"

    return str(date_obj)


def register_template_filters(flask_app: Flask):
    flask_app.jinja_env.filters["format_time_12h"] = format_time_12h
    flask_app.jinja_env.filters["format_date_us"] = format_date_us


register_template_filters(app)


@app.errorhandler(413)
def handle_request_too_large(err):
    app.logger.warning("Request too large on %s", request.path)
    if request.path in ("/capture", "/quiz_capture"):
        return redirect_with_msg(
            "/camera?mode=quiz" if request.path == "/quiz_capture" else "/camera?mode=register",
            "Camera image was too large. The page now compresses captures; please reload and try again.",
        )
    if request.path.startswith("/api/"):
        return fail("Request is too large. Please retry with a smaller upload.", 413)
    return "Request is too large. Please go back and try again.", 413

RECOG_FOLDER = os.path.join(BASE_DIR, "static", "recognized")
os.makedirs(RECOG_FOLDER, exist_ok=True)

# CHANGED: track per-attempt blackout state for WebSocket monitoring
ATTEMPT_BLACKOUT_STATE = {}

# CHANGED: backend grace counters for WebSocket continuous monitoring
ATTEMPT_MISMATCH_COUNT = {}
ATTEMPT_NO_FACE_COUNT = {}
ATTEMPT_MULTI_FACE_COUNT = {}
MISMATCH_GRACE_COUNT = 6  # CHANGED: tolerant for brief natural head movement/writing
# CHANGED: staged no-face handling so warning and pause are not logged at the same time
NO_FACE_WARNING_COUNT = 999  # CHANGED: disable no-face warning; looking down/writing is normal
NO_FACE_PAUSE_COUNT = 8      # CHANGED: prolonged absence only
NO_FACE_GRACE_COUNT = NO_FACE_WARNING_COUNT  # CHANGED: kept for backward compatibility with older references
MULTI_FACE_GRACE_COUNT = 2


# ============================================================
# HUMAN-READABLE VIOLATION LABELS
# Shared labels for admin/instructor monitoring displays.
# This only changes display text; it does not change detection logic.
# ============================================================
VIOLATION_LABELS = {
    "tab_left": "Student Left the Quiz Tab",
    "tab_returned": "Student Returned to Quiz Tab",
    "face_mismatch": "Face Mismatch Detected",
    "no_face_pause": "No Face Detected for Too Long",
    "multiple_faces_pause": "Multiple Faces Pause",
    "re_verify_failed": "Re-verification Failed",
    "face_out_of_frame": "Face Out of Camera Frame",
    "looking_away": "Looking Away from Screen",
    "excessive_movement": "Excessive Movement Detected",
}


def human_violation_label(violation_type: str) -> str:
    key = str(violation_type or "").strip()
    return VIOLATION_LABELS.get(key, key.replace("_", " ").title())

# ============================================================
# MODULE: QUIZ MONITORING / MOTION-CAPTURED ALERT SYSTEM
# TODO MOVE TO: modules/monitoring/monitoring.service.py
# ============================================================
# Privacy-safe, tolerance-based motion monitoring.
# IMPORTANT:
# - Does NOT save webcam images or video.
# - Does NOT change facial-recognition distance, cosine, MAX_DISTANCE,
#   CONFIDENCE_THRESHOLD, or 85% confidence matching logic.
# - Uses optional frontend metadata only: face_box, frame_width, frame_height, yaw_ratio.
# - If frontend does not send these optional fields, existing functionality continues unchanged.
ATTEMPT_MOTION_STATE = {}

# Human-friendly thresholds. These are intentionally tolerant so normal exam behaviour
# such as reading, thinking, adjusting posture, or briefly looking away is not punished.
MOTION_CENTER_OFFSET_LIMIT = 0.35          # face centre can move 35% away from frame centre
MOTION_YAW_LOOK_AWAY_LIMIT = 0.22          # tolerate small/normal head movement
MOTION_EXCESSIVE_DELTA_LIMIT = 0.28        # large frame-to-frame movement only
MOTION_WARNING_COUNT = 2                   # warn only after repeated/prolonged event
MOTION_PAUSE_COUNT = 4                     # blackout only after sustained non-compliance
MOTION_EXCESSIVE_WARNING_COUNT = 4         # movement needs more repeated samples
MOTION_EXCESSIVE_PAUSE_COUNT = 6
# CHANGED: Looking down to write/read/solve on paper is normal exam behaviour.
# It is detected only to be ignored, not warned/logged/paused.
LOOK_DOWN_BOTTOM_RATIO = 0.82
LOOK_DOWN_SIDE_TOLERANCE = 0.28


def _normalise_face_box(face_box):
    """
    Accepts face_box from frontend as either:
      - [x, y, w, h]
      - {x, y, w, h} / {left, top, width, height}
    Returns (x, y, w, h) as floats, or None when unavailable.
    """
    try:
        if isinstance(face_box, (list, tuple)) and len(face_box) >= 4:
            return tuple(float(v) for v in face_box[:4])

        if isinstance(face_box, dict):
            x = face_box.get("x", face_box.get("left"))
            y = face_box.get("y", face_box.get("top"))
            w = face_box.get("w", face_box.get("width"))
            h = face_box.get("h", face_box.get("height"))
            if x is not None and y is not None and w is not None and h is not None:
                return float(x), float(y), float(w), float(h)
    except Exception:
        return None

    return None


def detect_tolerant_motion_event(attempt_id: str, payload: dict):
    """
    Detects motion-related events without affecting face-recognition matching.

    Returns:
      None when motion data is unavailable or behaviour is within tolerance.
      dict with {action, violation_type, details} when warning/blackout is needed.

    action:
      - warning: notify only; quiz should continue
      - blackout_on: pause only after repeated/prolonged suspicious motion
    """
    attempt_key = str(attempt_id or "").strip()
    if not attempt_key:
        return None

    payload = payload or {}
    face_box = _normalise_face_box(payload.get("face_box") or payload.get("faceBox"))

    # Optional metadata. If not sent by frontend, we do nothing to preserve current behaviour.
    try:
        frame_w = float(payload.get("frame_width") or payload.get("frameWidth") or 0)
        frame_h = float(payload.get("frame_height") or payload.get("frameHeight") or 0)
    except Exception:
        frame_w, frame_h = 0.0, 0.0

    if not face_box or frame_w <= 0 or frame_h <= 0:
        return None

    try:
        yaw_ratio = payload.get("yaw_ratio", payload.get("yawRatio"))
        yaw_ratio = float(yaw_ratio) if yaw_ratio is not None else None
    except Exception:
        yaw_ratio = None

    x, y, w, h = face_box
    if w <= 0 or h <= 0:
        return None

    center_x = (x + (w / 2.0)) / frame_w
    center_y = (y + (h / 2.0)) / frame_h
    face_bottom_ratio = (y + h) / frame_h

    # CHANGED: Looking down is normal during exams, especially when writing or calculating.
    # If the face is still roughly centred horizontally and only moves downward, ignore it.
    is_looking_down_like = (
        face_bottom_ratio >= LOOK_DOWN_BOTTOM_RATIO
        and abs(center_x - 0.5) <= LOOK_DOWN_SIDE_TOLERANCE
    )

    state = ATTEMPT_MOTION_STATE.get(attempt_key, {
        "last_x": center_x,
        "last_y": center_y,
        "out_frame_count": 0,
        "look_away_count": 0,
        "excessive_move_count": 0,
    })

    if is_looking_down_like:
        # CHANGED: Allow looking down without warning, violation, or blackout.
        state["out_frame_count"] = 0
        state["look_away_count"] = 0
        state["excessive_move_count"] = 0
        state["last_x"] = center_x
        state["last_y"] = center_y
        ATTEMPT_MOTION_STATE[attempt_key] = state
        return None

    out_of_frame = (
        abs(center_x - 0.5) > MOTION_CENTER_OFFSET_LIMIT
        or abs(center_y - 0.5) > MOTION_CENTER_OFFSET_LIMIT
    )
    state["out_frame_count"] = state.get("out_frame_count", 0) + 1 if out_of_frame else 0

    looking_away = yaw_ratio is not None and abs(yaw_ratio) > MOTION_YAW_LOOK_AWAY_LIMIT
    state["look_away_count"] = state.get("look_away_count", 0) + 1 if looking_away else 0

    movement_delta = abs(center_x - float(state.get("last_x", center_x))) + abs(center_y - float(state.get("last_y", center_y)))
    excessive_movement = movement_delta > MOTION_EXCESSIVE_DELTA_LIMIT
    state["excessive_move_count"] = state.get("excessive_move_count", 0) + 1 if excessive_movement else 0

    state["last_x"] = center_x
    state["last_y"] = center_y
    ATTEMPT_MOTION_STATE[attempt_key] = state

    checks = [
        ("face_out_of_frame", state["out_frame_count"], MOTION_WARNING_COUNT, MOTION_PAUSE_COUNT),
        ("looking_away", state["look_away_count"], MOTION_WARNING_COUNT, MOTION_PAUSE_COUNT),
        ("excessive_movement", state["excessive_move_count"], MOTION_EXCESSIVE_WARNING_COUNT, MOTION_EXCESSIVE_PAUSE_COUNT),
    ]

    for violation_type, count, warning_count, pause_count in checks:
        if count >= pause_count:
            if violation_type == "face_out_of_frame":
                state["out_frame_count"] = 0
            elif violation_type == "looking_away":
                state["look_away_count"] = 0
            else:
                state["excessive_move_count"] = 0
            ATTEMPT_MOTION_STATE[attempt_key] = state
            return {
                "action": "blackout_on",
                "violation_type": violation_type,
                "details": {
                    "count": count,
                    "center_x": round(center_x, 3),
                    "center_y": round(center_y, 3),
                    "movement_delta": round(movement_delta, 3),
                    "yaw_ratio": round(yaw_ratio, 3) if yaw_ratio is not None else None,
                },
            }

        if count == warning_count:
            return {
                "action": "warning",
                "violation_type": violation_type,
                "details": {
                    "count": count,
                    "center_x": round(center_x, 3),
                    "center_y": round(center_y, 3),
                    "movement_delta": round(movement_delta, 3),
                    "yaw_ratio": round(yaw_ratio, 3) if yaw_ratio is not None else None,
                },
            }

    return None


# ============================================================
# CSRF PROTECTION
# Session-based CSRF helpers for forms and JSON API calls.
# ============================================================
def _ensure_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.context_processor
def inject_global_template_vars():
    return {
        "csrf_token": _ensure_csrf_token(),
        "firebase_web_api_key": firebase_web_api_key(),
        "active_class_id": session.get("active_class_id", ""),
        "active_class_name": session.get("active_class_name", ""),
        "role": session.get("role", ""),
    }


def _require_csrf_form():
    token_form = request.form.get("csrf_token", "")
    token_sess = session.get("csrf_token", "")
    if not token_form or not token_sess or token_form != token_sess:
        abort(400, description="CSRF validation failed.")


def _require_csrf_json():
    token = request.headers.get("X-CSRF-Token", "")
    token_sess = session.get("csrf_token", "")
    return bool(token and token_sess and token == token_sess)


@app.before_request
def csrf_guard():
    if request.method == "POST" and not request.is_json and request.endpoint in {
        "logout",
        "capture",
        "quiz_capture",
        "instructor_create_session",
    }:
        _require_csrf_form()


@app.after_request
def add_no_cache_headers(resp):
    if request.path in ("/video_feed",):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp


# ============================================================
# FIREBASE AUTH HELPERS
# MODULE: auth
# Verifies Firebase ID tokens and extracts claims.
# ============================================================
# Firebase ID token verification helper.
def fb_verify_id_token(id_token: str):
    logger = logging.getLogger("classiface")
    try:
        # Retry logic: Firebase token verification can fail due to clock skew
        for attempt in range(3):
            try:
                decoded = fb_auth.verify_id_token(id_token, clock_skew_seconds=60)
                logger.info("Firebase ID token verified")
                logger.debug(f"uid_masked: {_mask_uid(decoded.get('uid', ''))}")
                logger.debug(f"email_masked: {_mask_email(decoded.get('email', ''))}")
                return decoded
            except Exception as attempt_error:
                if attempt < 2:
                    logger.warning(f"Token verification attempt {attempt+1} failed: {type(attempt_error).__name__}")
                    time.sleep(0.5)  # Brief delay before retry
                    continue
                raise attempt_error
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Token verification failed: {type(e).__name__}: {error_msg}")
        try:
            parts = id_token.split(".")
            if len(parts) >= 2:
                payload_segment = parts[1] + "=" * (-len(parts[1]) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_segment.encode("utf-8")))
                logger.error(
                    "Rejected token details: aud=%s iss=%s exp=%s iat=%s",
                    payload.get("aud"),
                    payload.get("iss"),
                    payload.get("exp"),
                    payload.get("iat"),
                )
        except Exception:
            logger.error("Could not decode rejected token metadata")
        
        # Provide specific error guidance
        if "hasClaimsForProvider" in error_msg or "CREDENTIAL_MISMATCH" in error_msg:
            logger.error("Service account might not have Firebase Auth access")
        elif "revoked" in error_msg.lower():
            logger.error("Token was revoked or user disabled")
        elif "expired" in error_msg.lower():
            logger.error("Token expired. May need to refresh token on frontend")
        elif "malformed" in error_msg.lower():
            logger.error("Token format is invalid")
        
        return None


def fb_get_claim(decoded: dict, key: str, default=None):
    try:
        return decoded.get(key, default)
    except Exception:
        return default


# ============================================================
# POSTGRESQL USER HELPERS
# MODULE: users
# Users table stores profile data. Firebase handles authentication.
# ============================================================
_USERS_ROLE_COL_CACHE = None
_USERS_FIREBASE_UID_COL_CACHE = None


def pg_users_has_role_column() -> bool:
    global _USERS_ROLE_COL_CACHE
    if _USERS_ROLE_COL_CACHE is not None:
        return _USERS_ROLE_COL_CACHE

    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name='users'
                  AND column_name='role'
                LIMIT 1;
                """
            )
            _USERS_ROLE_COL_CACHE = (cur.fetchone() is not None)
    except Exception:
        _USERS_ROLE_COL_CACHE = False

    return _USERS_ROLE_COL_CACHE


def pg_users_has_firebase_uid_column() -> bool:
    global _USERS_FIREBASE_UID_COL_CACHE
    if _USERS_FIREBASE_UID_COL_CACHE is not None:
        return _USERS_FIREBASE_UID_COL_CACHE

    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name='users'
                  AND column_name='firebase_uid'
                LIMIT 1;
                """
            )
            _USERS_FIREBASE_UID_COL_CACHE = (cur.fetchone() is not None)
    except Exception:
        _USERS_FIREBASE_UID_COL_CACHE = False

    return _USERS_FIREBASE_UID_COL_CACHE


def pg_find_user_by_pg_id(pg_user_id: str):
    with pg_conn() as conn, conn.cursor() as cur:
        if pg_users_has_role_column():
            cur.execute(
                """
                SELECT id, firebase_uid, first_name, last_name, full_name, email, role
                FROM users
                WHERE id=%s
                LIMIT 1;
                """,
                (str(pg_user_id),),
            )
        else:
            cur.execute(
                """
                SELECT id, firebase_uid, full_name, email
                FROM users
                WHERE id=%s
                LIMIT 1;
                """,
                (str(pg_user_id),),
            )
        return cur.fetchone()


def pg_find_user_by_firebase_uid(firebase_uid: str):
    if not pg_users_has_firebase_uid_column():
        return None

    with pg_conn() as conn, conn.cursor() as cur:
        if pg_users_has_role_column():
            cur.execute(
                """
                SELECT id, firebase_uid, first_name, last_name, full_name, email, role
                FROM users
                WHERE firebase_uid=%s
                LIMIT 1;
                """,
                (str(firebase_uid),),
            )
        else:
            cur.execute(
                """
                SELECT id, firebase_uid, full_name, email
                FROM users
                WHERE firebase_uid=%s
                LIMIT 1;
                """,
                (str(firebase_uid),),
            )
        return cur.fetchone()


def pg_find_user_by_email(email: str):
    email = (email or "").strip().lower()
    if not email:
        return None

    with pg_conn() as conn, conn.cursor() as cur:
        if pg_users_has_role_column():
            cur.execute(
                """
                SELECT id, firebase_uid, first_name, last_name, full_name, email, role
                FROM users
                WHERE lower(email)=lower(%s)
                LIMIT 1;
                """,
                (email,),
            )
        else:
            cur.execute(
                """
                SELECT id, firebase_uid, full_name, email
                FROM users
                WHERE lower(email)=lower(%s)
                LIMIT 1;
                """,
                (email,),
            )
        return cur.fetchone()


def pg_create_or_update_user_profile(
    firebase_uid: str,
    first_name: str,
    last_name: str,
    email: str,
    role: str = "student",
):
    """
    ✅ Uses Firebase UID for mapping (users.firebase_uid).
    ✅ users.id stays UUID (default gen_random_uuid()).
    """
    if not pg_users_has_firebase_uid_column():
        raise RuntimeError(
            "users.firebase_uid column missing. Run:\n"
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS firebase_uid text;\n"
            "CREATE UNIQUE INDEX IF NOT EXISTS users_firebase_uid_uq ON users(firebase_uid);"
        )

    full_name = f"{first_name} {last_name}".strip()
    role = (role or "student").strip().lower()

    # Public registration does not allow admin creation
    if role not in ("student", "instructor"):
        role = "student"

    with pg_conn() as conn, conn.cursor() as cur:
        if pg_users_has_role_column():
            cur.execute(
                """
                INSERT INTO users (firebase_uid, first_name, last_name, full_name, email, role)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (firebase_uid) DO UPDATE
                  SET first_name=EXCLUDED.first_name,
                      last_name=EXCLUDED.last_name,
                      full_name=EXCLUDED.full_name,
                      email=EXCLUDED.email,
                      role=EXCLUDED.role;
                """,
                (str(firebase_uid), first_name, last_name, full_name, email, role),
            )
        else:
            cur.execute(
                """
                INSERT INTO users (firebase_uid, first_name, last_name, full_name, email)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (firebase_uid) DO UPDATE
                  SET first_name=EXCLUDED.first_name,
                      last_name=EXCLUDED.last_name,
                      full_name=EXCLUDED.full_name,
                      email=EXCLUDED.email;
                """,
                (str(firebase_uid), first_name, last_name, full_name, email),
            )


def pg_list_users(limit: int = 500):
    """
    Used for building embedding DB (needs firebase_uid).
    """
    if not pg_users_has_firebase_uid_column():
        return []
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, firebase_uid, full_name, email
            FROM users
            ORDER BY full_name ASC
            LIMIT %s;
            """,
            (limit,),
        )
        return cur.fetchall() or []


def pg_list_all_users(role=None, limit: int = 2000):
    with pg_conn() as conn, conn.cursor() as cur:
        if role:
            cur.execute(
                """
                SELECT id, firebase_uid, first_name, last_name, full_name, email, role
                FROM users
                WHERE role=%s
                ORDER BY full_name ASC
                LIMIT %s;
                """,
                (str(role), int(limit)),
            )
        else:
            cur.execute(
                """
                SELECT id, firebase_uid, first_name, last_name, full_name, email, role
                FROM users
                ORDER BY role ASC, full_name ASC
                LIMIT %s;
                """,
                (int(limit),),
            )
        return cur.fetchall() or []


def pg_update_user(user_id: str, first_name: str, last_name: str, email: str, role: str):
    full_name = f"{first_name} {last_name}".strip()
    role = (role or "student").strip().lower()

    if role not in ("student", "instructor", "admin"):
        role = "student"

    with pg_conn() as conn, conn.cursor() as cur:
        # Get the Firebase UID linked to this PostgreSQL user first.
        # Firebase Auth is the login source, so email changes must be synced there too.
        cur.execute(
            """
            SELECT firebase_uid
            FROM users
            WHERE id=%s
            LIMIT 1;
            """,
            (str(user_id),),
        )
        row = cur.fetchone()

        if not row:
            return False

        firebase_uid = row.get("firebase_uid")

        # Update Firebase Auth before PostgreSQL so login uses the new email.
        if firebase_uid:
            fb_auth.update_user(
                firebase_uid,
                email=email,
            )

        cur.execute(
            """
            UPDATE users
            SET first_name=%s,
                last_name=%s,
                full_name=%s,
                email=%s,
                role=%s
            WHERE id=%s;
            """,
            (
                first_name,
                last_name,
                full_name,
                email,
                role,
                str(user_id),
            ),
        )
        conn.commit()
        return cur.rowcount > 0


def pg_delete_user(user_id: str):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM class_students WHERE student_id=%s;", (str(user_id),))
        cur.execute("DELETE FROM class_instructors WHERE instructor_id=%s;", (str(user_id),))
        cur.execute("DELETE FROM quizzes WHERE created_by=%s;", (str(user_id),))
        cur.execute("DELETE FROM users WHERE id=%s;", (str(user_id),))
        conn.commit()
        return cur.rowcount > 0


# ============================================================
# POSTGRESQL CLASS HELPERS
# MODULE: classes
# Class CRUD and class-student/class-instructor assignments.
# ============================================================
def pg_list_student_classes(student_id: str):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              c.id,
              c.class_code,
              c.section_name,
              c.subject,
              COALESCE(c.description,'') AS description,
              c.created_at
            FROM class_students cs
            JOIN classes c ON c.id = cs.class_id
            WHERE cs.student_id=%s
            ORDER BY c.created_at DESC, c.section_name ASC;
            """,
            (student_id,),
        )
        return cur.fetchall() or []


def pg_list_instructor_classes(instructor_id: str):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              c.id,
              c.class_code,
              c.section_name,
              c.subject,
              COALESCE(c.description,'') AS description,
              c.created_at
            FROM class_instructors ci
            JOIN classes c ON c.id = ci.class_id
            WHERE ci.instructor_id=%s
            ORDER BY c.created_at DESC, c.section_name ASC;
            """,
            (instructor_id,),
        )
        return cur.fetchall() or []


def pg_first_student_class_id(student_id: str):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id
            FROM class_students cs
            JOIN classes c ON c.id = cs.class_id
            WHERE cs.student_id=%s
            ORDER BY c.created_at DESC
            LIMIT 1;
            """,
            (student_id,),
        )
        row = cur.fetchone()
        return str(row["id"]) if row else None


def pg_first_instructor_class_id(instructor_id: str):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id
            FROM class_instructors ci
            JOIN classes c ON c.id = ci.class_id
            WHERE ci.instructor_id=%s
            ORDER BY c.created_at DESC
            LIMIT 1;
            """,
            (instructor_id,),
        )
        row = cur.fetchone()
        return str(row["id"]) if row else None


def pg_get_class_by_id(class_id: str):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              id,
              class_code,
              section_name,
              subject,
              COALESCE(description,'') AS description,
              created_at
            FROM classes
            WHERE id=%s
            LIMIT 1;
            """,
            (class_id,),
        )
        return cur.fetchone()


def pg_student_in_class(student_id: str, class_id: str) -> bool:
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM class_students
            WHERE student_id=%s AND class_id=%s
            LIMIT 1;
            """,
            (student_id, class_id),
        )
        return cur.fetchone() is not None


def pg_instructor_in_class(instructor_id: str, class_id: str) -> bool:
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM class_instructors
            WHERE instructor_id=%s AND class_id=%s
            LIMIT 1;
            """,
            (instructor_id, class_id),
        )
        return cur.fetchone() is not None


def pg_list_all_classes(limit: int = 2000):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              id,
              class_code,
              section_name,
              subject,
              COALESCE(description,'') AS description,
              created_at
            FROM classes
            ORDER BY created_at DESC, section_name ASC
            LIMIT %s;
            """,
            (int(limit),),
        )
        return cur.fetchall() or []


def pg_create_class(class_code: str, section_name: str, subject: str, description: str = ""):
    class_id = str(uuid.uuid4())
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO classes (id, class_code, section_name, subject, description, created_at)
            VALUES (%s, %s, %s, %s, %s, now());
            """,
            (class_id, class_code, section_name, subject, description or ""),
        )
    return class_id


def pg_update_class(class_id: str, class_code: str, section_name: str, subject: str, description: str = ""):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE classes
            SET class_code=%s,
                section_name=%s,
                subject=%s,
                description=%s
            WHERE id=%s;
            """,
            (class_code, section_name, subject, description or "", str(class_id)),
        )
        return cur.rowcount > 0


def pg_delete_class(class_id: str):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM class_students WHERE class_id=%s;", (str(class_id),))
        cur.execute("DELETE FROM class_instructors WHERE class_id=%s;", (str(class_id),))
        cur.execute("DELETE FROM class_sessions WHERE class_id=%s;", (str(class_id),))
        cur.execute("DELETE FROM quizzes WHERE class_id=%s;", (str(class_id),))
        cur.execute("DELETE FROM classes WHERE id=%s;", (str(class_id),))
        return cur.rowcount > 0


def pg_assign_student_to_class(student_id: str, class_id: str):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO class_students (student_id, class_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING;
            """,
            (str(student_id), str(class_id)),
        )
    return True


def pg_remove_student_from_class(student_id: str, class_id: str):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM class_students
            WHERE student_id=%s AND class_id=%s;
            """,
            (str(student_id), str(class_id)),
        )
        return cur.rowcount > 0


def pg_assign_instructor_to_class(instructor_id: str, class_id: str):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO class_instructors (instructor_id, class_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING;
            """,
            (str(instructor_id), str(class_id)),
        )
    return True


def pg_remove_instructor_from_class(instructor_id: str, class_id: str):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM class_instructors
            WHERE instructor_id=%s AND class_id=%s;
            """,
            (str(instructor_id), str(class_id)),
        )
        return cur.rowcount > 0


def pg_list_students_in_class(class_id: str, limit: int = 5000):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, u.full_name, u.email
            FROM class_students cs
            JOIN users u ON u.id = cs.student_id
            WHERE cs.class_id=%s
            ORDER BY u.full_name ASC
            LIMIT %s;
            """,
            (str(class_id), int(limit)),
        )
        return cur.fetchall() or []


def pg_list_instructors_in_class(class_id: str, limit: int = 5000):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, u.full_name, u.email
            FROM class_instructors ci
            JOIN users u ON u.id = ci.instructor_id
            WHERE ci.class_id=%s
            ORDER BY u.full_name ASC
            LIMIT %s;
            """,
            (str(class_id), int(limit)),
        )
        return cur.fetchall() or []


# ============================================================
# POSTGRESQL QUIZ HELPERS
# MODULE: quizzes
# Quiz CRUD, quiz attempts, and quiz violation support.
# ============================================================
def pg_ensure_quiz_tables():
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS quizzes (
                  id uuid PRIMARY KEY,
                  class_id uuid NOT NULL,
                  created_by text,
                  title text NOT NULL,
                  description text,
                  total_points int NOT NULL DEFAULT 100,
                  questions_json jsonb NOT NULL,
                  created_at timestamp NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_quizzes_class_id ON quizzes(class_id);")
            cur.execute("ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS updated_at timestamp;")
            cur.execute("ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS question_count int DEFAULT 0;")
            cur.execute("ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS is_active boolean DEFAULT FALSE;")  # ensure is_active exists
            cur.execute("ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS time_limit_minutes integer DEFAULT 60;")  # quiz time limit
            cur.execute("ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS attempts_type text DEFAULT 'unlimited';")  # unlimited/limited attempts
            cur.execute("ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS attempts_limit integer DEFAULT NULL;")  # NULL means unlimited
            cur.execute("ALTER TABLE quizzes ADD COLUMN IF NOT EXISTS grade_method text DEFAULT 'highest';")  # highest/latest grading
    except Exception:
        pass


# CHANGED: Dedicated helper that ensures quiz_attempt_violations has the CORRECT schema.
# The table must NOT have a user_id column — user identity is always resolved via
# quiz_attempt_violations.attempt_id -> quiz_attempts.attempt_id -> quiz_attempts.user_id.
# This function is called at startup and before any violation INSERT/SELECT so the
# table always exists and always has every required column regardless of how it was
# originally created (different deployments may have used different column names).
def pg_ensure_violation_table():  # CHANGED
    """
    CHANGED: Idempotent migration for quiz_attempt_violations.

    Strategy:
      1. CREATE TABLE IF NOT EXISTS with the minimal required columns.
      2. ADD COLUMN IF NOT EXISTS for every required column — this is the critical
         step that was missing: if the table already existed with an older/different
         schema (e.g. missing timestamp_iso or time_remaining) each column is added
         individually so inserts never fail with "column does not exist".
      3. DROP COLUMN IF EXISTS user_id — user identity must always be resolved
         through the quiz_attempts JOIN, never stored directly here.
      4. CREATE INDEX IF NOT EXISTS on attempt_id for fast lookups.
    All steps are committed in a single transaction.
    """
    try:  # CHANGED
        with pg_conn() as conn, conn.cursor() as cur:  # CHANGED

            # CHANGED: Step 1 — create with minimal skeleton if the table doesn't exist yet
            cur.execute(  # CHANGED
                """
                CREATE TABLE IF NOT EXISTS quiz_attempt_violations (
                  id uuid PRIMARY KEY
                );
                """
            )

            # CHANGED: Step 2 — ADD every required column individually using
            # ADD COLUMN IF NOT EXISTS so this is safe to run against any existing
            # version of the table (old schema, partial schema, or brand-new).
            # This is the fix for "column timestamp_iso does not exist" errors —
            # the column simply wasn't present in the pre-existing table and
            # CREATE TABLE IF NOT EXISTS silently skipped past it.
            required_columns = [  # CHANGED
                ("attempt_id",     "text    NOT NULL DEFAULT ''"),   # CHANGED
                ("violation_type", "text    NOT NULL DEFAULT ''"),   # CHANGED
                ("timestamp_iso",  "text"),                          # CHANGED
                ("time_remaining", "numeric"),                       # CHANGED
            ]  # CHANGED
            for col_name, col_def in required_columns:  # CHANGED
                cur.execute(  # CHANGED
                    f"ALTER TABLE quiz_attempt_violations "  # CHANGED
                    f"ADD COLUMN IF NOT EXISTS {col_name} {col_def};"  # CHANGED
                )
                print(f"   OK Ensured column quiz_attempt_violations.{col_name}", flush=True)

            # CHANGED: Step 3 — remove legacy user_id column if it exists so that
            # inserts never accidentally include it and queries never reference it.
            cur.execute(  # CHANGED
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = 'quiz_attempt_violations'
                  AND column_name  = 'user_id'
                LIMIT 1;
                """
            )
            if cur.fetchone():  # CHANGED
                cur.execute(  # CHANGED
                    "ALTER TABLE quiz_attempt_violations DROP COLUMN IF EXISTS user_id;"
                )
                print("WARN Dropped legacy user_id column from quiz_attempt_violations", flush=True)

            # CHANGED: Step 4 — index for fast attempt-level lookups
            cur.execute(  # CHANGED
                "CREATE INDEX IF NOT EXISTS idx_qav_attempt_id "  # CHANGED
                "ON quiz_attempt_violations(attempt_id);"  # CHANGED
            )

            conn.commit()
            print("OK pg_ensure_violation_table: schema verified/migrated", flush=True)
    except Exception as e:  # CHANGED
        app.logger.error(f"Failed to ensure violation table: {type(e).__name__}")


# CHANGED: Run the violation table migration once at import time so the correct
# schema is in place before any request handler runs.
try:  # CHANGED
    pg_ensure_violation_table()
except Exception as _vt_err:  # CHANGED
    logger = logging.getLogger("classiface")
    logger.warning(f"Failed to initialize violation table at startup: {type(_vt_err).__name__}")


def pg_migrate_quiz_attempts_for_multiple_attempts():
    """
    NEW: Migrate quiz_attempts table to support multiple attempts per student.
    Removes the UNIQUE (user_id, quiz_id) constraint and adds attempt_number column.
    """
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            # Step 1: Add attempt_number column if it doesn't exist
            cur.execute("ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS attempt_number INT DEFAULT 1;")
            
            # Step 2: Drop the old UNIQUE constraint if it exists
            # The constraint name is typically idx_quiz_attempts_unique based on setup_database.sql
            try:
                cur.execute("ALTER TABLE quiz_attempts DROP CONSTRAINT IF EXISTS idx_quiz_attempts_unique;")
                cur.execute("DROP INDEX IF EXISTS idx_quiz_attempts_unique;")
            except:
                pass  # Constraint might not exist or have a different name
            
            # Step 3: Create an index on (user_id, quiz_id, attempt_number) for efficient lookups
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_quiz_attempts_multi_attempt 
                ON quiz_attempts(user_id, quiz_id, attempt_number DESC);
            """)
            
            conn.commit()
            print("✅ Quiz attempts table migrated for multiple attempts support", flush=True)
    except Exception as e:
        print(f"⚠️  pg_migrate_quiz_attempts_for_multiple_attempts: {e}", flush=True)


# NEW: Run the migration at startup
try:
    pg_migrate_quiz_attempts_for_multiple_attempts()
except Exception as _mu_err:
    print(f"WARN Migration error at startup: {_mu_err}", flush=True)


# ============================================================
# SECURITY: Quiz Submission Security Tables & Migrations
# ============================================================
def pg_ensure_quiz_security_tables():
    """
    Create tables for quiz submission security:
    - submission tokens for one-time use
    - audit log for tracking all submission attempts
    """
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            # Submission tokens table (one-time use tokens bound to sessions)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quiz_submission_tokens (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    attempt_id UUID NOT NULL REFERENCES quiz_attempts(attempt_id) ON DELETE CASCADE,
                    token VARCHAR(64) NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    used_at TIMESTAMP,
                    used BOOLEAN DEFAULT FALSE,
                    user_id UUID NOT NULL,
                    quiz_id UUID NOT NULL
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_submission_tokens_attempt_id 
                ON quiz_submission_tokens(attempt_id);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_submission_tokens_token 
                ON quiz_submission_tokens(token);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_submission_tokens_used 
                ON quiz_submission_tokens(used, used_at);
            """)

            # Audit log table for tracking submission attempts
            cur.execute("""
                CREATE TABLE IF NOT EXISTS quiz_submission_audit (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    attempt_id UUID NOT NULL REFERENCES quiz_attempts(attempt_id) ON DELETE CASCADE,
                    user_id UUID NOT NULL,
                    quiz_id UUID NOT NULL,
                    submission_token VARCHAR(64),
                    token_valid BOOLEAN DEFAULT FALSE,
                    submission_timestamp TIMESTAMP DEFAULT NOW(),
                    client_ip VARCHAR(45),
                    user_agent TEXT,
                    answers_hash VARCHAR(64),
                    score_awarded INT,
                    total_points INT,
                    tamper_detected BOOLEAN DEFAULT FALSE,
                    tamper_reason TEXT,
                    submission_attempt_number INT DEFAULT 1
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_attempt_id 
                ON quiz_submission_audit(attempt_id);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_user_id 
                ON quiz_submission_audit(user_id);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_quiz_id 
                ON quiz_submission_audit(quiz_id);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp 
                ON quiz_submission_audit(submission_timestamp);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_tamper 
                ON quiz_submission_audit(tamper_detected);
            """)

            # Add columns to quiz_attempts table for security
            cur.execute("""
                ALTER TABLE quiz_attempts 
                ADD COLUMN IF NOT EXISTS submitted_ip VARCHAR(45);
            """)
            cur.execute("""
                ALTER TABLE quiz_attempts 
                ADD COLUMN IF NOT EXISTS submission_token_used BOOLEAN DEFAULT FALSE;
            """)
            cur.execute("""
                ALTER TABLE quiz_attempts 
                ADD COLUMN IF NOT EXISTS tamper_detected BOOLEAN DEFAULT FALSE;
            """)
            cur.execute("""
                ALTER TABLE quiz_attempts 
                ADD COLUMN IF NOT EXISTS correct_answers_hash VARCHAR(64);
            """)

            conn.commit()
            logger = logging.getLogger("classiface")
            logger.info("Quiz security tables created")
    except Exception as e:
        logger = logging.getLogger("classiface")
        logger.error(f"pg_ensure_quiz_security_tables: {type(e).__name__}: {str(e)}")


def pg_generate_submission_token(attempt_id: str, user_id: str, quiz_id: str) -> str:
    """
    Generate a one-time submission token for this quiz attempt.
    Token is stored in database and can only be used once.
    """
    token = secrets.token_urlsafe(48)
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO quiz_submission_tokens 
                (attempt_id, token, user_id, quiz_id)
                VALUES (%s, %s, %s, %s);
            """, (str(attempt_id), token, str(user_id), str(quiz_id)))
            conn.commit()
    except Exception as e:
        logger = logging.getLogger("classiface")
        logger.error(f"Error generating submission token: {type(e).__name__}: {str(e)}")
        return ""
    return token


def pg_validate_submission_token(attempt_id: str, token: str) -> tuple[bool, str]:
    """
    Validate and consume a submission token.
    Returns (is_valid, error_message)
    - Token must exist and not be used yet
    - Prevents replay attacks
    """
    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Check if token exists and hasn't been used
            cur.execute("""
                SELECT id, used, used_at, created_at FROM quiz_submission_tokens
                WHERE token = %s AND attempt_id = %s
                LIMIT 1;
            """, (token, str(attempt_id)))
            row = cur.fetchone()
            
            if not row:
                return False, "Invalid or missing submission token"
            
            if row.get("used"):
                return False, "Submission token already used (replay attack detected)"
            
            # Check token age (should be used within 1 hour)
            token_age = datetime.now() - row.get("created_at")
            if token_age.total_seconds() > 3600:  # 1 hour
                return False, "Submission token expired"
            
            # Mark token as used
            cur.execute("""
                UPDATE quiz_submission_tokens 
                SET used = TRUE, used_at = NOW()
                WHERE token = %s;
            """, (token,))
            conn.commit()
            
            return True, ""
    except Exception as e:
        logger = logging.getLogger("classiface")
        logger.error(f"Token validation error: {type(e).__name__}: {str(e)}")
        return False, f"Token validation error: {type(e).__name__}"


def pg_log_submission_attempt(
    attempt_id: str,
    user_id: str,
    quiz_id: str,
    submission_token: str,
    token_valid: bool,
    client_ip: str,
    user_agent: str,
    answers_hash: str,
    score_awarded: int,
    total_points: int,
    tamper_detected: bool = False,
    tamper_reason: str = ""
):
    """
    Log all submission attempts with IP, timestamp, and details.
    Used for audit trail and detecting cheating attempts.
    """
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO quiz_submission_audit
                (attempt_id, user_id, quiz_id, submission_token, token_valid, 
                 client_ip, user_agent, answers_hash, score_awarded, total_points,
                 tamper_detected, tamper_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                str(attempt_id), str(user_id), str(quiz_id), submission_token, token_valid,
                client_ip, user_agent, answers_hash, int(score_awarded), int(total_points),
                tamper_detected, tamper_reason
            ))
            conn.commit()
    except Exception as e:
        print(f"Error logging submission attempt: {e}", flush=True)


def pg_get_client_ip() -> str:
    """Get client IP address from request, handling proxies."""
    if request.environ.get('HTTP_CF_CONNECTING_IP'):
        return request.environ['HTTP_CF_CONNECTING_IP']
    elif request.environ.get('HTTP_X_FORWARDED_FOR'):
        return request.environ['HTTP_X_FORWARDED_FOR'].split(',')[0].strip()
    else:
        return request.remote_addr or "unknown"


def pg_check_replay_attack(user_id: str, attempt_id: str) -> bool:
    """
    Check if user has already successfully submitted this attempt.
    Used to detect and prevent replay attacks.
    """
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) as cnt FROM quiz_submission_audit
                WHERE user_id = %s AND attempt_id = %s 
                AND token_valid = TRUE
                LIMIT 1;
            """, (str(user_id), str(attempt_id)))
            row = cur.fetchone()
            return row[0] > 0 if row else False
    except Exception as e:
        print(f"Error checking replay attack: {e}", flush=True)
        return False


# Initialize security tables at startup
try:
    pg_ensure_quiz_security_tables()
except Exception as _sec_err:
    print(f"WARN Security tables initialization error: {_sec_err}", flush=True)


def pg_compute_answers_hash(answers: dict) -> str:
    """
    Compute SHA256 hash of submitted answers.
    Used to detect tampering or unauthorized modifications.
    """
    try:
        answers_json = json.dumps(answers, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(answers_json.encode()).hexdigest()
    except Exception:
        return ""


def pg_extract_correct_answers(quiz_questions: list) -> dict:
    """
    Extract correct answers from quiz questions (server-side).
    Never expose these to the client.
    Returns dict mapping question ID to correct answer(s).
    """
    correct_answers = {}
    try:
        for q in quiz_questions:
            qid = q.get("id")
            qtype = (q.get("type") or "").strip()
            
            if qtype == "multiple-choice":
                correct_answers[str(qid)] = q.get("correctAnswers", [])
            elif qtype == "true-false":
                correct_answers[str(qid)] = q.get("correctAnswer", "")
            elif qtype == "matching":
                correct_answers[str(qid)] = q.get("correctAnswers", [])
            elif qtype == "numerical":
                correct_answers[str(qid)] = {
                    "value": q.get("correctAnswer"),
                    "tolerance": q.get("tolerance", 0.0)
                }
            elif qtype == "identification":
                correct_answers[str(qid)] = {
                    "value": q.get("correctAnswer"),
                    "caseInsensitive": q.get("caseInsensitive", True)
                }
            # Image answers are marked as pending - never auto-graded
    except Exception as e:
        print(f"Error extracting correct answers: {e}", flush=True)
    
    return correct_answers


def pg_validate_answers_integrity(client_answers: dict, server_answers: dict, quiz_questions: list) -> tuple[bool, str]:
    """
    Validate that client answers haven't been tampered with.
    Checks:
    1. All question IDs are valid
    2. Answer values are within expected format/range
    3. No extra answers were added by client
    """
    try:
        quiz_question_ids = {str(q.get("id")) for q in quiz_questions}
        client_question_ids = set(client_answers.keys())
        
        # Check for extra questions the client added
        extra_questions = client_question_ids - quiz_question_ids
        if extra_questions:
            return False, f"Invalid question IDs in submission: {extra_questions}"
        
        # Validate each answer format
        for qid, question in [(q.get("id"), q) for q in quiz_questions]:
            qid_str = str(qid)
            if qid_str not in client_answers:
                continue  # Unanswered is OK
            
            client_ans = client_answers[qid_str]
            qtype = (question.get("type") or "").strip()
            
            # Validate format based on question type
            if qtype == "multiple-choice":
                if not isinstance(client_ans, (int, str)):
                    try:
                        int(client_ans)
                    except (ValueError, TypeError):
                        return False, f"Invalid answer format for question {qid}"
            
            elif qtype == "true-false":
                if not isinstance(client_ans, (str, bool)):
                    return False, f"Invalid answer format for question {qid}"
            
            elif qtype == "matching":
                if not isinstance(client_ans, dict):
                    return False, f"Invalid answer format for question {qid}"
            
            elif qtype == "numerical":
                try:
                    float(client_ans)
                except (ValueError, TypeError):
                    return False, f"Invalid numerical answer for question {qid}"
        
        return True, ""
    except Exception as e:
        return False, f"Answer validation error: {str(e)}"


def pg_cleanup_unlimited_attempt_limits():
    """
    Clears attempts_limit for quizzes marked as unlimited.
    This fixes existing rows such as type=unlimited, limit=2.
    """
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE quizzes
                   SET attempts_limit = NULL
                 WHERE COALESCE(attempts_type, 'unlimited') = 'unlimited'
                   AND attempts_limit IS NOT NULL;
                """
            )
            if cur.rowcount:
                print(f"✅ Cleared attempts_limit for {cur.rowcount} unlimited quiz/quizzes", flush=True)
            conn.commit()
    except Exception as e:
        print(f"⚠️ pg_cleanup_unlimited_attempt_limits error: {e}", flush=True)


try:
    pg_cleanup_unlimited_attempt_limits()
except Exception as _cleanup_err:
    print(f"WARN Unlimited attempts cleanup error at startup: {_cleanup_err}", flush=True)



def pg_list_quizzes_for_class(class_id: str, limit: int = 200):
    pg_ensure_quiz_tables()
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              id,
              class_id,
              title,
              COALESCE(description,'') AS description,
              total_points,
              created_by,
              created_at,
              updated_at,
              questions_json,
              COALESCE(is_active, FALSE) AS is_active,
              COALESCE(time_limit_minutes, 60) AS time_limit_minutes,
              COALESCE(attempts_type, 'unlimited') AS attempts_type,
              attempts_limit,
              COALESCE(grade_method, 'highest') AS grade_method
            FROM quizzes
            WHERE class_id=%s
            ORDER BY COALESCE(updated_at, created_at) DESC
            LIMIT %s;
            """,
            (class_id, int(limit)),
        )
        return cur.fetchall() or []


def pg_get_quiz_by_id(quiz_id: str):
    pg_ensure_quiz_tables()
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              id,
              class_id,
              title,
              COALESCE(description,'') AS description,
              total_points,
              created_by,
              created_at,
              updated_at,
              questions_json,
              COALESCE(is_active, FALSE) AS is_active,
              COALESCE(time_limit_minutes, 60) AS time_limit_minutes,
              COALESCE(attempts_type, 'unlimited') AS attempts_type,
              attempts_limit,
              COALESCE(grade_method, 'highest') AS grade_method
            FROM quizzes
            WHERE id=%s
            LIMIT 1;
            """,
            (quiz_id,),
        )
        return cur.fetchone()



def _normalise_quiz_attempt_settings(attempts_type="unlimited", attempts_limit=None):
    """
    Ensures unlimited quizzes never keep an old attempts_limit value.
    - unlimited  -> attempts_limit = None
    - limited    -> attempts_limit = positive integer, otherwise None
    """
    attempts_type = (attempts_type or "unlimited").strip().lower()

    if attempts_type not in ("unlimited", "limited"):
        attempts_type = "unlimited"

    if attempts_type == "unlimited":
        allowed_attempts = None

    if attempts_type == "unlimited":
        return "unlimited", None

    try:
        attempts_limit = int(attempts_limit)
        if attempts_limit <= 0:
            attempts_limit = None
    except Exception:
        attempts_limit = None

    return "limited", attempts_limit



def pg_create_quiz(class_id: str, created_by: str, title: str, description: str, total_points: int, questions: dict, time_limit_minutes: int = 60, attempts_type: str = "unlimited", attempts_limit: int = None, grade_method: str = "highest"):
    pg_ensure_quiz_tables()
    attempts_type, attempts_limit = _normalise_quiz_attempt_settings(attempts_type, attempts_limit)
    quiz_id = str(uuid.uuid4())
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO quizzes
              (id, class_id, created_by, title, description, total_points, questions_json, time_limit_minutes, attempts_type, attempts_limit, grade_method, updated_at)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL);
            """,
            (
                quiz_id,
                class_id,
                str(created_by) if created_by else None,
                title,
                description or "",
                int(total_points or 100),
                psycopg2.extras.Json(questions),
                int(time_limit_minutes or 60),
                attempts_type,
                attempts_limit,
                grade_method if grade_method in ("highest", "latest") else "highest",
            ),
        )
    return quiz_id


def pg_update_quiz(quiz_id: str, class_id: str, title: str, description: str, total_points: int, questions: dict, updated_by: str, time_limit_minutes: int = 60, attempts_type: str = "unlimited", attempts_limit: int = None, grade_method: str = "highest", is_active=None):
    pg_ensure_quiz_tables()
    attempts_type, attempts_limit = _normalise_quiz_attempt_settings(attempts_type, attempts_limit)
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quizzes
               SET class_id=%s,
                   title=%s,
                   description=%s,
                   total_points=%s,
                   questions_json=%s,
                   time_limit_minutes=%s,
                   attempts_type=%s,
                   attempts_limit=%s,
                   grade_method=%s,
                   is_active=COALESCE(%s, is_active),
                   updated_at=now()
             WHERE id=%s;
            """,
            (
                class_id,
                title,
                description or "",
                int(total_points or 100),
                psycopg2.extras.Json(questions),
                int(time_limit_minutes or 60),
                attempts_type,
                attempts_limit,
                grade_method if grade_method in ("highest", "latest") else "highest",
                is_active if is_active is not None else None,
                quiz_id,
            ),
        )
        if cur.rowcount <= 0:
            return False
    return True


def pg_toggle_quiz_activation(quiz_id: str, is_active: bool):
    pg_ensure_quiz_tables()
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE quizzes
               SET is_active=%s,
                   updated_at=now()
             WHERE id=%s;
            """,
            (is_active, quiz_id),
        )
        if cur.rowcount <= 0:
            return False
    return True


def pg_list_quiz_attempt_violations_for_class(instructor_id: str, class_id: str, quiz_id: str = None, student_id: str = None, limit: int = 5000):
    # CHANGED: Ensure violation table exists with correct schema before querying
    # CHANGED: violation table schema is verified once at app startup; avoid repeated checks here.
    try:
        quiz_filter = ""
        student_filter = ""
        params = [str(instructor_id), str(class_id)]

        if quiz_id:
            quiz_filter = "AND qa.quiz_id = %s"
            params.append(str(quiz_id))

        if student_id:
            # CHANGED: use qa.user_id — quiz_attempt_violations has NO user_id column.
            # User identity is always resolved via quiz_attempts JOIN.
            student_filter = "AND qa.user_id = %s"  # CHANGED
            params.append(str(student_id))

        with pg_conn() as conn, conn.cursor() as cur:
            # CHANGED: All joins go through quiz_attempts to get user identity.
            # quiz_attempt_violations is joined on attempt_id only — no user_id reference.
            cur.execute(
                f"""
                SELECT
                  COALESCE(u.full_name, 'Unknown')   AS student_name,
                  COALESCE(qa.quiz_title, 'Unknown') AS quiz_title,
                  qv.violation_type,
                  qv.timestamp_iso,
                  qv.time_remaining
                FROM quiz_attempt_violations qv
                LEFT JOIN quiz_attempts qa
                  ON qa.attempt_id = qv.attempt_id
                LEFT JOIN users u
                  ON u.id = qa.user_id
                LEFT JOIN quizzes q
                  ON q.id = qa.quiz_id::uuid
                WHERE
                  qa.user_id IN (
                    SELECT cs.student_id
                    FROM class_students cs
                    JOIN class_instructors ci ON ci.class_id = cs.class_id
                    WHERE ci.instructor_id = %s
                      AND cs.class_id = %s
                  )
                  {quiz_filter}
                  {student_filter}
                ORDER BY qv.timestamp_iso DESC NULLS LAST
                LIMIT %s;
                """,
                params + [int(limit)],
            )
            return cur.fetchall() or []
    except Exception as e:
        print(f"ERROR pg_list_quiz_attempt_violations_for_class error: {str(e)}", flush=True)
        return []


# ============================================================
# POSTGRESQL SESSION HELPERS
# MODULE: sessions
# Session date ranges and attendance/quiz availability windows.
# ============================================================
def _parse_time_hhmm(s: str):
    s = (s or "").strip()
    if not s:
        return None

    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(s, fmt).time().replace(second=0, microsecond=0)
        except Exception:
            pass

    return None

def pg_upsert_class_session(
    class_id: str,
    start_date: date,
    end_date: date,
    present_until: dtime,
    late_until: dtime,
    session_end: dtime,
    created_by: str,
    present_start: dtime = None,
    late_start: dtime = None,
    is_all_day: bool = False,
):
    """
    Save one session row per day in the selected date range.

    Example:
      start_date = 2026-05-09
      end_date   = 2026-05-15

    This creates/updates:
      2026-05-09, 2026-05-10, 2026-05-11,
      2026-05-12, 2026-05-13, 2026-05-14, 2026-05-15

    This is required because instructor attendance is filtered per day using
    class_sessions.session_date.
    """

    if present_start is None:
        present_start = dtime(0, 0, 0)

    if late_start is None:
        late_start = _add_minutes(present_until, 1)

    if end_date < start_date:
        raise ValueError("end_date must be after or equal to start_date")

    saved_count = 0
    current_date = start_date

    with pg_conn() as conn, conn.cursor() as cur:
        while current_date <= end_date:
            sid = str(uuid.uuid4())

            cur.execute(
                """
                UPDATE class_sessions
                   SET present_start = %s,
                       present_until = %s,
                       late_start = %s,
                       late_until = %s,
                       session_end = %s,
                       created_by = %s,
                       start_date = %s,
                       end_date = %s,
                       is_all_day = %s
                 WHERE class_id = %s
                   AND session_date = %s
                RETURNING id;
                """,
                (
                    present_start,
                    present_until,
                    late_start,
                    late_until,
                    session_end,
                    str(created_by),
                    current_date,
                    current_date,
                    is_all_day,
                    str(class_id),
                    current_date,
                ),
            )

            if cur.fetchone() is None:
                cur.execute(
                    """
                    INSERT INTO class_sessions
                      (
                        id,
                        class_id,
                        session_date,
                        present_start,
                        present_until,
                        late_start,
                        late_until,
                        session_end,
                        created_by,
                        start_date,
                        end_date,
                        is_all_day
                      )
                    VALUES
                      (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        sid,
                        str(class_id),
                        current_date,
                        present_start,
                        present_until,
                        late_start,
                        late_until,
                        session_end,
                        str(created_by),
                        current_date,
                        current_date,
                        is_all_day,
                    ),
                )

            saved_count += 1
            current_date = current_date + timedelta(days=1)

        conn.commit()

    print(
        f"✅ Saved/updated {saved_count} class session day(s) "
        f"for class_id={class_id} from {start_date} to {end_date}",
        flush=True,
    )

    return True



def pg_get_active_session_for_date(class_id: str, target_date: date = None):
    """Get the most recently created session for a specific date using session_date."""

    if target_date is None:
        target_date = app_today()

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              id,
              class_id,
              session_date,
              present_start,
              present_until,
              late_start,
              late_until,
              session_end,
              created_by,
              created_at
            FROM class_sessions
            WHERE class_id = %s
              AND session_date = %s
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            (class_id, target_date),
        )
        return cur.fetchone()


def pg_get_all_sessions_for_class(class_id: str):
    """Get all session ranges for a class, ordered by most recent first."""
    
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              id,
              class_id,
              start_date,
              end_date,
              present_start,
              present_until,
              late_start,
              late_until,
              session_end,
              COALESCE(is_all_day, FALSE) AS is_all_day,
              created_at
            FROM class_sessions
            WHERE class_id=%s
            ORDER BY created_at DESC;
            """,
            (class_id,),
        )
        return cur.fetchall() or []


def pg_cleanup_expired_sessions(class_id: str = None):
    """
    Keep expired sessions for attendance history.

    Do not delete old class_sessions because attendance_records still reference
    their session_id for instructor history, reports, and admin analytics.
    """
    return 0


def _time_in_window(now_t: dtime, start_t: dtime, end_t: dtime) -> bool:
    if start_t is None or end_t is None:
        return False
    return start_t <= now_t <= end_t


def compute_attendance_status(
    now_t: dtime,
    present_start: dtime,
    present_until: dtime,
    late_start: dtime,
    late_until: dtime,
    session_end: dtime,
):
    if now_t < present_start:
        return "Not Started"
    if present_start <= now_t <= present_until:
        return "Present"
    if late_start <= now_t <= late_until:
        return "Late"
    if late_until < now_t <= session_end:
        return "Absent"
    return "Closed"


def compute_quiz_availability(now_dt: datetime, sess_row: dict):
    if not sess_row:
        return False, "no_session"

    present_start = sess_row.get("present_start")
    present_until = sess_row.get("present_until")
    late_start = sess_row.get("late_start")
    late_until = sess_row.get("late_until")
    session_end = sess_row.get("session_end")

    if isinstance(present_start, str):
        present_start = dtime.fromisoformat(present_start)
    if isinstance(present_until, str):
        present_until = dtime.fromisoformat(present_until)
    if isinstance(late_start, str):
        late_start = dtime.fromisoformat(late_start)
    if isinstance(late_until, str):
        late_until = dtime.fromisoformat(late_until)
    if isinstance(session_end, str):
        session_end = dtime.fromisoformat(session_end)

    now_t = now_dt.time().replace(second=0, microsecond=0)

    if now_t < present_start:
        return False, "not_started"
    if present_start <= now_t <= present_until:
        return True, "present"
    if late_start <= now_t <= late_until:
        return True, "late"
    if late_until < now_t <= session_end:
        return True, "absent"
    return False, "closed"


# ============================================================
# ATTENDANCE HELPERS
# MODULE: attendance
# Attendance marking, roster view, student attendance, and grade retrieval.
# ============================================================
def pg_mark_attendance_for_session(
    user_id: str,
    class_id: str,
    session_id: str,
    status: str,
    quiz_id: str = ""
):
    now_dt = app_now()
    today = now_dt.date()
    attendance_id = str(uuid.uuid4())

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO attendance_records
              (id, student_id, attendance_date, attendance_time, status, class_id, session_id, verified_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (student_id, class_id, attendance_date) DO UPDATE
              SET status = EXCLUDED.status,
                  session_id = EXCLUDED.session_id,
                  verified_at = now();
            """,
            (attendance_id, str(user_id), today, now_dt.time(), status, class_id, session_id),
        )
        conn.commit()
    return True, f"Attendance saved as {status}"


def pg_list_attendance_roster_for_session(class_id: str, session_id: str, limit=5000):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              u.id AS student_id,
              u.full_name,
              u.email,
              ar.status,
              ar.verified_at AS marked_at,  -- CHANGED
              CASE
                WHEN ar.verified_at IS NOT NULL THEN TRUE
                ELSE FALSE
              END AS verified  -- CHANGED
            FROM class_students cs
            JOIN users u ON u.id = cs.student_id
            LEFT JOIN attendance_records ar
              ON ar.student_id = cs.student_id
             AND ar.session_id = %s
            WHERE cs.class_id = %s
            ORDER BY u.full_name ASC
            LIMIT %s;
            """,
            (str(session_id), str(class_id), int(limit)),
        )
        return cur.fetchall() or []


def pg_list_attendance_records_for_class(
    class_id: str,
    attendance_date=None,
    limit: int = 5000
):
    """
    Show ALL students in the class roster for the selected date.

    If a student has no attendance record for that date, they still appear
    as Absent / Not Yet Marked. This is used by the instructor attendance
    page and admin analytics views that need complete roster visibility.
    """

    if attendance_date is None:
        attendance_date = app_today()

    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              ar.id,
              u.id AS student_id,
              u.full_name,
              u.email,
              COALESCE(ar.attendance_date, %s) AS attendance_date,
              ar.attendance_time,
              COALESCE(ar.status, 'Absent') AS status,
              ar.session_id,
              ar.verified_at,
              ar.verified_at AS marked_at,
              CASE
                WHEN ar.verified_at IS NOT NULL THEN TRUE
                ELSE FALSE
              END AS verified
            FROM class_students cs
            JOIN users u
              ON u.id = cs.student_id
            LEFT JOIN attendance_records ar
              ON ar.student_id = cs.student_id
             AND ar.class_id = cs.class_id
             AND ar.attendance_date = %s
            WHERE cs.class_id = %s
            ORDER BY
              u.full_name ASC
            LIMIT %s;
            """,
            (
                attendance_date,
                attendance_date,
                str(class_id),
                int(limit),
            ),
        )
        return cur.fetchall() or []


def pg_list_student_attendance(user_id: str, class_id: str, limit: int = 120):
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              cs.session_date AS attendance_date,
              ar.verified_at AS time_in,
              qa.submitted_at AS time_out,
              qa.started_at,
              COALESCE(ar.status, 'Absent') AS status

            FROM class_sessions cs

            LEFT JOIN attendance_records ar
              ON ar.session_id = cs.id
             AND ar.student_id = %s
             AND ar.class_id = cs.class_id

            LEFT JOIN quiz_attempts qa
              ON (
                  qa.user_id = ar.student_id
                  AND qa.submitted_at >= ar.verified_at
                  AND DATE(qa.submitted_at) = DATE(cs.session_date)
                  AND qa.attempt_id = (
                    SELECT attempt_id
                    FROM quiz_attempts qa2
                    WHERE qa2.user_id = ar.student_id
                      AND qa2.submitted_at >= ar.verified_at
                      AND DATE(qa2.submitted_at) = DATE(cs.session_date)
                    ORDER BY qa2.submitted_at DESC
                    LIMIT 1
                  )
              )

            WHERE cs.class_id = %s

            ORDER BY cs.session_date DESC
            LIMIT %s;
            """,
            (str(user_id), str(class_id), int(limit)),
        )
        rows = cur.fetchall() or []

    records = []
    for r in rows:
        d = r.get("attendance_date")
        time_in = r.get("time_in")
        time_out = r.get("time_out")
        started_at = r.get("started_at")
        status = (r.get("status") or "present")

        # Format time_in
        time_in_display = time_in.strftime("%I:%M %p").lstrip("0") if time_in else "—"

        # Format time_out (quiz submission time)
        time_out_display = time_out.strftime("%I:%M %p").lstrip("0") if time_out else "—"

        # Calculate duration (time spent on quiz)
        duration_display = "—"
        if started_at and time_out:
            duration = (time_out - started_at).total_seconds()
            if duration < 60:
                duration_display = f"{int(duration)}s"
            else:
                minutes = int(duration / 60)
                hours = minutes // 60
                remaining_mins = minutes % 60
                if hours > 0:
                    duration_display = f"{hours}h {remaining_mins}m"
                else:
                    duration_display = f"{minutes}m"

        status_display = status.capitalize() if status else "Present"

        records.append(
            {
                "date_display": d.strftime("%b %d, %Y") if d else "",
                "time_in_display": time_in_display,
                "time_out_display": time_out_display,
                "duration_display": duration_display,
                "status": status_display,
                "remarks": "-",
            }
        )

    total = len(records)
    present = sum(1 for x in records if x["status"].lower() == "present")
    late = sum(1 for x in records if x["status"].lower() == "late")
    absent = sum(1 for x in records if x["status"].lower() == "absent")
    rate_percent = int(round(((present + late) / total) * 100)) if total > 0 else 0

    summary = {
        "total": total,
        "present": present,
        "late": late,
        "absent": absent,
        "rate_percent": rate_percent,
    }
    return summary, records


def pg_list_student_grades(user_id: str, class_id: str, limit: int = 50):
    try:
        from psycopg2 import extras

        with pg_conn() as conn, conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute(
                """
                WITH ranked_attempts AS (
                    SELECT
                      qa.attempt_id,
                      qa.quiz_id,
                      qa.quiz_title,
                      qa.score,
                      qa.total_points,
                      qa.submitted_at,
                      COALESCE(q.grade_method, 'highest') AS grade_method,

                      ROW_NUMBER() OVER (
                        PARTITION BY qa.quiz_id
                        ORDER BY
                          CASE
                            WHEN COALESCE(q.grade_method, 'highest') = 'highest'
                            THEN qa.score
                            ELSE NULL
                          END DESC,
                          qa.submitted_at DESC
                      ) AS rn

                    FROM quiz_attempts qa
                    JOIN quizzes q
                      ON q.id = qa.quiz_id::uuid

                    WHERE qa.user_id = %s
                      AND q.class_id = %s
                      AND qa.submitted_at IS NOT NULL
                )

                SELECT
                  attempt_id,
                  quiz_title,
                  score,
                  total_points,
                  submitted_at,
                  grade_method
                FROM ranked_attempts
                WHERE rn = 1
                ORDER BY submitted_at DESC
                LIMIT %s;
                """,
                (str(user_id), str(class_id), int(limit)),
            )
            rows = cur.fetchall() or []

    except Exception as e:
        print(f"Error in pg_list_student_grades: {str(e)}")
        return []

    out = []

    for r in rows:
        row_dict = dict(r)
        submitted = row_dict.get("submitted_at")
        row_dict["submitted_at_display"] = (
            submitted.strftime("%Y-%m-%d %H:%M")
            if submitted else ""
        )
        out.append(row_dict)

    return out


def pg_list_instructor_grades_for_quiz(instructor_id: str, quiz_id: str, limit: int = 5000):
    """
    Lists instructor grade records for a quiz.

    CHANGED:
    - If quiz.grade_method = 'highest', show each student's highest score.
    - If quiz.grade_method = 'latest', show each student's latest submitted attempt.
    - Returns only one displayed grade row per student for the selected quiz.
    """
    with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            WITH ranked_attempts AS (
                SELECT
                  qa.attempt_id,
                  qa.attempt_id AS id,
                  qa.user_id,
                  u.full_name AS student_name,
                  u.email AS email,
                  qa.quiz_title,
                  qa.score,
                  qa.total_points,
                  qa.submitted_at,
                  COALESCE(q.grade_method, 'highest') AS grade_method,

                  ROW_NUMBER() OVER (
                    PARTITION BY qa.user_id, qa.quiz_id
                    ORDER BY
                      CASE
                        WHEN COALESCE(q.grade_method, 'highest') = 'highest'
                        THEN qa.score
                        ELSE NULL
                      END DESC,
                      qa.submitted_at DESC
                  ) AS rn

                FROM quiz_attempts qa
                JOIN users u
                  ON u.id = qa.user_id
                JOIN quizzes q
                  ON q.id = qa.quiz_id::uuid
                JOIN class_instructors ci
                  ON ci.class_id = q.class_id

                WHERE ci.instructor_id = %s
                  AND q.id = %s::uuid
                  AND qa.submitted_at IS NOT NULL
            )

            SELECT
              attempt_id,
              id,
              student_name,
              email,
              quiz_title,
              score,
              total_points,
              submitted_at,
              grade_method
            FROM ranked_attempts
            WHERE rn = 1
            ORDER BY student_name ASC
            LIMIT %s;
            """,
            (str(instructor_id), str(quiz_id), int(limit)),
        )
        return cur.fetchall() or []


# ============================================================
# FIREBASE EMBEDDING HELPERS
# MODULE: face recognition
# Stores and reads encrypted multi-sample face embeddings. Do not alter thresholds here.
# ============================================================
def fb_set_embedding_enc(firebase_uid, emb_list_128, allow_update=True):  #CHANGED
    """
    CHANGED: Saves a single embedding into embeddings_enc_list by appending it
    to the existing list in Firebase. If no list exists yet, creates a new one.
    Each entry in embeddings_enc_list is an independently encrypted 128D embedding.
    """
    logger = logging.getLogger("classiface")
    try:
        logger.info(f"Encrypting embedding for {_mask_uid(firebase_uid)}")

        node = db.reference("Embeddings").child(str(firebase_uid)).get()
        existing_list = []
        if node and isinstance(node, dict):
            raw_list = node.get("embeddings_enc_list", [])
            if isinstance(raw_list, list):
                existing_list = raw_list

        #CHANGED: Prevent embedding poisoning during quiz
        if not allow_update:
            logger.warning(f"Embedding update blocked for {_mask_uid(firebase_uid)} (quiz mode)")
            return

        #CHANGED: Encrypt only when update is allowed
        encrypted_emb = encrypt_embedding(emb_list_128)
        logger.debug(f"Encrypted embedding keys: {list(encrypted_emb.keys()) if isinstance(encrypted_emb, dict) else 'N/A'}")

        existing_list.append(encrypted_emb)

        db.reference("Embeddings").child(str(firebase_uid)).set(
            {
                "embeddings_enc_list": existing_list,
                "updatedAt": datetime.utcnow().isoformat() + "Z",
            }
        )
        logger.debug("Firebase write completed")

    except Exception as e:
        logger.error(f"Error in fb_set_embedding_enc: {type(e).__name__}: {str(e)}")
        raise


def fb_set_embedding_enc_list(firebase_uid: str, emb_lists: list):
    """
    CHANGED: Saves a full list of embeddings at once to Firebase under embeddings_enc_list.
    emb_lists is a list of 128D float lists. Each is encrypted individually.
    Overwrites any previously stored embeddings for this user.
    """
    logger = logging.getLogger("classiface")
    try:
        logger.info(f"Encrypting {len(emb_lists)} embedding(s) for {_mask_uid(firebase_uid)}")
        encrypted_list = []
        for emb in emb_lists:
            encrypted_list.append(encrypt_embedding(emb))

        logger.debug(f"Writing {len(encrypted_list)} embedding(s) to Firebase for {_mask_uid(firebase_uid)}")
        db.reference("Embeddings").child(str(firebase_uid)).set(
            {
                "embeddings_enc_list": encrypted_list,
                "updatedAt": datetime.utcnow().isoformat() + "Z",
            }
        )
        logger.info(f"Firebase write completed ({len(encrypted_list)} embeddings stored)")
    except Exception as e:
        logger.error(f"Error in fb_set_embedding_enc_list: {type(e).__name__}: {str(e)}", exc_info=True)
        raise


def fb_get_embedding_enc(firebase_uid: str):
    """
    CHANGED: Reads embeddings_enc_list from Firebase and returns a list of
    valid encrypted embedding dicts. Returns empty list if none found.
    For backward compatibility, also checks for legacy embeddings_enc single entry.
    """
    logger = logging.getLogger("classiface")
    logger.debug(f"Attempting to retrieve embeddings for uid: {_mask_uid(firebase_uid)}")
    node = db.reference("Embeddings").child(str(firebase_uid)).get()
    logger.debug(f"Firebase node retrieved: {node is not None}")
    if not node or not isinstance(node, dict):
        logger.debug(f"Node is None or not a dict, returning empty list")
        return []

    # CHANGED: Read from embeddings_enc_list first
    enc_list = node.get("embeddings_enc_list")
    if isinstance(enc_list, list) and len(enc_list) > 0:
        valid = []
        for enc in enc_list:
            if isinstance(enc, dict) and "ct" in enc and "nonce" in enc:
                valid.append(enc)
        if valid:
            print(f"   ✅ Found {len(valid)} valid encrypted embedding(s) in embeddings_enc_list", flush=True)
            return valid

    # CHANGED: Fallback — check for legacy single embedding under embeddings_enc
    enc_legacy = node.get("embeddings_enc")
    if isinstance(enc_legacy, dict) and "ct" in enc_legacy and "nonce" in enc_legacy:
        print(f"   ⚠️  Falling back to legacy embeddings_enc (single embedding)", flush=True)
        return [enc_legacy]

    print(f"   ❌ No valid embeddings found", flush=True)
    return []


# ============================================================
# FIREBASE EMBEDDING CACHE
# Temporarily caches decrypted 128D embeddings in backend memory.
# This reduces repeated Firebase reads/decryption during monitoring.
# It does NOT change embeddings, cosine distance, thresholds, or matching logic.
# ============================================================
EMBEDDING_CACHE = {}
EMBEDDING_CACHE_TTL_SECONDS = 300


def clear_embedding_cache(firebase_uid: str = None):
    try:
        if firebase_uid:
            EMBEDDING_CACHE.pop(str(firebase_uid), None)
        else:
            EMBEDDING_CACHE.clear()
    except Exception as e:
        print(f"WARNING clear_embedding_cache error: {e}", flush=True)


def fb_get_decrypted_embeddings_cached(firebase_uid: str):
    uid = str(firebase_uid or "").strip()
    if not uid:
        return []

    now = time.time()
    cached = EMBEDDING_CACHE.get(uid)
    if cached and now - cached.get("cached_at", 0) < EMBEDDING_CACHE_TTL_SECONDS:
        return cached.get("embeddings", [])

    enc_list = fb_get_embedding_enc(uid)
    decrypted_embeddings = []

    for enc in enc_list:
        try:
            stored_emb = decrypt_embedding(enc)
            if isinstance(stored_emb, list) and len(stored_emb) == 128:
                decrypted_embeddings.append(stored_emb)
        except Exception as e:
            logger = logging.getLogger("classiface")
            logger.warning(f"Failed to decrypt embedding for {_mask_uid(uid)}: {type(e).__name__}")

    EMBEDDING_CACHE[uid] = {
        "cached_at": now,
        "embeddings": decrypted_embeddings,
    }

    logger = logging.getLogger("classiface")
    logger.debug(f"Cached {len(decrypted_embeddings)} decrypted embedding(s) for {_mask_uid(uid)}")

    return decrypted_embeddings


def fb_get_best_embedding_match(firebase_uid: str, live_emb_list: list):
    """
    CHANGED: Retrieves all stored embeddings for a user and returns the best
    (lowest cosine distance) match against the live embedding.

    Performance improvement:
      - Uses the temporary decrypted embedding cache to avoid repeated
        Firebase reads and decrypt operations during continuous monitoring.

    IMPORTANT:
      - Does NOT change cosine distance logic.
      - Does NOT change thresholds.
      - Does NOT change embedding generation.
    """
    stored_embeddings = fb_get_decrypted_embeddings_cached(firebase_uid)
    if not stored_embeddings:
        return 999.0, False

    best_distance = 999.0
    for stored_emb in stored_embeddings:
        try:
            dist = _cosine_distance(live_emb_list, stored_emb)
            if dist < best_distance:
                best_distance = dist
        except Exception:
            continue

    return best_distance, True


def build_database_from_pg_and_firebase(limit_users: int = 500):
    """
    Builds { full_name: [[128d embedding], [128d embedding], ...] } using:
      - users.firebase_uid to read Firebase embeddings
      - users.full_name as display key
    CHANGED: Each user now maps to a LIST of embeddings instead of a single embedding.
    """
    database = {}
    users = pg_list_users(limit=limit_users)
    for u in users:
        firebase_uid = (u.get("firebase_uid") or "").strip()
        name = (u.get("full_name") or "").strip()
        if not firebase_uid or not name:
            continue

        user_embeddings = fb_get_decrypted_embeddings_cached(firebase_uid)
        if user_embeddings:
            database[name] = user_embeddings

    return database


# ============================================================
# CAMERA AND LIVENESS STATE
# MODULE: face recognition
# Runtime camera/liveness state. Face matching thresholds are intentionally untouched.
# ============================================================
video = None
video_last_used_ts = 0.0
video_active_clients = 0
CAMERA_IDLE_SECONDS = 8

is_liveness_running = False

# CHANGED: per-session liveness overlay state
LIVENESS_STATE = {}  # CHANGED
DEFAULT_LIVE_INSTRUCTION = "Open Camera → Capture"  # CHANGED
DEFAULT_LIVE_SUBTEXT = ""  # CHANGED

# CHANGED: per-session pending embeddings store now holds a LIST of embeddings per ts_key
# key: ts string, value: {"embs": [[128], [128], ...], "created_at": unix_ts}
# CHANGED: pending embeddings are now stored in PostgreSQL instead of RAM
PENDING_EMB_TTL_SECONDS = 15 * 60  # 15 minutes

# CHANGED: How many captures to collect during registration (multi-sample dataset)
REGISTRATION_SAMPLE_COUNT = 5


def _get_stream_key():  # CHANGED
    key = session.get("stream_key")
    if not key:
        key = session.get("ts")
    if not key:
        key = session.get("attempt_id")
    if not key:
        key = session.get("user_id")
    if not key:
        key = session.get("firebase_uid")
    if not key:
        key = str(uuid.uuid4())
        session["stream_key"] = key
    return str(key)


def _ensure_liveness_state(stream_key: str):  # CHANGED
    if stream_key not in LIVENESS_STATE:
        LIVENESS_STATE[stream_key] = {
            "live_instruction": DEFAULT_LIVE_INSTRUCTION,
            "live_subtext": DEFAULT_LIVE_SUBTEXT,
            "liveness_preview_frame": None,
        }
    return LIVENESS_STATE[stream_key]


def _reset_liveness_state(stream_key: str):  # CHANGED
    state = _ensure_liveness_state(stream_key)
    state["live_instruction"] = "Look at the camera"
    state["live_subtext"] = "Follow the on-screen instructions"
    state["liveness_preview_frame"] = None
    return state


def _clear_liveness_state(stream_key: str):  # CHANGED
    try:
        LIVENESS_STATE.pop(str(stream_key), None)
    except Exception:
        pass


def pg_ensure_pending_embeddings_table():
    """
    CHANGED: Ensures temporary pending_embeddings table exists.
    Stores embeddings only (not images) for short-lived registration flow.
    """
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_embeddings (
                    id uuid PRIMARY KEY,
                    ts_key text NOT NULL,
                    embedding jsonb NOT NULL,
                    created_at timestamp NOT NULL DEFAULT now(),
                    expires_at timestamp NOT NULL
                );
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_embeddings_ts_key
                ON pending_embeddings(ts_key);
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_embeddings_expires_at
                ON pending_embeddings(expires_at);
                """
            )
            conn.commit()
            print("✅ pg_ensure_pending_embeddings_table: schema verified", flush=True)
    except Exception as e:
        print(f"❌ pg_ensure_pending_embeddings_table error: {str(e)}", flush=True)


# CHANGED: run once at startup
try:
    pg_ensure_pending_embeddings_table()
except Exception as _pe_err:
    print(f"WARN pg_ensure_pending_embeddings_table at startup: {_pe_err}", flush=True)


def _pending_store_cleanup():
    """
    CHANGED: Deletes expired pending embeddings from PostgreSQL.
    """
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM pending_embeddings
                WHERE expires_at <= now();
                """
            )
            conn.commit()
    except Exception as e:
        print(f"❌ _pending_store_cleanup error: {str(e)}", flush=True)


def _pending_store_put(ts_key: str, emb_list_128: list):
    """
    CHANGED: Stores one 128D embedding as one row in PostgreSQL.
    Same logical behaviour as before: each capture adds one more pending sample.
    """
    _pending_store_cleanup()
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pending_embeddings (id, ts_key, embedding, expires_at)
                VALUES (%s, %s, %s, now() + (%s || ' seconds')::interval);
                """,
                (
                    str(uuid.uuid4()),
                    str(ts_key),
                    psycopg2.extras.Json(emb_list_128),
                    int(PENDING_EMB_TTL_SECONDS),
                ),
            )
            conn.commit()
    except Exception as e:
        print(f"❌ _pending_store_put error: {str(e)}", flush=True)
        raise


def _pending_store_get(ts_key: str):
    """
    CHANGED: Returns the list of pending embeddings for ts_key, or None if missing.
    """
    _pending_store_cleanup()
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT embedding
                FROM pending_embeddings
                WHERE ts_key = %s
                  AND expires_at > now()
                ORDER BY created_at ASC;
                """,
                (str(ts_key),),
            )
            rows = cur.fetchall() or []
            if not rows:
                return None
            return [row["embedding"] for row in rows]
    except Exception as e:
        print(f"❌ _pending_store_get error: {str(e)}", flush=True)
        return None


def _pending_store_get_count(ts_key: str) -> int:
    """
    CHANGED: Returns how many pending embeddings have been collected for ts_key.
    """
    _pending_store_cleanup()
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM pending_embeddings
                WHERE ts_key = %s
                  AND expires_at > now();
                """,
                (str(ts_key),),
            )
            row = cur.fetchone()
            return int(row["cnt"]) if row and row.get("cnt") is not None else 0
    except Exception as e:
        print(f"❌ _pending_store_get_count error: {str(e)}", flush=True)
        return 0


def _pending_store_pop(ts_key: str):
    """
    CHANGED: Returns all pending embeddings for ts_key, then deletes them.
    Keeps the same behaviour as the old in-memory pop().
    """
    _pending_store_cleanup()
    try:
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT embedding
                FROM pending_embeddings
                WHERE ts_key = %s
                  AND expires_at > now()
                ORDER BY created_at ASC;
                """,
                (str(ts_key),),
            )
            rows = cur.fetchall() or []
            if not rows:
                return None

            embs = [row["embedding"] for row in rows]

            cur.execute(
                """
                DELETE FROM pending_embeddings
                WHERE ts_key = %s;
                """,
                (str(ts_key),),
            )
            conn.commit()
            return embs
    except Exception as e:
        print(f"❌ _pending_store_pop error: {str(e)}", flush=True)
        return None


# -----------------------------
# Liveness settings
# -----------------------------
EAR_THRESHOLD = 0.23 # CHANGED
BLINK_CONSEC_FRAMES = 2 # CHANGED
LIVENESS_MAX_FRAMES = 180 # CHANGED
YAW_DELTA_REQUIRED = 0.06

BLINK_COUNT_CHOICES = [1, 2] # CHANGED: Reduced to 1 or 2 blinks for faster liveness completion
TURN_TIMEOUT = 10.0 # CHANGED 5.0


# -----------------------------
# Face box filtering helpers
# -----------------------------
def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh

    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)

    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0

    inter = (ix2 - ix1) * (iy2 - iy1)
    union = (aw * ah) + (bw * bh) - inter
    return inter / union if union > 0 else 0.0


def filter_faces(faces, img_shape, min_area_ratio=0.02, iou_thresh=0.45):
    if faces is None or len(faces) == 0:
        return []

    H, W = img_shape[:2]
    img_area = float(H * W)

    cleaned = []
    for (x, y, w, h) in faces:
        if (w * h) / img_area >= min_area_ratio:
            cleaned.append((int(x), int(y), int(w), int(h)))

    if not cleaned:
        return []

    cleaned.sort(key=lambda f: f[2] * f[3], reverse=True)

    kept = []
    for f in cleaned:
        if all(_iou(f, k) < iou_thresh for k in kept):
            kept.append(f)

    return kept


def pick_single_face(faces, frame):
    faces = filter_faces(faces, frame.shape)

    if len(faces) == 0:
        return None, "No face detected"
    if len(faces) == 1:
        return faces[0], None

    faces_sorted = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    a0 = faces_sorted[0][2] * faces_sorted[0][3]
    a1 = faces_sorted[1][2] * faces_sorted[1][3]

    if a0 >= 2.5 * a1:
        return faces_sorted[0], None

    return None, "Multiple faces detected - please stand alone"


def _delete_preview_file():
    try:
        preview_path = os.path.join(RECOG_FOLDER, "recognized.png")
        if os.path.exists(preview_path):
            os.remove(preview_path)
    except Exception:
        pass


def decode_browser_frame(frame_data: str):
    """Decode a browser data URL into an OpenCV BGR frame."""
    if not frame_data:
        return None, "No camera frame received."
    if "," in frame_data:
        frame_data = frame_data.split(",", 1)[1]
    try:
        raw = base64.b64decode(frame_data, validate=True)
        arr = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None, "Invalid camera frame."
    if frame is None or frame.size == 0:
        return None, "Invalid camera frame."
    return frame, None


def prepare_face_crop_from_frame(frame, pad_ratio=0.20):
    """Validate a submitted frame and return a padded single-face crop."""
    faces_raw = detect_faces(frame)
    face_box, err = pick_single_face(faces_raw, frame)
    if err or face_box is None:
        return None, None, err or "No usable face detected."

    x, y, w, h = face_box
    face_area = w * h
    frame_area = frame.shape[0] * frame.shape[1]
    face_ratio = face_area / frame_area if frame_area > 0 else 0.0
    if face_ratio < 0.04:
        return None, None, "Face too small. Please move closer."

    pad_x = int(w * pad_ratio)
    pad_y = int(h * pad_ratio)
    x1 = max(0, x - pad_x)
    y1 = max(0, y - pad_y)
    x2 = min(frame.shape[1], x + w + pad_x)
    y2 = min(frame.shape[0], y + h + pad_y)

    face_crop = frame[y1:y2, x1:x2]
    if face_crop is None or face_crop.size == 0:
        return None, None, "Invalid face crop. Please try again."
    if face_crop.shape[0] < 40 or face_crop.shape[1] < 40:
        return None, None, "Face too small. Please move closer."

    gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
    sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
    if sharpness < 50:
        return None, None, "Image is blurry. Please hold still and try again."
    brightness = float(np.mean(gray))
    if brightness < 35:
        return None, None, "Face is too dark. Please improve lighting."

    return face_crop, face_box, None


def _face_distance(a, b):  #CHANGED
    a = np.asarray(a, dtype=np.float32).reshape(-1)  #CHANGED
    b = np.asarray(b, dtype=np.float32).reshape(-1)  #CHANGED
    if a.size != b.size:  #CHANGED
        return 999.0  #CHANGED
    return float(np.linalg.norm(a - b))  #CHANGED


def _best_distance_against_embeddings(live_emb: list, stored_embs: list) -> float:  #CHANGED
    """
    CHANGED: Computes the Euclidean face distance between live_emb and each embedding
    in stored_embs, returning the best (lowest) distance found.
    stored_embs is a list of 128D float lists.
    """  #CHANGED
    best = 999.0  #CHANGED
    for stored in stored_embs:  #CHANGED
        try:  #CHANGED
            dist = _face_distance(live_emb, stored)  #CHANGED
            if dist < best:  #CHANGED
                best = dist  #CHANGED
        except Exception:  #CHANGED
            continue  #CHANGED
    return best  #CHANGED


# -----------------------------
# Camera helpers
# -----------------------------
def _flush_camera(cap, n=10):
    for _ in range(n):
        cap.read()


def _init_camera():
    global video, video_last_used_ts
    if video is None:
        if os.name == "nt":
            video = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        else:
            video = cv2.VideoCapture(0)
        video.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    video_last_used_ts = time.time()
    return video


def _release_camera_if_idle(force=False):
    global video, video_last_used_ts, video_active_clients

    if video is None:
        return

    idle_for = time.time() - video_last_used_ts

    if force or (video_active_clients <= 0 and idle_for >= CAMERA_IDLE_SECONDS and not is_liveness_running):
        try:
            video.release()
        except Exception:
            pass
        video = None


# -----------------------------
# Liveness
# -----------------------------
def _new_challenge():
    direction = secrets.choice(["LEFT", "RIGHT"])
    blinks_required = secrets.choice(BLINK_COUNT_CHOICES)

    session["challenge_direction"] = direction
    session["challenge_blinks"] = blinks_required
    session["challenge_text"] = f"Blink {blinks_required} time(s) and turn your head {direction}"  # CHANGED
    return direction, blinks_required


def _direction_yaw_reached(direction_required: str, yaw_base: float, yaw_now: float) -> bool:
    if yaw_base is None or yaw_now is None:
        return False

    delta = yaw_now - yaw_base
    if direction_required == "LEFT":
        return delta <= -YAW_DELTA_REQUIRED
    if direction_required == "RIGHT":
        return delta >= YAW_DELTA_REQUIRED
    return False


def pass_liveness_from_camera(video_cap, direction_required: str, blinks_required: int, stream_key: str):  # CHANGED
    state = _ensure_liveness_state(stream_key)

    blinks = 0
    closed_frames = 0
    yaw_base = None
    yaw_reached = False

    last_frame = None
    last_faces = None

    stage = 0  # CHANGED
    stage_t0 = time.time()
    prep_seconds = 1.5  # CHANGED

    frame_skip = 2  # CHANGED
    frame_idx = 0   # CHANGED
    cached_faces = None  # CHANGED

    state["live_instruction"] = "Get ready"  # CHANGED
    state["live_subtext"] = f"Blink {blinks_required} time(s), then turn {direction_required}"  # CHANGED

    for _ in range(LIVENESS_MAX_FRAMES):
        ret, frame = video_cap.read()
        if not ret:
            state["live_instruction"] = "Camera not ready."  # CHANGED
            state["live_subtext"] = "Please wait"  # CHANGED
            continue

        frame = cv2.resize(frame, (640, 480))
        frame_idx += 1  # CHANGED

        if frame_idx % frame_skip != 0:  # CHANGED
            continue

        if frame_idx % 3 == 0 or cached_faces is None:  # CHANGED
            cached_faces = detect_faces(frame)

        faces_raw = cached_faces if cached_faces is not None else []  # CHANGED
        last_faces = faces_raw

        face_box, err = pick_single_face(faces_raw, frame)

        # CHANGED: draw face box
        if face_box is not None:
            x, y, w, h = face_box
            color = (0, 255, 0) if err is None else (0, 0, 255)
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)

        if err == "No face detected":
            state["live_instruction"] = "No face detected"  # CHANGED
            state["live_subtext"] = "Move closer + face the camera"  # CHANGED

            cv2.rectangle(frame, (0, 0), (frame.shape[1], 80), (0, 0, 0), -1)
            cv2.putText(
                frame, state["live_instruction"], (12, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2
            )
            cv2.putText(
                frame, state["live_subtext"], (12, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 2
            )

            state["liveness_preview_frame"] = frame.copy()
            last_frame = frame.copy()
            continue

        if err is not None:
            state["live_instruction"] = "Multiple faces detected"  # CHANGED
            state["live_subtext"] = "Only ONE person in frame"  # CHANGED

            cv2.rectangle(frame, (0, 0), (frame.shape[1], 80), (0, 0, 0), -1)
            cv2.putText(
                frame, state["live_instruction"], (12, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2
            )
            cv2.putText(
                frame, state["live_subtext"], (12, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 2
            )

            state["liveness_preview_frame"] = frame.copy()
            last_frame = frame.copy()
            return False, frame, err

        yaw = yaw_ratio_from_face(frame, face_box)
        if yaw is not None and yaw_base is None:
            yaw_base = yaw

        ear = get_ear_from_face(frame, face_box)
        if ear is None:
            state["live_instruction"] = "Hold still"  # CHANGED
            state["live_subtext"] = "Trying to read eyes..."  # CHANGED

            cv2.rectangle(frame, (0, 0), (frame.shape[1], 80), (0, 0, 0), -1)
            cv2.putText(
                frame, state["live_instruction"], (12, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2
            )
            cv2.putText(
                frame, state["live_subtext"], (12, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 2
            )

            state["liveness_preview_frame"] = frame.copy()
            last_frame = frame.copy()
            continue

        now = time.time()

        if stage == 0:  # CHANGED
            state["live_instruction"] = "Get ready"  # CHANGED
            state["live_subtext"] = f"Blink {blinks_required} time(s), then turn {direction_required}"  # CHANGED

            if now - stage_t0 >= prep_seconds:  # CHANGED
                stage = 1  # CHANGED
                stage_t0 = now  # CHANGED

        elif stage == 1:  # CHANGED
            if ear < EAR_THRESHOLD:
                closed_frames += 1
            else:
                if closed_frames >= BLINK_CONSEC_FRAMES:  # CHANGED
                    blinks += 1
                closed_frames = 0

            if blinks < blinks_required:
                remaining = blinks_required - blinks
                state["live_instruction"] = "Blink slowly"  # CHANGED
                state["live_subtext"] = f"{remaining} more blink(s) needed"  # CHANGED
            else:
                stage = 2
                stage_t0 = now
                state["live_instruction"] = f"Turn your head {direction_required}"  # CHANGED
                state["live_subtext"] = "Turn slowly and hold briefly"  # CHANGED

        elif stage == 2:  # CHANGED
            if TURN_TIMEOUT is not None and (now - stage_t0 > TURN_TIMEOUT):
                return False, last_frame, "Head turn timeout"

            if yaw is not None and yaw_base is not None:
                if _direction_yaw_reached(direction_required, yaw_base, yaw):
                    yaw_reached = True

            if not yaw_reached:
                state["live_instruction"] = f"Turn your head {direction_required}"  # CHANGED
                state["live_subtext"] = "Turn slowly and hold briefly"  # CHANGED
            else:
                state["live_instruction"] = "Liveness confirmed"  # CHANGED
                state["live_subtext"] = "Capturing..."  # CHANGED

                cv2.rectangle(frame, (0, 0), (frame.shape[1], 80), (0, 0, 0), -1)
                cv2.putText(
                    frame, state["live_instruction"], (12, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2
                )
                cv2.putText(
                    frame, state["live_subtext"], (12, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 2
                )

                state["liveness_preview_frame"] = frame.copy()
                last_frame = frame.copy()
                return True, frame, "Liveness passed"

        cv2.rectangle(frame, (0, 0), (frame.shape[1], 80), (0, 0, 0), -1)
        cv2.putText(
            frame, state["live_instruction"], (12, 30),
            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2
        )
        cv2.putText(
            frame, state["live_subtext"], (12, 60),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 2
        )

        state["liveness_preview_frame"] = frame.copy()
        last_frame = frame.copy()

    if last_frame is None:
        return False, None, "Camera not available"
    if last_faces is None or len(last_faces) == 0:
        return False, last_frame, "No face detected"
    if blinks < blinks_required:
        return False, last_frame, f"Need {blinks_required} blinks"
    if not yaw_reached:
        return False, last_frame, "Head turn not detected"
    return False, last_frame, "Liveness failed"

# -----------------------------
# Instructor helper validation
# -----------------------------
def _require_instructor_class_or_redirect(class_id: str):
    instructor_id = str(session.get("user_id") or "")
    if not class_id:
        return None, redirect_with_msg("/class-lists", "Please select a class first.")
    if not pg_instructor_in_class(instructor_id, class_id):
        return None, redirect_with_msg("/class-lists", "You are not assigned to this class.")
    cmeta = pg_get_class_by_id(class_id)
    if not cmeta:
        return None, redirect_with_msg("/class-lists", "Class not found.")
    session["active_class_id"] = str(cmeta["id"])
    session["active_class_name"] = f"{cmeta['section_name']} ({cmeta['class_code']})"
    return cmeta, None


def _normalize_quiz_questions(raw_questions):
    if not isinstance(raw_questions, list):
        return None, "questions must be a list"

    out = []
    for idx, q in enumerate(raw_questions, start=1):
        if not isinstance(q, dict):
            return None, f"Question {idx} is invalid"

        qtype = str(q.get("type") or "").strip()
        text = str(q.get("text") or "").strip()
        points = _safe_int(q.get("points"), 10)
        qid = str(q.get("id") or uuid.uuid4())

        if not qtype:
            return None, f"Question {idx}: type is required"
        if not text:
            return None, f"Question {idx}: text is required"
        if points <= 0:
            return None, f"Question {idx}: points must be greater than 0"

        item = {
            "id": qid,
            "type": qtype,
            "text": text,
            "points": points,
        }

        if qtype == "multiple-choice":
            choices = q.get("choices") or []
            correct = q.get("correctAnswers") or []

            if not isinstance(choices, list) or len(choices) < 2:
                return None, f"Question {idx}: multiple-choice needs at least 2 choices"

            clean_choices = []
            for cidx, c in enumerate(choices, start=1):
                ctext = str((c or {}).get("text") if isinstance(c, dict) else c).strip()
                if not ctext:
                    return None, f"Question {idx}: choice {cidx} cannot be blank"
                clean_choices.append({"text": ctext})

            if not isinstance(correct, list) or len(correct) == 0:
                return None, f"Question {idx}: mark at least one correct choice"

            item["choices"] = clean_choices
            item["correctAnswers"] = [_safe_int(x, -1) for x in correct]

        elif qtype == "true-false":
            ca = str(q.get("correctAnswer") or "").strip().lower()
            if ca not in ("true", "false"):
                return None, f"Question {idx}: true-false answer must be true or false"
            item["correctAnswer"] = ca

        elif qtype == "matching":
            left_items = q.get("leftItems") or []
            right_items = q.get("rightItems") or []
            correct = q.get("correctAnswers") or []

            if not isinstance(left_items, list) or not isinstance(right_items, list) or not isinstance(correct, list):
                return None, f"Question {idx}: matching format is invalid"
            if len(left_items) == 0 or len(right_items) == 0:
                return None, f"Question {idx}: matching needs left and right items"

            item["leftItems"] = [str(x).strip() for x in left_items if str(x).strip()]
            item["rightItems"] = [str(x).strip() for x in right_items if str(x).strip()]
            item["correctAnswers"] = [_safe_int(x, 0) for x in correct]

        elif qtype == "numerical":
            try:
                item["correctAnswer"] = float(q.get("correctAnswer"))
            except Exception:
                return None, f"Question {idx}: numerical correctAnswer is invalid"
            try:
                item["tolerance"] = float(q.get("tolerance", 0.0))
            except Exception:
                item["tolerance"] = 0.0

        elif qtype == "identification":
            ca = str(q.get("correctAnswer") or "").strip()
            if not ca:
                return None, f"Question {idx}: identification answer is required"
            item["correctAnswer"] = ca
            item["caseInsensitive"] = bool(q.get("caseInsensitive", True))

        elif qtype == "image-answer":
            # Image answer questions only need question text and points
            # Students will upload an image, and professor grades manually
            item["requiresManualGrading"] = True

        else:
            return None, f"Question {idx}: unsupported type '{qtype}'"

        image_data = q.get("image")
        if isinstance(image_data, dict):
            image_name = str(image_data.get("name") or "").strip()
            image_value = str(image_data.get("data") or "").strip()
            if image_name and image_value:
                item["image"] = {
                    "name": image_name,
                    "type": str(image_data.get("type") or "image"),
                    "data": image_value,
                }

        out.append(item)

    return out, None

def pg_get_today_session(class_id: str):
    """
    Wrapper for compatibility with older code paths that still call
    pg_get_today_session().
    """
    return pg_get_active_session_for_date(class_id, app_today())


# ============================================================
# CONTROLLER SUPPORT HELPERS
# ============================================================
def _build_quiz_cards_for_class(class_id: str, quiz_available: bool = False, quiz_status: str = "no_session", is_instructor: bool = False, student_id: str = None):
    quizzes = []
    db_quizzes = pg_list_quizzes_for_class(str(class_id), limit=200)

    for q in db_quizzes:
        qjson = q.get("questions_json") or {}
        qlist = qjson.get("questions") if isinstance(qjson, dict) else []
        qcount = len(qlist) if isinstance(qlist, list) else 0

        created_at = q.get("created_at")
        created_display = created_at.strftime("%Y-%m-%d %H:%M") if created_at else "-"

        is_quiz_active = bool(q.get("is_active", False))

        if is_instructor:
            final_available = is_quiz_active
            final_status    = "available" if is_quiz_active else "unavailable"
        else:
            has_session = quiz_status != "no_session"
            final_available = is_quiz_active and has_session and quiz_available
            if not is_quiz_active:
                final_status = "unavailable"
            elif not has_session:
                final_status = "no_session"
            elif not quiz_available:
                final_status = "closed"
            else:
                final_status = "available"

        # Calculate remaining attempts for student
        attempts_remaining = None
        attempts_display = "Unlimited attempts"
        attempts_type = q.get("attempts_type", "unlimited")
        attempts_limit = q.get("attempts_limit")
        
        print(f"DEBUG Quiz {q.get('title')}: type={attempts_type}, limit={attempts_limit}, student_id={student_id}, is_instructor={is_instructor}", flush=True)
        
        if student_id and student_id.strip() and not is_instructor and attempts_type == "limited" and attempts_limit and attempts_limit > 0:
            print(f"DEBUG Calculating attempts for quiz {q.get('title')}", flush=True)
            try:
                with pg_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COUNT(*) as attempt_count
                        FROM quiz_attempts
                        WHERE user_id = %s AND quiz_id = %s AND submitted_at IS NOT NULL;
                        """,
                        (str(student_id), str(q.get("id"))),
                    )
                    result = cur.fetchone()
                    attempt_count = result["attempt_count"] if result else 0
                    attempts_remaining = max(0, attempts_limit - attempt_count)
                    attempts_display = f"{attempts_remaining} attempt{'s' if attempts_remaining != 1 else ''} left"
                    print(f"DEBUG Quiz {q.get('title')}: attempt_count={attempt_count}, attempts_limit={attempts_limit}, remaining={attempts_remaining}", flush=True)
            except Exception as e:
                print(f"⚠️  Error calculating attempts: {e}", flush=True)
                attempts_display = "Unlimited attempts"
        else:
            student_id_str = student_id.strip() if student_id else "None"
            print(f"DEBUG Condition failed: student_id.strip()={student_id_str}, is_instructor={is_instructor}, attempts_type={attempts_type}, attempts_limit={attempts_limit}", flush=True)

        quizzes.append(
            {
                "id": str(q.get("id")),
                "title": q.get("title") or "",
                "description": q.get("description") or "",
                "total_score": int(q.get("total_points") or 100),
                "question_count": qcount,
                "created_at": created_display,
                "is_active": is_quiz_active,
                "is_available": final_available,
                "availability_status": final_status,
                "attempts_display": attempts_display,
                "attempts_remaining": attempts_remaining,
            }
        )
    return quizzes
# ============================================================
# SOCKET EMIT HELPERS
# ============================================================
def _student_attempt_room(attempt_id: str) -> str:  # CHANGED
    return f"attempt_{str(attempt_id)}"  # CHANGED

def _instructor_quiz_room(class_id: str, quiz_id: str) -> str:  # CHANGED
    return f"class_{str(class_id)}_quiz_{str(quiz_id)}"  # CHANGED

def _emit_student_blackout_on(attempt_id: str, payload: dict):  # CHANGED
    socketio.emit("blackout_on", payload, room=_student_attempt_room(attempt_id))

def _emit_student_blackout_off(attempt_id: str, payload: dict):  # CHANGED
    socketio.emit("blackout_off", payload, room=_student_attempt_room(attempt_id))

def _emit_student_warning(attempt_id: str, payload: dict):  # CHANGED
    socketio.emit("warning", payload, room=_student_attempt_room(attempt_id))

def _emit_instructor_violation_alert(class_id: str, quiz_id: str, payload: dict):  # CHANGED
    socketio.emit("violation_alert", payload, room=_instructor_quiz_room(class_id, quiz_id))

# ============================================================
# VIDEO STREAMING
# ============================================================
def gen_frames(stream_key):  # CHANGED
    global is_liveness_running
    global video_active_clients, video_last_used_ts

    state = _ensure_liveness_state(str(stream_key))

    cap = _init_camera()
    video_active_clients += 1

    try:
        while True:
            video_last_used_ts = time.time()

            if is_liveness_running:
                frame = state.get("liveness_preview_frame")
                if frame is None:
                    time.sleep(0.03)
                    continue
            else:
                success, frame = cap.read()
                if not success:
                    time.sleep(0.03)
                    continue

            cv2.rectangle(frame, (10, 10), (630, 75), (0, 0, 0), -1)
            cv2.putText(
                frame,
                state.get("live_instruction") or "",  # CHANGED
                (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                state.get("live_subtext") or "",  # CHANGED
                (20, 65),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

            ret, buffer = cv2.imencode(".jpg", frame)
            if not ret:
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
            )

    except GeneratorExit:
        pass
    finally:
        video_active_clients -= 1
        _release_camera_if_idle()


# ============================================================
# REST API RESPONSE HELPERS
# ============================================================
def _api_json_safe(value):
    """Convert PostgreSQL/Firebase values into JSON-safe values."""
    try:
        import decimal
        import uuid as uuid_module
    except Exception:
        decimal = None
        uuid_module = None

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()

    if decimal is not None and isinstance(value, decimal.Decimal):
        return float(value)

    if uuid_module is not None and isinstance(value, uuid_module.UUID):
        return str(value)

    if isinstance(value, dict):
        return {str(k): _api_json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_api_json_safe(v) for v in value]

    return str(value)

def _api_success(data=None, message="OK", status=200):
    payload = {
        "success": True,
        "message": message,
        "data": _api_json_safe(data),
    }
    return jsonify(payload), status


def _api_error(message="Error", status=400):
    return jsonify({"success": False, "message": message}), status


def _api_require_admin():
    if not session.get("logged_in") or session.get("role") != "admin":
        return _api_error("Admin access only. Please log in as admin.", 403)
    return None


def _api_require_instructor_or_admin():
    if not session.get("logged_in") or session.get("role") not in ("instructor", "admin"):
        return _api_error("Instructor or admin access only.", 403)
    return None


# ============================================================
# PASSWORD STRENGTH CHECK API
# ============================================================
@app.route("/api/password/check-strength", methods=["POST"])
def api_check_password_strength():
    """
    Check password strength and return validation status.
    
    Request body: {"password": "password_to_check"}
    Response: {
        "success": true,
        "valid": true/false,
        "errors": ["error1", "error2"],
        "strength": 0-4,  # 0=weak, 4=very strong (zxcvbn score)
        "message": "Password requirements not met" or ""
    }
    """
    try:
        data = request.get_json() or {}
        password = data.get("password", "").strip()
        
        if not password:
            return jsonify({
                "success": True,
                "valid": False,
                "errors": ["Password cannot be empty"],
                "strength": 0,
                "message": "Password cannot be empty"
            }), 400
        
        is_valid, error_message, strength_score = validate_password_strength(password)
        
        errors = error_message.split("; ") if error_message else []
        
        return jsonify({
            "success": True,
            "valid": is_valid,
            "errors": errors,
            "strength": strength_score,
            "message": error_message
        }), 200
    
    except Exception as e:
        logger = logging.getLogger("classiface")
        logger.error(f"Password strength check failed: {type(e).__name__}")
        return jsonify({
            "success": False,
            "valid": False,
            "errors": ["Unable to validate password"],
            "strength": 0,
            "message": "Server error during validation"
        }), 500


# ============================================================
# GLOBAL ERROR HANDLERS
# ============================================================
@app.errorhandler(500)
def api_internal_error(e):
    """Return JSON for API 500s without catch-all recursion."""
    original = getattr(e, "original_exception", None) or e
    logging.getLogger("classiface").error(
        "API/server 500: %s: %s",
        type(original).__name__,
        original,
        exc_info=True,
    )
    if request.path.startswith("/api/"):
        return jsonify({
            "ok": False,
            "error": str(original),
            "message": f"{type(original).__name__}: {str(original)}",
        }), 500
    return "Internal Server Error", 500


# ============================================================
# CONTROLLER REGISTRATION
# ============================================================

from controllers import register_controllers

register_controllers()

if __name__ == "__main__":  # CHANGED
    socketio.run(app, debug=True, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)
