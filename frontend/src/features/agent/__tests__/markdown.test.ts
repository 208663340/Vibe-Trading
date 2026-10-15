import { normalizeMathDelimiters } from "../markdown";

describe("normalizeMathDelimiters", () => {
  it("converts \\(...\\) to inline $$ math", () => {
    expect(normalizeMathDelimiters("Sharpe is \\(\\frac{R_p - R_f}{\\sigma_p}\\) here")).toBe(
      "Sharpe is $$\\frac{R_p - R_f}{\\sigma_p}$$ here",
    );
  });

  it("converts \\[...\\] to a display $$ block", () => {
    expect(normalizeMathDelimiters("Formula: \\[\\sum_{i=1}^n w_i r_i\\] done")).toBe(
      "Formula: \n\n$$\n\\sum_{i=1}^n w_i r_i\n$$\n\n done",
    );
  });

  it("handles multiline display math", () => {
    const input = "\\[\na = b\n+ c\n\\]";
    expect(normalizeMathDelimiters(input)).toBe("\n\n$$\na = b\n+ c\n$$\n\n");
  });

  it("converts multiple inline formulas independently", () => {
    expect(normalizeMathDelimiters("\\(a\\) and \\(b\\)")).toBe("$$a$$ and $$b$$");
  });

  it("leaves dollar amounts untouched", () => {
    const text = "AAPL fell from $150 to $120, a $30 drop";
    expect(normalizeMathDelimiters(text)).toBe(text);
  });

  it("leaves fenced code blocks untouched", () => {
    const text = "```python\nre.match(r\"\\(x\\)\", s)\n```";
    expect(normalizeMathDelimiters(text)).toBe(text);
  });

  it("leaves inline code untouched", () => {
    const text = "use `\\(escaped\\)` in regex";
    expect(normalizeMathDelimiters(text)).toBe(text);
  });

  it("still transforms text around code segments", () => {
    expect(normalizeMathDelimiters("\\(x\\) `\\(y\\)` \\(z\\)")).toBe("$$x$$ `\\(y\\)` $$z$$");
  });

  it("leaves an unterminated streaming fence untouched", () => {
    const text = "done\n```python\npartial = r\"\\(x\\)\"";
    expect(normalizeMathDelimiters(text)).toBe(text);
  });

  it("passes through plain text and existing $$ math unchanged", () => {
    const text = "plain text with $$x^2$$ already delimited";
    expect(normalizeMathDelimiters(text)).toBe(text);
  });
});
