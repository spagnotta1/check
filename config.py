import os
import secrets


def _load_or_create_secret_key(base_dir):
    """Persist a random Flask session-signing key next to the database.

    A hardcoded key would let anyone forge session cookies (including the
    APP_PASSWORD login gate); a purely in-memory one would log everyone out
    on each restart.
    """
    path = os.path.join(base_dir, '.flask_secret_key')
    try:
        with open(path) as fh:
            key = fh.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    with open(path, 'w') as fh:
        fh.write(key)
    return key


class Config:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(BASE_DIR, "checkbook.db")}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.environ.get('SECRET_KEY') or _load_or_create_secret_key(
        os.path.abspath(os.path.dirname(__file__)))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
    AI_INSIGHT_CACHE_TTL = int(os.environ.get('AI_INSIGHT_CACHE_TTL', 3600))

    # --- Financial institution synchronization (finance_sync) ---
    SYNC_AUTO_ENABLED = os.environ.get('SYNC_AUTO_ENABLED', '1') != '0'
    SYNC_INTERVAL_HOURS = float(os.environ.get('SYNC_INTERVAL_HOURS', 12))
    # When True, manual sync API calls run inline instead of on a background
    # thread (used by the test suite for determinism).
    SYNC_SYNCHRONOUS = False