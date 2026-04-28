const cityCenters = {
  miami: [25.7743, -80.1937],
  dubai: [25.2048, 55.2708],
  "abu-dhabi": [24.4539, 54.3773],
};

const cityNames = {
  miami: "Miami",
  dubai: "Dubai",
  "abu-dhabi": "Abu Dhabi",
};

const form = document.querySelector("#routeForm");
const promptInput = document.querySelector("#prompt");
const statusPill = document.querySelector("#statusPill");
const routeSource = document.querySelector("#routeSource");
const routeTitle = document.querySelector("#routeTitle");
const routeLogline = document.querySelector("#routeLogline");
const routeMeta = document.querySelector("#routeMeta");
const stopsList = document.querySelector("#stopsList");
const mapCaption = document.querySelector("#mapCaption");
const submitButton = document.querySelector(".primary-action");
const aiPlacesToggle = document.querySelector("#useAiPlaces");
const routeTools = document.querySelector("#routeTools");
const routeMapsLink = document.querySelector("#routeMapsLink");
const variantButtons = document.querySelectorAll("[data-variant]");
const attractionPanel = document.querySelector("#attractionPanel");
const attractionPhoto = document.querySelector("#attractionPhoto");
const attractionSource = document.querySelector("#attractionSource");
const attractionTitle = document.querySelector("#attractionTitle");
const attractionSummary = document.querySelector("#attractionSummary");
const attractionMeta = document.querySelector("#attractionMeta");
const attractionCopy = document.querySelector("#attractionCopy");
const attractionMapsLink = document.querySelector("#attractionMapsLink");
const attractionWikiLink = document.querySelector("#attractionWikiLink");

const map = L.map("map", {
  zoomControl: false,
  scrollWheelZoom: true,
}).setView(cityCenters.miami, 12);

L.control.zoom({ position: "bottomleft" }).addTo(map);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap",
}).addTo(map);

let attractionLayer = L.layerGroup().addTo(map);
let markerLayer = L.layerGroup().addTo(map);
let routeLine = null;
let currentRoute = null;
let currentVariant = "balanced";
let lockedStopIds = new Set();
let currentAttractions = [];

async function init() {
  document.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      promptInput.value = button.dataset.prompt;
      promptInput.focus();
    });
  });

  document.querySelectorAll("input[name='city']").forEach((input) => {
    input.addEventListener("change", () => {
      const city = getSelectedCity();
      map.setView(cityCenters[city], city === "dubai" ? 11 : 12);
      mapCaption.textContent = cityNames[city];
      loadAttractions(city, true);
    });
  });

  variantButtons.forEach((button) => {
    button.addEventListener("click", () => {
      currentVariant = button.dataset.variant || "balanced";
      updateVariantButtons();
      generateRoute({ statusText: "Trying variant" });
    });
  });

  try {
    const health = await fetch("/api/health").then((res) => res.json());
    if (health.kimi) {
      statusPill.textContent = health.provider || "Kimi";
      statusPill.classList.add("kimi");
    } else {
      statusPill.textContent = "Local";
    }
  } catch {
    statusPill.textContent = "Offline";
  }

  loadAttractions(getSelectedCity(), false);
}

function getSelectedCity() {
  return new FormData(form).get("city") || "miami";
}

function formPayload(options = {}) {
  const data = new FormData(form);
  return {
    city: data.get("city"),
    prompt: data.get("prompt"),
    persona: data.get("persona"),
    duration: data.get("duration"),
    startTime: data.get("startTime"),
    budget: data.get("budget"),
    variant: currentVariant,
    lockedStops: options.lockedStops || lockedStopsFromCurrentRoute(),
    excludedStops: options.excludedStops || [],
    useKimi: data.get("useKimi") === "on",
    useAiPlaces: data.get("useAiPlaces") === "on",
    constraints: {
      avoidCrowds: data.get("avoidCrowds") === "on",
      heatFriendly: data.get("heatFriendly") === "on",
      walking: data.get("walking") === "on",
      family: data.get("family") === "on",
    },
  };
}

async function generateRoute(options = {}) {
  const payload = formPayload(options);
  submitButton.disabled = true;
  submitButton.textContent = "Generating";
  routeSource.textContent = options.statusText || (payload.useAiPlaces ? "Asking Kimi for places" : "Working");

  try {
    let route = await requestRoute(payload);
    if (route.error) {
      throw new Error(route.error);
    }
    renderRoute(route);
  } catch (error) {
    try {
      routeSource.textContent = "Recovering";
      const fallbackRoute = await requestRoute({
        ...payload,
        lockedStops: payload.lockedStops || [],
        excludedStops: payload.excludedStops || [],
        useKimi: false,
        useAiPlaces: false,
      });
      if (fallbackRoute.error) {
        throw new Error(fallbackRoute.error);
      }
      fallbackRoute.ai_places_error = error.message || "AI route request failed";
      renderRoute(fallbackRoute);
    } catch (fallbackError) {
      routeSource.textContent = "Error";
      routeTitle.textContent = "Route failed";
      routeLogline.textContent = fallbackError.message || error.message;
      routeMeta.innerHTML = "";
      stopsList.innerHTML = "";
    }
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Generate route";
  }
}

async function requestRoute(payload) {
  const response = await fetch("/api/route", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  if (!text.trim()) {
    throw new Error(`Empty response from route service (${response.status})`);
  }
  let route;
  try {
    route = JSON.parse(text);
  } catch {
    throw new Error(`Route service returned invalid JSON (${response.status})`);
  }
  if (!response.ok || route.error) {
    throw new Error(route.error || `Route request failed (${response.status})`);
  }
  return route;
}

function updateVariantButtons() {
  variantButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.variant === currentVariant);
  });
}

function stopKey(stop) {
  return String(stop?.id || stop?.name || "")
    .trim()
    .toLowerCase();
}

function syncLocksToRoute(route) {
  const routeKeys = new Set((route.stops || []).map((stop) => stopKey(stop)));
  lockedStopIds = new Set([...lockedStopIds].filter((key) => routeKeys.has(key)));
  (route.stops || []).forEach((stop) => {
    if (stop.locked) {
      lockedStopIds.add(stopKey(stop));
    }
  });
}

function serializeStop(stop, index) {
  return {
    position: index + 1,
    id: stop.id,
    name: stop.name,
    lat: stop.lat,
    lng: stop.lng,
    duration_minutes: stop.duration_minutes,
    best_time: stop.best_time,
    budget: stop.budget || [],
    tags: stop.tags || [],
    summary: stop.summary || "",
    reason: stop.reason || "",
    micro_story: stop.micro_story || "",
    activity: stop.activity || "",
    what_to_do: stop.what_to_do || "",
    interesting_fact: stop.interesting_fact || "",
    local_tip: stop.local_tip || "",
    walk_note: stop.walk_note || "",
    photo_prompt: stop.photo_prompt || "",
    next_move: stop.next_move || "",
    swap: stop.swap || "",
  };
}

function lockedStopsFromCurrentRoute() {
  if (!currentRoute?.stops) return [];
  return currentRoute.stops
    .map((stop, index) => ({ stop, index }))
    .filter(({ stop }) => lockedStopIds.has(stopKey(stop)))
    .map(({ stop, index }) => serializeStop(stop, index));
}

function lockedStopsExcept(indexToReplace) {
  if (!currentRoute?.stops) return [];
  return currentRoute.stops
    .map((stop, index) => ({ stop, index }))
    .filter(({ index }) => index !== indexToReplace)
    .map(({ stop, index }) => serializeStop(stop, index));
}

async function replaceStop(index) {
  if (!currentRoute?.stops?.[index]) return;
  const lockedStops = lockedStopsExcept(index);
  lockedStopIds = new Set(lockedStops.map((stop) => stopKey(stop)));
  const excludedStops = [
    {
      ...serializeStop(currentRoute.stops[index], index),
      reason: "User asked to replace this stop.",
    },
  ];
  await generateRoute({
    lockedStops,
    excludedStops,
    statusText: "Replacing stop",
  });
}

function buildStopMapsUrl(stop) {
  const query = encodeURIComponent(`${stop.lat},${stop.lng}`);
  return `https://www.google.com/maps/search/?api=1&query=${query}`;
}

function compactText(value, maxLength = 260) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 3).trim()}...`;
}

function buildRouteMapsUrl(route) {
  const stops = route?.stops || [];
  if (!stops.length) return "#";
  if (stops.length === 1) return buildStopMapsUrl(stops[0]);
  const origin = `${stops[0].lat},${stops[0].lng}`;
  const destination = `${stops[stops.length - 1].lat},${stops[stops.length - 1].lng}`;
  const waypoints = stops.slice(1, -1).map((stop) => `${stop.lat},${stop.lng}`).join("|");
  const travelmode = new FormData(form).get("walking") === "on" ? "walking" : "driving";
  const params = new URLSearchParams({
    api: "1",
    origin,
    destination,
    travelmode,
  });
  if (waypoints) {
    params.set("waypoints", waypoints);
  }
  return `https://www.google.com/maps/dir/?${params.toString()}`;
}

async function loadAttractions(city, fitMap = false) {
  try {
    const data = await fetch(`/api/attractions?city=${encodeURIComponent(city)}`).then((res) => res.json());
    if (data.error) throw new Error(data.error);
    currentAttractions = data.attractions || [];
    renderAttractions(currentAttractions, fitMap);
  } catch (error) {
    currentAttractions = [];
    attractionLayer.clearLayers();
    if (attractionPanel) {
      attractionPanel.hidden = false;
      attractionTitle.textContent = "Attractions unavailable";
      attractionSummary.textContent = error.message;
      attractionCopy.innerHTML = "";
      attractionMeta.innerHTML = "";
    }
  }
}

function renderAttractions(attractions, fitMap = false) {
  attractionLayer.clearLayers();
  attractions.forEach((attraction, index) => {
    const marker = L.marker([attraction.lat, attraction.lng], {
      zIndexOffset: -200,
      icon: L.divIcon({
        className: "",
        html: `
          <div class="poi-label ${attractionTone(attraction, index)}">
            <span>${index + 1}</span>
            <strong>${escapeHtml(shortStopName(attraction.name))}</strong>
          </div>
        `,
        iconAnchor: [12, 12],
      }),
    }).bindPopup(`
      <div class="poi-popup">
        ${attraction.photo_url ? `<img src="${escapeHtml(attraction.photo_url)}" alt="" />` : ""}
        <p class="popup-title">${escapeHtml(attraction.name)}</p>
        <p class="popup-text">${escapeHtml((attraction.tags || []).slice(0, 3).join(" · "))}</p>
      </div>
    `);
    marker.on("click", () => showAttraction(attraction));
    attractionLayer.addLayer(marker);
  });

  if (fitMap && attractions.length && !currentRoute) {
    const points = attractions.map((item) => [item.lat, item.lng]);
    map.fitBounds(L.latLngBounds(points), { padding: [48, 48], maxZoom: 12 });
  }
}

async function showAttraction(attraction) {
  if (!attractionPanel) return;
  attractionPanel.hidden = false;
  attractionTitle.textContent = attraction.name;
  attractionSource.textContent = `${cityNames[attraction.city] || attraction.city} attraction`;
  attractionSummary.textContent =
    compactText(attraction.wiki_extract, 220) || "Asking Kimi for a practical guide note.";
  attractionMeta.innerHTML = "";
  (attraction.tags || []).slice(0, 5).forEach((tag) => {
    const pill = document.createElement("span");
    pill.textContent = tag;
    attractionMeta.appendChild(pill);
  });
  attractionPhoto.src = attraction.photo_url || "";
  attractionPhoto.alt = attraction.photo_url ? `${attraction.name} photo` : "";
  attractionPhoto.hidden = !attraction.photo_url;
  attractionMapsLink.href = buildStopMapsUrl(attraction);
  attractionWikiLink.href = attraction.wiki_url || "#";
  attractionWikiLink.hidden = !attraction.wiki_url;
  attractionCopy.innerHTML = `<p class="attraction-loading">Asking Kimi for this place...</p>`;

  try {
    const payload = {
      city: attraction.city,
      id: attraction.id,
      prompt: promptInput.value || "",
    };
    const response = await fetch("/api/attraction-copy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "Attraction copy failed");
    const copy = data.copy || {};
    renderAttractionCopy(copy);
    if (data.attraction?.photo_url) {
      attractionPhoto.src = data.attraction.photo_url;
      attractionPhoto.hidden = false;
    }
  } catch (error) {
    attractionCopy.innerHTML = `
      <div class="attraction-copy-row">
        <span>Note</span>
        <p>${escapeHtml(error.message)}</p>
      </div>
    `;
  }
}

function renderAttractionCopy(copy) {
  if (copy.summary) {
    attractionSummary.textContent = copy.summary;
  }
  attractionCopy.innerHTML = `
    ${renderAttractionCopyRow("What it is", copy.what_it_is)}
    ${renderAttractionCopyRow("Why go", copy.why_go)}
    ${renderAttractionCopyRow("What to do", copy.what_to_do)}
    ${renderAttractionCopyRow("Look for", copy.look_for)}
    ${renderAttractionCopyRow("Time", copy.time_needed)}
    ${renderAttractionCopyRow("Fact", copy.interesting_fact)}
    ${renderAttractionCopyRow("Tip", copy.local_tip)}
    ${renderAttractionCopyRow("Best for", copy.best_for)}
    ${
      copy.llm?.usage?.total_tokens
        ? `<div class="attraction-copy-row"><span>Proof</span><p>${escapeHtml(copy.model || "")} · ${escapeHtml(copy.llm.usage.total_tokens)} tokens</p></div>`
        : ""
    }
  `;
}

function renderAttractionCopyRow(label, value) {
  if (!value) return "";
  return `
    <div class="attraction-copy-row">
      <span>${escapeHtml(label)}</span>
      <p>${escapeHtml(value)}</p>
    </div>
  `;
}

function attractionTone(attraction, index) {
  const tags = new Set(attraction.tags || []);
  if (tags.has("food") || tags.has("market")) return "warm";
  if (tags.has("waterfront") || tags.has("views") || tags.has("beach")) return "blue";
  if (tags.has("museum") || tags.has("art") || tags.has("culture")) return "pink";
  if (tags.has("garden") || tags.has("nature") || tags.has("park")) return "green";
  return index % 2 ? "violet" : "dark";
}

function renderRoute(route) {
  currentRoute = route;
  currentVariant = route.variant || currentVariant || "balanced";
  syncLocksToRoute(route);
  updateVariantButtons();
  if (routeTools) {
    routeTools.hidden = false;
  }
  if (routeMapsLink) {
    routeMapsLink.href = buildRouteMapsUrl(route);
  }

  const llm = route.llm || {};
  routeSource.textContent = route.model
    ? route.ai_places_error
      ? `${llm.provider || "Kimi"} AI fallback: ${route.model}`
      : route.source === "kimi-discovered"
      ? `${llm.provider || "Kimi"} AI places: ${route.model}`
      : `${llm.provider || "Kimi"} polished: ${route.model}`
    : route.ai_places_error
    ? "Local fallback"
    : "Local director";
  routeTitle.textContent = route.title;
  routeLogline.textContent = route.logline;

  routeMeta.innerHTML = "";
  [
    `${route.stops.length} stops`,
    `${Math.round(route.total_minutes / 5) * 5} min`,
    route.start_time && route.end_time ? `${route.start_time}-${route.end_time}` : "",
    route.city.name,
    route.variant_label || "",
    llm.mode || "",
    route.ai_places_error ? "AI fallback" : "",
    llm.usage?.total_tokens ? `${llm.usage.total_tokens} tokens` : "",
    typeof llm.usage?.cost === "number" ? `$${llm.usage.cost.toFixed(4)}` : "",
  ].forEach((item) => {
    if (!item) return;
    const pill = document.createElement("span");
    pill.textContent = item;
    routeMeta.appendChild(pill);
  });

  stopsList.innerHTML = "";
  route.stops.forEach((stop, index) => {
    const key = stopKey(stop);
    const locked = lockedStopIds.has(key);
    const item = document.createElement("li");
    item.className = `stop-card${locked ? " is-locked" : ""}`;
    item.innerHTML = `
      <div>
        <div class="stop-topline">
          ${stop.time_label ? `<span class="stop-time">${escapeHtml(stop.time_label)}</span>` : ""}
          <span>${escapeHtml(stop.duration_minutes || "")} min</span>
          ${stop.best_time ? `<span>${escapeHtml(stop.best_time)}</span>` : ""}
          ${locked ? "<span>Locked</span>" : ""}
        </div>
        <div class="stop-title-row">
          <h3>${escapeHtml(stop.name)}</h3>
          <div class="stop-actions">
            <button type="button" data-action="toggle-lock" data-index="${index}">${locked ? "Unlock" : "Lock"}</button>
            <button type="button" data-action="replace-stop" data-index="${index}">Replace</button>
            <a href="${escapeHtml(buildStopMapsUrl(stop))}" target="_blank" rel="noreferrer">Maps</a>
          </div>
        </div>
        <p class="stop-summary">${escapeHtml(stop.summary || stop.reason || "")}</p>
        <div class="stop-detail-grid">
          ${renderStopDetail("Do", stop.activity || stop.what_to_do)}
          ${renderStopDetail("Plan", stop.what_to_do)}
          ${renderStopDetail("Notice", stop.walk_note || stop.micro_story)}
          ${renderStopDetail("Fact", stop.interesting_fact)}
          ${renderStopDetail("Tip", stop.local_tip)}
          ${renderStopDetail("Next", stop.next_move)}
        </div>
        <div class="stop-tags">
          ${(stop.tags || []).slice(0, 5).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}
        </div>
      </div>
    `;
    stopsList.appendChild(item);
  });

  renderMap(route);
}

function renderMap(route) {
  markerLayer.clearLayers();
  if (routeLine) {
    map.removeLayer(routeLine);
  }

  const points = route.stops.map((stop) => [stop.lat, stop.lng]);
  route.stops.forEach((stop, index) => {
    const marker = L.marker([stop.lat, stop.lng], {
      zIndexOffset: 400,
      icon: L.divIcon({
        className: "",
        html: `
          <div class="map-label ${markerTone(stop, index)}">
            <span>${index + 1}</span>
            <strong>${escapeHtml(shortStopName(stop.name))}</strong>
          </div>
        `,
        iconAnchor: [14, 14],
      }),
    }).bindPopup(`
      <p class="popup-title">${index + 1}. ${escapeHtml(stop.name)}</p>
      <p class="popup-meta">${escapeHtml(stop.time_label || "")}</p>
      <p class="popup-text">${escapeHtml(stop.summary)}</p>
      <p class="popup-text">${escapeHtml(stop.activity || "")}</p>
    `);
    markerLayer.addLayer(marker);
  });

  routeLine = L.polyline(points, {
    color: "#c64d3f",
    weight: 4,
    opacity: 0.86,
  }).addTo(map);

  if (points.length) {
    map.fitBounds(L.latLngBounds(points), { padding: [42, 42], maxZoom: 13 });
  } else {
    map.setView(route.city.center, 12);
  }
  mapCaption.textContent = route.timeline_note || route.route_note;
}

function markerTone(stop, index) {
  const tags = new Set(stop.tags || []);
  if (stop.locked || lockedStopIds.has(stopKey(stop))) return "locked";
  if (tags.has("food") || tags.has("coffee") || tags.has("market")) return "warm";
  if (tags.has("waterfront") || tags.has("views") || tags.has("skyline")) return "blue";
  if (tags.has("museum") || tags.has("art") || tags.has("culture")) return "pink";
  return index % 2 ? "violet" : "green";
}

function shortStopName(name) {
  const cleaned = String(name || "Stop")
    .replace(/\s*\/\s*/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned.length > 24 ? `${cleaned.slice(0, 22).trim()}...` : cleaned;
}

function renderStopDetail(label, value) {
  if (!value) return "";
  return `
    <div class="stop-detail">
      <span>${escapeHtml(label)}</span>
      <p>${escapeHtml(value)}</p>
    </div>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  generateRoute();
});

stopsList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const index = Number(button.dataset.index);
  const stop = currentRoute?.stops?.[index];
  if (!stop) return;
  if (button.dataset.action === "toggle-lock") {
    const key = stopKey(stop);
    if (lockedStopIds.has(key)) {
      lockedStopIds.delete(key);
    } else {
      lockedStopIds.add(key);
    }
    renderRoute(currentRoute);
  }
  if (button.dataset.action === "replace-stop") {
    replaceStop(index);
  }
});

init();
