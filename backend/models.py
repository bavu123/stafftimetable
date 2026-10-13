from sqlalchemy import Column, Integer, String, ForeignKey, Text, Float
from sqlalchemy.orm import relationship
from backend.database import Base

class Faculty(Base):
    __tablename__ = "faculty"

    faculty_id = Column(Integer, primary_key=True, index=True)
    faculty_name = Column(String, nullable=False)
    department = Column(String, default="General")
    designation = Column(String, default="Assistant Professor")
    max_hours_per_week = Column(Integer, default=18)
    
    # Strict Faculty Profile Fields (JSON serialized or formatted text)
    preferred_domains = Column(Text, default="[]")          # Domains preferred by faculty
    verified_domains = Column(Text, default="[]")           # Verified Expertise / Official Domains
    skills_expertise = Column(Text, default="[]")           # Technical skills & methodologies
    qualified_subjects = Column(Text, default="[]")         # Subjects officially qualified to teach
    previously_taught_subjects = Column(Text, default="[]") # Historical teaching record
    teaching_experience_years = Column(Integer, default=0)  # Experience in years
    qualifications_certifications = Column(Text, default="")# Degrees and certifications
    preferred_subjects = Column(Text, default="[]")         # Subjects faculty wants to teach
    available_time_slots = Column(Text, default="[]")       # Availability constraints
    ai_suggested_domains = Column(Text, default="[]")       # AI predictions - NEVER verified automatically
    current_workload = Column(Integer, default=0)           # Current assigned weekly hours


class Subject(Base):
    __tablename__ = "subject"

    subject_id = Column(Integer, primary_key=True, index=True)
    subject_name = Column(String, nullable=False)
    subject_code = Column(String, nullable=False)
    hours_per_week = Column(Integer, default=4)
    semester = Column(String, default="Semester 5")
    
    # Required Subject Profile Fields
    required_domain = Column(String, default="")            # Primary academic domain needed
    required_skills = Column(Text, default="[]")            # Requisite skills list
    required_qualification = Column(String, default="")     # Minimum required qualification
    department = Column(String, default="Artificial Intelligence and Data Science")
    class_section = Column(String, default="III-A")         # Section / class target


class Assignment(Base):
    __tablename__ = "assignment"

    assignment_id = Column(Integer, primary_key=True, index=True)
    faculty_id = Column(Integer, ForeignKey("faculty.faculty_id"), nullable=True)
    subject_id = Column(Integer, ForeignKey("subject.subject_id"), nullable=False)
    day = Column(String, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)
    semester = Column(String, default="Semester 5")
    class_section = Column(String, default="III-A")
    
    # Unassigned and Exception Tracking
    is_unassigned = Column(Integer, default=0)              # 1 if no eligible faculty found
    unassigned_reason = Column(Text, default="")            # Reason why unassigned
    is_hod_exception = Column(Integer, default=0)           # 1 if assigned via HOD manual override
    hod_exception_id = Column(Integer, nullable=True)       # Link to HODExceptionApproval record


class HODExceptionApproval(Base):
    __tablename__ = "hod_exception_approval"

    id = Column(Integer, primary_key=True, index=True)
    faculty_id = Column(Integer, ForeignKey("faculty.faculty_id"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subject.subject_id"), nullable=False)
    approval_status = Column(String, default="APPROVED")     # APPROVED, PENDING, REJECTED
    approved_by = Column(String, nullable=False)             # Name/Title of HOD
    approval_date = Column(String, nullable=False)           # Timestamp of approval
    reason_for_exception = Column(Text, nullable=False)      # Justification for override
    created_at = Column(String, nullable=False)


class AISuggestionLog(Base):
    __tablename__ = "ai_suggestion_log"

    id = Column(Integer, primary_key=True, index=True)
    faculty_id = Column(Integer, ForeignKey("faculty.faculty_id"), nullable=False)
    suggested_domain = Column(String, nullable=False)
    source = Column(String, default="AI Recommendation Engine")
    status = Column(String, default="PENDING_REVIEW")        # PENDING_REVIEW, APPROVED, REJECTED
    reviewed_by = Column(String, nullable=True)
    review_date = Column(String, nullable=True)
    review_notes = Column(Text, nullable=True)


class Workload(Base):
    __tablename__ = "workload"

    faculty_id = Column(Integer, primary_key=True)
    weekly_hours = Column(Integer, default=0)
    monthly_hours = Column(Integer, default=0)
    status = Column(String, default="Normal")