export type AppRoute =
  | { name: "home" }
  | { name: "admin" }
  | { name: "setup" }
  | { name: "settings" }
  | { name: "credits" }
  | { name: "lobby"; matchId: string }
  | { name: "game"; matchId: string }
  | { name: "card"; payload: string };

const APP_SLUGS = ["minu", "menu"] as const;

function normalizeAppParts(parts: string[]): string[] {
  const prefix = parts[0]?.toLowerCase();
  if (prefix && APP_SLUGS.includes(prefix as (typeof APP_SLUGS)[number])) {
    return parts.slice(1);
  }

  return parts;
}

function parseParts(parts: string[]): AppRoute | null {
  const appParts = normalizeAppParts(parts);
  if (!appParts.length) {
    return null;
  }

  if (appParts[0] === "start") {
    return { name: "setup" };
  }
  if (appParts[0] === "admin") {
    return { name: "admin" };
  }
  if (appParts[0] === "settings") {
    return { name: "settings" };
  }
  if (appParts[0] === "credits") {
    return { name: "credits" };
  }
  if (appParts[0] === "lobby" && appParts[1]) {
    return { name: "lobby", matchId: appParts[1] };
  }
  if (appParts[0] === "game" && appParts[1]) {
    return { name: "game", matchId: appParts[1] };
  }
  if (appParts[0] === "card" && appParts[1]) {
    return { name: "card", payload: appParts.slice(1).join("/") };
  }

  return null;
}

function parseHash(hash: string): AppRoute | null {
  const normalized = hash.replace(/^#/, "").replace(/^\/+/, "");
  if (!normalized) {
    return null;
  }

  return parseParts(normalized.split("/").filter(Boolean));
}

function parsePathname(pathname: string): AppRoute | null {
  const normalized = pathname.replace(/^\/+|\/+$/g, "");
  if (!normalized) {
    return null;
  }

  return parseParts(normalized.split("/").filter(Boolean));
}

export function parseRoute(input: string | Location): AppRoute {
  if (typeof input === "string") {
    return parseHash(input) ?? { name: "home" };
  }

  return parsePathname(input.pathname) ?? parseHash(input.hash) ?? { name: "home" };
}

export function routeToHash(route: AppRoute): string {
  switch (route.name) {
    case "home":
      return "#/";
    case "setup":
      return "#/start";
    case "admin":
      return "#/admin";
    case "settings":
      return "#/settings";
    case "credits":
      return "#/credits";
    case "lobby":
      return `#/lobby/${route.matchId}`;
    case "game":
      return `#/game/${route.matchId}`;
    case "card":
      return `#/card/${route.payload}`;
  }
}

export function normalizePublicMinuUrl(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, "");
  if (!trimmed) {
    return "";
  }

  return trimmed.replace(/\/(menu|minu)$/i, "");
}
