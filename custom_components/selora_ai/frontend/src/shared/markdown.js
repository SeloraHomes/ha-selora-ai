// ---------------------------------------------------------------------------
// Markdown & automation block helpers
// ---------------------------------------------------------------------------

export function stripAutomationBlock(text) {
  if (!text)
    return { text: "", hasAutomationBlock: false, isPartialBlock: false };

  // Complete block: ```automation ... ```
  const completeRe = /```automation[\s\S]*?```/g;
  const hasComplete = completeRe.test(text);
  let cleaned = text.replace(completeRe, "").trim();

  // Partial block (still streaming): ```automation ... (no closing ```)
  const partialRe = /```automation[\s\S]*$/;
  const hasPartial = !hasComplete && partialRe.test(cleaned);
  if (hasPartial) {
    cleaned = cleaned.replace(partialRe, "").trim();
  }

  return {
    text: cleaned,
    hasAutomationBlock: hasComplete,
    isPartialBlock: hasPartial,
  };
}

export function renderMarkdown(text) {
  if (!text) return "";
  let escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Code blocks (```)
  escaped = escaped.replace(
    /```([\s\S]*?)```/g,
    '<pre style="background:#18181b;color:#e4e4e7;padding:10px;border-radius:8px;border:1px solid #27272a;font-size:12px;overflow-x:auto;margin:8px 0;">$1</pre>',
  );
  // Inline code
  escaped = escaped.replace(
    /`([^`]+)`/g,
    '<code style="background:rgba(255,255,255,0.08);padding:2px 5px;border-radius:4px;font-size:13px;border:1px solid rgba(255,255,255,0.06);">$1</code>',
  );
  // Headings (#### → h6, ### → h5, ## → h4, # → h3) — sized for chat bubbles
  escaped = escaped.replace(
    /^####\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:14px;margin:10px 0 4px;">$1</div>',
  );
  escaped = escaped.replace(
    /^###\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:15px;margin:12px 0 4px;">$1</div>',
  );
  escaped = escaped.replace(
    /^##\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:16px;margin:14px 0 6px;">$1</div>',
  );
  escaped = escaped.replace(
    /^#\s+(.+)$/gm,
    '<div style="font-weight:700;font-size:17px;margin:16px 0 6px;">$1</div>',
  );
  // Bold
  escaped = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  // Italic
  escaped = escaped.replace(/\*(.+?)\*/g, "<em>$1</em>");
  // Numbered lists: lines starting with "1. ", "2. ", etc.
  escaped = escaped.replace(
    /^(\d+)\.\s+(.+)$/gm,
    '<div style="margin:4px 0 4px 8px;"><strong>$1.</strong> $2</div>',
  );
  // Bullet lists: lines starting with "- "
  escaped = escaped.replace(
    /^[-•]\s+(.+)$/gm,
    '<div style="margin:4px 0 4px 8px;padding-left:12px;border-left:2px solid rgba(251,191,36,0.35);">$1</div>',
  );
  // Line breaks
  escaped = escaped.replace(/\n/g, "<br>");

  return escaped;
}
