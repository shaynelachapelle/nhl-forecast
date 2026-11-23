// Brand colors from TruColor's NHL franchise records:
// https://www.trucolor.net/portfolio/national-hockey-league-official-colors-franchise-records-1917-1918-through-present/
// primary is the team's identity color, secondary the next non-white one.

export type Team = {
  id: number;
  abbrev: string;
  place: string;
  name: string;
  primary: string;
  secondary: string;
  display: { light: string; dark: string };
};

const BRANDS: [number, string, string, string, string, string][] = [
  [1, "NJD", "New Jersey", "Devils", "#C8102E", "#010101"],
  [2, "NYI", "New York", "Islanders", "#003087", "#FC4C02"],
  [3, "NYR", "New York", "Rangers", "#0032A0", "#C8102E"],
  [4, "PHI", "Philadelphia", "Flyers", "#CF4520", "#010101"],
  [5, "PIT", "Pittsburgh", "Penguins", "#010101", "#FFB81C"],
  [6, "BOS", "Boston", "Bruins", "#010101", "#FFB81C"],
  [7, "BUF", "Buffalo", "Sabres", "#003087", "#FFB81C"],
  [8, "MTL", "Montréal", "Canadiens", "#A6192E", "#001E62"],
  [9, "OTT", "Ottawa", "Senators", "#C8102E", "#010101"],
  [10, "TOR", "Toronto", "Maple Leafs", "#00205B", "#00205B"],
  [12, "CAR", "Carolina", "Hurricanes", "#C8102E", "#010101"],
  [13, "FLA", "Florida", "Panthers", "#C8102E", "#041E42"],
  [14, "TBL", "Tampa Bay", "Lightning", "#00205B", "#00205B"],
  [15, "WSH", "Washington", "Capitals", "#C8102E", "#041E42"],
  [16, "CHI", "Chicago", "Blackhawks", "#C8102E", "#010101"],
  [17, "DET", "Detroit", "Red Wings", "#C8102E", "#C8102E"],
  [18, "NSH", "Nashville", "Predators", "#FFB81C", "#041E42"],
  [19, "STL", "St. Louis", "Blues", "#0072CE", "#FFB81C"],
  [20, "CGY", "Calgary", "Flames", "#C8102E", "#F1BE48"],
  [21, "COL", "Colorado", "Avalanche", "#6F263D", "#236192"],
  [22, "EDM", "Edmonton", "Oilers", "#00205B", "#CF4520"],
  [23, "VAN", "Vancouver", "Canucks", "#00205B", "#00843D"],
  [24, "ANA", "Anaheim", "Ducks", "#CF4520", "#010101"],
  [25, "DAL", "Dallas", "Stars", "#00843D", "#010101"],
  [26, "LAK", "Los Angeles", "Kings", "#010101", "#A2AAAD"],
  [28, "SJS", "San Jose", "Sharks", "#006271", "#010101"],
  [29, "CBJ", "Columbus", "Blue Jackets", "#041E42", "#C8102E"],
  [30, "MIN", "Minnesota", "Wild", "#154734", "#A6192E"],
  [52, "WPG", "Winnipeg", "Jets", "#041E42", "#004C97"],
  [53, "ARI", "Arizona", "Coyotes", "#6F263D", "#010101"],
  [54, "VGK", "Vegas", "Golden Knights", "#B9975B", "#333F48"],
  [55, "SEA", "Seattle", "Kraken", "#051C2C", "#9CDBD9"],
  [59, "UTA", "Utah", "Hockey Club", "#010101", "#69B3E7"],
  [68, "UTA", "Utah", "Mammoth", "#010101", "#69B3E7"],
];

const SURFACE = { light: "#F3F6F9", dark: "#141E2A" };
const MIN_CONTRAST = 3;

function channels(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function luminance(hex: string): number {
  const [r, g, b] = channels(hex).map((c) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

function mix(hex: string, toward: string, amount: number): string {
  const a = channels(hex);
  const b = channels(toward);
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * amount));
  return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase()}`;
}

// Primary if it stands out on the surface, else secondary, else primary shifted
// toward black (light theme) or white (dark theme) until it does.
function displayColor(primary: string, secondary: string, theme: "light" | "dark"): string {
  const surface = SURFACE[theme];
  for (const color of [primary, secondary]) {
    if (contrast(color, surface) >= MIN_CONTRAST) return color;
  }
  const toward = theme === "light" ? "#000000" : "#FFFFFF";
  for (let step = 1; step <= 20; step++) {
    const shifted = mix(primary, toward, step / 20);
    if (contrast(shifted, surface) >= MIN_CONTRAST) return shifted;
  }
  return toward;
}

export const TEAMS: ReadonlyMap<number, Team> = new Map(
  BRANDS.map(([id, abbrev, place, name, primary, secondary]) => [
    id,
    {
      id,
      abbrev,
      place,
      name,
      primary,
      secondary,
      display: {
        light: displayColor(primary, secondary, "light"),
        dark: displayColor(primary, secondary, "dark"),
      },
    },
  ]),
);

const UNKNOWN_COLOR = { light: "#5A6879", dark: "#96A3B4" };

// A team the map doesn't know yet (eg. an expansion team) still renders, in neutral grey.
export function team(id: number, abbrev = "???"): Team {
  return (
    TEAMS.get(id) ?? {
      id,
      abbrev,
      place: "",
      name: abbrev,
      primary: UNKNOWN_COLOR.light,
      secondary: UNKNOWN_COLOR.light,
      display: UNKNOWN_COLOR,
    }
  );
}
