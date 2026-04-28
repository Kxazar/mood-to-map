# Mood-to-Map

Mood-to-Map is a lightweight Hermes/Kimi-ready city guide that turns a travel mood into a cinematic route.

The MVP covers Miami, Dubai, and Abu Dhabi with a curated place database, interactive map, route timeline, story cards, and a budget-safe Kimi integration. It runs without an API key in local deterministic mode, then switches to Nous/Kimi when `NOUS_API_KEY` is present.

## Run

```bash
python3 server.py
```

Open:

```txt
http://localhost:8080
```

## Vercel

The repo includes Vercel serverless entrypoints in `api/` plus a tiny shared handler helper in `vercel_api.py`:

- `/api/health`
- `/api/route`
- `/api/attractions`
- `/api/attraction-copy`

Static files are served from `public/`. The local VPS server still uses `server.py`, while Vercel imports the same route-generation functions through the serverless handlers.

Set these Vercel environment variables for the live Kimi demo:

```txt
NOUS_API_KEY
NOUS_INFERENCE_BASE_URL
KIMI_MODEL
LLM_TIMEOUT_SECONDS
LLM_MAX_TOKENS
LLM_POLISH_MAX_TOKENS
LLM_DISCOVERY_MAX_TOKENS
```

## Kimi Mode

Create `.env` from `.env.example` and set:

```bash
cp .env.example .env
```

Then add your Nous key to `.env`.

The app keeps Kimi usage small by scoring places locally first, then sending only the selected route candidates for narrative polishing through the Nous inference endpoint. The optional **AI places** mode lets Kimi propose the actual stops and coordinates; the server validates that returned points sit inside the selected city before putting them on the map.

Every route is returned as a timed itinerary: each stop has a time window, concrete activity, mini-plan, conservative fact or visible detail, local tip, photo prompt, and a rough move window to the next stop. Each successful LLM call writes a compact proof record to `logs/kimi-proof.jsonl`.

The demo also supports route variants, per-stop Google Maps links, a full-route Google Maps link, stop locking, and one-stop replacement. Replacement sends locked stops and excluded stops back to the route director so Kimi can fill only the open slot.

Each city also has a 15-point attraction layer on the map. Attraction cards use Wikipedia/Wikimedia photos, then ask Kimi for compact place notes on click; generated notes are cached in `logs/attraction-copy-cache.json`.

## Deployments

Vercel production:

```txt
https://mood-to-map.vercel.app/
```

VPS fallback/demo:


```txt
http://77.232.41.200:8080/
```

Systemd service:

```bash
sudo systemctl status mood-to-map
sudo systemctl restart mood-to-map
```

## Hackathon Angle

Hermes is positioned as the route director:

1. Understand the traveler's mood and constraints.
2. Select places from a curated city memory.
3. Build a story arc across the day.
4. Ask Kimi to discover places or write compact itinerary copy.
5. Return structured JSON for the interactive map.

## Cities

- Miami
- Dubai
- Abu Dhabi
