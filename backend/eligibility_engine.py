"""
Faculty–Subject Eligibility and Matching Engine
===============================================
Strict eligibility validation and multi-factor suitability scoring.
Mandatory eligibility rules are HARD CONSTRAINTS.
Suitability factors are SOFT CONSTRAINTS.

Separates:
1. Preferred Domain
2. Verified Expertise
3. AI Suggested Domain (CANNOT auto-promote without HOD approval)
4. Eligible Subjects
"""

import json
import re
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from backend.models import Faculty, Subject, HODExceptionApproval


def parse_list(val: Any) -> List[str]:
    """Safely parse JSON strings, comma-delimited strings, or lists into a list of strings."""
    if not val:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        val = val.strip()
        if not val or val == "[]":
            return []
        if val.startswith("[") and val.endswith("]"):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                pass
        # Fallback to comma/semicolon/newline separated
        parts = re.split(r'[,;\n]+', val)
        return [p.strip().strip('"').strip("'") for p in parts if p.strip()]
    return [str(val).strip()]


def normalize_text(text: str) -> str:
    """Normalize text for consistent comparison (lowercase, strip extra punctuation)."""
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r'[\-_/()&]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def text_matches_any(needle: str, haystack: List[str]) -> bool:
    """Check if needle matches any item in haystack (exact or normalized token match)."""
    norm_needle = normalize_text(needle)
    if not norm_needle:
        return False
    for item in haystack:
        norm_item = normalize_text(item)
        if not norm_item:
            continue
        if norm_needle == norm_item:
            return True
        # Check token containment if meaningful length
        if len(norm_needle) >= 3 and len(norm_item) >= 3:
            if norm_needle in norm_item or norm_item in norm_needle:
                return True
    return False


def evaluate_faculty_eligibility(
    faculty: Faculty,
    subject: Subject,
    db: Optional[Session] = None,
    check_hod_exceptions: bool = True
) -> Dict[str, Any]:
    """
    Evaluates whether a faculty member is ELIGIBLE to teach a subject.
    
    Hard constraints require at least one verified qualification path:
    1. Verified Domain matches Subject's required domain
    2. Subject is in Faculty's qualified_subjects list
    3. Subject is in Faculty's previously_taught_subjects list
    4. An active, approved HOD Exception exists for this pair
    
    CRITICAL:
    - Preferred Domain ALONE is not sufficient without verified domain match.
    - AI Suggested Domain is NEVER treated as verified expertise.
    """
    verified_domains = parse_list(faculty.verified_domains)
    preferred_domains = parse_list(faculty.preferred_domains)
    ai_suggested = parse_list(faculty.ai_suggested_domains)
    qualified_subjects = parse_list(faculty.qualified_subjects)
    previously_taught = parse_list(faculty.previously_taught_subjects)
    faculty_skills = parse_list(faculty.skills_expertise)
    req_skills = parse_list(subject.required_skills)
    
    req_domain = (subject.required_domain or "").strip()
    sub_name = (subject.subject_name or "").strip()
    sub_code = (subject.subject_code or "").strip()

    # 1. Check for Approved HOD Exception
    is_hod_exception = False
    hod_reason = None
    if check_hod_exceptions and db is not None:
        approval = db.query(HODExceptionApproval).filter(
            HODExceptionApproval.faculty_id == faculty.faculty_id,
            HODExceptionApproval.subject_id == subject.subject_id,
            HODExceptionApproval.approval_status == "APPROVED"
        ).first()
        if approval:
            is_hod_exception = True
            hod_reason = approval.reason_for_exception

    # 2. Check Domain Match against Verified Domains
    domain_match = False
    if req_domain:
        domain_match = text_matches_any(req_domain, verified_domains)
    else:
        # If no explicit domain required, check if subject name matches any verified domain
        domain_match = text_matches_any(sub_name, verified_domains)

    # 3. Check Subject Qualification
    qualified_match = (
        text_matches_any(sub_name, qualified_subjects) or
        (sub_code and text_matches_any(sub_code, qualified_subjects))
    )

    # 4. Check Previous Teaching Experience
    experience_match = (
        text_matches_any(sub_name, previously_taught) or
        (sub_code and text_matches_any(sub_code, previously_taught))
    )

    # 5. Check Skills Match
    skills_matched = []
    if req_skills:
        for rs in req_skills:
            if text_matches_any(rs, faculty_skills):
                skills_matched.append(rs)
        skills_ratio = len(skills_matched) / len(req_skills) if req_skills else 1.0
    else:
        skills_ratio = 1.0

    # 6. Preferred Domain Check (Soft factor, tracked for scoring/reporting)
    preferred_domain_match = False
    if req_domain:
        preferred_domain_match = text_matches_any(req_domain, preferred_domains)

    # 7. Check if AI predicted this domain (for warning/audit)
    ai_domain_match = False
    if req_domain:
        ai_domain_match = text_matches_any(req_domain, ai_suggested)

    # Hard Constraint Determination
    is_eligible = False
    reasons = []
    rejection_reasons = []

    if is_hod_exception:
        is_eligible = True
        reasons.append(f"HOD Approved Exception: {hod_reason}")

    elif domain_match:
        is_eligible = True
        reasons.append(f"Verified Domain match for '{req_domain or sub_name}'")

    elif qualified_match:
        is_eligible = True
        reasons.append(f"Formally qualified to teach '{sub_name}' ({sub_code})")

    elif experience_match:
        is_eligible = True
        reasons.append(f"Documented previous teaching experience for '{sub_name}'")

    else:
        # INELIGIBLE
        is_eligible = False
        rejection_reasons.append(
            f"No verified expertise or teaching experience in '{req_domain or sub_name}'."
        )
        if preferred_domain_match and not domain_match:
            rejection_reasons.append(
                f"Preferred domain '{req_domain}' is not verified by academic credentials or prior teaching."
            )
        if ai_domain_match:
            rejection_reasons.append(
                f"Domain '{req_domain}' is an unverified AI recommendation and has not been approved by HOD."
            )
        rejection_reasons.append(
            f"Verified domains: {verified_domains if verified_domains else 'None'} | "
            f"Past subjects: {previously_taught if previously_taught else 'None'}"
        )

    return {
        "faculty_id": faculty.faculty_id,
        "faculty_name": faculty.faculty_name,
        "subject_id": subject.subject_id,
        "subject_name": subject.subject_name,
        "is_eligible": is_eligible,
        "is_hod_exception": is_hod_exception,
        "domain_match": domain_match,
        "preferred_domain_match": preferred_domain_match,
        "qualified_match": qualified_match,
        "experience_match": experience_match,
        "ai_domain_match": ai_domain_match,
        "skills_ratio": round(skills_ratio, 2),
        "skills_matched": skills_matched,
        "reasons": reasons,
        "rejection_reasons": rejection_reasons,
        "summary": "ELIGIBLE" if is_eligible else "NOT ELIGIBLE"
    }


def calculate_suitability_score(
    faculty: Faculty,
    subject: Subject,
    current_workload: int = 0,
    max_hours: int = 18
) -> Dict[str, Any]:
    """
    Computes a suitability score (0-100) for ELIGIBLE faculty members.
    
    Soft constraints and weights:
    - Domain Match (Verified): 30 pts
    - Preferred Domain Match: 15 pts
    - Subject Previously Taught: 20 pts
    - Required Skills Match: 15 pts
    - Teaching Experience: Up to 10 pts (min(years * 2, 10))
    - Faculty Preferred Subject: 10 pts
    - Workload Distribution Bonus: Up to 10 pts for faculty with available capacity
    """
    verified_domains = parse_list(faculty.verified_domains)
    preferred_domains = parse_list(faculty.preferred_domains)
    previously_taught = parse_list(faculty.previously_taught_subjects)
    preferred_subjects = parse_list(faculty.preferred_subjects)
    faculty_skills = parse_list(faculty.skills_expertise)
    req_skills = parse_list(subject.required_skills)

    req_domain = (subject.required_domain or "").strip()
    sub_name = (subject.subject_name or "").strip()
    sub_code = (subject.subject_code or "").strip()

    score = 0.0
    breakdown = {}

    # 1. Verified Domain Match (30 pts)
    if req_domain and text_matches_any(req_domain, verified_domains):
        score += 30.0
        breakdown["verified_domain_match"] = 30.0
    elif not req_domain and text_matches_any(sub_name, verified_domains):
        score += 25.0
        breakdown["verified_domain_match"] = 25.0
    else:
        breakdown["verified_domain_match"] = 0.0

    # 2. Preferred Domain Match (15 pts)
    if req_domain and text_matches_any(req_domain, preferred_domains):
        score += 15.0
        breakdown["preferred_domain_match"] = 15.0
    else:
        breakdown["preferred_domain_match"] = 0.0

    # 3. Subject Previously Taught (20 pts)
    if text_matches_any(sub_name, previously_taught) or (sub_code and text_matches_any(sub_code, previously_taught)):
        score += 20.0
        breakdown["previously_taught_subject"] = 20.0
    else:
        breakdown["previously_taught_subject"] = 0.0

    # 4. Required Skills Match (15 pts)
    if req_skills:
        matched = sum(1 for s in req_skills if text_matches_any(s, faculty_skills))
        skill_pts = round((matched / len(req_skills)) * 15.0, 1)
        score += skill_pts
        breakdown["skills_match"] = skill_pts
    else:
        score += 10.0
        breakdown["skills_match"] = 10.0

    # 5. Teaching Experience (10 pts)
    exp_years = faculty.teaching_experience_years or 0
    exp_pts = min(exp_years * 2.0, 10.0)
    score += exp_pts
    breakdown["experience_points"] = exp_pts

    # 6. Faculty Preferred Subject (10 pts)
    if text_matches_any(sub_name, preferred_subjects) or (sub_code and text_matches_any(sub_code, preferred_subjects)):
        score += 10.0
        breakdown["faculty_preferred_subject"] = 10.0
    else:
        breakdown["faculty_preferred_subject"] = 0.0

    # 7. Workload Distribution Bonus (Up to 10 pts)
    max_h = max_hours or (faculty.max_hours_per_week or 18)
    available_hours = max(0, max_h - current_workload)
    workload_utilization = current_workload / max_h if max_h > 0 else 1.0
    
    # Faculty with more remaining capacity receive a higher balancing bonus
    workload_bonus = round(max(0.0, (1.0 - workload_utilization) * 10.0), 1)
    score += workload_bonus
    breakdown["workload_balancing_bonus"] = workload_bonus

    final_score = round(min(score, 100.0), 1)
    return {
        "suitability_score": final_score,
        "breakdown": breakdown,
        "current_workload": current_workload,
        "max_hours": max_h,
        "remaining_capacity": available_hours
    }


def evaluate_all_candidates_for_subject(
    subject: Subject,
    faculty_list: List[Faculty],
    db: Optional[Session] = None,
    current_workloads: Optional[Dict[int, int]] = None
) -> Dict[str, Any]:
    """
    Evaluates all faculty members for a given subject.
    Filters strictly to ELIGIBLE faculty, calculates suitability scores,
    and flags unassigned subjects if no eligible faculty exists.
    """
    eligible_candidates = []
    ineligible_candidates = []

    if current_workloads is None:
        current_workloads = {}

    for fac in faculty_list:
        elig = evaluate_faculty_eligibility(fac, subject, db=db)
        cur_wl = current_workloads.get(fac.faculty_id, fac.current_workload or 0)
        
        if elig["is_eligible"]:
            suit = calculate_suitability_score(
                fac,
                subject,
                current_workload=cur_wl,
                max_hours=fac.max_hours_per_week or 18
            )
            candidate_record = {
                "faculty_id": fac.faculty_id,
                "faculty_name": fac.faculty_name,
                "department": fac.department,
                "designation": fac.designation,
                "is_hod_exception": elig["is_hod_exception"],
                "reasons": elig["reasons"],
                "suitability_score": suit["suitability_score"],
                "score_breakdown": suit["breakdown"],
                "current_workload": suit["current_workload"],
                "max_hours": suit["max_hours"],
                "remaining_capacity": suit["remaining_capacity"]
            }
            eligible_candidates.append(candidate_record)
        else:
            ineligible_candidates.append({
                "faculty_id": fac.faculty_id,
                "faculty_name": fac.faculty_name,
                "department": fac.department,
                "rejection_reasons": elig["rejection_reasons"]
            })

    # Sort eligible candidates by suitability score descending, then remaining capacity
    eligible_candidates.sort(
        key=lambda x: (x["suitability_score"], x["remaining_capacity"]),
        reverse=True
    )

    has_eligible = len(eligible_candidates) > 0
    hod_alert = None
    if not has_eligible:
        hod_alert = (
            f"🚨 ALERT: '{subject.subject_name}' ({subject.subject_code}) has no eligible faculty available. "
            f"Required domain: '{subject.required_domain or 'General'}'. Manual HOD assignment is required."
        )

    return {
        "subject_id": subject.subject_id,
        "subject_name": subject.subject_name,
        "subject_code": subject.subject_code,
        "required_domain": subject.required_domain,
        "has_eligible_faculty": has_eligible,
        "hod_alert": hod_alert,
        "eligible_candidates": eligible_candidates,
        "ineligible_candidates": ineligible_candidates,
        "total_evaluated": len(faculty_list),
        "eligible_count": len(eligible_candidates),
        "ineligible_count": len(ineligible_candidates)
    }


def build_full_eligibility_matrix(
    faculty_list: List[Faculty],
    subject_list: List[Subject],
    db: Optional[Session] = None
) -> List[Dict[str, Any]]:
    """Builds a complete matrix of Faculty vs Subject for inspection and display."""
    matrix = []
    for fac in faculty_list:
        for sub in subject_list:
            res = evaluate_faculty_eligibility(fac, sub, db=db)
            suit = calculate_suitability_score(fac, sub, fac.current_workload or 0, fac.max_hours_per_week or 18)
            matrix.append({
                "faculty_id": fac.faculty_id,
                "faculty_name": fac.faculty_name,
                "subject_id": sub.subject_id,
                "subject_name": sub.subject_name,
                "subject_code": sub.subject_code,
                "required_domain": sub.required_domain,
                "is_eligible": res["is_eligible"],
                "status": "ELIGIBLE" if res["is_eligible"] else "NOT ELIGIBLE",
                "is_hod_exception": res["is_hod_exception"],
                "suitability_score": suit["suitability_score"] if res["is_eligible"] else 0.0,
                "reasons": res["reasons"] if res["is_eligible"] else res["rejection_reasons"]
            })
    return matrix
