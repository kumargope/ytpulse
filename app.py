import os
import re
import uuid
import asyncio
import tempfile
import urllib.parse
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

@app.post("/api/info")
async def get_video_info(req: VideoInfoRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': False,
        'js_runtimes': {'node': {}}
    }

    try:
        loop = asyncio.get_event_loop()
        def extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        info = await loop.run_in_executor(None, extract)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch video: {str(e)}")

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

    if format_type == "audio":
        if bitrate == "original":
            out_template = str(TEMP_DOWNLOAD_DIR / f"{file_prefix}_%(title)s.%(ext)s")
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': out_template,
                'progress_hooks': [progress_hook],
                'js_runtimes': {'node': {}},
                'quiet': True,
                'no_warnings': True
            }
        else:
            out_template = str(TEMP_DOWNLOAD_DIR / f"{file_prefix}_%(title)s.%(ext)s")
            kbps = re.sub(r'\D', '', bitrate) or "192"
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': out_template,
                'progress_hooks': [progress_hook],
                'js_runtimes': {'node': {}},
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': kbps,
                }],
                'quiet': True,
                'no_warnings': True
            }
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

        ydl_opts = {
            'format': fmt_string,
            'outtmpl': out_template,
            'merge_output_format': 'mp4',
            'progress_hooks': [progress_hook],
            'js_runtimes': {'node': {}},
            'quiet': True,
            'no_warnings': True
        }

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
