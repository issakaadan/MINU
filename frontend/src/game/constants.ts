import type {
  AnswerRuleKey,
  MatchModeKey,
  MatchRound,
  PlayerReveal,
  QuestionCategory,
  QuestionCategoryKey,
} from "./types";

export type MatchModeDefinition = {
  key: MatchModeKey;
  label: string;
  description: string;
  victoryCondition: string;
  scoringHint: string;
};

export const MATCH_MODES: MatchModeDefinition[] = [
  {
    key: "race-to-100",
    label: "سباق 100",
    description: "نقاط",
    victoryCondition: "أول واحد يوصل 100",
    scoringHint: "نقاط",
  },
  {
    key: "lightning-rush",
    label: "الخطفة",
    description: "سريع",
    victoryCondition: "أول واحد ياخذ 3",
    scoringHint: "سريع",
  },
  {
    key: "hot-streak",
    label: "سلسلة الانتصار",
    description: "ورا بعض",
    victoryCondition: "3 ورا بعض",
    scoringHint: "ورا بعض",
  },
  {
    key: "best-of-five",
    label: "أفضل من 5",
    description: "سريع",
    victoryCondition: "3 من 5",
    scoringHint: "سريع",
  },
  {
    key: "marathon-180",
    label: "الماراثون",
    description: "طويل",
    victoryCondition: "أول واحد يوصل 180",
    scoringHint: "طويل",
  },
];

export const ANSWER_RULE_OPTIONS: Array<{ key: AnswerRuleKey; label: string }> = [
  { key: "yes-no-only", label: "نعم/لا" },
  { key: "no-spelling", label: "بدون تهجّي" },
  { key: "one-word-answer", label: "رد قصير" },
  { key: "five-second-reply", label: "5 ثواني" },
  { key: "no-club-hints", label: "بدون نادي" },
  { key: "single-pass", label: "تمريره" },
];

const ANSWER_RULE_LABELS: Record<AnswerRuleKey, string> = {
  "yes-no-only": "نعم/لا",
  "no-spelling": "بدون تهجّي",
  "one-word-answer": "رد قصير",
  "five-second-reply": "5 ثواني",
  "no-club-hints": "بدون نادي",
  "single-pass": "تمريره",
};

const POSITION_LABELS: Record<PlayerReveal["position_group"], string> = {
  goalkeeper: "حارس",
  defender: "مدافع",
  midfielder: "وسط",
  forward: "مهاجم",
  unknown: "غير واضح",
};

export function getModeDefinition(modeKey: MatchModeKey): MatchModeDefinition {
  return MATCH_MODES.find((mode) => mode.key === modeKey) ?? MATCH_MODES[0];
}

export function getCategoryLabel(
  categories: QuestionCategory[],
  key: QuestionCategoryKey,
): string {
  return categories.find((category) => category.key === key)?.label ?? key;
}

export function buildRuleChips(
  round: MatchRound,
  categories: QuestionCategory[],
): string[] {
  const chips: string[] = [
    `${round.question_limit} سؤال`,
    `${round.guess_limit} تخمين`,
  ];

  round.answer_rule_keys.forEach((key) => {
    const label = ANSWER_RULE_LABELS[key];
    if (label) {
      chips.push(label);
    }
  });

  round.prohibited_category_keys.forEach((key) => {
    chips.push(`ممنوع ${getCategoryLabel(categories, key)}`);
  });

  return chips;
}

function shuffle<T>(values: T[]): T[] {
  const copy = [...values];
  for (let index = copy.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [copy[index], copy[swapIndex]] = [copy[swapIndex], copy[index]];
  }
  return copy;
}

function randomCount(min: number, max: number): number {
  if (max <= min) {
    return min;
  }

  return min + Math.floor(Math.random() * (max - min + 1));
}

export function buildRandomTwistSelection(
  difficulty: number,
  categories: QuestionCategory[],
): {
  answerRuleKeys: AnswerRuleKey[];
  prohibitedCategoryKeys: QuestionCategoryKey[];
} {
  const answerRuleCount = randomCount(1, difficulty === 1 ? 2 : 3);
  const blockedCount = randomCount(difficulty >= 3 ? 1 : 0, difficulty === 1 ? 1 : 2);
  const answerRuleKeys = shuffle(ANSWER_RULE_OPTIONS)
    .slice(0, answerRuleCount)
    .map((entry) => entry.key);
  const prohibitedCategoryKeys = shuffle(categories)
    .slice(0, blockedCount)
    .map((entry) => entry.key);

  return {
    answerRuleKeys,
    prohibitedCategoryKeys,
  };
}

export function buildPlayerFacts(player: PlayerReveal): string[] {
  const facts = [
    `الجنسية: ${player.primary_country_ar || player.primary_country}`,
    `المركز: ${POSITION_LABELS[player.position_group]}`,
    `سنة الميلاد: ${player.birth_year}`,
    `الحالة: ${player.is_active ? "نشط" : "معتزل"}`,
  ];

  const teamLabel = player.current_team_ar || player.current_team;
  if (teamLabel) {
    facts.push(`النادي الحالي: ${teamLabel}`);
  }

  return facts;
}

export function positionLabelFromGroup(positionGroup: PlayerReveal["position_group"]): string {
  return POSITION_LABELS[positionGroup];
}
