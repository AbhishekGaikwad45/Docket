import re
import random
from datetime import date, datetime, time
from functools import wraps
from typing import Optional, List, Dict, Any, Tuple, Union
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from sqlalchemy import or_

from config import Config
from database import SessionLocal, close_db_session, init_db_defaults
from modules.models import (
    User,
    Employee,
    GuestHouseRequest,
    ApprovalWorkflow,
    ApprovalWorkflowStep,
    ApprovalStepApprover,
    ApprovalRequest,
)
from sync_service import sync_employees
from mail_service import (
    send_otp_email,
    send_request_outcome_email,
    send_request_outcome_email_async,
    send_final_approval_email,
    notify_final_approval_async,
    notify_vehicle_arrangement_async,
    notify_vehicle_details_async,
)

app = Flask(__name__)
app.config.from_object(Config)
app.secret_key = Config.SECRET_KEY

# Initialize database default admin user if not already present
init_db_defaults()


@app.teardown_appcontext
def shutdown_session(exception=None):
    close_db_session(exception)


def next_id():
    db = SessionLocal()
    try:
        count = db.query(GuestHouseRequest).count()
        return f"GH-{1042 + count}"
    finally:
        db.close()


def require_admin(view):
    """Restrict Administration controls to authenticated administrators."""
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Admin access is required to open the Administration panel.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped_view


@app.before_request
def require_login():
    open_endpoints = {
        "login",
        "admin_login",
        "send_otp",
        "verify_otp_form",
        "verify_otp",
        "static",
        "sync_employees_route",
    }
    if request.endpoint not in open_endpoints and not session.get("logged_in"):
        return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Auth: Dual Login (Employee OTP + Admin Password)
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/admin-login", methods=["POST"])
def admin_login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()

    if not username or not password:
        flash("Enter both username and password.", "error")
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user or not user.check_password(password):
            flash("Invalid admin username or password.", "error")
            return redirect(url_for("login"))

        if not user.is_active:
            flash("Your admin account is currently disabled.", "error")
            return redirect(url_for("login"))

        # Update last login
        user.last_login_at = datetime.utcnow()
        db.commit()

        session["logged_in"] = True
        session["user_id"] = user.id
        session["emp_id"] = user.emp_id or user.username
        session["emp_name"] = f"Admin ({user.username})"
        session["role"] = user.role or "admin"
        session["is_admin"] = user.is_admin

        flash(f"Signed in successfully as {user.username}.", "success")
        return redirect(url_for("admin_module"))
    finally:
        db.close()


@app.route("/send-otp", methods=["POST"])
def send_otp():
    emp_id = request.form.get("emp_id", "").strip()
    if not emp_id:
        flash("Enter your employee ID.", "error")
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        emp = db.query(Employee).filter(Employee.employee_id == emp_id).first()
        if not emp:
            flash(f"Employee ID '{emp_id}' not found in Mantra database. Please contact Admin or run Mantra Sync.", "error")
            return redirect(url_for("login"))

        # Generate 6-digit OTP
        otp_code = f"{random.randint(100000, 999999)}"

        if not emp.email_id:
            flash("No email ID is registered for this employee. Please contact the administrator.", "error")
            return redirect(url_for("login"))

        session["pending_emp_id"] = emp_id
        session["pending_emp_name"] = emp.employee_name
        session["pending_emp_email"] = emp.email_id
        session["pending_emp_dept"] = emp.department
        session["pending_otp"] = otp_code

        # Attempt SMTP delivery
        sent = False
        try:
            sent = send_otp_email(emp.email_id, otp_code, emp.employee_name)
        except Exception as mail_err:
            app.logger.warning(f"SMTP delivery error for {emp.email_id}: {mail_err}")

        if sent:
            flash(f"OTP sent to your registered email ({emp.email_id[:3]}***@...).", "success")
        else:
            app.logger.warning(f"[FALLBACK OTP] Employee {emp.employee_name} ({emp_id}) OTP: {otp_code}")
            flash(f"Email server is offline on this network. Your verification OTP is: {otp_code} (or enter bypass code 123456).", "info")

    finally:
        db.close()

    return redirect(url_for("verify_otp_form"))


@app.route("/verify", methods=["GET"])
def verify_otp_form():
    emp_id = session.get("pending_emp_id")
    if not emp_id:
        return redirect(url_for("login"))
    return render_template("verify_otp.html", emp_id=emp_id)


@app.route("/verify", methods=["POST"])
def verify_otp():
    emp_id = session.get("pending_emp_id")
    expected_otp = session.get("pending_otp")
    code = request.form.get("otp", "").strip()

    if not emp_id:
        return redirect(url_for("login"))

    # Verify matching OTP or demo bypass (123456)
    if code != expected_otp and code != "123456":
        flash("Invalid verification code. Please enter the 6-digit code sent.", "error")
        return redirect(url_for("verify_otp_form"))

    db = SessionLocal()
    try:
        # Check if a user record exists for this employee, or create one
        user = db.query(User).filter(User.emp_id == emp_id).first()
        if not user:
            user = User(
                emp_id=emp_id,
                username=emp_id,
                role="employee",
                is_admin=False,
                is_active=True,
                last_login_at=datetime.utcnow(),
            )
            db.add(user)
            db.commit()
        else:
            user.last_login_at = datetime.utcnow()
            db.commit()

        session["logged_in"] = True
        session["user_id"] = user.id
        session["emp_id"] = emp_id
        session["emp_name"] = session.get("pending_emp_name", emp_id)
        session["is_admin"] = user.is_admin
        session.pop("pending_emp_id", None)
        session.pop("pending_otp", None)

        # Set default active role based on user's authorized approval permissions
        allowed_roles = get_allowed_roles_for_employee(db, emp_id)
        default_role = "employee"
        for r in ["unit_head", "hr_head", "dept_head"]:
            if r in allowed_roles:
                default_role = r
                break
        session["role"] = default_role

    finally:
        db.close()

    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/set-role", methods=["POST"])
def set_role():
    role = request.form.get("role", "employee")
    emp_id = session.get("emp_id")
    is_admin = session.get("is_admin", False)

    if is_admin:
        if role in ("employee", "dept_head", "hr_head", "unit_head", "admin"):
            session["role"] = role
    elif emp_id:
        db = SessionLocal()
        try:
            allowed = get_allowed_roles_for_employee(db, emp_id)
            if role in allowed:
                session["role"] = role
            else:
                flash(f"You do not have permission to view as '{role}'.", "warning")
        finally:
            db.close()
    else:
        session["role"] = "employee"

    return redirect(request.referrer or url_for("dashboard"))


@app.context_processor
def inject_user_roles():
    emp_id = session.get("emp_id")
    is_admin = session.get("is_admin", False)

    if is_admin:
        unalloc = 0
        db = SessionLocal()
        try:
            v_reqs = (
                db.query(ApprovalRequest)
                .filter(
                    ApprovalRequest.status == "approved",
                    or_(
                        ApprovalRequest.workflow_id == 7,
                        ApprovalRequest.request_type.ilike("%vehicle%"),
                        ApprovalRequest.request_type.ilike("%car%"),
                        ApprovalRequest.title.ilike("%vehicle%"),
                        ApprovalRequest.title.ilike("%car%"),
                    ),
                )
                .all()
            )
            for r in v_reqs:
                d = {}
                if r.details:
                    try:
                        import json as _j
                        d = _j.loads(r.details) if isinstance(r.details, str) else r.details
                    except Exception:
                        d = {}
                if not (isinstance(d, dict) and d.get("vehicle_allocation")):
                    unalloc += 1
        except Exception:
            unalloc = 0
        finally:
            db.close()
        return {"allowed_roles": [], "unallocated_vehicle_count": unalloc}

    if not emp_id:
        return {"allowed_roles": []}

    db = SessionLocal()
    try:
        allowed = get_allowed_roles_for_employee(db, emp_id)
        # Ensure current session role does not exceed user's permissions
        current_role = session.get("role")
        if current_role and current_role not in allowed and not is_admin:
            session["role"] = allowed[0] if allowed else "employee"
        return {"allowed_roles": allowed}
    except Exception as e:
        logger.error(f"Error injecting user roles: {e}")
        return {"allowed_roles": ["employee"]}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Dashboard + department modules
# ---------------------------------------------------------------------------
@app.route("/dashboard")
def dashboard():
    emp_id = session.get("emp_id")
    db = SessionLocal()
    try:
        user_info = {}
        if emp_id:
            emp = db.query(Employee).filter(Employee.employee_id == emp_id).first()
            if emp:
                user_info = emp.to_dict()

        # Query this employee's requests
        req_records = (
            db.query(ApprovalRequest)
            .filter(ApprovalRequest.applicant_emp_id == emp_id)
            .order_by(ApprovalRequest.created_at.desc())
            .all()
        )
        my_requests = [r.to_dict() for r in req_records]

        # Active workflows for the form dropdown
        wfs = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.is_active == True).order_by(ApprovalWorkflow.name).all()
        workflows = [w.to_dict() for w in wfs]

        total_count = len(my_requests)
        pending_count = sum(1 for r in my_requests if r["status"] == "pending")
        approved_count = sum(1 for r in my_requests if r["status"] == "approved")
        rejected_count = sum(1 for r in my_requests if r["status"] == "rejected")

        # Distinct departments for user request form
        dept_rows = (
            db.query(Employee.department)
            .distinct()
            .filter(Employee.department != None)
            .all()
        )
        departments = sorted([d[0] for d in dept_rows if d[0]])

        return render_template(
            "dashboard.html",
            user_info=user_info,
            requests=my_requests,
            workflows=workflows,
            departments=departments,
            total_count=total_count,
            pending_count=pending_count,
            approved_count=approved_count,
            rejected_count=rejected_count,
        )
    finally:
        db.close()


@app.route("/department/<name>")
def department_placeholder(name):
    if name not in ("hr", "it", "finance"):
        return redirect(url_for("dashboard"))
    return render_template("placeholder.html", dept=name)


@app.route("/department/admin", methods=["GET"])
@require_admin
def admin_module():
    source = request.args.get("source")
    if source:
        session["last_admin_source"] = source
    else:
        source = session.get("last_admin_source", "both")
    return render_admin_module(selected_source=source)


def render_admin_module(sync_result=None, sync_records=None, selected_source="both"):
    """Render the User Management page with all employees from the database table."""
    db = SessionLocal()
    try:
        req_records = (
            db.query(GuestHouseRequest)
            .order_by(GuestHouseRequest.created_at.asc())
            .all()
        )
        my_requests = [r.to_dict() for r in req_records]
        latest = my_requests[-1] if my_requests else None
        employees = db.query(Employee).order_by(Employee.employee_id.asc()).all()
        users = db.query(User).all()
        user_roles = {u.emp_id: u.role for u in users if u.emp_id}

        employee_records = []
        departments_set = set()
        staff_count = 0
        associates_count = 0
        manual_count = 0

        for emp in employees:
            d = emp.to_dict()
            d["role"] = user_roles.get(emp.employee_id, "employee")
            employee_records.append(d)
            if emp.department:
                departments_set.add(emp.department)

            st = d.get("source_type", "manual")
            if st == "staff":
                staff_count += 1
            elif st == "associates":
                associates_count += 1
            else:
                manual_count += 1

        if associates_count == 0 and staff_count > 0 and selected_source not in ("staff", "manual"):
            selected_source = "staff"
        elif staff_count == 0 and associates_count > 0 and selected_source not in ("associates", "manual"):
            selected_source = "associates"

        departments = sorted(list(departments_set))
        pending_approvals_count = db.query(ApprovalRequest).filter(ApprovalRequest.status == "pending").count()

        return render_template(
            "admin.html",
            requests=my_requests,
            latest=latest,
            employee_count=len(employee_records),
            employee_records=employee_records,
            departments=departments,
            sync_result=sync_result,
            sync_records=sync_records or [],
            selected_source=selected_source,
            staff_count=staff_count,
            associates_count=associates_count,
            manual_count=manual_count,
            pending_approvals_count=pending_approvals_count,
        )
    finally:
        db.close()


@app.route("/admin/users/<employee_id>/json", methods=["GET"])
@require_admin
def get_user_json(employee_id):
    """Fetch a single user/employee record as JSON."""
    db = SessionLocal()
    try:
        emp = db.query(Employee).filter(Employee.employee_id == employee_id).first()
        if not emp:
            return jsonify({"success": False, "message": "User not found."}), 404
        data = emp.to_dict()
        user = db.query(User).filter(User.emp_id == employee_id).first()
        data["role"] = user.role if user else "employee"
        return jsonify({"success": True, "user": data})
    finally:
        db.close()


@app.route("/admin/users/add", methods=["POST"])
@require_admin
def add_user():
    """Create a new user/employee in the employees table where email_id is stored."""
    payload = request.get_json(silent=True) or request.form
    employee_id = (payload.get("employee_id") or "").strip()
    employee_name = (payload.get("employee_name") or "").strip()
    email_id = (payload.get("email_id") or "").strip().lower() or None
    designation = (payload.get("designation") or "").strip() or None
    department = (payload.get("department") or "").strip() or None
    contact_no = (payload.get("contact_no") or "").strip() or None
    gender = (payload.get("gender") or "").strip() or None
    employee_status = (payload.get("employee_status") or "Active").strip()
    role = (payload.get("role") or "employee").strip().lower()

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json

    if not employee_id or not employee_name:
        msg = "Employee ID and Employee Name are required."
        if is_ajax:
            return jsonify({"success": False, "message": msg}), 400
        flash(msg, "error")
        return redirect(url_for("admin_module"))

    db = SessionLocal()
    try:
        existing = db.query(Employee).filter(Employee.employee_id == employee_id).first()
        if existing:
            msg = f"A user with Employee ID '{employee_id}' already exists."
            if is_ajax:
                return jsonify({"success": False, "message": msg}), 400
            flash(msg, "error")
            return redirect(url_for("admin_module"))

        new_emp = Employee(
            employee_id=employee_id,
            employee_name=employee_name,
            email_id=email_id,
            designation=designation,
            department=department,
            contact_no=contact_no,
            gender=gender,
            employee_status=employee_status,
            source_view="manual_entry",
            last_synced_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(new_emp)

        # Sync/create linked User record for authentication
        user = db.query(User).filter(User.username == employee_id).first()
        if not user:
            user = User(
                emp_id=employee_id,
                username=employee_id,
                role=role if role in ("employee", "dept_head", "hr_head", "unit_head", "admin") else "employee",
                is_admin=(role == "admin"),
                is_active=(employee_status.lower() == "active"),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(user)
        else:
            user.emp_id = employee_id
            user.is_active = (employee_status.lower() == "active")
            if role in ("employee", "dept_head", "hr_head", "unit_head", "admin"):
                user.role = role
                user.is_admin = (role == "admin")

        db.commit()
        invalidate_roles_cache()
        msg = f"User '{employee_name}' (ID: {employee_id}) added successfully."
        if is_ajax:
            return jsonify({"success": True, "message": msg, "user": new_emp.to_dict()})
        flash(msg, "success")
        return redirect(url_for("admin_module"))
    except Exception as e:
        db.rollback()
        app.logger.exception("Unable to add user")
        msg = f"Failed to add user: {str(e)}"
        if is_ajax:
            return jsonify({"success": False, "message": msg}), 500
        flash(msg, "error")
        return redirect(url_for("admin_module"))
    finally:
        db.close()


@app.route("/admin/users/<employee_id>/edit", methods=["POST"])
@require_admin
def edit_user(employee_id):
    """Update a user/employee in the employees table where email_id is stored."""
    payload = request.get_json(silent=True) or request.form
    employee_name = (payload.get("employee_name") or "").strip()
    email_id = (payload.get("email_id") or "").strip().lower() or None
    designation = (payload.get("designation") or "").strip() or None
    department = (payload.get("department") or "").strip() or None
    contact_no = (payload.get("contact_no") or "").strip() or None
    gender = (payload.get("gender") or "").strip() or None
    employee_status = (payload.get("employee_status") or "Active").strip()
    role = (payload.get("role") or "").strip().lower()

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json

    if not employee_name:
        msg = "Employee Name cannot be empty."
        if is_ajax:
            return jsonify({"success": False, "message": msg}), 400
        flash(msg, "error")
        return redirect(url_for("admin_module"))

    db = SessionLocal()
    try:
        emp = db.query(Employee).filter(Employee.employee_id == employee_id).first()
        if not emp:
            msg = f"User '{employee_id}' not found."
            if is_ajax:
                return jsonify({"success": False, "message": msg}), 404
            flash(msg, "error")
            return redirect(url_for("admin_module"))

        emp.employee_name = employee_name
        emp.email_id = email_id
        emp.designation = designation
        emp.department = department
        emp.contact_no = contact_no
        emp.gender = gender
        emp.employee_status = employee_status
        emp.updated_at = datetime.utcnow()

        # Update linked User account if present
        user = db.query(User).filter((User.emp_id == employee_id) | (User.username == employee_id)).first()
        if user:
            user.is_active = (employee_status.lower() == "active")
            if role in ("employee", "dept_head", "hr_head", "unit_head", "admin"):
                user.role = role
                user.is_admin = (role == "admin")
            user.updated_at = datetime.utcnow()

        db.commit()
        invalidate_roles_cache()
        msg = f"User '{employee_name}' (ID: {employee_id}) updated successfully."
        if is_ajax:
            return jsonify({"success": True, "message": msg, "user": emp.to_dict()})
        flash(msg, "success")
        return redirect(url_for("admin_module"))
    except Exception as e:
        db.rollback()
        app.logger.exception("Unable to edit user")
        msg = f"Failed to update user: {str(e)}"
        if is_ajax:
            return jsonify({"success": False, "message": msg}), 500
        flash(msg, "error")
        return redirect(url_for("admin_module"))
    finally:
        db.close()


@app.route("/admin/users/<employee_id>/delete", methods=["POST"])
@require_admin
def delete_user(employee_id):
    """Delete a user/employee from the employees table and clean up related records."""
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json

    current_emp_id = session.get("emp_id")
    if current_emp_id == employee_id:
        msg = "You cannot delete your own logged-in account."
        if is_ajax:
            return jsonify({"success": False, "message": msg}), 400
        flash(msg, "error")
        return redirect(url_for("admin_module"))

    db = SessionLocal()
    try:
        emp = db.query(Employee).filter(Employee.employee_id == employee_id).first()
        if not emp:
            msg = f"User '{employee_id}' not found."
            if is_ajax:
                return jsonify({"success": False, "message": msg}), 404
            flash(msg, "error")
            return redirect(url_for("admin_module"))

        user_name = emp.employee_name

        # Dissociate any guest house requests
        db.query(GuestHouseRequest).filter(GuestHouseRequest.created_by == employee_id).update(
            {"created_by": None}, synchronize_session=False
        )

        # Delete any linked User account
        db.query(User).filter(User.emp_id == employee_id).delete(synchronize_session=False)

        # Delete the Employee record from the table where email_id is stored
        db.delete(emp)
        db.commit()
        invalidate_roles_cache()

        msg = f"User '{user_name}' (ID: {employee_id}) was deleted successfully."
        if is_ajax:
            return jsonify({"success": True, "message": msg})
        flash(msg, "success")
        return redirect(url_for("admin_module"))
    except Exception as e:
        db.rollback()
        app.logger.exception("Unable to delete user")
        msg = f"Failed to delete user: {str(e)}"
        if is_ajax:
            return jsonify({"success": False, "message": msg}), 500
        flash(msg, "error")
        return redirect(url_for("admin_module"))
    finally:
        db.close()


@app.route("/admin/sync-mantra", methods=["POST"])
@require_admin
def sync_mantra_admin():
    active_only = request.form.get("active_only") == "true"
    source = request.form.get("source", "both")
    session["last_admin_source"] = source

    include_staff = source in ("both", "staff")
    include_associates = source in ("both", "associates")

    result = sync_employees(
        include_staff=include_staff,
        include_associates=include_associates,
        active_only=active_only,
    )

    if result["success"]:
        source_label = "Staff" if source == "staff" else ("Associates" if source == "associates" else "Staff & Associates")
        purged_msg = f" ({result.get('purged_count', 0)} non-selected records cleared)" if result.get("purged_count") else ""
        flash(f"Mantra Sync Successful: {result['total_upserted']} {source_label} records synced from JSW_Dharamtar in {result['duration_seconds']}s{purged_msg}.", "success")
    else:
        err_msg = ", ".join(result["errors"])
        flash(f"Mantra Sync Failed: {err_msg}", "error")

    # Pass selected_source so the table automatically filters and displays the synced employee type!
    return render_admin_module(sync_result=result, sync_records=result.get("records", []), selected_source=source)


@app.route("/admin/employees/<employee_id>/email", methods=["POST"])
@require_admin
def update_employee_email(employee_id):
    """Persist an email edited in the Mantra sync results table."""
    payload = request.get_json(silent=True) or request.form
    email_id = (payload.get("email_id") or "").strip().lower() or None

    db = SessionLocal()
    try:
        employee = db.query(Employee).filter(Employee.employee_id == employee_id).first()
        if not employee:
            return jsonify({"success": False, "message": "Employee not found."}), 404

        employee.email_id = email_id
        db.commit()
        return jsonify({"success": True, "email_id": employee.email_id or ""})
    except Exception:
        db.rollback()
        app.logger.exception("Unable to save employee email")
        return jsonify({"success": False, "message": "Unable to save the email address."}), 500
    finally:
        db.close()


def get_designation_seniority(designation: str) -> tuple[int, str]:
    """Return a seniority rank and tier label for an employee designation."""
    if not designation:
        return (15, "Support Staff & Other Designations")

    normalized = designation.upper().strip()
    if any(term in normalized for term in ("VICE PRESIDENT", "UNIT HEAD", "ASSOCIATE VICE PRESIDENT")):
        return (1, "Head / Executive Leadership")
    if "GENERAL MANAGER" in normalized and not any(term in normalized for term in ("DEPUTY", "DY", "ASST", "ASSISTANT")):
        return (2, "General Manager")
    if any(term in normalized for term in ("DEPUTY GENERAL MANAGER", "ASSISTANT GENERAL MANAGER", "DGM", "DY. GENERAL MANAGER", "DY GENERAL MANAGER", "ASST GENERAL MANAGER", "ASST. GENERAL MANAGER")):
        return (3, "Deputy / Assistant General Manager")
    if any(term in normalized for term in ("SENIOR MANAGER", "SR.MANAGER", "SR. MANAGER", "SR MANAGER")):
        return (4, "Senior Manager")
    if any(term in normalized for term in ("MANAGER", "SITE INCHARGE", "LEAD", "OWNER")) and not any(term in normalized for term in ("DEPUTY", "DY", "ASST", "ASSISTANT", "JR", "JUNIOR")):
        return (5, "Manager")
    if any(term in normalized for term in ("DEPUTY MANAGER", "DY. MANAGER", "DY MANAGER", "JR.MANAGER", "JR. MANAGER", "JR MANAGER")):
        return (6, "Deputy / Junior Manager")
    if any(term in normalized for term in ("ASSISTANT MANAGER", "ASST. MANAGER", "ASST MANAGER")):
        return (7, "Assistant Manager")
    if any(term in normalized for term in ("SENIOR ENGINEER", "SR. ENGINEER", "SR.ENGINEER", "SR ENGINEER", "SENIOR OFFICER", "SR. OFFICER", "SR.OFFICER", "SR OFFICER", "HR &ADMIN OFFICER", "HR & ADMIN OFFICER", "ACCOUNTANT")):
        return (8, "Senior Executive / Officer / Accountant")
    if ("ENGINEER" in normalized or "OFFICER" in normalized) and not any(term in normalized for term in ("ASST", "ASSISTANT", "JR", "JUNIOR", "GET", "TRAINEE", "SENIOR", "SR")):
        return (9, "Executive / Officer / Engineer")
    if any(term in normalized for term in ("ASSISTANT ENGINEER", "ASST. ENGINEER", "ASST ENGINEER", "ASSISTANT OFFICER", "ASST. OFFICER", "ASST OFFICER", "ASSISTANT ADMIN", "ASST. ADMIN", "ASST ADMIN")):
        return (10, "Assistant Officer / Engineer / Admin")
    if any(term in normalized for term in ("JUNIOR", "JR.", "JR ", "GET", "GRADUATE ENGINEER TRAINEE", "TRAINEE")):
        return (11, "Junior Officer / Junior Engineer / Trainee")
    if any(term in normalized for term in ("SUPERVISOR", "FOREMAN")):
        return (12, "Supervisor / Foreman")
    if any(term in normalized for term in ("ASSISTANT", "RECEPTIONIST")):
        return (13, "Assistant / Staff")
    if any(term in normalized for term in ("TECHNICIAN", "ELECTRICIAN", "MECHANIC", "FITTER", "WELDER", "OPERATOR", "RIGGER", "CARPENTER", "PLUMBER", "MASION", "TECHNICAL")):
        return (14, "Technical & Skilled Trades")
    return (15, "Support Staff & Other Designations")


@require_admin
def _legacy_get_department_hierarchy():
    hod_id = (request.args.get("hod_id") or "").strip()
    department = (request.args.get("department") or "").strip()

    db = SessionLocal()
    try:
        hod_employee = None
        if hod_id:
            hod_employee = db.query(Employee).filter(Employee.employee_id == hod_id).first()
            if not hod_employee:
                return jsonify({"success": False, "message": f"Employee '{hod_id}' not found."}), 404
            department = department or (hod_employee.department or "").strip()

        if not department:
            return jsonify({
                "success": False,
                "message": "Department could not be detected. The selected employee has no assigned department."
            }), 400

        query = db.query(Employee).filter(
            Employee.department == department,
            Employee.employee_status.ilike("active")
        )
        if hod_id:
            query = query.filter(Employee.employee_id != hod_id)
        department_employees = query.all()

        hod_rank = get_designation_seniority(hod_employee.designation)[0] if hod_employee else 0
        grouped = {}
        for employee in department_employees:
            designation = (employee.designation or "").strip() or "General Staff"
            rank, tier_name = get_designation_seniority(designation)
            if hod_rank and rank < hod_rank:
                continue

            key = (rank, designation.upper())
            grouped.setdefault(key, {
                "rank": rank,
                "tier_name": tier_name,
                "designation": designation,
                "employees": [],
            })["employees"].append({
                "employee_id": employee.employee_id,
                "employee_name": employee.employee_name or employee.employee_id,
                "designation": employee.designation or "",
                "department": employee.department or department,
                "email_id": employee.email_id or "",
                "source_type": employee.source_type,
            })

        hierarchy_levels = []
        for index, item in enumerate(sorted(grouped.values(), key=lambda value: (value["rank"], value["designation"].upper())), start=1):
            hierarchy_levels.append({
                "level_order": index,
                "rank": item["rank"],
                "tier_name": item["tier_name"],
                "designation": item["designation"],
                "stage_name": f"{item['designation'].title()} Review",
                "employees": sorted(item["employees"], key=lambda employee: employee["employee_name"]),
                "count": len(item["employees"]),
            })

        return jsonify({
            "success": True,
            "department": department,
            "hod": hod_employee.to_dict() if hod_employee else None,
            "total_active_subordinates": len(department_employees),
            "hierarchy_levels": hierarchy_levels,
        })
    except Exception as error:
        app.logger.exception("Unable to generate department hierarchy")
        return jsonify({"success": False, "message": str(error)}), 500
    finally:
        db.close()


@app.route("/department/admin/submit", methods=["POST"])
@require_admin
def submit_guest_house():
    guest = request.form.get("guest", "").strip()
    checkin = request.form.get("checkin", "")
    checkout = request.form.get("checkout", "")
    purpose = request.form.get("purpose", "").strip()

    if not guest or not checkin or not checkout:
        flash("Fill in guest name and both dates.", "error")
        return redirect(url_for("admin_module"))

    new_req = GuestHouseRequest(
        id=next_id(),
        guest=guest,
        checkin=checkin,
        checkout=checkout,
        purpose=purpose,
        stage="pending_dept_head",
        remark="",
        rejected_at=None,
        created_by=session.get("emp_id"),
    )

    db = SessionLocal()
    try:
        db.add(new_req)
        db.commit()
        flash(f"Guest house request {new_req.id} submitted successfully.", "success")
    except Exception as e:
        db.rollback()
        flash(f"Error saving request: {e}", "error")
    finally:
        db.close()

    return redirect(url_for("admin_module"))



# ---------------------------------------------------------------------------
# Approval Management Module (Admin)
# ---------------------------------------------------------------------------
@app.route("/admin/approval-management")
@require_admin
def approval_management():
    db = SessionLocal()
    try:
        workflows = db.query(ApprovalWorkflow).order_by(ApprovalWorkflow.id).all()
        employees = db.query(Employee).order_by(Employee.employee_name).all()
        workflows_data = []
        for wf in workflows:
            workflows_data.append({
                "id": wf.id,
                "name": wf.name,
                "code": wf.code,
                "description": wf.description or "",
                "is_active": wf.is_active,
                "flow_data": wf.flow_data or "null",
                "form_schema": wf.get_form_schema(),
            })
        employees_data = []
        for emp in employees:
            employees_data.append({
                "employee_id": emp.employee_id,
                "employee_name": emp.employee_name or "",
                "designation": emp.designation or "",
                "department": emp.department or "",
                "email_id": emp.email_id or "",
                "source_type": emp.source_type,
            })
        pending_approvals_count = db.query(ApprovalRequest).filter(ApprovalRequest.status == "pending").count()
        return render_template(
            "approval_management.html",
            active="approvals_mgmt",
            workflows=workflows_data,
            employees=employees_data,
            pending_approvals_count=pending_approvals_count,
        )
    finally:
        db.close()


@app.route("/admin/approval-workflows/create", methods=["POST"])
@require_admin
def create_approval_workflow():
    import json as _json
    payload = request.get_json(silent=True) or request.form
    name = (payload.get("name") or "").strip()
    raw_code = (payload.get("code") or "").strip()
    code = raw_code.lower().replace(" ", "_") if raw_code else name.lower().replace(" ", "_")
    description = (payload.get("description") or "").strip()

    if not name or not code:
        return jsonify({"success": False, "message": "Name and code are required."}), 400

    db = SessionLocal()
    try:
        existing = db.query(ApprovalWorkflow).filter(
            (ApprovalWorkflow.name == name) | (ApprovalWorkflow.code == code)
        ).first()
        if existing:
            return jsonify({"success": False, "message": f"A workflow with name '{name}' or code '{code}' already exists."}), 409

        wf = ApprovalWorkflow(
            name=name,
            code=code,
            description=description,
            is_active=True
        )
        db.add(wf)
        db.flush()

        # Seed default Step 1: Final Approval
        final_step = ApprovalWorkflowStep(
            workflow_id=wf.id,
            step_order=1,
            step_name="Final Approval",
            is_final=True,
            parent_step_id=None,
        )
        db.add(final_step)
        db.flush()

        # Seed default Step 2: Review Stage (Constant)
        hr_step = ApprovalWorkflowStep(
            workflow_id=wf.id,
            step_order=2,
            step_name="Stage 2 Review",
            is_final=False,
            parent_step_id=final_step.id,
        )
        db.add(hr_step)
        db.flush()

        # Seed default Step 3: Department Head Review (HOD Branch 1)
        hod_step = ApprovalWorkflowStep(
            workflow_id=wf.id,
            step_order=3,
            step_name="Department Head Review",
            is_final=False,
            parent_step_id=hr_step.id,
        )
        db.add(hod_step)
        db.flush()

        initial_flow_data = {
            "workflow_id": wf.id,
            "name": wf.name,
            "code": wf.code,
            "stages": [
                {
                    "id": f"stage-{final_step.id}",
                    "db_id": final_step.id,
                    "name": "Final Approval",
                    "is_final": True,
                    "order": 1,
                    "parent_id": None,
                    "approvers": []
                },
                {
                    "id": f"stage-{hr_step.id}",
                    "db_id": hr_step.id,
                    "name": "Stage 2 Review",
                    "is_final": False,
                    "is_hr_stage": True,
                    "order": 2,
                    "parent_id": f"stage-{final_step.id}",
                    "approvers": []
                },
                {
                    "id": f"stage-{hod_step.id}",
                    "db_id": hod_step.id,
                    "name": "Department Head Review",
                    "is_final": False,
                    "is_hod_stage": True,
                    "branch_id": "branch-1",
                    "order": 3,
                    "parent_id": f"stage-{hr_step.id}",
                    "approvers": []
                }
            ]
        }
        wf.flow_data = _json.dumps(initial_flow_data)
        db.commit()
        db.refresh(wf)
        return jsonify({
            "success": True,
            "id": wf.id,
            "name": wf.name,
            "code": wf.code,
            "workflow": wf.to_dict(),
            "flow_data": initial_flow_data
        })
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        db.close()


@app.route("/admin/approval-workflows/<int:workflow_id>/save", methods=["POST"])
@require_admin
def save_approval_workflow(workflow_id):
    import json as _json
    db = SessionLocal()
    try:
        wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == workflow_id).first()
        if not wf:
            return jsonify({"success": False, "message": "Workflow not found."}), 404

        data = request.get_json(force=True)
        flow_data = data.get("flow_data") or {}
        stages = flow_data.get("stages", [])

        # Update workflow metadata if provided
        if "name" in data and data["name"].strip():
            wf.name = data["name"].strip()
        if "description" in data:
            wf.description = data["description"].strip()
        if "form_schema" in data:
            form_schema_val = data["form_schema"]
            if isinstance(form_schema_val, dict):
                wf.form_schema = _json.dumps(form_schema_val)
            elif isinstance(form_schema_val, str):
                wf.form_schema = form_schema_val.strip() if form_schema_val.strip() else None
            elif form_schema_val is None:
                wf.form_schema = None

        # 1. Clean out existing steps and approvers for complete synchronization
        db.query(ApprovalWorkflowStep).filter(ApprovalWorkflowStep.workflow_id == workflow_id).delete(synchronize_session=False)
        db.flush()

        # 2. Re-create steps and build client-id to db-step mapping
        stage_map = {}
        for index, stg in enumerate(stages):
            client_id = str(stg.get("id") or f"stg-{index}")
            step = ApprovalWorkflowStep(
                workflow_id=workflow_id,
                step_order=int(stg.get("order") or (index + 1)),
                step_name=(stg.get("name") or f"Stage {index + 1}").strip(),
                is_final=bool(stg.get("is_final", False)),
                parent_step_id=None,
            )
            db.add(step)
            db.flush()
            stage_map[client_id] = step
            stg["db_id"] = step.id

        # 3. Resolve parent_step_id hierarchy
        for stg in stages:
            client_id = str(stg.get("id"))
            parent_client_id = stg.get("parent_id")
            if parent_client_id and str(parent_client_id) in stage_map:
                stage_map[client_id].parent_step_id = stage_map[str(parent_client_id)].id

        # 4. Create approver assignments (many-to-many junction)
        for stg in stages:
            client_id = str(stg.get("id"))
            step_record = stage_map.get(client_id)
            if not step_record:
                continue

            approver_list = stg.get("approvers", [])
            seen_emp_ids = set()
            for appr in approver_list:
                emp_id = (appr.get("employee_id") or "").strip()
                if not emp_id or emp_id in seen_emp_ids:
                    continue
                seen_emp_ids.add(emp_id)

                # Verify employee exists in DB
                emp_exists = db.query(Employee).filter(Employee.employee_id == emp_id).first()
                if not emp_exists:
                    continue

                assignment = ApprovalStepApprover(
                    step_id=step_record.id,
                    employee_id=emp_id,
                    role_label=(appr.get("role_label") or "").strip() or None
                )
                db.add(assignment)

        # 5. Persist visual flow_data JSON
        wf.flow_data = _json.dumps(flow_data)
        db.commit()
        db.refresh(wf)
        invalidate_roles_cache()

        return jsonify({
            "success": True,
            "message": f"Workflow '{wf.name}' saved successfully.",
            "workflow": wf.to_dict()
        })
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        db.close()


@app.route("/admin/approval-workflows/<int:workflow_id>/json", methods=["GET"])
@require_admin
def get_approval_workflow_json(workflow_id):
    import json as _json
    db = SessionLocal()
    try:
        wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == workflow_id).first()
        if not wf:
            return jsonify({"success": False, "message": "Workflow not found."}), 404
        flow_data = _json.loads(wf.flow_data) if wf.flow_data else None
        return jsonify({
            "success": True,
            "id": wf.id,
            "name": wf.name,
            "code": wf.code,
            "description": wf.description or "",
            "is_active": wf.is_active,
            "flow_data": flow_data,
            "form_schema": wf.get_form_schema(),
            "workflow": wf.to_dict()
        })
    finally:
        db.close()


@app.route("/admin/approval-workflows/<int:workflow_id>/form-schema", methods=["GET", "POST"])
@require_admin
def manage_workflow_form_schema(workflow_id):
    import json as _json
    db = SessionLocal()
    try:
        wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == workflow_id).first()
        if not wf:
            return jsonify({"success": False, "message": "Workflow not found."}), 404
        if request.method == "POST":
            payload = request.get_json(force=True) or {}
            form_schema = payload.get("form_schema") if "form_schema" in payload else payload
            if isinstance(form_schema, dict):
                wf.form_schema = _json.dumps(form_schema)
            elif isinstance(form_schema, str):
                wf.form_schema = form_schema.strip() or None
            elif form_schema is None:
                wf.form_schema = None
            db.commit()
            db.refresh(wf)
            return jsonify({
                "success": True,
                "message": f"Form configuration saved for '{wf.name}'.",
                "form_schema": wf.get_form_schema()
            })
        return jsonify({
            "success": True,
            "workflow_id": wf.id,
            "name": wf.name,
            "form_schema": wf.get_form_schema()
        })
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        db.close()


@app.route("/api/workflows/<int:workflow_id>/form", methods=["GET"])
def get_workflow_form_api(workflow_id):
    db = SessionLocal()
    try:
        wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == workflow_id, ApprovalWorkflow.is_active == True).first()
        if not wf:
            return jsonify({"success": False, "message": "Workflow not found."}), 404
        schema = wf.get_form_schema()
        return jsonify({
            "success": True,
            "workflow_id": wf.id,
            "workflow_name": wf.name,
            "workflow_code": wf.code,
            "form_schema": schema,
            "has_dynamic_form": bool(schema and schema.get("enabled", True) and schema.get("fields"))
        })
    finally:
        db.close()


@app.route("/admin/approval-workflows/<int:workflow_id>/delete", methods=["POST"])
@require_admin
def delete_approval_workflow(workflow_id):
    db = SessionLocal()
    try:
        wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == workflow_id).first()
        if not wf:
            return jsonify({"success": False, "message": "Workflow not found."}), 404
        workflow_name = wf.name
        db.delete(wf)
        db.commit()
        return jsonify({"success": True, "message": f"Workflow '{workflow_name}' deleted successfully."})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        db.close()


@app.route("/admin/approval-workflows/<int:workflow_id>/rename", methods=["POST"])
@require_admin
def rename_approval_workflow(workflow_id):
    import json as _json
    payload = request.get_json(silent=True) or request.form
    new_name = (payload.get("name") or "").strip()
    new_description = (payload.get("description") or "").strip()
    if not new_name:
        return jsonify({"success": False, "message": "Workflow name cannot be empty."}), 400

    db = SessionLocal()
    try:
        wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == workflow_id).first()
        if not wf:
            return jsonify({"success": False, "message": "Workflow not found."}), 404

        # Check duplicate name
        dup = db.query(ApprovalWorkflow).filter(
            ApprovalWorkflow.name == new_name,
            ApprovalWorkflow.id != workflow_id
        ).first()
        if dup:
            return jsonify({"success": False, "message": f"Another workflow named '{new_name}' already exists."}), 409

        wf.name = new_name
        wf.description = new_description
        if wf.flow_data:
            try:
                fd = _json.loads(wf.flow_data)
                fd["name"] = new_name
                wf.flow_data = _json.dumps(fd)
            except Exception:
                pass

        db.commit()
        return jsonify({"success": True, "message": "Workflow renamed successfully.", "name": wf.name})
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Department Approval Hierarchy Engine
# ---------------------------------------------------------------------------
def get_designation_seniority(designation: str) -> tuple[int, str]:
    """
    Returns (rank_number, tier_title) where lower rank_number corresponds
    to higher corporate seniority.
    """
    if not designation:
        return (15, "Support Staff & Other Designations")
    u = designation.upper().strip()

    # Tier 1: Head / Executive Leadership
    if any(k in u for k in ["VICE PRESIDENT", "UNIT HEAD", "ASSOCIATE VICE PRESIDENT"]):
        return (1, "Head / Executive Leadership")
    # Tier 2: General Manager
    if "GENERAL MANAGER" in u and not any(k in u for k in ["DEPUTY", "DY", "ASST", "ASSISTANT"]):
        return (2, "General Manager")
    # Tier 3: Deputy / Assistant General Manager
    if any(k in u for k in ["DEPUTY GENERAL MANAGER", "ASSISTANT GENERAL MANAGER", "DGM", "DY. GENERAL MANAGER", "DY GENERAL MANAGER", "ASST GENERAL MANAGER", "ASST. GENERAL MANAGER"]):
        return (3, "Deputy / Assistant General Manager")
    # Tier 4: Senior Manager
    if any(k in u for k in ["SENIOR MANAGER", "SR.MANAGER", "SR. MANAGER", "SR MANAGER"]):
        return (4, "Senior Manager")
    # Tier 5: Manager / Site Lead / Owner
    if any(k in u for k in ["MANAGER", "SITE INCHARGE", "LEAD", "OWNER"]) and not any(k in u for k in ["DEPUTY", "DY", "ASST", "ASSISTANT", "JR", "JUNIOR"]):
        return (5, "Manager")
    # Tier 6: Deputy / Junior Manager
    if any(k in u for k in ["DEPUTY MANAGER", "DY. MANAGER", "DY MANAGER", "JR.MANAGER", "JR. MANAGER", "JR MANAGER"]):
        return (6, "Deputy / Junior Manager")
    # Tier 7: Assistant Manager
    if any(k in u for k in ["ASSISTANT MANAGER", "ASST. MANAGER", "ASST MANAGER"]):
        return (7, "Assistant Manager")
    # Tier 8: Senior Executive / Officer / Accountant
    if any(k in u for k in ["SENIOR ENGINEER", "SR. ENGINEER", "SR.ENGINEER", "SR ENGINEER", "SENIOR OFFICER", "SR. OFFICER", "SR.OFFICER", "SR OFFICER", "HR &ADMIN OFFICER", "HR & ADMIN OFFICER", "ACCOUNTANT"]):
        return (8, "Senior Executive / Officer / Accountant")
    # Tier 9: Executive / Officer / Engineer
    if ("ENGINEER" in u or "OFFICER" in u) and not any(k in u for k in ["ASST", "ASSISTANT", "JR", "JUNIOR", "GET", "TRAINEE", "SENIOR", "SR"]):
        return (9, "Executive / Officer / Engineer")
    # Tier 10: Assistant Officer / Assistant Engineer / Assistant Admin
    if any(k in u for k in ["ASSISTANT ENGINEER", "ASST. ENGINEER", "ASST ENGINEER", "ASSISTANT OFFICER", "ASST. OFFICER", "ASST OFFICER", "ASSISTANT ADMIN", "ASST. ADMIN", "ASST ADMIN"]):
        return (10, "Assistant Officer / Engineer / Admin")
    # Tier 11: Junior Engineer / Junior Officer / GET
    if any(k in u for k in ["JUNIOR", "JR.", "JR ", "GET", "GRADUATE ENGINEER TRAINEE", "TRAINEE"]):
        return (11, "Junior Officer / Junior Engineer / Trainee")
    # Tier 12: Supervisor / Foreman
    if any(k in u for k in ["SUPERVISOR", "FOREMAN"]):
        return (12, "Supervisor / Foreman")
    # Tier 13: Assistant / Administrative Staff
    if any(k in u for k in ["ASSISTANT", "RECEPTIONIST"]):
        return (13, "Assistant / Staff")
    # Tier 14: Skilled Technical Trades
    if any(k in u for k in ["TECHNICIAN", "ELECTRICIAN", "MECHANIC", "FITTER", "WELDER", "OPERATOR", "RIGGER", "CARPENTER", "PLUMBER", "MASION", "TECHNICAL"]):
        return (14, "Technical & Skilled Trades")
    # Tier 15: Support Staff
    return (15, "Support Staff & Other Designations")


def is_employee_hod(applicant: Optional[Employee], user: Optional[User] = None, session_role: Optional[str] = None) -> bool:
    """
    Determine if the applicant is a Department Head (HOD) or Executive.
    Returns True if:
      - session_role or user.role is 'dept_head' or 'hod'
      - applicant's designation seniority rank <= 5 (Manager, Senior Manager, DGM, AGM, GM, VP)
      - applicant has 'HEAD' or 'MANAGER' in their title
    """
    if session_role in ("dept_head", "hod"):
        return True
    if user and user.role in ("dept_head", "hod"):
        return True
    if applicant:
        rank, _ = get_designation_seniority(applicant.designation)
        if rank <= 5:
            return True
        desig_up = (applicant.designation or "").upper()
        if "HEAD" in desig_up:
            return True
    return False


DEPT_ALIASES = {
    "IT": "INFORMATION TECHNOLOGY",
    "HR": "HR & ADMIN",
    "HUMAN RESOURCES": "HR & ADMIN",
    "ACCOUNTS & FINANCE": "ACCOUNTS",
    "FINANCE": "ACCOUNTS",
    "MARINE OPS": "Marine Operations",
}


def get_department_hod(db, department: Optional[str]) -> Optional[Employee]:
    """
    Look up the active Department Head (HOD) for a given department name.
    Prioritizes staff list, sorted by highest corporate seniority (lowest rank number).
    Handles department aliases (e.g. IT -> INFORMATION TECHNOLOGY, HR -> HR & ADMIN).
    """
    if not department:
        return None
    dept_clean = department.strip()
    target_names = [dept_clean]
    dept_upper = dept_clean.upper()
    if dept_upper in DEPT_ALIASES:
        target_names.append(DEPT_ALIASES[dept_upper])
    for k, v in DEPT_ALIASES.items():
        if v.upper() == dept_upper and k not in target_names:
            target_names.append(k)

    # 1. Exact match on target department names
    emps = (
        db.query(Employee)
        .filter(
            Employee.department.in_(target_names),
            Employee.employee_status.ilike("active")
        )
        .all()
    )
    # 2. Case-insensitive exact match
    if not emps:
        filters = [Employee.department.ilike(name) for name in target_names]
        emps = (
            db.query(Employee)
            .filter(
                or_(*filters),
                Employee.employee_status.ilike("active")
            )
            .all()
        )
    # 3. Substring match if still not found
    if not emps:
        emps = (
            db.query(Employee)
            .filter(
                Employee.department.ilike(f"%{dept_clean}%"),
                Employee.employee_status.ilike("active")
            )
            .all()
        )
    if not emps:
        return None

    def hod_sort_key(e):
        src_priority = 0 if (e.source_type or "").lower() == "staff" else 1
        rank, _ = get_designation_seniority(e.designation)
        return (src_priority, rank)

    sorted_emps = sorted(emps, key=hod_sort_key)
    return sorted_emps[0]


def get_employee_core_role(db, emp_id: Optional[str]) -> str:
    """
    Returns 'unit_head', 'hr_head', 'dept_head', or 'employee' for a given employee ID.
    Sameer Gayakwad (4050163) is Unit Head.
    Parimita Behera (4050702) is HR Head.
    """
    if not emp_id:
        return "employee"
    eid = str(emp_id).strip()
    if eid == "4050163":
        return "unit_head"
    if eid == "4050702":
        return "hr_head"
    emp = db.query(Employee).filter(Employee.employee_id == eid).first()
    if not emp:
        return "employee"
    desig = (emp.designation or "").upper()
    dept = (emp.department or "").upper()
    if "UNIT HEAD" in desig:
        return "unit_head"
    if "HR" in dept or "HUMAN RESOURCE" in dept:
        return "hr_head"
    if dept:
        hod = get_department_hod(db, dept)
        if hod and str(hod.employee_id).strip() == eid:
            return "dept_head"
    return "employee"


def get_unit_head_from_approval_management(db, workflow_id: Optional[Union[int, str]] = None) -> Optional[Employee]:
    """
    Retrieve the exact Unit Head explicitly mentioned in Approval Management.
    Prioritizes the actual Unit Head approver (4050163 / 'Unit Head' designation) across workflow stages.
    """
    import json as _json

    # 1. Target workflows to check: specific workflow first, then all active workflows
    wfs = []
    if workflow_id:
        try:
            if isinstance(workflow_id, int) or (isinstance(workflow_id, str) and str(workflow_id).isdigit()):
                wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == int(workflow_id)).first()
            else:
                w_str = str(workflow_id).strip()
                wf = db.query(ApprovalWorkflow).filter(
                    (ApprovalWorkflow.name.ilike(w_str)) | (ApprovalWorkflow.code.ilike(w_str))
                ).first()
            if wf:
                wfs.append(wf)
        except Exception:
            pass
    other_wfs = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.is_active == True).order_by(ApprovalWorkflow.id.asc()).all()
    for w in other_wfs:
        if w not in wfs:
            wfs.append(w)

    for wf in wfs:
        # A. Check flow_data JSON: first look for explicit Unit Head approver
        if wf.flow_data:
            try:
                fd = _json.loads(wf.flow_data) if isinstance(wf.flow_data, str) else wf.flow_data
                stages = fd.get("stages", []) if isinstance(fd, dict) else []
                # First pass: check for actual Unit Head employee
                for stg in stages:
                    for appr in stg.get("approvers", []):
                        emp_id = (appr.get("employee_id") or "").strip()
                        if emp_id and get_employee_core_role(db, emp_id) == "unit_head":
                            emp = db.query(Employee).filter(
                                Employee.employee_id == emp_id,
                                Employee.employee_status.ilike("active")
                            ).first()
                            if emp:
                                return emp

                # Second pass: check by stage name / role flags
                for stg in stages:
                    s_name = (stg.get("name") or "").upper()
                    stg_role = (stg.get("role") or "").lower()
                    is_unit_head = (
                        "UNIT HEAD" in s_name
                        or "UNIT_HEAD" in s_name
                        or stg_role == "unit_head"
                        or (stg.get("is_final") and "HR" not in s_name and "STAFF" not in s_name and "DEPARTMENT" not in s_name)
                    )
                    if is_unit_head:
                        for appr in stg.get("approvers", []):
                            emp_id = appr.get("employee_id")
                            if emp_id and get_employee_core_role(db, emp_id) != "hr_head":
                                emp = db.query(Employee).filter(
                                    Employee.employee_id == emp_id,
                                    Employee.employee_status.ilike("active")
                                ).first()
                                if emp:
                                    return emp
            except Exception:
                pass

        # B. Check database steps and approvers
        if wf.steps:
            for step in wf.steps:
                for appr in step.approvers:
                    if appr.employee and get_employee_core_role(db, appr.employee.employee_id) == "unit_head":
                        return appr.employee

            for step in wf.steps:
                s_name = (step.step_name or "").upper()
                is_unit_head = (
                    "UNIT HEAD" in s_name
                    or "UNIT_HEAD" in s_name
                    or (step.is_final and "HR" not in s_name and "STAFF" not in s_name and "DEPARTMENT" not in s_name)
                )
                if is_unit_head and step.approvers:
                    for appr in step.approvers:
                        if appr.employee and get_employee_core_role(db, appr.employee.employee_id) != "hr_head" and (appr.employee.employee_status or "").lower() == "active":
                            return appr.employee

    # Fallback to Unit Head in employee master
    return db.query(Employee).filter(Employee.employee_id == "4050163").first()


def get_hr_head_from_approval_management(db, workflow_id: Optional[Union[int, str]] = None) -> Optional[Employee]:
    """
    Retrieve the exact HR Head explicitly mentioned in Approval Management.
    Prioritizes the actual HR Head approver (4050702 / HR department) across workflow stages.
    """
    import json as _json

    wfs = []
    if workflow_id:
        try:
            if isinstance(workflow_id, int) or (isinstance(workflow_id, str) and str(workflow_id).isdigit()):
                wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == int(workflow_id)).first()
            else:
                w_str = str(workflow_id).strip()
                wf = db.query(ApprovalWorkflow).filter(
                    (ApprovalWorkflow.name.ilike(w_str)) | (ApprovalWorkflow.code.ilike(w_str))
                ).first()
            if wf:
                wfs.append(wf)
        except Exception:
            pass
    other_wfs = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.is_active == True).order_by(ApprovalWorkflow.id.asc()).all()
    for w in other_wfs:
        if w not in wfs:
            wfs.append(w)

    for wf in wfs:
        # A. Check flow_data JSON: first look for explicit HR Head approver
        if wf.flow_data:
            try:
                fd = _json.loads(wf.flow_data) if isinstance(wf.flow_data, str) else wf.flow_data
                stages = fd.get("stages", []) if isinstance(fd, dict) else []
                # First pass: check for actual HR Head employee
                for stg in stages:
                    for appr in stg.get("approvers", []):
                        emp_id = (appr.get("employee_id") or "").strip()
                        if emp_id and get_employee_core_role(db, emp_id) == "hr_head":
                            emp = db.query(Employee).filter(
                                Employee.employee_id == emp_id,
                                Employee.employee_status.ilike("active")
                            ).first()
                            if emp:
                                return emp

                # Second pass: check by stage name / role flags
                for stg in stages:
                    s_name = (stg.get("name") or "").upper()
                    stg_role = (stg.get("role") or "").lower()
                    is_hr = (
                        stg.get("is_hr_stage")
                        or stg_role == "hr_head"
                        or (
                            ("HR" in s_name or "HUMAN RESOURCE" in s_name)
                            and not stg.get("is_final")
                            and not stg.get("is_hod_stage")
                            and "STAFF" not in s_name
                        )
                    )
                    if is_hr:
                        for appr in stg.get("approvers", []):
                            emp_id = appr.get("employee_id")
                            if emp_id and get_employee_core_role(db, emp_id) != "unit_head":
                                emp = db.query(Employee).filter(
                                    Employee.employee_id == emp_id,
                                    Employee.employee_status.ilike("active")
                                ).first()
                                if emp:
                                    return emp
            except Exception:
                pass

        # B. Check database steps and approvers
        if wf.steps:
            for step in wf.steps:
                for appr in step.approvers:
                    if appr.employee and get_employee_core_role(db, appr.employee.employee_id) == "hr_head":
                        return appr.employee

            for step in wf.steps:
                s_name = (step.step_name or "").upper()
                is_hr = (
                    step.step_order == 2
                    or (
                        ("HR" in s_name or "HUMAN RESOURCE" in s_name)
                        and ("HEAD" in s_name or "REVIEW" in s_name)
                        and not step.is_final
                        and "STAFF" not in s_name
                    )
                )
                if is_hr and step.approvers:
                    for appr in step.approvers:
                        if appr.employee and get_employee_core_role(db, appr.employee.employee_id) != "unit_head" and (appr.employee.employee_status or "").lower() == "active":
                            return appr.employee

    # Fallback to HR Head in employee master
    return db.query(Employee).filter(Employee.employee_id == "4050702").first()


def get_hr_head(db, workflow_id: Optional[int] = None) -> Optional[Employee]:
    """Retrieve the exact HR Head explicitly mentioned in Approval Management."""
    return get_hr_head_from_approval_management(db, workflow_id)


def get_unit_head(db, workflow_id: Optional[int] = None) -> Optional[Employee]:
    """Retrieve the exact Unit Head explicitly mentioned in Approval Management."""
    return get_unit_head_from_approval_management(db, workflow_id)


_ROLES_CACHE = {"ts": 0.0, "uh_ids": set(), "hr_ids": set(), "hod_ids": set()}


def invalidate_roles_cache():
    """Clear the cached approver sets so subsequent role lookups fetch latest DB configuration."""
    _ROLES_CACHE["ts"] = 0.0


def get_allowed_roles_for_employee(db, emp_id: Optional[str]) -> list:
    """
    Determine the allowed 'View as' roles for a given employee ID.
    Per business rules:
      - All authenticated non-admin users have 'employee' access.
      - Users preset in Approval Management or active workflows as Unit Head get 'unit_head'.
      - Users preset in Approval Management or active workflows as HR Head get 'hr_head'.
      - Users preset as Department Heads (HODs) get 'dept_head'.
      - Regular employees (not preset as Unit Head, HR Head, or HOD) only get ['employee']
        and will NOT have the 'View as' switcher displayed.
    """
    if not emp_id:
        return ["employee"]

    emp_id_str = str(emp_id).strip()
    import time as _time
    import json as _json

    now = _time.time()
    if now - _ROLES_CACHE["ts"] > 15.0:
        uh_ids = set()
        uh_default = get_unit_head_from_approval_management(db)
        if uh_default and uh_default.employee_id:
            uh_ids.add(str(uh_default.employee_id).strip())

        hr_ids = set()
        hr_default = get_hr_head_from_approval_management(db)
        if hr_default and hr_default.employee_id:
            hr_ids.add(str(hr_default.employee_id).strip())

        hod_ids = set()
        try:
            departments = [d[0] for d in db.query(Employee.department).distinct().all() if d[0]]
            for dept in departments:
                hod = get_department_hod(db, dept)
                if hod and hod.employee_id:
                    hid = str(hod.employee_id).strip()
                    if hid not in hr_ids and hid not in uh_ids:
                        hod_ids.add(hid)
        except Exception as e:
            logger.error(f"Error querying department HODs: {e}")

        try:
            workflows = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.is_active == True).all()
            for wf in workflows:
                if wf.flow_data:
                    fd = _json.loads(wf.flow_data) if isinstance(wf.flow_data, str) else wf.flow_data
                    for stg in fd.get("stages", []):
                        s_name = (stg.get("name") or "").upper()
                        stg_role = (stg.get("role") or "").lower()
                        is_uh = "UNIT HEAD" in s_name or stg_role == "unit_head" or stg.get("is_final")
                        is_hr = "HR" in s_name or "HUMAN RESOURCE" in s_name or stg_role == "hr_head" or stg.get("is_hr_stage")
                        is_hod = ("HEAD" in s_name or "HOD" in s_name or stg_role == "dept_head") and not is_uh and not is_hr
                        for appr in stg.get("approvers", []):
                            aid = str(appr.get("employee_id") or "").strip()
                            if not aid:
                                continue
                            if is_uh:
                                uh_ids.add(aid)
                            elif is_hr:
                                hr_ids.add(aid)
                            elif is_hod and aid not in hr_ids and aid not in uh_ids:
                                hod_ids.add(aid)

                if wf.steps:
                    for step in wf.steps:
                        s_name = (step.step_name or "").upper()
                        is_uh = "UNIT HEAD" in s_name or step.is_final
                        is_hr = "HR" in s_name or "HUMAN RESOURCE" in s_name
                        is_hod = ("HEAD" in s_name or "HOD" in s_name) and not is_uh and not is_hr
                        for appr in step.approvers:
                            aid = str(appr.employee_id or "").strip()
                            if not aid:
                                continue
                            if is_uh:
                                uh_ids.add(aid)
                            elif is_hr:
                                hr_ids.add(aid)
                            elif is_hod and aid not in hr_ids and aid not in uh_ids:
                                hod_ids.add(aid)
        except Exception as e:
            logger.error(f"Error querying workflow approvers: {e}")

        _ROLES_CACHE["ts"] = now
        _ROLES_CACHE["uh_ids"] = uh_ids
        _ROLES_CACHE["hr_ids"] = hr_ids
        _ROLES_CACHE["hod_ids"] = hod_ids

    uh_ids = _ROLES_CACHE["uh_ids"]
    hr_ids = _ROLES_CACHE["hr_ids"]
    hod_ids = _ROLES_CACHE["hod_ids"]

    roles = ["employee"]
    if emp_id_str in uh_ids:
        roles.append("unit_head")
    if emp_id_str in hr_ids:
        roles.append("hr_head")
    if emp_id_str in hod_ids:
        roles.append("dept_head")

    try:
        user_rec = db.query(User).filter(User.emp_id == emp_id_str).first()
        if user_rec and user_rec.role in ("unit_head", "hr_head", "dept_head"):
            if user_rec.role not in roles:
                roles.append(user_rec.role)
    except Exception:
        pass

    return roles


def sync_workflow_step_approvers_from_flow_data(db):
    """
    Ensure all ApprovalWorkflowStep records have their ApprovalStepApprover entries
    synchronized with the approvers configured in ApprovalWorkflow.flow_data.
    This guarantees relational steps always match the visual designer in Approval Management.
    """
    import json as _json
    try:
        wfs = db.query(ApprovalWorkflow).all()
        for wf in wfs:
            if not wf.flow_data:
                continue
            fd = _json.loads(wf.flow_data) if isinstance(wf.flow_data, str) else wf.flow_data
            stages = fd.get("stages", []) if isinstance(fd, dict) else []
            for stg in stages:
                step_id = stg.get("db_id")
                step = db.query(ApprovalWorkflowStep).filter(ApprovalWorkflowStep.id == step_id).first() if step_id else None
                if not step:
                    step = db.query(ApprovalWorkflowStep).filter(
                        ApprovalWorkflowStep.workflow_id == wf.id,
                        ApprovalWorkflowStep.step_name == stg.get("name")
                    ).first()
                if not step:
                    continue

                for appr in stg.get("approvers", []):
                    emp_id = (appr.get("employee_id") or "").strip()
                    if not emp_id:
                        continue
                    exists = db.query(ApprovalStepApprover).filter(
                        ApprovalStepApprover.step_id == step.id,
                        ApprovalStepApprover.employee_id == emp_id
                    ).first()
                    if not exists:
                        emp = db.query(Employee).filter(Employee.employee_id == emp_id).first()
                        if emp:
                            db.add(ApprovalStepApprover(
                                step_id=step.id,
                                employee_id=emp_id,
                                role_label=(appr.get("role_label") or "").strip() or None
                            ))
        db.commit()
    except Exception as e:
        db.rollback()
        app.logger.warning(f"Failed to sync workflow step approvers from flow_data: {e}")


# Run initial sync of workflow step approvers from flow_data
_startup_db = SessionLocal()
try:
    sync_workflow_step_approvers_from_flow_data(_startup_db)
finally:
    _startup_db.close()


def clean_stage_name(name: Optional[str], default_name: str) -> str:
    """Removes hardcoded '(Unit Head)' or '(HR Head)' tags to maintain clean, dynamic stage labels."""
    if not name:
        return default_name
    cleaned = re.sub(r'[\(\[\{]?(?:unit|hr)\s*head[\)\]\}]?', '', name, flags=re.I)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' -:')
    if cleaned.lower() in ('', 'review', 'stage', 'stage 2'):
        return default_name
    return cleaned


def build_request_approval_pipeline(db, applicant: Optional[Employee], department: Optional[str], workflow_id: Optional[int] = None, session_role: Optional[str] = None):
    """
    Constructs the exact approval pipeline according to company hierarchy:
    1. If applicant is a regular employee:
       - Stage 1: Department Head Review (strictly HOD of the employee's department)
       - Stage 2: HR Head Review (strictly HR Head mentioned in Approval Management)
       - Stage 3: Final Approval (Unit Head) (strictly Unit Head mentioned in Approval Management)
    2. If applicant is an HOD (Department Head / Manager / rank <= 5):
       - Stage 1: HR Head Review (strictly HR Head mentioned in Approval Management)
       - Stage 2: Final Approval (Unit Head) (strictly Unit Head mentioned in Approval Management)
    """
    user_record = db.query(User).filter(User.emp_id == applicant.employee_id).first() if applicant else None
    is_hod = is_employee_hod(applicant, user=user_record, session_role=session_role)

    dept_name = (department or (applicant.department if applicant else "") or "").strip() or "Department"

    hod_emp = get_department_hod(db, dept_name)
    hr_head_emp = get_hr_head_from_approval_management(db, workflow_id)
    unit_head_emp = get_unit_head_from_approval_management(db, workflow_id)

    # Check if a workflow is assigned and has specific step details/approvers
    wf = None
    if workflow_id:
        try:
            wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == int(workflow_id)).first()
        except Exception:
            wf = None

    wf_dept_step_name = None
    wf_dept_approver = None
    wf_hr_step_name = None
    wf_hr_approver = None
    wf_unit_step_name = None
    wf_unit_approver = None

    # Step A: Strictly isolate Department HOD, Review Approver, and Final Approver from workflow flow_data
    final_stage_meta = None
    review_stage_meta = None
    if wf and wf.flow_data:
        try:
            import json as _json
            fd = _json.loads(wf.flow_data) if isinstance(wf.flow_data, str) else wf.flow_data
            stages = fd.get("stages", []) if isinstance(fd, dict) else []
            final_stage_meta = next((s for s in stages if s.get("is_final")), None)
            review_stage_meta = next((s for s in stages if not s.get("is_final") and (s.get("is_hr_stage") or s.get("order") == 2 or s.get("parent_id") == (final_stage_meta.get("id") if final_stage_meta else None))), None)

            for stg in stages:
                stg_dept = (stg.get("department") or "").strip().upper()
                if stg_dept and not wf_dept_approver:
                    matches_dept = (
                        stg_dept == dept_name.upper()
                        or DEPT_ALIASES.get(dept_name.upper()) == stg_dept
                        or DEPT_ALIASES.get(stg_dept) == dept_name.upper()
                    )
                    if matches_dept:
                        is_review = (
                            stg.get("is_hod_stage")
                            or stg.get("order", 0) in (3, 4, 5, 6, 7, 8, 9, 10)
                            or "HEAD" in (stg.get("name") or "").upper()
                        )
                        if is_review and stg.get("approvers"):
                            first_appr = stg["approvers"][0]
                            appr_emp_id = first_appr.get("employee_id")
                            if appr_emp_id:
                                appr_emp = db.query(Employee).filter(Employee.employee_id == appr_emp_id).first()
                                if appr_emp:
                                    wf_dept_approver = appr_emp
                                    wf_dept_step_name = stg.get("name")
        except Exception:
            pass

    # 2. If not found in flow_data, search wf.steps specifically matching dept_name
    if not wf_dept_approver and wf and wf.steps:
        for step in wf.steps:
            s_name = (step.step_name or "").upper()
            if step.is_final or "UNIT HEAD" in s_name or "HR" in s_name or step.step_order in (1, 2):
                continue

            dept_appr_match = None
            for a in step.approvers:
                if a.employee:
                    emp_dept = (a.employee.department or "").strip().upper()
                    if emp_dept and (
                        emp_dept == dept_name.upper()
                        or DEPT_ALIASES.get(dept_name.upper()) == emp_dept
                        or DEPT_ALIASES.get(emp_dept) == dept_name.upper()
                    ):
                        dept_appr_match = a.employee
                        break
            if dept_appr_match:
                wf_dept_approver = dept_appr_match
                wf_dept_step_name = step.step_name
                break

            clean_token = dept_name.upper()
            if clean_token in s_name:
                if step.approvers and step.approvers[0].employee:
                    wf_dept_approver = step.approvers[0].employee
                    wf_dept_step_name = step.step_name
                    break

    # 3. Department HOD fallback
    final_hod = wf_dept_approver or hod_emp
    hod_name = final_hod.employee_name if final_hod else f"{dept_name} Head"
    hod_id = final_hod.employee_id if final_hod else None
    dept_stage_title = wf_dept_step_name or f"{dept_name} Head Review"

    # 4. Resolve Review Approver and Final Approver dynamically
    final_emp = None
    final_stage_name = None
    if final_stage_meta and final_stage_meta.get("approvers"):
        aid = final_stage_meta["approvers"][0].get("employee_id")
        if aid:
            final_emp = db.query(Employee).filter(Employee.employee_id == str(aid)).first()
            final_stage_name = final_stage_meta.get("name")
    if not final_emp:
        final_emp = unit_head_emp

    review_emp = None
    review_stage_name = None
    if review_stage_meta and review_stage_meta.get("approvers"):
        aid = review_stage_meta["approvers"][0].get("employee_id")
        if aid:
            review_emp = db.query(Employee).filter(Employee.employee_id == str(aid)).first()
            review_stage_name = review_stage_meta.get("name")
    if not review_emp:
        review_emp = hr_head_emp

    rev_role = get_employee_core_role(db, review_emp.employee_id if review_emp else None)
    fin_role = get_employee_core_role(db, final_emp.employee_id if final_emp else None)

    if rev_role == "employee":
        rev_role = "hr_head"
    if fin_role == "employee":
        fin_role = "unit_head"

    # Derive clean titles free of hardcoded '(Unit Head)' or '(HR Head)' tags
    rev_title = clean_stage_name(review_stage_name, "Stage 2 Review")
    fin_title = clean_stage_name(final_stage_name, "Final Approval")

    if is_hod:
        # HOD Submission:
        # Step 1: Intermediate Review
        # Step 2: Final Authorization
        pipeline_stages = [
            {
                "order": 1,
                "name": rev_title,
                "role": rev_role,
                "approver_name": review_emp.employee_name if review_emp else "Reviewer",
                "approver_id": review_emp.employee_id if review_emp else None,
                "is_final": False,
                "description": "Intermediate Review & Endorsement",
            },
            {
                "order": 2,
                "name": fin_title,
                "role": fin_role,
                "approver_name": final_emp.employee_name if final_emp else "Final Approver",
                "approver_id": final_emp.employee_id if final_emp else None,
                "is_final": True,
                "description": "Executive Leadership & Final Authorization",
            },
        ]
    else:
        # Regular Employee Submission:
        # Step 1: Department Head Review (strictly HOD of applicant's department)
        # Step 2: Intermediate Review
        # Step 3: Final Authorization
        pipeline_stages = [
            {
                "order": 1,
                "name": dept_stage_title,
                "role": "dept_head",
                "approver_name": hod_name,
                "approver_id": hod_id,
                "is_final": False,
                "description": f"{dept_name} Departmental Head Review & Verification",
            },
            {
                "order": 2,
                "name": rev_title,
                "role": rev_role,
                "approver_name": review_emp.employee_name if review_emp else "Reviewer",
                "approver_id": review_emp.employee_id if review_emp else None,
                "is_final": False,
                "description": "Intermediate Review & Endorsement",
            },
            {
                "order": 3,
                "name": fin_title,
                "role": fin_role,
                "approver_name": final_emp.employee_name if final_emp else "Final Approver",
                "approver_id": final_emp.employee_id if final_emp else None,
                "is_final": True,
                "description": "Executive Leadership & Final Authorization",
            },
        ]

    return {
        "is_hod": is_hod,
        "stages": pipeline_stages,
        "total_steps": len(pipeline_stages),
        "initial_stage": pipeline_stages[0]["name"],
        "initial_role": pipeline_stages[0]["role"],
        "initial_approver": pipeline_stages[0]["approver_name"],
        "initial_approver_id": pipeline_stages[0]["approver_id"],
    }


def get_admin_department_recipient(db) -> Optional[Tuple[str, str]]:
    """
    Finds one designated employee from the Admin / HR & ADMIN department to notify upon final approval.
    Returns (email, name) tuple or None.
    Priority:
    1. Config.ADMIN_NOTIFICATION_EMAIL (if set in environment or config)
    2. Active employee in 'HR & ADMIN' / 'ADMIN' department with a valid email.
       Prefers Admin Officer / Deputy Manager (e.g. ADITI RAMESH MORE: aditi.more@jsw.in) or AGM (PARIMITA BEHERA: pms.dppl@jsw.in).
    """
    env_admin_email = getattr(Config, "ADMIN_NOTIFICATION_EMAIL", "").strip()
    if env_admin_email:
        emp = db.query(Employee).filter(Employee.email_id.ilike(env_admin_email)).first()
        name = emp.employee_name if emp else "Admin Department"
        return env_admin_email, name

    # Search employees in admin / hr & admin department
    admin_candidates = (
        db.query(Employee)
        .filter(
            (Employee.department.ilike("%admin%") | Employee.department.ilike("%hr & admin%")),
            Employee.email_id.isnot(None),
            Employee.email_id != "",
            Employee.employee_status.ilike("active")
        )
        .all()
    )

    # Prefer Deputy Manager / Officer / Admin in designation (e.g. ADITI RAMESH MORE)
    for c in admin_candidates:
        if any(term in (c.designation or "").upper() for term in ("MANAGER", "OFFICER", "ADMIN")):
            return c.email_id, c.employee_name

    if admin_candidates:
        return admin_candidates[0].email_id, admin_candidates[0].employee_name

    return None



def is_vehicle_booking(req_item) -> bool:
    """Checks whether a request is for Vehicle / Car / Transport booking."""
    if not req_item:
        return False
    if getattr(req_item, "workflow_id", None) == 7:
        return True
    parts = []
    wf = getattr(req_item, "workflow", None)
    if wf:
        parts.extend([getattr(wf, "name", "") or "", getattr(wf, "code", "") or ""])
    parts.extend([
        getattr(req_item, "request_type", "") or "",
        getattr(req_item, "title", "") or "",
    ])
    details = getattr(req_item, "details", None)
    if details:
        try:
            import json as _j
            d_obj = _j.loads(details) if isinstance(details, str) else details
            if isinstance(d_obj, dict):
                parts.append(d_obj.get("form_title", "") or "")
                for fld in d_obj.get("form_fields", []):
                    parts.append(str(fld.get("name", "")) + " " + str(fld.get("label", "")))
        except Exception:
            pass
    combined = " ".join(parts).lower()
    keywords = ("vehicle", "car", "cab", "transport", "driver", "automobile")
    return any(k in combined for k in keywords)


def dispatch_final_approval_emails(db, req_item, actor_name: str, remarks: str = "") -> None:
    """
    Builds the requisition summary and dispatches final approval notification emails.
    ONLY IN CASE OF VEHICLE BOOKING: triggers email to dppl.admin@jsw.in saying 'PLEASE ARRANGE THE CAR'.
    Also notifies the applicant of the final approval.
    """
    if not req_item:
        return

    req_id = getattr(req_item, "id", None)
    if not req_id:
        return

    # If req_item is GuestHouseRequest, see if an ApprovalRequest counterpart exists
    if not hasattr(req_item, "request_type"):
        ar = db.query(ApprovalRequest).filter(ApprovalRequest.id == req_id).first()
        if ar:
            req_item = ar

    # 1. Resolve End User details
    end_user_email = (getattr(req_item, "applicant_email", None) or "").strip()
    end_user_name = getattr(req_item, "applicant_name", None) or ""
    applicant_emp_id = getattr(req_item, "applicant_emp_id", None) or getattr(req_item, "created_by", "") or ""
    applicant_dept = getattr(req_item, "department", None) or ""
    applicant_phone = getattr(req_item, "applicant_phone", None) or getattr(req_item, "mobile_no", "") or ""
    start_date = getattr(req_item, "start_date", None) or getattr(req_item, "check_in_date", "") or ""
    end_date = getattr(req_item, "end_date", None) or getattr(req_item, "check_out_date", "") or ""
    purpose = getattr(req_item, "purpose", None) or getattr(req_item, "business_justification", "") or ""
    title = getattr(req_item, "title", None) or getattr(req_item, "guest_name", "") or "Request"

    if hasattr(req_item, "workflow") and req_item.workflow:
        workflow_name = req_item.workflow.name
    elif hasattr(req_item, "request_type"):
        workflow_name = req_item.request_type
    else:
        workflow_name = "Guest House Accommodation"

    if not end_user_email and applicant_emp_id:
        emp = db.query(Employee).filter(Employee.employee_id == str(applicant_emp_id)).first()
        if emp:
            end_user_email = (emp.email_id or "").strip()
            if not end_user_name:
                end_user_name = emp.employee_name or ""
            if not applicant_dept:
                applicant_dept = emp.department or ""
            if not applicant_phone:
                applicant_phone = emp.contact_no or ""

    if not end_user_email:
        gh = db.query(GuestHouseRequest).filter(GuestHouseRequest.id == req_id).first()
        if gh and gh.created_by:
            emp = db.query(Employee).filter(Employee.employee_id == str(gh.created_by)).first()
            if emp:
                end_user_email = (emp.email_id or "").strip()
                if not end_user_name:
                    end_user_name = emp.employee_name or ""

    # 2. Resolve Admin Department Employee
    admin_recipient = get_admin_department_recipient(db)
    admin_email = admin_recipient[0] if admin_recipient else None
    admin_name = admin_recipient[1] if admin_recipient else "Admin Department"

    # 3. Extract custom dynamic form fields if available
    form_fields = []
    details_str = getattr(req_item, "details", None)
    if details_str:
        try:
            details_data = _json.loads(details_str) if isinstance(details_str, str) else details_str
            if isinstance(details_data, dict):
                form_fields = details_data.get("form_fields") or []
                if not form_fields and details_data.get("form_data"):
                    for k, v in details_data["form_data"].items():
                        form_fields.append({"name": k, "label": k.replace("_", " ").title(), "value": v})
        except Exception:
            form_fields = []

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    req_summary = {
        "req_id": req_id,
        "workflow_name": workflow_name,
        "title": title or workflow_name,
        "applicant_name": end_user_name or "Applicant",
        "applicant_emp_id": str(applicant_emp_id),
        "applicant_dept": applicant_dept,
        "applicant_phone": applicant_phone,
        "start_date": start_date,
        "end_date": end_date,
        "purpose": purpose,
        "approved_by": actor_name or "Approving Authority",
        "approved_at": now_str,
        "remarks": remarks or "Approved",
        "form_fields": form_fields,
    }

    # Check vehicle booking vs general workflow
    if is_vehicle_booking(req_item):
        # ONLY IN CASE OF VEHICLE BOOKING: email dppl.admin@jsw.in saying PLEASE ARRANGE THE CAR
        notify_vehicle_arrangement_async(req_summary)
        # Also notify applicant of final approval
        notify_final_approval_async(
            req_summary=req_summary,
            end_user_email=end_user_email,
            end_user_name=end_user_name,
            admin_email=None,  # Dedicated vehicle alert already sent to dppl.admin@jsw.in
            admin_name=None,
        )
    else:
        # Standard non-vehicle process approval notification
        notify_final_approval_async(
            req_summary=req_summary,
            end_user_email=end_user_email,
            end_user_name=end_user_name,
            admin_email=admin_email,
            admin_name=admin_name,
        )


@app.route("/api/department-hierarchy", methods=["GET"])
@require_admin
def get_department_hierarchy():
    hod_id = (request.args.get("hod_id") or "").strip()
    dept = (request.args.get("department") or "").strip()

    db = SessionLocal()
    try:
        hod_emp = None
        if hod_id:
            hod_emp = db.query(Employee).filter(Employee.employee_id == hod_id).first()
            if not hod_emp:
                return jsonify({"success": False, "message": f"Employee '{hod_id}' not found."}), 404
            if not dept:
                dept = hod_emp.department

        if not dept:
            return jsonify({
                "success": False,
                "message": "Department could not be detected. The selected employee has no assigned department."
            }), 400

        # Query all active employees in this department
        query = db.query(Employee).filter(
            Employee.department == dept,
            Employee.employee_status.ilike("active")
        )
        if hod_id:
            query = query.filter(Employee.employee_id != hod_id)

        all_dept_emps = query.all()
        # If staff records are present, prioritize staff; otherwise include all active employees
        staff_dept_emps = [e for e in all_dept_emps if e.source_type in ("staff", "manual")]
        dept_emps = staff_dept_emps if staff_dept_emps else all_dept_emps

        hod_rank = get_designation_seniority(hod_emp.designation)[0] if hod_emp else 0

        # Group all valid subordinates into a single level
        employees_list = []
        for emp in dept_emps:
            desig = (emp.designation or "").strip() or "General Staff"
            rank, tier_name = get_designation_seniority(desig)

            # Do not place individuals more senior than the HOD below the HOD
            if hod_rank and rank < hod_rank:
                continue

            employees_list.append({
                "employee_id": emp.employee_id,
                "employee_name": emp.employee_name or emp.employee_id,
                "designation": emp.designation or "",
                "department": emp.department or dept,
                "email_id": emp.email_id or "",
                "source_type": emp.source_type,
            })

        hierarchy_levels = []
        if employees_list:
            hierarchy_levels.append({
                "level_order": 1,
                "rank": 99,
                "tier_name": "Department Staff",
                "designation": "Multiple Designations",
                "stage_name": "Department Staff",
                "employees": sorted(employees_list, key=lambda x: x["employee_name"]),
                "count": len(employees_list),
            })

        return jsonify({
            "success": True,
            "department": dept,
            "hod": hod_emp.to_dict() if hod_emp else None,
            "total_active_subordinates": len(dept_emps),
            "hierarchy_levels": hierarchy_levels,
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Admin Process Approvals Management Module (New Tab)
# ---------------------------------------------------------------------------
@app.route("/admin/approvals", methods=["GET"])
@require_admin
def admin_approvals():
    db = SessionLocal()
    try:
        requests_query = (
            db.query(ApprovalRequest)
            .order_by(ApprovalRequest.created_at.desc())
            .all()
        )
        all_requests = [r.to_dict() for r in requests_query]

        # Distinct departments for filtering
        dept_rows = (
            db.query(Employee.department)
            .distinct()
            .filter(Employee.department != None)
            .all()
        )
        departments = sorted([d[0] for d in dept_rows if d[0]])

        # Active workflows
        workflows = (
            db.query(ApprovalWorkflow)
            .filter(ApprovalWorkflow.is_active == True)
            .order_by(ApprovalWorkflow.name)
            .all()
        )
        workflows_data = [w.to_dict() for w in workflows]

        employee_count = db.query(Employee).count()
        pending_count = sum(1 for r in all_requests if r["status"] == "pending")
        approved_count = sum(1 for r in all_requests if r["status"] == "approved")
        rejected_count = sum(1 for r in all_requests if r["status"] == "rejected")
        total_count = len(all_requests)

        return render_template(
            "admin_approvals.html",
            active="admin_approvals",
            requests=all_requests,
            departments=departments,
            workflows=workflows_data,
            employee_count=employee_count,
            pending_count=pending_count,
            approved_count=approved_count,
            rejected_count=rejected_count,
            total_count=total_count,
            pending_approvals_count=pending_count,
        )
    finally:
        db.close()


@app.route("/admin/approvals/<req_id>/decide", methods=["POST"])
@require_admin
def decide_admin_approval(req_id):
    import json as _json
    payload = request.get_json(silent=True) or request.form
    decision = (payload.get("decision") or "").strip().lower()
    remarks = (payload.get("remarks") or "").strip()
    advance_mode = (payload.get("advance_mode") or "final_approval").strip()

    db = SessionLocal()
    try:
        req_item = db.query(ApprovalRequest).filter(ApprovalRequest.id == req_id).first()
        if not req_item:
            return jsonify({"success": False, "message": "Request not found"}), 404

        actor_name = f"Admin ({session.get('emp_name') or session.get('emp_id') or 'Administrator'})"
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

        # Parse history
        history = []
        if req_item.approval_history:
            try:
                history = _json.loads(req_item.approval_history)
            except Exception:
                history = []

        if decision == "reject":
            if not remarks:
                return jsonify({"success": False, "message": "Rejection reason is required."}), 400

            req_item.status = "rejected"
            req_item.remarks = remarks
            req_item.action_by = actor_name
            req_item.action_at = datetime.utcnow()

            history.append({
                "stage": req_item.current_stage,
                "action": "rejected",
                "actor": actor_name,
                "timestamp": now_str,
                "remarks": remarks,
            })
            req_item.approval_history = _json.dumps(history)

            # Sync to GuestHouseRequest if exists
            gh = db.query(GuestHouseRequest).filter(GuestHouseRequest.id == req_id).first()
            if gh:
                gh.stage = "rejected"
                gh.remark = remarks
                gh.rejected_at = "admin"

            db.commit()

            # Email notification (non-blocking)
            if req_item.applicant_email:
                send_request_outcome_email_async(req_item.applicant_email, req_item.id, "rejected", remarks)

            return jsonify({"success": True, "message": f"Request {req_id} rejected.", "request": req_item.to_dict()})

        elif decision == "approve":
            # Parse details to access pipeline stages
            details_obj = {}
            if req_item.details:
                try:
                    details_obj = _json.loads(req_item.details)
                except Exception:
                    details_obj = {}
            stages_list = details_obj.get("stages", [])

            # Check advance stage vs final approval
            if advance_mode == "next_stage" and req_item.current_step_order < req_item.total_steps:
                old_stage = req_item.current_stage
                req_item.current_step_order += 1

                next_stage_name = None
                next_stage_role = None
                if stages_list and req_item.current_step_order <= len(stages_list):
                    stg_meta = stages_list[req_item.current_step_order - 1]
                    next_stage_name = stg_meta.get("name")
                    next_stage_role = stg_meta.get("role")
                    details_obj["current_stage_role"] = next_stage_role
                    details_obj["current_approver_name"] = stg_meta.get("approver_name")
                    details_obj["current_approver_id"] = stg_meta.get("approver_id")
                    req_item.details = _json.dumps(details_obj)

                if next_stage_name:
                    req_item.current_stage = next_stage_name
                elif req_item.workflow_id:
                    wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == req_item.workflow_id).first()
                    if wf and wf.steps:
                        next_step = next((s for s in wf.steps if s.step_order == req_item.current_step_order), None)
                        if next_step:
                            req_item.current_stage = next_step.step_name
                        else:
                            req_item.current_stage = f"Stage {req_item.current_step_order} Review"
                else:
                    req_item.current_stage = f"Stage {req_item.current_step_order} Review"

                history.append({
                    "stage": old_stage,
                    "action": "approved",
                    "actor": actor_name,
                    "timestamp": now_str,
                    "remarks": remarks or f"Approved {old_stage} and advanced to {req_item.current_stage}",
                })
                req_item.approval_history = _json.dumps(history)

                # Sync to GuestHouseRequest if exists
                gh = db.query(GuestHouseRequest).filter(GuestHouseRequest.id == req_id).first()
                if gh:
                    if next_stage_role == "hr_head" or "HR" in (req_item.current_stage or "").upper():
                        gh.stage = "pending_hr_head"
                    elif next_stage_role == "unit_head" or "UNIT HEAD" in (req_item.current_stage or "").upper():
                        gh.stage = "pending_unit_head"
                    gh.remark = remarks or f"Advanced to {req_item.current_stage}"

                db.commit()
                return jsonify({"success": True, "message": f"Advanced {req_id} to {req_item.current_stage}.", "request": req_item.to_dict()})
            else:
                req_item.status = "approved"
                req_item.current_step_order = req_item.total_steps
                req_item.remarks = remarks or "Approved by Administrator"
                req_item.action_by = actor_name
                req_item.action_at = datetime.utcnow()

                history.append({
                    "stage": req_item.current_stage,
                    "action": "approved",
                    "actor": actor_name,
                    "timestamp": now_str,
                    "remarks": remarks or "Granted Final Approval",
                })
                req_item.approval_history = _json.dumps(history)

                # Sync to GuestHouseRequest if exists
                gh = db.query(GuestHouseRequest).filter(GuestHouseRequest.id == req_id).first()
                if gh:
                    gh.stage = "approved"
                    gh.remark = remarks or "Approved by Administrator"

                db.commit()

                # Email notification to End User and Admin Department
                dispatch_final_approval_emails(db, req_item, actor_name, remarks or "Approved by Administrator")

                return jsonify({"success": True, "message": f"Request {req_id} approved successfully.", "request": req_item.to_dict()})

        else:
            return jsonify({"success": False, "message": "Invalid decision."}), 400

    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Dedicated Vehicle Allocation & Dispatch Tab for Admin
# ---------------------------------------------------------------------------
@app.route("/admin/vehicles", methods=["GET"])
@require_admin
def admin_vehicle_management():
    db = SessionLocal()
    try:
        reqs = (
            db.query(ApprovalRequest)
            .filter(
                or_(
                    ApprovalRequest.workflow_id == 7,
                    ApprovalRequest.request_type.ilike("%vehicle%"),
                    ApprovalRequest.request_type.ilike("%car%"),
                    ApprovalRequest.title.ilike("%vehicle%"),
                    ApprovalRequest.title.ilike("%car%"),
                )
            )
            .order_by(ApprovalRequest.created_at.desc())
            .all()
        )

        all_vehicle_reqs = [r.to_dict() for r in reqs]

        needs_allocation = []
        allocated = []
        pending_approval = []

        for r in all_vehicle_reqs:
            alloc = r.get("details", {}).get("vehicle_allocation")
            if r.get("status") == "approved":
                if alloc and alloc.get("fields"):
                    allocated.append(r)
                else:
                    needs_allocation.append(r)
            elif r.get("status") == "pending":
                pending_approval.append(r)

        target_id = (request.args.get("req_id") or "").strip()
        selected_req = None
        if target_id:
            selected_req = next((r for r in all_vehicle_reqs if str(r["id"]) == target_id), None)

        if not selected_req:
            if needs_allocation:
                selected_req = needs_allocation[0]
            elif allocated:
                selected_req = allocated[0]
            elif all_vehicle_reqs:
                selected_req = all_vehicle_reqs[0]

        employee_count = db.query(Employee).count()
        pending_approvals_count = db.query(ApprovalRequest).filter(ApprovalRequest.status == "pending").count()

        return render_template(
            "admin_vehicles.html",
            active="admin_vehicles",
            all_requests=all_vehicle_reqs,
            needs_allocation=needs_allocation,
            allocated=allocated,
            pending_approval=pending_approval,
            selected_req=selected_req,
            unallocated_vehicle_count=len(needs_allocation),
            employee_count=employee_count,
            pending_approvals_count=pending_approvals_count,
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Vehicle Details Allocation by Admin (Dispatched from dppl.admin@jsw.in)
# ---------------------------------------------------------------------------
@app.route("/admin/approvals/<req_id>/assign-vehicle", methods=["POST"])
@require_admin
def assign_vehicle_details(req_id):
    import json as _json
    payload = request.get_json(silent=True) or request.form
    raw_fields = payload.get("fields")
    if isinstance(raw_fields, str):
        try:
            fields = _json.loads(raw_fields)
        except Exception:
            fields = []
    elif isinstance(raw_fields, list):
        fields = raw_fields
    else:
        fields = []

    notes = (payload.get("notes") or "").strip()

    if not fields:
        return jsonify({"success": False, "message": "Please configure and provide at least one vehicle detail."}), 400

    db = SessionLocal()
    try:
        req_item = db.query(ApprovalRequest).filter(ApprovalRequest.id == req_id).first()
        if not req_item:
            return jsonify({"success": False, "message": "Request not found"}), 404

        details_obj = {}
        if req_item.details:
            try:
                details_obj = _json.loads(req_item.details) if isinstance(req_item.details, str) else req_item.details
            except Exception:
                details_obj = {}
        if not isinstance(details_obj, dict):
            details_obj = {}

        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        admin_actor = session.get("emp_name") or session.get("emp_id") or "DPPL Admin"

        # Save allocation record in details
        allocation_record = {
            "assigned_at": now_str,
            "assigned_by": "dppl.admin@jsw.in",
            "admin_user": admin_actor,
            "fields": fields,
            "notes": notes,
        }
        details_obj["vehicle_allocation"] = allocation_record
        req_item.details = _json.dumps(details_obj)

        # Append to approval history
        history = []
        if req_item.approval_history:
            try:
                history = _json.loads(req_item.approval_history)
            except Exception:
                history = []
        history.append({
            "stage": "Vehicle Arranged",
            "action": "vehicle_assigned",
            "actor": f"DPPL Admin ({admin_actor})",
            "timestamp": now_str,
            "remarks": f"Car and driver details allocated and dispatched by dppl.admin@jsw.in ({len(fields)} particulars)",
        })
        req_item.approval_history = _json.dumps(history)
        db.commit()

        # Resolve applicant email and name
        recipient_override = (payload.get("recipient_email") or "").strip()
        applicant_email = recipient_override or (req_item.applicant_email or "").strip()
        applicant_name = req_item.applicant_name or ""
        if not applicant_email and req_item.applicant_emp_id:
            emp = db.query(Employee).filter(Employee.employee_id == str(req_item.applicant_emp_id)).first()
            if emp:
                applicant_email = (emp.email_id or "").strip()
                if not applicant_name:
                    applicant_name = emp.employee_name or ""

        if recipient_override and not req_item.applicant_email:
            req_item.applicant_email = recipient_override
            db.commit()

        req_summary = {
            "req_id": req_item.id,
            "workflow_name": req_item.workflow.name if req_item.workflow else req_item.request_type,
            "start_date": req_item.start_date or "",
            "end_date": req_item.end_date or "",
            "title": req_item.title or "",
        }

        # Trigger confirmation email from dppl.admin@jsw.in to the applicant
        if applicant_email:
            notify_vehicle_details_async(
                to_email=applicant_email,
                applicant_name=applicant_name or "Applicant",
                req_id=req_item.id,
                vehicle_details=fields,
                req_summary=req_summary,
            )

        return jsonify({
            "success": True,
            "message": f"Vehicle details saved! Confirmation email dispatched from dppl.admin@jsw.in to {applicant_email or 'applicant'}.",
            "applicant_email": applicant_email,
            "allocation": allocation_record,
            "request": req_item.to_dict(),
        })

    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Booked Dates API — ONE booking per date per workflow (ignores time)
# ---------------------------------------------------------------------------

def _get_booked_dates(db, workflow_id=None):
    """Return a sorted list of ISO dates (YYYY-MM-DD) that already have an
    approved or pending request for the given workflow.  Time is ignored."""
    import json as _json
    q = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.status.in_(["pending", "approved"]))
    )
    if workflow_id:
        try:
            q = q.filter(ApprovalRequest.workflow_id == int(workflow_id))
        except (ValueError, TypeError):
            pass
    active_requests = q.all()

    booked = set()
    DATE_KEYS = ("event_date", "booking_date", "start_date",
                 "checkin", "check_in", "arrival_date", "date")
    for req in active_requests:
        form_data = {}
        if req.details:
            try:
                details = _json.loads(req.details)
                form_data = details.get("form_data", {}) or {}
            except Exception:
                pass
        # Prefer a date found in form fields, fall back to req.start_date
        raw_date = next(
            (str(form_data.get(k)) for k in DATE_KEYS if form_data.get(k)),
            req.start_date or ""
        )
        if not raw_date:
            continue
        try:
            from datetime import date as _date
            booked.add(_date.fromisoformat(raw_date.strip()[:10]).isoformat())
        except (TypeError, ValueError):
            pass
    return sorted(booked)


@app.route("/api/booked-slots")      # keep old URL working
@app.route("/api/booked-dates")      # new canonical URL
def api_booked_dates():
    workflow_id = request.args.get("workflow_id")
    db = SessionLocal()
    try:
        dates = _get_booked_dates(db, workflow_id)
        return jsonify({"success": True, "booked_dates": dates, "booked_slots": dates})
    except Exception as e:
        return jsonify({"success": False, "booked_dates": [], "booked_slots": [], "error": str(e)})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# User Service & Request Form Submission (Stored via Alembic in Postgres)
# ---------------------------------------------------------------------------
def _booking_resource(workflow, request_type, form_values):
    """Return the shared resource key for VIP hall/conference room bookings."""
    form_values = form_values if isinstance(form_values, dict) else {}
    resource_values = [
        form_values.get(key, "")
        for key in ("venue", "facility_type", "room_type", "hall_type", "conference_room")
    ]
    workflow_schema = workflow.get_form_schema() if workflow else {}
    workflow_name = " ".join((
        workflow.name if workflow else "",
        workflow.code if workflow else "",
        (workflow_schema or {}).get("title", ""),
    )).lower()
    if not workflow:
        workflow_name = str(request_type or "").lower()
    resource_text = " ".join(str(value) for value in resource_values).lower()
    combined = resource_text + " " + workflow_name

    if "vip" in combined or "banquet hall" in combined or "vip hall" in combined:
        return "vip hall"
    if "conference" in combined and ("room" in combined or "hall" in combined or "conference" in workflow_name):
        return "conference room"
    return None


def _booking_date_time(form_values, fallback_date=None):
    form_values = form_values if isinstance(form_values, dict) else {}
    booking_date = next((
        form_values.get(key)
        for key in ("event_date", "booking_date", "start_date", "checkin", "check_in", "date")
        if form_values.get(key)
    ), fallback_date or "")
    booking_time = next((
        form_values.get(key)
        for key in ("start_time", "event_time", "booking_time", "time")
        if form_values.get(key)
    ), "")

    try:
        normalized_date = date.fromisoformat(str(booking_date)).isoformat()
        normalized_time = time.fromisoformat(str(booking_time)).strftime("%H:%M")
    except (TypeError, ValueError):
        return None, None
    return normalized_date, normalized_time


def _has_booking_conflict(db, workflow, request_type, form_values, booking_date):
    resource = _booking_resource(workflow, request_type, form_values)
    requested_date, requested_time = _booking_date_time(form_values, booking_date)
    if not resource or not requested_date or not requested_time:
        return False

    db.query(ApprovalWorkflow).order_by(ApprovalWorkflow.id).with_for_update().all()
    active_requests = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.status.in_(("pending", "approved")))
        .all()
    )
    for existing in active_requests:
        details = existing.to_dict().get("details") or {}
        existing_values = details.get("form_data") if isinstance(details, dict) else {}
        existing_workflow = existing.workflow
        if _booking_resource(existing_workflow, existing.request_type, existing_values) != resource:
            continue
        existing_date, existing_time = _booking_date_time(existing_values, existing.start_date)
        if existing_date == requested_date and existing_time == requested_time:
            return True
    return False


@app.route("/requests/submit", methods=["POST"])
def submit_request():
    import json as _json
    payload = request.get_json(silent=True) or request.form
    workflow_id = payload.get("workflow_id")
    request_type = (payload.get("request_type") or "General Request").strip()
    title = (payload.get("title") or "").strip()
    start_date = (payload.get("start_date") or "").strip()
    end_date = (payload.get("end_date") or "").strip()
    purpose = (payload.get("purpose") or "").strip()
    details_data = payload.get("details") or {}
    if isinstance(details_data, str):
        try:
            details_data = _json.loads(details_data)
        except Exception:
            details_data = {"notes": details_data}
    if not isinstance(details_data, dict):
        details_data = {}

    db = SessionLocal()
    try:
        wf = None
        if workflow_id:
            try:
                wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == int(workflow_id)).first()
            except Exception:
                wf = None

        if not wf and request_type:
            try:
                wf = db.query(ApprovalWorkflow).filter(
                    (ApprovalWorkflow.name.ilike(request_type)) | (ApprovalWorkflow.code.ilike(request_type))
                ).first()
                if wf:
                    workflow_id = wf.id
            except Exception:
                wf = None

        if wf and (not request_type or request_type == "General Request"):
            request_type = wf.name

        form_schema = wf.get_form_schema() if wf else None
        has_dynamic_form = bool(form_schema and form_schema.get("enabled", True) and form_schema.get("fields"))

        dynamic_form_values = payload.get("form_data") or payload.get("dynamic_fields") or {}
        if isinstance(dynamic_form_values, str):
            try:
                dynamic_form_values = _json.loads(dynamic_form_values)
            except Exception:
                dynamic_form_values = {}
        if not isinstance(dynamic_form_values, dict):
            dynamic_form_values = {}

        dynamic_field_records = []
        if has_dynamic_form:
            fields = form_schema.get("fields", [])
            for f in fields:
                fname = f.get("name") or f.get("id")
                flabel = f.get("label") or fname
                ftype = f.get("type", "text")
                is_req = bool(f.get("required", False))

                # Extract value from dynamic_form_values or direct payload
                fval = dynamic_form_values.get(fname)
                if fval is None and fname in payload:
                    fval = payload.get(fname)
                if fval is None and f.get("id") and f.get("id") in payload:
                    fval = payload.get(f.get("id"))

                # Handle booleans / checkboxes
                if ftype == "checkbox":
                    fval_clean = True if str(fval).lower() in ("true", "1", "yes", "on") else False
                    fval_display = "Yes" if fval_clean else "No"
                else:
                    fval_clean = str(fval).strip() if fval is not None else ""
                    fval_display = fval_clean

                if ftype == "date" and fval_clean:
                    try:
                        field_date = date.fromisoformat(fval_clean)
                    except ValueError:
                        return jsonify({"success": False, "message": f"'{flabel}' must be a valid date."}), 400
                    if field_date < datetime.now(ZoneInfo("Asia/Kolkata")).date():
                        return jsonify({"success": False, "message": "Past dates cannot be selected."}), 400

                # Check requirement
                if is_req:
                    if ftype == "checkbox":
                        if not fval_clean:
                            return jsonify({"success": False, "message": f"'{flabel}' is required."}), 400
                    else:
                        if not fval_clean:
                            return jsonify({"success": False, "message": f"'{flabel}' is required."}), 400

                dynamic_form_values[fname] = fval_clean
                dynamic_field_records.append({
                    "key": fname,
                    "label": flabel,
                    "type": ftype,
                    "value": fval_display,
                    "required": is_req
                })

                # Map common dynamic field names to main table columns
                if fname in ("title", "guest_name", "event_title", "subject", "item_name") and fval_clean and not title:
                    title = fval_clean
                if fname in ("start_date", "checkin", "check_in", "event_date", "departure_date", "required_by_date") and fval_clean and not start_date:
                    start_date = fval_clean
                if fname in ("end_date", "checkout", "check_out", "return_date") and fval_clean and not end_date:
                    end_date = fval_clean
                if fname in ("purpose", "justification", "agenda", "reason", "description") and fval_clean and not purpose:
                    purpose = fval_clean
                if fname in ("num_guests", "expected_pax", "quantity", "attendees_count") and fval_clean:
                    details_data["num_guests"] = fval_clean
                if fname in ("facility_type", "hall_layout", "travel_mode", "item_type", "room_type") and fval_clean:
                    details_data["facility_type"] = fval_clean

        emp_id = session.get("emp_id")
        applicant = db.query(Employee).filter(Employee.employee_id == emp_id).first() if emp_id else None
        applicant_name = applicant.employee_name if applicant else session.get("emp_name", emp_id)

        # Fallbacks for core columns if not provided
        if not title:
            first_text = next((r["value"] for r in dynamic_field_records if r["type"] == "text" and r["value"]), None)
            title = first_text or f"{request_type} - {applicant_name or 'Request'}"

        if not start_date:
            first_date = next((r["value"] for r in dynamic_field_records if r["type"] == "date" and r["value"]), None)
            start_date = first_date or datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")

        if not end_date:
            all_dates = [r["value"] for r in dynamic_field_records if r["type"] == "date" and r["value"]]
            end_date = all_dates[1] if len(all_dates) > 1 else start_date

        local_today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        for date_value, label in ((start_date, "Start date"), (end_date, "End date")):
            if date_value:
                try:
                    parsed_date = date.fromisoformat(str(date_value))
                except ValueError:
                    return jsonify({"success": False, "message": f"{label} must be a valid date."}), 400
                if parsed_date < local_today:
                    return jsonify({"success": False, "message": "Past dates cannot be selected."}), 400
        if start_date and end_date and date.fromisoformat(start_date) > date.fromisoformat(end_date):
            return jsonify({"success": False, "message": "End date cannot be before start date."}), 400

        # ── One booking per date per workflow ───────────────────────────────
        booking_check_date = start_date
        # also look inside form fields for an explicit date
        if dynamic_form_values:
            DATE_KEYS = ("event_date", "booking_date", "arrival_date",
                         "checkin", "check_in", "date")
            booking_check_date = next(
                (str(dynamic_form_values.get(k))
                 for k in DATE_KEYS if dynamic_form_values.get(k)),
                start_date or ""
            )
        if booking_check_date:
            try:
                from datetime import date as _chk_date
                norm_check = _chk_date.fromisoformat(str(booking_check_date).strip()[:10]).isoformat()
                wf_id_for_check = int(workflow_id) if workflow_id else None
                booked_dates = _get_booked_dates(db, wf_id_for_check)
                if norm_check in booked_dates:
                    return jsonify({
                        "success": False,
                        "message": f"This date ({norm_check}) is already fully booked. "
                                   "Please choose a different date.",
                    }), 409
            except (TypeError, ValueError):
                pass

        if not purpose:
            first_textarea = next((r["value"] for r in dynamic_field_records if r["type"] == "textarea" and r["value"]), None)
            purpose = first_textarea or f"Application for {request_type}"

        if has_dynamic_form:
            details_data["has_dynamic_form"] = True
            details_data["form_data"] = dynamic_form_values
            details_data["form_fields"] = dynamic_field_records
            details_data["form_title"] = form_schema.get("title", request_type)
        applicant_email = applicant.email_id if applicant else None
        applicant_phone = applicant.contact_no if applicant else None

        submitted_dept = (payload.get("department") or "").strip()
        department = (applicant.department if (applicant and applicant.department) else submitted_dept) or None
        if applicant and not applicant.department and submitted_dept:
            applicant.department = submitted_dept
            db.commit()

        if not department:
            return jsonify({"success": False, "message": "Department is required to route your request to your Department Head."}), 400

        # Sequential human-readable Request ID
        count = db.query(ApprovalRequest).count()
        req_id = f"REQ-{datetime.utcnow().year}-{1001 + count}"

        # Build dynamic approval hierarchy pipeline (Employee vs HOD)
        pipeline = build_request_approval_pipeline(
            db=db,
            applicant=applicant,
            department=department,
            workflow_id=int(workflow_id) if workflow_id else None,
            session_role=session.get("role")
        )
        current_stage = pipeline["initial_stage"]
        total_steps = pipeline["total_steps"]
        details_data["stages"] = pipeline["stages"]
        details_data["applicant_type"] = "hod" if pipeline["is_hod"] else "employee"
        details_data["current_stage_role"] = pipeline["initial_role"]
        details_data["current_approver_name"] = pipeline["initial_approver"]
        details_data["current_approver_id"] = pipeline.get("initial_approver_id")
        details_data["department"] = department

        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        history = [
            {
                "stage": "Request Submission",
                "action": "submitted",
                "actor": applicant_name or emp_id or "Applicant",
                "timestamp": now_str,
                "remarks": f"Submitted {request_type} form ({'HOD' if pipeline['is_hod'] else 'Employee'} route)",
            }
        ]

        applicant_emp_id = applicant.employee_id if applicant else None

        new_req = ApprovalRequest(
            id=req_id,
            workflow_id=int(workflow_id) if workflow_id else None,
            request_type=request_type,
            title=title,
            department=department,
            applicant_emp_id=applicant_emp_id,
            applicant_name=applicant_name,
            applicant_email=applicant_email,
            applicant_phone=applicant_phone,
            start_date=start_date,
            end_date=end_date,
            purpose=purpose,
            details=_json.dumps(details_data),
            status="pending",
            current_stage=current_stage,
            current_step_order=1,
            total_steps=total_steps,
            approval_history=_json.dumps(history),
        )
        db.add(new_req)

        # Synchronize with guest_house_requests if applicable
        if "guest" in request_type.lower():
            gh_stage = "pending_hr_head" if pipeline["is_hod"] else "pending_dept_head"
            gh_req = GuestHouseRequest(
                id=req_id,
                guest=title,
                checkin=start_date,
                checkout=end_date,
                purpose=purpose,
                stage=gh_stage,
                remark="",
                rejected_at=None,
                created_by=applicant_emp_id,
            )
            db.add(gh_req)

        db.commit()
        db.refresh(new_req)

        return jsonify({
            "success": True,
            "message": f"Request {req_id} submitted successfully.",
            "request_id": req_id,
            "request": new_req.to_dict(),
        })
    except Exception as e:
        db.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        db.close()


@app.route("/requests/<req_id>/json", methods=["GET"])
def get_request_json(req_id):
    db = SessionLocal()
    try:
        req_item = db.query(ApprovalRequest).filter(ApprovalRequest.id == req_id).first()
        if not req_item:
            return jsonify({"success": False, "message": "Request not found"}), 404
        return jsonify({"success": True, "request": req_item.to_dict()})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Approvals (Department / Role-Based Reviewers)
# ---------------------------------------------------------------------------
@app.route("/approvals")
def approvals():
    if session.get("is_admin"):
        return redirect(url_for("admin_approvals"))
    role = session.get("role", "employee")
    current_emp_id = session.get("emp_id")

    if role not in ("dept_head", "hr_head", "unit_head"):
        flash("The Approvals section is only accessible to designated approvers.", "warning")
        return redirect(url_for("dashboard"))

    db = SessionLocal()
    try:
        user_emp = db.query(Employee).filter(Employee.employee_id == current_emp_id).first() if current_emp_id else None
        user_dept = (user_emp.department or "").strip().upper() if user_emp else ""

        pending_list = []
        seen_ids = set()

        # 1. Fetch from ApprovalRequest
        all_app_reqs = (
            db.query(ApprovalRequest)
            .filter(ApprovalRequest.status == "pending")
            .order_by(ApprovalRequest.created_at.desc())
            .all()
        )
        for ar in all_app_reqs:
            ar_dict = ar.to_dict()
            details = ar_dict.get("details") or {}
            stg_role = details.get("current_stage_role")
            stages = details.get("stages") or []

            cur_appr_id = str(details.get("current_approver_id") or "")
            if not cur_appr_id and stages and ar.current_step_order <= len(stages):
                cur_appr_id = str(stages[ar.current_step_order - 1].get("approver_id") or "")

            cur_step_approvers = []
            if stages and ar.current_step_order <= len(stages):
                stg_info = stages[ar.current_step_order - 1]
                if stg_info.get("approvers"):
                    cur_step_approvers = [str(a.get("employee_id") or "").strip() for a in stg_info["approvers"]]

            matches_role = False

            # 1. DIRECT APPROVER MATCH:
            # If current user is explicitly assigned as the current approver for this step,
            # they MUST see it and be able to approve it.
            if current_emp_id:
                if cur_appr_id and str(cur_appr_id) == str(current_emp_id):
                    matches_role = True
                elif str(current_emp_id) in cur_step_approvers:
                    matches_role = True

            # 2. ROLE-BASED MATCH (if not already matched directly):
            if not matches_role:
                if role == "dept_head":
                    is_stage_1 = (stg_role == "dept_head") or (ar.current_step_order == 1 and details.get("applicant_type") != "hod" and stg_role not in ("unit_head", "hr_head"))
                    if is_stage_1:
                        req_dept = (ar.department or (ar.applicant.department if ar.applicant else "") or "").strip().upper()
                        if user_dept and req_dept and (user_dept == req_dept or user_dept in req_dept or req_dept in user_dept or DEPT_ALIASES.get(user_dept) == req_dept or DEPT_ALIASES.get(req_dept) == user_dept):
                            matches_role = True
                        elif req_dept:
                            dept_hod = get_department_hod(db, req_dept)
                            if dept_hod and str(dept_hod.employee_id) == str(current_emp_id):
                                matches_role = True

                elif role == "hr_head":
                    if stg_role == "hr_head" or "HR" in (ar.current_stage or "").upper():
                        configured_hr = get_hr_head_from_approval_management(db, ar.workflow_id or ar.request_type)
                        if configured_hr and str(configured_hr.employee_id) == str(current_emp_id):
                            matches_role = True
                        elif str(current_emp_id) == "4050702":
                            matches_role = True

                elif role == "unit_head":
                    if stg_role == "unit_head" or "UNIT HEAD" in (ar.current_stage or "").upper() or (ar.current_step_order == ar.total_steps and stg_role != "hr_head"):
                        configured_uh = get_unit_head_from_approval_management(db, ar.workflow_id or ar.request_type)
                        if configured_uh and str(configured_uh.employee_id) == str(current_emp_id):
                            matches_role = True
                        elif str(current_emp_id) == "4050163":
                            matches_role = True

            # An approver should not review their own submitted request
            if matches_role and ar.applicant_emp_id != current_emp_id:
                pending_list.append(ar_dict)
                seen_ids.add(ar.id)

        # 2. Fetch from GuestHouseRequest for backward compatibility
        stage_for_role = {
            "dept_head": "pending_dept_head",
            "hr_head": "pending_hr_head",
            "unit_head": "pending_unit_head",
        }.get(role)

        if stage_for_role:
            gh_reqs = (
                db.query(GuestHouseRequest)
                .filter(GuestHouseRequest.stage == stage_for_role)
                .order_by(GuestHouseRequest.created_at.desc())
                .all()
            )
            for gh in gh_reqs:
                if gh.id not in seen_ids and gh.created_by != current_emp_id:
                    if role == "dept_head":
                        creator = db.query(Employee).filter(Employee.employee_id == gh.created_by).first()
                        creator_dept = (creator.department or "").strip().upper() if creator else ""
                        if not (creator_dept and user_dept and (creator_dept == user_dept or creator_dept in user_dept or user_dept in creator_dept or DEPT_ALIASES.get(creator_dept) == user_dept or DEPT_ALIASES.get(user_dept) == creator_dept)):
                            continue
                    elif role == "hr_head":
                        configured_hr = get_hr_head_from_approval_management(db)
                        if configured_hr and str(configured_hr.employee_id) != str(current_emp_id):
                            continue
                    elif role == "unit_head":
                        configured_uh = get_unit_head_from_approval_management(db)
                        if configured_uh and str(configured_uh.employee_id) != str(current_emp_id):
                            continue

                    gh_data = gh.to_dict()
                    gh_data["title"] = gh.guest
                    gh_data["request_type"] = "Guest House Request"
                    pending_list.append(gh_data)
                    seen_ids.add(gh.id)

        return render_template("approvals.html", pending=pending_list, role=role)
    finally:
        db.close()


@app.route("/approvals/decide/<req_id>", methods=["POST"])
def decide(req_id):
    import json as _json
    decision = request.form.get("decision")
    remark = request.form.get("remark", "").strip()
    role = session.get("role", "employee")
    current_emp_id = session.get("emp_id")
    actor_name = f"{role.replace('_', ' ').title()} ({session.get('emp_name') or current_emp_id or 'Approver'})"
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")

    db = SessionLocal()
    try:
        r = db.query(GuestHouseRequest).filter(GuestHouseRequest.id == req_id).first()
        req_obj = db.query(ApprovalRequest).filter(ApprovalRequest.id == req_id).first()

        if not r and not req_obj:
            flash(f"Request {req_id} not found.", "error")
            return redirect(url_for("approvals"))

        # Resolve current approver and step approvers
        details_obj = {}
        if req_obj and req_obj.details:
            try:
                details_obj = _json.loads(req_obj.details) if isinstance(req_obj.details, str) else req_obj.details
            except Exception:
                details_obj = {}
        cur_approver_id = str(details_obj.get("current_approver_id") or "")
        stages = details_obj.get("stages") or []
        if not cur_approver_id and stages and req_obj.current_step_order <= len(stages):
            cur_approver_id = str(stages[req_obj.current_step_order - 1].get("approver_id") or "")

        cur_step_approvers = []
        if stages and req_obj.current_step_order <= len(stages):
            stg_meta = stages[req_obj.current_step_order - 1]
            if stg_meta.get("approvers"):
                cur_step_approvers = [str(a.get("employee_id") or "").strip() for a in stg_meta["approvers"]]

        is_direct_approver = False
        if current_emp_id:
            if cur_approver_id and str(cur_approver_id) == str(current_emp_id):
                is_direct_approver = True
            elif str(current_emp_id) in cur_step_approvers:
                is_direct_approver = True

        # Strict Department Head Authorization Check
        if role == "dept_head" and req_obj:
            user_emp = db.query(Employee).filter(Employee.employee_id == current_emp_id).first() if current_emp_id else None
            user_dept = (user_emp.department or "").strip().upper() if user_emp else ""
            req_dept = (req_obj.department or (req_obj.applicant.department if req_obj.applicant else "") or "").strip().upper()

            is_authorized = is_direct_approver
            if not is_authorized and user_dept and req_dept and (user_dept == req_dept or user_dept in req_dept or req_dept in user_dept or DEPT_ALIASES.get(user_dept) == req_dept or DEPT_ALIASES.get(req_dept) == user_dept):
                is_authorized = True
            elif not is_authorized and req_dept:
                dept_hod = get_department_hod(db, req_dept)
                if dept_hod and str(dept_hod.employee_id) == str(current_emp_id):
                    is_authorized = True

            if not is_authorized:
                flash(f"Unauthorized: You are not authorized to approve requests for {req_dept or 'this department'}.", "error")
                return redirect(url_for("approvals"))

        # Strict HR Head Authorization Check
        if role == "hr_head" and req_obj:
            configured_hr = get_hr_head_from_approval_management(db, req_obj.workflow_id or req_obj.request_type)
            is_auth_hr = is_direct_approver
            if not is_auth_hr:
                if configured_hr and str(configured_hr.employee_id) == str(current_emp_id):
                    is_auth_hr = True
                elif str(current_emp_id) == "4050702":
                    is_auth_hr = True
            if not is_auth_hr:
                flash("Unauthorized: You are not authorized to review HR Head stage for this request.", "error")
                return redirect(url_for("approvals"))

        # Strict Unit Head Authorization Check
        if role == "unit_head" and req_obj:
            configured_uh = get_unit_head_from_approval_management(db, req_obj.workflow_id or req_obj.request_type)
            is_auth_uh = is_direct_approver
            if not is_auth_uh:
                if configured_uh and str(configured_uh.employee_id) == str(current_emp_id):
                    is_auth_uh = True
                elif str(current_emp_id) == "4050163":
                    is_auth_uh = True
            if not is_auth_uh:
                flash("Unauthorized: You are not authorized to review Unit Head stage for this request.", "error")
                return redirect(url_for("approvals"))

        submitter_email = None
        if req_obj and req_obj.applicant_email:
            submitter_email = req_obj.applicant_email
        elif r and r.created_by:
            submitter = db.query(Employee).filter(Employee.employee_id == r.created_by).first()
            if submitter:
                submitter_email = submitter.email_id

        # Parse history
        history = []
        if req_obj and req_obj.approval_history:
            try:
                history = _json.loads(req_obj.approval_history)
            except Exception:
                history = []

        if decision == "reject":
            if not remark:
                flash("Please add a remark or reason before rejecting.", "error")
                return redirect(url_for("approvals"))

            if r:
                r.stage = "rejected"
                r.remark = remark
                r.rejected_at = role
            if req_obj:
                req_obj.status = "rejected"
                req_obj.remarks = remark
                req_obj.action_by = actor_name
                req_obj.action_at = datetime.utcnow()
                history.append({
                    "stage": req_obj.current_stage,
                    "action": "rejected",
                    "actor": actor_name,
                    "timestamp": now_str,
                    "remarks": remark,
                })
                req_obj.approval_history = _json.dumps(history)

            db.commit()
            if submitter_email:
                send_request_outcome_email_async(submitter_email, req_id, "rejected", remark)
            flash(f"Request {req_id} has been rejected.", "info")

        elif decision == "approve":
            details_obj = {}
            if req_obj and req_obj.details:
                try:
                    details_obj = _json.loads(req_obj.details) if isinstance(req_obj.details, str) else req_obj.details
                except Exception:
                    details_obj = {}
            stages_list = details_obj.get("stages", [])

            # Check if this is the final step
            is_final_step = False
            if req_obj:
                current_stage_meta = {}
                if stages_list and req_obj.current_step_order <= len(stages_list):
                    current_stage_meta = stages_list[req_obj.current_step_order - 1]
                is_final_step = (req_obj.current_step_order >= req_obj.total_steps) or bool(current_stage_meta.get("is_final"))
            else:
                is_final_step = (role == "unit_head" or (r and r.stage == "pending_unit_head"))

            if not is_final_step:
                # Advance stage
                old_stage = req_obj.current_stage if req_obj else "Department Review"
                if req_obj:
                    req_obj.current_step_order += 1
                    next_stage_name = None
                    next_stage_role = None
                    if stages_list and req_obj.current_step_order <= len(stages_list):
                        stg_meta = stages_list[req_obj.current_step_order - 1]
                        next_stage_name = stg_meta.get("name")
                        next_stage_role = stg_meta.get("role")
                        details_obj["current_stage_role"] = next_stage_role
                        details_obj["current_approver_name"] = stg_meta.get("approver_name")
                        details_obj["current_approver_id"] = stg_meta.get("approver_id")

                        # Strictly sync HR Head and Unit Head approvers if not already explicitly defined
                        if next_stage_role == "hr_head":
                            if not stg_meta.get("approver_id"):
                                pipe_hr = get_hr_head_from_approval_management(db, req_obj.workflow_id or req_obj.request_type)
                                if pipe_hr:
                                    details_obj["current_approver_name"] = pipe_hr.employee_name
                                    details_obj["current_approver_id"] = pipe_hr.employee_id
                                    stg_meta["approver_name"] = pipe_hr.employee_name
                                    stg_meta["approver_id"] = pipe_hr.employee_id
                        elif next_stage_role == "unit_head":
                            if not stg_meta.get("approver_id"):
                                pipe_uh = get_unit_head_from_approval_management(db, req_obj.workflow_id or req_obj.request_type)
                                if pipe_uh:
                                    details_obj["current_approver_name"] = pipe_uh.employee_name
                                    details_obj["current_approver_id"] = pipe_uh.employee_id
                                    stg_meta["approver_name"] = pipe_uh.employee_name
                                    stg_meta["approver_id"] = pipe_uh.employee_id

                        req_obj.details = _json.dumps(details_obj)

                    req_obj.current_stage = clean_stage_name(
                        next_stage_name,
                        "Final Approval" if is_final_step else f"Stage {req_obj.current_step_order} Review"
                    )

                    history.append({
                        "stage": old_stage,
                        "action": "approved",
                        "actor": actor_name,
                        "timestamp": now_str,
                        "remarks": remark or f"Approved {old_stage} and forwarded to {req_obj.current_stage}",
                    })
                    req_obj.approval_history = _json.dumps(history)

                if r:
                    if r.stage == "pending_dept_head":
                        r.stage = "pending_hr_head"
                    elif r.stage == "pending_hr_head":
                        r.stage = "pending_unit_head"
                    r.remark = remark or f"Endorsed by {role}"

                db.commit()
                flash(f"Request {req_id} approved and forwarded to {req_obj.current_stage if req_obj else 'next stage'}.", "success")
            else:
                # Grant Final Approval
                if req_obj:
                    req_obj.status = "approved"
                    req_obj.current_step_order = req_obj.total_steps
                    req_obj.remarks = remark or "Granted Final Approval"
                    req_obj.action_by = actor_name
                    req_obj.action_at = datetime.utcnow()
                    history.append({
                        "stage": req_obj.current_stage,
                        "action": "approved",
                        "actor": actor_name,
                        "timestamp": now_str,
                        "remarks": remark or "Granted Final Approval",
                    })
                    req_obj.approval_history = _json.dumps(history)

                if r:
                    r.stage = "approved"
                    r.remark = remark or "Approved"

                db.commit()

                # Email notifications to End User and Admin Department
                dispatch_final_approval_emails(db, req_obj or r, actor_name, remark or "Approved")
                flash(f"Request {req_id} granted final approval.", "success")

    except Exception as e:
        db.rollback()
        flash(f"Error updating request: {e}", "error")
    finally:
        db.close()

    return redirect(url_for("approvals"))


# ---------------------------------------------------------------------------
# Employee Synchronization Route (JSON API)
# ---------------------------------------------------------------------------
@app.route("/api/sync-employees", methods=["POST"])
def sync_employees_route():
    data = request.get_json(silent=True) or {}
    active_only = request.args.get("active_only", "false").lower() == "true" or data.get("active_only", False)
    source = (request.args.get("source") or data.get("source") or "both").lower()
    include_staff = source in ("both", "staff")
    include_associates = source in ("both", "associates")
    result = sync_employees(
        include_staff=include_staff,
        include_associates=include_associates,
        active_only=active_only
    )
    status_code = 200 if result.get("success") else 500
    return jsonify(result), status_code


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
