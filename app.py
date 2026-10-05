import os
import re
import time
import uuid
import shutil
import base64
import asyncio
import tempfile
import urllib.parse
import contextlib
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import httpx
import ipaddress

app = FastAPI(title="StreamPulse YouTube Video Downloader")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS, HEAD",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Expose-Headers": "Content-Length, Content-Range, Content-Disposition, Accept-Ranges",
}

# Ephemeral temporary directory for active processing
TEMP_DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "yt_downloader_cache"
TEMP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

SECURE_COOKIE_DIR = Path(tempfile.gettempdir()) / "secure_cookies"
SECURE_COOKIE_DIR.mkdir(parents=True, exist_ok=True)

# Resource limits & Concurrency Control
MAX_CONCURRENT_DOWNLOADS = max(1, int(os.environ.get("MAX_CONCURRENT_DOWNLOADS", "3")))
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
DOWNLOAD_TIMEOUT_SECONDS = max(30, int(os.environ.get("DOWNLOAD_TIMEOUT_MS", "300000")) // 1000)

# In-memory real job progress tracking
download_progress = {}

# Supported YouTube domains for strict URL validation (prevents SSRF and command injection)
YOUTUBE_DOMAINS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be"
}

def validate_youtube_url(raw_url: str) -> str:
    url = raw_url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")
    
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid URL format")

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Invalid URL protocol. Only http and https are allowed.")

    domain = parsed.netloc.lower()
    if ":" in domain:
        domain = domain.split(":")[0]

    if domain not in YOUTUBE_DOMAINS:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL. Please provide a valid youtube.com or youtu.be link.")

    return url

def is_safe_stream_url(url_str: str) -> bool:
    """Validate that stream_url uses http/https and does not target internal/private network addresses."""
    try:
        parsed = urllib.parse.urlparse(url_str)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        blocked_hosts = {
            "localhost", "127.0.0.1", "0.0.0.0", "::1",
            "metadata.google.internal", "169.254.169.254"
        }
        if hostname in blocked_hosts or hostname.endswith(".localhost"):
            return False
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
                return False
        except ValueError:
            pass
        return True
    except Exception:
        return False

def is_cookies_configured() -> bool:
    """Check if server-side authorized cookies are configured without exposing values."""
    c_env = os.environ.get("YOUTUBE_COOKIES_BASE64") or os.environ.get("YOUTUBE_COOKIES")
    if c_env and c_env.strip():
        return True
    path_env = os.environ.get("YOUTUBE_COOKIES_PATH")
    if path_env and os.path.exists(path_env):
        return True
    local_file = Path(__file__).parent / "cookies.txt"
    if local_file.exists():
        return True
    return False

@contextlib.contextmanager
def get_secure_cookie_file():
    """
    Safely resolves server-side cookie file from:
    1. YOUTUBE_COOKIES_BASE64 or YOUTUBE_COOKIES (Render secret, base64 or Netscape text)
    2. YOUTUBE_COOKIES_PATH (mounted secret file)
    3. Local fallback (cookies.txt on server/machine outside git)
    Deletes any temporary decoded files on exit.
    NEVER logs or exposes cookie contents.
    """
    temp_cookie_path = None
    resolved_path = None

    try:
        cookie_env = os.environ.get("YOUTUBE_COOKIES_BASE64") or os.environ.get("YOUTUBE_COOKIES")
        if cookie_env and cookie_env.strip():
            raw_val = cookie_env.strip()
            temp_cookie_path = SECURE_COOKIE_DIR / f"cookie_{uuid.uuid4().hex}.txt"
            if raw_val.startswith("# Netscape") or "\t" in raw_val:
                temp_cookie_path.write_text(raw_val.replace('\\n', '\n'), encoding="utf-8")
            else:
                try:
                    decoded_bytes = base64.b64decode(raw_val, validate=False)
                    temp_cookie_path.write_bytes(decoded_bytes)
                except Exception:
                    temp_cookie_path.write_text(raw_val.replace('\\n', '\n'), encoding="utf-8")
            try:
                os.chmod(temp_cookie_path, 0o600)
            except Exception:
                pass
            resolved_path = str(temp_cookie_path)

        if not resolved_path:
            path_env = os.environ.get("YOUTUBE_COOKIES_PATH")
            if path_env and os.path.exists(path_env):
                resolved_path = str(path_env)

        if not resolved_path:
            local_cookie = Path(__file__).parent / "cookies.txt"
            if local_cookie.exists():
                resolved_path = str(local_cookie)

        yield resolved_path

    finally:
        if temp_cookie_path and temp_cookie_path.exists():
            try:
                temp_cookie_path.unlink()
            except Exception:
                pass

def map_extractor_error(err_str: str) -> str:
    """Map yt-dlp internal errors to clean, user-friendly frontend messages."""
    lower_err = err_str.lower()
    if "sign in to confirm you're not a bot" in lower_err or "bot" in lower_err or "verification" in lower_err:
        return "YouTube requires verification for this request. Configure a valid server-side YouTube cookie secret or try again later."
    if "video unavailable" in lower_err or "does not exist" in lower_err or "not found" in lower_err:
        return "This video is unavailable or cannot be accessed."
    if "private video" in lower_err or "private" in lower_err:
        return "This video is private and cannot be downloaded without authorized access."
    if "requested format is not available" in lower_err or "no suitable format" in lower_err:
        return "The requested quality is unavailable. Please select another quality."
    if "timed out" in lower_err or "timeout" in lower_err:
        return "Download timed out. Please try again."
    if "members-only" in lower_err:
        return "This video is for channel members only."
    
    clean = re.sub(r'https?://\S+', '', err_str)
    clean = re.sub(r'\[\w+\]', '', clean).strip()
    return clean or "An error occurred while processing the video."

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

def cleanup_file(filepath: Path):
    try:
        if filepath.exists():
            filepath.unlink()
    except Exception:
        pass

def cleanup_stale_temp_files():
    """Removes stale orphaned temporary files older than 30 minutes."""
    try:
        now = time.time()
        for f in TEMP_DOWNLOAD_DIR.glob("*"):
            if f.is_file() and (now - f.stat().st_mtime) > 1800:
                try:
                    f.unlink()
                except Exception:
                    pass
    except Exception:
        pass

# Run stale cleanup on startup
cleanup_stale_temp_files()

def build_ydl_opts(cookie_file: Optional[str] = None, extra_opts: Optional[dict] = None) -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 3,
        'remote_components': ['ejs:github'],
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web']
            }
        }
    }

    # Prefer Deno as primary JS runtime; fallback to Node.js
    if shutil.which("deno"):
        opts['js_runtimes'] = {'deno': {}}
    elif shutil.which("node"):
        opts['js_runtimes'] = {'node': {}}

    if cookie_file and os.path.exists(cookie_file):
        opts['cookiefile'] = cookie_file

    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")
    if proxy:
        opts['proxy'] = proxy

    if extra_opts:
        opts.update(extra_opts)
    return opts

# Diagnostic & Health Check Endpoint
@app.get("/api/diagnostics")
@app.get("/api/health")
async def get_diagnostics():
    yt_installed = False
    yt_version = ""
    try:
        yt_installed = True
        yt_version = yt_dlp.version.__version__
    except Exception:
        pass

    ejs_installed = False
    try:
        import yt_dlp_ejs
        ejs_installed = True
    except Exception:
        pass

    js_runtime = None
    if shutil.which("deno"):
        js_runtime = "deno"
    elif shutil.which("node"):
        js_runtime = "node"

    ffmpeg_ok = bool(shutil.which("ffmpeg"))
    ffprobe_ok = bool(shutil.which("ffprobe"))
    cookies_ok = is_cookies_configured()

    temp_writable = False
    try:
        test_file = TEMP_DOWNLOAD_DIR / f".test_{uuid.uuid4().hex}"
        test_file.write_text("ok")
        test_file.unlink()
        temp_writable = True
    except Exception:
        pass

    return {
        "ytDlp": yt_installed,
        "ytDlpVersion": yt_version,
        "ejs": ejs_installed,
        "jsRuntime": js_runtime or "none",
        "ffmpeg": ffmpeg_ok,
        "ffprobe": ffprobe_ok,
        "cookiesConfigured": cookies_ok,
        "tempDirWritable": temp_writable
    }

@app.get("/api/version")
async def get_version():
    diag = await get_diagnostics()
    return {
        "status": "online",
        "version": "v4.0-streampulse-production",
        "diagnostics": diag
    }

class ExtractRequest(BaseModel):
    url: str

class VideoInfoRequest(BaseModel):
    url: str

async def do_extract_video_info(raw_url: str) -> dict:
    url = validate_youtube_url(raw_url)

    with get_secure_cookie_file() as cookie_file:
        ydl_opts = build_ydl_opts(cookie_file=cookie_file, extra_opts={'skip_download': True, 'extract_flat': False})

        try:
            loop = asyncio.get_event_loop()
            def extract():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)

            info = await loop.run_in_executor(None, extract)
        except Exception as e:
            err_str = str(e).lower()
            if "bot" in err_str or "confirm you're not a bot" in err_str or "verification" in err_str:
                try:
                    def fallback_extract():
                        fb_opts = build_ydl_opts(cookie_file=None, extra_opts={
                            'skip_download': True,
                            'extract_flat': False,
                            'extractor_args': {
                                'youtube': {
                                    'player_client': ['android', 'ios']
                                }
                            }
                        })
                        with yt_dlp.YoutubeDL(fb_opts) as ydl_fb:
                            return ydl_fb.extract_info(url, download=False)
                    info = await loop.run_in_executor(None, fallback_extract)
                except Exception as fb_err:
                    user_msg = map_extractor_error(str(fb_err))
                    raise HTTPException(status_code=400, detail=user_msg)
            else:
                user_msg = map_extractor_error(str(e))
                raise HTTPException(status_code=400, detail=user_msg)

    if not info:
        raise HTTPException(status_code=404, detail="No video information found")

    title = info.get('title', 'Unknown Title')
    duration = info.get('duration', 0)
    thumbnail = info.get('thumbnail', '')
    uploader = info.get('uploader', 'Unknown Creator')
    view_count = info.get('view_count', 0)
    safe_title = sanitize_filename(title)

    formats = info.get('formats', [])

    # Identify best direct audio stream URL and audio size
    best_audio_size = 0
    best_audio_url = ""
    best_audio_ext = "m4a"

    # 1. Prefer pure audio streams with direct URL
    for f in formats:
        if f.get('vcodec') == 'none' and f.get('acodec') != 'none':
            size = f.get('filesize') or f.get('filesize_approx') or 0
            if size > best_audio_size:
                best_audio_size = size
            if f.get('url') and not best_audio_url:
                best_audio_url = f.get('url')
                best_audio_ext = f.get('ext') or 'm4a'

    # Fallback to any stream with audio if pure audio stream URL not available
    if not best_audio_url:
        for f in formats:
            if f.get('url') and f.get('acodec') != 'none':
                best_audio_url = f.get('url')
                best_audio_ext = f.get('ext') or 'mp4'
                break

    # Build video resolution tiers with direct stream URLs
    video_resolutions = {}

    for f in formats:
        height = f.get('height')
        vcodec = f.get('vcodec')
        if not height or vcodec == 'none':
            continue

        fps = f.get('fps') or 30

        if height >= 4320:
            category, badge, glow = "8K Ultra HD", "8K UHD", "glow-8k"
        elif height >= 2160:
            category, badge, glow = "4K Ultra HD", "4K UHD", "glow-4k"
        elif height >= 1440:
            category, badge, glow = "2K Quad HD", "2K QHD", "glow-2k"
        elif height >= 1080:
            category, badge, glow = "Full HD", "1080p FHD", "glow-1080"
        elif height >= 720:
            category, badge, glow = "HD", "720p HD", "glow-720"
        elif height >= 480:
            category, badge, glow = "SD", "480p SD", "glow-sd"
        elif height >= 360:
            category, badge, glow = "Data Saver", "360p", "glow-sd"
        elif height >= 240:
            category, badge, glow = "Low", "240p", "glow-sd"
        else:
            category, badge, glow = "Basic", "144p", "glow-sd"

        fps_label = f" {fps}fps" if fps > 30 else ""
        res_display = f"{height}p{fps_label}"

        video_size = f.get('filesize') or f.get('filesize_approx') or 0
        total_size = (video_size + best_audio_size) if video_size else 0

        stream_url = f.get('url') or ""
        ext = f.get('ext') or 'mp4'
        is_prog = (f.get('acodec') != 'none' and f.get('vcodec') != 'none')

        existing = video_resolutions.get(height)
        should_replace = False
        if not existing:
            should_replace = True
        elif not existing.get('stream_url') and stream_url:
            should_replace = True
        elif stream_url and is_prog and not existing.get('is_progressive'):
            should_replace = True
        elif total_size > existing.get('filesize', 0):
            should_replace = True

        if should_replace:
            filename_video = f"{safe_title}_{height}p.{ext}"
            proxy_url = f"/api/proxy?stream_url={urllib.parse.quote(stream_url)}&filename={urllib.parse.quote(filename_video)}" if stream_url else ""

            video_resolutions[height] = {
                'resolution': f"{height}p",
                'display_name': res_display,
                'height': height,
                'fps': fps,
                'category': category,
                'badge': badge,
                'glow': glow,
                'filesize': total_size,
                'filesize_str': format_bytes(total_size) if total_size else "Adaptive",
                'format_id': f.get('format_id', ''),
                'ext': ext,
                'stream_url': stream_url,
                'direct_url': stream_url,
                'proxy_url': proxy_url,
                'is_progressive': is_prog
            }

    # Fallback stream_url for resolutions if some don't have direct url
    fallback_stream_url = ""
    for r in video_resolutions.values():
        if r.get('stream_url'):
            fallback_stream_url = r['stream_url']
            break
    if not fallback_stream_url and best_audio_url:
        fallback_stream_url = best_audio_url

    for height, r in video_resolutions.items():
        if not r.get('stream_url') and fallback_stream_url:
            r['stream_url'] = fallback_stream_url
            r['direct_url'] = fallback_stream_url
            r['proxy_url'] = f"/api/proxy?stream_url={urllib.parse.quote(fallback_stream_url)}&filename={urllib.parse.quote(f'{safe_title}_{height}p.mp4')}"

    sorted_resolutions = sorted(video_resolutions.values(), key=lambda x: x['height'], reverse=True)

    # Audio format options with direct stream URLs
    audio_formats = []
    audio_specs = [
        {'bitrate': '320k', 'label': 'Ultra HQ MP3 (320 kbps)', 'desc': 'Best studio sound • High definition audio', 'badge': 'Studio HD', 'ext': 'mp3'},
        {'bitrate': '192k', 'label': 'Standard MP3 (192 kbps)', 'desc': 'Balanced size & high clarity audio', 'badge': 'High Quality', 'ext': 'mp3'},
        {'bitrate': '128k', 'label': 'Compressed MP3 (128 kbps)', 'desc': 'Fast download • Small file size', 'badge': 'Fast', 'ext': 'mp3'},
        {'bitrate': 'original', 'label': 'Original M4A / AAC', 'desc': 'Direct untouched original audio stream', 'badge': 'Lossless', 'ext': best_audio_ext or 'm4a'},
    ]

    for aspec in audio_specs:
        filename_audio = f"{safe_title}_{aspec['bitrate']}.{aspec['ext']}"
        proxy_audio_url = f"/api/proxy?stream_url={urllib.parse.quote(best_audio_url)}&filename={urllib.parse.quote(filename_audio)}" if best_audio_url else ""
        audio_formats.append({
            'bitrate': aspec['bitrate'],
            'label': aspec['label'],
            'desc': aspec['desc'],
            'badge': aspec['badge'],
            'ext': aspec['ext'],
            'stream_url': best_audio_url,
            'direct_url': best_audio_url,
            'proxy_url': proxy_audio_url
        })

    # Available stream list for zero server load
    available_streams = []
    for f in formats:
        if f.get('url') and (f.get('vcodec') != 'none' or f.get('acodec') != 'none'):
            h = f.get('height')
            res = f"{h}p" if h else "audio"
            f_ext = f.get('ext') or 'mp4'
            f_id = f.get('format_id') or 'stream'
            fname = f"{safe_title}_{res}_{f_id}.{f_ext}"
            available_streams.append({
                "format_id": f_id,
                "ext": f_ext,
                "resolution": res,
                "height": h,
                "fps": f.get('fps'),
                "filesize": f.get('filesize') or f.get('filesize_approx') or 0,
                "filesize_str": format_bytes(f.get('filesize') or f.get('filesize_approx')),
                "stream_url": f.get('url'),
                "direct_url": f.get('url'),
                "proxy_url": f"/api/proxy?stream_url={urllib.parse.quote(f.get('url'))}&filename={urllib.parse.quote(fname)}",
                "is_progressive": (f.get('acodec') != 'none' and f.get('vcodec') != 'none')
            })

    return {
        "id": info.get('id', ''),
        "title": title,
        "uploader": uploader,
        "duration": format_duration(duration),
        "duration_raw": duration,
        "views": f"{view_count:,}" if view_count else "N/A",
        "thumbnail": thumbnail,
        "video_formats": sorted_resolutions,
        "audio_formats": audio_formats,
        "streams": available_streams,
        "url": url
    }

@app.options("/api/extract")
async def extract_options():
    return Response(status_code=200, headers=CORS_HEADERS)

@app.post("/api/extract")
@app.get("/api/extract")
async def extract_endpoint(
    request: Request,
    req_body: Optional[ExtractRequest] = None,
    url: Optional[str] = Query(None)
):
    target_url = None
    if req_body and req_body.url:
        target_url = req_body.url
    elif url:
        target_url = url
    else:
        try:
            body = await request.json()
            if isinstance(body, dict):
                target_url = body.get("url")
        except Exception:
            pass

    if not target_url:
        raise HTTPException(status_code=400, detail="Missing YouTube URL")

    data = await do_extract_video_info(target_url)
    return JSONResponse(content=data, headers=CORS_HEADERS)

@app.options("/api/info")
async def info_options():
    return Response(status_code=200, headers=CORS_HEADERS)

@app.post("/api/info")
@app.get("/api/info")
async def get_video_info(
    request: Request,
    req: Optional[VideoInfoRequest] = None,
    url: Optional[str] = Query(None)
):
    target_url = (req.url if req else None) or url
    if not target_url:
        try:
            body = await request.json()
            if isinstance(body, dict):
                target_url = body.get("url")
        except Exception:
            pass

    if not target_url:
        raise HTTPException(status_code=400, detail="Missing YouTube URL")

    data = await do_extract_video_info(target_url)
    return JSONResponse(content=data, headers=CORS_HEADERS)

@app.options("/api/proxy")
async def proxy_options():
    return Response(status_code=200, headers=CORS_HEADERS)

@app.get("/api/proxy")
@app.head("/api/proxy")
async def proxy_stream(
    request: Request,
    stream_url: str = Query(..., description="Direct video/audio stream URL to proxy"),
    filename: Optional[str] = Query(None, description="Optional filename for attachment download")
):
    if not is_safe_stream_url(stream_url):
        raise HTTPException(status_code=400, detail="Prohibited or invalid stream URL")

    # Forward essential headers (Range for seeking / resume, User-Agent)
    fwd_headers = {
        "User-Agent": request.headers.get("User-Agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    if "range" in request.headers:
        fwd_headers["Range"] = request.headers["range"]

    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")
    client_kwargs = {
        "follow_redirects": True,
        "timeout": httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=30.0),
    }
    if proxy:
        client_kwargs["proxy"] = proxy

    client = httpx.AsyncClient(**client_kwargs)

    try:
        upstream_req = client.build_request(request.method, stream_url, headers=fwd_headers)
        upstream_resp = await client.send(upstream_req, stream=True)
    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Failed to connect to stream: {str(e)}")

    res_headers = {
        **CORS_HEADERS,
        "Accept-Ranges": "bytes",
    }

    for h in ("content-type", "content-length", "content-range", "last-modified", "etag"):
        val = upstream_resp.headers.get(h)
        if val:
            res_headers[h.title()] = val

    if filename:
        safe_name = sanitize_filename(filename)
        encoded_name = urllib.parse.quote(safe_name)
        res_headers["Content-Disposition"] = f'attachment; filename="{safe_name}"; filename*=UTF-8\'\'{encoded_name}'

    if request.method == "HEAD":
        await upstream_resp.aclose()
        await client.aclose()
        return Response(status_code=upstream_resp.status_code, headers=res_headers)

    async def stream_generator():
        try:
            async for chunk in upstream_resp.aiter_bytes(chunk_size=65536):
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_generator(),
        status_code=upstream_resp.status_code,
        headers=res_headers,
        media_type=upstream_resp.headers.get("content-type", "application/octet-stream")
    )

# Progress tracking endpoints
@app.get("/api/progress/{task_id}")
@app.get("/api/download/status/{task_id}")
async def get_progress(task_id: str):
    prog = download_progress.get(task_id, {"status": "not_found", "percent": 0})
    return prog

@app.get("/api/download")
async def download_media(
    url: str = Query(...),
    format_type: str = Query("video"),
    quality: str = Query("1080p"),
    bitrate: str = Query("320k"),
    task_id: Optional[str] = Query(None),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    valid_url = validate_youtube_url(url)

    if not task_id:
        task_id = f"job_{uuid.uuid4().hex[:12]}"

    download_progress[task_id] = {"status": "starting", "percent": 0, "speed": "", "eta": "Initializing..."}

    def progress_hook(d):
        if d.get('status') == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            percent = round((downloaded / total) * 100, 1) if total > 0 else 10
            download_progress[task_id] = {
                "status": "downloading",
                "percent": percent,
                "speed": d.get('_speed_str', '').strip(),
                "eta": d.get('_eta_str', '').strip()
            }
        elif d.get('status') == 'finished':
            download_progress[task_id] = {
                "status": "processing",
                "percent": 98,
                "speed": "",
                "eta": "Merging streams with FFmpeg..."
            }

    file_prefix = f"yt_{uuid.uuid4().hex[:8]}"

    # Concurrency control & Download execution
    async with download_semaphore:
        with get_secure_cookie_file() as cookie_file:
            if format_type == "audio":
                if bitrate == "original":
                    out_template = str(TEMP_DOWNLOAD_DIR / f"{file_prefix}_%(title)s.%(ext)s")
                    ydl_opts = build_ydl_opts(cookie_file=cookie_file, extra_opts={
                        'format': 'bestaudio[ext=m4a]/bestaudio/best',
                        'outtmpl': out_template,
                        'progress_hooks': [progress_hook],
                    })
                else:
                    out_template = str(TEMP_DOWNLOAD_DIR / f"{file_prefix}_%(title)s.%(ext)s")
                    kbps = re.sub(r'\D', '', bitrate) or "192"
                    ydl_opts = build_ydl_opts(cookie_file=cookie_file, extra_opts={
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
                height_match = re.search(r'\d+', quality)
                h_val = int(height_match.group(0)) if height_match else 1080
                out_template = str(TEMP_DOWNLOAD_DIR / f"{file_prefix}_%(title)s.%(ext)s")

                fmt_string = (
                    f"bestvideo[height={h_val}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height={h_val}]+bestaudio/"
                    f"bestvideo[height<={h_val}]+bestaudio/"
                    f"best[height<={h_val}]/"
                    f"best"
                )

                ydl_opts = build_ydl_opts(cookie_file=cookie_file, extra_opts={
                    'format': fmt_string,
                    'outtmpl': out_template,
                    'merge_output_format': 'mp4',
                    'progress_hooks': [progress_hook],
                })

            try:
                loop = asyncio.get_event_loop()
                def run_dl(opts_to_use):
                    with yt_dlp.YoutubeDL(opts_to_use) as ydl:
                        info = ydl.extract_info(valid_url, download=True)
                        filename = ydl.prepare_filename(info)
                        if format_type == "audio" and bitrate != "original":
                            base, _ = os.path.splitext(filename)
                            return f"{base}.mp3", info.get('title', 'audio')
                        return filename, info.get('title', 'video')

                try:
                    final_path_str, video_title = await asyncio.wait_for(
                        loop.run_in_executor(None, run_dl, ydl_opts),
                        timeout=DOWNLOAD_TIMEOUT_SECONDS
                    )
                except Exception as dl_err:
                    err_msg = str(dl_err).lower()
                    if "bot" in err_msg or "confirm you're not a bot" in err_msg or "verification" in err_msg:
                        fb_opts = build_ydl_opts(cookie_file=None, extra_opts={
                            'format': 'ba/b' if format_type == 'audio' else 'best[height<=720]/best',
                            'outtmpl': out_template,
                            'progress_hooks': [progress_hook],
                            'extractor_args': {
                                'youtube': {
                                    'player_client': ['android', 'ios']
                                }
                            }
                        })
                        final_path_str, video_title = await asyncio.wait_for(
                            loop.run_in_executor(None, run_dl, fb_opts),
                            timeout=DOWNLOAD_TIMEOUT_SECONDS
                        )
                    else:
                        raise dl_err
                final_path = Path(final_path_str)

                if not final_path.exists():
                    matched = list(TEMP_DOWNLOAD_DIR.glob(f"{file_prefix}*"))
                    if matched:
                        final_path = matched[0]
                    else:
                        raise Exception("Downloaded file could not be located on disk")

                download_progress[task_id] = {"status": "complete", "percent": 100}

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

            except asyncio.TimeoutError:
                download_progress[task_id] = {"status": "error", "error": "Download timed out. Please try again."}
                raise HTTPException(status_code=504, detail="Download timed out. Please try again.")
            except Exception as e:
                user_msg = map_extractor_error(str(e))
                download_progress[task_id] = {"status": "error", "error": user_msg}
                raise HTTPException(status_code=500, detail=user_msg)

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

# Mount frontend static files
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
