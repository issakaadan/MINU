import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import api_error
from app.core.risk_engine import CERTIFICATE_ISSUE_RULE_KEYS
from app.models import Assessment, Finding, Host, Port, Report, ScanJob, Scope
from app.schemas import AssessmentCreate, AssessmentRead, AssessmentUpdate, DashboardSummary

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("", response_model=list[AssessmentRead])
def list_assessments(
    search: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[Assessment]:
    query = select(Assessment).order_by(Assessment.created_at.desc())
    if search:
        like_term = f"%{search}%"
        query = query.where(
            or_(
                Assessment.name.ilike(like_term),
                Assessment.client_name.ilike(like_term),
                Assessment.owner.ilike(like_term),
                Assessment.description.ilike(like_term),
            )
        )
    return list(db.scalars(query))


@router.post("", response_model=AssessmentRead)
def create_assessment(payload: AssessmentCreate, db: Session = Depends(get_db)) -> Assessment:
    assessment = Assessment(
        name=payload.project_name,
        client_name=payload.client_name,
        description=payload.description,
        owner=payload.assessor_name,
        assessment_date=payload.assessment_date,
        scan_intensity=payload.scan_intensity,
        allow_disruptive_tests=payload.allow_disruptive_tests,
        status=payload.status,
        objectives="",
        severity_threshold="medium",
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    logger.info(
        "Created assessment %s for client %s with %s intensity.",
        assessment.id,
        assessment.client_name,
        assessment.scan_intensity,
    )
    return assessment


@router.put("/{assessment_id}", response_model=AssessmentRead)
def update_assessment(
    assessment_id: int,
    payload: AssessmentUpdate,
    db: Session = Depends(get_db),
) -> Assessment:
    assessment = db.get(Assessment, assessment_id)
    if assessment is None:
        raise api_error(404, "Assessment not found.")

    assessment.allow_disruptive_tests = payload.allow_disruptive_tests
    db.commit()
    db.refresh(assessment)
    logger.info(
        "Assessment %s disruptive test gate changed to %s.",
        assessment_id,
        payload.allow_disruptive_tests,
    )
    return assessment


@router.get("/summary", response_model=DashboardSummary)
def get_dashboard_summary(db: Session = Depends(get_db)) -> DashboardSummary:
    return DashboardSummary(
        assessments=db.scalar(select(func.count()).select_from(Assessment)) or 0,
        scopes=db.scalar(select(func.count()).select_from(Scope)) or 0,
        hosts=db.scalar(select(func.count()).select_from(Host)) or 0,
        live_hosts=db.scalar(
            select(func.count()).select_from(Host).where(Host.status == "live")
        )
        or 0,
        unknown_devices=db.scalar(
            select(func.count()).select_from(Host).where(Host.device_type == "Unknown")
        )
        or 0,
        total_open_ports=db.scalar(select(func.count()).select_from(Port)) or 0,
        findings=db.scalar(select(func.count()).select_from(Finding)) or 0,
        critical_findings=db.scalar(
            select(func.count()).select_from(Finding).where(Finding.severity == "Critical")
        )
        or 0,
        high_findings=db.scalar(
            select(func.count()).select_from(Finding).where(Finding.severity == "High")
        )
        or 0,
        medium_findings=db.scalar(
            select(func.count()).select_from(Finding).where(Finding.severity == "Medium")
        )
        or 0,
        low_findings=db.scalar(
            select(func.count()).select_from(Finding).where(Finding.severity == "Low")
        )
        or 0,
        informational_findings=db.scalar(
            select(func.count()).select_from(Finding).where(Finding.severity == "Informational")
        )
        or 0,
        certificate_issues=db.scalar(
            select(func.count()).select_from(Finding).where(Finding.rule_key.in_(CERTIFICATE_ISSUE_RULE_KEYS))
        )
        or 0,
        reports=db.scalar(select(func.count()).select_from(Report)) or 0,
        scans_running=db.scalar(
            select(func.count()).select_from(ScanJob).where(ScanJob.status == "running")
        )
        or 0,
        scans_total=db.scalar(select(func.count()).select_from(ScanJob)) or 0,
    )
