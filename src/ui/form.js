// The handful of markup helpers the map's dropdown menus are built from. Plain
// strings, no state, no knowledge of floods: a heading, a divider, a checkbox and
// a footnote, styled once so two menus side by side cannot drift apart.
//
// They live here rather than in either menu because Display Options and Filters
// are read together — one above the other in the same column — and a heading that
// is 10px in one and 11px in the other reads as a bug in the app, not a detail.

/** A small uppercase block heading. */
export const head = (t) =>
  `<div style="font-size:10px;color:#64748b;text-transform:uppercase;` +
  `letter-spacing:.04em;margin:0 0 5px">${t}</div>`;

/** The full-width divider between blocks. */
export const rule = `<div style="height:1px;background:#e2e8f0;margin:11px 0"></div>`;

/** A labelled checkbox. `accent` overrides the sky-blue tick. */
export const check = (id, label, on, accent) =>
  `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;margin-bottom:3px">` +
  `<input type="checkbox" id="${id}"${on ? " checked" : ""} ` +
  `style="accent-color:${accent || "#0284c7"}">${label}</label>`;

/** One option of a radio group. Same metrics as `check` so a menu holding both
 * does not look like two menus. `name` is the group; only one of them is on. */
export const radio = (name, id, label, on) =>
  `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;margin-bottom:3px">` +
  `<input type="radio" name="${name}" id="${id}"${on ? " checked" : ""} ` +
  `style="accent-color:#0284c7">${label}</label>`;

/** Explanatory small print under a control. `top` is its margin above, in px. */
export const note = (text, top = 5) =>
  // #64748b (slate-500), not #94a3b8 (slate-400): on the white menu panels that
// was 2.56:1, well under the 4.5:1 AA wants for 11px text. This helper is the
// small print in Views, Filters and all three Display tabs, so it was the most
// repeated instance of the problem in the app.
  `<p style="font:400 11px system-ui,sans-serif;color:#64748b;margin:${top}px 0 0;` +
  `line-height:1.45">${text}</p>`;

/** The full-width button a menu's own reset sits in. */
export const resetButton = (id, label) =>
  `<button type="button" id="${id}" style="width:100%;border:1px solid #cbd5e1;` +
  `background:#fff;padding:6px 8px;border-radius:6px;cursor:pointer;color:#334155;` +
  `font:inherit">${label}</button>`;
