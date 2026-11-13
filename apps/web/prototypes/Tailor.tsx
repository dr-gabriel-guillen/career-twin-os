import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Banner, Button, Checkbox, EmptyState, Icon, SelectField, useAnnounce } from "@careertwin/ui";
import { RESUME_SCHEMA, type ResumeBullet, type ResumeVariant } from "@/adapters/proposed";
import type { ProfileClaim } from "@/contracts/types";
import { skillLabel, truncate } from "@/lib/format";
import { reviewVariant, variantToText } from "@/lib/resumeLint";
import { downloadText, newId } from "@/lib/safety";
import { useApp } from "@/state/AppContext";
import { useIndexes, userEvent } from "@/state/selectors";
import { variantTitle } from "@/pages/ResumePage";
import { rankByNearTerm, splitApplications, suggestedClaims } from "./lib";
import { Screen } from "./Screen";

const STEPS = ["Target role", "Choose content", "Review changes", "Approve and export"] as const;

type Coverage = "evidence" | "self" | "hidden" | "missing";
const COVERAGE_LABEL: Record<Coverage, string> = {
  evidence: "Covered, backed by evidence",
  self: "Covered, self-reported",
  hidden: "In your vault, not in this résumé",
  missing: "No claim covers it",
};

/** Each posting gets its own tailoring session: switching jobs never carries a step or draft across. */
export default function TailorJourney() {
  const { jobId } = useParams();
  return <TailorSession key={jobId ?? "none"} />;
}

function TailorSession() {
  const { bundle, workspace, clock, dispatch } = useApp();
  const { jobById, matchByJob, claimById, applications } = useIndexes();
  const { jobId } = useParams();
  const navigate = useNavigate();
  const announce = useAnnounce();
  const job = jobId ? jobById.get(jobId) ?? null : null;
  const match = job ? matchByJob.get(job.id) ?? null : null;
  const variants = workspace.resumeVariants;
  const candidateName = bundle.preferences?.display_name ?? "Synthetic Candidate";

  const defaultBase = variants.find((v) => !v.target_job_id) ?? variants[0] ?? null;
  const [step, setStep] = useState(0);
  const [baseId, setBaseId] = useState<string | null>(defaultBase?.id ?? null);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [added, setAdded] = useState<string[]>([]);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [trackerNote, setTrackerNote] = useState<string | null>(null);
  const base = variants.find((v) => v.id === baseId) ?? null;
  const saved = draftId ? variants.find((v) => v.id === draftId) ?? null : null;

  // The draft is the base with lines switched off, plus vault claims added verbatim. No text is written for you.
  const draft: ResumeVariant | null = useMemo(() => {
    if (!base || !job) return null;
    const now = new Date().toISOString();
    const extra: ResumeBullet[] = added.map((id) => ({ id: `b-${id}`, text: claimById.get(id)?.claim_text ?? "", claim_ids: [id], origin: "from-claims" }));
    return {
      schema_version: RESUME_SCHEMA,
      id: draftId ?? "rv-draft-preview",
      candidate_ref: base.candidate_ref,
      label: `For ${truncate(job.title, 50)}`,
      target_job_id: job.id,
      parent_variant_id: base.id,
      version: base.version + 1,
      created_at: now,
      updated_at: now,
      sections: base.sections.map((s, i) => ({ ...s, bullets: [...s.bullets.filter((b) => !excluded.has(b.id)), ...(i === 0 ? extra : [])] })),
      approval: null,
    };
  }, [base, job, added, excluded, claimById, draftId]);

  const review = useMemo(() => (saved ? reviewVariant(saved, workspace.claims, clock) : draft ? reviewVariant(draft, workspace.claims, clock) : null), [saved, draft, workspace.claims, clock]);

  if (!job) return <JobPicker />;
  if (!base || !draft || !review) {
    return (
      <Screen title="Tailor résumé" lead={`For ${job.title} at ${job.organization}.`}>
        <EmptyState title="There is no résumé to start from.">
          <p>Create a variant in Resume Studio first; tailoring adapts an existing résumé and never writes one for you.</p>
        </EmptyState>
      </Screen>
    );
  }

  const wanted = [...new Set([...(match?.skills.map((s) => s.skill) ?? []), ...job.skills])];
  const draftClaims = new Map<string, ProfileClaim>();
  for (const s of draft.sections) for (const b of s.bullets) for (const id of b.claim_ids) { const c = claimById.get(id); if (c) draftClaims.set(id, c); }
  const coverageOf = (skill: string): Coverage => {
    const inDraft = [...draftClaims.values()].filter((c) => (c.skills ?? []).includes(skill));
    if (inDraft.some((c) => c.status === "supported")) return "evidence";
    if (inDraft.some((c) => c.status === "self-reported")) return "self";
    if (workspace.claims.some((c) => (c.skills ?? []).includes(skill) && c.status !== "contradicted")) return "hidden";
    return "missing";
  };
  const covered = wanted.filter((s) => ["evidence", "self"].includes(coverageOf(s))).length;
  // Claims already present through the base lines you kept. Claims you added stay listed so they can be removed again.
  const usedIds = new Set(base.sections.flatMap((s) => s.bullets.filter((b) => !excluded.has(b.id)).flatMap((b) => b.claim_ids)));
  const suggestions = suggestedClaims(job, match, workspace.claims, usedIds);
  const baseLines = base.sections.flatMap((s) => s.bullets);
  const removed = baseLines.filter((b) => excluded.has(b.id));
  const addedLines = draft.sections.flatMap((s) => s.bullets).filter((b) => b.id.startsWith("b-") && added.includes(b.id.slice(2)));
  const locked = saved?.approval != null;
  const { mine } = splitApplications(applications, bundle.experimentUnitApplicationIds);
  const app = mine.find((a) => a.job_id === job.id) ?? null;

  const saveDraft = () => {
    const id = draftId ?? newId("rv");
    const v: ResumeVariant = { ...draft, id, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
    const refused = dispatch({ type: "resume/upsert", variant: v });
    if (refused) return announce(refused);
    setDraftId(id);
    announce(`Saved draft “${v.label}”. It is not approved yet.`);
    setStep(3);
  };
  const approve = () => {
    if (!saved) return;
    const r = reviewVariant(saved, workspace.claims, clock);
    const refused = dispatch({ type: "resume/upsert", variant: { ...saved, updated_at: new Date().toISOString(), approval: { approved_at: new Date().toISOString(), approval_token_ref: newId("approval"), content_digest: r.digest } } });
    if (refused) return announce(refused);
    setConfirm(false);
    announce("Approved. This exact text is locked and can now be exported.");
  };
  const recordInTracker = () => {
    if (!saved?.approval) return;
    if (app && app.stage === "drafted" && app.resumeVariantId && app.resumeVariantId !== saved.id) {
      // The tracker allows one résumé per application step; it never swaps one silently.
      setTrackerNote("This posting is already in your tracker, drafted with a different résumé. Approve that résumé there, or withdraw that application and start a new one with this version.");
      return;
    }
    let appId = app?.id ?? null;
    const steps: Parameters<typeof userEvent>[0][] = [];
    if (!appId) {
      appId = newId("app");
      steps.push({ application_id: appId, job_id: job.id, event_type: "saved", notes: "Saved while tailoring a résumé." });
    }
    if (!app || app.stage === "saved") steps.push({ application_id: appId, job_id: job.id, event_type: "drafted", resume_variant_id: saved.id });
    if (!app || app.stage === "saved" || app.stage === "drafted") steps.push({ application_id: appId, job_id: job.id, event_type: "approved", resume_variant_id: saved.id, approval_token_ref: saved.approval.approval_token_ref });
    for (const e of steps) {
      const refused = dispatch({ type: "event/append", event: userEvent(e) });
      if (refused) {
        setTrackerNote(`Not recorded: ${refused}`);
        return announce(`Not recorded: ${refused}`);
      }
    }
    announce("Recorded in your tracker as approved. CareerTwin does not submit anything.");
    navigate(`/track/${encodeURIComponent(appId)}`);
  };

  return (
    <Screen title="Tailor résumé" lead={<>For <strong>{job.title}</strong> at {job.organization}. You choose which of your own lines and claims to use; every line keeps its evidence, and nothing is written for you.</>}>
      <ol className="pt-steps" aria-label="Steps">
        {STEPS.map((s, i) => (
          <li key={s} className={i === step ? "pt-step pt-step--on" : i < step ? "pt-step pt-step--done" : "pt-step"} aria-current={i === step ? "step" : undefined}>
            <button type="button" onClick={() => setStep(i)} disabled={i > step && !(i === 3 && saved)}>
              <span className="pt-step__n">{i < step ? <Icon name="check" size={14} /> : i + 1}</span>
              <span className="pt-step__label">{s}</span>
            </button>
          </li>
        ))}
      </ol>

      <div className="pt-tailor">
        <div className="pt-tailor__main">
          {step === 0 ? (
            <section className="pt-panel" aria-labelledby="pt-s1">
              <h2 id="pt-s1">What this posting asks for</h2>
              <p className="pt-muted">Skills the posting lists, and how your current selection covers them. Coverage updates as you choose content in the next step.</p>
              <ul className="ct-list-plain pt-cover" data-testid="pt-coverage">
                {wanted.map((s) => (
                  <li key={s} className={`pt-cover__row pt-cover--${coverageOf(s)}`}>
                    <span className="pt-cover__skill">{skillLabel(s)}</span>
                    <span className="pt-cover__state">{COVERAGE_LABEL[coverageOf(s)]}</span>
                  </li>
                ))}
              </ul>
              <SelectField label="Start from" value={baseId ?? ""} onChange={(e) => { setBaseId(e.target.value); setExcluded(new Set()); setAdded([]); setDraftId(null); }} options={variants.map((v) => ({ value: v.id, label: `${variantTitle(v, v.target_job_id ? jobById.get(v.target_job_id)?.title : null)} (version ${v.version})` }))} />
              <div className="pt-nav-buttons">
                <Button variant="primary" onClick={() => setStep(1)}>Next: choose content</Button>
              </div>
            </section>
          ) : null}

          {step === 1 ? (
            <section className="pt-panel" aria-labelledby="pt-s2">
              <h2 id="pt-s2">Choose what goes in</h2>
              <h3 className="pt-sub">From your current résumé</h3>
              <ul className="ct-list-plain pt-lines">
                {baseLines.map((b) => {
                  const claims = b.claim_ids.map((id) => claimById.get(id));
                  const hits = [...new Set(claims.flatMap((c) => (c?.skills ?? []).filter((s) => wanted.includes(s))))];
                  const on = !excluded.has(b.id);
                  return (
                    <li key={b.id} className={on ? "pt-line" : "pt-line pt-line--off"}>
                      <Checkbox
                        label={<span className="pt-line__text">{b.text}</span>}
                        hint={`${claims.length === 0 ? "No claim linked" : claims.map((c) => (c ? (c.status === "supported" ? "Backed by evidence" : c.status === "self-reported" ? "Self-reported" : c.status) : "Missing claim")).join(", ")}${hits.length ? `. Relevant to: ${hits.map(skillLabel).join(", ")}` : ". Not linked to a skill this posting lists"}`}
                        checked={on}
                        onChange={() => setExcluded((cur) => { const n = new Set(cur); if (on) n.add(b.id); else n.delete(b.id); return n; })}
                      />
                    </li>
                  );
                })}
              </ul>
              <h3 className="pt-sub">From your evidence vault</h3>
              {suggestions.length === 0 ? (
                <p className="pt-muted">No other claim in your vault covers a skill this posting lists.</p>
              ) : (
                <ul className="ct-list-plain pt-lines" data-testid="pt-suggestions">
                  {suggestions.map((s) => (
                    <li key={s.claim.id} className={s.usable ? "pt-line pt-line--suggest" : "pt-line pt-line--blocked"}>
                      <div className="pt-line__body">
                        <span className="pt-line__text">{s.claim.claim_text}</span>
                        <span className="pt-muted">{s.why}. Covers: {s.skills.map(skillLabel).join(", ")}</span>
                      </div>
                      {added.includes(s.claim.id) ? (
                        <Button size="sm" onClick={() => setAdded(added.filter((x) => x !== s.claim.id))}>Remove</Button>
                      ) : (
                        <Button size="sm" disabled={!s.usable} onClick={() => setAdded([...added, s.claim.id])}>Add line</Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              <div className="pt-nav-buttons">
                <Button onClick={() => setStep(0)}>Back</Button>
                <Button variant="primary" onClick={() => setStep(2)}>Next: review changes</Button>
              </div>
            </section>
          ) : null}

          {step === 2 ? (
            <section className="pt-panel" aria-labelledby="pt-s3">
              <h2 id="pt-s3">Review the changes</h2>
              <p className="pt-muted">Compared with “{variantTitle(base, base.target_job_id ? jobById.get(base.target_job_id)?.title : null)}”. Unchanged lines: {baseLines.length - removed.length}.</p>
              <p className="pt-diff-summary" data-testid="pt-diff-summary">
                {addedLines.length} added, {removed.length} removed, {baseLines.length - removed.length} unchanged.
              </p>
              {addedLines.length + removed.length === 0 ? (
                <p className="pt-diff-empty" data-testid="pt-diff-empty">
                  No changes yet. The draft is identical to the résumé you started from. Go back to switch lines off or add claims from your vault.
                </p>
              ) : (
                <ul className="ct-list-plain pt-diff" data-testid="pt-diff">
                  {addedLines.map((b) => (
                    <li key={b.id} className="pt-diff__row pt-diff__add" data-change="added">
                      <span className="pt-diff__mark" aria-hidden="true">+</span>
                      <span className="pt-diff__text">
                        <span className="ct-sr-only">Added: </span>
                        {b.text}
                      </span>
                    </li>
                  ))}
                  {removed.map((b) => (
                    <li key={b.id} className="pt-diff__row pt-diff__del" data-change="removed">
                      <span className="pt-diff__mark" aria-hidden="true">−</span>
                      <span className="pt-diff__text">
                        <span className="ct-sr-only">Removed: </span>
                        {b.text}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {review.blocking > 0 ? (
                <Banner tone="warning" title={`${review.blocking} line${review.blocking === 1 ? "" : "s"} would block export.`}>
                  <ul>{review.exportBlockers.slice(0, 4).map((b) => <li key={b}>{b}</li>)}</ul>
                  Go back and switch off or replace those lines.
                </Banner>
              ) : (
                <Banner tone="success" title="Every line traces to a usable claim.">
                  {review.warnings > 0 ? `${review.warnings} line${review.warnings === 1 ? " is" : "s are"} self-reported; you will confirm them when you approve.` : "Nothing needs a second look."}
                </Banner>
              )}
              <div className="pt-nav-buttons">
                <Button onClick={() => setStep(1)}>Back</Button>
                <Button variant="primary" onClick={saveDraft} disabled={review.blocking > 0 || draft.sections.every((s) => s.bullets.length === 0)} data-testid="pt-save-draft">
                  Save draft variant
                </Button>
              </div>
            </section>
          ) : null}

          {step === 3 && saved ? (
            <section className="pt-panel" aria-labelledby="pt-s4">
              <h2 id="pt-s4">{locked ? "Approved and ready to export" : "Approve the exact text"}</h2>
              <article className="pt-paper" aria-label="Résumé preview">
                <pre className="pt-paper__text">{variantToText(saved, candidateName)}</pre>
              </article>
              {locked ? (
                <>
                  <Banner tone="success" title="Approved snapshot, locked." data-testid="pt-approved">
                    What you approved is exactly what gets exported. To change it, create a new version.
                  </Banner>
                  <div className="pt-nav-buttons">
                    <Button onClick={() => downloadText(`${saved.id}.txt`, variantToText(saved, candidateName), "text/plain")} data-testid="pt-export">
                      Download .txt
                    </Button>
                    <Button variant="primary" onClick={recordInTracker} data-testid="pt-record">
                      Add to tracker as approved
                    </Button>
                  </div>
                  {trackerNote ? (
                    <Banner tone="warning" role="alert" title="Not added to the tracker" data-testid="pt-tracker-note">
                      {trackerNote}{" "}
                      {app ? <Link to={`/track/${encodeURIComponent(app.id)}`}>Open that application</Link> : null}
                    </Banner>
                  ) : null}
                  <p className="pt-muted">PDF and Word export are not available in this build. CareerTwin never sends a résumé anywhere.</p>
                </>
              ) : (
                <>
                  <Checkbox label="I have read every line and each one is true and supported as shown." checked={confirm} onChange={(e) => setConfirm(e.target.checked)} data-testid="pt-confirm" />
                  <div className="pt-nav-buttons">
                    <Button onClick={() => setStep(2)}>Back</Button>
                    <Button variant="primary" disabled={!confirm || review.blocking > 0} onClick={approve} data-testid="pt-approve">
                      Approve and lock
                    </Button>
                  </div>
                </>
              )}
            </section>
          ) : null}
        </div>

        <aside className="pt-tailor__side" aria-label="Coverage summary">
          <p className="pt-big">
            {covered}<span>/{wanted.length}</span>
          </p>
          <p className="pt-muted">listed skills covered by lines in this draft</p>
          <ul className="ct-list-plain pt-legend">
            {(["evidence", "self", "hidden", "missing"] as Coverage[]).map((k) => (
              <li key={k}>
                <i className={`pt-dot pt-cover--${k}`} aria-hidden="true" /> {COVERAGE_LABEL[k]}: {wanted.filter((s) => coverageOf(s) === k).length}
              </li>
            ))}
          </ul>
          <p className="pt-muted">AI rewriting is not available. Lines come only from your résumé and evidence vault.</p>
          <Link to={`/discover/${encodeURIComponent(job.id)}`} className="pt-sidelink">Back to the posting</Link>
        </aside>
      </div>
    </Screen>
  );
}

function JobPicker() {
  const { bundle, workspace } = useApp();
  const { jobById } = useIndexes();
  const { rankOf } = rankByNearTerm(bundle.matches);
  const picks = [...new Set([...workspace.shortlist, ...[...rankOf.entries()].sort((a, b) => a[1] - b[1]).slice(0, 6).map(([id]) => id)])].map((id) => jobById.get(id)).filter((j) => j !== undefined);
  return (
    <Screen title="Tailor résumé" lead="Choose the posting to adapt your résumé for. Shortlisted postings come first.">
      <ul className="ct-list-plain pt-picker">
        {picks.map((j) => (
          <li key={j.id}>
            <Link to={`/tailor/${encodeURIComponent(j.id)}`} className="pt-row">
              <span className="pt-row__title">{j.title}{workspace.shortlist.includes(j.id) ? <Icon name="bookmark" size={14} className="pt-row__saved" /> : null}</span>
              <span className="pt-row__meta">{j.organization}</span>
            </Link>
          </li>
        ))}
      </ul>
    </Screen>
  );
}
