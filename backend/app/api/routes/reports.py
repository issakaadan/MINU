from pathlib import Path
import logging

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import api_error
from app.core.report_generator import generate_assessment_reports
from app.models import Assessment, Report
from app.schemas import ReportCreate, ReportGenerateRequest, ReportRead

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("", response_model=list[ReportRead])
def list_reports(
    assessment_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[Report]:
    query = select(Report).order_by(Report.created_at.desc())
    if assessment_id is not None:
        query = query.where(Report.assessment_id == assessment_id)
    return list(db.scalars(query))


@router.post("", response_model=ReportRead, status_code=status.HTTP_201_CREATED)
def create_report(payload: ReportCreate, db: Session = Depends(get_db)) -> Report:
    assessment = db.get(Assessment, payload.assessment_id)
    if not assessment:
        raise api_error(404, "Assessment not found.")

    report = Report(**payload.model_dump())
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


@router.post("/generate", response_model=list[ReportRead], status_code=status.HTTP_201_CREATED)
def generate_report_bundle(
    payload: ReportGenerateRequest,
    db: Session = Depends(get_db),
) -> list[Report]:
    assessment = db.get(Assessment, payload.assessment_id)
    if not assessment:
        raise api_error(404, "Assessment not found.")

    try:
        reports = generate_assessment_reports(
            db,
            assessment_id=payload.assessment_id,
            report_type=payload.report_type,
        )
    except ValueError as exc:
        raise api_error(400, str(exc)) from exc
    except RuntimeError as exc:
        raise api_error(500, str(exc)) from exc

    db.commit()
    for report in reports:
        db.refresh(report)
    logger.info(
        "Generated %s report bundle for assessment %s (%s files).",
        payload.report_type,
        payload.assessment_id,
        len(reports),
    )
    return reports


@router.get("/{report_id}/download")
def download_report(report_id: int, db: Session = Depends(get_db)) -> FileResponse:
    report = db.get(Report, report_id)
    if report is None:
        raise api_error(404, "Report not found.")

    if report.status != "generated" or not report.storage_path:
        raise api_error(409, "This report has not been generated yet.")

    file_path = Path(report.storage_path)
    if not file_path.exists():
        raise api_error(404, "Report file is no longer available on disk.")

    media_type = "application/pdf" if report.format == "pdf" else "text/html"
    logger.info("Serving report download %s from %s.", report.id, file_path)
    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=file_path.name,
    )
