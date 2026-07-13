import { useEffect, useMemo, useState } from "react";

import { DataTable } from "../components/DataTable";
import { LoadingState } from "../components/LoadingState";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { StatusPill } from "../components/StatusPill";
import { ApiError, api } from "../lib/api";
import {
  buildScopePreview,
  formatDate,
  getSettingValue,
  parseTargets,
} from "../lib/utils";
import type { Assessment, Scope, Setting } from "../types";

const defaultLegalNotice =
  "Authorized internal use only. Save only private/local scope unless external scope has been deliberately enabled and confirmed.";

export function ScopeManagementPage() {
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [scopes, setScopes] = useState<Scope[]>([]);
  const [settings, setSettings] = useState<Setting[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [form, setForm] = useState({
    assessment_id: 0,
    name: "Primary Scope",
    network_ranges: "",
    individual_ips: "",
    excluded_ips: "",
    notes: "",
    external_scope_confirmed: false,
    legal_acknowledged: false,
  });

  useEffect(() => {
    async function load() {
      try {
        const [assessmentData, scopeData, settingsData] = await Promise.all([
          api.getAssessments(),
          api.getScopes(),
          api.getSettings(),
        ]);
        setAssessments(assessmentData);
        setScopes(scopeData);
        setSettings(settingsData);
        if (assessmentData.length > 0) {
          setForm((current) => ({
            ...current,
            assessment_id: current.assessment_id || assessmentData[0].id,
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

  const legalNotice = getSettingValue(settings, "legal_notice", defaultLegalNotice);
  const allowExternalScope =
    getSettingValue(settings, "allow_external_scope", "false").toLowerCase() ===
    "true";

  const networkRanges = useMemo(
    () => parseTargets(form.network_ranges),
    [form.network_ranges],
  );
  const individualIps = useMemo(
    () => parseTargets(form.individual_ips),
    [form.individual_ips],
  );
  const excludedIps = useMemo(
    () => parseTargets(form.excluded_ips),
    [form.excluded_ips],
  );

  const preview = useMemo(
    () => buildScopePreview(networkRanges, individualIps, excludedIps),
    [excludedIps, individualIps, networkRanges],
  );
  const liveFieldErrors = useMemo(() => {
    const merged = { ...preview.fieldErrors };

    Object.entries(fieldErrors).forEach(([key, value]) => {
      if (!merged[key]) {
        merged[key] = value;
      }
    });

    return merged;
  }, [fieldErrors, preview.fieldErrors]);

  const selectedAssessment = assessments.find(
    (assessment) => assessment.id === form.assessment_id,
  );
  const hasAssessments = assessments.length > 0;

  function setField<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    setFieldErrors({});

    if (!form.assessment_id) {
      setError("Create or select an assessment before saving scope.");
      setFieldErrors({
        assessment_id: ["Select an assessment before saving scope."],
      });
      return;
    }

    if (Object.keys(preview.fieldErrors).length > 0) {
      setError("Fix the highlighted scope entries before saving.");
      setFieldErrors(preview.fieldErrors);
      return;
    }

    if (preview.publicTargets.length > 0 && !allowExternalScope) {
      setError(
        "Public or external scope is blocked by default. Remove the public targets or enable the setting first.",
      );
      setFieldErrors({
        network_ranges: [
          "Public scope is currently blocked in settings.",
        ],
      });
      return;
    }

    try {
      const created = await api.createScope({
        assessment_id: form.assessment_id,
        name: form.name,
        network_ranges: networkRanges,
        individual_ips: individualIps,
        excluded_ips: excludedIps,
        notes: form.notes,
        external_scope_confirmed: form.external_scope_confirmed,
        legal_acknowledged: form.legal_acknowledged,
      });
      setScopes((current) => [created, ...current]);
      setMessage(
        "Scope saved. Included and excluded targets are now linked to the selected assessment.",
      );
      setForm((current) => ({
        ...current,
        name: "Primary Scope",
        network_ranges: "",
        individual_ips: "",
        excluded_ips: "",
        notes: "",
        external_scope_confirmed: false,
        legal_acknowledged: false,
      }));
    } catch (submitError) {
      if (submitError instanceof ApiError) {
        setError(submitError.message);
        setFieldErrors(submitError.fieldErrors);
        return;
      }

      setError((submitError as Error).message);
    }
  }

  return (
    <div className="page">
      <PageHeader
        title="Scope Management"
        subtitle="Define included networks, individual IPs, and exclusions with strong validation before any scan work begins."
      />
      {isLoading ? (
        <LoadingState
          title="Loading Scope Data"
          message="Reading saved assessments, settings, and scope records from the local API."
        />
      ) : null}

      <div className="split-grid split-grid--wide">
        <Panel
          title="Assessment Scope"
          subtitle="Use CIDR ranges and IPv4 addresses only. Public targets stay blocked unless explicitly enabled in settings."
        >
          <form className="form-grid" onSubmit={handleSubmit}>
            <label className="form-grid__full">
              Assessment
              <select
                className="input"
                disabled={!hasAssessments}
                value={form.assessment_id}
                onChange={(event) =>
                  setField("assessment_id", Number(event.target.value))
                }
              >
                {!hasAssessments ? (
                  <option value={0}>Create an assessment first</option>
                ) : null}
                {assessments.map((assessment) => (
                  <option key={assessment.id} value={assessment.id}>
                    {assessment.project_name} | {assessment.client_name}
                  </option>
                ))}
              </select>
              {fieldErrors.assessment_id ? (
                <span className="error-text">{fieldErrors.assessment_id[0]}</span>
              ) : null}
            </label>

            <label>
              Scope label
              <input
                className="input"
                value={form.name}
                onChange={(event) => setField("name", event.target.value)}
              />
            </label>

            <label>
              Assessment intensity
              <input
                className="input"
                disabled
                value={selectedAssessment?.scan_intensity ?? "Select assessment"}
              />
            </label>

            <label className="form-grid__full">
              Network ranges
              <textarea
                className="input textarea"
                rows={4}
                placeholder="192.168.1.0/24, 10.0.5.0/24"
                value={form.network_ranges}
                onChange={(event) => setField("network_ranges", event.target.value)}
              />
              {liveFieldErrors.network_ranges ? (
                <span className="error-text">{liveFieldErrors.network_ranges[0]}</span>
              ) : null}
            </label>

            <label className="form-grid__full">
              Individual IP addresses
              <textarea
                className="input textarea"
                rows={4}
                placeholder="192.168.1.10, 192.168.1.11"
                value={form.individual_ips}
                onChange={(event) => setField("individual_ips", event.target.value)}
              />
              {liveFieldErrors.individual_ips ? (
                <span className="error-text">{liveFieldErrors.individual_ips[0]}</span>
              ) : null}
            </label>

            <label className="form-grid__full">
              Excluded IP addresses
              <textarea
                className="input textarea"
                rows={3}
                placeholder="192.168.1.50, 192.168.1.51"
                value={form.excluded_ips}
                onChange={(event) => setField("excluded_ips", event.target.value)}
              />
              {liveFieldErrors.excluded_ips ? (
                <span className="error-text">{liveFieldErrors.excluded_ips[0]}</span>
              ) : null}
            </label>

            <label className="form-grid__full">
              Notes
              <textarea
                className="input textarea"
                rows={4}
                placeholder="Record scope assumptions, excluded systems, or operational notes."
                value={form.notes}
                onChange={(event) => setField("notes", event.target.value)}
              />
            </label>

            <div className="legal-notice form-grid__full">
              <div className="legal-notice__label">Scope Notice</div>
              <p>{legalNotice}</p>
            </div>

            {!hasAssessments ? (
              <div className="warning-banner form-grid__full">
                <strong>Assessment required:</strong> create the assessment record first, then return here to save the authorized target list.
              </div>
            ) : null}

            {preview.publicTargets.length > 0 ? (
              <div className="warning-banner form-grid__full">
                <strong>Warning:</strong> public or external targets detected:{" "}
                {preview.publicTargets.join(", ")}
              </div>
            ) : null}

            {preview.publicTargets.length > 0 && allowExternalScope ? (
              <label className="checkbox form-grid__full">
                <input
                  checked={form.external_scope_confirmed}
                  onChange={(event) =>
                    setField("external_scope_confirmed", event.target.checked)
                  }
                  type="checkbox"
                />
                I explicitly confirm that this external/public scope is authorized and should be stored.
              </label>
            ) : null}

            {liveFieldErrors.external_scope_confirmed ? (
              <span className="error-text form-grid__full">
                {liveFieldErrors.external_scope_confirmed[0]}
              </span>
            ) : null}

            <label className="checkbox form-grid__full">
              <input
                checked={form.legal_acknowledged}
                onChange={(event) =>
                  setField("legal_acknowledged", event.target.checked)
                }
                type="checkbox"
              />
              I have reviewed the legal notice and confirm this assessment scope is authorized.
            </label>

            {liveFieldErrors.legal_acknowledged ? (
              <span className="error-text form-grid__full">
                {liveFieldErrors.legal_acknowledged[0]}
              </span>
            ) : null}

            <div className="form-actions form-grid__full">
              <button className="button" type="submit">
                Save Scope
              </button>
              {message ? <span className="success-text">{message}</span> : null}
              {error ? <span className="error-text">{error}</span> : null}
            </div>
          </form>
        </Panel>

        <div className="page-stack">
          <Panel
            title="Scope Summary"
            subtitle="Review included and excluded targets before saving."
          >
            <div className="summary-grid">
              <div className="summary-card">
                <span className="summary-card__label">Included targets</span>
                <strong>{preview.includedTargets.length}</strong>
                <ul className="summary-list">
                  {preview.includedTargets.length > 0 ? (
                    preview.includedTargets.map((target) => (
                      <li key={target}>{target}</li>
                    ))
                  ) : (
                    <li>No included targets yet.</li>
                  )}
                </ul>
              </div>

              <div className="summary-card">
                <span className="summary-card__label">Excluded IPs</span>
                <strong>{preview.excludedTargets.length}</strong>
                <ul className="summary-list">
                  {preview.excludedTargets.length > 0 ? (
                    preview.excludedTargets.map((target) => (
                      <li key={target}>{target}</li>
                    ))
                  ) : (
                    <li>No exclusions yet.</li>
                  )}
                </ul>
              </div>
            </div>
          </Panel>

          <DataTable
            title="Saved Scopes"
            subtitle="Each scope stays linked to its assessment for later configuration and review."
            rows={scopes}
            searchPlaceholder="Search scopes"
            loading={isLoading}
            searchAccessor={(scope) =>
              `${scope.name} ${scope.notes} ${scope.included_targets.join(" ")}`
            }
            rowKey={(scope) => scope.id}
            columns={[
              { header: "Scope", cell: (scope) => scope.name },
              {
                header: "Status",
                cell: (scope) => (
                  <StatusPill label={scope.is_authorized ? "authorized" : "draft"} />
                ),
              },
              {
                header: "Included",
                cell: (scope) => scope.included_targets.join(", "),
              },
              {
                header: "Excluded",
                cell: (scope) =>
                  scope.excluded_ips.length > 0
                    ? scope.excluded_ips.join(", ")
                    : "None",
              },
              {
                header: "Updated",
                cell: (scope) => formatDate(scope.updated_at),
              },
            ]}
          />
        </div>
      </div>
    </div>
  );
}
