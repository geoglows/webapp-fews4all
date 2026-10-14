// The pointer tool: pan or select, as two buttons under the zoom.
//
// Select is the map as it has always behaved — hover reads out, click fills the
// panel. Pan is the same map with both switched off, so a drag across the world
// cannot pick a cell up on the way. The mode is enforced in map.js's one pointer
// dispatcher; this file only says which one is chosen.
//
// Built as a bare MapLibre control rather than through ui/dropdown.js: this is a
// two-state tool that should be one click away at all times, not a menu. Giving it
// MapLibre's own `maplibregl-ctrl-group` class makes it inherit the zoom buttons'
// box, border and sizing, so it sits under them as if it had always been there —
// which also means the buttons stay 29px squares and the names live in a hover
// label, the way the dropdown controls name themselves (see ui/dropdown.js).
import {icon} from "../icons.js";
import {setPointerMode} from "../map.js";
import {view} from "../settings.js";

// `size` is per mode because the two glyphs come from different sets and do not
// fill their boxes equally: the Calcite cursor sits on a 16px grid with margin
// around it, the Heroicons hand on a 24px one. Matching them by eye rather than by
// number is what makes the pair look like one control.
const MODES = [
  {key: "select", label: "Select", iconName: "cursor", size: 19},
  {key: "pan", label: "Pan", iconName: "hand-raised", size: 16},
];

const ACTIVE_BG = "#e0f2fe";      // sky-100, the app's accent at its lightest
const ACTIVE_FG = "#0369a1";      // sky-700

// Matches the dropdown controls' label exactly, so every named thing on the map
// names itself the same way. Opens to the RIGHT: this control is in a left corner.
const TIP_CSS =
  "position:absolute;left:36px;transform:translateY(-50%);white-space:nowrap;" +
  "background:#0f172a;color:#fff;font:600 11px system-ui,sans-serif;padding:3px 7px;" +
  "border-radius:5px;pointer-events:none;opacity:0;transition:opacity .1s;" +
  "box-shadow:0 1px 4px rgb(0 0 0 / .3)";

export function pointerControl() {
  return {
    onAdd() {
      const el = document.createElement("div");
      el.className = "maplibregl-ctrl maplibregl-ctrl-group";
      // The tips are positioned against this box.
      el.style.position = "relative";

      const buttons = [];
      const tips = [];
      for (const m of MODES) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "pointer-mode";
        btn.dataset.mode = m.key;
        btn.setAttribute("aria-label", m.label);
        btn.setAttribute("aria-pressed", "false");
        btn.style.cssText =
          "display:flex;align-items:center;justify-content:center;" +
          `font-size:${m.size}px`;
        btn.innerHTML = icon(m.iconName);

        const tip = document.createElement("div");
        tip.textContent = m.label;
        tip.style.cssText = TIP_CSS;

        // Measured rather than assumed: the button's own offset keeps the label
        // centred on it whatever MapLibre's stylesheet decides a button is.
        btn.addEventListener("mouseenter", () => {
          tip.style.top = `${btn.offsetTop + btn.offsetHeight / 2}px`;
          tip.style.opacity = "1";
        });
        btn.addEventListener("mouseleave", () => { tip.style.opacity = "0"; });

        buttons.push(btn);
        tips.push(tip);
      }

      const paint = () => {
        for (const b of buttons) {
          const on = b.dataset.mode === view.pointerMode;
          b.style.background = on ? ACTIVE_BG : "";
          b.style.color = on ? ACTIVE_FG : "";
          b.setAttribute("aria-pressed", String(on));
        }
      };
      for (const b of buttons) {
        b.addEventListener("click", () => {
          setPointerMode(b.dataset.mode);
          paint();
        });
      }
      paint();

      // Buttons first and adjacent, so MapLibre's `button + button` divider rule
      // still finds them; the labels go after, outside the stack.
      el.append(...buttons, ...tips);
      this._el = el;
      return el;
    },
    onRemove() {
      if (this._el) this._el.remove();
      this._el = null;
    },
  };
}
