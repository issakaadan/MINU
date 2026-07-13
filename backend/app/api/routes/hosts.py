from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.models import Assessment, Host
from app.schemas import HostCreate, HostRead

router = APIRouter()


@router.get("", response_model=list[HostRead])
def list_hosts(
    assessment_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[Host]:
    query = (
        select(Host)
        .options(selectinload(Host.ports), selectinload(Host.services))
        .order_by(Host.last_seen_at.desc(), Host.created_at.desc())
    )
    if assessment_id is not None:
        query = query.where(Host.assessment_id == assessment_id)
    return list(db.scalars(query))


@router.post("", response_model=HostRead, status_code=status.HTTP_201_CREATED)
def create_host(payload: HostCreate, db: Session = Depends(get_db)) -> Host:
    assessment = db.get(Assessment, payload.assessment_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    host = Host(**payload.model_dump())
    db.add(host)
    db.commit()
    db.refresh(host)
    return host
