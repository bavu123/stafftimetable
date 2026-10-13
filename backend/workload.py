from sqlalchemy.orm import Session
from .models import Assignment, Subject, Faculty, Workload

def calculate_workload(db: Session, faculty_id: int):
    assignments = db.query(Assignment).filter(
        Assignment.faculty_id == faculty_id
    ).all()

    total_weekly = len(assignments)
    total_monthly = total_weekly * 4

    faculty = db.query(Faculty).filter(
        Faculty.faculty_id == faculty_id
    ).first()

    max_hours = faculty.max_hours_per_week if (faculty and faculty.max_hours_per_week) else 18

    if total_weekly > max_hours:
        status = "Overloaded"
    elif total_weekly < 5:
        status = "Underloaded"
    else:
        status = "Normal"

    workload = Workload(
        faculty_id=faculty_id,
        weekly_hours=total_weekly,
        monthly_hours=total_monthly,
        status=status,
    )

    db.merge(workload)
    db.commit()

    return workload