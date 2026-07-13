from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
import math
import ssl
from typing import Any

EXPIRING_SOON_DAYS = 30


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


def extract_name_value(entries: Any, expected_key: str) -> str:
    if not isinstance(entries, tuple):
        return ""
    for section in entries:
        if not isinstance(section, tuple):
            continue
        for key, value in section:
            if str(key).strip().lower() == expected_key.lower():
                return str(value)
    return ""


def certificate_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        timestamp = ssl.cert_time_to_seconds(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def iso_or_empty(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def extract_subject_alt_names(
    certificate: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    subject_alt_names: list[str] = []
    dns_names: list[str] = []
    ip_addresses: list[str] = []

    entries = certificate.get("subjectAltName", ())
    if not isinstance(entries, tuple):
        return subject_alt_names, dns_names, ip_addresses

    for entry in entries:
        if not isinstance(entry, tuple) or len(entry) != 2:
            continue
        kind, value = str(entry[0]), str(entry[1])
        if kind == "DNS":
            dns_names.append(value)
            subject_alt_names.append(f"DNS:{value}")
        elif kind in {"IP Address", "IP"}:
            ip_addresses.append(value)
            subject_alt_names.append(f"IP:{value}")
        else:
            subject_alt_names.append(f"{kind}:{value}")

    return subject_alt_names, dns_names, ip_addresses


def dns_name_matches(reference: str, candidate: str) -> bool:
    normalized_reference = reference.strip().rstrip(".").lower()
    normalized_candidate = candidate.strip().rstrip(".").lower()
    if not normalized_reference or not normalized_candidate:
        return False
    if "*" not in normalized_candidate:
        return normalized_reference == normalized_candidate
    if not normalized_candidate.startswith("*.") or normalized_candidate.count("*") > 1:
        return False

    suffix = normalized_candidate[1:]
    if not normalized_reference.endswith(suffix):
        return False

    unmatched_prefix = normalized_reference[: -len(suffix)]
    return bool(unmatched_prefix) and "." not in unmatched_prefix


def detect_hostname_mismatch(
    certificate: dict[str, Any],
    *,
    target: str,
    subject_common_name: str,
    dns_names: list[str],
    ip_addresses: list[str],
) -> tuple[bool, bool, str]:
    reference = target.strip()
    if not reference:
        return False, False, ""

    try:
        ipaddress.ip_address(reference)
        target_is_ip = True
    except ValueError:
        target_is_ip = False

    has_names = bool(subject_common_name or dns_names or ip_addresses)
    if not has_names:
        return False, False, "Certificate did not expose host identity fields."

    if target_is_ip and not ip_addresses:
        return (
            False,
            False,
            "Target was scanned by IP address and the certificate did not advertise IP subject alternative names.",
        )

    if target_is_ip:
        target_ip = ipaddress.ip_address(reference)
        for ip_value in ip_addresses:
            try:
                if ipaddress.ip_address(ip_value) == target_ip:
                    return True, False, ""
            except ValueError:
                continue
        return True, True, "Certificate IP subject alternative names did not include the scanned IP address."

    comparable_names = dns_names or ([subject_common_name] if subject_common_name else [])
    if not comparable_names:
        return False, False, "Certificate did not expose a DNS name that could be checked safely."

    if any(dns_name_matches(reference, candidate) for candidate in comparable_names):
        return True, False, ""

    return True, True, "Certificate DNS names did not include the scanned service name."


def describe_tls_certificate(
    certificate: dict[str, Any],
    *,
    target: str,
    protocol: str = "",
    cipher_info: tuple[str, str, int] | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    if not certificate:
        return {}

    now = reference_time or datetime.now(timezone.utc)
    subject = flatten_certificate_name(certificate.get("subject"))
    issuer = flatten_certificate_name(certificate.get("issuer"))
    subject_common_name = extract_name_value(certificate.get("subject"), "commonName")
    issuer_common_name = extract_name_value(certificate.get("issuer"), "commonName")
    subject_alt_names, dns_names, ip_addresses = extract_subject_alt_names(certificate)

    valid_from_dt = certificate_datetime(str(certificate.get("notBefore", "")))
    valid_until_dt = certificate_datetime(str(certificate.get("notAfter", "")))
    expired = bool(valid_until_dt and valid_until_dt <= now)
    days_until_expiry: int | None = None
    expiring_soon = False
    if valid_until_dt is not None:
        delta_seconds = (valid_until_dt - now).total_seconds()
        days_until_expiry = math.ceil(delta_seconds / 86400)
        expiring_soon = not expired and delta_seconds <= EXPIRING_SOON_DAYS * 86400

    hostname_mismatch_detectable, hostname_mismatch, mismatch_reason = detect_hostname_mismatch(
        certificate,
        target=target,
        subject_common_name=subject_common_name,
        dns_names=dns_names,
        ip_addresses=ip_addresses,
    )

    cipher_name = ""
    cipher_protocol = ""
    cipher_bits = 0
    if cipher_info:
        cipher_name = str(cipher_info[0] or "")
        cipher_protocol = str(cipher_info[1] or "")
        cipher_bits = int(cipher_info[2] or 0)

    return {
        "subject": subject,
        "issuer": issuer,
        "subject_common_name": subject_common_name,
        "issuer_common_name": issuer_common_name,
        "subject_alt_names": subject_alt_names,
        "dns_names": dns_names,
        "ip_addresses": ip_addresses,
        "valid_from": iso_or_empty(valid_from_dt),
        "valid_until": iso_or_empty(valid_until_dt),
        "expired": expired,
        "expiring_soon": expiring_soon,
        "days_until_expiry": days_until_expiry,
        "self_signed": bool(subject and issuer and subject == issuer),
        "hostname_reference": target,
        "hostname_mismatch_detectable": hostname_mismatch_detectable,
        "hostname_mismatch": hostname_mismatch,
        "hostname_mismatch_reason": mismatch_reason,
        "protocol": protocol,
        "cipher": cipher_name,
        "cipher_protocol": cipher_protocol,
        "cipher_bits": cipher_bits,
    }


def inspect_tls_connection(
    connection: ssl.SSLSocket,
    *,
    target: str,
) -> dict[str, Any]:
    try:
        certificate = connection.getpeercert()
    except ssl.SSLError:
        return {}

    if not certificate:
        return {}

    return describe_tls_certificate(
        certificate,
        target=target,
        protocol=str(connection.version() or ""),
        cipher_info=connection.cipher(),
    )
