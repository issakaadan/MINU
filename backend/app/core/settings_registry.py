from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting

ScanIntensity = Literal["Light", "Standard", "Deep"]

SCAN_INTENSITY_OPTIONS: tuple[ScanIntensity, ...] = ("Light", "Standard", "Deep")


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    default: str
    description: str
    kind: Literal["boolean", "enum", "text", "multiline"]
    choices: tuple[str, ...] = ()
    max_length: int = 500
    require_non_empty: bool = False


SETTING_DEFINITIONS: dict[str, SettingDefinition] = {
    "organization_name": SettingDefinition(
        key="organization_name",
        default="Internal Security Team",
        description="Displayed throughout the local assessment platform.",
        kind="text",
        max_length=120,
        require_non_empty=True,
    ),
    "legal_notice": SettingDefinition(
        key="legal_notice",
        default=(
            "Authorized internal use only. Review scope carefully before saving. "
            "Private and local IPv4 targets are allowed by default. Public scope "
            "requires an explicit setting change and a clear confirmation."
        ),
        description="Global legal and safety notice shown inside the application.",
        kind="multiline",
        max_length=1600,
        require_non_empty=True,
    ),
    "default_report_format": SettingDefinition(
        key="default_report_format",
        default="html",
        description="Default report format for newly generated report bundles.",
        kind="enum",
        choices=("html", "pdf"),
    ),
    "default_scan_intensity": SettingDefinition(
        key="default_scan_intensity",
        default="Standard",
        description="Default scan intensity suggested when a new assessment is created.",
        kind="enum",
        choices=SCAN_INTENSITY_OPTIONS,
    ),
    "performance_module_enabled": SettingDefinition(
        key="performance_module_enabled",
        default="false",
        description="Global gate for the disabled-by-default disruptive / performance-impacting module.",
        kind="boolean",
    ),
    "allow_external_scope": SettingDefinition(
        key="allow_external_scope",
        default="false",
        description="Allow external/public scope definitions only with an explicit warning and confirmation.",
        kind="boolean",
    ),
    "report_branding_name": SettingDefinition(
        key="report_branding_name",
        default="Internal Security Team",
        description="Primary branding name shown on executive and technical reports.",
        kind="text",
        max_length=120,
        require_non_empty=True,
    ),
    "report_branding_tagline": SettingDefinition(
        key="report_branding_tagline",
        default="Authorized Network Assessment Platform",
        description="Short report cover tagline used beneath the brand name.",
        kind="text",
        max_length=180,
        require_non_empty=True,
    ),
    "report_branding_contact": SettingDefinition(
        key="report_branding_contact",
        default="Internal Security Team | Authorized internal use only",
        description="Footer or contact line shown in generated reports.",
        kind="text",
        max_length=220,
        require_non_empty=True,
    ),
}


def get_setting_definition(key: str) -> SettingDefinition | None:
    return SETTING_DEFINITIONS.get(key)


def normalize_setting_value(key: str, value: str) -> str:
    definition = get_setting_definition(key)
    if definition is None:
        raise ValueError("Setting not found.")

    normalized = value.strip()

    if definition.kind == "boolean":
        lowered = normalized.lower()
        if lowered not in {"true", "false"}:
            raise ValueError(f"{key} must be either true or false.")
        return lowered

    if definition.kind == "enum":
        matched_value = next(
            (choice for choice in definition.choices if choice.lower() == normalized.lower()),
            None,
        )
        if matched_value is None:
            allowed = ", ".join(definition.choices)
            raise ValueError(f"{key} must be one of: {allowed}.")
        return matched_value

    if definition.require_non_empty and not normalized:
        raise ValueError(f"{key} cannot be empty.")

    if len(normalized) > definition.max_length:
        raise ValueError(
            f"{key} is too long. Keep it under {definition.max_length} characters."
        )

    return normalized


def get_setting_map(db: Session) -> dict[str, str]:
    settings = list(db.scalars(select(Setting).order_by(Setting.key.asc())))
    values = {
        definition.key: definition.default for definition in SETTING_DEFINITIONS.values()
    }
    for setting in settings:
        values[setting.key] = setting.value
    return values


def get_setting_value(db: Session, key: str, fallback: str = "") -> str:
    setting = db.scalar(select(Setting).where(Setting.key == key))
    if setting is None:
        definition = get_setting_definition(key)
        if definition is not None:
            return definition.default
        return fallback
    return setting.value


def setting_enabled(db: Session, key: str) -> bool:
    return get_setting_value(db, key, "false").strip().lower() == "true"
