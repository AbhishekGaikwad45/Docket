"""
Central models module for DOCKET.
Contains all SQLAlchemy declarative models.
"""
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    emp_id = Column(String(50), ForeignKey("employees.employee_id", ondelete="SET NULL"), unique=True, nullable=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=True)
    role = Column(String(50), default="employee", nullable=False)  # admin, dept_head, unit_head, employee
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)

    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            "id": self.id,
            "emp_id": self.emp_id,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "is_admin": self.is_admin,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<User id={self.id} username='{self.username}' role='{self.role}'>"


class Employee(Base):
    __tablename__ = "employees"

    employee_id = Column(String(50), primary_key=True, index=True)
    employee_name = Column(String(300), nullable=False)
    designation = Column(String(150), nullable=True)
    department = Column(String(150), nullable=True, index=True)
    email_id = Column(String(150), nullable=True, index=True)
    employee_status = Column(String(50), nullable=True, index=True)
    contact_no = Column(String(50), nullable=True)
    gender = Column(String(50), nullable=True)
    category = Column(String(150), nullable=True)
    source_view = Column(String(100), nullable=True)

    last_synced_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    @property
    def source_type(self) -> str:
        if not self.source_view:
            return "manual"
        sv = self.source_view.lower()
        if "staff" in sv:
            return "staff"
        if "associate" in sv:
            return "associates"
        return "manual"

    def to_dict(self):
        return {
            "employee_id": self.employee_id,
            "employee_name": self.employee_name,
            "designation": self.designation,
            "department": self.department,
            "email_id": self.email_id,
            "employee_status": self.employee_status,
            "contact_no": self.contact_no,
            "gender": self.gender,
            "category": self.category,
            "source_view": self.source_view,
            "source_type": self.source_type,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<Employee id={self.employee_id} name='{self.employee_name}' dept='{self.department}' status='{self.employee_status}'>"


class GuestHouseRequest(Base):
    __tablename__ = "guest_house_requests"

    id = Column(String(50), primary_key=True)
    guest = Column(String(255), nullable=False)
    checkin = Column(String(50), nullable=False)
    checkout = Column(String(50), nullable=False)
    purpose = Column(Text, nullable=True)
    stage = Column(String(50), nullable=False, default="pending_dept_head")
    remark = Column(Text, nullable=True, default="")
    rejected_at = Column(String(50), nullable=True)
    created_by = Column(String(50), ForeignKey("employees.employee_id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "guest": self.guest,
            "checkin": self.checkin,
            "checkout": self.checkout,
            "purpose": self.purpose or "",
            "stage": self.stage,
            "remark": self.remark or "",
            "rejected_at": self.rejected_at,
            "created_by": self.created_by,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else None,
        }

    def __repr__(self):
        return f"<GuestHouseRequest id={self.id} guest='{self.guest}' stage='{self.stage}'>"


class ApprovalWorkflow(Base):
    __tablename__ = "approval_workflows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(150), unique=True, nullable=False, index=True)
    code = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    flow_data = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    steps = relationship(
        "ApprovalWorkflowStep",
        backref="workflow",
        cascade="all, delete-orphan",
        order_by="ApprovalWorkflowStep.step_order.asc()",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "code": self.code,
            "description": self.description or "",
            "is_active": self.is_active,
            "flow_data": self.flow_data or "",
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else None,
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else None,
        }

    def __repr__(self):
        return f"<ApprovalWorkflow id={self.id} name='{self.name}'>"


class ApprovalWorkflowStep(Base):
    __tablename__ = "approval_workflow_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(Integer, ForeignKey("approval_workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    step_order = Column(Integer, default=1, nullable=False)
    step_name = Column(String(150), nullable=False)
    is_final = Column(Boolean, default=False, nullable=False)
    parent_step_id = Column(Integer, ForeignKey("approval_workflow_steps.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    approvers = relationship(
        "ApprovalStepApprover",
        backref="step",
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "workflow_id": self.workflow_id,
            "step_order": self.step_order,
            "step_name": self.step_name,
            "is_final": self.is_final,
            "parent_step_id": self.parent_step_id,
            "approvers": [a.to_dict() for a in self.approvers],
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else None,
        }

    def __repr__(self):
        return f"<ApprovalWorkflowStep id={self.id} name='{self.step_name}' order={self.step_order}>"


class ApprovalStepApprover(Base):
    __tablename__ = "approval_step_approvers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    step_id = Column(Integer, ForeignKey("approval_workflow_steps.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = Column(String(50), ForeignKey("employees.employee_id", ondelete="CASCADE"), nullable=False, index=True)
    role_label = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    employee = relationship("Employee", lazy="joined")

    def to_dict(self):
        emp_data = self.employee.to_dict() if self.employee else {}
        return {
            "id": self.id,
            "step_id": self.step_id,
            "employee_id": self.employee_id,
            "role_label": self.role_label or "",
            "employee_name": emp_data.get("employee_name", self.employee_id),
            "designation": emp_data.get("designation", ""),
            "department": emp_data.get("department", ""),
            "email_id": emp_data.get("email_id", ""),
            "source_type": emp_data.get("source_type", "staff"),
        }

    def __repr__(self):
        return f"<ApprovalStepApprover id={self.id} step_id={self.step_id} emp_id='{self.employee_id}'>"


__all__ = [
    "Base",
    "User",
    "Employee",
    "GuestHouseRequest",
    "ApprovalWorkflow",
    "ApprovalWorkflowStep",
    "ApprovalStepApprover",
]
