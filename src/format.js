// Pure helpers: value formatting, human labels, deep links, severity ranking, and
// the colour maths. Depends only on config.js, so it is safe to import anywhere.
import {FLASH_TIER, LINK_ZOOM, MODEL_LABELS, SEVERITY, STREAM_ALL_ZOOM} from "./config.js";

// The model whose forecast set the cell's worst severity — it owns the colour
// of an undivided cell. Ties go to the first model in PANEL_MODELS order.
export function ownerModel(forecasts) {
  let best = null, bestRank = -1;
  for (const fc of forecasts) {
    const info = SEVERITY[(fc.severity || "").toLowerCase()];
    const r = info ? info.rank : -1;
    if (r > bestRank) { bestRank = r; best = fc.model; }
  }
  return best;
}

// A darker shade of a hex colour (multiply each channel).
export function darken(hex, f = 0.55) {
  const n = parseInt(hex.slice(1), 16);
  const c = (sh) => Math.round(((n >> sh) & 255) * f).toString(16).padStart(2, "0");
  return "#" + c(16) + c(8) + c(0);
}
export function streamMinOrder(z) {
  return z >= STREAM_ALL_ZOOM ? 1 : Math.max(1, STREAM_ALL_ZOOM - Math.floor(z) + 1);
}

// Chance label from polygon_type ("likely" -> "Likely"), via the shared legend.
export const flashChanceLabel = (t) => (FLASH_TIER[t] || {}).label || t || "—";

// Human "Near" value for a river forecast (a single reach/gauge point, so it sits
// in exactly one district): the district, else the country, else a dash.
// How a "Near" value is qualified when it did not come from a district: the
// pipeline falls back ADM2 -> ADM1 -> ADM0, and a country named as if it were a
// district would overstate how precisely the forecast is located.
const NEAR_LEVEL = {1: "region", 0: "country"};

export function nearLabel(name, count, country, level) {
  name = (name || "").trim();
  const n = Number(count) || 0;
  country = (country || "").trim();
  if (name && n > 1) return `${name} + ${n - 1} district${n - 1 === 1 ? "" : "s"}`;
  if (!name) return country || "—";
  const qualifier = NEAR_LEVEL[level];
  return qualifier ? `${name} (${qualifier})` : name;
}

// A short comma list, truncated after three (e.g. "India, Nepal, China +2").
function shortList(arr) {
  const a = (arr || []).filter(Boolean);
  return a.length <= 3 ? a.join(", ") : `${a.slice(0, 3).join(", ")} +${a.length - 3}`;
}

// Tiered "Near" value for a flash polygon, from the ADM1/ADM2 facts build_flash
// stamps on it: the country names when it spans more than one country, the ADM1
// region (with a count) when it spans several regions of one country, otherwise
// the ADM2 district (with a count of the extra districts it touches).
export function flashNearLabel(p) {
  const countries = (p.countries || []).filter(Boolean);
  const district = (p.district || "").trim();
  const region = (p.region || "").trim();
  const dc = Number(p.district_count) || 0;
  const rc = Number(p.region_count) || 0;
  if (countries.length > 1) return shortList(countries);
  if (rc > 1 && region) return `${region} + ${rc - 1} region${rc - 1 === 1 ? "" : "s"}`;
  if (district) return dc > 1 ? `${district} + ${dc - 1} district${dc - 1 === 1 ? "" : "s"}` : district;
  if (region) return region;
  return countries[0] || "—";
}

// Format an ISO issue time in the viewer's local timezone, e.g. "Aug 20, 12:00 MDT".
export function fmtFlashIssued(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso);
  return d.toLocaleString(undefined, {month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", hour12: false, timeZoneName: "short"});
}

// Compact counts for the impact tiles: 8_181_280 -> "8.2M".
export function fmtCount(n) {
  if (n === null || n === undefined || !isFinite(n)) return "—";
  if (n >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
  return Math.round(n).toLocaleString();
}

export function fmtValue(key, val) {
  if (val === undefined || val === null || val === "") return "—";
  if (key === "returnPeriodYr") return val + "-year";
  if (key === "peakDischargeCms") return val + " m³/s";
  if (key.endsWith("Time")) {
    // A bare YYYY-MM-DD is a forecast DAY, not an instant — GloFAS counts its
    // horizon in whole days. `new Date("2026-10-01")` is parsed as UTC midnight,
    // which renders as the evening BEFORE anywhere west of Greenwich, so the day
    // is built in local time and shown without a clock it does not have.
    const ymd = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(val).trim());
    if (ymd) {
      return new Date(+ymd[1], +ymd[2] - 1, +ymd[3])
        .toLocaleDateString(undefined, {month: "short", day: "numeric"});
    }
    const d = new Date(val);
    if (!isNaN(d)) {
      return d.toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
        hour12: false, timeZoneName: "short",
      });
    }
  }
  return String(val);
}

export function modelLabel(m) {
  return MODEL_LABELS[m] || m.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function modelLink(fc) {
  const lat = Number(fc && fc.lat);
  const lon = Number(fc && fc.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  const m = String((fc && fc.model) || "").toLowerCase();
  if (m === "geoglows") {
    // Hydroviewer is location-only; centering on the reach shows the stream.
    return `https://hydroviewer.geoglows.org/#lon=${lon}&lat=${lat}&zoom=${LINK_ZOOM.geoglows}&definition=`;
  }
  if (m === "flood_hub") {
    // /l/{lat}/{lng}/{zoom}[/g/{gaugeId}] — the id pins the specific gauge.
    const base = `https://sites.research.google/floods/l/${lat}/${lon}/${LINK_ZOOM.flood_hub}`;
    return fc.riverId ? `${base}/g/${encodeURIComponent(fc.riverId)}` : base;
  }
  // GloFAS has no per-point URL scheme; the caller falls back to MODEL_HOME.
  return null;
}

export function worstSeverity(forecasts) {
  let best = "", bestRank = -1;
  for (const fc of forecasts) {
    const info = SEVERITY[(fc.severity || "").toLowerCase()];
    const r = info ? info.rank : -1;
    if (r > bestRank) {
      bestRank = r;
      best = (fc.severity || "").toLowerCase();
    }
  }
  return best;
}
