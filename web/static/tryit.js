/*
 * "Score your own records."
 *
 * The deployed site is static, which would normally mean a visitor can only
 * replay answers computed in advance -- and a demo nobody can poke at is a
 * demo nobody believes. This panel runs the real scorer in the browser
 * (engine.js, generated from the same code the n8n pipeline runs) so anything
 * typed here is scored for real, by the same rules, with nothing sent anywhere.
 *
 * Faces are chosen from the synthetic corpus rather than uploaded. That is not
 * a shortcut: a project whose entire premise is that facial similarity must
 * never establish identity has no business inviting strangers to submit real
 * photographs of real people to it.
 */
(function () {
  "use strict";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const LABELS = {
    face: "Facial similarity", name: "Name", family: "Family members",
    birth_date: "Date of birth", origin_place: "Place of origin",
    last_seen_place: "Last seen", nationality: "Nationality",
  };
  const statusOf = (s) => !s.available ? "unavailable"
    : s.score >= 85 ? "strong" : s.score >= 65 ? "partial" : "weak";

  // Two records of one fictional person, recorded by two desks that never
  // spoke: a transliterated name, a vaguer date, an alternate place spelling.
  const PRESET = {
    a: { given_name: "Mohammed", family_name: "Darwazi", sex: "M",
         birth_date_raw: "1994-02-08", nationality: "Karavian",
         origin_place: "Qasr Nadeem", last_seen_place: "Camp Thirteen",
         fam_name: "Fatima Darwazi", fam_rel: "mother", face: "A-0001" },
    b: { given_name: "Muhammad", family_name: "Derwazi", sex: "M",
         birth_date_raw: "approx. 1994", nationality: "Karavien",
         origin_place: "Kasr Nadim", last_seen_place: "Camp 13",
         fam_name: "Fatimah Derwazi", fam_rel: "parent", face: "B-0001" },
  };

  const RELATIONS = ["", "mother", "father", "parent", "guardian", "son",
    "daughter", "child", "brother", "sister", "sibling", "spouse", "wife",
    "husband", "aunt", "uncle", "cousin", "grandparent"];

  let panel = null;
  let embeddings = null;
  let faceIds = null;
  let calibration = null;

  const BASE = (document.currentScript && document.currentScript.src || "")
    .replace(/\/static\/tryit\.js.*$/, "");

  function close() {
    if (panel) panel.remove();
    panel = null;
    document.body.classList.remove("demo-open");
  }

  async function loadFaceData() {
    if (embeddings) return;
    const res = await fetch(BASE + "/api/embeddings.json");
    if (!res.ok) throw new Error("face data unavailable (" + res.status + ")");
    const payload = await res.json();
    embeddings = payload.embeddings;
    calibration = payload.calibration;
    faceIds = Object.keys(embeddings).sort();
  }

  function field(side, key, label, placeholder) {
    return `<label class="ti-field">
        <span>${esc(label)}</span>
        <input type="text" data-side="${side}" data-key="${esc(key)}"
               value="${esc(PRESET[side][key] || "")}"
               placeholder="${esc(placeholder || "")}">
      </label>`;
  }

  function relationSelect(side) {
    const opts = RELATIONS.map((r) =>
      `<option value="${esc(r)}"${r === PRESET[side].fam_rel ? " selected" : ""}>${
        esc(r || "— none —")}</option>`).join("");
    return `<label class="ti-field">
        <span>Relationship</span>
        <select data-side="${side}" data-key="fam_rel">${opts}</select>
      </label>`;
  }

  function column(side, title) {
    return `<div class="ti-col">
        <h4>${esc(title)}</h4>
        <div class="ti-face" data-face-slot="${side}">
          <img src="${BASE}/faces/${esc(PRESET[side].face)}.png" alt="Selected synthetic face">
          <button type="button" class="btn ti-pick" data-pick="${side}">Change face</button>
          <span class="ti-faceid" data-faceid="${side}">${esc(PRESET[side].face)}</span>
        </div>
        ${field(side, "given_name", "Given name")}
        ${field(side, "family_name", "Family name")}
        ${field(side, "sex", "Sex", "M / F")}
        ${field(side, "birth_date_raw", "Date of birth", "1994-02-08 or approx. 1994")}
        ${field(side, "nationality", "Nationality")}
        ${field(side, "origin_place", "Place of origin")}
        ${field(side, "last_seen_place", "Last seen")}
        ${field(side, "fam_name", "Family member")}
        ${relationSelect(side)}
      </div>`;
  }

  function collect(side) {
    const get = (key) => {
      const el = panel.querySelector(`[data-side="${side}"][data-key="${key}"]`);
      return el ? el.value.trim() : "";
    };
    const famName = get("fam_name");
    const famRel = get("fam_rel");
    return {
      record_id: side === "a" ? "YOUR-A" : "YOUR-B",
      given_name: get("given_name"),
      family_name: get("family_name"),
      sex: get("sex"),
      birth_date_raw: get("birth_date_raw"),
      nationality: get("nationality"),
      origin_place: get("origin_place"),
      last_seen_place: get("last_seen_place"),
      family_members: famName ? [{ name: famName, relation: famRel }] : [],
    };
  }

  const selectedFace = (side) =>
    panel.querySelector(`[data-faceid="${side}"]`).textContent.trim();

  function renderResult(result, faceA, faceB) {
    const host = panel.querySelector("#ti-result");
    const band = result.band;
    const face = result.signals.find((s) => s.field === "face");
    const rows = result.signals.map((s) => {
      const st = statusOf(s);
      return `<div class="ev-row ${st === "unavailable" ? "unavailable" : ""}">
          <div class="ev-label">${esc(LABELS[s.field] || s.field)}
            <span class="ev-weight">${s.available ? "weight " + s.weight : "excluded"}</span>
          </div>
          <div><div class="ev-detail">${esc(s.detail || "")}</div></div>
          <div class="ev-score">${st === "unavailable"
            ? `<div class="ev-score-value s-unavailable">—</div>`
            : `<div class="ev-score-value s-${st}">${Math.round(s.score)}%</div>
               <div class="ev-score-bar"><div class="ev-score-fill f-${st}"
                    style="width:${s.score}%"></div></div>`}</div>
        </div>`;
    }).join("");

    const flags = result.flags.length
      ? `<div class="cand-flags">${result.flags.map(
          (f) => `<div class="flag">⚠ ${esc(f)}</div>`).join("")}</div>` : "";

    host.innerHTML = `
      <div class="candidate band-${esc(band)}">
        <div class="cand-head">
          <div class="cand-identity">
            <div class="cand-name">Your two records</div>
            <div class="cand-meta">scored in your browser · nothing was uploaded</div>
          </div>
          <div class="confidence">
            <div class="conf-row">
              <span class="conf-value">${result.potential_match_score.toFixed(0)}</span>
              <span class="conf-unit">/ 100 potential match</span>
            </div>
            <div class="conf-bar"><div class="conf-fill fill-${esc(band)}"
                 style="width:${result.potential_match_score}%"></div></div>
            <span class="band-pill pill-${esc(band)}">${esc({
              strong: "Strong candidate", possible: "Possible match",
              weak: "Weak signal", unlikely: "Unlikely" }[band] || band)}</span>
          </div>
        </div>
        ${face && face.available ? `<div class="face-pair">
          <figure class="face-fig"><img src="${BASE}/faces/${esc(faceA)}.png" alt="Synthetic face A">
            <figcaption>Record A · ${esc(faceA)}<span>synthetic image</span></figcaption></figure>
          <div class="face-verdict">
            <div class="face-pct s-${statusOf(face)}">${Math.round(face.score)}%</div>
            <div class="face-cap">Facial similarity</div>
            <div class="face-warn">Facial similarity is an indicator,<br>not proof of identity.</div>
          </div>
          <figure class="face-fig"><img src="${BASE}/faces/${esc(faceB)}.png" alt="Synthetic face B">
            <figcaption>Record B · ${esc(faceB)}<span>synthetic image</span></figcaption></figure>
        </div>` : ""}
        ${flags}
        <div class="evidence">${rows}</div>
        <div class="cand-actions">
          <span class="action-note">Evidence coverage
            ${Math.round(result.evidence_coverage * 100)}% of available fields ·
            ${esc(result.review_notice)}</span>
        </div>
      </div>`;
    host.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function score() {
    const btn = panel.querySelector("#ti-score");
    btn.disabled = true;
    btn.textContent = "Scoring…";
    try {
      await loadFaceData();
      const fa = selectedFace("a"), fb = selectedFace("b");
      const result = window.ReunifyEngine.scorePair(collect("a"), collect("b"), {
        embeddingA: embeddings[fa], embeddingB: embeddings[fb],
        calibration: calibration,
      });
      renderResult(result, fa, fb);
    } catch (err) {
      panel.querySelector("#ti-result").innerHTML =
        `<p class="demo-loading">Could not score: ${esc(err.message)}</p>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Score these records";
    }
  }

  async function pickFace(side) {
    await loadFaceData();
    const grid = faceIds.map((id) =>
      `<button type="button" class="ti-face-opt" data-id="${esc(id)}">
         <img loading="lazy" src="${BASE}/faces/${esc(id)}.png" alt="">
         <span>${esc(id)}</span>
       </button>`).join("");
    const picker = document.createElement("div");
    picker.className = "ti-picker";
    picker.innerHTML = `<div class="ti-picker-inner">
        <div class="demo-head">
          <span class="demo-eyebrow">Choose a synthetic face — record ${side.toUpperCase()}</span>
          <button class="demo-close" data-close-picker aria-label="Close">×</button>
        </div>
        <p class="ti-note">All 160 faces are drawn by the generator from a seeded
          number. Two records of the same fictional person are photographed
          independently, so A-0001 and B-0001 are the same face under different
          conditions — pick that pair to see a true match.</p>
        <div class="ti-face-grid">${grid}</div>
      </div>`;
    picker.addEventListener("click", (e) => {
      if (e.target === picker || e.target.closest("[data-close-picker]")) picker.remove();
      const opt = e.target.closest(".ti-face-opt");
      if (opt) {
        const id = opt.dataset.id;
        panel.querySelector(`[data-faceid="${side}"]`).textContent = id;
        panel.querySelector(`[data-face-slot="${side}"] img`).src =
          BASE + "/faces/" + id + ".png";
        picker.remove();
      }
    });
    panel.appendChild(picker);
  }

  function open() {
    if (panel) return;
    panel = document.createElement("div");
    panel.className = "demo-overlay ti-overlay";
    panel.innerHTML = `
      <div class="demo-panel ti-panel">
        <div class="demo-head">
          <span class="demo-eyebrow">Score your own records</span>
          <button class="demo-close" data-ti-close aria-label="Close">×</button>
        </div>
        <p class="ti-note">Edit either record and score them. This runs the real
          matcher in your browser — the same rules the pipeline uses — so nothing
          you type is sent anywhere. Faces are chosen from the synthetic corpus;
          there is deliberately no way to upload a photograph.</p>
        <div class="ti-cols">
          ${column("a", "Record A · Registry A")}
          ${column("b", "Record B · Registry B")}
        </div>
        <div class="ti-actions">
          <button class="btn btn-primary" id="ti-score" type="button">Score these records</button>
          <button class="btn" data-ti-close type="button">Close</button>
        </div>
        <div id="ti-result"></div>
      </div>`;
    panel.addEventListener("click", (e) => {
      if (e.target === panel || e.target.closest("[data-ti-close]")) close();
      const pick = e.target.closest("[data-pick]");
      if (pick) pickFace(pick.dataset.pick);
    });
    panel.querySelector("#ti-score").addEventListener("click", score);
    document.body.appendChild(panel);
    document.body.classList.add("demo-open");
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && panel) close();
  });

  const btn = document.getElementById("try-it");
  if (btn) btn.addEventListener("click", open);

  // ?try=1 opens the panel on load; ?try=demo also scores the worked example
  // straight away. Presenting from a bookmark is one less thing to get wrong
  // in front of an audience.
  const wanted = new URLSearchParams(location.search).get("try");
  if (wanted) {
    window.addEventListener("load", function () {
      open();
      if (wanted === "demo") setTimeout(score, 150);
    });
  }
})();
