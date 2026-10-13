import os
import io
import json
from datetime import datetime
from fastapi import FastAPI, Depends, Body, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .database import SessionLocal, engine, Base, auto_migrate_database
from . import models
from . import schemas
from .workload import calculate_workload
from .eligibility_engine import (
    evaluate_faculty_eligibility,
    calculate_suitability_score,
    evaluate_all_candidates_for_subject,
    build_full_eligibility_matrix,
    parse_list
)
from generator.timetable_generator import generate_automatic_timetable
from pdf.pdf_generator import generate_pdf
from chatbot.ai_chatbot import ask_ai

# Execute database migration and create tables
auto_migrate_database()
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Staff Timetable Intelligence AI with Strict Eligibility Engine",
    version="2.1.0",
    description="Academic timetable generation system enforcing strict faculty-subject eligibility constraints and HOD exception workflows."
)

# Enable CORS for Streamlit and web frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store uploaded PDF content (in-memory)
uploaded_pdf_content = ""


# ---------------- Database Dependency ----------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------- Health & Stats ----------------
@app.get("/")
def root():
    return {
        "message": "Staff Timetable AI API with Strict Eligibility Engine is running",
        "version": "2.1.0",
        "eligibility_engine": "ACTIVE"
    }


@app.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    faculty_count = db.query(models.Faculty).count()
    subject_count = db.query(models.Subject).count()
    assignment_count = db.query(models.Assignment).filter(models.Assignment.is_unassigned == 0).count()
    unassigned_count = db.query(models.Assignment).filter(models.Assignment.is_unassigned == 1).count()
    hod_exceptions_count = db.query(models.HODExceptionApproval).count()

    all_faculties = db.query(models.Faculty).all()
    overloaded_count = 0
    for f in all_faculties:
        wl = calculate_workload(db, f.faculty_id)
        if wl.status == "Overloaded":
            overloaded_count += 1

    return {
        "faculty_count": faculty_count,
        "subject_count": subject_count,
        "assignment_count": assignment_count,
        "unassigned_count": unassigned_count,
        "overloaded_count": overloaded_count,
        "hod_exceptions_count": hod_exceptions_count
    }


# ---------------- Faculty Management ----------------
@app.get("/faculty")
@app.get("/faculty/list")
def list_faculty(db: Session = Depends(get_db)):
    faculties = db.query(models.Faculty).all()
    results = []
    for f in faculties:
        results.append({
            "faculty_id": f.faculty_id,
            "faculty_name": f.faculty_name,
            "department": f.department,
            "designation": f.designation,
            "max_hours_per_week": f.max_hours_per_week,
            "preferred_domains": parse_list(f.preferred_domains),
            "verified_domains": parse_list(f.verified_domains),
            "skills_expertise": parse_list(f.skills_expertise),
            "qualified_subjects": parse_list(f.qualified_subjects),
            "previously_taught_subjects": parse_list(f.previously_taught_subjects),
            "teaching_experience_years": f.teaching_experience_years or 0,
            "qualifications_certifications": f.qualifications_certifications or "",
            "preferred_subjects": parse_list(f.preferred_subjects),
            "available_time_slots": parse_list(f.available_time_slots),
            "ai_suggested_domains": parse_list(f.ai_suggested_domains),
            "current_workload": f.current_workload or 0
        })
    return results


@app.post("/faculty")
@app.post("/faculty/add")
def add_faculty(data: dict = Body(...), db: Session = Depends(get_db)):
    faculty_name = data.get("faculty_name") or data.get("name")
    if not faculty_name:
        raise HTTPException(status_code=400, detail="Faculty name is required")
        
    department = data.get("department", "General")
    designation = data.get("designation", "Assistant Professor")
    max_hours = data.get("max_hours_per_week") or data.get("max_hours") or 18

    # Extract separate domains and skills
    preferred_domains = json.dumps(parse_list(data.get("preferred_domains", [])))
    verified_domains = json.dumps(parse_list(data.get("verified_domains", [])))
    skills_expertise = json.dumps(parse_list(data.get("skills_expertise", [])))
    qualified_subjects = json.dumps(parse_list(data.get("qualified_subjects", [])))
    previously_taught = json.dumps(parse_list(data.get("previously_taught_subjects", [])))
    preferred_subjects = json.dumps(parse_list(data.get("preferred_subjects", [])))
    available_slots = json.dumps(parse_list(data.get("available_time_slots", [])))
    ai_suggested = json.dumps(parse_list(data.get("ai_suggested_domains", [])))
    exp_years = int(data.get("teaching_experience_years") or 0)
    certs = str(data.get("qualifications_certifications") or "").strip()

    new_faculty = models.Faculty(
        faculty_name=str(faculty_name).strip(),
        department=str(department).strip(),
        designation=str(designation).strip(),
        max_hours_per_week=int(max_hours),
        preferred_domains=preferred_domains,
        verified_domains=verified_domains,
        skills_expertise=skills_expertise,
        qualified_subjects=qualified_subjects,
        previously_taught_subjects=previously_taught,
        teaching_experience_years=exp_years,
        qualifications_certifications=certs,
        preferred_subjects=preferred_subjects,
        available_time_slots=available_slots,
        ai_suggested_domains=ai_suggested,
        current_workload=0
    )
    db.add(new_faculty)
    db.commit()
    db.refresh(new_faculty)
    return {"message": "Faculty added successfully", "faculty_id": new_faculty.faculty_id}


@app.delete("/faculty/{faculty_id}")
def delete_faculty(faculty_id: int, db: Session = Depends(get_db)):
    fac = db.query(models.Faculty).filter(models.Faculty.faculty_id == faculty_id).first()
    if not fac:
        raise HTTPException(status_code=404, detail="Faculty not found")
    
    db.query(models.Assignment).filter(models.Assignment.faculty_id == faculty_id).delete()
    db.query(models.Workload).filter(models.Workload.faculty_id == faculty_id).delete()
    db.query(models.HODExceptionApproval).filter(models.HODExceptionApproval.faculty_id == faculty_id).delete()
    db.delete(fac)
    db.commit()
    return {"message": f"Faculty {faculty_id} deleted successfully"}


# ---------------- Subject Management ----------------
@app.get("/subject")
@app.get("/subject/list")
def list_subjects(db: Session = Depends(get_db)):
    subjects = db.query(models.Subject).all()
    results = []
    for s in subjects:
        results.append({
            "subject_id": s.subject_id,
            "subject_name": s.subject_name,
            "subject_code": s.subject_code,
            "hours_per_week": s.hours_per_week,
            "semester": s.semester,
            "required_domain": s.required_domain or "",
            "required_skills": parse_list(s.required_skills),
            "required_qualification": s.required_qualification or "",
            "department": s.department or "Artificial Intelligence and Data Science",
            "class_section": s.class_section or "III-A"
        })
    return results


@app.post("/subject")
@app.post("/subject/add")
def add_subject(data: dict = Body(...), db: Session = Depends(get_db)):
    subject_name = data.get("subject_name") or data.get("name")
    if not subject_name:
        raise HTTPException(status_code=400, detail="Subject name is required")

    subject_code = data.get("subject_code") or data.get("code") or "SUB101"
    hours_per_week = data.get("hours_per_week") or data.get("hours") or 4
    semester = data.get("semester", "Semester 5")
    required_domain = data.get("required_domain", "")
    required_skills = json.dumps(parse_list(data.get("required_skills", [])))
    required_qualification = data.get("required_qualification", "")
    department = data.get("department", "Artificial Intelligence and Data Science")
    class_section = data.get("class_section", "III-A")

    new_subject = models.Subject(
        subject_name=str(subject_name).strip(),
        subject_code=str(subject_code).strip(),
        hours_per_week=int(hours_per_week),
        semester=str(semester).strip(),
        required_domain=str(required_domain).strip(),
        required_skills=required_skills,
        required_qualification=str(required_qualification).strip(),
        department=str(department).strip(),
        class_section=str(class_section).strip()
    )
    db.add(new_subject)
    db.commit()
    db.refresh(new_subject)
    return {"message": "Subject added successfully", "subject_id": new_subject.subject_id}


@app.delete("/subject/{subject_id}")
def delete_subject(subject_id: int, db: Session = Depends(get_db)):
    sub = db.query(models.Subject).filter(models.Subject.subject_id == subject_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subject not found")
    
    db.query(models.Assignment).filter(models.Assignment.subject_id == subject_id).delete()
    db.query(models.HODExceptionApproval).filter(models.HODExceptionApproval.subject_id == subject_id).delete()
    db.delete(sub)
    db.commit()
    return {"message": f"Subject {subject_id} deleted successfully"}


# ---------------- Timetable & Assignment Management ----------------
@app.get("/timetable")
def get_timetable(db: Session = Depends(get_db)):
    assignments = db.query(models.Assignment).all()
    results = []
    for a in assignments:
        faculty = db.query(models.Faculty).filter(models.Faculty.faculty_id == a.faculty_id).first() if a.faculty_id else None
        subject = db.query(models.Subject).filter(models.Subject.subject_id == a.subject_id).first()
        results.append({
            "assignment_id": a.assignment_id,
            "faculty_id": a.faculty_id,
            "faculty_name": faculty.faculty_name if faculty else "Unassigned / Faculty Required",
            "department": faculty.department if faculty else (subject.department if subject else "N/A"),
            "subject_id": a.subject_id,
            "subject_name": subject.subject_name if subject else f"Subject #{a.subject_id}",
            "subject_code": subject.subject_code if subject else "N/A",
            "required_domain": subject.required_domain if subject else "N/A",
            "day": a.day,
            "start_time": a.start_time,
            "end_time": a.end_time,
            "time_slot": f"{a.start_time} - {a.end_time}",
            "semester": a.semester or "N/A",
            "class_section": a.class_section or "III-A",
            "is_unassigned": bool(a.is_unassigned),
            "unassigned_reason": a.unassigned_reason or "",
            "is_hod_exception": bool(a.is_hod_exception)
        })
    return results


@app.post("/assignment")
@app.post("/assignment/add")
def add_assignment(data: dict = Body(...), db: Session = Depends(get_db)):
    faculty_id = int(data.get("faculty_id", 0))
    subject_id = int(data.get("subject_id", 0))
    day = data.get("day", "Monday")
    allow_override = data.get("allow_hod_override", False)
    hod_reason = data.get("hod_reason", "")
    approved_by = data.get("approved_by", "Dr. HOD")
    
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    time_slot = data.get("time_slot")
    
    if time_slot and ("-" in time_slot):
        parts = time_slot.split("-")
        start_time = parts[0].strip()
        end_time = parts[1].strip()
    elif not start_time or not end_time:
        start_time = start_time or "09:00"
        end_time = end_time or "09:50"

    semester = data.get("semester") or data.get("section") or "Semester 5"
    class_section = data.get("class_section") or "III-A"

    # STRICT ELIGIBILITY CHECK
    fac = db.query(models.Faculty).filter(models.Faculty.faculty_id == faculty_id).first()
    sub = db.query(models.Subject).filter(models.Subject.subject_id == subject_id).first()
    
    if not fac or not sub:
        raise HTTPException(status_code=404, detail="Faculty or Subject not found")

    elig = evaluate_faculty_eligibility(fac, sub, db=db)
    is_hod_exception = 0
    hod_exception_id = None

    if not elig["is_eligible"]:
        if allow_override:
            # Grant HOD manual exception approval
            approval = models.HODExceptionApproval(
                faculty_id=faculty_id,
                subject_id=subject_id,
                approval_status="APPROVED",
                approved_by=approved_by,
                approval_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                reason_for_exception=hod_reason or "Manual HOD special exception approved.",
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            db.add(approval)
            db.commit()
            db.refresh(approval)
            is_hod_exception = 1
            hod_exception_id = approval.id
        else:
            # STRICT REJECTION
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "INELIGIBLE_FACULTY_ASSIGNMENT",
                    "faculty_name": fac.faculty_name,
                    "subject_name": sub.subject_name,
                    "required_domain": sub.required_domain,
                    "rejection_reasons": elig["rejection_reasons"],
                    "message": f"CRITICAL: {fac.faculty_name} is NOT eligible for {sub.subject_name}. Must be approved via HOD Exception."
                }
            )
    else:
        is_hod_exception = 1 if elig["is_hod_exception"] else 0

    # Conflict check
    conflict = db.query(models.Assignment).filter(
        models.Assignment.faculty_id == faculty_id,
        models.Assignment.day == day,
        models.Assignment.start_time == start_time
    ).first()

    conflict_warning = None
    if conflict:
        conflict_warning = f"Notice: Faculty already has an assignment on {day} at {start_time}."

    new_assign = models.Assignment(
        faculty_id=faculty_id,
        subject_id=subject_id,
        day=day,
        start_time=start_time,
        end_time=end_time,
        semester=semester,
        class_section=class_section,
        is_unassigned=0,
        is_hod_exception=is_hod_exception,
        hod_exception_id=hod_exception_id
    )
    db.add(new_assign)
    db.commit()
    db.refresh(new_assign)

    workload = calculate_workload(db, faculty_id)

    return {
        "message": "Assignment added successfully",
        "assignment_id": new_assign.assignment_id,
        "is_hod_exception": bool(is_hod_exception),
        "workload_status": workload.status,
        "conflict_warning": conflict_warning
    }


@app.delete("/assignment/{assignment_id}")
def delete_assignment(assignment_id: int, db: Session = Depends(get_db)):
    assign = db.query(models.Assignment).filter(models.Assignment.assignment_id == assignment_id).first()
    if not assign:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    fac_id = assign.faculty_id
    db.delete(assign)
    db.commit()
    if fac_id:
        calculate_workload(db, fac_id)
    return {"message": f"Assignment {assignment_id} deleted successfully"}


# ---------------- Automatic Timetable Generation ----------------
@app.post("/timetable/generate")
def run_automatic_timetable_generation(data: dict = Body(default={}), db: Session = Depends(get_db)):
    """Runs the Automatic Timetable Generator with the strict Eligibility Engine."""
    class_section = data.get("class_section", "III-A")
    semester = data.get("semester", "Semester 5")
    persist = data.get("persist_to_db", True)
    
    result = generate_automatic_timetable(
        db=db,
        class_section=class_section,
        semester=semester,
        persist_to_db=persist
    )
    return result


@app.get("/timetable/eligibility-matrix")
def get_eligibility_matrix(db: Session = Depends(get_db)):
    """Inspects all Faculty-Subject pairs and returns eligibility breakdown and suitability scores."""
    faculties = db.query(models.Faculty).all()
    subjects = db.query(models.Subject).all()
    matrix = build_full_eligibility_matrix(faculties, subjects, db=db)
    return matrix


@app.get("/timetable/unassigned")
def get_unassigned_subjects(db: Session = Depends(get_db)):
    """Fetches all unassigned subjects requiring HOD intervention."""
    unassigned_records = db.query(models.Assignment).filter(
        models.Assignment.is_unassigned == 1
    ).all()
    
    res = []
    for u in unassigned_records:
        sub = db.query(models.Subject).filter(models.Subject.subject_id == u.subject_id).first()
        res.append({
            "assignment_id": u.assignment_id,
            "subject_id": u.subject_id,
            "subject_name": sub.subject_name if sub else f"Subject #{u.subject_id}",
            "subject_code": sub.subject_code if sub else "",
            "required_domain": sub.required_domain if sub else "",
            "class_section": u.class_section,
            "unassigned_reason": u.unassigned_reason or "No eligible faculty available.",
            "status": "Unassigned / Faculty Required"
        })
    return res


# ---------------- HOD Exception & AI Domain Approvals ----------------
@app.post("/hod/exception-approval")
def create_hod_exception_approval(data: dict = Body(...), db: Session = Depends(get_db)):
    """HOD manually approves an exceptional assignment for an otherwise ineligible faculty."""
    faculty_id = int(data.get("faculty_id", 0))
    subject_id = int(data.get("subject_id", 0))
    approved_by = data.get("approved_by", "Dr. HOD")
    reason = data.get("reason_for_exception", "")
    
    if not faculty_id or not subject_id or not reason.strip():
        raise HTTPException(status_code=400, detail="faculty_id, subject_id, and reason_for_exception are required")

    approval = models.HODExceptionApproval(
        faculty_id=faculty_id,
        subject_id=subject_id,
        approval_status="APPROVED",
        approved_by=approved_by.strip(),
        approval_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        reason_for_exception=reason.strip(),
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return {
        "message": "HOD exception approved successfully",
        "approval_id": approval.id,
        "faculty_id": faculty_id,
        "subject_id": subject_id,
        "approved_by": approval.approved_by,
        "approval_date": approval.approval_date
    }


@app.get("/hod/exception-approvals")
def list_hod_exceptions(db: Session = Depends(get_db)):
    """Lists all approved HOD exceptions with audit trails."""
    approvals = db.query(models.HODExceptionApproval).all()
    results = []
    for a in approvals:
        fac = db.query(models.Faculty).filter(models.Faculty.faculty_id == a.faculty_id).first()
        sub = db.query(models.Subject).filter(models.Subject.subject_id == a.subject_id).first()
        results.append({
            "id": a.id,
            "faculty_id": a.faculty_id,
            "faculty_name": fac.faculty_name if fac else f"Faculty #{a.faculty_id}",
            "subject_id": a.subject_id,
            "subject_name": sub.subject_name if sub else f"Subject #{a.subject_id}",
            "approval_status": a.approval_status,
            "approved_by": a.approved_by,
            "approval_date": a.approval_date,
            "reason_for_exception": a.reason_for_exception
        })
    return results


@app.post("/hod/review-ai-domain")
def review_ai_domain(data: dict = Body(...), db: Session = Depends(get_db)):
    """
    CRITICAL RULE: AI suggested domains NEVER auto-promote without HOD explicit verification.
    """
    faculty_id = int(data.get("faculty_id", 0))
    domain = str(data.get("domain", "")).strip()
    action = str(data.get("action", "APPROVE")).upper()
    reviewed_by = str(data.get("reviewed_by", "Dr. HOD")).strip()
    notes = str(data.get("review_notes", "")).strip()

    fac = db.query(models.Faculty).filter(models.Faculty.faculty_id == faculty_id).first()
    if not fac or not domain:
        raise HTTPException(status_code=404, detail="Faculty not found or domain missing")

    verified = parse_list(fac.verified_domains)
    ai_domains = parse_list(fac.ai_suggested_domains)

    if action == "APPROVE":
        if domain not in verified:
            verified.append(domain)
            fac.verified_domains = json.dumps(verified)
        status = "APPROVED"
        msg = f"Domain '{domain}' successfully verified and approved by HOD for {fac.faculty_name}."
    else:
        status = "REJECTED"
        msg = f"Domain '{domain}' rejected by HOD for {fac.faculty_name}."

    if domain in ai_domains:
        ai_domains.remove(domain)
        fac.ai_suggested_domains = json.dumps(ai_domains)

    log_entry = models.AISuggestionLog(
        faculty_id=faculty_id,
        suggested_domain=domain,
        source="AI Recommendation",
        status=status,
        reviewed_by=reviewed_by,
        review_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        review_notes=notes or f"HOD {status}"
    )
    db.add(log_entry)
    db.commit()
    db.refresh(fac)

    return {
        "message": msg,
        "action": action,
        "faculty_id": faculty_id,
        "domain": domain,
        "verified_domains": verified
    }


@app.get("/hod/alerts")
def get_hod_alerts(db: Session = Depends(get_db)):
    """Aggregates all active HOD alerts."""
    alerts = []
    
    # Unassigned subjects
    unassigned = db.query(models.Assignment).filter(models.Assignment.is_unassigned == 1).all()
    for u in unassigned:
        sub = db.query(models.Subject).filter(models.Subject.subject_id == u.subject_id).first()
        sub_name = sub.subject_name if sub else f"Subject #{u.subject_id}"
        req_dom = sub.required_domain if sub else "N/A"
        alerts.append({
            "type": "UNASSIGNED_SUBJECT",
            "severity": "CRITICAL",
            "message": f"🚨 '{sub_name}' has no eligible faculty available. Manual HOD assignment required.",
            "subject_id": u.subject_id,
            "subject_name": sub_name,
            "required_domain": req_dom
        })

    # AI domain suggestions needing review
    faculties = db.query(models.Faculty).all()
    for f in faculties:
        ai_sug = parse_list(f.ai_suggested_domains)
        for s in ai_sug:
            alerts.append({
                "type": "PENDING_AI_DOMAIN_APPROVAL",
                "severity": "INFO",
                "message": f"💡 AI recommended domain '{s}' for {f.faculty_name}. Pending HOD verification.",
                "faculty_id": f.faculty_id,
                "faculty_name": f.faculty_name,
                "domain": s
            })

    # Overloaded faculty
    for f in faculties:
        wl = calculate_workload(db, f.faculty_id)
        if wl.status == "Overloaded":
            alerts.append({
                "type": "FACULTY_OVERLOAD",
                "severity": "WARNING",
                "message": f"⚠️ {f.faculty_name} is overloaded ({wl.weekly_hours} hrs / max {f.max_hours_per_week} hrs).",
                "faculty_id": f.faculty_id,
                "faculty_name": f.faculty_name
            })

    return alerts


# ---------------- Workload ----------------
@app.get("/workload/{faculty_id}")
def get_workload(faculty_id: int, db: Session = Depends(get_db)):
    workload = calculate_workload(db, faculty_id)
    faculty = db.query(models.Faculty).filter(models.Faculty.faculty_id == faculty_id).first()
    return {
        "faculty_id": faculty_id,
        "faculty_name": faculty.faculty_name if faculty else "Unknown",
        "weekly_hours": workload.weekly_hours,
        "monthly_hours": workload.monthly_hours,
        "status": workload.status,
        "max_hours": faculty.max_hours_per_week if faculty else 18
    }


@app.get("/timetable/{faculty_id}")
def get_faculty_timetable(faculty_id: int, db: Session = Depends(get_db)):
    assignments = db.query(models.Assignment).filter(
        models.Assignment.faculty_id == faculty_id,
        models.Assignment.is_unassigned == 0
    ).all()
    faculty = db.query(models.Faculty).filter(models.Faculty.faculty_id == faculty_id).first()

    schedule = []
    for a in assignments:
        sub = db.query(models.Subject).filter(models.Subject.subject_id == a.subject_id).first()
        schedule.append({
            "assignment_id": a.assignment_id,
            "day": a.day,
            "time_slot": f"{a.start_time} - {a.end_time}",
            "start_time": a.start_time,
            "end_time": a.end_time,
            "subject": sub.subject_name if sub else f"Subject #{a.subject_id}",
            "subject_code": sub.subject_code if sub else "",
            "hours": sub.hours_per_week if sub else 1,
            "semester": a.semester,
            "is_hod_exception": bool(a.is_hod_exception)
        })

    return schedule


@app.get("/workload/all/summary")
def get_all_workloads(db: Session = Depends(get_db)):
    faculties = db.query(models.Faculty).all()
    summary = []
    for f in faculties:
        wl = calculate_workload(db, f.faculty_id)
        summary.append({
            "faculty_id": f.faculty_id,
            "faculty_name": f.faculty_name,
            "department": f.department,
            "weekly_hours": wl.weekly_hours,
            "monthly_hours": wl.monthly_hours,
            "max_hours": f.max_hours_per_week,
            "status": wl.status
        })
    return summary


# ---------------- Seed Official College Data with Strict Profiles ----------------
@app.post("/seed")
def seed_sample_data(force: bool = False, db: Session = Depends(get_db)):
    if not force and db.query(models.Faculty).count() > 0:
        return {"message": "Database already has data. Use force=True to re-seed."}

    if force:
        db.query(models.Assignment).delete()
        db.query(models.HODExceptionApproval).delete()
        db.query(models.AISuggestionLog).delete()
        db.query(models.Workload).delete()
        db.query(models.Subject).delete()
        db.query(models.Faculty).delete()
        db.commit()

    # Seed Faculty with strict profile separation
    faculty_specs = [
        {
            "name": "Mrs. E. Mathumitha",
            "department": "Artificial Intelligence and Data Science",
            "designation": "Assistant Professor",
            "max_hours": 18,
            "preferred_domains": ["Text Analytics"],
            "verified_domains": ["Text Analytics", "Natural Language Processing", "Machine Learning"],
            "skills": ["NLP", "Python", "Transformers", "Text Processing", "Information Retrieval"],
            "qualified_subjects": ["Text Analytics", "Seminar Hour", "Library Hour / Net Lab", "Counselling Hour"],
            "previous_subjects": ["Text Analytics", "Data Structures"],
            "teaching_years": 6,
            "certs": "M.Tech AI & Data Science",
            "preferred_subjects": ["Text Analytics"],
            "ai_suggested": ["Web Technology"] # AI suggestion - unapproved
        },
        {
            "name": "Mrs. A. Sangeetha",
            "department": "Artificial Intelligence and Data Science",
            "designation": "Assistant Professor",
            "max_hours": 18,
            "preferred_domains": ["Web Technology", "Computer Networks"],
            "verified_domains": ["Web Technology", "Computer Networks", "Internet of Things"],
            "skills": ["HTML", "CSS", "JavaScript", "React", "NodeJS", "TCP/IP", "Web Development"],
            "qualified_subjects": ["Web Technology", "Web Technology Lab", "Computer Networks"],
            "previous_subjects": ["Web Technology", "Web Technology Lab"],
            "teaching_years": 7,
            "certs": "M.E Software Engineering",
            "preferred_subjects": ["Web Technology", "Web Technology Lab"],
            "ai_suggested": []
        },
        {
            "name": "Mrs. U. Vidhya",
            "department": "Artificial Intelligence and Data Science",
            "designation": "Assistant Professor",
            "max_hours": 18,
            "preferred_domains": ["Machine Learning"],
            "verified_domains": ["Machine Learning", "Deep Learning", "Pattern Recognition"],
            "skills": ["Python", "Scikit-Learn", "TensorFlow", "PyTorch", "Model Evaluation"],
            "qualified_subjects": ["Machine Learning Techniques", "Machine Learning Lab", "Counselling Hour"],
            "previous_subjects": ["Machine Learning Techniques", "Machine Learning Lab"],
            "teaching_years": 8,
            "certs": "M.Tech CSE, Ph.D Scholar",
            "preferred_subjects": ["Machine Learning Techniques"],
            "ai_suggested": []
        },
        {
            "name": "Mrs. S. Vimala",
            "department": "Artificial Intelligence and Data Science",
            "designation": "Assistant Professor",
            "max_hours": 18,
            "preferred_domains": ["Business Analytics"],
            "verified_domains": ["Business Analytics", "Research Analytics", "Data Mining"],
            "skills": ["SQL", "Power BI", "Tableau", "R", "Business Intelligence"],
            "qualified_subjects": ["Business Analytics", "Mini Project-1 Lab", "Research Analytics", "Counselling Hour"],
            "previous_subjects": ["Business Analytics", "Mini Project-1 Lab"],
            "teaching_years": 9,
            "certs": "M.E CSE",
            "preferred_subjects": ["Business Analytics"],
            "ai_suggested": []
        },
        {
            "name": "Mrs. S. Sahishkumar",
            "department": "Artificial Intelligence and Data Science",
            "designation": "Assistant Professor",
            "max_hours": 18,
            "preferred_domains": ["Data Warehousing and Data Mining"],
            "verified_domains": ["Data Warehousing and Data Mining", "Big Data Analytics", "Database Systems"],
            "skills": ["ETL", "Hadoop", "Data Modeling", "SQL", "Spark"],
            "qualified_subjects": ["Data Warehousing and Data Mining / Elective"],
            "previous_subjects": ["Data Warehousing and Data Mining / Elective"],
            "teaching_years": 5,
            "certs": "M.Tech Information Technology",
            "preferred_subjects": ["Data Warehousing and Data Mining / Elective"],
            "ai_suggested": []
        },
        {
            "name": "D. P. Elamurugan",
            "department": "Mechanical Engineering / HME",
            "designation": "Professor",
            "max_hours": 15,
            "preferred_domains": ["Consumer Electronics & Smart Systems"],
            "verified_domains": ["Consumer Electronics & Smart Systems", "Embedded Systems", "Hardware Architecture"],
            "skills": ["Microcontrollers", "IoT", "Sensors", "Circuit Design"],
            "qualified_subjects": ["Consumer Electronics & Smart Systems"],
            "previous_subjects": ["Consumer Electronics & Smart Systems"],
            "teaching_years": 15,
            "certs": "Ph.D Electronics",
            "preferred_subjects": ["Consumer Electronics & Smart Systems"],
            "ai_suggested": []
        },
        {
            "name": "Mrs. S. Selvalakshmi",
            "department": "Computer Science and Engineering",
            "designation": "Assistant Professor",
            "max_hours": 18,
            "preferred_domains": ["Professional Skills and Career Development"],
            "verified_domains": ["Professional Skills and Career Development", "Soft Skills", "Computer Science"],
            "skills": ["Communication", "Aptitude", "Interview Preparation", "Resume Building"],
            "qualified_subjects": ["Professional Skills and Career Development"],
            "previous_subjects": ["Professional Skills and Career Development"],
            "teaching_years": 10,
            "certs": "M.E CSE",
            "preferred_subjects": ["Professional Skills and Career Development"],
            "ai_suggested": []
        },
        {
            "name": "Mrs. K. Gayathri",
            "department": "Artificial Intelligence and Data Science",
            "designation": "Assistant Professor",
            "max_hours": 18,
            "preferred_domains": ["Machine Learning"],
            "verified_domains": ["Machine Learning", "Deep Learning"],
            "skills": ["Python", "Neural Networks", "Data Preprocessing"],
            "qualified_subjects": ["Machine Learning Techniques"],
            "previous_subjects": ["Python Programming"],
            "teaching_years": 3,
            "certs": "M.E AI",
            "preferred_subjects": ["Machine Learning Techniques"],
            "ai_suggested": [] # No verified Computer Networks or Web Technology
        }
    ]

    fac_map = {}
    for f in faculty_specs:
        fac_obj = models.Faculty(
            faculty_name=f["name"],
            department=f["department"],
            designation=f["designation"],
            max_hours_per_week=f["max_hours"],
            preferred_domains=json.dumps(f["preferred_domains"]),
            verified_domains=json.dumps(f["verified_domains"]),
            skills_expertise=json.dumps(f["skills"]),
            qualified_subjects=json.dumps(f["qualified_subjects"]),
            previously_taught_subjects=json.dumps(f["previous_subjects"]),
            teaching_experience_years=f["teaching_years"],
            qualifications_certifications=f["certs"],
            preferred_subjects=json.dumps(f["preferred_subjects"]),
            available_time_slots=json.dumps([]),
            ai_suggested_domains=json.dumps(f["ai_suggested"]),
            current_workload=0
        )
        db.add(fac_obj)
        db.commit()
        db.refresh(fac_obj)
        fac_map[f["name"]] = fac_obj

    # Seed Subjects with required domains and skills
    subject_specs = [
        ("Business Analytics", "24AD501", 5, "Semester 5", "Business Analytics", ["SQL", "Power BI", "Tableau"]),
        ("Text Analytics", "24AD502", 5, "Semester 5", "Text Analytics", ["NLP", "Python", "Transformers"]),
        ("Machine Learning Techniques", "24AD503", 6, "Semester 5", "Machine Learning", ["Scikit-Learn", "Python", "Regression"]),
        ("Web Technology", "24IT501", 6, "Semester 5", "Web Technology", ["HTML", "CSS", "JavaScript"]),
        ("Data Warehousing and Data Mining / Elective", "24AD302PE", 5, "Semester 5", "Data Warehousing and Data Mining", ["ETL", "Data Modeling", "SQL"]),
        ("Consumer Electronics & Smart Systems", "24EC1020E", 6, "Semester 5", "Consumer Electronics & Smart Systems", ["IoT", "Microcontrollers", "Sensors"]),
        ("Machine Learning Lab", "24AD04L", 3, "Semester 5", "Machine Learning", ["Python", "TensorFlow"]),
        ("Mini Project-1 Lab", "24AD05L", 3, "Semester 5", "Business Analytics", ["Python", "Project Development"]),
        ("Professional Skills and Career Development", "24ITEC501L", 10, "Semester 5", "Professional Skills and Career Development", ["Communication", "Aptitude"]),
        ("Seminar Hour", "SH", 1, "Semester 5", "Text Analytics", ["Presentation"]),
        ("Library Hour / Net Lab", "LIB/NET", 1, "Semester 5", "Text Analytics", ["Research"]),
        ("Counselling Hour", "COUNC", 3, "Semester 5", "General", ["Mentorship"]),
        ("Web Technology Lab", "24IT501L", 2, "Semester 5", "Web Technology", ["HTML", "CSS", "JavaScript"]),
        ("Research Analytics", "24AD501", 1, "Semester 5", "Business Analytics", ["Analytics"]),
        ("Computer Networks", "24CS401", 4, "Semester 5", "Computer Networks", ["TCP/IP", "Routing", "OSI Model"])
    ]

    sub_map = {}
    for name, code, hours, sem, req_dom, req_skills in subject_specs:
        sub_obj = models.Subject(
            subject_name=name,
            subject_code=code,
            hours_per_week=hours,
            semester=sem,
            required_domain=req_dom,
            required_skills=json.dumps(req_skills),
            required_qualification="M.Tech / Ph.D in relevant domain",
            department="Artificial Intelligence and Data Science",
            class_section="III-A"
        )
        db.add(sub_obj)
        db.commit()
        db.refresh(sub_obj)
        sub_map[name] = sub_obj

    # Run initial automatic generation using the strict eligibility engine
    gen_result = generate_automatic_timetable(db=db, class_section="III-A", semester="Semester 5", persist_to_db=True)

    return {
        "message": "Official College Faculty and Curriculum seeded with Strict Eligibility Engine!",
        "faculty_created": len(fac_map),
        "subjects_created": len(sub_map),
        "generator_result": gen_result
    }


# ---------------- PDF Export ----------------
@app.get("/timetable/export/pdf")
def export_pdf(db: Session = Depends(get_db)):
    assignments = db.query(models.Assignment).filter(models.Assignment.is_unassigned == 0).all()
    data = [["Day", "Start Time", "End Time", "Faculty Name", "Subject Name"]]

    for a in assignments:
        faculty = db.query(models.Faculty).filter(models.Faculty.faculty_id == a.faculty_id).first()
        subject = db.query(models.Subject).filter(models.Subject.subject_id == a.subject_id).first()

        data.append([
            str(a.day),
            str(a.start_time),
            str(a.end_time),
            faculty.faculty_name if faculty else "Unassigned",
            subject.subject_name if subject else "N/A"
        ])

    file_path = generate_pdf(data)

    if os.path.exists(file_path):
        return FileResponse(
            path=file_path,
            filename="timetable.pdf",
            media_type="application/pdf"
        )
    return {"message": "PDF generated", "file": file_path}


# ---------------- AI Chatbot ----------------
@app.post("/chatbot")
def chatbot(data: dict = Body(...), db: Session = Depends(get_db)):
    question = data.get("question", "")

    assignments = db.query(models.Assignment).all()
    faculty_list = db.query(models.Faculty).all()
    subjects = db.query(models.Subject).all()

    timetable_data = []
    for a in assignments:
        faculty = db.query(models.Faculty).filter(models.Faculty.faculty_id == a.faculty_id).first()
        subject = db.query(models.Subject).filter(models.Subject.subject_id == a.subject_id).first()
        timetable_data.append({
            "day": a.day,
            "start_time": a.start_time,
            "end_time": a.end_time,
            "faculty": faculty.faculty_name if faculty else "Unassigned",
            "subject": subject.subject_name if subject else "N/A",
            "semester": a.semester
        })

    faculty_names = [f.faculty_name for f in faculty_list]
    subject_names = [s.subject_name for s in subjects]

    prompt = f"""
    You are a Staff Timetable AI Assistant. You have access to the following timetable data:

    FACULTY LIST:
    {', '.join(faculty_names) if faculty_names else 'No faculty data available'}

    SUBJECTS:
    {', '.join(subject_names) if subject_names else 'No subject data available'}

    TIMETABLE ASSIGNMENTS:
    {timetable_data if timetable_data else 'No timetable data available'}

    Question: {question}
    """

    answer = ask_ai(prompt)
    return {"response": answer}


# ---------------- PDF Upload for Chatbot ----------------
@app.post("/chatbot/upload-pdf")
def upload_pdf_for_chatbot(file: UploadFile = File(...)):
    global uploaded_pdf_content
    try:
        contents = file.file.read()
        try:
            import PyPDF2
            pdf_file = io.BytesIO(contents)
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            extracted_text = ""
            for page in pdf_reader.pages:
                extracted_text += (page.extract_text() or "") + "\n"
            uploaded_pdf_content = extracted_text
            return {
                "message": "PDF uploaded and processed successfully",
                "filename": file.filename,
                "content_preview": extracted_text[:300] + "..." if len(extracted_text) > 300 else extracted_text
            }
        except Exception:
            uploaded_pdf_content = contents.decode('utf-8', errors='ignore')
            return {
                "message": "PDF uploaded (text decoded as fallback)",
                "filename": file.filename,
                "content_preview": uploaded_pdf_content[:300] + "..."
            }
    except Exception as e:
        return {"error": f"Failed to process PDF: {str(e)}"}


# ---------------- AI Chatbot with PDF Context ----------------
@app.post("/chatbot/with-pdf")
def chatbot_with_pdf(data: dict = Body(...), db: Session = Depends(get_db)):
    global uploaded_pdf_content
    question = data.get("question", "")

    assignments = db.query(models.Assignment).all()
    faculty_list = db.query(models.Faculty).all()
    subjects = db.query(models.Subject).all()

    timetable_data = []
    for a in assignments:
        faculty = db.query(models.Faculty).filter(models.Faculty.faculty_id == a.faculty_id).first()
        subject = db.query(models.Subject).filter(models.Subject.subject_id == a.subject_id).first()
        timetable_data.append({
            "day": a.day,
            "start_time": a.start_time,
            "end_time": a.end_time,
            "faculty": faculty.faculty_name if faculty else "Unassigned",
            "subject": subject.subject_name if subject else "N/A",
            "semester": a.semester
        })

    faculty_names = [f.faculty_name for f in faculty_list]
    subject_names = [s.subject_name for s in subjects]
    pdf_context = f"\n\nUPLOADED PDF CONTENT:\n{uploaded_pdf_content}" if uploaded_pdf_content else "\n\nNo PDF uploaded yet."

    prompt = f"""
    You are a Staff Timetable AI Assistant. You have access to the following data:

    FACULTY LIST:
    {', '.join(faculty_names) if faculty_names else 'No faculty data available'}

    SUBJECTS:
    {', '.join(subject_names) if subject_names else 'No subject data available'}

    TIMETABLE ASSIGNMENTS:
    {timetable_data if timetable_data else 'No timetable data available'}
    {pdf_context}

    Question: {question}
    """

    answer = ask_ai(prompt)
    return {"response": answer}