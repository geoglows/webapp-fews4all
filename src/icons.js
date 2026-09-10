// SVG icons, inlined into the bundle at build time via Vite's `?raw` (which
// imports a file's contents as a string) — no icon font, no web component, no
// runtime fetch. Heroicons are outline glyphs (stroke="currentColor"); Esri
// Calcite icons are filled and ship no `fill`, so their names go in FILL_ICONS
// to get fill="currentColor" injected. Add an import + a line in ICONS to use one.
import arrowTopRightOnSquare from "heroicons/24/outline/arrow-top-right-on-square.svg?raw";
import bolt from "heroicons/24/outline/bolt.svg?raw";
import buildingOffice2 from "heroicons/24/outline/building-office-2.svg?raw";
import chartBar from "heroicons/24/outline/chart-bar.svg?raw";
import chevronDown from "heroicons/24/outline/chevron-down.svg?raw";
import clock from "heroicons/24/outline/clock.svg?raw";
import exclamationTriangle from "heroicons/24/outline/exclamation-triangle.svg?raw";
import funnel from "heroicons/24/outline/funnel.svg?raw";
import magnifyingGlass from "heroicons/24/outline/magnifying-glass.svg?raw";
import users from "heroicons/24/outline/users.svg?raw";

// Esri Calcite UI icons (@esri/calcite-ui-icons) — filled 16px glyphs.
import calciteBasemap from "@esri/calcite-ui-icons/icons/basemap-16.svg?raw";
import calciteH3Hexagon from "@esri/calcite-ui-icons/icons/h3-hexagon-16.svg?raw";
import calciteLayers from "@esri/calcite-ui-icons/icons/layers-16.svg?raw";
import calciteAreaHashFilled from "@esri/calcite-ui-icons/icons/area-hash-filled-16.svg?raw";
import calciteCarTravelMode from "@esri/calcite-ui-icons/icons/car-travel-mode-16.svg?raw";
import calciteTrainTravelMode from "@esri/calcite-ui-icons/icons/train-travel-mode-16.svg?raw";
import calciteLasso from "@esri/calcite-ui-icons/icons/lasso-16.svg?raw";

const ICONS = {
  "arrow-top-right-on-square": arrowTopRightOnSquare,
  "bolt": bolt,
  "building-office-2": buildingOffice2,
  "chart-bar": chartBar,
  "chevron-down": chevronDown,
  "clock": clock,
  "exclamation-triangle": exclamationTriangle,
  "funnel": funnel,
  "magnifying-glass": magnifyingGlass,
  "users": users,
  // Esri Calcite icons
  "basemap": calciteBasemap,
  "h3-hexagon": calciteH3Hexagon,
  "layers": calciteLayers,
  "area-hash-filled":calciteAreaHashFilled,
  "car-travel-mode":calciteCarTravelMode,
  "train-travel-mode":calciteTrainTravelMode,
  "lasso": calciteLasso
};

// Filled icons whose paths carry no `fill` (so they'd default to black): inject
// fill="currentColor" for these so they inherit the surrounding text colour. The
// stroke-based heroicons must NOT get it (it would fill their outlines solid).
const FILL_ICONS = new Set(["basemap", "h3-hexagon", "layers","area-hash-filled","car-travel-mode","train-travel-mode","lasso"]);

// Returns the icon as an HTML string, sized in `em` so the surrounding
// font-size class still controls it and `currentColor` still inherits — the two
// things call sites relied on before. Names are literals in our own source, so
// an unknown one is a typo worth failing loudly on.
export function icon(name, cls = "") {
  const svg = ICONS[name];
  if (!svg) throw new Error(`Unknown icon: ${name}`);
  const attrs = `width="1em" height="1em" focusable="false"` +
    (FILL_ICONS.has(name) ? ` fill="currentColor"` : "") +
    ` class="inline-block shrink-0${cls ? " " + cls : ""}"`;
  return svg.replace("<svg", `<svg ${attrs}`);
}
