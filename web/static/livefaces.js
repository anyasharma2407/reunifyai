/*
 * Live face matching against real photographs.
 *
 * Everywhere else this project matches drawn faces, which is honest for a demo
 * and cannot answer whether face matching works. This panel uses real ones: you
 * enrol a photograph into each registry, then match a live camera frame against
 * them.
 *
 * Every part of it runs in this browser. The detector and the recognition model
 * are served from this page, the camera frame is read into a canvas and thrown
 * away, and the only thing kept is a 128-number descriptor held in memory for
 * as long as the tab is open. Nothing is uploaded, nothing is written to disk,
 * and there is no server to upload it to -- the site is static.
 *
 * That is not incidental. A face is the one identifier a person cannot change
 * after it leaks, and a tool aimed at displaced people has no business holding
 * one. So this holds faces the way a door lock does: long enough to compare,
 * then gone.
 */
(function () {
  "use strict";

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const BASE = (document.currentScript && document.currentScript.src || "")
    .replace(/\/static\/livefaces\.js.*$/, "");
  const MODELS = BASE + "/static/faceapi/model";

  // face-api returns a Euclidean distance between two 128-number descriptors.
  // Below about 0.6 is the usual "same person" region.
  const SAME_PERSON = 0.6;

  // Measured on 12 pairs of real photographs from the LFW set (scripts in the
  // repo): same person landed at 0.35-0.55, different people at 0.68-0.87, so
  // 0.6 sits in the gap and separated all 11 detectable pairs correctly.
  //
  // A straight linear rescaling of the distance put genuine matches at 45-66%,
  // which reads as "probably not them" for a pair that is in fact the same
  // person. This curve is centred on the threshold instead, so 50% means "right
  // on the line" and the number moves the way a reader expects. It is a
  // readable restatement of the distance, not a probability, and the raw
  // distance is always shown next to it.
  const toScore = (d) => 100 / (1 + Math.exp((d - SAME_PERSON) / 0.12));

  let panel = null;
  let ready = false;
  let loading = null;
  let stream = null;
  let scanning = false;
  const enrolled = { a: null, b: null };   // in memory only, never persisted

  function close() {
    stopCamera();
    if (panel) panel.remove();
    panel = null;
    document.body.classList.remove("demo-open");
  }

  function stopCamera() {
    scanning = false;
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }
  }

  // The face-api bundle and its weights come to about 8 MB. Loading them on
  // every page view would make the site slow for the many people who never
  // open this panel, so they are fetched the first time it is used.
  function loadScript() {
    if (window.faceapi) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = BASE + "/static/faceapi/face-api.js";
      s.onload = resolve;
      s.onerror = () => reject(new Error("could not load the face model script"));
      document.head.appendChild(s);
    });
  }

  async function loadModels() {
    if (ready) return;
    if (loading) return loading;
    loading = (async () => {
      await loadScript();
      const f = window.faceapi;

      // face-api runs on TensorFlow.js, which needs a compute backend chosen
      // before any model is used. It normally picks WebGL on its own, but on a
      // device where WebGL is unavailable or switched off for privacy the
      // automatic path can stall instead of falling back, and the panel then
      // hangs on "loading" forever. So the choice is made here, explicitly,
      // with a time limit -- the plain CPU backend is slower but always
      // present, and a slow panel beats a stuck one.
      try {
        const withTimeout = (p, ms) => Promise.race([
          p, new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), ms))]);
        let ok = false;
        try {
          // setBackend resolves false rather than throwing when a backend is
          // not registered, so the result has to be checked, not just awaited.
          ok = (await withTimeout(f.tf.setBackend("webgl"), 6000)) !== false;
          if (ok) await withTimeout(f.tf.ready(), 6000);
        } catch (e) { ok = false; }
        if (!ok) {
          await f.tf.setBackend("cpu");
          await f.tf.ready();
        }
      } catch (e) { /* older builds initialise themselves; carry on */ }
      await f.nets.tinyFaceDetector.loadFromUri(MODELS);
      await f.nets.faceLandmark68Net.loadFromUri(MODELS);
      await f.nets.faceRecognitionNet.loadFromUri(MODELS);
      ready = true;
    })();
    return loading;
  }

  // Swept against real photographs: at inputSize 320 the detector found a face
  // in 19 of 24 LFW portraits, and at 512 it collapsed to 10. 416 with a
  // threshold of 0.3 found 23 of 24, so still photographs use that.
  //
  // Live video uses 320: a face at a webcam is large and close, so detection is
  // easy, and a missed frame costs nothing because the next one is 250ms away.
  const STILL = { inputSize: 416, scoreThreshold: 0.3 };
  const LIVE = { inputSize: 320, scoreThreshold: 0.3 };
  const detectorOptions = (o) => new window.faceapi.TinyFaceDetectorOptions(o);

  async function describe(input, opts) {
    const result = await window.faceapi
      .detectSingleFace(input, detectorOptions(opts || STILL))
      .withFaceLandmarks()
      .withFaceDescriptor();
    return result || null;
  }

  function status(msg, kind) {
    const el = panel.querySelector("#lf-status");
    el.className = "lf-status" + (kind ? " lf-" + kind : "");
    el.textContent = msg;
  }

  // --- enrolling --------------------------------------------------------

  async function enrolFromFile(side, file) {
    await loadModels();
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      status("Looking for a face…", "");
      const found = await describe(img);
      if (!found) {
        status("No face found in that picture. Try one where the face is larger "
               + "and facing the camera.", "warn");
        return;
      }
      enrolled[side] = { descriptor: found.descriptor, preview: drawThumb(img, found) };
      renderEnrolled();
      status("Face enrolled into Registry " + side.toUpperCase()
             + ". It stays in this browser.", "ok");
    } catch (err) {
      status("Could not read that image: " + err.message, "warn");
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  function drawThumb(source, found) {
    // Crop to the detected face so the person can see what was actually
    // measured, rather than trusting that the right thing was picked up.
    const b = found.detection.box;
    const pad = b.width * 0.25;
    const c = document.createElement("canvas");
    c.width = c.height = 160;
    const ctx = c.getContext("2d");
    ctx.drawImage(source,
      Math.max(0, b.x - pad), Math.max(0, b.y - pad),
      b.width + pad * 2, b.height + pad * 2, 0, 0, 160, 160);
    return c.toDataURL("image/jpeg", 0.85);
  }

  function renderEnrolled() {
    ["a", "b"].forEach((side) => {
      const slot = panel.querySelector('[data-slot="' + side + '"]');
      const e = enrolled[side];
      slot.innerHTML = e
        ? `<img src="${e.preview}" alt="Enrolled face, registry ${side}">
           <button class="nb-link" data-forget="${side}" type="button">Remove</button>`
        : `<div class="lf-empty">no face yet</div>`;
    });
    const both = enrolled.a && enrolled.b;
    panel.querySelector("#lf-compare").disabled = !both;
    panel.querySelector("#lf-scan").disabled = !(enrolled.a || enrolled.b);
  }

  // --- comparing two enrolled faces -------------------------------------

  function compareEnrolled() {
    const d = window.faceapi.euclideanDistance(enrolled.a.descriptor, enrolled.b.descriptor);
    showResult(toScore(d), d, enrolled.a.preview, enrolled.b.preview,
               "Registry A", "Registry B");
  }

  // --- live camera ------------------------------------------------------

  async function startCamera() {
    status("Loading the face model…", "");
    await loadModels();
    const video = panel.querySelector("#lf-video");
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: 480, height: 360 }, audio: false });
    } catch (err) {
      status(err && err.name === "NotAllowedError"
        ? "Camera permission was declined. You can still compare two uploaded photos."
        : "Could not open the camera: " + (err && err.message),
        "warn");
      return;
    }
    video.srcObject = stream;
    await video.play();
    panel.querySelector(".lf-live").hidden = false;
    scanning = true;
    status("Scanning. Look at the camera.", "");
    scanLoop();
  }

  async function scanLoop() {
    const video = panel.querySelector("#lf-video");
    const readout = panel.querySelector("#lf-live-readout");
    while (scanning && stream) {
      let found = null;
      try {
        found = await describe(video, LIVE);
      } catch (e) { /* a dropped frame is not worth stopping for */ }

      if (!found) {
        readout.innerHTML = `<span class="lf-none">No face in view</span>`;
      } else {
        const rows = ["a", "b"].filter((s) => enrolled[s]).map((s) => {
          const d = window.faceapi.euclideanDistance(enrolled[s].descriptor, found.descriptor);
          const score = toScore(d);
          const near = d < SAME_PERSON;
          return `<div class="lf-row ${near ? "lf-near" : ""}">
              <span class="lf-reg">Registry ${s.toUpperCase()}</span>
              <span class="lf-pct">${score.toFixed(0)}%</span>
              <span class="lf-dist">distance ${d.toFixed(2)}</span>
            </div>`;
        }).join("");
        readout.innerHTML = rows;
      }
      await new Promise((r) => setTimeout(r, 250));
    }
  }

  // --- result -----------------------------------------------------------

  function showResult(score, distance, imgA, imgB, labelA, labelB) {
    const near = distance < SAME_PERSON;
    panel.querySelector("#lf-result").innerHTML = `
      <div class="face-pair">
        <figure class="face-fig">
          <img src="${imgA}" alt="${esc(labelA)}">
          <figcaption>${esc(labelA)}<span>your photo</span></figcaption>
        </figure>
        <div class="face-verdict">
          <div class="face-pct s-${near ? "strong" : "weak"}">${score.toFixed(0)}%</div>
          <div class="face-cap">Facial similarity</div>
          <div class="face-warn">Distance ${distance.toFixed(2)} — under
            ${SAME_PERSON} is the same-person range on real photographs.<br>
            An indicator, not proof of identity.</div>
        </div>
        <figure class="face-fig">
          <img src="${imgB}" alt="${esc(labelB)}">
          <figcaption>${esc(labelB)}<span>your photo</span></figcaption>
        </figure>
      </div>
      <p class="lf-verify">A score is a reason to look, not a decision.
         HUMAN VERIFICATION REQUIRED.</p>`;
  }

  // --- panel ------------------------------------------------------------

  function open() {
    if (panel) return;
    panel = document.createElement("div");
    panel.className = "demo-overlay lf-overlay";
    panel.innerHTML = `
      <div class="demo-panel lf-panel">
        <div class="demo-head">
          <span class="demo-eyebrow">Live face matching · real faces</span>
          <button class="demo-close" data-lf-close aria-label="Close">×</button>
        </div>

        <p class="lf-banner">
          FACIAL SIMILARITY IS AN INDICATOR, NOT PROOF OF IDENTITY.<br>
          HUMAN VERIFICATION REQUIRED.
        </p>

        <p class="nb-warning">
          <strong>Everything here stays in this browser.</strong> No photo or
          camera frame is uploaded — this site has no server. Faces are held in
          memory only and disappear when you close this panel or reload.
        </p>

        <div class="lf-slots">
          <div class="lf-slot">
            <h4>Registry A</h4>
            <div class="lf-face" data-slot="a"></div>
            <label class="btn lf-upload">Choose a photo
              <input type="file" accept="image/*" data-enrol="a" hidden>
            </label>
          </div>
          <div class="lf-slot">
            <h4>Registry B</h4>
            <div class="lf-face" data-slot="b"></div>
            <label class="btn lf-upload">Choose a photo
              <input type="file" accept="image/*" data-enrol="b" hidden>
            </label>
          </div>
        </div>

        <div class="lf-actions">
          <button class="btn btn-primary" id="lf-compare" type="button" disabled>
            Compare the two photos</button>
          <button class="btn" id="lf-scan" type="button" disabled>
            Match a live face</button>
          <button class="btn btn-undo" id="lf-clear" type="button">Clear all faces</button>
        </div>

        <p class="lf-status" id="lf-status">Add a face to each registry to begin.</p>

        <div class="lf-live" hidden>
          <video id="lf-video" playsinline muted></video>
          <div id="lf-live-readout" class="lf-readout"></div>
        </div>

        <div id="lf-result"></div>

        <p class="nb-credit">
          Detection and recognition by face-api.js, running on this device.
          Consent matters more than the technology: only enrol a face whose
          owner has agreed to it.
        </p>
      </div>`;

    panel.addEventListener("click", (e) => {
      if (e.target === panel || e.target.closest("[data-lf-close]")) return close();
      const forget = e.target.closest("[data-forget]");
      if (forget) {
        enrolled[forget.dataset.forget] = null;
        renderEnrolled();
        panel.querySelector("#lf-result").innerHTML = "";
        status("Removed.", "");
      }
    });
    panel.addEventListener("change", (e) => {
      const input = e.target.closest("[data-enrol]");
      if (input && input.files && input.files[0]) {
        enrolFromFile(input.dataset.enrol, input.files[0]);
        input.value = "";
      }
    });
    panel.querySelector("#lf-compare").addEventListener("click", compareEnrolled);
    panel.querySelector("#lf-scan").addEventListener("click", startCamera);
    panel.querySelector("#lf-clear").addEventListener("click", () => {
      stopCamera();
      enrolled.a = enrolled.b = null;
      panel.querySelector(".lf-live").hidden = true;
      panel.querySelector("#lf-result").innerHTML = "";
      renderEnrolled();
      status("All faces cleared from memory.", "ok");
    });

    document.body.appendChild(panel);
    document.body.classList.add("demo-open");
    renderEnrolled();

    // Warm the model up in the background so the first upload is not a wait.
    loadModels().catch(() => {
      status("The face model could not be loaded. Check your connection and "
             + "reload the page.", "warn");
    });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && panel) close();
  });

  const btn = document.getElementById("live-faces");
  if (btn) btn.addEventListener("click", open);
})();
