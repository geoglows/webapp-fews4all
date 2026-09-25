// The icon-button dropdown used by every map control. Generic: it knows nothing
// about floods, layers or datasets.
import {icon} from "../icons.js";

// ---- Shared dropdown controls (top-right column) --------------------------
// Each is an icon button that opens a panel to its left. Opening one closes the
// others; the button's `title` shows the tool's name on hover.
const openDropdowns = [];
function closeOtherDropdowns(self) {
  openDropdowns.forEach((close) => { if (close !== self) close(); });
}

export function dropdownControl(opts) {
  // opts: { iconName, title, side?, panelStyle?, render(panel), onReady?(container) }
  // side "left" = control sits in a left corner, so its panel/tip open to the
  // right; default (any other value) opens to the left for right-corner controls.
  const openRight = opts.side === "left";
  const panelAnchor = openRight ? "left:34px" : "right:34px";
  const tipAnchor = openRight ? "left:36px" : "right:36px";
  let container = null, panel = null, closeOnDoc = null;
  const close = () => { if (panel && !panel.hidden) panel.hidden = true; };
  function toggle() {
    if (panel.hidden) { closeOtherDropdowns(close); panel.hidden = false; }
    else close();
  }
  return {
    onAdd() {
      container = document.createElement("div");
      container.className = "maplibregl-ctrl";
      container.style.cssText = "position:relative;background:#fff;border-radius:4px;box-shadow:0 0 0 2px rgb(0 0 0 / .1)";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("aria-label", opts.title);
      btn.style.cssText = "display:flex;align-items:center;justify-content:center;width:29px;height:29px;cursor:pointer;background:none;border:0";
      btn.innerHTML = icon(opts.iconName, "text-[19px] text-slate-700");
      panel = document.createElement("div");
      panel.hidden = true;
      panel.style.cssText = "position:absolute;top:0;" + panelAnchor + ";background:#fff;border-radius:6px;" +
        "box-shadow:0 1px 6px rgb(0 0 0 / .3);" + (opts.panelStyle || "padding:6px");
      // Name label that pops up alongside on hover (native title is too slow).
      const tip = document.createElement("div");
      tip.textContent = opts.title;
      tip.style.cssText = "position:absolute;" + tipAnchor + ";top:50%;transform:translateY(-50%);white-space:nowrap;" +
        "background:#0f172a;color:#fff;font:600 11px system-ui,sans-serif;padding:3px 7px;border-radius:5px;" +
        "pointer-events:none;opacity:0;transition:opacity .1s;box-shadow:0 1px 4px rgb(0 0 0 / .3)";
      btn.addEventListener("mouseenter", () => { if (panel.hidden) tip.style.opacity = "1"; });
      btn.addEventListener("mouseleave", () => { tip.style.opacity = "0"; });
      container.append(btn, panel, tip);
      opts.render(panel);
      btn.addEventListener("click", toggle);
      container.addEventListener("click", (e) => e.stopPropagation());
      closeOnDoc = () => close();
      document.addEventListener("click", closeOnDoc);
      openDropdowns.push(close);
      if (opts.onReady) opts.onReady(container, panel);
      return container;
    },
    onRemove() {
      document.removeEventListener("click", closeOnDoc);
      const i = openDropdowns.indexOf(close);
      if (i >= 0) openDropdowns.splice(i, 1);
      container.remove();
    },
  };
}
