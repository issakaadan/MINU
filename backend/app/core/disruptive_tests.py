from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.discovery_scanner import append_job_log, mark_job_failed
from app.core.runtime import get_runtime_paths
from app.core.security import normalize_targets, validate_requested_targets_against_scope
from app.models import Assessment, DisruptiveTestResult, Host, ScanJob, Scope, Setting, utc_now

PING_LATENCY_PATTERN = re.compile(r"time[=<]?\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)
PING_AVERAGE_PATTERN = re.compile(r"Average\s*=\s*(\d+(?:\.\d+)?)ms", re.IGNORECASE)
CONNECT_PORT_PREFERENCE = [443, 80, 8443, 8080, 22, 445, 3389]
WEB_SERVICE_NAMES = {"http", "https", "elasticsearch"}
WEB_PORTS = {80, 443, 8080, 8443, 9200}


@dataclass(frozen=True)
class PerformanceProfile:
    ping_samples: int
    ping_interval_seconds: float
    ping_timeout_ms: int
    connect_timeout_seconds: float
    max_hosts: int
    bandwidth_byte_limit: int


PROFILES: dict[str, PerformanceProfile] = {
    "Light": PerformanceProfile(
        ping_samples=3,
        ping_interval_seconds=1.2,
        ping_timeout_ms=1200,
        connect_timeout_seconds=2.0,
        max_hosts=8,
        bandwidth_byte_limit=4096,
    ),
    "Standard": PerformanceProfile(
        ping_samples=4,
        ping_interval_seconds=0.8,
        ping_timeout_ms=1500,
        connect_timeout_seconds=2.5,
        max_hosts=12,
        bandwidth_byte_limit=8192,
    ),
    "Deep": PerformanceProfile(
        ping_samples=6,
        ping_interval_seconds=0.5,
        ping_timeout_ms=1800,
        connect_timeout_seconds=3.0,
        max_hosts=16,
        bandwidth_byte_limit=12288,
    ),
}


def profile_for_intensity(intensity: str) -> PerformanceProfile:
    return PROFILES.get(intensity, PROFILES["Standard"])


def build_summary(
    *,
    total_hosts: int,
    processed_hosts: int,
    results_recorded: int,
    hosts_with_ping: int,
    hosts_with_bandwidth: int,
    avg_latency_ms: float | None,
    avg_packet_loss_percent: float | None,
    avg_connect_time_ms: float | None,
) -> dict[str, int | float | str]:
    return {
        "total_hosts": total_hosts,
        "processed_hosts": processed_hosts,
        "results_recorded": results_recorded,
        "hosts_with_ping": hosts_with_ping,
        "hosts_with_bandwidth": hosts_with_bandwidth,
        "avg_latency_ms": round(avg_latency_ms, 2) if avg_latency_ms is not None else 0,
        "avg_packet_loss_percent": round(avg_packet_loss_percent, 2)
        if avg_packet_loss_percent is not None
        else 0,
        "avg_connect_time_ms": round(avg_connect_time_ms, 2)
        if avg_connect_time_ms is not None
        else 0,
        "result_label": "performance-impacting",
    }


def get_runtime_paths_for_scan(scan_job: ScanJob) -> tuple[Path, Path]:
    runtime_paths = get_runtime_paths()
    log_path = runtime_paths.logs_dir / f"disruptive-tests-{scan_job.id}.log"
    result_path = (
        runtime_paths.scan_results_dir / f"performance-impacting-tests-{scan_job.id}.json"
    )
    return log_path, result_path


def choose_connect_port(host: Host) -> int | None:
    open_ports = {port.port_number for port in host.ports}
    for preferred in CONNECT_PORT_PREFERENCE:
        if preferred in open_ports:
            return preferred
    if host.ports:
        return sorted(port.port_number for port in host.ports)[0]
    return CONNECT_PORT_PREFERENCE[0]


def choose_web_endpoint(host: Host) -> tuple[str, int] | None:
    for service in host.services:
        if service.name.lower() in WEB_SERVICE_NAMES and service.port_id is not None:
            port = next((port for port in host.ports if port.id == service.port_id), None)
            if port is None:
                continue
            scheme = "https" if service.name.lower() == "https" or port.port_number in {443, 8443} else "http"
            return scheme, port.port_number

    for port in host.ports:
        if port.port_number in WEB_PORTS:
            scheme = "https" if port.port_number in {443, 8443} else "http"
            return scheme, port.port_number
    return None


def parse_ping_latency(output: str) -> float | None:
    match = PING_LATENCY_PATTERN.search(output)
    if match:
        return float(match.group(1))

    average_match = PING_AVERAGE_PATTERN.search(output)
    if average_match:
        return float(average_match.group(1))
    return None


def ping_once(address: str, timeout_ms: int) -> tuple[bool, float | None]:
    if sys.platform.startswith("win"):
        command = ["ping", "-n", "1", "-w", str(timeout_ms), address]
    else:
        timeout_seconds = max(1, int(timeout_ms / 1000))
        command = ["ping", "-c", "1", "-W", str(timeout_seconds), address]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(4, int(timeout_ms / 1000) + 3),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, None

    output = f"{completed.stdout}\n{completed.stderr}"
    latency = parse_ping_latency(output)
    return completed.returncode == 0, latency


def measure_ping_series(address: str, profile: PerformanceProfile) -> tuple[list[float], int]:
    latencies: list[float] = []
    received = 0
    for attempt_index in range(profile.ping_samples):
        success, latency = ping_once(address, profile.ping_timeout_ms)
        if success:
            received += 1
        if latency is not None:
            latencies.append(latency)
        if attempt_index < profile.ping_samples - 1:
            time.sleep(profile.ping_interval_seconds)
    return latencies, received


def measure_connect_time(
    address: str,
    port: int,
    timeout_seconds: float,
) -> float | None:
    start = time.perf_counter()
    try:
        with socket.create_connection((address, port), timeout=timeout_seconds):
            return (time.perf_counter() - start) * 1000
    except OSError:
        return None


def estimate_bandwidth(
    address: str,
    port: int,
    scheme: str,
    timeout_seconds: float,
    byte_limit: int,
) -> float | None:
    connection: http.client.HTTPConnection | http.client.HTTPSConnection | None = None
    try:
        if scheme == "https":
            context = ssl._create_unverified_context()
            connection = http.client.HTTPSConnection(
                address,
                port,
                timeout=timeout_seconds,
                context=context,
            )
        else:
            connection = http.client.HTTPConnection(address, port, timeout=timeout_seconds)

        start = time.perf_counter()
        connection.request(
            "GET",
            "/",
            headers={
                "Range": f"bytes=0-{max(0, byte_limit - 1)}",
                "Connection": "close",
                "User-Agent": "AuthorizedNetworkAssessment/0.1",
            },
        )
        response = connection.getresponse()
        if response.status >= 500:
            return None

        bytes_read = 0
        while bytes_read < byte_limit:
            chunk = response.read(min(2048, byte_limit - bytes_read))
            if not chunk:
                break
            bytes_read += len(chunk)
        elapsed = time.perf_counter() - start
        if elapsed <= 0 or bytes_read == 0:
            return None
        return (bytes_read * 8 / elapsed) / 1000
    except OSError:
        return None
    finally:
        try:
            connection.close()
        except Exception:
            pass


def persist_result(
    *,
    scan_job: ScanJob,
    assessment: Assessment,
    scope: Scope,
    host: Host,
    ping_samples_sent: int,
    ping_samples_received: int,
    packet_loss_percent: float | None,
    min_latency_ms: float | None,
    avg_latency_ms: float | None,
    max_latency_ms: float | None,
    response_time_comparison_ms: float | None,
    connect_port: int | None,
    connect_time_ms: float | None,
    bandwidth_estimate_kbps: float | None,
    status: str,
    notes: str,
    observation_details: dict[str, object],
) -> DisruptiveTestResult:
    return DisruptiveTestResult(
        assessment_id=assessment.id,
        scope_id=scope.id,
        scan_job_id=scan_job.id,
        host_id=host.id,
        target_host=host.address,
        hostname=host.hostname or "",
        result_label="performance-impacting",
        ping_samples_sent=ping_samples_sent,
        ping_samples_received=ping_samples_received,
        packet_loss_percent=packet_loss_percent,
        min_latency_ms=min_latency_ms,
        avg_latency_ms=avg_latency_ms,
        max_latency_ms=max_latency_ms,
        response_time_comparison_ms=response_time_comparison_ms,
        connect_port=connect_port,
        connect_time_ms=connect_time_ms,
        bandwidth_estimate_kbps=bandwidth_estimate_kbps,
        status=status,
        notes=notes,
        observation_details=observation_details,
        warning_acknowledged=scan_job.warning_acknowledged,
        maintenance_window_confirmed=scan_job.maintenance_window_confirmed,
    )


def run_disruptive_tests(scan_id: int) -> None:
    with SessionLocal() as db:
        scan_job = db.get(ScanJob, scan_id)
        if scan_job is None:
            return

        log_path, result_path = get_runtime_paths_for_scan(scan_job)
        scan_job.log_path = str(log_path)

        assessment = db.get(Assessment, scan_job.assessment_id)
        scope = db.get(Scope, scan_job.scope_id)
        performance_setting = db.scalar(
            select(Setting).where(Setting.key == "performance_module_enabled")
        )

        if assessment is None or scope is None:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "Performance-impacting job could not load its assessment or scope context.",
            )
            return

        if performance_setting is None or performance_setting.value.strip().lower() != "true":
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "The disruptive / performance-impacting module is still disabled globally.",
            )
            return

        if not assessment.allow_disruptive_tests:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "This assessment has not enabled performance-impacting tests.",
            )
            return

        if not scope.is_authorized:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "Performance-impacting tests can run only against an authorized scope.",
            )
            return

        if not (
            scan_job.include_performance_module
            and scan_job.legal_acknowledged
            and scan_job.warning_acknowledged
            and scan_job.maintenance_window_confirmed
        ):
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "Required warning acknowledgement or maintenance window confirmation is missing.",
            )
            return

        profile = profile_for_intensity(scan_job.scan_intensity or assessment.scan_intensity)
        requested_targets = normalize_targets(scan_job.requested_targets)
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
                "Performance-impacting job contains targets outside the saved scope or inside the exclusion list.",
            )
            return

        discovered_hosts = list(
            db.scalars(
                select(Host).where(
                    Host.assessment_id == assessment.id,
                    Host.scope_id == scope.id,
                )
            )
        )
        discovered_by_address = {host.address: host for host in discovered_hosts}

        if requested_targets:
            if any("/" in target for target in requested_targets):
                mark_job_failed(
                    db,
                    scan_job,
                    log_path,
                    "Performance-impacting tests accept discovered host IP addresses only, not network ranges.",
                )
                return
            missing_hosts = [
                target for target in requested_targets if target not in discovered_by_address
            ]
            if missing_hosts:
                mark_job_failed(
                    db,
                    scan_job,
                    log_path,
                    "Performance-impacting tests must target previously discovered hosts inside the authorized scope.",
                )
                return
            eligible_hosts = [discovered_by_address[target] for target in requested_targets]
        else:
            eligible_hosts = [
                host for host in discovered_hosts if host.status in {"live", "unknown"}
            ] or discovered_hosts

        if not eligible_hosts:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "Run safe discovery first. No discovered hosts are available for performance-impacting tests in this scope.",
            )
            return

        eligible_hosts = eligible_hosts[: profile.max_hosts]
        db.query(DisruptiveTestResult).filter(
            DisruptiveTestResult.scan_job_id == scan_job.id
        ).delete()

        scan_job.progress_message = (
            "Warning acknowledged. Running low-rate performance-impacting checks against discovered hosts."
        )
        append_job_log(
            scan_job,
            log_path,
            "These tests may affect performance. Run only during an approved maintenance window.",
        )
        append_job_log(
            scan_job,
            log_path,
            "Maintenance window confirmation recorded. Strict rate limits are active.",
        )
        db.commit()

        recorded_results: list[DisruptiveTestResult] = []
        latency_values: list[float] = []
        packet_loss_values: list[float] = []
        connect_values: list[float] = []
        hosts_with_ping = 0
        hosts_with_bandwidth = 0

        try:
            for index, host in enumerate(eligible_hosts, start=1):
                notes: list[str] = []
                latencies, received = measure_ping_series(host.address, profile)
                ping_samples_sent = profile.ping_samples
                packet_loss_percent = (
                    ((ping_samples_sent - received) / ping_samples_sent) * 100
                    if ping_samples_sent
                    else None
                )
                min_latency_ms = min(latencies) if latencies else None
                avg_latency_ms = (
                    sum(latencies) / len(latencies) if latencies else None
                )
                max_latency_ms = max(latencies) if latencies else None

                if latencies:
                    latency_values.append(avg_latency_ms or 0)
                    hosts_with_ping += 1
                else:
                    notes.append("No ICMP latency samples were available for this host.")

                connect_port = choose_connect_port(host)
                connect_time_ms = (
                    measure_connect_time(
                        host.address,
                        connect_port,
                        profile.connect_timeout_seconds,
                    )
                    if connect_port is not None
                    else None
                )
                if connect_port is None:
                    notes.append("No candidate port was available for controlled connection timing.")
                elif connect_time_ms is None:
                    notes.append(
                        f"Controlled connection timing did not complete on port {connect_port}."
                    )
                else:
                    connect_values.append(connect_time_ms)

                response_time_comparison_ms = (
                    abs((avg_latency_ms or 0) - connect_time_ms)
                    if avg_latency_ms is not None and connect_time_ms is not None
                    else None
                )

                bandwidth_estimate_kbps: float | None = None
                web_endpoint = choose_web_endpoint(host)
                if web_endpoint is not None:
                    scheme, web_port = web_endpoint
                    bandwidth_estimate_kbps = estimate_bandwidth(
                        host.address,
                        web_port,
                        scheme,
                        profile.connect_timeout_seconds,
                        profile.bandwidth_byte_limit,
                    )
                    if bandwidth_estimate_kbps is not None:
                        hosts_with_bandwidth += 1
                    else:
                        notes.append(
                            f"Bandwidth estimate was skipped or unavailable on {scheme.upper()} port {web_port}."
                        )
                else:
                    notes.append("No safe HTTP or HTTPS endpoint was available for a bandwidth estimate.")

                if packet_loss_percent is not None:
                    packet_loss_values.append(packet_loss_percent)

                status = "completed"
                if (
                    avg_latency_ms is None
                    and connect_time_ms is None
                    and bandwidth_estimate_kbps is None
                ):
                    status = "failed"
                elif avg_latency_ms is None or connect_time_ms is None:
                    status = "partial"

                result = persist_result(
                    scan_job=scan_job,
                    assessment=assessment,
                    scope=scope,
                    host=host,
                    ping_samples_sent=ping_samples_sent,
                    ping_samples_received=received,
                    packet_loss_percent=packet_loss_percent,
                    min_latency_ms=min_latency_ms,
                    avg_latency_ms=avg_latency_ms,
                    max_latency_ms=max_latency_ms,
                    response_time_comparison_ms=response_time_comparison_ms,
                    connect_port=connect_port,
                    connect_time_ms=connect_time_ms,
                    bandwidth_estimate_kbps=bandwidth_estimate_kbps,
                    status=status,
                    notes=" ".join(notes),
                    observation_details={
                        "ping_interval_seconds": profile.ping_interval_seconds,
                        "ping_timeout_ms": profile.ping_timeout_ms,
                        "connect_timeout_seconds": profile.connect_timeout_seconds,
                        "bandwidth_byte_limit": profile.bandwidth_byte_limit,
                        "safe_web_endpoint": {
                            "scheme": web_endpoint[0],
                            "port": web_endpoint[1],
                        }
                        if web_endpoint is not None
                        else None,
                    },
                )
                db.add(result)
                db.flush()
                recorded_results.append(result)

                scan_job.progress = int(index / len(eligible_hosts) * 100)
                scan_job.progress_message = (
                    f"Performance-impacting checks recorded for {host.address}. "
                    f"Processed {index} of {len(eligible_hosts)} discovered hosts."
                )
                scan_job.result_summary = build_summary(
                    total_hosts=len(eligible_hosts),
                    processed_hosts=index,
                    results_recorded=len(recorded_results),
                    hosts_with_ping=hosts_with_ping,
                    hosts_with_bandwidth=hosts_with_bandwidth,
                    avg_latency_ms=sum(latency_values) / len(latency_values)
                    if latency_values
                    else None,
                    avg_packet_loss_percent=sum(packet_loss_values) / len(packet_loss_values)
                    if packet_loss_values
                    else None,
                    avg_connect_time_ms=sum(connect_values) / len(connect_values)
                    if connect_values
                    else None,
                )
                append_job_log(
                    scan_job,
                    log_path,
                    (
                        f"{host.address}: ping {received}/{ping_samples_sent}, "
                        f"connect {connect_port or 'n/a'}="
                        f"{round(connect_time_ms, 2) if connect_time_ms is not None else 'unavailable'} ms, "
                        f"bandwidth="
                        f"{round(bandwidth_estimate_kbps, 2) if bandwidth_estimate_kbps is not None else 'unavailable'} kbps."
                    ),
                )
                db.commit()

            artifact_payload = {
                "scan_job_id": scan_job.id,
                "assessment_id": assessment.id,
                "scope_id": scope.id,
                "result_label": "performance-impacting",
                "generated_at": utc_now().isoformat(),
                "summary": scan_job.result_summary,
                "results": [
                    {
                        "target_host": result.target_host,
                        "hostname": result.hostname,
                        "status": result.status,
                        "ping_samples_sent": result.ping_samples_sent,
                        "ping_samples_received": result.ping_samples_received,
                        "packet_loss_percent": result.packet_loss_percent,
                        "avg_latency_ms": result.avg_latency_ms,
                        "connect_port": result.connect_port,
                        "connect_time_ms": result.connect_time_ms,
                        "bandwidth_estimate_kbps": result.bandwidth_estimate_kbps,
                        "notes": result.notes,
                    }
                    for result in recorded_results
                ],
            }
            result_path.write_text(json.dumps(artifact_payload, indent=2), encoding="utf-8")

            scan_job.status = "completed"
            scan_job.completed_at = utc_now()
            scan_job.progress = 100
            scan_job.progress_message = (
                f"Performance-impacting checks completed for {len(recorded_results)} discovered hosts."
            )
            scan_job.result_summary = {
                **scan_job.result_summary,
                "result_artifact": str(result_path),
            }
            append_job_log(scan_job, log_path, scan_job.progress_message)
            db.commit()
        except Exception as exc:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                f"Performance-impacting checks stopped early: {exc}",
            )
