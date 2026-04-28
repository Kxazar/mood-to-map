# Mood-to-Map Hackathon Writeup

## One-liner

Mood-to-Map turns a travel mood into a curated city route with an interactive map, story arc, stop rationales, and compact guide copy.

## What It Does

The user chooses Miami, Dubai, or Abu Dhabi, describes a mood, and sets constraints like duration, budget, heat-friendly, avoid crowds, walking bias, or family-friendly. The app returns a route with:

- mapped stops;
- route order;
- total time;
- a short route logline;
- reason for each stop;
- micro-story for what to notice there.

## Creative Domain

Interactive media and creative software. The map is not a directory of attractions; it is a route director that turns a mood into a paced city experience.

## Agentic Flow

1. Parse the user's mood, persona, city, and constraints.
2. Score places against a curated city memory.
3. Compose a route as a story arc: opening, discovery, contrast, pause, golden hour, finale.
4. Generate compact guide copy.
5. Render the result as structured JSON for the interactive map.

## Kimi Use

Kimi is used as the budget-safe creative editor. The app first selects route candidates locally, then sends only the selected compact stop data to Kimi for polishing:

- route title;
- one-sentence logline;
- route note;
- stop-specific reasons and micro-stories.

This keeps token use low while still making Kimi visibly central to the final route copy.

## Hermes Use

Hermes Agent is the intended route director layer for the final submission:

- maintain city memory;
- run route-generation skill;
- call the Kimi-compatible endpoint;
- log Kimi usage proof;
- prepare/share routes through chat or web UI.

## Demo Script

1. Open Mood-to-Map.
2. Choose Dubai.
3. Enter: "I have five hours. I want architecture, coffee, cinematic views, shade, and a waterfront finale."
4. Select Architecture, Half day, Medium budget, Heat friendly.
5. Generate route.
6. Show the route cards and mapped path.
7. Show the server-side Kimi proof log if `KIMI_API_KEY` is enabled.

## Current Deployments

```txt
http://77.232.41.200:8080/
```

The project is also prepared for Vercel deployment with Python serverless API functions in `api/` and static assets in `public/`.

## Next Steps

1. Deploy the prepared project to Vercel.
2. Set `NOUS_API_KEY` and model env vars in Vercel.
3. Record the demo video.
4. Publish on X and submit the link to Discord.
