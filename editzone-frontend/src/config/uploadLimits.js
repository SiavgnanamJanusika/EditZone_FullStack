export const MB = 1024 * 1024;

const envMb = (name, fallback) => {
  const value = Number(import.meta.env[name]);
  return Number.isFinite(value) && value > 0 ? value : fallback;
};

const CHAT_IMAGE_MB = envMb("VITE_MAX_CHAT_IMAGE_MB", 1000);
const CHAT_AUDIO_MB = envMb("VITE_MAX_CHAT_AUDIO_MB", 25);
const CHAT_VIDEO_MB = envMb("VITE_MAX_CHAT_VIDEO_MB", 1000);
const CHAT_FILE_MB = envMb("VITE_MAX_CHAT_FILE_MB", 1000);

export const FILE_LIMIT_MB = Object.freeze({
  profileImage: envMb("VITE_MAX_PROFILE_IMAGE_MB", 10),
  image: CHAT_IMAGE_MB,
  voice: CHAT_AUDIO_MB,
  audio: CHAT_AUDIO_MB,
  document: CHAT_FILE_MB,
  zip: CHAT_FILE_MB,
  video: CHAT_VIDEO_MB,
  viewOnceVideo: CHAT_VIDEO_MB,
  statusImage: envMb("VITE_MAX_STATUS_IMAGE_MB", 25),
  statusVideo: envMb("VITE_MAX_STATUS_VIDEO_MB", 150),
  reelImage: envMb("VITE_MAX_REEL_IMAGE_MB", 20),
  reelVideo: envMb("VITE_MAX_REEL_VIDEO_MB", 500),
});

export const STATUS_CAPTION_MAX_LENGTH = envMb("VITE_STATUS_CAPTION_MAX_LENGTH", 300);

export const FILE_LIMITS = Object.freeze(Object.fromEntries(
  Object.entries(FILE_LIMIT_MB).map(([category, limit]) => [category, limit * MB]),
));
export const MAX_FILES_PER_MESSAGE = envMb("VITE_MAX_FILES_PER_MESSAGE", 5);
export const MAX_TEXT_MESSAGE_LENGTH = envMb("VITE_MAX_TEXT_MESSAGE_LENGTH", 5000);

export function fileCategory(file, { voice = false, viewOnce = false } = {}) {
  if (voice) return "voice";
  const extension = file.name?.split(".").pop()?.toLowerCase();
  if (file.type?.startsWith("image/")) return "image";
  if (file.type?.startsWith("video/")) return viewOnce ? "viewOnceVideo" : "video";
  if (file.type?.startsWith("audio/")) return "audio";
  if (extension === "zip") return "zip";
  if (["pdf", "doc", "docx", "txt"].includes(extension)) return "document";
  return null;
}

export function validateFileSize(file, category) {
  const limit = FILE_LIMITS[category];
  if (!limit) return { valid: false, message: "Unsupported file category." };
  if (!file?.size) return { valid: false, message: "The selected file is empty." };
  if (file.size > limit) {
    return { valid: false, message: `${category === "voice" ? "Voice message" : "File"} exceeds the ${FILE_LIMIT_MB[category]} MB limit.` };
  }
  return { valid: true, limit, limitMb: FILE_LIMIT_MB[category] };
}
