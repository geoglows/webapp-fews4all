// Context Layers: overlays that sit alongside the hazard rather than describing
// it. Two groups — layers covering the whole map, and layers drawn only for the
// basin you have selected.
import {boundariesOn, setBoundariesOn} from "../layers/boundaries.js";
import {setStreamsOn, streamsOn} from "../layers/streams.js";
import {dropdownControl} from "../ui/dropdown.js";

export function contextControl() {
  const check = (id, label, on, accent) =>
    `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;margin-bottom:3px">` +
    `<input type="checkbox" id="${id}"${on ? " checked" : ""} ` +
    `style="accent-color:${accent || "#0284c7"}">${label}</label>`;

  return dropdownControl({
    iconName: "layers",
    title: "Context Layers",
    panelStyle: "padding:10px 12px;min-width:186px;font:600 12px system-ui,sans-serif;color:#0f172a",
    render(panel) {
      panel.innerHTML =
        check("ctx-global-streams", "TDX-Hydro Streams", streamsOn(), "#2b7fd4") +
        check("ctx-global-boundaries", "ADM Boundaries", boundariesOn(), "#475569") +
        `<p style="font:400 11px system-ui,sans-serif;color:#94a3b8;margin:3px 0 0">` +
        `Detail follows the zoom: boundaries step from countries to regions to districts.</p>`;

      panel.querySelector("#ctx-global-streams")
        .addEventListener("change", (e) => setStreamsOn(e.target.checked));
      panel.querySelector("#ctx-global-boundaries")
        .addEventListener("change", (e) => setBoundariesOn(e.target.checked));
    },
  });
}
