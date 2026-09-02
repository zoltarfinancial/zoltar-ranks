# dashboard/

The research console. Owned by the Cowork session — see `docs/DASHBOARD.md`.

## Running it

```powershell
.\dashboard\serve.ps1        # then open http://localhost:8787/dashboard/
```

Opening `index.html` directly also works, but `file://` blocks the JSON fetch,
so the page falls back to its embedded seed data.

## How it gets data

On load the page tries `../data/results/dashboard_data.json`. If that file
exists, it renders live results and the rail reads `live · dashboard_data.json`.
If it doesn't, the page falls back to `seed_data.json`, embedded in the HTML at
build time, and shows an amber **ILLUSTRATIVE DATA** banner across the top.

That banner is a safety feature. Every figure in the seed is invented to
exercise the layout. Nothing on this page is a result until the banner is gone.

## Files

| File | What it is |
|---|---|
| `index.html` | the whole console — self-contained, no build step, no dependencies but Google Fonts |
| `seed_data.json` | the illustrative dataset, also embedded in `index.html` |
| `serve.ps1` | local static server |

## Re-embedding the seed after editing seed_data.json

The seed lives twice: as `seed_data.json` and inline in `index.html` inside
`<script id="seed-data" type="application/json">`. If you edit the JSON, paste
it back into that script tag — or ask the Cowork session to rebuild the page.

## Backend contract

`analysis/export_dashboard_data.py` (backend workstream) writes the live JSON.
The schema is in `docs/DASHBOARD.md`. Sections may be missing while their phase
is unbuilt; the page renders an explicit empty state rather than a zero.
