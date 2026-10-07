"""
Comprehensive test script to verify:
1. Unit Head and HR Head are STRICTLY fetched from Approval Management.
2. No arbitrary employee is fetched based on designation (Unit Head / VP) or department (HR).
3. Pipelines generated for requests strictly assign the Approval Management approvers.
4. Existing request REQ-2026-1014 stages are clean and point to Sameer Gayakwad.
"""
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
from modules.models import Employee, ApprovalWorkflow, ApprovalWorkflowStep, ApprovalStepApprover, ApprovalRequest
from app import (
    get_unit_head_from_approval_management,
    get_hr_head_from_approval_management,
    get_unit_head,
    get_hr_head,
    build_request_approval_pipeline,
)
import json

def test_strict_lookups():
    db = SessionLocal()
    try:
        print("=== Test 1: Strict Unit Head Lookup ===")
        uh = get_unit_head(db)
        print(f"Default Unit Head: {uh.employee_id} - {uh.employee_name} ({uh.designation})")
        assert uh is not None, "Unit Head should not be None"
        assert uh.employee_id == "4050163", f"Expected 4050163 (Sameer Gayakwad), got {uh.employee_id}"
        assert "SAMEER" in uh.employee_name.upper(), f"Expected Sameer Gayakwad, got {uh.employee_name}"

        # Test with workflow 1
        uh_wf1 = get_unit_head_from_approval_management(db, workflow_id=1)
        print(f"Workflow 1 Unit Head: {uh_wf1.employee_id} - {uh_wf1.employee_name}")
        assert uh_wf1.employee_id == "4050163"

        # Test with workflow 2
        uh_wf2 = get_unit_head_from_approval_management(db, workflow_id=2)
        print(f"Workflow 2 Unit Head: {uh_wf2.employee_id} - {uh_wf2.employee_name}")
        assert uh_wf2.employee_id == "4050163"

        # Check other employees who have 'Unit Head' in designation are NOT returned
        other_vps = db.query(Employee).filter(
            (Employee.designation.ilike("%UNIT HEAD%")) | (Employee.designation.ilike("%VICE PRESIDENT%"))
        ).all()
        print(f"Found {len(other_vps)} employees with Unit Head/VP in designation:")
        for emp in other_vps:
            print(f"  - {emp.employee_id}: {emp.employee_name} ({emp.designation})")
        assert uh.employee_id not in ["4050300", "4050831", "4050875"], "Must not be Vineeth, Suhas, or Vijayaraj"

        print("\n=== Test 2: Strict HR Head Lookup ===")
        hr = get_hr_head(db)
        print(f"Default HR Head: {hr.employee_id} - {hr.employee_name} ({hr.designation})")
        assert hr is not None, "HR Head should not be None"
        assert hr.employee_id == "4050702", f"Expected 4050702 (Parimita Behera), got {hr.employee_id}"
        assert "PARIMITA" in hr.employee_name.upper(), f"Expected Parimita Behera, got {hr.employee_name}"

        # Test with workflow 1
        hr_wf1 = get_hr_head_from_approval_management(db, workflow_id=1)
        print(f"Workflow 1 HR Head: {hr_wf1.employee_id} - {hr_wf1.employee_name}")
        assert hr_wf1.employee_id == "4050702"

        # Test with workflow 2
        hr_wf2 = get_hr_head_from_approval_management(db, workflow_id=2)
        print(f"Workflow 2 HR Head: {hr_wf2.employee_id} - {hr_wf2.employee_name}")
        assert hr_wf2.employee_id == "4050702"

        print("\n=== Test 3: Pipeline Creation for Regular Employee ===")
        emp_civil = db.query(Employee).filter(Employee.employee_id == "4070088").first() # Brahmanand Patil (Civil)
        pipeline = build_request_approval_pipeline(db, emp_civil, "CIVIL", workflow_id=1)
        stages = pipeline["stages"]
        print(f"Pipeline stages for regular Civil employee (total {len(stages)}):")
        for s in stages:
            print(f"  Stage {s['order']}: {s['name']} -> {s['approver_name']} ({s['approver_id']})")
        assert len(stages) == 3
        # Stage 1: Civil HOD (Surendra Thakur)
        assert stages[0]["approver_id"] == "4050053", f"Expected Civil HOD (4050053), got {stages[0]['approver_id']}"
        # Stage 2: HR Head from Approval Management (Parimita Behera)
        assert stages[1]["approver_id"] == "4050702", f"Expected HR Head (4050702), got {stages[1]['approver_id']}"
        # Stage 3: Unit Head from Approval Management (Sameer Gayakwad)
        assert stages[2]["approver_id"] == "4050163", f"Expected Unit Head (4050163), got {stages[2]['approver_id']}"

        print("\n=== Test 4: Pipeline Creation for HOD ===")
        hod_it = db.query(Employee).filter(Employee.employee_id == "4070156").first() # Pranay Satam (IT HOD)
        pipeline_hod = build_request_approval_pipeline(db, hod_it, "INFORMATION TECHNOLOGY", workflow_id=1)
        stages_hod = pipeline_hod["stages"]
        print(f"Pipeline stages for HOD (total {len(stages_hod)}):")
        for s in stages_hod:
            print(f"  Stage {s['order']}: {s['name']} -> {s['approver_name']} ({s['approver_id']})")
        assert len(stages_hod) == 2
        # Stage 1: HR Head from Approval Management (Parimita Behera)
        assert stages_hod[0]["approver_id"] == "4050702", f"Expected HR Head (4050702), got {stages_hod[0]['approver_id']}"
        # Stage 2: Unit Head from Approval Management (Sameer Gayakwad)
        assert stages_hod[1]["approver_id"] == "4050163", f"Expected Unit Head (4050163), got {stages_hod[1]['approver_id']}"

        print("\n=== Test 5: Verify REQ-2026-1014 Status and Stages ===")
        req = db.query(ApprovalRequest).filter(ApprovalRequest.id == "REQ-2026-1014").first()
        assert req is not None, "REQ-2026-1014 must exist"
        details = json.loads(req.details)
        print(f"REQ-2026-1014 current_stage: {req.current_stage}")
        for s in details.get("stages", []):
            print(f"  Stage {s.get('order')}: {s.get('name')} -> {s.get('approver_name')} ({s.get('approver_id')})")
            if s.get("role") == "unit_head":
                assert s.get("approver_id") == "4050163", f"Expected Sameer Gayakwad (4050163), got {s.get('approver_id')}"
            if s.get("role") == "hr_head":
                assert s.get("approver_id") == "4050702", f"Expected Parimita Behera (4050702), got {s.get('approver_id')}"

        print("\nALL STRICT LOOKUP TESTS PASSED SUCCESSFULLY!")
    finally:
        db.close()

if __name__ == "__main__":
    test_strict_lookups()
