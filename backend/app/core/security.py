from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress


PRIVATE_OR_LOCAL_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
)


@dataclass
class ScopeValidationResult:
    network_ranges: list[str] = field(default_factory=list)
    individual_ips: list[str] = field(default_factory=list)
    excluded_ips: list[str] = field(default_factory=list)
    included_targets: list[str] = field(default_factory=list)
    public_targets: list[str] = field(default_factory=list)
    has_external_targets: bool = False
    field_errors: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return not self.field_errors


def normalize_targets(raw_targets: list[str]) -> list[str]:
    normalized: list[str] = []
    for target in raw_targets:
        candidate = target.strip()
        if candidate:
            normalized.append(candidate)
    return normalized


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        lowered = value.lower()
        if lowered not in seen:
            deduped.append(value)
            seen.add(lowered)
    return deduped


def _parse_ipv4_network(value: str) -> tuple[ipaddress.IPv4Network | None, str | None]:
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None, f"Invalid CIDR range: {value}"

    if network.version != 4:
        return None, f"Only IPv4 CIDR ranges are supported in the MVP: {value}"

    return network, None


def _parse_ipv4_address(value: str) -> tuple[ipaddress.IPv4Address | None, str | None]:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None, f"Invalid IP address: {value}"

    if address.version != 4:
        return None, f"Only IPv4 addresses are supported in the MVP: {value}"

    return address, None


def _is_allowed_local_network(network: ipaddress.IPv4Network) -> bool:
    return any(network.subnet_of(allowed) for allowed in PRIVATE_OR_LOCAL_NETWORKS)


def _is_allowed_local_ip(address: ipaddress.IPv4Address) -> bool:
    return any(address in allowed for allowed in PRIVATE_OR_LOCAL_NETWORKS)


def is_private_or_local_target(target: str) -> bool:
    if "/" in target:
        network, _ = _parse_ipv4_network(target.strip())
        return bool(network and _is_allowed_local_network(network))

    address, _ = _parse_ipv4_address(target.strip())
    return bool(address and _is_allowed_local_ip(address))


def validate_scope_entries(
    raw_network_ranges: list[str],
    raw_individual_ips: list[str],
    raw_excluded_ips: list[str],
    *,
    allow_external_scope: bool,
    external_scope_confirmed: bool,
) -> ScopeValidationResult:
    result = ScopeValidationResult()
    public_network_ranges: list[str] = []
    public_individual_ips: list[str] = []

    network_ranges = _dedupe(normalize_targets(raw_network_ranges))
    individual_ips = _dedupe(normalize_targets(raw_individual_ips))
    excluded_ips = _dedupe(normalize_targets(raw_excluded_ips))

    if not network_ranges and not individual_ips:
        result.field_errors["network_ranges"] = [
            "Add at least one network range or one individual IP address.",
        ]
        return result

    included_networks: list[ipaddress.IPv4Network] = []
    for entry in network_ranges:
        network, error = _parse_ipv4_network(entry)
        if error:
            result.field_errors.setdefault("network_ranges", []).append(error)
            continue

        normalized = str(network)
        included_networks.append(network)
        result.network_ranges.append(normalized)

        if not _is_allowed_local_network(network):
            result.public_targets.append(normalized)
            public_network_ranges.append(normalized)

    included_addresses: list[ipaddress.IPv4Address] = []
    for entry in individual_ips:
        address, error = _parse_ipv4_address(entry)
        if error:
            result.field_errors.setdefault("individual_ips", []).append(error)
            continue

        normalized = str(address)
        included_addresses.append(address)
        result.individual_ips.append(normalized)

        if not _is_allowed_local_ip(address):
            result.public_targets.append(normalized)
            public_individual_ips.append(normalized)

    for entry in excluded_ips:
        address, error = _parse_ipv4_address(entry)
        if error:
            result.field_errors.setdefault("excluded_ips", []).append(error)
            continue

        normalized = str(address)
        is_in_scope = any(address in network for network in included_networks) or any(
            address == included for included in included_addresses
        )
        if not is_in_scope:
            result.field_errors.setdefault("excluded_ips", []).append(
                f"Excluded IP must belong to an included range or included IP list: {normalized}"
            )
            continue

        result.excluded_ips.append(normalized)

    result.has_external_targets = bool(result.public_targets)
    if result.has_external_targets and not allow_external_scope:
        if public_network_ranges:
            result.field_errors.setdefault("network_ranges", []).append(
                "External/public CIDR ranges are blocked by default. Update settings before adding public targets."
            )
        if public_individual_ips:
            result.field_errors.setdefault("individual_ips", []).append(
                "External/public IP addresses are blocked by default. Update settings before adding public targets."
            )

    if result.has_external_targets and allow_external_scope and not external_scope_confirmed:
        result.field_errors["external_scope_confirmed"] = [
            "External/public targets require explicit confirmation before the scope can be saved.",
        ]

    result.included_targets = result.network_ranges + [
        address for address in result.individual_ips if address not in result.excluded_ips
    ]

    if not result.included_targets and not (
        result.field_errors.get("network_ranges") or result.field_errors.get("individual_ips")
    ):
        result.field_errors.setdefault("individual_ips", []).append(
            "At least one included target must remain after exclusions are applied."
        )

    return result


def target_is_within_scope(
    target: str,
    *,
    scope_network_ranges: list[str],
    scope_individual_ips: list[str],
    scope_excluded_ips: list[str],
) -> bool:
    normalized = target.strip()
    if not normalized:
        return False

    excluded_set = {value.lower() for value in scope_excluded_ips}

    if "/" in normalized:
        target_network, error = _parse_ipv4_network(normalized)
        if error or target_network is None:
            return False

        for scope_network in scope_network_ranges:
            parsed_scope_network, parse_error = _parse_ipv4_network(scope_network)
            if parse_error or parsed_scope_network is None:
                continue
            if target_network.subnet_of(parsed_scope_network):
                return True

        return False

    target_address, error = _parse_ipv4_address(normalized)
    if error or target_address is None:
        return False

    if normalized.lower() in excluded_set:
        return False

    if normalized.lower() in {value.lower() for value in scope_individual_ips}:
        return True

    return any(
        target_address in parsed_scope_network
        for parsed_scope_network in (
            _parse_ipv4_network(value)[0] for value in scope_network_ranges
        )
        if parsed_scope_network is not None
    )


def validate_requested_targets_against_scope(
    requested_targets: list[str],
    *,
    scope_network_ranges: list[str],
    scope_individual_ips: list[str],
    scope_excluded_ips: list[str],
) -> list[str]:
    invalid: list[str] = []
    for target in normalize_targets(requested_targets):
        if not target_is_within_scope(
            target,
            scope_network_ranges=scope_network_ranges,
            scope_individual_ips=scope_individual_ips,
            scope_excluded_ips=scope_excluded_ips,
        ):
            invalid.append(target)
    return invalid
