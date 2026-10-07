import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, get_allowed_roles_for_employee, invalidate_roles_cache
from database import SessionLocal
from modules.models import Employee, User

def extract_role_select(html):
    if 'id="role-select"' not in html:
        return None
    start = html.find('id="role-select"')
    end = html.find('</select>', start)
    return html[start:end+9]

def run_tests():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test_secret"
    client = app.test_client()
    db = SessionLocal()

    try:
        invalidate_roles_cache()
        print("==================================================")
        print("TEST 1: Regular Employees (No preset approval role)")
        print("==================================================")
        regular_ids = ["4070124", "4061035", "4070145", "4070116", "4050300"]
        for emp_id in regular_ids:
            emp = db.query(Employee).filter(Employee.employee_id == emp_id).first()
            name = emp.employee_name if emp else "Unknown"
            allowed = get_allowed_roles_for_employee(db, emp_id)
            print(f"Employee {emp_id} ({name}): allowed_roles={allowed}")
            assert allowed == ["employee"], f"Expected ['employee'], got {allowed}"

            # Simulate login session
            with client.session_transaction() as sess:
                sess["logged_in"] = True
                sess["emp_id"] = emp_id
                sess["emp_name"] = name
                sess["role"] = "employee"
                sess["is_admin"] = False

            # Fetch dashboard
            resp = client.get("/dashboard")
            html = resp.data.decode("utf-8")
            role_select = extract_role_select(html)
            assert role_select is None, f"Role selector should NOT appear for regular employee {emp_id}"
            assert 'class="role-switch"' not in html, f"Role switch form should NOT appear for regular employee {emp_id}"
            print(f"  -> Verified: 'View as' switcher is completely HIDDEN for {name}")

            # Attempt unauthorized role escalation via /set-role
            post_resp = client.post("/set-role", data={"role": "dept_head"}, follow_redirects=True)
            with client.session_transaction() as sess:
                assert sess.get("role") == "employee", f"Role escalation to dept_head should be blocked for {emp_id}"
            
            post_resp2 = client.post("/set-role", data={"role": "hr_head"}, follow_redirects=True)
            with client.session_transaction() as sess:
                assert sess.get("role") == "employee", f"Role escalation to hr_head should be blocked for {emp_id}"
            print(f"  -> Verified: Unauthorized POST to /set-role blocked for {name}")

        print("\n==================================================")
        print("TEST 2: Department Heads (HODs)")
        print("==================================================")
        hod_cases = [("4050053", "SURENDRA THAKUR (Civil HOD)"), ("4070019", "NILESH MHATRE (Safety HOD)")]
        for emp_id, desc in hod_cases:
            allowed = get_allowed_roles_for_employee(db, emp_id)
            print(f"HOD {emp_id} ({desc}): allowed_roles={allowed}")
            assert "employee" in allowed and "dept_head" in allowed, f"Expected employee and dept_head in {allowed}"
            assert "hr_head" not in allowed and "unit_head" not in allowed, f"Should not have HR or Unit head: {allowed}"

            with client.session_transaction() as sess:
                sess["logged_in"] = True
                sess["emp_id"] = emp_id
                sess["emp_name"] = desc
                sess["role"] = "dept_head"
                sess["is_admin"] = False

            resp = client.get("/dashboard")
            html = resp.data.decode("utf-8")
            role_select = extract_role_select(html)
            assert role_select is not None, f"Role selector should appear for HOD {emp_id}"
            assert "Department Head (HOD)" in role_select, "Option 'Department Head (HOD)' must be present"
            assert "Employee" in role_select, "Option 'Employee' must be present"
            assert "HR Head" not in role_select, "Option 'HR Head' must NOT be present in selector for HOD"
            assert "Unit Head" not in role_select, "Option 'Unit Head' must NOT be present in selector for HOD"
            print(f"  -> Verified: Selector has ONLY Employee and Department Head (HOD) for {desc}")

            # Test switching between employee and dept_head
            client.post("/set-role", data={"role": "employee"}, follow_redirects=True)
            with client.session_transaction() as sess:
                assert sess.get("role") == "employee"

            client.post("/set-role", data={"role": "dept_head"}, follow_redirects=True)
            with client.session_transaction() as sess:
                assert sess.get("role") == "dept_head"

            # Test blocked role
            client.post("/set-role", data={"role": "unit_head"}, follow_redirects=True)
            with client.session_transaction() as sess:
                assert sess.get("role") == "dept_head", "Switch to unit_head must be rejected"
            print(f"  -> Verified: Switching between allowed roles works, unauthorized switch rejected for {desc}")

        print("\n==================================================")
        print("TEST 3: HR Head")
        print("==================================================")
        hr_id = "4050702"
        allowed = get_allowed_roles_for_employee(db, hr_id)
        print(f"HR Head {hr_id} (Parimita Behera): allowed_roles={allowed}")
        assert allowed == ["employee", "hr_head"], f"Expected ['employee', 'hr_head'], got {allowed}"

        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["emp_id"] = hr_id
            sess["emp_name"] = "PARIMITA BEHERA"
            sess["role"] = "hr_head"
            sess["is_admin"] = False

        resp = client.get("/dashboard")
        html = resp.data.decode("utf-8")
        role_select = extract_role_select(html)
        assert role_select is not None, f"Role selector should appear for HR Head"
        assert "HR Head" in role_select, "Option 'HR Head' must be present in selector"
        assert "Employee" in role_select, "Option 'Employee' must be present in selector"
        assert "Department Head (HOD)" not in role_select, "Option 'Department Head (HOD)' must NOT be present in selector"
        assert "Unit Head" not in role_select, "Option 'Unit Head' must NOT be present in selector"
        print(f"  -> Verified: Selector has ONLY Employee and HR Head")

        client.post("/set-role", data={"role": "employee"}, follow_redirects=True)
        with client.session_transaction() as sess:
            assert sess.get("role") == "employee"

        client.post("/set-role", data={"role": "hr_head"}, follow_redirects=True)
        with client.session_transaction() as sess:
            assert sess.get("role") == "hr_head"

        client.post("/set-role", data={"role": "unit_head"}, follow_redirects=True)
        with client.session_transaction() as sess:
            assert sess.get("role") == "hr_head", "Switch to unit_head must be rejected"
        print(f"  -> Verified: Switching between employee and hr_head works, unit_head switch rejected")

        print("\n==================================================")
        print("TEST 4: Unit Head")
        print("==================================================")
        uh_cases = [("4050163", "SAMEER GAYAKWAD (Unit Head)")]
        for emp_id, desc in uh_cases:
            allowed = get_allowed_roles_for_employee(db, emp_id)
            print(f"Unit Head {emp_id} ({desc}): allowed_roles={allowed}")
            assert allowed == ["employee", "unit_head"], f"Expected ['employee', 'unit_head'], got {allowed}"

            with client.session_transaction() as sess:
                sess["logged_in"] = True
                sess["emp_id"] = emp_id
                sess["emp_name"] = desc
                sess["role"] = "unit_head"
                sess["is_admin"] = False

            resp = client.get("/dashboard")
            html = resp.data.decode("utf-8")
            role_select = extract_role_select(html)
            assert role_select is not None, f"Role selector should appear for Unit Head {emp_id}"
            assert "Unit Head" in role_select, "Option 'Unit Head' must be present in selector"
            assert "Employee" in role_select, "Option 'Employee' must be present in selector"
            assert "Department Head (HOD)" not in role_select, "Option 'Department Head (HOD)' must NOT be present in selector"
            assert "HR Head" not in role_select, "Option 'HR Head' must NOT be present in selector"
            print(f"  -> Verified: Selector has ONLY Employee and Unit Head for {desc}")

            client.post("/set-role", data={"role": "employee"}, follow_redirects=True)
            with client.session_transaction() as sess:
                assert sess.get("role") == "employee"

            client.post("/set-role", data={"role": "unit_head"}, follow_redirects=True)
            with client.session_transaction() as sess:
                assert sess.get("role") == "unit_head"

            client.post("/set-role", data={"role": "dept_head"}, follow_redirects=True)
            with client.session_transaction() as sess:
                assert sess.get("role") == "unit_head", "Switch to dept_head must be rejected"
            print(f"  -> Verified: Switching between employee and unit_head works, dept_head switch rejected for {desc}")

        print("\n==================================================")
        print("ALL TESTS PASSED PERFECTLY!")
        print("==================================================")

        print("\n==================================================")
        print("ALL TESTS PASSED PERFECTLY!")
        print("==================================================")

    finally:
        db.close()

if __name__ == "__main__":
    run_tests()
