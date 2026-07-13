import type {
  AdminAssistantCompetitionMutationRead,
  AdminAssistantCompetitionWritePayload,
  AdminAssistantCompetitionsRead,
  AdminAssistantDeleteRead,
  AdminAssistantQuestionMutationRead,
  AdminAssistantQuestionWritePayload,
  AdminAssistantQuestionsRead,
  AdminCatalogRefresh,
  AdminDeleteRead,
  AdminOverview,
  AdminPlayerMutationRead,
  AdminPlayerWritePayload,
  AdminPlayersPage,
  AuthLoginPayload,
  AuthSessionRead,
  AwardRoundPayload,
  GameOverview,
  MatchCreatePayload,
  MatchRead,
  PlayerCardTokenRead,
  PlayerSecret,
  PublicCardAssistantAnswer,
  PublicCardAssistantQuestionPayload,
  ShareLinkRead,
  SharedPlayerCardPayload,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";
const MOJIBAKE_PATTERN = /[ÃÂâØÙ]/u;

function repairLikelyMojibake(value: string): string {
  if (!MOJIBAKE_PATTERN.test(value)) {
    return value;
  }

  try {
    const bytes = Uint8Array.from(Array.from(value).map((character) => character.charCodeAt(0) & 0xff));
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return value;
  }
}

export function getQrCodeUrl(value: string): string {
  return `${API_BASE_URL}/game/qr?value=${encodeURIComponent(value)}`;
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;

  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      credentials: "same-origin",
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError(
      "شغّل اللعبة وحاول مره ثانية.",
    );
  }

  if (!response.ok) {
    const rawText = await response.text();
    let detail = "";

    try {
      const payload = JSON.parse(rawText) as { detail?: string };
      detail = repairLikelyMojibake(payload.detail ?? "");
    } catch {
      detail = repairLikelyMojibake(rawText);
    }

    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent("minu:unauthorized"));
    }

    throw new ApiError(detail || "ما ضبط", response.status);
  }

  return (await response.json()) as T;
}

function withMatchHeaders(matchToken?: string): HeadersInit | undefined {
  if (!matchToken?.trim()) {
    return undefined;
  }

  return {
    "x-minu-match": matchToken.trim(),
  };
}

export const api = {
  getSession: () => request<AuthSessionRead>("/auth/session"),
  login: (payload: AuthLoginPayload) =>
    request<AuthSessionRead>("/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  logout: () =>
    request<AuthSessionRead>("/auth/logout", {
      method: "POST",
    }),
  getAdminOverview: () => request<AdminOverview>("/admin/overview"),
  getAdminPlayers: (params?: {
    q?: string;
    difficulty?: number | "all";
    active?: "all" | "active" | "retired";
    offset?: number;
    limit?: number;
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.q?.trim()) {
      searchParams.set("q", params.q.trim());
    }
    if (typeof params?.difficulty === "number") {
      searchParams.set("difficulty", String(params.difficulty));
    }
    if (params?.active === "active") {
      searchParams.set("active", "true");
    }
    if (params?.active === "retired") {
      searchParams.set("active", "false");
    }
    if (typeof params?.offset === "number") {
      searchParams.set("offset", String(params.offset));
    }
    if (typeof params?.limit === "number") {
      searchParams.set("limit", String(params.limit));
    }
    const query = searchParams.toString();
    return request<AdminPlayersPage>(`/admin/players${query ? `?${query}` : ""}`);
  },
  createAdminPlayer: (payload: AdminPlayerWritePayload) =>
    request<AdminPlayerMutationRead>("/admin/players", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateAdminPlayer: (playerId: number, payload: AdminPlayerWritePayload) =>
    request<AdminPlayerMutationRead>(`/admin/players/${playerId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteAdminPlayer: (playerId: number) =>
    request<AdminDeleteRead>(`/admin/players/${playerId}`, {
      method: "DELETE",
    }),
  getAdminAssistantQuestions: () =>
    request<AdminAssistantQuestionsRead>("/admin/assistant/questions"),
  createAdminAssistantQuestion: (payload: AdminAssistantQuestionWritePayload) =>
    request<AdminAssistantQuestionMutationRead>("/admin/assistant/questions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateAdminAssistantQuestion: (questionId: number, payload: AdminAssistantQuestionWritePayload) =>
    request<AdminAssistantQuestionMutationRead>(`/admin/assistant/questions/${questionId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteAdminAssistantQuestion: (questionId: number) =>
    request<AdminAssistantDeleteRead>(`/admin/assistant/questions/${questionId}`, {
      method: "DELETE",
    }),
  getAdminAssistantCompetitions: () =>
    request<AdminAssistantCompetitionsRead>("/admin/assistant/competitions"),
  createAdminAssistantCompetition: (payload: AdminAssistantCompetitionWritePayload) =>
    request<AdminAssistantCompetitionMutationRead>("/admin/assistant/competitions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateAdminAssistantCompetition: (competitionId: number, payload: AdminAssistantCompetitionWritePayload) =>
    request<AdminAssistantCompetitionMutationRead>(`/admin/assistant/competitions/${competitionId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteAdminAssistantCompetition: (competitionId: number) =>
    request<AdminAssistantDeleteRead>(`/admin/assistant/competitions/${competitionId}`, {
      method: "DELETE",
    }),
  refreshAdminCatalog: () =>
    request<AdminCatalogRefresh>("/admin/catalog/refresh", {
      method: "POST",
    }),
  getOverview: () => request<GameOverview>("/game/overview"),
  getShareLink: () => request<ShareLinkRead>("/game/share-link"),
  createMatch: (payload: MatchCreatePayload) =>
    request<MatchRead>("/game/matches", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getMatch: (matchId: string, matchToken?: string) =>
    request<MatchRead>(`/game/matches/${matchId}`, {
      headers: withMatchHeaders(matchToken),
    }),
  awardRound: (matchId: string, payload: AwardRoundPayload, matchToken?: string) =>
    request<MatchRead>(`/game/matches/${matchId}/award`, {
      method: "POST",
      headers: withMatchHeaders(matchToken),
      body: JSON.stringify(payload),
    }),
  noAnswerRound: (matchId: string, matchToken?: string) =>
    request<MatchRead>(`/game/matches/${matchId}/no-answer`, {
      method: "POST",
      headers: withMatchHeaders(matchToken),
    }),
  nextRound: (matchId: string, matchToken?: string) =>
    request<MatchRead>(`/game/matches/${matchId}/next-round`, {
      method: "POST",
      headers: withMatchHeaders(matchToken),
    }),
  endMatch: (matchId: string, matchToken?: string) =>
    request<MatchRead>(`/game/matches/${matchId}/end`, {
      method: "POST",
      headers: withMatchHeaders(matchToken),
    }),
  getPlayerSecret: (matchId: string, seat: number, matchToken?: string) =>
    request<PlayerSecret>(`/game/matches/${matchId}/players/${seat}`, {
      headers: withMatchHeaders(matchToken),
    }),
  getPlayerCardToken: (matchId: string, seat: number, matchToken?: string) =>
    request<PlayerCardTokenRead>(`/game/matches/${matchId}/players/${seat}/share-token`, {
      headers: withMatchHeaders(matchToken),
    }),
  getPublicPlayerCard: (token: string) =>
    request<SharedPlayerCardPayload>(`/game/card/${token}`),
  askPublicPlayerCardAssistant: (token: string, payload: PublicCardAssistantQuestionPayload) =>
    request<PublicCardAssistantAnswer>(`/game/card/${token}/assistant`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
