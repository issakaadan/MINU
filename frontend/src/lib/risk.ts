import type { Finding } from "../types";

export const severityWeight: Record<string, number> = {
  Critical: 5,
  High: 4,
  Medium: 3,
  Low: 2,
  Informational: 1,
};

export function severityRank(severity: string): number {
  return severityWeight[severity] ?? 0;
}

export function findingRiskScore(findings: Pick<Finding, "severity">[]): number {
  return findings.reduce(
    (total, finding) => total + severityRank(finding.severity),
    0,
  );
}

export function overallRiskLabel(findings: Pick<Finding, "severity">[]): string {
  if (findings.some((finding) => finding.severity === "Critical")) {
    return "Critical";
  }
  if (findings.some((finding) => finding.severity === "High")) {
    return "High";
  }
  if (findings.some((finding) => finding.severity === "Medium")) {
    return "Medium";
  }
  if (findings.some((finding) => finding.severity === "Low")) {
    return "Low";
  }
  return "Informational";
}
