import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import SessionLocal
from modules.models import Employee, ApprovalWorkflow, ApprovalWorkflowStep, ApprovalStepApprover
import json

db = SessionLocal()
try:
    workflows = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.is_active == True).all()
    print(f"Active workflows: {len(workflows)}")
    for wf in workflows:
        print(f"Workflow ID: {wf.id}, Name: {wf.name}")
        if wf.flow_data:
            fd = json.loads(wf.flow_data) if isinstance(wf.flow_data, str) else wf.flow_data
            stages = fd.get("stages", [])
            for s in stages:
                apprs = [a.get("employee_id") for a in s.get("approvers", [])]
                print(f"   Stage: {s.get('name')}, role: {s.get('role')}, approvers: {apprs}")
        if wf.steps:
            for st in wf.steps:
                step_apprs = [a.employee_id for a in st.approvers]
                print(f"   DB Step: {st.step_name}, approvers: {step_apprs}")

    departments = [d[0] for d in db.query(Employee.department).distinct().all() if d[0]]
    print(f"\nTotal distinct departments: {len(departments)}")
    from app import get_department_hod, get_hr_head_from_approval_management, get_unit_head_from_approval_management
    
    uh = get_unit_head_from_approval_management(db)
    print(f"UH: {uh.employee_id if uh else None} - {uh.employee_name if uh else None}")
    
    hr = get_hr_head_from_approval_management(db)
    print(f"HR: {hr.employee_id if hr else None} - {hr.employee_name if hr else None}")
    
    print("\nDepartment HODs:")
    for d in departments:
        hod = get_department_hod(db, d)
        if hod:
            print(f"  {d} -> {hod.employee_id} ({hod.employee_name})")
finally:
    db.close()
