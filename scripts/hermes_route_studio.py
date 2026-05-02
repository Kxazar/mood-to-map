#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import (  # noqa: E402
    CITY_BOUNDS,
    PLACES,
    STATIC_STORIES,
    STORY_PACKS,
    apply_timeline,
    city_attractions,
    get_attraction_asset,
    safe_story_id,
)


DEFAULT_INTENT = "quiet local art walk with coffee, shade, and one waterfront ending"
BAD_STORY_ASSETS = {
    "miami:miami-the-bass",
}


def slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return slug[:64] or "hermes-route"


def load_assets():
    path = ROOT / "data" / "attraction_assets.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def compact_places(city_id, assets):
    result = []
    for attraction in city_attractions(city_id):
        key = f"{attraction['city']}:{attraction['id']}"
        if key in BAD_STORY_ASSETS:
            continue
        asset = assets.get(key, {})
        result.append(
            {
                "id": attraction["id"],
                "name": attraction["name"],
                "lat": attraction["lat"],
                "lng": attraction["lng"],
                "tags": attraction.get("tags", [])[:7],
                "summary": (asset.get("wiki_extract") or "")[:240],
                "photo_available": bool(asset.get("photo_url")),
            }
        )
    return result


def build_prompt(args, available_places):
    city = PLACES["cities"][args.city]
    payload = {
        "project": "Mood-to-Map",
        "role": "Hermes Route Studio director",
        "city": city["name"],
        "city_id": args.city,
        "intent": args.intent,
        "duration": args.duration,
        "start_time": args.start_time,
        "budget": args.budget,
        "allowed_coordinate_bounds": CITY_BOUNDS.get(args.city, {}),
        "available_places": available_places,
        "output_contract": {
            "format": "Return exactly one JSON object, no markdown.",
            "keys": ["title", "logline", "route_note", "agent_trace", "stops"],
            "agent_trace": "4-6 short strings showing the route-director process.",
            "stops": (
                "Choose 4-6 stops only from available_places. Each stop must include id, duration_minutes, "
                "best_time, summary, reason, micro_story, activity, what_to_do, interesting_fact, "
                "local_tip, walk_note, photo_prompt, next_move."
            ),
            "style": (
                "Concrete, cinematic, practical. Prefer places with photo_available=true. "
                "No live events, opening hours, exact distances, percentages, prices, ticket rules, or historical dates "
                "unless supplied in available_places."
            ),
        },
    }
    return (
        "You are Hermes Agent acting as the route director for an interactive hackathon demo. "
        "Create a visual city story that the web app will render as a map and timed storyboard. "
        "Use only the supplied available_places ids for stops. "
        "Return valid JSON only.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def run_hermes(args, prompt):
    command = [
        "hermes",
        "--provider",
        args.provider,
        "--model",
        args.model,
        "-z",
        prompt,
    ]
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=args.timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "Hermes failed").strip())
    return completed.stdout.strip()


def parse_json_object(text):
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def text(value, fallback=""):
    if not isinstance(value, str):
        return fallback
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or fallback


def safe_copy(value, fallback=""):
    cleaned = text(value, fallback)
    risky = [
        r"\bopen(?:s|ing)?\b.*\b\d{1,2}:\d{2}\b",
        r"\b\d{1,2}:\d{2}\b.*\bopen(?:s|ing)?\b",
        r"\b\d+\s?%\b",
        r"\b\d+(?:\.\d+)?\s?(?:mi|miles|km)\b",
        r"\$\s?\d+",
    ]
    if any(re.search(pattern, cleaned, flags=re.IGNORECASE) for pattern in risky):
        return fallback
    return cleaned


def first_sentence(value):
    cleaned = text(value)
    if not cleaned:
        return ""
    if len(cleaned) <= 220:
        return cleaned
    truncated = cleaned[:220].rsplit(" ", 1)[0].strip()
    return f"{truncated}..."


def tag_context(source):
    tags = source.get("tags", [])[:3]
    if not tags:
        return "city texture"
    return ", ".join(tags)


def safe_scene_copy(source, asset, args, index, total):
    tags = set(source.get("tags", []))
    name = source["name"]
    extract = first_sentence(asset.get("wiki_extract", ""))
    tag_text = tag_context(source)
    if tags.intersection({"museum", "art"}):
        activity = "Use this as the focused art stop: choose one room, facade, or installation and slow down."
        what_to_do = "Spend a short first pass orienting yourself, then return to the detail that best matches the route mood."
        walk_note = "Let the transition out of the art stop reset the pace before the next outdoor scene."
        photo_prompt = "Capture one establishing frame and one close detail that explains the mood of the place."
    elif tags.intersection({"garden", "park", "shade"}):
        activity = "Take a shaded loop and pause long enough for the city noise to soften."
        what_to_do = "Find the calmest edge, sit briefly, then make one slow loop before moving on."
        walk_note = "Use this as the route's cooling beat before the next denser stop."
        photo_prompt = "Frame leaves, paths, or water against the surrounding city texture."
    elif tags.intersection({"waterfront", "views"}):
        activity = "Walk the open edge, pause for the view, and let this become the route's release."
        what_to_do = "Arrive without rushing, face the water for a few minutes, then take the final photos."
        walk_note = "Keep the last transfer simple so the route can end with space and air."
        photo_prompt = "Shoot one wide horizon frame and one human-scale detail near the water."
    elif tags.intersection({"market", "food"}):
        activity = "Use this as a sensory pause: order something small and watch the local rhythm."
        what_to_do = "Pick one simple stop, sit briefly, and let the neighborhood decide the next small detour."
        walk_note = "Leave before it becomes a checklist; the value is the pause."
        photo_prompt = "Capture color, texture, and one quiet table or counter detail."
    else:
        activity = "Make one compact loop and choose the detail that best fits the route intent."
        what_to_do = "Orient first, then spend the rest of the stop on one observation instead of a checklist."
        walk_note = "Let this stop change the pace before moving to the next scene."
        photo_prompt = "Capture the clearest visual reason this place belongs in the route."

    if index == 1:
        micro_story = f"Start at {name} with a quiet establishing scene before the city becomes busier."
    elif index == total:
        micro_story = f"End at {name} as the route opens up and gives the day a clear final image."
    else:
        micro_story = f"Use {name} as the route's {tag_text} beat, a shift in texture before the next move."

    return {
        "summary": extract or f"{name} adds a {tag_text} layer to the route.",
        "reason": f"Hermes placed it here because its {tag_text} context supports the intent: {args.intent}.",
        "micro_story": micro_story,
        "activity": activity,
        "what_to_do": what_to_do,
        "interesting_fact": extract or f"This stop is useful because it carries the {tag_text} side of the city.",
        "local_tip": "Keep the stop flexible and use the Maps link before committing to the exact transfer.",
        "walk_note": walk_note,
        "photo_prompt": photo_prompt,
        "next_move": "Use the next transfer as a small reset before the following scene.",
    }


def normalize_story(raw, args, assets, available_places, story_id):
    by_id = {place["id"]: place for place in available_places}
    city = PLACES["cities"][args.city]
    stops = []
    seen = set()
    for index, raw_stop in enumerate(raw.get("stops") if isinstance(raw.get("stops"), list) else [], 1):
        if not isinstance(raw_stop, dict):
            continue
        place_id = text(raw_stop.get("id"))
        place = by_id.get(place_id)
        if not place or place_id in seen:
            continue
        seen.add(place_id)
        source = next(item for item in city_attractions(args.city) if item["id"] == place_id)
        asset = assets.get(f"{args.city}:{place_id}", {})
        if not asset.get("photo_url"):
            asset = get_attraction_asset(source)
        scene = safe_scene_copy(source, asset, args, index, len(raw.get("stops") or []))
        stops.append(
            {
                "id": source["id"],
                "name": source["name"],
                "lat": source["lat"],
                "lng": source["lng"],
                "duration_minutes": raw_stop.get("duration_minutes") or 45,
                "best_time": text(raw_stop.get("best_time"), "flexible"),
                "budget": source.get("budget", [args.budget]),
                "tags": source.get("tags", [])[:7],
                "summary": scene["summary"],
                "reason": scene["reason"],
                "micro_story": scene["micro_story"],
                "activity": scene["activity"],
                "what_to_do": scene["what_to_do"],
                "interesting_fact": scene["interesting_fact"],
                "local_tip": scene["local_tip"],
                "walk_note": scene["walk_note"],
                "photo_prompt": scene["photo_prompt"],
                "next_move": scene["next_move"],
                "photo_url": asset.get("photo_url", ""),
                "photo_source": asset.get("photo_source", "Wikipedia/Wikimedia"),
            }
        )

    if len(stops) < 3:
        raise ValueError("Hermes returned fewer than 3 valid stops from available_places")

    agent_trace = raw.get("agent_trace") if isinstance(raw.get("agent_trace"), list) else []
    agent_trace = [safe_copy(item) for item in agent_trace if safe_copy(item)][:6]
    if not agent_trace:
        agent_trace = [
            "Intent parsed",
            "City memory selected",
            "Kimi reasoning completed",
            "Route pack validated",
            "Visual story rendered",
        ]

    route = {
        "story_id": story_id,
        "story_mode": True,
        "source": "hermes-route-studio",
        "model": args.model,
        "llm": {
            "provider": "Hermes Agent + Nous/Kimi",
            "model": args.model,
            "endpoint": "inference-api.nousresearch.com",
            "mode": "Hermes Route Studio",
        },
        "city": city,
        "title": text(raw.get("title"), f"{city['name']}: Hermes Story"),
        "logline": text(raw.get("logline"), f"A route shaped by Hermes from the intent: {args.intent}."),
        "route_note": safe_copy(raw.get("route_note"), "Hermes created this as a visual route pack for the web map."),
        "agent_trace": agent_trace,
        "intent": args.intent,
        "variant": "balanced",
        "variant_label": "Hermes Studio",
        "stops": stops[:6],
        "total_minutes": sum(int(stop.get("duration_minutes") or 45) for stop in stops[:6]),
        "created_at": int(time.time()),
    }
    request = {
        "city": args.city,
        "duration": args.duration,
        "startTime": args.start_time,
        "budget": args.budget,
        "variant": "balanced",
        "constraints": {"heatFriendly": True, "walking": True},
    }
    return apply_timeline(route, request)


def save_story(route, story_id, static=False):
    target_dir = STATIC_STORIES if static else STORY_PACKS
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{story_id}.json"
    path.write_text(json.dumps(route, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description="Generate a Mood-to-Map story pack through Hermes Agent.")
    parser.add_argument("--city", choices=sorted(PLACES["cities"].keys()), default="miami")
    parser.add_argument("--intent", default=DEFAULT_INTENT)
    parser.add_argument("--duration", choices=["2h", "half-day", "full-day"], default="half-day")
    parser.add_argument("--start-time", default="10:00")
    parser.add_argument("--budget", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--id", default="")
    parser.add_argument("--model", default=os.environ.get("KIMI_MODEL", "moonshotai/kimi-k2-0905"))
    parser.add_argument("--provider", default=os.environ.get("HERMES_PROVIDER", "custom:nousportalapi"))
    parser.add_argument("--site-url", default=os.environ.get("MOOD_TO_MAP_PUBLIC_URL", "https://mood-to-map.vercel.app"))
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--static", action="store_true", help="Save to data/stories instead of logs/story-packs.")
    args = parser.parse_args()

    story_id = safe_story_id(args.id or f"hermes-{args.city}-{slugify(args.intent)}-{int(time.time())}")
    assets = load_assets()
    available_places = compact_places(args.city, assets)
    prompt = build_prompt(args, available_places)

    print("Starting Hermes Route Studio...")
    print(f"City: {PLACES['cities'][args.city]['name']}")
    print(f"Intent: {args.intent}")
    print("Hermes is creating the route pack. This can take 30-90 seconds.")
    output = run_hermes(args, prompt)
    raw = parse_json_object(output)
    route = normalize_story(raw, args, assets, available_places, story_id)
    path = save_story(route, story_id, static=args.static)
    url = f"{args.site_url.rstrip('/')}/?story={story_id}&source=vps"

    print()
    print("Hermes Route Studio complete")
    print(f"Story id: {story_id}")
    print(f"Saved: {path}")
    print(f"Open: {url}")


if __name__ == "__main__":
    main()
