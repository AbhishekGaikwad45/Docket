import sys
sys.path.insert(0, 'D:/Docket')
from database import SessionLocal
from modules.models import ApprovalRequest
import json

db = SessionLocal()
reqs = db.query(ApprovalRequest).order_by(ApprovalRequest.created_at.desc()).limit(15).all()
for r in reqs:
    print(f'ID: {r.id}, status: {r.status}, type: {r.request_type}, dept: {r.department}, step: {r.current_step_order}/{r.total_steps}, stage: {r.current_stage}')
    if r.details:
        d = json.loads(r.details) if isinstance(r.details, str) else r.details
        print(f"  cur: {d.get('current_approver_name')} ({d.get('current_approver_id')})")
        for s in d.get('stages', []):
            print(f"    s{s.get('order')}: {s.get('name')} -> {s.get('approver_name')} ({s.get('approver_id')})")
db.close()

