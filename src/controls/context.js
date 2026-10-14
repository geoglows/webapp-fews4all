// Context Layers: overlays that sit alongside the hazard rather than describing it.
//
// The ADM boundary layer used to live here too. It is gone: every base map already
// draws national and regional borders, so a second set over them was redundant. The
// geoBoundaries data is still read by the backend for each forecast's "Near"
// district — that is a label, not a layer.
import {setStreamsOn, streamsOn} from "../layers/streams.js";
import {dropdownControl} from "../ui/dropdown.js";
import {check, note} from "../ui/form.js";

export function contextControl() {
  return dropdownControl({
    iconName: "layers",
    title: "Context Layers",
    panelStyle: "padding:10px 12px;min-width:186px;font:600 12px system-ui,sans-serif;color:#0f172a",
    render(panel) {
      // The note used to be an opening <p> tag with nothing in it — whatever it
      // once said had been deleted and the tag left behind. It says the one thing
      // worth saying about this layer: it is the network, not the forecast.
      panel.innerHTML =
        check("ctx-global-streams", "GEOGLOWS V3 Streams", streamsOn(), "#2b7fd4") +
        note("The stream network itself, drawn under the flagged areas. " +
          "It carries no forecast of its own.", 3);

      panel.querySelector("#ctx-global-streams")
        .addEventListener("change", (e) => setStreamsOn(e.target.checked));
    },
  });
}
