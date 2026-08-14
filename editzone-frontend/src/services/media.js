import { API_BASE_URL } from "./api";
import api from "./api";

export function resolveMediaUrl(value) {
  if (!value || /^(https?:|blob:|data:)/i.test(value)) return value || "";
  const apiOrigin = new URL(API_BASE_URL, window.location.origin).origin;
  return value.startsWith("/") ? `${apiOrigin}${value}` : `${API_BASE_URL}/${value}`;
}

export function isVideoMedia(value) {
  return /\.(mp4|webm|mov|mkv|avi)(?:\?|$)/i.test(value || "");
}

export function mediaFilename(value) {
  const match = String(value || "").match(/\/uploads\/file\/([^?#/]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export async function protectedMediaUrl(value, mode = "preview") {
  const s3Match = String(value || "").match(/\/uploads\/s3\/file\/([^?#/]+)/);
  if (s3Match) {
    const response = await api.post(`/uploads/s3/access/${encodeURIComponent(s3Match[1])}`, null, { params: { mode } });
    return response.data.url;
  }
  const filename = mediaFilename(value);
  if (!filename) return resolveMediaUrl(value);
  const response = await api.post(`/uploads/access/${encodeURIComponent(filename)}`, null, { params: { mode } });
  return resolveMediaUrl(response.data.url);
}

const pollingDelay = (ms, signal) => new Promise((resolve, reject) => {
  const timer = window.setTimeout(resolve, ms);
  signal?.addEventListener("abort", () => { window.clearTimeout(timer); reject(new DOMException("Upload cancelled", "AbortError")); }, { once: true });
});

export async function waitForUploadScan(uploadId, timeoutMs = 150000, signal, statusUrl = `/media/${encodeURIComponent(uploadId)}/status`) {
  const deadline = Date.now() + timeoutMs;
  const apiStatusUrl = String(statusUrl || `/media/${encodeURIComponent(uploadId)}/status`).replace(/^\/api\/v1/, "");
  while (Date.now() < deadline) {
    if (signal?.aborted) throw new DOMException("Upload cancelled", "AbortError");
    const { data } = await api.get(apiStatusUrl, { signal });
    if (data.status === "ready" || data.scan_status === "safe") return data;
    if (data.status === "rejected" || data.scan_status === "infected") throw new Error("The file was blocked because malware was detected");
    if (data.status === "failed" || data.scan_status === "scan_failed") {
      const messages = {
        stream_limit_exceeded: "This file exceeds the configured antivirus scanning limit.",
        scan_timeout: "The media security scan timed out. You can retry the scan.",
        invalid_response: "The media scanner returned an invalid response.",
      };
      const errorCode = data.error_code || data.scan_error;
      const error = new Error(messages[errorCode] || "The media security scan failed. You can retry it.");
      error.code = errorCode === "stream_limit_exceeded" ? "MEDIA_SCAN_LIMIT" : "MEDIA_SCAN_FAILED";
      throw error;
    }
    if (data.scan_status === "rejected") throw new Error("The file was rejected because its content did not match the declared type or size");
    await pollingDelay(2000, signal);
  }
  const error = new Error("Media processing took too long. Check status again.");
  error.code = "MEDIA_PROCESSING";
  error.uploadId = uploadId;
  throw error;
}

export async function retryUploadScan(uploadId, timeoutMs = 150000, signal) {
  const status = await api.get(`/media/${encodeURIComponent(uploadId)}/status`, { signal });
  if (status.data.status === "ready") return status.data;
  if (status.data.status === "failed") {
    await api.post(`/uploads/status/${encodeURIComponent(uploadId)}/retry`, null, { signal });
  }
  return waitForUploadScan(uploadId, timeoutMs, signal);
}

const multipartDelay = (ms, signal) => pollingDelay(ms, signal);

export async function secureUpload(formData, { onProgress, onMetrics, onStage, onProcessing, scanTimeoutMs = 150000, directUploadMinMb = 25, multipartConcurrency = 4, signal, waitForScan = true } = {}) {
  const file = formData.get("file");
  const requestId = formData.get("request_id");
  const purpose = formData.get("purpose");
  if (file instanceof File && requestId && file.type.startsWith("video/") && file.size >= directUploadMinMb * 1024 * 1024) {
    let initiated;
    try {
      ({ data: initiated } = await api.post("/uploads/multipart/initiate", {
        filename: file.name, content_type: file.type, size: file.size,
        request_id: requestId, purpose,
        category: formData.get("category") || undefined,
        view_once: formData.get("view_once") === "true",
      }, { signal, timeout: 30000 }));
    } catch (error) {
      // A development deployment may intentionally use private GridFS without
      // S3. Only storage-not-configured falls back; validation/auth/network
      // errors remain visible and are never bypassed.
      if (error.response?.status !== 503) throw error;
    }
    if (initiated) {
      const completedParts = [];
      let multipartCompleted = false;
      try {
        onStage?.("UPLOADING");
        const startedAt = performance.now();
        const partLoaded = new Map();
        const uploadPart = async (part) => {
          const start = (part.part_number - 1) * initiated.part_size;
          const end = Math.min(start + initiated.part_size, file.size);
          let lastError;
          for (let attempt = 0; attempt < 3; attempt += 1) {
            try {
              const response = await api.put(part.url, file.slice(start, end), {
                baseURL: "", withCredentials: false, headers: { "Content-Type": undefined }, signal, timeout: 10 * 60 * 1000,
                onUploadProgress: (event) => {
                  partLoaded.set(part.part_number, Math.min(end - start, event.loaded));
                  const loaded = [...partLoaded.values()].reduce((sum, value) => sum + value, 0);
                  const elapsed = Math.max(0.001, (performance.now() - startedAt) / 1000);
                  const bytesPerSecond = loaded / elapsed;
                  onProgress?.(Math.min(99, Math.floor((loaded / file.size) * 100)));
                  onMetrics?.({ loaded, total: file.size, bytesPerSecond, etaSeconds: bytesPerSecond ? (file.size - loaded) / bytesPerSecond : null });
                },
              });
              const etag = response.headers.etag;
              if (!etag) throw new Error("S3 did not expose the upload ETag. Add ETag to the bucket CORS ExposeHeaders setting.");
              completedParts.push({ part_number: part.part_number, etag });
              return;
            } catch (error) {
              lastError = error;
              if (signal?.aborted || attempt === 2) throw error;
              await multipartDelay(500 * (2 ** attempt), signal);
            }
          }
          throw lastError;
        };
        let cursor = 0;
        const workers = Array.from({ length: Math.min(Math.max(1, multipartConcurrency), initiated.parts.length) }, async () => {
          while (cursor < initiated.parts.length) {
            const part = initiated.parts[cursor++];
            await uploadPart(part);
          }
        });
        await Promise.all(workers);
        onStage?.("FINALIZING");
        const response = await api.post(`/uploads/multipart/${encodeURIComponent(initiated.upload_id)}/complete`, { parts: completedParts }, { signal, timeout: 60000 });
        multipartCompleted = true;
        onProgress?.(100);
        // Scanner-disabled uploads are ready immediately. Do not briefly put
        // the UI into a scanning state or make a redundant status request.
        if (!waitForScan || response.data.status === "ready" || response.data.scan_status === "safe") return response;
        onStage?.("SCANNING");
        onProcessing?.(response.data);
        try {
          await waitForUploadScan(response.data.upload_id, scanTimeoutMs, signal, response.data.status_url);
        } catch (scanError) {
          scanError.uploadResponse = response;
          throw scanError;
        }
        return response;
      } catch (error) {
        if (!multipartCompleted) await api.delete(`/uploads/multipart/${encodeURIComponent(initiated.upload_id)}`).catch(() => undefined);
        throw error;
      }
    }
  }
  const response = await api.post("/uploads", formData, {
    signal,
    timeout: 10 * 60 * 1000,
    onUploadProgress: (event) => {
      if (onProgress && event.total) onProgress(Math.round((event.loaded / event.total) * 100));
    },
  });
  if (!waitForScan || response.data.status === "ready" || response.data.scan_status === "safe") return response;
  onProcessing?.(response.data);
  try {
    await waitForUploadScan(response.data.upload_id, scanTimeoutMs, signal, response.data.status_url);
  } catch (scanError) {
    scanError.uploadResponse = response;
    throw scanError;
  }
  return response;
}
