/* Reunification Engine — review interface.
   Plain ES modules-free JS; no build step, so the demo machine needs nothing. */

const el = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { records: [], selected: null, reveal: false, lastPayload: null };

/* ------------------------------------------------------------ helpers */

const absent = (v) => !v || !String(v).trim();
const value = (v, fallback = "not recorded") =>
  absent(v) ? `<span class="val absent">${fallback}</span>` : `<span class="val">${esc(v)}</span>`;

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

/* Pull the parts of a detail sentence worth emphasising, so the reviewer's eye
   lands on the reason rather than on the boilerplate around it. */
function emphasise(detail) {
  return esc(detail)
    .replace(/(\b\w+ codes match \([A-Z0-9]+\))/g, '<span class="hl">$1</span>')
    .replace(/(known alias in place-name gazetteer)/g, '<span class="hl">$1</span>')
    .replace(/(Tolerance windows overlap|Exact dates agree)/g, '<span class="hl">$1</span>')
    .replace(/(Name order appears swapped between registries)/g, '<span class="hl">$1</span>');
}

/* Which technique carried the match, badged from the engine's own `method`
   field rather than by reading its prose — "not in the gazetteer" and "known
   alias" must never be confused for one another. */
const METHOD_TAGS = {
  "gazetteer-alias":  ["Known alias", "tag-alias"],
  "phonetic":         ["Phonetic match", "tag-phonetic"],
  "name-order-swap":  ["Fields swapped", "tag-phonetic"],
  "tolerance-window": ["Tolerance window", "tag-window"],
};

/* ------------------------------------------------------------- masthead */

async function loadMeta() {
  const m = await api("/api/meta");
  el("stats").innerHTML = [
    [m.registry_a_count, "Registry A"],
    [m.registry_b_count, "Registry B"],
    [m.comparisons.toLocaleString(), "Comparisons"],
  ].map(([v, l]) => `<div class="stat">
        <span class="stat-value">${esc(v)}</span>
        <span class="stat-label">${esc(l)}</span>
      </div>`).join("");
}

/* -------------------------------------------------------- record list */

async function loadRecords(q = "") {
  const data = await api(`/api/records?q=${encodeURIComponent(q)}`);
  state.records = data.records;
  el("registry-count").textContent = data.truncated
    ? `showing ${data.count} of ${data.total}`
    : `${data.total} record${data.total === 1 ? "" : "s"}`;

  const list = el("record-list");
  if (!data.records.length) {
    list.innerHTML = `<li class="list-empty">No records match that search.</li>`;
    return;
  }

  list.innerHTML = data.records.map((r) => {
    const dob = absent(r.birth_date_raw) ? "DOB not recorded" : r.birth_date_raw;
    const place = absent(r.origin_place) ? "origin not recorded" : r.origin_place;
    const dot = r.reviewed ? `<span class="dot ${esc(r.reviewed)}"
        title="${r.reviewed === "refer" ? "Referred for verification" : "Ruled out"}"></span>` : "";
    return `<li class="record-item${state.selected === r.record_id ? " active" : ""}"
                data-id="${esc(r.record_id)}" tabindex="0" role="button">
        <div class="record-name">${dot}${esc(r.given_name)} ${esc(r.family_name)}</div>
        <div class="record-meta">
          <span class="record-id">${esc(r.record_id)}</span> · ${esc(dob)} · ${esc(place)}
        </div>
      </li>`;
  }).join("");
}

/* --------------------------------------------------------- subject card */

function renderSubjectFace(rec) {
  if (!rec.face_image) return "";
  return `<img class="subject-face" src="/${esc(rec.face_image)}"
               alt="Synthetic face for record ${esc(rec.record_id)}" loading="lazy">`;
}

const familyText = (rec) => (rec.family_members || [])
  .map((m) => `${m.name} (${m.relation})`).join(", ");

function renderSubject(rec) {
  el("subject-name").textContent = `${rec.given_name} ${rec.family_name}`;
  el("subject-photo").innerHTML = renderSubjectFace(rec);
  el("subject-fields").innerHTML = [
    ["Record ID", rec.record_id],
    ["Sex", rec.sex, "not recorded"],
    ["Date of birth", rec.birth_date_raw, "not recorded"],
    ["Nationality", rec.nationality, "not recorded"],
    ["Place of origin", rec.origin_place, "not recorded"],
    ["Last seen", rec.last_seen_place, "not recorded"],
    ["Family named", familyText(rec), "none recorded"],
    ["Recorded by", rec.source_org],
  ].map(([label, v, fb]) => `<div>
        <dt>${esc(label)}</dt>
        <dd class="${absent(v) ? "absent" : ""}">${absent(v) ? esc(fb || "—") : esc(v)}</dd>
      </div>`).join("");
}

/* ---------------------------------------------------------- evidence */

function renderSignal(sig) {
  let tag = "";
  if (sig.status === "conflict") {
    tag = `<span class="ev-tag tag-conflict">Conflict</span>`;
  } else if (METHOD_TAGS[sig.method]) {
    const [text, cls] = METHOD_TAGS[sig.method];
    tag = `<span class="ev-tag ${cls}">${esc(text)}</span>`;
  }

  const scoreCell = sig.status === "unavailable"
    ? `<div class="ev-score-value s-unavailable">—</div>`
    : `<div class="ev-score-value s-${esc(sig.status)}">${sig.score}%</div>
       <div class="ev-score-bar">
         <div class="ev-score-fill f-${esc(sig.status)}" style="width:${sig.score}%"></div>
       </div>`;

  // Weight is shown because it is the honest part of the model: it says how
  // much this field was allowed to count, not just how well it matched.
  const weightLine = sig.status === "unavailable"
    ? `<span class="ev-weight">excluded</span>`
    : `<span class="ev-weight">weight ${sig.weight.toFixed(2)}</span>`;

  return `<div class="ev-row ${sig.status === "unavailable" ? "unavailable" : ""}">
      <div class="ev-label">${esc(sig.label)}${weightLine}</div>
      <div>
        <div class="ev-values">
          ${value(sig.a_value, "not recorded")}
          <span class="approx">≈</span>
          ${value(sig.b_value, "not recorded")}
          ${tag}
        </div>
        <div class="ev-detail">${emphasise(sig.detail)}</div>
      </div>
      <div class="ev-score">${scoreCell}</div>
    </div>`;
}

/* The two synthetic photographs, side by side, with the caveat attached to the
   number rather than parked at the bottom of the page. A reviewer who reads
   only the big percentage must still read what it does not mean. */
function renderFacePair(cand, aRecord) {
  if (cand.face_similarity === null || cand.face_similarity === undefined) return "";
  const b = cand.b_record;
  const sim = cand.face_similarity;
  const band = sim >= 85 ? "strong" : sim >= 65 ? "partial" : "weak";
  const capped = cand.face_only
    ? `<div class="face-capped">Face is the only available signal — score capped
       pending corroborating evidence</div>` : "";
  return `<div class="face-pair">
      <figure class="face-fig">
        <img src="/${esc(aRecord.face_image)}" alt="Synthetic face, Registry A record ${esc(aRecord.record_id)}" loading="lazy">
        <figcaption>Registry A · ${esc(aRecord.record_id)}<span>synthetic image</span></figcaption>
      </figure>
      <div class="face-verdict">
        <div class="face-pct s-${band}">${sim.toFixed(0)}%</div>
        <div class="face-cap">Facial similarity</div>
        <div class="face-warn">Facial similarity is an indicator,<br>not proof of identity.</div>
        ${capped}
      </div>
      <figure class="face-fig">
        <img src="/${esc(b.face_image)}" alt="Synthetic face, Registry B record ${esc(b.record_id)}" loading="lazy">
        <figcaption>Registry B · ${esc(b.record_id)}<span>synthetic image</span></figcaption>
      </figure>
    </div>`;
}

function renderSurfaced(cand) {
  if (!cand.surfaced_because || !cand.surfaced_because.length) return "";
  const items = cand.surfaced_because
    .map((r) => `<li><span class="tick">✓</span>${esc(r.label)}
                 <span class="surf-score">${r.score}%</span></li>`).join("");
  return `<div class="surfaced">
      <h4>Why was this case surfaced?</h4>
      <ul>${items}</ul>
    </div>`;
}

function renderFlags(cand) {
  if (!cand.flags || !cand.flags.length) return "";
  return `<div class="cand-flags">${cand.flags.map(
    (f) => `<div class="flag">⚠ ${esc(f)}</div>`).join("")}</div>`;
}

function renderCandidate(cand, index, truthId, aRecord) {
  const b = cand.b_record;
  const isTruth = truthId && b.record_id === truthId;
  const conf = cand.potential_match_score;
  const bandLabel = {
    strong: "Strong candidate", possible: "Possible match",
    weak: "Weak signal", unlikely: "Unlikely",
  }[cand.band] || cand.band;

  const cross = cand.cross_notes.length
    ? `<div class="cross-notes">${cand.cross_notes.map(
        (n) => `<div><span>↳</span>${esc(n)}</div>`).join("")}</div>`
    : "";

  return `<article class="candidate band-${esc(cand.band)}${isTruth ? " is-truth" : ""}"
                    data-b-id="${esc(b.record_id)}">
      <div class="cand-head">
        <div class="rank">${index + 1}</div>
        <div class="cand-identity">
          <div class="cand-name">${esc(b.given_name)} ${esc(b.family_name)}</div>
          <div class="cand-meta">${esc(b.record_id)} · ${esc(b.source_org)}</div>
        </div>
        <div class="confidence">
          <div class="conf-row">
            <span class="conf-value">${conf.toFixed(0)}</span>
            <span class="conf-unit">/ 100 potential match</span>
          </div>
          <div class="conf-bar">
            <div class="conf-fill fill-${esc(cand.band)}" style="width:${conf}%"></div>
          </div>
          <span class="band-pill pill-${esc(cand.band)}">${esc(bandLabel)}</span>
        </div>
      </div>

      ${renderFacePair(cand, aRecord)}
      ${renderFlags(cand)}
      <div class="evidence">${cand.signals.map(renderSignal).join("")}</div>
      ${cross}
      ${renderSurfaced(cand)}

      <div class="cand-actions">
        <button class="btn btn-primary" data-action="refer">Refer for human verification</button>
        <button class="btn" data-action="rule_out">Rule out</button>
        <span class="action-note">
          Evidence coverage ${Math.round(cand.evidence_coverage * 100)}% of available fields
        </span>
      </div>
    </article>`;
}

/* ----------------------------------------------------------- results */

function renderTruthRibbon(gt) {
  if (!gt) return "";
  const items = gt.divergences.length
    ? `<ul>${gt.divergences.map((d) => `<li>${esc(d)}</li>`).join("")}</ul>`
    : "";
  const head = gt.has_true_partner
    ? `The data generator planted <strong>${esc(gt.b_record_id)}</strong> as the same person
       (difficulty: ${esc(gt.difficulty)}). Obstacles introduced between the two registries:`
    : `This person has <strong>no true partner</strong> in Registry B. Any candidate below
       is a near-miss the reviewer should reject — which is exactly what the shortlist is for.`;
  return `<div class="truth-ribbon">
      <div>
        <span class="eyebrow">Demo overlay · hidden from the matcher</span>
        <div style="margin-top:4px">${head}</div>
        ${items}
      </div>
    </div>`;
}

function renderDecision(decision) {
  const banner = el("decision-banner");
  if (!decision) { banner.hidden = true; return; }
  banner.hidden = false;
  banner.className = `decision-banner ${decision.decision}`;
  banner.innerHTML = `
    <span><strong>${esc(decision.b_record_id)}</strong> — ${esc(decision.status)}
    <span style="opacity:.7">· logged ${esc(decision.recorded_at)}</span></span>
    <button data-action="clear-decision">Undo</button>`;
}

async function selectRecord(recordId) {
  state.selected = recordId;
  document.querySelectorAll(".record-item").forEach((n) =>
    n.classList.toggle("active", n.dataset.id === recordId));

  const payload = await api(`/api/match/${recordId}?reveal=${state.reveal}`);
  state.lastPayload = payload;

  el("empty-state").hidden = true;
  el("results").hidden = false;

  renderSubject(payload.a_record);
  renderDecision(payload.decision);

  // On a narrow screen the sidebar sits above the results, so choosing a
  // record leaves the reader looking at the list they just used. Bring the
  // answer they asked for into view.
  if (window.matchMedia("(max-width: 1040px)").matches) {
    requestAnimationFrame(() => {
      el("results").scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  const truthId = payload.ground_truth ? payload.ground_truth.b_record_id : null;
  const n = payload.candidates.length;

  el("results-title").textContent = n
    ? `Top ${n} candidate${n === 1 ? "" : "s"} for human review`
    : "No candidates above the reporting threshold";
  el("results-sub").textContent =
    `Ranked from ${payload.compared_against} Registry B records. `
    + `A potential match score expresses evidential support, not identity.`;

  el("candidates").innerHTML =
    (payload.ground_truth ? renderTruthRibbon(payload.ground_truth) : "")
    + (n
      ? payload.candidates.map((c, i) => renderCandidate(c, i, truthId, payload.a_record)).join("")
      : `<div class="no-candidates">
           No Registry B record reached the minimum potential match score for review.
           That is a legitimate outcome — this person may simply not be in Registry B.
         </div>`);

  // A decision taken earlier must still be visible when the record is reopened.
  markDecidedCards(payload.decision || null);
}

/* ------------------------------------------------------------ actions */

/* Show the decision on the card that was clicked.
   The banner sits above the candidate list, so on anything but a short page
   the only acknowledgement of a decision appeared off-screen above the button
   that triggered it -- it looked like nothing had happened. Feedback belongs
   where the click was. */
function markDecidedCards(decision) {
  document.querySelectorAll(".candidate").forEach((card) => {
    const isSubject = decision && card.dataset.bId === decision.b_record_id;
    card.classList.toggle("decided", !!isSubject);
    card.classList.toggle("decided-rule_out",
      !!isSubject && decision.decision === "rule_out");
    card.querySelectorAll("[data-action]").forEach((b) => {
      const mine = isSubject && b.dataset.action === decision.decision;
      b.classList.toggle("btn-decided", !!mine);
      if (!b.dataset.label) b.dataset.label = b.textContent.trim();
      b.textContent = mine
        ? (decision.decision === "refer" ? "✓ Referred" : "✓ Ruled out")
        : b.dataset.label;
    });
  });
}

async function recordDecision(bRecordId, decision) {
  const entry = await api("/api/review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      a_record_id: state.selected, b_record_id: bRecordId, decision,
    }),
  });
  renderDecision(entry);
  markDecidedCards(entry);
  await loadRecords(el("search").value);
}

async function clearDecision() {
  await api(`/api/review/${state.selected}`, { method: "DELETE" });
  renderDecision(null);
  markDecidedCards(null);
  await loadRecords(el("search").value);
}

/* -------------------------------------------------------- showcase */

async function loadShowcase() {
  const { cases } = await api("/api/showcase?limit=5");
  if (!cases.length) return;
  el("showcase").hidden = false;
  el("showcase-chips").innerHTML = cases.map((c) =>
    `<button class="chip" data-id="${esc(c.a_record_id)}"
             title="${esc(c.headline)}">
       <b>${esc(c.display_name)}</b> · ${c.obstacle_count} obstacles
     </button>`).join("");
}

/* ------------------------------------------------------------ wiring */

let searchTimer;
el("search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value;
  searchTimer = setTimeout(() => loadRecords(q), 140);
});

el("record-list").addEventListener("click", (e) => {
  const item = e.target.closest(".record-item");
  if (item) selectRecord(item.dataset.id);
});
el("record-list").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const item = e.target.closest(".record-item");
  if (item) { e.preventDefault(); selectRecord(item.dataset.id); }
});

el("showcase-chips").addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) selectRecord(chip.dataset.id);
});

el("reveal-toggle").addEventListener("change", (e) => {
  state.reveal = e.target.checked;
  if (state.selected) selectRecord(state.selected);
});

el("candidates").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-action]");
  if (!btn) return;
  const card = btn.closest(".candidate");
  if (card) recordDecision(card.dataset.bId, btn.dataset.action);
});

el("decision-banner").addEventListener("click", (e) => {
  if (e.target.closest('[data-action="clear-decision"]')) clearDecision();
});

/* ---------------------------------------------------------- startup */

// #A-0013 opens that record directly. Presenting from a bookmark beats
// searching for a record in front of an audience, and it makes the review
// screen reachable for screenshots and tests.
function selectFromHash() {
  const id = decodeURIComponent(location.hash.replace(/^#/, "")).trim();
  if (/^[AB]-\d+$/.test(id)) selectRecord(id);
}
window.addEventListener("hashchange", selectFromHash);

(async function init() {
  try {
    await Promise.all([loadMeta(), loadRecords(), loadShowcase()]);
    selectFromHash();
  } catch (err) {
    el("stats").innerHTML =
      `<span class="stat-loading">Could not reach the API — is the server running?</span>`;
    console.error(err);
  }
})();
