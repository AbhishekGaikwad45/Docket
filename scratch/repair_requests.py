import sys
sys.path.insert(0, 'D:/Docket')
from database import SessionLocal
from modules.models import ApprovalRequest, Employee
from app import get_unit_head_from_approval_management, get_hr_head_from_approval_management
import json

db = SessionLocal()
reqs = db.query(ApprovalRequest).all()
updated_count = 0

for r in reqs:
    if not r.details:
        continue
    try:
        det = json.loads(r.details) if isinstance(r.details, str) else r.details
    except Exception:
        continue

    stages = det.get("stages", [])
    changed = False

    pipe_hr = get_hr_head_from_approval_management(db, r.workflow_id or r.request_type)
    pipe_uh = get_unit_head_from_approval_management(db, r.workflow_id or r.request_type)

    for stg in stages:
        role = stg.get("role")
        sname = (stg.get("name") or "").upper()
        if role == "hr_head" or (("HR" in sname or "HUMAN RESOURCE" in sname) and not stg.get("is_final") and not stg.get("is_hod_stage")):
            if pipe_hr and (stg.get("approver_id") != pipe_hr.employee_id or stg.get("approver_name") != pipe_hr.employee_name):
                print(f"[{r.id}] Updating HR Head in stage {stg.get('order')} from {stg.get('approver_name')} ({stg.get('approver_id')}) to {pipe_hr.employee_name} ({pipe_hr.employee_id})")
                stg["approver_id"] = pipe_hr.employee_id
                stg["approver_name"] = pipe_hr.employee_name
                changed = True

        if role == "unit_head" or "UNIT HEAD" in sname or stg.get("is_final"):
            if pipe_uh and (stg.get("approver_id") != pipe_uh.employee_id or stg.get("approver_name") != pipe_uh.employee_name):
                print(f"[{r.id}] Updating Unit Head in stage {stg.get('order')} from {stg.get('approver_name')} ({stg.get('approver_id')}) to {pipe_uh.employee_name} ({pipe_uh.employee_id})")
                stg["approver_id"] = pipe_uh.employee_id
                stg["approver_name"] = pipe_uh.employee_name
                changed = True

    # Also check current_approver
    cur_role = det.get("current_stage_role")
    if cur_role == "hr_head" and pipe_hr and det.get("current_approver_id") != pipe_hr.employee_id:
        det["current_approver_id"] = pipe_hr.employee_id
        det["current_approver_name"] = pipe_hr.employee_name
        changed = True
    elif cur_role == "unit_head" and pipe_uh and det.get("current_approver_id") != pipe_uh.employee_id:
        det["current_approver_id"] = pipe_uh.employee_id
        det["current_approver_name"] = pipe_uh.employee_name
        changed = True

    if changed:
        det["stages"] = stages
        r.details = json.dumps(det)
        updated_count += 1

db.commit()
print(f"Successfully repaired {updated_count} requests in the database.")
db.close()

