export type Assessment = {
  id: number;
  project_name: string;
  client_name: string;
  assessor_name: string;
  assessment_date: string;
  description: string;
  scan_intensity: "Light" | "Standard" | "Deep";
  allow_disruptive_tests: boolean;
  status: string;
  created_at: string;
  updated_at: string;
};

export type AssessmentCreatePayload = Omit<
  Assessment,
  "id" | "created_at" | "updated_at"
>;

export type AssessmentUpdatePayload = {
  allow_disruptive_tests: boolean;
};

export type Scope = {
  id: number;
  assessment_id: number;
  name: string;
  notes: string;
  network_ranges: string[];
  individual_ips: string[];
  excluded_ips: string[];
  included_targets: string[];
  has_external_targets: boolean;
  external_scope_confirmed: boolean;
  is_authorized: boolean;
  enforce_private_targets: boolean;
  created_at: string;
  updated_at: string;
};

export type ScopeCreatePayload = {
  assessment_id: number;
  name: string;
  network_ranges: string[];
  individual_ips: string[];
  excluded_ips: string[];
  notes: string;
  external_scope_confirmed: boolean;
  legal_acknowledged: boolean;
};

export type Port = {
  id: number;
  port_number: number;
  protocol: string;
  state: string;
};

export type Service = {
  id: number;
  name: string;
  product: string;
  version: string;
  banner: string;
  observations: Record<string, unknown>;
  confidence: number;
  port_id: number | null;
};

export type Host = {
  id: number;
  assessment_id: number;
  scope_id: number | null;
  address: string;
  hostname: string;
  mac_address: string;
  vendor_name: string;
  device_type: string;
  discovery_method: string;
  operating_system: string;
  status: string;
  notes: string;
  last_seen_at: string;
  created_at: string;
  ports: Port[];
  services: Service[];
};

export type Finding = {
  id: number;
  assessment_id: number;
  host_id: number | null;
  service_id: number | null;
  source: string;
  rule_key: string;
  title: string;
  severity: string;
  priority: string;
  category: string;
  status: string;
  affected_host: string;
  port_number: number | null;
  service_name: string;
  evidence: string;
  technical_explanation: string;
  business_impact: string;
  remediation: string;
  description: string;
  recommendation: string;
  created_at: string;
};

export type ScanJob = {
  id: number;
  job_type: "safe_discovery" | "safe_enumeration" | "disruptive_tests";
  assessment_id: number;
  scope_id: number;
  name: string;
  profile_name: string;
  scan_intensity: "Light" | "Standard" | "Deep";
  requested_targets: string[];
  include_service_detection: boolean;
  include_safe_checks: boolean;
  udp_scan_enabled: boolean;
  include_performance_module: boolean;
  legal_acknowledged: boolean;
  warning_acknowledged: boolean;
  maintenance_window_confirmed: boolean;
  status: string;
  progress: number;
  progress_message: string;
  log_entries: string[];
  result_summary: Record<string, number | string>;
  log_path: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ScanJobCreatePayload = Omit<
  ScanJob,
  | "id"
  | "status"
  | "progress"
  | "progress_message"
  | "log_entries"
  | "result_summary"
  | "log_path"
  | "started_at"
  | "completed_at"
  | "created_at"
  | "updated_at"
>;

export type DisruptiveTestResult = {
  id: number;
  assessment_id: number;
  scope_id: number;
  scan_job_id: number;
  host_id: number | null;
  target_host: string;
  hostname: string;
  result_label: string;
  ping_samples_sent: number;
  ping_samples_received: number;
  packet_loss_percent: number | null;
  min_latency_ms: number | null;
  avg_latency_ms: number | null;
  max_latency_ms: number | null;
  response_time_comparison_ms: number | null;
  connect_port: number | null;
  connect_time_ms: number | null;
  bandwidth_estimate_kbps: number | null;
  status: string;
  notes: string;
  observation_details: Record<string, unknown>;
  warning_acknowledged: boolean;
  maintenance_window_confirmed: boolean;
  created_at: string;
};

export type Report = {
  id: number;
  assessment_id: number;
  scan_job_id: number | null;
  name: string;
  report_type: "executive" | "technical";
  format: string;
  status: string;
  storage_path: string;
  created_at: string;
};

export type ReportGeneratePayload = {
  assessment_id: number;
  report_type: "executive" | "technical";
};

export type Setting = {
  id: number;
  key: string;
  value: string;
  description: string;
  created_at: string;
  updated_at: string;
};

export type DashboardSummary = {
  assessments: number;
  scopes: number;
  hosts: number;
  live_hosts: number;
  unknown_devices: number;
  total_open_ports: number;
  findings: number;
  critical_findings: number;
  high_findings: number;
  medium_findings: number;
  low_findings: number;
  informational_findings: number;
  certificate_issues: number;
  reports: number;
  scans_running: number;
  scans_total: number;
};
