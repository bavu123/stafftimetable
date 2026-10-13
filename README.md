# 🎓 Staff Timetable Intelligence AI System (with Strict Eligibility Engine)

An AI-powered academic scheduling, workload distribution, and timetable management platform built with **FastAPI**, **Streamlit**, **SQLAlchemy**, and a **Strict Faculty–Subject Eligibility and Matching Engine**.

---

## 🚀 Quick Start Guide

### 1. Run All Test Cases
To verify strict eligibility enforcement, AI domain isolation, and conflict validation:
```bash
python -m pytest tests/test_eligibility_engine.py -v
```
All 9 unit & integration tests validate that unqualified assignments are rejected, unassigned subjects alert HOD, and AI domain predictions are strictly isolated.

### 2. Start the Backend API (FastAPI)
Run the backend server on port `8000`:
```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```
- API Docs & Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Interactive ReDoc: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

### 3. Start the Frontend Dashboard (Streamlit)
In a separate terminal, run:
```bash
python -m streamlit run dashboard/streamlit_app.py --server.port 8501
```
- Open Streamlit in browser: [http://localhost:8501](http://localhost:8501)

---

## 🌟 Key Architecture & Features

### 1. 🛡️ Strict Faculty–Subject Eligibility Engine (`backend/eligibility_engine.py`)
- **Hard Constraints (Mandatory Eligibility):**
  - **Verified Domain Match:** Subject required domain must be in faculty's verified domains.
  - **Formal Subject Qualification:** Subject code/name must be explicitly qualified.
  - **Historical Teaching Experience:** Proven track record in previous semesters.
  - **Approved HOD Exception:** Active exception approved by HOD with documented reason.
- **Concept Separation:**
  1. Preferred Domain
  2. Verified Expertise
  3. AI Suggested Domain (strictly isolated; requires HOD manual review before verified)
  4. Eligible Subjects
- **Auditable Rejection Reasons:** If a faculty member is rejected for a subject, the exact failure causes are logged and displayed.

### 2. ⚡ Automatic Timetable Generator (`generator/timetable_generator.py`)
Generates collision-free academic timetables in the required pipeline order:
1. Ingest Faculty and Subject profiles.
2. Run Eligibility Engine and eliminate all ineligible candidates.
3. Compute multi-factor **Suitability Scores** (0–100) for eligible candidates (domain match + preferred domain + previous experience + skills match + workload balance).
4. If no eligible faculty exists: flag subject as `Unassigned / Faculty Required` and dispatch high-priority HOD Alert. **Never force an unsuitable faculty.**
5. Schedule period slots while balancing workload and eliminating double-bookings.
6. Run post-generation validation check (100% eligibility compliance).

### 3. 🔍 Eligibility & Matching Matrix View
- Interactive matrix of every registered faculty member against all curriculum courses.
- Filter by status: `ELIGIBLE`, `NOT ELIGIBLE`, `HOD EXCEPTION`.
- Drill-down inspector displays exact rejection reasons or suitability score breakdown.

### 4. 🛡️ HOD Governance & Exception Center
- **Unassigned Subject Alerts:** Displays courses with no available qualified faculty.
- **Manual Exception Approvals:** Allows HOD to override with recorded justification and approver name.
- **AI Domain Recommendation Review:** Displays AI suggestions (e.g. Madhu Mam → Web Technology) allowing HOD to approve (promotes to verified) or reject (dismisses).
- **Historical Audit Log:** Permanent records of all exception approvals.
