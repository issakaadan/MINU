import { useEffect, useRef, useState, type FormEvent } from "react";

import { ApiError, api, getQrCodeUrl } from "./game/api";
import {
  ANSWER_RULE_OPTIONS,
  buildRandomTwistSelection,
  buildRuleChips,
  getModeDefinition,
  MATCH_MODES,
  positionLabelFromGroup,
} from "./game/constants";
import { normalizePublicMinuUrl, parseRoute, routeToHash, type AppRoute } from "./game/router";
import { fetchArabicWikipediaBundle, fetchEnglishWikipediaBundle, fetchWikidataClubSequence } from "./game/share";
import type {
  AdminAssistantCompetition,
  AdminAssistantCompetitionWritePayload,
  AdminAssistantQuestion,
  AdminAssistantQuestionWritePayload,
  AdminCatalogRefresh,
  AdminOverview,
  AdminPlayer,
  AdminPlayerWritePayload,
  AdminPlayersPage,
  AnswerRuleKey,
  AuthSessionRead,
  CardLanguage,
  GameOverview,
  MatchModeKey,
  MatchRead,
  MatchSeat,
  QuestionCategoryKey,
  SharedPlayerCardPayload,
  WikipediaPlayerDetails,
  WikipediaSummary,
} from "./game/types";
import GAME_LOGO_DATA_URL from "./logoData";

const SHARE_URL_KEY = "who-is-the-player.public-base";
const MATCH_TOKEN_KEY_PREFIX = "who-is-the-player.match-token:";
const MATCH_SETUP_KEY_PREFIX = "who-is-the-player.match-setup:";
const CANONICAL_PUBLIC_SHARE_URL = "https://minu-theta.vercel.app";
const CANONICAL_PUBLIC_SHARE_HOST = new URL(CANONICAL_PUBLIC_SHARE_URL).host.toLowerCase();

type ChallengeTypeKey = "head-to-head" | "teams" | "one-explains";
type TeamDistributionMode = "shuffle" | "manual";
type TeamAssignment = 1 | 2 | null;

type TeamParticipantDraft = {
  id: string;
  name: string;
  team: TeamAssignment;
};

type MatchSetupSeatMeta = {
  title: string;
  roleLabel: string;
  members: string[];
};

type MatchSetupMeta = {
  challengeType: ChallengeTypeKey;
  challengeLabel: string;
  boardPrompt: string;
  startPrompt: string;
  participantCount: number;
  teamDistributionMode: TeamDistributionMode | null;
  seatMeta: [MatchSetupSeatMeta, MatchSetupSeatMeta];
};

type ChallengeTypeDefinition = {
  key: ChallengeTypeKey;
  label: string;
  description: string;
  summary: string;
  seatPlaceholders: [string, string];
  seatRoleLabels: [string, string];
  boardPrompt: string;
  startPrompt: string;
  defaultModeKey: MatchModeKey;
  participantStyle: "pair" | "teams";
};

const CHALLENGE_TYPES: ChallengeTypeDefinition[] = [
  {
    key: "head-to-head",
    label: "راس براس",
    description: "شخص ضد شخص",
    summary: "اسمين وخلاص",
    seatPlaceholders: ["اللاعب 1", "اللاعب 2"],
    seatRoleLabels: ["فردي", "فردي"],
    boardPrompt: "مين جاوب أول؟",
    startPrompt: "اللي يبدأ",
    defaultModeKey: "race-to-100",
    participantStyle: "pair",
  },
  {
    key: "teams",
    label: "تيم ضد تيم",
    description: "وزّعوا الأسماء على فريقين",
    summary: "يدوي أو عشوائي",
    seatPlaceholders: ["الفريق 1", "الفريق 2"],
    seatRoleLabels: ["الفريق الأول", "الفريق الثاني"],
    boardPrompt: "أي فريق جاوب أول؟",
    startPrompt: "الفريق اللي يبدأ",
    defaultModeKey: "best-of-five",
    participantStyle: "teams",
  },
  {
    key: "one-explains",
    label: "واحد يشرح",
    description: "شخص واحد من كل طرف يرد",
    summary: "شارح ضد شارح",
    seatPlaceholders: ["الشارح 1", "الشارح 2"],
    seatRoleLabels: ["الشارح الأول", "الشارح الثاني"],
    boardPrompt: "أي شارح جاوب أول؟",
    startPrompt: "الشارح اللي يبدأ",
    defaultModeKey: "hot-streak",
    participantStyle: "pair",
  },
];

function defaultShareUrl(): string {
  return CANONICAL_PUBLIC_SHARE_URL;
}

function isTransientShareUrl(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) {
    return false;
  }

  try {
    const host = new URL(trimmed).host.toLowerCase();
    if (/trycloudflare\.com|lhr\.life|localhost\.run/i.test(host)) {
      return true;
    }
    if (host.endsWith(".vercel.app") && host !== CANONICAL_PUBLIC_SHARE_HOST) {
      return true;
    }
    return false;
  } catch {
    return /trycloudflare\.com|lhr\.life|localhost\.run/i.test(trimmed);
  }
}

function isPublicShareUrl(value: string): boolean {
  return Boolean(value.trim()) && !/localhost|127\.0\.0\.1/i.test(value) && !isTransientShareUrl(value);
}

function navigate(route: AppRoute) {
  window.location.hash = routeToHash(route);
}

function isActiveMatchRoute(route: AppRoute, matchId: string): boolean {
  return (route.name === "lobby" || route.name === "game") && route.matchId === matchId;
}

function seatByNumber(seats: MatchSeat[], seat: number): MatchSeat | undefined {
  return seats.find((entry) => entry.seat === seat);
}

function streakLabel(value: number): string {
  if (value <= 1) {
    return "بداية";
  }

  return `${value} ورا بعض`;
}

function getChallengeTypeDefinition(key: ChallengeTypeKey): ChallengeTypeDefinition {
  return CHALLENGE_TYPES.find((entry) => entry.key === key) ?? CHALLENGE_TYPES[0];
}

function normalizePlayerLabel(value: string, fallback: string): string {
  const cleaned = value.replace(/\s+/g, " ").trim();
  return cleaned || fallback;
}

function createClientId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }

  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function shuffleArray<T>(values: T[]): T[] {
  const copy = [...values];
  for (let index = copy.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [copy[index], copy[swapIndex]] = [copy[swapIndex], copy[index]];
  }
  return copy;
}

function normalizeUniqueParticipants(participants: TeamParticipantDraft[]): TeamParticipantDraft[] {
  const seen = new Set<string>();
  const normalized: TeamParticipantDraft[] = [];

  participants.forEach((participant) => {
    const cleanedName = participant.name.replace(/\s+/g, " ").trim();
    const key = cleanedName.toLocaleLowerCase();
    if (!cleanedName || seen.has(key)) {
      return;
    }

    seen.add(key);
    normalized.push({
      ...participant,
      name: cleanedName,
    });
  });

  return normalized;
}

function assignShuffledTeams(participants: TeamParticipantDraft[]): TeamParticipantDraft[] {
  const normalized = normalizeUniqueParticipants(participants);
  const shuffled = shuffleArray(normalized);
  const splitIndex = Math.ceil(shuffled.length / 2);

  return shuffled.map((participant, index) => ({
    ...participant,
    team: index < splitIndex ? 1 : 2,
  }));
}

function splitTeamParticipants(participants: TeamParticipantDraft[]): {
  teamOne: TeamParticipantDraft[];
  teamTwo: TeamParticipantDraft[];
  waiting: TeamParticipantDraft[];
} {
  const teamOne = participants.filter((participant) => participant.team === 1);
  const teamTwo = participants.filter((participant) => participant.team === 2);
  const waiting = participants.filter((participant) => participant.team === null);

  return { teamOne, teamTwo, waiting };
}

function matchSetupStorageKey(matchId: string): string {
  return `${MATCH_SETUP_KEY_PREFIX}${matchId}`;
}

function saveMatchSetupMeta(matchId: string, value: MatchSetupMeta) {
  localStorage.setItem(matchSetupStorageKey(matchId), JSON.stringify(value));
}

function readSavedMatchSetupMeta(matchId: string): MatchSetupMeta | null {
  try {
    const rawValue = localStorage.getItem(matchSetupStorageKey(matchId));
    if (!rawValue) {
      return null;
    }

    const parsed = JSON.parse(rawValue) as Partial<MatchSetupMeta>;
    if (!parsed || !Array.isArray(parsed.seatMeta) || parsed.seatMeta.length !== 2) {
      return null;
    }

    const challengeType = parsed.challengeType ?? "head-to-head";
    const definition = getChallengeTypeDefinition(challengeType);
    const firstSeat = parsed.seatMeta[0];
    const secondSeat = parsed.seatMeta[1];
    if (!firstSeat || !secondSeat) {
      return null;
    }

    return {
      challengeType,
      challengeLabel: typeof parsed.challengeLabel === "string" ? parsed.challengeLabel : definition.label,
      boardPrompt: typeof parsed.boardPrompt === "string" ? parsed.boardPrompt : definition.boardPrompt,
      startPrompt: typeof parsed.startPrompt === "string" ? parsed.startPrompt : definition.startPrompt,
      participantCount:
        typeof parsed.participantCount === "number" && parsed.participantCount > 0 ? parsed.participantCount : 2,
      teamDistributionMode:
        parsed.teamDistributionMode === "shuffle" || parsed.teamDistributionMode === "manual"
          ? parsed.teamDistributionMode
          : null,
      seatMeta: [
        {
          title: normalizePlayerLabel(firstSeat.title ?? "", definition.seatPlaceholders[0]),
          roleLabel: normalizePlayerLabel(firstSeat.roleLabel ?? "", definition.seatRoleLabels[0]),
          members: Array.isArray(firstSeat.members)
            ? firstSeat.members
                .map((value) => normalizePlayerLabel(String(value), ""))
                .filter(Boolean)
            : [normalizePlayerLabel(firstSeat.title ?? "", definition.seatPlaceholders[0])],
        },
        {
          title: normalizePlayerLabel(secondSeat.title ?? "", definition.seatPlaceholders[1]),
          roleLabel: normalizePlayerLabel(secondSeat.roleLabel ?? "", definition.seatRoleLabels[1]),
          members: Array.isArray(secondSeat.members)
            ? secondSeat.members
                .map((value) => normalizePlayerLabel(String(value), ""))
                .filter(Boolean)
            : [normalizePlayerLabel(secondSeat.title ?? "", definition.seatPlaceholders[1])],
        },
      ],
    };
  } catch {
    return null;
  }
}

function buildFallbackMatchSetupMeta(match: MatchRead): MatchSetupMeta {
  const definition = getChallengeTypeDefinition("head-to-head");
  const firstSeatName = match.seats[0]?.player_name ?? definition.seatPlaceholders[0];
  const secondSeatName = match.seats[1]?.player_name ?? definition.seatPlaceholders[1];

  return {
    challengeType: definition.key,
    challengeLabel: definition.label,
    boardPrompt: definition.boardPrompt,
    startPrompt: definition.startPrompt,
    participantCount: 2,
    teamDistributionMode: null,
    seatMeta: [
      {
        title: firstSeatName,
        roleLabel: definition.seatRoleLabels[0],
        members: [firstSeatName],
      },
      {
        title: secondSeatName,
        roleLabel: definition.seatRoleLabels[1],
        members: [secondSeatName],
      },
    ],
  };
}

function seatSetupMetaFor(
  matchSetupMeta: MatchSetupMeta | null,
  seatNumber: number,
  fallbackName: string,
): MatchSetupSeatMeta {
  const storedSeat = matchSetupMeta?.seatMeta[seatNumber - 1];

  if (storedSeat) {
    return storedSeat;
  }

  return {
    title: fallbackName,
    roleLabel: "",
    members: [fallbackName],
  };
}

function shortenSummary(value: string | undefined): string {
  const cleaned = (value ?? "")
    .replace(/\s+/g, " ")
    .replace(/\[[^\]]*]/g, " ")
    .replace(/\u00a0/g, " ")
    .trim();
  if (!cleaned) {
    return "";
  }

  const sentences = cleaned.match(/[^.!?؟]+[.!?؟]?/gu)?.map((entry) => entry.trim()).filter(Boolean) ?? [cleaned];
  if (!sentences.length) {
    return cleaned;
  }

  let summary = "";
  for (const sentence of sentences.slice(0, 3)) {
    const candidate = summary ? `${summary} ${sentence}` : sentence;
    if (candidate.length > 280 && summary) {
      break;
    }
    summary = candidate;
    if (summary.length >= 110 && /[.!?؟]$/.test(sentence)) {
      break;
    }
  }

  return (summary || cleaned).replace(/(?:\.{3,}|…)+$/u, ".").trim();
}

function hasUsefulCardText(value: string | undefined): boolean {
  const cleaned = (value ?? "").replace(/\s+/g, " ").trim();
  if (!cleaned) {
    return false;
  }

  const plain = cleaned.replace(/[\s.,،؛:!?؟\-–—()[\]{}'"`]+/g, "");
  return plain.length >= 14 && /[A-Za-z\u0600-\u06FF]/.test(plain);
}

function normalizeAssistantQuestion(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[.,!?؟،؛…(){}[\]:"'`~*#\\/=\\|+<>_-]/g, " ")
    .replace(/[\u0640]/g, "")
    .replace(/[\u064b-\u065f\u0670]/g, "")
    .replace(/[أإآٱ]/g, "ا")
    .replace(/ى/g, "ي")
    .replace(/ؤ/g, "و")
    .replace(/ئ/g, "ي")
    .replace(/ة/g, "ه")
    .replace(/\s+/g, " ")
    .trim();
}

function containsAny(normalizedQuery: string, tokens: string[]): boolean {
  return tokens.some((token) => {
    const normalizedToken = normalizeAssistantQuestion(token);
    return normalizedToken ? normalizedQuery.includes(normalizedToken) : false;
  });
}

function isFallbackClubEntry(value: string | undefined): boolean {
  const normalized = normalizeAssistantQuestion(value ?? "");
  if (!normalized) {
    return true;
  }

  return (
    normalized.startsWith(normalizeAssistantQuestion("Latest known club")) ||
    normalized.startsWith(normalizeAssistantQuestion("Club path is not clear")) ||
    normalized.startsWith(normalizeAssistantQuestion("آخر نادي معروف")) ||
    normalized.startsWith(normalizeAssistantQuestion("مسار الأندية")) ||
    normalized === normalizeAssistantQuestion("No clear club sequence available")
  );
}

function getAssistantClubSequence(values: string[]): string[] {
  return values.map((value) => value.trim()).filter((value) => value && !isFallbackClubEntry(value));
}

function getKnownCurrentClub(displayClubName: string, displayClubsFinal: string[]): string {
  const directClubName = displayClubName.trim();
  if (directClubName && !isFallbackClubEntry(directClubName)) {
    return directClubName;
  }

  return getAssistantClubSequence(displayClubsFinal)[0] ?? "";
}

function assistantUiText(language: CardLanguage) {
  if (language === "en") {
    return {
      title: "AI Assistant",
      prompt: "Ask about this player",
      button: "Ask",
      answerNotFound: "I can't answer this question from this card.",
      answerPlaceholder: "Type a question and press Ask.",
      answerHint:
        "Ask about nationality, club, position, age, retirement year, summary, or achievements.",
      questionMissing: "Write a question first.",
      noSummary: "No summary available for this player yet.",
      noClubs: "No clear club sequence available.",
      noAchievements: "No achievements found for this card.",
    };
  }

  return {
    title: "المساعد الذكي",
    prompt: "أسأل عن اللاعب",
    button: "اسأل",
    answerNotFound: "ما أقدر أجاوب على هذا السؤال من البطاقة.",
    answerHint: "اسأل عن الجنسية، النادي، المركز، العمر، سنة الاعتزال أو الإنجازات.",
    answerPlaceholder: "اكتب السؤال وضغط اسأل.",
    questionMissing: "اكتب سؤال أولاً.",
    noSummary: "النبذة غير متاحة للاعب بعد.",
    noClubs: "تسلسل الأندية غير واضح حالياً.",
    noAchievements: "ما لقينا إنجازات لهد البطاقة.",
  };
}

function buildAssistantReply({
  query,
  language,
  cardPayload,
  displayCountryName,
  displayClubName,
  displayStatusLabel,
  displaySummary,
  displayClubsFinal,
  displayAchievements,
}: {
  query: string;
  language: CardLanguage;
  cardPayload: SharedPlayerCardPayload;
  displayCountryName: string;
  displayClubName: string;
  displayStatusLabel: string;
  displaySummary: string;
  displayClubsFinal: string[];
  displayAchievements: string[];
}): string {
  const normalized = normalizeAssistantQuestion(query);
  const labels = assistantUiText(language);
  const enName = cardPayload.n;
  const arName = cardPayload.na || cardPayload.n;
  const birthYear = cardPayload.y;
  const age = birthYear ? new Date().getFullYear() - birthYear : null;
  const positionAr = positionLabelFromGroup(cardPayload.p as never);
  const positionEn = POSITION_LABELS_EN[cardPayload.p] ?? POSITION_LABELS_EN.unknown;
  const assistantClubs = getAssistantClubSequence(displayClubsFinal);
  const knownCurrentClub = getKnownCurrentClub(displayClubName, displayClubsFinal);

  if (!normalized) {
    return labels.questionMissing;
  }

  if (
    containsAny(normalized, [
      "name",
      "who",
      "who is",
      "who is this",
      "player name",
      "اسم",
      "اسمه",
      "شنو اسمه",
      "وش اسمه",
      "منو",
      "منو هذا",
      "مين",
      "مين هذا",
      "من هذا",
      "من اللاعب",
    ])
  ) {
    return language === "en" ? `This player is ${enName}.` : `اسم اللاعب هو ${arName}.`;
  }

  if (
    containsAny(normalized, [
      "country",
      "nationality",
      "nation",
      "where from",
      "where is he from",
      "where's he from",
      "what nationality",
      "what country is he from",
      "what country does he represent",
      "which country",
      "من وين",
      "وين من",
      "وش جنسيته",
      "شنو جنسيته",
      "اي بلد",
      "اي دوله",
      "من اي بلد",
      "من اي دوله",
      "بلد",
      "بلده",
      "دوله",
      "دولته",
      "جنسي",
      "جنسيته",
    ])
  ) {
    return language === "en" ? `Nationality: ${displayCountryName}.` : `الجنسية: ${displayCountryName}.`;
  }

  if (
    containsAny(normalized, [
      "position",
      "plays as",
      "center",
      "role",
      "defender",
      "forward",
      "midfield",
      "goalkeeper",
      "مركز",
      "مركزه",
      "شنو مركزه",
      "وش مركزه",
      "يلعب باي مركز",
      "حارس",
      "مدافع",
      "وسط",
      "مهاجم",
    ])
  ) {
    return language === "en" ? `${enName} plays as a ${positionEn}.` : `يلعب في مركز ${positionAr}.`;
  }

  if (
    containsAny(normalized, [
      "club",
      "team",
      "which club",
      "what club",
      "what team",
      "clubs",
      "current club",
      "current team",
      "where does he play",
      "where does he play now",
      "where did he play",
      "played for",
      "last club",
      "الأندية",
      "الانديه",
      "النادي",
      "نادي",
      "ناديه",
      "وش ناديه",
      "شنو ناديه",
      "فريق",
      "فريقه",
      "يلعب مع",
      "يلعب لأي نادي",
      "يلعب لاي نادي",
      "تسلسل",
      "مسيرته مع الانديه",
    ])
  ) {
    if (!assistantClubs.length && !knownCurrentClub) {
      return labels.noClubs;
    }
    if (
      containsAny(normalized, [
        "sequence",
        "list",
        "club sequence",
        "تسلسل",
        "قائمة",
        "قائمه",
        "كل الأندية",
        "كل الانديه",
      ])
    ) {
      return language === "en"
        ? `Club sequence: ${assistantClubs.join(" - ")}.`
        : `تسلسل الأندية: ${assistantClubs.join(" - ")}.`;
    }
    if (!knownCurrentClub) {
      return labels.noClubs;
    }
    return language === "en" ? `Current club: ${knownCurrentClub}.` : `النادي الحالي: ${knownCurrentClub}.`;
  }

  if (containsAny(normalized, ["age", "عمر", "كم سنة", "مولود", "ولد", "عام"])) {
    if (!age) {
      return language === "en" ? "Age is not available." : "العمر غير متوفر.";
    }
    return language === "en"
      ? `${enName} is about ${age} years old.`
      : `${arName} عمره تقريبًا ${age} سنة.`;
  }

  if (containsAny(normalized, ["retire", "retired", "اعتزال", "معتزل", "تقاعد", "تقاعد"])) {
    return language === "en"
      ? `Player status: ${displayStatusLabel}.`
      : `الوضع الحالي: ${displayStatusLabel}.`;
  }

  if (containsAny(normalized, ["achievement", "achievements", "titles", "honors", "trophy", "إنجاز", "ألقاب"])) {
    if (!displayAchievements.length) {
      return labels.noAchievements;
    }
    return language === "en"
      ? `Top achievements: ${displayAchievements.join(" - ")}.`
      : `أبرز الإنجازات: ${displayAchievements.join(" - ")}.`;
  }

  if (containsAny(normalized, ["summary", "نبذة", "about", "bio", "biography"])) {
    return hasUsefulCardText(displaySummary) ? displaySummary : labels.noSummary;
  }

  if (containsAny(normalized, ["birth", "was born", "مولود", "متى", "عام الميلاد", "مكان الولادة"])) {
    return language === "en"
      ? `${enName} was born in ${birthYear}.`
      : `${arName} تم ولادته في ${birthYear}.`;
  }

  if (containsAny(normalized, ["status", "active", "retirement", "active", "حالة", "حالي"])) {
    return language === "en"
      ? `Player status: ${displayStatusLabel}.`
      : `حالة اللاعب: ${displayStatusLabel}.`;
  }

  return `${labels.answerNotFound} ${labels.answerHint}`;
}

function buildSmartAssistantReply(
  args: Parameters<typeof buildAssistantReply>[0],
): string {
  const {
    query,
    language,
    cardPayload,
    displayCountryName,
    displayClubName,
    displayStatusLabel,
    displaySummary,
    displayClubsFinal,
    displayAchievements,
  } = args;
  const normalized = normalizeAssistantQuestion(query);
  const labels = assistantUiText(language);
  const enName = cardPayload.n;
  const arName = cardPayload.na || cardPayload.n;
  const birthYear = cardPayload.y;
  const age = birthYear ? new Date().getFullYear() - birthYear : null;
  const positionAr = positionLabelFromGroup(cardPayload.p as never);
  const positionEn = POSITION_LABELS_EN[cardPayload.p] ?? POSITION_LABELS_EN.unknown;
  const assistantClubs = getAssistantClubSequence(displayClubsFinal);
  const knownCurrentClub = getKnownCurrentClub(displayClubName, displayClubsFinal);

  if (!normalized) {
    return labels.questionMissing;
  }

  if (
    containsAny(normalized, [
      "منو",
      "منو هذا",
      "مين",
      "مين هذا",
      "من هذا",
      "من اللاعب",
      "اسمه",
      "وش اسمه",
      "شنو اسمه",
      "name",
      "who is this",
      "player name",
    ])
  ) {
    return language === "en" ? `This player is ${enName}.` : `اسم اللاعب هو ${arName}.`;
  }

  if (
    containsAny(normalized, [
      "من وين",
      "وين من",
      "اي بلد",
      "اي دوله",
      "من اي بلد",
      "من اي دوله",
      "بلده",
      "دولته",
      "وش جنسيته",
      "شنو جنسيته",
      "جنسي",
      "جنسيته",
      "nationality",
      "country",
      "where is he from",
      "where's he from",
      "where from",
      "what nationality",
      "what country is he from",
      "which country",
    ])
  ) {
    return language === "en" ? `Nationality: ${displayCountryName}.` : `الجنسية: ${displayCountryName}.`;
  }

  if (
    containsAny(normalized, [
      "مركزه",
      "وش مركزه",
      "شنو مركزه",
      "يلعب باي مركز",
      "يلعب بأي مركز",
      "position",
      "plays as",
      "role",
    ])
  ) {
    return language === "en" ? `${enName} plays as a ${positionEn}.` : `يلعب في مركز ${positionAr}.`;
  }

  if (
    containsAny(normalized, [
      "ناديه",
      "فريقه",
      "يلعب لاي نادي",
      "يلعب لأي نادي",
      "يلعب مع من",
      "وين يلعب",
      "الحين وين يلعب",
      "اي نادي",
      "آخر نادي",
      "اخر نادي",
      "club",
      "what club",
      "what team",
      "which club",
      "current team",
      "current club",
      "where does he play",
      "where does he play now",
    ])
  ) {
    if (!assistantClubs.length && !knownCurrentClub) {
      return labels.noClubs;
    }

    return language === "en"
      ? `Current club: ${knownCurrentClub}.`
      : `النادي الحالي: ${knownCurrentClub}.`;
  }

  if (
    containsAny(normalized, [
      "تسلسل",
      "كل الانديه",
      "كل الأندية",
      "وش الانديه",
      "شنو الانديه",
      "club sequence",
      "played for",
    ])
  ) {
    if (!assistantClubs.length) {
      return labels.noClubs;
    }

    return language === "en"
      ? `Club sequence: ${assistantClubs.join(" - ")}.`
      : `تسلسل الأندية: ${assistantClubs.join(" - ")}.`;
  }

  if (
    containsAny(normalized, [
      "كم عمره",
      "عمره",
      "age",
      "how old",
      "old",
    ])
  ) {
    if (!age) {
      return language === "en" ? "Age is not available." : "العمر مو متوفر.";
    }

    return language === "en"
      ? `${enName} is about ${age} years old.`
      : `${arName} عمره تقريبًا ${age} سنة.`;
  }

  if (
    containsAny(normalized, [
      "من مواليد",
      "مواليد",
      "سنة ميلاده",
      "سنه ميلاده",
      "birth year",
      "born in",
    ])
  ) {
    return language === "en"
      ? `${enName} was born in ${birthYear}.`
      : `${arName} من مواليد ${birthYear}.`;
  }

  if (
    containsAny(normalized, [
      "معتزل",
      "اعتزل",
      "متى اعتزل",
      "سنة الاعتزال",
      "سنه الاعتزال",
      "للحين يلعب",
      "لازال يلعب",
      "لسه يلعب",
      "status",
      "retired",
      "still playing",
    ])
  ) {
    return language === "en"
      ? `Player status: ${displayStatusLabel}.`
      : `حالة اللاعب: ${displayStatusLabel}.`;
  }

  if (
    containsAny(normalized, [
      "انجاز",
      "انجازاته",
      "ابرز انجازاته",
      "القاب",
      "ألقاب",
      "بطولات",
      "بطولاته",
      "وش فاز",
      "شنو فاز",
      "وش حقق",
      "شنو حقق",
      "achievements",
      "titles",
      "trophies",
    ])
  ) {
    if (!displayAchievements.length) {
      return labels.noAchievements;
    }

    return language === "en"
      ? `Top achievements: ${displayAchievements.join(" - ")}.`
      : `أبرز الإنجازات: ${displayAchievements.join(" - ")}.`;
  }

  if (
    containsAny(normalized, [
      "نبذه",
      "نبذة",
      "عنه",
      "قول لي عنه",
      "احكي عنه",
      "عرفني عليه",
      "about",
      "summary",
      "bio",
    ])
  ) {
    return hasUsefulCardText(displaySummary) ? displaySummary : labels.noSummary;
  }

  const legacyReply = buildAssistantReply(args);
  if (legacyReply !== `${labels.answerNotFound} ${labels.answerHint}`) {
    return legacyReply;
  }

  if (containsAny(normalized, ["وين"])) {
    return language === "en"
      ? `Nationality: ${displayCountryName}.`
      : `الجنسية: ${displayCountryName}.`;
  }

  return legacyReply;
}

function buildFallbackPlayerSummary({
  playerName,
  countryName,
  positionLabel,
  birthYear,
  clubName,
  isActive,
  retiredYear,
  language,
}: {
  playerName: string;
  countryName: string;
  positionLabel: string;
  birthYear: number;
  clubName: string;
  isActive: boolean;
  retiredYear: number | null;
  language: CardLanguage;
}): string {
  if (language === "en") {
    if (isActive) {
      return clubName
        ? `${playerName} is a football player from ${countryName} who plays as a ${positionLabel}. He currently plays for ${clubName} and was born in ${birthYear}.`
        : `${playerName} is a football player from ${countryName} who plays as a ${positionLabel}. He was born in ${birthYear} and is still active.`;
    }

    return retiredYear
      ? `${playerName} is a former football player from ${countryName} who played as a ${positionLabel}. He was born in ${birthYear} and retired in ${retiredYear}.`
      : `${playerName} is a former football player from ${countryName} who played as a ${positionLabel}. He was born in ${birthYear}.`;
  }

  if (isActive) {
    return clubName
      ? `${playerName} لاعب كرة قدم من ${countryName} ويلعب في مركز ${positionLabel}. يلعب حاليًا مع ${clubName} ومواليده ${birthYear}.`
      : `${playerName} لاعب كرة قدم من ${countryName} ويلعب في مركز ${positionLabel}. مواليده ${birthYear} وما زال يلعب.`;
  }

  return retiredYear
    ? `${playerName} لاعب كرة قدم سابق من ${countryName} وكان يلعب في مركز ${positionLabel}. مواليده ${birthYear} واعتزل في ${retiredYear}.`
    : `${playerName} لاعب كرة قدم سابق من ${countryName} وكان يلعب في مركز ${positionLabel}. مواليده ${birthYear}.`;
}

function buildFallbackClubSequence({
  clubs,
  clubName,
  language,
}: {
  clubs: string[];
  clubName: string;
  language: CardLanguage;
}): string[] {
  if (clubs.length) {
    return clubs;
  }

  if (clubName) {
    return [language === "en" ? `Latest known club: ${clubName}` : `آخر نادي معروف: ${clubName}`];
  }

  return [language === "en" ? "Club path is not clear on this card yet." : "مسار الأندية مو واضح بهالبطاقة للحين."];
}

function formatCount(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDateTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString("en-GB", {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function listToCsv(values: string[]): string {
  return values.join("، ");
}

function csvToList(value: string): string[] {
  return value
    .split(/[,،\n]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

type AdminPlayerFormState = {
  wikidata_id: string;
  name: string;
  name_ar: string;
  image_url: string;
  difficulty: number;
  fame_score: number;
  birth_year: number;
  position_group: "goalkeeper" | "defender" | "midfielder" | "forward";
  is_active: boolean;
  countries: string;
  countries_ar: string;
  continents: string;
  continents_ar: string;
  positions: string;
  positions_ar: string;
  aliases: string;
  current_team: string;
  current_team_ar: string;
  admin_locked: boolean;
};

function emptyAdminPlayerForm(): AdminPlayerFormState {
  return {
    wikidata_id: "",
    name: "",
    name_ar: "",
    image_url: "",
    difficulty: 2,
    fame_score: 50,
    birth_year: 1990,
    position_group: "midfielder",
    is_active: true,
    countries: "",
    countries_ar: "",
    continents: "",
    continents_ar: "",
    positions: "",
    positions_ar: "",
    aliases: "",
    current_team: "",
    current_team_ar: "",
    admin_locked: true,
  };
}

function adminPlayerToFormState(player: AdminPlayer): AdminPlayerFormState {
  return {
    wikidata_id: player.wikidata_id,
    name: player.name,
    name_ar: player.name_ar,
    image_url: player.image_url,
    difficulty: player.difficulty,
    fame_score: player.fame_score,
    birth_year: player.birth_year,
    position_group: player.position_group as AdminPlayerFormState["position_group"],
    is_active: player.is_active,
    countries: listToCsv(player.countries),
    countries_ar: listToCsv(player.countries_ar),
    continents: listToCsv(player.continents),
    continents_ar: listToCsv(player.continents_ar),
    positions: listToCsv(player.positions),
    positions_ar: listToCsv(player.positions_ar),
    aliases: listToCsv(player.aliases),
    current_team: player.current_team,
    current_team_ar: player.current_team_ar,
    admin_locked: player.admin_locked,
  };
}

function adminFormToPayload(form: AdminPlayerFormState): AdminPlayerWritePayload {
  return {
    wikidata_id: form.wikidata_id.trim(),
    name: form.name.trim(),
    name_ar: form.name_ar.trim(),
    image_url: form.image_url.trim(),
    difficulty: form.difficulty,
    fame_score: form.fame_score,
    birth_year: form.birth_year,
    position_group: form.position_group,
    is_active: form.is_active,
    countries: csvToList(form.countries),
    countries_ar: csvToList(form.countries_ar),
    continents: csvToList(form.continents),
    continents_ar: csvToList(form.continents_ar),
    positions: csvToList(form.positions),
    positions_ar: csvToList(form.positions_ar),
    aliases: csvToList(form.aliases),
    current_team: form.current_team.trim(),
    current_team_ar: form.current_team_ar.trim(),
    admin_locked: form.admin_locked,
  };
}

type AssistantArgumentKind = "" | "competition" | "team";

type AdminAssistantQuestionFormState = {
  intent_key: string;
  question_ar: string;
  question_en: string;
  aliases_ar: string;
  aliases_en: string;
  argument_kind: AssistantArgumentKind;
  enabled: boolean;
};

function emptyAdminAssistantQuestionForm(): AdminAssistantQuestionFormState {
  return {
    intent_key: "",
    question_ar: "",
    question_en: "",
    aliases_ar: "",
    aliases_en: "",
    argument_kind: "",
    enabled: true,
  };
}

function adminAssistantQuestionToFormState(item: AdminAssistantQuestion): AdminAssistantQuestionFormState {
  return {
    intent_key: item.intent_key,
    question_ar: item.question_ar,
    question_en: item.question_en,
    aliases_ar: listToCsv(item.aliases_ar),
    aliases_en: listToCsv(item.aliases_en),
    argument_kind: item.argument_kind,
    enabled: item.enabled,
  };
}

function adminAssistantQuestionFormToPayload(
  form: AdminAssistantQuestionFormState,
): AdminAssistantQuestionWritePayload {
  return {
    intent_key: form.intent_key.trim(),
    question_ar: form.question_ar.trim(),
    question_en: form.question_en.trim(),
    aliases_ar: csvToList(form.aliases_ar),
    aliases_en: csvToList(form.aliases_en),
    argument_kind: form.argument_kind,
    enabled: form.enabled,
  };
}

type AdminAssistantCompetitionFormState = {
  key: string;
  wikidata_id: string;
  name_ar: string;
  name_en: string;
  aliases_ar: string;
  aliases_en: string;
  enabled: boolean;
};

function emptyAdminAssistantCompetitionForm(): AdminAssistantCompetitionFormState {
  return {
    key: "",
    wikidata_id: "",
    name_ar: "",
    name_en: "",
    aliases_ar: "",
    aliases_en: "",
    enabled: true,
  };
}

function adminAssistantCompetitionToFormState(item: AdminAssistantCompetition): AdminAssistantCompetitionFormState {
  return {
    key: item.key,
    wikidata_id: item.wikidata_id,
    name_ar: item.name_ar,
    name_en: item.name_en,
    aliases_ar: listToCsv(item.aliases_ar),
    aliases_en: listToCsv(item.aliases_en),
    enabled: item.enabled,
  };
}

function adminAssistantCompetitionFormToPayload(
  form: AdminAssistantCompetitionFormState,
): AdminAssistantCompetitionWritePayload {
  return {
    key: form.key.trim(),
    wikidata_id: form.wikidata_id.trim(),
    name_ar: form.name_ar.trim(),
    name_en: form.name_en.trim(),
    aliases_ar: csvToList(form.aliases_ar),
    aliases_en: csvToList(form.aliases_en),
    enabled: form.enabled,
  };
}

function assistantArgumentLabel(value: AssistantArgumentKind): string {
  if (value === "competition") {
    return "دوري أو مسابقة";
  }
  if (value === "team") {
    return "نادٍ أو منتخب";
  }
  return "بدون متغير";
}

function shortId(value: string): string {
  return value.slice(0, 8);
}

function matchTokenStorageKey(matchId: string): string {
  return `${MATCH_TOKEN_KEY_PREFIX}${matchId}`;
}

function readSavedMatchToken(matchId: string): string {
  return sessionStorage.getItem(matchTokenStorageKey(matchId)) ?? "";
}

function saveMatchToken(matchId: string, matchToken: string) {
  if (!matchId || !matchToken) {
    return;
  }
  sessionStorage.setItem(matchTokenStorageKey(matchId), matchToken);
}

function clearSavedMatchToken(matchId: string) {
  if (!matchId) {
    return;
  }
  sessionStorage.removeItem(matchTokenStorageKey(matchId));
}

function clearSavedMatchSetupMeta(matchId: string) {
  if (!matchId) {
    return;
  }
  localStorage.removeItem(matchSetupStorageKey(matchId));
}

function useWikipediaCard(
  englishName: string | null,
  arabicName: string | null,
  wikidataId: string | null,
  language: CardLanguage,
) {
  const [summary, setSummary] = useState<WikipediaSummary | null>(null);
  const [details, setDetails] = useState<WikipediaPlayerDetails | null>(null);
  const [clubSequence, setClubSequence] = useState<string[]>([]);
  useEffect(() => {
    let active = true;
    if (!englishName) {
      setSummary(null);
      setDetails(null);
      setClubSequence([]);
      return;
    }
    const lookupEnglishName = englishName;
    const lookupArabicName = arabicName;
    const lookupWikidataId = wikidataId;

    async function loadWikipediaData() {
      const [payload, clubsFromWikidata] = await Promise.all([
        language === "ar"
          ? fetchArabicWikipediaBundle(lookupEnglishName, lookupArabicName ?? undefined, lookupWikidataId ?? undefined)
          : fetchEnglishWikipediaBundle(lookupEnglishName, lookupArabicName ?? undefined, lookupWikidataId ?? undefined),
        lookupWikidataId
          ? fetchWikidataClubSequence(lookupWikidataId, language)
          : Promise.resolve({ clubs: [], retired_year: null }),
      ]);
      if (!active) {
        return;
      }

      setSummary(payload.summary);
      setDetails(
        payload.details
          ? {
              ...payload.details,
              retired_year: payload.details.retired_year ?? clubsFromWikidata.retired_year,
            }
          : {
              achievements: [],
              club_sequence: clubsFromWikidata.clubs,
              retired_year: clubsFromWikidata.retired_year,
              page_language: language,
            },
      );
      setClubSequence(clubsFromWikidata.clubs);
    }

    void loadWikipediaData();

    return () => {
      active = false;
    };
  }, [arabicName, englishName, language, wikidataId]);

  return {
    clubSequence,
    summary,
    details,
  };
}

const POSITION_LABELS_EN: Record<string, string> = {
  goalkeeper: "Goalkeeper",
  defender: "Defender",
  midfielder: "Midfielder",
  forward: "Forward",
  unknown: "Unknown",
};

function positionLabelForCard(positionGroup: string, language: CardLanguage): string {
  if (language === "ar") {
    return positionLabelFromGroup(positionGroup as never);
  }

  return POSITION_LABELS_EN[positionGroup] ?? POSITION_LABELS_EN.unknown;
}

function cardUiText(language: CardLanguage) {
  if (language === "en") {
    return {
      dir: "ltr" as const,
      name: "Name",
      from: "From",
      position: "Position",
      born: "Born",
      status: "Status",
      club: "Club",
      active: "Still playing",
      retired: "Retired",
      notes: "Notes",
      notesPlaceholder: "Write here",
      wiki: "Open Wikipedia",
      summary: "Quick info",
      clubs: "Club sequence",
      achievements: "Top achievements",
      assistant: "AI Assistant",
      assistantPrompt: "Ask about this player",
      assistantButton: "Ask",
      assistantAnswerNotFound:
        "I can't answer this question from this card. Ask about nationality, club, position, age, retirement year, or achievements.",
      arabic: "العربية",
      english: "English",
      wikipediaSearchBase: "https://en.wikipedia.org/wiki/Special:Search?search=",
    };
  }

  return {
    dir: "rtl" as const,
    name: "الاسم",
    from: "من",
    position: "المركز",
    born: "مواليد",
    status: "الحالة",
    club: "النادي",
    active: "للحين يلعب",
    retired: "معتزل",
    notes: "ملاحظات",
    notesPlaceholder: "اكتب هنا",
    wiki: "افتح ويكيبيديا",
    summary: "نبذة سريعة",
    clubs: "تسلسل الأندية",
    achievements: "أهم إنجازاته",
    arabic: "العربية",
    english: "English",
    wikipediaSearchBase: "https://ar.wikipedia.org/wiki/Special:Search?search=",
  };
}

function BrandMark({
  className = "",
  compact = false,
}: {
  className?: string;
  compact?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const classes = ["brand-mark", compact ? "brand-mark--compact" : "", className]
    .filter(Boolean)
    .join(" ");

  if (failed) {
    return (
      <div className={classes}>
        <div className="brand-mark__fallback">منو</div>
      </div>
    );
  }

  return (
    <div className={classes}>
      <img
        alt="شعار منو"
        className="brand-mark__image"
        onError={() => setFailed(true)}
        src={GAME_LOGO_DATA_URL}
      />
    </div>
  );
}

export default function App() {
  const [route, setRoute] = useState<AppRoute>(() => parseRoute(window.location));
  const isPublicCardRoute = route.name === "card";
  const actionLockRef = useRef<null | "create" | "award" | "no-answer" | "next-round" | "end">(null);
  const endingMatchIdRef = useRef<string | null>(null);
  const matchTokenRef = useRef("");
  const awardedSeatRef = useRef<number | null>(null);
  const roundResolvedRef = useRef(false);
  const [session, setSession] = useState<AuthSessionRead>({
    authenticated: false,
    username: null,
  });
  const [sessionChecked, setSessionChecked] = useState(false);
  const [overview, setOverview] = useState<GameOverview | null>(null);
  const [overviewError, setOverviewError] = useState("");

  const [shareBaseUrl, setShareBaseUrl] = useState<string>(() => {
    const storedValue = localStorage.getItem(SHARE_URL_KEY) ?? defaultShareUrl();
    return isTransientShareUrl(storedValue) ? "" : storedValue;
  });
  const [autoShareBaseUrl, setAutoShareBaseUrl] = useState("");
  const effectiveShareBaseUrl = isPublicShareUrl(autoShareBaseUrl)
    ? autoShareBaseUrl.trim()
    : isPublicShareUrl(shareBaseUrl)
      ? shareBaseUrl.trim()
      : "";

  const [playerOneName, setPlayerOneName] = useState("");
  const [playerTwoName, setPlayerTwoName] = useState("");
  const [speakerOneName, setSpeakerOneName] = useState("");
  const [speakerTwoName, setSpeakerTwoName] = useState("");
  const [teamOneName, setTeamOneName] = useState("");
  const [teamTwoName, setTeamTwoName] = useState("");
  const [selectedChallengeType, setSelectedChallengeType] = useState<ChallengeTypeKey>("head-to-head");
  const [teamDistributionMode, setTeamDistributionMode] = useState<TeamDistributionMode>("shuffle");
  const [teamMemberDraft, setTeamMemberDraft] = useState("");
  const [teamParticipants, setTeamParticipants] = useState<TeamParticipantDraft[]>([]);
  const [selectedDifficulty, setSelectedDifficulty] = useState(2);
  const [selectedMode, setSelectedMode] = useState<MatchModeKey>("race-to-100");
  const [selectedAnswerRuleKeys, setSelectedAnswerRuleKeys] = useState<AnswerRuleKey[]>([]);
  const [selectedProhibitedCategoryKeys, setSelectedProhibitedCategoryKeys] = useState<
    QuestionCategoryKey[]
  >([]);

  const [match, setMatch] = useState<MatchRead | null>(null);
  const [matchSetupMeta, setMatchSetupMeta] = useState<MatchSetupMeta | null>(null);
  const [matchToken, setMatchToken] = useState("");
  const [secretLinks, setSecretLinks] = useState<Record<number, { url: string; qrUrl: string }>>({});
  const [, setStatusMessage] = useState("تمام");
  const [busyAction, setBusyAction] = useState<
    "create" | "award" | "no-answer" | "next-round" | "end" | "load" | "copy" | "login" | "logout" | null
  >(null);
  const activeMatch =
    match?.status === "active" && endingMatchIdRef.current !== match.match_id
      ? match
      : null;
  const visibleRoute: AppRoute = activeMatch
    ? route.name === "game" && route.matchId === activeMatch.match_id
      ? route
      : route.name === "settings"
        ? route
        : { name: "lobby", matchId: activeMatch.match_id }
    : route.name === "lobby" || route.name === "game"
      ? { name: "home" }
      : route;

  useEffect(() => {
    matchTokenRef.current = matchToken.trim();
  }, [matchToken]);

  useEffect(() => {
    if (match?.match_token) {
      matchTokenRef.current = match.match_token.trim();
    }
    awardedSeatRef.current = match?.round.awarded_to ?? null;
    roundResolvedRef.current = match?.round.resolved ?? false;
  }, [match]);

  function syncMatchPayload(payload: MatchRead) {
    if (payload.status === "active") {
      endingMatchIdRef.current = null;
    }
    matchTokenRef.current = payload.match_token.trim();
    awardedSeatRef.current = payload.round.awarded_to ?? null;
    roundResolvedRef.current = payload.round.resolved;
    setMatch(payload);
    setMatchToken(payload.match_token);
    saveMatchToken(payload.match_id, payload.match_token);
  }

  function clearCurrentMatch() {
    matchTokenRef.current = "";
    awardedSeatRef.current = null;
    roundResolvedRef.current = false;
    setMatch(null);
    setMatchToken("");
  }

  function readCurrentMatchToken(matchId: string, fallback = "") {
    const candidates = [
      matchTokenRef.current,
      fallback,
      readSavedMatchToken(matchId),
    ];
    return candidates.find((value) => value.trim())?.trim() ?? "";
  }

  useEffect(() => {
    function syncRoute() {
      setRoute(parseRoute(window.location));
    }

    window.addEventListener("hashchange", syncRoute);
    window.addEventListener("popstate", syncRoute);
    return () => {
      window.removeEventListener("hashchange", syncRoute);
      window.removeEventListener("popstate", syncRoute);
    };
  }, []);

  useEffect(() => {
    if (isPublicCardRoute) {
      setSessionChecked(true);
      return;
    }

    let active = true;

    async function loadSession() {
      try {
        const payload = await api.getSession();
        if (active) {
          setSession(payload);
        }
      } catch {
        if (active) {
          setSession({
            authenticated: false,
            username: null,
          });
        }
      } finally {
        if (active) {
          setSessionChecked(true);
        }
      }
    }

    void loadSession();
    return () => {
      active = false;
    };
  }, [isPublicCardRoute]);

  useEffect(() => {
    if (isPublicCardRoute) {
      return;
    }

    function handleUnauthorized() {
      setSession({
        authenticated: false,
        username: null,
      });
      clearCurrentMatch();
      setSecretLinks({});
      setSessionChecked(true);
    }

    window.addEventListener("minu:unauthorized", handleUnauthorized as EventListener);
    return () => {
      window.removeEventListener("minu:unauthorized", handleUnauthorized as EventListener);
    };
  }, [isPublicCardRoute]);

  useEffect(() => {
    if (!sessionChecked || !session.authenticated) {
      return;
    }

    async function loadOverview() {
      try {
        const payload = await api.getOverview();
        setOverview(payload);
        setOverviewError("");
      } catch (error) {
        const message =
          error instanceof ApiError ? error.message : "ما ضبط التحميل.";
        setOverviewError(message);
      }
    }

    void loadOverview();
  }, [session.authenticated, sessionChecked]);

  useEffect(() => {
    const storedValue = localStorage.getItem(SHARE_URL_KEY) ?? "";
    if (isTransientShareUrl(storedValue)) {
      localStorage.removeItem(SHARE_URL_KEY);
    }
  }, []);

  useEffect(() => {
    if (session.authenticated) {
      return;
    }

    setOverview(null);
    clearCurrentMatch();
    setMatchSetupMeta(null);
    setSecretLinks({});
  }, [session.authenticated]);

  useEffect(() => {
    if (!session.authenticated) {
      return;
    }

    if (!overview) {
      return;
    }

    const nextSelection = buildRandomTwistSelection(selectedDifficulty, overview.question_categories);
    setSelectedAnswerRuleKeys(nextSelection.answerRuleKeys);
    setSelectedProhibitedCategoryKeys(nextSelection.prohibitedCategoryKeys);
  }, [overview, selectedDifficulty]);

  useEffect(() => {
    if (!sessionChecked || !session.authenticated) {
      return;
    }

    let active = true;

    async function loadShareLink() {
      try {
        const payload = await api.getShareLink();
        if (!active) {
          return;
        }

        const nextUrl = payload.public_url?.trim() ?? "";
        setAutoShareBaseUrl(nextUrl);
      } catch {
        if (active) {
          setAutoShareBaseUrl("");
        }
      }
    }

    void loadShareLink();
    const intervalId = window.setInterval(() => {
      void loadShareLink();
    }, 15000);

    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [session.authenticated, sessionChecked]);

  useEffect(() => {
    if (!session.authenticated) {
      return;
    }

    if (busyAction === "end") {
      return;
    }

    if (route.name !== "lobby" && route.name !== "game") {
      return;
    }

    const matchId = route.matchId;
    if (endingMatchIdRef.current === matchId) {
      clearSavedMatchToken(matchId);
      clearSavedMatchSetupMeta(matchId);
      setSecretLinks({});
      setMatchSetupMeta(null);
      setRoute({ name: "home" });
      navigate({ name: "home" });
      return;
    }
    let active = true;
    const persistedMatchToken = readSavedMatchToken(matchId);
    const hasFreshLocalMatch =
      match?.match_id === matchId &&
      match.status === "active" &&
      Boolean(match.match_token) &&
      match.match_token === persistedMatchToken;

    if (hasFreshLocalMatch) {
      matchTokenRef.current = match.match_token.trim();
      setMatchToken(match.match_token);
      saveMatchToken(match.match_id, match.match_token);
      setOverviewError("");
      setBusyAction(null);

      const loadSecretLinks = async (token: string) => {
        if (endingMatchIdRef.current === matchId) {
          return;
        }

        if (!effectiveShareBaseUrl) {
          setSecretLinks({});
          return;
        }

        try {
          const [seatOneCard, seatTwoCard] = await Promise.all([
            api.getPlayerCardToken(matchId, 1, token),
            api.getPlayerCardToken(matchId, 2, token),
          ]);
          if (!active || endingMatchIdRef.current === matchId) {
            return;
          }
          setSecretLinks(buildSecretLinks(effectiveShareBaseUrl, seatOneCard.token, seatTwoCard.token));
          setOverviewError("");
          setStatusMessage("الأوراق جاهزة");
        } catch (error) {
          if (!active) {
            return;
          }
          setStatusMessage(error instanceof ApiError ? error.message : "حدث خطأ.");
        }
      };

      void loadSecretLinks(match.match_token);
      return;
    }

    clearCurrentMatch();
    setSecretLinks({});
    setBusyAction("load");

    async function loadMatchPackage() {
      try {
        const initialMatchToken = readCurrentMatchToken(matchId, match?.match_token ?? "");
        const matchPayload = await api.getMatch(matchId, initialMatchToken);
        if (!active || endingMatchIdRef.current === matchId) {
          return;
        }
        if (matchPayload.status !== "active") {
          clearSavedMatchToken(matchId);
          clearSavedMatchSetupMeta(matchId);
          if (match?.match_id === matchId) {
            clearCurrentMatch();
          }
          setMatchSetupMeta(null);
          setSecretLinks({});
          setRoute({ name: "home" });
          navigate({ name: "home" });
          return;
        }
        syncMatchPayload(matchPayload);
        setOverviewError("");

        const [seatOneCard, seatTwoCard] = await Promise.all([
          api.getPlayerCardToken(matchId, 1, matchPayload.match_token),
          api.getPlayerCardToken(matchId, 2, matchPayload.match_token),
        ]);
        if (!active) {
          return;
        }

        if (effectiveShareBaseUrl) {
          setSecretLinks(
            buildSecretLinks(effectiveShareBaseUrl, seatOneCard.token, seatTwoCard.token),
          );
        } else {
          setSecretLinks({});
        }
        setStatusMessage(effectiveShareBaseUrl ? "البطاقات جاهزة" : "حط رابط عام");
      } catch (error) {
        if (!active) {
          return;
        }
        setStatusMessage(
          error instanceof ApiError ? error.message : "ما ضبط",
        );
      } finally {
        if (active) {
          setBusyAction(null);
        }
      }
    }

    void loadMatchPackage();

    return () => {
      active = false;
    };
  }, [
    route,
    effectiveShareBaseUrl,
    session.authenticated,
    busyAction,
    match?.match_id,
    match?.match_token,
    match?.round.round_number,
  ]);

  useEffect(() => {
    if (!session.authenticated || !match || match.status !== "active") {
      return;
    }

    if (route.name === "settings" || isActiveMatchRoute(route, match.match_id)) {
      return;
    }

    navigate({ name: "lobby", matchId: match.match_id });
  }, [match?.match_id, match?.status, route, session.authenticated]);

  useEffect(() => {
    if (
      !session.authenticated
      || !match
      || match.status !== "active"
      || route.name !== "game"
      || route.matchId !== match.match_id
    ) {
      return;
    }

    const lockState = {
      minuMatchId: match.match_id,
      minuLockedRoute: "game",
      lockedAt: Date.now(),
    };

    window.history.pushState(lockState, "", window.location.href);

    function blockBrowserBack() {
      window.history.pushState(lockState, "", window.location.href);
    }

    window.addEventListener("popstate", blockBrowserBack);
    return () => {
      window.removeEventListener("popstate", blockBrowserBack);
    };
  }, [match?.match_id, match?.status, route, session.authenticated]);

  /*
  useEffect(() => {
    return;
    if (!match || route.name !== "lobby" || match.status !== "active") {
      return;
    }

    const currentState =
      seenMatchPairsRef.current[match.match_id]
      || (seenMatchPairsRef.current[match.match_id] = {
        pairs: new Set<string>(),
        seenPlayerIds: new Set<number>(),
        attempts: 0,
      });

    const seatIds = getMatchSeatIds(match).sort((left, right) => left - right);
    match.recent_player_ids.forEach((seatId) => {
      if (seatId > 0) {
        currentState.seenPlayerIds.add(Math.trunc(seatId));
      }
    });

    const currentPair = buildSeatPairKey(match);
    const duplicatePlayerFound = hasRepeatedSeats(match, [...currentState.seenPlayerIds]).repeated;

    if (match.round.round_number > 1 && (currentState.pairs.has(currentPair) || duplicatePlayerFound)) {
      const attempt = (currentState.attempts || 0) + 1;
      currentState.attempts = attempt;
      seenMatchPairsRef.current[match.match_id] = currentState;

      if (attempt <= 3 && !busyAction) {
        void createNextRound();
      } else if (attempt === 4) {
        setOverviewError("تعذر اختيار تحدي جديد غير مكرر حالياً. جرّب إنشاء مباراة جديدة.");
      }

      return;
    }

    if (seatIds.length === 2) {
      seatIds.forEach((seatId) => currentState.seenPlayerIds.add(seatId));
    }

    currentState.pairs.add(currentPair);
    currentState.attempts = 0;
    seenMatchPairsRef.current[match.match_id] = currentState;
  }, [match?.match_id, match?.round.round_number, route.name, match?.status, match?.seats, busyAction]);
  */

  useEffect(() => {
    if (!match) {
      setMatchSetupMeta(null);
      return;
    }

    setMatchSetupMeta(readSavedMatchSetupMeta(match.match_id) ?? buildFallbackMatchSetupMeta(match));
  }, [match]);

  const selectedModeConfig = getModeDefinition(selectedMode);
  const selectedChallengeTypeConfig = getChallengeTypeDefinition(selectedChallengeType);
  const teamParticipantGroups = splitTeamParticipants(teamParticipants);

  function handleChallengeTypeChange(nextType: ChallengeTypeKey) {
    const nextConfig = getChallengeTypeDefinition(nextType);
    setSelectedChallengeType(nextType);
    setSelectedMode(nextConfig.defaultModeKey);
    setOverviewError("");
  }

  function handleTeamDistributionModeChange(nextMode: TeamDistributionMode) {
    setTeamDistributionMode(nextMode);
    setTeamParticipants((current) =>
      nextMode === "shuffle"
        ? assignShuffledTeams(current)
        : normalizeUniqueParticipants(
            current.map((participant) => ({
              ...participant,
              team: null,
            })),
          ),
    );
    setOverviewError("");
  }

  function addTeamParticipant() {
    const cleanedName = normalizePlayerLabel(teamMemberDraft, "");
    if (!cleanedName) {
      return;
    }

    const duplicateExists = teamParticipants.some(
      (participant) => participant.name.trim().toLocaleLowerCase() === cleanedName.toLocaleLowerCase(),
    );
    if (duplicateExists) {
      setOverviewError("الاسم موجود من قبل.");
      return;
    }

    setTeamParticipants((current) => {
      const nextEntry: TeamParticipantDraft = {
        id: createClientId(),
        name: cleanedName,
        team: teamDistributionMode === "shuffle" ? 1 : null,
      };
      const nextValues = [...current, nextEntry];
      return teamDistributionMode === "shuffle" ? assignShuffledTeams(nextValues) : normalizeUniqueParticipants(nextValues);
    });
    setTeamMemberDraft("");
    setOverviewError("");
  }

  function reshuffleTeams() {
    setTeamParticipants((current) => assignShuffledTeams(current));
    setOverviewError("");
  }

  function removeTeamParticipant(participantId: string) {
    setTeamParticipants((current) => {
      const filtered = current.filter((participant) => participant.id !== participantId);
      return teamDistributionMode === "shuffle" ? assignShuffledTeams(filtered) : normalizeUniqueParticipants(filtered);
    });
    setOverviewError("");
  }

  function assignParticipantToTeam(participantId: string, team: TeamAssignment) {
    setTeamParticipants((current) =>
      normalizeUniqueParticipants(
        current.map((participant) =>
          participant.id === participantId
            ? {
                ...participant,
                team,
              }
            : participant,
        ),
      ),
    );
    setOverviewError("");
  }

  function buildMatchSetupSelection():
    | {
        seatNames: [string, string];
        meta: MatchSetupMeta;
        nextParticipants: TeamParticipantDraft[] | null;
      }
    | {
        error: string;
      } {
    if (selectedChallengeType === "teams") {
      const seatOneTitle = normalizePlayerLabel(teamOneName, selectedChallengeTypeConfig.seatPlaceholders[0]);
      const seatTwoTitle = normalizePlayerLabel(teamTwoName, selectedChallengeTypeConfig.seatPlaceholders[1]);
      const normalizedParticipants = normalizeUniqueParticipants(teamParticipants);
      if (normalizedParticipants.length < 2) {
        return { error: "ضيفوا اسمين على الأقل." };
      }

      const preparedParticipants =
        teamDistributionMode === "shuffle"
          ? assignShuffledTeams(normalizedParticipants)
          : normalizeUniqueParticipants(normalizedParticipants);
      const groupedParticipants = splitTeamParticipants(preparedParticipants);

      if (teamDistributionMode === "manual" && groupedParticipants.waiting.length) {
        return { error: "وزّعوا كل الأسماء أول." };
      }

      if (!groupedParticipants.teamOne.length || !groupedParticipants.teamTwo.length) {
        return { error: "لازم يكون فيه اسم في كل فريق." };
      }

      return {
        seatNames: [seatOneTitle, seatTwoTitle],
        nextParticipants: preparedParticipants,
        meta: {
          challengeType: selectedChallengeTypeConfig.key,
          challengeLabel: selectedChallengeTypeConfig.label,
          boardPrompt: selectedChallengeTypeConfig.boardPrompt,
          startPrompt: selectedChallengeTypeConfig.startPrompt,
          participantCount: preparedParticipants.length,
          teamDistributionMode,
          seatMeta: [
            {
              title: seatOneTitle,
              roleLabel: selectedChallengeTypeConfig.seatRoleLabels[0],
              members: groupedParticipants.teamOne.map((participant) => participant.name),
            },
            {
              title: seatTwoTitle,
              roleLabel: selectedChallengeTypeConfig.seatRoleLabels[1],
              members: groupedParticipants.teamTwo.map((participant) => participant.name),
            },
          ],
        },
      };
    }

    const currentNames =
      selectedChallengeType === "one-explains"
        ? [
            normalizePlayerLabel(speakerOneName, selectedChallengeTypeConfig.seatPlaceholders[0]),
            normalizePlayerLabel(speakerTwoName, selectedChallengeTypeConfig.seatPlaceholders[1]),
          ]
        : [
            normalizePlayerLabel(playerOneName, selectedChallengeTypeConfig.seatPlaceholders[0]),
            normalizePlayerLabel(playerTwoName, selectedChallengeTypeConfig.seatPlaceholders[1]),
          ];

    return {
      seatNames: [currentNames[0], currentNames[1]],
      nextParticipants: null,
      meta: {
        challengeType: selectedChallengeTypeConfig.key,
        challengeLabel: selectedChallengeTypeConfig.label,
        boardPrompt: selectedChallengeTypeConfig.boardPrompt,
        startPrompt: selectedChallengeTypeConfig.startPrompt,
        participantCount: 2,
        teamDistributionMode: null,
        seatMeta: [
          {
            title: currentNames[0],
            roleLabel: selectedChallengeTypeConfig.seatRoleLabels[0],
            members: [currentNames[0]],
          },
          {
            title: currentNames[1],
            roleLabel: selectedChallengeTypeConfig.seatRoleLabels[1],
            members: [currentNames[1]],
          },
        ],
      },
    };
  }

  function randomizeTwists() {
    if (!overview) {
      return;
    }

    const nextSelection = buildRandomTwistSelection(selectedDifficulty, overview.question_categories);
    setSelectedAnswerRuleKeys(nextSelection.answerRuleKeys);
    setSelectedProhibitedCategoryKeys(nextSelection.prohibitedCategoryKeys);
  }

  function clearTwists() {
    setSelectedAnswerRuleKeys([]);
    setSelectedProhibitedCategoryKeys([]);
  }

  function toggleAnswerRule(key: AnswerRuleKey) {
    setSelectedAnswerRuleKeys((current) =>
      current.includes(key) ? current.filter((entry) => entry !== key) : [...current, key],
    );
  }

  function toggleBlockedCategory(key: QuestionCategoryKey) {
    setSelectedProhibitedCategoryKeys((current) =>
      current.includes(key) ? current.filter((entry) => entry !== key) : [...current, key],
    );
  }

  async function login(username: string, password: string) {
    setBusyAction("login");
    try {
      const payload = await api.login({ username, password });
      setSession(payload);
      setSessionChecked(true);
      setOverviewError("");
      setStatusMessage("تم");
    } catch (error) {
      setOverviewError(error instanceof ApiError ? error.message : "ما ضبط");
    } finally {
      setBusyAction(null);
    }
  }

  async function logout() {
    setBusyAction("logout");
    try {
      await api.logout();
    } finally {
      setSession({
        authenticated: false,
        username: null,
      });
      setSessionChecked(true);
      setBusyAction(null);
    }
  }

  async function startMatch() {
    if (actionLockRef.current === "create") {
      return;
    }

    actionLockRef.current = "create";
    const setupSelection = buildMatchSetupSelection();
    if ("error" in setupSelection) {
      actionLockRef.current = null;
      setStatusMessage(setupSelection.error);
      setOverviewError(setupSelection.error);
      return;
    }

    setBusyAction("create");
    try {
      if (setupSelection.nextParticipants) {
        setTeamParticipants(setupSelection.nextParticipants);
      }

      const payload = await api.createMatch({
        difficulty: selectedDifficulty,
        mode_key: selectedMode,
        player_names: setupSelection.seatNames,
        recent_player_ids: [],
        selected_answer_rule_keys: selectedAnswerRuleKeys,
        selected_prohibited_category_keys: selectedProhibitedCategoryKeys,
      });
      setOverviewError("");
      syncMatchPayload(payload);
      setMatchSetupMeta(setupSelection.meta);
      saveMatchSetupMeta(payload.match_id, setupSelection.meta);
      setStatusMessage("يلا");
      navigate({ name: "lobby", matchId: payload.match_id });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "ما ضبط";
      setStatusMessage(message);
      setOverviewError(message);
    } finally {
      actionLockRef.current = null;
      setBusyAction(null);
    }
  }

  async function awardRound(seat: number) {
    if (!match) {
      return;
    }

    if (
      actionLockRef.current === "award"
      || actionLockRef.current === "no-answer"
      || match.round.resolved
      || match.status === "completed"
    ) {
      return;
    }

    actionLockRef.current = "award";
    setBusyAction("award");
    try {
      const payload = await api.awardRound(
        match.match_id,
        { seat },
        readCurrentMatchToken(match.match_id, match.match_token),
      );
      setOverviewError("");
      syncMatchPayload(payload);
      const winner = seatByNumber(payload.seats, seat);
      setStatusMessage(
        payload.status === "completed"
          ? `${winner?.player_name ?? "لاعب"} كسب`
          : `${winner?.player_name ?? "لاعب"} أخذها`,
      );
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "ما ضبط";
      setStatusMessage(message);
      setOverviewError(message);
    } finally {
      actionLockRef.current = null;
      setBusyAction(null);
    }
  }

  async function markRoundUnanswered() {
    if (!match) {
      return;
    }

    if (
      actionLockRef.current === "award"
      || actionLockRef.current === "no-answer"
      || match.round.resolved
      || match.status === "completed"
    ) {
      return;
    }

    actionLockRef.current = "no-answer";
    setBusyAction("no-answer");
    try {
      const payload = await api.noAnswerRound(
        match.match_id,
        readCurrentMatchToken(match.match_id, match.match_token),
      );
      setOverviewError("");
      syncMatchPayload(payload);
      setStatusMessage("ولا حد جاوب");
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "ما ضبط";
      setStatusMessage(message);
      setOverviewError(message);
    } finally {
      actionLockRef.current = null;
      setBusyAction(null);
    }
  }

  async function createNextRound() {
    if (!match) {
      return;
    }

    if (
      actionLockRef.current === "next-round"
      || match.status === "completed"
      || !match.round.resolved
    ) {
      return;
    }

    actionLockRef.current = "next-round";
    setBusyAction("next-round");
    try {
      const awardedSeat = match.round.awarded_to ?? awardedSeatRef.current;
      const roundResolved = match.round.resolved || roundResolvedRef.current;
      let payload: MatchRead;

      try {
        payload = await api.nextRound(
          match.match_id,
          readCurrentMatchToken(match.match_id, match.match_token),
        );
      } catch (error) {
        const shouldRepairAward =
          error instanceof ApiError
          && error.status === 400
          && error.message.includes("حدد مين خذها أول")
          && awardedSeat !== null;
        const shouldRepairNoAnswer =
          error instanceof ApiError
          && error.status === 400
          && error.message.includes("حدد مين خذها أول")
          && roundResolved
          && awardedSeat === null;

        if (!shouldRepairAward && !shouldRepairNoAnswer) {
          throw error;
        }

        const repairedMatch = shouldRepairAward
          ? await api.awardRound(
            match.match_id,
            { seat: awardedSeat },
            readCurrentMatchToken(match.match_id, match.match_token),
          )
          : await api.noAnswerRound(
            match.match_id,
            readCurrentMatchToken(match.match_id, match.match_token),
          );
        syncMatchPayload(repairedMatch);

        payload = await api.nextRound(
          repairedMatch.match_id,
          readCurrentMatchToken(repairedMatch.match_id, repairedMatch.match_token),
        );
      }
      setOverviewError("");
      syncMatchPayload(payload);
      setSecretLinks({});
      setStatusMessage("جولة جديدة");
      navigate({ name: "lobby", matchId: payload.match_id });
      return;
      /*
        const seatIds = getMatchSeatIds(payload);
        const currentPair = buildSeatPairKey(payload);
        const repeated =
          hasRepeatedSeats(payload, [...state.seenPlayerIds]).repeated ||
          state.pairs.has(currentPair);

        localToken = payload.match_token;
        if (repeated) {
          state.attempts += 1;
          attempts += 1;
          seenMatchPairsRef.current[match.match_id] = state;

          if (attempts >= maxAttempts) {
            throw new Error("Could not find a unique pair after many retries.");
          }

          continue;
        }

        seatIds.forEach((seatId) => {
          if (seatId > 0) {
            state.seenPlayerIds.add(seatId);
          }
        });
        state.pairs.add(currentPair);
        state.attempts = 0;
        seenMatchPairsRef.current[match.match_id] = state;

        setOverviewError("");
        setMatch(payload);
        setMatchToken(payload.match_token);
        saveMatchToken(payload.match_id, payload.match_token);
        setSecretLinks({});
        setStatusMessage("جولة جديدة");
        navigate({ name: "lobby", matchId: payload.match_id });
        return;
      }

      throw new Error("Could not find a unique pair after many retries.");
      */
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : "حدث خطأ.";
      setStatusMessage(message);
      setOverviewError(message);
    } finally {
      actionLockRef.current = null;
      setBusyAction(null);
      return;
    }
  }

    /* const payload = await api.nextRound(match.match_id, matchToken || match.match_token);
      setOverviewError("");
      setMatch(payload);
      setMatchToken(payload.match_token);
      saveMatchToken(payload.match_id, payload.match_token);
      setSecretLinks({});
      setStatusMessage("جولة جديدة");
      navigate({ name: "lobby", matchId: payload.match_id });
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "ما ضبط";
      setStatusMessage(message);
      setOverviewError(message);
    } finally {
      setBusyAction(null);
    }
  }

    */

  async function endMatchNow() {
    if (!match) {
      endingMatchIdRef.current = null;
      setRoute({ name: "home" });
      navigate({ name: "home" });
      return;
    }

    if (actionLockRef.current === "end") {
      return;
    }

    actionLockRef.current = "end";
    setBusyAction("end");
    const endingMatchId = match.match_id;
    const endingMatchToken = readCurrentMatchToken(match.match_id, match.match_token);
    endingMatchIdRef.current = endingMatchId;
    clearSavedMatchToken(endingMatchId);
    clearSavedMatchSetupMeta(endingMatchId);
    clearCurrentMatch();
    setMatchSetupMeta(null);
    setSecretLinks({});
    setOverviewError("");
    setStatusMessage("Game ended");
    setRoute({ name: "home" });
    navigate({ name: "home" });
    try {
      await api.endMatch(
        endingMatchId,
        endingMatchToken,
      );
    } catch (error) {
      const message = error instanceof ApiError ? error.message : "Something went wrong.";
      setStatusMessage(message);
      setOverviewError(message);
    } finally {
      actionLockRef.current = null;
      setBusyAction(null);
    }
  }

  function copyToClipboard(value: string) {
    setBusyAction("copy");
    void navigator.clipboard
      .writeText(value)
      .then(() => {
        setStatusMessage("اننسخ");
      })
      .catch(() => {
        setStatusMessage("ما اننسخ");
      })
      .finally(() => setBusyAction(null));
  }

  function saveShareSettings(value: string) {
    const normalized = normalizePublicMinuUrl(value);
    setShareBaseUrl(normalized);
    localStorage.setItem(SHARE_URL_KEY, normalized);
    setStatusMessage("انحفظ");
  }

  if (route.name === "card") {
    return <PublicCardScreen payload={route.payload} />;
  }

  if (!sessionChecked) {
    return (
      <main className="flow-screen">
        <section className="flow-panel auth-panel auth-panel--loading">
          <BrandMark compact />
        </section>
      </main>
    );
  }

  if (!session.authenticated) {
    return (
      <LoginScreen
        busyAction={busyAction}
        errorMessage={overviewError}
        onLogin={(username, password) => void login(username, password)}
      />
    );
  }

  return (
    <div className="app-shell" dir="rtl">
      {visibleRoute.name === "home" ? (
        <HomeScreen onLogout={() => void logout()} onNavigate={navigate} username={session.username} />
      ) : null}

      {visibleRoute.name === "admin" ? (
        <AdminScreen
          onBack={() => navigate({ name: "home" })}
          onLogout={() => void logout()}
          onSaveShareUrl={saveShareSettings}
          shareBaseUrl={effectiveShareBaseUrl || shareBaseUrl}
          username={session.username}
        />
      ) : null}

      {visibleRoute.name === "setup" ? (
        <SetupScreen
          busyAction={busyAction}
          challengeDefinition={selectedChallengeTypeConfig}
          challengeType={selectedChallengeType}
          modeDefinition={selectedModeConfig}
          onAddTeamParticipant={addTeamParticipant}
          onAssignParticipantToTeam={assignParticipantToTeam}
          onBack={() => navigate({ name: "home" })}
          onChallengeTypeChange={handleChallengeTypeChange}
          onClearTwists={clearTwists}
          onModeChange={setSelectedMode}
          onPlayerOneNameChange={setPlayerOneName}
          onPlayerTwoNameChange={setPlayerTwoName}
          onRandomizeTwists={randomizeTwists}
          onRemoveTeamParticipant={removeTeamParticipant}
          onReshuffleTeams={reshuffleTeams}
          onSpeakerOneNameChange={setSpeakerOneName}
          onSpeakerTwoNameChange={setSpeakerTwoName}
          onStartMatch={() => void startMatch()}
          onTeamDistributionModeChange={handleTeamDistributionModeChange}
          onTeamMemberDraftChange={setTeamMemberDraft}
          onTeamOneNameChange={setTeamOneName}
          onTeamTwoNameChange={setTeamTwoName}
          onToggleAnswerRule={toggleAnswerRule}
          onToggleBlockedCategory={toggleBlockedCategory}
          overview={overview}
          playerOneName={playerOneName}
          playerTwoName={playerTwoName}
          speakerOneName={speakerOneName}
          speakerTwoName={speakerTwoName}
          selectedAnswerRuleKeys={selectedAnswerRuleKeys}
          selectedChallengeType={selectedChallengeType}
          selectedDifficulty={selectedDifficulty}
          selectedMode={selectedMode}
          selectedProhibitedCategoryKeys={selectedProhibitedCategoryKeys}
          setSelectedDifficulty={setSelectedDifficulty}
          teamDistributionMode={teamDistributionMode}
          teamMemberDraft={teamMemberDraft}
          teamOneName={teamOneName}
          teamParticipants={teamParticipants}
          teamTwoName={teamTwoName}
        />
      ) : null}

      {visibleRoute.name === "settings" ? (
        <SettingsScreen
          onBack={() =>
            match?.status === "active"
              ? navigate({ name: "lobby", matchId: match.match_id })
              : navigate({ name: "home" })
          }
          onLogout={() => void logout()}
          onSave={saveShareSettings}
          shareBaseUrl={effectiveShareBaseUrl || shareBaseUrl}
          username={session.username}
        />
      ) : null}

      {visibleRoute.name === "credits" ? <CreditsScreen onBack={() => navigate({ name: "home" })} /> : null}

      {visibleRoute.name === "lobby" ? (
        <LobbyScreen
          busyAction={busyAction}
          copyToClipboard={copyToClipboard}
          isPublicReady={Boolean(effectiveShareBaseUrl)}
          match={match}
          matchSetupMeta={matchSetupMeta}
          onEndGameNow={() => void endMatchNow()}
          onOpenGameBoard={() => navigate({ name: "game", matchId: visibleRoute.matchId })}
          onOpenSettings={() => navigate({ name: "settings" })}
          secretLinks={secretLinks}
        />
      ) : null}

      {visibleRoute.name === "game" ? (
        <GameBoardScreen
          busyAction={busyAction}
          match={match}
          matchSetupMeta={matchSetupMeta}
          onAwardRound={(seat) => void awardRound(seat)}
          onMarkNoAnswer={() => void markRoundUnanswered()}
          onNextRound={() => void createNextRound()}
          overview={overview}
        />
      ) : null}

      {overviewError ? <div className="floating-error">{overviewError}</div> : null}
    </div>
  );
}

function buildSecretLinks(
  shareBaseUrl: string,
  seatOneToken: string,
  seatTwoToken: string,
) {
  const normalizeBase = normalizePublicMinuUrl(shareBaseUrl);

  return {
    1: buildSecretLinkEntry(normalizeBase, seatOneToken),
    2: buildSecretLinkEntry(normalizeBase, seatTwoToken),
  };
}

function buildSecretLinkEntry(baseUrl: string, token: string) {
  const url = `${normalizePublicMinuUrl(baseUrl)}/card/${token}`;
  const qrUrl = getQrCodeUrl(url);
  return { url, qrUrl };
}

function LoginScreen({
  busyAction,
  errorMessage,
  onLogin,
}: {
  busyAction: string | null;
  errorMessage: string;
  onLogin: (username: string, password: string) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  return (
    <main className="flow-screen auth-screen" dir="rtl">
      <section className="flow-panel auth-panel">
        <BrandMark className="auth-brand" />
        <form
          autoComplete="off"
          className="auth-form"
          onSubmit={(event) => {
            event.preventDefault();
            onLogin(username, password);
          }}
        >
          <input
            autoCapitalize="none"
            autoComplete="off"
            autoCorrect="off"
            className="input auth-input"
            onChange={(event) => setUsername(event.target.value)}
            placeholder="اسم المستخدم"
            spellCheck={false}
            value={username}
          />
          <input
            autoComplete="new-password"
            className="input auth-input"
            onChange={(event) => setPassword(event.target.value)}
            placeholder="كلمة المرور"
            type="password"
            value={password}
          />
          <button className="primary-button auth-submit" disabled={busyAction === "login"} type="submit">
            دخول
          </button>
          {errorMessage ? <div className="auth-error">{errorMessage}</div> : null}
        </form>
      </section>
    </main>
  );
}

function HomeScreen({
  onLogout,
  onNavigate,
  username,
}: {
  onLogout: () => void;
  onNavigate: (route: AppRoute) => void;
  username: string | null;
}) {
  return (
    <main className="home-screen">
      <section className="home-hero">
        <div className="home-topline">
          <span>{username || "minu-admin"}</span>
          <button className="ghost-button" onClick={onLogout} type="button">
            خروج
          </button>
        </div>
        <BrandMark className="home-brand" />
        <div className="home-actions">
          <button className="primary-button" onClick={() => onNavigate({ name: "setup" })} type="button">
            ابدأ
          </button>
          <button className="secondary-button" onClick={() => onNavigate({ name: "admin" })} type="button">
            الأدمن
          </button>
          <button className="secondary-button" onClick={() => onNavigate({ name: "credits" })} type="button">
            شحن
          </button>
          <button className="secondary-button" onClick={() => onNavigate({ name: "settings" })} type="button">
            ضبط
          </button>
        </div>
      </section>
    </main>
  );
}

function AdminScreen({
  onBack,
  onLogout,
  onSaveShareUrl,
  shareBaseUrl,
  username,
}: {
  onBack: () => void;
  onLogout: () => void;
  onSaveShareUrl: (value: string) => void;
  shareBaseUrl: string;
  username: string | null;
}) {
  const pageSize = 24;
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [playersPage, setPlayersPage] = useState<AdminPlayersPage | null>(null);
  const [overviewBusy, setOverviewBusy] = useState(true);
  const [playersBusy, setPlayersBusy] = useState(true);
  const [overviewError, setOverviewError] = useState("");
  const [playersError, setPlayersError] = useState("");
  const [shareDraft, setShareDraft] = useState(shareBaseUrl);
  const [searchDraft, setSearchDraft] = useState("");
  const [filters, setFilters] = useState<{
    q: string;
    difficulty: number | "all";
    active: "all" | "active" | "retired";
    offset: number;
  }>({
    q: "",
    difficulty: "all",
    active: "all",
    offset: 0,
  });
  const [playerForm, setPlayerForm] = useState<AdminPlayerFormState>(() => emptyAdminPlayerForm());
  const [editingPlayerId, setEditingPlayerId] = useState<number | null>(null);
  const [mutationBusy, setMutationBusy] = useState<"save" | "delete" | "refresh" | null>(null);
  const [mutationError, setMutationError] = useState("");
  const [mutationMessage, setMutationMessage] = useState("");
  const [assistantQuestions, setAssistantQuestions] = useState<AdminAssistantQuestion[]>([]);
  const [assistantCompetitions, setAssistantCompetitions] = useState<AdminAssistantCompetition[]>([]);
  const [assistantCatalogBusy, setAssistantCatalogBusy] = useState(true);
  const [assistantCatalogError, setAssistantCatalogError] = useState("");
  const [assistantQuestionForm, setAssistantQuestionForm] = useState<AdminAssistantQuestionFormState>(() =>
    emptyAdminAssistantQuestionForm(),
  );
  const [editingAssistantQuestionId, setEditingAssistantQuestionId] = useState<number | null>(null);
  const [assistantQuestionBusy, setAssistantQuestionBusy] = useState<"save" | "delete" | null>(null);
  const [assistantQuestionError, setAssistantQuestionError] = useState("");
  const [assistantQuestionMessage, setAssistantQuestionMessage] = useState("");
  const [assistantCompetitionForm, setAssistantCompetitionForm] = useState<AdminAssistantCompetitionFormState>(() =>
    emptyAdminAssistantCompetitionForm(),
  );
  const [editingAssistantCompetitionId, setEditingAssistantCompetitionId] = useState<number | null>(null);
  const [assistantCompetitionBusy, setAssistantCompetitionBusy] = useState<"save" | "delete" | null>(null);
  const [assistantCompetitionError, setAssistantCompetitionError] = useState("");
  const [assistantCompetitionMessage, setAssistantCompetitionMessage] = useState("");

  useEffect(() => {
    setShareDraft(shareBaseUrl);
  }, [shareBaseUrl]);

  function updatePlayerForm<Key extends keyof AdminPlayerFormState>(
    key: Key,
    value: AdminPlayerFormState[Key],
  ) {
    setPlayerForm((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function updateAssistantQuestionForm<Key extends keyof AdminAssistantQuestionFormState>(
    key: Key,
    value: AdminAssistantQuestionFormState[Key],
  ) {
    setAssistantQuestionForm((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function updateAssistantCompetitionForm<Key extends keyof AdminAssistantCompetitionFormState>(
    key: Key,
    value: AdminAssistantCompetitionFormState[Key],
  ) {
    setAssistantCompetitionForm((current) => ({
      ...current,
      [key]: value,
    }));
  }

  async function loadOverview() {
    setOverviewBusy(true);
    try {
      const payload = await api.getAdminOverview();
      setOverview(payload);
      setOverviewError("");
    } catch (error) {
      setOverviewError(error instanceof ApiError ? error.message : "ما ضبط التحميل.");
    } finally {
      setOverviewBusy(false);
    }
  }

  useEffect(() => {
    void loadOverview();
  }, []);

  async function loadAssistantCatalog() {
    setAssistantCatalogBusy(true);
    try {
      const [questionsPayload, competitionsPayload] = await Promise.all([
        api.getAdminAssistantQuestions(),
        api.getAdminAssistantCompetitions(),
      ]);
      setAssistantQuestions(questionsPayload.items);
      setAssistantCompetitions(competitionsPayload.items);
      setAssistantCatalogError("");
    } catch (error) {
      setAssistantCatalogError(error instanceof ApiError ? error.message : "ما ضبط تحميل بيانات المساعد.");
    } finally {
      setAssistantCatalogBusy(false);
    }
  }

  useEffect(() => {
    void loadAssistantCatalog();
  }, []);

  async function loadPlayers(nextFilters = filters) {
    setPlayersBusy(true);
    try {
      const payload = await api.getAdminPlayers({
        q: nextFilters.q,
        difficulty: nextFilters.difficulty,
        active: nextFilters.active,
        offset: nextFilters.offset,
        limit: pageSize,
      });
      setPlayersPage(payload);
      setPlayersError("");
    } catch (error) {
      setPlayersError(error instanceof ApiError ? error.message : "ما ضبط التحميل.");
    } finally {
      setPlayersBusy(false);
    }
  }

  useEffect(() => {
    void loadPlayers(filters);
    return;
    let active = true;
    setPlayersBusy(true);

    void api
      .getAdminPlayers({
        q: filters.q,
        difficulty: filters.difficulty,
        active: filters.active,
        offset: filters.offset,
        limit: pageSize,
      })
      .then((payload) => {
        if (!active) {
          return;
        }
        setPlayersPage(payload);
        setPlayersError("");
      })
      .catch((error) => {
        if (!active) {
          return;
        }
        setPlayersError(error instanceof ApiError ? error.message : "ما ضبط التحميل.");
      })
      .finally(() => {
        if (active) {
          setPlayersBusy(false);
        }
      });

    return () => {
      active = false;
    };
  }, [filters]);

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFilters((current) => ({
      ...current,
      q: searchDraft.trim(),
      offset: 0,
    }));
  }

  function resetFilters() {
    setSearchDraft("");
    setFilters({
      q: "",
      difficulty: "all",
      active: "all",
      offset: 0,
    });
  }

  function statusLabelForPlayer(player: AdminPlayer): string {
    return player.is_active ? "للحين يلعب" : "معتزل";
  }

  function resetEditor() {
    setEditingPlayerId(null);
    setPlayerForm(emptyAdminPlayerForm());
    setMutationError("");
    setMutationMessage("");
  }

  function resetAssistantQuestionEditor() {
    setEditingAssistantQuestionId(null);
    setAssistantQuestionForm(emptyAdminAssistantQuestionForm());
    setAssistantQuestionError("");
    setAssistantQuestionMessage("");
  }

  function resetAssistantCompetitionEditor() {
    setEditingAssistantCompetitionId(null);
    setAssistantCompetitionForm(emptyAdminAssistantCompetitionForm());
    setAssistantCompetitionError("");
    setAssistantCompetitionMessage("");
  }

  function startEditing(player: AdminPlayer) {
    setEditingPlayerId(player.id);
    setPlayerForm(adminPlayerToFormState(player));
    setMutationError("");
    setMutationMessage("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function startEditingAssistantQuestion(item: AdminAssistantQuestion) {
    setEditingAssistantQuestionId(item.id);
    setAssistantQuestionForm(adminAssistantQuestionToFormState(item));
    setAssistantQuestionError("");
    setAssistantQuestionMessage("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function startEditingAssistantCompetition(item: AdminAssistantCompetition) {
    setEditingAssistantCompetitionId(item.id);
    setAssistantCompetitionForm(adminAssistantCompetitionToFormState(item));
    setAssistantCompetitionError("");
    setAssistantCompetitionMessage("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function savePlayer() {
    setMutationBusy("save");
    setMutationError("");
    setMutationMessage("");
    try {
      const payload = adminFormToPayload(playerForm);
      if (editingPlayerId) {
        await api.updateAdminPlayer(editingPlayerId, payload);
        setMutationMessage("تم تحديث اللاعب");
      } else {
        await api.createAdminPlayer(payload);
        setMutationMessage("تمت إضافة اللاعب");
        setPlayerForm(emptyAdminPlayerForm());
      }
      await Promise.all([loadOverview(), loadPlayers(filters)]);
    } catch (error) {
      setMutationError(error instanceof ApiError ? error.message : "ما ضبط الحفظ.");
    } finally {
      setMutationBusy(null);
    }
  }

  async function removePlayer(player: AdminPlayer) {
    if (!window.confirm(`تحذف ${player.name_ar || player.name}؟`)) {
      return;
    }

    setMutationBusy("delete");
    setMutationError("");
    setMutationMessage("");
    try {
      await api.deleteAdminPlayer(player.id);
      if (editingPlayerId === player.id) {
        resetEditor();
      }
      setMutationMessage("تم حذف اللاعب");
      await Promise.all([loadOverview(), loadPlayers(filters)]);
    } catch (error) {
      setMutationError(error instanceof ApiError ? error.message : "ما ضبط الحذف.");
    } finally {
      setMutationBusy(null);
    }
  }

  async function refreshCatalog() {
    setMutationBusy("refresh");
    setMutationError("");
    setMutationMessage("");
    try {
      const result = await api.refreshAdminCatalog();
      setMutationMessage(
        `تم الفحص: ${result.updated_players} تحديث، ${result.removed_players} حذف، ${result.locked_players} مقفول`,
      );
      await Promise.all([loadOverview(), loadPlayers(filters)]);
    } catch (error) {
      setMutationError(error instanceof ApiError ? error.message : "ما ضبط التحديث.");
    } finally {
      setMutationBusy(null);
    }
  }

  async function saveAssistantQuestion() {
    setAssistantQuestionBusy("save");
    setAssistantQuestionError("");
    setAssistantQuestionMessage("");
    try {
      const payload = adminAssistantQuestionFormToPayload(assistantQuestionForm);
      if (editingAssistantQuestionId) {
        await api.updateAdminAssistantQuestion(editingAssistantQuestionId, payload);
        setAssistantQuestionMessage("تم تحديث سؤال المساعد.");
      } else {
        await api.createAdminAssistantQuestion(payload);
        setAssistantQuestionMessage("تمت إضافة سؤال جديد للمساعد.");
        setAssistantQuestionForm(emptyAdminAssistantQuestionForm());
      }
      await loadAssistantCatalog();
    } catch (error) {
      setAssistantQuestionError(error instanceof ApiError ? error.message : "ما ضبط حفظ السؤال.");
    } finally {
      setAssistantQuestionBusy(null);
    }
  }

  async function removeAssistantQuestion(item: AdminAssistantQuestion) {
    if (!window.confirm(`تحذف سؤال "${item.question_ar}"؟`)) {
      return;
    }

    setAssistantQuestionBusy("delete");
    setAssistantQuestionError("");
    setAssistantQuestionMessage("");
    try {
      await api.deleteAdminAssistantQuestion(item.id);
      if (editingAssistantQuestionId === item.id) {
        resetAssistantQuestionEditor();
      }
      setAssistantQuestionMessage("تم حذف سؤال المساعد.");
      await loadAssistantCatalog();
    } catch (error) {
      setAssistantQuestionError(error instanceof ApiError ? error.message : "ما ضبط حذف السؤال.");
    } finally {
      setAssistantQuestionBusy(null);
    }
  }

  async function saveAssistantCompetition() {
    setAssistantCompetitionBusy("save");
    setAssistantCompetitionError("");
    setAssistantCompetitionMessage("");
    try {
      const payload = adminAssistantCompetitionFormToPayload(assistantCompetitionForm);
      if (editingAssistantCompetitionId) {
        await api.updateAdminAssistantCompetition(editingAssistantCompetitionId, payload);
        setAssistantCompetitionMessage("تم تحديث الدوري أو المسابقة.");
      } else {
        await api.createAdminAssistantCompetition(payload);
        setAssistantCompetitionMessage("تمت إضافة دوري أو مسابقة جديدة.");
        setAssistantCompetitionForm(emptyAdminAssistantCompetitionForm());
      }
      await loadAssistantCatalog();
    } catch (error) {
      setAssistantCompetitionError(error instanceof ApiError ? error.message : "ما ضبط حفظ الدوري.");
    } finally {
      setAssistantCompetitionBusy(null);
    }
  }

  async function removeAssistantCompetition(item: AdminAssistantCompetition) {
    if (!window.confirm(`تحذف "${item.name_ar}"؟`)) {
      return;
    }

    setAssistantCompetitionBusy("delete");
    setAssistantCompetitionError("");
    setAssistantCompetitionMessage("");
    try {
      await api.deleteAdminAssistantCompetition(item.id);
      if (editingAssistantCompetitionId === item.id) {
        resetAssistantCompetitionEditor();
      }
      setAssistantCompetitionMessage("تم حذف الدوري أو المسابقة.");
      await loadAssistantCatalog();
    } catch (error) {
      setAssistantCompetitionError(error instanceof ApiError ? error.message : "ما ضبط حذف الدوري.");
    } finally {
      setAssistantCompetitionBusy(null);
    }
  }

  const hasNextPage = Boolean(
    playersPage && playersPage.offset + playersPage.limit < playersPage.total,
  );
  const lastCatalogRefresh: AdminCatalogRefresh | null = overview?.catalog_refresh ?? null;

  return (
    <main className="flow-screen">
      <section className="flow-panel flow-panel--wide admin-panel">
        <div className="flow-header">
          <div className="header-actions">
            <button className="ghost-button" onClick={onBack} type="button">
              ارجع
            </button>
            <button className="ghost-button" onClick={() => void loadOverview()} type="button">
              تحديث
            </button>
          </div>
          <div className="flow-title-group">
            <BrandMark compact />
            <div className="flow-title-copy">
              <h2>لوحة الأدمن</h2>
            </div>
          </div>
        </div>

        <div className="admin-stats-grid">
          <article className="admin-stat-card">
            <small>اللاعبين</small>
            <strong>{overview ? formatCount(overview.total_players) : "..."}</strong>
          </article>
          <article className="admin-stat-card">
            <small>نشط</small>
            <strong>{overview ? formatCount(overview.active_players) : "..."}</strong>
          </article>
          <article className="admin-stat-card">
            <small>معتزل</small>
            <strong>{overview ? formatCount(overview.retired_players) : "..."}</strong>
          </article>
          <article className="admin-stat-card">
            <small>بلدان</small>
            <strong>{overview ? formatCount(overview.represented_countries) : "..."}</strong>
          </article>
          <article className="admin-stat-card">
            <small>مباريات شغالة</small>
            <strong>{overview ? formatCount(overview.active_matches) : "..."}</strong>
          </article>
          <article className="admin-stat-card">
            <small>الصور</small>
            <strong>{overview ? formatCount(overview.players_with_images) : "..."}</strong>
          </article>
        </div>

        <div className="admin-overview-grid">
          <article className="admin-card">
            <div className="admin-card__head">
              <strong>الحساب والرابط</strong>
            </div>
            <div className="settings-account">
              <strong>{username || "minu-admin"}</strong>
              <button className="ghost-button" onClick={onLogout} type="button">
                خروج
              </button>
            </div>
            <input
              className="input"
              onChange={(event) => setShareDraft(event.target.value)}
              placeholder="الرابط العام"
              value={shareDraft}
            />
            <div className="admin-inline-actions">
              <button className="primary-button" onClick={() => onSaveShareUrl(shareDraft)} type="button">
                حفظ الرابط
              </button>
            </div>
            <div className="admin-info-list">
              <div className="admin-info-row">
                <span>الرابط الحالي</span>
                <strong>{overview?.runtime.public_base_url || shareBaseUrl || "مو ظاهر"}</strong>
              </div>
              <div className="admin-info-row">
                <span>الكوكي</span>
                <strong>{overview?.runtime.session_cookie_name || "..."}</strong>
              </div>
              <div className="admin-info-row">
                <span>الجلسة</span>
                <strong>{overview ? `${overview.runtime.session_ttl_hours} ساعة` : "..."}</strong>
              </div>
              <div className="admin-info-row">
                <span>رابط البطاقة</span>
                <strong>{overview ? `${overview.runtime.card_link_ttl_hours} ساعة` : "..."}</strong>
              </div>
            </div>
          </article>

          <article className="admin-card">
            <div className="admin-card__head">
              <strong>البيانات</strong>
            </div>
            {overviewBusy && !overview ? <div className="admin-empty">...</div> : null}
            {overviewError ? <div className="auth-error">{overviewError}</div> : null}
            {overview ? (
              <div className="admin-info-list">
                <div className="admin-info-row">
                  <span>القارات</span>
                  <strong>{formatCount(overview.represented_continents)}</strong>
                </div>
                <div className="admin-info-row">
                  <span>أسماء عربي</span>
                  <strong>{formatCount(overview.players_with_arabic_names)}</strong>
                </div>
                <div className="admin-info-row">
                  <span>إجمالي المباريات</span>
                  <strong>{formatCount(overview.total_matches)}</strong>
                </div>
                <div className="admin-info-row">
                  <span>مباريات مخلصة</span>
                  <strong>{formatCount(overview.completed_matches)}</strong>
                </div>
                <div className="admin-info-row">
                  <span>حجم القاعدة</span>
                  <strong>{formatBytes(overview.runtime.database_size_bytes)}</strong>
                </div>
                <div className="admin-info-row admin-info-row--stack">
                  <span>المجلد الرئيسي</span>
                  <strong>{overview.runtime.runtime_root}</strong>
                </div>
                <div className="admin-info-row admin-info-row--stack">
                  <span>مجلد البيانات</span>
                  <strong>{overview.runtime.data_dir}</strong>
                </div>
                <div className="admin-info-row admin-info-row--stack">
                  <span>قاعدة البيانات</span>
                  <strong>{overview.runtime.database_path}</strong>
                </div>
                <div className="admin-info-row admin-info-row--stack">
                  <span>ملف اللاعبين</span>
                  <strong>{overview.runtime.dataset_path}</strong>
                </div>
                <div className="admin-info-row admin-info-row--stack">
                  <span>ملف الدخول</span>
                  <strong>{overview.runtime.credentials_file_path}</strong>
                </div>
                <div className="admin-info-row admin-info-row--stack">
                  <span>ملف الحماية</span>
                  <strong>{overview.runtime.secret_file_path}</strong>
                </div>
              </div>
            ) : null}
          </article>

          <article className="admin-card">
            <div className="admin-card__head">
              <strong>تقسيم اللفلات</strong>
            </div>
            <div className="admin-difficulty-grid">
              {overview?.difficulty_stats.map((entry) => (
                <article className="admin-difficulty-card" key={entry.level}>
                  <div className="admin-difficulty-card__top">
                    <strong>{entry.label}</strong>
                    <span>{formatCount(entry.player_count)}</span>
                  </div>
                  <small>{entry.description}</small>
                  <div className="admin-difficulty-card__meta">
                    <span>{`من ${entry.fame_min}`}</span>
                    <span>{`إلى ${entry.fame_max}`}</span>
                  </div>
                </article>
              ))}
            </div>
          </article>

          <article className="admin-card">
            <div className="admin-card__head">
              <strong>مراقبة المباريات</strong>
            </div>
            {overview?.recent_matches.length ? (
              <div className="admin-match-list">
                {overview.recent_matches.map((matchEntry) => (
                  <article className="admin-match-card" key={matchEntry.match_id}>
                    <div className="admin-match-card__top">
                      <strong>{getModeDefinition(matchEntry.mode_key).label}</strong>
                      <span>{matchEntry.status === "active" ? "شغالة" : "مخلصة"}</span>
                    </div>
                    <div className="admin-match-card__meta">
                      <span>{`#${shortId(matchEntry.match_id)}`}</span>
                      <span>{`${matchEntry.difficulty_label} · جولة ${matchEntry.round_number}`}</span>
                    </div>
                    <div className="admin-match-card__seats">
                      {matchEntry.seats.map((seat) => (
                        <div className="admin-match-seat" key={`${matchEntry.match_id}-${seat.seat}`}>
                          <strong>{seat.player_name}</strong>
                          <span>{`${seat.score} نقطة`}</span>
                        </div>
                      ))}
                    </div>
                    <small>{formatDateTime(matchEntry.updated_at)}</small>
                  </article>
                ))}
              </div>
            ) : (
              <div className="admin-empty">ما فيه مباريات للحين</div>
            )}
          </article>
        </div>

        <article className="admin-card admin-card--players">
          <div className="admin-card__head">
            <strong>اللاعبين</strong>
            <small>{playersPage ? `${formatCount(playersPage.total)} لاعب` : "..."}</small>
          </div>

          <div className="admin-editor">
            <div className="admin-card__head">
              <strong>{editingPlayerId ? "تعديل لاعب" : "إضافة لاعب"}</strong>
              <small>{editingPlayerId ? `#${editingPlayerId}` : "يدوي"}</small>
            </div>
            <div className="admin-editor-grid">
              <input className="input" onChange={(event) => updatePlayerForm("name_ar", event.target.value)} placeholder="الاسم بالعربي" value={playerForm.name_ar} />
              <input className="input" onChange={(event) => updatePlayerForm("name", event.target.value)} placeholder="الاسم بالإنجليزي" value={playerForm.name} />
              <input className="input" onChange={(event) => updatePlayerForm("wikidata_id", event.target.value)} placeholder="Wikidata أو خله فاضي" value={playerForm.wikidata_id} />
              <input className="input" onChange={(event) => updatePlayerForm("image_url", event.target.value)} placeholder="رابط الصورة" value={playerForm.image_url} />
              <input className="input" onChange={(event) => updatePlayerForm("current_team_ar", event.target.value)} placeholder="النادي بالعربي" value={playerForm.current_team_ar} />
              <input className="input" onChange={(event) => updatePlayerForm("current_team", event.target.value)} placeholder="النادي بالإنجليزي" value={playerForm.current_team} />
              <input className="input" min={1} max={3} onChange={(event) => updatePlayerForm("difficulty", Number(event.target.value) || 1)} placeholder="اللفل" type="number" value={playerForm.difficulty} />
              <input className="input" min={0} onChange={(event) => updatePlayerForm("fame_score", Number(event.target.value) || 0)} placeholder="الشعبية" type="number" value={playerForm.fame_score} />
              <input className="input" min={1860} onChange={(event) => updatePlayerForm("birth_year", Number(event.target.value) || 1900)} placeholder="سنة الميلاد" type="number" value={playerForm.birth_year} />
              <select className="input" onChange={(event) => updatePlayerForm("position_group", event.target.value as AdminPlayerFormState["position_group"])} value={playerForm.position_group}>
                <option value="goalkeeper">حارس</option>
                <option value="defender">دفاع</option>
                <option value="midfielder">وسط</option>
                <option value="forward">هجوم</option>
              </select>
              <select className="input" onChange={(event) => updatePlayerForm("is_active", event.target.value === "active")} value={playerForm.is_active ? "active" : "retired"}>
                <option value="active">نشط</option>
                <option value="retired">معتزل</option>
              </select>
              <select className="input" onChange={(event) => updatePlayerForm("admin_locked", event.target.value === "locked")} value={playerForm.admin_locked ? "locked" : "auto"}>
                <option value="locked">مثبّت يدوي</option>
                <option value="auto">يتحدث تلقائي</option>
              </select>
              <input className="input" onChange={(event) => updatePlayerForm("countries_ar", event.target.value)} placeholder="البلدان بالعربي" value={playerForm.countries_ar} />
              <input className="input" onChange={(event) => updatePlayerForm("countries", event.target.value)} placeholder="البلدان بالإنجليزي" value={playerForm.countries} />
              <input className="input" onChange={(event) => updatePlayerForm("continents_ar", event.target.value)} placeholder="القارات بالعربي" value={playerForm.continents_ar} />
              <input className="input" onChange={(event) => updatePlayerForm("continents", event.target.value)} placeholder="القارات بالإنجليزي" value={playerForm.continents} />
              <input className="input" onChange={(event) => updatePlayerForm("positions_ar", event.target.value)} placeholder="المراكز بالعربي" value={playerForm.positions_ar} />
              <input className="input" onChange={(event) => updatePlayerForm("positions", event.target.value)} placeholder="المراكز بالإنجليزي" value={playerForm.positions} />
              <input className="input admin-editor-grid__full" onChange={(event) => updatePlayerForm("aliases", event.target.value)} placeholder="أسماء ثانية" value={playerForm.aliases} />
            </div>
            <div className="admin-inline-actions">
              <button className="primary-button" disabled={mutationBusy === "save"} onClick={() => void savePlayer()} type="button">
                {editingPlayerId ? "حفظ التعديل" : "إضافة"}
              </button>
              <button className="ghost-button" onClick={resetEditor} type="button">
                جديد
              </button>
              <button className="ghost-button" disabled={mutationBusy === "refresh"} onClick={() => void refreshCatalog()} type="button">
                فحص السجلات الآن
              </button>
            </div>
            {lastCatalogRefresh ? (
              <div className="admin-info-row admin-info-row--stack">
                <span>آخر فحص</span>
                <strong>{lastCatalogRefresh.refreshed_at ? formatDateTime(lastCatalogRefresh.refreshed_at) : "..."}</strong>
                <small>{`${lastCatalogRefresh.updated_players} تحديث · ${lastCatalogRefresh.removed_players} حذف · ${lastCatalogRefresh.locked_players} مقفول`}</small>
              </div>
            ) : null}
            {mutationError ? <div className="auth-error">{mutationError}</div> : null}
            {mutationMessage ? <div className="admin-success">{mutationMessage}</div> : null}
          </div>

          <form className="admin-filters" onSubmit={applyFilters}>
            <input
              className="input"
              onChange={(event) => setSearchDraft(event.target.value)}
              placeholder="اسم لاعب أو نادي أو Wikidata"
              value={searchDraft}
            />
            <select
              className="input"
              onChange={(event) =>
                setFilters((current) => ({
                  ...current,
                  difficulty: event.target.value === "all" ? "all" : Number(event.target.value),
                  offset: 0,
                }))
              }
              value={String(filters.difficulty)}
            >
              <option value="all">كل اللفلات</option>
              <option value="1">لفل 1</option>
              <option value="2">لفل 2</option>
              <option value="3">لفل 3</option>
            </select>
            <select
              className="input"
              onChange={(event) =>
                setFilters((current) => ({
                  ...current,
                  active: event.target.value as "all" | "active" | "retired",
                  offset: 0,
                }))
              }
              value={filters.active}
            >
              <option value="all">كل الحالات</option>
              <option value="active">نشط</option>
              <option value="retired">معتزل</option>
            </select>
            <div className="admin-inline-actions">
              <button className="primary-button" type="submit">
                دور
              </button>
              <button className="ghost-button" onClick={resetFilters} type="button">
                تصفير
              </button>
            </div>
          </form>

          {playersError ? <div className="auth-error">{playersError}</div> : null}
          {playersBusy && !playersPage ? <div className="admin-empty">...</div> : null}

          <div className="admin-player-grid">
            {playersPage?.items.map((player) => {
              const playerCountry = player.countries_ar[0] || player.countries[0] || "مو واضح";
              const playerPosition = player.positions_ar[0] || positionLabelFromGroup(player.position_group);
              const currentTeam = player.current_team_ar || player.current_team;

              return (
                <article className="admin-player-card" key={player.id}>
                  <img
                    alt={player.name}
                    className="admin-player-card__image"
                    src={player.image_url}
                  />
                  <div className="admin-player-card__copy">
                    <div className="admin-player-card__head">
                      <strong>{player.name_ar || player.name}</strong>
                      <span className="chip chip--bold">{`لفل ${player.difficulty}`}</span>
                    </div>
                    <small>{player.name}</small>
                    <div className="fact-list fact-list--single admin-fact-list">
                      <span>{`الشعبية: ${player.fame_score}`}</span>
                      <span>{`الحالة: ${statusLabelForPlayer(player)}`}</span>
                      <span>{`البلد: ${playerCountry}`}</span>
                      <span>{`المركز: ${playerPosition}`}</span>
                      <span>{`الميلاد: ${player.birth_year}`}</span>
                      {currentTeam ? <span>{`النادي: ${currentTeam}`}</span> : null}
                      <span>{`Wikidata: ${player.wikidata_id}`}</span>
                      {player.aliases.length ? <span>{`أسماء ثانية: ${player.aliases.slice(0, 3).join("، ")}`}</span> : null}
                    </div>
                    <div className="admin-inline-actions">
                      <button className="primary-button" onClick={() => startEditing(player)} type="button">
                        تعديل
                      </button>
                      <button className="ghost-button" disabled={mutationBusy === "delete"} onClick={() => void removePlayer(player)} type="button">
                        حذف
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>

          <div className="admin-pagination">
            <button
              className="ghost-button"
              disabled={!playersPage || playersPage.offset === 0}
              onClick={() =>
                setFilters((current) => ({
                  ...current,
                  offset: Math.max(0, current.offset - pageSize),
                }))
              }
              type="button"
            >
              السابق
            </button>
            <strong>
              {playersPage
                ? `${formatCount(playersPage.offset + 1)} - ${formatCount(
                    Math.min(playersPage.offset + playersPage.items.length, playersPage.total),
                  )}`
                : "..."}
            </strong>
            <button
              className="ghost-button"
              disabled={!hasNextPage}
              onClick={() =>
                setFilters((current) => ({
                  ...current,
                  offset: current.offset + pageSize,
                }))
              }
              type="button"
            >
              التالي
            </button>
          </div>
        </article>

        <article className="admin-card admin-card--players">
          <div className="admin-card__head">
            <strong>أسئلة المساعد الذكي</strong>
            <div className="admin-inline-actions">
              <small>{`${formatCount(assistantQuestions.length)} سؤال`}</small>
              <button className="ghost-button" onClick={() => void loadAssistantCatalog()} type="button">
                تحديث
              </button>
            </div>
          </div>

          <div className="admin-editor">
            <div className="admin-card__head">
              <strong>{editingAssistantQuestionId ? "تعديل سؤال" : "إضافة سؤال"}</strong>
              <small>{editingAssistantQuestionId ? `#${editingAssistantQuestionId}` : "يدوي"}</small>
            </div>
            <div className="admin-editor-grid">
              <input
                className="input"
                onChange={(event) => updateAssistantQuestionForm("question_ar", event.target.value)}
                placeholder="السؤال بالعربي"
                value={assistantQuestionForm.question_ar}
              />
              <input
                className="input"
                onChange={(event) => updateAssistantQuestionForm("question_en", event.target.value)}
                placeholder="السؤال بالإنجليزي - اختياري"
                value={assistantQuestionForm.question_en}
              />
              <input
                className="input"
                onChange={(event) => updateAssistantQuestionForm("intent_key", event.target.value)}
                placeholder="مفتاح السؤال"
                value={assistantQuestionForm.intent_key}
              />
              <select
                className="input"
                onChange={(event) =>
                  updateAssistantQuestionForm("argument_kind", event.target.value as AssistantArgumentKind)
                }
                value={assistantQuestionForm.argument_kind}
              >
                <option value="">بدون متغير</option>
                <option value="competition">دوري أو مسابقة</option>
                <option value="team">نادٍ أو منتخب</option>
              </select>
              <select
                className="input"
                onChange={(event) => updateAssistantQuestionForm("enabled", event.target.value === "enabled")}
                value={assistantQuestionForm.enabled ? "enabled" : "disabled"}
              >
                <option value="enabled">مفعل</option>
                <option value="disabled">موقوف</option>
              </select>
              <input
                className="input admin-editor-grid__full"
                onChange={(event) => updateAssistantQuestionForm("aliases_ar", event.target.value)}
                placeholder="صيغ السؤال بالعربي، مفصولة بفواصل"
                value={assistantQuestionForm.aliases_ar}
              />
              <input
                className="input admin-editor-grid__full"
                onChange={(event) => updateAssistantQuestionForm("aliases_en", event.target.value)}
                placeholder="صيغ السؤال بالإنجليزي - اختياري"
                value={assistantQuestionForm.aliases_en}
              />
            </div>
            <div className="admin-inline-actions">
              <button
                className="primary-button"
                disabled={assistantQuestionBusy === "save"}
                onClick={() => void saveAssistantQuestion()}
                type="button"
              >
                {editingAssistantQuestionId ? "حفظ التعديل" : "إضافة السؤال"}
              </button>
              <button className="ghost-button" onClick={resetAssistantQuestionEditor} type="button">
                جديد
              </button>
            </div>
            {assistantQuestionError ? <div className="auth-error">{assistantQuestionError}</div> : null}
            {assistantQuestionMessage ? <div className="admin-success">{assistantQuestionMessage}</div> : null}
          </div>

          {assistantCatalogError ? <div className="auth-error">{assistantCatalogError}</div> : null}
          {assistantCatalogBusy && !assistantQuestions.length ? <div className="admin-empty">...</div> : null}
          {assistantQuestions.length ? (
            <div className="assistant-admin-grid">
              {assistantQuestions.map((item) => (
                <article className="assistant-admin-card" key={item.id}>
                  <div className="admin-card__head">
                    <strong>{item.question_ar}</strong>
                    <span className="chip chip--bold">{item.enabled ? "مفعل" : "موقوف"}</span>
                  </div>
                  {item.question_en ? <small>{item.question_en}</small> : null}
                  <div className="assistant-admin-card__meta">
                    <span>{`المفتاح: ${item.intent_key}`}</span>
                    <span>{`النوع: ${assistantArgumentLabel(item.argument_kind)}`}</span>
                    <span>{formatDateTime(item.created_at)}</span>
                  </div>
                  {item.aliases_ar.length ? (
                    <div className="assistant-admin-card__aliases">
                      <strong>الصيغ العربية</strong>
                      <span>{item.aliases_ar.join("، ")}</span>
                    </div>
                  ) : null}
                  {item.aliases_en.length ? (
                    <div className="assistant-admin-card__aliases">
                      <strong>الصيغ الإنجليزية</strong>
                      <span>{item.aliases_en.join(", ")}</span>
                    </div>
                  ) : null}
                  <div className="admin-inline-actions">
                    <button className="primary-button" onClick={() => startEditingAssistantQuestion(item)} type="button">
                      تعديل
                    </button>
                    <button
                      className="ghost-button"
                      disabled={assistantQuestionBusy === "delete"}
                      onClick={() => void removeAssistantQuestion(item)}
                      type="button"
                    >
                      حذف
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            !assistantCatalogBusy ? <div className="admin-empty">ما فيه أسئلة محفوظة للحين.</div> : null
          )}
        </article>

        <article className="admin-card admin-card--players">
          <div className="admin-card__head">
            <strong>الدوريات والمسابقات</strong>
            <small>{`${formatCount(assistantCompetitions.length)} عنصر`}</small>
          </div>

          <div className="admin-editor">
            <div className="admin-card__head">
              <strong>{editingAssistantCompetitionId ? "تعديل دوري أو مسابقة" : "إضافة دوري أو مسابقة"}</strong>
              <small>{editingAssistantCompetitionId ? `#${editingAssistantCompetitionId}` : "يدوي"}</small>
            </div>
            <div className="admin-editor-grid">
              <input
                className="input"
                onChange={(event) => updateAssistantCompetitionForm("name_ar", event.target.value)}
                placeholder="الاسم بالعربي"
                value={assistantCompetitionForm.name_ar}
              />
              <input
                className="input"
                onChange={(event) => updateAssistantCompetitionForm("name_en", event.target.value)}
                placeholder="الاسم بالإنجليزي - اختياري"
                value={assistantCompetitionForm.name_en}
              />
              <input
                className="input"
                onChange={(event) => updateAssistantCompetitionForm("key", event.target.value)}
                placeholder="المفتاح"
                value={assistantCompetitionForm.key}
              />
              <input
                className="input"
                onChange={(event) => updateAssistantCompetitionForm("wikidata_id", event.target.value)}
                placeholder="Wikidata ID - اختياري"
                value={assistantCompetitionForm.wikidata_id}
              />
              <select
                className="input"
                onChange={(event) => updateAssistantCompetitionForm("enabled", event.target.value === "enabled")}
                value={assistantCompetitionForm.enabled ? "enabled" : "disabled"}
              >
                <option value="enabled">مفعل</option>
                <option value="disabled">موقوف</option>
              </select>
              <input
                className="input admin-editor-grid__full"
                onChange={(event) => updateAssistantCompetitionForm("aliases_ar", event.target.value)}
                placeholder="الأسماء البديلة بالعربي، مفصولة بفواصل"
                value={assistantCompetitionForm.aliases_ar}
              />
              <input
                className="input admin-editor-grid__full"
                onChange={(event) => updateAssistantCompetitionForm("aliases_en", event.target.value)}
                placeholder="الأسماء البديلة بالإنجليزي - اختياري"
                value={assistantCompetitionForm.aliases_en}
              />
            </div>
            <div className="admin-inline-actions">
              <button
                className="primary-button"
                disabled={assistantCompetitionBusy === "save"}
                onClick={() => void saveAssistantCompetition()}
                type="button"
              >
                {editingAssistantCompetitionId ? "حفظ التعديل" : "إضافة الدوري"}
              </button>
              <button className="ghost-button" onClick={resetAssistantCompetitionEditor} type="button">
                جديد
              </button>
            </div>
            {assistantCompetitionError ? <div className="auth-error">{assistantCompetitionError}</div> : null}
            {assistantCompetitionMessage ? <div className="admin-success">{assistantCompetitionMessage}</div> : null}
          </div>

          {assistantCompetitions.length ? (
            <div className="assistant-admin-grid">
              {assistantCompetitions.map((item) => (
                <article className="assistant-admin-card" key={item.id}>
                  <div className="admin-card__head">
                    <strong>{item.name_ar}</strong>
                    <span className="chip chip--bold">{item.enabled ? "مفعل" : "موقوف"}</span>
                  </div>
                  {item.name_en ? <small>{item.name_en}</small> : null}
                  <div className="assistant-admin-card__meta">
                    <span>{`المفتاح: ${item.key}`}</span>
                    {item.wikidata_id ? <span>{`Wikidata: ${item.wikidata_id}`}</span> : null}
                    <span>{formatDateTime(item.created_at)}</span>
                  </div>
                  {item.aliases_ar.length ? (
                    <div className="assistant-admin-card__aliases">
                      <strong>الأسماء العربية</strong>
                      <span>{item.aliases_ar.join("، ")}</span>
                    </div>
                  ) : null}
                  {item.aliases_en.length ? (
                    <div className="assistant-admin-card__aliases">
                      <strong>الأسماء الإنجليزية</strong>
                      <span>{item.aliases_en.join(", ")}</span>
                    </div>
                  ) : null}
                  <div className="admin-inline-actions">
                    <button
                      className="primary-button"
                      onClick={() => startEditingAssistantCompetition(item)}
                      type="button"
                    >
                      تعديل
                    </button>
                    <button
                      className="ghost-button"
                      disabled={assistantCompetitionBusy === "delete"}
                      onClick={() => void removeAssistantCompetition(item)}
                      type="button"
                    >
                      حذف
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            !assistantCatalogBusy ? <div className="admin-empty">ما فيه دوريات أو مسابقات محفوظة للحين.</div> : null
          )}
        </article>
      </section>
    </main>
  );
}

function SetupScreen({
  busyAction,
  challengeDefinition,
  challengeType,
  modeDefinition,
  onAddTeamParticipant,
  onAssignParticipantToTeam,
  onBack,
  onChallengeTypeChange,
  onClearTwists,
  onModeChange,
  onPlayerOneNameChange,
  onPlayerTwoNameChange,
  onRandomizeTwists,
  onRemoveTeamParticipant,
  onReshuffleTeams,
  onSpeakerOneNameChange,
  onSpeakerTwoNameChange,
  onStartMatch,
  onTeamDistributionModeChange,
  onTeamMemberDraftChange,
  onTeamOneNameChange,
  onTeamTwoNameChange,
  onToggleAnswerRule,
  onToggleBlockedCategory,
  overview,
  playerOneName,
  playerTwoName,
  speakerOneName,
  speakerTwoName,
  selectedAnswerRuleKeys,
  selectedChallengeType,
  selectedDifficulty,
  selectedMode,
  selectedProhibitedCategoryKeys,
  setSelectedDifficulty,
  teamDistributionMode,
  teamMemberDraft,
  teamOneName,
  teamParticipants,
  teamTwoName,
}: {
  busyAction: string | null;
  challengeDefinition: ChallengeTypeDefinition;
  challengeType: ChallengeTypeKey;
  modeDefinition: ReturnType<typeof getModeDefinition>;
  onAddTeamParticipant: () => void;
  onAssignParticipantToTeam: (participantId: string, team: TeamAssignment) => void;
  onBack: () => void;
  onChallengeTypeChange: (challengeType: ChallengeTypeKey) => void;
  onClearTwists: () => void;
  onModeChange: (modeKey: MatchModeKey) => void;
  onPlayerOneNameChange: (value: string) => void;
  onPlayerTwoNameChange: (value: string) => void;
  onRandomizeTwists: () => void;
  onRemoveTeamParticipant: (participantId: string) => void;
  onReshuffleTeams: () => void;
  onSpeakerOneNameChange: (value: string) => void;
  onSpeakerTwoNameChange: (value: string) => void;
  onStartMatch: () => void;
  onTeamDistributionModeChange: (mode: TeamDistributionMode) => void;
  onTeamMemberDraftChange: (value: string) => void;
  onTeamOneNameChange: (value: string) => void;
  onTeamTwoNameChange: (value: string) => void;
  onToggleAnswerRule: (key: AnswerRuleKey) => void;
  onToggleBlockedCategory: (key: QuestionCategoryKey) => void;
  overview: GameOverview | null;
  playerOneName: string;
  playerTwoName: string;
  speakerOneName: string;
  speakerTwoName: string;
  selectedAnswerRuleKeys: AnswerRuleKey[];
  selectedChallengeType: ChallengeTypeKey;
  selectedDifficulty: number;
  selectedMode: MatchModeKey;
  selectedProhibitedCategoryKeys: QuestionCategoryKey[];
  setSelectedDifficulty: (difficulty: number) => void;
  teamDistributionMode: TeamDistributionMode;
  teamMemberDraft: string;
  teamOneName: string;
  teamParticipants: TeamParticipantDraft[];
  teamTwoName: string;
}) {
  const groupedParticipants = splitTeamParticipants(teamParticipants);
  const duelInputPlaceholders: [string, string] = ["اسم 1", "اسم 2"];
  const teamInputPlaceholders: [string, string] = ["فريق 1", "فريق 2"];
  const teamMemberInputPlaceholder = "اسم اللاعب";

  return (
    <main className="flow-screen">
      <section className="flow-panel flow-panel--wide setup-panel">
        <div className="flow-header">
          <button className="ghost-button" onClick={onBack} type="button">
            ارجع
          </button>
          <div className="flow-title-group">
            <BrandMark compact />
            <div className="flow-title-copy">
              <h2>ابدأ</h2>
            </div>
          </div>
        </div>

        <div className="section-block">
          <div className="section-title">
            <strong>نوع التحدي</strong>
          </div>
          <div className="mode-grid challenge-grid">
            {CHALLENGE_TYPES.map((entry) => (
              <button
                className={`mode-card ${selectedChallengeType === entry.key ? "mode-card--selected" : ""}`}
                key={entry.key}
                onClick={() => onChallengeTypeChange(entry.key)}
                type="button"
              >
                <strong>{entry.label}</strong>
                <small>{entry.description}</small>
              </button>
            ))}
          </div>
        </div>

        <div className="setup-grid">
          <article className="setup-card setup-card--players">
            <div className="setup-card__title">
              <strong>{challengeType === "teams" ? "التوزيع" : "الأسامي"}</strong>
            </div>
            {challengeType === "head-to-head" ? (
              <div className="field-stack">
                <input
                  autoCapitalize="none"
                  autoComplete="off"
                  autoCorrect="off"
                  className="input"
                  onChange={(event) => onPlayerOneNameChange(event.target.value)}
                  placeholder={duelInputPlaceholders[0]}
                  spellCheck={false}
                  value={playerOneName}
                />
                <input
                  autoCapitalize="none"
                  autoComplete="off"
                  autoCorrect="off"
                  className="input"
                  onChange={(event) => onPlayerTwoNameChange(event.target.value)}
                  placeholder={duelInputPlaceholders[1]}
                  spellCheck={false}
                  value={playerTwoName}
                />
              </div>
            ) : null}

            {challengeType === "one-explains" ? (
              <div className="field-stack">
                <input
                  autoCapitalize="none"
                  autoComplete="off"
                  autoCorrect="off"
                  className="input"
                  onChange={(event) => onSpeakerOneNameChange(event.target.value)}
                  placeholder={duelInputPlaceholders[0]}
                  spellCheck={false}
                  value={speakerOneName}
                />
                <input
                  autoCapitalize="none"
                  autoComplete="off"
                  autoCorrect="off"
                  className="input"
                  onChange={(event) => onSpeakerTwoNameChange(event.target.value)}
                  placeholder={duelInputPlaceholders[1]}
                  spellCheck={false}
                  value={speakerTwoName}
                />
              </div>
            ) : null}

            {challengeType === "teams" ? (
              <div className="team-builder">
                <div className="team-name-grid">
                  <input
                    autoCapitalize="none"
                    autoComplete="off"
                    autoCorrect="off"
                    className="input"
                    onChange={(event) => onTeamOneNameChange(event.target.value)}
                    placeholder={teamInputPlaceholders[0]}
                    spellCheck={false}
                    value={teamOneName}
                  />
                  <input
                    autoCapitalize="none"
                    autoComplete="off"
                    autoCorrect="off"
                    className="input"
                    onChange={(event) => onTeamTwoNameChange(event.target.value)}
                    placeholder={teamInputPlaceholders[1]}
                    spellCheck={false}
                    value={teamTwoName}
                  />
                </div>

                <div className="team-add-row">
                  <input
                    autoCapitalize="none"
                    autoComplete="off"
                    autoCorrect="off"
                    className="input"
                    onChange={(event) => onTeamMemberDraftChange(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        onAddTeamParticipant();
                      }
                    }}
                    placeholder={teamMemberInputPlaceholder}
                    spellCheck={false}
                    value={teamMemberDraft}
                  />
                  <button className="primary-button team-add-button" onClick={onAddTeamParticipant} type="button">
                    +
                  </button>
                </div>

                <div className="chip-picker">
                  <button
                    className={`chip-toggle ${teamDistributionMode === "shuffle" ? "chip-toggle--selected" : ""}`}
                    onClick={() => onTeamDistributionModeChange("shuffle")}
                    type="button"
                  >
                    توزيع عشوائي
                  </button>
                  <button
                    className={`chip-toggle ${teamDistributionMode === "manual" ? "chip-toggle--selected" : ""}`}
                    onClick={() => onTeamDistributionModeChange("manual")}
                    type="button"
                  >
                    أنا أوزع
                  </button>
                  {teamDistributionMode === "shuffle" ? (
                    <button className="ghost-button chip-action" onClick={onReshuffleTeams} type="button">
                      بدّلهم
                    </button>
                  ) : null}
                </div>

                <div className="team-members-stack">
                  {teamParticipants.map((participant) => (
                    <div className="team-member-row" key={participant.id}>
                      <strong>{participant.name}</strong>
                      <div className="team-member-row__actions">
                        {teamDistributionMode === "manual" ? (
                          <>
                            <button
                              className={`ghost-button team-pick-button ${
                                participant.team === 1 ? "team-pick-button--active" : ""
                              }`}
                              onClick={() => onAssignParticipantToTeam(participant.id, 1)}
                              type="button"
                            >
                              1
                            </button>
                            <button
                              className={`ghost-button team-pick-button ${
                                participant.team === 2 ? "team-pick-button--active" : ""
                              }`}
                              onClick={() => onAssignParticipantToTeam(participant.id, 2)}
                              type="button"
                            >
                              2
                            </button>
                            <button
                              className={`ghost-button team-pick-button ${
                                participant.team === null ? "team-pick-button--active" : ""
                              }`}
                              onClick={() => onAssignParticipantToTeam(participant.id, null)}
                              type="button"
                            >
                              وقف
                            </button>
                          </>
                        ) : null}
                        <button
                          className="ghost-button team-remove-button"
                          onClick={() => onRemoveTeamParticipant(participant.id)}
                          type="button"
                        >
                          حذف
                        </button>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="team-preview-grid">
                  <article className="team-preview-card">
                    <small>{challengeDefinition.seatRoleLabels[0]}</small>
                    <strong>{teamOneName || challengeDefinition.seatPlaceholders[0]}</strong>
                    <ul className="detail-list">
                      {groupedParticipants.teamOne.length ? (
                        groupedParticipants.teamOne.map((participant) => <li key={participant.id}>{participant.name}</li>)
                      ) : (
                        <li>...</li>
                      )}
                    </ul>
                  </article>

                  <article className="team-preview-card">
                    <small>{challengeDefinition.seatRoleLabels[1]}</small>
                    <strong>{teamTwoName || challengeDefinition.seatPlaceholders[1]}</strong>
                    <ul className="detail-list">
                      {groupedParticipants.teamTwo.length ? (
                        groupedParticipants.teamTwo.map((participant) => <li key={participant.id}>{participant.name}</li>)
                      ) : (
                        <li>...</li>
                      )}
                    </ul>
                  </article>

                  {teamDistributionMode === "manual" && groupedParticipants.waiting.length ? (
                    <article className="team-preview-card team-preview-card--waiting">
                      <small>برا التوزيع</small>
                      <strong>لسه</strong>
                      <ul className="detail-list">
                        {groupedParticipants.waiting.map((participant) => (
                          <li key={participant.id}>{participant.name}</li>
                        ))}
                      </ul>
                    </article>
                  ) : null}
                </div>
              </div>
            ) : null}
          </article>

          <article className="setup-card setup-card--summary">
            <div className="setup-summary-row">
              <span className="setup-summary-label">النوع:</span>
              <strong className="setup-summary-value">{challengeDefinition.label}</strong>
            </div>
            <div className="setup-summary-row setup-summary-row--muted">
              <span className="setup-summary-label">الشكل:</span>
              <span className="setup-summary-text">{challengeDefinition.summary}</span>
            </div>
            <div className="setup-summary-row">
              <span className="setup-summary-label">المود:</span>
              <strong className="setup-summary-value">{modeDefinition.label}</strong>
            </div>
            <div className="setup-summary-row setup-summary-row--muted">
              <span className="setup-summary-label">الهدف:</span>
              <span className="setup-summary-text">{modeDefinition.victoryCondition}</span>
            </div>
            {challengeType === "teams" ? (
              <>
                <div className="setup-summary-row">
                  <span className="setup-summary-label">الأسماء:</span>
                  <strong className="setup-summary-value">{teamParticipants.length}</strong>
                </div>
                <div className="setup-summary-row setup-summary-row--muted">
                  <span className="setup-summary-label">التوزيع:</span>
                  <span className="setup-summary-text">
                    {teamDistributionMode === "shuffle" ? "عشوائي" : "يدوي"}
                  </span>
                </div>
              </>
            ) : null}
          </article>
        </div>

        <div className="section-block">
          <div className="section-title">
            <strong>المود</strong>
          </div>
          <div className="mode-grid">
            {MATCH_MODES.map((mode) => (
              <button
                className={`mode-card ${mode.key === selectedMode ? "mode-card--selected" : ""}`}
                key={mode.key}
                onClick={() => onModeChange(mode.key)}
                type="button"
              >
                <strong>{mode.label}</strong>
                <small>{mode.victoryCondition}</small>
              </button>
            ))}
          </div>
        </div>

        <div className="section-block">
          <div className="section-title">
            <strong>اللفل</strong>
          </div>
          <div className="difficulty-grid">
            {overview?.difficulty_levels.map((level) => (
              <button
                className={`difficulty-card ${selectedDifficulty === level.level ? "difficulty-card--selected" : ""}`}
                key={level.level}
                onClick={() => setSelectedDifficulty(level.level)}
                type="button"
              >
                <div className="difficulty-card__top">
                  <strong>{level.label}</strong>
                  <span>{level.base_points}</span>
                </div>
                <small>{level.description}</small>
              </button>
            ))}
          </div>
        </div>

        <div className="section-block">
          <div className="section-title">
            <strong>التويست</strong>
          </div>
          <div className="twist-toolbar">
            <button className="ghost-button chip-action" onClick={onRandomizeTwists} type="button">
              عشوائي
            </button>
            <button className="ghost-button chip-action" onClick={onClearTwists} type="button">
              بدون
            </button>
          </div>
          <div className="twist-toggle-list">
            {ANSWER_RULE_OPTIONS.map((rule) => (
              <button
                aria-checked={selectedAnswerRuleKeys.includes(rule.key)}
                className={`twist-toggle-row ${
                  selectedAnswerRuleKeys.includes(rule.key) ? "twist-toggle-row--selected" : ""
                }`}
                key={rule.key}
                onClick={() => onToggleAnswerRule(rule.key)}
                role="switch"
                type="button"
              >
                <span className="twist-toggle-row__label">{rule.label}</span>
                <span
                  className={`twist-switch ${
                    selectedAnswerRuleKeys.includes(rule.key) ? "twist-switch--selected" : ""
                  }`}
                >
                  <span className="twist-switch__knob" />
                </span>
              </button>
            ))}
            {overview?.question_categories.map((category) => (
              <button
                aria-checked={selectedProhibitedCategoryKeys.includes(category.key)}
                className={`twist-toggle-row ${
                  selectedProhibitedCategoryKeys.includes(category.key) ? "twist-toggle-row--selected" : ""
                }`}
                key={category.key}
                onClick={() => onToggleBlockedCategory(category.key)}
                role="switch"
                type="button"
              >
                <span className="twist-toggle-row__label">{`قفل ${category.label}`}</span>
                <span
                  className={`twist-switch ${
                    selectedProhibitedCategoryKeys.includes(category.key) ? "twist-switch--selected" : ""
                  }`}
                >
                  <span className="twist-switch__knob" />
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="cta-strip">
          <button
            className="primary-button"
            disabled={busyAction === "create"}
            onClick={onStartMatch}
            type="button"
          >
            يلا
          </button>
        </div>
      </section>
    </main>
  );
}

function LobbyScreen({
  busyAction,
  copyToClipboard,
  isPublicReady,
  match,
  matchSetupMeta,
  onEndGameNow,
  onOpenGameBoard,
  onOpenSettings,
  secretLinks,
}: {
  busyAction: string | null;
  copyToClipboard: (value: string) => void;
  isPublicReady: boolean;
  match: MatchRead | null;
  matchSetupMeta: MatchSetupMeta | null;
  onEndGameNow: () => void;
  onOpenGameBoard: () => void;
  onOpenSettings: () => void;
  secretLinks: Record<number, { url: string; qrUrl: string }>;
}) {
  const [stepIndex, setStepIndex] = useState(0);

  useEffect(() => {
    setStepIndex(0);
  }, [match?.match_id, match?.round.round_number]);

  if (!match) {
    return (
      <main className="flow-screen">
        <section className="flow-panel">
          <div className="flow-header">
            <div className="flow-title-group">
              <BrandMark compact />
              <div className="flow-title-copy">
                <h2>لحظة...</h2>
              </div>
            </div>
          </div>
        </section>
      </main>
    );
  }

  const mode = getModeDefinition(match.mode_key);
  const starterSeat = seatByNumber(match.seats, match.round.starting_seat) ?? match.seats[0];
  const secondSeat = seatByNumber(match.seats, starterSeat.seat === 1 ? 2 : 1) ?? match.seats[1];
  const orderedSeats = [starterSeat, secondSeat];
  const currentSeat = stepIndex > 0 ? orderedSeats[Math.min(stepIndex - 1, orderedSeats.length - 1)] : null;
  const currentLink = currentSeat ? secretLinks[currentSeat.seat] : undefined;
  const activeSeatNumber = stepIndex <= 1 ? starterSeat.seat : secondSeat.seat;
  const nextDisabled = stepIndex > 0 && (!isPublicReady || !currentLink);
  const nextLabel = stepIndex === 0 ? "التالي" : stepIndex === 1 ? "بعده" : "ابدأ الجولة";
  const setupMeta = matchSetupMeta ?? buildFallbackMatchSetupMeta(match);
  const starterMeta = seatSetupMetaFor(setupMeta, starterSeat.seat, starterSeat.player_name);
  const secondMeta = seatSetupMetaFor(setupMeta, secondSeat.seat, secondSeat.player_name);

  function handleNextStep() {
    if (stepIndex >= orderedSeats.length) {
      onOpenGameBoard();
      return;
    }

    setStepIndex((current) => current + 1);
  }

  return (
    <main className="flow-screen">
      <section className="flow-panel flow-panel--wide lobby-panel">
        <div className="flow-header">
          <div className="header-actions">
            <button className="ghost-button" onClick={onOpenSettings} type="button">
              الرابط
            </button>
          </div>
          <div className="flow-title-group">
            <BrandMark compact />
            <div className="flow-title-copy">
              <h2>{mode.label}</h2>
              <small>{setupMeta.challengeLabel}</small>
            </div>
          </div>
        </div>

        <div
          className={`share-hint share-hint--inline ${
            isPublicReady ? "share-hint--ready" : "share-hint--pending"
          }`}
        >
          <strong>{isPublicReady ? "جاهز" : "حط الرابط العام"}</strong>
          {!isPublicReady ? (
            <button className="ghost-button chip-action" onClick={onOpenSettings} type="button">
              الضبط
            </button>
          ) : null}
        </div>

        <div className="lobby-order-grid">
          {orderedSeats.map((seat, index) => (
            <article
              className={`lobby-order-card ${activeSeatNumber === seat.seat ? "lobby-order-card--active" : ""}`}
              key={seat.seat}
            >
              <small>{index === 0 ? "يبدأ" : "بعده"}</small>
              <strong>{seat.player_name}</strong>
              <span>
                {seatSetupMetaFor(setupMeta, seat.seat, seat.player_name).members.length > 1
                  ? seatSetupMetaFor(setupMeta, seat.seat, seat.player_name).members.join(" • ")
                  : seatSetupMetaFor(setupMeta, seat.seat, seat.player_name).roleLabel}
              </span>
            </article>
          ))}
        </div>

        <article className="lobby-stage-card">
          {stepIndex === 0 ? (
            <div className="lobby-stage-card__copy">
              <small>{setupMeta.challengeLabel}</small>
              <strong>{`${setupMeta.startPrompt}: ${starterSeat.player_name}`}</strong>
              <span>{`وبعده: ${secondSeat.player_name}`}</span>
              <span className="lobby-stage-card__members">
                {starterMeta.members.length > 1 ? starterMeta.members.join(" • ") : starterMeta.roleLabel}
              </span>
              <span className="lobby-stage-card__members">
                {secondMeta.members.length > 1 ? secondMeta.members.join(" • ") : secondMeta.roleLabel}
              </span>
            </div>
          ) : (
            <>
              <div className="lobby-stage-card__copy">
                <small>{`الدور ${stepIndex}`}</small>
                <strong>{currentSeat?.player_name}</strong>
                {currentSeat ? (
                  <span className="lobby-stage-card__members">
                    {seatSetupMetaFor(setupMeta, currentSeat.seat, currentSeat.player_name).members.length > 1
                      ? seatSetupMetaFor(setupMeta, currentSeat.seat, currentSeat.player_name).members.join(" • ")
                      : seatSetupMetaFor(setupMeta, currentSeat.seat, currentSeat.player_name).roleLabel}
                  </span>
                ) : null}
              </div>

              <div className="qr-frame">
                {currentLink ? (
                  <img alt={`QR ${currentSeat?.player_name ?? "player"}`} src={currentLink.qrUrl} />
                ) : (
                  <div className="qr-placeholder">...</div>
                )}
              </div>

              <button
                className="secondary-button"
                disabled={!currentLink || busyAction === "copy"}
                onClick={() => currentLink && copyToClipboard(currentLink.url)}
                type="button"
              >
                نسخ الرابط
              </button>
            </>
          )}

          <div className="cta-strip lobby-stage-card__actions">
            <button
              className="secondary-button"
              disabled={busyAction === "end"}
              onClick={onEndGameNow}
              type="button"
            >
              {"\u0625\u0646\u0647\u0627\u0621 \u0627\u0644\u0644\u0639\u0628\u0629 \u0627\u0644\u0622\u0646"}
            </button>
            <button
              className="primary-button"
              disabled={nextDisabled || busyAction === "end"}
              onClick={handleNextStep}
              type="button"
            >
              {nextLabel}
            </button>
          </div>
        </article>
      </section>
    </main>
  );
}

function GameBoardScreen({
  busyAction,
  match,
  matchSetupMeta,
  onAwardRound,
  onMarkNoAnswer,
  onNextRound,
  overview,
}: {
  busyAction: string | null;
  match: MatchRead | null;
  matchSetupMeta: MatchSetupMeta | null;
  onAwardRound: (seat: number) => void;
  onMarkNoAnswer: () => void;
  onNextRound: () => void;
  overview: GameOverview | null;
}) {
  if (!match || !overview) {
    return (
      <main className="flow-screen">
        <section className="flow-panel">
          <div className="flow-header">
            <div className="flow-title-group">
              <BrandMark compact />
              <div className="flow-title-copy">
                <h2>لحظة...</h2>
              </div>
            </div>
          </div>
        </section>
      </main>
    );
  }

  const mode = getModeDefinition(match.mode_key);
  const chips = buildRuleChips(match.round, overview.question_categories);
  const winner = match.winner_seat ? seatByNumber(match.seats, match.winner_seat) : null;
  const awardedSeat = match.round.awarded_to ? seatByNumber(match.seats, match.round.awarded_to) : null;
  const roundResolvedWithoutWinner = match.round.resolved && match.round.awarded_to === null;
  const resolutionDisabled =
    busyAction === "award"
    || busyAction === "no-answer"
    || busyAction === "next-round"
    || match.round.resolved
    || match.status === "completed";
  const nextDisabled =
    busyAction === "next-round" || match.status === "completed" || !match.round.resolved;
  const setupMeta = matchSetupMeta ?? buildFallbackMatchSetupMeta(match);

  return (
    <main className="flow-screen">
      <section className="flow-panel flow-panel--wide game-board">
        <div className="game-board__top">
          <div className="game-board__title game-board__title--solo">
            <BrandMark compact />
            <div className="game-board__title-copy">
              <small>{mode.label}</small>
              <h2>{mode.victoryCondition}</h2>
              <small>{setupMeta.challengeLabel}</small>
            </div>
          </div>

          {winner ? (
            <div className="winner-banner winner-banner--game">
              <span>خلصت</span>
              <strong>{winner.player_name}</strong>
            </div>
          ) : null}
        </div>

        <div className="game-round-strip">
          <article className="game-stat-card">
            <small>الجولة</small>
            <strong>{match.round.round_number}</strong>
          </article>
          <article className="game-stat-card">
            <small>اللفل</small>
            <strong>{match.round.difficulty_label}</strong>
          </article>
          <article className="game-stat-card">
            <small>نقاطها</small>
            <strong>{match.round.points_for_win}</strong>
          </article>
          <article className="game-stat-card">
            <small>الأسئلة</small>
            <strong>{match.round.question_limit}</strong>
          </article>
          <article className="game-stat-card">
            <small>التخمين</small>
            <strong>{match.round.guess_limit}</strong>
          </article>
        </div>

        <div className="game-seat-grid">
          {match.seats.map((seat) => (
            <article
              className={`game-seat-card ${awardedSeat?.seat === seat.seat ? "game-seat-card--picked" : ""}`}
              key={seat.seat}
            >
              {(() => {
                const seatMeta = seatSetupMetaFor(setupMeta, seat.seat, seat.player_name);
                return (
                  <>
              <div className="game-seat-card__head">
                <strong>{seat.player_name}</strong>
                <span>{seat.score} نقطة</span>
              </div>
              <div className="game-seat-card__group">
                <small>{seatMeta.roleLabel}</small>
                {seatMeta.members.length > 1 ? <span>{seatMeta.members.join(" • ")}</span> : null}
              </div>
              <div className="game-seat-card__meta">
                <small>{seat.rounds_won} جولات</small>
                <small>{streakLabel(seat.current_streak)}</small>
              </div>
                  </>
                );
              })()}
            </article>
          ))}
        </div>

        <div className="game-bottom-grid">
          <article className="game-rules-card">
            <strong>قوانين الجولة</strong>
            <div className="chip-row game-chip-row">
              {chips.map((chip) => (
                <span className="chip chip--bold" key={chip}>
                  {chip}
                </span>
              ))}
            </div>
          </article>

          <article className="game-actions-card">
            <div className="game-actions-card__head">
              <strong>{setupMeta.boardPrompt}</strong>
              {awardedSeat ? <small>{awardedSeat.player_name}</small> : null}
              {roundResolvedWithoutWinner ? <small>ولا حد جاوب</small> : null}
            </div>

            <div className="game-actions-grid">
              {match.seats.map((seat) => (
                <button
                  className="primary-button game-seat-button"
                  disabled={resolutionDisabled}
                  key={`award-${seat.seat}`}
                  onClick={() => onAwardRound(seat.seat)}
                  type="button"
                >
                  {seat.player_name}
                </button>
              ))}
            </div>

            <button
              className="secondary-button game-no-answer-button"
              disabled={resolutionDisabled}
              onClick={onMarkNoAnswer}
              type="button"
            >
              ولا حد جاوب
            </button>

            <button
              className="secondary-button game-next-button"
              disabled={nextDisabled}
              onClick={onNextRound}
              type="button"
            >
              التالي
            </button>
          </article>
        </div>
      </section>
    </main>
  );
}

function PublicCardScreen({ payload }: { payload: string }) {
  const [cardPayload, setCardPayload] = useState<SharedPlayerCardPayload | null>(null);
  const [cardError, setCardError] = useState("");
  const [cardLanguage, setCardLanguage] = useState<CardLanguage>("ar");
  const [assistantQuestion, setAssistantQuestion] = useState("");
  const [assistantAnswer, setAssistantAnswer] = useState("");
  const [assistantBusy, setAssistantBusy] = useState(false);
  const { clubSequence, details, summary } = useWikipediaCard(
    cardPayload?.n ?? null,
    cardPayload?.na ?? null,
    cardPayload?.wd ?? null,
    cardLanguage,
  );
  const noteKey = cardPayload
    ? `who-is-the-player-note:${cardPayload.m}:${cardPayload.r}:${cardPayload.s}`
    : "";
  const [notes, setNotes] = useState("");

  useEffect(() => {
    let active = true;
    setCardPayload(null);
    setCardError("");

    void api
      .getPublicPlayerCard(payload)
      .then((value) => {
        if (!active) {
          return;
        }
        setCardPayload(value);
      })
      .catch((error) => {
        if (!active) {
          return;
        }
        setCardError(error instanceof ApiError ? error.message : "...");
      });

    return () => {
      active = false;
    };
  }, [payload]);

  useEffect(() => {
    if (!noteKey) {
      return;
    }
    setNotes(localStorage.getItem(noteKey) ?? "");
  }, [noteKey]);

  useEffect(() => {
    setAssistantQuestion("");
    setAssistantAnswer("");
    setAssistantBusy(false);
  }, [cardPayload]);

  useEffect(() => {
    if (!noteKey) {
      return;
    }
    localStorage.setItem(noteKey, notes);
  }, [noteKey, notes]);

  if (!cardPayload) {
    return (
      <div className="player-screen">
        <section className="player-panel">
          <h1>{cardError || "..."}</h1>
        </section>
      </div>
    );
  }

  const shortSummary = shortenSummary(summary?.extract);
  const cardText = cardUiText(cardLanguage);
  const displayPlayerName = cardLanguage === "ar" ? cardPayload.na || cardPayload.n : cardPayload.n;
  const displayCountryName =
    cardLanguage === "ar" ? cardPayload.c || cardPayload.ce : cardPayload.ce || cardPayload.c;
  const displayClubName =
    cardLanguage === "ar" ? cardPayload.cta || cardPayload.ct : cardPayload.ct || cardPayload.cta;
  const displayStatusLabel = cardPayload.a
    ? cardText.active
    : details?.retired_year
      ? `${cardText.retired} ${details.retired_year}`
      : cardText.retired;
  const displayFacts = [
    `${cardText.from}: ${displayCountryName}`,
    `${cardText.position}: ${positionLabelForCard(cardPayload.p, cardLanguage)}`,
    `${cardText.born}: ${cardPayload.y}`,
    `${cardText.status}: ${displayStatusLabel}`,
  ];
  if (displayClubName) {
    displayFacts.push(`${cardText.club}: ${displayClubName}`);
  }
  const displayWikipediaUrl =
    summary?.content_urls?.mobile?.page ??
    summary?.content_urls?.desktop?.page ??
    `${cardText.wikipediaSearchBase}${encodeURIComponent(cardLanguage === "ar" ? cardPayload.na || cardPayload.n : cardPayload.n)}`;
  const displayRetiredYear = details?.retired_year ?? null;
  const summaryFallback = buildFallbackPlayerSummary({
    playerName: displayPlayerName,
    countryName: displayCountryName,
    positionLabel: positionLabelForCard(cardPayload.p, cardLanguage),
    birthYear: cardPayload.y,
    clubName: displayClubName,
    isActive: cardPayload.a === 1,
    retiredYear: displayRetiredYear,
    language: cardLanguage,
  });
  const displaySummary = hasUsefulCardText(shortSummary)
    ? shortSummary
    : hasUsefulCardText(summary?.description)
      ? summary?.description ?? summaryFallback
      : summaryFallback;
  const displayAchievements = details?.achievements ?? [];
  const displayClubsFinal = buildFallbackClubSequence({
    clubs: (clubSequence.length ? clubSequence : details?.club_sequence ?? []).filter(
      (club) => !/\bnational\b.*\bteam\b|\bU(?:17|18|19|20|21|23)\b|under-\d+|\u0645\u0646\u062a\u062e\u0628|school|schools|\u0645\u062f\u0627\u0631\u0633/ui.test(club),
    ),
    clubName: displayClubName,
    language: cardLanguage,
  }).filter(
    (club) => !/\bnational\b.*\bteam\b|\bU(?:17|18|19|20|21|23)\b|under-\d+|\u0645\u0646\u062a\u062e\u0628|school|schools|\u0645\u062f\u0627\u0631\u0633/ui.test(club),
  );
  const assistantText = assistantUiText("ar");
  const assistantPlaceholder =
    (assistantText as { answerPlaceholder?: string }).answerPlaceholder || "اكتب السؤال واضغط اسأل.";
  const fallbackAssistantAnswer = buildSmartAssistantReply({
    query: assistantQuestion,
    language: "ar",
    cardPayload,
    displayCountryName,
    displayClubName,
    displayStatusLabel: displayStatusLabel,
    displaySummary,
    displayClubsFinal,
    displayAchievements,
  });
  const handleAskAssistant = async () => {
    if (!assistantQuestion.trim()) {
      setAssistantAnswer(fallbackAssistantAnswer);
      return;
    }

    setAssistantBusy(true);
    try {
      const response = await api.askPublicPlayerCardAssistant(payload, {
        question: assistantQuestion,
        language: "ar",
      });
      setAssistantAnswer(response.answer || fallbackAssistantAnswer);
    } catch {
      setAssistantAnswer(fallbackAssistantAnswer);
    } finally {
      setAssistantBusy(false);
    }
  };

  return (
    <div className="player-screen" dir={cardText.dir}>
      <section className="player-panel player-panel--wide">
        <div className="player-panel__brand">
          <BrandMark compact />
        </div>
        <div className="public-card-layout">
          <article className="public-card">
            <div className="public-card__media">
              <div className="assistant-card">
                <strong>{assistantText.title}</strong>
                <div className="assistant-card__form">
                  <input
                    className="input assistant-input"
                    dir="rtl"
                    onChange={(event) => setAssistantQuestion(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        void handleAskAssistant();
                      }
                    }}
                    placeholder={assistantText.prompt}
                    value={assistantQuestion}
                  />
                  <button
                    className="secondary-button assistant-button"
                    disabled={assistantBusy}
                    onClick={() => {
                      void handleAskAssistant();
                    }}
                    type="button"
                  >
                    {assistantText.button}
                  </button>
                </div>
                <p className="assistant-answer">
                  {assistantBusy
                    ? true
                      ? "جاري التحليل..."
                      : "Thinking..."
                    : assistantAnswer || assistantPlaceholder || assistantText.answerHint}
                </p>
              </div>
              <img
                alt={displayPlayerName}
                className="public-card__image"
                src={summary?.thumbnail?.source || cardPayload.i}
              />
              <div className="card-language-toggle" dir="ltr">
                <button
                  className={`ghost-button card-language-toggle__button ${
                    cardLanguage === "ar" ? "card-language-toggle__button--active" : ""
                  }`}
                  onClick={() => setCardLanguage("ar")}
                  type="button"
                >
                  {cardText.arabic}
                </button>
                <button
                  className={`ghost-button card-language-toggle__button ${
                    cardLanguage === "en" ? "card-language-toggle__button--active" : ""
                  }`}
                  onClick={() => setCardLanguage("en")}
                  type="button"
                >
                  {cardText.english}
                </button>
              </div>
            </div>
            <div className="public-card__copy">
              <h1>{displayPlayerName}</h1>
              <div className="fact-list fact-list--single">
                {displayFacts.map((fact) => (
                  <span key={fact}>{fact}</span>
                ))}
              </div>
              <div className="summary-card">
                <strong className="detail-card__title">{cardText.summary}</strong>
                <p className="summary-extract">{displaySummary}</p>
              </div>
              <div className="detail-card detail-card--clubs-list">
                <strong className="detail-card__title">{cardText.clubs}</strong>
                <ul className="detail-list">
                  {displayClubsFinal.map((club) => (
                    <li key={club}>{club}</li>
                  ))}
                </ul>
              </div>
              {false ? (
                <div className="detail-card">
                  <strong className="detail-card__title">تسلسل الأندية</strong>
                  <p className="detail-card__text">{details?.club_sequence?.join("، ") ?? ""}</p>
                </div>
              ) : null}
              {displayAchievements.length ? (
                <div className="detail-card detail-card--achievements-list">
                  <strong className="detail-card__title">{cardText.achievements}</strong>
                  <ul className="detail-list">
                    {displayAchievements.map((achievement) => (
                      <li key={achievement}>{achievement}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {false ? (
                <div className="detail-card">
                  <strong className="detail-card__title">أهم إنجازاته</strong>
                  <div className="fact-list fact-list--single">
                    {(details?.achievements ?? []).map((achievement) => (
                      <span key={achievement}>{achievement}</span>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          </article>

          <article className="notes-card">
            <div className="notes-card__header">
              <strong>{cardText.notes}</strong>
            </div>
            <textarea
              className="notes-area"
              dir={cardText.dir}
              style={{ textAlign: cardText.dir === "rtl" ? "right" : "left" }}
              onChange={(event) => setNotes(event.target.value)}
              placeholder={cardText.notesPlaceholder}
              value={notes}
            />
            <a className="primary-button primary-button--link" href={displayWikipediaUrl} rel="noreferrer" target="_blank">
              {cardText.wiki}
            </a>
          </article>
        </div>
      </section>
    </div>
  );
}

function SettingsScreen({
  onBack,
  onLogout,
  onSave,
  shareBaseUrl,
  username,
}: {
  onBack: () => void;
  onLogout: () => void;
  onSave: (value: string) => void;
  shareBaseUrl: string;
  username: string | null;
}) {
  const [draft, setDraft] = useState(shareBaseUrl);

  useEffect(() => {
    setDraft(shareBaseUrl);
  }, [shareBaseUrl]);

  return (
    <main className="flow-screen">
      <section className="flow-panel">
        <div className="flow-header">
          <button className="ghost-button" onClick={onBack} type="button">
            ارجع
          </button>
          <div className="flow-title-group">
            <BrandMark compact />
            <div className="flow-title-copy">
              <h2>الرابط</h2>
            </div>
          </div>
        </div>

        <div className="settings-card">
          <div className="settings-account">
            <strong>{username || "minu-admin"}</strong>
            <button className="ghost-button" onClick={onLogout} type="button">
              خروج
            </button>
          </div>
          <input
            className="input"
            onChange={(event) => setDraft(event.target.value)}
            placeholder="الرابط العام"
            value={draft}
          />
          <button className="primary-button" onClick={() => onSave(draft)} type="button">
            حفظ
          </button>
        </div>
      </section>
    </main>
  );
}

function CreditsScreen({ onBack }: { onBack: () => void }) {
  return (
    <main className="flow-screen">
      <section className="flow-panel">
        <div className="flow-header">
          <button className="ghost-button" onClick={onBack} type="button">
            ارجع
          </button>
          <div className="flow-title-group">
            <BrandMark compact />
            <div className="flow-title-copy">
              <h2>شحن</h2>
            </div>
          </div>
        </div>
        <div className="settings-card">
          <strong>قريبًا</strong>
        </div>
      </section>
    </main>
  );
}
