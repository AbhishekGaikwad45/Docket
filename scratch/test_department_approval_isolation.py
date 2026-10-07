import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from app import app, SessionLocal, Employee, build_request_approval_pipeline, is_employee_hod
from modules.models import ApprovalRequest

def run_tests():
    print("=====================================================================")
    print("RUNNING DEPARTMENT APPROVAL ROUTING & ISOLATION TEST SUITE")
    print("=====================================================================")
    
    db = SessionLocal()
    test_client = app.test_client()
    
    try:
        # TEST 1: Pipeline HOD Routing across all departments
        print("\n--- TEST 1: Pipeline HOD Routing for All Departments ---")
        depts = [d[0] for d in db.query(Employee.department).filter(Employee.department != None, Employee.employee_status.ilike("active")).distinct() if d[0]]
        
        for dept in sorted(depts):
            emps = db.query(Employee).filter(Employee.department == dept, Employee.employee_status.ilike("active")).all()
            reg_emp = next((e for e in emps if not is_employee_hod(e)), None)
            if not reg_emp:
                continue
            
            # Workflow 1
            pipe1 = build_request_approval_pipeline(db, reg_emp, dept, workflow_id=1)
            s1 = pipe1["stages"][0]
            assert s1["role"] == "dept_head", f"Expected dept_head role, got {s1['role']}"
            assert s1["approver_id"] is not None, f"Approver ID should not be None for {dept}"
            
            # Approver must belong to the same department or alias
            appr = db.query(Employee).filter(Employee.employee_id == s1["approver_id"]).first()
            assert appr is not None, f"Approver {s1['approver_id']} not found in DB"
            appr_dept = (appr.department or "").strip().upper()
            dept_up = dept.strip().upper()
            
            dept_match = (
                appr_dept == dept_up
                or dept_up in appr_dept
                or appr_dept in dept_up
                or (dept_up in ("IT", "INFORMATION TECHNOLOGY") and appr_dept in ("IT", "INFORMATION TECHNOLOGY"))
                or (dept_up in ("HR", "HR & ADMIN") and appr_dept in ("HR", "HR & ADMIN"))
                or (dept_up in ("MARINE", "MARINE OPERATIONS") and appr_dept in ("MARINE", "MARINE OPERATIONS"))
            )
            assert dept_match, f"Cross-department leakage: Submitter dept={dept}, but Approver dept={appr.department} (Approver={appr.employee_name})"
            print(f"  [PASS] {dept:23} -> {s1['approver_name']} ({appr.department})")
            
            # Workflow 4
            pipe4 = build_request_approval_pipeline(db, reg_emp, dept, workflow_id=4)
            s4 = pipe4["stages"][0]
            assert s4["role"] == "dept_head"
            appr4 = db.query(Employee).filter(Employee.employee_id == s4["approver_id"]).first()
            appr4_dept = (appr4.department or "").strip().upper()
            dept4_match = (
                appr4_dept == dept_up
                or dept_up in appr4_dept
                or appr4_dept in dept_up
                or (dept_up in ("IT", "INFORMATION TECHNOLOGY") and appr4_dept in ("IT", "INFORMATION TECHNOLOGY"))
                or (dept_up in ("HR", "HR & ADMIN") and appr4_dept in ("HR", "HR & ADMIN"))
                or (dept_up in ("MARINE", "MARINE OPERATIONS") and appr4_dept in ("MARINE", "MARINE OPERATIONS"))
            )
            assert dept4_match, f"WF4 Cross-department leakage: Submitter dept={dept}, but Approver dept={appr4.department} (Approver={appr4.employee_name})"

        print("All department pipelines route strictly to their own HOD!")

        # TEST 2: Inbox Isolation for Department Heads
        print("\n--- TEST 2: Approvals Inbox Scoping ---")
        
        # Ensure test requests are in pending state for inbox testing
        r1011 = db.query(ApprovalRequest).filter(ApprovalRequest.id == "REQ-2026-1011").first()
        if r1011:
            r1011.status = "pending"
            r1011.current_step_order = 1
            r1011.current_stage = "MECHANICAL Head Review"
        r1010 = db.query(ApprovalRequest).filter(ApprovalRequest.id == "REQ-2026-1010").first()
        if r1010:
            r1010.status = "pending"
            r1010.current_step_order = 1
            r1010.current_stage = "Marine Operations Head Review"
        db.commit()

        # A. Civil HOD Surendra Thakur (4050053)
        with test_client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["emp_id"] = "4050053"
            sess["emp_name"] = "SURENDRA THAKUR"
            sess["role"] = "dept_head"
            sess["is_admin"] = False
        
        resp = test_client.get("/approvals")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "REQ-2026-1011" not in html, "Civil HOD should NOT see Mechanical request REQ-2026-1011!"
        assert "REQ-2026-1010" not in html, "Civil HOD should NOT see Marine Operations request REQ-2026-1010!"
        print("  [PASS] Civil HOD does NOT see Mechanical or Marine Operations requests.")

        # B. Mechanical HOD Pundalik Paradhi (4070025)
        with test_client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["emp_id"] = "4070025"
            sess["emp_name"] = "PUNDALIK PARADHI"
            sess["role"] = "dept_head"
            sess["is_admin"] = False
        
        resp = test_client.get("/approvals")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "REQ-2026-1011" in html, "Mechanical HOD SHOULD see Mechanical request REQ-2026-1011!"
        assert "REQ-2026-1010" not in html, "Mechanical HOD should NOT see Marine Operations request REQ-2026-1010!"
        print("  [PASS] Mechanical HOD sees REQ-2026-1011 and does NOT see Marine Operations requests.")

        # C. Marine Operations HOD Sibaram Rauta (4070122)
        with test_client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["emp_id"] = "4070122"
            sess["emp_name"] = "SIBARAM RAUTA"
            sess["role"] = "dept_head"
            sess["is_admin"] = False
        
        resp = test_client.get("/approvals")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "REQ-2026-1010" in html, "Marine Operations HOD SHOULD see Marine Operations request REQ-2026-1010!"
        assert "REQ-2026-1011" not in html, "Marine Operations HOD should NOT see Mechanical request REQ-2026-1011!"
        print("  [PASS] Marine Operations HOD sees REQ-2026-1010 and does NOT see Mechanical requests.")

        # TEST 3: Cross-Department Approval Authorization Guard
        print("\n--- TEST 3: Cross-Department Action Guard ---")
        # Try to approve REQ-2026-1011 (Mechanical) as Civil HOD Surendra Thakur
        with test_client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["emp_id"] = "4050053"
            sess["emp_name"] = "SURENDRA THAKUR"
            sess["role"] = "dept_head"
            sess["is_admin"] = False
        
        resp = test_client.post("/approvals/decide/REQ-2026-1011", data={"decision": "approve", "remark": "Unauthorized attempt"}, follow_redirects=True)
        html = resp.data.decode("utf-8")
        assert "Unauthorized" in html, "Cross-department approval attempt must be blocked with Unauthorized flash message!"
        print("  [PASS] Civil HOD was blocked from approving Mechanical request.")

        # TEST 4: Valid Department Head Approval
        print("\n--- TEST 4: Authorized HOD Approval & Stage Advance ---")
        with test_client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["emp_id"] = "4070025"
            sess["emp_name"] = "PUNDALIK PARADHI"
            sess["role"] = "dept_head"
            sess["is_admin"] = False
        
        resp = test_client.post("/approvals/decide/REQ-2026-1011", data={"decision": "approve", "remark": "Approved by Mechanical HOD"}, follow_redirects=True)
        assert resp.status_code == 200
        
        # Verify in DB that it advanced to Stage 2 (HR Head Review)
        db.expire_all()
        req1011 = db.query(ApprovalRequest).filter(ApprovalRequest.id == "REQ-2026-1011").first()
        assert req1011.current_step_order == 2
        assert "HR" in req1011.current_stage.upper()
        details_1011 = json.loads(req1011.details)
        assert details_1011["current_stage_role"] == "hr_head"
        assert details_1011["current_approver_id"] == "4050702"
        print(f"  [PASS] REQ-2026-1011 successfully advanced to {req1011.current_stage} (Approver: {details_1011['current_approver_name']}).")

        # Rollback / Reset REQ-2026-1011 back to stage 1 for idempotence
        pipe_reset = build_request_approval_pipeline(db, req1011.applicant, "MECHANICAL", req1011.workflow_id)
        details_1011["stages"] = pipe_reset["stages"]
        details_1011["current_stage_role"] = pipe_reset["initial_role"]
       
       
        details_1011["current_approver_name"] = pipe_reset["initial_approver"]
        details_1011["current_approver_id"] = pipe_reset["initial_approver_id"]
        req1011.details = json.dumps(details_1011)
        req1011.current_stage = pipe_reset["initial_stage"]
        req1011.current_step_order = 1
        db.commit()

        print("\n=====================================================================")
        print("ALL TESTS PASSED! Department approvals are strictly isolated to their own HODs.")
        print("=====================================================================")

    finally:
        db.close()

if __name__ == "__main__":
    run_tests()
