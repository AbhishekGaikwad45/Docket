"""
End-to-end verification script for Dynamic Form Builder & Workflow Form Lifecycle:
1. Verifies database model & ApprovalWorkflow.form_schema persistence
2. Tests saving/updating workflow dynamic form schemas with various data types (date, text, number, select, etc.)
3. Tests dynamic form validation (missing required fields return 400 with descriptive error)
4. Tests request submission with complete dynamic form data
5. Verifies stored form_fields and form_data in approval_requests.details
6. Tests employee dashboard & approver queue rendering
"""

import os
import sys
import json

# Ensure project root is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app import app
from database import SessionLocal
from modules.models import ApprovalWorkflow, ApprovalRequest, Employee

def test_dynamic_forms():
    print("=== STARTING DYNAMIC FORMS END-TO-END TEST ===")
    client = app.test_client()

    # 1. Check database workflow schemas
    db = SessionLocal()
    try:
        wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.is_active == True).first()
        assert wf is not None, "At least one active ApprovalWorkflow must exist"
        wf_id = wf.id
        orig_schema = wf.form_schema
        print(f"[OK] Found active workflow: {wf.name} (ID: {wf_id})")

        # 2. Test saving a new dynamic form schema to this workflow
        custom_schema = {
            "enabled": True,
            "title": f"Dynamic Test Application ({wf.name})",
            "description": "Please complete all test fields below.",
            "fields": [
                {
                    "id": "applicant_project",
                    "name": "applicant_project",
                    "label": "Project / Cost Center Code",
                    "type": "text",
                    "required": True,
                    "placeholder": "e.g. PRJ-2026-ENG",
                    "width": "half"
                },
                {
                    "id": "budget_amount",
                    "name": "budget_amount",
                    "label": "Estimated Budget (INR)",
                    "type": "number",
                    "required": True,
                    "min": 100,
                    "max": 1000000,
                    "placeholder": "50000",
                    "width": "half"
                },
                {
                    "id": "event_date",
                    "name": "event_date",
                    "label": "Execution / Event Date",
                    "type": "date",
                    "required": True,
                    "width": "half"
                },
                {
                    "id": "priority_level",
                    "name": "priority_level",
                    "label": "Priority Level",
                    "type": "select",
                    "required": True,
                    "options": ["Standard", "High Priority", "Urgent / Critical"],
                    "width": "half"
                },
                {
                    "id": "compliance_confirmed",
                    "name": "compliance_confirmed",
                    "label": "I confirm compliance with safety & security standards",
                    "type": "checkbox",
                    "required": True,
                    "width": "full"
                },
                {
                    "id": "justification_notes",
                    "name": "justification_notes",
                    "label": "Detailed Justification & Scope",
                    "type": "textarea",
                    "required": True,
                    "width": "full",
                    "placeholder": "Provide scope of work and business justification..."
                }
            ]
        }

        # Test POST /admin/approval-workflows/<id>/form-schema
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["is_admin"] = True
            sess["emp_id"] = "ADMIN-001"
            sess["role"] = "admin"

        save_res = client.post(
            f"/admin/approval-workflows/{wf_id}/form-schema",
            json={"form_schema": custom_schema}
        )
        assert save_res.status_code == 200, f"Failed to save form schema: {save_res.data}"
        save_data = save_res.get_json()
        assert save_data.get("success") is True, f"Save unsuccessful: {save_data}"
        print(f"[OK] Successfully saved dynamic form schema for workflow {wf_id}")

        # Test GET /api/workflows/<id>/form
        get_res = client.get(f"/api/workflows/{wf_id}/form")
        assert get_res.status_code == 200, f"Failed to get form schema: {get_res.data}"
        get_data = get_res.get_json()
        assert get_data.get("form_schema", {}).get("title") == custom_schema["title"], "Retrieved schema title mismatch"
        assert len(get_data.get("form_schema", {}).get("fields", [])) == 6, "Retrieved field count mismatch"
        print("[OK] GET /api/workflows/<id>/form returned exact saved schema with 6 fields")

        # 3. Test client submitting request with MISSING required field
        # Set employee session
        emp = db.query(Employee).filter(Employee.department != None, Employee.department != "").first()
        emp_id = emp.employee_id if emp else "EMP-001"
        emp_dept = emp.department if (emp and emp.department) else "OPERATIONS"

        with client.session_transaction() as sess:
            sess.clear()
            sess["logged_in"] = True
            sess["emp_id"] = emp_id
            sess["emp_name"] = emp.employee_name if emp else "Test User"
            sess["role"] = "employee"

        incomplete_payload = {
            "workflow_id": wf_id,
            "department": emp_dept,
            "form_data": {
                "applicant_project": "PRJ-9999",
                # missing budget_amount!
                "event_date": "2026-10-15",
                "priority_level": "High Priority",
                "compliance_confirmed": True,
                "justification_notes": "Urgent scope"
            }
        }

        bad_sub = client.post("/requests/submit", json=incomplete_payload)
        assert bad_sub.status_code == 400, f"Expected 400 for missing required field, got {bad_sub.status_code}"
        bad_json = bad_sub.get_json()
        assert "Estimated Budget (INR)" in bad_json.get("message", ""), f"Error message should mention missing field: {bad_json}"
        print(f"[OK] Validation correctly caught missing required field: {bad_json['message']}")

        # 4. Test submitting with unconfirmed required checkbox
        unchecked_payload = {
            "workflow_id": wf_id,
            "department": emp_dept,
            "form_data": {
                "applicant_project": "PRJ-9999",
                "budget_amount": "75000",
                "event_date": "2026-10-15",
                "priority_level": "High Priority",
                "compliance_confirmed": False, # Required checkbox is false!
                "justification_notes": "Urgent scope"
            }
        }
        bad_check = client.post("/requests/submit", json=unchecked_payload)
        assert bad_check.status_code == 400, f"Expected 400 for unconfirmed required checkbox, got {bad_check.status_code}"
        print("[OK] Validation correctly rejected unconfirmed required checkbox")

        # 5. Test valid request submission
        valid_payload = {
            "workflow_id": wf_id,
            "department": emp_dept,
            "form_data": {
                "applicant_project": "PRJ-2026-DH-01",
                "budget_amount": "85000",
                "event_date": "2026-11-01",
                "priority_level": "High Priority",
                "compliance_confirmed": True,
                "justification_notes": "Complete port facility modernization and security upgrade project."
            }
        }
        good_sub = client.post("/requests/submit", json=valid_payload)
        assert good_sub.status_code == 200, f"Valid submission failed: {good_sub.data}"
        good_json = good_sub.get_json()
        assert good_json.get("success") is True, f"Submission returned false: {good_json}"
        created_req = good_json.get("request", {})
        req_id = created_req.get("id")
        assert req_id is not None, "Request ID should be returned"
        print(f"[OK] Successfully submitted dynamic request: {req_id}")

        # 6. Verify database record details
        db.expire_all()
        req_db = db.query(ApprovalRequest).filter(ApprovalRequest.id == req_id).first()
        assert req_db is not None, f"Request {req_id} not found in DB"
        details = json.loads(req_db.details)
        assert details.get("has_dynamic_form") is True, "has_dynamic_form flag must be True"
        assert "form_data" in details, "form_data dict must be stored"
        assert details["form_data"]["applicant_project"] == "PRJ-2026-DH-01"
        assert details["form_data"]["budget_amount"] == "85000"
        assert "form_fields" in details, "form_fields list must be stored"
        assert len(details["form_fields"]) == 6, f"Expected 6 form fields, got {len(details['form_fields'])}"
        print("[OK] Database contains fully structured dynamic form_data and form_fields records")

        # 7. Test Dashboard view rendering for employee
        dash_res = client.get("/dashboard")
        assert dash_res.status_code == 200, f"Dashboard failed: {dash_res.status_code}"
        dash_html = dash_res.data.decode("utf-8")
        assert req_id in dash_html, f"Dashboard should display submitted request {req_id}"
        assert "user-dynamic-fields-container" in dash_html, "Dashboard must have dynamic fields container"
        assert "utl-form-details-card" in dash_html, "Dashboard must have dynamic details card in timeline modal"
        print("[OK] Dashboard HTML contains dynamic form container, timeline details card, and submitted request")

        # 8. Test Admin Approvals view rendering
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["is_admin"] = True
            sess["emp_id"] = "ADMIN-001"
            sess["role"] = "admin"

        admin_res = client.get("/admin/approvals")
        assert admin_res.status_code == 200, f"Admin approvals failed: {admin_res.status_code}"
        admin_html = admin_res.data.decode("utf-8")
        assert req_id in admin_html, f"Admin approvals must show request {req_id}"
        assert "det-extra-fields" in admin_html, "Admin details modal must have det-extra-fields container"
        assert "admin-dynamic-fields-container" in admin_html, "Admin modal must have dynamic fields container"
        print("[OK] Admin Approvals view contains dynamic inspection card and new request dynamic container")

        print("=== ALL DYNAMIC FORM TESTS PASSED SUCCESSFULLY! ===")

    finally:
        # Restore workflow schema
        try:
            wf_restore = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == wf_id).first()
            if wf_restore:
                wf_restore.form_schema = orig_schema
                db.commit()
                print(f"[CLEANUP] Restored workflow {wf_id} original schema")
        except Exception as e:
            print(f"[CLEANUP WARNING] Could not restore schema: {e}")
        db.close()

if __name__ == "__main__":
    test_dynamic_forms()
