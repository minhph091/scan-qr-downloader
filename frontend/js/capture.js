const els = {
  serverDirText: document.getElementById("serverDirText"),
  savedBadge: document.getElementById("savedBadge"),
  prefixInput: document.getElementById("prefixInput"),
  cameraCaptureBtn: document.getElementById("cameraCaptureBtn"),
  galleryUploadBtn: document.getElementById("galleryUploadBtn"),
  liveCameraBtn: document.getElementById("liveCameraBtn"),
  cameraInput: document.getElementById("cameraInput"),
  galleryInput: document.getElementById("galleryInput"),
  uploadStatusBox: document.getElementById("uploadStatusBox"),
  uploadStatusText: document.getElementById("uploadStatusText"),
  liveCameraSection: document.getElementById("liveCameraSection"),
  liveVideo: document.getElementById("liveVideo"),
  snapLiveBtn: document.getElementById("snapLiveBtn"),
  closeLiveCamBtn: document.getElementById("closeLiveCamBtn"),
  snapCanvas: document.getElementById("snapCanvas"),
  lastResultCard: document.getElementById("lastResultCard"),
  lastResultTag: document.getElementById("lastResultTag"),
  lastResultBody: document.getElementById("lastResultBody"),
  refreshListBtn: document.getElementById("refreshListBtn"),
  capturesList: document.getElementById("capturesList"),
  toast: document.getElementById("toast"),
};

let liveStream = null;
let toastTimer = null;

function showToast(msg, isError = false) {
  clearTimeout(toastTimer);
  els.toast.textContent = msg;
  els.toast.className = `cap-toast ${isError ? "error" : ""}`;
  els.toast.hidden = false;
  toastTimer = setTimeout(() => {
    els.toast.hidden = true;
  }, 3000);
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

async function loadLanInfo() {
  try {
    const res = await fetch("/api/lan/info");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const info = await res.json();
    const parts = (info.capture_dir || "").split(/[\\/]/).filter(Boolean);
    const shortDir = parts.length ? parts[parts.length - 1] : info.capture_dir;
    els.serverDirText.textContent = `Thư mục lưu: ${shortDir}`;
    els.serverDirText.title = info.capture_dir;
    els.savedBadge.textContent = `${info.saved_count} ảnh`;
    renderCaptures(info.recent_captures || []);
  } catch (err) {
    els.serverDirText.textContent = `Lỗi kết nối: ${err.message}`;
  }
}

async function refreshCaptures() {
  try {
    const res = await fetch("/api/lan/captures?limit=30");
    if (!res.ok) return;
    const list = await res.json();
    els.savedBadge.textContent = `${list.length} ảnh`;
    renderCaptures(list);
  } catch (_) {}
}

function renderCaptures(items) {
  if (!items.length) {
    els.capturesList.innerHTML = `<p class="empty-msg">Chưa có ảnh nào trong thư mục lưu chụp.</p>`;
    return;
  }
  els.capturesList.innerHTML = items
    .map(
      (item) => `
      <div class="capture-row">
        <img class="capture-thumb" src="/api/lan/captures/${encodeURIComponent(item.filename)}/thumbnail" alt="" loading="lazy" />
        <div class="capture-meta">
          <div class="capture-name">${escapeHtml(item.filename)}</div>
          <div class="capture-size">${formatBytes(item.size_bytes)}</div>
        </div>
        <button class="btn-del" data-delete="${escapeHtml(item.filename)}" title="Xóa ảnh">✕</button>
      </div>`
    )
    .join("");
}

async function sendFilesToServer(files) {
  const list = Array.from(files || []).filter((f) => f && f.size > 0);
  if (!list.length) return;

  const prefix = els.prefixInput.value.trim();
  if (prefix) {
    localStorage.setItem("sqd-lan-prefix", prefix);
  }

  const formData = new FormData();
  for (const file of list) {
    formData.append("files", file, file.name || "photo.jpg");
  }
  if (prefix) {
    formData.append("prefix", prefix);
  }

  els.uploadStatusBox.hidden = false;
  els.uploadStatusText.textContent = `Đang gửi ${list.length} ảnh lên máy tính...`;

  try {
    const res = await fetch("/api/lan/capture", {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const errBody = await res.json();
        detail = errBody.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    const data = await res.json();
    showUploadFeedback(data.saved || []);
    showToast(`Đã gửi và lưu ${data.saved.length} ảnh thành công!`);
    await loadLanInfo();
  } catch (err) {
    showToast(`Gửi ảnh thất bại: ${err.message}`, true);
  } finally {
    els.uploadStatusBox.hidden = true;
  }
}

function showUploadFeedback(savedList) {
  if (!savedList.length) return;
  els.lastResultCard.hidden = false;
  const anyMissingQr = savedList.some((s) => !s.detected_urls || s.detected_urls.length === 0);
  els.lastResultTag.textContent = anyMissingQr ? "Đã lưu (cần kiểm tra QR)" : "Đã lưu & thấy QR ✓";
  els.lastResultTag.className = `tag ${anyMissingQr ? "warn" : ""}`;

  els.lastResultBody.innerHTML = savedList
    .map((item) => {
      const qrInfo =
        item.detected_urls && item.detected_urls.length > 0
          ? item.detected_urls
              .map((u) => `<div class="result-url">🔗 ${escapeHtml(u)}</div>`)
              .join("")
          : `<div class="result-warn">⚠ Chưa đọc được QR từ ảnh này — bạn có thể chụp gần hơn hoặc giữ chắc tay.</div>`;
      return `
        <div class="result-item">
          <div><strong>${escapeHtml(item.filename)}</strong> (${formatBytes(item.size_bytes)})</div>
          ${qrInfo}
        </div>`;
    })
    .join("");
}

/* Optional Live Camera (works on HTTPS or localhost) */
async function startLiveCamera() {
  try {
    liveStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      audio: false,
    });
    els.liveVideo.srcObject = liveStream;
    els.liveCameraSection.hidden = false;
  } catch (err) {
    showToast(`Không mở được camera trực tiếp: ${err.message}`, true);
  }
}

function stopLiveCamera() {
  if (liveStream) {
    liveStream.getTracks().forEach((t) => t.stop());
    liveStream = null;
  }
  els.liveVideo.srcObject = null;
  els.liveCameraSection.hidden = true;
}

async function snapFromLiveCamera() {
  const video = els.liveVideo;
  if (!video.videoWidth || !video.videoHeight) return;
  els.snapCanvas.width = video.videoWidth;
  els.snapCanvas.height = video.videoHeight;
  const ctx = els.snapCanvas.getContext("2d");
  ctx.drawImage(video, 0, 0);
  const blob = await new Promise((resolve) => els.snapCanvas.toBlob(resolve, "image/jpeg", 0.92));
  if (blob) {
    const file = new File([blob], `live_${Date.now()}.jpg`, { type: "image/jpeg" });
    await sendFilesToServer([file]);
  }
}

/* Init */
function init() {
  const savedPrefix = localStorage.getItem("sqd-lan-prefix");
  if (savedPrefix) els.prefixInput.value = savedPrefix;

  if (window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    els.liveCameraBtn.hidden = false;
  }

  els.cameraCaptureBtn.addEventListener("click", () => els.cameraInput.click());
  els.galleryUploadBtn.addEventListener("click", () => els.galleryInput.click());

  els.cameraInput.addEventListener("change", () => {
    sendFilesToServer(els.cameraInput.files);
    els.cameraInput.value = "";
  });
  els.galleryInput.addEventListener("change", () => {
    sendFilesToServer(els.galleryInput.files);
    els.galleryInput.value = "";
  });

  els.liveCameraBtn.addEventListener("click", startLiveCamera);
  els.closeLiveCamBtn.addEventListener("click", stopLiveCamera);
  els.snapLiveBtn.addEventListener("click", snapFromLiveCamera);
  els.refreshListBtn.addEventListener("click", loadLanInfo);

  els.capturesList.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-delete]");
    if (!btn) return;
    const name = btn.dataset.delete;
    if (!confirm(`Xóa ảnh "${name}" khỏi thư mục trên máy tính?`)) return;
    const res = await fetch(`/api/lan/captures/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (res.ok) {
      showToast("Đã xóa ảnh.");
      await loadLanInfo();
    }
  });

  loadLanInfo();
}

init();
