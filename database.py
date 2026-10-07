from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
from config import Config

# Engine for PostgreSQL
engine = create_engine(
    Config.SQLALCHEMY_DATABASE_URI,
    **Config.SQLALCHEMY_ENGINE_OPTIONS
)

# Thread-safe scoped session factory
SessionLocal = scoped_session(
    sessionmaker(autocommit=False, autoflush=False, bind=engine)
)

# Base class for all declarative SQLAlchemy models
Base = declarative_base()


def get_db():
    """Dependency helper for standalone scripts or generator contexts."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def close_db_session(exception=None):
    """Clean up the scoped session (ideal for Flask teardown_appcontext)."""
    SessionLocal.remove()


def init_db_defaults():
    """Seed initial default admin user and approval workflow tables/defaults if not present."""
    import json
    from modules.models import (
        User,
        Employee,
        ApprovalWorkflow,
        ApprovalWorkflowStep,
        ApprovalStepApprover,
    )

    # Ensure all tables defined on Base exist in PostgreSQL
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return

    session = SessionLocal()
    try:
        admin_user = session.query(User).filter(User.username == Config.DEFAULT_ADMIN_USERNAME).first()
        if not admin_user:
            emp_exists = None
            if Config.DEFAULT_ADMIN_EMP_ID:
                emp = session.query(Employee).filter(Employee.employee_id == Config.DEFAULT_ADMIN_EMP_ID).first()
                if emp:
                    emp_exists = emp.employee_id

            admin_user = User(
                emp_id=emp_exists,
                username=Config.DEFAULT_ADMIN_USERNAME,
                role="admin",
                is_admin=True,
                is_active=True,
            )
            admin_user.set_password(Config.DEFAULT_ADMIN_PASSWORD)
            session.add(admin_user)
            session.commit()

        # Seed initial default workflow if none exists
        if "approval_workflows" in inspector.get_table_names():
            wf_count = session.query(ApprovalWorkflow).count()
            if wf_count == 0:
                sample_staff = session.query(Employee).filter(Employee.source_view.ilike("%staff%")).first()
                sample_emp_id = sample_staff.employee_id if sample_staff else None

                default_wf = ApprovalWorkflow(
                    name="Guest House Approval",
                    code="guest_house",
                    description="Standard multi-level approval pipeline for guest house reservations.",
                    is_active=True,
                )
                session.add(default_wf)
                session.flush()

                # Step 1: Parent Final Approval
                final_step = ApprovalWorkflowStep(
                    workflow_id=default_wf.id,
                    step_order=1,
                    step_name="Final Approval (Unit Head)",
                    is_final=True,
                    parent_step_id=None,
                )
                session.add(final_step)
                session.flush()

                # Step 2: Department Head Review
                dept_step = ApprovalWorkflowStep(
                    workflow_id=default_wf.id,
                    step_order=2,
                    step_name="Department Head Review",
                    is_final=False,
                    parent_step_id=final_step.id,
                )
                session.add(dept_step)
                session.flush()

                if sample_emp_id:
                    session.add(ApprovalStepApprover(
                        step_id=final_step.id,
                        employee_id=sample_emp_id,
                        role_label="Unit Head"
                    ))

                flow_data = {
                    "workflow_id": default_wf.id,
                    "name": default_wf.name,
                    "code": default_wf.code,
                    "stages": [
                        {
                            "id": f"stage-{final_step.id}",
                            "db_id": final_step.id,
                            "name": "Final Approval (Unit Head)",
                            "is_final": True,
                            "order": 1,
                            "approvers": [sample_staff.to_dict()] if sample_staff else []
                        },
                        {
                            "id": f"stage-{dept_step.id}",
                            "db_id": dept_step.id,
                            "name": "Department Head Review",
                            "is_final": False,
                            "order": 2,
                            "parent_id": f"stage-{final_step.id}",
                            "approvers": []
                        }
                    ]
                }
                default_wf.flow_data = json.dumps(flow_data)
                session.commit()

            # Ensure Vehicle Booking workflow exists
            veh_wf = session.query(ApprovalWorkflow).filter(
                (ApprovalWorkflow.code == "vehicle_booking") | (ApprovalWorkflow.name.ilike("%vehicle%"))
            ).first()
            if not veh_wf:
                veh_wf = ApprovalWorkflow(
                    name="Vehicle Booking",
                    code="vehicle_booking",
                    description="Official vehicle requisition and driver allocation workflow.",
                    is_active=True,
                )
                veh_wf.form_schema = json.dumps({
                    "title": "Vehicle & Transport Application Form",
                    "description": "Please specify journey details, passenger count, and reporting requirements.",
                    "enabled": True,
                    "fields": [
                        {"name": "passenger_name", "label": "Passenger / Guest Name", "type": "text", "required": True, "width": "half", "placeholder": "Full name of traveler"},
                        {"name": "contact_no", "label": "Contact / Mobile Number", "type": "tel", "required": True, "width": "half", "placeholder": "10-digit mobile number"},
                        {"name": "travel_date", "label": "Date of Travel", "type": "date", "required": True, "width": "half"},
                        {"name": "reporting_time", "label": "Reporting / Pickup Time", "type": "time", "required": True, "width": "half"},
                        {"name": "pickup_location", "label": "Pickup Location / Reporting Point", "type": "text", "required": True, "width": "half", "placeholder": "e.g. JSW Dharamtar Guest House"},
                        {"name": "destination", "label": "Destination / Drop Location", "type": "text", "required": True, "width": "half", "placeholder": "e.g. Mumbai Airport / Alibaug"},
                        {"name": "num_passengers", "label": "Number of Passengers", "type": "number", "required": True, "width": "half", "placeholder": "e.g. 2", "min": 1, "max": 10},
                        {"name": "vehicle_preference", "label": "Vehicle Preference", "type": "select", "required": False, "width": "half", "options": ["Sedan (Dzire / Etios)", "SUV (Innova / Ertiga)", "Utility (Bolero / Pickup)", "Any Available"]},
                        {"name": "purpose", "label": "Official Business Justification", "type": "textarea", "required": True, "width": "full", "placeholder": "Official visit justification..."}
                    ]
                })
                session.add(veh_wf)
                session.flush()

                v_step1 = ApprovalWorkflowStep(
                    workflow_id=veh_wf.id,
                    step_order=1,
                    step_name="Final Approval (Unit Head)",
                    is_final=True,
                    parent_step_id=None
                )
                session.add(v_step1)
                session.flush()

                v_step2 = ApprovalWorkflowStep(
                    workflow_id=veh_wf.id,
                    step_order=2,
                    step_name="Department Head Review",
                    is_final=False,
                    parent_step_id=v_step1.id
                )
                session.add(v_step2)
                session.flush()

                v_flow_data = {
                    "workflow_id": veh_wf.id,
                    "name": veh_wf.name,
                    "code": veh_wf.code,
                    "stages": [
                        {"id": f"stage-{v_step1.id}", "db_id": v_step1.id, "name": "Final Approval (Unit Head)", "is_final": True, "order": 1, "approvers": []},
                        {"id": f"stage-{v_step2.id}", "db_id": v_step2.id, "name": "Department Head Review", "is_final": False, "order": 2, "parent_id": f"stage-{v_step1.id}", "approvers": []}
                    ]
                }
                veh_wf.flow_data = json.dumps(v_flow_data)
                session.commit()

    except Exception:
        session.rollback()
    finally:
        session.close()

