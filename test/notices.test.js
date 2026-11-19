// Runs with `npm test` (node --test). notices.js is pure and imports nothing, so
// the awkward combinations can be exercised straight from node — which is the
// reason the decisions were lifted out of panel.js in the first place.
//
// Every case below is a sentence the panel actually said to someone, wrongly.
import {test} from "node:test";
import assert from "node:assert/strict";

import {askedLevels, emptyAgreement, hiddenSetHeading, nothingToShowBody}
  from "../src/notices.js";

const COUNTS = [2, 3];

test("a floor asks one question, not one per level above it", () => {
  // The bug: "2 or more agree" treated level 3 as separately asked for, so on a
  // day with 217 two-model cells and no three-model one, the panel raised an
  // amber "No 3-model agreement — nothing overlaps today" over a map full of
  // agreement. Amber is reserved for news about the data; this was its opposite.
  assert.deepEqual(askedLevels({concurMin: 2, concurHeat: true}, COUNTS), [2]);
  assert.deepEqual(
    emptyAgreement({concurMin: 2, concurHeat: true}, {2: 217, 3: 0}, COUNTS), [],
    "two-model agreement exists, so the banner stays quiet");
});

test("...and still speaks when nothing reaches the floor", () => {
  assert.deepEqual(
    emptyAgreement({concurMin: 2}, {2: 0, 3: 0}, COUNTS), [2]);
  assert.deepEqual(
    emptyAgreement({concurMin: 3}, {2: 217, 3: 0}, COUNTS), [3],
    "asking for all three models is a claim about level 3 alone");
});

test("the heat ramp keys every level, so it asks about every level", () => {
  assert.deepEqual(askedLevels({concurHeat: true}, COUNTS), [2, 3]);
  assert.deepEqual(
    emptyAgreement({concurHeat: true}, {2: 217, 3: 0}, COUNTS), [3]);
});

test("no agreement filter at all asks nothing", () => {
  assert.deepEqual(askedLevels({}, COUNTS), []);
  assert.deepEqual(emptyAgreement({}, {2: 0, 3: 0}, COUNTS), [],
    "a reader who has not asked about agreement is not told about it");
});

test("a floor outside the known levels asks nothing rather than guessing", () => {
  assert.deepEqual(askedLevels({concurMin: 4}, COUNTS), []);
});

test("a hidden set is counted, not assumed to be one of two", () => {
  assert.equal(hiddenSetHeading(["Extreme"], ["Warning", "Danger"]).title,
               "Extreme only");
  // The flash copy said "<first shown> only" for any non-empty set. True with
  // two tiers; a lie the day a third arrives.
  assert.equal(
    hiddenSetHeading(["Likely", "Possible"], ["Highly likely"],
                     {filteredLabel: "Tiers filtered"}).title,
    "Tiers filtered");
  assert.equal(hiddenSetHeading([], ["Likely", "Highly likely"]).kind, "none");
  assert.equal(hiddenSetHeading(["Warning", "Danger", "Extreme"], []), null,
               "nothing hidden means no notice at all");
});

test("'Nothing to show' blames the filters only when there are filters", () => {
  assert.match(nothingToShowBody(["2+ models must agree", "Warning hidden"]),
               /^Your filters leave no areas/);
  // A day the models simply did not reach has no filter to blame, and blaming
  // one is the exact misreading these notices exist to prevent.
  const quiet = nothingToShowBody([]);
  assert.doesNotMatch(quiet, /filters leave/);
  assert.match(quiet, /No filter is hiding them/);
});
