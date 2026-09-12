/*
 * The scripted walk-through.
 *
 * Everything revealed at the end is computed by the engine: the overlay asks
 * /api/demo, which scores a planted pair through exactly the same code path as
 * any other match. The only scripted things are which pair is chosen and the
 * pacing of the reveal. Nothing here fabricates a number, which matters --
 * a demo that hardcodes its own result is a slide, not a system.
 */
(function () {
  "use strict";

  const button = document.getElementById("run-demo");
  if (!button) return;

  /* The overlay is built on demand and removed again on close, rather than
     sitting in the page waiting to be un-hidden.
     It covers the viewport, so while it exists it swallows every click. Hiding
     it with the `hidden` attribute alone is not enough: an author `display`
     rule beats the user agent's `[hidden] { display: none }`, and if the
     stylesheet that fixes that is ever stale in someone's cache, the whole
     interface silently becomes unclickable behind an invisible sheet. An
     element that does not exist cannot do that. */
  let overlay = null;
  let stage = null;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  // Honour a reduced-motion preference by collapsing the pacing rather than
  // removing steps: the sequence is the explanation, so it still has to run.
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const beat = (ms) => wait(reduced ? Math.min(ms, 120) : ms);

  let running = false;

  const SIGNAL_ORDER = ["face", "name", "family", "birth_date",
                        "origin_place", "last_seen_place", "nationality"];

  function mount() {
    if (overlay) return;
    overlay = document.createElement("div");
    overlay.className = "demo-overlay";
    stage = document.createElement("div");
    stage.className = "demo-stage";
    overlay.appendChild(stage);
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay || e.target.closest("[data-demo-close]")) close();
    });
    document.body.appendChild(overlay);
    document.body.classList.add("demo-open");
  }

  function close() {
    if (overlay) overlay.remove();
    overlay = null;
    stage = null;
    running = false;
    document.body.classList.remove("demo-open");
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && overlay) close();
  });

  async function runStages(stages) {
    stage.innerHTML = `
      <div class="demo-panel">
        <div class="demo-head">
          <span class="demo-eyebrow">Cross-registry comparison</span>
          <button class="demo-close" data-demo-close aria-label="Close demo">×</button>
        </div>
        <ol class="demo-steps" id="demo-steps">
          ${stages.map((s) => `<li data-key="${esc(s.key)}">
              <span class="step-mark" aria-hidden="true"></span>
              <span class="step-text">${esc(s.label)}</span>
            </li>`).join("")}
        </ol>
      </div>`;
    const items = stage.querySelectorAll(".demo-steps li");
    for (const li of items) {
      li.classList.add("active");
      await beat(620);
      li.classList.remove("active");
      li.classList.add("done");
      await beat(110);
    }
  }

  function scoreRow(sig) {
    const unavailable = sig.status === "unavailable";
    return `<div class="dscore-row" data-field="${esc(sig.field)}">
        <span class="dscore-label">${esc(sig.label)}</span>
        <span class="dscore-track">
          <span class="dscore-fill s-${esc(sig.status)}" style="width:0%"></span>
        </span>
        <span class="dscore-val">${unavailable ? "—" : sig.score + "%"}</span>
      </div>`;
  }

  async function reveal(data) {
    const r = data.result;
    const a = data.a_record;
    const b = data.b_record;
    const byField = {};
    r.signals.forEach((s) => { byField[s.field] = s; });
    const ordered = SIGNAL_ORDER.map((f) => byField[f]).filter(Boolean);
    const face = byField.face;

    stage.innerHTML = `
      <div class="demo-panel demo-reveal">
        <div class="demo-head">
          <span class="demo-eyebrow">Potential match detected</span>
          <button class="demo-close" data-demo-close aria-label="Close demo">×</button>
        </div>

        <div class="demo-faces">
          <figure>
            <img src="/${esc(a.face_image)}" alt="Synthetic face, Registry A">
            <figcaption><strong>${esc(a.given_name)} ${esc(a.family_name)}</strong>
              <span>Registry A · ${esc(a.record_id)} · synthetic image</span></figcaption>
          </figure>
          <div class="demo-facescore">
            <div class="demo-facepct" id="demo-facepct">0%</div>
            <div class="demo-facecap">Facial similarity</div>
          </div>
          <figure>
            <img src="/${esc(b.face_image)}" alt="Synthetic face, Registry B">
            <figcaption><strong>${esc(b.given_name)} ${esc(b.family_name)}</strong>
              <span>Registry B · ${esc(b.record_id)} · synthetic image</span></figcaption>
          </figure>
        </div>

        <p class="demo-facewarn">${esc(data.face_notice)}</p>

        <div class="dscores">${ordered.map(scoreRow).join("")}</div>

        <div class="demo-total">
          <div class="demo-total-num" id="demo-total">0</div>
          <div class="demo-total-cap">
            <strong>Potential match score</strong>
            <span>How strongly these records should be prioritised for review —
                  not a confirmed identity.</span>
          </div>
        </div>

        <div class="demo-verify">${esc(data.review_notice)}</div>

        <div class="demo-foot">
          <span>Compared on ${ordered.filter((s) => s.weight > 0).length} independent signals ·
                evidence coverage ${Math.round(r.evidence_coverage * 100)}%</span>
          <button class="btn btn-primary" data-demo-close>Open the review interface</button>
        </div>
      </div>`;

    // Count the face percentage up first: it is the thing the audience is
    // looking at, and it has to land before the caveat under it means anything.
    await beat(320);
    if (face) await countTo(document.getElementById("demo-facepct"),
                            face.score, 900, (v) => v + "%");

    await beat(220);
    const rows = stage.querySelectorAll(".dscore-row");
    for (let i = 0; i < rows.length; i++) {
      const sig = ordered[i];
      rows[i].classList.add("in");
      const fill = rows[i].querySelector(".dscore-fill");
      if (sig.status !== "unavailable") fill.style.width = sig.score + "%";
      await beat(190);
    }

    await beat(300);
    await countTo(document.getElementById("demo-total"),
                  r.potential_match_score, 1100, (v) => v);
    stage.querySelector(".demo-verify").classList.add("in");
  }

  /* Driven by timers rather than requestAnimationFrame.
     rAF stops entirely in a background tab, and because the reveal sequence
     awaits this promise, an rAF-driven counter leaves the whole walk-through
     frozen at zero if the presenter switches away mid-run and comes back.
     setTimeout is throttled in the background but still fires, so the sequence
     always completes. */
  function countTo(node, target, ms, fmt) {
    if (!node) return Promise.resolve();
    const land = () => { node.textContent = fmt(Math.round(target)); };
    if (reduced) { land(); return Promise.resolve(); }
    return new Promise((resolve) => {
      const t0 = Date.now();
      (function tick() {
        const p = Math.min(1, (Date.now() - t0) / ms);
        // Ease out, so the number decelerates into place rather than stopping dead.
        node.textContent = fmt(Math.round(target * (1 - Math.pow(1 - p, 3))));
        if (p < 1) setTimeout(tick, 16);
        else { land(); resolve(); }
      })();
    });
  }

  async function start() {
    if (running) return;
    running = true;
    mount();
    stage.innerHTML = `<div class="demo-panel"><p class="demo-loading">Loading…</p></div>`;
    try {
      const res = await fetch("/api/demo");
      if (!res.ok) throw new Error(`demo unavailable (${res.status})`);
      const data = await res.json();
      await runStages(data.stages);
      await reveal(data);
    } catch (err) {
      stage.innerHTML = `<div class="demo-panel">
          <div class="demo-head"><span class="demo-eyebrow">Demo unavailable</span>
            <button class="demo-close" data-demo-close aria-label="Close">×</button></div>
          <p class="demo-loading">${esc(err.message)}</p>
        </div>`;
    } finally {
      running = false;
    }
  }

  button.addEventListener("click", start);

  // ?demo=1 starts the walk-through on load. Presenting from a bookmark is one
  // less thing to get wrong in front of an audience.
  if (new URLSearchParams(location.search).has("demo")) {
    window.addEventListener("load", () => setTimeout(start, 250));
  }
})();
