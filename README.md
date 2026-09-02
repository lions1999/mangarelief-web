# MangaRelief — Web API

Turns 2D artwork into terraced, 3D-printable meshes (STL + Bambu Studio 3MF)
over HTTP. This is the web half of **MangaRelief**; the desktop application and
the generation engine live in [MangaRelief](https://github.com/lions1999/MangaRelief).

> **Status: phase 2.** A browser flow with an orbitable 3D preview, on top of
> the phase 1 API. Accounts, quota and payments are still ahead.

---

## What it does

The engine quantises grayscale or colour artwork into stacked "terraces", one
per printed colour, and emits both a plain STL and a 3MF carrying Bambu Studio's
filament-change instructions, so a single-extruder printer produces a
multi-colour relief.

Two modes are exposed on the web:

| Mode | What you get |
|---|---|
| `standard` | Grayscale relief; 2/3/4-colour sub-mode chosen from the image's halftone percentage |
| `spot_color` | "Silkscreen" look: white base, one or two accent colours, black linework on top |

Topographic, Deckbox and Phone Cover exist in the engine but need a colour
picker or physical presets that only make sense inside the desktop UI.

---

## API

Interactive docs at `/docs` once running.

### `POST /api/mockup`
Multipart: `image` (file), optional `params` (JSON). Returns a small PNG of how
the image will be classified. Spot Colour cannot be tuned without it — accents
and coverage are unjudgeable as numbers — and it is cheap enough to call on
every slider move (debounced). Phase 4 replaces it with a client-side port.

### `POST /api/analyze`
Multipart: `image` (file), optional `params` (JSON). Returns the analysis the
desktop UI performs when an image is loaded — halftone percentage, colour mode,
suggested white clip, K-Means midtones, auto Z heights and detected accent
colours. Use it to prefill a form instead of reimplementing the analysis in the
browser.

### `POST /api/jobs`
Multipart: `image` (file), optional `params` (JSON). Returns `202` with a job id.
Every parameter is optional; anything omitted is derived from the image.

```bash
curl -X POST http://localhost:8080/api/jobs \
  -F "image=@panel.png" \
  -F 'params={"mode":"spot_color","max_dim":120,"spot_coverage":40}'
# {"job_id":"cf35...","status":"queued","status_url":"..."}
```

### `GET /api/jobs/{id}`
Status, progress percentage and message (the same strings the desktop progress
bar shows), plus artifact links and the expiry timestamp once finished.

### `GET /api/jobs/{id}/artifacts/{stl|3mf}`
Streams the file. **The first download shortens the retention window to 24h.**
Add `?preview=true` for the in-page viewer: same bytes, served inline, and it
does *not* start the countdown — looking at a model in the browser is not taking
it.

### `POST /api/internal/cleanup`
Deletes expired artifacts. Requires the `X-Cleanup-Token` header; called nightly
by `.github/workflows/cleanup.yml`.

### Parameters

| Field | Default | Range | Note |
|---|---|---|---|
| `mode` | `standard` | `standard`, `spot_color` | |
| `max_dim` | 180 | 20–250 mm | Long side |
| `base_h` / `max_h` | 1.0 / 2.4 | mm | `max_h` must exceed `base_h` by ≥ one layer |
| `layer_height` | 0.2 | 0.04–0.4 mm | |
| `max_res_cap` | 800 | 200–1600 px | Mesh quality |
| `white_clip` / `black_clip` | derived | | From the highlight histogram peak |
| `sampled_values` | derived | 4 values | `[white, L1, L2, black]` |
| `color_changes_z` | derived | 3 values | Auto-Z, snapped to the layer height |
| `spot_accents` | detected | ≤ 2 RGB triples | |
| `spot_coverage` | 40 | 0–100 | How far an accent spreads into muted tones |

---

## Retention

Results are **deleted after 48 hours**, or **24 hours after the first download**,
whichever comes first. `expires_at` lives on the row, downloads return `410 Gone`
past it, and `/api/internal/cleanup` removes the files.

## Free-tier limits

Anonymous requests are capped at Draft resolution (`max_res_cap` 800) and 200 mm.
This is not only a commercial line: an Ultra run peaks well past a gigabyte of
RAM before decimation, which no free instance survives. Lowered values are
reported back in the `X-MangaRelief-Notes` response header.

Also enforced: 12 MB upload, 40 MP image, 5 jobs per hour per IP (a salted hash
of the address is stored, never the address), one concurrent generation.

---

## Running it

```bash
# backend
pip install -r api/requirements.txt
(cd api && uvicorn app.main:app --reload --port 8080)   # SQLite + ./.data, no cloud account
python api/tests/smoke_api.py                           # end-to-end API suite

# frontend
cd web && npm install
echo "VITE_API_URL=http://localhost:8080" > .env
npm run dev
```

With no `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` the service runs in local mode:
SQLite for the `generations` table, a directory for the files. Set them and the
exact same code paths talk to Supabase instead. See `.env.example`.

### Docker

```bash
docker build -t mangarelief-api .
docker run -p 8080:8080 --env-file .env mangarelief-api
```

The image uses `opencv-python-headless`, so it needs no GUI libraries. It listens
on `$PORT`, which is what Cloud Run and Hugging Face Spaces inject, and defaults
`LOCAL_DATA_DIR` to `/data`.

### Deploying

**Backend → Cloud Run.** Either connect the repository in the Cloud Run console
("deploy from repository", which sets up a Cloud Build trigger and needs no
secrets in GitHub), or run `./scripts/deploy_cloudrun.sh <project-id>`.

Two settings are correctness, not tuning:

- **CPU always allocated** (`--no-cpu-throttling`). Cloud Run throttles CPU to
  near zero outside a request, and a generation runs in a background thread
  *after* the `202` response — with the default setting it stalls.
- **2 GiB of memory.** A draft (800px) generation peaks around 900 MB RSS and
  full quality around 2 GB, measured; 1 GiB is OOM-killed mid-mesh.

Scaling to zero means the first request after an idle period pays a cold start —
the UI says so rather than hiding it.

(Hugging Face Spaces was the original target, but since July 2026 Docker Spaces
require a paid plan, so the free path there is gone.)

**Frontend → Cloudflare Pages.** Root directory `web`, build command
`npm run build`, output directory `dist`, and one environment variable:
`VITE_API_URL` pointing at the Space. Vite inlines it at build time, so changing
it needs a redeploy, not just a restart.

**Nightly cleanup** needs `API_URL` and `CLEANUP_TOKEN` as repository secrets;
the same `CLEANUP_TOKEN` goes into the backend's environment.

### Supabase

The schema lives in `supabase/migrations/` — the layout the Supabase GitHub
integration and `supabase db push` expect. Paste it into the SQL editor if you
prefer; every statement is idempotent either way. It creates the `generations`
table (state, retention ledger and usage log in one), its indexes, and the
private `generations` storage bucket. Row level security is on with no public
policy: the API is the only client and it uses the service-role key.
See `supabase/README.md` for the queries that confirm it landed.

---

## Architecture

```
api/
  app/
    main.py       Routes: analyze, mockup, create, poll, download, cleanup
    schemas.py    Pydantic contract — the ranges the desktop spinboxes enforced
    analysis.py   Auto parameters, mirroring what the desktop UI derives on load
    jobs.py       Single-worker background runner around engine.generate
    store.py      The generations table: SQLite locally, PostgREST on Supabase
    storage.py    Artifacts: local directory or Supabase Storage
    limits.py     Rate limit, free-tier caps, IP hashing, Turnstile
    imaging.py    Safe decoding of untrusted uploads
  engine/         Vendored from the desktop repo — see api/ENGINE_SOURCE
  tests/          The end-to-end suite CI runs
web/
  src/api.ts              The only module that knows the API exists
  src/components/         Dropzone, params, spot panel, viewer, progress
  src/components/ModelViewer.tsx   three.js STL viewer
supabase/         Schema as migrations
Dockerfile        Backend image, at the root because Spaces builds from there
```

### About `engine/`

The engine is a copy, kept honest by `scripts/sync_engine.sh`, which records the
desktop commit it came from in `ENGINE_SOURCE`. Publishing it as a third,
installable package is the tidier end state, but it buys packaging and version
management before the product is validated. Nothing in `engine/` imports PyQt —
that is what makes this service possible at all.

```bash
./scripts/sync_engine.sh ../MangaRelief
```

### Known trade-offs

- **Rate limiting is per instance.** In-memory sliding window; with several
  instances the effective limit multiplies. The durable count is in
  `generations`, which the per-user quota of phase 3 will use.
- **Downloads stream through the API** rather than via signed URLs, because the
  API is what enforces expiry. Fine at this scale, worth revisiting later.
- **Cold starts are slow.** Scaling to zero plus heavy scientific imports means
  the first request after idling takes several seconds; the UI says so rather
  than hiding it.
- **Memory is the real ceiling.** 913 MB peak for a draft run, 1.98 GB at
  1200px — measured, not estimated. It is why the anonymous tier is capped at
  draft, and why any 512 MB free tier is out of the question.
- **The STL preview is the full mesh** — a few MB over the wire. Fine on a
  desktop, heavy on a phone; a decimated preview mesh is the fix when it starts
  to matter.
- **Turnstile is optional.** Set `VITE_TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET`
  to switch it on. Verification fails *open* on a Cloudflare outage: a captcha
  being down should slow abuse, not take the service offline.

---

## Roadmap

1. ~~Backend API~~
2. ~~Frontend (Vite + React), Three.js preview~~ ← you are here; the public
   demo needs the deploy secrets above
3. Accounts, roles, quota (Supabase Auth)
4. Client-side mockup for Spot Color tuning
5. Payments
