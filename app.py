```python
import asyncio
import io
import ipaddress
import json
import os
import re
import socket
import threading
import time
import uuid
import zipfile

from pathlib import Path
from urllib.parse import urljoin, urlparse

from flask import Flask, request, jsonify, render_template_string, send_file, abort
from PIL import Image, ImageFile
from playwright.async_api import async_playwright

APP = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "panelpress_output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_CHAPTERS = 1000
MAX_IMAGES = 200
MAX_IMAGE_BYTES = 25 * 1024 * 1024

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = 100_000_000

jobs = {}
jobs_lock = threading.Lock()
job_semaphore = threading.Semaphore(1)


# ============================================================
# DASHBOARD
# ============================================================

PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PanelPress</title>
<style>
:root {
 --bg:#090d18;--panel:#111827;--border:#273449;
 --text:#edf2ff;--muted:#94a3b8;--accent:#8b5cf6;
 --green:#34d399;--red:#fb7185;
}
*{box-sizing:border-box}
body{
 margin:0;padding:28px 16px;color:var(--text);
 background:radial-gradient(ellipse at top,#1d2446,transparent 48%),var(--bg);
 font-family:system-ui,-apple-system,Segoe UI,sans-serif
}
.container{max-width:1100px;margin:auto}
header{margin-bottom:25px}
h1{margin:0;font-size:32px}
h2{font-size:19px;margin:0 0 18px}
h3{font-size:14px}
.muted,.hint{color:var(--muted);font-size:13px}
.panel{
 background:linear-gradient(145deg,#141d30,#0f1726);
 border:1px solid var(--border);border-radius:18px;
 padding:22px;margin-bottom:20px
}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}
.full{grid-column:1/-1}
label{display:block;font-size:13px;margin-bottom:7px}
input,select,button,.link{
 font:inherit;border:1px solid #334155;border-radius:10px;
 padding:12px;color:var(--text);background:#090f1c
}
input{width:100%}
button,.link{cursor:pointer;text-decoration:none;font-weight:700}
button:hover,.link:hover{filter:brightness(1.15)}
button:disabled{opacity:.5;cursor:wait}
.primary{width:100%;background:linear-gradient(135deg,#8b5cf6,#6366f1);border:0}
.secondary{background:#29364d}
.download{background:#34d399;color:#052e22;border:0}
.reader-link{background:#c4b5fd;color:#24134c;border:0}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px;margin:20px 0}
.stat{padding:14px;border:1px solid #253148;background:#0a1120;border-radius:12px;min-width:0}
.stat-label{font-size:12px;color:var(--muted);margin-bottom:8px}
.stat-value{font-size:23px;font-weight:800;overflow-wrap:anywhere}
.stat-sub{font-size:11px;color:#64748b;margin-top:4px}
.status{display:flex;align-items:center;gap:8px;font-size:13px}
.dot{width:8px;height:8px;border-radius:50%;background:#fbbf24}
.dot.running{background:#34d399}
.dot.error{background:#fb7185}
.progress{margin:22px 0}
.progress-title{display:flex;justify-content:space-between;gap:10px;margin-bottom:9px;font-size:13px}
.track{height:12px;background:#080d17;border:1px solid #273449;border-radius:30px;overflow:hidden}
.bar{height:100%;width:0;background:linear-gradient(90deg,#8b5cf6,#c4b5fd);transition:width .25s}
.bar.images{background:linear-gradient(90deg,#059669,#34d399)}
.current{background:#0b1220;border:1px solid #303c56;padding:14px;border-radius:11px;overflow-wrap:anywhere}
.log{height:210px;overflow:auto;background:#070c15;border:1px solid #273449;border-radius:11px;padding:12px;font:12px/1.8 Consolas,monospace}
.log-entry{overflow-wrap:anywhere;color:#a5b4fc}
.log-entry.error{color:#fda4af}
.log-entry.success{color:#6ee7b7}
.log-time{color:#64748b}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:15px}
.hidden{display:none!important}
.error{display:none;background:#4c0519;border:1px solid #9f1239;color:#fecdd3;border-radius:10px;padding:12px;margin-top:14px;overflow-wrap:anywhere}
footer{text-align:center;color:#64748b;font-size:12px;padding:10px}
@media(max-width:700px){.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.panel{padding:16px}}
@media(max-width:480px){.grid{grid-template-columns:1fr}.full{grid-column:auto}.actions>*{width:100%}}
</style>
</head>
<body>
<div class="container">
<header>
 <h1>📚 PanelPress</h1>
 <div class="muted">Download, organize, and read manga.</div>
</header>

<section class="panel">
<h2>⚙️ Download settings</h2>
<form id="form">
 <div class="grid">
  <div class="full">
   <label for="website">Website domain</label>
   <input id="website" value="mangafish.cc" required>
   <div class="hint">Enter a public domain without https:// or a path.</div>
  </div>
  <div class="full">
   <label for="manga">Manga slug</label>
   <input id="manga" value="blue-lock" required>
  </div>
  <div>
   <label for="first">First chapter</label>
   <input id="first" type="number" min="1" value="1" required>
  </div>
  <div>
   <label for="last">Last chapter</label>
   <input id="last" type="number" min="1" value="10" required>
  </div>
 </div>
 <p><button class="primary" id="start" type="submit">▶ Start download</button></p>
 <div class="error" id="formError"></div>
</form>
</section>

<section class="panel">
 <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap">
  <h2>📊 Progress dashboard</h2>
  <div class="status"><span class="dot" id="dot"></span><span id="status">Ready</span></div>
 </div>

 <div class="stats">
  <div class="stat"><div class="stat-label">Chapters completed</div><div class="stat-value" id="completed">0</div><div class="stat-sub" id="chapterSub">of 0 chapters</div></div>
  <div class="stat"><div class="stat-label">Images downloaded</div><div class="stat-value" id="images">0</div><div class="stat-sub" id="imageSub">Images saved</div></div>
  <div class="stat"><div class="stat-label">PDF pages</div><div class="stat-value" id="pages">0</div><div class="stat-sub">Image pages</div></div>
  <div class="stat"><div class="stat-label">Failed chapters</div><div class="stat-value" id="failed">0</div><div class="stat-sub">Without usable images</div></div>
  <div class="stat"><div class="stat-label">Downloaded size</div><div class="stat-value" id="bytes">0 B</div><div class="stat-sub">Original image data</div></div>
  <div class="stat"><div class="stat-label">Elapsed time</div><div class="stat-value" id="elapsed">00:00</div><div class="stat-sub">Since job started</div></div>
  <div class="stat"><div class="stat-label">Estimated remaining</div><div class="stat-value" id="eta">—</div><div class="stat-sub">Estimate</div></div>
  <div class="stat"><div class="stat-label">Image errors</div><div class="stat-value" id="imageErrors">0</div><div class="stat-sub">Failed image downloads</div></div>
 </div>

 <div class="progress">
  <div class="progress-title"><strong>Chapter progress</strong><strong id="chapterPercent">0%</strong></div>
  <div class="track"><div class="bar" id="chapterBar"></div></div>
  <div class="hint" id="chapterDescription">Waiting for a job.</div>
 </div>

 <div class="progress">
  <div class="progress-title"><strong>Image progress</strong><strong id="imagePercent">0%</strong></div>
  <div class="track"><div class="bar images" id="imageBar"></div></div>
  <div class="hint" id="imageDescription">The estimate updates as images are discovered.</div>
 </div>

 <div class="current">
  <div class="hint">CURRENTLY PROCESSING</div>
  <strong id="currentChapter">No chapter selected</strong>
 </div>

 <h3>Activity log</h3>
 <div class="log" id="log" aria-live="polite"></div>
 <div class="actions">
  <a class="link download hidden" id="zipLink">⬇ Download ZIP</a>
  <a class="link reader-link hidden" id="readerLink">📖 Read Manga</a>
  <button class="secondary" id="clearLog" type="button">Clear log</button>
 </div>
 <div class="error" id="jobError"></div>
</section>
<footer>PanelPress · Leave this page open to watch progress.</footer>
</div>

<script>
const $ = id => document.getElementById(id);
let jobId = null, pollTimer = null, seenLogs = 0, busy = false;

function errorBox(id, message) {
  $(id).textContent = message;
  $(id).style.display = "block";
}
function hideError(id) {
  $(id).textContent = "";
  $(id).style.display = "none";
}
function bytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + " B";
  const units = ["KB","MB","GB","TB"];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < units.length - 1);
  return n.toFixed(n < 10 ? 2 : 1) + " " + units[i];
}
function duration(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  let n = Math.max(0, Math.floor(Number(value)));
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  const s = n % 60;
  return h ? [h,m,s].map(v => String(v).padStart(2,"0")).join(":")
           : [m,s].map(v => String(v).padStart(2,"0")).join(":");
}
function addLog(entry) {
  const row = document.createElement("div");
  row.className = "log-entry";
  if (/error|failed|blocked/i.test(entry.message)) row.classList.add("error");
  if (/finished|created|complete/i.test(entry.message)) row.classList.add("success");
  const stamp = document.createElement("span");
  stamp.className = "log-time";
  stamp.textContent = "[" + (entry.time || "Info") + "] ";
  row.append(stamp, document.createTextNode(entry.message || ""));
  $("log").appendChild(row);
  $("log").scrollTop = $("log").scrollHeight;
}
function updateLogs(entries) {
  if (!Array.isArray(entries)) return;
  if (entries.length < seenLogs) {
    $("log").replaceChildren();
    seenLogs = 0;
  }
  for (let i = seenLogs; i < entries.length; i++) addLog(entries[i]);
  seenLogs = entries.length;
}
function updateDashboard(d) {
  const total = Number(d.total) || 0;
  const completed = Number(d.completed) || 0;
  const images = Number(d.images) || 0;
  const target = Number(d.image_target) || 0;
  const cp = total ? Math.min(100, completed / total * 100) : 0;
  const ip = target ? Math.min(100, images / target * 100) : 0;

  $("chapterBar").style.width = cp + "%";
  $("imageBar").style.width = ip + "%";
  $("chapterPercent").textContent = Math.floor(cp) + "%";
  $("imagePercent").textContent = Math.floor(ip) + "%";
  $("completed").textContent = completed;
  $("chapterSub").textContent = "of " + total + " chapters";
  $("images").textContent = images;
  $("imageSub").textContent = target + " found/estimated";
  $("pages").textContent = d.pages || 0;
  $("failed").textContent = d.failed || 0;
  $("bytes").textContent = bytes(d.bytes);
  $("elapsed").textContent = duration(d.elapsed);
  $("eta").textContent = d.eta === null ? "—" : duration(d.eta);
  $("imageErrors").textContent = d.image_errors || 0;
  $("chapterDescription").textContent = completed + " of " + total + " chapters completed";
  $("imageDescription").textContent = images + " images saved; " + target + " discovered so far";
  $("currentChapter").textContent = d.current_chapter || "No chapter selected";

  $("status").textContent = d.state === "complete" ? "Complete" :
    d.state === "error" ? "Error" :
    d.state === "running" ? "Running" : (d.status || "Queued");

  $("dot").className = "dot";
  if (d.state === "running") $("dot").classList.add("running");
  if (d.state === "error") $("dot").classList.add("error");

  updateLogs(d.log);

  if (d.state === "complete") {
    busy = false;
    $("start").disabled = false;
    $("start").textContent = "▶ Start another download";
    $("zipLink").href = d.download_url;
    $("zipLink").classList.remove("hidden");
    $("readerLink").href = "/reader/" + encodeURIComponent(d.job_id);
    $("readerLink").classList.remove("hidden");
  }

  if (d.state === "error") {
    busy = false;
    $("start").disabled = false;
    $("start").textContent = "▶ Try again";
    if (d.error) errorBox("jobError", d.error);
  }
}
async function poll() {
  if (!jobId) return;
  try {
    const response = await fetch("/progress/" + encodeURIComponent(jobId), {cache:"no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Progress request failed.");
    updateDashboard(data);
    if (data.state === "complete" || data.state === "error") {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  } catch (e) {
    errorBox("jobError", e.message);
  }
}
$("form").addEventListener("submit", async event => {
  event.preventDefault();
  if (busy) return;
  hideError("formError");
  hideError("jobError");

  const first = Number($("first").value);
  const last = Number($("last").value);
  if (!Number.isInteger(first) || !Number.isInteger(last) ||
      first < 1 || last < first || last - first + 1 > 1000) {
    errorBox("formError", "Enter a valid chapter range of no more than 1,000 chapters.");
    return;
  }

  busy = true;
  $("start").disabled = true;
  $("start").textContent = "Starting...";
  $("zipLink").classList.add("hidden");
  $("readerLink").classList.add("hidden");
  $("log").replaceChildren();
  seenLogs = 0;

  try {
    const response = await fetch("/create", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({
        website:$("website").value,
        manga:$("manga").value,
        first:first,
        last:last
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not start the job.");

    jobId = data.job_id;
    await poll();
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(poll, 1000);
  } catch (e) {
    busy = false;
    $("start").disabled = false;
    $("start").textContent = "▶ Start download";
    errorBox("formError", e.message);
  }
});
$("clearLog").addEventListener("click", () => {
  $("log").replaceChildren();
  seenLogs = 0;
});
</script>
</body>
</html>
"""


# ============================================================
# READER
# ============================================================

READER_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PanelPress Reader</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#080b12;color:#eee;font-family:system-ui,sans-serif}
header{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:13px;background:#111827;border-bottom:1px solid #30394c}
header h1{font-size:19px;margin:0 auto 0 0}
button,select,a{font:inherit;border:1px solid #3b465c;border-radius:9px;padding:9px 12px;background:#202b40;color:#fff;text-decoration:none;cursor:pointer}
button:hover,a:hover{background:#34415c}
main{max-width:1300px;margin:auto;padding:12px}
.toolbar{display:flex;justify-content:center;gap:9px;align-items:center;flex-wrap:wrap;margin:12px 0}
#counter{color:#c4b5fd;font-weight:700;min-width:105px;text-align:center}
.reader{height:calc(100vh - 175px);min-height:300px;display:flex;align-items:center;justify-content:center;position:relative;background:#05070b;border:1px solid #263044;border-radius:12px;overflow:hidden}
#pageImage{max-width:100%;max-height:100%;object-fit:contain;display:block}
.tap{position:absolute;top:0;bottom:0;width:25%;border:0;border-radius:0;background:transparent;opacity:0}
.tap:hover{background:#ffffff0b}
#leftTap{left:0}#rightTap{right:0}
#message{color:#aebbd0;text-align:center;padding:20px}
.hint{text-align:center;color:#8997ad;font-size:12px;margin:10px}
@media(max-width:600px){header h1{width:100%}.reader{height:calc(100vh - 235px)}}
</style>
</head>
<body>
<header>
 <h1>📖 PanelPress Reader</h1>
 <a href="/">← Dashboard</a>
 <select id="chapterSelect" aria-label="Choose chapter"></select>
 <select id="direction" aria-label="Reading direction">
  <option value="ltr">Left to right</option>
  <option value="rtl">Right to left</option>
 </select>
 <button id="fullscreen">⛶ Fullscreen</button>
</header>
<main>
 <div class="toolbar">
  <button id="first">|← First</button>
  <button id="previous">← Previous</button>
  <span id="counter">Page 0 / 0</span>
  <button id="next">Next →</button>
  <button id="last">Last →|</button>
 </div>
 <div class="reader" id="reader">
  <div id="message">Loading chapters...</div>
  <img id="pageImage" alt="Manga page" hidden>
  <button class="tap" id="leftTap" aria-label="Left side page turn"></button>
  <button class="tap" id="rightTap" aria-label="Right side page turn"></button>
 </div>
 <div class="hint">Use ← → to turn pages. Press F for fullscreen. Tap the sides of the page to turn.</div>
</main>
<script>
const jobId = {{ job_id|tojson }};
const chapters = {{ chapters|tojson }};
const select = document.getElementById("chapterSelect");
const direction = document.getElementById("direction");
const img = document.getElementById("pageImage");
const message = document.getElementById("message");
const counter = document.getElementById("counter");
const reader = document.getElementById("reader");
let pages = [], index = 0;

for (const chapter of chapters) {
  const option = document.createElement("option");
  option.value = chapter.number;
  option.textContent = "Chapter " + chapter.number;
  select.appendChild(option);
}

function renderPage() {
  if (!pages.length) {
    img.hidden = true;
    message.hidden = false;
    message.textContent = "No readable pages were found in this chapter.";
    counter.textContent = "Page 0 / 0";
    return;
  }
  index = Math.max(0, Math.min(index, pages.length - 1));
  img.hidden = true;
  img.onload = () => { img.hidden = false; message.hidden = true; };
  img.onerror = () => {
    img.hidden = true;
    message.hidden = false;
    message.textContent = "This image could not be loaded.";
  };
  img.src = pages[index];
  message.hidden = false;
  message.textContent = "Loading page...";
  counter.textContent = "Page " + (index + 1) + " / " + pages.length;
}

async function loadChapter(number) {
  pages = [];
  index = 0;
  img.hidden = true;
  message.hidden = false;
  message.textContent = "Loading chapter " + number + "...";
  try {
    const response = await fetch(
      "/reader-pages/" + encodeURIComponent(jobId) + "/" + encodeURIComponent(number),
      {cache:"no-store"}
    );
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load chapter.");
    pages = data.pages || [];
    renderPage();
  } catch (e) {
    message.textContent = e.message;
    counter.textContent = "Unavailable";
  }
}
function turn(amount) {
  if (!pages.length) return;
  index += amount;
  renderPage();
}
function previousPage() { turn(direction.value === "rtl" ? 1 : -1); }
function nextPage() { turn(direction.value === "rtl" ? -1 : 1); }

document.getElementById("previous").onclick = previousPage;
document.getElementById("next").onclick = nextPage;
document.getElementById("leftTap").onclick = () => direction.value === "rtl" ? nextPage() : previousPage();
document.getElementById("rightTap").onclick = () => direction.value === "rtl" ? previousPage() : nextPage();
document.getElementById("first").onclick = () => { index = direction.value === "rtl" ? pages.length - 1 : 0; renderPage(); };
document.getElementById("last").onclick = () => { index = direction.value === "rtl" ? 0 : pages.length - 1; renderPage(); };
select.onchange = () => loadChapter(select.value);

document.getElementById("fullscreen").onclick = async () => {
  try {
    if (!document.fullscreenElement) await reader.requestFullscreen();
    else await document.exitFullscreen();
  } catch (e) {
    message.hidden = false;
    message.textContent = "Fullscreen is unavailable in this browser.";
  }
};

document.addEventListener("keydown", event => {
  if (event.target.matches("select,input,textarea")) return;
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    direction.value === "rtl" ? nextPage() : previousPage();
  }
  if (event.key === "ArrowRight") {
    event.preventDefault();
    direction.value === "rtl" ? previousPage() : nextPage();
  }
  if (event.key.toLowerCase() === "f") document.getElementById("fullscreen").click();
});

if (chapters.length) {
  select.value = String(chapters[0].number);
  loadChapter(select.value);
} else {
  message.textContent = "No chapters are available to read.";
}
</script>
</body>
</html>
"""


# ============================================================
# VALIDATION AND JOB HELPERS
# ============================================================

def safe_name(value):
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value)).strip("._-")[:100] or "download"


def validate_domain(raw):
    raw = str(raw or "").strip().lower()

    if not raw or "://" in raw or "/" in raw or "@" in raw or ":" in raw:
        raise ValueError("Enter only a public domain, such as mangafish.cc.")

    if not re.fullmatch(r"[a-z0-9.-]+", raw) or "." not in raw:
        raise ValueError("Enter a valid public domain.")

    if raw.startswith(".") or raw.endswith(".") or ".." in raw:
        raise ValueError("Enter a valid domain.")

    try:
        addresses = socket.getaddrinfo(raw, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError("The domain could not be resolved.")

    for item in addresses:
        ip = ipaddress.ip_address(item[4][0])
        if not ip.is_global:
            raise ValueError("The domain resolves to a private/non-public address.")

    return raw


def validate_slug(raw):
    slug = str(raw or "").strip().strip("/")
    if not slug or len(slug) > 150:
        raise ValueError("Enter a valid manga slug.")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", slug):
        raise ValueError("The slug may contain letters, numbers, dots, underscores, and hyphens.")
    return slug


def chapter_url(domain, manga, chapter, variant=None):
    suffix = f"-{variant}" if variant else ""
    return f"https://{domain}/manga/{manga}/chapter-{chapter:03d}{suffix}"


def log_message(job_id, message):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job["log"].append({"time": time.strftime("%H:%M:%S"), "message": str(message)[:1000]})
        job["log"] = job["log"][-500:]


def update_job(job_id, **fields):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(fields)


def increment_job(job_id, **fields):
    with jobs_lock:
        job = jobs.get(job_id)
        if job:
            for key, amount in fields.items():
                job[key] = job.get(key, 0) + amount


def job_snapshot(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return None
        result = dict(job)
        result["log"] = list(job["log"])

    elapsed = max(0, time.time() - result["started_at"])
    result["elapsed"] = round(elapsed, 1)
    completed = result["completed"]
    total = result["total"]

    if completed and total > completed:
        result["eta"] = round((elapsed / completed) * (total - completed))
    elif total and completed >= total:
        result["eta"] = 0
    else:
        result["eta"] = None

    return result


# ============================================================
# CHAPTER SCANNING AND IMAGE DOWNLOAD
# ============================================================

def srcset_urls(srcset, base_url):
    found = []
    for part in (srcset or "").split(","):
        candidate = part.strip().split()
        if candidate:
            found.append(urljoin(base_url, candidate[0]))
    return found


async def discover_images(page, url, job_id):
    found = []

    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if response and response.status >= 400:
            log_message(job_id, f"HTTP {response.status}: {url}")
            return found

        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        for _ in range(18):
            await page.evaluate("window.scrollBy(0, Math.max(window.innerHeight, 700))")
            await page.wait_for_timeout(250)

        await page.evaluate("window.scrollTo(0,0)")
        await page.wait_for_timeout(400)

        rows = await page.locator("img").evaluate_all("""
            els => els.map(img => ({
              src: img.getAttribute('src'),
              lazy: img.getAttribute('data-src'),
              original: img.getAttribute('data-original'),
              lazy2: img.getAttribute('data-lazy-src'),
              url: img.getAttribute('data-url'),
              srcset: img.getAttribute('srcset'),
              current: img.currentSrc,
              width: img.naturalWidth || img.width || 0,
              height: img.naturalHeight || img.height || 0
            }))
        """)

        for row in rows:
            width = int(row.get("width") or 0)
            height = int(row.get("height") or 0)

            if width and height and (width < 80 or height < 80):
                continue

            for candidate in (
                row.get("original"), row.get("lazy2"), row.get("lazy"),
                row.get("url"), row.get("src"), row.get("current")
            ):
                if not candidate or str(candidate).startswith(("data:", "blob:", "javascript:")):
                    continue

                absolute = urljoin(url, str(candidate))
                if urlparse(absolute).scheme in ("http", "https"):
                    found.append(absolute)

            found.extend(srcset_urls(row.get("srcset"), url))

    except Exception as exc:
        log_message(job_id, f"Could not scan chapter page: {str(exc)[:180]}")

    # Keep the original page order while removing duplicate URLs.
    return list(dict.fromkeys(found))[:MAX_IMAGES]


async def collect_chapter(context, chapter, domain, manga, job_id, folder):
    page = await context.new_page()
    image_urls = []
    seen = set()

    try:
        variants = [
            chapter_url(domain, manga, chapter, variant=2),
            chapter_url(domain, manga, chapter),
        ]

        for url in variants:
            update_job(job_id, current_chapter=f"Chapter {chapter}: scanning")
            log_message(job_id, f"Scanning {url}")

            urls = await discover_images(page, url, job_id)
            added = 0

            for image_url in urls:
                if image_url not in seen:
                    seen.add(image_url)
                    image_urls.append((image_url, url))
                    added += 1

            increment_job(job_id, image_target=added)
            log_message(job_id, f"Chapter {chapter}: discovered {len(urls)} URLs; {added} new.")

        saved = 0

        for index, (image_url, referer) in enumerate(image_urls, 1):
            update_job(job_id, current_chapter=f"Chapter {chapter}: image {index}/{len(image_urls)}")
            target = folder / f"{index:04d}.jpg"

            try:
                response = await context.request.get(
                    image_url,
                    headers={
                        "Referer": referer,
                        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    },
                    timeout=25000,
                )

                if not response.ok:
                    increment_job(job_id, image_errors=1)
                    continue

                body = await response.body()
                if not body or len(body) > MAX_IMAGE_BYTES:
                    increment_job(job_id, image_errors=1)
                    continue

                try:
                    with Image.open(io.BytesIO(body)) as image:
                        image.load()

                        if image.width < 80 or image.height < 80:
                            increment_job(job_id, image_errors=1)
                            continue

                        if image.mode in ("RGBA", "LA"):
                            rgba = image.convert("RGBA")
                            background = Image.new("RGB", rgba.size, "white")
                            background.paste(rgba, mask=rgba.getchannel("A"))
                            converted = background
                        else:
                            converted = image.convert("RGB")

                        converted.save(target, "JPEG", quality=94, optimize=True)

                except Exception:
                    increment_job(job_id, image_errors=1)
                    continue

                saved += 1
                increment_job(job_id, images=1, pages=1, bytes=len(body))

            except Exception as exc:
                increment_job(job_id, image_errors=1)
                log_message(job_id, f"Image error: {str(exc)[:160]}")

        log_message(job_id, f"Chapter {chapter}: saved {saved} images.")
        return saved

    finally:
        await page.close()


# ============================================================
# PDF AND ZIP CREATION
# ============================================================

def image_files(folder):
    return sorted(folder.glob("*.jpg"), key=lambda p: p.name)


def make_pdf(paths, destination):
    images = []

    try:
        for path in paths:
            try:
                with Image.open(path) as source:
                    source.load()
                    images.append(source.convert("RGB"))
            except Exception:
                continue

        if not images:
            return 0

        first, *rest = images
        first.save(destination, "PDF", save_all=True, append_images=rest, resolution=150.0)
        return len(images)

    finally:
        for image in images:
            image.close()


async def run_browser_job(job_id, domain, manga, first, last, work_dir):
    chapter_pdfs = []
    all_images = []

    update_job(job_id, state="running", status="Running")
    log_message(job_id, "Launching Playwright Chromium.")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/132.0.0.0 Safari/537.36"
            ),
        )

        try:
            for chapter in range(first, last + 1):
                update_job(job_id, current_chapter=f"Chapter {chapter}")
                folder = work_dir / f"chapter_{chapter:04d}"
                folder.mkdir(parents=True, exist_ok=True)

                try:
                    saved = await collect_chapter(
                        context, chapter, domain, manga, job_id, folder
                    )

                    if saved == 0:
                        increment_job(job_id, failed=1)
                        log_message(job_id, f"Chapter {chapter} failed: no usable images.")
                    else:
                        paths = image_files(folder)
                        all_images.extend(paths)
                        pdf_path = work_dir / f"{safe_name(manga)}_chapter_{chapter:04d}.pdf"
                        count = make_pdf(paths, pdf_path)

                        if count:
                            chapter_pdfs.append(pdf_path)
                            log_message(job_id, f"Created chapter PDF: {count} pages.")

                except Exception as exc:
                    increment_job(job_id, failed=1)
                    log_message(job_id, f"Chapter {chapter} failed: {str(exc)[:200]}")

                finally:
                    increment_job(job_id, completed=1)

        finally:
            await context.close()
            await browser.close()

    combined = work_dir / f"{safe_name(manga)}_combined.pdf"

    if all_images:
        update_job(job_id, current_chapter="Creating combined PDF")
        count = make_pdf(all_images, combined)
        log_message(job_id, f"Combined PDF created: {count} pages.")

    if not chapter_pdfs and not combined.exists():
        raise RuntimeError(
            "No PDFs could be created. The site may have blocked the request "
            "or its chapter layout may have changed."
        )

    update_job(job_id, current_chapter="Creating ZIP archive")
    zip_path = OUTPUT_DIR / f"PanelPress_{job_id}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for pdf in chapter_pdfs:
            archive.write(pdf, pdf.name)

        if combined.exists():
            archive.write(combined, combined.name)

        snapshot = job_snapshot(job_id) or {}
        summary = {
            "manga": manga,
            "website": domain,
            "first_chapter": first,
            "last_chapter": last,
            "chapters_completed": snapshot.get("completed", 0),
            "failed_chapters": snapshot.get("failed", 0),
            "images_downloaded": snapshot.get("images", 0),
            "pdf_pages": snapshot.get("pages", 0),
            "image_errors": snapshot.get("image_errors", 0),
        }
        archive.writestr("PanelPress_summary.json", json.dumps(summary, indent=2))

    update_job(
        job_id,
        state="complete",
        status="Complete",
        current_chapter="Finished",
        download_path=str(zip_path),
        chapter_pdfs=[str(p) for p in chapter_pdfs],
    )
    log_message(job_id, "Finished! Your ZIP and reader are ready.")


def worker(job_id, domain, manga, first, last):
    work_dir = OUTPUT_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        log_message(job_id, "Waiting for download worker.")
        with job_semaphore:
            asyncio.run(
                run_browser_job(job_id, domain, manga, first, last, work_dir)
            )
    except Exception as exc:
        message = str(exc)[:1000]
        update_job(
            job_id,
            state="error",
            status="Error",
            error=message,
            current_chapter="Stopped because of an error",
        )
        log_message(job_id, f"Job failed: {message}")


# ============================================================
# FLASK ROUTES
# ============================================================

@APP.route("/")
def index():
    return render_template_string(PAGE)


@APP.route("/create", methods=["POST"])
def create_job():
    data = request.get_json(silent=True) or {}

    try:
        domain = validate_domain(data.get("website"))
        manga = validate_slug(data.get("manga"))
        first = int(data.get("first", 1))
        last = int(data.get("last", first))

        if first < 1 or last < first:
            raise ValueError("Enter a valid chapter range.")
        if last - first + 1 > MAX_CHAPTERS:
            raise ValueError(f"Maximum {MAX_CHAPTERS} chapters per job.")

    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400

    job_id = uuid.uuid4().hex[:12]

    job = {
        "job_id": job_id,
        "state": "queued",
        "status": "Queued",
        "completed": 0,
        "total": last - first + 1,
        "images": 0,
        "image_target": 0,
        "pages": 0,
        "failed": 0,
        "bytes": 0,
        "image_errors": 0,
        "started_at": time.time(),
        "current_chapter": "Waiting for worker",
        "log": [],
        "error": None,
        "download_path": None,
        "chapter_pdfs": [],
    }

    with jobs_lock:
        jobs[job_id] = job

    log_message(job_id, f"Created job for {manga}, chapters {first}–{last}.")
    thread = threading.Thread(
        target=worker,
        args=(job_id, domain, manga, first, last),
        daemon=True,
    )
    thread.start()

    return jsonify({"job_id": job_id, "state": "queued"})


@APP.route("/progress/<job_id>")
def progress(job_id):
    data = job_snapshot(job_id)
    if not data:
        return jsonify({"error": "Job not found."}), 404

    download_url = f"/download/{job_id}" if data["state"] == "complete" else None
    data.pop("download_path", None)
    data.pop("chapter_pdfs", None)
    data["download_url"] = download_url
    return jsonify(data)


@APP.route("/download/<job_id>")
def download(job_id):
    data = job_snapshot(job_id)
    if not data or data.get("state") != "complete":
        abort(404)

    path = data.get("download_path")
    if not path or not Path(path).is_file():
        abort(404)

    return send_file(
        path,
        as_attachment=True,
        download_name=f"PanelPress_{job_id}.zip",
        mimetype="application/zip",
    )


@APP.route("/reader/<job_id>")
def manga_reader(job_id):
    data = job_snapshot(job_id)
    if not data:
        abort(404)
    if data.get("state") != "complete":
        return "This download is not finished yet.", 409

    folder = OUTPUT_DIR / job_id
    chapters = []

    if folder.is_dir():
        for chapter_folder in folder.iterdir():
            match = re.fullmatch(r"chapter_(\d+)", chapter_folder.name)
            if not match or not chapter_folder.is_dir():
                continue

            pages = image_files(chapter_folder)
            if pages:
                chapters.append({
                    "number": int(match.group(1)),
                    "pages": len(pages),
                })

    chapters.sort(key=lambda item: item["number"])

    return render_template_string(
        READER_PAGE,
        job_id=job_id,
        chapters=chapters,
    )


@APP.route("/reader-pages/<job_id>/<int:chapter>")
def reader_pages(job_id, chapter):
    data = job_snapshot(job_id)
    if not data or data.get("state") != "complete":
        return jsonify({"error": "Completed job not found."}), 404
    if chapter < 1:
        return jsonify({"error": "Invalid chapter."}), 400

    folder = OUTPUT_DIR / job_id / f"chapter_{chapter:04d}"
    if not folder.is_dir():
        return jsonify({"error": "Chapter not found."}), 404

    pages = [
        f"/reader-image/{job_id}/{chapter}/{path.name}"
        for path in image_files(folder)
        if path.is_file()
    ]
    return jsonify({"pages": pages})


@APP.route("/reader-image/<job_id>/<int:chapter>/<filename>")
def reader_image(job_id, chapter, filename):
    if not re.fullmatch(r"\d{4}\.jpg", filename):
        abort(404)

    data = job_snapshot(job_id)
    if not data or data.get("state") != "complete" or chapter < 1:
        abort(404)

    path = OUTPUT_DIR / job_id / f"chapter_{chapter:04d}" / filename
    if not path.is_file():
        abort(404)

    return send_file(
        path,
        mimetype="image/jpeg",
        conditional=True,
        max_age=3600,
    )


# ============================================================
# SERVER START
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    APP.run(host="0.0.0.0", port=port, debug=False, threaded=True)
```
