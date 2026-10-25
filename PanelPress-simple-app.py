
import io
import re
import time
import uuid
import zipfile
import threading
import asyncio
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_file, render_template_string
from PIL import Image, ImageFile
from playwright.async_api import async_playwright

Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "panelpress_output"
OUTPUT_DIR.mkdir(exist_ok=True)

MAX_PAGES_PER_CHAPTER = 200
MAX_IMAGE_BYTES = 25 * 1024 * 1024

jobs = {}
jobs_lock = threading.Lock()


PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PanelPress</title>
<style>
:root {
    color-scheme: dark;
    --bg: #0b1020;
    --panel: #151d32;
    --panel2: #1d2942;
    --text: #edf2ff;
    --muted: #a7b4d0;
    --accent: #8b7cff;
    --accent2: #45d6bd;
    --border: #303d5c;
}
* { box-sizing: border-box; }
body {
    margin: 0;
    min-height: 100vh;
    font-family: Segoe UI, Arial, sans-serif;
    background:
        radial-gradient(ellipse at top left, #25234e 0, transparent 45%),
        var(--bg);
    color: var(--text);
}
main { width: min(1000px, 94%); margin: 36px auto; }
header { margin-bottom: 26px; }
.logo {
    display: inline-flex;
    align-items: center;
    gap: 12px;
    font-size: 30px;
    font-weight: 800;
    letter-spacing: -1px;
}
.logo-icon {
    display: grid;
    place-items: center;
    width: 48px;
    height: 48px;
    border-radius: 15px;
    background: linear-gradient(135deg, var(--accent), #6253c9);
}
header p { color: var(--muted); margin-top: 8px; }
.layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 18px;
}
.card {
    background: #151d32ed;
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 24px;
    box-shadow: 0 16px 45px #0002;
}
h2 { font-size: 19px; margin: 0 0 20px; }
label {
    display: block;
    margin: 16px 0 7px;
    font-size: 13px;
    font-weight: 650;
    color: #c9d4ed;
}
input {
    width: 100%;
    border: 1px solid var(--border);
    background: #0c1427;
    color: var(--text);
    border-radius: 10px;
    padding: 12px 13px;
    font: inherit;
    outline: none;
}
input:focus { border-color: var(--accent); }
.two {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
}
button, .download {
    display: inline-flex;
    justify-content: center;
    align-items: center;
    gap: 8px;
    width: 100%;
    border: 0;
    border-radius: 11px;
    padding: 13px 16px;
    background: linear-gradient(135deg, var(--accent), #6556d8);
    color: white;
    font: inherit;
    font-weight: 750;
    text-decoration: none;
    cursor: pointer;
    margin-top: 20px;
}
button:disabled { opacity: .5; cursor: not-allowed; }
.hint { color: var(--muted); font-size: 12px; line-height: 1.6; }
.status {
    padding: 12px 14px;
    border-radius: 10px;
    background: #0b1427;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.6;
    overflow-wrap: anywhere;
}
.progress-track {
    height: 12px;
    border-radius: 30px;
    overflow: hidden;
    background: #0b1427;
    margin: 15px 0;
    border: 1px solid var(--border);
}
#progressBar {
    width: 0%;
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--accent2));
    transition: width .25s;
}
.stats {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin: 18px 0;
}
.stat { padding: 13px; background: var(--panel2); border-radius: 12px; }
.stat strong { display: block; font-size: 23px; }
.stat span { color: var(--muted); font-size: 11px; }
#log {
    height: 230px;
    overflow-y: auto;
    background: #0b1427;
    border: 1px solid var(--border);
    border-radius: 11px;
    padding: 12px;
    font: 12px/1.7 Consolas, monospace;
    color: #c9d4ed;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}
.download { background: #087e70; }
.download.hidden { display: none; }
.success { color: #6fe5c4; }
.error { color: #ff9caa; }
footer {
    margin: 22px 0;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.7;
}
@media (max-width: 700px) {
    .layout { grid-template-columns: 1fr; }
    main { margin-top: 20px; }
    .card { padding: 18px; }
}
</style>
</head>
<body>
<main>
<header>
    <div class="logo"><span class="logo-icon">▤</span> PanelPress</div>
    <p>Chapter images to individual PDFs and one combined ZIP.</p>
</header>

<div class="layout">
<section class="card">
    <h2>⚙️ Download settings</h2>
    <form id="jobForm">
        <label for="website">Website domain</label>
        <input id="website" name="website" value="mangafish.cc"
               placeholder="example.com" required>

        <label for="manga">Manga slug</label>
        <input id="manga" name="manga" value="blue-lock"
               placeholder="blue-lock" required>

        <div class="two">
            <div>
                <label for="first">First chapter</label>
                <input id="first" name="first" type="number"
                       min="1" value="1" required>
            </div>
            <div>
                <label for="last">Last chapter</label>
                <input id="last" name="last" type="number"
                       min="1" value="10" required>
            </div>
        </div>

        <p class="hint">
            PanelPress checks the <code>chapter-NNN-2</code> URL first,
            then tries <code>chapter-NNN</code>.
            Use only content you are authorized to download.
        </p>
        <button id="startButton" type="submit">▶ Start processing</button>
    </form>
</section>

<section class="card">
    <h2>📊 Live progress</h2>
    <div id="statusText" class="status">Ready to start.</div>
    <div class="progress-track"><div id="progressBar"></div></div>
    <div id="percent">0%</div>

    <div class="stats">
        <div class="stat"><strong id="completed">0</strong><span>Chapters done</span></div>
        <div class="stat"><strong id="total">0</strong><span>Total chapters</span></div>
        <div class="stat"><strong id="images">0</strong><span>Images found</span></div>
    </div>

    <a id="downloadLink" class="download hidden" href="#"
       download="PanelPress_PDFs.zip">⬇ Download ZIP again</a>
</section>
</div>

<section class="card" style="margin-top:18px">
    <h2>📜 Activity log</h2>
    <div id="log">PanelPress is ready.</div>
</section>

<footer>
Keep this browser tab and the Python terminal open while processing.
The ZIP download starts automatically when the job completes.
</footer>
</main>

<script>
const form = document.getElementById("jobForm");
const startButton = document.getElementById("startButton");
const statusText = document.getElementById("statusText");
const progressBar = document.getElementById("progressBar");
const percent = document.getElementById("percent");
const completedEl = document.getElementById("completed");
const totalEl = document.getElementById("total");
const imagesEl = document.getElementById("images");
const logEl = document.getElementById("log");
const downloadLink = document.getElementById("downloadLink");

let currentJob = null;
let polling = false;
let autoDownloadedJob = null;

function setStatus(message, kind = "") {
    statusText.textContent = message;
    statusText.className = "status " + kind;
}

function updateLog(lines) {
    if (!Array.isArray(lines)) return;
    logEl.textContent = lines.join("\n");
    logEl.scrollTop = logEl.scrollHeight;
}

async function showJob(data) {
    const done = Number(data.completed || 0);
    const all = Number(data.total || 0);
    const pct = all > 0 ? Math.min(100, Math.round(done / all * 100)) : 0;

    completedEl.textContent = done;
    totalEl.textContent = all;
    imagesEl.textContent = data.images || 0;
    progressBar.style.width = pct + "%";
    percent.textContent = pct + "%";
    updateLog(data.log);

    if (data.state === "completed") {
        downloadLink.href = data.download_url;
        downloadLink.classList.remove("hidden");
        setStatus("Complete! Your ZIP download is starting.", "success");

        if (autoDownloadedJob !== currentJob) {
            autoDownloadedJob = currentJob;
            const link = document.createElement("a");
            link.href = data.download_url;
            link.download = "PanelPress_PDFs.zip";
            document.body.appendChild(link);
            link.click();
            link.remove();
        }

        startButton.disabled = false;
        startButton.textContent = "Start another job";
        polling = false;
        return true;
    }

    if (data.state === "error") {
        setStatus(data.error || "Processing failed.", "error");
        startButton.disabled = false;
        startButton.textContent = "Try again";
        polling = false;
        return true;
    }

    setStatus(data.status || "Working...");
    return false;
}

async function pollJob() {
    if (polling || !currentJob) return;
    polling = true;

    while (polling && currentJob) {
        try {
            const response = await fetch("/progress/" + currentJob);
            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || "Unable to get progress.");
            }

            if (await showJob(data)) break;
        } catch (err) {
            setStatus("Connection issue: " + err.message, "error");
        }

        await new Promise(resolve => setTimeout(resolve, 1200));
    }

    polling = false;
}

form.addEventListener("submit", async event => {
    event.preventDefault();

    const first = Number(document.getElementById("first").value);
    const last = Number(document.getElementById("last").value);

    if (!Number.isInteger(first) || !Number.isInteger(last)
        || first < 1 || last < first) {
        setStatus("Enter a valid chapter range.", "error");
        return;
    }

    startButton.disabled = true;
    startButton.textContent = "Starting...";
    downloadLink.classList.add("hidden");
    logEl.textContent = "Starting job...";
    progressBar.style.width = "0%";
    percent.textContent = "0%";
    completedEl.textContent = "0";
    totalEl.textContent = "0";
    imagesEl.textContent = "0";
    setStatus("Starting processing...");

    try {
        const response = await fetch("/create", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                website: document.getElementById("website").value,
                manga: document.getElementById("manga").value,
                first,
                last
            })
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Could not start job.");

        currentJob = data.job_id;
        autoDownloadedJob = null;
        startButton.textContent = "Processing...";
        polling = false;
        pollJob();
    } catch (err) {
        setStatus(err.message, "error");
        startButton.disabled = false;
        startButton.textContent = "Start processing";
    }
});
</script>
</body>
</html>
"""


def safe_name(value):
    value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
    return value.strip("._-") or "output"


def normalize_website(value):
    value = value.strip()
    if not value:
        raise ValueError("Enter a website domain.")

    if "://" not in value:
        value = "https://" + value

    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a valid HTTP or HTTPS website.")

    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Enter just the website domain, not a page URL.")

    return parsed.netloc


def chapter_url(website, manga, chapter):
    return f"https://{website}/manga/{manga}/chapter-{chapter:03d}"


async def choose_chapter_url(context, website, manga, chapter, job):
    """
    Test chapter-NNN-2 first. If it doesn't load successfully or has
    no image elements, test the normal chapter-NNN URL.
    """
    normal_url = chapter_url(website, manga, chapter)
    alternate_url = (
        f"https://{website}/manga/{manga}/chapter-{chapter:03d}-2"
    )

    for candidate in (alternate_url, normal_url):
        page = await context.new_page()
        try:
            log_message(job, f"Checking chapter URL: {candidate}")

            response = await page.goto(
                candidate,
                wait_until="domcontentloaded",
                timeout=30000
            )

            if response is None or not response.ok:
                code = response.status if response else "no response"
                log_message(job, f"URL unavailable ({code}): {candidate}")
                continue

            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            image_count = await page.locator("img").count()

            if image_count > 0:
                log_message(job, f"Selected URL: {candidate}")
                return candidate

            log_message(job, f"No image elements found at {candidate}")

        except Exception as exc:
            log_message(job, f"Could not check URL: {str(exc)[:150]}")
        finally:
            await page.close()

    log_message(job, f"Neither variant passed the check for chapter {chapter}.")
    return normal_url


def log_message(job, message):
    with jobs_lock:
        job["log"].append(time.strftime("[%H:%M:%S] ") + str(message))
        job["log"] = job["log"][-300:]


def update_job(job, **kwargs):
    with jobs_lock:
        job.update(kwargs)


def make_pdf(image_paths, output_path):
    if not image_paths:
        raise RuntimeError("No usable images were found for this PDF.")

    converted = []
    try:
        for path in image_paths:
            with Image.open(path) as image:
                converted.append(image.convert("RGB").copy())

        first, rest = converted[0], converted[1:]
        first.save(
            output_path,
            "PDF",
            save_all=True,
            append_images=rest,
            resolution=100.0
        )
    finally:
        for image in converted:
            image.close()


async def collect_chapter(context, url, chapter_dir, job, chapter_number):
    page = await context.new_page()
    image_paths = []
    seen_urls = set()

    try:
        log_message(job, f"Collecting chapter {chapter_number}: {url}")

        response = await page.goto(
            url, wait_until="domcontentloaded", timeout=60000
        )

        if response and response.status >= 400:
            raise RuntimeError(f"Page returned HTTP {response.status}: {url}")

        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        # Scroll to trigger standard lazy-loaded images.
        for _ in range(16):
            await page.evaluate(
                "window.scrollBy(0, Math.max(window.innerHeight, 600))"
            )
            await page.wait_for_timeout(350)

        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(500)

        images = await page.locator("img").evaluate_all("""
            elements => elements.map(img => ({
                src: img.currentSrc || img.src || "",
                dataSrc: img.getAttribute("data-src") || "",
                original: img.getAttribute("data-original") || "",
                lazy: img.getAttribute("data-lazy-src") || "",
                width: img.naturalWidth || img.width || 0,
                height: img.naturalHeight || img.height || 0
            }))
        """)

        for info in images:
            image_url = (
                info.get("src")
                or info.get("dataSrc")
                or info.get("original")
                or info.get("lazy")
            )

            if not image_url or image_url.startswith(("data:", "blob:")):
                continue
            if image_url in seen_urls:
                continue
            seen_urls.add(image_url)

            width = int(info.get("width") or 0)
            height = int(info.get("height") or 0)
            if width and height and (width < 120 or height < 120):
                continue

            if len(image_paths) >= MAX_PAGES_PER_CHAPTER:
                log_message(job, "Reached the per-chapter image safety cap.")
                break

            try:
                result = await context.request.get(
                    image_url,
                    timeout=30000,
                    headers={"Referer": url}
                )

                if not result.ok:
                    log_message(job, f"Skipped image: HTTP {result.status}")
                    continue

                content_type = result.headers.get("content-type", "").lower()
                if content_type and not content_type.startswith("image/"):
                    continue

                body = await result.body()
                if not body or len(body) > MAX_IMAGE_BYTES:
                    log_message(job, "Skipped empty or oversized image.")
                    continue

                with Image.open(io.BytesIO(body)) as test_image:
                    test_image.verify()

                extension = ".jpg"
                if "png" in content_type:
                    extension = ".png"
                elif "webp" in content_type:
                    extension = ".webp"
                elif "gif" in content_type:
                    extension = ".gif"

                filename = chapter_dir / (
                    f"page_{len(image_paths) + 1:04d}{extension}"
                )
                filename.write_bytes(body)
                image_paths.append(filename)

                with jobs_lock:
                    job["images"] += 1

            except Exception as exc:
                log_message(job, f"Skipped an image: {str(exc)[:150]}")

        if not image_paths:
            raise RuntimeError(
                "No usable images found. The page may use a different "
                "layout or may not be accessible."
            )

        log_message(
            job, f"Chapter {chapter_number}: {len(image_paths)} images found."
        )
        return image_paths

    finally:
        await page.close()


async def run_browser_job(job, website, manga, first, last, work_dir):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            channel="msedge",
            headless=True
        )

        context = await browser.new_context(
            accept_downloads=False,
            viewport={"width": 1440, "height": 1000}
        )

        try:
            for chapter in range(first, last + 1):
                chapter_dir = work_dir / f"chapter_{chapter:03d}"
                chapter_dir.mkdir(parents=True, exist_ok=True)

                update_job(
                    job,
                    status=f"Checking chapter {chapter} of {last}...",
                    current_chapter=chapter
                )

                # Check the -2 variant first for EVERY chapter.
                url = await choose_chapter_url(
                    context, website, manga, chapter, job
                )

                try:
                    images = await collect_chapter(
                        context, url, chapter_dir, job, chapter
                    )

                    chapter_pdf = work_dir / (
                        f"{safe_name(manga)}_Chapter_{chapter:03d}.pdf"
                    )
                    make_pdf(images, chapter_pdf)
                    job["chapter_pdfs"].append(chapter_pdf)
                    log_message(job, f"Created {chapter_pdf.name}")

                except Exception as exc:
                    log_message(
                        job,
                        f"Chapter {chapter} failed: {str(exc)[:250]}"
                    )

                update_job(job, completed=chapter - first + 1)

            if not job["chapter_pdfs"]:
                raise RuntimeError(
                    "No PDFs were created. Check the website, chapter URLs, "
                    "and whether the pages are accessible."
                )

            update_job(job, status="Creating combined PDF...")

            # Combine the collected chapter images in chapter order.
            all_images = []
            for chapter in range(first, last + 1):
                chapter_dir = work_dir / f"chapter_{chapter:03d}"
                if chapter_dir.exists():
                    all_images.extend(sorted(
                        path for path in chapter_dir.iterdir()
                        if path.is_file() and path.suffix.lower()
                        in (".jpg", ".jpeg", ".png", ".webp", ".gif")
                    ))

            combined_pdf = work_dir / f"{safe_name(manga)}_Combined.pdf"
            if all_images:
                make_pdf(all_images, combined_pdf)
                log_message(job, f"Created {combined_pdf.name}")

            zip_path = work_dir / f"{safe_name(manga)}_PanelPress.zip"
            update_job(job, status="Packaging ZIP file...")

            with zipfile.ZipFile(
                zip_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for pdf_path in job["chapter_pdfs"]:
                    archive.write(pdf_path, pdf_path.name)

                if combined_pdf.exists():
                    archive.write(combined_pdf, combined_pdf.name)

            update_job(
                job,
                state="completed",
                status="Ready! Starting ZIP download...",
                download_path=str(zip_path)
            )
            log_message(job, "Finished. The ZIP is ready to download.")

        finally:
            await context.close()
            await browser.close()


def worker(job_id, website, manga, first, last):
    job = jobs[job_id]
    work_dir = OUTPUT_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        asyncio.run(
            run_browser_job(job, website, manga, first, last, work_dir)
        )
    except Exception as exc:
        update_job(
            job,
            state="error",
            status="Processing failed.",
            error=str(exc)
        )
        log_message(job, f"ERROR: {exc}")


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.post("/create")
def create():
    data = request.get_json(silent=True) or {}

    try:
        website = normalize_website(str(data.get("website", "")))
        manga = safe_name(str(data.get("manga", "")))
        first = int(data.get("first", 1))
        last = int(data.get("last", first))

        if first < 1 or last < first:
            raise ValueError("Enter a valid chapter range.")

        job_id = uuid.uuid4().hex
        job = {
            "state": "running",
            "status": "Starting browser...",
            "completed": 0,
            "total": last - first + 1,
            "images": 0,
            "log": [],
            "error": None,
            "download_path": None,
            "chapter_pdfs": [],
        }

        with jobs_lock:
            jobs[job_id] = job

        threading.Thread(
            target=worker,
            args=(job_id, website, manga, first, last),
            daemon=True
        ).start()

        return jsonify({"job_id": job_id})

    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/progress/<job_id>")
def progress(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return jsonify({"error": "Job not found."}), 404

        data = {
            "state": job["state"],
            "status": job["status"],
            "completed": job["completed"],
            "total": job["total"],
            "images": job["images"],
            "log": list(job["log"]),
            "error": job["error"],
        }

        if job["state"] == "completed":
            data["download_url"] = f"/download/{job_id}"

    return jsonify(data)


@app.get("/download/<job_id>")
def download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job["state"] != "completed":
            return "The ZIP is not ready yet.", 404
        path = job.get("download_path")

    if not path or not Path(path).is_file():
        return "The ZIP file could not be found.", 404

    return send_file(
        path,
        as_attachment=True,
        download_name=Path(path).name,
        mimetype="application/zip"
    )


if __name__ == "__main__":
    print("\nPanelPress is starting")
    print("Open http://127.0.0.1:8000 in Edge")
    print("Keep this terminal window open.\n")

    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)
