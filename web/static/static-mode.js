/*
 * Static mode.
 *
 * The matcher is deterministic over a fixed corpus, so every answer the API
 * would ever give can be computed once at build time and shipped as JSON.
 * scripts/build_static.py does that; this file makes the existing application
 * read it without knowing anything has changed.
 *
 * It intercepts fetch rather than editing app.js, so the same source runs
 * against the real FastAPI service locally and against a static host in
 * production. Two copies of the frontend that drift apart would be a worse
 * problem than the one this solves.
 *
 * The one thing genuinely lost is the reviewer's triage log, which the server
 * kept in memory. Here it lives in localStorage: per-browser, and gone if the
 * visitor clears their data. That is honest for a prototype whose server-side
 * log was also discarded on restart, and it is stated in the interface.
 */
(function () {
  "use strict";

  const BASE = document.currentScript.src.replace(/\/static\/static-mode\.js.*$/, "");
  const REVIEW_KEY = "reunifyai.reviews.v1";

  const readReviews = () => {
    try { return JSON.parse(localStorage.getItem(REVIEW_KEY) || "{}"); }
    catch (e) { return {}; }
  };
  const writeReviews = (r) => {
    try { localStorage.setItem(REVIEW_KEY, JSON.stringify(r)); } catch (e) { /* private mode */ }
  };

  const json = (body, status) => new Response(JSON.stringify(body), {
    status: status || 200, headers: { "Content-Type": "application/json" },
  });

  // Captured before the override below. The precomputed files live under
  // /api/ too, so loading one through the patched fetch would match the
  // interception rule and recurse until the stack gives out.
  const realFetch = window.fetch.bind(window);

  const loadJson = (path) => realFetch(BASE + path).then((r) => {
    if (!r.ok) throw new Error(path + " -> " + r.status);
    return r.json();
  });

  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (!url.startsWith("/api/")) return realFetch(input, init);

    const [path, query] = url.split("?");
    const params = new URLSearchParams(query || "");
    const method = ((init && init.method) || "GET").toUpperCase();

    // --- reviewer triage, kept in the browser -----------------------------
    if (path === "/api/review" && method === "POST") {
      const body = JSON.parse((init && init.body) || "{}");
      const entry = Object.assign({}, body, {
        recorded_at: new Date().toISOString(),
        status: body.decision === "refer"
          ? "referred for human verification" : "ruled out by reviewer",
      });
      const all = readReviews();
      all[body.a_record_id] = entry;
      writeReviews(all);
      return Promise.resolve(json(entry));
    }
    if (path.startsWith("/api/review/") && method === "DELETE") {
      const id = path.slice("/api/review/".length);
      const all = readReviews();
      delete all[id];
      writeReviews(all);
      return Promise.resolve(json({ cleared: id }));
    }
    if (path === "/api/review") {
      const all = readReviews();
      return Promise.resolve(json({ count: Object.keys(all).length,
                                    decisions: Object.values(all) }));
    }

    // --- everything else is precomputed -----------------------------------
    if (path === "/api/meta") return loadJson("/api/meta.json").then(json);
    if (path === "/api/showcase") return loadJson("/api/showcase.json").then(json);
    if (path === "/api/queue") {
      return loadJson("/api/queue.json").then((q) => {
        const reviews = readReviews();
        // A record the reviewer has already dealt with drops out of the queue:
        // a worklist that keeps showing you what you have finished is not a
        // worklist.
        const pending = q.queue.filter((r) => !reviews[r.a_record_id]);
        const limit = Number(params.get("limit") || 12);
        return json({ queue: pending.slice(0, limit),
                      waiting: pending.length,
                      reviewed: q.queue.length - pending.length,
                      notice: q.notice });
      });
    }
    if (path === "/api/demo") return loadJson("/api/demo.json").then(json);

    if (path === "/api/records") {
      const needle = (params.get("q") || "").trim().toLowerCase();
      const limit = Number(params.get("limit") || 60);
      return loadJson("/api/records.json").then((all) => {
        const reviews = readReviews();
        const matched = all.records.filter((r) => !needle ||
          [r.record_id, r.given_name, r.family_name, r.nationality,
           r.origin_place, r.last_seen_place, r.source_org]
            .join(" ").toLowerCase().includes(needle));
        const out = matched.slice(0, limit).map((r) => Object.assign({}, r, {
          reviewed: (reviews[r.record_id] || {}).decision || null,
        }));
        return json({ count: out.length, total: matched.length,
                      truncated: matched.length > out.length, records: out });
      });
    }

    if (path.startsWith("/api/match/")) {
      const id = path.slice("/api/match/".length);
      const reveal = params.get("reveal") === "true";
      return loadJson("/api/match/" + id + ".json").then((payload) => {
        const out = Object.assign({}, payload);
        // Ground truth ships with every record so the toggle works offline,
        // but it is withheld unless asked for, exactly as the API withheld it.
        if (!reveal) delete out.ground_truth;
        out.decision = readReviews()[id] || null;
        return json(out);
      });
    }

    return Promise.resolve(json({ error: "not available in static mode" }, 404));
  };
})();
