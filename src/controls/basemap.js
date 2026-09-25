// Base-map gallery control.
import {BASEMAPS, DEFAULT_BASEMAP, basemapLayerIds} from "../basemaps.js";
import {dropdownControl} from "../ui/dropdown.js";
import {map} from "../map.js";

// Base-map switcher: a gallery of thumbnails.
export function basemapControl() {
  return dropdownControl({
    iconName: "basemap",
    title: "Base Map Layers",
    panelStyle: "padding:0 4px 4px",
    render(panel) {
      panel.innerHTML = BASEMAPS.map((b) =>
        `<button type="button" class="basemap-row" data-basemap="${b.id}">` +
        `<img src="${b.thumb}" alt="" loading="lazy"><span>${b.name}</span></button>`).join("");
      const rows = [...panel.querySelectorAll(".basemap-row")];
      const highlight = (id) => rows.forEach((r) => r.classList.toggle("basemap-selected", r.dataset.basemap === id));
      rows.forEach((r) => r.addEventListener("click", () => {
        BASEMAPS.forEach((b) => {
          const vis = b.id === r.dataset.basemap ? "visible" : "none";
          basemapLayerIds(b).forEach((lid) => map.setLayoutProperty(lid, "visibility", vis));
        });
        highlight(r.dataset.basemap);
      }));
      highlight(DEFAULT_BASEMAP);
    },
  });
}
