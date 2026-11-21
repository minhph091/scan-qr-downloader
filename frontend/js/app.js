const state = {
  batchId: null,
  batch: null,
  settings: null,
  filter: "all",
  eventSource: null,
};

const els = {
  sseStatus: document.getElementById("sseStatus"),
  themeToggleBtn: document.getElementById("themeToggleBtn"),
  themeIconSun: document.getElementById("themeIconSun"),
  themeIconMoon: document.getElementById("themeIconMoon"),
  dropzone: document.getElementById("dropzone"),
  selectFilesBtn: document.getElementById("selectFilesBtn"),
  selectFolderImagesBtn: document.getElementById("selectFolderImagesBtn"),
  fileInput: document.getElementById("fileInput"),
  folderInput: document.getElementById("folderInput"),
  addManualUrlBtn: document.getElementById("addManualUrlBtn"),
  outputDirInput: document.getElementById("outputDirInput"),
  pickFolderBtn: document.getElementById("pickFolderBtn"),
  openFolderBtn: document.getElementById("openFolderBtn"),
  namingModeSelect: document.getElementById("namingModeSelect"),
  conflictPolicySelect: document.getElementById("conflictPolicySelect"),
  mediaFilterSelect: document.getElementById("mediaFilterSelect"),
  concurrencyRange: document.getElementById("concurrencyRange"),
  concurrencyValue: document.getElementById("concurrencyValue"),
  autoExpandCheckbox: document.getElementById("autoExpandCheckbox"),
  subfolderMultiCheckbox: document.getElementById("subfolderMultiCheckbox"),
  dedupCheckbox: document.getElementById("dedupCheckbox"),
  autoDownloadCheckbox: document.getElementById("autoDownloadCheckbox"),
  lanQrImg: document.getElementById("lanQrImg"),
  lanUrlSelect: document.getElementById("lanUrlSelect"),
  copyLanUrlBtn: document.getElementById("copyLanUrlBtn"),
  openLanCaptureLink: document.getElementById("openLanCaptureLink"),
  captureDirInput: document.getElementById("captureDirInput"),
  pickCaptureFolderBtn: document.getElementById("pickCaptureFolderBtn"),
  openCaptureFolderBtn: document.getElementById("openCaptureFolderBtn"),
  autoImportLanCheckbox: document.getElementById("autoImportLanCheckbox"),
  lanSavedCount: document.getElementById("lanSavedCount"),
  importCapturesBtn: document.getElementById("importCapturesBtn"),
  statTotal: document.getElementById("statTotal"),
  statReady: document.getElementById("statReady"),
  statActive: document.getElementById("statActive"),
  statCompleted: document.getElementById("statCompleted"),
  statWarn: document.getElementById("statWarn"),
  statFailed: document.getElementById("statFailed"),
  expandLinksBtn: document.getElementById("expandLinksBtn"),
  startDownloadBtn: document.getElementById("startDownloadBtn"),
  retryFailedBtn: document.getElementById("retryFailedBtn"),
  cancelDownloadBtn: document.getElementById("cancelDownloadBtn"),
  exportCsvBtn: document.getElementById("exportCsvBtn"),
  clearBatchBtn: document.getElementById("clearBatchBtn"),
  selectAllCheckbox: document.getElementById("selectAllCheckbox"),
  itemsTableBody: document.getElementById("itemsTableBody"),
  imageModal: document.getElementById("imageModal"),
  imageModalTitle: document.getElementById("imageModalTitle"),
  imageModalImg: document.getElementById("imageModalImg"),
  editModal: document.getElementById("editModal"),
  editModalTitle: document.getElementById("editModalTitle"),
  editItemForm: document.getElementById("editItemForm"),
  editItemId: document.getElementById("editItemId"),
  editMode: document.getElementById("editMode"),
  manualLabelGroup: document.getElementById("manualLabelGroup"),
  manualLabelInput: document.getElementById("manualLabelInput"),
  editUrlInput: document.getElementById("editUrlInput"),
  editFilenameInput: document.getElementById("editFilenameInput"),
  toastContainer: document.getElementById("toastContainer"),
};

const STATUS_LABELS = {
  queued_scan: "Chờ quét QR",
  scanning: "Đang quét QR...",
  ready: "Sẵn sàng tải",
  no_qr: "Không thấy QR",
  duplicate: "URL trùng lặp",
  queued_download: "Trong hàng đợi",
  downloading: "Đang tải...",
  completed: "Hoàn tất",
  skipped: "Đã bỏ qua",
  failed: "Lỗi",
  cancelled: "Đã hủy",
};

function showToast(message, type = "info") {
  const div = document.createElement("div");
  div.className = `toast ${type === "error" ? "toast-error" : type === "success" ? "toast-success" : ""}`;
  div.textContent = message;
  els.toastContainer.appendChild(div);
  setTimeout(() => div.remove(), 3500);
}

function escapeHtml(str) {
  return String(str ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let idx = 0;
  let value = bytes;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  return `${value.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, options);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

/* Theme */
function initTheme() {
  const saved = localStorage.getItem("sqd-theme") || "light";
  applyTheme(saved);
  els.themeToggleBtn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    localStorage.setItem("sqd-theme", next);
  });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  els.themeIconSun.style.display = theme === "dark" ? "block" : "none";
  els.themeIconMoon.style.display = theme === "dark" ? "none" : "block";
}

/* Settings sync */
function populateSettingsUI(settings) {
  state.settings = settings;
  els.outputDirInput.value = settings.output_dir || "";
  els.captureDirInput.value = settings.capture_dir || "";
  els.autoImportLanCheckbox.checked = settings.auto_import_lan_captures !== false;
  els.namingModeSelect.value = settings.naming_mode || "original";
  els.conflictPolicySelect.value = settings.conflict_policy || "rename";
  els.mediaFilterSelect.value = settings.media_filter || "all";
  els.concurrencyRange.value = String(settings.concurrency || 4);
  els.concurrencyValue.textContent = String(settings.concurrency || 4);
  els.autoExpandCheckbox.checked = settings.auto_expand_links !== false;
  els.subfolderMultiCheckbox.checked = Boolean(settings.create_subfolder_for_multi_links);
  els.dedupCheckbox.checked = Boolean(settings.deduplicate_urls);
  els.autoDownloadCheckbox.checked = Boolean(settings.auto_download_after_scan);
}

let settingsDebounceTimer = null;
function scheduleSaveSettings() {
  clearTimeout(settingsDebounceTimer);
  settingsDebounceTimer = setTimeout(async () => {
    const payload = {
      output_dir: els.outputDirInput.value.trim(),
      capture_dir: els.captureDirInput.value.trim(),
      auto_import_lan_captures: els.autoImportLanCheckbox.checked,
      naming_mode: els.namingModeSelect.value,
      conflict_policy: els.conflictPolicySelect.value,
      media_filter: els.mediaFilterSelect.value,
      auto_expand_links: els.autoExpandCheckbox.checked,
      create_subfolder_for_multi_links: els.subfolderMultiCheckbox.checked,
      concurrency: Number(els.concurrencyRange.value),
      deduplicate_urls: els.dedupCheckbox.checked,
      auto_download_after_scan: els.autoDownloadCheckbox.checked,
      timeout_seconds: state.settings?.timeout_seconds || 60,
    };
    try {
      const saved = await apiFetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      state.settings = saved;
      await refreshLanInfo();
    } catch (err) {
      showToast(`Không lưu được cài đặt: ${err.message}`, "error");
    }
  }, 250);
}

async function refreshLanInfo() {
  try {
    const info = await apiFetch("/api/lan/info");
    const urls = info.capture_urls?.length ? info.capture_urls : [info.capture_url];
    const prevSelected = els.lanUrlSelect.value;
    els.lanUrlSelect.innerHTML = urls
      .map((u) => `<option value="${escapeHtml(u)}">${escapeHtml(u)}</option>`)
      .join("");
    const activeUrl = urls.includes(prevSelected) ? prevSelected : urls[0];
    els.lanUrlSelect.value = activeUrl;
    els.openLanCaptureLink.href = activeUrl;
    els.lanQrImg.src = `/api/lan/qr?url=${encodeURIComponent(activeUrl)}`;
    els.lanSavedCount.textContent = String(info.saved_count || 0);
  } catch (_) {}
}

/* SSE realtime updates */
function connectSSE(batchId) {
  if (state.eventSource) {
    state.eventSource.close();
  }
  const es = new EventSource(`/api/batches/${batchId}/events`);
  state.eventSource = es;

  es.onopen = () => {
    els.sseStatus.classList.add("connected");
    els.sseStatus.querySelector(".label").textContent = "Đã kết nối trực tiếp";
  };

  es.onerror = () => {
    els.sseStatus.classList.remove("connected");
    els.sseStatus.querySelector(".label").textContent = "Đang kết nối lại...";
  };

  es.addEventListener("batch_updated", (ev) => {
    state.batch = JSON.parse(ev.data);
    renderAll();
  });

  es.addEventListener("item_updated", (ev) => {
    const { item, summary } = JSON.parse(ev.data);
    if (!state.batch) return;
    const idx = state.batch.items.findIndex((i) => i.id === item.id);
    if (idx >= 0) {
      state.batch.items[idx] = item;
    }
    state.batch.summary = summary;
    renderStatsAndControls();
    renderTable();
  });

  es.addEventListener("item_progress", (ev) => {
    const data = JSON.parse(ev.data);
    if (!state.batch) return;
    const item = state.batch.items.find((i) => i.id === data.id);
    if (!item) return;
    item.downloaded_bytes = data.downloaded_bytes;
    item.total_bytes = data.total_bytes;
    item.speed_bps = data.speed_bps;
    item.progress_percent = data.progress_percent;
    updateRowProgressDOM(item);
  });

  es.addEventListener("lan_captures_changed", (ev) => {
    const data = JSON.parse(ev.data);
    if (typeof data.saved_count === "number") {
      els.lanSavedCount.textContent = String(data.saved_count);
    }
    if (Array.isArray(data.latest) && data.latest.length > 0) {
      showToast(`Đã nhận ${data.latest.length} ảnh chụp mới từ điện thoại (LAN)!`, "success");
    }
  });
}

function updateRowProgressDOM(item) {
  const row = els.itemsTableBody.querySelector(`tr[data-id="${item.id}"]`);
  if (!row) return;
  const fill = row.querySelector(".progress-fill");
  const sizeEl = row.querySelector(".progress-size");
  const speedEl = row.querySelector(".progress-speed");
  if (fill) {
    const pct = item.total_bytes ? item.progress_percent : 45;
    fill.style.width = `${pct}%`;
  }
  if (sizeEl) {
    sizeEl.textContent = item.total_bytes
      ? `${formatBytes(item.downloaded_bytes)} / ${formatBytes(item.total_bytes)}`
      : formatBytes(item.downloaded_bytes);
  }
  if (speedEl) {
    speedEl.textContent = item.speed_bps > 0 ? `${formatBytes(item.speed_bps)}/s` : "";
  }
}

/* Rendering */
function renderAll() {
  renderStatsAndControls();
  renderTable();
}

function renderStatsAndControls() {
  const batch = state.batch;
  if (!batch) return;
  const s = batch.summary;

  els.statTotal.textContent = s.total;
  els.statReady.textContent = s.ready;
  els.statActive.textContent = s.scanning + s.downloading;
  els.statCompleted.textContent = s.completed + s.skipped;
  els.statWarn.textContent = s.no_qr + s.duplicate;
  els.statFailed.textContent = s.failed;

  const readySelectedCount = batch.items.filter(
    (i) => i.selected && i.url && ["ready", "failed", "cancelled"].includes(i.status)
  ).length;

  const hasExpandable = batch.items.some((i) => Boolean(i.url) && ["ready", "failed"].includes(i.status));
  els.expandLinksBtn.disabled = batch.is_scanning || batch.is_downloading || !hasExpandable;

  els.startDownloadBtn.disabled = batch.is_downloading || readySelectedCount === 0;
  els.startDownloadBtn.textContent = batch.is_downloading
    ? "Đang tải..."
    : `Bắt đầu tải (${readySelectedCount})`;

  els.retryFailedBtn.style.display = !batch.is_downloading && s.failed > 0 ? "inline-flex" : "none";
  els.retryFailedBtn.textContent = `Thử lại mục lỗi (${s.failed})`;

  els.cancelDownloadBtn.style.display = batch.is_downloading ? "inline-flex" : "none";
  els.exportCsvBtn.disabled = s.total === 0;
  els.clearBatchBtn.disabled = s.total === 0;

  const selectable = batch.items.filter((i) => Boolean(i.url));
  els.selectAllCheckbox.checked =
    selectable.length > 0 && selectable.every((i) => i.selected);
}

function filterItems(items) {
  switch (state.filter) {
    case "ready":
      return items.filter((i) => i.status === "ready");
    case "downloading":
      return items.filter((i) => ["queued_scan", "scanning", "queued_download", "downloading"].includes(i.status));
    case "completed":
      return items.filter((i) => ["completed", "skipped"].includes(i.status));
    case "no_qr":
      return items.filter((i) => ["no_qr", "duplicate"].includes(i.status));
    case "failed":
      return items.filter((i) => ["failed", "cancelled"].includes(i.status));
    default:
      return items;
  }
}

function renderTable() {
  const batch = state.batch;
  if (!batch || batch.items.length === 0) {
    els.itemsTableBody.innerHTML = `
      <tr class="empty-row">
        <td colspan="7">
          <div class="empty-state">
            <p>Chưa có ảnh nào được nạp.</p>
            <span>Kéo thả ảnh trang sách vào khung phía trên để ứng dụng tự động quét mã QR.</span>
          </div>
        </td>
      </tr>`;
    return;
  }

  const visible = filterItems(batch.items);
  if (visible.length === 0) {
    els.itemsTableBody.innerHTML = `
      <tr class="empty-row">
        <td colspan="7">
          <div class="empty-state">
            <p>Không có mục nào khớp bộ lọc hiện tại.</p>
          </div>
        </td>
      </tr>`;
    return;
  }

  const rowsHtml = visible
    .map((item) => {
      const badgeClass = `badge-${item.status}`;
      const statusText = STATUS_LABELS[item.status] || item.status;
      const pct =
        item.status === "completed" || item.status === "skipped"
          ? 100
          : item.status === "downloading"
          ? item.total_bytes
            ? item.progress_percent
            : 45
          : 0;
      const fillClass =
        item.status === "completed"
          ? "done"
          : item.status === "failed"
          ? "error"
          : "";

      const thumbHtml = item.source_image_path
        ? `<button class="thumb-btn" data-action="preview" data-id="${item.id}" title="Nhấn để phóng to ảnh">
             <img src="/api/batches/${state.batchId}/items/${item.id}/thumbnail" alt="" loading="lazy" />
           </button>`
        : `<div class="thumb-btn" style="cursor:default"><span class="thumb-placeholder">URL</span></div>`;

      const qrMeta = item.qr_index_in_image > 1 ? ` • QR #${item.qr_index_in_image}` : "";
      const trackMeta =
        item.track_index && item.track_total
          ? ` • File ${item.track_index}/${item.track_total}`
          : "";
      const stageMeta = item.scanner_stage ? `${item.scanner_stage}${qrMeta}${trackMeta}` : "Đang chờ";

      const urlBlock = item.url
        ? `<a class="url-link" href="${escapeHtml(item.url)}" target="_blank" rel="noopener" title="${escapeHtml(item.url)}">${escapeHtml(item.url)}</a>`
        : `<span class="error-text">${escapeHtml(item.error_message || "Chưa có URL")}</span>`;

      const trackBadges = [];
      if (item.track_index && item.track_total) {
        trackBadges.push(`<span class="track-chip">Track ${item.track_index}/${item.track_total}</span>`);
      }
      if (item.subfolder_name) {
        trackBadges.push(`<span class="adapter-chip">📁 ${escapeHtml(item.subfolder_name)}</span>`);
      }
      if (item.adapter_name) {
        trackBadges.push(`<span class="adapter-chip">${escapeHtml(item.adapter_name)}</span>`);
      }

      const savedLine = item.saved_filename
        ? `<div class="saved-meta">
             <span>📄 <strong>${escapeHtml(item.saved_filename)}</strong></span>
             ${trackBadges.join("")}
           </div>`
        : item.custom_filename || trackBadges.length
        ? `<div class="saved-meta">
             ${item.custom_filename ? `<span>Tên file: <strong>${escapeHtml(item.custom_filename)}</strong></span>` : ""}
             ${trackBadges.join("")}
           </div>`
        : "";

      const errLine =
        item.url && item.error_message
          ? `<div class="error-text">${escapeHtml(item.error_message)}</div>`
          : "";

      const sizeText =
        item.total_bytes && item.total_bytes > 0
          ? `${formatBytes(item.downloaded_bytes)} / ${formatBytes(item.total_bytes)}`
          : item.downloaded_bytes > 0
          ? formatBytes(item.downloaded_bytes)
          : "—";

      const speedText = item.speed_bps > 0 ? `${formatBytes(item.speed_bps)}/s` : pct === 100 ? "100%" : "";

      const canSingleDownload = Boolean(item.url) && !batch.is_downloading && item.status !== "downloading";

      return `
        <tr data-id="${item.id}">
          <td class="col-check">
            <input type="checkbox" data-action="toggle" data-id="${item.id}" ${item.selected ? "checked" : ""} ${!item.url ? "disabled" : ""} />
          </td>
          <td class="col-index">${String(item.order_index).padStart(2, "0")}</td>
          <td class="col-image">
            <div class="source-cell">
              ${thumbHtml}
              <div class="source-meta">
                <div class="source-name" title="${escapeHtml(item.source_filename)}">${escapeHtml(item.source_filename)}</div>
                <div class="source-sub">${escapeHtml(stageMeta)}</div>
              </div>
            </div>
          </td>
          <td>
            <div class="url-cell">
              ${urlBlock}
              ${savedLine}
              ${errLine}
            </div>
          </td>
          <td class="col-progress">
            <div class="progress-wrap">
              <div class="progress-track">
                <div class="progress-fill ${fillClass}" style="width:${pct}%"></div>
              </div>
              <div class="progress-meta">
                <span class="progress-size">${sizeText}</span>
                <span class="progress-speed">${speedText}</span>
              </div>
            </div>
          </td>
          <td class="col-status">
            <span class="badge ${badgeClass}">${statusText}</span>
          </td>
          <td class="col-actions">
            <div class="row-actions">
              ${
                canSingleDownload
                  ? `<button class="btn btn-ghost btn-sm" data-action="expand-one" data-id="${item.id}" title="Mở link tìm nhiều file MP3 / tài liệu">🔍</button>
                     <button class="btn btn-ghost btn-sm" data-action="download-one" data-id="${item.id}" title="Tải riêng mục này">⬇</button>`
                  : ""
              }
              <button class="btn btn-ghost btn-sm" data-action="edit" data-id="${item.id}" title="Sửa URL / Tên file">✎</button>
              <button class="btn btn-ghost btn-sm" data-action="delete" data-id="${item.id}" title="Xóa khỏi danh sách">✕</button>
            </div>
          </td>
        </tr>`;
    })
    .join("");

  els.itemsTableBody.innerHTML = rowsHtml;
}

/* Upload files */
async function uploadFiles(fileList) {
  const files = Array.from(fileList || []).filter((f) => f.size > 0);
  if (!files.length) return;

  const formData = new FormData();
  for (const file of files) {
    // Use webkitRelativePath or file.name
    formData.append("files", file, file.name);
  }

  showToast(`Đang nạp ${files.length} ảnh và quét mã QR...`);
  try {
    const added = await apiFetch(`/api/batches/${state.batchId}/upload`, {
      method: "POST",
      body: formData,
    });
    showToast(`Đã nạp ${added.length} ảnh hợp lệ.`, "success");
  } catch (err) {
    showToast(`Lỗi tải ảnh lên: ${err.message}`, "error");
  }
}

/* Modals */
function openEditModal(item) {
  els.editMode.value = "edit";
  els.editModalTitle.textContent = `Chỉnh sửa — ${item.source_filename}`;
  els.manualLabelGroup.hidden = true;
  els.editItemId.value = item.id;
  els.editUrlInput.value = item.url || item.raw_qr_text || "";
  els.editFilenameInput.value = item.custom_filename || "";
  els.editModal.hidden = false;
  els.editUrlInput.focus();
}

function openManualUrlModal() {
  els.editMode.value = "manual";
  els.editModalTitle.textContent = "Thêm URL tải thủ công";
  els.manualLabelGroup.hidden = false;
  els.manualLabelInput.value = "";
  els.editItemId.value = "";
  els.editUrlInput.value = "";
  els.editFilenameInput.value = "";
  els.editModal.hidden = false;
  els.editUrlInput.focus();
}

function openImageModal(item) {
  els.imageModalTitle.textContent = item.source_filename;
  els.imageModalImg.src = `/api/batches/${state.batchId}/items/${item.id}/thumbnail?full=true`;
  els.imageModal.hidden = false;
}

/* Event Bindings */
function bindEvents() {
  // Dropzone
  els.selectFilesBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    els.fileInput.click();
  });
  els.selectFolderImagesBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    els.folderInput.click();
  });
  els.dropzone.addEventListener("click", () => els.fileInput.click());

  els.fileInput.addEventListener("change", () => {
    uploadFiles(els.fileInput.files);
    els.fileInput.value = "";
  });
  els.folderInput.addEventListener("change", () => {
    uploadFiles(els.folderInput.files);
    els.folderInput.value = "";
  });

  ["dragenter", "dragover"].forEach((evName) => {
    els.dropzone.addEventListener(evName, (e) => {
      e.preventDefault();
      els.dropzone.classList.add("drag-over");
    });
  });
  ["dragleave", "drop"].forEach((evName) => {
    els.dropzone.addEventListener(evName, (e) => {
      e.preventDefault();
      els.dropzone.classList.remove("drag-over");
    });
  });
  els.dropzone.addEventListener("drop", async (e) => {
    const dt = e.dataTransfer;
    if (!dt) return;
    if (dt.items && dt.items.length && typeof dt.items[0].webkitGetAsEntry === "function") {
      const collected = [];
      const readEntry = (entry) =>
        new Promise((resolve) => {
          if (entry.isFile) {
            entry.file((file) => {
              collected.push(file);
              resolve();
            }, () => resolve());
          } else if (entry.isDirectory) {
            const reader = entry.createReader();
            const readAll = () => {
              reader.readEntries(async (entries) => {
                if (!entries.length) {
                  resolve();
                  return;
                }
                await Promise.all(entries.map(readEntry));
                readAll();
              }, () => resolve());
            };
            readAll();
          } else {
            resolve();
          }
        });
      const entries = Array.from(dt.items)
        .map((it) => it.webkitGetAsEntry())
        .filter(Boolean);
      await Promise.all(entries.map(readEntry));
      if (collected.length) {
        uploadFiles(collected);
        return;
      }
    }
    if (dt.files?.length) {
      uploadFiles(dt.files);
    }
  });

  // Settings controls
  els.outputDirInput.addEventListener("input", scheduleSaveSettings);
  els.captureDirInput.addEventListener("input", scheduleSaveSettings);
  els.autoImportLanCheckbox.addEventListener("change", scheduleSaveSettings);
  els.namingModeSelect.addEventListener("change", scheduleSaveSettings);
  els.conflictPolicySelect.addEventListener("change", scheduleSaveSettings);
  els.mediaFilterSelect.addEventListener("change", scheduleSaveSettings);
  els.concurrencyRange.addEventListener("input", () => {
    els.concurrencyValue.textContent = els.concurrencyRange.value;
    scheduleSaveSettings();
  });
  els.autoExpandCheckbox.addEventListener("change", scheduleSaveSettings);
  els.subfolderMultiCheckbox.addEventListener("change", scheduleSaveSettings);
  els.dedupCheckbox.addEventListener("change", scheduleSaveSettings);
  els.autoDownloadCheckbox.addEventListener("change", scheduleSaveSettings);

  // LAN controls
  els.lanUrlSelect.addEventListener("change", () => {
    const activeUrl = els.lanUrlSelect.value;
    els.openLanCaptureLink.href = activeUrl;
    els.lanQrImg.src = `/api/lan/qr?url=${encodeURIComponent(activeUrl)}`;
  });

  els.copyLanUrlBtn.addEventListener("click", async () => {
    const url = els.lanUrlSelect.value;
    try {
      await navigator.clipboard.writeText(url);
      showToast(`Đã sao chép: ${url}`, "success");
    } catch (_) {
      showToast(`Link LAN: ${url}`);
    }
  });

  els.pickCaptureFolderBtn.addEventListener("click", async () => {
    els.pickCaptureFolderBtn.disabled = true;
    try {
      const current = encodeURIComponent(els.captureDirInput.value.trim());
      const res = await apiFetch(`/api/system/pick-capture-folder?initial_dir=${current}`, {
        method: "POST",
      });
      if (res.path) {
        els.captureDirInput.value = res.path;
        await refreshLanInfo();
        showToast(`Đã chọn thư mục lưu ảnh chụp: ${res.path}`, "success");
      }
    } catch (err) {
      showToast(`Không mở được hộp thoại chọn thư mục: ${err.message}`, "error");
    } finally {
      els.pickCaptureFolderBtn.disabled = false;
    }
  });

  els.openCaptureFolderBtn.addEventListener("click", async () => {
    const dir = encodeURIComponent(els.captureDirInput.value.trim());
    try {
      await apiFetch(`/api/system/open-folder?path=${dir}`, { method: "POST" });
    } catch (err) {
      showToast(`Không mở được thư mục ảnh chụp: ${err.message}`, "error");
    }
  });

  els.importCapturesBtn.addEventListener("click", async () => {
    els.importCapturesBtn.disabled = true;
    try {
      const added = await apiFetch(`/api/batches/${state.batchId}/import-captures`, {
        method: "POST",
      });
      if (!added.length) {
        showToast("Thư mục ảnh chụp hiện đang trống.");
      } else {
        showToast(`Đã nạp ${added.length} ảnh từ thư mục chụp để quét QR!`, "success");
      }
    } catch (err) {
      showToast(`Lỗi nạp ảnh: ${err.message}`, "error");
    } finally {
      els.importCapturesBtn.disabled = false;
    }
  });

  // Native folder picker & open folder
  els.pickFolderBtn.addEventListener("click", async () => {
    els.pickFolderBtn.disabled = true;
    try {
      const current = encodeURIComponent(els.outputDirInput.value.trim());
      const res = await apiFetch(`/api/system/pick-folder?initial_dir=${current}`, {
        method: "POST",
      });
      if (res.path) {
        els.outputDirInput.value = res.path;
        showToast(`Đã chọn thư mục: ${res.path}`, "success");
      }
    } catch (err) {
      showToast(`Không mở được hộp thoại chọn thư mục: ${err.message}`, "error");
    } finally {
      els.pickFolderBtn.disabled = false;
    }
  });

  els.openFolderBtn.addEventListener("click", async () => {
    const dir = encodeURIComponent(els.outputDirInput.value.trim());
    try {
      await apiFetch(`/api/system/open-folder?path=${dir}`, { method: "POST" });
    } catch (err) {
      showToast(`Không mở được thư mục: ${err.message}`, "error");
    }
  });

  // Filter tabs
  document.querySelectorAll(".filter-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".filter-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.filter = tab.dataset.filter;
      renderTable();
    });
  });

  // Batch actions
  els.expandLinksBtn.addEventListener("click", async () => {
    showToast("Đang quét sâu các trang web / thư mục để tách nhiều link MP3...");
    try {
      const updated = await apiFetch(`/api/batches/${state.batchId}/expand`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          media_filter: els.mediaFilterSelect.value,
          create_subfolder_for_multi_links: els.subfolderMultiCheckbox.checked,
        }),
      });
      state.batch = updated;
      renderAll();
      showToast(`Hoàn tất phân tích — hiện có ${updated.summary.total} mục.`, "success");
    } catch (err) {
      showToast(`Lỗi khi tách link: ${err.message}`, "error");
    }
  });

  els.startDownloadBtn.addEventListener("click", async () => {
    const outputDir = els.outputDirInput.value.trim();
    if (!outputDir) {
      showToast("Vui lòng chọn thư mục đích trước khi tải.", "error");
      return;
    }
    try {
      await apiFetch(`/api/batches/${state.batchId}/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          output_dir: outputDir,
          naming_mode: els.namingModeSelect.value,
          conflict_policy: els.conflictPolicySelect.value,
          media_filter: els.mediaFilterSelect.value,
          create_subfolder_for_multi_links: els.subfolderMultiCheckbox.checked,
          concurrency: Number(els.concurrencyRange.value),
        }),
      });
      showToast("Bắt đầu tải các file đã chọn...", "success");
    } catch (err) {
      showToast(`Không thể bắt đầu tải: ${err.message}`, "error");
    }
  });

  els.retryFailedBtn.addEventListener("click", async () => {
    try {
      await apiFetch(`/api/batches/${state.batchId}/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          output_dir: els.outputDirInput.value.trim(),
          retry_failed_only: true,
        }),
      });
      showToast("Đang tải lại các mục bị lỗi...");
    } catch (err) {
      showToast(`Lỗi khi thử lại: ${err.message}`, "error");
    }
  });

  els.cancelDownloadBtn.addEventListener("click", async () => {
    await apiFetch(`/api/batches/${state.batchId}/cancel`, { method: "POST" });
    showToast("Đang dừng tải xuống...");
  });

  els.exportCsvBtn.addEventListener("click", () => {
    window.location.href = `/api/batches/${state.batchId}/report.csv`;
  });

  els.clearBatchBtn.addEventListener("click", async () => {
    if (!confirm("Xóa toàn bộ danh sách ảnh trong phiên hiện tại?")) return;
    await apiFetch(`/api/batches/${state.batchId}/items`, { method: "DELETE" });
    showToast("Đã xóa sạch danh sách.");
  });

  els.selectAllCheckbox.addEventListener("change", async () => {
    if (!state.batch) return;
    const checked = els.selectAllCheckbox.checked;
    const eligible = state.batch.items.filter((i) => Boolean(i.url));
    await Promise.all(
      eligible.map((item) =>
        apiFetch(`/api/batches/${state.batchId}/items/${item.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ selected: checked }),
        })
      )
    );
  });

  // Table row delegated actions
  els.itemsTableBody.addEventListener("click", async (e) => {
    const target = e.target.closest("[data-action]");
    if (!target || !state.batch) return;
    const action = target.dataset.action;
    const itemId = target.dataset.id;
    const item = state.batch.items.find((i) => i.id === itemId);
    if (!item) return;

    if (action === "preview") {
      openImageModal(item);
    } else if (action === "edit") {
      openEditModal(item);
    } else if (action === "delete") {
      await apiFetch(`/api/batches/${state.batchId}/items/${itemId}`, { method: "DELETE" });
    } else if (action === "expand-one") {
      showToast("Đang mở link tìm các file MP3 / tài liệu bên trong...");
      try {
        const updated = await apiFetch(`/api/batches/${state.batchId}/expand`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            item_ids: [itemId],
            media_filter: els.mediaFilterSelect.value,
            create_subfolder_for_multi_links: els.subfolderMultiCheckbox.checked,
          }),
        });
        state.batch = updated;
        renderAll();
        showToast("Đã phân tích xong link.", "success");
      } catch (err) {
        showToast(`Lỗi tách link: ${err.message}`, "error");
      }
    } else if (action === "download-one") {
      try {
        await apiFetch(`/api/batches/${state.batchId}/download`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            output_dir: els.outputDirInput.value.trim(),
            naming_mode: els.namingModeSelect.value,
            conflict_policy: els.conflictPolicySelect.value,
            concurrency: Number(els.concurrencyRange.value),
            item_ids: [itemId],
          }),
        });
      } catch (err) {
        showToast(`Lỗi: ${err.message}`, "error");
      }
    }
  });

  els.itemsTableBody.addEventListener("change", async (e) => {
    const input = e.target.closest('input[data-action="toggle"]');
    if (!input) return;
    const itemId = input.dataset.id;
    await apiFetch(`/api/batches/${state.batchId}/items/${itemId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected: input.checked }),
    });
  });

  // Manual URL & Edit modal
  els.addManualUrlBtn.addEventListener("click", openManualUrlModal);

  els.editItemForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const mode = els.editMode.value;
    const url = els.editUrlInput.value.trim();
    const customFilename = els.editFilenameInput.value.trim();
    try {
      if (mode === "manual") {
        await apiFetch(`/api/batches/${state.batchId}/manual-url`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url,
            label: els.manualLabelInput.value.trim() || null,
            custom_filename: customFilename || null,
          }),
        });
        showToast("Đã thêm URL mới.", "success");
      } else {
        const itemId = els.editItemId.value;
        await apiFetch(`/api/batches/${state.batchId}/items/${itemId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url,
            custom_filename: customFilename,
          }),
        });
        showToast("Đã cập nhật mục.", "success");
      }
      els.editModal.hidden = true;
    } catch (err) {
      showToast(`Lỗi lưu dữ liệu: ${err.message}`, "error");
    }
  });

  document.querySelectorAll("[data-close-modal]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById(btn.dataset.closeModal).hidden = true;
    });
  });

  [els.imageModal, els.editModal].forEach((backdrop) => {
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) backdrop.hidden = true;
    });
  });
}

/* Bootstrap */
async function init() {
  initTheme();
  bindEvents();
  try {
    const [settings, batch] = await Promise.all([
      apiFetch("/api/settings"),
      apiFetch("/api/batches", { method: "POST" }),
    ]);
    populateSettingsUI(settings);
    state.batchId = batch.batch_id;
    state.batch = batch;
    renderAll();
    connectSSE(batch.batch_id);
    await refreshLanInfo();
  } catch (err) {
    showToast(`Không thể khởi tạo phiên làm việc: ${err.message}`, "error");
  }
}

init();
