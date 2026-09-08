from flask import Flask, render_template, request, redirect, url_for, session, flash

app = Flask(__name__)
app.secret_key = "dev-secret-change-me"

# ---------------------------------------------------------------------------
# In-memory demo data (replace with a real database).
# Each request: id, guest, checkin, checkout, purpose, stage, remark, rejected_at
# stage: pending_dept_head -> pending_unit_head -> approved / rejected
# ---------------------------------------------------------------------------
REQUESTS = []
SEQ = {"n": 1041}


def next_id():
    SEQ["n"] += 1
    return "GH-" + str(SEQ["n"])


@app.before_request
def require_login():
    open_endpoints = {"login", "send_otp", "verify_otp_form", "verify_otp", "static"}
    if request.endpoint not in open_endpoints and not session.get("logged_in"):
        return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Auth: employee ID -> OTP -> session
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/send-otp", methods=["POST"])
def send_otp():
    emp_id = request.form.get("emp_id", "").strip()
    if not emp_id:
        flash("Enter your employee ID.")
        return redirect(url_for("login"))
    # Real version: look up emp_id in the Mantra user base, fetch registered
    # mobile/email, generate an OTP, and send it through your SMS/email gateway.
    session["pending_emp_id"] = emp_id
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
    code = request.form.get("otp", "").strip()
    if not emp_id:
        return redirect(url_for("login"))
    if len(code) != 6 or not code.isdigit():
        flash("Enter the 6-digit code.")
        return redirect(url_for("verify_otp_form"))
    # Real version: verify the OTP against what you sent, with expiry + retry limits.
    session["logged_in"] = True
    session["emp_id"] = emp_id
    session["role"] = "employee"
    session.pop("pending_emp_id", None)
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/set-role", methods=["POST"])
def set_role():
    role = request.form.get("role", "employee")
    if role in ("employee", "dept_head", "unit_head"):
        session["role"] = role
    return redirect(request.referrer or url_for("dashboard"))


# ---------------------------------------------------------------------------
# Dashboard + department modules
# ---------------------------------------------------------------------------
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/department/<name>")
def department_placeholder(name):
    if name not in ("hr", "it", "finance"):
        return redirect(url_for("dashboard"))
    return render_template("placeholder.html", dept=name)


@app.route("/department/admin", methods=["GET"])
def admin_module():
    my_requests = REQUESTS  # single shared list for this demo
    latest = my_requests[-1] if my_requests else None
    return render_template("admin.html", requests=my_requests, latest=latest)


@app.route("/department/admin/submit", methods=["POST"])
def submit_guest_house():
    guest = request.form.get("guest", "").strip()
    checkin = request.form.get("checkin", "")
    checkout = request.form.get("checkout", "")
    purpose = request.form.get("purpose", "").strip()

    if not guest or not checkin or not checkout:
        flash("Fill in guest name and both dates.")
        return redirect(url_for("admin_module"))

    REQUESTS.append({
        "id": next_id(),
        "guest": guest,
        "checkin": checkin,
        "checkout": checkout,
        "purpose": purpose,
        "stage": "pending_dept_head",
        "remark": "",
        "rejected_at": None,
    })
    return redirect(url_for("admin_module"))


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------
@app.route("/approvals")
def approvals():
    role = session.get("role", "employee")
    stage_for_role = {
        "dept_head": "pending_dept_head",
        "unit_head": "pending_unit_head",
    }.get(role)
    pending = [r for r in REQUESTS if stage_for_role and r["stage"] == stage_for_role]
    return render_template("approvals.html", pending=pending, role=role)


@app.route("/approvals/decide/<req_id>", methods=["POST"])
def decide(req_id):
    decision = request.form.get("decision")
    remark = request.form.get("remark", "").strip()
    role = session.get("role", "employee")

    r = next((x for x in REQUESTS if x["id"] == req_id), None)
    if not r:
        return redirect(url_for("approvals"))

    if decision == "reject":
        if not remark:
            flash("Add a remark before rejecting.")
            return redirect(url_for("approvals"))
        r["stage"] = "rejected"
        r["remark"] = remark
        r["rejected_at"] = role
        send_outcome_email(r, "rejected")
    elif decision == "approve":
        if r["stage"] == "pending_dept_head":
            r["stage"] = "pending_unit_head"
        elif r["stage"] == "pending_unit_head":
            r["stage"] = "approved"
            send_outcome_email(r, "approved")

    return redirect(url_for("approvals"))


def send_outcome_email(r, outcome):
    # Real version: send an actual email/SMS to the employee.
    print(f"[email] Your guest house request {r['id']} was {outcome}."
          + (f" Remark: {r['remark']}" if r.get("remark") else ""))


if __name__ == "__main__":
    # host="0.0.0.0" makes the app reachable from other devices on the same
    # network (not just this machine). Turn debug off before real deployment.
    app.run(debug=True, host="0.0.0.0", port=5000)