from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.risk_engine import CERTIFICATE_ISSUE_RULE_KEYS, SEVERITY_SCORE, normalize_severity
from app.core.runtime import get_runtime_paths
from app.core.settings_registry import get_setting_map
from app.models import Assessment, Finding, Host, Port, Report, ScanJob, Scope, Service

SEVERITY_ORDER = ["Critical", "High", "Medium", "Low", "Informational"]
SEVERITY_COLORS = {
    "Critical": "#a61b1b",
    "High": "#d9480f",
    "Medium": "#d68c00",
    "Low": "#2b6cb0",
    "Informational": "#5b7083",
}
REPORT_TITLES = {
    "executive": "Executive Report",
    "technical": "Technical Report",
}


@dataclass(frozen=True)
class HostReportView:
    host: Host
    ports: list[Port]
    services: list[Service]
    findings: list[Finding]
    risk_score: int


@dataclass(frozen=True)
class ReportContext:
    assessment: Assessment
    branding_name: str
    branding_tagline: str
    branding_contact: str
    scopes: list[Scope]
    hosts: list[HostReportView]
    findings: list[Finding]
    scan_jobs: list[ScanJob]
    generated_at: datetime
    severity_counts: dict[str, int]
    overall_risk_rating: str
    overall_risk_score: int
    top_findings: list[Finding]
    top_hosts: list[HostReportView]
    web_findings: list[Finding]
    tls_findings: list[Finding]
    configuration_findings: list[Finding]
    executive_summary: list[str]
    key_findings: list[str]
    business_impact_points: list[str]
    immediate_actions: list[str]
    short_term_actions: list[str]
    long_term_improvements: list[str]


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "report"


def html_escape(value: Any) -> str:
    return escape(str(value))


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return "Unavailable"
    normalized = normalize_datetime(value)
    return normalized.strftime("%Y-%m-%d %H:%M UTC")


def format_assessment_date(value: str) -> str:
    return value or "Unavailable"


def report_footer_text(context: ReportContext) -> str:
    footer_parts = [context.branding_name]
    if context.branding_contact and context.branding_contact != context.branding_name:
        footer_parts.append(context.branding_contact)
    footer_parts.append("Authorized internal use only")
    return " | ".join(footer_parts)


def severity_sort_key(finding: Finding) -> tuple[int, str, str]:
    severity = normalize_severity(finding.severity)
    return (-SEVERITY_SCORE.get(severity, 0), finding.affected_host or "", finding.title)


def calculate_severity_counts(findings: list[Finding]) -> dict[str, int]:
    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for finding in findings:
        counts[normalize_severity(finding.severity)] += 1
    return counts


def calculate_overall_risk_rating(counts: dict[str, int]) -> str:
    if counts["Critical"] > 0:
        return "Critical"
    if counts["High"] >= 2 or (counts["High"] >= 1 and counts["Medium"] >= 3):
        return "High"
    if counts["High"] >= 1 or counts["Medium"] >= 3:
        return "Moderate"
    if counts["Medium"] >= 1 or counts["Low"] >= 3:
        return "Guarded"
    return "Low"


def total_risk_score(findings: list[Finding]) -> int:
    return sum(SEVERITY_SCORE.get(normalize_severity(finding.severity), 0) for finding in findings)


def finding_risk_score(finding: Finding) -> int:
    return SEVERITY_SCORE.get(normalize_severity(finding.severity), 0)


def summarize_scope(scopes: list[Scope]) -> list[str]:
    lines: list[str] = []
    for scope in scopes:
        included = ", ".join(scope.included_targets) or "No included targets recorded"
        excluded = ", ".join(scope.excluded_ips) or "No exclusions recorded"
        lines.append(f"{scope.name}: included {included}; excluded {excluded}.")
    return lines or ["No saved scope records were found."]


def scope_targets_table(scopes: list[Scope]) -> list[list[str]]:
    rows: list[list[str]] = []
    for scope in scopes:
        rows.append(
            [
                scope.name,
                ", ".join(scope.included_targets) or "None recorded",
                ", ".join(scope.excluded_ips) or "None",
                "Authorized" if scope.is_authorized else "Pending",
            ]
        )
    return rows


def findings_by_host(findings: list[Finding]) -> dict[int | None, list[Finding]]:
    grouped: dict[int | None, list[Finding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.host_id].append(finding)
    for host_id in grouped:
        grouped[host_id] = sorted(grouped[host_id], key=severity_sort_key)
    return grouped


def build_host_views(hosts: list[Host], findings: list[Finding]) -> list[HostReportView]:
    grouped_findings = findings_by_host(findings)
    views: list[HostReportView] = []
    for host in hosts:
        ports = sorted(host.ports, key=lambda port: (port.port_number, port.protocol))
        services = sorted(
            host.services,
            key=lambda service: (
                next((port.port_number for port in ports if port.id == service.port_id), 0),
                service.name,
            ),
        )
        host_findings = grouped_findings.get(host.id, [])
        views.append(
            HostReportView(
                host=host,
                ports=ports,
                services=services,
                findings=host_findings,
                risk_score=total_risk_score(host_findings),
            )
        )
    return sorted(
        views,
        key=lambda view: (-view.risk_score, -(len(view.findings)), view.host.address),
    )


def latest_relevant_scan(scan_jobs: list[ScanJob]) -> ScanJob | None:
    def scan_sort_key(scan_job: ScanJob) -> tuple[datetime, datetime]:
        return (
            normalize_datetime(scan_job.completed_at) if scan_job.completed_at else datetime.min.replace(tzinfo=timezone.utc),
            normalize_datetime(scan_job.created_at),
        )

    if not scan_jobs:
        return None
    return max(scan_jobs, key=scan_sort_key)


def normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def finding_highlights(findings: list[Finding], limit: int = 5) -> list[str]:
    if not findings:
        return ["No risk findings were recorded for this assessment."]

    highlights: list[str] = []
    for finding in sorted(findings, key=severity_sort_key)[:limit]:
        severity = normalize_severity(finding.severity)
        host = finding.affected_host or "assessment-wide scope"
        service = finding.service_name or "host posture"
        highlights.append(
            f"{severity}: {finding.title} affecting {host} through {service}."
        )
    return highlights


def unique_texts(values: list[str], limit: int) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(normalized)
        if len(items) >= limit:
            break
    return items


def action_plan(findings: list[Finding]) -> tuple[list[str], list[str], list[str]]:
    sorted_findings = sorted(findings, key=severity_sort_key)
    immediate = unique_texts(
        [
            finding.remediation
            for finding in sorted_findings
            if normalize_severity(finding.severity) in {"Critical", "High"}
        ],
        limit=4,
    )
    short_term = unique_texts(
        [
            finding.remediation
            for finding in sorted_findings
            if normalize_severity(finding.severity) in {"Medium", "Low"}
        ],
        limit=4,
    )

    long_term: list[str] = []
    rule_keys = {finding.rule_key for finding in findings}
    categories = {finding.category for finding in findings}
    if any(rule_key.startswith("tls-") for rule_key in rule_keys):
        long_term.append(
            "Establish certificate lifecycle monitoring so expiring, self-signed, or mismatched internal certificates are corrected before they interrupt services."
        )
    if "web-security" in categories:
        long_term.append(
            "Standardize internal web service hardening with HTTPS defaults, browser security headers, and secure cookie settings."
        )
    if any(rule_key in {"smb-exposed", "rdp-open", "telnet-open", "ftp-open"} for rule_key in rule_keys):
        long_term.append(
            "Review which remote access and legacy services are truly required, then narrow exposure through segmentation and service retirement."
        )
    long_term.append(
        "Repeat this authorized assessment on a scheduled basis and track remediation progress against the saved asset inventory and findings history."
    )

    if not immediate:
        immediate = ["No immediate high-priority remediation items were identified from the current saved findings."]
    if not short_term:
        short_term = ["Use the current report as a baseline and review medium-risk configuration gaps during the next planned maintenance window."]

    return immediate, short_term, unique_texts(long_term, limit=4)


def business_impacts(findings: list[Finding]) -> list[str]:
    prioritized = sorted(findings, key=severity_sort_key)
    impacts = unique_texts([finding.business_impact for finding in prioritized], limit=5)
    if impacts:
        return impacts
    return [
        "The current assessment did not record business-impact text, so leadership should treat the report as an asset and control baseline until further validation is added."
    ]


def executive_summary_lines(
    assessment: Assessment,
    severity_counts: dict[str, int],
    host_count: int,
    risk_rating: str,
) -> list[str]:
    total_findings_count = sum(severity_counts.values())
    lines = [
        (
            f"This authorized internal assessment reviewed {host_count} discovered hosts across the saved scope for "
            f"{assessment.client_name or 'the organization'} on {format_assessment_date(assessment.assessment_date)}."
        ),
        (
            f"The current overall risk rating is {risk_rating}. The saved data shows {total_findings_count} findings, "
            f"including {severity_counts['Critical']} critical, {severity_counts['High']} high, and {severity_counts['Medium']} medium items."
        ),
    ]
    if severity_counts["High"] > 0 or severity_counts["Critical"] > 0:
        lines.append(
            "Leadership attention is recommended because a small number of exposed services or weak configurations could have an outsized operational impact if left unaddressed."
        )
    else:
        lines.append(
            "The current issues lean toward hardening and inventory follow-up rather than immediate outage-level exposure, which makes this a good time to plan corrective work before conditions worsen."
        )
    return lines


def categorize_findings(findings: list[Finding]) -> tuple[list[Finding], list[Finding], list[Finding]]:
    web_findings: list[Finding] = []
    tls_findings: list[Finding] = []
    configuration_findings: list[Finding] = []

    for finding in sorted(findings, key=severity_sort_key):
        if finding.rule_key in CERTIFICATE_ISSUE_RULE_KEYS or finding.category == "transport-security":
            tls_findings.append(finding)
        elif finding.category == "web-security":
            web_findings.append(finding)
        else:
            configuration_findings.append(finding)

    return web_findings, tls_findings, configuration_findings


def load_report_context(db: Session, *, assessment_id: int) -> ReportContext:
    assessment = db.scalar(select(Assessment).where(Assessment.id == assessment_id))
    if assessment is None:
        raise ValueError("Assessment not found.")
    settings = get_setting_map(db)

    scopes = list(
        db.scalars(
            select(Scope)
            .where(Scope.assessment_id == assessment_id)
            .order_by(Scope.created_at.asc())
        )
    )
    hosts = list(
        db.scalars(
            select(Host)
            .options(selectinload(Host.ports), selectinload(Host.services))
            .where(Host.assessment_id == assessment_id)
            .order_by(Host.address.asc())
        )
    )
    findings = list(
        db.scalars(
            select(Finding)
            .options(
                selectinload(Finding.host),
                selectinload(Finding.service).selectinload(Service.port),
            )
            .where(Finding.assessment_id == assessment_id)
            .order_by(Finding.created_at.desc())
        )
    )
    scan_jobs = list(
        db.scalars(
            select(ScanJob)
            .where(ScanJob.assessment_id == assessment_id)
            .order_by(ScanJob.created_at.desc())
        )
    )

    severity_counts = calculate_severity_counts(findings)
    overall_risk_rating = calculate_overall_risk_rating(severity_counts)
    host_views = build_host_views(hosts, findings)
    top_findings = sorted(findings, key=severity_sort_key)[:5]
    top_hosts = host_views[:5]
    web_findings, tls_findings, configuration_findings = categorize_findings(findings)
    immediate_actions, short_term_actions, long_term_improvements = action_plan(findings)

    return ReportContext(
        assessment=assessment,
        branding_name=(
            settings.get("report_branding_name")
            or settings.get("organization_name")
            or "Internal Security Team"
        ),
        branding_tagline=(
            settings.get("report_branding_tagline")
            or "Authorized Network Assessment Platform"
        ),
        branding_contact=(
            settings.get("report_branding_contact")
            or settings.get("organization_name")
            or "Authorized internal use only"
        ),
        scopes=scopes,
        hosts=host_views,
        findings=sorted(findings, key=severity_sort_key),
        scan_jobs=scan_jobs,
        generated_at=datetime.now(timezone.utc),
        severity_counts=severity_counts,
        overall_risk_rating=overall_risk_rating,
        overall_risk_score=total_risk_score(findings),
        top_findings=top_findings,
        top_hosts=top_hosts,
        web_findings=web_findings,
        tls_findings=tls_findings,
        configuration_findings=configuration_findings,
        executive_summary=executive_summary_lines(
            assessment,
            severity_counts,
            len(host_views),
            overall_risk_rating,
        ),
        key_findings=finding_highlights(findings),
        business_impact_points=business_impacts(findings),
        immediate_actions=immediate_actions,
        short_term_actions=short_term_actions,
        long_term_improvements=long_term_improvements,
    )


def severity_label_html(severity: str) -> str:
    normalized = normalize_severity(severity)
    color = SEVERITY_COLORS.get(normalized, "#5b7083")
    return (
        f"<span class='severity-pill severity-pill--{slugify(normalized)}' "
        f"style='background:{color};'>{html_escape(normalized)}</span>"
    )


def risk_distribution_svg(counts: dict[str, int]) -> str:
    width = 700
    height = 240
    chart_left = 80
    chart_bottom = 170
    bar_width = 90
    gap = 30
    max_count = max(max(counts.values()), 1)
    bars: list[str] = []

    for index, severity in enumerate(SEVERITY_ORDER):
        x = chart_left + index * (bar_width + gap)
        count = counts[severity]
        bar_height = int((count / max_count) * 120)
        y = chart_bottom - bar_height
        color = SEVERITY_COLORS[severity]
        bars.append(
            f"<rect x='{x}' y='{y}' width='{bar_width}' height='{bar_height}' rx='10' fill='{color}' opacity='0.9'></rect>"
        )
        bars.append(
            f"<text x='{x + bar_width / 2}' y='{chart_bottom + 22}' text-anchor='middle' font-size='13' fill='#304254'>{html_escape(severity)}</text>"
        )
        bars.append(
            f"<text x='{x + bar_width / 2}' y='{max(y - 10, 20)}' text-anchor='middle' font-size='14' font-weight='700' fill='#1f2d3d'>{count}</text>"
        )

    return (
        f"<svg viewBox='0 0 {width} {height}' class='risk-chart' role='img' aria-label='Risk distribution chart'>"
        f"<rect x='0' y='0' width='{width}' height='{height}' rx='18' fill='#f5f7fb'></rect>"
        "<text x='28' y='34' font-size='18' font-weight='700' fill='#1f2d3d'>Risk Distribution</text>"
        "<line x1='60' y1='170' x2='660' y2='170' stroke='#c6d3e0' stroke-width='2'></line>"
        + "".join(bars)
        + "</svg>"
    )


def html_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "<div class='empty-note'>No data was recorded for this section.</div>"
    header_html = "".join(f"<th>{html_escape(header)}</th>" for header in headers)
    row_html = []
    for row in rows:
        row_html.append(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        )
    return (
        "<div class='table-wrap'><table class='report-table'>"
        f"<thead><tr>{header_html}</tr></thead><tbody>{''.join(row_html)}</tbody></table></div>"
    )


def html_list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{html_escape(item)}</li>" for item in items) + "</ul>"


def html_findings_rows(
    findings: list[Finding],
    *,
    include_score: bool = False,
    include_explanation: bool = False,
) -> list[list[str]]:
    rows: list[list[str]] = []
    for finding in findings:
        row = [
            severity_label_html(finding.severity),
            html_escape(finding.title),
            html_escape(finding.affected_host or "Assessment-wide"),
            html_escape(
                f"{finding.port_number or 'Host'} / {finding.service_name or 'general'}"
            ),
            html_escape(finding.evidence or finding.technical_explanation),
        ]
        if include_score:
            row.append(str(finding_risk_score(finding)))
        if include_explanation:
            row.append(html_escape(finding.technical_explanation or finding.description))
        row.append(html_escape(finding.remediation))
        rows.append(row)
    return rows


def html_host_cards(context: ReportContext) -> str:
    if not context.hosts:
        return "<div class='empty-note'>No hosts were recorded in the saved assessment state.</div>"

    cards: list[str] = []
    for host_view in context.hosts:
        service_rows = []
        for service in host_view.services:
            port_number = next(
                (port.port_number for port in host_view.ports if port.id == service.port_id),
                0,
            )
            service_rows.append(
                [
                    str(port_number),
                    html_escape(service.name or "unknown"),
                    html_escape(service.product or "Unknown"),
                    html_escape(service.version or "Unavailable"),
                    html_escape(service.banner or "No safe banner captured"),
                ]
            )
        cards.append(
            "<section class='host-card'>"
            f"<h3>{html_escape(host_view.host.address)}"
            + (
                f" <span class='muted'>| {html_escape(host_view.host.hostname)}</span>"
                if host_view.host.hostname
                else ""
            )
            + "</h3>"
            f"<p><strong>Status:</strong> {html_escape(host_view.host.status)} | "
            f"<strong>Device:</strong> {html_escape(host_view.host.device_type)} | "
            f"<strong>Risk score:</strong> {host_view.risk_score}</p>"
            + html_table(
                ["Port", "Service", "Product", "Version", "Banner"],
                service_rows,
            )
            + (
                "<div class='host-findings'>"
                "<h4>Findings</h4>"
                + html_table(
                    ["Severity", "Title", "Host / Service", "Evidence", "Risk Score", "Technical Explanation", "Remediation"],
                    html_findings_rows(
                        host_view.findings,
                        include_score=True,
                        include_explanation=True,
                    ),
                )
                + "</div>"
                if host_view.findings
                else "<div class='empty-note'>No findings were associated with this host.</div>"
            )
            + "</section>"
        )
    return "".join(cards)


def report_stylesheet() -> str:
    return """
    <style>
      :root {
        --ink: #1f2d3d;
        --muted: #5b7083;
        --panel: #ffffff;
        --panel-soft: #f5f7fb;
        --line: #d8e1ea;
        --accent: #0c4a6e;
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: "Segoe UI", Tahoma, sans-serif;
        color: var(--ink);
        background: #eef3f8;
        line-height: 1.58;
      }
      .page-shell {
        width: 1040px;
        margin: 0 auto;
        padding: 38px;
      }
      .cover {
        padding: 56px 60px;
        border-radius: 28px;
        background: linear-gradient(145deg, #0f3e5c, #1c6689);
        color: #ffffff;
        box-shadow: 0 24px 48px rgba(15, 62, 92, 0.2);
      }
      .cover h1 {
        margin: 0 0 8px;
        font-size: 42px;
        line-height: 1.1;
      }
      .cover p {
        margin: 10px 0 0;
        max-width: 720px;
        color: rgba(255, 255, 255, 0.9);
      }
      .cover-tagline {
        font-size: 14px;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: rgba(255, 255, 255, 0.76);
      }
      .cover-grid, .summary-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 16px;
        margin-top: 28px;
      }
      .cover-card, .summary-card {
        padding: 18px 20px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.12);
        backdrop-filter: blur(6px);
      }
      .summary-card {
        background: var(--panel);
        border: 1px solid var(--line);
      }
      .summary-card strong, .cover-card strong {
        display: block;
        font-size: 22px;
        margin-top: 6px;
      }
      .section {
        margin-top: 26px;
        padding: 28px 32px;
        border-radius: 22px;
        background: var(--panel);
        border: 1px solid var(--line);
        box-shadow: 0 10px 26px rgba(31, 45, 61, 0.04);
      }
      h2 {
        margin: 0 0 14px;
        font-size: 26px;
        color: var(--accent);
      }
      h3 {
        margin: 0 0 10px;
        font-size: 20px;
      }
      h4 {
        margin: 12px 0 8px;
        font-size: 16px;
      }
      p, ul { margin: 12px 0; }
      ul { padding-left: 20px; }
      .muted { color: var(--muted); }
      .severity-pill {
        display: inline-block;
        padding: 6px 10px;
        border-radius: 999px;
        color: #ffffff;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.02em;
      }
      .report-table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 12px;
        font-size: 13px;
      }
      .report-table th {
        text-align: left;
        padding: 12px;
        background: #ecf3f9;
        color: #244158;
        border-bottom: 1px solid var(--line);
      }
      .report-table td {
        padding: 12px;
        border-bottom: 1px solid #e7edf4;
        vertical-align: top;
      }
      .table-wrap { overflow-x: auto; }
      .empty-note {
        margin-top: 12px;
        padding: 18px;
        border-radius: 16px;
        background: var(--panel-soft);
        color: var(--muted);
      }
      .host-card + .host-card { margin-top: 18px; }
      .host-card {
        padding: 20px;
        border: 1px solid var(--line);
        border-radius: 18px;
        background: #fbfdff;
      }
      .toc a {
        color: var(--accent);
        text-decoration: none;
      }
      pre {
        white-space: pre-wrap;
        word-break: break-word;
        background: #0f1720;
        color: #ecf3f9;
        padding: 16px;
        border-radius: 16px;
        font-size: 12px;
      }
      .risk-chart { width: 100%; max-width: 100%; margin-top: 14px; }
      .footer-note {
        margin-top: 24px;
        color: var(--muted);
        font-size: 12px;
      }
    </style>
    """


def render_executive_html(context: ReportContext) -> str:
    top_risk_rows = [
        [
            severity_label_html(finding.severity),
            html_escape(finding.title),
            html_escape(finding.affected_host or "Assessment-wide"),
            html_escape(finding.business_impact),
            html_escape(finding.remediation),
        ]
        for finding in context.top_findings
    ]
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{html_escape(REPORT_TITLES['executive'])}</title>
    {report_stylesheet()}
  </head>
  <body>
    <div class="page-shell">
      <section class="cover">
        <p>{html_escape(context.branding_name)}</p>
        <h1>{html_escape(context.assessment.project_name)}</h1>
        <p class="cover-tagline">{html_escape(context.branding_tagline)}</p>
        <p>{html_escape(REPORT_TITLES['executive'])} for {html_escape(context.assessment.client_name or 'internal stakeholders')}.</p>
        <div class="cover-grid">
          <div class="cover-card"><span>Date</span><strong>{html_escape(format_assessment_date(context.assessment.assessment_date))}</strong></div>
          <div class="cover-card"><span>Overall Risk</span><strong>{html_escape(context.overall_risk_rating)}</strong></div>
          <div class="cover-card"><span>Hosts Reviewed</span><strong>{len(context.hosts)}</strong></div>
          <div class="cover-card"><span>Findings</span><strong>{len(context.findings)}</strong></div>
        </div>
      </section>

      <section class="section">
        <h2>Assessment Overview</h2>
        <div class="summary-grid">
          <div class="summary-card"><span>Client</span><strong>{html_escape(context.assessment.client_name or 'Internal Authorized Client')}</strong></div>
          <div class="summary-card"><span>Assessor</span><strong>{html_escape(context.assessment.assessor_name)}</strong></div>
          <div class="summary-card"><span>Risk Score</span><strong>{context.overall_risk_score}</strong></div>
          <div class="summary-card"><span>Scan Intensity</span><strong>{html_escape(context.assessment.scan_intensity)}</strong></div>
        </div>
        <p>{html_escape(context.assessment.description or 'The saved assessment description was not provided, so this report uses the stored scope, hosts, services, and findings as the authoritative record.')}</p>
      </section>

      <section class="section">
        <h2>Scope</h2>
        {html_list(summarize_scope(context.scopes))}
      </section>

      <section class="section">
        <h2>Executive Summary</h2>
        {''.join(f'<p>{html_escape(line)}</p>' for line in context.executive_summary)}
      </section>

      <section class="section">
        <h2>Key Findings</h2>
        {html_list(context.key_findings)}
      </section>

      <section class="section">
        <h2>Top 5 Risks</h2>
        {html_table(["Severity", "Finding", "Affected Area", "Business Impact", "Recommended Action"], top_risk_rows)}
      </section>

      <section class="section">
        <h2>Risk Distribution Chart</h2>
        {risk_distribution_svg(context.severity_counts)}
      </section>

      <section class="section">
        <h2>Business Impact</h2>
        {html_list(context.business_impact_points)}
      </section>

      <section class="section">
        <h2>Recommended Action Plan</h2>
        <h3>Immediate Actions</h3>
        {html_list(context.immediate_actions)}
        <h3>Short-term Actions</h3>
        {html_list(context.short_term_actions)}
        <h3>Long-term Improvements</h3>
        {html_list(context.long_term_improvements)}
      </section>

      <section class="section">
        <h2>Conclusion</h2>
        <p>This report reflects only the safe host discovery, service enumeration, web evidence, TLS inspection, and risk rules currently built into the local platform. It is intended to help management prioritize remediation and plan follow-up work without requiring technical readers to interpret raw scan data directly.</p>
      </section>

      <div class="footer-note">Generated {html_escape(format_datetime(context.generated_at))}. {html_escape(report_footer_text(context))}.</div>
    </div>
  </body>
</html>"""


def scan_configuration_rows(scan_jobs: list[ScanJob]) -> list[list[str]]:
    rows: list[list[str]] = []
    for scan_job in scan_jobs:
        rows.append(
            [
                html_escape(scan_job.name),
                html_escape(scan_job.job_type),
                html_escape(scan_job.profile_name),
                html_escape(scan_job.scan_intensity),
                html_escape(scan_job.status),
                html_escape("Yes" if scan_job.include_safe_checks else "No"),
            ]
        )
    return rows


def technical_toc() -> str:
    items = [
        ("scope-methodology", "Scope and Methodology"),
        ("scan-configuration", "Scan Configuration"),
        ("asset-results", "Asset Discovery Results"),
        ("host-services", "Per-host Open Ports and Services"),
        ("web-findings", "Web Findings"),
        ("tls-findings", "TLS Findings"),
        ("configuration-findings", "Vulnerability and Configuration Findings"),
        ("appendix", "Appendix and Raw Scan Summary"),
    ]
    return (
        "<ol class='toc'>"
        + "".join(
            f"<li><a href='#{anchor}'>{html_escape(title)}</a></li>"
            for anchor, title in items
        )
        + "</ol>"
    )


def raw_scan_summary_html(scan_jobs: list[ScanJob]) -> str:
    if not scan_jobs:
        return "<div class='empty-note'>No scan jobs were recorded for this assessment.</div>"

    blocks: list[str] = []
    for scan_job in scan_jobs:
        payload = {
            "name": scan_job.name,
            "job_type": scan_job.job_type,
            "status": scan_job.status,
            "profile_name": scan_job.profile_name,
            "scan_intensity": scan_job.scan_intensity,
            "requested_targets": scan_job.requested_targets,
            "result_summary": scan_job.result_summary,
        }
        blocks.append(
            "<section class='host-card'>"
            f"<h4>{html_escape(scan_job.name)}</h4>"
            f"<pre>{html_escape(json.dumps(payload, indent=2, sort_keys=True))}</pre>"
            "</section>"
        )
    return "".join(blocks)


def render_technical_html(context: ReportContext) -> str:
    asset_rows = [
        [
            html_escape(host_view.host.address),
            html_escape(host_view.host.hostname or "Unavailable"),
            html_escape(host_view.host.status),
            html_escape(host_view.host.device_type),
            str(len(host_view.ports)),
            str(host_view.risk_score),
        ]
        for host_view in context.hosts
    ]
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>{html_escape(REPORT_TITLES['technical'])}</title>
    {report_stylesheet()}
  </head>
  <body>
    <div class="page-shell">
      <section class="cover">
        <p>{html_escape(context.branding_name)}</p>
        <h1>{html_escape(context.assessment.project_name)}</h1>
        <p class="cover-tagline">{html_escape(context.branding_tagline)}</p>
        <p>{html_escape(REPORT_TITLES['technical'])} for infrastructure, security, and operations teams.</p>
        <div class="cover-grid">
          <div class="cover-card"><span>Date</span><strong>{html_escape(format_assessment_date(context.assessment.assessment_date))}</strong></div>
          <div class="cover-card"><span>Assessor</span><strong>{html_escape(context.assessment.assessor_name)}</strong></div>
          <div class="cover-card"><span>Overall Risk</span><strong>{html_escape(context.overall_risk_rating)}</strong></div>
          <div class="cover-card"><span>Saved Findings</span><strong>{len(context.findings)}</strong></div>
        </div>
      </section>

      <section class="section">
        <h2>Table of Contents</h2>
        {technical_toc()}
      </section>

      <section class="section" id="scope-methodology">
        <h2>Scope and Methodology</h2>
        <p>The report is based on the current saved assessment state only. Discovery and enumeration data comes from safe host discovery, safe TCP enumeration, lightweight web checks, and non-aggressive TLS inspection inside the authorized scope.</p>
        {html_table(["Scope", "Included Targets", "Excluded Targets", "Authorization"], scope_targets_table(context.scopes))}
      </section>

      <section class="section" id="scan-configuration">
        <h2>Scan Configuration</h2>
        {html_table(["Scan Job", "Type", "Profile", "Intensity", "Status", "Safe Checks"], scan_configuration_rows(context.scan_jobs))}
      </section>

      <section class="section" id="asset-results">
        <h2>Asset Discovery Results</h2>
        {html_table(["Host", "Hostname", "Status", "Device Type", "Open TCP Ports", "Risk Score"], asset_rows)}
      </section>

      <section class="section" id="host-services">
        <h2>Per-host Open Ports and Services</h2>
        {html_host_cards(context)}
      </section>

      <section class="section" id="web-findings">
        <h2>Web Findings</h2>
        {html_table(["Severity", "Title", "Host", "Port / Service", "Evidence", "Risk Score", "Technical Explanation", "Remediation"], html_findings_rows(context.web_findings, include_score=True, include_explanation=True))}
      </section>

      <section class="section" id="tls-findings">
        <h2>TLS Findings</h2>
        {html_table(["Severity", "Title", "Host", "Port / Service", "Evidence", "Risk Score", "Technical Explanation", "Remediation"], html_findings_rows(context.tls_findings, include_score=True, include_explanation=True))}
      </section>

      <section class="section" id="configuration-findings">
        <h2>Vulnerability and Configuration Findings</h2>
        {html_table(["Severity", "Title", "Host", "Port / Service", "Evidence", "Risk Score", "Technical Explanation", "Remediation"], html_findings_rows(context.configuration_findings, include_score=True, include_explanation=True))}
      </section>

      <section class="section" id="appendix">
        <h2>Appendix</h2>
        <h3>Risk Distribution</h3>
        {risk_distribution_svg(context.severity_counts)}
        <h3>Raw Scan Summary</h3>
        {raw_scan_summary_html(context.scan_jobs)}
      </section>

      <div class="footer-note">Generated {html_escape(format_datetime(context.generated_at))}. {html_escape(report_footer_text(context))}.</div>
    </div>
  </body>
</html>"""


def pdf_imports():
    try:
        from reportlab.graphics.charts.barcharts import VerticalBarChart
        from reportlab.graphics.shapes import Drawing, Rect, String
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            ListFlowable,
            ListItem,
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:  # pragma: no cover - dependency guidance
        raise RuntimeError(
            "PDF generation requires the 'reportlab' package. Install backend dependencies from requirements.txt before generating reports."
        ) from exc

    return {
        "VerticalBarChart": VerticalBarChart,
        "Drawing": Drawing,
        "Rect": Rect,
        "String": String,
        "colors": colors,
        "TA_CENTER": TA_CENTER,
        "TA_LEFT": TA_LEFT,
        "A4": A4,
        "ParagraphStyle": ParagraphStyle,
        "getSampleStyleSheet": getSampleStyleSheet,
        "inch": inch,
        "ListFlowable": ListFlowable,
        "ListItem": ListItem,
        "PageBreak": PageBreak,
        "Paragraph": Paragraph,
        "SimpleDocTemplate": SimpleDocTemplate,
        "Spacer": Spacer,
        "Table": Table,
        "TableStyle": TableStyle,
    }


def pdf_styles(deps: dict[str, Any]) -> dict[str, Any]:
    colors = deps["colors"]
    base = deps["getSampleStyleSheet"]()
    ParagraphStyle = deps["ParagraphStyle"]
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontSize=28,
            leading=34,
            alignment=deps["TA_LEFT"],
            textColor=colors.HexColor("#123c5a"),
            spaceAfter=18,
        ),
        "heading": ParagraphStyle(
            "SectionHeading",
            parent=base["Heading2"],
            fontSize=18,
            leading=24,
            textColor=colors.HexColor("#0c4a6e"),
            spaceAfter=8,
            spaceBefore=10,
        ),
        "subheading": ParagraphStyle(
            "SectionSubheading",
            parent=base["Heading3"],
            fontSize=13,
            leading=18,
            textColor=colors.HexColor("#244158"),
            spaceAfter=6,
            spaceBefore=6,
        ),
        "body": ParagraphStyle(
            "BodyCopy",
            parent=base["BodyText"],
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#24313f"),
            spaceAfter=6,
        ),
        "muted": ParagraphStyle(
            "MutedCopy",
            parent=base["BodyText"],
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#5b7083"),
            spaceAfter=6,
        ),
        "cover_meta": ParagraphStyle(
            "CoverMeta",
            parent=base["BodyText"],
            fontSize=11,
            leading=15,
            textColor=colors.white,
            spaceAfter=4,
        ),
    }


def pdf_severity_color(deps: dict[str, Any], severity: str):
    colors = deps["colors"]
    return colors.HexColor(SEVERITY_COLORS.get(normalize_severity(severity), "#5b7083"))


def make_pdf_table(
    deps: dict[str, Any],
    rows: list[list[Any]],
    *,
    column_widths: list[float] | None = None,
    header_background: str = "#eaf2f8",
    body_font_size: int = 8,
):
    Table = deps["Table"]
    TableStyle = deps["TableStyle"]
    colors = deps["colors"]
    table = Table(rows, colWidths=column_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(header_background)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#16334a")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), body_font_size),
                ("LEADING", (0, 0), (-1, -1), body_font_size + 3),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d6dde6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbfdff")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def pdf_bar_chart(deps: dict[str, Any], counts: dict[str, int]):
    Drawing = deps["Drawing"]
    Rect = deps["Rect"]
    String = deps["String"]
    VerticalBarChart = deps["VerticalBarChart"]
    colors = deps["colors"]

    drawing = Drawing(420, 220)
    drawing.add(Rect(0, 0, 420, 220, fillColor=colors.HexColor("#f5f7fb"), strokeColor=colors.HexColor("#d8e1ea")))

    if sum(counts.values()) == 0:
        drawing.add(String(110, 108, "No findings were available for charting.", fontSize=12, fillColor=colors.HexColor("#5b7083")))
        return drawing

    chart = VerticalBarChart()
    chart.x = 45
    chart.y = 45
    chart.height = 130
    chart.width = 300
    chart.data = [[counts[severity] for severity in SEVERITY_ORDER]]
    chart.categoryAxis.categoryNames = SEVERITY_ORDER
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max(max(counts.values()), 1) + 1
    chart.valueAxis.valueStep = max(1, (chart.valueAxis.valueMax // 4) or 1)
    chart.barWidth = 22
    chart.groupSpacing = 12
    chart.barSpacing = 8
    chart.bars[0].fillColor = colors.HexColor("#0c4a6e")
    chart.bars[0].strokeColor = colors.HexColor("#0c4a6e")
    chart.categoryAxis.labels.fontSize = 8
    chart.valueAxis.labels.fontSize = 8
    drawing.add(chart)

    legend_x = 360
    legend_y = 170
    for index, severity in enumerate(SEVERITY_ORDER):
        drawing.add(
            Rect(
                legend_x,
                legend_y - index * 26,
                12,
                12,
                fillColor=colors.HexColor(SEVERITY_COLORS[severity]),
                strokeColor=colors.HexColor(SEVERITY_COLORS[severity]),
            )
        )
        drawing.add(
            String(
                legend_x + 18,
                legend_y + 2 - index * 26,
                f"{severity}: {counts[severity]}",
                fontSize=8,
                fillColor=colors.HexColor("#24313f"),
            )
        )

    return drawing


def pdf_bullets(deps: dict[str, Any], styles: dict[str, Any], items: list[str]):
    ListFlowable = deps["ListFlowable"]
    ListItem = deps["ListItem"]
    Paragraph = deps["Paragraph"]
    return ListFlowable(
        [
            ListItem(Paragraph(html_escape(item), styles["body"]))
            for item in items
        ],
        bulletType="bullet",
    )


def findings_table_rows_for_pdf(
    deps: dict[str, Any],
    styles: dict[str, Any],
    findings: list[Finding],
    *,
    include_score: bool = False,
    include_explanation: bool = False,
) -> list[list[Any]]:
    Paragraph = deps["Paragraph"]
    header_row: list[Any] = [
        Paragraph("Severity", styles["body"]),
        Paragraph("Finding", styles["body"]),
        Paragraph("Host / Service", styles["body"]),
        Paragraph("Evidence", styles["body"]),
    ]
    if include_score:
        header_row.append(Paragraph("Risk Score", styles["body"]))
    if include_explanation:
        header_row.append(Paragraph("Technical Explanation", styles["body"]))
    header_row.append(Paragraph("Remediation", styles["body"]))
    rows: list[list[Any]] = [header_row]

    for finding in findings:
        service_descriptor = f"{finding.port_number or 'Host'} / {finding.service_name or 'general'}"
        row: list[Any] = [
            Paragraph(normalize_severity(finding.severity), styles["body"]),
            Paragraph(html_escape(finding.title), styles["body"]),
            Paragraph(
                f"{html_escape(finding.affected_host or 'Assessment-wide')}<br/>{html_escape(service_descriptor)}",
                styles["body"],
            ),
            Paragraph(html_escape(finding.evidence or finding.technical_explanation), styles["body"]),
        ]
        if include_score:
            row.append(Paragraph(str(finding_risk_score(finding)), styles["body"]))
        if include_explanation:
            row.append(
                Paragraph(
                    html_escape(finding.technical_explanation or finding.description),
                    styles["body"],
                )
            )
        row.append(Paragraph(html_escape(finding.remediation), styles["body"]))
        rows.append(row)
    return rows


def add_pdf_cover(story: list[Any], deps: dict[str, Any], styles: dict[str, Any], context: ReportContext, report_type: str) -> None:
    Paragraph = deps["Paragraph"]
    Spacer = deps["Spacer"]
    colors = deps["colors"]
    Drawing = deps["Drawing"]
    Rect = deps["Rect"]

    cover = Drawing(520, 160)
    cover.add(Rect(0, 0, 520, 160, fillColor=colors.HexColor("#0f3e5c"), strokeColor=colors.HexColor("#0f3e5c")))
    story.append(cover)
    story.append(Spacer(1, -140))
    story.append(Paragraph(html_escape(context.branding_name), styles["cover_meta"]))
    story.append(Paragraph(REPORT_TITLES[report_type], styles["cover_meta"]))
    story.append(Paragraph(html_escape(context.assessment.project_name), styles["title"]))
    story.append(Paragraph(html_escape(context.branding_tagline), styles["cover_meta"]))
    story.append(Paragraph(f"For {html_escape(context.assessment.client_name or 'internal stakeholders')}", styles["cover_meta"]))
    story.append(Paragraph(f"Assessment date: {html_escape(format_assessment_date(context.assessment.assessment_date))}", styles["cover_meta"]))
    story.append(Paragraph(f"Generated: {html_escape(format_datetime(context.generated_at))}", styles["cover_meta"]))
    story.append(Spacer(1, 80))


def render_executive_pdf(context: ReportContext, destination: Path) -> None:
    deps = pdf_imports()
    styles = pdf_styles(deps)
    Paragraph = deps["Paragraph"]
    Spacer = deps["Spacer"]
    PageBreak = deps["PageBreak"]
    SimpleDocTemplate = deps["SimpleDocTemplate"]

    doc = SimpleDocTemplate(
        str(destination),
        pagesize=deps["A4"],
        leftMargin=36,
        rightMargin=36,
        topMargin=40,
        bottomMargin=36,
    )
    doc._footer_text = report_footer_text(context)
    story: list[Any] = []
    add_pdf_cover(story, deps, styles, context, "executive")
    story.append(PageBreak())

    story.append(Paragraph("Assessment Overview", styles["heading"]))
    story.append(Paragraph(html_escape(context.assessment.description or "The saved assessment description was not provided."), styles["body"]))
    story.append(make_pdf_table(
        deps,
        [
            ["Client", "Assessor", "Overall Risk", "Risk Score"],
            [
                context.assessment.client_name or "Internal Authorized Client",
                context.assessment.assessor_name,
                context.overall_risk_rating,
                str(context.overall_risk_score),
            ],
        ],
        column_widths=[110, 110, 110, 110],
    ))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Scope", styles["heading"]))
    story.append(pdf_bullets(deps, styles, summarize_scope(context.scopes)))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Executive Summary", styles["heading"]))
    for line in context.executive_summary:
        story.append(Paragraph(html_escape(line), styles["body"]))

    story.append(Paragraph("Key Findings", styles["heading"]))
    story.append(pdf_bullets(deps, styles, context.key_findings))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Top 5 Risks", styles["heading"]))
    story.append(
        make_pdf_table(
            deps,
            findings_table_rows_for_pdf(deps, styles, context.top_findings),
            column_widths=[50, 120, 110, 105, 105],
            body_font_size=7,
        )
    )
    story.append(Spacer(1, 12))

    story.append(Paragraph("Risk Distribution", styles["heading"]))
    story.append(pdf_bar_chart(deps, context.severity_counts))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Business Impact", styles["heading"]))
    story.append(pdf_bullets(deps, styles, context.business_impact_points))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Recommended Action Plan", styles["heading"]))
    story.append(Paragraph("Immediate Actions", styles["subheading"]))
    story.append(pdf_bullets(deps, styles, context.immediate_actions))
    story.append(Paragraph("Short-term Actions", styles["subheading"]))
    story.append(pdf_bullets(deps, styles, context.short_term_actions))
    story.append(Paragraph("Long-term Improvements", styles["subheading"]))
    story.append(pdf_bullets(deps, styles, context.long_term_improvements))

    story.append(Paragraph("Conclusion", styles["heading"]))
    story.append(
        Paragraph(
            "This executive report translates the saved assessment evidence into business-facing priorities so management can sponsor remediation, track accountability, and plan the next review window without working from raw scan detail.",
            styles["body"],
        )
    )

    doc.build(story, onFirstPage=add_pdf_page_number, onLaterPages=add_pdf_page_number)


def render_technical_pdf(context: ReportContext, destination: Path) -> None:
    deps = pdf_imports()
    styles = pdf_styles(deps)
    Paragraph = deps["Paragraph"]
    Spacer = deps["Spacer"]
    PageBreak = deps["PageBreak"]
    SimpleDocTemplate = deps["SimpleDocTemplate"]

    doc = SimpleDocTemplate(
        str(destination),
        pagesize=deps["A4"],
        leftMargin=30,
        rightMargin=30,
        topMargin=36,
        bottomMargin=32,
    )
    doc._footer_text = report_footer_text(context)
    story: list[Any] = []
    add_pdf_cover(story, deps, styles, context, "technical")
    story.append(PageBreak())

    story.append(Paragraph("Table of Contents", styles["heading"]))
    story.append(
        pdf_bullets(
            deps,
            styles,
            [
                "Scope and Methodology",
                "Scan Configuration",
                "Asset Discovery Results",
                "Per-host Open Ports and Services",
                "Web Findings",
                "TLS Findings",
                "Vulnerability and Configuration Findings",
                "Appendix and Raw Scan Summary",
            ],
        )
    )
    story.append(PageBreak())

    story.append(Paragraph("Scope and Methodology", styles["heading"]))
    story.append(
        Paragraph(
            "The technical report is built from the saved assessment, scope, host, service, web, TLS, and finding records currently stored in the local platform. Collection methods remain limited to safe discovery, safe TCP enumeration, safe web requests, and non-aggressive TLS inspection.",
            styles["body"],
        )
    )
    scope_rows = [["Scope", "Included Targets", "Excluded Targets", "Authorization"]]
    scope_rows.extend(scope_targets_table(context.scopes))
    story.append(make_pdf_table(deps, scope_rows, column_widths=[90, 180, 100, 80], body_font_size=7))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Scan Configuration", styles["heading"]))
    scan_rows = [["Scan Job", "Type", "Profile", "Intensity", "Status", "Safe Checks"]]
    scan_rows.extend(
        [
            [
                scan_job.name,
                scan_job.job_type,
                scan_job.profile_name,
                scan_job.scan_intensity,
                scan_job.status,
                "Yes" if scan_job.include_safe_checks else "No",
            ]
            for scan_job in context.scan_jobs
        ]
    )
    story.append(make_pdf_table(deps, scan_rows, column_widths=[110, 70, 75, 55, 60, 65], body_font_size=7))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Asset Discovery Results", styles["heading"]))
    asset_rows = [["Host", "Hostname", "Status", "Device", "Open Ports", "Risk Score"]]
    asset_rows.extend(
        [
            [
                host_view.host.address,
                host_view.host.hostname or "Unavailable",
                host_view.host.status,
                host_view.host.device_type,
                str(len(host_view.ports)),
                str(host_view.risk_score),
            ]
            for host_view in context.hosts
        ]
    )
    story.append(make_pdf_table(deps, asset_rows, column_widths=[80, 90, 55, 65, 60, 60], body_font_size=7))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Per-host Open Ports and Services", styles["heading"]))
    for host_view in context.hosts:
        story.append(
            Paragraph(
                html_escape(
                    f"{host_view.host.address} | {host_view.host.hostname or 'No hostname'} | {host_view.host.device_type}"
                ),
                styles["subheading"],
            )
        )
        story.append(
            Paragraph(
                html_escape(
                    f"Status: {host_view.host.status} | Discovery method: {host_view.host.discovery_method or 'Unavailable'} | Vendor: {host_view.host.vendor_name or 'Unknown'} | Last seen: {format_datetime(host_view.host.last_seen_at)}"
                ),
                styles["muted"],
            )
        )
        service_rows = [["Port", "Service", "Product", "Version", "Confidence", "Banner"]]
        for service in host_view.services:
            port_number = next(
                (port.port_number for port in host_view.ports if port.id == service.port_id),
                0,
            )
            service_rows.append(
                [
                    str(port_number),
                    service.name or "unknown",
                    service.product or "Unknown",
                    service.version or "Unavailable",
                    f"{round(service.confidence * 100)}%",
                    service.banner or "No safe banner captured",
                ]
            )
        story.append(make_pdf_table(deps, service_rows, column_widths=[35, 55, 85, 60, 45, 210], body_font_size=7))
        if host_view.findings:
            story.append(Spacer(1, 6))
            story.append(make_pdf_table(
                deps,
                findings_table_rows_for_pdf(
                    deps,
                    styles,
                    host_view.findings,
                    include_score=True,
                    include_explanation=True,
                ),
                column_widths=[40, 86, 74, 92, 40, 96, 88],
                body_font_size=7,
            ))
        story.append(Spacer(1, 10))

    story.append(PageBreak())
    story.append(Paragraph("Web Findings", styles["heading"]))
    story.append(make_pdf_table(
        deps,
        findings_table_rows_for_pdf(
            deps,
            styles,
            context.web_findings,
            include_score=True,
            include_explanation=True,
        ),
        column_widths=[40, 86, 74, 92, 40, 96, 88],
        body_font_size=7,
    ))
    story.append(Spacer(1, 12))

    story.append(Paragraph("TLS Findings", styles["heading"]))
    story.append(make_pdf_table(
        deps,
        findings_table_rows_for_pdf(
            deps,
            styles,
            context.tls_findings,
            include_score=True,
            include_explanation=True,
        ),
        column_widths=[40, 86, 74, 92, 40, 96, 88],
        body_font_size=7,
    ))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Vulnerability and Configuration Findings", styles["heading"]))
    story.append(make_pdf_table(
        deps,
        findings_table_rows_for_pdf(
            deps,
            styles,
            context.configuration_findings,
            include_score=True,
            include_explanation=True,
        ),
        column_widths=[40, 86, 74, 92, 40, 96, 88],
        body_font_size=7,
    ))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Appendix", styles["heading"]))
    story.append(Paragraph("Risk Distribution", styles["subheading"]))
    story.append(pdf_bar_chart(deps, context.severity_counts))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Raw Scan Summary", styles["subheading"]))
    raw_rows = [["Scan Job", "Status", "Summary"]]
    for scan_job in context.scan_jobs:
        raw_rows.append(
            [
                scan_job.name,
                scan_job.status,
                json.dumps(scan_job.result_summary, sort_keys=True),
            ]
        )
    story.append(make_pdf_table(deps, raw_rows, column_widths=[130, 70, 280], body_font_size=7))

    doc.build(story, onFirstPage=add_pdf_page_number, onLaterPages=add_pdf_page_number)


def add_pdf_page_number(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColorRGB(0.36, 0.44, 0.51)
    canvas.drawRightString(doc.pagesize[0] - 30, 18, f"Page {doc.page}")
    canvas.drawString(30, 18, getattr(doc, "_footer_text", "Authorized internal use only"))
    canvas.restoreState()


def report_file_paths(context: ReportContext, report_type: str) -> tuple[Path, Path, str]:
    runtime_paths = get_runtime_paths()
    timestamp = context.generated_at.strftime("%Y%m%d-%H%M%S")
    base_name = f"{report_type}-{slugify(context.assessment.project_name)}-{timestamp}"
    return (
        runtime_paths.reports_dir / f"{base_name}.html",
        runtime_paths.reports_dir / f"{base_name}.pdf",
        f"{REPORT_TITLES[report_type]} | {context.assessment.project_name} | {timestamp}",
    )


def write_report_files(context: ReportContext, report_type: str) -> tuple[Path, Path, str]:
    html_path, pdf_path, report_name = report_file_paths(context, report_type)
    html_content = (
        render_executive_html(context)
        if report_type == "executive"
        else render_technical_html(context)
    )
    html_path.write_text(html_content, encoding="utf-8")

    if report_type == "executive":
        render_executive_pdf(context, pdf_path)
    else:
        render_technical_pdf(context, pdf_path)

    return html_path, pdf_path, report_name


def generate_assessment_reports(
    db: Session,
    *,
    assessment_id: int,
    report_type: str,
) -> list[Report]:
    normalized_type = report_type.strip().lower()
    if normalized_type not in REPORT_TITLES:
        raise ValueError("Unsupported report type.")

    context = load_report_context(db, assessment_id=assessment_id)
    html_path, pdf_path, report_name = write_report_files(context, normalized_type)
    related_scan = latest_relevant_scan(context.scan_jobs)

    reports = [
        Report(
            assessment_id=assessment_id,
            scan_job_id=related_scan.id if related_scan else None,
            name=report_name,
            report_type=normalized_type,
            format="html",
            status="generated",
            storage_path=str(html_path),
        ),
        Report(
            assessment_id=assessment_id,
            scan_job_id=related_scan.id if related_scan else None,
            name=report_name,
            report_type=normalized_type,
            format="pdf",
            status="generated",
            storage_path=str(pdf_path),
        ),
    ]
    for report in reports:
        db.add(report)
    db.flush()
    return reports
