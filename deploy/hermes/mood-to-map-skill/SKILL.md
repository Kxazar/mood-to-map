---
name: mood-to-map
description: "Create real Mood-to-Map route story packs with Hermes, Kimi, and the VPS story generator."
version: 1.0.0
metadata:
  hermes:
    tags: [mood-to-map, route, travel, story, city-guide, hermes-route-studio, kimi, nous, miami, dubai, abu-dhabi]
    related_skills: [creative]
---

# Mood-to-Map Route Studio

Use this skill when the user asks to create, save, generate, publish, or demo a Mood-to-Map route, walk, itinerary, city story, story pack, or journey.

## Non-Negotiable Rule

Do not invent a route URL. A valid URL exists only after the real generator creates a story pack file.

## Workflow

1. Extract:
   - `city`: `miami`, `dubai`, or `abu-dhabi`
   - `duration`: `2h`, `half-day`, or `full-day`
   - `start-time`: choose a reasonable `HH:MM` if omitted
   - `intent`: keep the user's actual creative request
   - `story-id`: create a short lowercase slug with `a-z`, `0-9`, and `-`

2. Run:

   ```bash
   cd /home/codex/mood-to-map
   python3 scripts/hermes_route_studio.py --id <story-id> --city <city> --duration <duration> --start-time <HH:MM> --intent "<intent>"
   ```

3. Wait for completion.

4. Return the exact `Story id` and `Open` URL printed by the script.

## Valid URL Formats

Canonical:

```text
https://mood-to-map.vercel.app/?story=<story-id>&source=vps
```

Clean browser route:

```text
https://mood-to-map.vercel.app/story/<story-id>
```

If a command fails or the story id is invalid, explain the failure. Never pretend a story exists.
