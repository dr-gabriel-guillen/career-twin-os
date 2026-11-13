import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Banner, Button, Checkbox, Dialog, EmptyState, Icon, SelectField, TabPanel, Tabs, TextArea, TextField, useAnnounce } from "@careertwin/ui";
import { Page, TruthBadge } from "@/components/common";
import { RESUME_SCHEMA, type EvidenceItem, type ResumeBullet, type ResumeSection, type ResumeVariant } from "@/adapters/proposed";
import type { ProfileClaim } from "@/contracts/types";
import { diffWords, hasChanges } from "@/lib/diff";
import { formatDate, formatDateTime, skillLabel, truncate } from "@/lib/format";
import { reviewVariant, variantToText, type Finding, type VariantReview } from "@/lib/resumeLint";
import { downloadText, newId } from "@/lib/safety";
import { useApp } from "@/state/AppContext";
import { useIndexes } from "@/state/selectors";
import "./resume.css";

/** How a line stands against the truthfulness rules. Drives the gutter mark beside each line. */
type LineState = "supported" | "self" | "check" | "block";
const STATE_LABEL: Record<LineState, string> = {
  supported: "Backed by evidence",
  self: "Self-reported",
  check: "Check before approving",
  block: "Blocks export",
};

function lineState(findings: Finding[], claims: (ProfileClaim | undefined)[]): LineState {
  if (findings.some((f) => f.severity === "block")) return "block";
  if (findings.length > 0) return claims.length > 0 && claims.every((c) => c?.status === "self-reported") && findings.every((f) => f.code === "self-reported") ? "self" : "check";
  return claims.length > 0 && claims.every((c) => c?.status === "supported") ? "supported" : "self";
}

/** What a person can do about a finding. Only links to places that exist; nothing is fixed automatically. */
function remedy(f: Finding): string | null {
  switch (f.code) {
    case "no-evidence-link":
      return "Add the line from a claim instead, or remove it.";
    case "self-reported":
    case "unverified-claim":
      return "Attach evidence to the claim in Profile Evidence, or keep it and confirm it yourself when approving.";
    case "contradicted-claim":
    case "expired-claim":
    case "private-claim":
    case "unknown-claim":
      return "Remove the line or link it to a different claim.";
    case "new-figure":
    case "new-credential":
    case "new-title":
    case "new-name":
    case "reworded":
      return "Edit the wording back to what the claim says.";
    default:
      return null;
  }
}

/**
 * Some upstream variants are labelled with a machine reference (for example `pinned:jp_…`).
 * People see the target role instead; the raw label stays in Technical details.
 */
export function variantTitle(v: ResumeVariant, jobTitle: string | null | undefined): string {
  const machine = /^[a-z][\w-]*:[\w-]+$/i.test(v.label) || /^[a-z]{1,4}_[0-9a-f]{8,}$/i.test(v.label);
  if (!machine) return v.label;
  return jobTitle ? `Résumé for ${jobTitle}` : `Untitled résumé, version ${v.version}`;
}

function Findings({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) return null;
  return (
    <ul className="ct-list-plain rs-findings">
      {findings.map((f, i) => (
        <li key={i} className={f.severity === "block" ? "rs-finding rs-finding--block" : "rs-finding"}>
          <strong>{f.severity === "block" ? "Blocks export" : "Check"}</strong> <span>{f.message}</span>
        </li>
      ))}
    </ul>
  );
}

function DiffText({ before, after }: { before: string; after: string }) {
  const parts = diffWords(before, after);
  return (
    <p className="app-diff">
      {parts.map((p, i) =>
        p.op === "same" ? <span key={i}>{p.text}</span> : p.op === "add" ? <ins key={i} aria-label={`added: ${p.text}`}>{p.text}</ins> : <del key={i} aria-label={`removed: ${p.text}`}>{p.text}</del>,
      )}
    </p>
  );
}

/** The evidence behind one line: each linked claim, its truth state and the evidence items it cites. */
function LineEvidence({ bullet, findings, claimById, evidenceById, compact }: { bullet: ResumeBullet; findings: Finding[]; claimById: Map<string, ProfileClaim>; evidenceById: Map<string, EvidenceItem>; compact?: boolean }) {
  const remedies = [...new Set(findings.map(remedy).filter((r): r is string => r !== null))];
  return (
    <div className={compact ? "rs-ev rs-ev--compact" : "rs-ev"}>
      {remedies.length > 0 ? (
        <div className="rs-ev__fix">
          <h3>What to do</h3>
          <ul className="ct-list-plain">
            {remedies.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <h3>{bullet.claim_ids.length === 0 ? "No claim behind this line" : `Claim${bullet.claim_ids.length === 1 ? "" : "s"} behind this line`}</h3>
      {bullet.claim_ids.length === 0 ? (
        <p className="ct-small ct-muted">Free text has no evidence link. It blocks export until it is linked to a claim or removed.</p>
      ) : (
        <ul className="ct-list-plain rs-ev__claims">
          {bullet.claim_ids.map((id) => {
            const c = claimById.get(id);
            if (!c) {
              return (
                <li key={id} className="rs-ev__claim">
                  <p>This line points at a claim that no longer exists in the vault.</p>
                </li>
              );
            }
            const items = c.evidence_ids.map((e) => evidenceById.get(e));
            return (
              <li key={id} className="rs-ev__claim">
                <p className="rs-ev__claimtext">{c.claim_text}</p>
                <TruthBadge status={c.status} />
                {items.length === 0 ? (
                  <p className="ct-small ct-muted">No evidence attached.</p>
                ) : (
                  <ul className="ct-list-plain rs-ev__items">
                    {items.map((e, i) =>
                      e ? (
                        <li key={e.id}>
                          <span className="rs-ev__item">{e.label}</span>
                          <span className="ct-small ct-muted">
                            {skillLabel(e.kind)}, captured {formatDate(e.captured_at)}. {e.note}
                          </span>
                        </li>
                      ) : (
                        <li key={`missing-${i}`} className="ct-small">
                          <span className="rs-ev__item">Evidence recorded, details not provided</span>
                          <span className="ct-muted">
                            The claim cites <code translate="no">{c.evidence_ids[i]}</code>, but this data source did not include the evidence item itself.
                          </span>
                        </li>
                      ),
                    )}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {compact ? null : (
        <p className="ct-small">
          <Link to="/evidence">Open Profile Evidence</Link>
        </p>
      )}
      <details className="rs-tech">
        <summary>Technical details</summary>
        <dl className="rs-tech__list">
          <dt>Line</dt>
          <dd>
            <code translate="no">{bullet.id}</code>, {bullet.origin === "manual-edit" ? "edited by hand" : "assembled from claims"}
          </dd>
          <dt>Claim references</dt>
          <dd>{bullet.claim_ids.length === 0 ? "none" : bullet.claim_ids.map((id) => <code key={id} translate="no">{id}</code>)}</dd>
        </dl>
      </details>
    </div>
  );
}

/** One-line status of the export gate, always visible above the document. */
function GateBar({ review, locked, onReview }: { review: VariantReview; locked: boolean; onReview: () => void }) {
  if (locked && review.approvalCurrent) return null;
  const blocked = review.blocking > 0;
  return (
    <div className={blocked ? "rs-gate rs-gate--blocked" : "rs-gate"} role="status" data-testid="gate-bar">
      <Icon name={blocked ? "alert" : "check"} size={16} />
      <span>
        {blocked
          ? `Export blocked: ${review.blocking} line${review.blocking === 1 ? "" : "s"} need${review.blocking === 1 ? "s" : ""} evidence or removal.`
          : `Ready for your approval.${review.warnings > 0 ? ` ${review.warnings} line${review.warnings === 1 ? "" : "s"} to double-check.` : ""}`}
      </span>
      <Button size="sm" variant={blocked ? "secondary" : "primary"} onClick={onReview}>
        {blocked ? "See what blocks export" : "Review and approve"}
      </Button>
    </div>
  );
}

export default function ResumePage() {
  const { bundle, workspace, clock, dispatch } = useApp();
  const { jobById, claimById } = useIndexes();
  const announce = useAnnounce();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState("edit");
  const [editing, setEditing] = useState<{ bulletId: string; text: string } | null>(null);
  const [focusLine, setFocusLine] = useState<string | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [confirmRead, setConfirmRead] = useState(false);
  const [addClaim, setAddClaim] = useState<Record<string, string>>({});
  const [freeText, setFreeText] = useState<Record<string, string>>({});
  const [compareWith, setCompareWith] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [newLabel, setNewLabel] = useState("");

  const variants = workspace.resumeVariants;
  const selected = variants.find((v) => v.id === params.get("variant")) ?? variants[0] ?? null;
  const tailorJob = params.get("job") ? jobById.get(params.get("job")!) ?? null : null;
  const candidateRef = bundle.preferences?.candidate_ref ?? "candidate-synthetic-001";
  const candidateName = bundle.preferences?.display_name ?? "Synthetic Candidate";
  const evidenceById = useMemo(() => new Map(bundle.evidence.map((e) => [e.id, e])), [bundle.evidence]);
  const titleOf = (v: ResumeVariant) => variantTitle(v, v.target_job_id ? jobById.get(v.target_job_id)?.title : null);

  const review = useMemo(() => (selected ? reviewVariant(selected, workspace.claims, clock) : null), [selected, workspace.claims, clock]);
  const locked = selected !== null && selected.approval !== null;

  // Versions in lineage order: each variant follows the one it was duplicated from.
  const lineage = useMemo(() => {
    const ids = new Set(variants.map((v) => v.id));
    const children = new Map<string | null, ResumeVariant[]>();
    for (const v of variants) {
      const parent = v.parent_variant_id && ids.has(v.parent_variant_id) ? v.parent_variant_id : null;
      children.set(parent, [...(children.get(parent) ?? []), v]);
    }
    const out: { v: ResumeVariant; depth: number }[] = [];
    const walk = (parent: string | null, depth: number) => {
      for (const v of children.get(parent) ?? []) {
        out.push({ v, depth: Math.min(depth, 3) });
        walk(v.id, depth + 1);
      }
    };
    walk(null, 0);
    return out;
  }, [variants]);

  const select = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("variant", id);
    setParams(next);
    setEditing(null);
    setFocusLine(null);
  };

  const save = (v: ResumeVariant, message: string) => {
    const refused = dispatch({ type: "resume/upsert", variant: { ...v, updated_at: new Date().toISOString() } });
    setNotice(refused);
    if (!refused) announce(message);
  };

  const create = (base: ResumeVariant | null, label: string, jobId: string | null) => {
    const now = new Date().toISOString();
    const v: ResumeVariant = {
      schema_version: RESUME_SCHEMA,
      id: newId("rv"),
      candidate_ref: candidateRef,
      label: label.trim() || (base ? `${titleOf(base)} (copy)` : "New variant"),
      target_job_id: jobId,
      parent_variant_id: base?.id ?? null,
      version: base ? base.version + 1 : 1,
      created_at: now,
      updated_at: now,
      sections: base
        ? base.sections.map((s) => ({ ...s, bullets: s.bullets.map((b) => ({ ...b, claim_ids: [...b.claim_ids] })) }))
        : [{ id: newId("sec"), heading: "Experience", bullets: [] }, { id: newId("sec"), heading: "Skills", bullets: [] }],
      approval: null,
    };
    dispatch({ type: "resume/upsert", variant: v });
    select(v.id);
    setTab("edit");
    setNewLabel("");
    announce(`Created variant “${v.label}”. It is an unapproved draft.`);
  };

  const mapSections = (v: ResumeVariant, fn: (s: ResumeSection) => ResumeSection): ResumeVariant => ({ ...v, sections: v.sections.map(fn) });

  const updateBullet = (bulletId: string, text: string) => {
    if (!selected) return;
    save(mapSections(selected, (s) => ({ ...s, bullets: s.bullets.map((b) => (b.id === bulletId ? { ...b, text, origin: "manual-edit" as const } : b)) })), "Line updated. It is marked as edited by hand.");
    setEditing(null);
  };
  const removeBullet = (bulletId: string) => selected && save(mapSections(selected, (s) => ({ ...s, bullets: s.bullets.filter((b) => b.id !== bulletId) })), "Line removed.");
  const moveBullet = (sectionId: string, index: number, delta: number) => {
    if (!selected) return;
    save(
      mapSections(selected, (s) => {
        if (s.id !== sectionId) return s;
        const target = index + delta;
        if (target < 0 || target >= s.bullets.length) return s;
        const bullets = [...s.bullets];
        const [moved] = bullets.splice(index, 1);
        bullets.splice(target, 0, moved!);
        return { ...s, bullets };
      }),
      "Line moved.",
    );
  };
  const addFromClaim = (sectionId: string) => {
    const claim = claimById.get(addClaim[sectionId] ?? "");
    if (!selected || !claim) return;
    const bullet: ResumeBullet = { id: newId("b"), text: claim.claim_text, claim_ids: [claim.id], origin: "from-claims" };
    save(mapSections(selected, (s) => (s.id === sectionId ? { ...s, bullets: [...s.bullets, bullet] } : s)), "Line added from the claim.");
    setAddClaim({ ...addClaim, [sectionId]: "" });
  };
  const addFreeText = (sectionId: string) => {
    const text = (freeText[sectionId] ?? "").trim();
    if (!selected || text.length === 0) return;
    const bullet: ResumeBullet = { id: newId("b"), text, claim_ids: [], origin: "manual-edit" };
    save(mapSections(selected, (s) => (s.id === sectionId ? { ...s, bullets: [...s.bullets, bullet] } : s)), "Line added without an evidence link. It blocks export until linked or removed.");
    setFreeText({ ...freeText, [sectionId]: "" });
    setFocusLine(bullet.id);
  };

  const approve = () => {
    if (!selected || !review) return;
    save({ ...selected, approval: { approved_at: new Date().toISOString(), approval_token_ref: newId("approval"), content_digest: review.digest } }, "Approved. This exact content is now locked.");
    setApproveOpen(false);
    setConfirmRead(false);
  };

  const other = selected ? variants.find((v) => v.id === (compareWith ?? selected.parent_variant_id)) ?? null : null;
  const lines = selected ? selected.sections.flatMap((s) => s.bullets) : [];
  const findingsOf = (id: string) => review?.byBullet.get(id) ?? [];
  const activeLine = lines.find((b) => b.id === focusLine) ?? lines.find((b) => findingsOf(b.id).some((f) => f.severity === "block")) ?? lines[0] ?? null;
  const targetTitle = selected?.target_job_id ? jobById.get(selected.target_job_id)?.title ?? null : null;
  const statusText = (v: ResumeVariant, r: VariantReview) => (v.approval ? (r.approvalCurrent ? "Approved by you" : "Approval out of date") : "Draft");

  return (
    <Page title="Resume Studio" lead="Each variant is assembled from claims in your evidence vault. The mark beside every line shows what it rests on; nothing can be exported until you approve the exact text.">
      {tailorJob ? (
        <Banner tone="info" title={`Tailoring for: ${tailorJob.title}`} data-testid="tailor-banner" actions={<Button variant="primary" onClick={() => create(selected, `For ${truncate(tailorJob.title, 40)}`, tailorJob.id)} data-testid="tailor-create">Create a variant for this posting</Button>}>
          {tailorJob.organization}. The posting lists: {tailorJob.skills.map(skillLabel).join(", ")}. A new variant starts as a copy of the one selected; you then reorder and trim it. No text is generated for you.
        </Banner>
      ) : null}
      {notice ? (
        <Banner tone="warning" role="alert" title="Not changed" actions={<Button size="sm" onClick={() => setNotice(null)}>Dismiss</Button>}>
          {notice}
        </Banner>
      ) : null}

      <div className={tab === "edit" && selected ? "rs-studio rs-studio--inspect" : "rs-studio"}>
        <nav className="rs-versions" aria-label="Résumé versions">
          <h2 className="rs-versions__title">Versions</h2>
          {variants.length === 0 ? (
            <EmptyState title="No résumé variants yet.">
              <p>Create a blank variant, then add lines from your evidence vault.</p>
            </EmptyState>
          ) : (
            <ul className="ct-list-plain rs-versions__list" data-testid="variant-list">
              {lineage.map(({ v, depth }) => {
                const r = reviewVariant(v, workspace.claims, clock);
                const current = selected?.id === v.id;
                return (
                  <li key={v.id} style={{ paddingLeft: `${depth * 14}px` }}>
                    <button type="button" className="rs-version" aria-current={current ? "true" : undefined} onClick={() => select(v.id)} data-testid={`variant-${v.id}`}>
                      <span className="rs-version__title">{titleOf(v)}</span>
                      <span className="rs-version__meta">
                        <span>Version {v.version}</span>
                        <span className={v.approval ? (r.approvalCurrent ? "rs-st rs-st--ok" : "rs-st rs-st--warn") : "rs-st"}>{statusText(v, r)}</span>
                        {r.blocking > 0 ? <span className="rs-st rs-st--block">{r.blocking} blocking</span> : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <details className="rs-new">
            <summary>New variant</summary>
            <div className="ct-stack">
              <TextField label="Name" value={newLabel} onChange={(e) => setNewLabel(e.target.value)} maxLength={80} data-testid="variant-label" />
              <div className="ct-row">
                <Button size="sm" onClick={() => create(selected, newLabel, selected?.target_job_id ?? null)} disabled={!selected} data-testid="variant-duplicate">
                  Duplicate selected
                </Button>
                <Button size="sm" onClick={() => create(null, newLabel, null)} data-testid="variant-blank">
                  Start blank
                </Button>
              </div>
            </div>
          </details>
        </nav>

        {selected && review ? (
          <section className="rs-desk" data-testid="variant-editor" aria-labelledby="rs-doc-title">
            <header className="rs-toolbar">
              <div className="rs-doctitle">
                <h2 id="rs-doc-title">{titleOf(selected)}</h2>
                <p className="rs-doctitle__meta">
                  Version {selected.version}
                  {targetTitle ? <>, tailored for {targetTitle}</> : <>, general</>}
                  <span className="ct-muted">. Updated {formatDateTime(selected.updated_at)}</span>
                </p>
              </div>
              <Tabs idBase="rs" label="Résumé variant" active={tab} onChange={setTab} tabs={[{ id: "edit", label: "Edit" }, { id: "compare", label: "Compare" }, { id: "review", label: `Review & export${review.blocking > 0 ? ` (${review.blocking} blocking)` : ""}` }]} />
            </header>
            {locked ? (
              <Banner tone={review.approvalCurrent ? "success" : "warning"} title={review.approvalCurrent ? "Approved snapshot — locked." : "Approval is out of date."} actions={<Button size="sm" onClick={() => create(selected, `${titleOf(selected)} (next)`, selected.target_job_id)} data-testid="new-version">Create a new version to edit</Button>} data-testid="locked-banner">
                Approved {formatDateTime(selected.approval?.approved_at)}. An approved variant cannot be edited, so what you approved is exactly what gets exported.
              </Banner>
            ) : null}
            {tab !== "review" ? <GateBar review={review} locked={locked} onReview={() => setTab("review")} /> : null}

            <TabPanel idBase="rs" id="edit" active={tab}>
              <article className="rs-paper" aria-label={`Résumé document: ${titleOf(selected)}`}>
                <header className="rs-paper__head">
                  <h3 className="rs-paper__name">{candidateName}</h3>
                  <p className="rs-paper__role">{targetTitle ?? "General résumé"}</p>
                </header>
                {review.headingFindings.length > 0 ? <Findings findings={review.headingFindings} /> : null}
                {selected.sections.map((s) => (
                  <section key={s.id} className="rs-section" aria-label={s.heading}>
                    <h4 className="rs-heading">{s.heading}</h4>
                    {s.bullets.length === 0 ? <p className="rs-empty">No lines in this section yet.</p> : null}
                    <ul className="ct-list-plain rs-lines">
                      {s.bullets.map((b, i) => {
                        const findings = findingsOf(b.id);
                        const claims = b.claim_ids.map((id) => claimById.get(id));
                        const state = lineState(findings, claims);
                        const active = activeLine?.id === b.id;
                        const isEditing = editing?.bulletId === b.id;
                        return (
                          <li key={b.id} className={["app-bullet", `rs-line--${state}`, state === "block" ? "app-bullet--block" : "", active ? "rs-line--active" : ""].join(" ")} data-testid={`bullet-${b.id}`}>
                            <span className={`rs-gutter rs-gutter--${state}`} title={STATE_LABEL[state]} aria-hidden="true" />
                            <div className="rs-line__body">
                              {isEditing ? (
                                <div className="rs-editbox">
                                  <TextArea label="Line text" value={editing.text} onChange={(e) => setEditing({ bulletId: b.id, text: e.target.value })} maxLength={400} data-testid="bullet-text" />
                                  <div className="ct-row">
                                    <Button variant="primary" size="sm" onClick={() => updateBullet(b.id, editing.text.trim())} disabled={editing.text.trim().length === 0} data-testid="bullet-save">
                                      Save line
                                    </Button>
                                    <Button size="sm" onClick={() => setEditing(null)}>
                                      Cancel
                                    </Button>
                                  </div>
                                </div>
                              ) : (
                                <button type="button" className="rs-line" aria-pressed={active} onClick={() => setFocusLine(b.id)}>
                                  {b.text}
                                </button>
                              )}
                              <p className="rs-cite">
                                <span className="ct-sr-only">{STATE_LABEL[state]}. </span>
                                {b.claim_ids.length === 0
                                  ? "Not linked to any claim"
                                  : claims.map((c, ci) => (
                                      <span key={b.claim_ids[ci]}>
                                        {ci > 0 ? ", " : ""}
                                        {c ? (c.status === "supported" ? "Supported by evidence" : c.status === "self-reported" ? "Self-reported" : c.status === "unverified" ? "Unverified claim" : "Contradicted claim") : "Missing claim"}
                                      </span>
                                    ))}
                                {b.origin === "manual-edit" ? <span>, edited by hand</span> : null}
                              </p>
                              <Findings findings={findings} />
                            </div>
                            {!locked && !isEditing ? (
                              <div className="rs-actions" role="group" aria-label="Line actions">
                                <Button size="sm" variant="ghost" onClick={() => setEditing({ bulletId: b.id, text: b.text })} aria-label="Edit text" title="Edit text" data-testid={`bullet-edit-${b.id}`}>
                                  <Icon name="edit" size={15} />
                                </Button>
                                <Button size="sm" variant="ghost" onClick={() => moveBullet(s.id, i, -1)} disabled={i === 0} aria-label="Move line up" title="Move line up">
                                  <Icon name="up" size={15} />
                                </Button>
                                <Button size="sm" variant="ghost" onClick={() => moveBullet(s.id, i, 1)} disabled={i === s.bullets.length - 1} aria-label="Move line down" title="Move line down">
                                  <Icon name="down" size={15} />
                                </Button>
                                <Button size="sm" variant="ghost" className="rs-remove" onClick={() => removeBullet(b.id)} aria-label="Remove" title="Remove line" data-testid={`bullet-remove-${b.id}`}>
                                  <Icon name="trash" size={15} />
                                </Button>
                              </div>
                            ) : null}
                            {active ? (
                              <div className="rs-inline-ev">
                                <LineEvidence bullet={b} findings={findings} claimById={claimById} evidenceById={evidenceById} compact />
                              </div>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                    {!locked ? (
                      <div className="rs-add">
                        <div className="rs-addrow">
                          <SelectField
                            label={`Add a line to ${s.heading} from a claim`}
                            value={addClaim[s.id] ?? ""}
                            onChange={(e) => setAddClaim({ ...addClaim, [s.id]: e.target.value })}
                            options={[{ value: "", label: "Add a line from a claim…" }, ...workspace.claims.map((c) => ({ value: c.id, label: `${truncate(c.claim_text, 70)} (${c.status})` }))]}
                            data-testid={`add-claim-${s.id}`}
                          />
                          <Button size="sm" onClick={() => addFromClaim(s.id)} disabled={!addClaim[s.id]} data-testid={`add-claim-btn-${s.id}`}>
                            Add
                          </Button>
                        </div>
                        <div className="rs-addrow">
                          <TextField label={`Add a free-text line to ${s.heading}`} hint="Free text has no evidence link and will block export." placeholder="Or type a line…" value={freeText[s.id] ?? ""} onChange={(e) => setFreeText({ ...freeText, [s.id]: e.target.value })} maxLength={400} data-testid={`free-text-${s.id}`} />
                          <Button size="sm" onClick={() => addFreeText(s.id)} disabled={!(freeText[s.id] ?? "").trim()} data-testid={`free-text-btn-${s.id}`}>
                            Add
                          </Button>
                        </div>
                      </div>
                    ) : null}
                  </section>
                ))}
              </article>
            </TabPanel>

            <TabPanel idBase="rs" id="compare" active={tab}>
              <div className="ct-stack rs-panel">
                <SelectField label="Compare this variant with" value={other?.id ?? ""} onChange={(e) => setCompareWith(e.target.value || null)} options={[{ value: "", label: "Choose a variant…" }, ...variants.filter((v) => v.id !== selected.id).map((v) => ({ value: v.id, label: `${titleOf(v)} (version ${v.version})` }))]} data-testid="compare-select" />
                {!other ? (
                  <EmptyState title="Nothing to compare with yet.">
                    <p>Pick another variant. A duplicated variant is compared with its parent by default.</p>
                  </EmptyState>
                ) : (
                  <div className="ct-stack" data-testid="compare-view">
                    <p className="ct-small ct-muted">
                      Changes from “{titleOf(other)}” to “{titleOf(selected)}”. <ins className="app-diff">Added</ins> and <del className="app-diff">removed</del> words are marked.
                    </p>
                    {(() => {
                      const before = new Map(other.sections.flatMap((s) => s.bullets.map((b) => [b.id, b] as const)));
                      const after = new Map(selected.sections.flatMap((s) => s.bullets.map((b) => [b.id, b] as const)));
                      const beforeOrder = other.sections.flatMap((s) => s.bullets.map((b) => b.id)).filter((id) => after.has(id));
                      const afterOrder = selected.sections.flatMap((s) => s.bullets.map((b) => b.id)).filter((id) => before.has(id));
                      const rows = [
                        ...[...after.values()].map((b) => {
                          const prev = before.get(b.id);
                          if (!prev) return { key: b.id, kind: "Added line", node: <DiffText before="" after={b.text} /> };
                          if (!hasChanges(diffWords(prev.text, b.text))) return null;
                          return { key: b.id, kind: "Reworded line", node: <DiffText before={prev.text} after={b.text} /> };
                        }),
                        ...[...before.values()].filter((b) => !after.has(b.id)).map((b) => ({ key: `del-${b.id}`, kind: "Removed line", node: <DiffText before={b.text} after="" /> })),
                      ].filter((r): r is { key: string; kind: string; node: React.JSX.Element } => r !== null);
                      const reordered = beforeOrder.join("|") !== afterOrder.join("|");
                      if (rows.length === 0 && !reordered) return <p>No differences in text or order.</p>;
                      return (
                        <>
                          {reordered ? <Banner tone="info" title="The order of shared lines changed." /> : null}
                          <ul className="ct-list-plain rs-diffs">
                            {rows.map((r) => (
                              <li key={r.key}>
                                <strong className="ct-small">{r.kind}</strong>
                                {r.node}
                              </li>
                            ))}
                          </ul>
                        </>
                      );
                    })()}
                  </div>
                )}
              </div>
            </TabPanel>

            <TabPanel idBase="rs" id="review" active={tab}>
              <div className="ct-stack rs-panel" data-testid="review-panel">
                {review.exportBlockers.length > 0 ? (
                  <Banner tone="warning" title="Export is blocked." data-testid="export-blockers">
                    <ul>
                      {review.exportBlockers.map((b) => (
                        <li key={b}>{b}</li>
                      ))}
                    </ul>
                  </Banner>
                ) : (
                  <Banner tone="success" title="Ready to export." data-testid="export-ready">
                    You approved this exact content. Exports are files saved to this device; nothing is uploaded or sent.
                  </Banner>
                )}
                <p>
                  {review.blocking} blocking issue{review.blocking === 1 ? "" : "s"}, {review.warnings} thing{review.warnings === 1 ? "" : "s"} to double-check.
                </p>
                <div className="ct-row">
                  <Button variant="primary" onClick={() => setApproveOpen(true)} disabled={locked || review.blocking > 0 || selected.sections.every((s) => s.bullets.length === 0)} data-testid="approve-open">
                    {locked ? "Already approved" : "Approve this exact text…"}
                  </Button>
                  <Button onClick={() => downloadText(`${selected.id}.txt`, variantToText(selected, candidateName), "text/plain")} disabled={!review.exportAllowed} data-testid="export-txt">
                    Download .txt
                  </Button>
                  <Button onClick={() => downloadText(`${selected.id}.json`, JSON.stringify(selected, null, 2), "application/json")} disabled={!review.exportAllowed} data-testid="export-json">
                    Download .json
                  </Button>
                </div>
                <h3>Exact text that will be exported</h3>
                <pre className="app-preview" data-testid="resume-preview">{variantToText(selected, candidateName)}</pre>
                <details className="rs-tech">
                  <summary>Technical details</summary>
                  <dl className="rs-tech__list">
                    <dt>Variant</dt>
                    <dd>
                      <code translate="no">{selected.id}</code>
                      {selected.label !== titleOf(selected) ? (
                        <>
                          , labelled <code translate="no">{selected.label}</code> upstream
                        </>
                      ) : null}
                    </dd>
                    <dt>Content fingerprint</dt>
                    <dd>
                      <code translate="no">{review.digest.slice(0, 16)}…</code>
                    </dd>
                    {selected.approval ? (
                      <>
                        <dt>Approval reference</dt>
                        <dd>
                          <code translate="no">{selected.approval.approval_token_ref}</code>
                        </dd>
                      </>
                    ) : null}
                  </dl>
                </details>
                <p className="ct-small ct-muted">
                  After approval, record the application yourself in <Link to="/applications">Applications</Link>. CareerTwin does not send résumés anywhere.
                </p>
              </div>
            </TabPanel>
          </section>
        ) : (
          <EmptyState title="Select or create a variant to begin." />
        )}

        {tab === "edit" && selected && review ? (
          <aside className="rs-inspector" aria-labelledby="rs-insp-title">
            <h2 id="rs-insp-title" className="rs-inspector__title">
              Selected line
            </h2>
            {activeLine ? (
              <>
                <blockquote className="rs-inspector__quote">{activeLine.text}</blockquote>
                <p className={`rs-inspector__state rs-state--${lineState(findingsOf(activeLine.id), activeLine.claim_ids.map((id) => claimById.get(id)))}`}>
                  {STATE_LABEL[lineState(findingsOf(activeLine.id), activeLine.claim_ids.map((id) => claimById.get(id)))]}
                </p>
                <LineEvidence bullet={activeLine} findings={findingsOf(activeLine.id)} claimById={claimById} evidenceById={evidenceById} />
              </>
            ) : (
              <p className="ct-muted">Select a line in the document to see the claims and evidence behind it.</p>
            )}
            <div className="rs-legend" aria-label="What the marks mean">
              {(Object.keys(STATE_LABEL) as LineState[]).map((k) => (
                <span key={k}>
                  <i className={`rs-gutter rs-gutter--${k}`} aria-hidden="true" /> {STATE_LABEL[k]}
                </span>
              ))}
            </div>
          </aside>
        ) : null}
      </div>

      <Dialog
        open={approveOpen}
        onClose={() => {
          setApproveOpen(false);
          setConfirmRead(false);
        }}
        title="Approve this résumé variant?"
        testId="approve-dialog"
        actions={
          <>
            <Button
              onClick={() => {
                setApproveOpen(false);
                setConfirmRead(false);
              }}
            >
              Cancel
            </Button>
            <Button variant="primary" onClick={approve} disabled={!confirmRead} data-testid="approve-confirm">
              Approve and lock
            </Button>
          </>
        }
      >
        <p>Approval records that you, not the software, vouch for every line. The approved text is locked; to change it you create a new version.</p>
        {review && review.warnings > 0 ? (
          <Banner tone="warning" title={`${review.warnings} line${review.warnings === 1 ? "" : "s"} to double-check`}>
            Self-reported or hand-edited lines are allowed, but only you can confirm they are accurate.
          </Banner>
        ) : null}
        <Checkbox label="I have read every line and each one is true and supported as shown." checked={confirmRead} onChange={(e) => setConfirmRead(e.target.checked)} data-testid="approve-check" />
      </Dialog>
    </Page>
  );
}
