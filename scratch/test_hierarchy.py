import json
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from database import SessionLocal
from modules.models import Employee, User, ApprovalRequest, GuestHouseRequest, ApprovalWorkflow
from app import is_employee_hod, get_department_hod, get_hr_head, get_unit_head, build_request_approval_pipeline

def run_tests():
    db = SessionLocal()
    try:
        print("=== 1. TESTING HELPER RESOLUTIONS ===")
        # Test regular employee
        vinayak = db.query(Employee).filter(Employee.employee_id == '4070124').first()
        assert vinayak is not None, "VINAYAK PATIL (4070124) should exist"
        is_vinayak_hod = is_employee_hod(vinayak)
        print(f"Vinayak Patil is_hod: {is_vinayak_hod} (Expected: False)")
        assert not is_vinayak_hod, "Vinayak Patil is an engineer, should NOT be HOD"

        # Test HOD
        surendra = db.query(Employee).filter(Employee.employee_id == '4050053').first()
        assert surendra is not None, "SURENDRA THAKUR (4050053) should exist"
        is_surendra_hod = is_employee_hod(surendra)
        print(f"Surendra Thakur is_hod: {is_surendra_hod} (Expected: True)")
        assert is_surendra_hod, "Surendra Thakur is Senior Manager (Civil), SHOULD be HOD"

        # Test HR Head
        hr_head = get_hr_head(db)
        print(f"HR Head detected: {hr_head.employee_name if hr_head else 'None'}")
        assert hr_head is not None, "HR Head should be found"

        # Test Unit Head
        unit_head = get_unit_head(db)
        print(f"Unit Head detected: {unit_head.employee_name if unit_head else 'None'}")
        assert unit_head is not None, "Unit Head should be found"

        # Test Department HOD
        civil_hod = get_department_hod(db, "CIVIL")
        print(f"CIVIL HOD detected: {civil_hod.employee_name if civil_hod else 'None'}")
        assert civil_hod is not None and civil_hod.employee_id == '4050053'

        print("\n=== 2. TESTING PIPELINE FOR EMPLOYEE (VINAYAK PATIL) ===")
        emp_pipeline = build_request_approval_pipeline(db, vinayak, vinayak.department)
        print(f"Employee pipeline is_hod: {emp_pipeline['is_hod']}")
        print(f"Total steps: {emp_pipeline['total_steps']}")
        print(f"Initial stage: {emp_pipeline['initial_stage']}")
        print(f"Initial role: {emp_pipeline['initial_role']}")
        for s in emp_pipeline['stages']:
            print(f"  Step {s['order']}: {s['name']} (Role: {s['role']}, Approver: {s['approver_name']})")
        
        assert emp_pipeline['total_steps'] == 3, "Employee pipeline must have 3 steps"
        assert "CIVIL" in emp_pipeline['stages'][0]['name'], "Step 1 must be CIVIL Head Review"
        assert emp_pipeline['stages'][0]['role'] == "dept_head"
        assert "HR" in emp_pipeline['stages'][1]['name'], "Step 2 must be HR Head Review"
        assert emp_pipeline['stages'][1]['role'] == "hr_head"
        assert "Unit Head" in emp_pipeline['stages'][2]['name'], "Step 3 must be Unit Head"
        assert emp_pipeline['stages'][2]['role'] == "unit_head"

        print("\n=== 3. TESTING PIPELINE FOR HOD (SURENDRA THAKUR) ===")
        hod_pipeline = build_request_approval_pipeline(db, surendra, surendra.department)
        print(f"HOD pipeline is_hod: {hod_pipeline['is_hod']}")
        print(f"Total steps: {hod_pipeline['total_steps']}")
        print(f"Initial stage: {hod_pipeline['initial_stage']}")
        print(f"Initial role: {hod_pipeline['initial_role']}")
        for s in hod_pipeline['stages']:
            print(f"  Step {s['order']}: {s['name']} (Role: {s['role']}, Approver: {s['approver_name']})")

        assert hod_pipeline['total_steps'] == 2, "HOD pipeline must have 2 steps"
        assert "HR" in hod_pipeline['stages'][0]['name'], "Step 1 for HOD must be HR Head Review"
        assert hod_pipeline['stages'][0]['role'] == "hr_head"
        assert "Unit Head" in hod_pipeline['stages'][1]['name'], "Step 2 for HOD must be Unit Head"
        assert hod_pipeline['stages'][1]['role'] == "unit_head"

        print("\n=== ALL HIERARCHY TESTS PASSED SUCCESSFULLY! ===")
    finally:
        db.close()

if __name__ == "__main__":
    run_tests()
