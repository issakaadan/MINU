from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from itertools import islice
import re
import socket
import ssl
import time
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.core.database import SessionLocal
from app.core.discovery_scanner import append_job_log, mark_job_failed, utc_now
from app.core.risk_engine import refresh_generated_findings
from app.core.security import normalize_targets, validate_requested_targets_against_scope
from app.core.web_checker import run_web_checks
from app.models import Host, Port, ScanJob, Scope, Service

COMMON_TCP_PORTS = (
    21,
    22,
    23,
    25,
    53,
    80,
    110,
    139,
    143,
    443,
    445,
    587,
    993,
    995,
    1433,
    1521,
    3306,
    3389,
    5432,
    5900,
    6379,
    8080,
    8443,
    9200,
    9300,
)

PORT_HINTS: dict[int, tuple[str, str]] = {
    21: ("ftp", "FTP"),
    22: ("ssh", "SSH"),
    23: ("telnet", "Telnet"),
    25: ("smtp", "SMTP"),
    53: ("dns", "DNS"),
    80: ("http", "HTTP"),
    110: ("pop3", "POP3"),
    139: ("smb", "SMB"),
    143: ("imap", "IMAP"),
    443: ("https", "HTTPS"),
    445: ("smb", "SMB"),
    587: ("smtp", "SMTP Submission"),
    993: ("imaps", "IMAP over TLS"),
    995: ("pop3s", "POP3 over TLS"),
    1433: ("mssql", "Microsoft SQL Server"),
    1521: ("oracle", "Oracle Database"),
    3306: ("mysql", "MySQL"),
    3389: ("rdp", "Remote Desktop"),
    5432: ("postgresql", "PostgreSQL"),
    5900: ("vnc", "VNC"),
    6379: ("redis", "Redis"),
    8080: ("http", "HTTP Alternate"),
    8443: ("https", "HTTPS Alternate"),
    9200: ("elasticsearch", "Elasticsearch"),
    9300: ("elasticsearch", "Elasticsearch Transport"),
}
ADMIN_PAGE_HINTS = (
    "admin",
    "login",
    "dashboard",
    "management",
    "control panel",
    "console",
)
TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
WEB_HTTP_PORTS = {80, 8080, 8000, 3000, 5000, 5173, 9200}
WEB_HTTPS_PORTS = {443, 8443, 9443, 10443}


@dataclass(frozen=True)
class EnumerationProfile:
    name: str
    connect_timeout_seconds: float
    banner_timeout_seconds: float
    concurrent_connections: int
    batch_size: int
    inter_batch_delay_seconds: float
    max_socket_checks: int


@dataclass(frozen=True)
class PortObservation:
    port_number: int
    protocol: str
    state: str
    service_name: str
    product: str
    version: str
    banner: str
    observations: dict[str, Any]
    confidence: float


def profile_for_intensity(intensity: str) -> EnumerationProfile:
    normalized = intensity.strip().title() if intensity else "Standard"
    if normalized == "Light":
        return EnumerationProfile(
            name="Light",
            connect_timeout_seconds=0.28,
            banner_timeout_seconds=0.32,
            concurrent_connections=12,
            batch_size=96,
            inter_batch_delay_seconds=0.08,
            max_socket_checks=131070,
        )
    if normalized == "Deep":
        return EnumerationProfile(
            name="Deep",
            connect_timeout_seconds=0.12,
            banner_timeout_seconds=0.2,
            concurrent_connections=48,
            batch_size=384,
            inter_batch_delay_seconds=0.015,
            max_socket_checks=524280,
        )
    return EnumerationProfile(
        name="Standard",
        connect_timeout_seconds=0.18,
        banner_timeout_seconds=0.25,
        concurrent_connections=24,
        batch_size=192,
        inter_batch_delay_seconds=0.04,
        max_socket_checks=262140,
    )


def iter_batches(values: list[int], batch_size: int):
    iterator = iter(values)
    while batch := list(islice(iterator, batch_size)):
        yield batch


def ports_for_profile(profile_name: str) -> list[int]:
    normalized = profile_name.strip().lower()
    if normalized == "full-tcp":
        return list(range(1, 65536))
    return list(COMMON_TCP_PORTS)


def resolve_enumeration_hosts(
    db: Session,
    *,
    scan_job: ScanJob,
    scope: Scope,
) -> list[Host]:
    available_hosts = list(
        db.query(Host)
        .options(selectinload(Host.ports), selectinload(Host.services))
        .filter(
            Host.assessment_id == scan_job.assessment_id,
            Host.scope_id == scan_job.scope_id,
        )
        .order_by(Host.last_seen_at.desc(), Host.created_at.desc())
    )
    if not available_hosts:
        raise ValueError(
            "No discovered hosts are available in this authorized scope. Run safe discovery before port and service enumeration."
        )

    requested_targets = normalize_targets(scan_job.requested_targets)
    invalid_targets = validate_requested_targets_against_scope(
        requested_targets or scope.included_targets,
        scope_network_ranges=scope.network_ranges,
        scope_individual_ips=scope.individual_ips,
        scope_excluded_ips=scope.excluded_ips,
    )
    if invalid_targets:
        raise ValueError(
            "Enumeration targets must remain inside the approved scope and outside the exclusion list."
        )

    hosts_by_address = {host.address: host for host in available_hosts}
    if requested_targets:
        if any("/" in target for target in requested_targets):
            raise ValueError(
                "Port and service enumeration accepts discovered host IP addresses only, not network ranges."
            )

        selected_hosts: list[Host] = []
        missing_hosts: list[str] = []
        for target in requested_targets:
            host = hosts_by_address.get(target)
            if host is None:
                missing_hosts.append(target)
                continue
            selected_hosts.append(host)

        if missing_hosts:
            raise ValueError(
                "Enumeration targets must already exist in the discovered host inventory. Missing: "
                + ", ".join(missing_hosts)
            )
        return selected_hosts

    preferred_hosts = [
        host for host in available_hosts if host.status in {"live", "unknown"}
    ]
    return preferred_hosts or available_hosts


def is_tcp_port_open(address: str, port: int, timeout_seconds: float) -> bool:
    try:
        with socket.create_connection((address, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def scan_open_tcp_ports(
    address: str,
    ports: list[int],
    profile: EnumerationProfile,
) -> list[int]:
    open_ports: list[int] = []
    for batch in iter_batches(ports, profile.batch_size):
        with ThreadPoolExecutor(
            max_workers=profile.concurrent_connections,
            thread_name_prefix="safe-enum",
        ) as executor:
            results = list(
                executor.map(
                    lambda port: (port, is_tcp_port_open(address, port, profile.connect_timeout_seconds)),
                    batch,
                )
            )
        open_ports.extend(
            sorted(port for port, is_open in results if is_open)
        )
        if len(batch) == profile.batch_size:
            time.sleep(profile.inter_batch_delay_seconds)
    return open_ports


def decode_bytes(payload: bytes) -> str:
    return payload.decode("utf-8", errors="ignore").strip()


def read_socket_payload(connection: socket.socket, limit: int = 4096) -> bytes:
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = connection.recv(min(1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
        if len(chunk) < 1024:
            break
    return b"".join(chunks)


def receive_initial_banner(address: str, port: int, timeout_seconds: float) -> str:
    try:
        with socket.create_connection((address, port), timeout=timeout_seconds) as connection:
            connection.settimeout(timeout_seconds)
            return decode_bytes(read_socket_payload(connection, limit=512))
    except OSError:
        return ""


def flatten_certificate_name(entries: Any) -> str:
    parts: list[str] = []
    if not isinstance(entries, tuple):
        return ""
    for section in entries:
        if not isinstance(section, tuple):
            continue
        for key, value in section:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def inspect_tls_certificate(connection: ssl.SSLSocket) -> dict[str, Any]:
    try:
        certificate = connection.getpeercert()
    except ssl.SSLError:
        return {}

    if not certificate:
        return {}

    not_after = str(certificate.get("notAfter", ""))
    expired = False
    if not_after:
        try:
            expired = ssl.cert_time_to_seconds(not_after) < time.time()
        except (TypeError, ValueError):
            expired = False

    subject = flatten_certificate_name(certificate.get("subject"))
    issuer = flatten_certificate_name(certificate.get("issuer"))
    return {
        "subject": subject,
        "issuer": issuer,
        "not_after": not_after,
        "expired": expired,
        "self_signed": bool(subject and issuer and subject == issuer),
    }


def parse_http_response(response_text: str, *, secure_transport: bool) -> dict[str, Any]:
    if not response_text:
        return {}

    header_text, _, body = response_text.partition("\r\n\r\n")
    if not body:
        header_text, _, body = response_text.partition("\n\n")

    lines = [line.strip() for line in header_text.splitlines() if line.strip()]
    status_line = lines[0] if lines else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    title_match = TITLE_PATTERN.search(body)
    page_title = " ".join(title_match.group(1).split()) if title_match else ""
    body_snippet = body[:1200]
    page_fingerprint = f"{page_title} {body_snippet}".lower()
    is_admin_page = any(keyword in page_fingerprint for keyword in ADMIN_PAGE_HINTS)
    missing_headers = [
        header for header in HTTP_SECURITY_HEADERS if header not in headers
    ]
    if secure_transport and "strict-transport-security" not in headers:
        missing_headers.append("strict-transport-security")

    return {
        "status_line": status_line,
        "headers": headers,
        "page_title": page_title,
        "body_snippet": body_snippet[:800],
        "is_admin_page": is_admin_page,
        "missing_security_headers": missing_headers,
        "redirect_location": headers.get("location", ""),
        "server": headers.get("server", ""),
    }


def summarize_http_banner(response_text: str, http_observations: dict[str, Any]) -> str:
    parts = [
        str(http_observations.get("status_line", "")).strip(),
        str(http_observations.get("server", "")).strip(),
        str(http_observations.get("page_title", "")).strip(),
    ]
    summary = " | ".join(part for part in parts if part)
    if not summary:
        summary = response_text[:500]
    return summary[:500]


def http_probe(address: str, port: int, timeout_seconds: float) -> tuple[str, dict[str, Any]]:
    request = (
        f"GET / HTTP/1.0\r\nHost: {address}\r\nUser-Agent: AuthorizedNetworkAssessment\r\nConnection: close\r\n\r\n"
    ).encode("ascii", errors="ignore")
    try:
        with socket.create_connection((address, port), timeout=timeout_seconds) as connection:
            connection.settimeout(timeout_seconds)
            connection.sendall(request)
            response_text = decode_bytes(read_socket_payload(connection))
        http_observations = parse_http_response(
            response_text,
            secure_transport=False,
        )
        return summarize_http_banner(response_text, http_observations), {
            "http": http_observations,
        }
    except OSError:
        return "", {}


def https_probe(address: str, port: int, timeout_seconds: float) -> tuple[str, dict[str, Any]]:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    request = (
        f"GET / HTTP/1.0\r\nHost: {address}\r\nUser-Agent: AuthorizedNetworkAssessment\r\nConnection: close\r\n\r\n"
    ).encode("ascii", errors="ignore")
    try:
        with socket.create_connection((address, port), timeout=timeout_seconds) as raw_connection:
            raw_connection.settimeout(timeout_seconds)
            with context.wrap_socket(raw_connection, server_hostname=address) as tls_connection:
                tls_connection.settimeout(timeout_seconds)
                tls_connection.sendall(request)
                response_text = decode_bytes(read_socket_payload(tls_connection))
                tls_observations = inspect_tls_certificate(tls_connection)
        http_observations = parse_http_response(
            response_text,
            secure_transport=True,
        )
        observations = {"http": http_observations}
        if tls_observations:
            observations["tls"] = tls_observations
        return summarize_http_banner(response_text, http_observations), observations
    except OSError:
        return "", {}


def parse_http_metadata(http_observations: dict[str, Any], banner: str) -> tuple[str, str, str]:
    server_header = str(http_observations.get("server", "")).strip()
    first_line = str(http_observations.get("status_line", "")).strip()
    page_title = str(http_observations.get("page_title", "")).strip()

    version = ""
    product = server_header or page_title or "HTTP Service"
    match = re.search(r"/([0-9][^\s]*)", server_header)
    if match:
        version = match.group(1)

    if "x-elastic-product" in str(http_observations.get("headers", {})).lower():
        product = "Elasticsearch"
    elif page_title:
        product = page_title

    return first_line, product, version


def observations_for_banner(
    observations: dict[str, Any],
    section: str,
) -> dict[str, Any]:
    value = observations.get(section, {})
    return value if isinstance(value, dict) else {}


def parse_version_from_banner(banner: str) -> str:
    match = re.search(r"([0-9]+(?:\.[0-9A-Za-z_-]+)+)", banner)
    return match.group(1) if match else ""


def attempt_web_check(
    address: str,
    port: int,
    *,
    hinted_name: str,
    timeout_seconds: float,
) -> tuple[str, dict[str, Any]] | None:
    attempts: list[bool] = []
    normalized_hint = hinted_name.strip().lower()

    if normalized_hint in {"http", "elasticsearch"} or port in WEB_HTTP_PORTS:
        attempts.append(False)
    if normalized_hint == "https" or port in WEB_HTTPS_PORTS:
        attempts.append(True)
    if normalized_hint == "unknown":
        if False not in attempts:
            attempts.append(False)
        if port in WEB_HTTPS_PORTS and True not in attempts:
            attempts.append(True)

    for use_https in attempts:
        result = run_web_checks(
            address,
            port,
            use_https=use_https,
            timeout_seconds=timeout_seconds,
        )
        if result is None:
            continue
        return result.summary_banner, result.to_observation_payload()

    return None


def infer_service_observation(
    address: str,
    port: int,
    *,
    enable_service_detection: bool,
    enable_banner_grab: bool,
    timeout_seconds: float,
) -> tuple[str, str, str, str, dict[str, Any], float]:
    hinted_name, hinted_product = PORT_HINTS.get(port, ("unknown", "Unknown"))
    if not enable_service_detection:
        return hinted_name, hinted_product, "", "", {}, 0.35

    banner = ""
    product = hinted_product
    service_name = hinted_name
    version = ""
    observations: dict[str, Any] = {}
    confidence = 0.55

    if enable_banner_grab:
        web_result = attempt_web_check(
            address,
            port,
            hinted_name=hinted_name,
            timeout_seconds=timeout_seconds,
        )
        if web_result is not None:
            banner, observations = web_result
        else:
            banner = receive_initial_banner(address, port, timeout_seconds)

    banner_lower = banner.lower()
    if service_name == "ssh" or banner.startswith("SSH-"):
        service_name = "ssh"
        product = "SSH"
        version = parse_version_from_banner(banner)
        confidence = 0.9 if banner else 0.7
    elif service_name == "ftp" or banner.startswith("220") and "ftp" in banner_lower:
        service_name = "ftp"
        product = "FTP"
        version = parse_version_from_banner(banner)
        if "anonymous" in banner_lower and any(
            token in banner_lower for token in ("allowed", "enabled", "okay", "ok")
        ):
            observations["ftp"] = {"anonymous_access_detected": True}
        confidence = 0.85 if banner else 0.65
    elif service_name == "telnet":
        product = "Telnet"
        confidence = 0.7 if banner else 0.55
    elif service_name in {"http", "https", "elasticsearch"} or "web" in observations:
        if banner:
            web_observations = observations_for_banner(observations, "web")
            http_observations = observations_for_banner(observations, "http")
            _, parsed_product, parsed_version = parse_http_metadata(
                http_observations,
                banner,
            )
            product = (
                str(web_observations.get("server_header", "")).strip()
                or parsed_product
                or hinted_product
            )
            version = parsed_version
            technology_hints = [
                hint.lower()
                for hint in web_observations.get("technology_hints", [])
                if isinstance(hint, str)
            ]
            if (
                "elasticsearch" in banner_lower
                or port in {9200, 9300}
                or any("elasticsearch" in hint for hint in technology_hints)
            ):
                service_name = "elasticsearch"
                product = "Elasticsearch"
            elif bool(web_observations.get("https_in_use")):
                service_name = "https"
            else:
                service_name = "http"
            confidence = 0.9
        else:
            confidence = 0.6
    elif service_name == "smb":
        product = "SMB"
        if any(token in banner_lower for token in ("smbv1", "smb1", "nt lm 0.12")):
            observations["smb"] = {"smbv1_detected": True}
        confidence = 0.65
    elif service_name == "rdp":
        product = "Remote Desktop"
        confidence = 0.65
    elif service_name in {"mssql", "oracle", "mysql", "postgresql"}:
        product = PORT_HINTS[port][1]
        if banner:
            version = parse_version_from_banner(banner)
        confidence = 0.8 if banner else 0.65
    elif service_name == "redis":
        product = "Redis"
        confidence = 0.6
    elif service_name == "vnc" or banner.startswith("RFB"):
        service_name = "vnc"
        product = "VNC"
        version = parse_version_from_banner(banner)
        confidence = 0.9 if banner else 0.65
    elif service_name == "smtp":
        product = "SMTP"
        version = parse_version_from_banner(banner)
        confidence = 0.85 if banner else 0.6
    elif service_name == "pop3":
        product = "POP3"
        confidence = 0.8 if banner else 0.6
    elif service_name == "imap":
        product = "IMAP"
        confidence = 0.8 if banner else 0.6
    elif service_name == "dns":
        product = "DNS"
        confidence = 0.5
    else:
        service_name = "unknown"
        product = "Unknown"
        confidence = 0.3 if not banner else 0.5

    if banner and not version:
        version = parse_version_from_banner(banner)

    return service_name, product, version, banner[:500], observations, confidence


def enumerate_host_services(
    address: str,
    ports: list[int],
    *,
    profile: EnumerationProfile,
    enable_service_detection: bool,
    enable_banner_grab: bool,
) -> list[PortObservation]:
    open_ports = scan_open_tcp_ports(address, ports, profile)
    observations: list[PortObservation] = []
    for port in open_ports:
        service_name, product, version, banner, observed_details, confidence = infer_service_observation(
            address,
            port,
            enable_service_detection=enable_service_detection,
            enable_banner_grab=enable_banner_grab,
            timeout_seconds=profile.banner_timeout_seconds,
        )
        observations.append(
            PortObservation(
                port_number=port,
                protocol="tcp",
                state="open",
                service_name=service_name,
                product=product,
                version=version,
                banner=banner,
                observations=observed_details,
                confidence=confidence,
            )
        )
    return observations


def replace_host_inventory(db: Session, host: Host, observations: list[PortObservation]) -> None:
    for service in list(host.services):
        db.delete(service)
    for port in list(host.ports):
        db.delete(port)
    db.flush()

    for observation in observations:
        port_record = Port(
            host_id=host.id,
            port_number=observation.port_number,
            protocol=observation.protocol,
            state=observation.state,
        )
        db.add(port_record)
        db.flush()

        service_record = Service(
            host_id=host.id,
            port_id=port_record.id,
            name=observation.service_name,
            product=observation.product,
            version=observation.version,
            banner=observation.banner,
            observations=observation.observations,
            confidence=observation.confidence,
        )
        db.add(service_record)


def build_enumeration_summary(
    *,
    total_hosts: int,
    processed_hosts: int,
    open_ports: int,
    services_detected: int,
) -> dict[str, int]:
    return {
        "total_hosts": total_hosts,
        "processed_hosts": processed_hosts,
        "open_ports": open_ports,
        "services_detected": services_detected,
    }


def run_safe_enumeration(scan_id: int) -> None:
    with SessionLocal() as db:
        scan_job = db.get(ScanJob, scan_id)
        if scan_job is None:
            return
        log_path = get_runtime_log_path(scan_job)

        scope = db.get(Scope, scan_job.scope_id)
        if scope is None:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "Scope could not be loaded for this enumeration job.",
            )
            return

        if not scope.is_authorized:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "Port and service enumeration can run only against an authorized scope.",
            )
            return

        scan_job.log_path = str(log_path)

        if scan_job.udp_scan_enabled:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                "UDP scanning remains disabled by default in this MVP.",
            )
            return

        profile = profile_for_intensity(scan_job.scan_intensity)
        ports = ports_for_profile(scan_job.profile_name)
        try:
            target_hosts = resolve_enumeration_hosts(db, scan_job=scan_job, scope=scope)
        except ValueError as exc:
            mark_job_failed(db, scan_job, log_path, str(exc))
            return

        total_socket_checks = len(target_hosts) * len(ports)
        if total_socket_checks > profile.max_socket_checks:
            mark_job_failed(
                db,
                scan_job,
                log_path,
                f"This enumeration job would require {total_socket_checks} TCP checks, which exceeds the safe {profile.name.lower()} limit of {profile.max_socket_checks}. Narrow the host list or use the common TCP profile.",
            )
            return

        append_job_log(
            scan_job,
            log_path,
            f"Starting safe TCP enumeration across {len(target_hosts)} discovered hosts using the {scan_job.profile_name} profile at {profile.name} intensity.",
        )
        scan_job.progress_message = (
            f"Prepared {len(target_hosts)} discovered hosts for safe TCP enumeration."
        )
        db.commit()

        open_port_count = 0
        services_detected = 0

        for index, host in enumerate(target_hosts, start=1):
            try:
                observations = enumerate_host_services(
                    host.address,
                    ports,
                    profile=profile,
                    enable_service_detection=scan_job.include_service_detection,
                    enable_banner_grab=scan_job.include_safe_checks,
                )
                replace_host_inventory(db, host, observations)
                open_port_count += len(observations)
                services_detected += len(observations)
                append_job_log(
                    scan_job,
                    log_path,
                    f"{host.address}: {len(observations)} open TCP ports recorded.",
                )
            except Exception as exc:  # pragma: no cover - keep host failures isolated
                append_job_log(
                    scan_job,
                    log_path,
                    f"{host.address}: enumeration failed with {type(exc).__name__}; continuing with remaining hosts.",
                )

            scan_job.progress = min(99, max(1, int(index / len(target_hosts) * 100)))
            scan_job.progress_message = (
                f"Enumerated {index} of {len(target_hosts)} discovered hosts."
            )
            scan_job.result_summary = build_enumeration_summary(
                total_hosts=len(target_hosts),
                processed_hosts=index,
                open_ports=open_port_count,
                services_detected=services_detected,
            )
            db.commit()

        scan_job.status = "completed"
        scan_job.progress = 100
        scan_job.completed_at = utc_now()
        scan_job.progress_message = (
            f"Enumeration complete. Hosts: {len(target_hosts)}, open ports: {open_port_count}, services: {services_detected}."
        )
        scan_job.result_summary = build_enumeration_summary(
            total_hosts=len(target_hosts),
            processed_hosts=len(target_hosts),
            open_ports=open_port_count,
            services_detected=services_detected,
        )
        generated_findings = refresh_generated_findings(
            db,
            assessment_id=scan_job.assessment_id,
            scope_id=scan_job.scope_id,
        )
        append_job_log(
            scan_job,
            log_path,
            f"Risk engine refreshed {len(generated_findings)} generated findings for the enumerated scope.",
        )
        append_job_log(scan_job, log_path, scan_job.progress_message)
        db.commit()


def get_runtime_log_path(scan_job: ScanJob):
    from app.core.runtime import get_runtime_paths

    return get_runtime_paths().logs_dir / f"{scan_job.job_type.replace('_', '-')}-{scan_job.id}.log"
