import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.disruptive_tests import run_disruptive_tests
from app.core.database import get_db
from app.core.discovery_scanner import run_safe_discovery
from app.core.enumeration_scanner import run_safe_enumeration
from app.core.errors import api_error
from app.core.security import normalize_targets, validate_requested_targets_against_scope
from app.core.settings_registry import setting_enabled
from app.models import Assessment, Host, ScanJob, Scope, utc_now
from app.schemas import ScanJobCreate, ScanJobRead

router = APIRouter()
logger = logging.getLogger(__name__)

DISCOVERY_TARGET_LIMITS = {
    "Light": 256,
    "Standard": 512,
    "Deep": 1024,
}
ENUMERATION_TARGET_LIMITS = {
    "common-tcp": {"Light": 64, "Standard": 128, "Deep": 256},
    "full-tcp": {"Light": 16, "Standard": 32, "Deep": 64},
}
DISRUPTIVE_TARGET_LIMITS = {
    "Light": 4,
    "Standard": 8,
    "Deep": 16,
}
MAX_PENDING_JOBS_PER_ASSESSMENT = 12


def _initial_summary(job_type: str) -> dict[str, int | float | str]:
    if job_type == "disruptive_tests":
        return {
            "total_hosts": 0,
            "processed_hosts": 0,
            "results_recorded": 0,
            "hosts_with_ping": 0,
            "hosts_with_bandwidth": 0,
        }

    if job_type == "safe_enumeration":
        return {
            "total_hosts": 0,
            "processed_hosts": 0,
            "open_ports": 0,
            "services_detected": 0,
        }

    return {
        "total_targets": 0,
        "processed_targets": 0,
        "live_hosts": 0,
        "offline_hosts": 0,
        "unknown_hosts": 0,
    }


def _enforce_target_limit(
    target_count: int,
    *,
    limit: int,
    message: str,
) -> None:
    if target_count > limit:
        raise api_error(400, f"{message} Limit: {limit} targets.")


def _assert_job_capacity(db: Session, assessment_id: int) -> None:
    running_jobs = db.scalar(
        select(func.count())
        .select_from(ScanJob)
        .where(ScanJob.assessment_id == assessment_id, ScanJob.status == "running")
    ) or 0
    if running_jobs > 0:
        raise api_error(
            409,
            "A scan job is already running for this assessment. Wait for it to finish before starting another job.",
        )

    pending_jobs = db.scalar(
        select(func.count())
        .select_from(ScanJob)
        .where(ScanJob.assessment_id == assessment_id, ScanJob.status == "pending")
    ) or 0
    if pending_jobs >= MAX_PENDING_JOBS_PER_ASSESSMENT:
        raise api_error(
            400,
            "Too many pending jobs are already queued for this assessment. Clear older jobs before adding more.",
        )


@router.get("", response_model=list[ScanJobRead])
def list_scans(
    status_filter: str | None = Query(default=None, alias="status"),
    assessment_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[ScanJob]:
    query = select(ScanJob).order_by(ScanJob.created_at.desc())
    if status_filter:
        query = query.where(ScanJob.status == status_filter)
    if assessment_id is not None:
        query = query.where(ScanJob.assessment_id == assessment_id)
    return list(db.scalars(query))


@router.post("", response_model=ScanJobRead, status_code=status.HTTP_201_CREATED)
def create_scan(payload: ScanJobCreate, db: Session = Depends(get_db)) -> ScanJob:
    if payload.include_performance_module and payload.job_type != "disruptive_tests":
        raise api_error(
            400,
            "Performance-impact modules are available only through the separate disruptive tests workflow.",
        )

    if payload.udp_scan_enabled:
        raise api_error(
            400,
            "UDP scanning remains disabled by default in this MVP.",
        )

    if not payload.legal_acknowledged:
        raise api_error(
            400,
            "Legal and safety acknowledgement is required before creating a scan job.",
            field_errors={
                "legal_acknowledged": [
                    "Confirm the legal and safety acknowledgement before creating a scan job."
                ]
            },
        )

    assessment = db.get(Assessment, payload.assessment_id)
    if not assessment:
        raise api_error(404, "Assessment not found.")

    scope = db.get(Scope, payload.scope_id)
    if not scope or scope.assessment_id != payload.assessment_id:
        raise api_error(404, "Scope not found for this assessment.")

    if not scope.is_authorized:
        raise api_error(400, "Scope must be authorized before a scan can be configured.")

    _assert_job_capacity(db, payload.assessment_id)

    if payload.job_type == "disruptive_tests":
        if not payload.include_performance_module:
            raise api_error(
                400,
                "The disruptive / performance-impacting module must be explicitly enabled for this job.",
            )

        if not assessment.allow_disruptive_tests:
            raise api_error(
                400,
                "This assessment has not enabled disruptive / performance-impacting tests.",
            )

        if not setting_enabled(db, "performance_module_enabled"):
            raise api_error(
                400,
                "Enable the disruptive / performance-impacting module in Settings before creating this job.",
            )

        if not payload.warning_acknowledged or not payload.maintenance_window_confirmed:
            raise api_error(
                400,
                (
                    "These tests may affect performance. Run only during an approved maintenance window. "
                    "Explicit warning acknowledgement and maintenance window confirmation are required."
                ),
            )

        discovered_hosts = list(
            db.scalars(
                select(Host).where(
                    Host.assessment_id == payload.assessment_id,
                    Host.scope_id == payload.scope_id,
                )
            )
        )
        if not discovered_hosts:
            raise api_error(
                400,
                "Run safe discovery first. No discovered hosts are available for performance-impacting tests in this scope.",
            )

        requested_targets = normalize_targets(payload.requested_targets)
        if requested_targets and any("/" in target for target in requested_targets):
            raise api_error(
                400,
                "Performance-impacting tests accept discovered host IP addresses only, not network ranges.",
            )

        discovered_by_address = {host.address: host for host in discovered_hosts}
        if requested_targets:
            invalid_targets = validate_requested_targets_against_scope(
                requested_targets,
                scope_network_ranges=scope.network_ranges,
                scope_individual_ips=scope.individual_ips,
                scope_excluded_ips=scope.excluded_ips,
            )
            if invalid_targets:
                raise api_error(
                    400,
                    (
                        "Requested targets must remain inside the approved scope and must not overlap excluded IPs. "
                        f"Invalid targets: {', '.join(invalid_targets)}"
                    ),
                )

            missing_hosts = [
                target for target in requested_targets if target not in discovered_by_address
            ]
            if missing_hosts:
                raise api_error(
                    400,
                    (
                        "Performance-impacting targets must already exist in the discovered host inventory. "
                        f"Missing hosts: {', '.join(missing_hosts)}"
                    ),
                )
        else:
            preferred_hosts = [
                host.address for host in discovered_hosts if host.status in {"live", "unknown"}
            ]
            requested_targets = preferred_hosts or [host.address for host in discovered_hosts]

        _enforce_target_limit(
            len(requested_targets),
            limit=DISRUPTIVE_TARGET_LIMITS[payload.scan_intensity],
            message=(
                f"Performance-impacting tests are tightly rate-limited for {payload.scan_intensity} intensity."
            ),
        )
        profile_name = "performance-impacting"
        progress_message = (
            "Performance-impacting tests are ready to run against discovered hosts in the authorized scope."
        )
    elif payload.job_type == "safe_enumeration":
        discovered_hosts = list(
            db.scalars(
                select(Host).where(
                    Host.assessment_id == payload.assessment_id,
                    Host.scope_id == payload.scope_id,
                )
            )
        )
        if not discovered_hosts:
            raise api_error(
                400,
                "Run safe discovery first. No discovered hosts are available for port and service enumeration in this scope.",
            )

        requested_targets = normalize_targets(payload.requested_targets)
        if requested_targets and any("/" in target for target in requested_targets):
            raise api_error(
                400,
                "Port and service enumeration accepts discovered host IP addresses only, not network ranges.",
            )

        discovered_by_address = {host.address: host for host in discovered_hosts}
        if requested_targets:
            invalid_targets = validate_requested_targets_against_scope(
                requested_targets,
                scope_network_ranges=scope.network_ranges,
                scope_individual_ips=scope.individual_ips,
                scope_excluded_ips=scope.excluded_ips,
            )
            if invalid_targets:
                raise api_error(
                    400,
                    (
                        "Requested targets must remain inside the approved scope and must not overlap excluded IPs. "
                        f"Invalid targets: {', '.join(invalid_targets)}"
                    ),
                )

            missing_hosts = [
                target for target in requested_targets if target not in discovered_by_address
            ]
            if missing_hosts:
                raise api_error(
                    400,
                    (
                        "Enumeration targets must already exist in the discovered host inventory. "
                        f"Missing hosts: {', '.join(missing_hosts)}"
                    ),
                )
        else:
            preferred_hosts = [
                host.address
                for host in discovered_hosts
                if host.status in {"live", "unknown"}
            ]
            requested_targets = preferred_hosts or [host.address for host in discovered_hosts]

        profile_name = (
            payload.profile_name.strip().lower() if payload.profile_name else "common-tcp"
        )
        if profile_name not in {"common-tcp", "full-tcp"}:
            profile_name = "common-tcp"
        _enforce_target_limit(
            len(requested_targets),
            limit=ENUMERATION_TARGET_LIMITS[profile_name][payload.scan_intensity],
            message=(
                f"{'Full' if profile_name == 'full-tcp' else 'Common'} TCP enumeration is rate-limited for {payload.scan_intensity} intensity."
            ),
        )
        progress_message = "Enumeration job is ready to scan discovered hosts within the authorized scope."
    else:
        requested_targets = normalize_targets(payload.requested_targets or scope.included_targets)
        if not requested_targets:
            raise api_error(
                400,
                "No authorized discovery targets are available. Save a scope with included targets before creating a discovery job.",
            )
        invalid_targets = validate_requested_targets_against_scope(
            requested_targets,
            scope_network_ranges=scope.network_ranges,
            scope_individual_ips=scope.individual_ips,
            scope_excluded_ips=scope.excluded_ips,
        )
        if invalid_targets:
            raise api_error(
                400,
                (
                    "Requested targets must remain inside the approved scope and must not overlap excluded IPs. "
                    f"Invalid targets: {', '.join(invalid_targets)}"
                ),
            )
        _enforce_target_limit(
            len(requested_targets),
            limit=DISCOVERY_TARGET_LIMITS[payload.scan_intensity],
            message=(
                f"Safe discovery is rate-limited for {payload.scan_intensity} intensity."
            ),
        )
        profile_name = "safe-discovery"
        progress_message = "Discovery job is ready to run against the saved authorized scope."

    scan_job = ScanJob(
        **payload.model_dump(
            exclude={"job_type", "requested_targets", "profile_name", "scan_intensity"}
        ),
        job_type=payload.job_type,
        status="pending",
        progress=0,
        progress_message=progress_message,
        result_summary=_initial_summary(payload.job_type),
        scan_intensity=payload.scan_intensity or assessment.scan_intensity,
        profile_name=profile_name,
        requested_targets=requested_targets,
    )
    db.add(scan_job)
    db.commit()
    db.refresh(scan_job)
    logger.info(
        "Created %s job %s for assessment %s over scope %s with %s requested targets.",
        scan_job.job_type,
        scan_job.id,
        scan_job.assessment_id,
        scan_job.scope_id,
        len(scan_job.requested_targets),
    )
    return scan_job


@router.post("/{scan_id}/start", response_model=ScanJobRead)
def start_scan(
    scan_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ScanJob:
    scan_job = db.get(ScanJob, scan_id)
    if not scan_job:
        raise api_error(404, "Scan job not found.")

    if scan_job.status == "running":
        raise api_error(400, "This scan job is already running.")

    if scan_job.status == "completed":
        raise api_error(
            400,
            "Completed scan jobs cannot be restarted in the MVP. Create a new job instead.",
        )

    scan_job.status = "running"
    scan_job.progress = 1
    scan_job.progress_message = (
        "Starting performance-impacting checks."
        if scan_job.job_type == "disruptive_tests"
        else
        "Starting safe port and service enumeration."
        if scan_job.job_type == "safe_enumeration"
        else "Starting authorized safe discovery."
    )
    scan_job.started_at = utc_now()
    scan_job.completed_at = None
    scan_job.log_entries = []
    scan_job.result_summary = _initial_summary(scan_job.job_type)
    db.commit()
    db.refresh(scan_job)
    logger.info(
        "Starting %s job %s for assessment %s.",
        scan_job.job_type,
        scan_job.id,
        scan_job.assessment_id,
    )
    if scan_job.job_type == "disruptive_tests":
        background_tasks.add_task(run_disruptive_tests, scan_job.id)
    elif scan_job.job_type == "safe_enumeration":
        background_tasks.add_task(run_safe_enumeration, scan_job.id)
    else:
        background_tasks.add_task(run_safe_discovery, scan_job.id)
    return scan_job
