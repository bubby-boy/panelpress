import os
import re
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string, request, send_file

app = Flask(__name__)

BASE_URL = "https://www.mangzio.com/en"
OUTPUT_DIR = Path("media_downloads") / "manga"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

JOBS = {}
JOBS_LOCK = threading.Lock()
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.mangzio.com/",
}
TIMEOUT = 25
MAX_IMAGES = 500


def slugify_manga(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def make_chapter_url(manga_slug: str, chapter: str) -> str:
    return f"{BASE_URL}/{manga_slug}-en-chapter{chapter}"


def update_job(job_id, **values):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values)


def extract_image_urls(html: str, page_url: str):
    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        ".reading-content img",
        ".chapter-content img",
        ".reader-content img",
        ".chapter-images img",
        ".page-chapter img",
        "#chapter-reader img",
        "#readerarea img",
        ".container-chapter-reader img",
    ]
    found, seen = [], set()

    def add_images(images):
        for img in images:
            candidate = (
                img.get("data-src")
                or img.get("data-lazy-src")
                or img.get("data-original")
                or img.get("data-url")
                or img.get("src")
            )
            if not candidate:
                srcset = img.get("data-srcset") or img.get("srcset")
                if srcset:
                    candidate = srcset.split(",")[0].strip().split(" ")[0]
            if not candidate:
                continue
            absolute = urljoin(page_url, candidate.strip())
            parsed = urlparse(absolute)
            lower = absolute.lower()
            if parsed.scheme not in ("http", "https"):
                continue
            if any(word in lower for word in ("logo", "avatar", "favicon", "banner", "advert")):
                continue
            if absolute not in seen:
                seen.add(absolute)
                found.append(absolute)

    for selector in selectors:
        add_images(soup.select(selector))
        if found:
            break

    if not found:
        add_images(soup.find_all("img"))

    return found[:MAX_IMAGES]


def fetch_image_urls(chapter_url):
    response = requests.get(chapter_url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    final_url = response.url
    if urlparse(final_url).netloc.lower() not in ("www.mangzio.com", "mangzio.com"):
        raise ValueError("The chapter page redirected away from Mangzio.")
    urls = extract_image_urls(response.text, final_url)
    if not urls:
        raise ValueError(
            "No chapter images were found in the page HTML. Mangzio may load images "
            "with JavaScript or its page layout may have changed."
        )
    return urls


def download_images(image_urls, folder: Path, job_id):
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    with requests.Session() as session:
        session.headers.update(HEADERS)
        for index, image_url in enumerate(image_urls, start=1):
            response = session.get(image_url, timeout=TIMEOUT)
            response.raise_for_status()
            data = response.content
            # Verify that the downloaded data is an actual image.
            from PIL import Image
            import io
            try:
                with Image.open(io.BytesIO(data)) as im:
                    im.verify()
                    fmt = (im.format or "JPEG").lower()
            except Exception as exc:
                raise ValueError(
                    f"Item {index} was not a readable image. Mangzio may block direct image downloads."
                ) from exc
            ext = {"jpeg": "jpg", "tiff": "tif"}.get(fmt, fmt)
            path = folder / f"page_{index:04d}.{ext}"
            path.write_bytes(data)
            saved.append(path)
            update_job(
                job_id,
                progress=10 + int(index / len(image_urls) * 70),
                message=f"Downloaded image {index} of {len(image_urls)}…",
            )
    return saved


def make_zip(image_paths, zip_path: Path, job_id):
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, path in enumerate(image_paths, start=1):
            archive.write(path, arcname=path.name)
            update_job(
                job_id,
                progress=80 + int(index / len(image_paths) * 18),
                message=f"Packing image {index} of {len(image_paths)}…",
            )
    return zip_path


def run_job(job_id, manga_name, chapter):
    manga_slug = slugify_manga(manga_name)
    if not manga_slug:
        raise ValueError("Enter a manga title.")
    if not re.fullmatch(r"\d+(?:\.\d+)?", chapter):
        raise ValueError("Chapter must be a number, such as 1 or 12.5.")

    chapter_url = make_chapter_url(manga_slug, chapter)
    folder = OUTPUT_DIR / manga_slug / f"chapter_{chapter}"
    update_job(job_id, status="running", progress=3, message="Opening chapter page…", chapter_url=chapter_url)

    image_urls = fetch_image_urls(chapter_url)
    update_job(job_id, progress=8, message=f"Found {len(image_urls)} images. Downloading…")
    image_paths = download_images(image_urls, folder, job_id)

    safe_chapter = re.sub(r"[^0-9A-Za-z.-]+", "-", chapter)
    zip_path = folder / f"{manga_slug}_chapter_{safe_chapter}.zip"
    make_zip(image_paths, zip_path, job_id)

    update_job(
        job_id,
        status="done",
        progress=100,
        message=f"Finished! ZIP contains {len(image_paths)} images.",
        file_path=str(zip_path.resolve()),
        filename=zip_path.name,
        image_count=len(image_paths),
    )


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PanelPress — Manga ZIP Downloader</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#151d32;--line:#2a3655;--text:#eef3ff;--muted:#a9b6d2;--accent:#8b9dff;--good:#73e0b0;--bad:#ff8d9a}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at top,#1d2850 0,#0b1020 58%);font:16px/1.5 system-ui,Segoe UI,Arial;color:var(--text);display:grid;place-items:center;padding:24px}
main{width:min(760px,100%)}.brand{font-weight:900;letter-spacing:.04em;font-size:clamp(2rem,6vw,3.2rem);margin:0}.brand span{color:var(--accent)}.sub{color:var(--muted);margin:4px 0 24px}
.card{background:rgba(21,29,50,.94);border:1px solid var(--line);border-radius:22px;padding:clamp(20px,4vw,32px);box-shadow:0 24px 80px #0005}
label{display:block;font-weight:700;margin:16px 0 7px}input{width:100%;padding:14px 15px;border:1px solid #3b496d;border-radius:11px;background:#0c1326;color:var(--text);font:inherit;outline:none}input:focus{border-color:var(--accent);box-shadow:0 0 0 3px #8b9dff22}
.row{display:grid;grid-template-columns:1fr 160px;gap:14px}.btn{margin-top:20px;width:100%;padding:14px;border:0;border-radius:11px;background:var(--accent);color:#10152c;font:inherit;font-weight:850;cursor:pointer}.btn:disabled{opacity:.55;cursor:wait}
.status{margin-top:20px;padding:15px;border-radius:12px;background:#0c1326;border:1px solid var(--line);color:var(--muted);overflow-wrap:anywhere}.bar{height:8px;background:#27314d;border-radius:99px;overflow:hidden;margin-top:12px}.fill{height:100%;width:0;background:linear-gradient(90deg,#8b9dff,#73e0b0);transition:width .25s}
a.download{display:none;margin-top:14px;padding:13px 15px;text-align:center;text-decoration:none;color:#082016;background:var(--good);border-radius:11px;font-weight:850}.hint{font-size:.9rem;color:var(--muted);margin:18px 0 0}.error{color:var(--bad)}code{color:#d5dcff}
@media(max-width:560px){.row{grid-template-columns:1fr}}
</style></head>
<body><main>
<h1 class="brand">Panel<span>Press</span></h1>
<p class="sub">Download a Mangzio chapter as a ZIP, or read it here.</p>
<section class="card">
<form id="form"><div class="row">
<div><label for="manga">Manga title</label><input id="manga" placeholder="Blue Lock" required></div>
<div><label for="chapter">Chapter</label><input id="chapter" placeholder="1" required inputmode="decimal"></div>
</div><button class="btn" id="submit" type="submit">Download chapter as ZIP</button></form>
<div class="status" id="status" role="status">Enter a title and chapter number to begin.<div class="bar"><div class="fill" id="fill"></div></div></div>
<a class="download" id="download" href="#">⬇ Download ZIP</a>
<a class="download" id="read" href="#" style="background:#8b9dff;color:#10152c">📖 Read chapter</a>
<p class="hint">Example: <code>Blue Lock</code> + <code>1</code> builds <code>/en/blue-lock-en-chapter1</code>. The reader lets you choose left-to-right or right-to-left. Only download content you have permission to save.</p>
</section></main>
<script>
const form=document.getElementById('form'),btn=document.getElementById('submit'),box=document.getElementById('status'),fill=document.getElementById('fill'),download=document.getElementById('download'),read=document.getElementById('read');
let timer=null;
function showStatus(msg,pct,error=false){box.firstChild.textContent=msg;fill.style.width=Math.max(0,Math.min(100,pct))+'%';box.classList.toggle('error',error);}
form.addEventListener('submit',async e=>{
 e.preventDefault();if(timer)clearInterval(timer);download.style.display='none';read.style.display='none';btn.disabled=true;btn.textContent='Working…';showStatus('Starting download…',2);
 try{
  const response=await fetch('/api/download',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({manga:document.getElementById('manga').value,chapter:document.getElementById('chapter').value})});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Could not start job.');
  timer=setInterval(async()=>{
   try{
    const r=await fetch('/api/status/'+data.job_id),s=await r.json();
    showStatus(s.message||'Working…',s.progress||0,s.status==='error');
    if(s.status==='done'){clearInterval(timer);btn.disabled=false;btn.textContent='Download chapter as ZIP';download.href='/download/'+data.job_id;download.style.display='block';read.href='/reader/'+data.job_id;read.style.display='block';}
    if(s.status==='error'){clearInterval(timer);btn.disabled=false;btn.textContent='Try again';}
   }catch(err){clearInterval(timer);btn.disabled=false;btn.textContent='Try again';showStatus(err.message,0,true);}
  },900);
 }catch(err){btn.disabled=false;btn.textContent='Try again';showStatus(err.message,0,true);}
});
</script></body></html>"""


@app.get("/")
def index():
    return render_template_string(HTML)


@app.post("/api/download")
def start_download():
    data = request.get_json(silent=True) or {}
    manga = str(data.get("manga", "")).strip()
    chapter = str(data.get("chapter", "")).strip()
    if not manga:
        return jsonify(error="Enter a manga title."), 400
    if not re.fullmatch(r"\d+(?:\.\d+)?", chapter):
        return jsonify(error="Enter a valid chapter number, such as 1 or 12.5."), 400
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "queued", "progress": 0, "message": "Waiting to start…",
            "created": time.time(), "filename": None, "file_path": None,
            "manga_slug": slugify_manga(manga), "chapter": chapter,
            "image_folder": str((OUTPUT_DIR / slugify_manga(manga) / f"chapter_{chapter}").resolve())
        }

    def worker():
        try:
            run_job(job_id, manga, chapter)
        except Exception as exc:
            update_job(job_id, status="error", progress=0, message=str(exc))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify(job_id=job_id)


@app.get("/api/status/<job_id>")
def job_status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify(error="Job not found."), 404
        return jsonify({k: v for k, v in job.items() if k not in ("file_path", "created", "image_folder")})


@app.get("/download/<job_id>")
def download_file(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("status") != "done" or not job.get("file_path"):
            return jsonify(error="ZIP is not ready."), 404
        path = Path(job["file_path"])
        filename = job.get("filename") or path.name
    if not path.is_file():
        return jsonify(error="The ZIP file no longer exists."), 404
    return send_file(path, as_attachment=True, download_name=filename, mimetype="application/zip")



@app.get("/reader/<job_id>")
def read_chapter(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("status") != "done":
            return "Chapter is not ready yet. Start a download first.", 404
        folder = Path(job.get("image_folder", ""))
        manga = job.get("manga_slug", "manga")
        chapter = job.get("chapter", "?")
    if not folder.is_dir():
        return "Downloaded chapter images could not be found.", 404
    pages = sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"} and p.name.startswith("page_"))
    if not pages:
        return "No readable chapter pages were found.", 404
    return render_template_string(READER_HTML, job_id=job_id, manga=manga.replace("-", " ").title(), chapter=chapter, pages=pages)


@app.get("/reader-image/<job_id>/<path:filename>")
def reader_image(job_id, filename):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("status") != "done":
            return jsonify(error="Chapter is not ready."), 404
        folder = Path(job.get("image_folder", "")).resolve()
    candidate = (folder / filename).resolve()
    if candidate.parent != folder or not candidate.is_file() or not candidate.name.startswith("page_"):
        return jsonify(error="Page not found."), 404
    return send_file(candidate)


READER_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PanelPress Reader</title>
<style>
:root{color-scheme:dark;--bg:#080b14;--panel:#141b2d;--line:#2a3655;--text:#eef3ff;--muted:#a9b6d2;--accent:#8b9dff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,Segoe UI,Arial}.top{position:sticky;top:0;z-index:5;background:#0b1020ed;border-bottom:1px solid var(--line);padding:10px 14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}.top strong{margin-right:auto}.btn,select{border:1px solid #3b496d;background:var(--panel);color:var(--text);padding:9px 12px;border-radius:9px;font:inherit;cursor:pointer}.stage{display:flex;justify-content:center;align-items:flex-start;min-height:calc(100vh - 60px);padding:14px}.pages{display:flex;flex-direction:column;align-items:center;width:100%;gap:8px}.pages img{display:block;max-width:min(100%,1000px);height:auto;object-fit:contain}.single{max-width:min(100%,1100px);max-height:calc(100vh - 115px)}.footer{position:fixed;bottom:12px;left:50%;transform:translateX(-50%);display:flex;gap:8px;align-items:center;background:#10182beF;border:1px solid var(--line);border-radius:14px;padding:8px;box-shadow:0 8px 35px #0008}.counter{color:var(--muted);min-width:78px;text-align:center}.hidden{display:none!important}body.rtl .pages{direction:rtl}a{color:var(--accent)}
</style></head><body>
<header class="top"><strong>📖 {{ manga }} — Chapter {{ chapter }}</strong><label>Direction <select id="direction"><option value="rtl">Right to left</option><option value="ltr">Left to right</option></select></label><button class="btn" id="mode">Single page</button><button class="btn" id="full">Fullscreen</button><a href="/" style="color:#cbd5ff">Home</a></header>
<main class="stage"><div class="pages" id="pages">{% for page in pages %}<img loading="lazy" src="{{ url_for('reader_image', job_id=job_id, filename=page) }}" alt="Page {{ loop.index }}" data-index="{{ loop.index0 }}">{% endfor %}</div></main>
<nav class="footer"><button class="btn" id="prev">← Prev</button><span class="counter" id="counter">1 / {{ pages|length }}</span><button class="btn" id="next">Next →</button></nav>
<script>
const imgs=[...document.querySelectorAll('#pages img')],pages=document.getElementById('pages'),counter=document.getElementById('counter');let index=0,single=false;function show(){if(single){imgs.forEach((im,i)=>im.classList.toggle('hidden',i!==index));counter.textContent=(index+1)+' / '+imgs.length;}else{imgs.forEach(im=>im.classList.remove('hidden'));counter.textContent=imgs.length+' pages';}}function move(n){index=Math.max(0,Math.min(imgs.length-1,index+n));if(single){show();window.scrollTo({top:0,behavior:'smooth'});}else{const im=imgs[index];im?.scrollIntoView({behavior:'smooth',block:'start'});counter.textContent=(index+1)+' / '+imgs.length;}}document.getElementById('prev').onclick=()=>move(document.getElementById('direction').value==='rtl'?1:-1);document.getElementById('next').onclick=()=>move(document.getElementById('direction').value==='rtl'?-1:1);document.getElementById('direction').onchange=e=>{document.body.classList.toggle('rtl',e.target.value==='rtl');};document.getElementById('mode').onclick=()=>{single=!single;document.getElementById('mode').textContent=single?'All pages':'Single page';show();window.scrollTo(0,0);};document.getElementById('full').onclick=()=>{if(!document.fullscreenElement)document.documentElement.requestFullscreen?.();else document.exitFullscreen?.();};document.addEventListener('keydown',e=>{if(e.key==='ArrowRight')move(document.getElementById('direction').value==='rtl'?-1:1);if(e.key==='ArrowLeft')move(document.getElementById('direction').value==='rtl'?1:-1);});let touchX=0;document.addEventListener('touchstart',e=>touchX=e.changedTouches[0].screenX,{passive:true});document.addEventListener('touchend',e=>{const dx=e.changedTouches[0].screenX-touchX;if(Math.abs(dx)>60)move((dx<0?1:-1)*(document.getElementById('direction').value==='rtl'?-1:1));},{passive:true});show();
</script></body></html>"""


@app.get("/health")
def health():
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), debug=False)
