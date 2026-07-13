import { useEffect, useMemo, useState } from "react";

import { LoadingState } from "../components/LoadingState";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { api } from "../lib/api";
import type { Setting } from "../types";

type SettingOption = {
  label: string;
  value: string;
};

type SettingMeta = {
  label: string;
  subtitle: string;
  type: "boolean" | "select" | "text" | "textarea";
  rows?: number;
  options?: SettingOption[];
  warning?: {
    title: string;
    message: string;
    confirmLabel: string;
  };
};

const settingMeta: Record<string, SettingMeta> = {
  organization_name: {
    label: "Organization Name",
    subtitle: "Displayed throughout the local application shell.",
    type: "text",
  },
  legal_notice: {
    label: "Legal Notice",
    subtitle:
      "Shown inside the app before scope and scan actions so operators see the guardrails every time.",
    type: "textarea",
    rows: 5,
  },
  default_scan_intensity: {
    label: "Default Scan Intensity",
    subtitle:
      "Applied as the suggested default when a new assessment is created.",
    type: "select",
    options: [
      { label: "Light", value: "Light" },
      { label: "Standard", value: "Standard" },
      { label: "Deep", value: "Deep" },
    ],
  },
  allow_external_scope: {
    label: "Allow External Scope",
    subtitle:
      "Keeps public or external targets blocked by default unless you deliberately change this policy.",
    type: "boolean",
    warning: {
      title: "Enable External Scope?",
      message:
        "This setting removes the default private/local-only protection. Enable it only when you have explicit written authorization for public or external targets.",
      confirmLabel: "Enable External Scope",
    },
  },
  performance_module_enabled: {
    label: "Enable Disruptive Tests",
    subtitle:
      "Global gate for the separate disruptive / performance-impacting tests module. It should remain disabled unless there is a controlled need.",
    type: "boolean",
    warning: {
      title: "Enable Performance-Impacting Module?",
      message:
        "This module remains disabled by default for safety. Enabling it here still does not permit use unless the assessment also allows it and the operator confirms an approved maintenance window.",
      confirmLabel: "Enable Module",
    },
  },
  report_branding_name: {
    label: "Report Branding Name",
    subtitle:
      "Primary brand name shown on executive and technical report covers.",
    type: "text",
  },
  report_branding_tagline: {
    label: "Report Branding Tagline",
    subtitle: "Short line shown beneath the report branding name.",
    type: "text",
  },
  report_branding_contact: {
    label: "Report Branding Footer / Contact",
    subtitle:
      "Footer line shown on generated reports for ownership, contact, or internal-use messaging.",
    type: "text",
  },
  default_report_format: {
    label: "Default Report Format",
    subtitle:
      "Default preferred format for newly generated report bundles.",
    type: "select",
    options: [
      { label: "HTML", value: "html" },
      { label: "PDF", value: "pdf" },
    ],
  },
};

const settingOrder = [
  "organization_name",
  "legal_notice",
  "default_scan_intensity",
  "allow_external_scope",
  "performance_module_enabled",
  "report_branding_name",
  "report_branding_tagline",
  "report_branding_contact",
  "default_report_format",
];

export function SettingsPage() {
  const [settings, setSettings] = useState<Setting[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [savingKey, setSavingKey] = useState("");
  const [pendingWarning, setPendingWarning] = useState<{
    settingId: number;
    key: string;
  } | null>(null);

  useEffect(() => {
    async function load() {
      try {
        setError("");
        const settingRows = await api.getSettings();
        setSettings(settingRows);
      } catch (loadError) {
        setError((loadError as Error).message);
      } finally {
        setIsLoading(false);
      }
    }

    void load();
  }, []);

  const orderedSettings = useMemo(() => {
    const orderMap = new Map(
      settingOrder.map((key, index) => [key, index] as const),
    );

    return [...settings].sort((left, right) => {
      const leftIndex = orderMap.get(left.key) ?? Number.MAX_SAFE_INTEGER;
      const rightIndex = orderMap.get(right.key) ?? Number.MAX_SAFE_INTEGER;
      if (leftIndex !== rightIndex) {
        return leftIndex - rightIndex;
      }
      return left.key.localeCompare(right.key);
    });
  }, [settings]);

  function updateLocalSetting(settingId: number, value: string) {
    setSettings((current) =>
      current.map((item) =>
        item.id === settingId ? { ...item, value } : item,
      ),
    );
  }

  async function handleSave(setting: Setting) {
    setError("");
    setMessage("");
    setSavingKey(setting.key);
    try {
      const updated = await api.updateSetting(
        setting.key,
        setting.value,
        setting.description,
      );
      setSettings((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setMessage(`${settingMeta[setting.key]?.label ?? setting.key} saved.`);
    } catch (saveError) {
      setError((saveError as Error).message);
    } finally {
      setSavingKey("");
    }
  }

  function handleBooleanChange(setting: Setting, checked: boolean) {
    const metadata = settingMeta[setting.key];
    if (checked && metadata?.warning) {
      setPendingWarning({ settingId: setting.id, key: setting.key });
      return;
    }
    updateLocalSetting(setting.id, checked ? "true" : "false");
  }

  const pendingSetting =
    pendingWarning === null
      ? null
      : settings.find((setting) => setting.id === pendingWarning.settingId) ??
        null;
  const pendingMeta =
    pendingSetting === null ? null : settingMeta[pendingSetting.key];

  return (
    <div className="page">
      <PageHeader
        title="Settings"
        subtitle="Control safe defaults, scope guardrails, disruptive module gates, and report branding for local operation."
      />
      {isLoading ? (
        <LoadingState
          title="Loading Settings"
          message="Reading the local application policy and branding settings."
        />
      ) : null}
      {error ? <div className="error-banner">{error}</div> : null}
      {message ? <div className="success-banner">{message}</div> : null}
      <div className="settings-stack">
        {orderedSettings.map((setting) => {
          const metadata = settingMeta[setting.key];
          const title = metadata?.label ?? setting.key;
          const subtitle =
            (metadata?.subtitle ?? setting.description) || "No description";
          const isSaving = savingKey === setting.key;
          return (
            <Panel
              key={setting.id}
              title={title}
              subtitle={subtitle}
              actions={
                <button
                  className="button"
                  disabled={isSaving}
                  onClick={() => void handleSave(setting)}
                  type="button"
                >
                  {isSaving ? "Saving..." : "Save"}
                </button>
              }
            >
              {metadata?.type === "boolean" ? (
                <label className="checkbox">
                  <input
                    checked={setting.value.toLowerCase() === "true"}
                    onChange={(event) =>
                      handleBooleanChange(setting, event.target.checked)
                    }
                    type="checkbox"
                  />
                  {title}
                </label>
              ) : null}

              {metadata?.type === "select" ? (
                <select
                  className="input"
                  value={setting.value}
                  onChange={(event) =>
                    updateLocalSetting(setting.id, event.target.value)
                  }
                >
                  {metadata.options?.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              ) : null}

              {metadata?.type === "text" ? (
                <input
                  className="input"
                  value={setting.value}
                  onChange={(event) =>
                    updateLocalSetting(setting.id, event.target.value)
                  }
                />
              ) : null}

              {metadata?.type === "textarea" ? (
                <textarea
                  className="input textarea"
                  rows={metadata.rows ?? 4}
                  value={setting.value}
                  onChange={(event) =>
                    updateLocalSetting(setting.id, event.target.value)
                  }
                />
              ) : null}

              {setting.key === "allow_external_scope" ? (
                <div className="warning-banner">
                  <strong>Warning:</strong> keeping this disabled preserves the default protection that blocks public and external targets in scope management.
                </div>
              ) : null}

              {setting.key === "performance_module_enabled" ? (
                <div className="warning-banner">
                  <strong>Warning:</strong> even when enabled here, the disruptive tests module still requires explicit per-assessment enablement and a maintenance-window confirmation before any job can run.
                </div>
              ) : null}
            </Panel>
          );
        })}
      </div>

      {pendingSetting && pendingMeta?.warning ? (
        <Modal
          title={pendingMeta.warning.title}
          onClose={() => setPendingWarning(null)}
          actions={
            <>
              <button
                className="button button--ghost"
                onClick={() => setPendingWarning(null)}
                type="button"
              >
                Cancel
              </button>
              <button
                className="button"
                onClick={() => {
                  updateLocalSetting(pendingSetting.id, "true");
                  setPendingWarning(null);
                }}
                type="button"
              >
                {pendingMeta.warning.confirmLabel}
              </button>
            </>
          }
        >
          <div className="warning-banner">
            <strong>Warning:</strong> {pendingMeta.warning.message}
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
