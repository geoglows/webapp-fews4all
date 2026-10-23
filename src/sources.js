// Central source hub for FEWS4All data files.
//
// Where the app's data lives, in one place, so there is a single spot to repoint
// it if the data ever moves.
//
// Everything now comes from one release folder under OUTPUT_BASE. To serve from
// a different origin — an S3 bucket, a CDN — change OUTPUT_BASE to that URL and
// every file follows, because they are all addressed relative to it.

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

// Nothing is fetched from public/ any more. The flash polygons used to be a
// static data_flash_floods.geojson shipped with the site, which is exactly how
// they came to be frozen on 23 September while the pipeline published fresh ones
// into every release and nothing read them. They now come from the release like
// everything else: RELEASE_BASE + "flash_areas.geojson".
//
// The cells, the basins, the stream network and the ADM boundaries all used to be
// whole-world GeoJSON downloads too; they are vector tile archives now, or
// retired outright, and their files are in Other/retired/.
