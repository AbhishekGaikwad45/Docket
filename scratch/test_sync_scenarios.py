import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
from database import SessionLocal
from modules.models import Employee, User, ApprovalWorkflow
from sync_service import sync_employees
import app as flask_app

def test_sync_scenarios():
    print("=== TEST 1: Sync Staff Only ===")
    res_staff = sync_employees(include_staff=True, include_associates=False, active_only=True)
    assert res_staff["success"], f"Sync failed: {res_staff['errors']}"
    assert res_staff["staff_fetched"] > 0, "No staff records fetched"
    assert res_staff["associates_fetched"] == 0, "Associates were fetched when staff only requested"

    s = SessionLocal()
    staff_count = s.query(Employee).filter(Employee.source_view == "view_EmployeeMaster_Report_Staff").count()
    assoc_count = s.query(Employee).filter(Employee.source_view == "view_EmployeeMaster_Report_Associates").count()
    s.close()

    print(f"DB after Staff sync: Staff={staff_count}, Associates={assoc_count}")
    assert staff_count > 0, "Staff records missing from DB"
    assert assoc_count == 0, f"Expected 0 associates in DB, found {assoc_count}"

    # Test via Flask test client
    with flask_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["emp_id"] = "4050053"
            sess["role"] = "admin"
            sess["is_admin"] = True
            sess["logged_in"] = True

        # Test User Management
        resp_admin = client.get("/department/admin")
        assert resp_admin.status_code == 200
        html_admin = resp_admin.get_data(as_text=True)
        assert f"Staff ({staff_count})" in html_admin
        assert "Associates (0)" in html_admin
        assert f"All types ({staff_count + 1})" in html_admin # +1 manual entry

        # Test Approval Management
        resp_wf = client.get("/admin/approval-management")
        assert resp_wf.status_code == 200
        html_wf = resp_wf.get_data(as_text=True)
        assert f'"source_type": "staff"' in html_wf
        assert f'"source_type": "associates"' not in html_wf

    print("=== TEST 1 PASSED: Staff only is correctly synced and isolated. ===")

    print("\n=== TEST 2: Sync Staff & Associates ===")
    res_both = sync_employees(include_staff=True, include_associates=True, active_only=True)
    assert res_both["success"], f"Sync failed: {res_both['errors']}"
    assert res_both["staff_fetched"] > 0
    assert res_both["associates_fetched"] > 0

    s = SessionLocal()
    staff_count2 = s.query(Employee).filter(Employee.source_view == "view_EmployeeMaster_Report_Staff").count()
    assoc_count2 = s.query(Employee).filter(Employee.source_view == "view_EmployeeMaster_Report_Associates").count()
    s.close()

    print(f"DB after Both sync: Staff={staff_count2}, Associates={assoc_count2}")
    assert staff_count2 > 0
    assert assoc_count2 > 0

    with flask_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["emp_id"] = "4050053"
            sess["role"] = "admin"
            sess["is_admin"] = True
            sess["logged_in"] = True

        resp_admin2 = client.get("/department/admin")
        assert resp_admin2.status_code == 200
        html_admin2 = resp_admin2.get_data(as_text=True)
        assert f"Staff ({staff_count2})" in html_admin2
        assert f"Associates ({assoc_count2})" in html_admin2

        resp_wf2 = client.get("/admin/approval-management")
        assert resp_wf2.status_code == 200
        html_wf2 = resp_wf2.get_data(as_text=True)
        assert f'"source_type": "staff"' in html_wf2
        assert f'"source_type": "associates"' in html_wf2

    print("=== TEST 2 PASSED: Staff & Associates both synced and visible. ===")

    print("\n=== TEST 3: Switch back to Staff Only ===")
    res_staff_again = sync_employees(include_staff=True, include_associates=False, active_only=True)
    assert res_staff_again["success"]
    assert res_staff_again.get("purged_count", 0) > 0, "Expected associate records to be purged"

    s = SessionLocal()
    staff_count3 = s.query(Employee).filter(Employee.source_view == "view_EmployeeMaster_Report_Staff").count()
    assoc_count3 = s.query(Employee).filter(Employee.source_view == "view_EmployeeMaster_Report_Associates").count()
    s.close()

    print(f"DB after Staff sync again: Staff={staff_count3}, Associates={assoc_count3}, Purged={res_staff_again['purged_count']}")
    assert staff_count3 > 0
    assert assoc_count3 == 0

    with flask_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["emp_id"] = "4050053"
            sess["role"] = "admin"
            sess["is_admin"] = True
            sess["logged_in"] = True

        resp_wf3 = client.get("/admin/approval-management")
        assert resp_wf3.status_code == 200
        html_wf3 = resp_wf3.get_data(as_text=True)
        assert f'"source_type": "associates"' not in html_wf3

    print("=== TEST 3 PASSED: Switching back to Staff Only purges associates again. ===")
    print("\nALL SYNC AND VISIBILITY TESTS COMPLETED SUCCESSFULLY!")

if __name__ == "__main__":
    test_sync_scenarios()
