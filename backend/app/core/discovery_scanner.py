from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.risk_engine import refresh_generated_findings
from app.core.runtime import get_runtime_paths
from app.core.security import normalize_targets, validate_requested_targets_against_scope
from app.models import Assessment, Host, ScanJob, Scope

COMMON_LIVENESS_PORTS = (22, 80, 443, 445, 3389, 8080, 8443)
LOG_RETENTION = 60
MAX_DISCOVERY_TARGETS = {
    "Light": 256,
    "Standard": 512,
    "Deep": 1024,
}
MAC_PATTERN = re.compile(r"((?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})")
LOCAL_VENDOR_MAP = {
    "00:0C:29": "VMware",
    "00:15:5D": "Microsoft Hyper-V",
    "00:1B:63": "Apple",
    "00:50:56": "VMware",
    "08:00:27": "Oracle VirtualBox",
    "3C:52:82": "Hewlett Packard",
    "40:B0:34": "Cisco Meraki",
    "44:38:39": "Ubiquiti",
    "B8:27:EB": "Raspberry Pi",
    "DC:A6:32": "Raspberry Pi",
    "F4:F5:D8": "Cisco Systems",
}


@dataclass(frozen=True)
class DiscoveryProfile:
    name: str
    ping_timeout_ms: int
    tcp_timeout_seconds: float
    host_delay_seconds: float
    fallback_ports: tuple[int, ...]
    max_targets: int


@dataclass(frozen=True)
class DiscoveryObservation:
    status: str
    hostname: str
    mac_address: str
    vendor_name: str
    device_type: str
    discovery_method: str
    notes: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def profile_for_intensity(intensity: str) -> DiscoveryProfile:
    normalized = intensity.strip().title() if intensity else "Standard"
    if normalized == "Light":
        return DiscoveryProfile(
            name="Light",
            ping_timeout_ms=450,
            tcp_timeout_seconds=0.35,
            host_delay_seconds=0.16,
            fallback_ports=(80, 443, 445),
            max_targets=MAX_DISCOVERY_TARGETS["Light"],
        )
    if normalized == "Deep":
        return DiscoveryProfile(
            name="Deep",
            ping_timeout_ms=950,
            tcp_timeout_seconds=0.65,
            host_delay_seconds=0.04,
            fallback_ports=COMMON_LIVENESS_PORTS,
            max_targets=MAX_DISCOVERY_TARGETS["Deep"],
        )
    return DiscoveryProfile(
        name="Standard",
        ping_timeout_ms=700,
        tcp_timeout_seconds=0.5,
        host_delay_seconds=0.08,
        fallback_ports=(80, 443, 445, 22, 3389),
        max_targets=MAX_DISCOVERY_TARGETS["Standard"],
    )


def expand_discovery_targets(
    requested_targets: list[str],
    *,
    excluded_ips: list[str],
    limit: int,
) -> list[str]:
    expanded: list[str] = []
    excluded = {value.strip().lower() for value in excluded_ips if value.strip()}
    seen: set[str] = set()

    for target in normalize_targets(requested_targets):
        if "/" not in target:
            lowered = target.lower()
            if lowered not in excluded and lowered not in seen:
                expanded.append(target)
                seen.add(lowered)
            if len(expanded) > limit:
                raise ValueError(
                    f"Discovery scope exceeds the safe MVP limit of {limit} targets. Narrow the target list before running discovery."
                )
            continue

        network = ipaddress.ip_network(target, strict=False)
        addresses = network.hosts()
        if network.num_addresses <= 2:
            addresses = network

        for address in addresses:
            rendered = str(address)
            lowered = rendered.lower()
            if lowered in excluded or lowered in seen:
                continue
            expanded.append(rendered)
            seen.add(lowered)
            if len(expanded) > limit:
                raise ValueError(
                    f"Discovery scope exceeds the safe MVP limit of {limit} targets. Narrow the target list before running discovery."
                )

    return expanded


def ping_host(address: str, timeout_ms: int) -> bool:
    if sys.platform.startswith("win"):
        command = ["ping", "-n", "1", "-w", str(timeout_ms), address]
        process_timeout = max(2, int(timeout_ms / 1000) + 2)
    else:
        command = ["ping", "-c", "1", "-W", str(max(1, round(timeout_ms / 1000))), address]
        process_timeout = max(2, int(timeout_ms / 1000) + 2)

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=process_timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

    output = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode == 0:
        return True
    return "ttl=" in output or "bytes from" in output


def tcp_connect_liveness(
    address: str,
    ports: tuple[int, ...],
    timeout_seconds: float,
) -> tuple[bool, str]:
    for port in ports:
        try:
            with socket.create_connection((address, port), timeout=timeout_seconds):
                return True, f"tcp-connect:{port}"
        except ConnectionRefusedError:
            return True, f"tcp-refused:{port}"
        except OSError as exc:
            if getattr(exc, "errno", None) in {61, 111, 10061}:
                return True, f"tcp-refused:{port}"
            continue
    return False, ""


def reverse_dns_lookup(address: str) -> str:
    try:
        return socket.gethostbyaddr(address)[0]
    except (socket.herror, socket.gaierror, OSError):
        return ""


def normalize_mac_address(raw_value: str) -> str:
    return raw_value.replace("-", ":").upper()


def arp_lookup(address: str) -> str:
    commands = (
        ["arp", "-a", address],
        ["arp", "-n", address],
        ["ip", "neigh", "show", address],
    )
    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

        output = f"{result.stdout}\n{result.stderr}"
        match = MAC_PATTERN.search(output)
        if match:
            return normalize_mac_address(match.group(1))
    return ""


def vendor_for_mac(mac_address: str) -> str:
    if not mac_address:
        return ""
    prefix = mac_address[:8]
    return LOCAL_VENDOR_MAP.get(prefix, "Unknown vendor")


def classify_device(hostname: str, vendor_name: str, discovery_method: str) -> str:
    hostname_lower = hostname.lower()
    vendor_lower = vendor_name.lower()

    hostname_hints = (
        ("printer", "Printer"),
        ("print", "Printer"),
        ("camera", "Camera"),
        ("cam", "Camera"),
        ("router", "Router"),
        ("gateway", "Router"),
        ("switch", "Switch"),
        ("nas", "NAS"),
        ("server", "Server"),
        ("srv", "Server"),
        ("desktop", "Workstation"),
        ("workstation", "Workstation"),
        ("laptop", "Workstation"),
        ("pc", "Workstation"),
        ("iot", "IoT"),
        ("sensor", "IoT"),
    )
    for hint, device_type in hostname_hints:
        if hint in hostname_lower:
            return device_type

    if any(vendor in vendor_lower for vendor in ("ubiquiti", "cisco meraki")):
        return "Router"
    if any(vendor in vendor_lower for vendor in ("cisco", "juniper", "arista")):
        return "Switch"
    if "raspberry pi" in vendor_lower:
        return "IoT"

    if ":" in discovery_method:
        try:
            liveness_port = int(discovery_method.rsplit(":", 1)[1])
        except ValueError:
            liveness_port = 0

        if liveness_port in {22, 445}:
            return "Server"
        if liveness_port == 3389:
            return "Workstation"
        if liveness_port in {8080, 8443}:
            return "IoT"

    return "Unknown"


def discover_host(
    address: str,
    *,
    previous_status: str,
    profile: DiscoveryProfile,
    enable_tcp_fallback: bool,
    enable_enrichment: bool,
) -> DiscoveryObservation:
    is_live = ping_host(address, profile.ping_timeout_ms)
    discovery_method = "icmp-echo" if is_live else "no-safe-response"

    if not is_live and enable_tcp_fallback:
        tcp_live, tcp_method = tcp_connect_liveness(
            address,
            profile.fallback_ports,
            profile.tcp_timeout_seconds,
        )
        if tcp_live:
            is_live = True
            discovery_method = tcp_method

    hostname = reverse_dns_lookup(address) if enable_enrichment else ""
    mac_address = arp_lookup(address) if enable_enrichment and is_live else ""
    vendor_name = vendor_for_mac(mac_address) if enable_enrichment else ""

    if is_live:
        device_type = classify_device(hostname, vendor_name, discovery_method)
        note = (
            f"Discovery: host responded via {discovery_method} during the {profile.name.lower()} safe discovery profile."
        )
        return DiscoveryObservation(
            status="live",
            hostname=hostname,
            mac_address=mac_address,
            vendor_name=vendor_name,
            device_type=device_type,
            discovery_method=discovery_method,
            notes=note,
        )

    status = "offline" if previous_status == "live" else "unknown"
    note = (
        "Discovery: no safe response observed from ICMP or the configured TCP liveness fallback within the authorized discovery profile."
    )
    return DiscoveryObservation(
        status=status,
        hostname=hostname,
        mac_address="",
        vendor_name="",
        device_type="Unknown",
        discovery_method=discovery_method,
        notes=note,
    )


def append_job_log(scan_job: ScanJob, log_path: Path, message: str) -> None:
    timestamp = utc_now().strftime("%Y-%m-%d %H:%M:%SZ")
    entry = f"[{timestamp}] {message}"
    log_entries = list(scan_job.log_entries or [])
    log_entries.append(entry)
    scan_job.log_entries = log_entries[-LOG_RETENTION:]
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{entry}\n")


def build_summary(
    *,
    total_targets: int,
    processed_targets: int,
    live_hosts: int,
    offline_hosts: int,
    unknown_hosts: int,
) -> dict[str, int]:
    return {
        "total_targets": total_targets,
        "processed_targets": processed_targets,
        "live_hosts": live_hosts,
        "offline_hosts": offline_hosts,
        "unknown_hosts": unknown_hosts,
    }


def upsert_host_record(
    db: Session,
    *,
    assessment_id: int,
    scope_id: int,
    address: str,
    observation: DiscoveryObservation,
) -> Host:
    host = (
        db.query(Host)
        .filter(
            Host.assessment_id == assessment_id,
            Host.scope_id == scope_id,
            Host.address == address,
        )
        .one_or_none()
    )

    if host is None:
        host = Host(
            assessment_id=assessment_id,
            scope_id=scope_id,
            address=address,
        )
        db.add(host)

    host.hostname = observation.hostname or host.hostname
    host.mac_address = observation.mac_address or host.mac_address
    host.vendor_name = observation.vendor_name or host.vendor_name
    host.device_type = observation.device_type or host.device_type
    host.discovery_method = observation.discovery_method
    host.status = observation.status
    host.last_seen_at = utc_now()
    if not host.notes or host.notes.startswith("Discovery:"):
        host.notes = observation.notes
    return host


def mark_job_failed(db: Session, scan_job: ScanJob, log_path: Path, message: str) -> None:
    scan_job.status = "failed"
    scan_job.completed_at = utc_now()
    scan_job.log_path = str(log_path)
    scan_job.progress_message = message
    append_job_log(scan_job, log_path, message)
    db.commit()


def run_safe_discovery(scan_id: int) -> None:
    runtime_paths = get_runtime_paths()
    log_path = runtime_paths.logs_dir / f"safe-discovery-{scan_id}.log"

    with SessionLocal() as db:
        scan_job = db.get(ScanJob, scan_id)
        if scan_job is None:
            return
        scan_job.log_path = str(log_path)

        assessment = db.get(Assessment, scan_job.assessment_id)
        scope = db.get(Scope, scan_job.scope_id)
        if assessment is None or scope is None:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "Discovery job could not load its assessment or scope context.",
            )
            return

        if not scope.is_authorized:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "Discovery jobs can run only against an authorized scope.",
            )
            return

        profile = profile_for_intensity(scan_job.scan_intensity or assessment.scan_intensity)
        requested_targets = normalize_targets(scan_job.requested_targets or scope.included_targets)
        invalid_targets = validate_requested_targets_against_scope(
            requested_targets,
            scope_network_ranges=scope.network_ranges,
            scope_individual_ips=scope.individual_ips,
            scope_excluded_ips=scope.excluded_ips,
        )
        if invalid_targets:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "Discovery job contains targets outside the saved scope or inside the exclusion list.",
            )
            return

        try:
            authorized_targets = expand_discovery_targets(
                requested_targets,
                excluded_ips=scope.excluded_ips,
                limit=profile.max_targets,
            )
        except ValueError as exc:
            mark_job_failed(db, scan_job, log_path, str(exc))
            return

        if not authorized_targets:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "No authorized hosts remained after exclusions were applied.",
            )
            return

        scan_job.log_path = str(log_path)
        scan_job.progress_message = (
            f"Prepared {len(authorized_targets)} authorized targets for {profile.name.lower()} safe discovery."
        )
        append_job_log(
            scan_job,
            log_path,
            f"Starting safe discovery across {len(authorized_targets)} authorized targets with the {profile.name} intensity profile.",
        )
        db.commit()

        live_hosts = 0
        offline_hosts = 0
        unknown_hosts = 0

        for index, address in enumerate(authorized_targets, start=1):
            host = (
                db.query(Host)
                .filter(
                    Host.assessment_id == scan_job.assessment_id,
                    Host.scope_id == scan_job.scope_id,
                    Host.address == address,
                )
                .one_or_none()
            )
            previous_status = host.status if host else "unknown"

            try:
                observation = discover_host(
                    address,
                    previous_status=previous_status,
                    profile=profile,
                    enable_tcp_fallback=scan_job.include_service_detection,
                    enable_enrichment=scan_job.include_safe_checks,
                )
                upsert_host_record(
                    db,
                    assessment_id=scan_job.assessment_id,
                    scope_id=scan_job.scope_id,
                    address=address,
                    observation=observation,
                )
                append_job_log(
                    scan_job,
                    log_path,
                    f"{address}: {observation.status} via {observation.discovery_method or 'no-safe-response'}"
                    + (f" ({observation.hostname})" if observation.hostname else ""),
                )
            except Exception as exc:  # pragma: no cover - defensive per-host isolation
                fallback_status = "offline" if previous_status == "live" else "unknown"
                observation = DiscoveryObservation(
                    status=fallback_status,
                    hostname="",
                    mac_address="",
                    vendor_name="",
                    device_type="Unknown",
                    discovery_method="host-error",
                    notes=f"Discovery: host processing failed with {type(exc).__name__}.",
                )
                upsert_host_record(
                    db,
                    assessment_id=scan_job.assessment_id,
                    scope_id=scan_job.scope_id,
                    address=address,
                    observation=observation,
                )
                append_job_log(
                    scan_job,
                    log_path,
                    f"{address}: host processing failed with {type(exc).__name__}; continuing with remaining targets.",
                )

            if observation.status == "live":
                live_hosts += 1
            elif observation.status == "offline":
                offline_hosts += 1
            else:
                unknown_hosts += 1

            scan_job.progress = min(99, max(1, int(index / len(authorized_targets) * 100)))
            scan_job.progress_message = (
                f"Checked {index} of {len(authorized_targets)} authorized targets."
            )
            scan_job.result_summary = build_summary(
                total_targets=len(authorized_targets),
                processed_targets=index,
                live_hosts=live_hosts,
                offline_hosts=offline_hosts,
                unknown_hosts=unknown_hosts,
            )
            db.commit()

            if index < len(authorized_targets):
                time.sleep(profile.host_delay_seconds)

        scan_job.status = "completed"
        scan_job.progress = 100
        scan_job.completed_at = utc_now()
        scan_job.progress_message = (
            f"Discovery complete. Live: {live_hosts}, offline: {offline_hosts}, unknown: {unknown_hosts}."
        )
        scan_job.result_summary = build_summary(
            total_targets=len(authorized_targets),
            processed_targets=len(authorized_targets),
            live_hosts=live_hosts,
            offline_hosts=offline_hosts,
            unknown_hosts=unknown_hosts,
        )
        generated_findings = refresh_generated_findings(
            db,
            assessment_id=scan_job.assessment_id,
            scope_id=scan_job.scope_id,
        )
        append_job_log(
            scan_job,
            log_path,
            f"Risk engine refreshed {len(generated_findings)} generated findings for the discovered scope.",
        )
        append_job_log(scan_job, log_path, scan_job.progress_message)
        db.commit()
