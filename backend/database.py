import os
import sqlite3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "timetable.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def auto_migrate_database():
    """Ensures existing SQLite database contains all new columns."""
    if not os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Columns to ensure for 'faculty'
    faculty_cols = {
        "preferred_domains": "TEXT DEFAULT '[]'",
        "verified_domains": "TEXT DEFAULT '[]'",
        "skills_expertise": "TEXT DEFAULT '[]'",
        "qualified_subjects": "TEXT DEFAULT '[]'",
        "previously_taught_subjects": "TEXT DEFAULT '[]'",
        "teaching_experience_years": "INTEGER DEFAULT 0",
        "qualifications_certifications": "TEXT DEFAULT ''",
        "preferred_subjects": "TEXT DEFAULT '[]'",
        "available_time_slots": "TEXT DEFAULT '[]'",
        "ai_suggested_domains": "TEXT DEFAULT '[]'",
        "current_workload": "INTEGER DEFAULT 0"
    }

    # Columns to ensure for 'subject'
    subject_cols = {
        "required_domain": "TEXT DEFAULT ''",
        "required_skills": "TEXT DEFAULT '[]'",
        "required_qualification": "TEXT DEFAULT ''",
        "department": "TEXT DEFAULT 'Artificial Intelligence and Data Science'",
        "class_section": "TEXT DEFAULT 'III-A'"
    }

    # Columns to ensure for 'assignment'
    assignment_cols = {
        "class_section": "TEXT DEFAULT 'III-A'",
        "is_unassigned": "INTEGER DEFAULT 0",
        "unassigned_reason": "TEXT DEFAULT ''",
        "is_hod_exception": "INTEGER DEFAULT 0",
        "hod_exception_id": "INTEGER DEFAULT NULL"
    }

    tables_and_cols = [
        ("faculty", faculty_cols),
        ("subject", subject_cols),
        ("assignment", assignment_cols),
    ]

    for table_name, cols in tables_and_cols:
        cursor.execute(f"PRAGMA table_info({table_name})")
        existing_cols = {row[1] for row in cursor.fetchall()}
        if not existing_cols:
            continue
        for col_name, col_type in cols.items():
            if col_name not in existing_cols:
                try:
                    cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")
                except Exception as e:
                    print(f"Migration note ({table_name}.{col_name}): {e}")

    conn.commit()
    conn.close()