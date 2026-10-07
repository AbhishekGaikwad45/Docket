import logging
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

import pyodbc
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import Config
from database import SessionLocal
from modules.models import Employee, User

logger = logging.getLogger("employee_sync")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _clean_str(val: Any) -> Optional[str]:
    """Clean string values and return stripped string or None."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def fetch_from_sql_server(view_name: str, active_only: bool = False) -> List[Dict[str, Any]]:
    """Fetch employee rows from a SQL Server view and return clean dicts."""
    conn_str = Config.get_source_odbc_connection_string()
    query = f"SELECT * FROM dbo.{view_name}"
    if active_only:
        query += " WHERE [Employee Status] = 'Active'"

    logger.info(f"Connecting to SQL Server to fetch from dbo.{view_name}...")
    employees = []

    with pyodbc.connect(conn_str, timeout=15) as conn:
        cursor = conn.cursor()
        cursor.execute(query)

        # Normalize column names: strip spaces and convert to uppercase
        col_names = [col[0].strip().upper() for col in cursor.description]

        # Helper index finders
        def find_idx(*candidates):
            for cand in candidates:
                cand_upper = cand.upper()
                for i, col in enumerate(col_names):
                    if col == cand_upper or col.replace(" ", "") == cand_upper.replace(" ", ""):
                        return i
            return None

        idx_id = find_idx("EMPLOYEE ID", "EMPLOYEE_ID", "EMPID")
        idx_name = find_idx("EMPLOYEE NAME", "EMPLOYEE  NAME", "EMPLOYEE_NAME", "NAME")
        idx_desig = find_idx("DESIGNATION")
        idx_dept = find_idx("DEPARTMENT")
        idx_email = find_idx("EMAIL ID", "EMAIL_ID", "EMAIL")
        idx_status = find_idx("EMPLOYEE STATUS", "EMPLOYEE_STATUS", "STATUS")
        idx_contact = find_idx("CONTACT NO", "CONTACT_NO", "MOBILE")
        idx_gender = find_idx("GENDER")
        idx_category = find_idx("CATEGORY")

        for row in cursor.fetchall():
            emp_id = _clean_str(row[idx_id]) if idx_id is not None else None
            if not emp_id:
                continue

            emp_name = _clean_str(row[idx_name]) if idx_name is not None else ""
            if not emp_name:
                emp_name = f"Employee {emp_id}"

            email = _clean_str(row[idx_email]) if idx_email is not None else None
            if email:
                email = email.lower()

            dept = _clean_str(row[idx_dept]) if idx_dept is not None else None
            if dept and dept.upper() == "IT":
                dept = "Information Technology"

            employees.append({
                "employee_id": emp_id,
                "employee_name": emp_name,
                "designation": _clean_str(row[idx_desig]) if idx_desig is not None else None,
                "department": dept,
                "email_id": email,
                "employee_status": _clean_str(row[idx_status]) if idx_status is not None else "Active",
                "contact_no": _clean_str(row[idx_contact]) if idx_contact is not None else None,
                "gender": _clean_str(row[idx_gender]) if idx_gender is not None else None,
                "category": _clean_str(row[idx_category]) if idx_category is not None else None,
                "source_view": view_name,
            })

    logger.info(f"Fetched {len(employees)} records from dbo.{view_name}.")
    return employees


def upsert_employees_to_postgres(records: List[Dict[str, Any]], batch_size: int = 500) -> int:
    """Upsert records into PostgreSQL employees table using batch ON CONFLICT DO UPDATE."""
    if not records:
        return 0

    now = datetime.utcnow()
    total_upserted = 0
    session = SessionLocal()

    try:
        # Deduplicate records by employee_id within the batch (prefer last seen)
        deduped = {}
        for r in records:
            deduped[r["employee_id"]] = r
        clean_records = list(deduped.values())

        for i in range(0, len(clean_records), batch_size):
            batch = clean_records[i : i + batch_size]
            for item in batch:
                item["last_synced_at"] = now
                item["created_at"] = now
                item["updated_at"] = now

            # PostgreSQL Upsert statement
            stmt = pg_insert(Employee).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["employee_id"],
                set_={
                    "employee_name": stmt.excluded.employee_name,
                    "designation": stmt.excluded.designation,
                    "department": stmt.excluded.department,
                    # Mantra may return no email. Preserve an email saved in
                    # the portal instead of replacing it with a blank value.
                    "email_id": func.coalesce(stmt.excluded.email_id, Employee.email_id),
                    "employee_status": stmt.excluded.employee_status,
                    "contact_no": stmt.excluded.contact_no,
                    "gender": stmt.excluded.gender,
                    "category": stmt.excluded.category,
                    "source_view": stmt.excluded.source_view,
                    "last_synced_at": stmt.excluded.last_synced_at,
                    "updated_at": stmt.excluded.updated_at,
                },
                where=(
                    (stmt.excluded.source_view == "view_EmployeeMaster_Report_Staff")
                    | (Employee.source_view.is_distinct_from("view_EmployeeMaster_Report_Staff"))
                ),
            )

            session.execute(stmt)
            session.commit()
            total_upserted += len(batch)

        return total_upserted

    except Exception as e:
        session.rollback()
        logger.error(f"Error during upsert to PostgreSQL: {e}")
        raise
    finally:
        session.close()


def sync_employees(include_staff: bool = True, include_associates: bool = False, active_only: bool = False) -> Dict[str, Any]:
    """
    Main entry point for syncing employee data from MS SQL Server to PostgreSQL.
    When syncing Staff only, non-staff synced records (associates) are cleared from PostgreSQL
    so that only Staff data is visible across the entire portal.
    When syncing Staff & Associates, both datasets are fetched and retained.
    """
    start_time = time.time()
    staff_count = 0
    associates_count = 0
    errors = []

    all_records = []

    if include_staff:
        try:
            staff_records = fetch_from_sql_server("view_EmployeeMaster_Report_Staff", active_only=active_only)
            staff_count = len(staff_records)
            all_records.extend(staff_records)
        except Exception as e:
            msg = f"Failed to sync Staff view: {e}"
            logger.error(msg)
            errors.append(msg)

    if include_associates:
        try:
            assoc_records = fetch_from_sql_server("view_EmployeeMaster_Report_Associates", active_only=active_only)
            associates_count = len(assoc_records)
            all_records.extend(assoc_records)
        except Exception as e:
            msg = f"Failed to sync Associates view: {e}"
            logger.error(msg)
            errors.append(msg)

    upserted_count = 0
    if all_records:
        try:
            logger.info(f"Upserting {len(all_records)} total employee records to PostgreSQL...")
            upserted_count = upsert_employees_to_postgres(all_records)
            logger.info(f"Successfully upserted {upserted_count} employee records.")
        except Exception as e:
            msg = f"Failed to save records to PostgreSQL: {e}"
            logger.error(msg)
            errors.append(msg)

    purged_count = 0
    # Clean up non-synced source views if a specific source was selected
    # E.g., if Staff only was selected, purge Associates from PostgreSQL so they do not show up in User Management or Approval Management.
    # If Associates only was selected, purge Staff.
    # If both were selected, retain both.
    # Manual records ('manual_entry') are always preserved.
    if upserted_count > 0 and len(errors) == 0:
        session = SessionLocal()
        try:
            if include_staff and not include_associates:
                assoc_emps = session.query(Employee.employee_id).filter(
                    Employee.source_view == "view_EmployeeMaster_Report_Associates"
                ).all()
                assoc_ids = [r[0] for r in assoc_emps]
                if assoc_ids:
                    session.query(User).filter(
                        User.emp_id.in_(assoc_ids),
                        User.is_admin == False,
                        User.role != "admin"
                    ).delete(synchronize_session=False)
                    purged_count = session.query(Employee).filter(
                        Employee.source_view == "view_EmployeeMaster_Report_Associates"
                    ).delete(synchronize_session=False)
                    session.commit()
                    logger.info(f"Purged {purged_count} Associate records from PostgreSQL because Staff only was synced.")

            elif include_associates and not include_staff:
                staff_emps = session.query(Employee.employee_id).filter(
                    Employee.source_view == "view_EmployeeMaster_Report_Staff"
                ).all()
                staff_ids = [r[0] for r in staff_emps]
                if staff_ids:
                    session.query(User).filter(
                        User.emp_id.in_(staff_ids),
                        User.is_admin == False,
                        User.role != "admin"
                    ).delete(synchronize_session=False)
                    purged_count = session.query(Employee).filter(
                        Employee.source_view == "view_EmployeeMaster_Report_Staff"
                    ).delete(synchronize_session=False)
                    session.commit()
                    logger.info(f"Purged {purged_count} Staff records from PostgreSQL because Associates only was synced.")
        except Exception as e:
            session.rollback()
            logger.error(f"Error purging excluded employee sources: {e}")
            errors.append(f"Source cleanup error: {e}")
        finally:
            session.close()

    duration = round(time.time() - start_time, 2)

    # Reflect manually maintained emails in the result table too. This matters
    # when Mantra supplies a blank email for an employee.
    if all_records and upserted_count:
        session = SessionLocal()
        try:
            employee_ids = [record["employee_id"] for record in all_records]
            saved_emails = dict(
                session.query(Employee.employee_id, Employee.email_id)
                .filter(Employee.employee_id.in_(employee_ids))
                .all()
            )
            for record in all_records:
                if not record.get("email_id"):
                    record["email_id"] = saved_emails.get(record["employee_id"])
        finally:
            session.close()

    # Re-sync Approval Management step approvers from workflow flow_data
    try:
        from app import sync_workflow_step_approvers_from_flow_data
        _sync_session = SessionLocal()
        try:
            sync_workflow_step_approvers_from_flow_data(_sync_session)
        finally:
            _sync_session.close()
    except Exception:
        pass

    return {
        "success": len(errors) == 0,
        "staff_fetched": staff_count,
        "associates_fetched": associates_count,
        "total_fetched": len(all_records),
        "total_upserted": upserted_count,
        "purged_count": purged_count,
        "duration_seconds": duration,
        "errors": errors,
        # The admin UI uses these source rows to show the result of this run.
        "records": all_records,
    }
