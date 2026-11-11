import { describe, expect, it } from "vitest";
import type { ProfileClaim } from "@/contracts/types";
import type { ResumeBullet, ResumeVariant } from "@/adapters/proposed";
import { diffWords, hasChanges } from "@/lib/diff";
import { daysBefore, duplicateKey, formatCompensation, freshness, percent } from "@/lib/format";
import { lintBullet, reviewVariant, variantToText } from "@/lib/resumeLint";
import { resumeContentDigest } from "@/lib/resumeDigest";
import { classifyLink, csvCell, toCsv } from "@/lib/safety";
import { evidenceLevel, minimumDetectableIncrease, newcombeDifference, normalQuantile, sampleSizePerArm, wilsonInterval, type ArmSummary } from "@/lib/stats";

describe("stats: textbook values", () => {
  it("normalQuantile", () => {
    expect(normalQuantile(0.975)).toBeCloseTo(1.959964, 5);
    expect(normalQuantile(0.8)).toBeCloseTo(0.841621, 5);
    expect(normalQuantile(0.5)).toBeCloseTo(0, 9);
    expect(normalQuantile(0.001)).toBeCloseTo(-3.090232, 5);
    expect(() => normalQuantile(0)).toThrow();
    expect(() => normalQuantile(1)).toThrow();
  });
  it("wilsonInterval matches published values", () => {
    // Newcombe (1998) Table I: 81/263 -> 0.2553 to 0.3662
    const w = wilsonInterval(81, 263)!;
    expect(w.lower).toBeCloseTo(0.2553, 3);
    expect(w.upper).toBeCloseTo(0.3662, 3);
    const zero = wilsonInterval(0, 10)!;
    expect(zero.lower).toBe(0);
    expect(zero.upper).toBeCloseTo(0.2775, 3);
    expect(wilsonInterval(0, 0)).toBeNull();
    expect(() => wilsonInterval(5, 3)).toThrow();
    expect(() => wilsonInterval(1.5, 3)).toThrow();
  });
  it("newcombeDifference matches Newcombe (1998) example (56/70 vs 48/80)", () => {
    const d = newcombeDifference(56, 70, 48, 80)!;
    expect(d.estimate).toBeCloseTo(0.2, 6);
    expect(d.lower).toBeCloseTo(0.0524, 3);
    expect(d.upper).toBeCloseTo(0.3339, 3);
    expect(newcombeDifference(0, 0, 1, 5)).toBeNull();
  });
  it("sampleSizePerArm matches the standard two-proportion formula", () => {
    // 10% vs 20%, alpha .05 two-sided, power .8 -> 199 per arm (pooled-variance formula)
    expect(sampleSizePerArm(0.1, 0.2)).toBe(199);
    expect(sampleSizePerArm(0.2, 0.1)).toBe(199);
    expect(sampleSizePerArm(0.5, 0.5)).toBe(Number.POSITIVE_INFINITY);
    expect(() => sampleSizePerArm(0, 0.2)).toThrow();
  });
  it("minimumDetectableIncrease is consistent with sampleSizePerArm and honest about tiny samples", () => {
    const mde = minimumDetectableIncrease(0.1, 199)!;
    expect(mde).toBeCloseTo(0.1, 2);
    expect(sampleSizePerArm(0.1, 0.1 + mde)).toBeLessThanOrEqual(199);
    expect(minimumDetectableIncrease(0.1, 10)).toBeGreaterThan(0.45);
    expect(minimumDetectableIncrease(0.5, 3)).toBeNull();
  });
  const arm = (variant: string, observed: number, successes: number): ArmSummary => ({ variant, assigned: observed, observed, successes, censored: 0, missing: 0, rate: observed ? successes / observed : null, interval: wilsonInterval(successes, observed) });
  it("evidenceLevel never reaches the top label on small, unlocked or unplanned data", () => {
    const big = [arm("a", 200, 120), arm("b", 200, 60)];
    const diff = newcombeDifference(120, 200, 60, 200);
    expect(evidenceLevel({ closed: true, arms: big, designLocked: true, plannedPerArm: 200, difference: diff }).level).toBe("meets-preregistered-criterion");
    expect(evidenceLevel({ closed: true, arms: big, designLocked: false, plannedPerArm: 200, difference: diff }).level).toBe("inconclusive");
    expect(evidenceLevel({ closed: true, arms: big, designLocked: true, plannedPerArm: null, difference: diff }).level).toBe("inconclusive");
    expect(evidenceLevel({ closed: true, arms: big, designLocked: true, plannedPerArm: 500, difference: diff }).level).toBe("inconclusive");
    const small = [arm("a", 5, 5), arm("b", 4, 0)];
    const res = evidenceLevel({ closed: true, arms: small, designLocked: true, plannedPerArm: 4, difference: newcombeDifference(5, 5, 0, 4) });
    expect(res.level).toBe("insufficient");
    expect(evidenceLevel({ closed: true, arms: [arm("a", 0, 0), arm("b", 0, 0)], designLocked: true, plannedPerArm: 10, difference: null }).level).toBe("no-data");
    const three = [arm("a", 200, 120), arm("b", 200, 60), arm("c", 200, 60)];
    expect(evidenceLevel({ closed: true, arms: three, designLocked: true, plannedPerArm: 200, difference: null }).level).toBe("inconclusive");
    const same = [arm("a", 200, 60), arm("b", 200, 62)];
    expect(evidenceLevel({ closed: true, arms: same, designLocked: true, plannedPerArm: 200, difference: newcombeDifference(60, 200, 62, 200) }).level).toBe("inconclusive");
  });
});

describe("safety: links", () => {
  const blocked = (u: string) => classifyLink(u).kind === "blocked";
  it("allows only absolute http(s) links on named public hosts", () => {
    const ok = classifyLink("https://careers.example.invalid/x/1");
    expect(ok.kind).toBe("ok");
    if (ok.kind === "ok") expect(ok.notes.join(" ")).toContain("reserved test domain");
    const http = classifyLink("http://insecure.example.invalid/jobs/22");
    expect(http.kind === "ok" && http.notes.some((n) => n.includes("not encrypted"))).toBe(true);
  });
  it("blocks script, data, file and other schemes, including obfuscated ones", () => {
    for (const u of [
      "javascript:alert(1)", "JaVaScRiPt:alert(1)", " javascript:alert(1)", "java\tscript:alert(1)", "java\nscript:alert(1)", "\u0001javascript:alert(1)",
      "data:text/html,<script>alert(1)</script>", "vbscript:msgbox(1)", "file:///etc/passwd", "blob:https://x/y", "ftp://example.invalid/x", "mailto:a@example.invalid", "tel:123",
      "//evil.example.invalid/x", "/relative/path", "relative", "", "   ", "https://", "http:///x",
    ]) expect(blocked(u), u).toBe(true);
  });
  it("blocks credentials and local, private or numeric hosts (SSRF-shaped links)", () => {
    for (const u of [
      "https://user:pass@example.invalid/", "https://user@example.invalid/", "http://localhost:8080/", "http://127.0.0.1/", "http://10.0.0.5/admin", "http://192.168.1.1/",
      "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://2130706433/", "http://0x7f000001/", "http://printer.local/", "http://svc.internal/", "http://0.0.0.0/",
    ]) expect(blocked(u), u).toBe(true);
  });
  it("blocks absurdly long links and flags punycode hosts", () => {
    expect(blocked(`https://example.invalid/${"a".repeat(3000)}`)).toBe(true);
    const p = classifyLink("https://xn--80ak6aa92e.example.invalid/");
    expect(p.kind === "ok" && p.notes.some((n) => n.includes("punycode"))).toBe(true);
  });
});

describe("safety: CSV export", () => {
  it("neutralises spreadsheet formulas", () => {
    for (const lead of ["=", "+", "-", "@", "\t", "\r"]) expect(csvCell(`${lead}SUM(1,2)`).replace(/^"/, "").startsWith("'")).toBe(true);
    expect(csvCell('=HYPERLINK("https://evil.example.invalid","x")')).toBe(`"'=HYPERLINK(""https://evil.example.invalid"",""x"")"`);
    expect(csvCell("plain")).toBe("plain");
    expect(csvCell(null)).toBe("");
    expect(csvCell(12)).toBe("12");
    // A real negative number is data, not a formula; the same characters as text are neutralised.
    expect(csvCell(-5)).toBe("-5");
    expect(csvCell("-5")).toBe("'-5");
  });
  it("quotes commas, quotes and newlines and ends rows with CRLF", () => {
    expect(csvCell('a,"b"\nc')).toBe('"a,""b""\nc"');
    expect(toCsv(["a", "b"], [["1", "x,y"], ["=1+1", ""]])).toBe('a,b\r\n1,"x,y"\r\n\'=1+1,\r\n');
  });
});

describe("format", () => {
  it("compensation: unknown is never rendered as zero", () => {
    expect(formatCompensation(null)).toEqual({ text: "Not disclosed", known: false, verified: false });
    expect(formatCompensation(undefined).text).toBe("Not disclosed");
    expect(formatCompensation({ currency: "USD", min: null, max: null, basis: "unknown", verified: false }).text).toBe("Range unknown");
    expect(formatCompensation({ currency: "USD", min: 150000, max: 185000, basis: "base", verified: false }).text).toBe("$150,000–$185,000 · base · unverified");
    expect(formatCompensation({ currency: "USD", min: 140000, max: null, basis: "base", verified: true }).text).toContain("From $140,000");
    expect(formatCompensation({ currency: "NOTACUR", min: 1, max: 2, basis: "total", verified: false }).known).toBe(true);
  });
  it("freshness tiers and clamping", () => {
    const clock = "2026-11-10T12:00:00Z";
    expect(freshness("2026-11-09T12:00:00Z", clock).level).toBe("fresh");
    expect(freshness("2026-10-25T12:00:00Z", clock).level).toBe("aging");
    expect(freshness("2026-09-01T12:00:00Z", clock).level).toBe("stale");
    expect(daysBefore("2026-12-01T00:00:00Z", clock)).toBe(0);
    expect(daysBefore("garbage", clock)).toBe(0);
    expect(percent(null)).toBe("Unknown");
    expect(percent(0.256)).toBe("26%");
  });
  it("duplicateKey ignores case and punctuation", () => {
    expect(duplicateKey({ organization: "Fictional Meridian Systems", title: "Forward Deployed Engineer" })).toBe(duplicateKey({ organization: "fictional  meridian-systems!", title: "forward deployed  engineer" }));
  });
});

describe("diff", () => {
  it("marks added and removed words and reconstructs both sides", () => {
    const before = "Reduced p95 latency of the order API by 38%";
    const after = "Reduced p99 latency of the fictional order API by 38%";
    const parts = diffWords(before, after);
    expect(parts.filter((p) => p.op !== "add").map((p) => p.text).join("")).toBe(before);
    expect(parts.filter((p) => p.op !== "del").map((p) => p.text).join("")).toBe(after);
    expect(parts.find((p) => p.op === "del")?.text).toContain("p95");
    expect(parts.some((p) => p.op === "add" && p.text.includes("fictional"))).toBe(true);
    expect(hasChanges(parts)).toBe(true);
    expect(hasChanges(diffWords("same text", "same text"))).toBe(false);
    expect(diffWords("", "new")).toEqual([{ op: "add", text: "new" }]);
    expect(diffWords("old", "")).toEqual([{ op: "del", text: "old" }]);
  });
});

describe("résumé truthfulness guard", () => {
  const clock = "2026-11-10T12:00:00Z";
  const claim = (over: Partial<ProfileClaim>): ProfileClaim => ({ id: "c1", subject_id: "s", claim_type: "achievement", claim_text: "Reduced p95 latency of the fictional order API by 38%", status: "supported", evidence_ids: ["ev1"], visibility: "shareable", updated_at: clock, skills: [], ...over });
  const bullet = (over: Partial<ResumeBullet>): ResumeBullet => ({ id: "b1", text: "Reduced p95 latency of the fictional order API by 38%", claim_ids: ["c1"], origin: "from-claims", ...over });
  const codes = (b: ResumeBullet, claims: ProfileClaim[]) => lintBullet(b, new Map(claims.map((c) => [c.id, c])), clock).map((f) => `${f.severity}:${f.code}`);

  it("passes a verbatim line that rests on a supported claim", () => {
    expect(codes(bullet({}), [claim({})])).toEqual([]);
  });
  it("blocks lines with no evidence link, an unknown claim, or a contradicted / unverified / expired / private claim", () => {
    expect(codes(bullet({ claim_ids: [] }), [])).toEqual(["block:no-evidence-link"]);
    expect(codes(bullet({}), [])).toEqual(["block:unknown-claim"]);
    expect(codes(bullet({}), [claim({ status: "contradicted" })])).toContain("block:contradicted-claim");
    expect(codes(bullet({}), [claim({ status: "unverified" })])).toContain("block:unverified-claim");
    expect(codes(bullet({}), [claim({ valid_to: "2025-06-30T00:00:00Z" })])).toContain("block:expired-claim");
    expect(codes(bullet({}), [claim({ visibility: "private" })])).toContain("block:private-claim");
  });
  it("warns, but does not block, on self-reported claims", () => {
    expect(codes(bullet({}), [claim({ status: "self-reported", evidence_ids: [] })])).toEqual(["warn:self-reported"]);
  });
  it("detects inflated figures in hand-edited lines", () => {
    expect(codes(bullet({ origin: "manual-edit", text: "Reduced p95 latency of the fictional order API by 83%" }), [claim({})])).toContain("block:new-figure");
    expect(codes(bullet({ origin: "manual-edit", text: "Led 12 engineers and reduced p95 latency by 38%" }), [claim({})])).toContain("block:new-figure");
  });
  it("detects invented credentials in hand-edited lines", () => {
    const found = codes(bullet({ origin: "manual-edit", text: "Certified performance engineer with a PhD who reduced p95 latency by 38%" }), [claim({})]);
    expect(found).toContain("block:new-credential");
    // A credential word that IS in the linked claim is not flagged.
    expect(codes(bullet({ origin: "manual-edit", text: "Holds the Fictional Security Analyst Certificate" }), [claim({ claim_text: "Fictional Security Analyst Certificate" })])).toEqual(["warn:reworded"]);
  });
  it("only warns on harmless rewording", () => {
    expect(codes(bullet({ origin: "manual-edit", text: "Cut p95 latency of the fictional order API by 38%" }), [claim({})])).toEqual(["warn:reworded"]);
  });

  const variant = (bullets: ResumeBullet[], approval: ResumeVariant["approval"] = null): ResumeVariant => ({ schema_version: "resume-variant/0.1-proposed", id: "rv", candidate_ref: "s", label: "L", target_job_id: null, parent_variant_id: null, version: 1, created_at: clock, updated_at: clock, sections: [{ id: "s1", heading: "Experience", bullets }], approval });
  it("export needs an approval bound to the exact current content", () => {
    const v = variant([bullet({})]);
    const r = reviewVariant(v, [claim({})], clock);
    expect(r.exportAllowed).toBe(false);
    expect(r.exportBlockers.join(" ")).toContain("not approved");
    const approved = variant([bullet({})], { approved_at: clock, approval_token_ref: "approval-1", content_digest: r.digest });
    expect(reviewVariant(approved, [claim({})], clock).exportAllowed).toBe(true);
    // Same approval, edited content -> stale.
    const edited = variant([bullet({ text: "Reduced p95 latency of the fictional order API by 38%!" })], approved.approval);
    const stale = reviewVariant(edited, [claim({})], clock);
    expect(stale.approvalStale).toBe(true);
    expect(stale.exportAllowed).toBe(false);
    // Approved content whose claim later becomes contradicted is blocked again.
    expect(reviewVariant(approved, [claim({ status: "contradicted" })], clock).exportAllowed).toBe(false);
    expect(reviewVariant(variant([]), [], clock).exportBlockers[0]).toContain("no content");
  });
  it("digest changes with text, order and evidence links, and ignores claim-id order", () => {
    const base = [{ heading: "H", bullets: [{ text: "a", claim_ids: ["1", "2"] }, { text: "b", claim_ids: [] }] }];
    const d = resumeContentDigest(base);
    expect(d).toMatch(/^[0-9a-f]{64}$/);
    expect(resumeContentDigest([{ heading: "H", bullets: [{ text: "a", claim_ids: ["2", "1"] }, { text: "b", claim_ids: [] }] }])).toBe(d);
    expect(resumeContentDigest([{ heading: "H", bullets: [{ text: "b", claim_ids: [] }, { text: "a", claim_ids: ["1", "2"] }] }])).not.toBe(d);
    expect(resumeContentDigest([{ heading: "H", bullets: [{ text: "a", claim_ids: ["1"] }, { text: "b", claim_ids: [] }] }])).not.toBe(d);
    expect(resumeContentDigest([{ heading: "H2", bullets: base[0]!.bullets }])).not.toBe(d);
  });
  it("text export is labelled synthetic", () => {
    expect(variantToText(variant([bullet({})]), "Synthetic Candidate")).toContain("Synthetic demo export");
  });
});
