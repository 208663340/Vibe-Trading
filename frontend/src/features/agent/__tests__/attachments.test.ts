import {
  extractUploadedAttachments,
  prependUploadedAttachments,
} from "@/features/agent/attachments";

describe("attachment prompt envelope", () => {
  it("round-trips multiple attachments without exposing paths in display metadata", () => {
    const prompt = prependUploadedAttachments("Compare them", [
      { filename: "trades.csv", filePath: "uploads/a.csv" },
      { filename: "chart.png", filePath: "uploads/b.png" },
    ]);

    expect(prompt).toContain("uploads/a.csv");
    expect(extractUploadedAttachments(prompt)).toEqual({
      content: "Compare them",
      filenames: ["trades.csv", "chart.png"],
    });
  });

  it("keeps legacy single-attachment history readable", () => {
    expect(
      extractUploadedAttachments(
        "[Uploaded file: old.pdf, path: uploads/old.pdf]\n\nSummarize it",
      ),
    ).toEqual({ content: "Summarize it", filenames: ["old.pdf"] });
  });

  it("leaves malformed envelopes untouched", () => {
    const prompt = "[Uploaded files: not json]\n\nAnalyze";
    expect(extractUploadedAttachments(prompt)).toEqual({
      content: prompt,
      filenames: [],
    });
  });
});
