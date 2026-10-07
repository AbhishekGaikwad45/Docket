import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
from app import SessionLocal, Employee, build_request_approval_pipeline
from modules.models import ApprovalRequest

def repair_pending():
    db = SessionLocal()
    try:
        for req_id in ['REQ-2026-1011', 'REQ-2026-1010']:
            req = db.query(ApprovalRequest).filter(ApprovalRequest.id == req_id).first()
            if not req:
                print(f"{req_id} not found")
                continue
            applicant = db.query(Employee).filter(Employee.employee_id == req.applicant_emp_id).first() if req.applicant_emp_id else None
            dept = req.department or (applicant.department if applicant else '')
            pipeline = build_request_approval_pipeline(db, applicant, dept, req.workflow_id)
            
            details_obj = {}
            if req.details:
                try:
                    details_obj = json.loads(req.details)
                except Exception:
                    pass
            
            details_obj['stages'] = pipeline['stages']
            details_obj['current_stage_role'] = pipeline['initial_role']
            details_obj['current_approver_name'] = pipeline['initial_approver']
            details_obj['current_approver_id'] = pipeline['initial_approver_id']
            details_obj['department'] = dept
            
            req.details = json.dumps(details_obj)
            req.current_stage = pipeline['initial_stage']
            approver_name = details_obj['current_approver_name']
            approver_id = details_obj['current_approver_id']
            print(f"Repaired {req_id}: Dept={dept}, New Stage={req.current_stage}, Approver={approver_name} ({approver_id})")
        
        db.commit()
        print("All pending requests successfully repaired in database!")
    finally:
        db.close()

if __name__ == "__main__":
    repair_pending()
