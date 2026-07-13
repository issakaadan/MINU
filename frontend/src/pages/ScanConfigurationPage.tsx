import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, api } from "../lib/api";
import { LoadingState } from "../components/LoadingState";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { parseTargets } from "../lib/utils";
import type { Assessment, Host, Scope, ScanJob } from "../types";

type ScanFormState = {
  job_type: ScanJob["job_type"];
  assessment_id: number;
  scope_id: number;
  name: string;
  profile_name: string;
  scan_intensity: Assessment["scan_intensity"];
  requested_targets: string;
  include_service_detection: boolean;
  include_safe_checks: boolean;
  udp_scan_enabled: boolean;
  include_performance_module: boolean;
  legal_acknowledged: boolean;
};

function buildInitialForm(): ScanFormState {
  return {
    job_type: "safe_discovery",
    assessment_id: 0,
    scope_id: 0,
    name: "",
    profile_name: "safe-discovery",
    scan_intensity: "Standard",
    requested_targets: "",
    include_service_detection: true,
    include_safe_checks: true,
    udp_scan_enabled: false,
    include_performance_module: false,
    legal_acknowledged: false,
  };
}

export function ScanConfigurationPage() {
  const navigate = useNavigate();
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [hosts, setHosts] = useState<Host[]>([]);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [form, setForm] = useState<ScanFormState>(() => buildInitialForm());

  useEffect(() => {
    async function load() {
      try {
        const [assessmentData, scopeData, hostData] = await Promise.all([
          api.getAssessments(),
          api.getScopes(),
          api.getHosts(),
        ]);
        setAssessments(assessmentData);
        setScopes(scopeData);
        setHosts(hostData);

        if (assessmentData.length > 0) {
          const defaultAssessment = assessmentData[0];
          const defaultScope =
            scopeData.find((scope) => scope.assessment_id === defaultAssessment.id) ??
            scopeData[0] ??
            null;
          setForm((current) => ({
            ...current,
            assessment_id: defaultAssessment.id,
            scope_id: defaultScope?.id ?? 0,
            scan_intensity: defaultAssessment.scan_intensity,
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
  }, []);

  const assessmentScopes = useMemo(
    () => scopes.filter((scope) => scope.assessment_id === form.assessment_id),
    [form.assessment_id, scopes],
  );
  const selectedAssessment = assessments.find(
    (assessment) => assessment.id === form.assessment_id,
  );
  const selectedScope = scopes.find((scope) => scope.id === form.scope_id);
  const scopeHosts = useMemo(
    () =>
      hosts.filter(
        (host) =>
          host.assessment_id === form.assessment_id && host.scope_id === form.scope_id,
      ),
    [form.assessment_id, form.scope_id, hosts],
  );
  const preferredEnumerationHosts = useMemo(
    () => scopeHosts.filter((host) => host.status !== "offline"),
    [scopeHosts],
  );
  const canSubmit =
    Boolean(form.assessment_id && form.scope_id) &&
    (form.job_type === "safe_discovery" || scopeHosts.length > 0);

  function setField<K extends keyof ScanFormState>(key: K, value: ScanFormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function setJobType(jobType: ScanJob["job_type"]) {
    setForm((current) => ({
      ...current,
      job_type: jobType,
      profile_name: jobType === "safe_enumeration" ? "common-tcp" : "safe-discovery",
      requested_targets: "",
      include_service_detection: true,
      include_safe_checks: true,
      udp_scan_enabled: false,
    }));
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);

    if (!canSubmit) {
      setError(
        form.job_type === "safe_enumeration"
          ? "Run safe discovery first so the selected scope has discovered hosts to enumerate."
          : "Select an assessment and authorized scope before running discovery.",
      );
      setSubmitting(false);
      return;
    }

    try {
      const created = await api.createScan({
        job_type: form.job_type,
        assessment_id: form.assessment_id,
        scope_id: form.scope_id,
        name: form.name,
        profile_name: form.profile_name,
        scan_intensity: form.scan_intensity,
        requested_targets: parseTargets(form.requested_targets),
        include_service_detection: form.include_service_detection,
        include_safe_checks: form.include_safe_checks,
        udp_scan_enabled: false,
        include_performance_module: false,
        legal_acknowledged: form.legal_acknowledged,
        warning_acknowledged: false,
        maintenance_window_confirmed: false,
      });
      await api.startScan(created.id);
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
        title="Scan Configuration"
        subtitle="Choose a safe operation, keep it inside the saved authorized scope, and tune the pace without enabling destructive behavior."
      />
      {isLoading ? (
        <LoadingState
          title="Loading Scan Context"
          message="Reading saved assessments, scopes, and discovered hosts before a job is created."
        />
      ) : null}

      <Panel
        title="Authorized Scan Operations"
        subtitle="Safe discovery finds hosts. Safe port and service enumeration inspects only discovered hosts inside the same approved scope."
      >
        <form className="form-grid" onSubmit={handleSubmit}>
          <label className="form-grid__full">
            Operation
            <div className="segmented-control">
              <button
                className={
                  form.job_type === "safe_discovery"
                    ? "button button--segmented-active"
                    : "button button--ghost"
                }
                onClick={() => setJobType("safe_discovery")}
                type="button"
              >
                Safe Host Discovery
              </button>
              <button
                className={
                  form.job_type === "safe_enumeration"
                    ? "button button--segmented-active"
                    : "button button--ghost"
                }
                onClick={() => setJobType("safe_enumeration")}
                type="button"
              >
                Safe Port Enumeration
              </button>
            </div>
          </label>

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
                  {assessment.project_name}
                </option>
              ))}
            </select>
          </label>

          <label>
            Scope
            <select
              className="input"
              value={form.scope_id}
              onChange={(event) =>
                setField("scope_id", Number(event.target.value))
              }
            >
              {assessmentScopes.map((scope) => (
                <option key={scope.id} value={scope.id}>
                  {scope.name}
                </option>
              ))}
            </select>
          </label>

          <label>
            Scan job name
            <input
              className="input"
              required
              value={form.name}
              onChange={(event) => setField("name", event.target.value)}
            />
          </label>

          <label>
            Scan intensity
            <select
              className="input"
              value={form.scan_intensity}
              onChange={(event) =>
                setField(
                  "scan_intensity",
                  event.target.value as ScanFormState["scan_intensity"],
                )
              }
            >
              <option value="Light">Light</option>
              <option value="Standard">Standard</option>
              <option value="Deep">Deep</option>
            </select>
          </label>

          {form.job_type === "safe_enumeration" ? (
            <label>
              TCP coverage
              <select
                className="input"
                value={form.profile_name}
                onChange={(event) => setField("profile_name", event.target.value)}
              >
                <option value="common-tcp">Common TCP Ports</option>
                <option value="full-tcp">Full TCP Scan</option>
              </select>
            </label>
          ) : (
            <label>
              Selected assessment default
              <input
                className="input"
                disabled
                value={selectedAssessment?.scan_intensity ?? "Select assessment"}
              />
            </label>
          )}

          <label className={form.job_type === "safe_enumeration" ? "checkbox checkbox--disabled" : "checkbox"}>
            <input
              checked={false}
              disabled
              type="checkbox"
            />
            UDP scan remains disabled by default in this MVP
          </label>

          <label className="form-grid__full">
            Requested targets override
            <textarea
              className="input textarea"
              rows={3}
              placeholder={
                form.job_type === "safe_enumeration"
                  ? "Leave blank to enumerate discovered live or unknown hosts in the selected scope. Or enter discovered IP addresses only."
                  : "Leave blank to inherit the authorized scope target list exactly as saved."
              }
              value={form.requested_targets}
              onChange={(event) =>
                setField("requested_targets", event.target.value)
              }
            />
          </label>

          <div className="warning-banner form-grid__full">
            <strong>Authorized target summary:</strong>{" "}
            {selectedScope
              ? selectedScope.included_targets.join(", ") || "No included targets"
              : "Select a scope to review the target list."}
            {selectedScope && selectedScope.excluded_ips.length > 0
              ? ` | Excluded: ${selectedScope.excluded_ips.join(", ")}`
              : ""}
          </div>

          {form.job_type === "safe_enumeration" ? (
            <div className="warning-banner form-grid__full">
              <strong>Discovered host inventory:</strong>{" "}
              {scopeHosts.length} discovered host{scopeHosts.length === 1 ? "" : "s"} in
              this scope.
              {preferredEnumerationHosts.length > 0
                ? ` ${preferredEnumerationHosts.length} currently marked live or unknown will be preferred by default.`
                : " No live or unknown hosts are currently available, so enumeration will require existing discovered records."}
            </div>
          ) : null}

          <label className="checkbox">
            <input
              checked={form.include_service_detection}
              onChange={(event) =>
                setField("include_service_detection", event.target.checked)
              }
              type="checkbox"
            />
            {form.job_type === "safe_enumeration"
              ? "Perform basic service detection on open TCP ports"
              : "Use TCP connect fallback when ping is blocked"}
          </label>

          <label className="checkbox">
            <input
              checked={form.include_safe_checks}
              onChange={(event) =>
                setField("include_safe_checks", event.target.checked)
              }
              type="checkbox"
            />
            {form.job_type === "safe_enumeration"
              ? "Capture safe banners where possible"
              : "Collect hostname and MAC details where available"}
          </label>

          <label className="checkbox checkbox--disabled">
            <input checked={false} disabled type="checkbox" />
            Performance-impacting tests run from the separate Disruptive Tests page only
          </label>

          <label className="checkbox form-grid__full">
            <input
              checked={form.legal_acknowledged}
              onChange={(event) =>
                setField("legal_acknowledged", event.target.checked)
              }
              required
              type="checkbox"
            />
            I confirm this scan stays within the authorized scope, avoids brute force or destructive behavior, and does not test credentials.
          </label>

          <div className="form-actions form-grid__full">
            <button
              className="button"
              disabled={submitting || !canSubmit || isLoading}
              type="submit"
            >
              {submitting
                ? "Starting Job..."
                : form.job_type === "safe_enumeration"
                  ? "Run Port Enumeration"
                  : "Run Discovery"}
            </button>
            {error ? <span className="error-text">{error}</span> : null}
          </div>
        </form>
      </Panel>
    </div>
  );
}
