import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { LoadingState } from "../components/LoadingState";
import { PageHeader } from "../components/PageHeader";
import { Panel } from "../components/Panel";
import { StatusPill } from "../components/StatusPill";
import { api } from "../lib/api";
import { buildCsvFilename, downloadCsv } from "../lib/csv";
import { formatDate } from "../lib/utils";
import type { Assessment, Host, Port, Service } from "../types";

type InventoryView = {
  host: Host;
  visiblePorts: Port[];
};

type WebCookieObservation = {
  name: string;
  secure: boolean;
  http_only: boolean;
  same_site: string;
  missing_flags: string[];
};

type TlsObservation = {
  subject: string;
  issuer: string;
  subject_common_name: string;
  issuer_common_name: string;
  subject_alt_names: string[];
  dns_names: string[];
  ip_addresses: string[];
  valid_from: string;
  valid_until: string;
  expired: boolean;
  expiring_soon: boolean;
  days_until_expiry: number | null;
  self_signed: boolean;
  hostname_reference: string;
  hostname_mismatch_detectable: boolean;
  hostname_mismatch: boolean;
  hostname_mismatch_reason: string;
  protocol: string;
  cipher: string;
  cipher_protocol: string;
  cipher_bits: number | null;
};

type WebObservation = {
  scheme: string;
  url: string;
  status_code: number | null;
  reason_phrase: string;
  page_title: string;
  server_header: string;
  technology_hints: string[];
  headers: Record<string, string>;
  missing_security_headers: string[];
  cookies: WebCookieObservation[];
  risky_cookies: WebCookieObservation[];
  robots_txt_detected: boolean;
  sitemap_xml_detected: boolean;
  directory_listing_detected: boolean;
  default_page_detected: boolean;
  login_page_detected: boolean;
  is_admin_page: boolean;
  https_in_use: boolean;
  https_warnings: string[];
  redirect_location: string;
  body_snippet: string;
  tls: TlsObservation | null;
};

type WebServiceView = {
  host: Host;
  port: Port;
  service: Service;
  web: WebObservation;
};

function serviceForPort(host: Host, port: Port): Service | undefined {
  return host.services.find((service) => service.port_id === port.id);
}

function portForService(host: Host, service: Service): Port | undefined {
  return host.ports.find((port) => port.id === service.port_id);
}

function rowSearchText(port: Port, service?: Service): string {
  return [
    port.port_number,
    port.protocol,
    port.state,
    service?.name,
    service?.product,
    service?.version,
    service?.banner,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === "string")
    : [];
}

function asBoolean(value: unknown): boolean {
  return typeof value === "boolean" ? value : false;
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function parseCookieObservations(value: unknown): WebCookieObservation[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.map((entry) => {
    const cookie = asRecord(entry);
    return {
      name: typeof cookie.name === "string" ? cookie.name : "unnamed",
      secure: asBoolean(cookie.secure),
      http_only: asBoolean(cookie.http_only),
      same_site:
        typeof cookie.same_site === "string" ? cookie.same_site : "Unset",
      missing_flags: asStringArray(cookie.missing_flags),
    };
  });
}

function parseTlsObservation(value: unknown): TlsObservation | null {
  const tls = asRecord(value);
  if (Object.keys(tls).length === 0) {
    return null;
  }

  return {
    subject: typeof tls.subject === "string" ? tls.subject : "",
    issuer: typeof tls.issuer === "string" ? tls.issuer : "",
    subject_common_name:
      typeof tls.subject_common_name === "string"
        ? tls.subject_common_name
        : "",
    issuer_common_name:
      typeof tls.issuer_common_name === "string"
        ? tls.issuer_common_name
        : "",
    subject_alt_names: asStringArray(tls.subject_alt_names),
    dns_names: asStringArray(tls.dns_names),
    ip_addresses: asStringArray(tls.ip_addresses),
    valid_from: typeof tls.valid_from === "string" ? tls.valid_from : "",
    valid_until: typeof tls.valid_until === "string" ? tls.valid_until : "",
    expired: asBoolean(tls.expired),
    expiring_soon: asBoolean(tls.expiring_soon),
    days_until_expiry: asNumberOrNull(tls.days_until_expiry),
    self_signed: asBoolean(tls.self_signed),
    hostname_reference:
      typeof tls.hostname_reference === "string" ? tls.hostname_reference : "",
    hostname_mismatch_detectable: asBoolean(tls.hostname_mismatch_detectable),
    hostname_mismatch: asBoolean(tls.hostname_mismatch),
    hostname_mismatch_reason:
      typeof tls.hostname_mismatch_reason === "string"
        ? tls.hostname_mismatch_reason
        : "",
    protocol: typeof tls.protocol === "string" ? tls.protocol : "",
    cipher: typeof tls.cipher === "string" ? tls.cipher : "",
    cipher_protocol:
      typeof tls.cipher_protocol === "string" ? tls.cipher_protocol : "",
    cipher_bits: asNumberOrNull(tls.cipher_bits),
  };
}

function webObservationForService(service: Service): WebObservation | null {
  const observations = asRecord(service.observations);
  const web = asRecord(observations.web);
  if (Object.keys(web).length === 0) {
    return null;
  }

  const headers = Object.fromEntries(
    Object.entries(asRecord(web.headers)).filter(
      (entry): entry is [string, string] => typeof entry[1] === "string",
    ),
  );

  return {
    scheme: typeof web.scheme === "string" ? web.scheme : "http",
    url: typeof web.url === "string" ? web.url : "",
    status_code: asNumberOrNull(web.status_code),
    reason_phrase:
      typeof web.reason_phrase === "string" ? web.reason_phrase : "",
    page_title: typeof web.page_title === "string" ? web.page_title : "",
    server_header:
      typeof web.server_header === "string" ? web.server_header : "",
    technology_hints: asStringArray(web.technology_hints),
    headers,
    missing_security_headers: asStringArray(web.missing_security_headers),
    cookies: parseCookieObservations(web.cookies),
    risky_cookies: parseCookieObservations(web.risky_cookies),
    robots_txt_detected: asBoolean(web.robots_txt_detected),
    sitemap_xml_detected: asBoolean(web.sitemap_xml_detected),
    directory_listing_detected: asBoolean(web.directory_listing_detected),
    default_page_detected: asBoolean(web.default_page_detected),
    login_page_detected: asBoolean(web.login_page_detected),
    is_admin_page: asBoolean(web.is_admin_page),
    https_in_use: asBoolean(web.https_in_use),
    https_warnings: asStringArray(web.https_warnings),
    redirect_location:
      typeof web.redirect_location === "string" ? web.redirect_location : "",
    body_snippet: typeof web.body_snippet === "string" ? web.body_snippet : "",
    tls: parseTlsObservation(observations.tls),
  };
}

function isWebService(service: Service): boolean {
  const normalized = service.name.trim().toLowerCase();
  return (
    normalized === "http" ||
    normalized === "https" ||
    normalized === "elasticsearch" ||
    webObservationForService(service) !== null
  );
}

function boolLabel(value: boolean, positive: string, negative: string): string {
  return value ? positive : negative;
}

function formatOptionalDate(value: string): string {
  return value ? formatDate(value) : "Unavailable";
}

function tlsStatusSummary(tls: TlsObservation | null): string {
  if (!tls) {
    return "No certificate details captured";
  }

  const states: string[] = [];
  if (tls.expired) {
    states.push("Expired");
  } else if (tls.expiring_soon) {
    if (tls.days_until_expiry !== null) {
      states.push(`Expiring in ${tls.days_until_expiry} days`);
    } else {
      states.push("Expiring soon");
    }
  } else {
    states.push("Valid");
  }

  if (tls.self_signed) {
    states.push("Self-signed");
  }
  if (tls.hostname_mismatch_detectable && tls.hostname_mismatch) {
    states.push("Hostname mismatch");
  }
  if (tls.protocol) {
    states.push(tls.protocol);
  }

  return states.join(" | ");
}

export function AssetsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const assessmentParam = searchParams.get("assessmentId");
  const parsedAssessmentId = assessmentParam ? Number(assessmentParam) : null;
  const selectedAssessmentId =
    parsedAssessmentId !== null && Number.isFinite(parsedAssessmentId)
      ? parsedAssessmentId
      : null;
  const [assessments, setAssessments] = useState<Assessment[]>([]);
  const [hosts, setHosts] = useState<Host[]>([]);
  const [error, setError] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [query, setQuery] = useState("");
  const [serviceFilter, setServiceFilter] = useState("all");
  const [portFilter, setPortFilter] = useState("");
  const [viewMode, setViewMode] = useState<"inventory" | "web">("inventory");
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [assessmentRows, hostRows] = await Promise.all([
          api.getAssessments(),
          api.getHosts(selectedAssessmentId ?? undefined),
        ]);
        setAssessments(assessmentRows);
        setHosts(hostRows);
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
    }, 5000);

    return () => window.clearInterval(intervalId);
  }, [selectedAssessmentId]);

  const selectedAssessment = useMemo(
    () =>
      selectedAssessmentId === null
        ? null
        : assessments.find((assessment) => assessment.id === selectedAssessmentId) ??
          null,
    [assessments, selectedAssessmentId],
  );
  const assessmentsById = useMemo(
    () =>
      new Map(
        assessments.map((assessment) => [assessment.id, assessment] as const),
      ),
    [assessments],
  );

  const serviceOptions = useMemo(() => {
    const names = new Set<string>();
    hosts.forEach((host) => {
      host.services.forEach((service) => {
        if (!service.name) {
          return;
        }
        if (viewMode === "web" && !isWebService(service)) {
          return;
        }
        names.add(service.name);
      });
    });

    return Array.from(names).sort((left, right) => left.localeCompare(right));
  }, [hosts, viewMode]);

  const filteredInventory = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const normalizedPort = portFilter.trim();
    const noStructuredFilters =
      serviceFilter === "all" && normalizedPort.length === 0;

    return hosts.flatMap((host): InventoryView[] => {
      const hostSearchText = [
        host.address,
        host.hostname,
        host.mac_address,
        host.vendor_name,
        host.device_type,
        host.discovery_method,
        host.notes,
      ]
        .join(" ")
        .toLowerCase();
      const matchesHostQuery =
        normalizedQuery.length > 0 && hostSearchText.includes(normalizedQuery);
      const sortedPorts = [...host.ports].sort(
        (left, right) => left.port_number - right.port_number,
      );
      const visiblePorts = sortedPorts.filter((port) => {
        const service = serviceForPort(host, port);
        const matchesService =
          serviceFilter === "all" || service?.name === serviceFilter;
        const matchesPort =
          normalizedPort.length === 0 ||
          String(port.port_number) === normalizedPort;
        const matchesRowQuery =
          normalizedQuery.length === 0 ||
          rowSearchText(port, service).includes(normalizedQuery);

        if (!matchesService || !matchesPort) {
          return false;
        }

        return matchesHostQuery || matchesRowQuery;
      });

      if (visiblePorts.length > 0) {
        return [{ host, visiblePorts }];
      }

      if (host.ports.length === 0 && noStructuredFilters) {
        if (normalizedQuery.length === 0 || matchesHostQuery) {
          return [{ host, visiblePorts }];
        }
      }

      if (
        host.ports.length > 0 &&
        noStructuredFilters &&
        matchesHostQuery &&
        normalizedQuery.length > 0
      ) {
        return [{ host, visiblePorts: sortedPorts }];
      }

      return [];
    });
  }, [hosts, portFilter, query, serviceFilter]);

  const filteredWebServices = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const normalizedPort = portFilter.trim();

    return hosts
      .flatMap((host): WebServiceView[] =>
        host.services.flatMap((service) => {
          const web = webObservationForService(service);
          const port = portForService(host, service);
          if (!web || !port) {
            return [];
          }

          const matchesService =
            serviceFilter === "all" || service.name === serviceFilter;
          const matchesPort =
            normalizedPort.length === 0 ||
            String(port.port_number) === normalizedPort;
          const searchText = [
            host.address,
            host.hostname,
            host.vendor_name,
            host.device_type,
            service.name,
            service.product,
            service.version,
            web.url,
            web.page_title,
            web.server_header,
            ...web.technology_hints,
            ...Object.keys(web.headers),
            ...Object.values(web.headers),
            ...web.missing_security_headers,
            ...web.https_warnings,
            ...web.risky_cookies.map((cookie) => cookie.name),
            ...web.risky_cookies.flatMap((cookie) => cookie.missing_flags),
            web.tls?.subject,
            web.tls?.issuer,
            web.tls?.subject_common_name,
            web.tls?.issuer_common_name,
            web.tls?.hostname_reference,
            web.tls?.hostname_mismatch_reason,
            web.tls?.protocol,
            web.tls?.cipher,
            ...(web.tls?.subject_alt_names ?? []),
            ...(web.tls?.dns_names ?? []),
            ...(web.tls?.ip_addresses ?? []),
          ]
            .join(" ")
            .toLowerCase();
          const matchesQuery =
            normalizedQuery.length === 0 || searchText.includes(normalizedQuery);

          if (!matchesService || !matchesPort || !matchesQuery) {
            return [];
          }

          return [{ host, port, service, web }];
        }),
      )
      .sort((left, right) => {
        if (left.host.address !== right.host.address) {
          return left.host.address.localeCompare(right.host.address);
        }
        return left.port.port_number - right.port.port_number;
      });
  }, [hosts, portFilter, query, serviceFilter]);

  function handleExportHosts() {
    const rows =
      viewMode === "inventory"
        ? filteredInventory.map(({ host, visiblePorts }) => ({
            assessment: assessmentsById.get(host.assessment_id)?.project_name ?? "",
            address: host.address,
            hostname: host.hostname,
            status: host.status,
            device_type: host.device_type,
            vendor_name: host.vendor_name,
            mac_address: host.mac_address,
            discovery_method: host.discovery_method,
            visible_open_ports: visiblePorts.length,
            total_open_ports: host.ports.length,
            last_seen_at: host.last_seen_at,
            notes: host.notes,
          }))
        : Array.from(
            new Map(
              filteredWebServices.map(({ host }) => [
                host.id,
                {
                  assessment:
                    assessmentsById.get(host.assessment_id)?.project_name ?? "",
                  address: host.address,
                  hostname: host.hostname,
                  status: host.status,
                  device_type: host.device_type,
                  vendor_name: host.vendor_name,
                  mac_address: host.mac_address,
                  discovery_method: host.discovery_method,
                  total_open_ports: host.ports.length,
                  last_seen_at: host.last_seen_at,
                  notes: host.notes,
                },
              ]),
            ).values(),
          );
    const didDownload = downloadCsv(buildCsvFilename("hosts"), rows);
    setStatusMessage(
      didDownload
        ? "Hosts CSV downloaded from the current filtered view."
        : "No hosts matched the current filters, so no CSV was created.",
    );
  }

  function handleExportPorts() {
    const rows =
      viewMode === "inventory"
        ? filteredInventory.flatMap(({ host, visiblePorts }) =>
            visiblePorts.map((port) => {
              const service = serviceForPort(host, port);
              return {
                assessment:
                  assessmentsById.get(host.assessment_id)?.project_name ?? "",
                host: host.address,
                hostname: host.hostname,
                port_number: port.port_number,
                protocol: port.protocol,
                state: port.state,
                service_name: service?.name ?? "unknown",
                product: service?.product ?? "Unknown",
                version: service?.version ?? "Unavailable",
                confidence: service
                  ? `${Math.round(service.confidence * 100)}%`
                  : "0%",
                banner: service?.banner ?? "",
              };
            }),
          )
        : filteredWebServices.map(({ host, port, service, web }) => ({
            assessment:
              assessmentsById.get(host.assessment_id)?.project_name ?? "",
            host: host.address,
            hostname: host.hostname,
            port_number: port.port_number,
            protocol: port.protocol,
            state: port.state,
            service_name: service.name,
            product: service.product,
            version: service.version,
            url: web.url,
            status_code: web.status_code ?? "",
            https_in_use: web.https_in_use,
            page_title: web.page_title,
          }));
    const didDownload = downloadCsv(buildCsvFilename("ports"), rows);
    setStatusMessage(
      didDownload
        ? "Ports and services CSV downloaded from the current filtered view."
        : "No ports or services matched the current filters, so no CSV was created.",
    );
  }

  return (
    <div className="page">
      <PageHeader
        title="Assets"
        subtitle={
          selectedAssessment
            ? `Safe discovery, service enumeration, and web evidence for ${selectedAssessment.project_name}.`
            : "Safe discovery, service enumeration, and web evidence land here with host identity, TCP inventory, and browser-facing checks."
        }
        actions={
          <div className="table-toolbar">
            <button
              className="button button--ghost"
              disabled={
                isLoading ||
                (viewMode === "inventory"
                  ? filteredInventory.length === 0
                  : filteredWebServices.length === 0)
              }
              onClick={handleExportHosts}
              type="button"
            >
              Export Hosts CSV
            </button>
            <button
              className="button button--ghost"
              disabled={
                isLoading ||
                (viewMode === "inventory"
                  ? filteredInventory.every(
                      (entry) => entry.visiblePorts.length === 0,
                    )
                  : filteredWebServices.length === 0)
              }
              onClick={handleExportPorts}
              type="button"
            >
              Export Ports CSV
            </button>
          </div>
        }
      />
      {isLoading ? (
        <LoadingState
          title="Loading Assets"
          message="Refreshing the authorized host inventory, service records, and saved web evidence."
        />
      ) : null}
      {error ? <div className="error-banner">{error}</div> : null}
      {statusMessage ? <div className="success-banner">{statusMessage}</div> : null}

      <Panel
        title="Assessment Context"
        subtitle="Filter the asset inventory to one saved assessment or keep a combined cross-assessment view."
      >
        <div className="asset-filters">
          <label className="field-stack">
            <span>Assessment</span>
            <select
              className="input"
              value={selectedAssessmentId ?? ""}
              onChange={(event) => {
                const nextValue = event.target.value;
                const nextParams = new URLSearchParams(searchParams);
                if (nextValue) {
                  nextParams.set("assessmentId", nextValue);
                } else {
                  nextParams.delete("assessmentId");
                }
                setSearchParams(nextParams);
              }}
            >
              <option value="">All assessments</option>
              {assessments.map((assessment) => (
                <option key={assessment.id} value={assessment.id}>
                  {assessment.project_name} | {assessment.client_name}
                </option>
              ))}
            </select>
          </label>
          <div className="summary-card">
            <span className="summary-card__label">Visible Hosts</span>
            <strong>{hosts.length}</strong>
            <div className="summary-list-inline">
              {selectedAssessment
                ? `${selectedAssessment.project_name} selected`
                : "Combined assessment view"}
            </div>
          </div>
        </div>
      </Panel>

      <Panel
        title="Views"
        subtitle="Switch between the general host inventory and safe web-service evidence collected from discovered HTTP and HTTPS listeners."
      >
        <div className="segmented-control">
          <button
            className={`button ${viewMode === "inventory" ? "button--segmented-active" : "button--ghost"}`}
            onClick={() => setViewMode("inventory")}
            type="button"
          >
            Host Inventory
          </button>
          <button
            className={`button ${viewMode === "web" ? "button--segmented-active" : "button--ghost"}`}
            onClick={() => setViewMode("web")}
            type="button"
          >
            Web Services
          </button>
        </div>
      </Panel>

      <Panel
        title={viewMode === "inventory" ? "Asset Filters" : "Web Service Filters"}
        subtitle={
          viewMode === "inventory"
            ? "Filter the inventory by host text, service name, or exact TCP port."
            : "Filter safe web-check evidence by host text, service name, or exact TCP port."
        }
      >
        <div className="asset-filters">
          <input
            className="input"
            placeholder={
              viewMode === "inventory"
                ? "Search hosts, vendors, services, or notes"
                : "Search hosts, titles, headers, cookies, or HTTPS warnings"
            }
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <select
            className="input"
            value={serviceFilter}
            onChange={(event) => setServiceFilter(event.target.value)}
          >
            <option value="all">All services</option>
            {serviceOptions.map((serviceName) => (
              <option key={serviceName} value={serviceName}>
                {serviceName}
              </option>
            ))}
          </select>
          <input
            className="input"
            inputMode="numeric"
            placeholder="Filter by exact TCP port"
            value={portFilter}
            onChange={(event) => setPortFilter(event.target.value)}
          />
        </div>
      </Panel>

      {viewMode === "inventory" ? (
        <div className="asset-stack">
          {filteredInventory.length > 0 ? (
            filteredInventory.map(({ host, visiblePorts }) => (
              <Panel
                key={`${host.scope_id ?? "scope"}-${host.address}`}
                title={`${host.address}${host.hostname ? ` | ${host.hostname}` : ""}`}
                subtitle={`${host.device_type || "Unknown"} | ${host.vendor_name || "Unknown vendor"}`}
              >
                <div className="asset-meta-grid">
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">Status</span>
                    <StatusPill label={host.status} />
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">Discovery Method</span>
                    <strong>{host.discovery_method || "Unavailable"}</strong>
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">MAC Address</span>
                    <strong>{host.mac_address || "Unavailable"}</strong>
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">Vendor</span>
                    <strong>{host.vendor_name || "Unknown"}</strong>
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">Host Notes</span>
                    <strong>{host.notes || "No notes"}</strong>
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">Last Observed</span>
                    <strong>{formatDate(host.last_seen_at)}</strong>
                  </div>
                </div>

                <div className="asset-service-table">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Port</th>
                        <th>Protocol</th>
                        <th>State</th>
                        <th>Service</th>
                        <th>Product</th>
                        <th>Version</th>
                        <th>Confidence</th>
                        <th>Banner</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visiblePorts.length > 0 ? (
                        visiblePorts.map((port) => {
                          const service = serviceForPort(host, port);
                          return (
                            <tr key={port.id}>
                              <td>{port.port_number}</td>
                              <td>{port.protocol}</td>
                              <td>{port.state}</td>
                              <td>{service?.name || "unknown"}</td>
                              <td>{service?.product || "Unknown"}</td>
                              <td>{service?.version || "Unavailable"}</td>
                              <td>
                                {service
                                  ? `${Math.round(service.confidence * 100)}%`
                                  : "0%"}
                              </td>
                              <td className="table-cell-wrap">
                                {service?.banner || "No safe banner captured"}
                              </td>
                            </tr>
                          );
                        })
                      ) : (
                        <tr>
                          <td className="table-empty" colSpan={8}>
                            {isLoading
                              ? "Loading TCP service data..."
                              : host.ports.length > 0
                              ? "No TCP services for this host matched the current filters."
                              : "No open TCP ports recorded for this host yet."}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </Panel>
            ))
          ) : (
            <Panel
              title="Asset Inventory"
              subtitle="No hosts matched the current filters."
            >
              <div className="empty-state">
                {isLoading
                  ? "Loading host inventory..."
                  : "Run safe discovery first, then safe port enumeration to populate host service tables."}
              </div>
            </Panel>
          )}
        </div>
      ) : (
        <div className="web-service-stack">
          {filteredWebServices.length > 0 ? (
            filteredWebServices.map(({ host, port, service, web }) => (
              <Panel
                key={`${host.id}-${service.id}`}
                title={`${host.address}${host.hostname ? ` | ${host.hostname}` : ""} | ${service.name.toUpperCase()} ${port.port_number}`}
                subtitle={web.url || `${web.scheme}://${host.address}:${port.port_number}/`}
              >
                <div className="asset-meta-grid">
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">HTTP Status</span>
                    <strong>
                      {web.status_code !== null
                        ? `${web.status_code}${web.reason_phrase ? ` ${web.reason_phrase}` : ""}`
                        : "Unavailable"}
                    </strong>
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">Page Title</span>
                    <strong>{web.page_title || "Unavailable"}</strong>
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">Server Header</span>
                    <strong>{web.server_header || "Unavailable"}</strong>
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">Technology Hints</span>
                    <strong>
                      {web.technology_hints.join(", ") || "No header-based hints"}
                    </strong>
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">Content Signals</span>
                    <strong>
                      {[
                        boolLabel(web.login_page_detected, "Login page", "No login"),
                        boolLabel(
                          web.directory_listing_detected,
                          "Directory listing",
                          "No directory listing",
                        ),
                        boolLabel(
                          web.default_page_detected,
                          "Default page",
                          "No default page",
                        ),
                      ].join(" | ")}
                    </strong>
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">HTTPS</span>
                    <strong>
                      {web.https_in_use ? "HTTPS in use" : "HTTP only"}
                      {web.redirect_location
                        ? ` | Redirect: ${web.redirect_location}`
                        : ""}
                    </strong>
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">robots.txt</span>
                    <strong>
                      {boolLabel(
                        web.robots_txt_detected,
                        "Detected",
                        "Not detected",
                      )}
                    </strong>
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">sitemap.xml</span>
                    <strong>
                      {boolLabel(
                        web.sitemap_xml_detected,
                        "Detected",
                        "Not detected",
                      )}
                    </strong>
                  </div>
                  <div className="asset-meta-card">
                    <span className="asset-meta-card__label">Risky Cookies</span>
                    <strong>
                      {web.risky_cookies.length > 0
                        ? `${web.risky_cookies.length} flagged`
                        : "No risky cookies observed"}
                    </strong>
                  </div>
                  {web.tls ? (
                    <div className="asset-meta-card">
                      <span className="asset-meta-card__label">TLS Certificate</span>
                      <strong>{tlsStatusSummary(web.tls)}</strong>
                    </div>
                  ) : null}
                </div>

                <div className="web-evidence-grid">
                  <div className="summary-card">
                    <span className="summary-card__label">Detected Headers</span>
                    {Object.keys(web.headers).length > 0 ? (
                      <ul className="web-list">
                        {Object.entries(web.headers)
                          .sort((left, right) => left[0].localeCompare(right[0]))
                          .map(([key, value]) => (
                            <li key={key}>
                              <strong>{key}:</strong> {value}
                            </li>
                          ))}
                      </ul>
                    ) : (
                      <div className="empty-state">
                        No response headers were captured for this service.
                      </div>
                    )}
                  </div>

                  <div className="summary-card">
                    <span className="summary-card__label">Missing Headers</span>
                    {web.missing_security_headers.length > 0 ? (
                      <ul className="web-list">
                        {web.missing_security_headers.map((header) => (
                          <li key={header}>{header}</li>
                        ))}
                      </ul>
                    ) : (
                      <div className="empty-state">
                        No missing tracked security headers were observed.
                      </div>
                    )}
                  </div>

                  <div className="summary-card">
                    <span className="summary-card__label">Risky Cookies</span>
                    {web.risky_cookies.length > 0 ? (
                      <ul className="web-list">
                        {web.risky_cookies.map((cookie) => (
                          <li key={`${cookie.name}-${cookie.missing_flags.join("-")}`}>
                            <strong>{cookie.name}</strong>:{" "}
                            {cookie.missing_flags.join(", ")}
                            {cookie.same_site
                              ? ` | SameSite=${cookie.same_site}`
                              : ""}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="empty-state">
                        No risky cookie flags were observed.
                      </div>
                    )}
                  </div>

                  <div className="summary-card">
                    <span className="summary-card__label">HTTPS Warnings</span>
                    {web.https_warnings.length > 0 ? (
                      <ul className="web-list">
                        {web.https_warnings.map((warning) => (
                          <li key={warning}>{warning}</li>
                        ))}
                      </ul>
                    ) : (
                      <div className="empty-state">
                        No HTTPS warnings were recorded.
                      </div>
                    )}
                  </div>

                  {web.tls ? (
                    <div className="summary-card">
                      <span className="summary-card__label">TLS Certificate</span>
                      <dl className="detail-list">
                        <div>
                          <dt>Subject</dt>
                          <dd>
                            {web.tls.subject_common_name || web.tls.subject || "Unavailable"}
                          </dd>
                        </div>
                        <div>
                          <dt>Issuer</dt>
                          <dd>
                            {web.tls.issuer_common_name || web.tls.issuer || "Unavailable"}
                          </dd>
                        </div>
                        <div>
                          <dt>Valid From</dt>
                          <dd>{formatOptionalDate(web.tls.valid_from)}</dd>
                        </div>
                        <div>
                          <dt>Valid Until</dt>
                          <dd>{formatOptionalDate(web.tls.valid_until)}</dd>
                        </div>
                        <div>
                          <dt>Status</dt>
                          <dd>{tlsStatusSummary(web.tls)}</dd>
                        </div>
                        <div>
                          <dt>Protocol / Cipher</dt>
                          <dd>
                            {web.tls.protocol || web.tls.cipher
                              ? [
                                  web.tls.protocol,
                                  web.tls.cipher,
                                  web.tls.cipher_bits !== null
                                    ? `${web.tls.cipher_bits}-bit`
                                    : "",
                                ]
                                  .filter(Boolean)
                                  .join(" | ")
                              : "Unavailable"}
                          </dd>
                        </div>
                      </dl>
                    </div>
                  ) : null}

                  {web.tls ? (
                    <div className="summary-card">
                      <span className="summary-card__label">TLS Name Coverage</span>
                      <dl className="detail-list">
                        <div>
                          <dt>Reference</dt>
                          <dd>{web.tls.hostname_reference || host.address}</dd>
                        </div>
                        <div>
                          <dt>Hostname Match</dt>
                          <dd>
                            {web.tls.hostname_mismatch_detectable
                              ? web.tls.hostname_mismatch
                                ? "Mismatch observed"
                                : "Match observed"
                              : "Not safely detectable from this scan path"}
                          </dd>
                        </div>
                        <div>
                          <dt>DNS SANs</dt>
                          <dd>{web.tls.dns_names.join(", ") || "None observed"}</dd>
                        </div>
                        <div>
                          <dt>IP SANs</dt>
                          <dd>{web.tls.ip_addresses.join(", ") || "None observed"}</dd>
                        </div>
                        <div>
                          <dt>All SAN Entries</dt>
                          <dd>
                            {web.tls.subject_alt_names.join(", ") || "No subject alternative names observed"}
                          </dd>
                        </div>
                        {web.tls.hostname_mismatch_reason ? (
                          <div>
                            <dt>Validation Detail</dt>
                            <dd>{web.tls.hostname_mismatch_reason}</dd>
                          </div>
                        ) : null}
                      </dl>
                    </div>
                  ) : null}
                </div>
              </Panel>
            ))
          ) : (
            <Panel
              title="Web Services"
              subtitle="No discovered HTTP or HTTPS services matched the current filters."
            >
              <div className="empty-state">
                {isLoading
                  ? "Loading web-service evidence..."
                  : "Run safe port enumeration against discovered hosts to collect safe web evidence for internal services."}
              </div>
            </Panel>
          )}
        </div>
      )}
    </div>
  );
}
