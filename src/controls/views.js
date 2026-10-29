// The Views menu: the named combinations in src/views.js, as one list.
//
// It sits at the TOP of the right-hand column, above Display Options and
// Filters, because it is the control that writes to both — and because it is the
// one a reader who does not yet know the app should meet first. Nothing in it
// nests: the panel opens straight onto the list, and a view is one click from
// there. That was the whole brief, so a sub-tab or a disclosure triangle in this
// file would be the bug.
import {VIEWS, applyView, swatchColors} from "../views.js";
import {dropdownControl} from "../ui/dropdown.js";
import {note, rule} from "../ui/form.js";
import {view} from "../settings.js";

let rebuild = null;    // set once the panel is mounted; see restate()

/** Rebuild the list — the active row follows state set from anywhere else. */
export function restate() {
  if (rebuild) rebuild();
}

// A view's ramps as a row of small blocks, so the name in the list always has the
// colors it stands for beside it. One block per ramp, the middle (danger) rung of
// each: "Agreement levels" means nothing until you can see that it is three.
const swatchStrip = (v) =>
  `<span style="display:inline-flex;border-radius:3px;overflow:hidden;` +
  `border:1px solid #cbd5e1;height:13px;flex:none">` +
  swatchColors(v).map((c) =>
    `<span style="width:13px;height:13px;background:${c}"></span>`).join("") +
  `</span>`;

const row = (v) => {
  const on = view.preset === v.id;
  return `<button type="button" class="view-row" data-view="${v.id}" ` +
    `style="display:block;width:100%;text-align:left;cursor:pointer;` +
    `border:1px solid ${on ? "#0284c7" : "#e2e8f0"};background:${on ? "#f0f9ff" : "#fff"};` +
    `border-radius:7px;padding:7px 9px;margin-bottom:4px;font:inherit;color:#0f172a">` +
    `<span style="display:flex;align-items:center;gap:7px">` +
    swatchStrip(v) +
    `<span style="font-weight:600;font-size:12px">${v.label}</span>` +
    (on ? `<span style="margin-left:auto;font-size:10px;color:#0284c7;font-weight:700">ON</span>` : "") +
    `</span>` +
    `</button>`;
};

function body() {
  // Named plainly, because a view is a statement about what is on the map rather
  // than a theme: the list answers "what do you want to see", not "what color".
  const custom = view.preset === null
    ? note("Custom — Display or Filters has been changed by hand.", 0)
    : "";
  return VIEWS.map(row).join("") +
    rule +
    note("Choose what to see from a pre-determined list of views.", 0) +
    (custom ? `<div style="margin-top:6px">${custom}</div>` : "");
}

export function viewsControl() {
  return dropdownControl({
    iconName: "swatch",
    title: "Views",
    panelStyle: "padding:10px 12px;width:258px;max-height:calc(100vh - 150px);" +
      "overflow-y:auto;font:600 12px system-ui,sans-serif;color:#0f172a",
    render(panel) {
      function renderBody() {
        panel.innerHTML = body();
        panel.querySelectorAll(".view-row").forEach((btn) => {
          btn.addEventListener("click", () => {
            applyView(btn.dataset.view);
            renderBody();          // the ON marker moves to the row just clicked
          });
        });
      }
      rebuild = renderBody;
      renderBody();
    },
  });
}
