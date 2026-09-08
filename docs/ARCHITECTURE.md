# DOCKET - Department Portal Architecture

## Overview
DOCKET is an enterprise department portal designed for managing departmental workflows, employee authentication, and guest house approval pipelines.

## Folder Structure (PORTMAN Architecture)

```
Docket/
├── alembic/              # Database migration scripts & configuration
│   ├── env.py            # Alembic runtime environment
│   └── versions/         # Versioned schema migrations
├── docs/                 # System architecture and technical documentation
│   └── ARCHITECTURE.md
├── logs/                 # Application and sync runtime log files
│   └── .gitkeep
├── modules/              # Application modular business components
│   ├── __init__.py       # Module exports
│   └── models.py         # Central SQLAlchemy ORM models (Employee, GuestHouseRequest)
├── static/               # CSS stylesheets, images, client assets
│   ├── style.css
│   └── img/
├── templates/            # Jinja2 HTML presentation templates
│   ├── base.html
│   ├── login.html
│   ├── verify_otp.html
│   ├── dashboard.html
│   ├── admin.html
│   └── approvals.html
├── tests/                # Automated unit and integration tests
│   ├── __init__.py
│   └── test_basic.py
├── .env                  # Local environment configuration & credentials
├── .gitignore            # Git exclusion rules
├── alembic.ini           # Alembic migration configuration
├── app.py                # Flask application entry point
├── config.py             # Centralized settings (PostgreSQL & SQL Server)
├── database.py           # Database engine & session lifecycle
├── sync_service.py       # SQL Server (JSW_Dharamtar) to PostgreSQL synchronizer
└── sync_employees.py     # CLI runner for employee synchronization
```

## Database Architecture
- **Target DB**: PostgreSQL (`docket_db`)
- **Source DB**: MS SQL Server (`172.21.30.101` / `JSW_Dharamtar`)
- **Migration Manager**: Alembic (all schema evolution is managed via `alembic upgrade head`)
