import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { LoadingState } from "../components/LoadingState";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { ProgressPanel } from "../components/ProgressPanel";
import { StatusPill } from "../components/StatusPill";
import { ApiError, api } from "../lib/api";
import { getSettingValue, parseTargets } from "../lib/utils";
import type {
  Assessment,
  DisruptiveTestResult,
  Host,
  ScanJob,
  Scope,
  Setting,
} from "../types";

type DisruptiveFormState = {
  assessment_id: number;
  scope_id: number;
  name: string;
  scan_intensity: Assessment["scan_intensity"];
  requested_targets: string;
  legal_acknowledged: boolean;
};

function buildInitialForm(): DisruptiveFormState {
  return {
    assessment_id: 0,
    scope_id: 0,
    name: "",
    scan_intensity: "Standard",
    requested_targets: "",
    legal_acknowledged: false,
  };
}

function formatNumber(value: number | null, suffix = ""): string {
  return value === null ? "Unavailable" : `${value.toFixed(2)}${suffix}`;
}

export function DisruptiveTestsPage() {
  const navigate = useNavigate();
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [hosts, setHosts] = useState<Host[]>([]);
  const [settings, setSettings] = useState<Setting[]>([]);
  const [scans, setScans] = useState<ScanJob[]>([]);
  const [results, setResults] = useState<DisruptiveTestResult[]>([]);
  const [form, setForm] = useState<DisruptiveFormState>(() => buildInitialForm());
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [savingAssessment, setSavingAssessment] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [showWarningModal, setShowWarningModal] = useState(false);
  const [maintenanceWindowConfirmed, setMaintenanceWindowConfirmed] =
    useState(false);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [
          assessmentRows,
          scopeRows,
          hostRows,
          settingRows,
          scanRows,
          resultRows,
        ] = await Promise.all([
          api.getAssessments(),
          api.getScopes(),
          api.getHosts(),
          api.getSettings(),
          api.getScans(),
          api.getDisruptiveTestResults(),
        ]);
        setAssessments(assessmentRows);
        setScopes(scopeRows);
        setHosts(hostRows);
        setSettings(settingRows);
        setScans(scanRows);
        setResults(resultRows);

        if (assessmentRows.length > 0) {
          const defaultAssessment = assessmentRows[0];
          const defaultScope =
            scopeRows.find((scope) => scope.assessment_id === defaultAssessment.id) ??
            scopeRows[0] ??
            null;
          setForm((current) => ({
            ...current,
            assessment_id: current.assessment_id || defaultAssessment.id,
            scope_id: current.scope_id || defaultScope?.id || 0,
            scan_intensity:
              current.assessment_id === defaultAssessment.id && current.scan_intensity
                ? current.scan_intensity
                : defaultAssessment.scan_intensity,
            }));
        }
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
    }, 4000);

    return () => window.clearInterval(intervalId);
  }, []);

  const performanceModuleEnabled =
    getSettingValue(settings, "performance_module_enabled", "false").toLowerCase() ===
    "true";
  const selectedAssessment =
    assessments.find((assessment) => assessment.id === form.assessment_id) ?? null;
  const assessmentScopes = useMemo(
    () => scopes.filter((scope) => scope.assessment_id === form.assessment_id),
    [form.assessment_id, scopes],
  );
  const selectedScope =
    scopes.find((scope) => scope.id === form.scope_id) ?? null;
  const scopeHosts = useMemo(
    () =>
      hosts.filter(
        (host) =>
          host.assessment_id === form.assessment_id && host.scope_id === form.scope_id,
      ),
    [form.assessment_id, form.scope_id, hosts],
  );
  const discoveredEligibleHosts = useMemo(
    () => scopeHosts.filter((host) => host.status !== "offline"),
    [scopeHosts],
  );
  const disruptiveScans = useMemo(
    () =>
      scans.filter(
        (scan) =>
          scan.job_type === "disruptive_tests" &&
          (!form.assessment_id || scan.assessment_id === form.assessment_id),
      ),
    [form.assessment_id, scans],
  );
  const visibleResults = useMemo(
    () =>
      results.filter(
        (result) =>
          !form.assessment_id || result.assessment_id === form.assessment_id,
      ),
    [form.assessment_id, results],
  );
  const latestSummary = useMemo(() => {
    const latestScan = disruptiveScans[0] ?? null;
    if (!latestScan) {
      return null;
    }
    return latestScan.result_summary;
  }, [disruptiveScans]);
  const canPrepareRun =
    performanceModuleEnabled &&
    Boolean(selectedAssessment?.allow_disruptive_tests) &&
    Boolean(form.assessment_id && form.scope_id) &&
    scopeHosts.length > 0;

  function setField<K extends keyof DisruptiveFormState>(
    key: K,
    value: DisruptiveFormState[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleAssessmentToggle(nextValue: boolean) {
    if (!selectedAssessment) {
      return;
    }

    try {
      setSavingAssessment(true);
      setError("");
      setMessage("");
      const updated = await api.updateAssessment(selectedAssessment.id, {
        allow_disruptive_tests: nextValue,
      });
      setAssessments((current) =>
        current.map((assessment) =>
          assessment.id === updated.id ? updated : assessment,
        ),
      );
      setMessage(
        nextValue
          ? "This assessment now allows performance-impacting tests when the global setting is also enabled."
          : "Performance-impacting tests were disabled for this assessment.",
      );
    } catch (saveError) {
      setError((saveError as Error).message);
    } finally {
      setSavingAssessment(false);
    }
  }

  function prepareRun() {
    setError("");
    setMessage("");

    if (!performanceModuleEnabled) {
      setError("Enable the disruptive / performance-impacting module globally in Settings first.");
      return;
    }

    if (!selectedAssessment?.allow_disruptive_tests) {
      setError("Enable disruptive / performance-impacting tests for this assessment before creating a job.");
      return;
    }

    if (!scopeHosts.length) {
      setError("Run safe discovery first so this scope has discovered hosts available for performance-impacting checks.");
      return;
    }

    if (!form.legal_acknowledged) {
      setError("Confirm the legal and safety acknowledgement before continuing.");
      return;
    }

    setMaintenanceWindowConfirmed(false);
    setShowWarningModal(true);
  }

  async function handleConfirmRun() {
    if (!maintenanceWindowConfirmed) {
      setError("Confirm the approved maintenance window before running performance-impacting tests.");
      return;
    }

    try {
      setSubmitting(true);
      setError("");
      const created = await api.createScan({
        job_type: "disruptive_tests",
        assessment_id: form.assessment_id,
        scope_id: form.scope_id,
        name: form.name,
        profile_name: "performance-impacting",
        scan_intensity: form.scan_intensity,
        requested_targets: parseTargets(form.requested_targets),
        include_service_detection: false,
        include_safe_checks: true,
        udp_scan_enabled: false,
        include_performance_module: true,
        legal_acknowledged: form.legal_acknowledged,
        warning_acknowledged: true,
        maintenance_window_confirmed: true,
      });
      await api.startScan(created.id);
      setMessage("Performance-impacting test job created and started.");
      setShowWarningModal(false);
      navigate("/running-scan");
    } catch (submitError) {
      if (submitError instanceof ApiError) {
        setError(submitError.message);
      } else {
        setError((submitError as Error).message);
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page">
      <PageHeader
        title="Disruptive Tests"
        subtitle="Use this separate module for low-rate performance-impacting checks only after discovery is complete and an approved maintenance window exists."
      />
      {isLoading ? (
        <LoadingState
          title="Loading Module State"
          message="Reading assessments, scopes, discovered hosts, settings, jobs, and saved disruptive-test results."
        />
      ) : null}

      {error ? <div className="error-banner">{error}</div> : null}
      {message ? <div className="success-banner">{message}</div> : null}

      <Panel
        title="Module Gates"
        subtitle="This module is disabled by default globally and must also be enabled per assessment before any performance-impacting job can be created."
      >
        <div className="summary-grid">
          <div className="summary-card">
            <span className="summary-card__label">Global Module Status</span>
            <strong>{performanceModuleEnabled ? "Enabled" : "Disabled"}</strong>
            <div className="summary-list-inline">
              Change this in Settings under `performance_module_enabled`.
            </div>
          </div>
          <div className="summary-card">
            <span className="summary-card__label">Assessment Scope Status</span>
            <strong>
              {selectedAssessment?.allow_disruptive_tests
                ? "Enabled for assessment"
                : "Disabled for assessment"}
            </strong>
            <div className="summary-list-inline">
              {selectedAssessment
                ? selectedAssessment.project_name
                : "Select an assessment"}
            </div>
          </div>
        </div>
      </Panel>

      <Panel
        title="Assessment Controls"
        subtitle="Select the assessment and explicitly enable or disable this module for that engagement."
      >
        <div className="form-grid">
          <label>
            Assessment
            <select
              className="input"
              value={form.assessment_id}
              onChange={(event) => {
                const assessmentId = Number(event.target.value);
                const nextAssessment =
                  assessments.find((assessment) => assessment.id === assessmentId) ?? null;
                const nextScope =
                  scopes.find((scope) => scope.assessment_id === assessmentId) ?? null;
                setForm((current) => ({
                  ...current,
                  assessment_id: assessmentId,
                  scope_id: nextScope?.id ?? 0,
                  scan_intensity:
                    nextAssessment?.scan_intensity ?? current.scan_intensity,
                }));
              }}
            >
              {assessments.map((assessment) => (
                <option key={assessment.id} value={assessment.id}>
                  {assessment.project_name} | {assessment.client_name}
                </option>
              ))}
            </select>
          </label>

          <label className="checkbox">
            <input
              checked={selectedAssessment?.allow_disruptive_tests ?? false}
              disabled={!selectedAssessment || savingAssessment}
              onChange={(event) => void handleAssessmentToggle(event.target.checked)}
              type="checkbox"
            />
            Enable disruptive / performance-impacting tests for this assessment
          </label>
        </div>

        <div className="warning-banner">
          <strong>Warning:</strong> These tests may affect performance. Run only during an approved maintenance window.
        </div>
      </Panel>

      <Panel
        title="Performance-Impacting Test Configuration"
        subtitle="Only previously discovered hosts inside the authorized scope are eligible. Results stay separate from normal scan data."
      >
        <form className="form-grid" onSubmit={(event) => event.preventDefault()}>
          <label>
            Scope
            <select
              className="input"
              value={form.scope_id}
              onChange={(event) => setField("scope_id", Number(event.target.value))}
            >
              {assessmentScopes.map((scope) => (
                <option key={scope.id} value={scope.id}>
                  {scope.name}
                </option>
              ))}
            </select>
          </label>

          <label>
            Job name
            <input
              className="input"
              required
              value={form.name}
              onChange={(event) => setField("name", event.target.value)}
            />
          </label>

          <label>
            Intensity
            <select
              className="input"
              value={form.scan_intensity}
              onChange={(event) =>
                setField(
                  "scan_intensity",
                  event.target.value as DisruptiveFormState["scan_intensity"],
                )
              }
            >
              <option value="Light">Light</option>
              <option value="Standard">Standard</option>
              <option value="Deep">Deep</option>
            </select>
          </label>

          <label>
            Requested discovered hosts override
            <textarea
              className="input textarea"
              rows={3}
              placeholder="Leave blank to use discovered live or unknown hosts in the selected scope. Or enter discovered IP addresses only."
              value={form.requested_targets}
              onChange={(event) => setField("requested_targets", event.target.value)}
            />
          </label>

          <div className="summary-card form-grid__full">
            <span className="summary-card__label">Allowed Checks Only</span>
            <strong>Performance-impacting</strong>
            <ul className="summary-list">
              <li>Repeated low-rate ping latency measurement</li>
              <li>Basic response time comparison</li>
              <li>Controlled connection attempt timing</li>
              <li>Simple packet loss observation</li>
              <li>Basic bandwidth estimate only if safe and rate-limited</li>
            </ul>
          </div>

          <div className="warning-banner form-grid__full">
            <strong>Authorized discovered targets:</strong>{" "}
            {selectedScope
              ? selectedScope.included_targets.join(", ") || "No included targets"
              : "Select a scope to review the target list."}
            {selectedScope && selectedScope.excluded_ips.length > 0
              ? ` | Excluded: ${selectedScope.excluded_ips.join(", ")}`
              : ""}
          </div>

          <div className="warning-banner form-grid__full">
            <strong>Discovered host requirement:</strong> {scopeHosts.length} discovered host
            {scopeHosts.length === 1 ? "" : "s"} in this scope.{" "}
            {discoveredEligibleHosts.length} currently marked live or unknown will be
            preferred by default.
          </div>

          <label className="checkbox form-grid__full">
            <input
              checked={form.legal_acknowledged}
              onChange={(event) =>
                setField("legal_acknowledged", event.target.checked)
              }
              type="checkbox"
            />
            I confirm this job stays inside the authorized scope and does not include DoS, flooding, stress testing, exploit-based crashes, or anything intended to overwhelm devices or services.
          </label>

          <div className="form-actions form-grid__full">
            <button
              className="button"
              disabled={submitting || !canPrepareRun}
              onClick={prepareRun}
              type="button"
            >
              {submitting ? "Starting..." : "Prepare Performance-Impacting Test"}
            </button>
          </div>
        </form>
      </Panel>

      <Panel
        title="Performance-Impacting Results"
        subtitle="These results are stored separately from normal scan results and clearly marked as performance-impacting tests."
      >
        <div className="summary-grid">
          <div className="summary-card">
            <span className="summary-card__label">Saved Results</span>
            <strong>{visibleResults.length}</strong>
            <div className="summary-list-inline">Separate performance-impacting records</div>
          </div>
          <div className="summary-card">
            <span className="summary-card__label">Latest Average Latency</span>
            <strong>
              {formatNumber(
                typeof latestSummary?.avg_latency_ms === "number"
                  ? latestSummary.avg_latency_ms
                  : null,
                " ms",
              )}
            </strong>
            <div className="summary-list-inline">Latest performance summary</div>
          </div>
          <div className="summary-card">
            <span className="summary-card__label">Latest Packet Loss</span>
            <strong>
              {formatNumber(
                typeof latestSummary?.avg_packet_loss_percent === "number"
                  ? latestSummary.avg_packet_loss_percent
                  : null,
                "%",
              )}
            </strong>
            <div className="summary-list-inline">Latest performance summary</div>
          </div>
        </div>

        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Host</th>
                <th>Label</th>
                <th>Status</th>
                <th>Ping / Loss</th>
                <th>Latency</th>
                <th>Connect Timing</th>
                <th>Bandwidth</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {visibleResults.length > 0 ? (
                visibleResults.map((result) => (
                  <tr key={result.id}>
                    <td>
                      {result.target_host}
                      <div className="finding-subtext">
                        {result.hostname || "No hostname"}
                      </div>
                    </td>
                    <td>{result.result_label}</td>
                    <td>
                      <StatusPill label={result.status} />
                    </td>
                    <td>
                      {result.ping_samples_received}/{result.ping_samples_sent}
                      <div className="finding-subtext">
                        Loss {formatNumber(result.packet_loss_percent, "%")}
                      </div>
                    </td>
                    <td>
                      {formatNumber(result.avg_latency_ms, " ms")}
                      <div className="finding-subtext">
                        Min {formatNumber(result.min_latency_ms, " ms")} | Max{" "}
                        {formatNumber(result.max_latency_ms, " ms")}
                      </div>
                    </td>
                    <td>
                      {result.connect_port ?? "Unavailable"}
                      <div className="finding-subtext">
                        {formatNumber(result.connect_time_ms, " ms")}
                      </div>
                    </td>
                    <td>{formatNumber(result.bandwidth_estimate_kbps, " kbps")}</td>
                    <td className="table-cell-wrap">{result.notes || "No notes"}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td className="table-empty" colSpan={8}>
                    No performance-impacting test results have been recorded yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <ProgressPanel
        scans={disruptiveScans}
        title="Performance-Impacting Job Progress"
        subtitle="These jobs run separately from safe discovery and enumeration and remain tightly rate-limited."
      />

      {showWarningModal ? (
        <Modal
          title="Maintenance Window Warning"
          onClose={() => setShowWarningModal(false)}
          actions={
            <>
              <button
                className="button button--ghost"
                onClick={() => setShowWarningModal(false)}
                type="button"
              >
                Cancel
              </button>
              <button
                className="button"
                disabled={!maintenanceWindowConfirmed || submitting}
                onClick={() => void handleConfirmRun()}
                type="button"
              >
                {submitting ? "Starting..." : "Run Performance-Impacting Test"}
              </button>
            </>
          }
        >
          <div className="warning-banner">
            <strong>Warning:</strong> These tests may affect performance. Run only during an approved maintenance window.
          </div>
          <label className="checkbox">
            <input
              checked={maintenanceWindowConfirmed}
              onChange={(event) =>
                setMaintenanceWindowConfirmed(event.target.checked)
              }
              type="checkbox"
            />
            I confirm there is an approved maintenance window for this assessment and I want to continue.
          </label>
        </Modal>
      ) : null}
    </div>
  );
}
