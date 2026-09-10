import random
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

from config import Config
from database import SessionLocal, close_db_session, init_db_defaults
from modules.models import User, Employee, GuestHouseRequest
from sync_service import sync_employees
from mail_service import send_otp_email, send_request_outcome_email

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
            flash("Admin access is required to open the Administration panel.")
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
        flash("Enter both username and password.")
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user or not user.check_password(password):
            flash("Invalid admin username or password.")
            return redirect(url_for("login"))

        if not user.is_active:
            flash("Your admin account is currently disabled.")
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

        flash(f"Signed in successfully as {user.username}.")
        return redirect(url_for("admin_module"))
    finally:
        db.close()


@app.route("/send-otp", methods=["POST"])
def send_otp():
    emp_id = request.form.get("emp_id", "").strip()
    if not emp_id:
        flash("Enter your employee ID.")
        return redirect(url_for("login"))

    db = SessionLocal()
    try:
        emp = db.query(Employee).filter(Employee.employee_id == emp_id).first()
        if not emp:
            flash(f"Employee ID '{emp_id}' not found in Mantra database. Please contact Admin or run Mantra Sync.")
            return redirect(url_for("login"))

        # Generate 6-digit OTP
        otp_code = f"{random.randint(100000, 999999)}"

        if not emp.email_id:
            flash("No email ID is registered for this employee. Please contact the administrator.")
            return redirect(url_for("login"))

        # Only allow verification after the message has been accepted by SMTP.
        if not send_otp_email(emp.email_id, otp_code, emp.employee_name):
            flash("Unable to send the OTP email. Please contact the administrator and try again.")
            return redirect(url_for("login"))

        session["pending_emp_id"] = emp_id
        session["pending_emp_name"] = emp.employee_name
        session["pending_emp_email"] = emp.email_id
        session["pending_emp_dept"] = emp.department
        session["pending_otp"] = otp_code
        flash(f"OTP sent to your registered email ({emp.email_id[:3]}***@...).")

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
        flash("Invalid verification code. Please enter the 6-digit code sent.")
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
        session["role"] = user.role or "employee"
        session["is_admin"] = user.is_admin
        session.pop("pending_emp_id", None)
        session.pop("pending_otp", None)

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
    if role in ("employee", "dept_head", "unit_head", "admin"):
        session["role"] = role
    return redirect(request.referrer or url_for("dashboard"))


# ---------------------------------------------------------------------------
# Dashboard + department modules
# ---------------------------------------------------------------------------
@app.route("/dashboard")
def dashboard():
    if session.get("is_admin"):
        return redirect(url_for("admin_module"))
    return render_template("dashboard.html")


@app.route("/department/<name>")
def department_placeholder(name):
    if name not in ("hr", "it", "finance"):
        return redirect(url_for("dashboard"))
    return render_template("placeholder.html", dept=name)


@app.route("/department/admin", methods=["GET"])
@require_admin
def admin_module():
    return render_admin_module()


def render_admin_module(sync_result=None, sync_records=None):
    """Render the admin page, optionally showing the latest Mantra sync rows."""
    db = SessionLocal()
    try:
        req_records = (
            db.query(GuestHouseRequest)
            .order_by(GuestHouseRequest.created_at.asc())
            .all()
        )
        my_requests = [r.to_dict() for r in req_records]
        latest = my_requests[-1] if my_requests else None
        employee_count = db.query(Employee).count()

        return render_template(
            "admin.html",
            requests=my_requests,
            latest=latest,
            employee_count=employee_count,
            sync_result=sync_result,
            sync_records=sync_records or [],
        )
    finally:
        db.close()


@app.route("/admin/sync-mantra", methods=["POST"])
@require_admin
def sync_mantra_admin():
    active_only = request.form.get("active_only") == "true"
    source = request.form.get("source", "both")

    include_staff = source in ("both", "staff")
    include_associates = source in ("both", "associates")

    result = sync_employees(
        include_staff=include_staff,
        include_associates=include_associates,
        active_only=active_only,
    )

    if result["success"]:
        flash(f"Mantra Sync Successful: {result['total_upserted']} employee records synced from JSW_Dharamtar in {result['duration_seconds']}s.")
    else:
        err_msg = ", ".join(result["errors"])
        flash(f"Mantra Sync Failed: {err_msg}")

    # Show exactly the rows returned by Mantra for this synchronization.
    return render_admin_module(sync_result=result, sync_records=result.get("records", []))


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


@app.route("/department/admin/submit", methods=["POST"])
@require_admin
def submit_guest_house():
    guest = request.form.get("guest", "").strip()
    checkin = request.form.get("checkin", "")
    checkout = request.form.get("checkout", "")
    purpose = request.form.get("purpose", "").strip()

    if not guest or not checkin or not checkout:
        flash("Fill in guest name and both dates.")
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
        flash(f"Guest house request {new_req.id} submitted successfully.")
    except Exception as e:
        db.rollback()
        flash(f"Error saving request: {e}")
    finally:
        db.close()

    return redirect(url_for("admin_module"))


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------
@app.route("/approvals")
def approvals():
    if session.get("is_admin"):
        return redirect(url_for("admin_module"))
    role = session.get("role", "employee")
    stage_for_role = {
        "dept_head": "pending_dept_head",
        "unit_head": "pending_unit_head",
    }.get(role)

    db = SessionLocal()
    try:
        if stage_for_role:
            reqs = (
                db.query(GuestHouseRequest)
                .filter(GuestHouseRequest.stage == stage_for_role)
                .order_by(GuestHouseRequest.created_at.desc())
                .all()
            )
            pending = [r.to_dict() for r in reqs]
        else:
            pending = []
        return render_template("approvals.html", pending=pending, role=role)
    finally:
        db.close()


@app.route("/approvals/decide/<req_id>", methods=["POST"])
def decide(req_id):
    decision = request.form.get("decision")
    remark = request.form.get("remark", "").strip()
    role = session.get("role", "employee")

    db = SessionLocal()
    try:
        r = db.query(GuestHouseRequest).filter(GuestHouseRequest.id == req_id).first()
        if not r:
            return redirect(url_for("approvals"))

        submitter_email = None
        if r.created_by:
            submitter = db.query(Employee).filter(Employee.employee_id == r.created_by).first()
            if submitter:
                submitter_email = submitter.email_id

        if decision == "reject":
            if not remark:
                flash("Add a remark before rejecting.")
                return redirect(url_for("approvals"))
            r.stage = "rejected"
            r.remark = remark
            r.rejected_at = role
            db.commit()
            if submitter_email:
                send_request_outcome_email(submitter_email, r.id, "rejected", remark)
        elif decision == "approve":
            if r.stage == "pending_dept_head":
                r.stage = "pending_unit_head"
            elif r.stage == "pending_unit_head":
                r.stage = "approved"
                if submitter_email:
                    send_request_outcome_email(submitter_email, r.id, "approved", remark)
            db.commit()
    except Exception as e:
        db.rollback()
        flash(f"Error updating request: {e}")
    finally:
        db.close()

    return redirect(url_for("approvals"))


# ---------------------------------------------------------------------------
# Employee Synchronization Route (JSON API)
# ---------------------------------------------------------------------------
@app.route("/api/sync-employees", methods=["POST"])
def sync_employees_route():
    active_only = request.args.get("active_only", "false").lower() == "true"
    result = sync_employees(active_only=active_only)
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
