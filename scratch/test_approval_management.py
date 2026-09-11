import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
from database import SessionLocal, init_db_defaults, engine, inspect
from modules.models import (
    User,
    Employee,
    ApprovalWorkflow,
    ApprovalWorkflowStep,
    ApprovalStepApprover,
)
from app import app

def run_tests():
    print("=== 1. Testing Database Tables ===")
    insp = inspect(engine)
    tables = insp.get_table_names()
    print("Detected DB tables:", tables)
    assert "approval_workflows" in tables, "approval_workflows table missing!"
    assert "approval_workflow_steps" in tables, "approval_workflow_steps table missing!"
    assert "approval_step_approvers" in tables, "approval_step_approvers table missing!"
    print("PASS: All approval tables verified in PostgreSQL.")

    print("\n=== 2. Testing Seeded Workflow ===")
    db = SessionLocal()
    try:
        wfs = db.query(ApprovalWorkflow).all()
        print(f"Total workflows in DB: {len(wfs)}")
        assert len(wfs) >= 1, "Expected at least 1 default seeded workflow!"
        first_wf = wfs[0]
        print(f"Workflow ID={first_wf.id}, Name='{first_wf.name}', Code='{first_wf.code}', Steps={len(first_wf.steps)}")
        assert first_wf.steps, "Expected default workflow to have steps!"
    finally:
        db.close()
    print("PASS: Seeded default workflow verified.")

    print("\n=== 3. Testing Flask Routes with Test Client ===")
    client = app.test_client()

    # Authenticate as admin via session
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["is_admin"] = True
        sess["emp_id"] = "admin"
        sess["role"] = "admin"

    # Test GET /admin/approval-management
    res = client.get("/admin/approval-management")
    print(f"GET /admin/approval-management -> status {res.status_code}")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    html = res.data.decode("utf-8")
    assert "Approval Management" in html
    assert "wf-tree-pipeline" in html
    assert "palette-employee-list" in html
    print("PASS: GET /admin/approval-management renders properly.")

    # Test POST /admin/approval-workflows/create
    test_code = "auto_test_gate_pass"
    test_name = "Automated Test Gate Pass"

    # Clean up any leftover test wf with this code
    db = SessionLocal()
    try:
        old_wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.code == test_code).first()
        if old_wf:
            db.delete(old_wf)
            db.commit()
    finally:
        db.close()

    res = client.post(
        "/admin/approval-workflows/create",
        json={"name": test_name, "code": test_code, "description": "Workflow for gate pass testing"}
    )
    print(f"POST /admin/approval-workflows/create -> status {res.status_code}")
    assert res.status_code == 200, f"Create failed: {res.data}"
    create_data = res.get_json()
    assert create_data["success"] is True
    wf_id = create_data["id"]
    print(f"Created workflow ID: {wf_id}")

    # Pick up to 2 real employees from DB to assign
    db = SessionLocal()
    try:
        sample_employees = db.query(Employee).limit(3).all()
        emp_ids = [e.employee_id for e in sample_employees]
    finally:
        db.close()

    print(f"Sample employees for assignment: {emp_ids}")
    approvers_stage1 = [{"employee_id": emp_ids[0], "role_label": "Final Authority"}] if len(emp_ids) > 0 else []
    approvers_stage2 = [{"employee_id": eid, "role_label": "Reviewer"} for eid in emp_ids]

    # Test POST /admin/approval-workflows/<id>/save with hierarchical stages & multiple approvers
    flow_payload = {
        "workflow_id": wf_id,
        "name": test_name,
        "description": "Updated description for gate pass",
        "stages": [
            {
                "id": "stage-final-1",
                "name": "Final Security Approval",
                "is_final": True,
                "order": 1,
                "parent_id": None,
                "approvers": approvers_stage1
            },
            {
                "id": "stage-sub-2",
                "name": "Department Head Verification",
                "is_final": False,
                "order": 2,
                "parent_id": "stage-final-1",
                "approvers": approvers_stage2
            }
        ]
    }

    res = client.post(
        f"/admin/approval-workflows/{wf_id}/save",
        json={"flow_data": flow_payload, "name": test_name, "description": "Updated description"}
    )
    print(f"POST /admin/approval-workflows/{wf_id}/save -> status {res.status_code}")
    assert res.status_code == 200, f"Save failed: {res.data}"
    save_data = res.get_json()
    assert save_data["success"] is True

    # Verify Relational Database Structure
    db = SessionLocal()
    try:
        wf_record = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == wf_id).first()
        assert wf_record is not None
        assert len(wf_record.steps) == 2, f"Expected 2 steps, got {len(wf_record.steps)}"

        final_step = next(s for s in wf_record.steps if s.is_final)
        sub_step = next(s for s in wf_record.steps if not s.is_final)

        assert final_step.step_name == "Final Security Approval"
        assert sub_step.step_name == "Department Head Verification"
        assert sub_step.parent_step_id == final_step.id, "Hierarchical parent_step_id link broken!"

        if approvers_stage2:
            assert len(sub_step.approvers) == len(approvers_stage2), "Many-to-many approver assignments mismatch!"

        # Verify JSON flow_data stored
        parsed_flow = json.loads(wf_record.flow_data)
        assert len(parsed_flow["stages"]) == 2
        print("PASS: Relational database steps, hierarchy, and approvers verified in PostgreSQL.")
    finally:
        db.close()

    # Test GET /admin/approval-workflows/<id>/json
    res = client.get(f"/admin/approval-workflows/{wf_id}/json")
    print(f"GET /admin/approval-workflows/{wf_id}/json -> status {res.status_code}")
    assert res.status_code == 200
    json_data = res.get_json()
    assert json_data["success"] is True
    assert json_data["name"] == test_name
    assert len(json_data["flow_data"]["stages"]) == 2
    print("PASS: GET json route returned exact restored workflow layout.")

    # Test POST /admin/approval-workflows/<id>/rename
    new_name = "Automated Test Gate Pass (Renamed)"
    res = client.post(
        f"/admin/approval-workflows/{wf_id}/rename",
        json={"name": new_name, "description": "Renamed description"}
    )
    assert res.status_code == 200
    assert res.get_json()["success"] is True
    print("PASS: Workflow rename verified.")

    # Test POST /admin/approval-workflows/<id>/delete
    res = client.post(f"/admin/approval-workflows/{wf_id}/delete")
    assert res.status_code == 200
    assert res.get_json()["success"] is True

    # Verify Cascade Delete from DB
    db = SessionLocal()
    try:
        deleted_wf = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.id == wf_id).first()
        assert deleted_wf is None, "Workflow was not deleted!"
        orphan_steps = db.query(ApprovalWorkflowStep).filter(ApprovalWorkflowStep.workflow_id == wf_id).all()
        assert len(orphan_steps) == 0, "Orphan steps remained after deletion!"
        print("PASS: Cascading deletion verified successfully.")
    finally:
        db.close()

    print("\nALL AUTOMATED TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()
