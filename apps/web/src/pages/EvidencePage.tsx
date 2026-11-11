import { useMemo, useState } from "react";
import { Badge, Banner, Button, Card, Dialog, EmptyState, Facts, SelectField, TableRegion, TextArea, TextField, useAnnounce } from "@careertwin/ui";
import { CLAIM_LABEL, ConfirmDialog, Page, SkillChips, TruthBadge } from "@/components/common";
import { CLAIM_STATUSES, CLAIM_TYPES, CLAIM_VISIBILITIES, type ClaimStatus, type ClaimType, type ClaimVisibility, type ProfileClaim } from "@/contracts/types";
import { formatDate, formatDateTime, humanise } from "@/lib/format";
import { newId } from "@/lib/safety";
import { USER_SETTABLE_STATUSES } from "@/state/workspace";
import { useApp } from "@/state/AppContext";

interface Draft {
  id: string | null;
  claim_type: ClaimType;
  claim_text: string;
  skills: string;
  visibility: ClaimVisibility;
  status: ClaimStatus;
}

const emptyDraft: Draft = { id: null, claim_type: "skill", claim_text: "", skills: "", visibility: "private", status: "self-reported" };

export default function EvidencePage() {
  const { bundle, workspace, clock, dispatch } = useApp();
  const announce = useAnnounce();
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<ProfileClaim | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const evidenceById = useMemo(() => new Map(bundle.evidence.map((e) => [e.id, e])), [bundle.evidence]);
  const usage = useMemo(() => {
    const m = new Map<string, number>();
    for (const v of workspace.resumeVariants) for (const s of v.sections) for (const b of s.bullets) for (const id of b.claim_ids) m.set(id, (m.get(id) ?? 0) + 1);
    return m;
  }, [workspace.resumeVariants]);

  const counts = useMemo(() => {
    const c: Record<ClaimStatus, number> = { supported: 0, "self-reported": 0, unverified: 0, contradicted: 0 };
    for (const claim of workspace.claims) c[claim.status]++;
    return c;
  }, [workspace.claims]);

  const visible = workspace.claims.filter((c) => (statusFilter === "all" || c.status === statusFilter) && (typeFilter === "all" || c.claim_type === typeFilter));
  const subject = bundle.preferences?.candidate_ref ?? "candidate-synthetic-001";

  const setStatus = (claim: ProfileClaim, status: ClaimStatus) => {
    const refused = dispatch({ type: "claim/setStatus", id: claim.id, status, at: new Date().toISOString(), auditId: newId("audit") });
    setNotice(refused);
    if (!refused) announce(`Truth state changed to ${CLAIM_LABEL[status]}.`);
  };

  const saveDraft = () => {
    if (!draft) return;
    const text = draft.claim_text.trim();
    if (text.length < 3) return setDraftError("Describe the claim in at least three characters.");
    if (text.length > 400) return setDraftError("Keep the claim under 400 characters.");
    const existing = draft.id ? workspace.claims.find((c) => c.id === draft.id) : undefined;
    const skills = [...new Set(draft.skills.split(",").map((s) => s.trim().toLowerCase().replace(/\s+/g, "-")).filter(Boolean))];
    const at = new Date().toISOString();
    const textChanged = existing !== undefined && existing.claim_text !== text;
    const claim: ProfileClaim = {
      id: existing?.id ?? newId("claim"),
      subject_id: existing?.subject_id ?? subject,
      claim_type: draft.claim_type,
      claim_text: text,
      // Rewording detaches a claim from what its evidence showed: the evidence references are dropped
      // and a supported claim becomes unverified. The reducer enforces the same rule.
      status: existing ? (textChanged && existing.status === "supported" ? "unverified" : existing.status) : draft.status,
      evidence_ids: existing && !textChanged ? existing.evidence_ids : [],
      visibility: draft.visibility,
      updated_at: at,
      skills,
    };
    if (existing?.valid_to !== undefined) claim.valid_to = existing.valid_to;
    const refused = dispatch({ type: "claim/upsert", claim, at, auditId: newId("audit") });
    if (refused) {
      setNotice(refused);
      setDraft(null);
      setDraftError(null);
      return;
    }
    announce(existing ? "Claim updated." : "Claim added.");
    if (textChanged && existing.status === "supported") setNotice("The wording changed, so the claim is now unverified and its evidence references were removed: they were about the old wording.");
    setDraft(null);
    setDraftError(null);
  };

  return (
    <Page
      title="Profile Evidence"
      lead="Every statement about the candidate is a claim with a truth state and pointers to evidence. Résumé text can only be built from these claims."
      actions={
        <Button variant="primary" onClick={() => { setDraft(emptyDraft); setDraftError(null); }} data-testid="add-claim">
          Add a claim
        </Button>
      }
    >
      <div className="ct-grid ct-grid--stats" data-testid="truth-summary">
        {CLAIM_STATUSES.map((s) => (
          <Card as="div" key={s} className="app-stat">
            <TruthBadge status={s} />
            <span className="app-stat__value">{counts[s]}</span>
          </Card>
        ))}
      </div>

      <Banner tone="neutral" title="What the truth states mean">
        <ul className="ct-small">
          <li><strong>Supported by evidence</strong>: at least one evidence item backs the claim. May be used freely. Only evidence can put a claim in this state; it cannot be chosen by hand.</li>
          <li><strong>Self-reported</strong>: you assert it; no evidence is attached. May be used, and is labelled as self-reported.</li>
          <li><strong>Unverified</strong>: not yet asserted or checked. Blocked from résumé export.</li>
          <li><strong>Contradicted by evidence</strong>: evidence disagrees with the claim. Blocked from résumé export.</li>
        </ul>
      </Banner>

      {notice ? (
        <Banner tone="warning" role="alert" title="Not changed" actions={<Button size="sm" onClick={() => setNotice(null)}>Dismiss</Button>} data-testid="evidence-notice">
          {notice}
        </Banner>
      ) : null}

      <Card title="Claims">
        <div className="app-toolbar">
          <SelectField label="Truth state" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} options={[{ value: "all", label: "All states" }, ...CLAIM_STATUSES.map((s) => ({ value: s, label: CLAIM_LABEL[s] }))]} data-testid="claim-status-filter" />
          <SelectField label="Claim type" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} options={[{ value: "all", label: "All types" }, ...CLAIM_TYPES.map((t) => ({ value: t, label: humanise(t) }))]} />
        </div>
        <p role="status" data-testid="claim-count">
          Showing {visible.length} of {workspace.claims.length} claims
        </p>
        {workspace.claims.length === 0 ? (
          <EmptyState title="The evidence vault is empty." action={<Button onClick={() => setDraft(emptyDraft)}>Add a claim</Button>}>
            <p>Add a claim to start. Claims you add are self-reported or unverified until evidence exists.</p>
          </EmptyState>
        ) : visible.length === 0 ? (
          <EmptyState title="No claims match these filters." />
        ) : (
          <ul className="ct-list-plain ct-stack" data-testid="claim-list">
            {visible.map((c) => {
              const expired = Boolean(c.valid_to && Date.parse(c.valid_to) < Date.parse(clock));
              return (
                <Card as="li" key={c.id} headingLevel={3} title={c.claim_text} data-testid={`claim-${c.id}`}>
                  <div className="ct-row">
                    <TruthBadge status={c.status} />
                    <Badge>{humanise(c.claim_type)}</Badge>
                    <Badge tone={c.visibility === "private" ? "warning" : "neutral"}>{humanise(c.visibility)}</Badge>
                    {expired ? <Badge tone="danger">Expired {formatDate(c.valid_to)}</Badge> : null}
                    {(usage.get(c.id) ?? 0) > 0 ? <Badge tone="info">Used in {usage.get(c.id)} résumé line{usage.get(c.id) === 1 ? "" : "s"}</Badge> : null}
                  </div>
                  <Facts
                    items={[
                      {
                        label: "Evidence",
                        value:
                          c.evidence_ids.length === 0 ? (
                            <span className="ct-muted">No evidence attached</span>
                          ) : (
                            <ul className="ct-list-plain">
                              {c.evidence_ids.map((id) => {
                                const ev = evidenceById.get(id);
                                return (
                                  <li key={id}>
                                    {ev ? (
                                      <>
                                        {ev.label} <span className="ct-muted ct-small">({ev.kind}, captured {formatDate(ev.captured_at)}) — {ev.note}</span>
                                      </>
                                    ) : (
                                      <span>Reference <code>{id}</code> does not resolve to an evidence item.</span>
                                    )}
                                  </li>
                                );
                              })}
                            </ul>
                          ),
                      },
                      { label: "Skills", value: <SkillChips skills={c.skills ?? []} /> },
                      { label: "Updated", value: formatDateTime(c.updated_at) },
                    ]}
                  />
                  <div className="ct-row">
                    <div className="app-inline-select">
                      <SelectField label="Truth state" value={c.status} onChange={(e) => setStatus(c, e.target.value as ClaimStatus)} options={CLAIM_STATUSES.filter((s) => s === c.status || USER_SETTABLE_STATUSES.includes(s)).map((s) => ({ value: s, label: CLAIM_LABEL[s] }))} disabled={c.status === "contradicted"} hint={c.status === "contradicted" ? "Set by evidence; cannot be changed." : c.status === "supported" ? "Set by evidence. Downgrading cannot be undone here." : undefined} data-testid={`claim-status-${c.id}`} />
                    </div>
                    <Button size="sm" onClick={() => { setDraft({ id: c.id, claim_type: c.claim_type, claim_text: c.claim_text, skills: (c.skills ?? []).join(", "), visibility: c.visibility, status: c.status }); setDraftError(null); }} data-testid={`claim-edit-${c.id}`}>
                      Edit
                    </Button>
                    <Button size="sm" variant="danger" onClick={() => setRemoving(c)}>
                      Remove
                    </Button>
                  </div>
                </Card>
              );
            })}
          </ul>
        )}
      </Card>

      <Card title="Importing a profile">
        <p>
          <Badge tone="neutral">Unavailable in this build</Badge> LinkedIn is not connected. CareerTwin never scrapes LinkedIn or signs in
          on your behalf. A future private build may import a data export that you download and choose yourself.
        </p>
        <div className="ct-row">
          <Button disabled aria-disabled="true">Import a LinkedIn export (unavailable)</Button>
          <Button disabled aria-disabled="true">Attach evidence file (unavailable)</Button>
        </div>
        <p className="ct-small ct-muted">Evidence upload is disabled because this repository is public and only synthetic data may exist in it.</p>
      </Card>

      <Card title="Change history">
        {workspace.audit.length === 0 ? (
          <p className="ct-muted">No changes have been made to the vault in this browser.</p>
        ) : (
          <TableRegion label="Evidence vault change history">
            <table className="ct-table" data-testid="audit-table">
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Action</th>
                  <th scope="col">Detail</th>
                </tr>
              </thead>
              <tbody>
                {[...workspace.audit].reverse().map((a) => (
                  <tr key={a.id}>
                    <td>{formatDateTime(a.at)}</td>
                    <td>{a.action}</td>
                    <td>{a.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableRegion>
        )}
      </Card>

      <Dialog
        open={draft !== null}
        onClose={() => setDraft(null)}
        title={draft?.id ? "Edit claim" : "Add a claim"}
        testId="claim-dialog"
        actions={
          <>
            <Button onClick={() => setDraft(null)}>Cancel</Button>
            <Button variant="primary" onClick={saveDraft} data-testid="claim-save">
              Save claim
            </Button>
          </>
        }
      >
        {draft ? (
          <>
            <Banner tone="synthetic" title="Synthetic data only.">Do not enter real personal details. This repository is public.</Banner>
            <TextArea label="Claim" value={draft.claim_text} onChange={(e) => setDraft({ ...draft, claim_text: e.target.value })} error={draftError} maxLength={400} data-testid="claim-text" />
            <SelectField label="Type" value={draft.claim_type} onChange={(e) => setDraft({ ...draft, claim_type: e.target.value as ClaimType })} options={CLAIM_TYPES.map((t) => ({ value: t, label: humanise(t) }))} />
            <TextField label="Skills (comma separated)" value={draft.skills} onChange={(e) => setDraft({ ...draft, skills: e.target.value })} />
            <SelectField label="Visibility" value={draft.visibility} onChange={(e) => setDraft({ ...draft, visibility: e.target.value as ClaimVisibility })} options={CLAIM_VISIBILITIES.map((v) => ({ value: v, label: humanise(v) }))} hint="Private claims are never exported." />
            {draft.id === null ? (
              <SelectField label="Truth state" value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value as ClaimStatus })} options={[{ value: "self-reported", label: CLAIM_LABEL["self-reported"] }, { value: "unverified", label: CLAIM_LABEL.unverified }]} hint="A new claim cannot start as supported: no evidence is attached to it." />
            ) : null}
          </>
        ) : null}
      </Dialog>

      <ConfirmDialog
        open={removing !== null}
        title="Remove this claim?"
        confirmLabel="Remove claim"
        danger
        testId="claim-remove"
        onClose={() => setRemoving(null)}
        onConfirm={() => {
          if (removing) {
            dispatch({ type: "claim/remove", id: removing.id, at: new Date().toISOString(), auditId: newId("audit") });
            announce("Claim removed.");
          }
          setRemoving(null);
        }}
      >
        <p>“{removing?.claim_text}”</p>
        {removing && (usage.get(removing.id) ?? 0) > 0 ? (
          <Banner tone="warning" title="This claim is used in a résumé variant.">Lines that link to it will be blocked from export until you fix them.</Banner>
        ) : null}
      </ConfirmDialog>
    </Page>
  );
}
