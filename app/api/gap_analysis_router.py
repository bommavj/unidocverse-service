"""
gap_analysis_router.py
======================
Policy Gap Analysis API
"""
import logging, uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ams/gap", tags=["gap_analysis"])

REQUIRED_COVERAGE_BY_TYPE = {
    "personal": ["homeowners", "auto", "umbrella"],
    "commercial": ["commercial_property", "general_liability", "workers_comp", "bop"],
}

COVERAGE_LIMITS = {
    "homeowners": {"dwelling": 200000, "liability": 100000},
    "auto": {"liability": 100000},
    "general_liability": {"each_occurrence": 1000000, "aggregate": 2000000},
}


@router.post("/analyze/{client_id}")
def analyze_client_gaps(client_id: str, db: Session = Depends(get_db)):
    """Run full gap analysis for a client."""

    # Get client
    client = db.execute(text("SELECT * FROM clients WHERE id = :id"), {"id": client_id}).fetchone()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    # Get all active policies
    policies = db.execute(text("""
        SELECT * FROM policies
        WHERE client_id = :id AND status IN ('active', 'renewal_due', 'bound')
        ORDER BY line_of_business
    """), {"id": client_id}).fetchall()

    client_type = client.client_type or "personal"
    required = REQUIRED_COVERAGE_BY_TYPE.get(client_type, [])
    covered_lobs = {p.line_of_business for p in policies}

    gaps = []
    recommendations = []
    total_premium = sum(float(p.premium_amount or 0) for p in policies)

    # Check missing coverage types
    for req_lob in required:
        if req_lob not in covered_lobs:
            gaps.append({
                "type": "missing_coverage",
                "priority": "critical",
                "title": f"No {req_lob.replace('_', ' ').title()} coverage",
                "description": f"Client has no {req_lob.replace('_', ' ')} policy.",
                "category": req_lob,
            })

    # Check coverage limits
    for policy in policies:
        coverage = policy.coverage_summary or {}
        limits = COVERAGE_LIMITS.get(policy.line_of_business, {})
        for field, min_val in limits.items():
            actual = coverage.get(field, 0)
            if actual and float(actual) < min_val:
                gaps.append({
                    "type": "low_limit",
                    "priority": "high",
                    "title": f"Low {field.replace('_', ' ')} on {policy.policy_number}",
                    "description": f"Current: ${float(actual):,.0f} — Recommended: ${min_val:,.0f}",
                    "policy_id": str(policy.id),
                    "category": policy.line_of_business,
                })

    # Check umbrella
    has_high_value = total_premium > 5000
    has_umbrella = "umbrella" in covered_lobs
    if has_high_value and not has_umbrella:
        gaps.append({
            "type": "missing_coverage",
            "priority": "high",
            "title": "No umbrella/excess liability coverage",
            "description": "Client has significant premium exposure but no umbrella policy.",
            "category": "umbrella",
        })

    # Score
    total_gaps = len(gaps)
    critical = sum(1 for g in gaps if g["priority"] == "critical")
    score = max(0, 100 - (critical * 20) - ((total_gaps - critical) * 10))
    if score >= 85: grade = "A"
    elif score >= 70: grade = "B"
    elif score >= 55: grade = "C"
    elif score >= 40: grade = "D"
    else: grade = "F"

    # Save analysis
    analysis_id = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO gap_analyses
            (id, client_id, overall_score, overall_grade, total_gaps,
             critical_gaps, total_premium, analyzed_by)
        VALUES (:id, :client_id, :score, :grade, :total_gaps,
                :critical, :premium, 'ai')
    """), {
        "id": analysis_id, "client_id": client_id,
        "score": score, "grade": grade,
        "total_gaps": total_gaps, "critical": critical, "premium": total_premium,
    })

    for gap in gaps:
        rec_id = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO gap_recommendations
                (id, gap_analysis_id, client_id, priority, category,
                 title, description, affected_policy_id)
            VALUES (:id, :analysis_id, :client_id, :priority, :category,
                    :title, :description, :policy_id)
        """), {
            "id": rec_id, "analysis_id": analysis_id, "client_id": client_id,
            "priority": gap["priority"], "category": gap.get("category"),
            "title": gap["title"], "description": gap["description"],
            "policy_id": gap.get("policy_id"),
        })

    db.commit()

    return {
        "analysis_id": analysis_id,
        "client_id": client_id,
        "score": score,
        "grade": grade,
        "total_gaps": total_gaps,
        "critical_gaps": critical,
        "total_premium": total_premium,
        "gaps": gaps,
        "policies_reviewed": len(policies),
        "covered_lines": list(covered_lobs),
        "missing_lines": [l for l in required if l not in covered_lobs],
    }


@router.get("/client/{client_id}")
def get_client_gaps(client_id: str, db: Session = Depends(get_db)):
    analysis = db.execute(text("""
        SELECT * FROM gap_analyses WHERE client_id = :id ORDER BY analyzed_at DESC LIMIT 1
    """), {"id": client_id}).fetchone()

    if not analysis:
        return {"message": "No analysis yet", "client_id": client_id}

    recs = db.execute(text("""
        SELECT r.*, p.policy_number FROM gap_recommendations r
        LEFT JOIN policies p ON p.id = r.affected_policy_id
        WHERE r.gap_analysis_id = :id ORDER BY r.priority
    """), {"id": str(analysis.id)}).fetchall()

    return {
        "analysis": dict(analysis._mapping),
        "recommendations": [dict(r._mapping) for r in recs],
    }


@router.put("/recommendations/{rec_id}/status")
def update_recommendation(rec_id: str, status: str, actioned_by: str = "agent",
                           db: Session = Depends(get_db)):
    db.execute(text("""
        UPDATE gap_recommendations
        SET status = :status, actioned_by = :by, actioned_at = NOW()
        WHERE id = :id
    """), {"status": status, "by": actioned_by, "id": rec_id})
    db.commit()
    return {"message": "Recommendation updated"}