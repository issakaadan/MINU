import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { DataTable } from "../components/DataTable";
import { LoadingState } from "../components/LoadingState";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { api } from "../lib/api";
import { formatDate } from "../lib/utils";
import type { Assessment, Report } from "../types";

function assessmentLabel(assessment: Assessment): string {
  return `${assessment.project_name} | ${assessment.client_name}`;
}

function reportTypeLabel(reportType: Report["report_type"]): string {
  return reportType === "executive" ? "Executive" : "Technical";
}

function reportFilename(report: Report): string {
  const pathParts = report.storage_path.split(/[\\/]/);
  return pathParts[pathParts.length - 1] || "Pending";
}

export function ReportsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const assessmentParam = searchParams.get("assessmentId");
  const parsedAssessmentId = assessmentParam ? Number(assessmentParam) : null;
  const requestedAssessmentId =
    parsedAssessmentId !== null && Number.isFinite(parsedAssessmentId)
      ? parsedAssessmentId
      : null;
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [selectedAssessmentId, setSelectedAssessmentId] = useState<number | null>(
    requestedAssessmentId,
  );
  const [error, setError] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [busyType, setBusyType] = useState<Report["report_type"] | "">("");
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [assessmentRows, reportRows] = await Promise.all([
          api.getAssessments(),
          api.getReports(),
        ]);
        setAssessments(assessmentRows);
        setReports(reportRows);
        setSelectedAssessmentId((current) => {
          if (
            requestedAssessmentId !== null &&
            assessmentRows.some((assessment) => assessment.id === requestedAssessmentId)
          ) {
            return requestedAssessmentId;
          }
          if (
            current !== null &&
            assessmentRows.some((assessment) => assessment.id === current)
          ) {
            return current;
          }
          return assessmentRows[0]?.id ?? null;
        });
        setError("");
      } catch (loadError) {
        setError((loadError as Error).message);
      } finally {
        setIsLoading(false);
      }
    }

    void load();
  }, [requestedAssessmentId]);

  useEffect(() => {
    const nextParams = new URLSearchParams(searchParams);
    if (selectedAssessmentId !== null) {
      nextParams.set("assessmentId", String(selectedAssessmentId));
    } else {
      nextParams.delete("assessmentId");
    }
    if (nextParams.toString() !== searchParams.toString()) {
      setSearchParams(nextParams, { replace: true });
    }
  }, [searchParams, selectedAssessmentId, setSearchParams]);

  const assessmentsById = useMemo(
    () =>
      new Map(
        assessments.map((assessment) => [assessment.id, assessment] as const),
      ),
    [assessments],
  );

  const selectedAssessment = selectedAssessmentId
    ? assessmentsById.get(selectedAssessmentId) ?? null
    : null;

  const visibleReports = useMemo(() => {
    if (selectedAssessmentId === null) {
      return reports;
    }
    return reports.filter((report) => report.assessment_id === selectedAssessmentId);
  }, [reports, selectedAssessmentId]);

  const summary = useMemo(() => {
    const generated = visibleReports.filter((report) => report.status === "generated");
    const executiveBundles = new Set(
      generated
        .filter((report) => report.report_type === "executive")
        .map((report) => report.name),
    ).size;
    const technicalBundles = new Set(
      generated
        .filter((report) => report.report_type === "technical")
        .map((report) => report.name),
    ).size;
    return {
      total: visibleReports.length,
      executiveBundles,
      technicalBundles,
      html: generated.filter((report) => report.format === "html").length,
      pdf: generated.filter((report) => report.format === "pdf").length,
    };
  }, [visibleReports]);

  async function refreshReports() {
    const reportRows = await api.getReports();
    setReports(reportRows);
  }

  async function handleGenerate(reportType: Report["report_type"]) {
    if (selectedAssessmentId === null) {
      setError("Create or select an assessment before generating a report.");
      return;
    }

    try {
      setError("");
      setStatusMessage("");
      setBusyType(reportType);
      const generated = await api.generateReports({
        assessment_id: selectedAssessmentId,
        report_type: reportType,
      });
      await refreshReports();
      setStatusMessage(
        `${reportTypeLabel(reportType)} report bundle generated: ${generated.length} files ready for export.`,
      );
    } catch (generateError) {
      setError((generateError as Error).message);
    } finally {
      setBusyType("");
    }
  }

  return (
    <div className="page">
      <PageHeader
        title="Reports"
        subtitle="Generate professional executive and technical reports from the saved local assessment state, then export them as HTML or PDF."
      />

      {error ? <div className="error-banner">{error}</div> : null}
      {statusMessage ? <div className="success-banner">{statusMessage}</div> : null}
      {isLoading ? (
        <LoadingState
          title="Loading Reports"
          message="Reading saved assessments and existing report artifacts from local storage."
        />
      ) : null}

      <Panel
        title="Generate Reports"
        subtitle="Report generation uses the current saved assessment, authorized scope, asset inventory, scan history, findings, web evidence, and TLS results already stored in the local platform."
      >
        <div className="report-generator-grid">
          <label className="field-stack">
            <span>Select Assessment</span>
            <select
              className="input"
              value={selectedAssessmentId ?? ""}
              disabled={isLoading}
              onChange={(event) => {
                setSelectedAssessmentId(event.target.value ? Number(event.target.value) : null);
                setStatusMessage("");
              }}
            >
              {assessments.length > 0 ? (
                assessments.map((assessment) => (
                  <option key={assessment.id} value={assessment.id}>
                    {assessmentLabel(assessment)}
                  </option>
                ))
              ) : (
                <option value="">No assessments available</option>
              )}
            </select>
          </label>

          <div className="report-summary-grid">
            <div className="summary-card">
              <span className="summary-card__label">Generated Reports</span>
              <strong>{summary.total}</strong>
              <div className="summary-list-inline">
                {selectedAssessment ? assessmentLabel(selectedAssessment) : "No assessment selected"}
              </div>
            </div>
            <div className="summary-card">
              <span className="summary-card__label">Executive</span>
              <strong>{summary.executiveBundles}</strong>
              <div className="summary-list-inline">Report bundles ready</div>
            </div>
            <div className="summary-card">
              <span className="summary-card__label">Technical</span>
              <strong>{summary.technicalBundles}</strong>
              <div className="summary-list-inline">
                {summary.html} HTML files | {summary.pdf} PDF files
              </div>
            </div>
          </div>
        </div>

        <div className="report-action-row">
          <button
            className="button"
            onClick={() => void handleGenerate("executive")}
            type="button"
            disabled={selectedAssessmentId === null || busyType !== "" || isLoading}
          >
            {busyType === "executive"
              ? "Generating Executive Report..."
              : "Generate Executive Report"}
          </button>
          <button
            className="button button--ghost"
            onClick={() => void handleGenerate("technical")}
            type="button"
            disabled={selectedAssessmentId === null || busyType !== "" || isLoading}
          >
            {busyType === "technical"
              ? "Generating Technical Report..."
              : "Generate Technical Report"}
          </button>
        </div>
      </Panel>

      <DataTable
        title="Generated Reports"
        subtitle="Each generated row is stored in the local database and points to a saved HTML or PDF artifact under the local runtime reports directory."
        rows={visibleReports}
        searchPlaceholder="Search reports"
        loading={isLoading}
        searchAccessor={(report) =>
          `${report.name} ${report.report_type} ${report.format} ${report.status} ${
            assessmentsById.get(report.assessment_id)?.project_name ?? ""
          }`
        }
        rowKey={(report) => report.id}
        emptyMessage="Generate an executive or technical report to create exportable HTML and PDF files."
        columns={[
          {
            header: "Assessment",
            cell: (report) =>
              assessmentsById.get(report.assessment_id)?.project_name ?? "Unknown assessment",
          },
          {
            header: "Type",
            cell: (report) => reportTypeLabel(report.report_type),
          },
          {
            header: "Format",
            cell: (report) => report.format.toUpperCase(),
          },
          {
            header: "Status",
            cell: (report) => report.status,
          },
          {
            header: "File",
            cell: (report) => reportFilename(report),
          },
          {
            header: "Export HTML",
            cell: (report) =>
              report.status === "generated" && report.format === "html" ? (
                <a
                  className="button button--ghost"
                  href={api.getReportDownloadUrl(report.id)}
                  target="_blank"
                  rel="noreferrer"
                >
                  Export HTML
                </a>
              ) : (
                <button className="button button--ghost" disabled type="button">
                  Export HTML
                </button>
              ),
          },
          {
            header: "Export PDF",
            cell: (report) =>
              report.status === "generated" && report.format === "pdf" ? (
                <a
                  className="button button--ghost"
                  href={api.getReportDownloadUrl(report.id)}
                  target="_blank"
                  rel="noreferrer"
                >
                  Export PDF
                </a>
              ) : (
                <button className="button button--ghost" disabled type="button">
                  Export PDF
                </button>
              ),
          },
          {
            header: "Created",
            cell: (report) => formatDate(report.created_at),
          },
        ]}
      />
    </div>
  );
}
