// Particle background simulation matching mk.mp4 bokeh palette
const canvas = document.getElementById('bg-canvas');
const ctx = canvas.getContext('2d');
let particles = [];

function resizeCanvas() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

class Particle {
  constructor() {
    this.x = Math.random() * canvas.width;
    this.y = Math.random() * canvas.height;
    this.size = Math.random() * 2.2 + 0.8;
    this.speedX = (Math.random() - 0.5) * 0.5;
    this.speedY = (Math.random() - 0.5) * 0.5;
    this.color = ['#10b981', '#34d399', '#6ee7b7', '#06b6d4', '#a7f3d0'][Math.floor(Math.random() * 5)];
    this.alpha = Math.random() * 0.55 + 0.2;
  }
  update() {
    this.x += this.speedX;
    this.y += this.speedY;
    if (this.x < 0 || this.x > canvas.width) this.speedX *= -1;
    if (this.y < 0 || this.y > canvas.height) this.speedY *= -1;
  }
  draw() {
    ctx.save();
    ctx.globalAlpha = this.alpha;
    ctx.fillStyle = this.color;
    ctx.shadowBlur = 10;
    ctx.shadowColor = this.color;
    ctx.beginPath();
    ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }
}

function initParticles() {
  particles = [];
  const count = Math.min(window.innerWidth / 16, 65);
  for (let i = 0; i < count; i++) {
    particles.push(new Particle());
  }
}
initParticles();

function animateParticles() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  for (let i = 0; i < particles.length; i++) {
    particles[i].update();
    particles[i].draw();

    // Connect close particles with thin atmospheric lines
    for (let j = i + 1; j < particles.length; j++) {
      const dx = particles[i].x - particles[j].x;
      const dy = particles[i].y - particles[j].y;
      const dist = Math.hypot(dx, dy);
      if (dist < 115) {
        ctx.save();
        ctx.strokeStyle = '#578ec2';
        ctx.globalAlpha = (1 - dist / 115) * 0.12;
        ctx.beginPath();
        ctx.moveTo(particles[i].x, particles[i].y);
        ctx.lineTo(particles[j].x, particles[j].y);
        ctx.stroke();
        ctx.restore();
      }
    }
  }
  requestAnimationFrame(animateParticles);
}
animateParticles();

// Fullscreen Background Video & 3D Parallax Movement
const bgVideo = document.getElementById('bg-video');
const btnSoundToggle = document.getElementById('btn-sound-toggle');

if (bgVideo) {
  // Ensure video autoplays smoothly
  bgVideo.play().catch(() => {});

  // 3D Parallax Mouse Tracking for Background Video
  window.addEventListener('mousemove', (e) => {
    const deltaX = (e.clientX - window.innerWidth / 2) / (window.innerWidth / 2);
    const deltaY = (e.clientY - window.innerHeight / 2) / (window.innerHeight / 2);

    // Subtle 3D camera pan without pixel distortion to keep 100% sharp HD
    const moveX = -deltaX * 10;
    const moveY = -deltaY * 10;
    bgVideo.style.transform = `translate(${moveX}px, ${moveY}px)`;
  });

  // Sound Toggle Controller
  if (btnSoundToggle) {
    btnSoundToggle.addEventListener('click', () => {
      bgVideo.muted = !bgVideo.muted;
      if (bgVideo.muted) {
        btnSoundToggle.classList.remove('active');
        btnSoundToggle.innerHTML = `<i class="fa-solid fa-volume-xmark"></i> <span>Sound</span>`;
        showToast('Background audio muted', 'info');
      } else {
        btnSoundToggle.classList.add('active');
        btnSoundToggle.innerHTML = `<i class="fa-solid fa-volume-high"></i> <span>Sound ON</span>`;
        bgVideo.play().catch(() => {});
        showToast('Background audio enabled!', 'success');
      }
    });
  }
}

// Application State
let currentVideoData = null;
let selectedFormatType = 'video'; // 'video' or 'audio'
let selectedQuality = '1080p';
let selectedAudioBitrate = '320k';
let activeTaskId = null;
let pollInterval = null;

// DOM Elements
const videoUrlInput = document.getElementById('video-url');
const btnPaste = document.getElementById('btn-paste');
const btnClear = document.getElementById('btn-clear');
const btnFetch = document.getElementById('btn-fetch');
const previewSection = document.getElementById('preview-section');
const btnClosePreview = document.getElementById('btn-close-preview');

const videoThumb = document.getElementById('video-thumb');
const videoDuration = document.getElementById('video-duration');
const videoAuthor = document.getElementById('video-author');
const videoViews = document.getElementById('video-views');
const videoTitle = document.getElementById('video-title');
const videoQualitiesContainer = document.getElementById('video-qualities');
const audioQualitiesContainer = document.getElementById('audio-qualities');

const tabButtons = document.querySelectorAll('.tab-btn');
const tabContents = document.querySelectorAll('.tab-content');
const btnStartDownload = document.getElementById('btn-start-download');
const downloadBtnLabel = document.getElementById('download-btn-label');
const btnDirectDownload = document.getElementById('btn-direct-download');
const directBtnLabel = document.getElementById('direct-btn-label');

const downloadTracker = document.getElementById('download-tracker');
const trackerStatus = document.getElementById('tracker-status');
const trackerPercent = document.getElementById('tracker-percent');
const progressBarFill = document.getElementById('progress-bar-fill');
const trackerSpeed = document.getElementById('tracker-speed');
const trackerEta = document.getElementById('tracker-eta');

const toastContainer = document.getElementById('toast-container');
const historySection = document.getElementById('history-section');
const historyList = document.getElementById('history-list');
const btnClearHistory = document.getElementById('btn-clear-history');

// Toast Notification
function showToast(message, type = 'info') {
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  let icon = 'info-circle';
  if (type === 'success') icon = 'circle-check';
  if (type === 'error') icon = 'triangle-exclamation';

  toast.innerHTML = `<i class="fa-solid fa-${icon}"></i> <span>${message}</span>`;
  toastContainer.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(50px)';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Input change listeners
videoUrlInput.addEventListener('input', () => {
  if (videoUrlInput.value.trim().length > 0) {
    btnClear.classList.remove('hidden');
  } else {
    btnClear.classList.add('hidden');
  }
});

btnClear.addEventListener('click', () => {
  videoUrlInput.value = '';
  btnClear.classList.add('hidden');
  videoUrlInput.focus();
});

// 1-Click Clipboard Paste Feature
btnPaste.addEventListener('click', async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (text && (text.includes('youtube.com') || text.includes('youtu.be'))) {
      videoUrlInput.value = text.trim();
      btnClear.classList.remove('hidden');
      showToast('Link pasted automatically! Fetching details...', 'success');
      fetchVideoInfo(text.trim());
    } else if (text) {
      videoUrlInput.value = text.trim();
      btnClear.classList.remove('hidden');
      showToast('Pasted from clipboard', 'info');
    } else {
      showToast('Clipboard is empty!', 'error');
    }
  } catch (err) {
    videoUrlInput.focus();
    showToast('Clipboard permission denied. Please paste manually.', 'info');
  }
});

// Quick shortcut pills
document.querySelectorAll('.quick-pill').forEach(pill => {
  pill.addEventListener('click', () => {
    const url = pill.getAttribute('data-url');
    videoUrlInput.value = url;
    btnClear.classList.remove('hidden');
    fetchVideoInfo(url);
  });
});

// Close Preview
btnClosePreview.addEventListener('click', () => {
  previewSection.classList.add('hidden');
});

// Fetch Video Details
btnFetch.addEventListener('click', () => {
  const url = videoUrlInput.value.trim();
  if (!url) {
    showToast('Kripya pehle YouTube video link dalein!', 'error');
    videoUrlInput.focus();
    return;
  }
  fetchVideoInfo(url);
});

videoUrlInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    btnFetch.click();
  }
});

async function fetchVideoInfo(url) {
  const cleanUrl = (url || '').trim();
  if (!cleanUrl) {
    showToast('Please enter a YouTube video URL', 'error');
    videoUrlInput.focus();
    return;
  }

  // Basic client-side validation
  if (!cleanUrl.includes('youtube.com') && !cleanUrl.includes('youtu.be')) {
    showToast('Invalid YouTube URL. Please provide a valid youtube.com or youtu.be link.', 'error');
    videoUrlInput.focus();
    return;
  }

  // Set loading state
  const btnText = btnFetch.querySelector('.btn-text');
  const spinner = btnFetch.querySelector('.spinner');
  btnText.classList.add('hidden');
  spinner.classList.remove('hidden');
  btnFetch.disabled = true;

  try {
    let response = await fetch('/api/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: cleanUrl })
    });

    // Fallback to /api/info if /api/extract returned non-ok
    if (!response.ok) {
      response = await fetch('/api/info', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: cleanUrl })
      });
    }

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || 'Failed to extract video streams');
    }

    currentVideoData = data;
    renderVideoPreview(data);
    showToast('Video and stream links extracted successfully!', 'success');
  } catch (err) {
    showToast(err.message || 'Error communicating with server', 'error');
  } finally {
    btnText.classList.remove('hidden');
    spinner.classList.add('hidden');
    btnFetch.disabled = false;
  }
}

function getActiveFormatData() {
  if (!currentVideoData) {
    return { streamUrl: '', proxyUrl: '', filename: 'video.mp4', ext: 'mp4', qualityLabel: '', isProgressive: false };
  }

  const safeTitle = (currentVideoData.title || 'video').replace(/[/\\?%*:|"<>]/g, '').trim() || 'video';

  if (selectedFormatType === 'video') {
    const formats = currentVideoData.video_formats || [];
    const fmt = formats.find(f => f.resolution === selectedQuality) || formats[0] || {};
    const ext = fmt.ext || 'mp4';
    const streamUrl = fmt.stream_url || fmt.direct_url || '';
    const filename = `${safeTitle}_${fmt.resolution || 'video'}.${ext}`;
    const proxyUrl = fmt.proxy_url || (streamUrl ? `/api/proxy?stream_url=${encodeURIComponent(streamUrl)}&filename=${encodeURIComponent(filename)}` : '');
    return {
      streamUrl,
      proxyUrl,
      filename,
      ext,
      qualityLabel: fmt.resolution || selectedQuality,
      isProgressive: fmt.is_progressive || false
    };
  } else {
    const audioFormats = currentVideoData.audio_formats || [];
    const afmt = audioFormats.find(a => a.bitrate === selectedAudioBitrate) || audioFormats[0] || {};
    const ext = afmt.ext || 'mp3';
    const streamUrl = afmt.stream_url || afmt.direct_url || '';
    const filename = `${safeTitle}_${afmt.bitrate || 'audio'}.${ext}`;
    const proxyUrl = afmt.proxy_url || (streamUrl ? `/api/proxy?stream_url=${encodeURIComponent(streamUrl)}&filename=${encodeURIComponent(filename)}` : '');
    return {
      streamUrl,
      proxyUrl,
      filename,
      ext,
      qualityLabel: afmt.label || selectedAudioBitrate,
      isProgressive: false
    };
  }
}

function renderVideoPreview(data) {
  videoThumb.src = data.thumbnail || 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=600';
  videoDuration.textContent = data.duration;
  videoAuthor.innerHTML = `<i class="fa-solid fa-circle-user"></i> ${data.uploader}`;
  videoViews.innerHTML = `<i class="fa-solid fa-eye"></i> ${data.views} views`;
  videoTitle.textContent = data.title;

  // Render video qualities
  videoQualitiesContainer.innerHTML = '';
  const formats = data.video_formats || [];
  
  if (formats.length > 0) {
    selectedQuality = formats[0].resolution; // Default to highest available
    formats.forEach((fmt, index) => {
      const card = document.createElement('div');
      card.className = `quality-card ${fmt.glow || 'glow-sd'} ${index === 0 ? 'selected' : ''}`;
      card.dataset.res = fmt.resolution;

      const fpsHtml = fmt.fps > 30 ? `<span class="fps-badge">${fmt.fps}fps</span>` : '';

      card.innerHTML = `
        <span class="tier-badge">${fmt.badge || fmt.category}</span>
        <div>
          <span class="res-label">${fmt.height}p ${fpsHtml}</span>
          <span class="size-label">${fmt.filesize_str}</span>
        </div>
      `;
      card.addEventListener('click', () => {
        document.querySelectorAll('.quality-card').forEach(c => c.classList.remove('selected'));
        card.classList.add('selected');
        selectedQuality = fmt.resolution;
        updateDownloadButtonText();
      });
      videoQualitiesContainer.appendChild(card);
    });
  } else {
    selectedQuality = '1080p';
  }

  // Render audio qualities
  if (audioQualitiesContainer) {
    audioQualitiesContainer.innerHTML = '';
    const audioFormats = data.audio_formats || [];
    selectedAudioBitrate = audioFormats[0]?.bitrate || '320k';

    audioFormats.forEach((afmt, index) => {
      const card = document.createElement('div');
      card.className = `audio-option-card ${index === 0 ? 'selected' : ''}`;
      card.dataset.bitrate = afmt.bitrate;
      card.innerHTML = `
        <div class="audio-info">
          <div class="audio-icon"><i class="fa-solid fa-${afmt.ext === 'm4a' ? 'bolt' : 'headphones'}"></i></div>
          <div>
            <h4>${afmt.label}</h4>
            <p>${afmt.desc}</p>
          </div>
        </div>
        <span class="badge badge-glow">${afmt.badge}</span>
      `;
      card.addEventListener('click', () => {
        document.querySelectorAll('.audio-option-card').forEach(c => c.classList.remove('selected'));
        card.classList.add('selected');
        selectedAudioBitrate = afmt.bitrate;
        updateDownloadButtonText();
      });
      audioQualitiesContainer.appendChild(card);
    });
  }

  // Reset tab to video
  switchTab('video');
  updateDownloadButtonText();

  // Reset progress tracker
  downloadTracker.classList.add('hidden');
  progressBarFill.style.width = '0%';

  previewSection.classList.remove('hidden');
  previewSection.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// Tab Switching
tabButtons.forEach(btn => {
  btn.addEventListener('click', () => {
    const tabName = btn.dataset.tab;
    switchTab(tabName);
  });
});

function switchTab(tabName) {
  selectedFormatType = tabName;
  tabButtons.forEach(b => b.classList.toggle('active', b.dataset.tab === tabName));
  tabContents.forEach(c => c.classList.toggle('active', c.id === `tab-content-${tabName}`));
  updateDownloadButtonText();
}

function updateDownloadButtonText() {
  const active = getActiveFormatData();

  if (selectedFormatType === 'video') {
    if (downloadBtnLabel) downloadBtnLabel.textContent = `Download Video (${selectedQuality} MP4)`;
    if (directBtnLabel) directBtnLabel.textContent = `Direct CDN Link (${selectedQuality})`;
  } else {
    const label = selectedAudioBitrate === 'original' ? 'Original M4A' : `MP3 (${selectedAudioBitrate})`;
    if (downloadBtnLabel) downloadBtnLabel.textContent = `Download Audio (${label})`;
    if (directBtnLabel) directBtnLabel.textContent = `Direct CDN Link (${label})`;
  }

  if (btnDirectDownload) {
    if (active.streamUrl) {
      btnDirectDownload.href = active.streamUrl;
      btnDirectDownload.setAttribute('download', active.filename);
      btnDirectDownload.classList.remove('disabled');
      btnDirectDownload.removeAttribute('disabled');
    } else {
      btnDirectDownload.href = '#';
      btnDirectDownload.classList.add('disabled');
    }
  }
}

// Direct CDN Link Download Click Handler (Zero Server Load)
if (btnDirectDownload) {
  btnDirectDownload.addEventListener('click', (e) => {
    const active = getActiveFormatData();
    if (!active.streamUrl) {
      e.preventDefault();
      showToast('No direct stream URL available for this format', 'error');
      return;
    }

    showToast('Opening direct stream from YouTube CDN! Zero server load.', 'success');

    // Save to history
    saveToHistory({
      title: currentVideoData?.title || 'YouTube Video',
      quality: `Direct ${active.qualityLabel}`,
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    });
  });
}

// Download Click Handler
btnStartDownload.addEventListener('click', async () => {
  if (!currentVideoData) return;

  const active = getActiveFormatData();
  const url = currentVideoData.url;
  const taskId = 'task_' + Math.random().toString(36).substring(2, 9);
  activeTaskId = taskId;

  // Case 1: Progressive format (video + audio combined) or original audio stream
  if ((selectedFormatType === 'video' && active.isProgressive && active.proxyUrl) || (selectedFormatType === 'audio' && selectedAudioBitrate === 'original' && active.proxyUrl)) {
    const proxyDownloadUrl = active.proxyUrl;

    downloadTracker.classList.remove('hidden');
    trackerStatus.innerHTML = `<i class="fa-solid fa-bolt-lightning fa-bounce" style="color: #38bdf8;"></i> Downloading ${active.qualityLabel} directly to your device...`;
    trackerPercent.textContent = '100%';
    progressBarFill.style.width = '100%';
    trackerSpeed.textContent = 'Speed: High-Speed Direct';
    trackerEta.textContent = 'Status: Direct Browser Stream';

    if (typeof confetti === 'function') confetti({ particleCount: 70, spread: 65, origin: { y: 0.6 } });

    showToast(`Downloading ${active.qualityLabel}! Saved in your downloads.`, 'success');

    const link = document.createElement('a');
    link.href = proxyDownloadUrl;
    link.setAttribute('download', active.filename);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    saveToHistory({
      title: currentVideoData.title,
      quality: active.qualityLabel,
      time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    });

    setTimeout(() => {
      trackerStatus.innerHTML = `<i class="fa-solid fa-circle-check" style="color: #4ade80;"></i> Download initiated! Check your downloads bar.`;
    }, 2500);
    return;
  }

  // Case 2: High Definition formats (4K, 2K, 1080p, 720p HD) or converted MP3 (320k)
  // Uses /api/download so server FFmpeg merges crisp video + high-quality audio into a complete MP4
  downloadTracker.classList.remove('hidden');
  trackerStatus.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i> Preparing ${active.qualityLabel} with full audio...`;
  trackerPercent.textContent = '5%';
  progressBarFill.style.width = '5%';
  trackerSpeed.textContent = 'Speed: Initializing';
  trackerEta.textContent = 'Merging video & audio';
  btnStartDownload.disabled = true;

  if (typeof confetti === 'function') confetti({ particleCount: 60, spread: 60, origin: { y: 0.6 } });

  showToast(`Processing ${active.qualityLabel} (Audio + Video Merging)...`, 'info');

  startProgressPolling(taskId);

  const downloadUrl = `/api/download?url=${encodeURIComponent(url)}&format_type=${selectedFormatType}&quality=${encodeURIComponent(selectedQuality)}&bitrate=${encodeURIComponent(selectedAudioBitrate)}&task_id=${taskId}`;
  const link = document.createElement('a');
  link.href = downloadUrl;
  link.setAttribute('download', active.filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);

  saveToHistory({
    title: currentVideoData.title,
    quality: selectedFormatType === 'video' ? selectedQuality : `MP3 ${selectedAudioBitrate}`,
    time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  });
});

function startProgressPolling(taskId) {
  if (pollInterval) clearInterval(pollInterval);

  pollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/progress/${taskId}`);
      if (!res.ok) return;
      const data = await res.json();

      if (data.status === 'downloading') {
        const pct = data.percent || 0;
        progressBarFill.style.width = `${pct}%`;
        trackerPercent.textContent = `${pct}%`;
        trackerStatus.innerHTML = `<i class="fa-solid fa-cloud-arrow-down fa-bounce"></i> Fetching stream...`;
        if (data.speed) trackerSpeed.textContent = `Speed: ${data.speed}`;
        if (data.eta) trackerEta.textContent = `ETA: ${data.eta}`;
      } else if (data.status === 'processing') {
        progressBarFill.style.width = `95%`;
        trackerPercent.textContent = `95%`;
        trackerStatus.innerHTML = `<i class="fa-solid fa-gear fa-spin"></i> Merging audio & video via FFmpeg...`;
        trackerSpeed.textContent = 'Finalizing file';
        trackerEta.textContent = 'Almost done...';
      } else if (data.status === 'complete') {
        progressBarFill.style.width = `100%`;
        trackerPercent.textContent = `100%`;
        trackerStatus.innerHTML = `<i class="fa-solid fa-circle-check" style="color: #4ade80;"></i> Download Started! Check your downloads folder.`;
        trackerSpeed.textContent = 'Complete';
        trackerEta.textContent = 'Done';
        btnStartDownload.disabled = false;
        clearInterval(pollInterval);
        showToast('Download finished! File saved.', 'success');
      } else if (data.status === 'error') {
        trackerStatus.innerHTML = `<i class="fa-solid fa-circle-xmark" style="color: #ef4444;"></i> Error: ${data.error || 'Failed'}`;
        btnStartDownload.disabled = false;
        clearInterval(pollInterval);
      }
    } catch (e) {
      // Ignore network tick errors
    }
  }, 1000);

  // Stop polling after 5 minutes max to avoid hanging
  setTimeout(() => {
    if (pollInterval) {
      clearInterval(pollInterval);
      btnStartDownload.disabled = false;
    }
  }, 300000);
}

// Local Storage History
function saveToHistory(item) {
  try {
    let history = JSON.parse(localStorage.getItem('yt_dl_history') || '[]');
    history.unshift(item);
    if (history.length > 5) history = history.slice(0, 5);
    localStorage.setItem('yt_dl_history', JSON.stringify(history));
    renderHistory();
  } catch (e) {}
}

function renderHistory() {
  try {
    const history = JSON.parse(localStorage.getItem('yt_dl_history') || '[]');
    if (history.length === 0) {
      historySection.classList.add('hidden');
      return;
    }

    historySection.classList.remove('hidden');
    historyList.innerHTML = history.map(item => `
      <div class="history-item">
        <span style="font-weight: 600; max-width: 65%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
          <i class="fa-solid fa-circle-check" style="color: #4ade80; margin-right: 6px;"></i> ${item.title}
        </span>
        <span style="color: var(--text-muted); font-size: 0.8rem;">
          <span class="badge badge-glow" style="margin-right: 8px;">${item.quality}</span> ${item.time}
        </span>
      </div>
    `).join('');
  } catch (e) {}
}

btnClearHistory.addEventListener('click', () => {
  localStorage.removeItem('yt_dl_history');
  renderHistory();
  showToast('History cleared', 'info');
});

// Load history on boot
renderHistory();
