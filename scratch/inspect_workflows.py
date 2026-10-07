import sys, os, json
sys.path.insert(0, r'D:\Docket')
from database import SessionLocal
from modules.models import ApprovalWorkflow, Employee

db = SessionLocal()
try:
    for wf in db.query(ApprovalWorkflow).all():
        print(f"*** WF {wf.id}: {wf.name} (code: {wf.code}) ***")
        fd = json.loads(wf.flow_data) if isinstance(wf.flow_data, str) else wf.flow_data
        if fd:
            for s in fd.get('stages', []):
                apprs = []
                for a in s.get('approvers', []):
                    emp = db.query(Employee).filter(Employee.employee_id == a.get('employee_id')).first()
                    apprs.append(f"{a.get('employee_id')} ({emp.employee_name if emp else '?'})")
                print(f"  Stage {s.get('order')}: '{s.get('name')}' | dept: {s.get('department')} | role: {s.get('role')} | is_final: {s.get('is_final')} | is_hr: {s.get('is_hr_stage')} | is_hod: {s.get('is_hod_stage')} | approvers: {', '.join(apprs)}")
finally:
    db.close()

