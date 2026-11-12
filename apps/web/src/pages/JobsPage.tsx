import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Banner, Button, Checkbox, EmptyState, Facts, ScorePair, SelectField, TableRegion, TextField, useAnnounce, type Tone } from "@careertwin/ui";
import { ExternalLinkDialog, Page, SkillChips } from "@/components/common";
import type { JobPosting } from "@/contracts/types";
import { duplicateKey, formatCompensation, formatDate, freshness, humanise, type Freshness } from "@/lib/format";
import { newId } from "@/lib/safety";
import { useApp } from "@/state/AppContext";
import { blendedScore, useIndexes, userEvent } from "@/state/selectors";

const STATUS_TEXT: Record<JobPosting["status"], { label: string; tone: Tone }> = {
  active: { label: "Marked active at capture", tone: "info" },
  expired: { label: "Expired at capture", tone: "danger" },
  unknown: { label: "Status unknown", tone: "warning" },
};
const FRESH_TONE: Record<Freshness, Tone> = { fresh: "success", aging: "warning", stale: "danger" };
const FRESH_LABEL: Record<Freshness, string> = { fresh: "Fresh (7 days or less)", aging: "Aging (8–21 days)", stale: "Stale (over 21 days)" };

type SortKey = "captured" | "title" | "organization" | "near" | "strategic" | "blend";
const SORTS: { value: SortKey; label: string }[] = [
  { value: "captured", label: "Most recently captured" },
  { value: "blend", label: "Your blended ranking" },
  { value: "near", label: "Near-term fit" },
  { value: "strategic", label: "Strategic value" },
  { value: "title", label: "Title (A–Z)" },
  { value: "organization", label: "Employer (A–Z)" },
];

export default function JobsPage() {
  const { bundle, workspace, clock, dispatch } = useApp();
  const { matchByJob, appByJob, familyLabel, families } = useIndexes();
  const announce = useAnnounce();
  const [query, setQuery] = useState("");
  const [family, setFamily] = useState("all");
  const [remote, setRemote] = useState("all");
  const [status, setStatus] = useState("all");
  const [fresh, setFresh] = useState("all");
  const [shortOnly, setShortOnly] = useState(false);
  const [collapse, setCollapse] = useState(false);
  const [sort, setSort] = useState<SortKey>("captured");
  const [view, setView] = useState<"cards" | "table">("cards");
  const [linkJob, setLinkJob] = useState<JobPosting | null>(null);

  const duplicates = useMemo(() => {
    const groups = new Map<string, JobPosting[]>();
    for (const j of bundle.jobs) {
      const f = bundle.jobFacets[j.id];
      const k = Object.keys(bundle.jobFacets).length > 0 ? f?.duplicate_cluster ?? `single:${j.id}` : duplicateKey(j);
      groups.set(k, [...(groups.get(k) ?? []), j]);
    }
    return groups;
  }, [bundle.jobs, bundle.jobFacets]);

  // When lane A's envelope is available, duplicates come from its dedup clusters, not from a title heuristic.
  const facets = bundle.jobFacets;
  const hasFacets = Object.keys(facets).length > 0;
  const groupKey = (j: JobPosting) => (hasFacets ? facets[j.id]?.duplicate_cluster ?? `single:${j.id}` : duplicateKey(j));

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    let list = bundle.jobs.filter((j) => {
      if (family !== "all" && j.role_family !== family) return false;
      if (remote !== "all" && (j.remote_policy ?? "unknown") !== remote) return false;
      if (status !== "all" && j.status !== status) return false;
      if (fresh !== "all" && freshness(j.fetched_at, clock).level !== fresh) return false;
      if (shortOnly && !workspace.shortlist.includes(j.id)) return false;
      if (q) {
        const hay = [j.title, j.organization, j.role_family ?? "", j.location ?? "", ...j.skills, ...(j.requirements ?? [])].join(" ").toLowerCase();
        if (!q.split(/\s+/).every((term) => hay.includes(term))) return false;
      }
      return true;
    });
    if (collapse) {
      // Keep the most recently captured posting of each likely-duplicate group.
      const keep = new Map<string, JobPosting>();
      for (const j of list) {
        const k = groupKey(j);
        const cur = keep.get(k);
        if (!cur || j.fetched_at > cur.fetched_at) keep.set(k, j);
      }
      list = list.filter((j) => keep.get(groupKey(j)) === j);
    }
    const score = (j: JobPosting, kind: SortKey): number => {
      const m = matchByJob.get(j.id);
      if (!m) return -1;
      const v = kind === "near" ? m.near_term_fit.score : kind === "strategic" ? m.strategic_value.score : blendedScore(m, workspace.strategicBlend);
      return v ?? -1;
    };
    return [...list].sort((a, b) => {
      if (sort === "title") return a.title.localeCompare(b.title) || a.id.localeCompare(b.id);
      if (sort === "organization") return a.organization.localeCompare(b.organization) || a.id.localeCompare(b.id);
      if (sort === "captured") return b.fetched_at.localeCompare(a.fetched_at) || a.id.localeCompare(b.id);
      return score(b, sort) - score(a, sort) || a.id.localeCompare(b.id);
    });
  }, [bundle.jobs, bundle.jobFacets, query, family, remote, status, fresh, shortOnly, collapse, sort, clock, workspace.shortlist, workspace.strategicBlend, matchByJob]);

  const toggleShortlist = (j: JobPosting) => {
    const was = workspace.shortlist.includes(j.id);
    dispatch({ type: "shortlist/toggle", jobId: j.id });
    announce(was ? "Removed from shortlist." : "Added to shortlist.");
  };
  const track = (j: JobPosting) => {
    const refused = dispatch({ type: "event/append", event: userEvent({ application_id: newId("app"), job_id: j.id, event_type: "saved", notes: "Saved from Discover Jobs." }) });
    if (!refused) announce("Saved to Applications. Nothing was sent to the employer.");
  };
  const reset = () => {
    setQuery("");
    setFamily("all");
    setRemote("all");
    setStatus("all");
    setFresh("all");
    setShortOnly(false);
    setCollapse(false);
  };

  const jobsReport = bundle.reports.find((r) => r.adapter.id === "jobs");
  const rejected = jobsReport?.rejected ?? [];

  const actions = (j: JobPosting) => {
    const shortlisted = workspace.shortlist.includes(j.id);
    const app = appByJob.get(j.id);
    return (
      <div className="jb-actions">
        <Link className="ct-btn ct-btn--sm ct-btn--secondary" to={`/matching?job=${encodeURIComponent(j.id)}`}>
          Explain match
        </Link>
        <Button size="sm" aria-pressed={shortlisted} onClick={() => toggleShortlist(j)} data-testid={`shortlist-${j.id}`}>
          {shortlisted ? "Shortlisted" : "Shortlist"}
        </Button>
        {app ? (
          <Link className="ct-btn ct-btn--sm ct-btn--secondary" to={`/applications?app=${encodeURIComponent(app.id)}`}>
            Tracked
          </Link>
        ) : (
          <Button size="sm" onClick={() => track(j)} data-testid={`track-${j.id}`}>
            Track application
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={() => setLinkJob(j)} data-testid={`open-${j.id}`}>
          Posting link…
        </Button>
      </div>
    );
  };

  return (
    <Page
      title="Discover Jobs"
      lead="A fictional job feed. Each posting shows where it came from, when it was captured, and what is unknown. No posting here is asserted to be open today."
    >
      <section className="jb-filters" aria-label="Search and filter">
        <div className="jb-filters__row">
          <div className="jb-search">
            <TextField label="Search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Title, employer, skill…" data-testid="job-search" />
          </div>
          <SelectField label="Role family" value={family} onChange={(e) => setFamily(e.target.value)} options={[{ value: "all", label: "All role families" }, ...families.map((f) => ({ value: f, label: familyLabel(f) }))]} data-testid="job-family" />
          <SelectField label="Work style" value={remote} onChange={(e) => setRemote(e.target.value)} options={[{ value: "all", label: "Any" }, { value: "remote", label: "Remote" }, { value: "hybrid", label: "Hybrid" }, { value: "onsite", label: "On-site" }, { value: "unknown", label: "Unknown" }]} />
          <SelectField label="Status at capture" value={status} onChange={(e) => setStatus(e.target.value)} options={[{ value: "all", label: "Any" }, { value: "active", label: "Marked active" }, { value: "expired", label: "Expired" }, { value: "unknown", label: "Unknown" }]} />
          <SelectField label="Freshness" value={fresh} onChange={(e) => setFresh(e.target.value)} options={[{ value: "all", label: "Any" }, ...(["fresh", "aging", "stale"] as const).map((f) => ({ value: f, label: FRESH_LABEL[f] }))]} />
          <SelectField label="Sort by" value={sort} onChange={(e) => setSort(e.target.value as SortKey)} options={SORTS} data-testid="job-sort" />
        </div>
        <div className="jb-filters__row jb-filters__row--meta">
          <div className="ct-row jb-checks">
            <Checkbox label="Shortlisted only" checked={shortOnly} onChange={(e) => setShortOnly(e.target.checked)} />
            <Checkbox label="Collapse likely duplicates" hint="Same employer and title; keeps the most recent capture." checked={collapse} onChange={(e) => setCollapse(e.target.checked)} data-testid="job-collapse" />
          </div>
          <div className="ct-row jb-view">
            <p role="status" data-testid="job-count" className="jb-count">
              Showing {visible.length} of {bundle.jobs.length} postings
            </p>
            <div className="jb-seg" role="group" aria-label="View">
              <Button size="sm" aria-pressed={view === "cards"} onClick={() => setView("cards")}>
                List
              </Button>
              <Button size="sm" aria-pressed={view === "table"} onClick={() => setView("table")} data-testid="job-view-table">
                Table
              </Button>
            </div>
            <Button size="sm" variant="ghost" onClick={reset}>
              Clear filters
            </Button>
          </div>
        </div>
      </section>

      {rejected.length > 0 ? (
        <Banner tone="warning" title={`${rejected.length} feed record${rejected.length === 1 ? "" : "s"} did not match the JobPosting contract and are not shown.`} data-testid="jobs-rejected">
          <ul className="ct-small">
            {rejected.map((r) => (
              <li key={r.index}>
                Record #{r.index + 1}
                {r.id ? ` (${r.id})` : ""}: {r.issues.slice(0, 3).map((i) => `${i.path} — ${i.message}`).join("; ")}
                {r.issues.length > 3 ? `; and ${r.issues.length - 3} more` : ""}
              </li>
            ))}
          </ul>
        </Banner>
      ) : null}

      {bundle.jobs.length === 0 ? (
        <EmptyState title="The job feed is empty.">
          <p>No postings were provided by the data source. Live connectors are not available in this build.</p>
        </EmptyState>
      ) : visible.length === 0 ? (
        <EmptyState title="No postings match these filters." action={<Button onClick={reset}>Clear filters</Button>} />
      ) : view === "table" ? (
        <TableRegion label="Job postings table">
          <table className="ct-table" data-testid="jobs-table">
            <thead>
              <tr>
                <th scope="col">Posting</th>
                <th scope="col">Role family</th>
                <th scope="col">Status at capture</th>
                <th scope="col">Captured</th>
                <th scope="col">Work style</th>
                <th scope="col">Compensation</th>
                <th scope="col" className="ct-num">Near-term</th>
                <th scope="col" className="ct-num">Strategic</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((j) => {
                const m = matchByJob.get(j.id);
                return (
                  <tr key={j.id}>
                    <th scope="row">
                      {j.title}
                      <div className="ct-muted ct-small">{j.organization}</div>
                    </th>
                    <td>{familyLabel(j.role_family)}</td>
                    <td>{STATUS_TEXT[j.status].label}</td>
                    <td>{formatDate(j.fetched_at)}</td>
                    <td>{humanise(j.remote_policy ?? "unknown")}</td>
                    <td>{formatCompensation(j.compensation).text}</td>
                    <td className="ct-num">{m?.near_term_fit.score ?? "Unknown"}</td>
                    <td className="ct-num">{m?.strategic_value.score ?? "Unknown"}</td>
                    <td>{actions(j)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableRegion>
      ) : (
        <>
        <h2 className="ct-sr-only">Postings</h2>
        <ul className="ct-list-plain jb-list" data-testid="jobs-cards">
          {visible.map((j) => {
            const f = freshness(j.fetched_at, clock);
            const comp = formatCompensation(j.compensation);
            const group = duplicates.get(groupKey(j)) ?? [];
            const facet = facets[j.id];
            const m = matchByJob.get(j.id);
            const synthetic = j.source === "synthetic" || facet?.synthetic === true;
            const failed = m ? m.hard_constraints.filter((h) => h.status === "fail") : [];
            const shortlisted = workspace.shortlist.includes(j.id);
            return (
              <li key={j.id} className={shortlisted ? "jb-row jb-row--short" : "jb-row"} data-testid={`job-${j.id}`}>
                <div className="jb-main">
                  <h3 className="jb-title">{j.title}</h3>
                  <p className="jb-org">
                    <span>{j.organization}</span>
                    <span>{familyLabel(j.role_family)}</span>
                    <span>{j.remote_policy && j.remote_policy !== "unknown" ? humanise(j.remote_policy) : "Work style unknown"}</span>
                    <span>{j.location ?? "Location not stated"}</span>
                    <span>{`${humanise(j.seniority)}, ${humanise(j.employment_type)}`}</span>
                  </p>
                  <div className="jb-tags">
                    {synthetic ? null : <Badge tone="warning">Provenance not verified</Badge>}
                    {(facet?.security_flags ?? []).map((flag) => (
                      <Badge key={flag} tone="danger" title="Flagged by the data lane during ingestion">
                        {humanise(flag)}
                      </Badge>
                    ))}
                    {failed.map((h) => (
                      <Badge key={h.key} tone="danger">
                        Fails {h.label.toLowerCase()}
                      </Badge>
                    ))}
                    {group.length > 1 ? (
                      <Badge tone="warning" title={group.map((g) => g.id).join(", ")}>
                        {hasFacets ? "Duplicate cluster" : "Possible duplicate"} ({group.length} postings)
                      </Badge>
                    ) : null}
                    <span className={`jb-state jb-tone--${STATUS_TEXT[j.status].tone}`}>{STATUS_TEXT[j.status].label}</span>
                    <span className={`jb-state jb-tone--${FRESH_TONE[f.level]}`}>{f.label}</span>
                  </div>
                </div>
                <div className="jb-scores">
                  {m ? (
                    m.near_term_fit.score === null && m.strategic_value.score === null && m.overall ? (
                      <span className="ct-small">Single score {m.overall.score}</span>
                    ) : (
                      <ScorePair near={m.near_term_fit.score} strategic={m.strategic_value.score} />
                    )
                  ) : (
                    <span className="ct-small ct-muted">No match data</span>
                  )}
                </div>
                <div className="jb-facts">
                  {comp.known ? (
                    <span className="jb-pay">{comp.text}</span>
                  ) : (
                    <span className="jb-pay jb-pay--unknown" data-testid={`comp-${j.id}`}>
                      {comp.text} (unknown — not treated as zero)
                    </span>
                  )}
                  <span className="ct-small ct-muted">Captured {formatDate(j.fetched_at)}</span>
                </div>
                <div className="jb-foot">
                  {actions(j)}
                  <div className="jb-more">
                    {j.requirements && j.requirements.length > 0 ? (
                      <details>
                        <summary>Requirements text ({j.requirements.length})</summary>
                        <ul className="ct-small" data-testid={`req-${j.id}`}>
                          {j.requirements.map((r, i) => (
                            <li key={i}>{r}</li>
                          ))}
                        </ul>
                      </details>
                    ) : null}
                    <details>
                      <summary>Skills and source</summary>
                      <div className="ct-stack">
                        <SkillChips skills={j.skills} />
                        <Facts
                          items={[
                            { label: "Source", value: `${j.source} · id ${j.source_id}${synthetic ? " (synthetic)" : ""}` },
                            { label: "Captured", value: `${formatDate(j.fetched_at)} (${f.days} day${f.days === 1 ? "" : "s"} before the snapshot)` },
                            { label: "Licence note", value: j.license_note ?? "None" },
                            { label: "Evidence", value: (j.source_evidence ?? []).join(" ") || "None recorded" },
                          ]}
                        />
                      </div>
                    </details>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
        </>
      )}
      <ExternalLinkDialog job={linkJob} onClose={() => setLinkJob(null)} />
    </Page>
  );
}
