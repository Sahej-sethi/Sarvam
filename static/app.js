"use strict";

const $ = (id) => document.getElementById(id);
let session = null, current = 0, selectedFile = null, busy = false, recording = false;
let stopCapture = null, clock = null, recordingBlob = null;
let stateToken = "", maxPdfBytes = 20 * 1024 * 1024;
try { stateToken = sessionStorage.getItem("samjhaproof-state") || ""; } catch (_) {}
const drafts = {}, mediaURLs = new Map();
const audioCache = new Map();
const languageLabels = {English: "en", Hindi: "hi"};

function show(id, visible = true) { $(id).classList.toggle("hidden", !visible); }
function error(message) { $("notice").textContent = message; show("notice"); }
function setBusy(value, message = "") {
  busy = value;
  document.body.classList.toggle("busy", value);
  show("working", value);
  $("working-text").textContent = message;
  document.querySelectorAll("button, select, input, textarea").forEach(el => el.disabled = value);
  if (!value) updateNavigation();
}
async function task(message, fn) {
  if (busy || recording) return;
  show("notice", false); setBusy(true, message);
  try { await fn(); } catch (e) { error(e.message || "Something went wrong. Please retry."); }
  finally { setBusy(false); }
}
async function api(path, data = {}) {
  let response;
  try {
    response = await fetch(`/api/${path}`, {method: "POST", headers: {"Content-Type": "application/json", "X-SamjhaProof": "1"}, body: JSON.stringify({...data, ...(stateToken ? {_state: stateToken} : {})})});
  } catch (_) { throw new Error("The app is unreachable. Check your connection and retry."); }
  let result;
  try { result = await response.json(); } catch (_) { throw new Error("The server returned an unreadable response. Please retry."); }
  if (result._state) {
    stateToken = result._state;
    try { sessionStorage.setItem("samjhaproof-state", stateToken); } catch (_) {}
    delete result._state;
  }
  if (!response.ok) throw new Error(result.error || "The operation could not finish.");
  return result;
}
function base64(blob) {
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(",")[1]); reader.onerror = () => reject(new Error("Could not read this file.")); reader.readAsDataURL(blob); });
}
function node(tag, text, cls) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; }
function clearAudio(id) {
  const el = $(id); el.pause(); el.removeAttribute("src"); show(id, false);
  if (mediaURLs.has(id)) { URL.revokeObjectURL(mediaURLs.get(id)); mediaURLs.delete(id); }
}
function audioBlob(id, blob) {
  clearAudio(id); const url = URL.createObjectURL(blob); mediaURLs.set(id, url); $(id).src = url; show(id);
}
async function play(kind, id) {
  await task("Creating spoken audio…", async () => {
    const text = kind === "summary" ? session.lesson.summary : kind === "feedback" ? session.answers[String(current)]?.correction : session.lesson.questions[current].question;
    const cacheKey = `${session.language}:${kind}:${current}:${text}`;
    let result = audioCache.get(cacheKey);
    if (!result) { result = await api("speak", {kind, index: current}); audioCache.set(cacheKey, result); }
    const bytes = Uint8Array.from(atob(result.audio), c => c.charCodeAt(0));
    audioBlob(id, new Blob([bytes], {type: result.mime || "audio/mpeg"}));
    try { await $(id).play(); } catch (_) { /* Browser may require clicking its play button. */ }
  });
}
function chooseFile(file) {
  if (!file || busy || recording) return;
  if (!file.name.toLowerCase().endsWith(".pdf") || file.size > maxPdfBytes) {
    error(`Choose a PDF smaller than ${Math.floor(maxPdfBytes / 1000000)} MB, with no more than 10 pages.`); return;
  }
  selectedFile = file; $("file-label").textContent = file.name;
  $("file-meta").textContent = `${(file.size / 1024).toFixed(0)} KB · Ready to upload`;
  show("notice", false);
}
$("pdf").addEventListener("change", e => chooseFile(e.target.files[0]));
$("dropzone").addEventListener("dragover", e => {e.preventDefault(); $("dropzone").classList.add("drag");});
$("dropzone").addEventListener("dragleave", () => $("dropzone").classList.remove("drag"));
$("dropzone").addEventListener("drop", e => {e.preventDefault(); $("dropzone").classList.remove("drag"); chooseFile(e.dataTransfer.files[0]);});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function finishDocument() {
  render();
  if (!session.pages.length) {
    const deadline = Date.now() + 180000;
    while (true) {
      const result = await api("poll"); session = result;
      if (result.status === "ready") break;
      $("working-text").textContent = `Reading your document… ${result.processed || 0}/${result.total || "?"} pages processed`;
      if (Date.now() > deadline) throw new Error("Sarvam is still processing the document. Use ‘Continue processing’ to resume the same job.");
      await delay(3000);
    }
  }
  $("working-text").textContent = "Finding three important takeaways and checking their sources…";
  if (!session.lesson) session = await api("prepare");
  render();
}
async function start(file) {
  session = await api("document", {filename: file.name, file: await base64(file), language: $("language").value, document_language: $("document-language").value});
  await finishDocument();
}
$("upload-form").addEventListener("submit", e => {
  e.preventDefault();
  if (!selectedFile) { error("Choose your PDF first, or try the workshop safety sample."); return; }
  task("Sending your PDF to Sarvam Vision…", async () => { try { await start(selectedFile); } finally { render(); } });
});
$("sample").addEventListener("click", () => task("Opening the workshop safety sample…", async () => {
  const r = await fetch("/sample.pdf");
  if (!r.ok) throw new Error("Could not load the sample PDF.");
  const file = new File([await r.blob()], "Workshop Safety.pdf", {type: "application/pdf"});
  try { await start(file); } finally { render(); }
}));
$("reset").addEventListener("click", () => task("Clearing this local session…", async () => {
  session = await api("reset"); current = 0; selectedFile = null; recordingBlob = null; audioCache.clear();
  Object.keys(drafts).forEach(k => delete drafts[k]);
  for (const id of [...mediaURLs.keys()]) clearAudio(id);
  $("pdf").value = ""; $("file-label").textContent = "Drop your PDF here"; $("file-meta").textContent = "or click to choose a file";
  render();
}));

function render() {
  if (!session) return;
  const active = Boolean(session.job);
  document.body.classList.toggle("session-active", active);
  $("step2").classList.toggle("active", Boolean(session.lesson));
  $("step3").classList.toggle("active", Object.keys(session.answers).length === 3);
  show("session-tools", active); show("document-status", active);
  show("session-progress", Boolean(session.lesson));
  const completed = Object.keys(session.answers).length;
  $("session-progress-label").textContent = `${completed} / 3`;
  $("session-progress-bar").value = completed;
  $("document-status").replaceChildren();
  if (active) {
    $("document-status").append(node("strong", session.filename), node("div", `${session.document_language} document → ${session.language} conversation`));
    $("document-status").append(node("div", session.pages.length ? `${session.pages.length} pages read · ${session.partial ? "Some pages failed; only extracted pages will be assessed" : "Ready to explore"}` : "Document submitted · Processing in progress"));
    if (!session.lesson) {
      const retry = node("button", session.pages.length ? "Generate questions →" : "Continue processing →", "text-button");
      retry.addEventListener("click", () => task("Resuming your document…", async () => { try { await finishDocument(); } finally { render(); } }));
      $("document-status").append(retry);
    }
  }
  show("empty", !session.lesson); show("lesson", Boolean(session.lesson));
  if (!session.lesson) return;
  $("lesson-title").textContent = session.lesson.title;
  $("lesson-summary").textContent = session.lesson.summary;
  $("lesson").lang = languageLabels[session.language];
  $("pages").replaceChildren();
  for (const p of session.pages) $("pages").append(node("strong", `Page ${p.page}`), node("div", p.text, "page-content"));
  renderQuestion(); renderReport();
}
function renderQuestion() {
  const q = session.lesson.questions[current];
  $("counter").textContent = `${String(current + 1).padStart(2, "0")} / 03`;
  $("question-text").textContent = q.question; $("explanation").textContent = q.explanation;
  $("source-page").textContent = `Page ${q.page}`; $("source-quote").textContent = q.quote;
  $("question-tabs").replaceChildren();
  session.lesson.questions.forEach((_, i) => {
    const answered = Boolean(session.answers[String(i)]);
    const tab = node("button", `${answered ? "✓ " : ""}Takeaway ${i + 1}`, current === i ? "selected" : "");
    tab.setAttribute("aria-current", current === i ? "step" : "false");
    tab.addEventListener("click", () => navigate(i)); $("question-tabs").append(tab);
  });
  $("answer").value = drafts[current] ?? session.answers[String(current)]?.answer ?? "";
  $("answer-count").textContent = `${$("answer").value.length.toLocaleString()} / 4,000`;
  show("retry-transcribe", Boolean(recordingBlob));
  const result = session.answers[String(current)];
  show("feedback", Boolean(result));
  $("feedback").replaceChildren();
  if (result) {
    const header = node("div", undefined, "score-header");
    const copy = node("div"); copy.append(node("div", "YOUR UNDERSTANDING", "panel-label"), node("p", `Attempt ${result.attempt} · Your meaning, compared with the source`));
    const score = node("div", `${result.score}% `, "score-number"); score.append(node("small", "understood")); header.append(copy, score); $("feedback").append(header);
    const assessed = node("details"), assessedLabel = node("summary", "The answer assessed");
    assessed.append(assessedLabel, node("p", result.answer, "small")); $("feedback").append(assessed);
    for (const r of result.results) {
      const row = node("div", undefined, "assessment-row");
      row.append(node("span", {understood: "✓ Understood", missed: "↳ Missed", incorrect: "! Needs another look"}[r.status], `status-badge ${r.status}`), node("strong", r.criterion));
      if (r.evidence) row.append(node("q", r.evidence));
      row.append(node("p", r.feedback)); $("feedback").append(row);
    }
    $("feedback").append(node("div", result.correction, "correction"));
    const actions = node("div", undefined, "feedback-actions"), listen = node("button", "◖ Hear the feedback", "secondary"), retry = node("button", "Try explaining it again ↗", "text-button");
    listen.addEventListener("click", () => play("feedback", "feedback-audio"));
    retry.addEventListener("click", () => { if (busy || recording) return; $("answer").focus(); $("answer").scrollIntoView({behavior:"smooth", block:"center"}); });
    actions.append(listen, retry); $("feedback").append(actions);
    const audio = node("audio"); audio.id = "feedback-audio"; audio.controls = true; audio.className = "hidden"; audio.setAttribute("aria-label", "Spoken feedback"); $("feedback").append(audio);
  }
  updateNavigation();
}
function updateNavigation() {
  $("previous").disabled = busy || recording || current === 0;
  $("next").disabled = busy || recording || current === 2;
  $("evaluate").disabled = busy || recording || !$("answer").value.trim();
  if (session) $("completion-note").textContent = `${Object.keys(session.answers).length} of 3 explored`;
}
function navigate(index) {
  if (busy || recording || index < 0 || index > 2 || current === index) return;
  drafts[current] = $("answer").value;
  ["question-audio", "recording-audio"].forEach(clearAudio);
  if ($("feedback-audio")) clearAudio("feedback-audio");
  recordingBlob = null; current = index; renderQuestion();
}
$("previous").addEventListener("click", () => navigate(current - 1));
$("next").addEventListener("click", () => navigate(current + 1));
$("listen-summary").addEventListener("click", () => play("summary", "summary-audio"));
$("listen-question").addEventListener("click", () => play("question", "question-audio"));
$("answer").addEventListener("input", () => { drafts[current] = $("answer").value; $("answer-count").textContent = `${$("answer").value.length.toLocaleString()} / 4,000`; updateNavigation(); });
$("evaluate").addEventListener("click", () => task("Listening for meaning and checking each takeaway…", async () => {
  drafts[current] = $("answer").value;
  if ($("feedback-audio")) clearAudio("feedback-audio");
  session = await api("evaluate", {index: current, answer: drafts[current]});
  render(); $("feedback").scrollIntoView({behavior:"smooth", block:"center"});
}));

function renderReport() {
  const results = Object.values(session.answers), complete = results.length === 3;
  show("report", complete); $("report").replaceChildren();
  if (!complete) return;
  const total = results.reduce((sum, r) => sum + r.results.length, 0);
  const understood = results.reduce((sum, r) => sum + r.results.filter(x => x.status === "understood").length, 0);
  const score = Math.round(understood / total * 100);
  $("report").append(node("div", "YOUR CLARITY CHECK", "panel-label"), node("h2", score === 100 ? "That’s understanding you can explain." : "Now you know what needs another look."), node("div", `${score}%`, "score-number"), node("p", `${understood} of ${total} key facts demonstrated across three takeaways. This is an AI assessment of these answers, not a certification.`));
  const bars = node("div", undefined, "report-bars");
  session.lesson.questions.forEach((_, i) => { const item = node("div"), r = session.answers[String(i)], track = node("div", undefined, "report-bar"), fill = node("div", undefined, "report-bar-fill"); fill.style.width = `${r.score}%`; track.append(fill); item.append(node("span", `Takeaway ${i + 1} · ${r.score}%`), track); bars.append(item); });
  $("report").append(bars);
  const download = node("button", "Download my comprehension report ↓", "primary");
  download.addEventListener("click", () => {
    const report = {product: "SamjhaProof", generated_at: new Date().toISOString(), document: session.filename, language: session.language, partial_extraction: session.partial, score, understood, criteria_total: total,
      scope: "AI assessment of these answers against extracted source text; does not verify the original document.",
      takeaways: session.lesson.questions.map((q, i) => ({...q, assessment: session.answers[String(i)]}))};
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type:"application/json"}));
    const a = node("a"); a.href = url; a.download = "samjhaproof-report.json"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }); $("report").append(download);
}

function wavBlob(chunks, sampleRate) {
  const count = Math.min(chunks.reduce((n, c) => n + c.length, 0), sampleRate * 30), samples = new Float32Array(count);
  let offset = 0; for (const chunk of chunks) { const part = chunk.subarray(0, count - offset); samples.set(part, offset); offset += part.length; if (offset >= count) break; }
  const targetRate = 16000, ratio = sampleRate / targetRate, length = Math.floor(count / ratio);
  const buffer = new ArrayBuffer(44 + length * 2), view = new DataView(buffer);
  const str = (offset, value) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  str(0,"RIFF"); view.setUint32(4,36 + length * 2,true); str(8,"WAVE"); str(12,"fmt ");
  view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true);
  view.setUint32(24,targetRate,true); view.setUint32(28,targetRate * 2,true); view.setUint16(32,2,true); view.setUint16(34,16,true);
  str(36,"data"); view.setUint32(40,length * 2,true);
  for (let i=0; i<length; i++) {
    let sum=0, n=0;
    for (let j=Math.floor(i*ratio); j<Math.min(Math.floor((i+1)*ratio), count); j++) { sum+=samples[j]; n++; }
    const value=Math.max(-1,Math.min(1,sum/(n || 1))); view.setInt16(44+i*2,value<0?value*32768:value*32767,true);
  }
  return new Blob([buffer], {type:"audio/wav"});
}
async function transcribe() {
  if (!recordingBlob) return;
  await task("Transcribing your answer with Saaras…", async () => {
    const result = await api("transcribe", {index:current, audio:await base64(recordingBlob)});
    drafts[current] = result.transcript; $("answer").value = result.transcript; show("retry-transcribe", false);
    $("answer-count").textContent = `${result.transcript.length.toLocaleString()} / 4,000`;
    $("answer").focus();
  });
}
$("retry-transcribe").addEventListener("click", transcribe);
$("record").addEventListener("click", async () => {
  if (recording) { if (stopCapture) await stopCapture(); return; }
  if (busy) return;
  if (!navigator.mediaDevices?.getUserMedia) { error("Microphone access needs localhost and a supported browser. You can type your answer instead."); return; }
  let stream, context, source, processor, mute;
  let stopped = false;
  const release = async () => {
    clearInterval(clock); processor?.disconnect(); source?.disconnect(); mute?.disconnect();
    stream?.getTracks().forEach(t => t.stop());
    if (context && context.state !== "closed") await context.close();
  };
  try {
    setBusy(true, "Waiting for microphone permission…"); show("notice", false);
    stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});
    context = new AudioContext(); await context.resume();
    source = context.createMediaStreamSource(stream); processor = context.createScriptProcessor(4096,1,1);
    mute = context.createGain(); mute.gain.value = 0;
    const chunks = [], rate = context.sampleRate;
    processor.onaudioprocess = e => { if (!stopped) chunks.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
    source.connect(processor); processor.connect(mute); mute.connect(context.destination);
    setBusy(false); recording = true;
    document.querySelectorAll("button, select, input, textarea").forEach(el => el.disabled = true);
    $("record").disabled = false; $("record").classList.add("recording");
    $("record").replaceChildren(node("span", undefined, "record-dot"), document.createTextNode("Stop recording"));
    clearAudio("question-audio"); clearAudio("summary-audio"); if ($("feedback-audio")) clearAudio("feedback-audio");
    const started = Date.now();
    stopCapture = async () => {
      if (stopped) return; stopped = true;
      const blob = wavBlob(chunks, rate);
      await release(); recording = false; stopCapture = null;
      document.querySelectorAll("button, select, input, textarea").forEach(el => el.disabled = false);
      $("record").classList.remove("recording"); $("record").replaceChildren(node("span", undefined, "record-dot"), document.createTextNode("Record again"));
      $("record-time").textContent = "Up to 30 seconds";
      recordingBlob = blob; audioBlob("recording-audio", blob); show("retry-transcribe"); updateNavigation(); await transcribe();
    };
    clock = setInterval(() => { const elapsed = Math.floor((Date.now()-started)/1000); $("record-time").textContent = `${Math.min(elapsed,30)}s / 30s`; if(elapsed >= 30) stopCapture?.(); }, 200);
  } catch (_) {
    await release(); recording = false; setBusy(false);
    error("Could not start the microphone. Allow microphone access in your browser, or type your answer below.");
  }
});
window.addEventListener("beforeunload", () => { if (stopCapture) stopCapture(); for (const url of mediaURLs.values()) URL.revokeObjectURL(url); });

(async () => {
  await task("Opening your workspace…", async () => {
    if (stateToken) {
      try { session = await api("session"); }
      catch (_) { stateToken = ""; try { sessionStorage.removeItem("samjhaproof-state"); } catch (_) {} }
    }
    if (!session) {
      const r = await fetch("/api/session"); if (!r.ok) throw new Error("Could not open your session.");
      session = await r.json();
      if (session._state) {
        stateToken = session._state; delete session._state;
        try { sessionStorage.setItem("samjhaproof-state", stateToken); } catch (_) {}
      }
    }
    maxPdfBytes = session.max_pdf_bytes || maxPdfBytes;
    $("file-limit").textContent = `PDF · Up to 10 pages · ${Math.floor(maxPdfBytes / 1000000)} MB`;
    if (!session.configured) error("Add SARVAM_API_KEY to .env, then restart server.py.");
    current = [0,1,2].find(i => !session.answers[String(i)]) ?? 0;
    render();
  });
})();
