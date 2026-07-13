from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Assessment, Finding
from app.schemas import FindingCreate, FindingRead

router = APIRouter()


@router.get("", response_model=list[FindingRead])
def list_findings(
    severity: str | None = Query(default=None),
    assessment_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[Finding]:
    query = select(Finding).order_by(Finding.created_at.desc())
    if severity:
        query = query.where(Finding.severity.ilike(severity.strip()))
    if assessment_id is not None:
        query = query.where(Finding.assessment_id == assessment_id)
    return list(db.scalars(query))


@router.post("", response_model=FindingRead, status_code=status.HTTP_201_CREATED)
def create_finding(payload: FindingCreate, db: Session = Depends(get_db)) -> Finding:
    assessment = db.get(Assessment, payload.assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    finding = Finding(**payload.model_dump())
    db.add(finding)
    db.commit()
    db.refresh(finding)
    return finding
