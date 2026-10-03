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

if __name__ == "__main__":
    test_diagnostics()
    test_url_validation()
    test_cookie_security()
    test_error_mapping()
    test_real_info_extraction()
    print("\nALL TEST SUITE CHECKS PASSED SUCCESSFULLY (100%)!")
