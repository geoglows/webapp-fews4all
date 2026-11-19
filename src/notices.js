/**
 * What the panel's filter notices SAY, with no DOM in sight.
 *
 * The notices are the only part of the app that speaks to the reader in
 * sentences, and a sentence that is subtly wrong is worse than no sentence: it
 * sends someone looking for a bug that is not there, or tells them the day is
 * quiet when their own filter emptied the map. The wording is argued over in
 * panel.js; the DECISIONS are here, pure and testable, because every one of
 * them has been wrong at least once.
 *
 * Nothing here imports the map, the release or the view. Callers pass plain
 * values in and get plain values back, which is what lets test/notices.test.js
 * run the awkward combinations — a level nobody asked for, a tier set that
 * grew, an empty day with no filter to blame — without a browser.
 */

/**
 * Which agreement levels the reader is actually asking about.
 *
 * `concurMin` is a FLOOR — "2 or more models agree" — and the counts nest, so a
 * three-model cell already satisfies it. Asking about every level at or above
 * the floor therefore asks a question the reader did not: with the ordinary
 * "2 models agree" filter on, level 3 came back empty on most days and the panel
 * raised an amber "No 3-model agreement — nothing overlaps today" over a map
 * carrying two hundred two-model cells. Amber is reserved for news about the
 * data, and that was the opposite of the news.
 *
 * So: a floor asks exactly one question, "does anything reach it". Only the
 * concurrence heat ramp, which keys every level separately, asks about them all.
 */
export function askedLevels({concurMin = 0, concurHeat = false} = {}, counts = []) {
  if (concurMin) return counts.includes(concurMin) ? [concurMin] : [];
  return concurHeat ? [...counts] : [];
}

/**
 * The levels being asked about that nothing reaches today — the amber banner.
 *
 * `today` is the release's own count, with no filter applied, for the reason
 * spelled out where it is built: if the agreement exists and the reader has
 * filtered it away, that is the severity or model notice's news to break.
 */
export function emptyAgreement(view, today = {}, counts = []) {
  return askedLevels(view, counts).filter((n) => !today[n]);
}

/**
 * The "N only" / "Filtered" heading for a set of things, some of them hidden.
 *
 * Shared by the river severities and the flash tiers because they are the same
 * sentence about different nouns, and the flash copy had drifted: it said
 * "<first shown> only" for any non-empty set, which is true while there are two
 * tiers and a lie the day a third arrives. Counting is the whole job, so it is
 * done once.
 *
 * Returns null when nothing is hidden — there is no notice to draw.
 */
export function hiddenSetHeading(shown, hidden, {filteredLabel = "Filtered"} = {}) {
  if (!hidden.length) return null;
  if (!shown.length) return {kind: "none"};
  return shown.length === 1
    ? {kind: "only", title: `${shown[0]} only`}
    : {kind: "some", title: filteredLabel};
}

/**
 * The body of "Nothing to show".
 *
 * It used to assert "Your filters leave no areas on the map" and then append the
 * reasons only when there were any — so on a day the models simply did not
 * reach, it blamed filters that were not set, which is precisely the misreading
 * these notices exist to prevent. With nothing to blame it reports the day
 * instead.
 */
export function nothingToShowBody(reasons = []) {
  return reasons.length
    ? `Your filters leave no areas on the map — ${reasons.join("; ")}.`
    : "No areas are flagged on this day. No filter is hiding them.";
}
