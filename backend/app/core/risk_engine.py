from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Assessment, Finding, Host, Port, Service

CRITICAL_DEVICE_TYPES = {"server", "nas", "printer"}
DATABASE_SERVICES = {"mssql", "oracle", "mysql", "postgresql"}
HTTPS_PORTS = {443, 8443}
HTTP_SECURITY_HEADERS = (
    "x-content-type-options",
    "x-frame-options",
    "content-security-policy",
    "strict-transport-security",
    "referrer-policy",
)
SEVERITY_PRIORITY = {
    "Critical": "P1",
    "High": "P2",
    "Medium": "P3",
    "Low": "P4",
    "Informational": "P5",
}
SEVERITY_SCORE = {
    "Critical": 5,
    "High": 4,
    "Medium": 3,
    "Low": 2,
    "Informational": 1,
}
TOO_MANY_OPEN_PORTS_THRESHOLD = 8
CERTIFICATE_ISSUE_RULE_KEYS = {
    "tls-expired",
    "tls-expiring-soon",
    "tls-self-signed",
    "tls-hostname-mismatch",
}


@dataclass(frozen=True)
class FindingDraft:
    host_id: int | None
    service_id: int | None
    rule_key: str
    title: str
    severity: str
    category: str
    affected_host: str
    port_number: int | None
    service_name: str
    evidence: str
    technical_explanation: str
    business_impact: str
    remediation: str

    @property
    def priority(self) -> str:
        return SEVERITY_PRIORITY.get(self.severity, "P5")


def host_label(host: Host) -> str:
    if host.hostname:
        return f"{host.address} ({host.hostname})"
    return host.address


def normalize_severity(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized == "critical":
        return "Critical"
    if normalized == "high":
        return "High"
    if normalized == "medium":
        return "Medium"
    if normalized == "low":
        return "Low"
    return "Informational"


def severity_sort_key(finding: FindingDraft) -> tuple[int, str, str]:
    return (
        -SEVERITY_SCORE.get(finding.severity, 0),
        finding.affected_host,
        finding.title,
    )


def host_has_https(host: Host) -> bool:
    for port in host.ports:
        if port.port_number in HTTPS_PORTS:
            return True
    for service in host.services:
        if service.name.strip().lower() == "https":
            return True
    return False


def service_port(host: Host, service: Service) -> Port | None:
    for port in host.ports:
        if port.id == service.port_id:
            return port
    return None


def observations_for(service: Service, section: str) -> dict[str, Any]:
    payload = service.observations or {}
    value = payload.get(section, {})
    return value if isinstance(value, dict) else {}


def summarize_service(host: Host, service: Service, port: Port | None) -> str:
    details = [f"Host {host_label(host)}"]
    if port is not None:
        details.append(f"port {port.port_number}/{port.protocol}")
    if service.name:
        details.append(f"service {service.name}")
    if service.product:
        details.append(f"product {service.product}")
    if service.version:
        details.append(f"version {service.version}")
    if service.banner:
        details.append(f"banner snippet: {service.banner[:140]}")
    return "; ".join(details) + "."


def summarize_risky_cookies(risky_cookies: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for cookie in risky_cookies:
        name = str(cookie.get("name", "unnamed"))
        missing_flags = [
            flag for flag in cookie.get("missing_flags", []) if isinstance(flag, str)
        ]
        if missing_flags:
            parts.append(f"{name} missing {', '.join(missing_flags)}")
        else:
            parts.append(name)
    return "; ".join(parts)


def build_finding(
    *,
    host: Host,
    service: Service | None,
    port: Port | None,
    rule_key: str,
    title: str,
    severity: str,
    category: str,
    evidence: str,
    technical_explanation: str,
    business_impact: str,
    remediation: str,
) -> FindingDraft:
    return FindingDraft(
        host_id=host.id,
        service_id=service.id if service else None,
        rule_key=rule_key,
        title=title,
        severity=normalize_severity(severity),
        category=category,
        affected_host=host_label(host),
        port_number=port.port_number if port else None,
        service_name=(service.name if service else ""),
        evidence=evidence,
        technical_explanation=technical_explanation,
        business_impact=business_impact,
        remediation=remediation,
    )


def evaluate_host(host: Host) -> list[FindingDraft]:
    findings: list[FindingDraft] = []
    has_https = host_has_https(host)
    open_port_count = sum(1 for port in host.ports if port.state == "open")
    host_services = sorted(
        host.services,
        key=lambda service: (service_port(host, service).port_number if service_port(host, service) else 0, service.name),
    )

    if host.status == "live" and host.device_type.strip().lower() == "unknown":
        findings.append(
            build_finding(
                host=host,
                service=None,
                port=None,
                rule_key="unknown-device",
                title="Unknown Device Classification Requires Review",
                severity="Medium",
                category="inventory",
                evidence=(
                    f"Host {host_label(host)} is live but remains classified as Unknown. "
                    f"Discovery method: {host.discovery_method or 'unavailable'}."
                ),
                technical_explanation=(
                    "The host responded during safe discovery, but available hostname, MAC vendor, and "
                    "discovery context were not enough to classify its role confidently."
                ),
                business_impact=(
                    "Unidentified assets reduce confidence in inventory completeness and can hide unmanaged "
                    "systems or unsupported business workflows."
                ),
                remediation=(
                    "Validate the system owner, document the host purpose, and update the expected device "
                    "classification in the assessment record."
                ),
            )
        )

    if open_port_count >= TOO_MANY_OPEN_PORTS_THRESHOLD:
        findings.append(
            build_finding(
                host=host,
                service=None,
                port=None,
                rule_key="too-many-open-ports",
                title="Broad TCP Exposure Detected On Host",
                severity="Medium",
                category="exposure",
                evidence=(
                    f"Host {host_label(host)} currently exposes {open_port_count} open TCP ports during safe enumeration."
                ),
                technical_explanation=(
                    "A high number of reachable services expands the attack surface and usually indicates that "
                    "multiple management, application, or legacy endpoints are active on the same system."
                ),
                business_impact=(
                    "More exposed services increase the number of entry points that require patching, monitoring, "
                    "and configuration review."
                ),
                remediation=(
                    "Review which services are required for the host role, disable unused listeners, and restrict "
                    "access to the remaining ports with network segmentation or host firewall rules."
                ),
            )
        )

    open_ports = {port.port_number for port in host.ports if port.state == "open"}
    for service in host_services:
        port = service_port(host, service)
        port_number = port.port_number if port else 0
        service_name = service.name.strip().lower()
        banner_lower = service.banner.lower()
        web_observations = observations_for(service, "web")
        http_observations = observations_for(service, "http")
        tls_observations = observations_for(service, "tls")
        ftp_observations = observations_for(service, "ftp")
        smb_observations = observations_for(service, "smb")
        page_title = str(
            web_observations.get("page_title")
            or http_observations.get("page_title")
            or ""
        )
        status_code = web_observations.get("status_code") or http_observations.get(
            "status_code"
        )
        is_admin_page = bool(
            web_observations.get("is_admin_page")
            or http_observations.get("is_admin_page")
        )
        login_page_detected = bool(
            web_observations.get("login_page_detected")
            or http_observations.get("login_page_detected")
        )
        directory_listing_detected = bool(
            web_observations.get("directory_listing_detected")
            or http_observations.get("directory_listing_detected")
        )
        default_page_detected = bool(
            web_observations.get("default_page_detected")
            or http_observations.get("default_page_detected")
        )
        redirect_location = str(
            web_observations.get("redirect_location")
            or http_observations.get("redirect_location")
            or ""
        )
        missing_headers = [
            header
            for header in (
                web_observations.get("missing_security_headers")
                or http_observations.get("missing_security_headers")
                or []
            )
            if isinstance(header, str)
        ]
        risky_cookies = [
            cookie
            for cookie in web_observations.get("risky_cookies", [])
            if isinstance(cookie, dict)
        ]
        https_warnings = [
            warning
            for warning in web_observations.get("https_warnings", [])
            if isinstance(warning, str)
        ]

        if service_name == "telnet" or port_number == 23:
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="telnet-open",
                    title="Telnet Service Exposed",
                    severity="High",
                    category="remote-access",
                    evidence=summarize_service(host, service, port),
                    technical_explanation=(
                        "Telnet provides interactive remote access without transport encryption and exposes "
                        "session content and credentials to interception on the local network."
                    ),
                    business_impact=(
                        "A reachable Telnet service can enable unauthorized access attempts and expose "
                        "administrative traffic to passive monitoring."
                    ),
                    remediation=(
                        "Disable Telnet where possible and replace it with SSH or another encrypted remote "
                        "administration method."
                    ),
                )
            )

        if service_name == "smb" or port_number in {139, 445}:
            smbv1_detected = bool(smb_observations.get("smbv1_detected"))
            if smbv1_detected or "smbv1" in banner_lower or "nt lm 0.12" in banner_lower:
                findings.append(
                    build_finding(
                        host=host,
                        service=service,
                        port=port,
                        rule_key="smbv1-detected",
                        title="SMBv1 Signature Detected",
                        severity="Critical",
                        category="file-sharing",
                        evidence=summarize_service(host, service, port),
                        technical_explanation=(
                            "SMBv1 is a legacy file-sharing protocol with a long history of high-impact "
                            "security weaknesses and weak default protections."
                        ),
                        business_impact=(
                            "Legacy SMB exposure can materially increase the chance of data access, lateral "
                            "movement, and outage scenarios if the host is compromised."
                        ),
                        remediation=(
                            "Disable SMBv1, enforce SMBv2 or newer, and validate that file-sharing workflows "
                            "still operate with modern protocol settings."
                        ),
                    )
                )
            else:
                smb_severity = (
                    "High"
                    if 139 in open_ports or host.device_type.strip().lower() not in CRITICAL_DEVICE_TYPES
                    else "Medium"
                )
                findings.append(
                    build_finding(
                        host=host,
                        service=service,
                        port=port,
                        rule_key="smb-exposed",
                        title="SMB Service Exposure Detected",
                        severity=smb_severity,
                        category="file-sharing",
                        evidence=summarize_service(host, service, port),
                        technical_explanation=(
                            "SMB services provide file and remote management functionality that should remain "
                            "limited to hosts with a documented business need."
                        ),
                        business_impact=(
                            "Exposed SMB services increase the chance of unauthorized access, relay activity, "
                            "or misuse of file-sharing and administrative interfaces."
                        ),
                        remediation=(
                            "Confirm that SMB is required for this host role, restrict exposure to approved "
                            "subnets, and enforce modern protocol and signing settings."
                        ),
                    )
                )

        if service_name == "rdp" or port_number == 3389:
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="rdp-open",
                    title="Remote Desktop Service Exposed",
                    severity="High",
                    category="remote-access",
                    evidence=summarize_service(host, service, port),
                    technical_explanation=(
                        "RDP exposes an interactive remote administration surface that should be tightly scoped "
                        "and monitored even on internal networks."
                    ),
                    business_impact=(
                        "Unnecessary RDP exposure can widen administrative access paths and increase the impact "
                        "of compromised credentials or workstation misuse."
                    ),
                    remediation=(
                        "Restrict RDP to approved administration segments, require jump hosts where possible, "
                        "and disable the service on systems that do not need remote desktop access."
                    ),
                )
            )

        if service_name == "ftp" or port_number == 21:
            anonymous_access = bool(ftp_observations.get("anonymous_access_detected"))
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="ftp-open",
                    title=(
                        "FTP Service May Permit Anonymous Access"
                        if anonymous_access
                        else "FTP Service Exposed"
                    ),
                    severity="High" if anonymous_access else "Medium",
                    category="file-transfer",
                    evidence=summarize_service(host, service, port),
                    technical_explanation=(
                        "FTP is a legacy file transfer protocol that does not provide modern transport "
                        "encryption. Anonymous-access indicators in the service banner further increase exposure."
                        if anonymous_access
                        else "FTP is a legacy file transfer protocol that does not provide modern transport encryption."
                    ),
                    business_impact=(
                        "Exposed FTP services can increase the risk of data leakage, unauthorized file access, "
                        "and uncontrolled legacy transfer workflows."
                    ),
                    remediation=(
                        "Replace FTP with SFTP, FTPS, or a managed transfer service. If FTP must remain, "
                        "limit network access and review the configured authentication posture."
                    ),
                )
            )

        if (
            service_name == "http"
            and is_admin_page
            and not login_page_detected
            and not redirect_location.lower().startswith("https://")
        ):
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="http-admin-without-https",
                    title="Administrative HTTP Interface Without HTTPS",
                    severity="Medium" if has_https else "High",
                    category="web-security",
                    evidence=summarize_service(host, service, port),
                    technical_explanation=(
                        "The safe web probe observed admin-oriented content over plain HTTP. Without a consistent "
                        "HTTPS path or redirect, management traffic may travel without transport protection."
                    ),
                    business_impact=(
                        "Administrative workflows over unencrypted HTTP can expose session tokens, configuration "
                        "changes, or sensitive operational details to interception on the local network."
                    ),
                    remediation=(
                        "Move the administrative interface behind HTTPS, enforce HTTP-to-HTTPS redirects, and "
                        "limit access to approved administration segments only."
                    ),
                )
            )

        if (
            service_name == "http"
            and login_page_detected
            and not redirect_location.lower().startswith("https://")
        ):
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="login-page-without-https",
                    title="Login Page Served Without HTTPS",
                    severity="High" if not has_https else "Medium",
                    category="web-security",
                    evidence=(
                        f"{summarize_service(host, service, port)}"
                        f" Root page appears to present a login interface"
                        + (f" with title '{page_title}'." if page_title else ".")
                    ),
                    technical_explanation=(
                        "The safe web check observed login-oriented content over HTTP without an HTTPS redirect. "
                        "Credentials or session state could be exposed to interception on the local network."
                    ),
                    business_impact=(
                        "Users may submit credentials or session-bearing requests over cleartext transport, "
                        "increasing the chance of account compromise and management access exposure."
                    ),
                    remediation=(
                        "Serve the login workflow only over HTTPS, enforce HTTP-to-HTTPS redirects, and "
                        "review whether the interface should be reachable from the scanned segment."
                    ),
                )
            )

        if service_name in {"http", "https"} and directory_listing_detected:
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="directory-listing-detected",
                    title="Directory Listing Detected",
                    severity="Medium",
                    category="web-security",
                    evidence=(
                        f"{summarize_service(host, service, port)}"
                        f" Root content matched common directory-listing markers"
                        + (f" on HTTP status {status_code}." if status_code else ".")
                    ),
                    technical_explanation=(
                        "Directory listings can reveal file names, deployment artifacts, backups, and "
                        "application structure that are not meant for general browsing."
                    ),
                    business_impact=(
                        "Exposed listings can aid reconnaissance and unintentionally disclose operational or "
                        "sensitive content available on the web service."
                    ),
                    remediation=(
                        "Disable automatic directory indexing and restrict access to directories that should "
                        "not be directly browsable."
                    ),
                )
            )

        if service_name in {"http", "https"} and default_page_detected:
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="default-page-detected",
                    title="Default Web Page Detected",
                    severity="Low",
                    category="web-security",
                    evidence=(
                        f"{summarize_service(host, service, port)}"
                        f" Root page matched a common default or test-page pattern"
                        + (f" with title '{page_title}'." if page_title else ".")
                    ),
                    technical_explanation=(
                        "Default pages often indicate placeholder deployments, incomplete hardening, or "
                        "services that are reachable without a documented business purpose."
                    ),
                    business_impact=(
                        "An exposed default page can reveal stack details and signal that the service was not "
                        "fully configured for production use."
                    ),
                    remediation=(
                        "Replace the default page with the intended application content or disable the web "
                        "listener if it is not required."
                    ),
                )
            )

        if service_name in {"http", "https"} and risky_cookies:
            severity = (
                "Medium"
                if any(
                    flag in {"Secure", "HttpOnly"}
                    for cookie in risky_cookies
                    for flag in cookie.get("missing_flags", [])
                    if isinstance(flag, str)
                )
                else "Low"
            )
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="risky-cookie-flags",
                    title="Cookie Flags Need Hardening",
                    severity=severity,
                    category="web-security",
                    evidence=(
                        f"{summarize_service(host, service, port)} Risky cookies: "
                        f"{summarize_risky_cookies(risky_cookies)}."
                    ),
                    technical_explanation=(
                        "The safe web check observed cookies that are missing one or more common safety flags "
                        "such as Secure, HttpOnly, or SameSite."
                    ),
                    business_impact=(
                        "Weak cookie flags can increase session exposure, cross-site request risks, and the "
                        "chance that client-side scripts access session-bearing values."
                    ),
                    remediation=(
                        "Set Secure, HttpOnly, and appropriate SameSite values for session and sensitive "
                        "cookies, and confirm that cleartext HTTP does not issue authentication cookies."
                    ),
                )
            )

        if service_name in {"http", "https"} and https_warnings and service_name == "http":
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="http-without-https",
                    title="Web Service Uses HTTP Without HTTPS",
                    severity="Low" if not login_page_detected and not is_admin_page else "Medium",
                    category="web-security",
                    evidence=(
                        f"{summarize_service(host, service, port)} HTTPS warnings: "
                        f"{'; '.join(https_warnings)}"
                    ),
                    technical_explanation=(
                        "The service responded over HTTP and the safe root request did not observe a redirect "
                        "to HTTPS."
                    ),
                    business_impact=(
                        "Cleartext web transport can expose browsing activity, configuration data, and user "
                        "interactions to interception on internal segments."
                    ),
                    remediation=(
                        "Enable HTTPS for the service, redirect HTTP traffic to HTTPS, and remove any "
                        "cleartext-only management workflow where possible."
                    ),
                )
            )

        if service_name in DATABASE_SERVICES or port_number in {1433, 1521, 3306, 5432}:
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="database-port-open",
                    title="Database Service Exposed",
                    severity="High",
                    category="database",
                    evidence=summarize_service(host, service, port),
                    technical_explanation=(
                        "Reachable database listeners provide direct access to data-handling services and should "
                        "normally be restricted to application tiers and approved administration paths."
                    ),
                    business_impact=(
                        "Broad database exposure can increase the risk of data disclosure, configuration drift, "
                        "and unauthorized administrative access."
                    ),
                    remediation=(
                        "Restrict database access to required application and administration hosts, and disable "
                        "or firewall listeners that are not part of the intended design."
                    ),
                )
            )

        if service_name == "redis" or port_number == 6379:
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="redis-exposed",
                    title="Redis Service Exposed",
                    severity="High",
                    category="database",
                    evidence=summarize_service(host, service, port),
                    technical_explanation=(
                        "Redis is often intended for trusted internal application paths and can expose "
                        "sensitive cache data or operational controls when broadly reachable."
                    ),
                    business_impact=(
                        "Unexpected Redis exposure can affect application state, cached data confidentiality, "
                        "and service stability."
                    ),
                    remediation=(
                        "Bind Redis only to required interfaces, restrict access with host-based controls, and "
                        "review whether the listener is needed for this assessment scope."
                    ),
                )
            )

        if service_name == "elasticsearch" or port_number in {9200, 9300}:
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="elasticsearch-exposed",
                    title="Elasticsearch Service Exposed",
                    severity="High",
                    category="database",
                    evidence=summarize_service(host, service, port),
                    technical_explanation=(
                        "Elasticsearch services can expose indexed data and cluster-level functionality when "
                        "reachable from broad internal segments."
                    ),
                    business_impact=(
                        "Unexpected Elasticsearch exposure can lead to data disclosure, reconnaissance, and "
                        "operational disruption if access controls are weak."
                    ),
                    remediation=(
                        "Restrict Elasticsearch listeners to required application nodes, review network access "
                        "controls, and validate that administrative APIs are not broadly reachable."
                    ),
                )
            )

        if service_name == "https":
            valid_until = str(tls_observations.get("valid_until") or "").strip()
            subject_label = str(
                tls_observations.get("subject_common_name")
                or tls_observations.get("subject")
                or ""
            ).strip()
            issuer_label = str(
                tls_observations.get("issuer_common_name")
                or tls_observations.get("issuer")
                or ""
            ).strip()
            hostname_reference = str(
                tls_observations.get("hostname_reference") or host.address
            ).strip()
            hostname_mismatch_reason = str(
                tls_observations.get("hostname_mismatch_reason") or ""
            ).strip()
            protocol_label = str(tls_observations.get("protocol") or "").strip()
            days_until_expiry = tls_observations.get("days_until_expiry")

            if bool(tls_observations.get("expired")):
                findings.append(
                    build_finding(
                        host=host,
                        service=service,
                        port=port,
                        rule_key="tls-expired",
                        title="Expired TLS Certificate Observed",
                        severity="Medium",
                        category="transport-security",
                        evidence=(
                            f"{summarize_service(host, service, port)}"
                            + (
                                f" Certificate subject: {subject_label}."
                                if subject_label
                                else ""
                            )
                            + (
                                f" Observed validity end: {valid_until}."
                                if valid_until
                                else ""
                            )
                        ),
                        technical_explanation=(
                            "The observed TLS certificate appears to be past its validity period, which can "
                            "break trust decisions and encourage unsafe certificate handling by users."
                        ),
                        business_impact=(
                            "Expired certificates can interrupt access, reduce operator trust in browser or "
                            "client warnings, and increase the likelihood of unsafe exception handling."
                        ),
                        remediation=(
                            "Replace the expired certificate with a currently valid certificate and review "
                            "certificate lifecycle monitoring for the service."
                        ),
                    )
                )

            if bool(tls_observations.get("expiring_soon")):
                findings.append(
                    build_finding(
                        host=host,
                        service=service,
                        port=port,
                        rule_key="tls-expiring-soon",
                        title="TLS Certificate Expiring Soon",
                        severity=(
                            "Medium"
                            if is_admin_page or (isinstance(days_until_expiry, int) and days_until_expiry <= 7)
                            else "Low"
                        ),
                        category="transport-security",
                        evidence=(
                            f"{summarize_service(host, service, port)}"
                            + (
                                f" Certificate subject: {subject_label}."
                                if subject_label
                                else ""
                            )
                            + (
                                f" Certificate expires in {days_until_expiry} days."
                                if isinstance(days_until_expiry, int)
                                else " Certificate validity window is approaching its end."
                            )
                            + (
                                f" Observed validity end: {valid_until}."
                                if valid_until
                                else ""
                            )
                        ),
                        technical_explanation=(
                            "The observed TLS certificate remains valid but is approaching expiry. "
                            "Short remaining validity can create avoidable outages or emergency certificate changes."
                        ),
                        business_impact=(
                            "Certificates that are close to expiry can interrupt internal applications, "
                            "administration interfaces, and automation if they are not rotated in time."
                        ),
                        remediation=(
                            "Schedule certificate renewal before expiry, update deployment procedures if needed, "
                            "and add monitoring that alerts the team before the next certificate window closes."
                        ),
                    )
                )

            if bool(tls_observations.get("self_signed")):
                findings.append(
                    build_finding(
                        host=host,
                        service=service,
                        port=port,
                        rule_key="tls-self-signed",
                        title="Self-Signed TLS Certificate Observed",
                        severity="Medium" if is_admin_page else "Low",
                        category="transport-security",
                        evidence=(
                            f"{summarize_service(host, service, port)}"
                            + (
                                f" Certificate subject: {subject_label}."
                                if subject_label
                                else ""
                            )
                            + (
                                f" Issuer: {issuer_label}."
                                if issuer_label
                                else ""
                            )
                            + (
                                f" Negotiated protocol: {protocol_label}."
                                if protocol_label
                                else ""
                            )
                        ),
                        technical_explanation=(
                            "The observed certificate appears self-signed, which limits independent trust "
                            "validation and often leads to manual certificate exceptions."
                        ),
                        business_impact=(
                            "Self-signed certificates can normalize unsafe warning bypasses and make "
                            "impersonation or interception harder for users to recognize."
                        ),
                        remediation=(
                            "Replace the self-signed certificate with one issued by an approved internal or "
                            "external certificate authority and document the trust model for administrators."
                        ),
                    )
                )

            if (
                bool(tls_observations.get("hostname_mismatch_detectable"))
                and bool(tls_observations.get("hostname_mismatch"))
            ):
                findings.append(
                    build_finding(
                        host=host,
                        service=service,
                        port=port,
                        rule_key="tls-hostname-mismatch",
                        title="TLS Certificate Hostname Mismatch Observed",
                        severity="Medium",
                        category="transport-security",
                        evidence=(
                            f"{summarize_service(host, service, port)}"
                            f" Certificate did not match the scanned reference '{hostname_reference}'."
                            + (
                                f" Validation detail: {hostname_mismatch_reason}."
                                if hostname_mismatch_reason
                                else ""
                            )
                        ),
                        technical_explanation=(
                            "The TLS certificate names did not align with the scanned service reference, which can "
                            "trigger client trust warnings and indicate misaligned service naming or certificate deployment."
                        ),
                        business_impact=(
                            "Hostname mismatches can break automation, reduce operator trust in certificate warnings, "
                            "and make it harder to distinguish legitimate services from misconfigured or impersonated endpoints."
                        ),
                        remediation=(
                            "Issue or install a certificate whose subject alternative names cover the intended HTTPS "
                            "hostname or IP reference, and confirm that clients use the correct internal service name."
                        ),
                    )
                )

        if service_name in {"http", "https"} and missing_headers:
            findings.append(
                build_finding(
                    host=host,
                    service=service,
                    port=port,
                    rule_key="missing-http-security-headers",
                    title="Missing Common HTTP Security Headers",
                    severity="Medium" if is_admin_page else "Low",
                    category="web-security",
                    evidence=(
                        f"{summarize_service(host, service, port)} Missing headers: {', '.join(missing_headers)}."
                    ),
                    technical_explanation=(
                        "The safe web probe did not observe several common response headers that help harden "
                        "browser behavior and reduce misuse of rendered content."
                    ),
                    business_impact=(
                        "Missing browser hardening headers can increase the impact of misconfigurations, unsafe "
                        "content handling, and clickjacking-style exposure in internal applications."
                    ),
                    remediation=(
                        "Add the missing headers through the application or reverse proxy configuration and "
                        "review secure defaults for internal web services."
                    ),
                )
            )

    return sorted(findings, key=severity_sort_key)


def refresh_generated_findings(
    db: Session,
    *,
    assessment_id: int,
    scope_id: int | None = None,
) -> list[Finding]:
    db.flush()
    db.expire_all()

    query = (
        db.query(Host)
        .options(selectinload(Host.ports), selectinload(Host.services))
        .filter(Host.assessment_id == assessment_id)
        .order_by(Host.address.asc())
    )
    if scope_id is not None:
        query = query.filter(Host.scope_id == scope_id)

    hosts = list(query)
    host_ids = [host.id for host in hosts]

    existing_generated = (
        db.query(Finding)
        .filter(Finding.assessment_id == assessment_id, Finding.source == "risk-engine")
    )
    if host_ids:
        existing_generated = existing_generated.filter(Finding.host_id.in_(host_ids))
    elif scope_id is not None:
        existing_generated = existing_generated.filter(Finding.host_id.is_(None))

    for finding in list(existing_generated):
        db.delete(finding)
    db.flush()

    generated: list[Finding] = []
    drafts = [draft for host in hosts for draft in evaluate_host(host)]
    for draft in sorted(drafts, key=severity_sort_key):
        finding = Finding(
            assessment_id=assessment_id,
            host_id=draft.host_id,
            service_id=draft.service_id,
            source="risk-engine",
            rule_key=draft.rule_key,
            title=draft.title,
            severity=draft.severity,
            priority=draft.priority,
            category=draft.category,
            status="open",
            affected_host=draft.affected_host,
            port_number=draft.port_number,
            service_name=draft.service_name,
            evidence=draft.evidence,
            technical_explanation=draft.technical_explanation,
            business_impact=draft.business_impact,
            remediation=draft.remediation,
            description=draft.technical_explanation,
            recommendation=draft.remediation,
        )
        db.add(finding)
        generated.append(finding)

    db.flush()
    return generated


def refresh_all_generated_findings(db: Session) -> int:
    assessment_ids = list(db.scalars(select(Assessment.id).order_by(Assessment.id.asc())))
    total_generated = 0
    for assessment_id in assessment_ids:
        total_generated += len(
            refresh_generated_findings(db, assessment_id=assessment_id)
        )
    return total_generated


def hydrate_existing_findings(db: Session) -> int:
    findings = list(
        db.query(Finding)
        .options(
            selectinload(Finding.host),
            selectinload(Finding.service).selectinload(Service.port),
        )
        .order_by(Finding.id.asc())
    )
    updated = 0
    for finding in findings:
        changed = False
        if not finding.affected_host and finding.host is not None:
            finding.affected_host = host_label(finding.host)
            changed = True
        if not finding.service_name and finding.service is not None:
            finding.service_name = finding.service.name
            changed = True
        if finding.port_number is None and finding.service is not None and finding.service.port is not None:
            finding.port_number = finding.service.port.port_number
            changed = True
        if not finding.priority:
            finding.priority = SEVERITY_PRIORITY.get(
                normalize_severity(finding.severity),
                "P5",
            )
            changed = True
        if not finding.technical_explanation and finding.description:
            finding.technical_explanation = finding.description
            changed = True
        if not finding.evidence:
            finding.evidence = (
                finding.description
                or finding.technical_explanation
                or "Manual finding record pending supporting evidence."
            )
            changed = True
        if not finding.business_impact:
            finding.business_impact = (
                "This issue may increase operational or security risk for the affected host until it is reviewed and remediated."
            )
            changed = True
        if not finding.remediation and finding.recommendation:
            finding.remediation = finding.recommendation
            changed = True
        if changed:
            updated += 1
    db.flush()
    return updated


def top_host_risk_score(findings: list[Finding]) -> int:
    return sum(SEVERITY_SCORE.get(normalize_severity(finding.severity), 0) for finding in findings)
