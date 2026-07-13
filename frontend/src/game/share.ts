import type { CardLanguage, WikipediaPlayerDetails, WikipediaSummary } from "./types";

const WIKIPEDIA_API_URLS: Record<CardLanguage, string> = {
  ar: "https://ar.wikipedia.org/w/api.php",
  en: "https://en.wikipedia.org/w/api.php",
};

const WIKIPEDIA_SUMMARY_URLS: Record<CardLanguage, string> = {
  ar: "https://ar.wikipedia.org/api/rest_v1/page/summary",
  en: "https://en.wikipedia.org/api/rest_v1/page/summary",
};

type WikipediaTitleMap = Partial<Record<CardLanguage, string>>;

const MOJIBAKE_PATTERN = /[ÃƒÆ’Ãƒâ€žÃƒâ€¦Ãƒâ€ Ãƒâ€¡ÃƒÂÃƒâ€˜ÃƒËœÃ…â€™Ã…Â Ã…Â½]/;

const SAFE_MOJIBAKE_PATTERN = /[ÃÂâØÙ]/u;

function repairLikelyMojibake(value: string): string {
  if (!SAFE_MOJIBAKE_PATTERN.test(value) && !MOJIBAKE_PATTERN.test(value)) {
    return value;
  }

  try {
    const bytes = Uint8Array.from(Array.from(value).map((character) => character.charCodeAt(0) & 0xff));
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return value;
  }
}

function normalizeWikipediaLookup(value: string): string {
  return repairLikelyMojibake(value).trim();
}

function cleanText(value: string): string {
  return repairLikelyMojibake(value)
    .replace(/\[[^\]]*]/g, " ")
    .replace(/\([^)]*listen[^)]*\)/gi, " ")
    .replace(/\s+/g, " ")
    .replace(/\u00a0/g, " ")
    .trim();
}

function cleanClubName(value: string): string {
  return cleanText(value)
    .replace(/^Ã¢â€ â€™\s*/u, "")
    .replace(/^â†’\s*/u, "")
    .replace(/^→\s*/u, "")
    .replace(/\s*\((?:loan|إعارة)\)\s*$/iu, "")
    .trim();
}

function extractLastYear(value: string): number | null {
  if (/(present|الآن|حتى الآن|حاليًا|حالياً|مستمر)/i.test(value)) {
    return null;
  }

  const fullYears = value.match(/\b(?:19|20)\d{2}\b/g);
  if (fullYears?.length) {
    return Number(fullYears[fullYears.length - 1]);
  }

  const shortRange = value.match(/\b((?:19|20)\d{2})\s*[–—-]\s*(\d{2})\b/);
  if (!shortRange) {
    return null;
  }

  const baseYear = shortRange[1];
  return Number(`${baseYear.slice(0, 2)}${shortRange[2]}`);
}

function normalizeAchievementLabel(value: string): string {
  const cleaned = cleanText(value);
  if (!cleaned) {
    return "";
  }

  const beforeColon = cleaned.split(":")[0]?.trim() ?? "";
  return beforeColon || cleaned;
}

function isGenericAchievementGroup(group: string, language: CardLanguage): boolean {
  if (!group) {
    return true;
  }

  if (language === "ar") {
    return /\u0641\u0631\u062f\u064a\u0629|\u062c\u0648\u0627\u0626\u0632|\u0623\u0648\u0633\u0645\u0629|\u062a\u0643\u0631\u064a\u0645|\u0633\u062c\u0644\u0627\u062a/u.test(group);
  }

  return /\bindividual\b|\bawards?\b|\borders?\b|\bspecial awards?\b|\brecords?\b|\bdistinctions?\b/i.test(group);
}

function shouldSkipGroup(group: string): boolean {
  return /\bU(?:17|18|19|20|21|23)\b|under-\d+/i.test(group);
}

function formatAchievement(group: string, item: string, language: CardLanguage): string {
  const groupLabel = cleanText(group);
  const achievementLabel = normalizeAchievementLabel(item);
  if (!achievementLabel) {
    return "";
  }

  if (!groupLabel) {
    return achievementLabel;
  }

  if (language === "ar") {
    if (/^فردية$/i.test(groupLabel)) {
      return `فردية: ${achievementLabel}`;
    }
    return `${groupLabel}: ${achievementLabel}`;
  }

  if (/^individual$/i.test(groupLabel)) {
    return `Individual: ${achievementLabel}`;
  }

  return `${groupLabel}: ${achievementLabel}`;
}

function formatAchievementDisplay(group: string, item: string, language: CardLanguage): string {
  const groupLabel = cleanText(group);
  const formatted = formatAchievement(group, item, language);
  if (!formatted || !groupLabel) {
    return formatted;
  }

  if (language === "ar") {
    if (/^\u0641\u0631\u062f\u064a\u0629$/i.test(groupLabel) || isGenericAchievementGroup(groupLabel, language)) {
      return formatted;
    }
    return `مع ${groupLabel}: ${normalizeAchievementLabel(item)}`;
  }

  if (/^individual$/i.test(groupLabel) || isGenericAchievementGroup(groupLabel, language)) {
    return formatted;
  }

  return `With ${groupLabel}: ${normalizeAchievementLabel(item)}`;
}

function isCareerHeader(value: string): boolean {
  return /^(Senior career|Club career)$/i.test(value)
    || /(المسيرة|المشوار).*(الأندية|الاحترافية|الكروية)/i.test(value)
    || /الأندية التي لعب لها/i.test(value);
}

function isAchievementHeading(value: string): boolean {
  return /^(Honours|Honors)$/i.test(value)
    || /(الإنجازات|الالقاب|الألقاب|البطولات)/i.test(value);
}

function extractSectionHeaderText(row: Element): string {
  const explicitHeader = cleanText(row.querySelector(".infobox-header")?.textContent ?? "");
  if (explicitHeader) {
    return explicitHeader;
  }

  if (row.querySelector("td")) {
    return "";
  }

  const cells = Array.from(row.children).filter(
    (child): child is HTMLTableCellElement => child instanceof HTMLTableCellElement,
  );
  if (!cells.length) {
    return "";
  }

  const firstCell = cells[0];
  const colspanValue = Number(firstCell.getAttribute("colspan") ?? "1");
  const scopeValue = (firstCell.getAttribute("scope") ?? "").toLowerCase();
  if (colspanValue <= 1 && scopeValue !== "col" && !firstCell.classList.contains("infobox-header")) {
    return "";
  }

  return cleanText(firstCell.textContent ?? "");
}

function isYearLikeCellValue(value: string): boolean {
  return /^years?$/i.test(value)
    || /^السنوات$/i.test(value)
    || /^الموسم$/i.test(value)
    || /^(?:\d{4}|(?:19|20)\d{2})\s*(?:[–—-]\s*(?:\d{2}|\d{4}|present|الآن|حتى الآن|حاليًا|حالياً))?$/iu.test(value);
}

function isStatLikeCellValue(value: string): boolean {
  return /^\(?\d+\)?$/.test(value);
}

function isTeamHeaderLikeValue(value: string): boolean {
  return /^team$/i.test(value)
    || /^club$/i.test(value)
    || /^فريق$/i.test(value)
    || /^النادي$/i.test(value)
    || /^المجموع$/i.test(value)
    || /^total$/i.test(value);
}

function extractClubCellText(row: Element): string {
  const explicitClubCell = row.querySelector("td.infobox-data-a");
  const allCells = Array.from(row.querySelectorAll("td"));
  const preferredClubCell =
    explicitClubCell
    ?? allCells.find((cell) => {
      const cellText = cleanText(cell.textContent ?? "");
      if (!cellText || isYearLikeCellValue(cellText) || isStatLikeCellValue(cellText) || isTeamHeaderLikeValue(cellText)) {
        return false;
      }

      return /[A-Za-z\u0600-\u06FF]/.test(cellText);
    })
    ?? row.querySelector("td");

  if (!preferredClubCell) {
    return "";
  }

  const linkedClubText = cleanClubName(
    Array.from(preferredClubCell.querySelectorAll("a"))
      .map((anchor) => cleanText(anchor.textContent ?? ""))
      .filter(Boolean)
      .join(" "),
  );
  if (linkedClubText) {
    return linkedClubText;
  }

  return cleanClubName(preferredClubCell.textContent ?? "");
}

async function fetchWikipediaSummaryRaw(
  playerName: string,
  language: CardLanguage,
): Promise<WikipediaSummary | null> {
  try {
    const response = await fetch(
      `${WIKIPEDIA_SUMMARY_URLS[language]}/${encodeURIComponent(normalizeWikipediaLookup(playerName))}`,
    );
    if (!response.ok) {
      return null;
    }

    return {
      ...((await response.json()) as WikipediaSummary),
      page_language: language,
    };
  } catch {
    return null;
  }
}

async function fetchWikipediaTitlesFromWikidata(wikidataId: string): Promise<WikipediaTitleMap> {
  const normalizedId = wikidataId.trim();
  if (!normalizedId) {
    return {};
  }

  try {
    const response = await fetch(
      `https://www.wikidata.org/w/api.php?${new URLSearchParams({
        action: "wbgetentities",
        ids: normalizedId,
        props: "sitelinks",
        format: "json",
        origin: "*",
      }).toString()}`,
    );
    if (!response.ok) {
      return {};
    }

    const payload = (await response.json()) as {
      entities?: Record<
        string,
        {
          sitelinks?: {
            arwiki?: { title?: string };
            enwiki?: { title?: string };
          };
        }
      >;
    };
    const sitelinks = payload.entities?.[normalizedId]?.sitelinks;
    return {
      ar: cleanText(sitelinks?.arwiki?.title ?? ""),
      en: cleanText(sitelinks?.enwiki?.title ?? ""),
    };
  } catch {
    return {};
  }
}

async function fetchWikipediaDetailsRaw(
  playerName: string,
  language: CardLanguage,
  clubTranslations?: Map<string, string>,
): Promise<WikipediaPlayerDetails | null> {
  try {
    const response = await fetch(
      `${WIKIPEDIA_API_URLS[language]}?${new URLSearchParams({
        action: "parse",
        page: normalizeWikipediaLookup(playerName),
        prop: "text",
        formatversion: "2",
        format: "json",
        origin: "*",
      }).toString()}`,
    );
    if (!response.ok) {
      return null;
    }

    const payload = (await response.json()) as {
      parse?: {
        text?: string;
      };
    };
    const pageHtml = payload.parse?.text;
    if (!pageHtml) {
      return null;
    }

    const document = new DOMParser().parseFromString(pageHtml, "text/html");
    const root = document.querySelector(".mw-parser-output") ?? document.body;
    const clubsData = extractClubSequence(root, clubTranslations);
    const achievements = extractAchievements(root, language);

    return {
      achievements,
      club_sequence: clubsData.club_sequence,
      retired_year: clubsData.retired_year,
      page_language: language,
    };
  } catch {
    return null;
  }
}

function extractClubSequence(
  root: ParentNode,
  clubTranslations?: Map<string, string>,
): {
  club_sequence: string[];
  retired_year: number | null;
} {
  const infobox = root.querySelector("table.infobox");
  if (!infobox) {
    return {
      club_sequence: [],
      retired_year: null,
    };
  }

  const clubSequence: string[] = [];
  let retiredYear: number | null = null;
  let insideSeniorCareer = false;

  infobox.querySelectorAll("tr").forEach((row) => {
    const sectionHeader = extractSectionHeaderText(row);
    if (sectionHeader) {
      insideSeniorCareer = isCareerHeader(sectionHeader);
      return;
    }

    if (!insideSeniorCareer) {
      return;
    }

    const teamLabel = extractClubCellText(row);
    const yearsLabel = cleanText(row.querySelector("th, td")?.textContent ?? "");
    if (
      !teamLabel
      || isTeamHeaderLikeValue(teamLabel)
      || isYearLikeCellValue(teamLabel)
      || isYearLikeCellValue(yearsLabel)
    ) {
      return;
    }

    const translatedLabel = clubTranslations?.get(teamLabel) ?? teamLabel;
    if (!clubSequence.some((entry) => entry.toLowerCase() === translatedLabel.toLowerCase())) {
      clubSequence.push(translatedLabel);
    }

    const endYear = extractLastYear(yearsLabel);
    if (endYear) {
      retiredYear = endYear;
    }
  });

  return {
    club_sequence: clubSequence,
    retired_year: retiredYear,
  };
}

function extractAchievements(root: ParentNode, language: CardLanguage): string[] {
  const headingNode = Array.from(
    root.querySelectorAll(".mw-heading2, .mw-heading3, h2, h3, .mw-headline"),
  ).find((node) => isAchievementHeading(cleanText(node.textContent ?? "")));

  const sectionStart = headingNode?.closest(".mw-heading2, .mw-heading3, .mw-heading, h2, h3") ?? headingNode;
  if (!sectionStart || !(sectionStart instanceof Element)) {
    return [];
  }

  const groupedAchievements: Array<{ group: string; items: string[] }> = [];
  let currentGroup = "";

  for (let node = sectionStart.nextElementSibling; node; node = node.nextElementSibling) {
    if (node.matches(".mw-heading2, h2")) {
      break;
    }

    if (node.classList.contains("hatnote")) {
      continue;
    }

    if (node.matches(".mw-heading3, .mw-heading4, h3, h4")) {
      const headingText = cleanText(node.textContent ?? "");
      if (headingText) {
        currentGroup = headingText;
      }
      continue;
    }

    if (node.tagName === "P") {
      const boldLabel = cleanText(node.querySelector("b")?.textContent ?? "");
      if (boldLabel && boldLabel.length <= 60) {
        currentGroup = boldLabel;
      }
      continue;
    }

    if (node.tagName !== "UL") {
      continue;
    }

    const items = Array.from(node.children)
      .filter((entry) => entry.tagName === "LI")
      .map((entry) => cleanText(entry.textContent ?? ""))
      .map((entry) => normalizeAchievementLabel(entry))
      .filter(Boolean);

    if (!items.length) {
      continue;
    }

    groupedAchievements.push({
      group: currentGroup,
      items,
    });
  }

  const sourceGroups = groupedAchievements.filter((entry) => !shouldSkipGroup(entry.group));
  const groups = sourceGroups.length >= 3 ? sourceGroups : groupedAchievements;
  const seen = new Set<string>();
  const achievements: string[] = [];

  groups.forEach((groupEntry) => {
    const firstItem = groupEntry.items[0];
    const formatted = formatAchievementDisplay(groupEntry.group, firstItem, language);
    const key = formatted.toLowerCase();
    if (formatted && !seen.has(key) && achievements.length < 6) {
      seen.add(key);
      achievements.push(formatted);
    }
  });

  if (achievements.length >= 4) {
    return achievements;
  }

  groups.forEach((groupEntry) => {
    groupEntry.items.forEach((item) => {
      const formatted = formatAchievementDisplay(groupEntry.group, item, language);
      const key = formatted.toLowerCase();
      if (!formatted || seen.has(key) || achievements.length >= 6) {
        return;
      }
      seen.add(key);
      achievements.push(formatted);
    });
  });

  return achievements;
}

async function fetchLangLinks(
  titles: string[],
  fromLanguage: CardLanguage,
  toLanguage: CardLanguage,
): Promise<Map<string, string>> {
  const cleanedTitles = titles.map((title) => normalizeWikipediaLookup(title)).filter(Boolean);
  if (!cleanedTitles.length || fromLanguage === toLanguage) {
    return new Map();
  }

  try {
    const response = await fetch(
      `${WIKIPEDIA_API_URLS[fromLanguage]}?${new URLSearchParams({
        action: "query",
        titles: cleanedTitles.join("|"),
        prop: "langlinks",
        lllang: toLanguage,
        lllimit: "1",
        redirects: "1",
        formatversion: "2",
        format: "json",
        origin: "*",
      }).toString()}`,
    );
    if (!response.ok) {
      return new Map();
    }

    const payload = (await response.json()) as {
      query?: {
        pages?: Array<{
          title?: string;
          langlinks?: Array<{ title?: string }>;
        }>;
      };
    };

    const map = new Map<string, string>();
    payload.query?.pages?.forEach((page) => {
      const sourceTitle = page.title ? cleanText(page.title) : "";
      const translatedTitle = page.langlinks?.[0]?.title ? cleanText(page.langlinks[0].title ?? "") : "";
      if (sourceTitle && translatedTitle) {
        map.set(sourceTitle, translatedTitle);
      }
    });
    return map;
  } catch {
    return new Map();
  }
}

function readClaimTimeYear(
  claim: {
    qualifiers?: Record<string, Array<{ datavalue?: { value?: { time?: string } } }>>;
  },
  property: string,
): number | null {
  const timeValue = claim.qualifiers?.[property]?.[0]?.datavalue?.value?.time;
  if (!timeValue) {
    return null;
  }

  const matchedYear = timeValue.match(/([+-]?\d{4})/);
  return matchedYear ? Number(matchedYear[1].replace("+", "")) : null;
}

function isLikelyNationalTeamLabel(value: string): boolean {
  return /\bnational\b.*\bteam\b|\bU(?:17|18|19|20|21|23)\b|under-\d+|\u0645\u0646\u062a\u062e\u0628|school|schools|\u0645\u062f\u0627\u0631\u0633/ui.test(value);
}

export async function fetchWikidataClubSequence(
  wikidataId: string,
  language: CardLanguage,
): Promise<{
  clubs: string[];
  retired_year: number | null;
}> {
  const normalizedId = wikidataId.trim();
  if (!normalizedId) {
    return {
      clubs: [],
      retired_year: null,
    };
  }

  try {
    const claimsResponse = await fetch(
      `https://www.wikidata.org/w/api.php?${new URLSearchParams({
        action: "wbgetentities",
        ids: normalizedId,
        props: "claims",
        format: "json",
        origin: "*",
      }).toString()}`,
    );
    if (!claimsResponse.ok) {
      return {
        clubs: [],
        retired_year: null,
      };
    }

    const claimsPayload = (await claimsResponse.json()) as {
      entities?: Record<
        string,
        {
          claims?: Record<
            string,
            Array<{
              rank?: string;
              mainsnak?: {
                datavalue?: {
                  value?: {
                    id?: string;
                  };
                };
              };
              qualifiers?: Record<string, Array<{ datavalue?: { value?: { time?: string } } }>>;
            }>
          >;
        }
      >;
    };

    const claims = claimsPayload.entities?.[normalizedId]?.claims?.P54 ?? [];
    const orderedClaims = claims
      .map((claim, index) => ({
        id: claim.mainsnak?.datavalue?.value?.id ?? "",
        startYear: readClaimTimeYear(claim, "P580"),
        endYear: readClaimTimeYear(claim, "P582"),
        rank: claim.rank ?? "normal",
        index,
      }))
      .filter((entry) => entry.id)
      .sort((left, right) => {
        const leftStart = left.startYear ?? Number.MAX_SAFE_INTEGER;
        const rightStart = right.startYear ?? Number.MAX_SAFE_INTEGER;
        if (leftStart !== rightStart) {
          return leftStart - rightStart;
        }

        const leftEnd = left.endYear ?? Number.MAX_SAFE_INTEGER;
        const rightEnd = right.endYear ?? Number.MAX_SAFE_INTEGER;
        if (leftEnd !== rightEnd) {
          return leftEnd - rightEnd;
        }

        if (left.rank !== right.rank) {
          return left.rank === "preferred" ? -1 : 1;
        }

        return left.index - right.index;
      });

    const uniqueTeamIds = orderedClaims.reduce<string[]>((sequence, claim) => {
      if (!sequence.includes(claim.id)) {
        sequence.push(claim.id);
      }
      return sequence;
    }, []);
    if (!uniqueTeamIds.length) {
      return {
        clubs: [],
        retired_year: null,
      };
    }

    const preferredLanguages = language === "ar" ? "ar|en" : "en|ar";
    const labelsResponse = await fetch(
      `https://www.wikidata.org/w/api.php?${new URLSearchParams({
        action: "wbgetentities",
        ids: uniqueTeamIds.join("|"),
        props: "labels",
        languages: preferredLanguages,
        languagefallback: "1",
        format: "json",
        origin: "*",
      }).toString()}`,
    );
    if (!labelsResponse.ok) {
      return {
        clubs: [],
        retired_year: null,
      };
    }

    const labelsPayload = (await labelsResponse.json()) as {
      entities?: Record<
        string,
        {
          labels?: Record<string, { value?: string }>;
        }
      >;
    };

    const clubs = uniqueTeamIds
      .map((teamId) => {
        const labels = labelsPayload.entities?.[teamId]?.labels ?? {};
        return cleanText(
          labels[language]?.value
            ?? labels.en?.value
            ?? labels.ar?.value
            ?? "",
        );
      })
      .filter(Boolean)
      .filter((label) => !isLikelyNationalTeamLabel(label));

    const retiredYear = orderedClaims.reduce<number | null>((latest, claim) => {
      if (!claim.endYear) {
        return latest;
      }
      if (latest === null || claim.endYear > latest) {
        return claim.endYear;
      }
      return latest;
    }, null);

    return {
      clubs: Array.from(new Set(clubs)),
      retired_year: retiredYear,
    };
  } catch {
    return {
      clubs: [],
      retired_year: null,
    };
  }
}

export async function fetchWikipediaSummary(
  playerName: string,
  language: CardLanguage,
): Promise<WikipediaSummary | null> {
  return fetchWikipediaSummaryRaw(playerName, language);
}

export async function fetchWikipediaPlayerDetails(
  playerName: string,
  language: CardLanguage,
): Promise<WikipediaPlayerDetails | null> {
  return fetchWikipediaDetailsRaw(playerName, language);
}

export async function fetchArabicWikipediaBundle(
  englishName: string,
  arabicName?: string,
  wikidataId?: string,
): Promise<{
  summary: WikipediaSummary | null;
  details: WikipediaPlayerDetails | null;
}> {
  const sitelinks = wikidataId ? await fetchWikipediaTitlesFromWikidata(wikidataId) : {};
  const arabicCandidates = [sitelinks.ar, arabicName, sitelinks.en, englishName]
    .map((value) => normalizeWikipediaLookup(value ?? ""))
    .filter(Boolean);

  for (const candidate of arabicCandidates) {
    const summary = await fetchWikipediaSummaryRaw(candidate, "ar");
    const details = await fetchWikipediaDetailsRaw(summary?.title ?? candidate, "ar");
    if (summary || details) {
      return { summary, details };
    }
  }

  const englishSummary = await fetchWikipediaSummaryRaw(sitelinks.en ?? englishName, "en");
  const englishTitle = englishSummary?.title ?? normalizeWikipediaLookup(sitelinks.en ?? englishName);
  const translatedTitleMap = await fetchLangLinks([englishTitle], "en", "ar");
  const translatedTitle = sitelinks.ar || translatedTitleMap.get(englishTitle);
  if (!translatedTitle) {
    const englishDetails = await fetchWikipediaDetailsRaw(englishTitle, "en");
    const translatedClubs = englishDetails?.club_sequence.length
      ? await fetchLangLinks(englishDetails.club_sequence, "en", "ar")
      : new Map<string, string>();

    return {
      summary: null,
      details: englishDetails
        ? {
            ...englishDetails,
            achievements: [],
            club_sequence: englishDetails.club_sequence
              .map((club) => translatedClubs.get(club) ?? "")
              .filter(Boolean),
            page_language: "ar",
          }
        : null,
    };
  }

  const summary = await fetchWikipediaSummaryRaw(translatedTitle, "ar");
  const details = await fetchWikipediaDetailsRaw(summary?.title ?? translatedTitle, "ar");
  return { summary, details };
}

export async function fetchEnglishWikipediaBundle(
  englishName: string,
  arabicName?: string,
  wikidataId?: string,
): Promise<{
  summary: WikipediaSummary | null;
  details: WikipediaPlayerDetails | null;
}> {
  const sitelinks = wikidataId ? await fetchWikipediaTitlesFromWikidata(wikidataId) : {};
  const englishCandidates = [sitelinks.en, englishName]
    .map((value) => normalizeWikipediaLookup(value ?? ""))
    .filter(Boolean);

  for (const candidate of englishCandidates) {
    const summary = await fetchWikipediaSummaryRaw(candidate, "en");
    const details = await fetchWikipediaDetailsRaw(summary?.title ?? candidate, "en");
    if (summary || details) {
      return { summary, details };
    }
  }

  const arabicTitle = normalizeWikipediaLookup(sitelinks.ar ?? arabicName ?? "");
  if (arabicTitle) {
    const translatedTitleMap = await fetchLangLinks([arabicTitle], "ar", "en");
    const translatedTitle = translatedTitleMap.get(arabicTitle);
    if (translatedTitle) {
      const summary = await fetchWikipediaSummaryRaw(translatedTitle, "en");
      const details = await fetchWikipediaDetailsRaw(summary?.title ?? translatedTitle, "en");
      if (summary || details) {
        return { summary, details };
      }
    }
  }

  return { summary: null, details: null };
}
