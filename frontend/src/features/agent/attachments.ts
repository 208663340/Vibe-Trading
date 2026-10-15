export interface UploadedAttachment {
  filename: string;
  filePath: string;
}

const ATTACHMENTS_OPEN = "[Uploaded files: ";
const ATTACHMENTS_CLOSE = "]\n\n";
const LEGACY_ATTACHMENT = /^\[Uploaded file: (.+), path: [^\n]*\]\n\n/;

/** Add a machine-readable attachment envelope to the agent prompt. */
export function prependUploadedAttachments(
  prompt: string,
  attachments: UploadedAttachment[],
): string {
  if (attachments.length === 0) return prompt;
  const payload = attachments.map(({ filename, filePath }) => ({
    filename,
    path: filePath,
  }));
  return `${ATTACHMENTS_OPEN}${JSON.stringify(payload)}${ATTACHMENTS_CLOSE}${prompt}`;
}

/** Strip attachment paths from a stored prompt before rendering it. */
export function extractUploadedAttachments(content: string): {
  content: string;
  filenames: string[];
} {
  if (content.startsWith(ATTACHMENTS_OPEN)) {
    const closeIndex = content.indexOf(ATTACHMENTS_CLOSE, ATTACHMENTS_OPEN.length);
    if (closeIndex >= 0) {
      const serialized = content.slice(ATTACHMENTS_OPEN.length, closeIndex);
      try {
        const payload: unknown = JSON.parse(serialized);
        if (Array.isArray(payload)) {
          const filenames = payload.flatMap((item) => {
            if (!item || typeof item !== "object") return [];
            const filename = (item as Record<string, unknown>).filename;
            return typeof filename === "string" && filename ? [filename] : [];
          });
          if (filenames.length > 0) {
            return {
              content: content.slice(closeIndex + ATTACHMENTS_CLOSE.length),
              filenames,
            };
          }
        }
      } catch {
        // Preserve malformed prompts verbatim instead of hiding user content.
      }
    }
  }

  const legacy = content.match(LEGACY_ATTACHMENT);
  if (legacy) {
    return {
      content: content.slice(legacy[0].length),
      filenames: [legacy[1]],
    };
  }
  return { content, filenames: [] };
}
