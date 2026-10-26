```python
import asyncio
import io
import json
import os
import re
import threading
import time
import uuid
import zipfile
import ipaddress
import socket

from pathlib import Path
from urllib.parse import urlparse, urljoin

from flask import (
    Flask,
    request,
    jsonify,
    render_template_string,
    send_file,
    abort,
)

from PIL import Image, ImageFile
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

APP = Flask(__name__)

APP.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "panelpress_output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_CHAPTERS = 1000
MAX_IMAGES_PER_CHAPTER = 200
MAX_IMAGE_BYTES = 25 * 1024 * 1024

# Be tolerant of slightly damaged images.
ImageFile.LOAD_TRUNCATED_IMAGES = True

# Avoid accepting an arbitrarily large image decompression.
Image.MAX_IMAGE_PIXELS = 100_000_000

jobs = {}
jobs_lock = threading.Lock()

# Limit concurrent jobs to avoid exhausting server memory.
job_semaphore = threading.Semaphore(1)


# ============================================================
# HTML DASHBOARD
# ============================================================

PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>PanelPress — Manga Downloader</title>

<style>
:root {
    --bg: #090d18;
    --panel: #111827;
    --panel2: #172033;
    --border: #273449;
    --text: #edf2ff;
    --muted: #94a3b8;
    --accent: #8b5cf6;
    --accent2: #6366f1;
    --green: #34d399;
    --red: #fb7185;
    --yellow: #fbbf24;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    min-height: 100vh;
    padding: 30px 16px;
    color: var(--text);
    background:
        radial-gradient(ellipse at top, #1d2446 0%, transparent 48%),
        var(--bg);
    font-family: Inter, system-ui, -apple-system, Segoe UI, sans-serif;
}

.container {
    width: min(1120px, 100%);
    margin: auto;
}

header {
    margin-bottom: 28px;
}

.logo {
    display: flex;
    align-items: center;
    gap: 13px;
}

.logo-icon {
    width: 50px;
    height: 50px;
    display: grid;
    place-items: center;
    font-size: 25px;
    border-radius: 16px;
    background: linear-gradient(135deg, var(--accent), #4338ca);
    box-shadow: 0 8px 35px #8b5cf633;
}

h1 {
    margin: 0;
    font-size: clamp(25px, 5vw, 35px);
    letter-spacing: -1px;
}

.subtitle {
    color: var(--muted);
    margin: 5px 0 0;
    font-size: 14px;
}

.panel {
    background: linear-gradient(145deg, #141d30, #0f1726);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 23px;
    margin-bottom: 20px;
    box-shadow: 0 15px 40px #00000016;
}

h2 {
    margin: 0 0 18px;
    font-size: 19px;
}

label {
    display: block;
    font-size: 13px;
    color: #cbd5e1;
    margin-bottom: 8px;
}

input {
    width: 100%;
    color: var(--text);
    background: #090f1c;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 13px 14px;
    font: inherit;
    outline: none;
    transition: border-color .2s, box-shadow .2s;
}

input:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px #8b5cf622;
}

.form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 17px;
    margin-bottom: 17px;
}

.field {
    min-width: 0;
}

.full {
    grid-column: 1 / -1;
}

.hint {
    color: var(--muted);
    font-size: 12px;
    margin-top: 7px;
    line-height: 1.5;
}

button, .download {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    border: 0;
    border-radius: 11px;
    padding: 13px 19px;
    font-size: 14px;
    font-weight: 750;
    cursor: pointer;
    text-decoration: none;
    transition: filter .2s, transform .2s;
}

button:hover, .download:hover {
    filter: brightness(1.12);
    transform: translateY(-1px);
}

button:disabled {
    opacity: .55;
    cursor: not-allowed;
    transform: none;
}

.primary {
    color: white;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    width: 100%;
}

.secondary {
    color: white;
    background: #29364d;
}

.status-line {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 18px;
}

.status-pill {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 7px 11px;
    border-radius: 99px;
    background: #1e293b;
    color: #cbd5e1;
    font-size: 12px;
    font-weight: 700;
}

.status-dot {
    width: 7px;
    height: 7px;
    background: var(--yellow);
    border-radius: 50%;
}

.status-dot.running {
    background: var(--green);
    box-shadow: 0 0 12px #34d39980;
}

.status-dot.error {
    background: var(--red);
}

.stats-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin-bottom: 23px;
}

.stat {
    min-width: 0;
    padding: 15px;
    background: #0a1120;
    border: 1px solid #253148;
    border-radius: 13px;
}

.stat-label {
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 9px;
}

.stat-value {
    font-size: clamp(19px, 3vw, 26px);
    font-weight: 800;
    overflow-wrap: anywhere;
}

.stat-sub {
    margin-top: 5px;
    color: #64748b;
    font-size: 11px;
}

.section-title {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    align-items: center;
    margin-bottom: 10px;
}

.section-title h3 {
    margin: 0;
    font-size: 14px;
}

.percentage {
    color: #c4b5fd;
    font-size: 13px;
    font-weight: 800;
}

.progress-track {
    width: 100%;
    height: 12px;
    border-radius: 99px;
    background: #080d17;
    border: 1px solid #273449;
    overflow: hidden;
}

.progress-fill {
    width: 0%;
    height: 100%;
    border-radius: 99px;
    background: linear-gradient(90deg, var(--accent), #a78bfa);
    transition: width .3s ease;
}

.progress-fill.images {
    background: linear-gradient(90deg, #059669, #34d399);
}

.progress-block {
    margin-bottom: 22px;
}

.progress-description {
    color: var(--muted);
    font-size: 12px;
    margin-top: 8px;
}

.current-box {
    border: 1px solid #303c56;
    background: #0b1220;
    border-radius: 12px;
    padding: 14px;
    margin: 22px 0;
}

.current-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--muted);
    margin-bottom: 7px;
}

.current-value {
    overflow-wrap: anywhere;
    font-weight: 700;
    font-size: 14px;
}

.log {
    background: #070c15;
    border: 1px solid #273449;
    border-radius: 12px;
    height: 240px;
    overflow-y: auto;
    padding: 13px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    line-height: 1.75;
}

.log-entry {
    overflow-wrap: anywhere;
    color: #a5b4fc;
}

.log-entry.error {
    color: #fda4af;
}

.log-entry.success {
    color: #6ee7b7;
}

.log-time {
    color: #64748b;
}

.actions {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-top: 18px;
}

.download {
    color: #052e22;
    background: var(--green);
}

.error-message {
    display: none;
    border: 1px solid #9f1239;
    background: #4c0519;
    color: #fecdd3;
    border-radius: 11px;
    padding: 13px;
    margin-top: 16px;
    font-size: 13px;
    overflow-wrap: anywhere;
}

.footer {
    color: #64748b;
    text-align: center;
    font-size: 12px;
    padding: 12px;
}

.hidden {
    display: none !important;
}

@media (max-width: 760px) {
    .stats-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .panel {
        padding: 17px;
    }
}

@media (max-width: 480px) {
    .form-grid {
        grid-template-columns: 1fr;
    }

    .full {
        grid-column: auto;
    }

    .actions > * {
        width: 100%;
    }
}
</style>
</head>

<body>
<div class="container">

<header>
    <div class="logo">
        <div class="logo-icon">📚</div>
        <div>
            <h1>PanelPress</h1>
            <p class="subtitle">Manga chapters → images → PDF → ZIP</p>
        </div>
    </div>
</header>

<section class="panel">
    <h2>⚙️ Download settings</h2>

    <form id="downloadForm">
        <div class="form-grid">

            <div class="field full">
                <label for="website">Website domain</label>
                <input id="website"
                       name="website"
                       value="mangafish.cc"
                       placeholder="example.com"
                       required>
                <div class="hint">
                    Enter the website's domain only, without a URL path.
                </div>
            </div>

            <div class="field full">
                <label for="manga">Manga slug</label>
                <input id="manga"
                       name="manga"
                       value="blue-lock"
                       placeholder="blue-lock"
                       required>
            </div>

            <div class="field">
                <label for="first">First chapter</label>
                <input id="first"
                       name="first"
                       type="number"
                       min="1"
                       max="999999"
                       value="1"
                       required>
            </div>

            <div class="field">
                <label for="last">Last chapter</label>
                <input id="last"
                       name="last"
                       type="number"
                       min="1"
                       max="999999"
                       value="10"
                       required>
            </div>

        </div>

        <button class="primary" id="startButton" type="submit">
            ▶ Start download
        </button>

        <div id="formError" class="error-message"></div>
    </form>
</section>

<section class="panel">
    <div class="status-line">
        <h2 style="margin:0">📊 Download dashboard</h2>
        <div class="status-pill">
            <span class="status-dot" id="statusDot"></span>
            <span id="statusText">Ready</span>
        </div>
    </div>

    <div class="stats-grid">
        <div class="stat">
            <div class="stat-label">Chapters completed</div>
            <div class="stat-value" id="completed">0</div>
            <div class="stat-sub" id="chapterSub">of 0 chapters</div>
        </div>

        <div class="stat">
            <div class="stat-label">Images downloaded</div>
            <div class="stat-value" id="images">0</div>
            <div class="stat-sub" id="imageSub">0 found so far</div>
        </div>

        <div class="stat">
            <div class="stat-label">PDF pages</div>
            <div class="stat-value" id="pages">0</div>
            <div class="stat-sub">Image pages created</div>
        </div>

        <div class="stat">
            <div class="stat-label">Failed chapters</div>
            <div class="stat-value" id="failed">0</div>
            <div class="stat-sub">Chapters without usable images</div>
        </div>

        <div class="stat">
            <div class="stat-label">Downloaded size</div>
            <div class="stat-value" id="bytes">0 B</div>
            <div class="stat-sub">Successful image downloads</div>
        </div>

        <div class="stat">
            <div class="stat-label">Elapsed time</div>
            <div class="stat-value" id="elapsed">00:00</div>
            <div class="stat-sub">Time since job started</div>
        </div>

        <div class="stat">
            <div class="stat-label">Estimated remaining</div>
            <div class="stat-value" id="eta">—</div>
            <div class="stat-sub">Based on completed chapters</div>
        </div>

        <div class="stat">
            <div class="stat-label">Image errors</div>
            <div class="stat-value" id="imageErrors">0</div>
            <div class="stat-sub">Images that could not be saved</div>
        </div>
    </div>

    <div class="progress-block">
        <div class="section-title">
            <h3>Chapter progress</h3>
            <span class="percentage" id="chapterPercent">0%</span>
        </div>
        <div class="progress-track">
            <div class="progress-fill" id="progressBar"></div>
        </div>
        <div class="progress-description" id="chapterDescription">
            Waiting for a job.
        </div>
    </div>

    <div class="progress-block">
        <div class="section-title">
            <h3>Image progress</h3>
            <span class="percentage" id="imagePercent">0%</span>
        </div>
        <div class="progress-track">
            <div class="progress-fill images" id="imageProgressBar"></div>
        </div>
        <div class="progress-description" id="imageDescription">
            Images found and downloaded will appear here.
        </div>
    </div>

    <div class="current-box">
        <div class="current-label">Currently processing</div>
        <div class="current-value" id="currentChapter">
            No chapter selected
        </div>
    </div>

    <h3 style="font-size:14px; margin-bottom:10px;">Activity log</h3>
    <div class="log" id="log" aria-live="polite">
        <div class="log-entry">
            <span class="log-time">[PanelPress]</span>
            Ready to start.
        </div>
    </div>

    <div class="actions">
        <a class="download hidden" id="downloadLink" href="#">
            ⬇ Download ZIP
        </a>
        <button class="secondary" id="clearLog" type="button">
            Clear log
        </button>
    </div>

    <div id="jobError" class="error-message"></div>
</section>

<div class="footer">
    PanelPress · Progress updates automatically · Keep this page open while downloading
</div>

</div>

<script>
const form = document.getElementById("downloadForm");
const startButton = document.getElementById("startButton");
const formError = document.getElementById("formError");
const jobError = document.getElementById("jobError");
const logElement = document.getElementById("log");

let currentJobId = null;
let pollTimer = null;
let seenLogCount = 0;
let isRunning = false;
let hasAutoDownloaded = false;

function setText(id, value) {
    document.getElementById(id).textContent = value;
}

function showError(element, message) {
    element.textContent = message;
    element.style.display = "block";
}

function hideError(element) {
    element.textContent = "";
    element.style.display = "none";
}

function formatBytes(bytes) {
    bytes = Number(bytes) || 0;

    if (bytes < 1024) return bytes + " B";

    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024;
    let index = 0;

    while (value >= 1024 && index < units.length - 1) {
        value /= 1024;
        index++;
    }

    return value.toFixed(value < 10 ? 2 : 1) + " " + units[index];
}

function formatTime(seconds) {
    if (seconds === null || seconds === undefined ||
        !Number.isFinite(Number(seconds))) {
        return "—";
    }

    seconds = Math.max(0, Math.floor(Number(seconds)));

    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;

    if (hours > 0) {
        return String(hours).padStart(2, "0") + ":" +
               String(minutes).padStart(2, "0") + ":" +
               String(secs).padStart(2, "0");
    }

    return String(minutes).padStart(2, "0") + ":" +
           String(secs).padStart(2, "0");
}

function addLog(entry) {
    const row = document.createElement("div");
    row.className = "log-entry";

    if (/error|failed|blocked/i.test(entry.message)) {
        row.classList.add("error");
    } else if (/complete|finished|success|created/i.test(entry.message)) {
        row.classList.add("success");
    }

    const stamp = document.createElement("span");
    stamp.className = "log-time";
    stamp.textContent = "[" + (entry.time || "Info") + "] ";

    row.appendChild(stamp);
    row.appendChild(document.createTextNode(entry.message || ""));

    logElement.appendChild(row);
    logElement.scrollTop = logElement.scrollHeight;
}

function updateLog(entries) {
    if (!Array.isArray(entries)) return;

    if (entries.length < seenLogCount) {
        logElement.replaceChildren();
        seenLogCount = 0;
    }

    for (let i = seenLogCount; i < entries.length; i++) {
        addLog(entries[i]);
    }

    seenLogCount = entries.length;
}

function setStatus(status) {
    setText("statusText", status);

    const dot = document.getElementById("statusDot");
    dot.className = "status-dot";

    if (status === "Running" || status === "Preparing") {
        dot.classList.add("running");
    } else if (status === "Error") {
        dot.classList.add("error");
    }
}

function showJob(data) {
    const total = Number(data.total) || 0;
    const completed = Number(data.completed) || 0;
    const images = Number(data.images) || 0;
    const imageTarget = Number(data.image_target) || 0;

    const chapterPercent = total > 0
        ? Math.min(100, completed / total * 100)
        : 0;

    const imagePercent = imageTarget > 0
        ? Math.min(100, images / imageTarget * 100)
        : 0;

    document.getElementById("progressBar").style.width =
        chapterPercent + "%";

    document.getElementById("imageProgressBar").style.width =
        imagePercent + "%";

    setText("chapterPercent", Math.floor(chapterPercent) + "%");
    setText("imagePercent", Math.floor(imagePercent) + "%");

    setText("completed", completed);
    setText("chapterSub", "of " + total + " chapters");

    setText("images", images);
    setText("imageSub", imageTarget
        ? imageTarget + " found/expected"
        : "Discovering images");

    setText("pages", Number(data.pages) || 0);
    setText("failed", Number(data.failed) || 0);
    setText("bytes", formatBytes(data.bytes));
    setText("elapsed", formatTime(data.elapsed));
    setText("eta", data.eta === null ? "—" : formatTime(data.eta));
    setText("imageErrors", Number(data.image_errors) || 0);

    setText(
        "chapterDescription",
        completed + " of " + total + " chapters completed"
    );

    setText(
        "imageDescription",
        images + " images downloaded" +
        (imageTarget ? " out of approximately " + imageTarget : "")
    );

    setText(
        "currentChapter",
        data.current_chapter || "No chapter selected"
    );

    updateLog(data.log);

    if (data.state === "running") {
        setStatus("Running");
    } else if (data.state === "complete") {
        setStatus("Complete");
    } else if (data.state === "error") {
        setStatus("Error");
    } else {
        setStatus(data.status || "Ready");
    }

    if (data.state === "complete") {
        isRunning = false;
        startButton.disabled = false;
        startButton.textContent = "▶ Start another download";

        if (data.download_url) {
            const link = document.getElementById("downloadLink");
            link.href = data.download_url;
            link.classList.remove("hidden");

            if (!hasAutoDownloaded) {
                hasAutoDownloaded = true;

                // Show the download link instead of forcing a download.
                // This avoids repeated downloads if the page reconnects.
            }
        }
    }

    if (data.state === "error") {
        isRunning = false;
        startButton.disabled = false;
        startButton.textContent = "▶ Try again";

        if (data.error) {
            showError(jobError, data.error);
        }
    }
}

async function pollJob() {
    if (!currentJobId) return;

    try {
        const response = await fetch(
            "/progress/" + encodeURIComponent(currentJobId),
            {cache: "no-store"}
        );

        if (!response.ok) {
            throw new Error("Could not retrieve job progress.");
        }

        const data = await response.json();
        showJob(data);

        if (data.state === "complete" ||
            data.state === "error") {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    } catch (error) {
        showError(jobError, error.message || "Progress update failed.");
    }
}

form.addEventListener("submit", async function(event) {
    event.preventDefault();

    if (isRunning) return;

    hideError(formError);
    hideError(jobError);

    const first = Number(document.getElementById("first").value);
    const last = Number(document.getElementById("last").value);

    if (!Number.isInteger(first) ||
        !Number.isInteger(last) ||
        first < 1 ||
        last < first) {
        showError(formError, "Enter a valid chapter range.");
        return;
    }

    if (last - first + 1 > 1000) {
        showError(formError, "You can request up to 1,000 chapters per job.");
        return;
    }

    startButton.disabled = true;
    startButton.textContent = "Starting...";
    isRunning = true;
    hasAutoDownloaded = false;
    seenLogCount = 0;
    logElement.replaceChildren();

    document.getElementById("downloadLink").classList.add("hidden");

    try {
        const response = await fetch("/create", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                website: document.getElementById("website").value,
                manga: document.getElementById("manga").value,
                first: first,
                last: last
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Could not start the job.");
        }

        currentJobId = data.job_id;
        startButton.textContent = "Downloading...";
        setStatus("Preparing");

        await pollJob();

        if (pollTimer) clearInterval(pollTimer);

        pollTimer = setInterval(pollJob, 1000);
    } catch (error) {
        isRunning = false;
        startButton.disabled = false;
        startButton.textContent = "▶ Start download";
        showError(formError, error.message || "Could not start the job.");
    }
});

document.getElementById("clearLog").addEventListener("click", function() {
    logElement.replaceChildren();
    seenLogCount = 0;
});
</script>
</body>
</html>
"""


# ============================================================
# VALIDATION
# ============================================================

def safe_name(value):
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value))
    return value.strip("._-")[:100] or "download"


def validate_domain(raw_domain):
    """
    Accept a public domain only.
    This app intentionally does not accept full URLs, local addresses,
    private IP addresses, or arbitrary URL paths.
    """
    raw_domain = str(raw_domain or "").strip().lower()

    if not raw_domain:
        raise ValueError("Enter a website domain.")

    if "://" in raw_domain or "/" in raw_domain or "@" in raw_domain:
        raise ValueError(
            "Enter only a domain, such as mangafish.cc, without https:// or a path."
        )

    if ":" in raw_domain or "\\" in raw_domain:
        raise ValueError("Ports and backslashes are not allowed in the domain.")

    if len(raw_domain) > 253:
        raise ValueError("The domain is too long.")

    try:
        parsed = urlparse("https://" + raw_domain)
        host = parsed.hostname
    except Exception:
        host = None

    if not host or "." not in host:
        raise ValueError("Enter a valid public domain.")

    if host in ("localhost", "localhost.localdomain"):
        raise ValueError("Local addresses are not allowed.")

    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise ValueError("Private or non-public IP addresses are not allowed.")
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
        # Not an IP address, so validate it as a domain below.
    except Exception:
        pass

    if not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ValueError("The domain contains unsupported characters.")

    if host.startswith(".") or host.endswith(".") or ".." in host:
        raise ValueError("Enter a valid domain.")

    # Resolve the domain and reject non-public destinations.
    # This helps prevent access to private/internal network services.
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError("The domain could not be resolved.")

    for address in addresses:
        ip_text = address[4][0]

        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            raise ValueError("The domain resolved to an invalid address.")

        if not ip.is_global:
            raise ValueError(
                "The domain resolves to a private or non-public address."
            )

    return host


def validate_manga_slug(raw_slug):
    slug = str(raw_slug or "").strip().strip("/")

    if not slug:
        raise ValueError("Enter a manga slug.")

    if len(slug) > 150:
        raise ValueError("The manga slug is too long.")

    if not re.fullmatch(r"[A-Za-z0-9._-]+", slug):
        raise ValueError(
            "The manga slug may contain only letters, numbers, dots, underscores, and hyphens."
        )

    return slug


def chapter_url(domain, manga, chapter, variant=None):
    number = f"{chapter:03d}"

    suffix = f"-{variant}" if variant else ""

    return (
        f"https://{domain}/manga/{manga}/chapter-{number}{suffix}"
    )


# ============================================================
# JOB STATE / PROGRESS
# ============================================================

def log_message(job_id, message):
    with jobs_lock:
        job = jobs.get(job_id)

        if not job:
            return

        job["log"].append({
            "time": time.strftime("%H:%M:%S"),
            "message": str(message)[:1000],
        })

        # Keep memory use bounded.
        if len(job["log"]) > 500:
            job["log"] = job["log"][-500:]


def update_job(job_id, **fields):
    with jobs_lock:
        job = jobs.get(job_id)

        if job:
            job.update(fields)


def increment_job(job_id, **increments):
    with jobs_lock:
        job = jobs.get(job_id)

        if not job:
            return

        for key, amount in increments.items():
            job[key] = job.get(key, 0) + amount


def job_snapshot(job_id):
    with jobs_lock:
        job = jobs.get(job_id)

        if not job:
            return None

        result = dict(job)
        result["log"] = list(job["log"])

        elapsed = max(0, time.time() - job["started_at"])
        result["elapsed"] = round(elapsed, 1)

        completed = result["completed"]
        total = result["total"]

        if completed > 0 and total > completed:
            average = elapsed / completed
            result["eta"] = round(average * (total - completed))
        elif total > 0 and completed >= total:
            result["eta"] = 0
        else:
            result["eta"] = None

        return result


# ============================================================
# IMAGE DISCOVERY
# ============================================================

def extract_srcset_urls(srcset, page_url):
    results = []

    if not srcset:
        return results

    # Each entry is typically "URL 800w" or "URL 2x".
    for item in str(srcset).split(","):
        part = item.strip()

        if not part:
            continue

        candidate = part.split()[0].strip()

        if candidate:
            results.append(urljoin(page_url, candidate))

    return results


async def discover_images(page, url, job_id):
    """
    Visit a chapter URL, scroll the page to trigger lazy loading,
    and collect image URLs from common image attributes.
    """
    discovered = []

    try:
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=45000,
        )

        if response and response.status >= 400:
            log_message(
                job_id,
                f"HTTP {response.status} for {url}"
            )
            return discovered

        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        # Scroll to trigger lazy-loaded chapter images.
        for _ in range(18):
            await page.evaluate(
                "window.scrollBy(0, Math.max(window.innerHeight, 700))"
            )
            await page.wait_for_timeout(250)

        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(500)

        image_data = await page.locator("img").evaluate_all("""
            elements => elements.map(img => ({
                src: img.getAttribute("src"),
                dataSrc: img.getAttribute("data-src"),
                dataOriginal: img.getAttribute("data-original"),
                dataLazySrc: img.getAttribute("data-lazy-src"),
                dataUrl: img.getAttribute("data-url"),
                srcset: img.getAttribute("srcset"),
                currentSrc: img.currentSrc,
                width: img.naturalWidth || img.width || 0,
                height: img.naturalHeight || img.height || 0,
                alt: img.getAttribute("alt") || ""
            }))
        """)

        for item in image_data:
            # Prefer lazy-load attributes where present.
            candidates = [
                item.get("dataOriginal"),
                item.get("dataLazySrc"),
                item.get("dataSrc"),
                item.get("dataUrl"),
                item.get("src"),
                item.get("currentSrc"),
            ]

            for candidate in candidates:
                if not candidate:
                    continue

                candidate = str(candidate).strip()

                if candidate.startswith(("data:", "blob:", "javascript:")):
                    continue

                absolute = urljoin(url, candidate)

                if urlparse(absolute).scheme not in ("http", "https"):
                    continue

                # Ignore tiny icons and tracking pixels when dimensions
                # are available. Unknown dimensions are allowed.
                width = int(item.get("width") or 0)
                height = int(item.get("height") or 0)

                if width and height and (width < 80 or height < 80):
                    continue

                discovered.append(absolute)

            discovered.extend(
                extract_srcset_urls(item.get("srcset"), url)
            )

    except Exception as exc:
        log_message(
            job_id,
            f"Could not scan {url}: {str(exc)[:200]}"
        )

    # Preserve the order while removing duplicates.
    seen = set()
    unique = []

    for item in discovered:
        if item not in seen:
            seen.add(item)
            unique.append(item)

    return unique[:MAX_IMAGES_PER_CHAPTER]


async def collect_chapter(
    browser_context,
    chapter,
    domain,
    manga,
    job_id,
    chapter_dir,
):
    """
    Try both chapter URL variants, combine their image URLs,
    download valid images, and return the number of saved images.
    """
    page = await browser_context.new_page()

    image_urls = []
    seen_urls = set()

    variants = [
        chapter_url(domain, manga, chapter, variant=2),
        chapter_url(domain, manga, chapter),
    ]

    try:
        for url in variants:
            update_job(
                job_id,
                current_chapter=f"Chapter {chapter}: scanning page variant"
            )

            log_message(job_id, f"Scanning chapter {chapter}: {url}")

            found = await discover_images(page, url, job_id)

            added = 0

            for image_url in found:
                if image_url not in seen_urls:
                    seen_urls.add(image_url)
                    image_urls.append((image_url, url))
                    added += 1

            log_message(
                job_id,
                f"Chapter {chapter}: found {len(found)} image URLs; "
                f"added {added} new URLs."
            )

            # Report the number discovered so far for the image bar.
            with jobs_lock:
                job = jobs.get(job_id)
                if job:
                    job["image_target"] += added

        if not image_urls:
            log_message(
                job_id,
                f"Chapter {chapter}: no image URLs were found."
            )
            return 0

        saved = 0

        for index, (image_url, referer) in enumerate(image_urls, start=1):
            update_job(
                job_id,
                current_chapter=(
                    f"Chapter {chapter} — image {index}/{len(image_urls)}"
                )
            )

            filename = chapter_dir / f"{index:04d}.jpg"

            try:
                response = await browser_context.request.get(
                    image_url,
                    headers={
                        "Referer": referer,
                        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
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

                # Decode and re-encode the image as JPEG for reliable PDFs.
                try:
                    with Image.open(io.BytesIO(body)) as image:
                        image.load()

                        if image.width < 80 or image.height < 80:
                            increment_job(job_id, image_errors=1)
                            continue

                        if image.mode in ("RGBA", "LA"):
                            rgba = image.convert("RGBA")
                            background = Image.new("RGB", rgba.size, "white")
                            background.paste(
                                rgba,
                                mask=rgba.getchannel("A")
                            )
                            converted = background
                        else:
                            converted = image.convert("RGB")

                        converted.save(
                            filename,
                            format="JPEG",
                            quality=94,
                            optimize=True,
                        )

                except Exception:
                    increment_job(job_id, image_errors=1)
                    continue

                saved += 1

                increment_job(
                    job_id,
                    images=1,
                    pages=1,
                    bytes=len(body),
                )

            except Exception as exc:
                increment_job(job_id, image_errors=1)
                log_message(
                    job_id,
                    f"Image download failed in chapter {chapter}: "
                    f"{str(exc)[:140]}"
                )

        log_message(
            job_id,
            f"Chapter {chapter}: saved {saved} usable images."
        )

        return saved

    finally:
        await page.close()


# ============================================================
# PDF CREATION
# ============================================================

def image_files_in(folder):
    return sorted(
        [
            path for path in folder.glob("*.jpg")
            if path.is_file()
        ],
        key=lambda path: path.name,
    )


def make_pdf(image_paths, output_path):
    """
    Create a PDF with one image per page.
    Returns the number of pages created.
    """
    if not image_paths:
        return 0

    converted_images = []

    try:
        for path in image_paths:
            try:
                with Image.open(path) as image:
                    image.load()
                    converted_images.append(image.convert("RGB"))
            except Exception:
                continue

        if not converted_images:
            return 0

        first, *remaining = converted_images

        first.save(
            output_path,
            "PDF",
            save_all=True,
            append_images=remaining,
            resolution=150.0,
        )

        return len(converted_images)

    finally:
        for image in converted_images:
            try:
                image.close()
            except Exception:
                pass


# ============================================================
# DOWNLOAD WORKER
# ============================================================

async def run_browser_job(job_id, domain, manga, first, last, work_dir):
    chapter_pdf_paths = []
    all_image_paths = []

    update_job(
        job_id,
        state="running",
        status="Running",
    )

    log_message(job_id, "Launching browser.")

    async with async_playwright() as playwright:
        # Uses Playwright's bundled Chromium, not installed Edge.
        browser = await playwright.chromium.launch(
            headless=True
        )

        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            ignore_https_errors=False,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/132.0.0.0 Safari/537.36"
            ),
        )

        try:
            for chapter in range(first, last + 1):
                update_job(
                    job_id,
                    current_chapter=f"Chapter {chapter}",
                )

                log_message(
                    job_id,
                    f"Starting chapter {chapter}."
                )

                chapter_dir = (
                    work_dir / f"chapter_{chapter:04d}"
                )
                chapter_dir.mkdir(parents=True, exist_ok=True)

                try:
                    saved = await collect_chapter(
                        context,
                        chapter,
                        domain,
                        manga,
                        job_id,
                        chapter_dir,
                    )

                    if saved == 0:
                        increment_job(job_id, failed=1)
                        log_message(
                            job_id,
                            f"Chapter {chapter} failed: no usable images."
                        )
                    else:
                        paths = image_files_in(chapter_dir)
                        all_image_paths.extend(paths)

                        pdf_path = (
                            work_dir /
                            f"{safe_name(manga)}_chapter_{chapter:04d}.pdf"
                        )

                        page_count = make_pdf(paths, pdf_path)

                        if page_count > 0:
                            chapter_pdf_paths.append(pdf_path)
                            log_message(
                                job_id,
                                f"Created chapter PDF with {page_count} pages."
                            )
                        else:
                            log_message(
                                job_id,
                                f"Chapter {chapter}: PDF creation skipped."
                            )

                except Exception as exc:
                    increment_job(job_id, failed=1)
                    log_message(
                        job_id,
                        f"Chapter {chapter} failed: {str(exc)[:250]}"
                    )

                finally:
                    increment_job(job_id, completed=1)

            log_message(job_id, "All requested chapters processed.")

        finally:
            await context.close()
            await browser.close()

    # Create a combined PDF.
    combined_pdf = work_dir / f"{safe_name(manga)}_combined.pdf"

    if all_image_paths:
        update_job(job_id, current_chapter="Creating combined PDF")
        log_message(job_id, "Creating combined PDF.")

        combined_pages = make_pdf(
            all_image_paths,
            combined_pdf,
        )

        if combined_pages:
            log_message(
                job_id,
                f"Combined PDF created with {combined_pages} pages."
            )
        else:
            log_message(
                job_id,
                "Could not create the combined PDF."
            )

    # Package PDFs into a ZIP file.
    zip_path = OUTPUT_DIR / f"PanelPress_{job_id}.zip"

    update_job(job_id, current_chapter="Creating ZIP archive")
    log_message(job_id, "Packaging PDF files into a ZIP.")

    with zipfile.ZipFile(
        zip_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:

        for pdf_path in chapter_pdf_paths:
            if pdf_path.exists():
                archive.write(
                    pdf_path,
                    arcname=pdf_path.name,
                )

        if combined_pdf.exists():
            archive.write(
                combined_pdf,
                arcname=combined_pdf.name,
            )

        # Include a simple summary of the job.
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

        archive.writestr(
            "PanelPress_summary.json",
            json.dumps(summary, indent=2),
        )

    if not chapter_pdf_paths and not combined_pdf.exists():
        raise RuntimeError(
            "No PDFs could be created. The site may have blocked the request "
            "or its chapter layout may have changed."
        )

    update_job(
        job_id,
        state="complete",
        status="Complete",
        current_chapter="Finished",
        download_path=str(zip_path),
        chapter_pdfs=[str(path) for path in chapter_pdf_paths],
    )

    log_message(job_id, "Finished! Your ZIP is ready.")


def worker(job_id, domain, manga, first, last):
    work_dir = OUTPUT_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        log_message(job_id, "Waiting for the download worker.")

        with job_semaphore:
            log_message(job_id, "Worker started.")

            asyncio.run(
                run_browser_job(
                    job_id,
                    domain,
                    manga,
                    first,
                    last,
                    work_dir,
                )
            )

    except Exception as exc:
        error = str(exc)[:1000]

        update_job(
            job_id,
            state="error",
            status="Error",
            error=error,
            current_chapter="Stopped because of an error",
        )

        log_message(job_id, f"Job failed: {error}")


# ============================================================
# FLASK ROUTES
# ============================================================

@APP.route("/", methods=["GET"])
def index():
    return render_template_string(PAGE)


@APP.route("/create", methods=["POST"])
def create_job():
    data = request.get_json(silent=True) or {}

    try:
        domain = validate_domain(data.get("website"))
        manga = validate_manga_slug(data.get("manga"))

        first = int(data.get("first", 1))
        last = int(data.get("last", first))

        if first < 1 or last < first:
            raise ValueError("Enter a valid chapter range.")

        if last - first + 1 > MAX_CHAPTERS:
            raise ValueError(
                f"You can request up to {MAX_CHAPTERS} chapters per job."
            )

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
        "elapsed": 0,
        "eta": None,
        "current_chapter": "Waiting for worker",
        "started_at": time.time(),
        "log": [],
        "error": None,
        "download_path": None,
        "chapter_pdfs": [],
    }

    with jobs_lock:
        jobs[job_id] = job

    log_message(job_id, "Download job created.")
    log_message(job_id, f"Website: {domain}")
    log_message(job_id, f"Manga: {manga}")
    log_message(job_id, f"Chapters: {first} through {last}")

    thread = threading.Thread(
        target=worker,
        args=(job_id, domain, manga, first, last),
        daemon=True,
    )
    thread.start()

    return jsonify({
        "job_id": job_id,
        "state": "queued",
    })


@APP.route("/progress/<job_id>", methods=["GET"])
def progress(job_id):
    data = job_snapshot(job_id)

    if not data:
        return jsonify({"error": "Job not found."}), 404

    download_url = None

    if data.get("state") == "complete" and data.get("download_path"):
        download_url = f"/download/{job_id}"

    # Do not expose server filesystem paths to the browser.
    data.pop("download_path", None)
    data.pop("chapter_pdfs", None)
    data["download_url"] = download_url

    return jsonify(data)


@APP.route("/download/<job_id>", methods=["GET"])
def download(job_id):
    data = job_snapshot(job_id)

    if not data:
        abort(404)

    if data.get("state") != "complete":
        abort(409)

    path = data.get("download_path")

    if not path or not Path(path).is_file():
        abort(404)

    return send_file(
        path,
        as_attachment=True,
        download_name=f"PanelPress_{job_id}.zip",
        mimetype="application/zip",
    )


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))

    APP.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        threaded=True,
    )
```
