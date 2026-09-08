from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from config import Config

# Engine for PostgreSQL
engine = create_engine(
    Config.SQLALCHEMY_DATABASE_URI,
    **Config.SQLALCHEMY_ENGINE_OPTIONS
)

# Thread-safe scoped session factory
SessionLocal = scoped_session(
    sessionmaker(autocommit=False, autoflush=False, bind=engine)
)

# Base class for all declarative SQLAlchemy models
Base = declarative_base()


def get_db():
    """Dependency helper for standalone scripts or generator contexts."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def close_db_session(exception=None):
    """Clean up the scoped session (ideal for Flask teardown_appcontext)."""
    SessionLocal.remove()


def init_db_defaults():
    """Seed initial default admin user if tables exist and admin is not yet present."""
    from modules.models import User, Employee

    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return

    session = SessionLocal()
    try:
        admin_user = session.query(User).filter(User.username == Config.DEFAULT_ADMIN_USERNAME).first()
        if not admin_user:
            # Check if DEFAULT_ADMIN_EMP_ID exists in employees table, otherwise None
            emp_exists = None
            if Config.DEFAULT_ADMIN_EMP_ID:
                emp = session.query(Employee).filter(Employee.employee_id == Config.DEFAULT_ADMIN_EMP_ID).first()
                if emp:
                    emp_exists = emp.employee_id

            admin_user = User(
                emp_id=emp_exists,
                username=Config.DEFAULT_ADMIN_USERNAME,
                role="admin",
                is_admin=True,
                is_active=True,
            )
            admin_user.set_password(Config.DEFAULT_ADMIN_PASSWORD)
            session.add(admin_user)
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()

