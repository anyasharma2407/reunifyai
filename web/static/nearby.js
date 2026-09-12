/*
 * "Find help near you."
 *
 * Everything else in this project is a caseworker's tool running on synthetic
 * data. This is the one feature an affected person might actually act on, and
 * that changes what is acceptable.
 *
 * Two rules follow from it, and both are load-bearing:
 *
 *   1. The data is real or there is no data. Showing someone a fictional
 *      shelter while they are looking for one is the worst thing this
 *      application could do. The synthetic corridor stays in the matching
 *      demo, where nobody is going to walk to it. If a lookup fails, this says
 *      so rather than falling back to something invented.
 *
 *   2. Places are labelled as what the map says they are. OpenStreetMap knows
 *      about pharmacies, clinics and community centres; it does not know which
 *      of them is running a relief operation today. Calling a pharmacy a
 *      "rescue camp" because the surrounding page is about displacement would
 *      be a lie with consequences, so each result carries its own category and
 *      the provenance is stated where it cannot be missed.
 *
 * Location handling: coordinates are rounded to about 100 m before they are
 * sent, the query goes to OpenStreetMap's public Overpass service and nowhere
 * else, nothing is stored, and searching by place name is offered as an equal
 * alternative for anyone who does not want to share GPS at all -- which, for
 * someone who may be fleeing, is a reasonable thing not to want.
 */
(function () {
  "use strict";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const OVERPASS = "https://overpass-api.de/api/interpreter";
  const NOMINATIM = "https://nominatim.openstreetmap.org/search";
  const RADIUS_M = 6000;

  // What each OSM tag actually means, in plain words. Nothing here is
  // upgraded into a claim the map does not make.
  const KINDS = {
    refugee_site:     { label: "Refugee site",        rank: 0 },
    shelter:          { label: "Shelter",             rank: 0 },
    homeless_shelter: { label: "Shelter",             rank: 0 },
    emergency_shelter:{ label: "Emergency shelter",   rank: 0 },
    assembly_point:   { label: "Assembly point",      rank: 0 },
    food_bank:        { label: "Food bank",           rank: 1 },
    soup_kitchen:     { label: "Food distribution",   rank: 1 },
    hospital:         { label: "Hospital",            rank: 1 },
    clinic:           { label: "Clinic",              rank: 1 },
    doctors:          { label: "Doctor",              rank: 2 },
    pharmacy:         { label: "Pharmacy",            rank: 3 },
    social_facility:  { label: "Social facility",     rank: 2 },
    day_centre:       { label: "Day centre",          rank: 2 },
    outreach:         { label: "Outreach service",    rank: 2 },
    water_point:      { label: "Water point",         rank: 2 },
    centre:           { label: "Health centre",       rank: 1 },
    doctor:           { label: "Doctor",              rank: 2 },
    community_centre: { label: "Community centre",    rank: 2 },
    drinking_water:   { label: "Drinking water",      rank: 2 },
    police:           { label: "Police",              rank: 3 },
    fire_station:     { label: "Fire station",        rank: 3 },
  };

  // Toilets are deliberately not queried. Near a camp they are mapped in the
  // hundreds -- 209 of 250 results in one test of this area -- and they were
  // filling the result cap before a single refugee site reached the browser. A
  // panel that buries the camp under two hundred latrines is worse than one
  // that does not mention latrines.
  //
  // Tag choices matter more than they look. A refugee camp is amenity=refugee_site
  // -- an amenity value, not a social_facility one, which is how this query
  // originally had it and why camps never appeared. amenity=shelter is filtered
  // by shelter_type because, untagged, it is usually a bus stop. Clinics are
  // matched on healthcare=* as well, because a large number are tagged that way
  // and nothing else.
  const QUERY = (lat, lon) => `[out:json][timeout:40];
(
  nwr["amenity"~"^(refugee_site|hospital|clinic|doctors|pharmacy|police|fire_station|community_centre|social_facility|drinking_water|water_point)$"](around:${RADIUS_M},${lat},${lon});
  nwr["amenity"="shelter"]["shelter_type"~"^(emergency_shelter|basic_hut)$"](around:${RADIUS_M},${lat},${lon});
  nwr["emergency"~"^(assembly_point|shelter)$"](around:${RADIUS_M},${lat},${lon});
  nwr["social_facility"~"^(shelter|homeless_shelter|food_bank|soup_kitchen|day_centre|outreach)$"](around:${RADIUS_M},${lat},${lon});
  nwr["healthcare"~"^(hospital|clinic|centre|doctor)$"](around:${RADIUS_M},${lat},${lon});
  nwr["man_made"="water_tap"]["drinking_water"="yes"](around:${RADIUS_M},${lat},${lon});
);
out center 400;`;

  let panel = null;

  function close() {
    if (panel) panel.remove();
    panel = null;
    document.body.classList.remove("demo-open");
  }

  // --- geometry ---------------------------------------------------------

  function distanceM(aLat, aLon, bLat, bLon) {
    const R = 6371000, rad = Math.PI / 180;
    const dLat = (bLat - aLat) * rad, dLon = (bLon - aLon) * rad;
    const s = Math.sin(dLat / 2) ** 2 +
      Math.cos(aLat * rad) * Math.cos(bLat * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(s));
  }

  function bearing(aLat, aLon, bLat, bLon) {
    const rad = Math.PI / 180;
    const y = Math.sin((bLon - aLon) * rad) * Math.cos(bLat * rad);
    const x = Math.cos(aLat * rad) * Math.sin(bLat * rad) -
      Math.sin(aLat * rad) * Math.cos(bLat * rad) * Math.cos((bLon - aLon) * rad);
    const deg = (Math.atan2(y, x) / rad + 360) % 360;
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(deg / 45) % 8];
  }

  const readable = (m) => m < 1000
    ? Math.round(m / 10) * 10 + " m"
    : (m / 1000).toFixed(m < 10000 ? 1 : 0) + " km";

  // --- data -------------------------------------------------------------

  function classify(tags) {
    const keys = [tags.amenity === "refugee_site" ? "refugee_site" : null,
                  tags.emergency, tags.social_facility, tags.shelter_type,
                  tags.amenity, tags.healthcare,
                  tags.man_made === "water_tap" ? "drinking_water" : null];
    for (const k of keys) if (k && KINDS[k]) return KINDS[k];
    return null;
  }

  async function lookup(lat, lon) {
    const res = await fetch(OVERPASS, {
      method: "POST",
      body: new URLSearchParams({ data: QUERY(lat, lon) }),
    });
    if (!res.ok) throw new Error("the map service did not respond (" + res.status + ")");
    const data = await res.json();

    const seen = new Set();
    const out = [];
    for (const el of data.elements || []) {
      const t = el.tags || {};
      const kind = classify(t);
      if (!kind) continue;
      const plat = el.lat ?? (el.center && el.center.lat);
      const plon = el.lon ?? (el.center && el.center.lon);
      if (plat == null || plon == null) continue;
      const name = t.name || t["name:en"] || "";
      const key = (name || "") + "|" + plat.toFixed(4) + "|" + plon.toFixed(4);
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        name: name, kind: kind, lat: plat, lon: plon,
        distance: distanceM(lat, lon, plat, plon),
        dir: bearing(lat, lon, plat, plon),
        hours: t.opening_hours || "",
        phone: t.phone || t["contact:phone"] || "",
        address: [t["addr:housenumber"], t["addr:street"]].filter(Boolean).join(" "),
      });
    }
    out.sort((a, b) => (a.kind.rank - b.kind.rank) || (a.distance - b.distance));
    return out;
  }

  async function geocode(place) {
    const url = NOMINATIM + "?" + new URLSearchParams({
      q: place, format: "json", limit: "1",
    });
    const res = await fetch(url, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error("place lookup failed (" + res.status + ")");
    const hits = await res.json();
    if (!hits.length) throw new Error("no place found matching “" + place + "”");
    return { lat: Number(hits[0].lat), lon: Number(hits[0].lon), label: hits[0].display_name };
  }

  // --- rendering --------------------------------------------------------

  function renderResults(list, originLabel) {
    const host = panel.querySelector("#nb-results");
    if (!list.length) {
      host.innerHTML = `<div class="nb-empty">
          <strong>Nothing found within ${RADIUS_M / 1000} km.</strong>
          <p>That does not mean there is nothing there. It means the community
             map has no record of it. Contact local emergency services or an
             official relief line.</p>
        </div>`;
      return;
    }
    const LIMIT = 60;
    const shown = list.slice(0, LIMIT);
    const rows = shown.map((p) => `
      <li class="nb-item">
        <div class="nb-kind">${esc(p.kind.label)}</div>
        <div class="nb-main">
          <div class="nb-name">${esc(p.name || "Unnamed " + p.kind.label.toLowerCase())}</div>
          ${p.address ? `<div class="nb-sub">${esc(p.address)}</div>` : ""}
          ${p.hours ? `<div class="nb-sub">Hours: ${esc(p.hours)}</div>` : ""}
          ${p.phone ? `<div class="nb-sub">☎ <a href="tel:${esc(p.phone)}">${esc(p.phone)}</a></div>` : ""}
        </div>
        <div class="nb-dist">
          <span class="nb-km">${esc(readable(p.distance))}</span>
          <span class="nb-dir">${esc(p.dir)}</span>
          <a class="nb-map" target="_blank" rel="noopener"
             href="https://www.openstreetmap.org/?mlat=${p.lat}&mlon=${p.lon}#map=17/${p.lat}/${p.lon}">Map</a>
        </div>
      </li>`).join("");
    host.innerHTML = `
      ${shown.some((p) => p.kind.rank === 0) ? "" : `<p class="nb-none-urgent">
         No refugee sites, shelters or assembly points are recorded here — only
         the everyday services below. In most places there are none because
         there is no emergency; in an emergency the map is often the last thing
         to be updated. Neither is evidence that none exist.</p>`}
      <p class="nb-origin">${list.length > shown.length
          ? "Nearest " + shown.length + " of " + list.length + " places"
          : shown.length + " place" + (shown.length === 1 ? "" : "s")}
         within ${RADIUS_M / 1000} km of ${esc(originLabel)}.</p>
      <ul class="nb-list">${rows}</ul>`;
  }

  function setStatus(msg, isError) {
    panel.querySelector("#nb-results").innerHTML =
      `<p class="${isError ? "nb-error" : "demo-loading"}">${esc(msg)}</p>`;
  }

  async function runAt(lat, lon, label) {
    setStatus("Searching the community map…", false);
    try {
      renderResults(await lookup(lat, lon), label);
    } catch (err) {
      setStatus("Could not search: " + err.message, true);
    }
  }

  // A failed location lookup must not be a dead end. Someone looking for a
  // shelter does not want to be told "unavailable" -- they want the next thing
  // to try, so each failure names its likely cause and leaves two working
  // alternatives in front of them.
  function locationFailed(message, hint) {
    panel.querySelector("#nb-results").innerHTML = `
      <div class="nb-empty">
        <strong class="nb-error-line">${esc(message)}</strong>
        ${hint ? `<p>${esc(hint)}</p>` : ""}
        <p>You can still search: type a town or area above, or use your
           approximate area, which needs no permission.</p>
        <button class="btn" id="nb-approx" type="button">Use my approximate area</button>
      </div>`;
    panel.querySelector("#nb-approx").addEventListener("click", useApproximateArea);
    const input = panel.querySelector("#nb-place");
    if (input) input.focus();
  }

  function useMyLocation() {
    if (!navigator.geolocation) {
      locationFailed("This browser cannot provide a location.", "");
      return;
    }
    setStatus("Waiting for your device to provide a location…", false);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        // Rounded to roughly 100 m before it is sent anywhere. A radius search
        // does not need better, and precise coordinates are not this service's
        // business.
        const lat = Number(pos.coords.latitude.toFixed(3));
        const lon = Number(pos.coords.longitude.toFixed(3));
        runAt(lat, lon, "your approximate location");
      },
      (err) => {
        if (err.code === err.PERMISSION_DENIED) {
          locationFailed("Location permission was declined.",
            "You can allow it from the padlock or location icon in the address bar.");
        } else if (err.code === err.TIMEOUT) {
          locationFailed("Your device took too long to find a location.",
            "This is common indoors or with a weak signal.");
        } else {
          // POSITION_UNAVAILABLE almost always means the browser asked the
          // operating system and the operating system refused -- most often
          // because location services are switched off system-wide, which no
          // amount of granting permission in the browser will fix.
          locationFailed("Your device would not provide a location.",
            "This usually means location services are turned off for the whole " +
            "system rather than just this page — on macOS, System Settings → " +
            "Privacy & Security → Location Services; on Windows, Settings → " +
            "Privacy → Location.");
        }
      },
      { enableHighAccuracy: false, timeout: 15000, maximumAge: 300000 });
  }

  // Approximate area, worked out from the network connection rather than the
  // device. It needs no permission and works where GPS is switched off, but it
  // is city-level at best and the address is visible to the service that
  // resolves it -- so it is offered explicitly and never used on its own
  // initiative.
  async function useApproximateArea() {
    setStatus("Working out your approximate area from your connection…", false);
    try {
      const res = await fetch("https://get.geojs.io/v1/ip/geo.json");
      if (!res.ok) throw new Error("the lookup service did not respond");
      const d = await res.json();
      const lat = Number(d.latitude), lon = Number(d.longitude);
      if (!isFinite(lat) || !isFinite(lon)) throw new Error("no area could be determined");
      const where = [d.city, d.country].filter(Boolean).join(", ") || "your area";
      await runAt(lat, lon, where + " (approximate, from your connection)");
    } catch (err) {
      setStatus("Could not determine an approximate area: " + err.message +
                ". Please search by place name.", true);
    }
  }

  async function searchPlace() {
    const q = panel.querySelector("#nb-place").value.trim();
    if (!q) return;
    setStatus("Looking up “" + q + "”…", false);
    try {
      const hit = await geocode(q);
      await runAt(hit.lat, hit.lon, hit.label.split(",").slice(0, 2).join(","));
    } catch (err) {
      setStatus("Could not search: " + err.message, true);
    }
  }

  function open() {
    if (panel) return;
    panel = document.createElement("div");
    panel.className = "demo-overlay nb-overlay";
    panel.innerHTML = `
      <div class="demo-panel nb-panel">
        <div class="demo-head">
          <span class="demo-eyebrow">Find help near you</span>
          <button class="demo-close" data-nb-close aria-label="Close">×</button>
        </div>

        <div class="nb-warning">
          <strong>This is not an emergency service.</strong>
          If you are in immediate danger, contact your local emergency number.
          The places below come from OpenStreetMap, a public map anyone can
          edit. They are <strong>not verified</strong>, may be out of date, and
          are listed as whatever the map records them to be — a pharmacy is a
          pharmacy, not a relief centre. Always confirm before travelling.
        </div>

        <p class="nb-privacy">
          Your location is used to search and nothing else. It is rounded to
          about 100&nbsp;m, sent only to OpenStreetMap's public search service,
          and never stored or sent to us — this page has no server. If you would
          rather not share it, search by place name instead.
        </p>

        <div class="nb-actions">
          <button class="btn btn-primary" id="nb-locate" type="button">Use my location</button>
          <span class="nb-or">or</span>
          <input id="nb-place" type="search" placeholder="town, city or area…"
                 aria-label="Search by place name">
          <button class="btn" id="nb-search" type="button">Search</button>
        </div>
        <p class="nb-alt">
          No location permission? <button class="nb-link" id="nb-approx-top"
          type="button">Use my approximate area</button> — worked out from your
          network connection, city-level accuracy, and your network address is
          visible to the service that resolves it.
        </p>

        <div id="nb-results"></div>

        <p class="nb-credit">
          Place data ©&nbsp;<a href="https://www.openstreetmap.org/copyright"
          target="_blank" rel="noopener">OpenStreetMap contributors</a>,
          available under the Open Database Licence, retrieved live through the
          public Overpass API; place-name search by Nominatim. Unverified
          community data. This page holds no records and stores nothing.
        </p>
      </div>`;
    panel.addEventListener("click", (e) => {
      if (e.target === panel || e.target.closest("[data-nb-close]")) close();
    });
    panel.querySelector("#nb-locate").addEventListener("click", useMyLocation);
    panel.querySelector("#nb-search").addEventListener("click", searchPlace);
    panel.querySelector("#nb-approx-top").addEventListener("click", useApproximateArea);
    panel.querySelector("#nb-place").addEventListener("keydown", (e) => {
      if (e.key === "Enter") searchPlace();
    });
    document.body.appendChild(panel);
    document.body.classList.add("demo-open");
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && panel) close();
  });

  const btn = document.getElementById("find-help");
  if (btn) btn.addEventListener("click", open);

  const wanted = new URLSearchParams(location.search).get("help");
  if (wanted) {
    window.addEventListener("load", () => {
      open();
      if (wanted !== "1") {
        panel.querySelector("#nb-place").value = wanted;
        searchPlace();
      }
    });
  }
})();
