import type { Setting } from "../types";

type ScopePreview = {
  includedTargets: string[];
  excludedTargets: string[];
  publicTargets: string[];
  fieldErrors: Record<string, string[]>;
};

const PRIVATE_IPV4_RANGES = [
  { start: "10.0.0.0", end: "10.255.255.255" },
  { start: "172.16.0.0", end: "172.31.255.255" },
  { start: "192.168.0.0", end: "192.168.255.255" },
  { start: "127.0.0.0", end: "127.255.255.255" },
];

export function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

export function parseTargets(value: string): string[] {
  return value
    .split(/[\n,]+/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function dedupeEntries(values: string[]): string[] {
  const deduped: string[] = [];
  const seen = new Set<string>();

  values.forEach((value) => {
    const key = value.toLowerCase();
    if (!seen.has(key)) {
      deduped.push(value);
      seen.add(key);
    }
  });

  return deduped;
}

function parseIpv4(value: string): number[] | null {
  const parts = value.split(".");
  if (parts.length !== 4) {
    return null;
  }

  const octets = parts.map((part) => Number(part));
  if (octets.some((octet, index) => !Number.isInteger(octet) || octet < 0 || octet > 255 || (parts[index] !== String(octet) && !(octet === 0 && /^0+$/.test(parts[index]))))) {
    return null;
  }

  return octets;
}

function ipv4ToNumber(octets: number[]): number {
  return octets.reduce(
    (accumulator, octet) => accumulator * 256 + octet,
    0,
  );
}

function parseIpv4Number(value: string): number | null {
  const parsed = parseIpv4(value);
  if (!parsed) {
    return null;
  }
  return ipv4ToNumber(parsed);
}

function parseCidr(value: string): { ip: string; prefix: number } | null {
  const [ip, prefixString] = value.split("/");
  if (!ip || prefixString === undefined || prefixString === "") {
    return null;
  }

  const prefix = Number(prefixString);
  if (!Number.isInteger(prefix) || prefix < 0 || prefix > 32) {
    return null;
  }

  if (!parseIpv4(ip.trim())) {
    return null;
  }

  return { ip: ip.trim(), prefix };
}

function getCidrBounds(value: string): { start: number; end: number } | null {
  const parsed = parseCidr(value);
  if (!parsed) {
    return null;
  }

  const ipNumber = parseIpv4Number(parsed.ip);
  if (ipNumber === null) {
    return null;
  }

  const hostBits = 32 - parsed.prefix;
  const blockSize = 2 ** hostBits;
  const start = Math.floor(ipNumber / blockSize) * blockSize;
  const end = start + blockSize - 1;

  return { start, end };
}

export function isValidIpv4Address(value: string): boolean {
  return parseIpv4(value.trim()) !== null;
}

export function isValidIpv4Cidr(value: string): boolean {
  return getCidrBounds(value.trim()) !== null;
}

export function isPrivateOrLocalIpv4Address(value: string): boolean {
  const ipNumber = parseIpv4Number(value.trim());
  if (ipNumber === null) {
    return false;
  }

  return PRIVATE_IPV4_RANGES.some((range) => {
    const start = parseIpv4Number(range.start);
    const end = parseIpv4Number(range.end);
    return start !== null && end !== null && ipNumber >= start && ipNumber <= end;
  });
}

export function isPrivateOrLocalIpv4Cidr(value: string): boolean {
  const bounds = getCidrBounds(value.trim());
  if (!bounds) {
    return false;
  }

  return PRIVATE_IPV4_RANGES.some((range) => {
    const start = parseIpv4Number(range.start);
    const end = parseIpv4Number(range.end);
    return (
      start !== null &&
      end !== null &&
      bounds.start >= start &&
      bounds.end <= end
    );
  });
}

export function buildScopePreview(
  rawNetworkRanges: string[],
  rawIndividualIps: string[],
  rawExcludedIps: string[],
): ScopePreview {
  const networkRanges = dedupeEntries(rawNetworkRanges);
  const individualIps = dedupeEntries(rawIndividualIps);
  const excludedIps = dedupeEntries(rawExcludedIps);
  const fieldErrors: Record<string, string[]> = {};
  const publicTargets: string[] = [];

  networkRanges.forEach((entry) => {
    if (!isValidIpv4Cidr(entry)) {
      fieldErrors.network_ranges = [
        ...(fieldErrors.network_ranges ?? []),
        `Invalid CIDR range: ${entry}`,
      ];
      return;
    }

    if (!isPrivateOrLocalIpv4Cidr(entry)) {
      publicTargets.push(entry);
    }
  });

  individualIps.forEach((entry) => {
    if (!isValidIpv4Address(entry)) {
      fieldErrors.individual_ips = [
        ...(fieldErrors.individual_ips ?? []),
        `Invalid IP address: ${entry}`,
      ];
      return;
    }

    if (!isPrivateOrLocalIpv4Address(entry)) {
      publicTargets.push(entry);
    }
  });

  excludedIps.forEach((entry) => {
    if (!isValidIpv4Address(entry)) {
      fieldErrors.excluded_ips = [
        ...(fieldErrors.excluded_ips ?? []),
        `Invalid excluded IP address: ${entry}`,
      ];
      return;
    }

    const ipNumber = parseIpv4Number(entry);
    const isInNetwork = networkRanges.some((networkRange) => {
      const bounds = getCidrBounds(networkRange);
      return bounds !== null && ipNumber !== null && ipNumber >= bounds.start && ipNumber <= bounds.end;
    });
    const isInIndividualList = individualIps.some(
      (individualIp) => individualIp.toLowerCase() === entry.toLowerCase(),
    );

    if (!isInNetwork && !isInIndividualList) {
      fieldErrors.excluded_ips = [
        ...(fieldErrors.excluded_ips ?? []),
        `Excluded IP must fall inside an included range or match an included IP: ${entry}`,
      ];
    }
  });

  return {
    includedTargets: [
      ...networkRanges,
      ...individualIps.filter(
        (ip) => !excludedIps.some((excluded) => excluded.toLowerCase() === ip.toLowerCase()),
      ),
    ],
    excludedTargets: excludedIps,
    publicTargets: dedupeEntries(publicTargets),
    fieldErrors,
  };
}

export function getSettingValue(
  settings: Setting[],
  key: string,
  fallback = "",
): string {
  return settings.find((setting) => setting.key === key)?.value ?? fallback;
}
