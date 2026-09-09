import os
from pathlib import Path
from dotenv import load_dotenv

# Load a conventional .env first, then this project's existing env.py file.
# Both files use dotenv KEY=value syntax; neither overrides real environment
# variables supplied by the deployment environment.
load_dotenv()
load_dotenv(dotenv_path=Path(__file__).with_name("env.py"))


class Config:
    # Flask configuration
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

    # =========================================================================
    # Target Database (PostgreSQL - Docket Application DB)
    # =========================================================================
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "password")
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "docket_db")

    # Full PostgreSQL connection URI
    _default_pg_url = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", _default_pg_url)

    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    }

    # =========================================================================
    # Source Database (MS SQL Server - JSW_Dharamtar Employee Master / Mantra)
    # =========================================================================
    SOURCE_DB_DRIVER = os.getenv("SOURCE_DB_DRIVER", "SQL Server")
    SOURCE_DB_SERVER = os.getenv("SOURCE_DB_SERVER", "172.21.30.101")
    SOURCE_DB_PORT = os.getenv("SOURCE_DB_PORT", "1433")
    SOURCE_DB_NAME = os.getenv("SOURCE_DB_NAME", "JSW_Dharamtar")
    SOURCE_DB_USER = os.getenv("SOURCE_DB_USER", "Report")
    SOURCE_DB_PASSWORD = os.getenv("SOURCE_DB_PASSWORD", "Report@123")
    SOURCE_DB_TRUSTED_CONNECTION = os.getenv("SOURCE_DB_TRUSTED_CONNECTION", "no")

    # =========================================================================
    # SMTP Email Configuration
    # =========================================================================
    SMTP_SERVER = os.getenv("SMTP_SERVER", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "True").lower() in ("true", "1", "yes")
    SMTP_SENDER_EMAIL = os.getenv("SMTP_SENDER_EMAIL", "noreply-docket@jsw.in")

    # =========================================================================
    # Default Admin Credentials
    # =========================================================================
    DEFAULT_ADMIN_USERNAME = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
    DEFAULT_ADMIN_PASSWORD = os.getenv("DEFAULT_ADMIN_PASSWORD", "Admin@123")
    DEFAULT_ADMIN_EMP_ID = os.getenv("DEFAULT_ADMIN_EMP_ID", "ADMIN01")

    @classmethod
    def get_source_odbc_connection_string(cls) -> str:
        """Returns the pyodbc connection string for the source SQL Server database."""
        if os.getenv("SOURCE_DATABASE_URL"):
            return os.getenv("SOURCE_DATABASE_URL")

        if cls.SOURCE_DB_TRUSTED_CONNECTION.lower() in ("yes", "true", "1"):
            return (
                f"DRIVER={{{cls.SOURCE_DB_DRIVER}}};"
                f"SERVER={cls.SOURCE_DB_SERVER},{cls.SOURCE_DB_PORT};"
                f"DATABASE={cls.SOURCE_DB_NAME};"
                f"Trusted_Connection=yes;"
            )
        else:
            return (
                f"DRIVER={{{cls.SOURCE_DB_DRIVER}}};"
                f"SERVER={cls.SOURCE_DB_SERVER},{cls.SOURCE_DB_PORT};"
                f"DATABASE={cls.SOURCE_DB_NAME};"
                f"UID={cls.SOURCE_DB_USER};"
                f"PWD={cls.SOURCE_DB_PASSWORD};"
            )
