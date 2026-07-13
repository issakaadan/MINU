import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import api_error
from app.core.settings_registry import get_setting_definition, normalize_setting_value
from app.models import Setting
from app.schemas import SettingBase, SettingRead

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("", response_model=list[SettingRead])
def list_settings(db: Session = Depends(get_db)) -> list[Setting]:
    query = select(Setting).order_by(Setting.key.asc())
    return list(db.scalars(query))


@router.put("/{key}", response_model=SettingRead)
def update_setting(key: str, payload: SettingBase, db: Session = Depends(get_db)) -> Setting:
    setting = db.scalar(select(Setting).where(Setting.key == key))
    if not setting:
        raise api_error(404, "Setting not found.")

    definition = get_setting_definition(key)
    if definition is None:
        raise api_error(404, "Setting metadata was not found.")

    try:
        normalized_value = normalize_setting_value(key, payload.value)
    except ValueError as exc:
        raise api_error(
            400,
            str(exc),
            field_errors={key: [str(exc)]},
        )
    previous_value = setting.value

    setting.value = normalized_value
    setting.description = payload.description or definition.description
    db.commit()
    db.refresh(setting)
    if previous_value != normalized_value:
        logger.info(
            "Setting updated: %s changed from %s to %s",
            key,
            previous_value,
            normalized_value,
        )
        if key in {"allow_external_scope", "performance_module_enabled"} and normalized_value == "true":
            logger.warning(
                "High-impact setting enabled: %s was switched on by a local operator.",
                key,
            )
    return setting
