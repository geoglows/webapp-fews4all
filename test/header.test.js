// Runs with `npm test` (node --test). header.js's two exported helpers are pure,
// so the release stamp can be checked without a browser or a map — which matters,
// because the thing it states is the date on a flood forecast.
import {test} from "node:test";
import assert from "node:assert/strict";

import {prettyDate, describeRun} from "../src/ui/header.js";

test("prettyDate reads the date as written, not as UTC", () => {
  // Through Date(), a bare ISO date is UTC midnight and shows as the day before
  // for every user west of Greenwich. This is why the parse is done by hand.
  assert.equal(prettyDate("2026-09-23"), "23 Sep 2026");
  assert.equal(prettyDate("2026-01-05"), "5 Jan 2026");
  assert.equal(prettyDate("2026-12-31"), "31 Dec 2026");
});

test("prettyDate survives a missing date", () => {
  assert.equal(prettyDate(""), "");
  assert.equal(prettyDate(null), "");
  assert.equal(prettyDate(undefined), "");
  assert.equal(prettyDate("not a date"), "not a date");
});

test("an ordinary morning says so in three words", () => {
  const d = describeRun({models: {geoglows: {status: "ok"}, glofas: {status: "ok"}}});
  assert.equal(d.text, "All 2 models");
  assert.equal(d.tone, "ok");
});

test("a model left out of the release is the whole line", () => {
  const d = describeRun({
    models: {
      geoglows: {status: "ok"},
      glofas: {status: "dropped", notes: ["issued 2026-09-20, 3 day(s) behind"]},
    },
  });
  assert.equal(d.tone, "warn");
  assert.match(d.text, /not in this release/);
  assert.match(d.title, /3 day\(s\) behind/);   // the reason lives in the tooltip
});

test("a model with no file counts as missing, not as quiet", () => {
  const d = describeRun({models: {flash: {status: "skipped"}, glofas: {status: "ok"}}});
  assert.equal(d.tone, "warn");
  assert.match(d.text, /not in this release/);
});

test("a thin model warns without claiming to be absent", () => {
  const d = describeRun({
    models: {geoglows: {status: "thin", notes: ["12 against a median of 9,000"]},
             glofas: {status: "ok"}},
  });
  assert.equal(d.tone, "warn");
  assert.match(d.text, /thin/);
  assert.doesNotMatch(d.text, /not in this release/);
});

test("a release with no run record still shows its date", () => {
  // Releases published before run.json existed must not blank the stamp.
  assert.equal(describeRun(null), null);
  assert.equal(describeRun({}), null);
  assert.equal(describeRun({models: {}}), null);
});
