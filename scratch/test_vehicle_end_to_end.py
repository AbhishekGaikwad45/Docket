import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
from app import app, SessionLocal, Employee, ApprovalRequest, ApprovalWorkflow, build_request_approval_pipeline, is_vehicle_booking

def run_tests():
    db = SessionLocal()
    try:
        print("=== Test 1: Check Workflow 7 (Vehicle Booking) Stages ===")
        wf7 = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == 7).first()
        assert wf7 is not None, "Workflow 7 must exist"
        print(f"Workflow 7: {wf7.name}")
        if wf7.flow_data:
            fd = json.loads(wf7.flow_data)
            for stg in fd.get("stages", []):
                name = stg.get("name", "")
                print(f"  Stage: {name} (is_final={stg.get('is_final')})")
                assert "unit head" not in name.lower(), f"Unexpected Unit Head in {name}"
                assert "hr head" not in name.lower(), f"Unexpected HR Head in {name}"

        print("\n=== Test 2: Build Pipeline for Employee (Vehicle Booking) ===")
        emp = db.query(Employee).filter(Employee.department != None).first()
        pipe = build_request_approval_pipeline(db, emp, emp.department, workflow_id=7)
        for s in pipe["stages"]:
            print(f"  Order {s['order']}: {s['name']} | Approver: {s['approver_name']} ({s['role']}) | Desc: {s['description']}")
            assert "unit head" not in s["name"].lower(), f"Found Unit Head in stage name: {s['name']}"
            assert "hr head" not in s["name"].lower(), f"Found HR Head in stage name: {s['name']}"
            assert "unit head" not in s["description"].lower(), f"Found Unit Head in description: {s['description']}"
            assert "hr head" not in s["description"].lower(), f"Found HR Head in description: {s['description']}"

        print("\n=== Test 3: Create & Route a Vehicle Request Through Final Approval ===")
        # Remove any previous test req
        prev = db.query(ApprovalRequest).filter(ApprovalRequest.id == "REQ-TEST-VEHICLE-001").all()
        for p in prev:
            db.delete(p)
        db.commit()

        stages_meta = [
            {
                "order": s["order"],
                "name": s["name"],
                "role": s["role"],
                "approver_name": s["approver_name"],
                "approver_id": s["approver_id"],
                "is_final": s["is_final"],
                "description": s["description"],
            }
            for s in pipe["stages"]
        ]
        test_req = ApprovalRequest(
            id="REQ-TEST-VEHICLE-001",
            workflow_id=7,
            request_type="Vehicle Booking",
            title="END_TO_END_VEHICLE_TEST",
            department=emp.department,
            applicant_emp_id=emp.employee_id,
            applicant_name=emp.employee_name,
            applicant_email=emp.email_id or "applicant@example.com",
            start_date="2026-10-15",
            end_date="2026-10-15",
            purpose="Official Port Inspection",
            status="pending",
            current_stage=pipe["initial_stage"],
            current_step_order=1,
            total_steps=pipe["total_steps"],
            details=json.dumps({
                "stages": stages_meta,
                "current_stage_role": pipe["initial_role"],
                "current_approver_name": pipe["initial_approver"],
                "current_approver_id": pipe["initial_approver_id"],
                "form_fields": [
                    {"name": "pickup_location", "label": "Pickup Location", "value": "Port Gate 1"},
                    {"name": "drop_location", "label": "Drop Location", "value": "Main Jetty"},
                    {"name": "passenger_count", "label": "Passenger Count", "value": "2"}
                ]
            }),
            approval_history=json.dumps([])
        )
        db.add(test_req)
        db.commit()

        assert is_vehicle_booking(test_req) == True, "Request should be detected as vehicle booking"
        print(f"Created {test_req.id}: Current Stage = {test_req.current_stage}")

        client = app.test_client()

        # Approve through all stages sequentially
        for idx, stg in enumerate(stages_meta):
            step_num = idx + 1
            print(f"\nStep {step_num}: Approving {stg['name']} with {stg['approver_name']} ({stg['approver_id']})")
            with client.session_transaction() as sess:
                sess["logged_in"] = True
                sess["emp_id"] = stg["approver_id"]
                sess["emp_name"] = stg["approver_name"]
                sess["role"] = stg["role"]
                sess["allowed_roles"] = ["employee", stg["role"]]

            resp = client.post(f"/approvals/decide/REQ-TEST-VEHICLE-001", data={"decision": "approve", "remark": f"Approved step {step_num}"}, follow_redirects=True)
            assert resp.status_code == 200

            db.expire_all()
            curr = db.query(ApprovalRequest).filter(ApprovalRequest.id == "REQ-TEST-VEHICLE-001").first()
            print(f"  After Step {step_num}: Current Stage = {curr.current_stage}, Step Order = {curr.current_step_order}, Status = {curr.status}")
            assert "unit head" not in curr.current_stage.lower()
            assert "hr head" not in curr.current_stage.lower()

        db.expire_all()
        final_req = db.query(ApprovalRequest).filter(ApprovalRequest.id == "REQ-TEST-VEHICLE-001").first()
        print(f"\nAfter Pipeline Completion: Status = {final_req.status}, Action By = {final_req.action_by}")
        assert final_req.status == "approved"

        print("\n=== Test 4: Admin Portal & Vehicle Details Allocation ===")
        # Test Admin accessing /admin/approvals and assigning vehicle details
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["emp_id"] = "ADMIN001"
            sess["emp_name"] = "Portal Administrator"
            sess["is_admin"] = True
            sess["role"] = "admin"

        # Check /admin/approvals HTML contains the vehicle button
        resp = client.get("/admin/approvals")
        assert resp.status_code == 200
        # Check /admin/approvals HTML contains the vehicle button
        resp = client.get("/admin/approvals")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "REQ-TEST-VEHICLE-001" in html
        assert "Arrange Car" in html or "Car Details" in html, "Vehicle button must appear for approved vehicle request"
        print("Verified: Admin Approvals portal displays Arrange Car button linking to separate tab!")

        # Check new separate tab: /admin/vehicles
        resp = client.get("/admin/vehicles")
        assert resp.status_code == 200
        v_html = resp.get_data(as_text=True)
        assert "REQ-TEST-VEHICLE-001" in v_html, "Request must be visible in dedicated Vehicle Allocation tab"
        assert "Vehicle Allocation" in v_html
        print("Verified: Dedicated Vehicle Allocation tab (/admin/vehicles) renders request queue and form!")

        # Check direct request loading in /admin/vehicles?req_id=...
        resp = client.get(f"/admin/vehicles?req_id={test_req.id}")
        assert resp.status_code == 200
        v_detail_html = resp.get_data(as_text=True)
        assert test_req.id in v_detail_html
        print(f"Verified: /admin/vehicles?req_id={test_req.id} pre-selects the requisition into the form!")

        # Post vehicle assignment details as admin
        assign_payload = {
            "fields": [
                {"name": "vehicle_number", "label": "Vehicle Registration No", "value": "MH-06-BW-5432"},
                {"name": "driver_name", "label": "Driver Name", "value": "Ramesh Patil"},
                {"name": "driver_contact", "label": "Driver Contact No", "value": "+91 9876543210"},
                {"name": "vehicle_model", "label": "Vehicle Model / Type", "value": "Toyota Innova Crysta (AC)"},
                {"name": "reporting_time", "label": "Reporting Time", "value": "08:30 AM"},
                {"name": "pickup_location", "label": "Pickup Location", "value": "Port Gate 1"}
            ],
            "notes": "Vehicle will report 15 mins prior to reporting time at Port Gate 1."
        }

        resp = client.post(
            "/admin/approvals/REQ-TEST-VEHICLE-001/assign-vehicle",
            data=json.dumps(assign_payload),
            content_type="application/json"
        )
        assert resp.status_code == 200, f"Expected 200 from assign-vehicle, got {resp.status_code}"
        res_data = resp.get_json()
        assert res_data["success"] == True
        print("Assign Vehicle Response:", res_data["message"])

        db.expire_all()
        updated_req = db.query(ApprovalRequest).filter(ApprovalRequest.id == "REQ-TEST-VEHICLE-001").first()
        details = json.loads(updated_req.details)
        assert "vehicle_allocation" in details, "vehicle_allocation must be saved in details"
        alloc = details["vehicle_allocation"]
        print(f"Saved Vehicle Allocation: {len(alloc['fields'])} fields, assigned by {alloc['assigned_by']}")
        assert len(alloc["fields"]) == 6
        assert alloc["fields"][0]["value"] == "MH-06-BW-5432"
        assert alloc["fields"][1]["value"] == "Ramesh Patil"

        # Verify button in Admin portal changes to Car Details
        resp = client.get("/admin/approvals")
        html = resp.get_data(as_text=True)
        assert "Car Details" in html, "Button should change to Car Details after assignment"
        print("Verified: Button switched to Car Details indicating completed allocation!")

        print("\nALL VERIFICATIONS PASSED SUCCESSFULLY!")

    finally:
        db.close()

if __name__ == "__main__":
    run_tests()
