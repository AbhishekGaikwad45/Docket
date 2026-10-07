import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app, SessionLocal, Employee, ApprovalRequest, ApprovalWorkflow, get_admin_department_recipient, dispatch_final_approval_emails
import mail_service
from unittest.mock import patch

def test_admin_recipient_resolution():
    print("--- TEST 1: Resolving Admin Department Recipient ---")
    db = SessionLocal()
    try:
        recipient = get_admin_department_recipient(db)
        print(f"Resolved Admin Recipient: {recipient}")
        assert recipient is not None, "Failed to resolve any admin department recipient"
        email, name = recipient
        assert email and "@" in email, f"Invalid email: {email}"
        print(f"  [PASS] Admin department employee identified: {name} <{email}>")
    finally:
        db.close()

def test_final_approval_dispatch():
    print("\n--- TEST 2: Testing dispatch_final_approval_emails Execution ---")
    db = SessionLocal()
    try:
        # Create a mock or real test request
        req = db.query(ApprovalRequest).first()
        if not req:
            req = ApprovalRequest(
                id="TEST-REQ-9999",
                request_type="VIP Hall",
                title="VIP Delegation Review",
                department="CIVIL",
                applicant_emp_id="4070116",
                applicant_name="Test Submitter",
                applicant_email="applicant.test@example.com",
                start_date="2026-10-10",
                end_date="2026-10-12",
                purpose="Annual Plant Inspection",
                status="approved",
                current_stage="Final Approval (Unit Head)",
                current_step_order=3,
                total_steps=3
            )
            db.add(req)
            db.commit()

        # Track sent emails
        sent_emails = []
        def mock_send(to_email, subject, html_content, text_content=None):
            sent_emails.append({
                "to": to_email,
                "subject": subject,
                "html": html_content,
                "text": text_content
            })
            return True

        with patch("mail_service.send_email", side_effect=mock_send):
            # Dispatch synchronously for testing
            admin_rec = get_admin_department_recipient(db)
            admin_email, admin_name = admin_rec

            summary = {
                "req_id": req.id,
                "workflow_name": req.request_type,
                "title": req.title,
                "applicant_name": req.applicant_name,
                "applicant_emp_id": req.applicant_emp_id,
                "applicant_dept": req.department,
                "start_date": req.start_date,
                "end_date": req.end_date,
                "purpose": req.purpose,
                "approved_by": "Capt. B.V. Joglekar (Unit Head)",
                "approved_at": "2026-10-06 11:30",
                "remarks": "Approved with priority seating",
                "form_fields": [
                    {"label": "Room Category", "value": "Executive Suite"},
                    {"label": "Number of Guests", "value": 15}
                ]
            }

            mail_service.send_final_approval_email(
                to_email=req.applicant_email or "applicant@example.com",
                recipient_name=req.applicant_name,
                is_admin_department=False,
                req_summary=summary
            )

            mail_service.send_final_approval_email(
                to_email=admin_email,
                recipient_name=admin_name,
                is_admin_department=True,
                req_summary=summary
            )

        print(f"Total emails dispatched: {len(sent_emails)}")
        assert len(sent_emails) == 2, f"Expected 2 emails, got {len(sent_emails)}"
        
        # Verify End User email
        user_mail = sent_emails[0]
        assert user_mail["to"] == (req.applicant_email or "applicant@example.com")
        assert "Final Approval Granted" in user_mail["subject"]
        assert "Room Category" in user_mail["html"]
        assert "Executive Suite" in user_mail["html"]
        assert "Capt. B.V. Joglekar" in user_mail["html"]
        print(f"  [PASS] End User email verified to {user_mail['to']}: Subject: '{user_mail['subject']}'")

        # Verify Admin Department email
        admin_mail = sent_emails[1]
        assert admin_mail["to"] == admin_email
        assert "[Admin Action Required]" in admin_mail["subject"]
        assert "Admin Department" in admin_mail["html"]
        assert "Room Category" in admin_mail["html"]
        assert "Executive Suite" in admin_mail["html"]
        print(f"  [PASS] Admin department email verified to {admin_mail['to']}: Subject: '{admin_mail['subject']}'")

    finally:
        db.close()

def test_admin_portal_action_endpoint():
    print("\n--- TEST 3: Testing End-to-End Approval via Web Endpoints ---")
    with app.test_client() as client:
        # Sign in as admin
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user_id"] = 1
            sess["email"] = "admin@example.com"
            sess["role"] = "admin"
            sess["is_admin"] = True
            sess["emp_id"] = "ADMIN01"

        db = SessionLocal()
        try:
            # Create a test request pending final approval
            test_req = ApprovalRequest(
                id="TEST-AUTO-FINAL-01",
                request_type="Guest House Accommodation",
                title="Guest House for Auditor",
                department="CIVIL",
                applicant_emp_id="4070116",
                applicant_name="Aditi Submitter",
                applicant_email="aditi.applicant@example.com",
                start_date="2026-10-15",
                end_date="2026-10-17",
                purpose="Statutory Environmental Audit",
                status="pending",
                current_stage="Final Approval (Unit Head)",
                current_step_order=3,
                total_steps=3
            )
            db.merge(test_req)
            db.commit()

            dispatched = []
            def track_dispatch(req_summary, end_user_email=None, end_user_name=None, admin_email=None, admin_name=None):
                dispatched.append({
                    "summary": req_summary,
                    "end_user": (end_user_email, end_user_name),
                    "admin": (admin_email, admin_name)
                })

            with patch("app.notify_final_approval_async", side_effect=track_dispatch):
                res = client.post(
                    f"/admin/approvals/{test_req.id}/decide",
                    json={"decision": "approve", "remarks": "Auditor lodging approved"}
                )
                assert res.status_code == 200, f"Status: {res.status_code}, response: {res.data}"
                data = res.get_json()
                assert data["success"] is True

            print(f"Dispatched calls: {len(dispatched)}")
            assert len(dispatched) == 1, "notify_final_approval_async was not called!"
            call = dispatched[0]
            print(f"  [PASS] Dispatched with End User: {call['end_user']} and Admin: {call['admin']}")
            assert call["end_user"][0] == "aditi.applicant@example.com"
            assert call["admin"][0] is not None and "@" in call["admin"][0]

            # Cleanup
            db.query(ApprovalRequest).filter(ApprovalRequest.id == "TEST-AUTO-FINAL-01").delete()
            db.commit()
            print("  [PASS] Test request cleaned up.")

        finally:
            db.close()

def test_regular_pipeline_final_approval():
    print("\n--- TEST 4: Testing Regular Approver Portal Final Approval Action ---")
    with app.test_client() as client:
        # Sign in as Unit Head
        with client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user_id"] = 2
            sess["email"] = "vineeth.xavier@jsw.in"
            sess["role"] = "unit_head"
            sess["is_admin"] = False
            sess["emp_id"] = "4050163"
            sess["emp_name"] = "SAMEER GAYAKWAD"

        db = SessionLocal()
        try:
            test_req = ApprovalRequest(
                id="TEST-REG-FINAL-02",
                request_type="VIP Hall",
                title="VIP Hall Meeting",
                department="MECHANICAL",
                applicant_emp_id="4070116",
                applicant_name="Mechanical Staff",
                applicant_email="mech.staff@example.com",
                start_date="2026-10-20",
                end_date="2026-10-20",
                purpose="Plant Operations Review",
                status="pending",
                current_stage="Final Approval (Unit Head)",
                current_step_order=3,
                total_steps=3
            )
            db.merge(test_req)
            db.commit()

            dispatched = []
            def track_dispatch(req_summary, end_user_email=None, end_user_name=None, admin_email=None, admin_name=None):
                dispatched.append({
                    "summary": req_summary,
                    "end_user": (end_user_email, end_user_name),
                    "admin": (admin_email, admin_name)
                })

            with patch("app.notify_final_approval_async", side_effect=track_dispatch):
                res = client.post(
                    f"/approvals/decide/{test_req.id}",
                    data={"decision": "approve", "remark": "Approved by Unit Head", "stage": "Final Approval (Unit Head)"},
                    follow_redirects=True
                )
                assert res.status_code == 200

            print(f"Dispatched calls from approver action: {len(dispatched)}")
            assert len(dispatched) == 1, "notify_final_approval_async was not called!"
            call = dispatched[0]
            print(f"  [PASS] End User notified: {call['end_user']}")
            print(f"  [PASS] Admin department notified: {call['admin']}")
            assert call["end_user"][0] == "mech.staff@example.com"
            assert call["admin"][0] is not None and "@" in call["admin"][0]

            # Cleanup
            db.query(ApprovalRequest).filter(ApprovalRequest.id == "TEST-REG-FINAL-02").delete()
            db.commit()
            print("  [PASS] Cleaned up TEST-REG-FINAL-02")

        finally:
            db.close()

if __name__ == '__main__':
    test_admin_recipient_resolution()
    test_final_approval_dispatch()
    test_admin_portal_action_endpoint()
    test_regular_pipeline_final_approval()
    print("\n=======================================================")
    print("ALL FINAL APPROVAL EMAIL TESTS PASSED WITH 100% SUCCESS!")
    print("=======================================================")
