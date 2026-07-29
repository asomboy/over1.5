import os
import sys

"""Backend configuration module."""
# Base directory for backend
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs) -> bool:
        return True

# Load environment variables from .env file located in backend directory
ENV_PATH = os.path.join(BASE_DIR, ".env")
if os.path.exists(ENV_PATH):
    load_dotenv(dotenv_path=ENV_PATH)
else:
    ROOT_ENV_PATH = os.path.join(os.path.dirname(BASE_DIR), ".env")
    if os.path.exists(ROOT_ENV_PATH):
        load_dotenv(dotenv_path=ROOT_ENV_PATH)
    else:
        load_dotenv()

# Database configuration: resolve relative SQLite URLs relative to backend directory
raw_db_url = os.getenv("DATABASE_URL", "sqlite:///soccer.db")
if raw_db_url.startswith("sqlite:///") and not os.path.isabs(raw_db_url.replace("sqlite:///", "")):
    db_filename = raw_db_url.replace("sqlite:///", "")
    abs_db_path = os.path.normpath(os.path.join(BASE_DIR, db_filename)).replace("\\", "/")
    DATABASE_URL = f"sqlite:///{abs_db_path}"
else:
    DATABASE_URL = raw_db_url.replace("\\", "/")

# Server & Environment settings
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
HOST = os.getenv("HOST", "0.0.0.0")

port_env = os.getenv("PORT", "8000")
PORT = int(port_env) if port_env and port_env.isdigit() else 8000

# External API Keys
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")

# CORS origins list parsing (allow * in production for Render cross-origin requests)
cors_origins_env = os.getenv("CORS_ORIGINS")
if cors_origins_env:
    CORS_ORIGINS = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
else:
    CORS_ORIGINS = ["*"]
