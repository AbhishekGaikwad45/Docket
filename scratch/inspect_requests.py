import sys, os, json
sys.path.insert(0, r'D:\Docket')
from database import SessionLocal
from modules.models import ApprovalRequest

db = SessionLocal()
try:
    pending = db.query(ApprovalRequest).filter(ApprovalRequest.status == 'pending').all()
    print(f"Total pending requests: {len(pending)}")
    for p in pending:
        d = json.loads(p.details) if p.details else {}
        print(f"ID: {p.id} | Type: {p.request_type} | WF: {p.workflow_id} | Dept: {p.department}")
        print(f"   Current Stage: {p.current_stage} (step {p.current_step_order}/{p.total_steps})")
        print(f"   Current Approver: {d.get('current_approver_name')} ({d.get('current_approver_id')}) [role: {d.get('current_stage_role')}]")
        stages = d.get('stages', [])
        for s in stages:
            print(f"      Stage {s.get('order')}: {s.get('name')} -> {s.get('approver_name')} ({s.get('approver_id')}) [{s.get('role')}]")
finally:
    db.close()

