import { useEffect, useState } from "react";

import { ApiError, api } from "../lib/api";
import { LoadingState } from "../components/LoadingState";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { getSettingValue } from "../lib/utils";
import type { Setting } from "../types";
import type { AssessmentCreatePayload } from "../types";

function getTodayDate(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function buildInitialForm(
  defaultIntensity: AssessmentCreatePayload["scan_intensity"] = "Standard",
): AssessmentCreatePayload {
  return {
    project_name: "",
    client_name: "",
    assessor_name: "",
    assessment_date: getTodayDate(),
    description: "",
    scan_intensity: defaultIntensity,
    allow_disruptive_tests: false,
    status: "draft",
  };
}

export function NewAssessmentPage() {
  const [defaultIntensity, setDefaultIntensity] =
    useState<AssessmentCreatePayload["scan_intensity"]>("Standard");
  const [form, setForm] = useState<AssessmentCreatePayload>(() =>
    buildInitialForm(),
  );
  const [settings, setSettings] = useState<Setting[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [isLoadingDefaults, setIsLoadingDefaults] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [showDisruptiveModal, setShowDisruptiveModal] = useState(false);

  useEffect(() => {
    async function loadDefaults() {
      try {
        const settingRows = await api.getSettings();
        setSettings(settingRows);
        const nextIntensity = getSettingValue(
          settingRows,
          "default_scan_intensity",
          "Standard",
        ) as AssessmentCreatePayload["scan_intensity"];
        setDefaultIntensity(nextIntensity);
        setForm((current) => ({
          ...current,
          scan_intensity:
            current.scan_intensity === "Standard" &&
            current.project_name === "" &&
            current.client_name === "" &&
            current.assessor_name === ""
              ? nextIntensity
              : current.scan_intensity,
        }));
      } catch (loadError) {
        setError((loadError as Error).message);
      } finally {
        setIsLoadingDefaults(false);
      }
    }

    void loadDefaults();
  }, []);

  function setField<K extends keyof AssessmentCreatePayload>(
    key: K,
    value: AssessmentCreatePayload[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    setError("");
    setFieldErrors({});
    setIsSubmitting(true);

    try {
      await api.createAssessment(form);
      setMessage("Assessment record created. Continue to Scope Management to define the authorized target list.");
      setForm(buildInitialForm(defaultIntensity));
    } catch (submitError) {
      if (submitError instanceof ApiError) {
        setError(submitError.message);
        setFieldErrors(submitError.fieldErrors);
        setIsSubmitting(false);
        return;
      }

      setError((submitError as Error).message);
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleDisruptiveToggle(checked: boolean) {
    if (checked) {
      setShowDisruptiveModal(true);
      return;
    }
    setField("allow_disruptive_tests", false);
  }

  return (
    <div className="page">
      <PageHeader
        title="New Assessment"
        subtitle="Capture the core engagement details before any scope is recorded. Disruptive tests remain off by default."
      />
      {isLoadingDefaults ? (
        <LoadingState
          title="Loading Defaults"
          message="Pulling safe application defaults before the assessment form is saved."
        />
      ) : null}

      <Panel
        title="Assessment Intake"
        subtitle="Create the project record with client, assessor, date, description, and scan intensity."
      >
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            Project name
            <input
              className="input"
              required
              value={form.project_name}
              onChange={(event) => setField("project_name", event.target.value)}
            />
            {fieldErrors.project_name ? (
              <span className="error-text">{fieldErrors.project_name[0]}</span>
            ) : null}
          </label>

          <label>
            Client or company name
            <input
              className="input"
              required
              value={form.client_name}
              onChange={(event) => setField("client_name", event.target.value)}
            />
            {fieldErrors.client_name ? (
              <span className="error-text">{fieldErrors.client_name[0]}</span>
            ) : null}
          </label>

          <label>
            Assessor name
            <input
              className="input"
              required
              value={form.assessor_name}
              onChange={(event) => setField("assessor_name", event.target.value)}
            />
            {fieldErrors.assessor_name ? (
              <span className="error-text">{fieldErrors.assessor_name[0]}</span>
            ) : null}
          </label>

          <label>
            Assessment date
            <input
              className="input"
              required
              type="date"
              value={form.assessment_date}
              onChange={(event) => setField("assessment_date", event.target.value)}
            />
            {fieldErrors.assessment_date ? (
              <span className="error-text">{fieldErrors.assessment_date[0]}</span>
            ) : null}
          </label>

          <label>
            Scan intensity
            <select
              className="input"
              value={form.scan_intensity}
              onChange={(event) =>
                setField(
                  "scan_intensity",
                  event.target.value as AssessmentCreatePayload["scan_intensity"],
                )
              }
            >
              <option value="Light">Light</option>
              <option value="Standard">Standard</option>
              <option value="Deep">Deep</option>
            </select>
            <span className="finding-subtext">
              Default from Settings: {defaultIntensity}
            </span>
          </label>

          <label className="checkbox">
            <input
              checked={form.allow_disruptive_tests}
              onChange={(event) => handleDisruptiveToggle(event.target.checked)}
              type="checkbox"
            />
            Enable disruptive / performance-impacting tests for this assessment
          </label>

          <label className="form-grid__full">
            Description
            <textarea
              className="input textarea"
              rows={4}
              value={form.description}
              onChange={(event) => setField("description", event.target.value)}
            />
          </label>

          <div className="warning-banner form-grid__full">
            <strong>Safety default:</strong> disruptive tests stay off unless you explicitly enable them for this assessment record.
          </div>

          <div className="form-actions form-grid__full">
            <button
              className="button"
              disabled={isSubmitting || isLoadingDefaults}
              type="submit"
            >
              {isSubmitting ? "Saving Assessment..." : "Save Assessment"}
            </button>
            {message ? <span className="success-text">{message}</span> : null}
            {error ? <span className="error-text">{error}</span> : null}
          </div>
        </form>
      </Panel>

      {showDisruptiveModal ? (
        <Modal
          title="Enable Performance-Impacting Tests?"
          onClose={() => setShowDisruptiveModal(false)}
          actions={
            <>
              <button
                className="button button--ghost"
                onClick={() => setShowDisruptiveModal(false)}
                type="button"
              >
                Keep Disabled
              </button>
              <button
                className="button"
                onClick={() => {
                  setField("allow_disruptive_tests", true);
                  setShowDisruptiveModal(false);
                }}
                type="button"
              >
                Enable For Assessment
              </button>
            </>
          }
        >
          <div className="warning-banner">
            <strong>Warning:</strong> enabling this flag allows the separate disruptive tests module to be used later for this assessment, but the global setting and maintenance-window confirmation are still required before any performance-impacting job can run.
          </div>
          <div className="summary-card">
            <span className="summary-card__label">Current Safety Gates</span>
            <strong>
              Global disruptive module:{" "}
              {getSettingValue(settings, "performance_module_enabled", "false") ===
              "true"
                ? "enabled"
                : "disabled"}
            </strong>
            <div className="summary-list-inline">
              This assessment flag does not bypass the global default-off control.
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
