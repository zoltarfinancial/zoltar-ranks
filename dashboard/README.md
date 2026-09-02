# dashboard/

The research console. Owned by the Cowork session — see `docs/DASHBOARD.md` for
the research feed and `BUILD_MONITOR.md` for the build-monitor feed.

Eleven sections in two groups:

| | |
|---|---|
| **Research** 01–07 | archive health, the open question, benchmarks, signal quality, execution timing, the hypothesis register, what to test next |
| **Active build** 08–11 | pipeline health, phase progress, contract gates, feeds & handoff |

## Running it

```powershell
.\dashboard\serve.ps1        # then open http://localhost:8787/dashboard/
```

Opening `index.html` directly also works. `file://` blocks the JSON fetch, but
the page also reads `build_status.js` and `dashboard_data.js` beside it — both
written by the emitters — so it renders live data either way.

## How it gets data

Two feeds, each with the same fallback chain: a `.js` file beside the page, then
`fetch` of the JSON, then (in the published artifact) the artifact's own store,
then the embedded example data.

| Feed | Written by | Sections |
|---|---|---|
| `data/results/dashboard_data.json` | `analysis/export_dashboard_data.py` | 01–07 |
| `data/results/build_status.json` | `dashboard/emit_build_status.py` | 08–11 |

When a feed is missing the page says so rather than hiding it: sections 01–07
show an amber **ILLUSTRATIVE DATA** banner, and §08's tiles read *example
scaffold*. Every figure in the examples is invented to exercise the layout.
Nothing on this page is a result until those markers are gone.

## Files

| File | What it is |
|---|---|
| `index.html` | the whole console — self-contained, no build step, no dependencies but Google Fonts |
| `emit_build_status.py` | reference implementation of the build-monitor emitter; the backend calls it from `scripts/daily.py::STEPS` |
| `BUILD_MONITOR.md` | the §08–§11 feed contract and derivation rules |
| `seed_data.json` | the illustrative research dataset, also embedded in `index.html` |
| `serve.ps1` | local static server |
| `build_status.js`, `dashboard_data.js` | generated feeds, gitignored |

## The published copy

The same page is published as a private artifact on claude.ai so it can be read
away from this machine. It is updated by the Cowork session, and its build
monitor is refreshed by pasting `build_status.json` into
§11 → *Update this monitor*, which saves it to the artifact's own store.

## Re-embedding the seed after editing seed_data.json

The research seed lives twice: as `seed_data.json` and inline in `index.html`
inside `<script id="seed-data" type="application/json">`. The build-monitor
example lives once, in `<script id="seed-build">`. If you edit either, paste it
back into the matching script tag — or ask the Cowork session to rebuild.
