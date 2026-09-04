# MangaRelief — Web API

Turns 2D artwork into terraced, 3D-printable meshes (STL + Bambu Studio 3MF)
over HTTP. This is the web half of **MangaRelief**; the desktop application and
the generation engine live in [MangaRelief](https://github.com/lions1999/MangaRelief).

**Live:** [mangarelief.com](https://mangarelief.com) — frontend on Cloudflare
Pages, API on Cloud Run, storage and database on Supabase. The original
`mangarelief-web.pages.dev` still answers, and stays in `CORS_ORIGINS` so that
per-branch preview builds keep working.

> **Status: phase 2 complete.** Anyone can open the site, upload artwork, watch
> the model appear and download it. Accounts, quota and payments are still ahead.

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

## How it runs

Three services, each doing one thing. The browser talks to two of them; only
the API ever touches the database.

```
   browser
      │
      ├──► Cloudflare Pages   the site: HTML, CSS, JS. Static files, no logic.
      │
      └──► Cloud Run          the API: analysis, generation, downloads.
                 │
                 └──► Supabase   Postgres row per job + private file bucket.
```

That separation is why CORS matters here: the page and the API are on different
origins, so the API has to name the site explicitly. It is also why the
service-role key is safe — it lives only in the API's environment and never
reaches a browser.

A generation, end to end:

1. The page loads from Pages. From then on the browser calls the API directly.
2. `POST /api/analyze` on upload — halftone percentage, colour mode, tones.
3. `POST /api/mockup` on every slider move (debounced): the same two engine
   steps the job runs, so the preview *is* the classification.
4. `POST /api/jobs` writes the row to Supabase, answers `202`, and generates in
   a background thread.
5. The browser polls `GET /api/jobs/{id}` every 1.2s, reading that row.
6. On success the files go to the Supabase bucket and the row records them.
7. Downloads stream through the API, which is what enforces expiry.
8. A nightly GitHub Action calls the cleanup endpoint, which deletes what has
   expired.

## API

Interactive docs at `/docs` once running.

### `POST /api/mockup`
Multipart: `image` (file), optional `params` (JSON). Returns a small PNG of how
the image will be classified. With two colours the response also carries
`X-MangaRelief-Ambiguous`: the share of the artwork sitting near the coverage
cut — hatching or screentone that will print all ink or all paper and flip with
a nudge of the slider. The UI warns above 10%. Spot Colour cannot be tuned without it — accents
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
Streams the file, named `<image>_mangarelief_<mode>[_<N>col].stl` after the
uploaded artwork (reduced to `[A-Za-z0-9_-]`; the job id stands in when nothing
usable is left). **The first download shortens the retention window to 24h.**
Add `?preview=true` for the in-page viewer: same bytes, served inline, and it
does *not* start the countdown — looking at a model in the browser is not taking
it.

### `POST /api/auth/code`
`{email}`. Sends a six-digit code. A magic link would open a new tab and lose
whatever the visitor had already set up on the page, which is why it is a code.

The request goes through the API rather than straight to Supabase from the
browser, because this is the only place that can refuse disposable mailboxes
and fold aliases onto one identity: `m.ario+x@gmail.com` and `mario@gmail.com`
are one inbox and must be one account, or the per-account quota is bypassed by
typing a different alias. The answer is the same whether or not the address is
already registered — telling them apart would leak who has an account here.

The blocklist (8700+ domains) lives in the repo, so the check costs no network
call; a weekly Action refreshes it and refuses an update that looks broken.
It is not a wall: the anon key is public, so an account *can* be created around
this endpoint. What makes such an account useless is that every authenticated
request re-checks the domain.

### `POST /api/auth/verify`
`{email, code, device_id?}` → session tokens. Also attributes to the new
account the generations already made anonymously from that browser, so someone
who tries twice and then signs up does not restart with the full allowance.

### `POST /api/auth/refresh`
`{refresh_token}` → a fresh session.

### `GET /api/history`
The signed-in account's generations, newest first: name, mode, colours, state,
a thumbnail URL, and whether it can still be redone. Requires an account —
anonymous rows are keyed to a browser, and listing them per browser would show
whoever shares that computer what someone else made.

A generation outlives its files. The files are 9 MB and expire in 48h because
1 GB of bucket is roughly 113 of them; what stays is a 7 KB thumbnail and, for
the most recent generations of each account, the artwork reduced to 800px
(~110 KB) so the model can be remade with one click. The thumbnail is the
*mockup*, not the uploaded artwork: the same panel at 2, 3 and 4 colours would
otherwise be three identical tiles, and telling those apart is the whole reason
a history is useful.

`POST /api/history/{id}/regenerate` remakes an expired one from that stored
artwork, and **spends one of the day's generations** — remaking a file costs
exactly what making it cost, so not counting it would be a back door onto the
quota. It reuses the *resolved* parameters, not the requested ones: the sampled
tones were read off the full-size upload, and recomputing them on the reduced
copy would hand back a slightly different model from the one being asked for.

`DELETE /api/history/{id}` deletes the files and hides the entry, but keeps the
row. The row is the quota ledger: deleting it would be a way to reset your own
counter — generate, download, delete, repeat.

`GET /api/history/{id}/preview` serves the thumbnail, unauthenticated like the
artifact downloads: the 32-hex job id is the capability. An `<img>` cannot carry
an Authorization header, and fetching a 7 KB picture through JavaScript to pass
it as a blob buys nothing.

### `GET /api/limits`
The rules of the service as numbers — how many generations per window, how long
files live, how large an upload may be, the caps of the free tier. Public, and
the same answer for everybody: what is left *for you* stays in `/api/quota`.

It exists so the interface never states a limit of its own. Copy that repeats a
setting goes stale silently, and the sidebar footer really did announce
"5 per hour" long after the limit had become something else. The welcome screen
and that footer both read their numbers from here.

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
| `color_changes_z` | derived | 3 values | Auto-Z: the *top* of each tone's terrace, snapped to the layer height. The filament changes the job reports are derived from the terraces actually produced — one layer above the terrace below — which is where the slicer needs them |
| `bw_coverage` | 0.35 | 0–1 | Two colours only: a zone prints as ink when at least this share of its area is inked, judged over ~0.7 mm. The image's histogram decides what counts as ink |
| `spot_accents` | detected | ≤ 2 RGB triples | |
| `spot_coverage` | 40 | 0–100 | How far an accent spreads into muted tones |

---

## Retention

Results are **deleted after 48 hours**, or **24 hours after the first download**,
whichever comes first. `expires_at` lives on the row, downloads return `410 Gone`
past it, and `/api/internal/cleanup` removes the files.

## Free-tier limits

**Generations are counted on the database**, not in memory: two per rolling
24h without an account, five with one. The count that matters is keyed on
`user_id` when signed in and on a random per-browser id otherwise — *not* on
the IP address, because under CGNAT one address covers thousands of people and
counting it denies the free trial to whoever arrives second. Clearing browser
data resets that id, which is why a wider per-IP ceiling sits behind it.

`GET /api/quota` reports what is left without consuming anything, so the page
can say so before an upload rather than after.

If the count cannot be made, the answer is `503`, not a number. The total comes
back in PostgREST's `Content-Range` header rather than in the body, and the
fallback used to read a missing header as **zero generations used** — a quota
that disappears for everybody, silently, with no error anywhere. A fallback has
a direction, and that one pointed the wrong way.

A first-time visitor is told all of this before loading anything: a welcome
screen states the allowance, that the free generations already made from that
browser transfer onto the account at sign-in (so signing in brings you to five
in total, not seven), and that files are deleted on the retention schedule.
Each of those is unpleasant to discover afterwards — the refusal mid-work, the
counter that does not restart, the link from yesterday that no longer resolves.

### Plans

| Plan | Generations | How you get it |
|---|---|---|
| `anonymous` | 2 per rolling 24h | no account |
| `registered` | 5 per rolling 24h | signed in |
| `unlimited` | no cap | granted per account |

The retention window and the history are the same budget seen twice: **files
are the scarce resource, entries are not.** A generation's STL and 3MF weigh
~9 MB, so the free 1 GB bucket saturates around 58 generations a day; a history
entry weighs 7 KB, so 20,000 of them take 14% of it. That asymmetry is why the
files expire on schedule while the entry stays, and why the reduced artwork —
the only part of an entry that is expensive — is kept for the last
`HISTORY_KEEP_SOURCES` generations per account rather than all of them.

**Retention does not change with the plan.** Files still expire after 48h, or
24h from the first download: the cap that is lifted is on the count, not on
storage, which is the scarce resource.

The plan lives in `auth.users.raw_app_meta_data`, which only the service-role
key can write — `user_metadata` is writable by the account itself, and a plan
its beneficiary can edit is not a plan. To grant it, in the Supabase SQL
editor:

```sql
update auth.users
   set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb)
                           || '{"plan":"unlimited"}'::jsonb
 where email = 'tu@esempio.it';
```

Revoke with `- 'plan'` in place of the `||` clause. The change takes effect
within a minute — the API caches a verified token that long.

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

**Frontend → Cloudflare Pages.** Connect the repository, then: root directory
`web`, build command `npm run build`, output directory `dist`, and one build
variable, `VITE_API_URL`, pointing at the Cloud Run URL. Vite inlines it at
build time, so changing it needs a rebuild, not just a restart.

**Nightly cleanup** needs `API_URL` and `CLEANUP_TOKEN` as repository secrets;
the same `CLEANUP_TOKEN` goes into the backend's environment.

### Environment

Everything is an env var, so the same image runs locally and in production.

| Variable | Where | Purpose |
|---|---|---|
| `VITE_API_URL` | Pages, **build time** | The Cloud Run URL. Vite inlines it into the bundle, so changing it needs a rebuild, not a restart. Absent, the frontend falls back to `localhost:8080` |
| `SUPABASE_URL` | Cloud Run | Project URL. **Absent, the service silently runs in local mode** — SQLite inside the container, files lost on restart |
| `SUPABASE_SERVICE_KEY` | Cloud Run | Service-role key: bypasses row level security. Server-side only, never in a browser |
| `SUPABASE_BUCKET` | Cloud Run | Bucket for the produced files. Default `generations` |
| `SUPABASE_ANON_KEY` | Cloud Run | Public key, used only for the sign-in endpoints. Falls back to the service key when unset — it works, but it is more powerful than the job needs |
| `CORS_ORIGINS` | Cloud Run | Comma-separated origins allowed to call the API — in production `https://mangarelief.com,https://www.mangarelief.com,https://mangarelief-web.pages.dev`. Set it *before* pointing a new domain at the site, or the first visitor on it gets a page that silently does nothing. A trailing slash used to break every call; it is stripped on read now |
| `CLEANUP_TOKEN` | Cloud Run **and** GitHub | Shared secret for the cleanup endpoint. Must match on both sides; empty gives `503`, wrong gives `401` |
| `IP_HASH_SALT` | Cloud Run | Salt for the stored IP hashes. **Leaving it unset keeps the public default from the source, which makes those hashes reversible by anyone** |
| `API_URL` | GitHub secret | Where the nightly cleanup job posts |
| `TURNSTILE_SECRET` / `VITE_TURNSTILE_SITE_KEY` | Cloud Run / Pages | Optional captcha. Unset means no challenge |
| `RETENTION_HOURS`, `POST_DOWNLOAD_HOURS` | Cloud Run | 48 and 24 |
| `MAX_UPLOAD_BYTES`, `MAX_IMAGE_PIXELS` | Cloud Run | 12 MB, 40 MP |
| `QUOTA_ANON_DAILY`, `QUOTA_USER_DAILY` | Cloud Run | 2 and 5 generations per rolling window |
| `QUOTA_WINDOW_H` | Cloud Run | 24 — the window the two above are counted over |
| `QUOTA_ANON_IP_DAILY` | Cloud Run | 10 — safety net for someone clearing their browser to reset the free trials. Higher than the per-device figure on purpose: under CGNAT one address covers many people |
| `ANON_MAX_RES_CAP`, `ANON_MAX_DIM_MM` | Cloud Run | 800 px, 200 mm — the free tier, and the RAM ceiling |
| `MAX_WORKERS`, `MAX_QUEUE`, `JOB_TIMEOUT_S` | Cloud Run | 1, 8, 600 |

`/api/health` reports which backend is live: `"backend":"supabase"` or
`"backend":"local"`. If a deploy that should be talking to Supabase says
`local`, its credentials are missing.

**Use `/api/health`, not `/healthz`.** The same handler answers on both, but on
Cloud Run `/healthz` is rejected at Google's edge before it ever enters the
path that serves this service. Measured, not assumed — the two responses say it
themselves:

```
/healthz    404  text/html         referrer-policy: no-referrer   (nothing else)
/healthzz   404  application/json  server: Google Frontend
                                   x-cloud-trace-context: 8ce7d4f1…
```

`/healthzz` — two z's, just as non-existent to this code — is FastAPI's own 404,
and comes back signed and traced. `/healthz` carries neither the signature nor
the trace id Google assigns to requests destined for a service: it is not the
container saying no, it is the container never hearing the question.

The interception is a literal, exact match on that one string. `/healthz/`
gets the 307 with which FastAPI redirects a trailing slash (so it arrived),
`/Healthz` arrives, `/healthz/x` arrives; the query string is irrelevant
(`/healthz?deep=true` is taken too) and it applies to GET and POST alike. It is
not a list of probe names either: `/livez`, `/readyz`, `/healthcheck` and
`/_ah/health` all reach the app. Only that string is taken.

`/healthz` stays registered because it works everywhere else, and because
removing it would break whatever already calls it.

**The reusable part of this**, when something 404s in production and works
locally: compare the response headers against a path you know is missing.
`server` and `x-cloud-trace-context` tell "the app said no" apart from "the app
never heard" — and those are two entirely different investigations.

`/api/health?deep=true` goes further and answers the question a live service
cannot otherwise be asked: **does the table have the columns this code
writes?** It asks PostgREST for exactly the columns in `store.COLUMNS` with
`limit=0`, and reports the answer as `"schema":"ok"` or, degraded, the column
PostgREST says is missing.

That check exists because of a real outage. A migration adds a column, the code
starts writing it, and the insert names *every* column — so a pending migration
does not break the new feature, it breaks **every generation**, including
anonymous ones that have nothing to do with it. With deploy-from-repository the
push and the migration are separate events, and until this endpoint could say
so the only symptom was `502 could not record the job`.

**So the order is: apply the migration first, push second.** The migrations are
additive and idempotent, so applying one before its code ships is always safe;
the reverse is not. `scripts/deploy_cloudrun.sh` now reads this endpoint after
deploying and fails loudly rather than printing a URL that looks fine.

### `web/tests/`

`account_bar.py` drives a real browser against the built site. It is committed,
unlike the throwaway scripts this project usually leans on, because the defect
it covers has now come back twice in two different places for the same reason:
that bar mixes text of unknown length (an email address) with controls that
must stay readable, and every time something is added to the row the elastic
part gives way again. First `sign out` was squeezed to `sig…`; then, once the
history link joined it, the address started truncating itself.

It measures clipping rather than judging looks — an element is clipped when
`scrollWidth > clientWidth`, which is a question the browser can answer.

```bash
cd web && npm run build && npx vite preview --port 4173   # one terminal
python web/tests/account_bar.py                           # the other
```

Not wired into CI: that would mean a browser in the workflow for one file. It
is here so it survives, and so the next person to add something to that row has
something to run.

### The store contract

`api/tests/contract_store.py` is one list of assertions written against the
store *interface* — no SQL, no URLs, nothing specific to either back end — and
`esercita(store, check)` runs it against whatever store it is handed. The smoke
suite hands it `SqliteStore`; the same list is what a real PostgREST has to
satisfy.

It exists because there are two implementations of the same store and CI only
ever ran one of them — the other is the one serving the site. What the suite
did check about Supabase was the URL the code *builds*, judged by that same
code: if a belief about PostgREST is wrong, the code and its test are wrong
together and agree perfectly.

Two rules for anything added there:

- **Never assume an empty database.** Against a real project it runs on a table
  that already holds real rows: every assertion creates its own, with its own
  identifiers, and looks only at those.
- **Never rely on equal `created_at`.** Ordering between rows sharing an
  instant is undefined in Postgres while SQLite follows the rowid — a test
  leaning on that passes here and is a coin toss there.

`.github/workflows/contract.yml` runs that same list against a **real
PostgREST** — the software Supabase puts in front of Postgres, and the one
every query in this code actually talks to. The job applies
`supabase/migrations/*.sql` from empty, in order, unmodified, then points
`SupabaseStore` at it.

Two things fall out of it for free, and either would justify the job alone:

- **The migrations get executed by someone before you do.** Until this existed
  nobody ever ran them on an empty database — they were pasted into the
  dashboard by hand, so a syntax error was found in production. The job also
  applies them twice, because they are meant to be idempotent for exactly that
  reason.
- **`COLUMNS` and the real table get compared**, since an insert naming a
  column that is not there fails immediately.

`supabase/testing/00_bootstrap.sql` supplies the parts of Supabase a bare
Postgres lacks: the `auth` and `storage` schemas the migrations reference, and
the `authenticator` / `service_role` / `anon` roles PostgREST connects through.
The migrations themselves are never adapted — an adapted copy would be proving
something other than what production runs.

**What a green run does not say.** GoTrue, Storage and the project's own RLS
policies are not here. It is PostgREST over Postgres, not Supabase: the job
says our queries are right, not that the platform is.

### Storage

`scripts/check_storage.py` exercises the real bucket under a prefix of its own
(`_check/<timestamp>-<random>/`, deleted afterwards even when a check fails).
Run it after touching `app/storage.py`; it needs nothing but `httpx` and the
service-role key in the environment.

It is a script you run, not a CI job, and that is a decision rather than an
omission: a fake storage would prove my assumptions with my assumptions, and a
CI job would mean putting the service-role key in GitHub secrets — a second
place it can leak from — for a check that matters only when that file changes.

`--locale` runs the same list against `LocalStorage` with no network at all,
which is how the script itself is tested, and incidentally holds the two
implementations to the same behaviour the way `contract_store.py` does for the
database.

The first run against the real bucket found two things, which is what it was
for:

- **`delete_prefix` left orphans.** The listing returns at most 100 names, and
  the old code deleted that one page and answered 200 — with 105 objects it
  removed 100 and left 5, silently. It now loops until a short page comes back.
  Nothing was being lost in practice (a prefix holds two or three files), but
  with 1 GB of space that is a leak you notice late.
- **Keys must never be reused**, and that is now a measured limit rather than a
  preference. Overwrite one and the write lands — the listing shows the new
  size immediately — while reads keep returning the old bytes for about a
  minute: the CDN in front of Supabase invalidates asynchronously
  (`cf-cache-status: HIT`, stale body, `last-modified` already updated,
  consistent after ~56s). It cannot be fixed from this side:
  `cache-control: no-store` is accepted, stored and echoed back, and the CDN
  serves from cache anyway. Uploads send it regardless — it states the intent
  and keeps the lag to the CDN's instead of adding an hour of object cache on
  top — but nothing here is ever overwritten anyway: every key carries a job
  id. To change an object, change the key.

`--molti` writes 105 objects on purpose: that is the number that exposed the
first one.

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
  src/App.tsx             The shell: settings sidebar + stage
  src/api.ts              The only module that knows the API exists
  src/components/Stage.tsx         The stage: artwork picker or finished model
  src/components/         Dropzone, look, print, tones, accents, progress
  src/components/Welcome.tsx       First visit: what it does, what it costs
  src/components/History.tsx       Past generations, as a grid of mockups
  src/components/Info.tsx          The «i» popovers next to the headings
  src/components/ModelViewer.tsx   three.js STL viewer
  src/hooks/              Debounced mockup, tone slots, accent slots
supabase/         Schema as migrations
Dockerfile        Backend image, at the root because Spaces builds from there
```

### Layout

An app shell, not a page: a fixed settings sidebar on the left and one stage
that takes everything else. The window does not scroll — the sidebar scrolls
its own contents, Generate is pinned to its foot, and the stage holds either
the artwork you sample colours from or the finished model, as two tabs.

That last part is deliberate. The model does not *replace* the picker, because
the loop this tool is used in is "look at the model, go back, move a tone,
generate again"; discarding the picker on success would make that a reload.
The preview mockup is the control you actually judge Spot Colour by, so it gets
half the stage rather than a thumbnail inside a form.

Under each control the sidebar shows only what *changes*: the filament-change
count, the sampled value, the warning when two colours are the wrong tool. The
durable explanation — what a colour actually costs to print, why a click
averages a disc instead of a pixel, what the coverage threshold measures — sits
behind an `i` next to the heading. It reads well the first time and becomes
noise on the tenth, and on the tenth it is standing between you and the next
control. The popover opens on click, not on hover, because a phone has no
hover and an explanation only the mouse can reach is one half the visitors
never see.

The history takes over the stage rather than opening as a page of its own:
it is the same surface the artwork and the model use, and closing it puts back
whichever of the two you were looking at. A past model opens straight into the
viewer without loading the artwork again — reviewing yesterday's result should
not require having yesterday's file to hand.

Below 900px the two stack and the window scrolls again — a sidebar and a stage
cannot both be usable in one phone viewport — with Generate stuck to the bottom
edge and the stage scrolled into view when it is pressed.

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
- **The Supabase branch has no test coverage.** The suite runs in local mode on
  SQLite, so that code only executes in production — which is where its three
  bugs were found (a missing lxml, a `+` in a URL-encoded timestamp, and errors
  that reported nothing). A CI job against a real Postgres would close the gap.
- **Turnstile is optional.** Set `VITE_TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET`
  to switch it on. Verification fails *open* on a Cloudflare outage: a captcha
  being down should slow abuse, not take the service offline.

---

## Roadmap

1. ~~Backend API~~
2. ~~Frontend (Vite + React), Three.js preview, public demo~~
3. Accounts, roles, quota (Supabase Auth) ← next
4. Client-side mockup for Spot Color tuning
5. Payments
