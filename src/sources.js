// Central source hub for FEWS4All data files.
//
// Every GeoJSON the map loads is named here, in one place, so there is a single
// spot to repoint the app if the data ever moves. The files currently live in
// the `public/` folder, which Vite serves from the site root; using
// import.meta.env.BASE_URL resolves them correctly in dev and in production,
// even if the site is later served from a subpath (e.g. /fews4all/).
//
// To point the app at a different origin - for example back to a cloud CDN -
// change BASE to that URL (keep the trailing slash) and every entry below
// follows automatically. To move a single file, edit just its line.

const BASE = import.meta.env.BASE_URL; // public/ -> served from the site root

// One forecast release's delivered tables. The path is the dev-server one — Vite
// serves the project root, so backend/output resolves while `npm run dev` is
// running. Production needs these served from somewhere real.
export const OUTPUT_BASE = "/backend/output/";

// Which release to draw is NOT hardcoded any more. Step 2 writes the folder name
// into current_release.txt when a run finishes, so the app follows the pipeline
// instead of needing an edit and a redeploy every morning. Two properties of that
// file matter: it is written last, so it never points at a half-built release,
// and it names the release step 2 actually published rather than whichever folder
// happens to sort last — a rerun of an older date would otherwise drag the app
// onto a newer, unrelated one.
//
// FALLBACK_RELEASE is what the app draws when the pointer cannot be read at all,
// which in practice means a static deployment that ships one release and no
// pointer. It is the last resort, not the normal path.
export const FALLBACK_RELEASE = "2026-09-23";

export async function resolveRelease() {
  let release = FALLBACK_RELEASE;
  try {
    const r = await fetch(OUTPUT_BASE + "current_release.txt", {cache: "no-store"});
    if (r.ok) {
      const name = (await r.text()).trim();
      if (/^\d{4}-\d{2}-\d{2}$/.test(name)) release = name;
    }
  } catch {
    // No pointer served, or offline. The fallback is a usable answer; a failed
    // fetch here must not be the reason the map stays blank.
  }
  return {release, base: OUTPUT_BASE + release + "/"};
}

// One entry, because one file is still fetched from here. The cells, the basins,
// the stream network and the ADM boundaries all used to be whole-world GeoJSON
// downloads; they are vector tile archives now, or retired outright, and their
// files are in Other/retired/.
export const DATA = {
  flash: BASE + "data_flash_floods.geojson",
};
