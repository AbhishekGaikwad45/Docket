import sys
sys.path.insert(0, 'D:/Docket')
from database import SessionLocal
from modules.models import ApprovalRequest
import json

db = SessionLocal()
reqs = db.query(ApprovalRequest).all()
stale_count = 0
for r in reqs:
    if r.details:
        d = json.loads(r.details) if isinstance(r.details, str) else r.details
        stale = False
        for s in d.get('stages', []):
            if s.get('role') == 'unit_head' and s.get('approver_id') != '4050163':
                stale = True
            if s.get('role') == 'hr_head' and s.get('approver_id') != '4050702':
                stale = True
        if stale:
            stale_count += 1
            print(f"Stale request: {r.id} ({r.status}) - cur: {d.get('current_approver_name')} ({d.get('current_approver_id')})")
            for s in d.get('stages', []):
                print(f"   s{s.get('order')}: {s.get('name')} | role: {s.get('role')} | appr: {s.get('approver_name')} ({s.get('approver_id')})")
print(f"Total stale requests found: {stale_count}")
db.close()

