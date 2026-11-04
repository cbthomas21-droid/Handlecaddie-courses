# HandleCaddie course data (Utah)

A GitHub repository that rebuilds the course data once a month from OpenStreetMap and serves it with GitHub Pages,
so the app downloads one small file instead of running a slow live map query.

## One-time setup (about 10 minutes)
1. Create a free GitHub account if you don't have one. Click **New repository**, name it `handlecaddie-courses`, make it **Public**, no template.
2. Upload everything in this folder (keep the folders: `.github/workflows/build.yml`, `tools/build_courses.py`, `docs/`).
   Easiest: on the repository page choose **Add file > Upload files** and drag the folders in. Hidden folder `.github` may not show in
   your file browser - turn on "show hidden files", or use GitHub Desktop.
3. Repository **Settings > Pages**: Source = *Deploy from a branch*, Branch = `main`, Folder = `/docs`. Save.
4. Repository **Settings > Actions > General > Workflow permissions**: choose **Read and write permissions**. Save.
5. **Actions** tab > *Build course data* > **Run workflow**. The first run takes roughly 15-60 minutes (about 100 courses,
   politely paced). When it finishes, `docs/index.json` and `docs/c/*.json` appear in the repository.
6. Your address is `https://YOURNAME.github.io/handlecaddie-courses`. Open `<address>/index.json` in a browser to check it loads.
7. In the app, open `CourseRepository.kt` and set `HOSTED_BASE` to that address (no trailing slash), then rebuild the app.

After that it refreshes itself on the 1st of every month (or press *Run workflow* any time after you fix a course in OpenStreetMap).

## Another state or region
Change `REGION: US-UT` in `.github/workflows/build.yml` to another ISO 3166-2 code (e.g. `US-CO`, `US-AZ`).
One region per repository for now; the app falls back to the live map query for anything not found.

## Notes
- A course that fails to download keeps its previous file. A course not mapped hole-by-hole in OpenStreetMap is skipped;
  the app then tries the live query, as before.
- The data is OpenStreetMap data (c) OpenStreetMap contributors, ODbL. Keep that credit in the app's about screen.
