/**
 * LLMs emit LaTeX with `\(...\)` / `\[...\]` delimiters, but remark-math only
 * parses dollar-delimited math. Single-dollar parsing is disabled in the
 * renderer (finance text like "from $150 to $120" would become a formula), so
 * everything is normalized to the `$$` forms remark-math still accepts:
 * `\(x\)` → inline `$$x$$`, `\[x\]` → display block. Fenced/inline code is
 * left untouched, including a trailing fence left unclosed mid-stream.
 */

const CODE_SEGMENT = /(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`)/g;

function normalizeSegment(segment: string): string {
  return segment
    .replace(/\\\[([\s\S]+?)\\\]/g, (_match, expr: string) => `\n\n$$\n${expr.trim()}\n$$\n\n`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_match, expr: string) => `$$${expr.trim()}$$`);
}

export function normalizeMathDelimiters(content: string): string {
  return content
    .split(CODE_SEGMENT)
    .map((segment, index) => (index % 2 === 1 ? segment : normalizeSegment(segment)))
    .join("");
}
