import type { Attachment } from "./types";

// Mirrors the current backend ingress contract. These are intentionally conservative:
// an 8 MiB image stays below MAX_IMAGE_CHARS after base64 expansion, while a 10 MiB PDF
// stays below MAX_PDF_CHARS and the 15 MB per-message JSON cap.
export const MAX_ATTACHMENTS = 8;
export const MAX_ATTACHMENT_PAYLOAD_BYTES = 15_000_000;
export const MAX_IMAGE_FILE_BYTES = 8 * 1024 * 1024;
export const MAX_PDF_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_TEXT_FILE_BYTES = 200_000;
export const MAX_IMAGE_DATA_URL_CHARS = 12_000_000;
export const MAX_PDF_DATA_URL_CHARS = 15_000_000;
export const MAX_TEXT_CHARS = 200_000;

export const TEXT_FILE_EXTENSIONS = [
  "txt", "md", "markdown", "csv", "tsv", "json", "yaml", "yml", "log", "ini",
  "toml", "py", "js", "ts", "tsx", "jsx", "rs", "go", "java", "c", "h", "cpp",
  "sh", "html", "htm", "css", "sql", "xml",
] as const;
const TEXT_RE = new RegExp(`\\.(${TEXT_FILE_EXTENSIONS.join("|")})$`, "i");
export const TEXT_FILE_ACCEPT = `text/*,${TEXT_FILE_EXTENSIONS.map((ext) => `.${ext}`).join(",")}`;

export type AttachmentRejectCode =
  | "unsupported"
  | "file_too_large"
  | "read_failed"
  | "too_many"
  | "total_too_large"
  | "duplicate"
  | "invalid_payload";

export type AttachmentReject = {
  ok: false;
  code: AttachmentRejectCode;
  name: string;
  maxBytes?: number;
};

export type ReadFileResult = { ok: true; attachment: Attachment } | AttachmentReject;

export const isPdfFile = (file: File) =>
  file.type === "application/pdf" || /\.pdf$/i.test(file.name);

export const attachmentFileKind = (file: File): Attachment["kind"] | null => {
  if (file.type.startsWith("image/")) return "image";
  if (isPdfFile(file)) return "pdf";
  if (file.type.startsWith("text/") || TEXT_RE.test(file.name)) return "text";
  return null;
};

const maxBytesForKind = (kind: Attachment["kind"]): number =>
  kind === "image"
    ? MAX_IMAGE_FILE_BYTES
    : kind === "pdf"
      ? MAX_PDF_FILE_BYTES
      : MAX_TEXT_FILE_BYTES;

/** Read one file according to the same policy used by picker, drag/drop, and paste. */
export function readFile(file: File): Promise<ReadFileResult> {
  const kind = attachmentFileKind(file);
  const name = file.name || (kind === "image" ? "image" : kind === "pdf" ? "file.pdf" : "file.txt");
  if (!kind) return Promise.resolve({ ok: false, code: "unsupported", name });
  const maxBytes = maxBytesForKind(kind);
  if (file.size > maxBytes) {
    return Promise.resolve({ ok: false, code: "file_too_large", name, maxBytes });
  }
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onerror = () => resolve({ ok: false, code: "read_failed", name });
    reader.onabort = () => resolve({ ok: false, code: "read_failed", name });
    reader.onload = () => {
      const result = String(reader.result ?? "");
      const attachment: Attachment =
        kind === "image"
          ? { kind, name, mime: file.type, data_url: result }
          : kind === "pdf"
            ? { kind, name, mime: "application/pdf", data_url: result }
            : { kind, name, mime: file.type, text: result };
      resolve(validateAttachment(attachment) ? { ok: false, code: "invalid_payload", name } : { ok: true, attachment });
    };
    if (kind === "image" || kind === "pdf") reader.readAsDataURL(file);
    else reader.readAsText(file);
  });
}

export const attachmentKey = (attachment: Attachment): string =>
  attachment.kind === "text"
    ? `t:${attachment.name}:${attachment.text?.length ?? 0}`
    : `${attachment.kind[0]}:${attachment.name}:${attachment.data_url?.length ?? 0}`;

export const attachmentPayloadBytes = (attachments: Attachment[]): number =>
  new TextEncoder().encode(JSON.stringify(attachments)).byteLength;

/** Return null only when every attachment can pass the current backend ingress contract. */
export function validateAttachmentSet(attachments: Attachment[]): AttachmentReject | null {
  if (attachments.length > MAX_ATTACHMENTS) {
    return { ok: false, code: "too_many", name: attachments[MAX_ATTACHMENTS]?.name || "" };
  }
  for (const attachment of attachments) {
    const code = validateAttachment(attachment);
    if (code) return { ok: false, code, name: attachment.name };
  }
  if (attachmentPayloadBytes(attachments) > MAX_ATTACHMENT_PAYLOAD_BYTES) {
    return {
      ok: false,
      code: "total_too_large",
      name: attachments[attachments.length - 1]?.name || "",
    };
  }
  return null;
}

function validateAttachment(attachment: Attachment): AttachmentRejectCode | null {
  if (!attachment.name || attachment.name.length > 1024) return "invalid_payload";
  if (attachment.kind === "image") {
    const data = attachment.data_url || "";
    const marker = data.indexOf(";base64,");
    return data.startsWith("data:image/") && marker >= 0 && marker + 8 < data.length && data.length <= MAX_IMAGE_DATA_URL_CHARS
      ? null
      : "invalid_payload";
  }
  if (attachment.kind === "pdf") {
    const data = attachment.data_url || "";
    const prefix = "data:application/pdf;base64,";
    return data.startsWith(prefix) && data.length > prefix.length && data.length <= MAX_PDF_DATA_URL_CHARS
      ? null
      : "invalid_payload";
  }
  return typeof attachment.text === "string" && attachment.text.length > 0 && attachment.text.length <= MAX_TEXT_CHARS
    ? null
    : "invalid_payload";
}

/** Merge without silent truncation; every omitted item has an explicit rejection result. */
export function mergeAttachments(
  current: Attachment[],
  incoming: Attachment[],
): { attachments: Attachment[]; rejected: AttachmentReject[] } {
  const attachments = [...current];
  const seen = new Set(current.map(attachmentKey));
  const rejected: AttachmentReject[] = [];
  for (const attachment of incoming) {
    const key = attachmentKey(attachment);
    if (seen.has(key)) {
      rejected.push({ ok: false, code: "duplicate", name: attachment.name });
      continue;
    }
    if (attachments.length >= MAX_ATTACHMENTS) {
      rejected.push({ ok: false, code: "too_many", name: attachment.name });
      continue;
    }
    const candidate = [...attachments, attachment];
    const invalid = validateAttachmentSet(candidate);
    if (invalid) {
      rejected.push({ ...invalid, name: attachment.name });
      continue;
    }
    attachments.push(attachment);
    seen.add(key);
  }
  return { attachments, rejected };
}
