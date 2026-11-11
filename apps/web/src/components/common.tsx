import { useEffect, useRef, type ReactNode } from "react";
import { Badge, Banner, Button, Dialog, type Tone } from "@careertwin/ui";
import type { EvidenceStrength } from "@/adapters/proposed";
import type { ClaimStatus, JobPosting } from "@/contracts/types";
import { classifyLink } from "@/lib/safety";
import { humanise } from "@/lib/format";

/**
 * True until the first screen has mounted. On the initial page load focus is left
 * alone, so the first Tab reaches the skip link; on later navigations focus moves
 * to the new heading so keyboard and screen-reader users land on the new content.
 */
let firstScreen = true;

/** Page frame: sets the document title and moves focus to the heading on navigation. */
export function Page({ title, lead, actions, children }: { title: string; lead?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    document.title = `${title} — CareerTwin OS (synthetic demo)`;
    if (firstScreen) {
      firstScreen = false;
      return;
    }
    ref.current?.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }, [title]);
  return (
    <div className="ct-stack ct-stack--lg">
      <header className="app-pagehead">
        <div className="ct-stack">
          <h1 ref={ref} tabIndex={-1}>
            {title}
          </h1>
          {lead ? <p className="ct-muted app-lead">{lead}</p> : null}
        </div>
        {actions ? <div className="ct-row">{actions}</div> : null}
      </header>
      {children}
    </div>
  );
}

export const CLAIM_TONE: Record<ClaimStatus | "missing", Tone> = {
  supported: "success",
  "self-reported": "info",
  unverified: "warning",
  contradicted: "danger",
  missing: "neutral",
};
export const CLAIM_LABEL: Record<ClaimStatus | "missing", string> = {
  supported: "Supported by evidence",
  "self-reported": "Self-reported",
  unverified: "Unverified",
  contradicted: "Contradicted by evidence",
  missing: "No claim",
};

export function TruthBadge({ status }: { status: ClaimStatus | EvidenceStrength }) {
  return <Badge tone={CLAIM_TONE[status]}>{CLAIM_LABEL[status]}</Badge>;
}

export function SyntheticBadge() {
  return (
    <Badge tone="synthetic" title="Fictional record authored for tests">
      Synthetic
    </Badge>
  );
}

export function SkillChips({ skills }: { skills: string[] }) {
  if (skills.length === 0) return <span className="ct-muted">None listed</span>;
  return (
    <ul className="ct-list-plain ct-row" aria-label="Skills">
      {skills.map((s) => (
        <li key={s}>
          <Badge>{humanise(s)}</Badge>
        </li>
      ))}
    </ul>
  );
}

/**
 * The only way a posting link can be opened. Shows the full destination as text,
 * refuses unsafe schemes outright, and never navigates on its own.
 */
export function ExternalLinkDialog({ job, onClose }: { job: JobPosting | null; onClose: () => void }) {
  const verdict = job ? classifyLink(job.url) : null;
  return (
    <Dialog
      open={job !== null}
      onClose={onClose}
      title="Open the posting in a new tab?"
      testId="link-dialog"
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          {verdict?.kind === "ok" ? (
            <a className="ct-btn ct-btn--primary" href={verdict.href} target="_blank" rel="noopener noreferrer nofollow" referrerPolicy="no-referrer" onClick={onClose} data-testid="link-open">
              Open in new tab
            </a>
          ) : null}
        </>
      }
    >
      {job && verdict ? (
        <>
          {job.source === "synthetic" ? (
            <Banner tone="synthetic" title="This posting is synthetic.">
              The link was authored for tests and is not expected to lead to a real job.
            </Banner>
          ) : (
            <Banner tone="warning" title="The provenance of this posting is not verified." data-testid="link-unverified">
              Its source is recorded as “{job.source}”. This build is meant for synthetic data only; check the destination below before you open it.
            </Banner>
          )}
          {verdict.kind === "ok" ? (
            <>
              <p>You are about to leave CareerTwin and open:</p>
              <p className="ct-mono app-url" data-testid="link-destination">
                {verdict.href}
              </p>
              {verdict.notes.length > 0 ? (
                <ul>
                  {verdict.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              ) : null}
              <p>
                CareerTwin never submits applications or contacts anyone. Anything you do on the external site is your own manual
                action; come back and record it yourself if you want it tracked.
              </p>
            </>
          ) : (
            <Banner tone="danger" role="alert" title="This link is blocked." data-testid="link-blocked">
              {verdict.reason} The raw value is shown below as text only.
              <p className="ct-mono app-url">{job.url}</p>
            </Banner>
          )}
        </>
      ) : null}
    </Dialog>
  );
}

/** Confirmation dialog for destructive or commitment actions. */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  onConfirm,
  onClose,
  danger,
  confirmDisabled,
  testId,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onClose: () => void;
  danger?: boolean;
  confirmDisabled?: boolean;
  testId?: string;
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      tone={danger ? "danger" : undefined}
      testId={testId}
      actions={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} disabled={confirmDisabled} data-testid={testId ? `${testId}-confirm` : undefined}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Dialog>
  );
}
