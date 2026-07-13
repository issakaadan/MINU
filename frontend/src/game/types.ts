export type QuestionCategoryKey =
  | "country"
  | "continent"
  | "position"
  | "activity"
  | "birth_range";

export type AnswerRuleKey =
  | "yes-no-only"
  | "no-spelling"
  | "one-word-answer"
  | "five-second-reply"
  | "no-club-hints"
  | "single-pass";

export type QuestionOption = {
  value: string;
  label: string;
};

export type QuestionCategory = {
  key: QuestionCategoryKey;
  label: string;
  description: string;
  options: QuestionOption[];
};

export type DifficultyLevel = {
  level: number;
  label: string;
  description: string;
  base_points: number;
  typical_question_limit: number;
  typical_guess_limit: number;
  image_mode: string;
};

export type GameOverview = {
  total_players: number;
  active_players: number;
  retired_players: number;
  represented_countries: number;
  question_categories: QuestionCategory[];
  difficulty_levels: DifficultyLevel[];
};

export type ShareLinkRead = {
  public_url: string | null;
};

export type AuthSessionRead = {
  authenticated: boolean;
  username: string | null;
};

export type AdminDifficultyStat = {
  level: number;
  label: string;
  description: string;
  player_count: number;
  fame_min: number;
  fame_max: number;
};

export type AdminMatchSeat = {
  seat: number;
  player_name: string;
  score: number;
  rounds_won: number;
  current_streak: number;
};

export type AdminMatchSummary = {
  match_id: string;
  mode_key: MatchModeKey;
  status: "active" | "completed";
  winner_seat: number | null;
  round_number: number;
  difficulty: number;
  difficulty_label: string;
  points_for_win: number;
  question_limit: number;
  guess_limit: number;
  updated_at: string;
  seats: AdminMatchSeat[];
};

export type AdminRuntime = {
  public_base_url: string | null;
  runtime_root: string;
  data_dir: string;
  database_path: string;
  dataset_path: string;
  credentials_file_path: string;
  secret_file_path: string;
  session_cookie_name: string;
  session_ttl_hours: number;
  card_link_ttl_hours: number;
  database_size_bytes: number;
};

export type AdminOverview = {
  username: string;
  total_players: number;
  active_players: number;
  retired_players: number;
  represented_countries: number;
  represented_continents: number;
  players_with_images: number;
  players_with_arabic_names: number;
  total_matches: number;
  active_matches: number;
  completed_matches: number;
  difficulty_stats: AdminDifficultyStat[];
  recent_matches: AdminMatchSummary[];
  catalog_refresh: AdminCatalogRefresh | null;
  runtime: AdminRuntime;
};

export type AdminPlayer = {
  id: number;
  wikidata_id: string;
  name: string;
  name_ar: string;
  image_url: string;
  difficulty: number;
  fame_score: number;
  birth_year: number;
  gender_key: string;
  position_group: PlayerReveal["position_group"];
  is_active: boolean;
  countries: string[];
  countries_ar: string[];
  continents: string[];
  continents_ar: string[];
  positions: string[];
  positions_ar: string[];
  aliases: string[];
  current_team: string;
  current_team_ar: string;
  admin_locked: boolean;
  created_at: string;
};

export type AdminPlayersPage = {
  total: number;
  offset: number;
  limit: number;
  items: AdminPlayer[];
};

export type AdminPlayerWritePayload = {
  wikidata_id: string;
  name: string;
  name_ar: string;
  image_url: string;
  difficulty: number;
  fame_score: number;
  birth_year: number;
  gender_key?: string;
  position_group: "goalkeeper" | "defender" | "midfielder" | "forward";
  is_active: boolean;
  countries: string[];
  countries_ar: string[];
  continents: string[];
  continents_ar: string[];
  positions: string[];
  positions_ar: string[];
  aliases: string[];
  current_team: string;
  current_team_ar: string;
  admin_locked: boolean;
};

export type AdminPlayerMutationRead = {
  player: AdminPlayer;
  total_players: number;
};

export type AdminDeleteRead = {
  deleted_id: number;
  total_players: number;
};

export type AdminCatalogRefresh = {
  refreshed_at: string | null;
  scanned_players: number;
  updated_players: number;
  removed_players: number;
  locked_players: number;
  total_players: number;
};

export type AuthLoginPayload = {
  username: string;
  password: string;
};

export type PlayerCardTokenRead = {
  token: string;
};

export type MatchModeKey =
  | "race-to-100"
  | "lightning-rush"
  | "hot-streak"
  | "best-of-five"
  | "marathon-180";

export type PlayerReveal = {
  id: number;
  wikidata_id: string;
  name: string;
  name_ar: string;
  image_url: string;
  primary_country: string;
  primary_country_ar: string;
  continents: string[];
  continents_ar: string[];
  birth_year: number;
  gender_key: string;
  position_group: "goalkeeper" | "defender" | "midfielder" | "forward" | "unknown";
  is_active: boolean;
  current_team: string;
  current_team_ar: string;
  difficulty: number;
  fame_score: number;
  positions: string[];
  positions_ar: string[];
};

export type MatchCreatePayload = {
  difficulty: number;
  mode_key: MatchModeKey;
  player_names: [string, string];
  recent_player_ids: number[];
  selected_answer_rule_keys: AnswerRuleKey[];
  selected_prohibited_category_keys: QuestionCategoryKey[];
};

export type MatchSeat = {
  seat: number;
  player_id: number;
  player_name: string;
  score: number;
  rounds_won: number;
  current_streak: number;
};

export type MatchRound = {
  round_number: number;
  difficulty: number;
  difficulty_label: string;
  starting_seat: number;
  image_mode: string;
  points_for_win: number;
  question_limit: number;
  guess_limit: number;
  twist_keys: string[];
  answer_rule_keys: AnswerRuleKey[];
  prohibited_category_keys: QuestionCategoryKey[];
  allowed_category_keys: QuestionCategoryKey[];
  awarded_to: number | null;
  resolved: boolean;
};

export type MatchRead = {
  match_id: string;
  match_token: string;
  recent_player_ids: number[];
  mode_key: MatchModeKey;
  status: "active" | "completed";
  winner_seat: number | null;
  seats: MatchSeat[];
  round: MatchRound;
  updated_at: string;
};

export type AwardRoundPayload = {
  seat: number;
};

export type PlayerSecret = {
  match_id: string;
  mode_key: MatchModeKey;
  status: "active" | "completed";
  winner_seat: number | null;
  seat: number;
  player_name: string;
  opponent_name: string;
  round: MatchRound;
  player: PlayerReveal;
  wikipedia_url: string;
  updated_at: string;
};

export type SharedPlayerCardPayload = {
  m: string;
  r: number;
  s: number;
  pn: string;
  on: string;
  mk: MatchModeKey;
  n: string;
  na: string;
  i: string;
  c: string;
  ce: string;
  p: PlayerReveal["position_group"];
  y: number;
  a: 0 | 1;
  ct: string;
  cta: string;
  wd?: string;
};

export type CardLanguage = "ar" | "en";

export type WikipediaSummary = {
  title: string;
  page_language?: CardLanguage;
  description?: string;
  extract?: string;
  thumbnail?: {
    source: string;
  };
  content_urls?: {
    desktop?: {
      page: string;
    };
    mobile?: {
      page: string;
    };
  };
};

export type WikipediaPlayerDetails = {
  achievements: string[];
  club_sequence: string[];
  retired_year: number | null;
  page_language?: CardLanguage;
};
