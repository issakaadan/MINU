from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import DisruptiveTestResult
from app.schemas import DisruptiveTestResultRead

router = APIRouter()


@router.get("", response_model=list[DisruptiveTestResultRead])
def list_disruptive_test_results(
    assessment_id: int | None = Query(default=None),
    scan_job_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[DisruptiveTestResult]:
    query = select(DisruptiveTestResult).order_by(
        DisruptiveTestResult.created_at.desc(),
        DisruptiveTestResult.id.desc(),
    )
    if assessment_id is not None:
        query = query.where(DisruptiveTestResult.assessment_id == assessment_id)
    if scan_job_id is not None:
        query = query.where(DisruptiveTestResult.scan_job_id == scan_job_id)
    return list(db.scalars(query))
