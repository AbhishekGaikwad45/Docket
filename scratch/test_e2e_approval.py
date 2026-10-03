import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import app
from database import SessionLocal
from modules.models import ApprovalRequest, GuestHouseRequest, Employee

def run_e2e_tests():
    client = app.test_client()

    print("=== TEST 1: EMPLOYEE SUBMISSION & 3-STAGE APPROVAL PIPELINE ===")
    with client.session_transaction() as sess:
        sess["emp_id"] = "4070124"  # Vinayak Patil, Civil engineer
        sess["emp_name"] = "Vinayak Patil"
        sess["role"] = "employee"
        sess["is_admin"] = False
        sess["logged_in"] = True

    submit_res = client.post("/requests/submit", json={
        "title": "Site Inspection Equipment Requisition",
        "request_type": "Civil Work Request",
        "start_date": "2026-10-10",
        "end_date": "2026-10-15",
        "purpose": "Site leveling inspection and material verification",
        "workflow_id": 4
    })
    assert submit_res.status_code == 200, f"Submit failed: {submit_res.get_json()}"
    data = submit_res.get_json()
    req_id = data["request_id"]
    req = data["request"]
    print(f"Created Request ID: {req_id}")
    print(f"Initial Stage: {req['current_stage']}, Step: {req['current_step_order']}/{req['total_steps']}, Status: {req['status']}")
    assert req["total_steps"] == 3
    assert req["current_step_order"] == 1
    assert "CIVIL" in req["current_stage"]
    assert req["status"] == "pending"

    # Step 1 Approval by Department Head (CIVIL HOD)
    print("\n--- HOD approves Step 1 ---")
    with client.session_transaction() as sess:
        sess["emp_id"] = "4050053"  # Surendra Thakur, Civil HOD
        sess["emp_name"] = "Surendra Thakur"
        sess["role"] = "dept_head"

    decide_res1 = client.post(f"/approvals/decide/{req_id}", data={
        "decision": "approve",
        "remark": "Verified by Civil Department Head"
    }, follow_redirects=True)
    assert decide_res1.status_code == 200

    db = SessionLocal()
    req_obj = db.query(ApprovalRequest).filter(ApprovalRequest.id == req_id).first()
    print(f"After HOD Approval: Stage: {req_obj.current_stage}, Step: {req_obj.current_step_order}/{req_obj.total_steps}, Status: {req_obj.status}")
    assert req_obj.current_step_order == 2
    assert "HR" in req_obj.current_stage
    assert req_obj.status == "pending"

    # Step 2 Approval by HR Head
    print("\n--- HR Head approves Step 2 ---")
    with client.session_transaction() as sess:
        sess["emp_id"] = "4050702"  # Parimita Behera, HR Head
        sess["emp_name"] = "Parimita Behera"
        sess["role"] = "hr_head"

    decide_res2 = client.post(f"/approvals/decide/{req_id}", data={
        "decision": "approve",
        "remark": "Endorsed by HR Head"
    }, follow_redirects=True)
    assert decide_res2.status_code == 200

    req_obj = db.query(ApprovalRequest).filter(ApprovalRequest.id == req_id).first()
    print(f"After HR Head Approval: Stage: {req_obj.current_stage}, Step: {req_obj.current_step_order}/{req_obj.total_steps}, Status: {req_obj.status}")
    assert req_obj.current_step_order == 3
    assert "Unit Head" in req_obj.current_stage
    assert req_obj.status == "pending"

    # Step 3 Final Approval by Unit Head
    print("\n--- Unit Head grants Final Approval ---")
    with client.session_transaction() as sess:
        sess["emp_id"] = "4050163"  # Sameer Gayakwad, Unit Head
        sess["emp_name"] = "Sameer Gayakwad"
        sess["role"] = "unit_head"

    decide_res3 = client.post(f"/approvals/decide/{req_id}", data={
        "decision": "approve",
        "remark": "Final authorization granted by Unit Head"
    }, follow_redirects=True)
    assert decide_res3.status_code == 200

    db.expire_all()
    req_obj = db.query(ApprovalRequest).filter(ApprovalRequest.id == req_id).first()
    print(f"After Unit Head Approval: Stage: {req_obj.current_stage}, Status: {req_obj.status}")
    assert req_obj.status == "approved"

    print("\n=== TEST 2: HOD SUBMISSION & 2-STAGE APPROVAL PIPELINE ===")
    with client.session_transaction() as sess:
        sess["emp_id"] = "4050053"  # Surendra Thakur, Civil HOD
        sess["emp_name"] = "Surendra Thakur"
        sess["role"] = "dept_head"
        sess["is_admin"] = False
        sess["logged_in"] = True

    submit_hod = client.post("/requests/submit", json={
        "title": "Quarry Site Vendor Pass Authorization",
        "request_type": "Vendor Access Request",
        "start_date": "2026-10-12",
        "end_date": "2026-10-18",
        "purpose": "HOD direct requisition for jetty civil works",
        "workflow_id": 4
    })
    assert submit_hod.status_code == 200
    hod_req_data = submit_hod.get_json()["request"]
    hod_req_id = submit_hod.get_json()["request_id"]
    print(f"Created HOD Request ID: {hod_req_id}")
    print(f"Initial Stage: {hod_req_data['current_stage']}, Step: {hod_req_data['current_step_order']}/{hod_req_data['total_steps']}, Status: {hod_req_data['status']}")
    assert hod_req_data["total_steps"] == 2, "HOD request must have exactly 2 steps"
    assert hod_req_data["current_step_order"] == 1
    assert "HR" in hod_req_data["current_stage"], "HOD request must start at HR Head Review"

    # Step 1 Approval for HOD Request by HR Head
    print("\n--- HR Head approves Step 1 of HOD Request ---")
    with client.session_transaction() as sess:
        sess["emp_id"] = "4050702"
        sess["emp_name"] = "Parimita Behera"
        sess["role"] = "hr_head"

    client.post(f"/approvals/decide/{hod_req_id}", data={
        "decision": "approve",
        "remark": "HR review approved"
    }, follow_redirects=True)

    hod_obj = db.query(ApprovalRequest).filter(ApprovalRequest.id == hod_req_id).first()
    print(f"After HR Head Approval: Stage: {hod_obj.current_stage}, Step: {hod_obj.current_step_order}/{hod_obj.total_steps}, Status: {hod_obj.status}")
    assert hod_obj.current_step_order == 2
    assert "Unit Head" in hod_obj.current_stage

    # Step 2 Approval for HOD Request by Unit Head
    print("\n--- Unit Head grants Final Approval for HOD Request ---")
    with client.session_transaction() as sess:
        sess["emp_id"] = "4050163"
        sess["emp_name"] = "Sameer Gayakwad"
        sess["role"] = "unit_head"

    client.post(f"/approvals/decide/{hod_req_id}", data={
        "decision": "approve",
        "remark": "Unit Head final approval"
    }, follow_redirects=True)

    db.expire_all()
    hod_obj = db.query(ApprovalRequest).filter(ApprovalRequest.id == hod_req_id).first()
    print(f"Final HOD Request Status: {hod_obj.status}")
    assert hod_obj.status == "approved"

    print("\n=== TEST 3: OTP RESILIENCE & LOGIN FALLBACK ===")
    otp_res = client.post("/send-otp", data={"emp_id": "4070002"})
    assert otp_res.status_code == 302
    assert "/verify" in otp_res.headers["Location"], f"Expected redirect to /verify, got: {otp_res.headers['Location']}"
    with client.session_transaction() as sess:
        otp = sess.get("pending_otp")
        print(f"Generated and saved OTP: {otp}")
        assert otp is not None and len(otp) == 6

    # Test verify with that OTP
    verify_res = client.post("/verify", data={"otp": otp})
    assert verify_res.status_code == 302
    assert "/dashboard" in verify_res.headers["Location"]
    print("Employee verified OTP and redirected to /dashboard successfully!")

    db.close()
    print("\n==========================================")
    print("ALL END-TO-END AUTOMATED TESTS PASSED 100%!")
    print("==========================================")

if __name__ == "__main__":
    run_e2e_tests()
