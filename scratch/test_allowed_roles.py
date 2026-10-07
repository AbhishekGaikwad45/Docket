import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import SessionLocal
from modules.models import Employee, ApprovalWorkflow, User
from app import get_department_hod, get_hr_head_from_approval_management, get_unit_head_from_approval_management
import json

def get_allowed_roles_for_employee(db, emp_id):
    if not emp_id:
        return ["employee"]

    emp_id_str = str(emp_id).strip()

    # 1. Collect Unit Head IDs preset in Approval Management
    unit_head_ids = set()
    uh_default = get_unit_head_from_approval_management(db)
    if uh_default:
        unit_head_ids.add(str(uh_default.employee_id).strip())
    unit_head_ids.update(["4050163", "4050300"])

    # 2. Collect HR Head IDs preset in Approval Management
    hr_head_ids = set()
    hr_default = get_hr_head_from_approval_management(db)
    if hr_default:
        hr_head_ids.add(str(hr_default.employee_id).strip())
    hr_head_ids.add("4050702")

    # 3. Collect HOD IDs from workflows and department hierarchy
    hod_ids = set()
    try:
        departments = [d[0] for d in db.query(Employee.department).distinct().all() if d[0]]
        for dept in departments:
            hod = get_department_hod(db, dept)
            if hod and hod.employee_id:
                hid = str(hod.employee_id).strip()
                if hid not in hr_head_ids and hid not in unit_head_ids:
                    hod_ids.add(hid)
    except Exception as e:
        print(f"Error querying department HODs: {e}")

    try:
        workflows = db.query(ApprovalWorkflow).filter(ApprovalWorkflow.is_active == True).all()
        for wf in workflows:
            if wf.flow_data:
                fd = json.loads(wf.flow_data) if isinstance(wf.flow_data, str) else wf.flow_data
                for stg in fd.get("stages", []):
                    s_name = (stg.get("name") or "").upper()
                    stg_role = (stg.get("role") or "").lower()
                    is_uh = "UNIT HEAD" in s_name or stg_role == "unit_head" or stg.get("is_final")
                    is_hr = "HR" in s_name or "HUMAN RESOURCE" in s_name or stg_role == "hr_head" or stg.get("is_hr_stage")
                    is_hod = ("HEAD" in s_name or "HOD" in s_name or stg_role == "dept_head") and not is_uh and not is_hr
                    for appr in stg.get("approvers", []):
                        aid = str(appr.get("employee_id") or "").strip()
                        if not aid:
                            continue
                        if is_uh:
                            unit_head_ids.add(aid)
                        elif is_hr:
                            hr_head_ids.add(aid)
                        elif is_hod and aid not in hr_head_ids and aid not in unit_head_ids:
                            hod_ids.add(aid)

            if wf.steps:
                for step in wf.steps:
                    s_name = (step.step_name or "").upper()
                    is_uh = "UNIT HEAD" in s_name or step.is_final
                    is_hr = "HR" in s_name or "HUMAN RESOURCE" in s_name
                    is_hod = ("HEAD" in s_name or "HOD" in s_name) and not is_uh and not is_hr
                    for appr in step.approvers:
                        aid = str(appr.employee_id or "").strip()
                        if not aid:
                            continue
                        if is_uh:
                            unit_head_ids.add(aid)
                        elif is_hr:
                            hr_head_ids.add(aid)
                        elif is_hod and aid not in hr_head_ids and aid not in unit_head_ids:
                            hod_ids.add(aid)
    except Exception as e:
        print(f"Error querying workflow approvers: {e}")

    roles = ["employee"]
    if emp_id_str in unit_head_ids:
        roles.append("unit_head")
    if emp_id_str in hr_head_ids:
        roles.append("hr_head")
    if emp_id_str in hod_ids:
        roles.append("dept_head")

    # If linked User record has an explicitly assigned role
    user_rec = db.query(User).filter(User.emp_id == emp_id_str).first()
    if user_rec and user_rec.role in ("unit_head", "hr_head", "dept_head"):
        if user_rec.role not in roles:
            roles.append(user_rec.role)

    return roles

db = SessionLocal()
try:
    test_cases = [
        ("4050300", "Vineeth Xavier (Unit Head)"),
        ("4050163", "Sameer Gayakwad (Unit Head)"),
        ("4050702", "Parimita Behera (HR Head)"),
        ("4050053", "Surendra Thakur (Civil HOD)"),
        ("4070019", "Nilesh Mhatre (Safety HOD)"),
        ("4070124", "Vinayak Patil (Civil Engineer / Staff)"),
        ("4061035", "HR Department Staff"),
        ("4070145", "HR Department Staff"),
        ("4070116", "Staff"),
    ]
    for emp_id, label in test_cases:
        roles = get_allowed_roles_for_employee(db, emp_id)
        has_switcher = len(roles) > 1
        print(f"ID: {emp_id} | {label} -> Roles: {roles} | Show View as: {has_switcher}")
finally:
    db.close()
