from __future__ import annotations

import json

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.core.security import is_private_or_local_target


TABLE_UPDATES: dict[str, dict[str, str]] = {
    "assessments": {
        "client_name": "ALTER TABLE assessments ADD COLUMN client_name TEXT NOT NULL DEFAULT ''",
        "assessment_date": "ALTER TABLE assessments ADD COLUMN assessment_date TEXT NOT NULL DEFAULT ''",
        "scan_intensity": "ALTER TABLE assessments ADD COLUMN scan_intensity TEXT NOT NULL DEFAULT 'Standard'",
        "allow_disruptive_tests": "ALTER TABLE assessments ADD COLUMN allow_disruptive_tests BOOLEAN NOT NULL DEFAULT 0",
    },
    "scopes": {
        "network_ranges": "ALTER TABLE scopes ADD COLUMN network_ranges TEXT NOT NULL DEFAULT '[]'",
        "individual_ips": "ALTER TABLE scopes ADD COLUMN individual_ips TEXT NOT NULL DEFAULT '[]'",
        "excluded_ips": "ALTER TABLE scopes ADD COLUMN excluded_ips TEXT NOT NULL DEFAULT '[]'",
        "has_external_targets": "ALTER TABLE scopes ADD COLUMN has_external_targets BOOLEAN NOT NULL DEFAULT 0",
        "external_scope_confirmed": "ALTER TABLE scopes ADD COLUMN external_scope_confirmed BOOLEAN NOT NULL DEFAULT 0",
    },
    "hosts": {
        "mac_address": "ALTER TABLE hosts ADD COLUMN mac_address TEXT NOT NULL DEFAULT ''",
        "vendor_name": "ALTER TABLE hosts ADD COLUMN vendor_name TEXT NOT NULL DEFAULT ''",
        "device_type": "ALTER TABLE hosts ADD COLUMN device_type TEXT NOT NULL DEFAULT 'Unknown'",
        "discovery_method": "ALTER TABLE hosts ADD COLUMN discovery_method TEXT NOT NULL DEFAULT ''",
    },
    "services": {
        "observations": "ALTER TABLE services ADD COLUMN observations TEXT NOT NULL DEFAULT '{}'",
    },
    "findings": {
        "source": "ALTER TABLE findings ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'",
        "rule_key": "ALTER TABLE findings ADD COLUMN rule_key TEXT NOT NULL DEFAULT ''",
        "priority": "ALTER TABLE findings ADD COLUMN priority TEXT NOT NULL DEFAULT 'P5'",
        "affected_host": "ALTER TABLE findings ADD COLUMN affected_host TEXT NOT NULL DEFAULT ''",
        "port_number": "ALTER TABLE findings ADD COLUMN port_number INTEGER NULL",
        "service_name": "ALTER TABLE findings ADD COLUMN service_name TEXT NOT NULL DEFAULT ''",
        "evidence": "ALTER TABLE findings ADD COLUMN evidence TEXT NOT NULL DEFAULT ''",
        "technical_explanation": "ALTER TABLE findings ADD COLUMN technical_explanation TEXT NOT NULL DEFAULT ''",
        "business_impact": "ALTER TABLE findings ADD COLUMN business_impact TEXT NOT NULL DEFAULT ''",
        "remediation": "ALTER TABLE findings ADD COLUMN remediation TEXT NOT NULL DEFAULT ''",
    },
    "scan_jobs": {
        "job_type": "ALTER TABLE scan_jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'safe_discovery'",
        "progress_message": "ALTER TABLE scan_jobs ADD COLUMN progress_message TEXT NOT NULL DEFAULT ''",
        "log_entries": "ALTER TABLE scan_jobs ADD COLUMN log_entries TEXT NOT NULL DEFAULT '[]'",
        "result_summary": "ALTER TABLE scan_jobs ADD COLUMN result_summary TEXT NOT NULL DEFAULT '{}'",
        "log_path": "ALTER TABLE scan_jobs ADD COLUMN log_path TEXT NOT NULL DEFAULT ''",
        "scan_intensity": "ALTER TABLE scan_jobs ADD COLUMN scan_intensity TEXT NOT NULL DEFAULT 'Standard'",
        "udp_scan_enabled": "ALTER TABLE scan_jobs ADD COLUMN udp_scan_enabled BOOLEAN NOT NULL DEFAULT 0",
        "warning_acknowledged": "ALTER TABLE scan_jobs ADD COLUMN warning_acknowledged BOOLEAN NOT NULL DEFAULT 0",
        "maintenance_window_confirmed": "ALTER TABLE scan_jobs ADD COLUMN maintenance_window_confirmed BOOLEAN NOT NULL DEFAULT 0",
        "started_at": "ALTER TABLE scan_jobs ADD COLUMN started_at TIMESTAMP NULL",
        "completed_at": "ALTER TABLE scan_jobs ADD COLUMN completed_at TIMESTAMP NULL",
    },
    "reports": {
        "report_type": "ALTER TABLE reports ADD COLUMN report_type TEXT NOT NULL DEFAULT 'executive'",
    },
}


def apply_sqlite_schema_updates(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    with engine.begin() as connection:
        for table_name, updates in TABLE_UPDATES.items():
            if not inspector.has_table(table_name):
                continue

            existing_columns = {
                column["name"] for column in inspect(connection).get_columns(table_name)
            }
            for column_name, statement in updates.items():
                if column_name not in existing_columns:
                    connection.execute(text(statement))

        if inspector.has_table("assessments"):
            connection.execute(
                text(
                    "UPDATE assessments "
                    "SET client_name = COALESCE(NULLIF(TRIM(client_name), ''), 'Internal Authorized Client'), "
                    "assessment_date = COALESCE(NULLIF(TRIM(assessment_date), ''), DATE(COALESCE(created_at, CURRENT_TIMESTAMP))), "
                    "scan_intensity = COALESCE(NULLIF(TRIM(scan_intensity), ''), 'Standard'), "
                    "allow_disruptive_tests = COALESCE(allow_disruptive_tests, 0)"
                )
            )

        if inspector.has_table("hosts"):
            connection.execute(
                text(
                    "UPDATE hosts "
                    "SET status = CASE "
                    "WHEN LOWER(COALESCE(status, '')) IN ('identified', 'queued') THEN 'unknown' "
                    "ELSE COALESCE(NULLIF(TRIM(status), ''), 'unknown') "
                    "END, "
                    "device_type = COALESCE(NULLIF(TRIM(device_type), ''), 'Unknown'), "
                    "mac_address = COALESCE(mac_address, ''), "
                    "vendor_name = COALESCE(vendor_name, ''), "
                    "discovery_method = COALESCE(discovery_method, '')"
                )
            )

        if inspector.has_table("services"):
            connection.execute(
                text(
                    "UPDATE services "
                    "SET observations = COALESCE(observations, '{}')"
                )
            )

        if inspector.has_table("findings"):
            connection.execute(
                text(
                    "UPDATE findings "
                    "SET source = COALESCE(NULLIF(TRIM(source), ''), 'manual'), "
                    "rule_key = COALESCE(rule_key, ''), "
                    "severity = CASE LOWER(COALESCE(TRIM(severity), '')) "
                    "WHEN 'critical' THEN 'Critical' "
                    "WHEN 'high' THEN 'High' "
                    "WHEN 'medium' THEN 'Medium' "
                    "WHEN 'low' THEN 'Low' "
                    "WHEN 'info' THEN 'Informational' "
                    "WHEN 'informational' THEN 'Informational' "
                    "ELSE COALESCE(NULLIF(TRIM(severity), ''), 'Informational') "
                    "END, "
                    "priority = CASE LOWER(COALESCE(TRIM(priority), '')) "
                    "WHEN 'p1' THEN 'P1' "
                    "WHEN 'p2' THEN 'P2' "
                    "WHEN 'p3' THEN 'P3' "
                    "WHEN 'p4' THEN 'P4' "
                    "WHEN 'p5' THEN 'P5' "
                    "ELSE CASE LOWER(COALESCE(TRIM(severity), '')) "
                    "WHEN 'critical' THEN 'P1' "
                    "WHEN 'high' THEN 'P2' "
                    "WHEN 'medium' THEN 'P3' "
                    "WHEN 'low' THEN 'P4' "
                    "ELSE 'P5' "
                    "END "
                    "END, "
                    "affected_host = COALESCE(affected_host, ''), "
                    "service_name = COALESCE(service_name, ''), "
                    "evidence = COALESCE(evidence, ''), "
                    "technical_explanation = COALESCE(NULLIF(technical_explanation, ''), COALESCE(description, '')), "
                    "business_impact = COALESCE(business_impact, ''), "
                    "remediation = COALESCE(NULLIF(remediation, ''), COALESCE(recommendation, '')), "
                    "description = COALESCE(description, ''), "
                    "recommendation = COALESCE(recommendation, '')"
                )
            )

        if inspector.has_table("scan_jobs"):
            connection.execute(
                text(
                    "UPDATE scan_jobs "
                    "SET job_type = COALESCE(NULLIF(TRIM(job_type), ''), 'safe_discovery'), "
                    "status = CASE "
                    "WHEN LOWER(COALESCE(status, '')) = 'queued' THEN 'pending' "
                    "WHEN LOWER(COALESCE(status, '')) = 'running' AND completed_at IS NULL THEN 'failed' "
                    "ELSE COALESCE(NULLIF(TRIM(status), ''), 'pending') "
                    "END, "
                    "progress_message = CASE "
                    "WHEN LOWER(COALESCE(status, '')) = 'running' AND completed_at IS NULL "
                    "THEN 'The application restarted before this scan job finished. Review the scope and run it again.' "
                    "ELSE COALESCE(progress_message, '') "
                    "END, "
                    "scan_intensity = COALESCE(NULLIF(TRIM(scan_intensity), ''), 'Standard'), "
                    "log_entries = COALESCE(log_entries, '[]'), "
                    "result_summary = COALESCE(result_summary, '{}'), "
                    "log_path = COALESCE(log_path, ''), "
                    "udp_scan_enabled = COALESCE(udp_scan_enabled, 0), "
                    "warning_acknowledged = COALESCE(warning_acknowledged, 0), "
                    "maintenance_window_confirmed = COALESCE(maintenance_window_confirmed, 0)"
                )
            )

        if inspector.has_table("scopes"):
            rows = connection.execute(
                text(
                    "SELECT id, targets, network_ranges, individual_ips, has_external_targets "
                    "FROM scopes"
                )
            ).mappings()
            for row in rows:
                existing_network_ranges = json.loads(row["network_ranges"] or "[]")
                existing_individual_ips = json.loads(row["individual_ips"] or "[]")
                if existing_network_ranges or existing_individual_ips:
                    continue

                legacy_targets = json.loads(row["targets"] or "[]")
                network_ranges: list[str] = []
                individual_ips: list[str] = []
                has_external_targets = False

                for target in legacy_targets:
                    if "/" in target:
                        network_ranges.append(target)
                    elif target.count(".") == 3:
                        individual_ips.append(target)
                    if ("/" in target or target.count(".") == 3) and not is_private_or_local_target(target):
                        has_external_targets = True

                connection.execute(
                    text(
                        "UPDATE scopes "
                        "SET targets = :targets, "
                        "network_ranges = :network_ranges, "
                        "individual_ips = :individual_ips, "
                        "has_external_targets = :has_external_targets "
                        "WHERE id = :scope_id"
                    ),
                    {
                        "targets": json.dumps(network_ranges + individual_ips),
                        "network_ranges": json.dumps(network_ranges),
                        "individual_ips": json.dumps(individual_ips),
                        "has_external_targets": int(has_external_targets),
                        "scope_id": row["id"],
                    },
                )

        if inspector.has_table("scan_jobs") and inspector.has_table("scopes"):
            scope_targets_by_id = {
                row["id"]: json.loads(row["targets"] or "[]")
                for row in connection.execute(
                    text("SELECT id, targets FROM scopes")
                ).mappings()
            }
            rows = connection.execute(
                text(
                    "SELECT id, scope_id, requested_targets, profile_name, progress_message, job_type, scan_intensity "
                    "FROM scan_jobs"
                )
            ).mappings()
            for row in rows:
                requested_targets = json.loads(row["requested_targets"] or "[]")
                sanitized_targets = [
                    target
                    for target in requested_targets
                    if isinstance(target, str) and ("/" in target or target.count(".") == 3)
                ]
                if not sanitized_targets:
                    sanitized_targets = [
                        target
                        for target in scope_targets_by_id.get(row["scope_id"], [])
                        if isinstance(target, str) and ("/" in target or target.count(".") == 3)
                    ]

                job_type = (row["job_type"] or "").strip() or "safe_discovery"
                profile_name = (row["profile_name"] or "").strip() or "safe-discovery"
                if profile_name in {"baseline", "safe-services", "inventory-only"}:
                    profile_name = "safe-discovery"
                if job_type == "safe_enumeration" and profile_name == "safe-discovery":
                    profile_name = "common-tcp"
                if job_type == "disruptive_tests" and profile_name == "safe-discovery":
                    profile_name = "performance-impacting"

                progress_message = (row["progress_message"] or "").strip()
                if not progress_message:
                    progress_message = (
                        "Performance-impacting tests are ready to run against discovered hosts in the authorized scope."
                        if job_type == "disruptive_tests"
                        else
                        "Enumeration job is ready to scan discovered hosts within the authorized scope."
                        if job_type == "safe_enumeration"
                        else "Discovery job is ready to run against the saved authorized scope."
                    )

                scan_intensity = (row["scan_intensity"] or "").strip() or "Standard"

                connection.execute(
                    text(
                        "UPDATE scan_jobs "
                        "SET requested_targets = :requested_targets, "
                        "profile_name = :profile_name, "
                        "progress_message = :progress_message, "
                        "scan_intensity = :scan_intensity "
                        "WHERE id = :scan_job_id"
                    ),
                    {
                        "requested_targets": json.dumps(sanitized_targets),
                        "profile_name": profile_name,
                        "progress_message": progress_message,
                        "scan_intensity": scan_intensity,
                        "scan_job_id": row["id"],
                    },
                )

        if inspector.has_table("reports"):
            connection.execute(
                text(
                    "UPDATE reports "
                    "SET report_type = CASE LOWER(COALESCE(TRIM(report_type), '')) "
                    "WHEN 'technical' THEN 'technical' "
                    "ELSE 'executive' "
                    "END, "
                    "format = CASE LOWER(COALESCE(TRIM(format), '')) "
                    "WHEN 'pdf' THEN 'pdf' "
                    "ELSE 'html' "
                    "END, "
                    "status = COALESCE(NULLIF(TRIM(status), ''), 'draft'), "
                    "storage_path = COALESCE(storage_path, '')"
                )
            )
