import { useEffect, useMemo, useState } from "react";

import { DataTable } from "../components/DataTable";
import { LoadingState } from "../components/LoadingState";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { SeverityBadge } from "../components/SeverityBadge";
import { StatusPill } from "../components/StatusPill";
import { DonutChart } from "../components/charts/DonutChart";
import { HorizontalBarChart } from "../components/charts/HorizontalBarChart";
import { LineChart } from "../components/charts/LineChart";
import { api } from "../lib/api";
import { findingRiskScore, overallRiskLabel, severityRank } from "../lib/risk";
import { formatDate } from "../lib/utils";
import type {
  Assessment,
  DashboardSummary,
  Finding,
  Host,
  ScanJob,
} from "../types";

const emptySummary: DashboardSummary = {
  assessments: 0,
  scopes: 0,
  hosts: 0,
  live_hosts: 0,
  unknown_devices: 0,
  total_open_ports: 0,
  findings: 0,
  critical_findings: 0,
  high_findings: 0,
  medium_findings: 0,
  low_findings: 0,
  informational_findings: 0,
  certificate_issues: 0,
  reports: 0,
  scans_running: 0,
  scans_total: 0,
};

const severityPalette: Record<string, string> = {
  Critical: "#ff7a7a",
  High: "#ff9f6e",
  Medium: "#f5c45f",
  Low: "#49cbb6",
  Informational: "#86d9ef",
};

const chartPalette = [
  "#41c6b4",
  "#f5a85f",
  "#7aa7ff",
  "#f07c9c",
  "#a9df6c",
  "#d6c16c",
  "#82d7f1",
  "#b58cff",
];

type RiskyHostRow = {
  host: string;
  deviceType: string;
  score: number;
  findingCount: number;
  highestSeverity: string;
  services: string[];
};

function formatScanType(scan: ScanJob): string {
  if (scan.job_type === "disruptive_tests") {
    return "Performance-Impacting";
  }
  return scan.job_type === "safe_enumeration" ? "Port Enumeration" : "Discovery";
}

function serviceNameForPort(host: Host, portId: number): string {
  return (
    host.services.find((service) => service.port_id === portId)?.name || "Unknown"
  );
}

export function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary>(emptySummary);
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [scans, setScans] = useState<ScanJob[]>([]);
  const [hosts, setHosts] = useState<Host[]>([]);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [summaryData, assessmentsData, findingsData, scansData, hostsData] =
          await Promise.all([
            api.getSummary(),
            api.getAssessments(),
            api.getFindings(),
            api.getScans(),
            api.getHosts(),
          ]);
        setSummary(summaryData);
        setAssessments(assessmentsData);
        setFindings(findingsData);
        setScans(scansData);
        setHosts(hostsData);
        setError("");
      } catch (loadError) {
        setError((loadError as Error).message);
      } finally {
        setIsLoading(false);
      }
    }

    void load();
  }, []);

  const severityDistribution = useMemo(
    () => [
      {
        label: "Critical",
        value: summary.critical_findings,
        color: severityPalette.Critical,
      },
      {
        label: "High",
        value: summary.high_findings,
        color: severityPalette.High,
      },
      {
        label: "Medium",
        value: summary.medium_findings,
        color: severityPalette.Medium,
      },
      {
        label: "Low",
        value: summary.low_findings,
        color: severityPalette.Low,
      },
      {
        label: "Informational",
        value: summary.informational_findings,
        color: severityPalette.Informational,
      },
    ],
    [summary],
  );

  const assetTypeDistribution = useMemo(() => {
    const counts = new Map<string, number>();
    hosts.forEach((host) => {
      const label = host.device_type || "Unknown";
      counts.set(label, (counts.get(label) ?? 0) + 1);
    });

    return Array.from(counts.entries())
      .sort((left, right) => right[1] - left[1])
      .slice(0, 6)
      .map(([label, value], index) => ({
        label,
        value,
        color: chartPalette[index % chartPalette.length],
      }));
  }, [hosts]);

  const openPortsByService = useMemo(() => {
    const counts = new Map<string, number>();
    hosts.forEach((host) => {
      host.ports.forEach((port) => {
        const label = serviceNameForPort(host, port.id);
        counts.set(label, (counts.get(label) ?? 0) + 1);
      });
    });

    return Array.from(counts.entries())
      .sort((left, right) => right[1] - left[1])
      .slice(0, 8)
      .map(([label, value], index) => ({
        label,
        value,
        color: chartPalette[index % chartPalette.length],
      }));
  }, [hosts]);

  const findingsByHost = useMemo(() => {
    const counts = new Map<string, number>();
    findings.forEach((finding) => {
      const label = finding.affected_host || "Assessment-wide";
      counts.set(label, (counts.get(label) ?? 0) + 1);
    });

    return Array.from(counts.entries())
      .sort((left, right) => right[1] - left[1])
      .slice(0, 8)
      .map(([label, value], index) => ({
        label,
        value,
        color: chartPalette[index % chartPalette.length],
      }));
  }, [findings]);

  const riskyHosts = useMemo(() => {
    const grouped = new Map<string, RiskyHostRow>();

    findings.forEach((finding) => {
      const host = finding.affected_host || "Assessment-wide";
      const hostRecord = hosts.find((candidate) => candidate.address === host);
      const existing = grouped.get(host) ?? {
        host,
        deviceType: hostRecord?.device_type || "Unknown",
        score: 0,
        findingCount: 0,
        highestSeverity: "Informational",
        services: [],
      };

      existing.score += severityRank(finding.severity);
      existing.findingCount += 1;

      if (severityRank(finding.severity) > severityRank(existing.highestSeverity)) {
        existing.highestSeverity = finding.severity;
      }

      if (
        finding.service_name &&
        !existing.services.includes(finding.service_name)
      ) {
        existing.services.push(finding.service_name);
      }

      grouped.set(host, existing);
    });

    return Array.from(grouped.values())
      .sort((left, right) => {
        if (right.score !== left.score) {
          return right.score - left.score;
        }
        return right.findingCount - left.findingCount;
      })
      .slice(0, 5);
  }, [findings, hosts]);

  const topRiskyHostsChart = useMemo(
    () =>
      riskyHosts.map((row) => ({
        label: row.host,
        value: row.score,
        meta: `${row.findingCount} findings | ${row.highestSeverity}`,
        color: severityPalette[row.highestSeverity] ?? severityPalette.Informational,
      })),
    [riskyHosts],
  );

  const riskTrend = useMemo(() => {
    return scans
      .filter((scan) => Boolean(scan.completed_at))
      .sort((left, right) => {
        const leftTime = new Date(left.completed_at ?? left.created_at).getTime();
        const rightTime = new Date(right.completed_at ?? right.created_at).getTime();
        return leftTime - rightTime;
      })
      .slice(-8)
      .map((scan) => {
        const cutoff = new Date(scan.completed_at ?? scan.created_at).getTime();
        const relatedFindings = findings.filter((finding) => {
          if (finding.assessment_id !== scan.assessment_id) {
            return false;
          }
          return new Date(finding.created_at).getTime() <= cutoff;
        });

        return {
          label: `${scan.name} #${scan.id}`,
          value: findingRiskScore(relatedFindings),
          detail: `${overallRiskLabel(relatedFindings)} | ${formatDate(
            scan.completed_at ?? scan.created_at,
          )}`,
        };
      });
  }, [findings, scans]);

  return (
    <div className="page">
      <PageHeader
        title="Dashboard"
        subtitle="Track assessment volume, asset exposure, risk concentration, and how safe scan results are trending over time."
      />

      {error ? <div className="error-banner">{error}</div> : null}
      {isLoading ? (
        <LoadingState
          title="Loading Dashboard"
          message="Pulling summary metrics, findings, scans, and host inventory from the local API."
        />
      ) : null}

      <div className="metrics-grid metrics-grid--dashboard">
        <MetricCard
          label="Total Assessments"
          value={summary.assessments}
          caption="Saved engagement records"
        />
        <MetricCard
          label="Total Discovered Hosts"
          value={summary.hosts}
          caption="Authorized inventory observed"
        />
        <MetricCard
          label="Live Hosts"
          value={summary.live_hosts}
          caption="Hosts responding to safe checks"
        />
        <MetricCard
          label="Unknown Devices"
          value={summary.unknown_devices}
          caption="Classification still needs review"
        />
        <MetricCard
          label="Total Open Ports"
          value={summary.total_open_ports}
          caption="Safe TCP findings stored"
        />
        <MetricCard
          label="Critical Findings"
          value={summary.critical_findings}
          caption="Immediate action recommended"
        />
        <MetricCard
          label="High Findings"
          value={summary.high_findings}
          caption="Priority remediation queue"
        />
        <MetricCard
          label="Medium Findings"
          value={summary.medium_findings}
          caption="Planned follow-up needed"
        />
        <MetricCard
          label="Low Findings"
          value={summary.low_findings}
          caption={`${summary.informational_findings} informational`}
        />
      </div>

      <div className="chart-grid">
        <Panel
          title="Severity Distribution"
          subtitle="Current finding mix across all saved assessments."
        >
          <DonutChart
            items={severityDistribution}
            totalLabel="findings"
            emptyMessage="No findings are available yet."
          />
        </Panel>

        <Panel
          title="Asset Type Distribution"
          subtitle="Detected device classes across the current host inventory."
        >
          <DonutChart
            items={assetTypeDistribution}
            totalLabel="assets"
            emptyMessage="Run safe discovery to populate asset types."
          />
        </Panel>

        <Panel
          title="Open Ports By Service"
          subtitle="Most frequently observed services across authorized hosts."
        >
          <HorizontalBarChart
            items={openPortsByService}
            emptyMessage="Run safe port enumeration to populate service counts."
            valueLabel="ports"
          />
        </Panel>

        <Panel
          title="Findings By Host"
          subtitle="Hosts with the highest concentration of generated findings."
        >
          <HorizontalBarChart
            items={findingsByHost}
            emptyMessage="No host findings are available yet."
            valueLabel="findings"
          />
        </Panel>

        <Panel
          title="Risk Trend Across Scans"
          subtitle="Aggregate risk score across the most recent completed scan milestones."
        >
          <LineChart
            items={riskTrend}
            emptyMessage="Complete more scan jobs to display a risk trend."
          />
        </Panel>

        <Panel
          title="Top Risky Hosts"
          subtitle="Hosts ranked by weighted severity and total finding count."
        >
          <HorizontalBarChart
            items={topRiskyHostsChart}
            emptyMessage="No risky hosts are available yet."
            valueLabel="risk"
          />
        </Panel>
      </div>

      <div className="split-grid">
        <DataTable
          title="Recent Assessments"
          subtitle="Quick visibility into the latest assessment records."
          rows={assessments.slice(0, 5)}
          searchPlaceholder="Search assessments"
          loading={isLoading}
          searchAccessor={(assessment) =>
            `${assessment.project_name} ${assessment.client_name} ${assessment.assessor_name} ${assessment.status}`
          }
          columns={[
            { header: "Project", cell: (assessment) => assessment.project_name },
            { header: "Client", cell: (assessment) => assessment.client_name },
            {
              header: "Status",
              cell: (assessment) => <StatusPill label={assessment.status} />,
            },
            {
              header: "Created",
              cell: (assessment) => formatDate(assessment.created_at),
            },
          ]}
        />

        <DataTable
          title="Recent Scan Activity"
          subtitle="Latest discovery and enumeration activity across assessments."
          rows={scans.slice(0, 5)}
          searchPlaceholder="Search scan jobs"
          loading={isLoading}
          searchAccessor={(scan) =>
            `${scan.name} ${scan.profile_name} ${scan.status} ${scan.scan_intensity}`
          }
          columns={[
            { header: "Job", cell: (scan) => scan.name },
            { header: "Type", cell: (scan) => formatScanType(scan) },
            {
              header: "Status",
              cell: (scan) => <StatusPill label={scan.status} />,
            },
            { header: "Intensity", cell: (scan) => scan.scan_intensity },
            { header: "Progress", cell: (scan) => `${scan.progress}%` },
          ]}
        />
      </div>

      <DataTable
        title="Top Risky Host Detail"
        subtitle="Use this detail view to jump from dashboard trends into remediation planning."
        rows={riskyHosts}
        searchPlaceholder="Search risky hosts"
        loading={isLoading}
        searchAccessor={(row) =>
          `${row.host} ${row.deviceType} ${row.highestSeverity} ${row.services.join(" ")}`
        }
        emptyMessage="No risky hosts are available yet."
        columns={[
          { header: "Host", cell: (row) => row.host },
          { header: "Device Type", cell: (row) => row.deviceType },
          {
            header: "Highest Severity",
            cell: (row) => <SeverityBadge severity={row.highestSeverity} />,
          },
          { header: "Risk Score", cell: (row) => row.score },
          { header: "Findings", cell: (row) => row.findingCount },
          {
            header: "Services",
            cell: (row) => row.services.join(", ") || "Host-level only",
          },
        ]}
      />
    </div>
  );
}
