// ---------------------------------------------------------------------------
// Markdown & automation block helpers
// ---------------------------------------------------------------------------

/**
 * @param {string|null|undefined} text
 * @returns {{ text: string, hasAutomationBlock: boolean, isPartialBlock: boolean, partialBlockType: string|null }}
 */
export function stripAutomationBlock(text) {
  if (!text)
    return {
      text: "",
      hasAutomationBlock: false,
      isPartialBlock: false,
      partialBlockType: null,
    };

  // Block types the backend extracts and re-attaches structurally — never
  // show their raw JSON to the user.
  const blockTypes = "automation|scene|quick_actions|delayed_command|cancel";

  // Complete block: ```<type> ... ```
  const completeRe = new RegExp("```(?:" + blockTypes + ")[\\s\\S]*?```", "g");
  const hasComplete = completeRe.test(text);
  let cleaned = text.replace(completeRe, "").trim();

  // Partial block (still streaming): ```<type> ... (no closing ```)
  const partialRe = new RegExp("```(" + blockTypes + ")[\\s\\S]*$");
  const partialMatch = !hasComplete ? cleaned.match(partialRe) : null;
  const hasPartial = !!partialMatch;
  if (hasPartial) {
    cleaned = cleaned.replace(partialRe, "").trim();
  }

  // Spinners only make sense for the long-form blocks; quick_actions /
  // delayed_command / cancel are short and finalize quickly, so we don't
  // surface a building-state UI for them.
  const spinnerType = ["automation", "scene"].includes(partialMatch?.[1])
    ? partialMatch[1]
    : null;

  return {
    text: cleaned,
    hasAutomationBlock: hasComplete,
    isPartialBlock: hasPartial,
    partialBlockType: spinnerType,
  };
}

/** @param {string|null|undefined} text @returns {string} sanitized HTML */
export function renderMarkdown(text) {
  if (!text) return "";
  let escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Code blocks (```)
  escaped = escaped.replace(
    /```([\s\S]*?)```/g,
    '<pre style="background:var(--primary-background-color,#18181b);color:var(--primary-text-color,#e4e4e7);padding:10px;border-radius:8px;border:1px solid var(--divider-color,#27272a);font-size:12px;overflow-x:auto;margin:8px 0;">$1</pre>',
  );
  // Inline code
  escaped = escaped.replace(
    /`([^`]+)`/g,
    '<code style="background:var(--secondary-background-color,rgba(255,255,255,0.08));padding:2px 5px;border-radius:4px;font-size:13px;border:1px solid var(--divider-color,rgba(255,255,255,0.06));">$1</code>',
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
  // Bold — inline accent color so it works regardless of theme resolution
  escaped = escaped.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  // Italic (*text*) — require non-word boundaries
  escaped = escaped.replace(
    /(?<!\w)\*([^\s*](?:.*?[^\s*])?)\*(?!\w)/g,
    "<em>$1</em>",
  );
  // Italic (_text_) — underscore style; boundaries avoid matching entity_names
  escaped = escaped.replace(
    /(?<![a-zA-Z0-9_])_([^\s_](?:.*?[^\s_])?)_(?![a-zA-Z0-9_])/g,
    "<em>$1</em>",
  );
  // Numbered lists: lines starting with "1. ", "2. ", etc.
  escaped = escaped.replace(
    /^(\d+)\.\s+(.+)$/gm,
    '<div style="display:flex;gap:6px;margin:2px 0 2px 4px;align-items:baseline;"><span style="opacity:0.55;flex-shrink:0;min-width:18px;">$1.</span><span style="flex:1;">$2</span></div>',
  );
  // Bullet lists: lines starting with "- " or "• "
  escaped = escaped.replace(
    /^[-•]\s+(.+)$/gm,
    '<div style="margin:4px 0 4px 8px;padding-left:12px;border-left:2px solid rgba(251,191,36,0.35);">$1</div>',
  );
  // Line breaks
  escaped = escaped.replace(/\n/g, "<br>");

  return escaped;
}
