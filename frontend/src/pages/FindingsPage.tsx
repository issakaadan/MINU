import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { LoadingState } from "../components/LoadingState";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { SeverityBadge } from "../components/SeverityBadge";
import { api } from "../lib/api";
import { buildCsvFilename, downloadCsv } from "../lib/csv";
import { formatDate } from "../lib/utils";
import type { Assessment, Finding } from "../types";

const severityOptions = [
  "all",
  "Critical",
  "High",
  "Medium",
  "Low",
  "Informational",
];

const severityWeight: Record<string, number> = {
  Critical: 5,
  High: 4,
  Medium: 3,
  Low: 2,
  Informational: 1,
};

function severityRank(severity: string): number {
  return severityWeight[severity] ?? 0;
}

export function FindingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const assessmentParam = searchParams.get("assessmentId");
  const parsedAssessmentId = assessmentParam ? Number(assessmentParam) : null;
  const selectedAssessmentId =
    parsedAssessmentId !== null && Number.isFinite(parsedAssessmentId)
      ? parsedAssessmentId
      : null;
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [severityFilter, setSeverityFilter] = useState("all");
  const [statusMessage, setStatusMessage] = useState("");
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [assessmentRows, findingRows] = await Promise.all([
          api.getAssessments(),
          api.getFindings({
            assessmentId: selectedAssessmentId ?? undefined,
          }),
        ]);
        setAssessments(assessmentRows);
        setFindings(findingRows);
        setError("");
      } catch (loadError) {
        setError((loadError as Error).message);
      } finally {
        setIsLoading(false);
      }
    }

    void load();

    const intervalId = window.setInterval(() => {
      void load();
    }, 5000);

    return () => window.clearInterval(intervalId);
  }, [selectedAssessmentId]);

  const selectedAssessment = useMemo(
    () =>
      selectedAssessmentId === null
        ? null
        : assessments.find((assessment) => assessment.id === selectedAssessmentId) ??
          null,
    [assessments, selectedAssessmentId],
  );

  const filteredFindings = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return findings.filter((finding) => {
      const matchesSeverity =
        severityFilter === "all" || finding.severity === severityFilter;
      const searchText = [
        finding.title,
        finding.category,
        finding.affected_host,
        finding.service_name,
        finding.evidence,
        finding.technical_explanation,
        finding.business_impact,
        finding.remediation,
        finding.priority,
      ]
        .join(" ")
        .toLowerCase();
      const matchesQuery =
        normalizedQuery.length === 0 || searchText.includes(normalizedQuery);
      return matchesSeverity && matchesQuery;
    });
  }, [findings, query, severityFilter]);

  const groupedFindings = useMemo(() => {
    const groups = new Map<
      string,
      { host: string; highestSeverity: string; findings: Finding[] }
    >();

    filteredFindings.forEach((finding) => {
      const host = finding.affected_host || "Assessment-wide";
      const existing = groups.get(host) ?? {
        host,
        highestSeverity: "Informational",
        findings: [],
      };

      existing.findings.push(finding);
      if (severityRank(finding.severity) > severityRank(existing.highestSeverity)) {
        existing.highestSeverity = finding.severity;
      }

      groups.set(host, existing);
    });

    return Array.from(groups.values())
      .sort((left, right) => {
        if (
          severityRank(right.highestSeverity) !==
          severityRank(left.highestSeverity)
        ) {
          return (
            severityRank(right.highestSeverity) -
            severityRank(left.highestSeverity)
          );
        }
        return right.findings.length - left.findings.length;
      })
      .map((group) => ({
        ...group,
        findings: [...group.findings].sort(
          (left, right) => severityRank(right.severity) - severityRank(left.severity),
        ),
      }));
  }, [filteredFindings]);

  function handleExportFindings() {
    const didDownload = downloadCsv(
      buildCsvFilename("findings"),
      filteredFindings.map((finding) => ({
        assessment_id: finding.assessment_id,
        severity: finding.severity,
        title: finding.title,
        affected_host: finding.affected_host || "Assessment-wide",
        port_number: finding.port_number ?? "",
        service_name: finding.service_name || "Host-level",
        priority: finding.priority,
        category: finding.category,
        source: finding.source,
        evidence: finding.evidence,
        technical_explanation: finding.technical_explanation,
        business_impact: finding.business_impact,
        remediation: finding.remediation,
        created_at: finding.created_at,
      })),
    );
    setStatusMessage(
      didDownload
        ? "Findings CSV downloaded from the current filtered view."
        : "No findings matched the current filters, so no CSV was created.",
    );
  }

  return (
    <div className="page">
      <PageHeader
        title="Findings"
        subtitle={
          selectedAssessment
            ? `The risk engine findings for ${selectedAssessment.project_name}, with host-specific evidence and remediation guidance.`
            : "The risk engine turns safe discovery and enumeration evidence into host-specific findings with remediation guidance."
        }
        actions={
          <button
            className="button button--ghost"
            disabled={isLoading || filteredFindings.length === 0}
            onClick={handleExportFindings}
            type="button"
          >
            Export Findings CSV
          </button>
        }
      />
      {isLoading ? (
        <LoadingState
          title="Loading Findings"
          message="Collecting saved assessments and generated findings from the local API."
        />
      ) : null}
      {error ? <div className="error-banner">{error}</div> : null}
      {statusMessage ? <div className="success-banner">{statusMessage}</div> : null}

      <Panel
        title="Findings Filters"
        subtitle="Filter by assessment, severity, or search across hosts, services, evidence, and remediation text."
      >
        <div className="findings-filters">
          <select
            className="input"
            value={selectedAssessmentId ?? ""}
            onChange={(event) => {
              const nextValue = event.target.value;
              const nextParams = new URLSearchParams(searchParams);
              if (nextValue) {
                nextParams.set("assessmentId", nextValue);
              } else {
                nextParams.delete("assessmentId");
              }
              setSearchParams(nextParams);
            }}
          >
            <option value="">All assessments</option>
            {assessments.map((assessment) => (
              <option key={assessment.id} value={assessment.id}>
                {assessment.project_name} | {assessment.client_name}
              </option>
            ))}
          </select>
          <input
            className="input"
            placeholder="Search title, host, service, evidence, or remediation"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <select
            className="input"
            value={severityFilter}
            onChange={(event) => setSeverityFilter(event.target.value)}
          >
            {severityOptions.map((option) => (
              <option key={option} value={option}>
                {option === "all" ? "All severities" : option}
              </option>
            ))}
          </select>
          <div className="summary-card">
            <span className="summary-card__label">Visible Findings</span>
            <strong>{filteredFindings.length}</strong>
            <div className="summary-list-inline">
              {groupedFindings.length} hosts represented
            </div>
          </div>
        </div>
      </Panel>

      <Panel
        title="Findings Register"
        subtitle="Use this register for quick triage across all generated and manual findings."
      >
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Severity</th>
                <th>Title</th>
                <th>Host</th>
                <th>Port</th>
                <th>Service</th>
                <th>Priority</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {filteredFindings.length > 0 ? (
                filteredFindings.map((finding) => (
                  <tr key={finding.id}>
                    <td>
                      <SeverityBadge severity={finding.severity} />
                    </td>
                    <td>{finding.title}</td>
                    <td>{finding.affected_host || "Assessment-wide"}</td>
                    <td>{finding.port_number ?? "Host-level"}</td>
                    <td>{finding.service_name || "Host-level"}</td>
                    <td>{finding.priority}</td>
                    <td>{formatDate(finding.created_at)}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={7} className="table-empty">
                    {isLoading
                      ? "Loading findings..."
                      : "No findings matched the current filters."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="findings-host-stack">
        {groupedFindings.length > 0 ? groupedFindings.map((group) => (
          <Panel
            key={group.host}
            title={group.host}
            subtitle={`${group.findings.length} findings | highest severity ${group.highestSeverity}`}
          >
            <div className="findings-host-table">
              <table className="table">
                <thead>
                  <tr>
                    <th>Severity</th>
                    <th>Title</th>
                    <th>Port / Service</th>
                    <th>Evidence</th>
                    <th>Business Impact</th>
                    <th>Remediation</th>
                    <th>Priority</th>
                  </tr>
                </thead>
                <tbody>
                  {group.findings.map((finding) => (
                    <tr key={`${group.host}-${finding.id}`}>
                      <td>
                        <SeverityBadge severity={finding.severity} />
                      </td>
                      <td>
                        <div className="finding-title">{finding.title}</div>
                        <div className="finding-subtext">
                          {finding.category} | {finding.source}
                        </div>
                      </td>
                      <td>
                        {finding.port_number ?? "Host-level"}
                        <div className="finding-subtext">
                          {finding.service_name || "Host-level"}
                        </div>
                      </td>
                      <td className="table-cell-wrap">
                        {finding.evidence || finding.technical_explanation}
                      </td>
                      <td className="table-cell-wrap">{finding.business_impact}</td>
                      <td className="table-cell-wrap">{finding.remediation}</td>
                      <td>{finding.priority}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        )) : (
          <Panel
            title="Per-Host Findings"
            subtitle="Host-grouped triage details will appear here when findings exist."
          >
            <div className="empty-state">
              {isLoading
                ? "Loading host-grouped findings..."
                : "No host-specific findings matched the current filters."}
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}
