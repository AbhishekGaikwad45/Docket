import sys
sys.path.insert(0, 'D:/Docket')
from database import SessionLocal
from modules.models import ApprovalWorkflow, ApprovalRequest, Employee
import json

db = SessionLocal()
wfs = db.query(ApprovalWorkflow).all()
print('=== WORKFLOWS ===')
for wf in wfs:
    print(f'WF ID: {wf.id}, Name: {wf.name}, Code: {wf.code}, Active: {wf.is_active}')
    if wf.flow_data:
        fd = json.loads(wf.flow_data) if isinstance(wf.flow_data, str) else wf.flow_data
        print('  flow_data stages:')
        for s in fd.get('stages', []):
            print(f"    Stage: {s.get('name')}, order: {s.get('order')}, role: {s.get('role')}, approvers: {s.get('approvers')}")
    if wf.steps:
        print('  relational steps:')
        for st in wf.steps:
            apprs = [(a.employee_id, a.employee.employee_name if a.employee else None) for a in st.approvers]
            print(f"    Step: {st.step_name}, order: {st.step_order}, is_final: {st.is_final}, approvers: {apprs}")

print('\n=== RECENT REQUESTS ===')
reqs = db.query(ApprovalRequest).order_by(ApprovalRequest.created_at.desc()).limit(10).all()
for r in reqs:
    print(f'REQ: {r.id}, wf_id: {r.workflow_id}, type: {r.request_type}, dept: {r.department}, stage: {r.current_stage}, step: {r.current_step_order}/{r.total_steps}, status: {r.status}')
    if r.details:
        det = json.loads(r.details) if isinstance(r.details, str) else r.details
        print(f"  cur_approver: {det.get('current_approver_name')} ({det.get('current_approver_id')}), cur_role: {det.get('current_stage_role')}")
        stages_summary = [(s.get('order'), s.get('name'), s.get('role'), s.get('approver_name'), s.get('approver_id')) for s in det.get('stages', [])]
        print(f"  stages: {stages_summary}")

db.close()
