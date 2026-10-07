import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
from database import SessionLocal
from modules.models import Employee
from app import build_request_approval_pipeline

s = SessionLocal()
depts = sorted(list({e.department for e in s.query(Employee).all() if e.department}))
for d in depts:
    res = build_request_approval_pipeline(s, None, department=d, workflow_id=4)
    stg1 = res["stages"][0]
    print(f"Workflow 4 | Dept: {d:25} -> Stage 1: {stg1['approver_name']} ({stg1['approver_id']}) [Stage name: {stg1['name']}]")
s.close()
