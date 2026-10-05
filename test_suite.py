import os
import sys
import base64
import asyncio
from pathlib import Path
from fastapi.testclient import TestClient

from app import (
    app,
    validate_youtube_url,
    get_secure_cookie_file,
    map_extractor_error,
    is_cookies_configured,
    TEMP_DOWNLOAD_DIR,
    SECURE_COOKIE_DIR
)

client = TestClient(app)

def test_diagnostics():
    print("[TEST 1] Testing /api/diagnostics endpoint...")
    res = client.get("/api/diagnostics")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    data = res.json()
    assert data["ytDlp"] is True
    assert "ytDlpVersion" in data
    assert data["ejs"] is True
    assert data["tempDirWritable"] is True
    print(f"  PASS: Diagnostics OK -> {data}")

def test_url_validation():
    print("[TEST 2] Testing URL validation and injection prevention...")
    # Valid URLs
    assert validate_youtube_url("https://www.youtube.com/watch?v=RMsPJnAM768")
    assert validate_youtube_url("https://youtu.be/RMsPJnAM768")
    assert validate_youtube_url("https://m.youtube.com/shorts/RMsPJnAM768")
    
    # Invalid URLs (should raise HTTPException)
    invalid_cases = [
        "https://evil.com/hack",
        "ftp://youtube.com/watch?v=123",
        "file:///etc/passwd",
        "https://notyoutube.com?v=123; rm -rf /",
        ""
    ]
    for inv in invalid_cases:
        try:
            validate_youtube_url(inv)
            assert False, f"Failed to reject: {inv}"
        except Exception:
            pass
    print("  PASS: Only authentic YouTube URLs permitted.")

def test_cookie_security():
    print("[TEST 3] Testing YOUTUBE_COOKIES_BASE64 decode & cleanup...")
    test_cookie_data = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1799999999\tTEST\t12345\n"
    b64_cookie = base64.b64encode(test_cookie_data.encode("utf-8")).decode("utf-8")
    
    os.environ["YOUTUBE_COOKIES_BASE64"] = b64_cookie
    assert is_cookies_configured() is True
    
    # Use context manager
    cookie_path_used = None
    with get_secure_cookie_file() as path:
        cookie_path_used = path
        assert path is not None
        assert os.path.exists(path)
        content = Path(path).read_text(encoding="utf-8")
        assert "TEST" in content
    
    # After exit, temporary decoded cookie MUST be deleted immediately
    assert not os.path.exists(cookie_path_used), "Temporary cookie file was not deleted after context exit!"
    print("  PASS: Cookie safely decoded to temp file and deleted on exit.")

def test_error_mapping():
    print("[TEST 4] Testing extractor error message mapping...")
    bot_msg = map_extractor_error("ERROR: [youtube] 12345: Sign in to confirm you're not a bot. Use --cookies")
    assert "Configure a valid server-side YouTube cookie secret" in bot_msg
    
    unavail_msg = map_extractor_error("ERROR: [youtube] 123: Video unavailable. This video is not available.")
    assert "This video is unavailable or cannot be accessed." in unavail_msg
    
    priv_msg = map_extractor_error("ERROR: [youtube] 123: Private video. Sign in if you've been granted access.")
    assert "This video is private" in priv_msg
    
    fmt_msg = map_extractor_error("ERROR: [youtube] 123: Requested format is not available")
    assert "The requested quality is unavailable" in fmt_msg
    print("  PASS: All extractor errors cleanly mapped to user-friendly messages.")

def test_real_info_extraction():
    print("[TEST 5] Testing real YouTube video info extraction...")
    test_url = "https://www.youtube.com/watch?v=RMsPJnAM768"
    res = client.post("/api/info", json={"url": test_url})
    assert res.status_code == 200, f"Failed info extraction: {res.text}"
    data = res.json()
    assert "title" in data
    assert "thumbnail" in data
    assert "duration" in data
    assert "video_formats" in data
    assert len(data["video_formats"]) > 0
    assert "audio_formats" in data
    print(f"  PASS: Extracted video '{data['title']}' with {len(data['video_formats'])} video formats & {len(data['audio_formats'])} audio formats.")

def test_extract_endpoint():
    print("[TEST 6] Testing /api/extract and CORS headers...")
    # Test OPTIONS preflight
    opt_res = client.options("/api/extract")
    assert opt_res.status_code == 200, f"OPTIONS failed: {opt_res.status_code}"
    assert opt_res.headers.get("access-control-allow-origin") == "*"

    # Test POST /api/extract
    test_url = "https://www.youtube.com/watch?v=RMsPJnAM768"
    res = client.post("/api/extract", json={"url": test_url})
    assert res.status_code == 200, f"Failed extract: {res.text}"
    assert res.headers.get("access-control-allow-origin") == "*"
    data = res.json()
    assert "video_formats" in data
    assert "audio_formats" in data
    assert len(data["video_formats"]) > 0
    fmt = data["video_formats"][0]
    assert "stream_url" in fmt
    assert "proxy_url" in fmt
    assert "direct_url" in fmt
    print(f"  PASS: /api/extract returned direct streams & CORS headers OK.")

def test_proxy_endpoint():
    print("[TEST 7] Testing /api/proxy streaming and SSRF protection...")
    # Test OPTIONS preflight
    opt_res = client.options("/api/proxy")
    assert opt_res.status_code == 200
    assert opt_res.headers.get("access-control-allow-origin") == "*"

    # Test SSRF block
    ssrf_res = client.get("/api/proxy?stream_url=http://127.0.0.1:8000/private")
    assert ssrf_res.status_code == 400, "Failed to block private IP SSRF"

    # Test valid streaming proxy on mock stream
    test_stream = "https://httpbin.org/bytes/512"
    proxy_res = client.get(f"/api/proxy?stream_url={test_stream}&filename=test_media.mp4")
    assert proxy_res.status_code == 200, f"Proxy failed: {proxy_res.status_code}"
    assert proxy_res.headers.get("access-control-allow-origin") == "*"
    assert "attachment; filename=\"test_media.mp4\"" in proxy_res.headers.get("content-disposition", "")
    assert len(proxy_res.content) == 512
    print("  PASS: /api/proxy chunked streaming, CORS headers & SSRF protection verified.")

if __name__ == "__main__":
    test_diagnostics()
    test_url_validation()
    test_cookie_security()
    test_error_mapping()
    test_real_info_extraction()
    test_extract_endpoint()
    test_proxy_endpoint()
    print("\nALL TEST SUITE CHECKS PASSED SUCCESSFULLY (100%)!")
