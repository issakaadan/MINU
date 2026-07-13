import type { ScanJob } from "../types";

import { Panel } from "./Panel";
import { StatusPill } from "./StatusPill";

type ProgressPanelProps = {
  scans: ScanJob[];
  onStart?: (scanId: number) => Promise<void> | void;
  title?: string;
  subtitle?: string;
};

function formatJobType(jobType: ScanJob["job_type"]): string {
  if (jobType === "disruptive_tests") {
    return "performance-impacting tests";
  }
  return jobType === "safe_enumeration"
    ? "safe port enumeration"
    : "safe discovery";
}

function formatProfileName(scan: ScanJob): string {
  if (scan.job_type === "disruptive_tests") {
    return "performance-impacting";
  }
  if (scan.job_type === "safe_enumeration") {
    return scan.profile_name === "full-tcp" ? "full TCP" : "common TCP";
  }
  return "authorized discovery";
}

export function ProgressPanel({
  scans,
  onStart,
  title = "Scan Progress",
  subtitle = "Each job stays inside the authorized scope, respects exclusions, and records only safe discovery or enumeration observations.",
}: ProgressPanelProps) {
  return (
    <Panel
      title={title}
      subtitle={subtitle}
    >
      <div className="progress-stack">
        {scans.length > 0 ? (
          scans.map((scan) => (
            <div className="progress-card" key={scan.id}>
              <div className="progress-card__row">
                <div>
                  <strong>{scan.name}</strong>
                  <p>
                    Type: {formatJobType(scan.job_type)} | Profile:{" "}
                    {formatProfileName(scan)} | Intensity: {scan.scan_intensity}
                  </p>
                  <p>Targets: {scan.requested_targets.join(", ")}</p>
                </div>
                <div className="progress-card__meta">
                  <StatusPill label={scan.status} />
                  {scan.status === "pending" && onStart ? (
                    <button
                      className="button button--ghost"
                      onClick={() => void onStart(scan.id)}
                      type="button"
                    >
                      {scan.job_type === "disruptive_tests"
                        ? "Run Performance Checks"
                        : scan.job_type === "safe_enumeration"
                        ? "Run Enumeration"
                        : "Run Discovery"}
                    </button>
                  ) : null}
                </div>
              </div>
              <div className="progress-bar">
                <div
                  className="progress-bar__fill"
                  style={{ width: `${scan.progress}%` }}
                />
              </div>
              <span className="progress-bar__caption">
                {scan.progress}% complete
              </span>
              {scan.progress_message ? (
                <p className="progress-card__message">{scan.progress_message}</p>
              ) : null}
              <div className="progress-card__summary">
                {scan.job_type === "disruptive_tests" ? (
                  <>
                    <span>
                      Hosts: {Number(scan.result_summary.processed_hosts ?? 0)}/
                      {Number(scan.result_summary.total_hosts ?? 0)}
                    </span>
                    <span>
                      Results: {Number(scan.result_summary.results_recorded ?? 0)}
                    </span>
                    <span>
                      Ping Hosts: {Number(scan.result_summary.hosts_with_ping ?? 0)}
                    </span>
                  </>
                ) : scan.job_type === "safe_enumeration" ? (
                  <>
                    <span>
                      Hosts: {Number(scan.result_summary.processed_hosts ?? 0)}/
                      {Number(scan.result_summary.total_hosts ?? 0)}
                    </span>
                    <span>
                      Open Ports: {Number(scan.result_summary.open_ports ?? 0)}
                    </span>
                    <span>
                      Services: {Number(scan.result_summary.services_detected ?? 0)}
                    </span>
                  </>
                ) : (
                  <>
                    <span>
                      Live: {Number(scan.result_summary.live_hosts ?? 0)}
                    </span>
                    <span>
                      Offline: {Number(scan.result_summary.offline_hosts ?? 0)}
                    </span>
                    <span>
                      Unknown: {Number(scan.result_summary.unknown_hosts ?? 0)}
                    </span>
                  </>
                )}
              </div>
                {scan.log_entries.length > 0 ? (
                  <div className="progress-card__logs">
                    {scan.log_entries.slice(-4).map((entry) => (
                      <div key={entry}>{entry}</div>
                    ))}
                </div>
              ) : null}
            </div>
          ))
        ) : (
          <div className="empty-state">No jobs have been created yet.</div>
        )}
      </div>
    </Panel>
  );
}
