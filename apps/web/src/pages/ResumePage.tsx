import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Badge, Banner, Button, Card, Checkbox, Dialog, EmptyState, SelectField, TabPanel, Tabs, TextArea, TextField, useAnnounce } from "@careertwin/ui";
import { Page, TruthBadge } from "@/components/common";
import { RESUME_SCHEMA, type ResumeBullet, type ResumeSection, type ResumeVariant } from "@/adapters/proposed";
import { diffWords, hasChanges } from "@/lib/diff";
import { formatDateTime, humanise, truncate } from "@/lib/format";
import { reviewVariant, variantToText, type Finding } from "@/lib/resumeLint";
import { downloadText, newId } from "@/lib/safety";
import { useApp } from "@/state/AppContext";
import { useIndexes } from "@/state/selectors";

function Findings({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) return <Badge tone="success">Traceable to evidence</Badge>;
  return (
    <ul className="ct-list-plain ct-stack">
      {findings.map((f, i) => (
        <li key={i} className="ct-row">
          <Badge tone={f.severity === "block" ? "danger" : "warning"}>{f.severity === "block" ? "Blocks export" : "Check"}</Badge>
          <span className="ct-small">{f.message}</span>
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

export default function ResumePage() {
  const { bundle, workspace, clock, dispatch } = useApp();
  const { jobById, claimById } = useIndexes();
  const announce = useAnnounce();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState("edit");
  const [editing, setEditing] = useState<{ bulletId: string; text: string } | null>(null);
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

  const review = useMemo(() => (selected ? reviewVariant(selected, workspace.claims, clock) : null), [selected, workspace.claims, clock]);
  const locked = selected !== null && selected.approval !== null;

  const select = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("variant", id);
    setParams(next);
    setEditing(null);
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
      label: label.trim() || (base ? `${base.label} (copy)` : "New variant"),
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
  };
  const addFreeText = (sectionId: string) => {
    const text = (freeText[sectionId] ?? "").trim();
    if (!selected || text.length === 0) return;
    const bullet: ResumeBullet = { id: newId("b"), text, claim_ids: [], origin: "manual-edit" };
    save(mapSections(selected, (s) => (s.id === sectionId ? { ...s, bullets: [...s.bullets, bullet] } : s)), "Line added without an evidence link. It blocks export until linked or removed.");
    setFreeText({ ...freeText, [sectionId]: "" });
  };

  const approve = () => {
    if (!selected || !review) return;
    save({ ...selected, approval: { approved_at: new Date().toISOString(), approval_token_ref: newId("approval"), content_digest: review.digest } }, "Approved. This exact content is now locked.");
    setApproveOpen(false);
    setConfirmRead(false);
  };

  const other = selected ? variants.find((v) => v.id === (compareWith ?? selected.parent_variant_id)) ?? null : null;

  return (
    <Page
      title="Resume Studio"
      lead="Résumé variants are assembled from claims in your evidence vault. Every line shows what it rests on. Nothing can be exported until you have approved the exact text, and unsupported lines block export."
    >
      {tailorJob ? (
        <Banner tone="info" title={`Tailoring for: ${tailorJob.title}`} data-testid="tailor-banner" actions={<Button variant="primary" onClick={() => create(selected, `For ${truncate(tailorJob.title, 40)}`, tailorJob.id)} data-testid="tailor-create">Create a variant for this posting</Button>}>
          {tailorJob.organization}. The posting lists: {tailorJob.skills.map(humanise).join(", ")}. A new variant starts as a copy of the one selected below; you then reorder and trim it. No text is generated for you.
        </Banner>
      ) : null}
      {notice ? (
        <Banner tone="warning" role="alert" title="Not changed" actions={<Button size="sm" onClick={() => setNotice(null)}>Dismiss</Button>}>
          {notice}
        </Banner>
      ) : null}

      <div className="app-split">
        <Card title="Variants">
          {variants.length === 0 ? (
            <EmptyState title="No résumé variants yet.">
              <p>Create a blank variant, then add lines from your evidence vault.</p>
            </EmptyState>
          ) : (
            <ul className="ct-list-plain ct-stack" data-testid="variant-list">
              {variants.map((v) => {
                const r = reviewVariant(v, workspace.claims, clock);
                return (
                  <li key={v.id}>
                    <button type="button" className="app-pick" aria-current={selected?.id === v.id ? "true" : undefined} onClick={() => select(v.id)} data-testid={`variant-${v.id}`}>
                      <strong>{v.label}</strong>
                      <span className="ct-row ct-small">
                        <span>v{v.version}</span>
                        {v.approval ? <Badge tone={r.approvalCurrent ? "success" : "warning"}>{r.approvalCurrent ? "Approved by you" : "Approval out of date"}</Badge> : <Badge>Draft</Badge>}
                        {r.blocking > 0 ? <Badge tone="danger">{r.blocking} blocking</Badge> : null}
                        {v.target_job_id ? <span className="ct-muted">for {truncate(jobById.get(v.target_job_id)?.title ?? v.target_job_id, 32)}</span> : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="ct-stack">
            <TextField label="Name for a new variant" value={newLabel} onChange={(e) => setNewLabel(e.target.value)} maxLength={80} data-testid="variant-label" />
            <div className="ct-row">
              <Button onClick={() => create(selected, newLabel, selected?.target_job_id ?? null)} disabled={!selected} data-testid="variant-duplicate">
                Duplicate selected
              </Button>
              <Button onClick={() => create(null, newLabel, null)} data-testid="variant-blank">
                New blank variant
              </Button>
            </div>
          </div>
        </Card>

        {selected && review ? (
          <div className="ct-stack" data-testid="variant-editor">
            <Card title={selected.label}>
              <div className="ct-row">
                <Badge>Version {selected.version}</Badge>
                {selected.target_job_id ? <Badge tone="info">Target: {truncate(jobById.get(selected.target_job_id)?.title ?? selected.target_job_id, 40)}</Badge> : <Badge>General</Badge>}
                <span className="ct-small ct-muted">Updated {formatDateTime(selected.updated_at)}</span>
              </div>
              {locked ? (
                <Banner tone={review.approvalCurrent ? "success" : "warning"} title={review.approvalCurrent ? "Approved snapshot — locked." : "Approval is out of date."} actions={<Button onClick={() => create(selected, `${selected.label} (next)`, selected.target_job_id)} data-testid="new-version">Create a new version to edit</Button>} data-testid="locked-banner">
                  Approved {formatDateTime(selected.approval?.approved_at)} · approval <code>{selected.approval?.approval_token_ref}</code>. An approved variant cannot be edited, so what you approved is exactly what gets exported.
                </Banner>
              ) : null}
              <Tabs idBase="rs" label="Résumé variant" active={tab} onChange={setTab} tabs={[{ id: "edit", label: "Edit" }, { id: "compare", label: "Compare" }, { id: "review", label: `Review & export${review.blocking > 0 ? ` (${review.blocking} blocking)` : ""}` }]} />

              <TabPanel idBase="rs" id="edit" active={tab}>
                <div className="ct-stack ct-stack--lg">
                  {selected.sections.map((s) => (
                    <section key={s.id} className="ct-stack" aria-label={s.heading}>
                      <h3>{s.heading}</h3>
                      {s.bullets.length === 0 ? <p className="ct-muted">No lines in this section.</p> : null}
                      <ul className="ct-list-plain ct-stack">
                        {s.bullets.map((b, i) => {
                          const findings = review.byBullet.get(b.id) ?? [];
                          const blocked = findings.some((f) => f.severity === "block");
                          return (
                            <li key={b.id} className={blocked ? "app-bullet app-bullet--block" : "app-bullet"} data-testid={`bullet-${b.id}`}>
                              {editing?.bulletId === b.id ? (
                                <>
                                  <TextArea label="Line text" value={editing.text} onChange={(e) => setEditing({ bulletId: b.id, text: e.target.value })} maxLength={400} data-testid="bullet-text" />
                                  <div className="ct-row">
                                    <Button variant="primary" size="sm" onClick={() => updateBullet(b.id, editing.text.trim())} disabled={editing.text.trim().length === 0} data-testid="bullet-save">
                                      Save line
                                    </Button>
                                    <Button size="sm" onClick={() => setEditing(null)}>
                                      Cancel
                                    </Button>
                                  </div>
                                </>
                              ) : (
                                <p>{b.text}</p>
                              )}
                              <div className="ct-row ct-small">
                                {b.origin === "manual-edit" ? <Badge tone="warning">Edited by hand</Badge> : <Badge>From claims</Badge>}
                                {b.claim_ids.map((id) => {
                                  const c = claimById.get(id);
                                  return c ? <TruthBadge key={id} status={c.status} /> : <Badge key={id} tone="danger">Missing claim</Badge>;
                                })}
                              </div>
                              {b.claim_ids.length > 0 ? (
                                <details>
                                  <summary className="ct-small">Evidence references ({b.claim_ids.length})</summary>
                                  <ul className="ct-small">
                                    {b.claim_ids.map((id) => (
                                      <li key={id}>
                                        <code>{id}</code>: {claimById.get(id)?.claim_text ?? "claim no longer exists"}
                                        {claimById.get(id) ? ` — evidence: ${claimById.get(id)!.evidence_ids.join(", ") || "none"}` : ""}
                                      </li>
                                    ))}
                                  </ul>
                                </details>
                              ) : null}
                              <Findings findings={findings} />
                              {!locked && editing?.bulletId !== b.id ? (
                                <div className="ct-row">
                                  <Button size="sm" onClick={() => setEditing({ bulletId: b.id, text: b.text })} data-testid={`bullet-edit-${b.id}`}>
                                    Edit text
                                  </Button>
                                  <Button size="sm" onClick={() => moveBullet(s.id, i, -1)} disabled={i === 0} aria-label="Move line up">
                                    ↑
                                  </Button>
                                  <Button size="sm" onClick={() => moveBullet(s.id, i, 1)} disabled={i === s.bullets.length - 1} aria-label="Move line down">
                                    ↓
                                  </Button>
                                  <Button size="sm" variant="danger" onClick={() => removeBullet(b.id)} data-testid={`bullet-remove-${b.id}`}>
                                    Remove
                                  </Button>
                                </div>
                              ) : null}
                            </li>
                          );
                        })}
                      </ul>
                      {!locked ? (
                        <div className="ct-grid ct-grid--2">
                          <div className="ct-stack">
                            <SelectField
                              label={`Add a line to ${s.heading} from a claim`}
                              value={addClaim[s.id] ?? ""}
                              onChange={(e) => setAddClaim({ ...addClaim, [s.id]: e.target.value })}
                              options={[{ value: "", label: "Choose a claim…" }, ...workspace.claims.map((c) => ({ value: c.id, label: `[${c.status}] ${truncate(c.claim_text, 70)}` }))]}
                              data-testid={`add-claim-${s.id}`}
                            />
                            <Button size="sm" onClick={() => addFromClaim(s.id)} disabled={!addClaim[s.id]} data-testid={`add-claim-btn-${s.id}`}>
                              Add line from claim
                            </Button>
                          </div>
                          <div className="ct-stack">
                            <TextField label={`Add a free-text line to ${s.heading}`} hint="Free text has no evidence link and will block export." value={freeText[s.id] ?? ""} onChange={(e) => setFreeText({ ...freeText, [s.id]: e.target.value })} maxLength={400} data-testid={`free-text-${s.id}`} />
                            <Button size="sm" onClick={() => addFreeText(s.id)} disabled={!(freeText[s.id] ?? "").trim()} data-testid={`free-text-btn-${s.id}`}>
                              Add free-text line
                            </Button>
                          </div>
                        </div>
                      ) : null}
                    </section>
                  ))}
                </div>
              </TabPanel>

              <TabPanel idBase="rs" id="compare" active={tab}>
                <div className="ct-stack">
                  <SelectField label="Compare this variant with" value={other?.id ?? ""} onChange={(e) => setCompareWith(e.target.value || null)} options={[{ value: "", label: "Choose a variant…" }, ...variants.filter((v) => v.id !== selected.id).map((v) => ({ value: v.id, label: `${v.label} (v${v.version})` }))]} data-testid="compare-select" />
                  {!other ? (
                    <EmptyState title="Nothing to compare with yet.">
                      <p>Pick another variant. A duplicated variant is compared with its parent by default.</p>
                    </EmptyState>
                  ) : (
                    <div className="ct-stack" data-testid="compare-view">
                      <p className="ct-small ct-muted">
                        Changes from “{other.label}” to “{selected.label}”. <ins className="app-diff">Added</ins> · <del className="app-diff">Removed</del>
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
                            <ul className="ct-list-plain ct-stack">
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
                <div className="ct-stack" data-testid="review-panel">
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
                    {review.blocking} blocking issue{review.blocking === 1 ? "" : "s"} · {review.warnings} thing{review.warnings === 1 ? "" : "s"} to double-check. Content fingerprint:{" "}
                    <code>{review.digest.slice(0, 16)}…</code>
                  </p>
                  <h3>Preview</h3>
                  <pre className="app-preview" data-testid="resume-preview">{variantToText(selected, candidateName)}</pre>
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
                  <p className="ct-small ct-muted">
                    After approval, record the application yourself in <Link to="/applications">Applications</Link>. CareerTwin does not send résumés anywhere.
                  </p>
                </div>
              </TabPanel>
            </Card>
          </div>
        ) : (
          <EmptyState title="Select or create a variant to begin." />
        )}
      </div>

      <Dialog
        open={approveOpen}
        onClose={() => { setApproveOpen(false); setConfirmRead(false); }}
        title="Approve this résumé variant?"
        testId="approve-dialog"
        actions={
          <>
            <Button onClick={() => { setApproveOpen(false); setConfirmRead(false); }}>Cancel</Button>
            <Button variant="primary" onClick={approve} disabled={!confirmRead} data-testid="approve-confirm">
              Approve and lock
            </Button>
          </>
        }
      >
        <p>Approval records that you, not the software, vouch for every line. The approved text is locked; to change it you create a new version.</p>
        {review && review.warnings > 0 ? <Banner tone="warning" title={`${review.warnings} line${review.warnings === 1 ? "" : "s"} to double-check`}>Self-reported or hand-edited lines are allowed, but only you can confirm they are accurate.</Banner> : null}
        <Checkbox label="I have read every line and each one is true and supported as shown." checked={confirmRead} onChange={(e) => setConfirmRead(e.target.checked)} data-testid="approve-check" />
      </Dialog>
    </Page>
  );
}
