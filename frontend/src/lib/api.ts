import type {
  Assessment,
  AssessmentCreatePayload,
  AssessmentUpdatePayload,
  DashboardSummary,
  DisruptiveTestResult,
  Finding,
  Host,
  Report,
  ReportGeneratePayload,
  ScanJob,
  ScanJobCreatePayload,
  Scope,
  ScopeCreatePayload,
  Setting,
} from "../types";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000/api";

type ApiFieldErrors = Record<string, string[]>;

export class ApiError extends Error {
  fieldErrors: ApiFieldErrors;

  constructor(message: string, fieldErrors: ApiFieldErrors = {}) {
    super(message);
    this.name = "ApiError";
    this.fieldErrors = fieldErrors;
  }
}

function withQuery(
  path: string,
  params: Record<string, number | string | undefined | null>,
): string {
  const query = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") {
      return;
    }
    query.set(key, String(value));
  });

  const queryString = query.toString();
  return queryString ? `${path}?${queryString}` : path;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
      ...init,
    });
  } catch {
    throw new ApiError(
      "Unable to reach the local backend. Confirm the FastAPI service is running on http://127.0.0.1:8000.",
    );
  }

  if (!response.ok) {
    let message = "Request failed";
    let fieldErrors: ApiFieldErrors = {};
    const errorBody = await response.text();

    try {
      const payload = JSON.parse(errorBody) as {
        detail?:
          | string
          | { message?: string; field_errors?: ApiFieldErrors }
          | Array<{ loc?: Array<string | number>; msg?: string }>;
      };

      if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (Array.isArray(payload.detail)) {
        message =
          payload.detail.map((issue) => issue.msg).filter(Boolean).join(" ") ||
          message;
      } else if (payload.detail) {
        message = payload.detail.message ?? message;
        fieldErrors = payload.detail.field_errors ?? {};
      }
    } catch {
      if (errorBody) {
        message = errorBody;
      }
    }

    throw new ApiError(message, fieldErrors);
  }

  return (await response.json()) as T;
}

export const api = {
  getSummary: () => request<DashboardSummary>("/assessments/summary"),
  getAssessments: () => request<Assessment[]>("/assessments"),
  createAssessment: (payload: AssessmentCreatePayload) =>
    request<Assessment>("/assessments", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateAssessment: (assessmentId: number, payload: AssessmentUpdatePayload) =>
    request<Assessment>(`/assessments/${assessmentId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  getScopes: () => request<Scope[]>("/scopes"),
  createScope: (payload: ScopeCreatePayload) =>
    request<Scope>("/scopes", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getScans: (options?: { assessmentId?: number; status?: string }) =>
    request<ScanJob[]>(
      withQuery("/scans", {
        assessment_id: options?.assessmentId,
        status: options?.status,
      }),
    ),
  createScan: (payload: ScanJobCreatePayload) =>
    request<ScanJob>("/scans", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  startScan: (scanId: number) =>
    request<ScanJob>(`/scans/${scanId}/start`, {
      method: "POST",
    }),
  getHosts: (assessmentId?: number) =>
    request<Host[]>(
      withQuery("/hosts", {
        assessment_id: assessmentId,
      }),
    ),
  getFindings: (options?: { assessmentId?: number; severity?: string }) =>
    request<Finding[]>(
      withQuery("/findings", {
        assessment_id: options?.assessmentId,
        severity: options?.severity,
      }),
    ),
  getReports: (assessmentId?: number) =>
    request<Report[]>(
      withQuery("/reports", {
        assessment_id: assessmentId,
      }),
    ),
  generateReports: (payload: ReportGeneratePayload) =>
    request<Report[]>("/reports/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getReportDownloadUrl: (reportId: number) =>
    `${API_BASE_URL}/reports/${reportId}/download`,
  getDisruptiveTestResults: (options?: {
    assessmentId?: number;
    scanJobId?: number;
  }) =>
    request<DisruptiveTestResult[]>(
      withQuery("/disruptive-tests", {
        assessment_id: options?.assessmentId,
        scan_job_id: options?.scanJobId,
      }),
    ),
  getSettings: () => request<Setting[]>("/settings"),
  updateSetting: (key: string, value: string, description: string) =>
    request<Setting>(`/settings/${key}`, {
      method: "PUT",
      body: JSON.stringify({ value, description }),
    }),
};
