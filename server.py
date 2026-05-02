#!/usr/bin/env python3
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DATA = ROOT / "data" / "places.json"
ATTRACTIONS_DATA = ROOT / "data" / "attractions.json"
ATTRACTION_ASSETS_DATA = ROOT / "data" / "attraction_assets.json"
DEFAULT_LOGS = Path("/tmp/mood-to-map-logs") if os.environ.get("VERCEL") else ROOT / "logs"
LOGS = Path(os.environ.get("MOOD_TO_MAP_LOG_DIR", str(DEFAULT_LOGS)))
STORY_PACKS = LOGS / "story-packs"
STATIC_STORIES = ROOT / "data" / "stories"
ATTRACTION_ASSET_CACHE = LOGS / "attraction-assets-cache.json"
ATTRACTION_COPY_CACHE = LOGS / "attraction-copy-cache.json"
ATTRACTION_COPY_VERSION = "v2-place-briefing"
DEFAULT_MOONSHOT_BASE_URL = "https://api.moonshot.ai/v1"
DEFAULT_NOUS_BASE_URL = "https://inference-api.nousresearch.com/v1"
DEFAULT_HERMES_STORY_SOURCE_URL = "http://77.232.41.200:8080/api/story"
DEFAULT_LLM_TIMEOUT_SECONDS = 90
WIKI_USER_AGENT = "Mood-to-Map-Hackathon/1.0 (Nous demo city guide)"


def load_env():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

PLACES = json.loads(DATA.read_text(encoding="utf-8"))
ATTRACTIONS = json.loads(ATTRACTIONS_DATA.read_text(encoding="utf-8"))
PREBUILT_ATTRACTION_ASSETS = (
    json.loads(ATTRACTION_ASSETS_DATA.read_text(encoding="utf-8-sig")) if ATTRACTION_ASSETS_DATA.exists() else {}
)

PERSONAS = {
    "cinematic": {
        "label": "Cinematic",
        "keywords": ["cinematic", "film", "views", "sunset", "neon", "photo", "skyline", "iconic"],
        "tags": ["views", "architecture", "waterfront", "photo", "sunset", "cinematic"],
        "tone": "visual, paced like a short film",
    },
    "local": {
        "label": "Local",
        "keywords": ["local", "authentic", "hidden", "neighborhood", "food", "quiet"],
        "tags": ["local", "food", "neighborhood", "culture", "walkable", "hidden"],
        "tone": "grounded, specific, less obvious",
    },
    "architecture": {
        "label": "Architecture",
        "keywords": ["architecture", "design", "buildings", "urban", "museum", "modern"],
        "tags": ["architecture", "design", "museum", "modern", "heritage"],
        "tone": "observant, spatial, design-aware",
    },
    "luxury": {
        "label": "Quiet Luxury",
        "keywords": ["luxury", "premium", "elegant", "calm", "coffee", "hotel"],
        "tags": ["luxury", "calm", "views", "coffee", "design", "premium"],
        "tone": "restrained, polished, calm",
    },
    "food": {
        "label": "Food-first",
        "keywords": ["food", "coffee", "restaurant", "market", "cafe", "dessert"],
        "tags": ["food", "coffee", "market", "local", "neighborhood"],
        "tone": "sensory and practical",
    },
    "family": {
        "label": "Family",
        "keywords": ["family", "kids", "easy", "safe", "park", "short"],
        "tags": ["family", "park", "easy", "interactive", "shade"],
        "tone": "clear, relaxed, low-friction",
    },
    "introvert": {
        "label": "Introvert",
        "keywords": ["quiet", "introvert", "calm", "solo", "reading", "less crowded"],
        "tags": ["calm", "quiet", "museum", "garden", "waterfront", "shade"],
        "tone": "soft, spacious, unhurried",
    },
}

CONSTRAINT_TAGS = {
    "avoidCrowds": ["quiet", "calm", "hidden", "garden", "museum"],
    "heatFriendly": ["indoor", "shade", "museum", "mall", "evening"],
    "walking": ["walkable", "neighborhood", "compact"],
    "family": ["family", "park", "easy", "interactive"],
}

ROLE_ORDER = ["opening", "discovery", "contrast", "pause", "golden_hour", "finale"]

CITY_BOUNDS = {
    "miami": {"lat": [25.62, 25.93], "lng": [-80.37, -80.08]},
    "dubai": {"lat": [24.95, 25.36], "lng": [55.02, 55.47]},
    "abu-dhabi": {"lat": [24.25, 24.62], "lng": [54.25, 54.65]},
}

DEFAULT_START_TIMES = {
    "2h": "10:00",
    "half-day": "10:00",
    "full-day": "09:00",
}

TARGET_ROUTE_MINUTES = {
    "2h": 150,
    "half-day": 330,
    "full-day": 510,
}

ROUTE_VARIANTS = {
    "balanced": {
        "label": "Balanced",
        "tags": [],
        "instruction": "Balance landmarks, local texture, scenery, and practical movement.",
    },
    "local": {
        "label": "More local",
        "tags": ["local", "food", "coffee", "neighborhood", "hidden", "market", "culture"],
        "instruction": "Prefer neighborhood texture, food or coffee stops, smaller cultural places, and less obvious choices.",
    },
    "scenic": {
        "label": "More scenic",
        "tags": ["views", "waterfront", "sunset", "photo", "cinematic", "skyline", "garden"],
        "instruction": "Prefer views, waterfront movement, photo-friendly places, golden-hour scenes, and a strong visual finale.",
    },
    "less-walking": {
        "label": "Less walking",
        "tags": ["indoor", "shade", "compact", "mall", "museum", "easy", "family"],
        "instruction": "Prefer compact, shaded, indoor, easy-transfer stops and avoid long open-air walks.",
    },
}


def normalize(text):
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def wanted_count(duration):
    return {"2h": 3, "half-day": 5, "full-day": 7}.get(duration, 5)


def city_places(city_id):
    return [p for p in PLACES["places"] if p["city"] == city_id]


def city_attractions(city_id):
    return [a for a in ATTRACTIONS["attractions"] if a["city"] == city_id]


def attraction_key(attraction):
    return f"{attraction['city']}:{attraction['id']}"


def find_attraction(city_id, attraction_id):
    for attraction in city_attractions(city_id):
        if attraction["id"] == attraction_id:
            return attraction
    return None


def load_cache(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_cache(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def safe_story_id(value):
    story_id = str(value or "").strip()
    if not re.match(r"^[a-z0-9][a-z0-9-]{2,96}$", story_id):
        raise ValueError("Invalid story id")
    return story_id


def read_story_pack(story_id):
    for directory in (STORY_PACKS, STATIC_STORIES):
        path = directory / f"{story_id}.json"
        if path.exists() and path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def fetch_vps_story(story_id):
    base_url = os.environ.get("HERMES_STORY_SOURCE_URL", DEFAULT_HERMES_STORY_SOURCE_URL).rstrip("/")
    url = f"{base_url}?{urlencode({'id': story_id})}"
    req = urllib.request.Request(url, headers={"User-Agent": WIKI_USER_AGENT})
    with urllib.request.urlopen(req, timeout=12) as res:
        return json.loads(res.read().decode("utf-8"))


def handle_story(query):
    params = parse_qs(query or "")
    story_id = safe_story_id(params.get("id", [""])[0])
    source = (params.get("source", [""])[0] or "").strip().lower()
    if source == "vps":
        return fetch_vps_story(story_id)
    story = read_story_pack(story_id)
    if not story:
        raise ValueError("Story not found")
    return story


def wiki_summary(title):
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}"
    req = urllib.request.Request(url, headers={"User-Agent": WIKI_USER_AGENT})
    with urllib.request.urlopen(req, timeout=8) as res:
        return json.loads(res.read().decode("utf-8"))


def commons_photo_search(query):
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": "1",
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": "640",
        "format": "json",
    }
    req = urllib.request.Request(
        "https://commons.wikimedia.org/w/api.php?" + urlencode(params),
        headers={"User-Agent": WIKI_USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=8) as res:
        data = json.loads(res.read().decode("utf-8"))
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return ""
    first = next(iter(pages.values()))
    image_info = (first.get("imageinfo") or [{}])[0]
    return image_info.get("thumburl") or image_info.get("url", "")


def get_attraction_asset(attraction):
    cache = load_cache(ATTRACTION_ASSET_CACHE)
    key = attraction_key(attraction)
    if key in cache and cache[key].get("photo_url"):
        return cache[key]
    if key in PREBUILT_ATTRACTION_ASSETS and PREBUILT_ATTRACTION_ASSETS[key].get("photo_url"):
        return PREBUILT_ATTRACTION_ASSETS[key]

    asset = {
        "photo_url": "",
        "wiki_extract": "",
        "wiki_url": "",
        "photo_source": "Wikipedia/Wikimedia",
        "asset_error": "",
    }
    try:
        data = wiki_summary(attraction.get("wiki_title") or attraction["name"])
        asset["photo_url"] = data.get("thumbnail", {}).get("source", "")
        asset["wiki_extract"] = data.get("extract", "")
        asset["wiki_url"] = data.get("content_urls", {}).get("desktop", {}).get("page", "")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        asset["asset_error"] = str(exc)

    if not asset["photo_url"]:
        try:
            asset["photo_url"] = commons_photo_search(f"{attraction['name']} {PLACES['cities'][attraction['city']]['name']}")
            if asset["photo_url"] and not asset["wiki_url"]:
                asset["wiki_url"] = f"https://commons.wikimedia.org/wiki/Special:MediaSearch?type=image&search={quote(attraction['name'])}"
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            asset["asset_error"] = asset["asset_error"] or str(exc)

    cache[key] = asset
    write_cache(ATTRACTION_ASSET_CACHE, cache)
    return asset


def serialize_attraction(attraction):
    asset = get_attraction_asset(attraction)
    return {
        **attraction,
        "photo_url": asset.get("photo_url", ""),
        "wiki_extract": asset.get("wiki_extract", ""),
        "wiki_url": asset.get("wiki_url", ""),
        "photo_source": asset.get("photo_source", "Wikipedia/Wikimedia"),
        "asset_error": asset.get("asset_error", ""),
    }


def route_variant(request):
    variant = str(request.get("variant") or "balanced").strip().lower()
    if variant not in ROUTE_VARIANTS:
        return "balanced"
    return variant


def variant_config(request):
    return ROUTE_VARIANTS[route_variant(request)]


def score_place(place, request):
    prompt_words = set(normalize(request.get("prompt", "")))
    persona = PERSONAS.get(request.get("persona", "cinematic"), PERSONAS["cinematic"])
    variant = variant_config(request)
    tags = set(place.get("tags", []))
    score = 0.0

    score += len(tags.intersection(persona["tags"])) * 3
    score += len(tags.intersection(variant["tags"])) * 2.5
    score += len(prompt_words.intersection(tags)) * 4
    score += len(prompt_words.intersection(normalize(place.get("name", "")))) * 3
    score += len(prompt_words.intersection(normalize(place.get("summary", "")))) * 2

    for keyword in persona["keywords"]:
        if keyword in prompt_words:
            score += 2

    constraints = request.get("constraints", {})
    for key, tag_list in CONSTRAINT_TAGS.items():
        if constraints.get(key):
            score += len(tags.intersection(tag_list)) * 2

    budget = request.get("budget", "medium")
    if budget in place.get("budget", []):
        score += 2
    elif budget == "low" and "premium" in tags:
        score -= 3

    if constraints.get("avoidCrowds") and place.get("crowd") == "high":
        score -= 4
    if constraints.get("heatFriendly") and not tags.intersection({"indoor", "shade", "evening", "museum", "mall"}):
        score -= 2
    if route_variant(request) == "less-walking" and not tags.intersection({"indoor", "shade", "compact", "walkable", "museum", "mall"}):
        score -= 2.5

    score += place.get("editorial_weight", 0)
    return score


def order_route(candidates, count):
    if count <= 0:
        return []
    by_role = {role: [] for role in ROLE_ORDER}
    for place in candidates:
        by_role.setdefault(place.get("story_role", "discovery"), []).append(place)

    ordered = []
    used = set()
    for role in ROLE_ORDER:
        for place in by_role.get(role, []):
            if place["id"] not in used:
                ordered.append(place)
                used.add(place["id"])
                break

    for place in candidates:
        if place["id"] not in used:
            ordered.append(place)
            used.add(place["id"])
        if len(ordered) >= count:
            break

    return ordered[:count]


def safe_stop_id(value, fallback):
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")[:48]
    return cleaned or fallback


def identity_values(stop):
    values = set()
    for key in ("id", "name"):
        raw = stop.get(key) if isinstance(stop, dict) else None
        if raw:
            values.add(str(raw).strip().lower())
            values.add(safe_stop_id(raw, ""))
    return {value for value in values if value}


def excluded_identities(request):
    identities = set()
    raw_items = request.get("excludedStops", [])
    if not isinstance(raw_items, list):
        raw_items = []
    for item in raw_items:
        if isinstance(item, dict):
            identities.update(identity_values(item))
        elif isinstance(item, str):
            identities.add(item.strip().lower())
            identities.add(safe_stop_id(item, ""))
    return {item for item in identities if item}


def normalize_client_stops(raw_stops, request):
    normalized = []
    if not isinstance(raw_stops, list):
        return normalized
    for index, raw in enumerate(raw_stops, 1):
        if not isinstance(raw, dict):
            continue
        name = meaningful_text(raw.get("name"))
        try:
            lat = float(raw.get("lat"))
            lng = float(raw.get("lng"))
        except (TypeError, ValueError):
            continue
        if not name or not in_city_bounds(request["city"], lat, lng):
            continue
        try:
            position = int(raw.get("position") or index)
        except (TypeError, ValueError):
            position = index
        stop_id = safe_stop_id(raw.get("id") or name, f"locked-stop-{index}")
        normalized.append(
            {
                "id": stop_id,
                "name": name,
                "lat": lat,
                "lng": lng,
                "position": max(1, min(position, wanted_count(request.get("duration")))),
                "duration_minutes": clamp_duration(raw.get("duration_minutes")),
                "best_time": meaningful_text(raw.get("best_time")) or "flexible",
                "budget": raw.get("budget") if isinstance(raw.get("budget"), list) else [request.get("budget", "medium")],
                "tags": coerce_tags(raw.get("tags")) or ["locked"],
                "summary": meaningful_text(raw.get("summary")) or f"Locked stop: {name}.",
                "reason": meaningful_text(raw.get("reason")) or "Locked by the traveler for this route.",
                "micro_story": meaningful_text(raw.get("micro_story")) or meaningful_text(raw.get("story")) or "Keep this beat in the route.",
                "story": meaningful_text(raw.get("micro_story")) or meaningful_text(raw.get("story")) or "Keep this beat in the route.",
                "route_logic": meaningful_text(raw.get("reason")) or "it was locked by the traveler",
                "activity": meaningful_text(raw.get("activity")),
                "what_to_do": meaningful_text(raw.get("what_to_do")),
                "interesting_fact": meaningful_text(raw.get("interesting_fact")),
                "local_tip": meaningful_text(raw.get("local_tip")),
                "walk_note": meaningful_text(raw.get("walk_note")),
                "photo_prompt": meaningful_text(raw.get("photo_prompt")),
                "next_move": meaningful_text(raw.get("next_move")),
                "swap": meaningful_text(raw.get("swap")),
                "locked": True,
            }
        )
    return normalized


def compact_client_stops(stops):
    return [
        {
            "position": stop.get("position"),
            "id": stop.get("id"),
            "name": stop.get("name"),
            "lat": stop.get("lat"),
            "lng": stop.get("lng"),
            "summary": stop.get("summary"),
            "tags": stop.get("tags", [])[:5],
        }
        for stop in stops
    ]


def compact_excluded_stops(request):
    result = []
    raw_items = request.get("excludedStops", [])
    if not isinstance(raw_items, list):
        return result
    for item in raw_items[:8]:
        if isinstance(item, dict):
            result.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "reason": item.get("reason") or "User asked to replace or avoid this stop.",
                }
            )
        elif isinstance(item, str):
            result.append({"name": item, "reason": "User asked to avoid this stop."})
    return result


def merge_locked_and_generated(locked_stops, generated_stops, count):
    result = [None] * count
    used = set()
    for stop in locked_stops[:count]:
        slot = max(0, min(count - 1, int(stop.get("position", 1)) - 1))
        while slot < count and result[slot] is not None:
            slot += 1
        if slot >= count:
            continue
        result[slot] = stop
        used.update(identity_values(stop))

    for stop in generated_stops:
        identities = identity_values(stop)
        if identities.intersection(used):
            continue
        for slot, existing in enumerate(result):
            if existing is None:
                result[slot] = stop
                used.update(identities)
                break

    return [stop for stop in result if stop is not None][:count]


def local_copy(route, request):
    persona = PERSONAS.get(request.get("persona", "cinematic"), PERSONAS["cinematic"])
    city = PLACES["cities"][request["city"]]
    variant = variant_config(request)
    prompt = request.get("prompt", "").strip()
    mood = prompt if prompt else f"a {persona['label'].lower()} day"

    title = f"{city['name']}: {variant['label']} Route"
    logline = f"A {duration_label(request.get('duration'))} route shaped around {mood}, with a {variant['label'].lower()} bias."
    trailer = f"Start with a clear first scene, let the city reveal a less obvious layer, then end somewhere the route clicks into place."

    stops = []
    for index, place in enumerate(route, 1):
        reason = place.get("reason") if place.get("locked") else build_reason(place, request, index, len(route))
        stops.append(
            {
                "id": place["id"],
                "name": place["name"],
                "lat": place["lat"],
                "lng": place["lng"],
                "duration_minutes": place.get("duration_minutes", 45),
                "best_time": place.get("best_time", "flexible"),
                "budget": place.get("budget", []),
                "tags": place.get("tags", []),
                "summary": place["summary"],
                "reason": reason,
                "micro_story": place.get("micro_story") or place.get("story", ""),
                "activity": place.get("activity", ""),
                "what_to_do": place.get("what_to_do", ""),
                "interesting_fact": place.get("interesting_fact", ""),
                "local_tip": place.get("local_tip", ""),
                "walk_note": place.get("walk_note", ""),
                "photo_prompt": place.get("photo_prompt", ""),
                "next_move": place.get("next_move", ""),
                "swap": place.get("swap", ""),
                "locked": bool(place.get("locked")),
            }
        )

    return {
        "source": "local-director",
        "model": None,
        "city": city,
        "title": title,
        "logline": logline,
        "route_note": trailer,
        "variant": route_variant(request),
        "variant_label": variant["label"],
        "stops": stops,
        "total_minutes": sum(s["duration_minutes"] for s in stops),
        "created_at": int(time.time()),
    }


def duration_label(duration):
    return {"2h": "two-hour", "half-day": "half-day", "full-day": "full-day"}.get(duration, "half-day")


def build_reason(place, request, index, total):
    persona = PERSONAS.get(request.get("persona", "cinematic"), PERSONAS["cinematic"])
    role = place.get("story_role", "discovery").replace("_", " ")
    if index == 1:
        frame = "It opens the route with"
    elif index == total:
        frame = "It works as the finale because of"
    else:
        frame = f"It adds a {role} beat through"
    shared = ", ".join(sorted(set(place.get("tags", [])).intersection(persona["tags"]))[:3])
    if shared:
        return f"{frame} {shared}: {place['route_logic']}"
    return f"{frame} this contrast: {place['route_logic']}"


def parse_clock(value, fallback):
    candidate = str(value or fallback or "").strip()
    match = re.match(r"^(\d{1,2}):(\d{2})$", candidate)
    if not match:
        candidate = fallback
        match = re.match(r"^(\d{1,2}):(\d{2})$", candidate)
    if not match:
        return 10 * 60
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return 10 * 60
    return hour * 60 + minute


def format_clock(total_minutes):
    total_minutes = total_minutes % (24 * 60)
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def distance_km(first, second):
    lat1 = math.radians(float(first.get("lat", 0)))
    lng1 = math.radians(float(first.get("lng", 0)))
    lat2 = math.radians(float(second.get("lat", 0)))
    lng2 = math.radians(float(second.get("lng", 0)))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    a = min(1, max(0, a))
    return 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def clamp_duration(value):
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = 45
    return max(20, min(minutes, 120))


def round_to_five(minutes):
    return int(max(20, min(120, round(minutes / 5) * 5)))


def estimate_move_minutes(city_id, current_stop, next_stop, request):
    km = distance_km(current_stop, next_stop)
    walking_bias = request.get("constraints", {}).get("walking")
    tags = set(current_stop.get("tags", []) + next_stop.get("tags", []))
    compact_pair = tags.intersection({"walkable", "neighborhood", "compact", "waterfront"})
    if walking_bias and compact_pair:
        return int(max(8, min(18, round(6 + km * 3))))
    base = 9 if city_id == "miami" else 12
    cap = 20 if request.get("duration") != "full-day" else 26
    return int(max(8, min(cap, round(base + km * 1.6))))


def fit_durations_to_route_length(stops, move_windows, request):
    target = TARGET_ROUTE_MINUTES.get(request.get("duration"), TARGET_ROUTE_MINUTES["half-day"])
    move_total = sum(move_windows)
    available_stop_minutes = max(len(stops) * 20, target - move_total)
    current_stop_minutes = sum(stop["duration_minutes"] for stop in stops)
    if current_stop_minutes <= available_stop_minutes:
        return
    scale = available_stop_minutes / current_stop_minutes
    for stop in stops:
        stop["duration_minutes"] = round_to_five(stop["duration_minutes"] * scale)


def default_activity(stop, request, index, total):
    tags = set(stop.get("tags", []))
    if tags.intersection({"museum", "indoor"}):
        return "Walk the main public spaces first, then choose one room or terrace to slow down in."
    if tags.intersection({"food", "coffee", "market"}):
        return "Treat this as the sensory stop: order one small thing, sit briefly, and watch the neighborhood rhythm."
    if tags.intersection({"waterfront", "views", "sunset"}):
        return "Take a slow edge walk, pause for the view, then use the final minutes for photos or quiet."
    if tags.intersection({"architecture", "design"}):
        return "Make a short design loop and notice entrances, shade, materials, and how people move through the space."
    if index == total:
        return "Use this as the closing scene: stop moving, look back at the route, and let the day resolve."
    return "Walk one compact loop, choose a detail that matches your mood, and leave before the stop feels overworked."


def default_what_to_do(stop, request):
    name = stop.get("name", "this stop")
    best_time = stop.get("best_time", "flexible")
    return f"Spend the first 10 minutes orienting around {name}, then follow the most interesting side path. Best window: {best_time}."


def default_fact(stop):
    tags = ", ".join(stop.get("tags", [])[:3])
    if tags:
        return f"This stop is useful in the route because it carries the {tags} layer of the city."
    return "This stop gives the route a different rhythm from the previous place."


def default_tip(stop, request):
    if request.get("constraints", {}).get("heatFriendly"):
        return "Keep this stop flexible: use shade, indoor pauses, or a shorter loop if the heat is high."
    if request.get("constraints", {}).get("avoidCrowds"):
        return "Stay at the edges first; the quieter details usually appear just off the obvious path."
    return "Do not turn it into a checklist. Pick one small observation and let that lead the stop."


def default_walk_note(stop):
    tags = set(stop.get("tags", []))
    if tags.intersection({"waterfront", "views"}):
        return "Start on the most open edge, then turn back once so the skyline changes direction."
    if tags.intersection({"architecture", "design"}):
        return "Look for the transition between public space, facade, and shade."
    if tags.intersection({"food", "market", "neighborhood"}):
        return "Let sound, smell, and foot traffic decide the small detour."
    return "Do one unhurried pass before deciding whether to linger."


def default_photo_prompt(stop):
    tags = set(stop.get("tags", []))
    if tags.intersection({"cinematic", "views", "sunset", "waterfront"}):
        return "Frame one wide establishing shot, then one close detail that would belong in the same scene."
    if tags.intersection({"architecture", "design"}):
        return "Shoot a clean line, a texture, and a human-scale detail."
    return "Capture the detail that best explains why this stop is on the route."


def apply_stop_defaults(stop, request, index, total):
    stop["duration_minutes"] = clamp_duration(stop.get("duration_minutes"))
    defaults = {
        "summary": stop.get("summary") or "A useful stop for this route.",
        "activity": default_activity(stop, request, index, total),
        "what_to_do": default_what_to_do(stop, request),
        "interesting_fact": default_fact(stop),
        "local_tip": default_tip(stop, request),
        "walk_note": default_walk_note(stop),
        "photo_prompt": default_photo_prompt(stop),
    }
    for key, value in defaults.items():
        stop[key] = meaningful_text(stop.get(key)) or value


def apply_timeline(route_result, request):
    stops = route_result.get("stops", [])
    variant = variant_config(request)
    route_result["variant"] = route_variant(request)
    route_result["variant_label"] = variant["label"]
    fallback_start = DEFAULT_START_TIMES.get(request.get("duration"), "10:00")
    current = parse_clock(request.get("startTime"), fallback_start)
    route_result["start_time"] = format_clock(current)
    total = 0

    for index, stop in enumerate(stops):
        apply_stop_defaults(stop, request, index + 1, len(stops))

    move_windows = [
        estimate_move_minutes(request["city"], stops[index], stops[index + 1], request)
        for index in range(max(0, len(stops) - 1))
    ]
    fit_durations_to_route_length(stops, move_windows, request)

    for index, stop in enumerate(stops):
        start = current
        end = start + stop["duration_minutes"]
        stop["start_time"] = format_clock(start)
        stop["end_time"] = format_clock(end)
        stop["time_label"] = f"{stop['start_time']} - {stop['end_time']}"
        total += stop["duration_minutes"]

        if index < len(stops) - 1:
            move_minutes = move_windows[index]
            stop["transfer_to_next_minutes"] = move_minutes
            stop["next_move"] = meaningful_text(stop.get("next_move")) or (
                f"Plan about {move_minutes} minutes as a flexible move window to the next stop."
            )
            current = end + move_minutes
            total += move_minutes
        else:
            stop["transfer_to_next_minutes"] = 0
            stop["next_move"] = meaningful_text(stop.get("next_move")) or "End here, or keep this as the optional linger stop."
            current = end

    route_result["end_time"] = format_clock(current)
    route_result["total_minutes"] = total
    route_result["timeline_note"] = (
        f"Starts at {route_result['start_time']} and ends around {route_result['end_time']}. "
        "Move windows are rough buffers, not live traffic."
    )
    return route_result


def select_route(request):
    count = wanted_count(request.get("duration"))
    locked_stops = normalize_client_stops(request.get("lockedStops", []), request)[:count]
    excluded = excluded_identities(request)
    locked_identities = set()
    for stop in locked_stops:
        locked_identities.update(identity_values(stop))

    places = [
        p
        for p in city_places(request["city"])
        if not identity_values(p).intersection(excluded) and not identity_values(p).intersection(locked_identities)
    ]
    scored = sorted(
        [{**p, "_score": score_place(p, request)} for p in places],
        key=lambda p: p["_score"],
        reverse=True,
    )
    needed = max(0, count - len(locked_stops))
    candidates = scored[: max(10, needed + 4)]
    generated = order_route(candidates, needed)
    return merge_locked_and_generated(locked_stops, generated, count), candidates


def llm_config():
    kimi_key = os.environ.get("KIMI_API_KEY", "").strip()
    nous_key = os.environ.get("NOUS_API_KEY", "").strip()
    if nous_key:
        base_url = os.environ.get("NOUS_INFERENCE_BASE_URL") or os.environ.get("KIMI_BASE_URL") or DEFAULT_NOUS_BASE_URL
        return {
            "enabled": True,
            "provider": "Nous + Kimi",
            "key": nous_key,
            "base_url": base_url.rstrip("/"),
            "model": os.environ.get("KIMI_MODEL", "moonshotai/kimi-k2-0905"),
        }
    if kimi_key:
        return {
            "enabled": True,
            "provider": "Moonshot Kimi",
            "key": kimi_key,
            "base_url": os.environ.get("KIMI_BASE_URL", DEFAULT_MOONSHOT_BASE_URL).rstrip("/"),
            "model": os.environ.get("KIMI_MODEL", "kimi-k2.5"),
        }
    return {
        "enabled": False,
        "provider": "Local",
        "key": "",
        "base_url": "",
        "model": None,
    }


def safe_endpoint_label(base_url):
    host = urlparse(base_url or "").netloc
    return host or "local"


def write_llm_proof(config, model, city, request, usage, mode):
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        (LOGS / "kimi-proof.jsonl").open("a", encoding="utf-8").write(
            json.dumps(
                {
                    "ts": int(time.time()),
                    "mode": mode,
                    "provider": config["provider"],
                    "model": model,
                    "endpoint": safe_endpoint_label(config["base_url"]),
                    "city": city["name"],
                    "prompt": request.get("prompt", ""),
                    "usage": usage,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    except OSError:
        pass


def llm_json_request(config, messages, max_tokens=None):
    payload = {
        "model": config["model"],
        "messages": messages,
        "temperature": 0.45,
        "max_tokens": int(max_tokens or os.environ.get("LLM_MAX_TOKENS", "950")),
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{config['base_url']}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config['key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    timeout_seconds = int(os.environ.get("LLM_TIMEOUT_SECONDS", str(DEFAULT_LLM_TIMEOUT_SECONDS)))
    with urllib.request.urlopen(req, timeout=timeout_seconds) as res:
        raw = res.read().decode("utf-8")
    data = json.loads(raw)
    message = data["choices"][0].get("message", {})
    content = message.get("content") or message.get("reasoning") or ""
    return data, parse_json_object(content)


def parse_json_object(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def meaningful_text(value, *blocked):
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    if not cleaned:
        return ""
    blocked_values = {
        "string",
        "one sentence",
        "one compact paragraph",
        "matching id",
        "why this stop fits the prompt",
        "stable general knowledge",
        "practical tip",
        "concrete 1-sentence mini-plan",
    }
    blocked_values.update(item.lower() for item in blocked)
    if cleaned.lower() in blocked_values:
        return ""
    return cleaned


def normalize_stop_copy(raw):
    if isinstance(raw, list):
        result = {}
        for item in raw:
            if isinstance(item, dict) and item.get("id"):
                result[item["id"]] = item
        return result
    if isinstance(raw, dict):
        result = {}
        for stop_id, item in raw.items():
            if isinstance(item, dict):
                result[stop_id] = item
            elif isinstance(item, str):
                result[stop_id] = {"micro_story": item}
        return result
    return {}


def coerce_tags(value):
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()][:7]
    if isinstance(value, str):
        return [item.strip().lower() for item in value.split(",") if item.strip()][:7]
    return []


def in_city_bounds(city_id, lat, lng):
    bounds = CITY_BOUNDS.get(city_id)
    if not bounds:
        return True
    return bounds["lat"][0] <= lat <= bounds["lat"][1] and bounds["lng"][0] <= lng <= bounds["lng"][1]


def normalize_ai_stops(raw_stops, request):
    stops = []
    seen = set()
    excluded = excluded_identities(request)
    for index, raw in enumerate(raw_stops if isinstance(raw_stops, list) else [], 1):
        if not isinstance(raw, dict):
            continue
        name = meaningful_text(raw.get("name"))
        try:
            lat = float(raw.get("lat"))
            lng = float(raw.get("lng"))
        except (TypeError, ValueError):
            continue
        if not name or not in_city_bounds(request["city"], lat, lng):
            continue
        stop_id = safe_stop_id(raw.get("id") or name, f"ai-stop-{index}")
        if {name.lower(), stop_id}.intersection(excluded):
            continue
        if stop_id in seen:
            stop_id = f"{stop_id}-{index}"
        seen.add(stop_id)
        try:
            duration = int(raw.get("duration_minutes") or 45)
        except (TypeError, ValueError):
            duration = 45
        stops.append(
            {
                "id": stop_id,
                "name": name,
                "lat": lat,
                "lng": lng,
                "duration_minutes": max(20, min(duration, 120)),
                "best_time": meaningful_text(raw.get("best_time")) or "flexible",
                "budget": [request.get("budget", "medium")],
                "tags": coerce_tags(raw.get("tags")) or ["ai-discovered"],
                "summary": meaningful_text(raw.get("summary")) or "AI-selected place for this route.",
                "reason": meaningful_text(raw.get("reason")) or "Kimi selected it for the requested mood and constraints.",
                "micro_story": meaningful_text(raw.get("micro_story")) or meaningful_text(raw.get("story")) or "Notice how this stop changes the rhythm of the route.",
                "activity": meaningful_text(raw.get("activity")),
                "what_to_do": meaningful_text(raw.get("what_to_do")),
                "interesting_fact": meaningful_text(raw.get("interesting_fact")) or meaningful_text(raw.get("fact")),
                "local_tip": meaningful_text(raw.get("local_tip")) or meaningful_text(raw.get("tip")),
                "walk_note": meaningful_text(raw.get("walk_note")),
                "photo_prompt": meaningful_text(raw.get("photo_prompt")),
                "next_move": meaningful_text(raw.get("next_move")),
                "swap": meaningful_text(raw.get("swap")),
            }
        )
    return stops


def kimi_discover_route(request):
    config = llm_config()
    if not config["enabled"]:
        return None

    city = PLACES["cities"][request["city"]]
    persona = PERSONAS.get(request.get("persona", "cinematic"), PERSONAS["cinematic"])
    variant = variant_config(request)
    count = wanted_count(request.get("duration"))
    locked_stops = normalize_client_stops(request.get("lockedStops", []), request)[:count]
    bounds = CITY_BOUNDS.get(request["city"], {})
    system = (
        "You are Hermes Route Director inside a hackathon demo, running through Nous Portal and Kimi. "
        "Create stable city routes from general knowledge. Do not use live events. "
        "Do not invent opening hours, floors, ticket access, reservations, prices, or named cafes unless supplied. "
        "Do not invent architects, dates, records, material provenance, or historical claims. "
        "If a factual detail is uncertain, write a visual observation instead. "
        "Return one valid JSON object only. Do not wrap it in markdown."
    )
    user = {
        "mode": "ai_discovered_places",
        "city": city["name"],
        "city_center": city["center"],
        "allowed_coordinate_bounds": bounds,
        "stop_count": count,
        "traveler_prompt": request.get("prompt", ""),
        "persona": persona["label"],
        "route_variant": {"id": route_variant(request), "label": variant["label"], "instruction": variant["instruction"]},
        "duration": request.get("duration"),
        "start_time": request.get("startTime") or DEFAULT_START_TIMES.get(request.get("duration"), "10:00"),
        "budget": request.get("budget"),
        "constraints": request.get("constraints", {}),
        "locked_stops": compact_client_stops(locked_stops),
        "excluded_stops": compact_excluded_stops(request),
        "open_stop_count": max(0, count - len(locked_stops)),
        "output_contract": (
            "Return exactly one JSON object with keys: title, logline, route_note, stops. "
            "stops must be an array of real, stable places in the selected city. "
            "Respect route_variant. If locked_stops are provided, preserve them conceptually and fill the open slots. "
            "Do not include any place listed in excluded_stops. "
            "Each stop must include name, lat, lng, duration_minutes, best_time, tags, summary, reason, micro_story, "
            "activity, what_to_do, interesting_fact, local_tip, walk_note, photo_prompt. "
            "Coordinates must be decimal WGS84 and inside allowed_coordinate_bounds. "
            "activity is the main thing to do there. what_to_do is a concrete 1-sentence mini-plan. "
            "interesting_fact must be conservative, stable general knowledge, not live info. "
            "If uncertain, start it with 'Detail:' and describe something visible instead of making a factual claim. "
            "local_tip must be practical. "
            "Keep uncertain details general and practical. "
            "Keep title under 8 words, logline under 22 words, route_note under 35 words, "
            "and each stop field under 22 words."
        ),
    }

    try:
        data, discovered = llm_json_request(
            config,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            max_tokens=os.environ.get("LLM_DISCOVERY_MAX_TOKENS", "1300"),
        )
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError, ValueError) as exc:
        return {"error": str(exc), "model": config["model"], "provider": config["provider"]}

    stops = normalize_ai_stops(discovered.get("stops", []), request)
    stops = merge_locked_and_generated(locked_stops, stops, count)
    if len(stops) < 3:
        return {
            "error": f"AI returned only {len(stops)} valid in-city stops",
            "model": data.get("model") or config["model"],
            "provider": config["provider"],
        }
    stops = stops[:count]
    usage = data.get("usage", {})
    actual_model = data.get("model") or config["model"]
    write_llm_proof(config, actual_model, city, request, usage, "ai-discovery")
    return {
        "source": "kimi-discovered",
        "model": actual_model,
        "llm": {
            "provider": config["provider"],
            "model": actual_model,
            "endpoint": safe_endpoint_label(config["base_url"]),
            "usage": usage,
            "mode": "AI-discovered places",
        },
        "city": city,
        "title": meaningful_text(discovered.get("title")) or f"{city['name']}: AI Discovery Route",
        "logline": meaningful_text(discovered.get("logline")) or f"A {duration_label(request.get('duration'))} route generated from your mood.",
        "route_note": meaningful_text(discovered.get("route_note")) or "Kimi selected the route points and the app validated that they sit inside the city.",
        "variant": route_variant(request),
        "variant_label": variant["label"],
        "stops": stops,
        "total_minutes": sum(s["duration_minutes"] for s in stops),
        "created_at": int(time.time()),
    }


def kimi_polish(route, candidates, request):
    config = llm_config()
    if not config["enabled"]:
        return None

    model = config["model"]
    city = PLACES["cities"][request["city"]]
    persona = PERSONAS.get(request.get("persona", "cinematic"), PERSONAS["cinematic"])
    variant = variant_config(request)
    compact_stops = [
        {
            "id": p["id"],
            "name": p["name"],
            "summary": p["summary"],
            "story": p["story"],
            "route_logic": p["route_logic"],
            "tags": p["tags"],
            "best_time": p.get("best_time"),
            "duration_minutes": p.get("duration_minutes", 45),
        }
        for p in route
    ]

    system = (
        "You are Hermes Route Director inside a hackathon demo, running through Nous Portal and Kimi. "
        "Write compact, concrete city-guide copy. Do not invent places, prices, or events. "
        "Do not invent opening hours, floors, ticket access, transit times, reservations, or named cafes unless supplied. "
        "Do not invent architects, dates, records, material provenance, or historical claims. "
        "If a factual detail is uncertain, write a visual observation instead. "
        "Return one valid JSON object only. Do not wrap it in markdown."
    )
    user = {
        "city": city["name"],
        "traveler_prompt": request.get("prompt", ""),
        "persona": persona["label"],
        "route_variant": {"id": route_variant(request), "label": variant["label"], "instruction": variant["instruction"]},
        "duration": request.get("duration"),
        "budget": request.get("budget"),
        "start_time": request.get("startTime") or DEFAULT_START_TIMES.get(request.get("duration"), "10:00"),
        "constraints": request.get("constraints", {}),
        "selected_stops": compact_stops,
        "output_contract": (
            "Return exactly one JSON object with keys: title, logline, route_note, stop_copy. "
            "title: a finished route title, not a placeholder. "
            "logline: one polished sentence. "
            "route_note: one compact practical paragraph. "
            "stop_copy: an object keyed by selected stop id; each value has reason, micro_story, activity, "
            "what_to_do, interesting_fact, local_tip, walk_note, photo_prompt. "
            "activity is the main thing to do there. what_to_do is a concrete 1-sentence mini-plan. "
            "interesting_fact must be conservative; if uncertain, start with 'Detail:' and describe something visible. "
            "Use only facts in selected_stops; keep uncertain details general. "
            "Keep each stop field under 22 words. "
            "Never output placeholder words such as string, matching id, or one sentence."
        ),
    }

    try:
        data, polished = llm_json_request(
            config,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            max_tokens=os.environ.get("LLM_POLISH_MAX_TOKENS", "1200"),
        )
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError, ValueError) as exc:
        return {"error": str(exc), "model": model, "provider": config["provider"]}

    usage = data.get("usage", {})
    actual_model = data.get("model") or model

    write_llm_proof(config, actual_model, city, request, usage, "curated-polish")
    return {
        "model": actual_model,
        "provider": config["provider"],
        "endpoint": safe_endpoint_label(config["base_url"]),
        "usage": usage,
        "data": polished,
    }


def merge_polish(base, polish):
    if not polish or "data" not in polish:
        if polish and polish.get("error"):
            base["kimi_error"] = polish["error"]
            base["kimi_model_attempted"] = polish.get("model")
        return base

    data = polish["data"]
    base["source"] = "kimi-polished"
    base["model"] = polish["model"]
    base["llm"] = {
        "provider": polish.get("provider", "Kimi"),
        "model": polish["model"],
        "endpoint": polish.get("endpoint", ""),
        "usage": polish.get("usage", {}),
        "mode": "Curated places + Kimi itinerary copy",
    }
    base["title"] = meaningful_text(data.get("title")) or base["title"]
    base["logline"] = meaningful_text(data.get("logline")) or base["logline"]
    base["route_note"] = meaningful_text(data.get("route_note")) or base["route_note"]
    copy_by_id = normalize_stop_copy(data.get("stop_copy", {}))
    for stop in base["stops"]:
        item = copy_by_id.get(stop["id"])
        if item:
            stop["reason"] = meaningful_text(item.get("reason")) or stop["reason"]
            stop["micro_story"] = meaningful_text(item.get("micro_story"), "what to notice there, 1-2 sentences") or stop["micro_story"]
            for key in ("activity", "what_to_do", "interesting_fact", "local_tip", "walk_note", "photo_prompt"):
                stop[key] = meaningful_text(item.get(key)) or stop.get(key, "")
    return base


def handle_attractions(query):
    params = parse_qs(query or "")
    city = (params.get("city", ["miami"])[0] or "miami").strip()
    if city not in PLACES["cities"]:
        raise ValueError("Unknown city")
    attractions = [serialize_attraction(attraction) for attraction in city_attractions(city)]
    return {
        "city": PLACES["cities"][city],
        "count": len(attractions),
        "attractions": attractions,
    }


def fallback_attraction_copy(attraction, asset):
    extract = meaningful_text(asset.get("wiki_extract")) or f"{attraction['name']} is a stable point of interest in this city."
    city_name = PLACES["cities"][attraction["city"]]["name"]
    tag_context = ", ".join(attraction.get("tags", [])[:4]) or "city context"
    return {
        "source": "local-wiki-fallback",
        "model": None,
        "summary": extract[:360],
        "what_it_is": extract[:300],
        "why_go": f"Use it to understand the {tag_context} side of {city_name}, then decide if it fits the route mood.",
        "what_to_do": "Start with one slow orientation loop, pick the most visually clear edge or entrance, then spend a few minutes on photos or people-watching.",
        "look_for": "Look for the detail that explains the place fastest: facade, view corridor, public space, crowd rhythm, or how locals move through it.",
        "time_needed": "Plan 20-40 minutes unless you want a deeper museum, beach, or waterfront stop.",
        "interesting_fact": "The photo, map position, and nearby streets usually explain why this place belongs in the city's mental map.",
        "local_tip": "Open it in Maps before committing; travel time can change the value of the stop more than distance alone.",
        "best_for": tag_context,
    }


def kimi_attraction_copy(attraction, asset, request):
    config = llm_config()
    if not config["enabled"]:
        return fallback_attraction_copy(attraction, asset)

    cache = load_cache(ATTRACTION_COPY_CACHE)
    key = f"{ATTRACTION_COPY_VERSION}:{attraction_key(attraction)}:{config['model']}"
    if key in cache:
        return cache[key]

    city = PLACES["cities"][attraction["city"]]
    system = (
        "You are Hermes City Guide inside a hackathon demo, running through Nous Portal and Kimi. "
        "Write concrete, practical guide copy for a map point of interest. "
        "The user must immediately understand what the place is, why it matters, and what to do there. "
        "Avoid generic travel phrases such as hidden gem, vibrant, must-see, flexible anchor, or city layer. "
        "Do not invent opening hours, prices, current events, reservations, or exact access rules. "
        "Use only stable general knowledge and the supplied wiki extract. "
        "Return one valid JSON object only."
    )
    user = {
        "city": city["name"],
        "place": attraction["name"],
        "tags": attraction.get("tags", []),
        "traveler_mood": request.get("prompt", ""),
        "wiki_extract": asset.get("wiki_extract", "")[:1400],
        "output_contract": (
            "Return keys: summary, what_it_is, why_go, what_to_do, look_for, time_needed, "
            "interesting_fact, local_tip, best_for. "
            "summary: 32-48 words, plain language, explain the place without hype. "
            "what_it_is: 1-2 sentences, name the category and why it is known. "
            "why_go: 1 sentence about the route value for a visitor. "
            "what_to_do: 2 concrete actions a visitor can do on site. "
            "look_for: 2-3 specific visual/cultural details to notice. "
            "time_needed: practical time range, no opening hours. "
            "interesting_fact: one stable fact or context point. "
            "local_tip: one practical caution or route tip. "
            "best_for: 2-4 comma-separated use cases."
        ),
    }
    try:
        data, generated = llm_json_request(
            config,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            max_tokens=900,
        )
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError, ValueError) as exc:
        fallback = fallback_attraction_copy(attraction, asset)
        fallback["kimi_error"] = str(exc)
        return fallback

    actual_model = data.get("model") or config["model"]
    usage = data.get("usage", {})
    write_llm_proof(config, actual_model, city, {"prompt": f"attraction:{attraction['name']}"}, usage, "attraction-copy")
    result = {
        "source": "kimi-attraction-copy",
        "model": actual_model,
        "llm": {
            "provider": config["provider"],
            "model": actual_model,
            "endpoint": safe_endpoint_label(config["base_url"]),
            "usage": usage,
            "mode": "Kimi attraction description",
        },
        "summary": meaningful_text(generated.get("summary")) or fallback_attraction_copy(attraction, asset)["summary"],
        "what_it_is": meaningful_text(generated.get("what_it_is")) or fallback_attraction_copy(attraction, asset)["what_it_is"],
        "why_go": meaningful_text(generated.get("why_go")) or "It helps turn the city from a set of names into a place you can actually read on foot.",
        "what_to_do": meaningful_text(generated.get("what_to_do")) or fallback_attraction_copy(attraction, asset)["what_to_do"],
        "look_for": meaningful_text(generated.get("look_for")) or fallback_attraction_copy(attraction, asset)["look_for"],
        "time_needed": meaningful_text(generated.get("time_needed")) or fallback_attraction_copy(attraction, asset)["time_needed"],
        "interesting_fact": meaningful_text(generated.get("interesting_fact")) or "Use the photo and surrounding streets to understand why this stop matters in the route.",
        "local_tip": meaningful_text(generated.get("local_tip")) or "Use Maps before committing; the best stop is often the one with the cleanest transfer.",
        "best_for": meaningful_text(generated.get("best_for")) or ", ".join(attraction.get("tags", [])[:3]),
    }
    cache[key] = result
    write_cache(ATTRACTION_COPY_CACHE, cache)
    return result


def handle_attraction_copy(body):
    request = json.loads(body.decode("utf-8-sig") or "{}")
    city = request.get("city", "miami")
    if city not in PLACES["cities"]:
        raise ValueError("Unknown city")
    attraction = find_attraction(city, request.get("id", ""))
    if not attraction:
        raise ValueError("Unknown attraction")
    asset = get_attraction_asset(attraction)
    return {
        "attraction": serialize_attraction(attraction),
        "copy": kimi_attraction_copy(attraction, asset, request),
    }


def handle_route(body):
    request = json.loads(body.decode("utf-8-sig") or "{}")
    city = request.get("city", "miami")
    if city not in PLACES["cities"]:
        raise ValueError("Unknown city")
    request["city"] = city
    skip_polish = False
    if request.get("useAiPlaces") and request.get("useKimi", True):
        discovered = kimi_discover_route(request)
        if discovered and "error" not in discovered:
            return apply_timeline(discovered, request)
        skip_polish = True
    route, candidates = select_route(request)
    base = local_copy(route, request)
    if request.get("useKimi", True) and not skip_polish:
        polish = kimi_polish(route, candidates, request)
        base = merge_polish(base, polish)
    if request.get("useAiPlaces") and "discovered" in locals() and discovered and discovered.get("error"):
        base["ai_places_error"] = discovered["error"]
    return apply_timeline(base, request)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            config = llm_config()
            self.send_json(
                {
                    "ok": True,
                    "kimi": config["enabled"],
                    "provider": config["provider"],
                    "model": config["model"],
                    "endpoint": safe_endpoint_label(config["base_url"]),
                }
            )
            return
        if parsed.path == "/api/attractions":
            try:
                self.send_json(handle_attractions(parsed.query))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/story":
            try:
                self.send_json(handle_story(parsed.query))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if re.match(r"^/(story|journey)/[a-z0-9][a-z0-9-]{2,96}/?$", parsed.path, re.IGNORECASE):
            self.path = "/index.html"
            return super().do_GET()
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/route", "/api/attraction-copy"}:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            body = self.rfile.read(length)
            if parsed.path == "/api/route":
                result = handle_route(body)
            else:
                result = handle_attraction_copy(body)
            self.send_json(result)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=400)

    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main():
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Mood-to-Map listening on http://0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
