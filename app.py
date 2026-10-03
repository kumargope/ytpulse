import os
import re
import json
import uuid
import asyncio
import tempfile
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp

app = FastAPI(title="Ultra Animated YouTube Downloader")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TEMP_DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "yt_downloader_cache"
TEMP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# In-memory progress tracking
download_progress = {}

@app.get("/api/version")
async def get_version():
    c_path = Path(__file__).parent / "cookies.txt"
    c_exists = c_path.exists()
    c_size = c_path.stat().st_size if c_exists else 0
    return {
        "status": "online",
        "version": "v3.0-rapidapi-cloud",
        "rapidapi_active": bool(RAPIDAPI_KEY),
        "cookies_present": c_exists,
        "cookies_bytes": c_size
    }

class VideoInfoRequest(BaseModel):
    url: str

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def format_bytes(size: Optional[int]) -> str:
    if not size:
        return "Adaptive"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"

def format_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "00:00"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def build_ydl_opts(extra_opts: Optional[dict] = None) -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 15,
        'retries': 2,
        'remote_components': ['ejs:github'],
        'js_runtimes': {'node': {}},
    }
    
    # Priority: bundled cookies.txt file, or YOUTUBE_COOKIES environment variable
    cookies_path = Path(__file__).parent / "cookies.txt"
    env_cookies = os.environ.get("YOUTUBE_COOKIES") or os.environ.get("COOKIES_DATA")
    
    if cookies_path.exists():
        opts['cookiefile'] = str(cookies_path)
    elif env_cookies:
        temp_c = TEMP_DOWNLOAD_DIR / "yt_cookies.txt"
        clean_c = env_cookies.replace('\\n', '\n').strip()
        if not clean_c.startswith("# Netscape"):
            clean_c = "# Netscape HTTP Cookie File\n" + clean_c
        temp_c.write_text(clean_c, encoding="utf-8")
        opts['cookiefile'] = str(temp_c)

    # Proxy support (Webshare / Residential Proxy for Cloud Datacenter bypass)
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("YTPULSE_PROXY")
    if proxy:
        opts['proxy'] = proxy

    if extra_opts:
        opts.update(extra_opts)
    return opts

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "14a4f0702amsh4efb3c1c6719a9fp116bf9jsnb8b41959bb5e")
RAPIDAPI_HOST = "youtube-video-fast-downloader-24-7.p.rapidapi.com"

def extract_video_id(url: str) -> Optional[str]:
    patterns = [
        r'(?:v=|\/videos\/|embed\/|youtu\.be\/|\/v\/|\/e\/|watch\?v=|watch\?.+&v=)([\w-]{11})',
        r'(?:shorts\/)([\w-]{11})',
        r'^([\w-]{11})$'
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

def fetch_rapidapi_info(video_id: str) -> Optional[dict]:
    if not RAPIDAPI_KEY:
        return None
    api_url = f"https://{RAPIDAPI_HOST}/get-video-info/{video_id}?return_available_quality=true&response_mode=default"
    req = urllib.request.Request(
        api_url,
        headers={
            'x-rapidapi-host': RAPIDAPI_HOST,
            'x-rapidapi-key': RAPIDAPI_KEY,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as res:
            if res.status == 200:
                return json.loads(res.read().decode('utf-8'))
    except Exception as e:
        print(f"RapidAPI info exception: {e}")
    return None

def fetch_rapidapi_download_file(video_id: str, quality_id: str) -> Optional[dict]:
    if not RAPIDAPI_KEY:
        return None
    api_url = f"https://{RAPIDAPI_HOST}/download_video/{video_id}?quality={quality_id}"
    req = urllib.request.Request(
        api_url,
        headers={
            'x-rapidapi-host': RAPIDAPI_HOST,
            'x-rapidapi-key': RAPIDAPI_KEY,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            if res.status == 200:
                return json.loads(res.read().decode('utf-8'))
    except Exception as e:
        print(f"RapidAPI download exception: {e}")
    return None

@app.post("/api/info")
async def get_video_info(req: VideoInfoRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    loop = asyncio.get_event_loop()
    video_id = extract_video_id(url)

    # Priority 1: Try RapidAPI 24/7 Cloud Engine first
    if video_id and RAPIDAPI_KEY:
        rapid_data = await loop.run_in_executor(None, fetch_rapidapi_info, video_id)
        if rapid_data and rapid_data.get('title'):
            title = rapid_data.get('title', 'YouTube Video')
            dur = int(rapid_data.get('lengthSeconds') or 0)
            thumbnail = ""
            thumbs = rapid_data.get('thumbnail')
            if isinstance(thumbs, list) and len(thumbs) > 0:
                thumbnail = thumbs[-1].get('url', '')
            elif isinstance(thumbs, dict):
                thumbnail = thumbs.get('url', '')
            if not thumbnail:
                thumbnail = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

            uploader = rapid_data.get('author') or rapid_data.get('ownerChannelName') or 'YouTube Creator'
            v_count = int(rapid_data.get('viewCount') or 0)

            qualities = rapid_data.get('availableQuality', [])
            video_resolutions = {}
            best_audio_sz = 0
            best_audio_id = "251"

            for q in qualities:
                if q.get('type') == 'audio':
                    sz = q.get('size') or 0
                    if sz > best_audio_sz:
                        best_audio_sz = sz
                        best_audio_id = str(q.get('id', '251'))

            for q in qualities:
                if q.get('type') == 'video':
                    lbl = q.get('quality', '')
                    if not lbl or lbl == 'Unknown':
                        continue
                    h = int(re.sub(r'\D', '', lbl) or 0)
                    if not h:
                        continue

                    if h >= 4320:
                        cat, badge, glow = "8K Ultra HD", "8K UHD", "glow-8k"
                    elif h >= 2160:
                        cat, badge, glow = "4K Ultra HD", "4K UHD", "glow-4k"
                    elif h >= 1440:
                        cat, badge, glow = "2K Quad HD", "2K QHD", "glow-2k"
                    elif h >= 1080:
                        cat, badge, glow = "Full HD", "1080p FHD", "glow-1080"
                    elif h >= 720:
                        cat, badge, glow = "HD", "720p HD", "glow-720"
                    elif h >= 480:
                        cat, badge, glow = "SD", "480p SD", "glow-sd"
                    elif h >= 360:
                        cat, badge, glow = "Data Saver", "360p", "glow-sd"
                    elif h >= 240:
                        cat, badge, glow = "Low", "240p", "glow-sd"
                    else:
                        cat, badge, glow = "Basic", "144p", "glow-sd"

                    raw_sz = q.get('size') or 0
                    total_sz = (raw_sz + best_audio_sz) if raw_sz else 0

                    if h not in video_resolutions or raw_sz > video_resolutions[h].get('raw_size', 0):
                        video_resolutions[h] = {
                            'resolution': f"{h}p",
                            'display_name': f"{h}p",
                            'height': h,
                            'fps': 30,
                            'category': cat,
                            'badge': badge,
                            'glow': glow,
                            'filesize': total_sz,
                            'filesize_str': format_bytes(total_sz) if total_sz else "Adaptive",
                            'rapid_id': str(q.get('id')),
                            'raw_size': raw_sz
                        }

            sorted_res = sorted(video_resolutions.values(), key=lambda x: x['height'], reverse=True)

            audio_opts = [
                {
                    'bitrate': '320k',
                    'label': 'Ultra HQ MP3 (320 kbps)',
                    'desc': 'Best studio sound • High definition audio',
                    'badge': 'Studio HD',
                    'ext': 'mp3'
                },
                {
                    'bitrate': '192k',
                    'label': 'Standard MP3 (192 kbps)',
                    'desc': 'Balanced size & high clarity audio',
                    'badge': 'High Quality',
                    'ext': 'mp3'
                },
                {
                    'bitrate': '128k',
                    'label': 'Compressed MP3 (128 kbps)',
                    'desc': 'Fast download • Small file size',
                    'badge': 'Fast',
                    'ext': 'mp3'
                },
                {
                    'bitrate': 'original',
                    'label': 'Original M4A / AAC',
                    'desc': 'Direct untouched original audio stream',
                    'badge': 'Lossless',
                    'ext': 'm4a'
                }
            ]

            return {
                "title": title,
                "uploader": uploader,
                "duration": format_duration(dur),
                "duration_raw": dur,
                "views": f"{v_count:,}" if v_count else "N/A",
                "thumbnail": thumbnail,
                "video_formats": sorted_res,
                "audio_formats": audio_opts,
                "url": url,
                "engine": "rapidapi"
            }

    # Priority 2: Fallback to yt-dlp
    ydl_opts = build_ydl_opts({'skip_download': True, 'extract_flat': False})

    try:
        def extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = await loop.run_in_executor(None, extract)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not info:
        raise HTTPException(status_code=404, detail="No video information found")

    title = info.get('title', 'Unknown Title')
    duration = info.get('duration', 0)
    thumbnail = info.get('thumbnail', '')
    uploader = info.get('uploader', 'Unknown Creator')
    view_count = info.get('view_count', 0)

    # Process all available video/audio options
    formats = info.get('formats', [])
    
    # Calculate best audio size for combined size estimation
    best_audio_size = 0
    for f in formats:
        if f.get('vcodec') == 'none' and f.get('acodec') != 'none':
            size = f.get('filesize') or f.get('filesize_approx') or 0
            if size > best_audio_size:
                best_audio_size = size

    video_resolutions = {}

    for f in formats:
        height = f.get('height')
        vcodec = f.get('vcodec')
        if not height or vcodec == 'none':
            continue

        fps = f.get('fps') or 30
        
        # Determine category & visual badge style
        if height >= 4320:
            category = "8K Ultra HD"
            badge = "8K UHD"
            glow = "glow-8k"
        elif height >= 2160:
            category = "4K Ultra HD"
            badge = "4K UHD"
            glow = "glow-4k"
        elif height >= 1440:
            category = "2K Quad HD"
            badge = "2K QHD"
            glow = "glow-2k"
        elif height >= 1080:
            category = "Full HD"
            badge = "1080p FHD"
            glow = "glow-1080"
        elif height >= 720:
            category = "HD"
            badge = "720p HD"
            glow = "glow-720"
        elif height >= 480:
            category = "SD"
            badge = "480p SD"
            glow = "glow-sd"
        elif height >= 360:
            category = "Data Saver"
            badge = "360p"
            glow = "glow-sd"
        elif height >= 240:
            category = "Low"
            badge = "240p"
            glow = "glow-sd"
        else:
            category = "Basic"
            badge = "144p"
            glow = "glow-sd"

        fps_label = f" {fps}fps" if fps > 30 else ""
        res_display = f"{height}p{fps_label}"

        video_size = f.get('filesize') or f.get('filesize_approx') or 0
        total_size = (video_size + best_audio_size) if video_size else 0

        # Group by resolution height; keep the highest quality (e.g. 60fps or highest bitrate)
        if height not in video_resolutions or total_size > video_resolutions[height]['filesize']:
            video_resolutions[height] = {
                'resolution': f"{height}p",
                'display_name': res_display,
                'height': height,
                'fps': fps,
                'category': category,
                'badge': badge,
                'glow': glow,
                'filesize': total_size,
                'filesize_str': format_bytes(total_size) if total_size else "Adaptive"
            }

    sorted_resolutions = sorted(video_resolutions.values(), key=lambda x: x['height'], reverse=True)

    # Audio format options
    audio_formats = [
        {
            'bitrate': '320k',
            'label': 'Ultra HQ MP3 (320 kbps)',
            'desc': 'Best studio sound • High definition audio',
            'badge': 'Studio HD',
            'ext': 'mp3'
        },
        {
            'bitrate': '192k',
            'label': 'Standard MP3 (192 kbps)',
            'desc': 'Balanced size & high clarity audio',
            'badge': 'High Quality',
            'ext': 'mp3'
        },
        {
            'bitrate': '128k',
            'label': 'Compressed MP3 (128 kbps)',
            'desc': 'Fast download • Small file size',
            'badge': 'Fast',
            'ext': 'mp3'
        },
        {
            'bitrate': 'original',
            'label': 'Original M4A / AAC',
            'desc': 'Direct untouched original audio stream',
            'badge': 'Lossless',
            'ext': 'm4a'
        }
    ]

    return {
        "title": title,
        "uploader": uploader,
        "duration": format_duration(duration),
        "duration_raw": duration,
        "views": f"{view_count:,}" if view_count else "N/A",
        "thumbnail": thumbnail,
        "video_formats": sorted_resolutions,
        "audio_formats": audio_formats,
        "url": url
    }

@app.get("/api/progress/{task_id}")
async def get_progress(task_id: str):
    prog = download_progress.get(task_id, {"status": "not_found", "percent": 0})
    return prog

def cleanup_file(filepath: Path):
    try:
        if filepath.exists():
            filepath.unlink()
    except Exception:
        pass

@app.get("/api/download")
async def download_media(
    url: str = Query(...),
    format_type: str = Query("video"), # 'video' or 'audio'
    quality: str = Query("1080p"),
    bitrate: str = Query("320k"),
    task_id: Optional[str] = Query(None),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    if not task_id:
        task_id = str(uuid.uuid4())

    download_progress[task_id] = {"status": "starting", "percent": 0, "speed": "", "eta": ""}

    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
            downloaded = d.get('downloaded_bytes', 0)
            percent = round((downloaded / total) * 100, 1)
            download_progress[task_id] = {
                "status": "downloading",
                "percent": percent,
                "speed": d.get('_speed_str', ''),
                "eta": d.get('_eta_str', '')
            }
        elif d['status'] == 'finished':
            download_progress[task_id] = {
                "status": "processing",
                "percent": 98,
                "speed": "",
                "eta": "Merging Audio & Video with FFmpeg..."
            }

    file_prefix = f"yt_{uuid.uuid4().hex[:8]}"
    video_id = extract_video_id(url)

    # Priority 1: RapidAPI 24/7 Cloud Download Engine
    if video_id and RAPIDAPI_KEY:
        try:
            target_rapid_id = None
            if format_type == "audio":
                if bitrate in ["128k", "128"]:
                    target_rapid_id = "139"
                elif bitrate in ["192k", "192"]:
                    target_rapid_id = "140"
                else:
                    target_rapid_id = "251"
            else:
                h_match = re.search(r'\d+', quality)
                target_h = int(h_match.group(0)) if h_match else 1080
                if target_h >= 1080:
                    target_rapid_id = "270"
                elif target_h >= 720:
                    target_rapid_id = "232"
                elif target_h >= 480:
                    target_rapid_id = "230"
                elif target_h >= 360:
                    target_rapid_id = "230"
                elif target_h >= 240:
                    target_rapid_id = "243"
                else:
                    target_rapid_id = "269"

            download_progress[task_id] = {
                "status": "downloading",
                "percent": 15,
                "speed": "Ultra Fast",
                "eta": "Requesting stream from RapidAPI cloud..."
            }

            loop = asyncio.get_event_loop()
            file_info = await loop.run_in_executor(None, fetch_rapidapi_download_file, video_id, target_rapid_id)
            if file_info and file_info.get("file"):
                file_url = file_info.get("file")
                
                # Poll readiness (up to 75 seconds)
                ready = False
                for step in range(25):
                    download_progress[task_id] = {
                        "status": "downloading",
                        "percent": min(20 + step * 3, 88),
                        "speed": "Cloud Processing",
                        "eta": f"Merging streams on cloud (~{max(5, 75 - step * 3)}s)..."
                    }
                    def check_head():
                        try:
                            h_req = urllib.request.Request(file_url, headers={'User-Agent': 'Mozilla/5.0'}, method='HEAD')
                            with urllib.request.urlopen(h_req, timeout=5) as h_res:
                                return h_res.status == 200
                        except Exception:
                            return False

                    ready = await loop.run_in_executor(None, check_head)
                    if ready:
                        break
                    await asyncio.sleep(3)

                clean_title = sanitize_filename(f"ytpulse_{video_id}")
                ext = "mp3" if format_type == "audio" else "mp4"
                temp_file = TEMP_DOWNLOAD_DIR / f"{file_prefix}_{clean_title}.{ext}"

                def download_stream():
                    s_req = urllib.request.Request(file_url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(s_req, timeout=60) as resp, open(temp_file, "wb") as f_out:
                        total_sz = int(resp.headers.get("Content-Length", 0)) or 1
                        downloaded = 0
                        while True:
                            chunk = resp.read(64 * 1024)
                            if not chunk:
                                break
                            f_out.write(chunk)
                            downloaded += len(chunk)
                            download_progress[task_id] = {
                                "status": "downloading",
                                "percent": round(88 + (downloaded / total_sz) * 11, 1),
                                "speed": "High Speed CDN",
                                "eta": "Delivering file..."
                            }

                await loop.run_in_executor(None, download_stream)
                download_progress[task_id] = {"status": "complete", "percent": 100}
                background_tasks.add_task(cleanup_file, temp_file)
                download_name = f"{clean_title}.{ext}"
                encoded_name = urllib.parse.quote(download_name)
                media_type = "audio/mpeg" if ext == "mp3" else "video/mp4"
                headers = {
                    "Content-Disposition": f"attachment; filename=\"{download_name}\"; filename*=UTF-8''{encoded_name}"
                }
                return FileResponse(
                    path=str(temp_file),
                    filename=download_name,
                    media_type=media_type,
                    headers=headers
                )
            else:
                download_progress[task_id] = {"status": "error", "error": "Cloud conversion timeout. Please try another resolution or click Download again."}
                raise HTTPException(status_code=504, detail="Cloud conversion timeout. Please try another resolution.")
        except Exception as e:
            err_msg = str(e)
            download_progress[task_id] = {"status": "error", "error": err_msg}
            raise HTTPException(status_code=500, detail=err_msg)

    # Priority 2: Fallback to yt-dlp local / proxy download
    if format_type == "audio":
        if bitrate == "original":
            out_template = str(TEMP_DOWNLOAD_DIR / f"{file_prefix}_%(title)s.%(ext)s")
            ydl_opts = build_ydl_opts({
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': out_template,
                'progress_hooks': [progress_hook],
            })
        else:
            out_template = str(TEMP_DOWNLOAD_DIR / f"{file_prefix}_%(title)s.%(ext)s")
            kbps = re.sub(r'\D', '', bitrate) or "192"
            ydl_opts = build_ydl_opts({
                'format': 'bestaudio/best',
                'outtmpl': out_template,
                'progress_hooks': [progress_hook],
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': kbps,
                }],
            })
    else:
        # Video: Exact or best matching height
        height_match = re.search(r'\d+', quality)
        h_val = height_match.group(0) if height_match else "1080"
        out_template = str(TEMP_DOWNLOAD_DIR / f"{file_prefix}_%(title)s.%(ext)s")
        
        # Priority:
        # 1. Exact resolution match + best audio
        # 2. Under or equal resolution + best audio
        # 3. Fallback best
        fmt_string = (
            f"bestvideo[height={h_val}]+bestaudio/"
            f"bestvideo[height<={h_val}]+bestaudio/"
            f"best[height<={h_val}]/"
            f"best"
        )

        ydl_opts = build_ydl_opts({
            'format': fmt_string,
            'outtmpl': out_template,
            'merge_output_format': 'mp4',
            'progress_hooks': [progress_hook],
        })

    try:
        loop = asyncio.get_event_loop()
        def run_dl():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                if format_type == "audio" and bitrate != "original":
                    base, _ = os.path.splitext(filename)
                    return f"{base}.mp3", info.get('title', 'audio')
                return filename, info.get('title', 'video')

        final_path_str, video_title = await loop.run_in_executor(None, run_dl)
        final_path = Path(final_path_str)

        # Check if actual file exists or suffix changed
        if not final_path.exists():
            matched = list(TEMP_DOWNLOAD_DIR.glob(f"{file_prefix}*"))
            if matched:
                final_path = matched[0]
            else:
                raise Exception("Downloaded file could not be located on disk")

        download_progress[task_id] = {"status": "complete", "percent": 100}

        # Safe filename for Content-Disposition
        safe_title = sanitize_filename(video_title)
        ext = final_path.suffix.lstrip('.')
        download_name = f"{safe_title}.{ext}"
        encoded_name = urllib.parse.quote(download_name)

        background_tasks.add_task(cleanup_file, final_path)

        media_type = "audio/mpeg" if ext == "mp3" else ("audio/mp4" if ext == "m4a" else "video/mp4")

        headers = {
            "Content-Disposition": f"attachment; filename=\"{safe_title}.{ext}\"; filename*=UTF-8''{encoded_name}"
        }

        return FileResponse(
            path=str(final_path),
            media_type=media_type,
            filename=download_name,
            headers=headers
        )

    except Exception as e:
        download_progress[task_id] = {"status": "error", "error": str(e)}
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)

@app.get("/robots.txt")
async def get_robots():
    robots_path = static_dir / "robots.txt"
    if robots_path.exists():
        return FileResponse(path=str(robots_path), media_type="text/plain")
    raise HTTPException(status_code=404)

@app.get("/sitemap.xml")
async def get_sitemap():
    sitemap_path = static_dir / "sitemap.xml"
    if sitemap_path.exists():
        return FileResponse(path=str(sitemap_path), media_type="application/xml")
    raise HTTPException(status_code=404)

# Mount static files
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
