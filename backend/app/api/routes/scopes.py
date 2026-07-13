import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import api_error
from app.core.security import validate_scope_entries
from app.core.settings_registry import setting_enabled
from app.models import Assessment, Scope, Setting
from app.schemas import ScopeCreate, ScopeRead

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("", response_model=list[ScopeRead])
def list_scopes(db: Session = Depends(get_db)) -> list[Scope]:
    query = select(Scope).order_by(Scope.created_at.desc())
    return list(db.scalars(query))


@router.post("", response_model=ScopeRead, status_code=status.HTTP_201_CREATED)
def create_scope(payload: ScopeCreate, db: Session = Depends(get_db)) -> Scope:
    assessment = db.get(Assessment, payload.assessment_id)
    if not assessment:
        raise api_error(404, "Assessment not found.")

    if not payload.legal_acknowledged:
        raise api_error(
            400,
            "Legal acknowledgement is required before a scope can be saved.",
            field_errors={
                "legal_acknowledged": [
                    "Read and acknowledge the legal notice before saving scope.",
                ]
            },
        )

    allow_external_scope = setting_enabled(db, "allow_external_scope")
    validation = validate_scope_entries(
        payload.network_ranges,
        payload.individual_ips,
        payload.excluded_ips,
        allow_external_scope=allow_external_scope,
        external_scope_confirmed=payload.external_scope_confirmed,
    )

    if not validation.is_valid:
        raise api_error(
            400,
            "Scope validation failed. Review the highlighted targets and try again.",
            field_errors=validation.field_errors,
        )

    scope = Scope(
        assessment_id=payload.assessment_id,
        name=payload.name,
        description=payload.notes,
        targets=validation.included_targets,
        network_ranges=validation.network_ranges,
        individual_ips=validation.individual_ips,
        excluded_ips=validation.excluded_ips,
        has_external_targets=validation.has_external_targets,
        external_scope_confirmed=payload.external_scope_confirmed,
        manual_approved_targets=[],
        authorized_by=assessment.owner,
        approval_reference="Recorded via scope management workflow",
        is_authorized=True,
        enforce_private_targets=not allow_external_scope,
    )
    db.add(scope)
    db.commit()
    db.refresh(scope)
    logger.info(
        "Scope saved for assessment %s with %s included targets and %s excluded targets.",
        payload.assessment_id,
        len(validation.included_targets),
        len(validation.excluded_ips),
    )
    return scope
