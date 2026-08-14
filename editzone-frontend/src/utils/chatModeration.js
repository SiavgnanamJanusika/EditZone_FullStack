const PHONE_CONTEXT = /\b(?:call|phone|mobile|whatsapp|contact|ring|text me|number)\b/i;
const CONTACT_LINK = /(?:\btel\s*:\s*\+?(?:[0-9][\s().-]*){7,15}|(?:https?:\/\/)?wa\.me\/\+?(?:[0-9][\s().-]*){7,15}|(?:https?:\/\/)?api\.whatsapp\.com\/send\?[^\s]*\bphone=\+?(?:[0-9][\s().-]*){7,15})/i;
const CANDIDATE = /(?:^|[^\w])((?:\+?[0-9][\s().\-–—_/\\]*){7,15})(?![\w])/g;

export const PHONE_BLOCK_MESSAGE = "Phone numbers cannot be shared in chat. Please communicate through EditZone for your security.";

const DIGIT_RANGES = [
  0x0660, 0x06f0, 0x07c0, 0x0966, 0x09e6, 0x0a66, 0x0ae6, 0x0b66,
  0x0be6, 0x0c66, 0x0ce6, 0x0d66, 0x0de6, 0x0e50, 0x0ed0, 0x0f20,
  0x1040, 0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90,
  0x1b50, 0x1bb0, 0x1c40, 0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0,
  0xa9f0, 0xaa50, 0xabf0, 0xff10,
];

function normalizeUnicodeDigits(text) {
  return String(text || "").normalize("NFKC").replace(/\p{Nd}/gu, (char) => {
    const point = char.codePointAt(0);
    const start = DIGIT_RANGES.find((value) => point >= value && point <= value + 9);
    return start == null ? char : String(point - start);
  });
}

export function containsPhoneNumber(text) {
  const value = normalizeUnicodeDigits(text).toLowerCase();
  if (CONTACT_LINK.test(value)) return true;
  for (const match of value.matchAll(CANDIDATE)) {
    const raw = match[1];
    const digits = raw.replace(/\D/g, "");
    if (/^(?:0094|94|0)7\d{8}$/.test(digits)) return true;
    if (raw.trimStart().startsWith("+") && digits.length >= 8 && digits.length <= 15) return true;
    if (digits.startsWith("00") && digits.length >= 10 && digits.length <= 15) return true;
    if (PHONE_CONTEXT.test(value) && digits.length >= 7 && digits.length <= 15) return true;
    const separators = (raw.match(/[\s().\-–—_/\\]/g) || []).length;
    if (digits.length >= 9 && digits.length <= 15 && separators >= 3) return true;
    if (digits.length >= 10 && digits.length <= 15 && separators === 0) return true;
  }
  return false;
}
