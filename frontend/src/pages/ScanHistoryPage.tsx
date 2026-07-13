import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { DataTable } from "../components/DataTable";
import { LoadingState } from "../components/LoadingState";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { SeverityBadge } from "../components/SeverityBadge";
import { StatusPill } from "../components/StatusPill";
import { api } from "../lib/api";
import { findingRiskScore, overallRiskLabel } from "../lib/risk";
import { formatDate } from "../lib/utils";
import type { Assessment, Finding, Host, Report, ScanJob } from "../types";

type AssessmentHistoryRow = {
  assessment: Assessment;
  latestScan: ScanJob | null;
  latestTimestamp: string;
  hostsCount: number;
  findingsCount: number;
  overallRisk: string;
  riskScore: number;
  reportCount: number;
};

function scanTypeLabel(scan: ScanJob): string {
  if (scan.job_type === "disruptive_tests") {
    return "Performance-Impacting";
  }
  return scan.job_type === "safe_enumeration" ? "Port Enumeration" : "Discovery";
}

export function ScanHistoryPage() {
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [scans, setScans] = useState<ScanJob[]>([]);
  const [hosts, setHosts] = useState<Host[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [error, setError] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [busyKey, setBusyKey] = useState("");
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [assessmentRows, scanRows, hostRows, findingRows, reportRows] =
          await Promise.all([
            api.getAssessments(),
            api.getScans(),
            api.getHosts(),
            api.getFindings(),
            api.getReports(),
          ]);
        setAssessments(assessmentRows);
        setScans(scanRows);
        setHosts(hostRows);
        setFindings(findingRows);
        setReports(reportRows);
        setError("");
      } catch (loadError) {
        setError((loadError as Error).message);
      } finally {
        setIsLoading(false);
      }
    }

    void load();
  }, []);

  const assessmentHistory = useMemo(() => {
    return assessments
      .map((assessment): AssessmentHistoryRow => {
        const relatedScans = scans
          .filter((scan) => scan.assessment_id === assessment.id)
          .sort((left, right) => {
            const leftTime = new Date(
              left.completed_at ?? left.created_at,
            ).getTime();
            const rightTime = new Date(
              right.completed_at ?? right.created_at,
            ).getTime();
            return rightTime - leftTime;
          });
        const latestScan = relatedScans[0] ?? null;
        const assessmentHosts = hosts.filter(
          (host) => host.assessment_id === assessment.id,
        );
        const assessmentFindings = findings.filter(
          (finding) => finding.assessment_id === assessment.id,
        );

        return {
          assessment,
          latestScan,
          latestTimestamp:
            latestScan?.completed_at ??
            latestScan?.created_at ??
            assessment.created_at,
          hostsCount: assessmentHosts.length,
          findingsCount: assessmentFindings.length,
          overallRisk: overallRiskLabel(assessmentFindings),
          riskScore: findingRiskScore(assessmentFindings),
          reportCount: reports.filter(
            (report) => report.assessment_id === assessment.id,
          ).length,
        };
      })
      .sort(
        (left, right) =>
          new Date(right.latestTimestamp).getTime() -
          new Date(left.latestTimestamp).getTime(),
      );
  }, [assessments, scans, hosts, findings, reports]);

  async function refreshReports() {
    setReports(await api.getReports());
  }

  async function handleRegenerate(
    assessmentId: number,
    reportType: "executive" | "technical",
  ) {
    try {
      setError("");
      setStatusMessage("");
      setBusyKey(`${assessmentId}-${reportType}`);
      const generated = await api.generateReports({
        assessment_id: assessmentId,
        report_type: reportType,
      });
      await refreshReports();
      setStatusMessage(
        `${reportType === "executive" ? "Executive" : "Technical"} reports regenerated for assessment ${assessmentId}: ${generated.length} files ready.`,
      );
    } catch (regenerateError) {
      setError((regenerateError as Error).message);
    } finally {
      setBusyKey("");
    }
  }

  return (
    <div className="page">
      <PageHeader
        title="Scan History"
        subtitle="Review past assessments, compare result volume over time, reopen historical views, and regenerate fresh report bundles from saved local evidence."
      />

      {error ? <div className="error-banner">{error}</div> : null}
      {statusMessage ? <div className="success-banner">{statusMessage}</div> : null}
      {isLoading ? (
        <LoadingState
          title="Loading History"
          message="Collecting saved assessments, scans, findings, hosts, and reports for the timeline view."
        />
      ) : null}

      <DataTable
        title="Assessment Result History"
        subtitle="Each row summarizes the latest known state of an assessment so you can reopen old results quickly."
        rows={assessmentHistory}
        searchPlaceholder="Search previous assessments"
        loading={isLoading}
        searchAccessor={(row) =>
          `${row.assessment.project_name} ${row.assessment.client_name} ${row.assessment.assessor_name} ${row.assessment.scan_intensity} ${row.overallRisk}`
        }
        rowKey={(row) => row.assessment.id}
        emptyMessage="Create assessments and run discovery or enumeration to build history."
        columns={[
          {
            header: "Assessment",
            cell: (row) => (
              <div>
                <strong>{row.assessment.project_name}</strong>
                <div className="finding-subtext">
                  {row.assessment.client_name} | {row.assessment.assessor_name}
                </div>
              </div>
            ),
          },
          {
            header: "Date / Time",
            cell: (row) => formatDate(row.latestTimestamp),
          },
          {
            header: "Scan Intensity",
            cell: (row) =>
              row.latestScan?.scan_intensity ?? row.assessment.scan_intensity,
          },
          {
            header: "Hosts",
            cell: (row) => row.hostsCount,
          },
          {
            header: "Findings",
            cell: (row) => `${row.findingsCount} | score ${row.riskScore}`,
          },
          {
            header: "Overall Risk",
            cell: (row) => <SeverityBadge severity={row.overallRisk} />,
          },
          {
            header: "Reports",
            cell: (row) => row.reportCount,
          },
          {
            header: "Open Results",
            cell: (row) => (
              <div className="table-actions">
                <Link
                  className="button button--ghost"
                  to={`/assets?assessmentId=${row.assessment.id}`}
                >
                  Assets
                </Link>
                <Link
                  className="button button--ghost"
                  to={`/findings?assessmentId=${row.assessment.id}`}
                >
                  Findings
                </Link>
                <Link
                  className="button button--ghost"
                  to={`/reports?assessmentId=${row.assessment.id}`}
                >
                  Reports
                </Link>
              </div>
            ),
          },
          {
            header: "Regenerate Reports",
            cell: (row) => (
              <div className="table-actions">
                <button
                  className="button"
                  type="button"
                  disabled={busyKey !== ""}
                  onClick={() =>
                    void handleRegenerate(row.assessment.id, "executive")
                  }
                >
                  {busyKey === `${row.assessment.id}-executive`
                    ? "Generating..."
                    : "Executive"}
                </button>
                <button
                  className="button button--ghost"
                  type="button"
                  disabled={busyKey !== ""}
                  onClick={() =>
                    void handleRegenerate(row.assessment.id, "technical")
                  }
                >
                  {busyKey === `${row.assessment.id}-technical`
                    ? "Generating..."
                    : "Technical"}
                </button>
              </div>
            ),
          },
        ]}
      />

      <Panel
        title="Scan Job Timeline"
        subtitle="Raw job history remains available for operational review and troubleshooting."
      >
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Assessment</th>
                <th>Job</th>
                <th>Type</th>
                <th>Status</th>
                <th>Intensity</th>
                <th>Progress</th>
                <th>Completed</th>
              </tr>
            </thead>
            <tbody>
              {scans.length > 0 ? (
                scans.map((scan) => {
                  const assessment = assessments.find(
                    (candidate) => candidate.id === scan.assessment_id,
                  );
                  return (
                    <tr key={scan.id}>
                      <td>{assessment?.project_name ?? `Assessment ${scan.assessment_id}`}</td>
                      <td>
                        <div>{scan.name}</div>
                        <div className="finding-subtext">{scan.profile_name}</div>
                      </td>
                      <td>{scanTypeLabel(scan)}</td>
                      <td>
                        <StatusPill label={scan.status} />
                      </td>
                      <td>{scan.scan_intensity}</td>
                      <td>{scan.progress}%</td>
                      <td>{formatDate(scan.completed_at ?? scan.created_at)}</td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={7} className="table-empty">
                    {isLoading
                      ? "Loading scan history..."
                      : "No scan jobs have been recorded yet."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
