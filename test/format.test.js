// Runs with `npm test` (node --test). format.js imports only config.js, so the
// value formatting can be checked without a browser or a map.
//
// This replaces test/header.test.js, which tested src/ui/header.js — a module
// deleted when the timeline took over the release date. The module went; the
// hazard it was careful about did not, so the assertion follows the behaviour to
// where the behaviour now lives.
import {test} from "node:test";
import assert from "node:assert/strict";

import {fmtValue} from "../src/format.js";

test("a bare date is read as written, not as UTC midnight", () => {
  // `new Date("2026-10-01")` is UTC midnight, which renders as the evening
  // BEFORE anywhere west of Greenwich — so a forecast for the 1st reads as the
  // 30th for every user in the Americas. The parse is done by hand for exactly
  // this reason, and this is the line that notices if that stops being true.
  const ymd = (s) => fmtValue("peak_time", s);
  assert.equal(ymd("2026-10-01"), "Oct 1");
  assert.equal(ymd("2026-01-05"), "Jan 5");
  assert.equal(ymd("2026-12-31"), "Dec 31");
});

test("a date with a clock keeps its clock, and a non-date survives", () => {
  assert.match(fmtValue("peak_time", "2026-10-01T06:00:00Z"), /Oct 1|Sep 30/,
    "an instant is localised, so either side of midnight is correct");
  assert.equal(fmtValue("peak_time", ""), "—");
  assert.equal(fmtValue("peak_time", "not a date"), "not a date");
});

test("flows carry one decimal, and a trickle is not rounded to nothing", () => {
  // A small headwater reach can warn on a flow under 0.1; "0.0 m³/s" beside a
  // warning reads as a bug in the model rather than as a small river.
  assert.equal(fmtValue("flow_cms", "12.04"), "12 m³/s");
  assert.equal(fmtValue("flow_cms", "12.37"), "12.4 m³/s");
  assert.equal(fmtValue("flow_cms", "0.02"), "<0.1 m³/s");
});

test("a return period reads the same whichever model published it", () => {
  // Flood Hub publishes "2.0" where GEOGLOWS publishes "2".
  assert.equal(fmtValue("return_period_yr", "2"), "2-year");
  assert.equal(fmtValue("return_period_yr", "2.0"), "2-year");
  assert.equal(fmtValue("return_period_yr", "1.5"), "1.5-year");
});
