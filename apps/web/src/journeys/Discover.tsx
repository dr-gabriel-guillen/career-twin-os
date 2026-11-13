import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { Button, Checkbox, EmptyState, Icon, ScorePair, SelectField, useAnnounce } from "@careertwin/ui";
import { ExternalLinkDialog } from "@/components/common";
import type { JobPosting } from "@/contracts/types";
import { duplicateKey, formatCompensation, formatDate, freshness, humanise, skillLabel } from "@/lib/format";
import { newId } from "@/lib/safety";
import { useApp } from "@/state/AppContext";
import { useIndexes, userEvent } from "@/state/selectors";
import { blockersFor, improvementsFor, rankByNearTerm, reasonsFor, rowReason, standingOf, STANDING_LABEL, type Reason } from "./lib";
import { Page as Screen } from "@/components/common";

type View = "best" | "passes" | "strategic" | "shortlist" | "all";
const VIEWS: { id: View; label: string }[] = [
  { id: "best", label: "Best matches" },
  { id: "passes", label: "Passes my constraints" },
  { id: "strategic", label: "Most strategic" },
  { id: "shortlist", label: "Shortlisted" },
  { id: "all", label: "All postings" },
];
const PAGE = 40;

function ReasonList({ items, empty, testId }: { items: Reason[]; empty: string; testId: string }) {
  if (items.length === 0) return <p className="pt-muted">{empty}</p>;
  return (
    <ul className="ct-list-plain pt-reasons" data-testid={testId}>
      {items.map((r, i) => (
        <li key={`${i}-${r.text}`} className={`pt-reason pt-reason--${r.tone}`}>
          <span className="pt-reason__mark" aria-hidden="true" />
          <span>
            <strong>{r.text}</strong>
            {r.detail ? <span className="pt-reason__detail">{r.detail}</span> : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function DiscoverJourney() {
  const { bundle, workspace, clock, dispatch } = useApp();
  const { matchByJob, applications, familyLabel, families, matchesStale } = useIndexes();
  const { jobId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const announce = useAnnounce();
  const [view, setView] = useState<View>(bundle.matches.length > 0 ? "best" : "all");
  const [query, setQuery] = useState("");
  const [family, setFamily] = useState("all");
  const [remote, setRemote] = useState("all");
  const [fresh, setFresh] = useState("all");
  const [collapse, setCollapse] = useState(false);
  const activeFilters = [family !== "all", remote !== "all", fresh !== "all", collapse].filter(Boolean).length;
  const [limit, setLimit] = useState(PAGE);
  const [linkJob, setLinkJob] = useState<JobPosting | null>(null);
  const detailRef = useRef<HTMLElement>(null);
  const lastPicked = useRef<string | null>(null);

  const mine = applications;
  const myAppByJob = new Map(mine.map((a) => [a.job_id, a]));
  const { rankOf, scored } = useMemo(() => rankByNearTerm(bundle.matches), [bundle.matches]);

  const list = useMemo(() => {
    const q = query.trim().toLowerCase();
    let items = bundle.jobs.filter((j) => {
      if (family !== "all" && j.role_family !== family) return false;
      if (remote !== "all" && (j.remote_policy ?? "unknown") !== remote) return false;
      if (fresh !== "all" && freshness(j.fetched_at, clock).level !== fresh) return false;
      return !q || q.split(/\s+/).every((t) => [j.title, j.organization, j.location ?? "", j.role_family ?? "", ...j.skills].join(" ").toLowerCase().includes(t));
    });
    if (collapse) {
      // Keep the most recently captured posting of each likely-duplicate group (lane A's clusters when present).
      const key = (j: JobPosting) => bundle.jobFacets[j.id]?.duplicate_cluster ?? (Object.keys(bundle.jobFacets).length > 0 ? `single:${j.id}` : duplicateKey(j));
      const keep = new Map<string, JobPosting>();
      for (const j of items) {
        const cur = keep.get(key(j));
        if (!cur || j.fetched_at > cur.fetched_at) keep.set(key(j), j);
      }
      items = items.filter((j) => keep.get(key(j)) === j);
    }
    const m = (j: JobPosting) => matchByJob.get(j.id);
    const failed = (j: JobPosting) => m(j)?.hard_constraints.some((h) => h.status === "fail") ?? false;
    if (view === "best") items = items.filter((j) => { const st = standingOf(m(j), rankOf, scored); return st === "closest" || st === "close"; });
    if (view === "passes") items = items.filter((j) => m(j) && !failed(j));
    if (view === "strategic") items = items.filter((j) => m(j)?.strategic_value.score !== null && m(j) !== undefined);
    if (view === "shortlist") items = items.filter((j) => workspace.shortlist.includes(j.id));
    const near = (j: JobPosting) => m(j)?.near_term_fit.score ?? -1;
    const strat = (j: JobPosting) => m(j)?.strategic_value.score ?? -1;
    return [...items].sort((a, b) => (view === "strategic" ? strat(b) - strat(a) : near(b) - near(a)) || a.title.localeCompare(b.title));
  }, [bundle.jobs, bundle.jobFacets, matchByJob, view, query, family, remote, fresh, collapse, clock, workspace.shortlist, rankOf, scored]);

  useEffect(() => setLimit(PAGE), [view, query, family, remote, fresh, collapse]);

  const selectedJob = (jobId ? bundle.jobs.find((j) => j.id === jobId) : null) ?? null;
  const shown = selectedJob ?? list[0] ?? null;
  const match = shown ? matchByJob.get(shown.id) ?? null : null;

  // Phones: list and detail are separate views; choosing a job focuses its detail, Back restores the row.
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia("(max-width: 1000px)").matches) return;
    if (jobId) {
      window.scrollTo(0, 0);
      if (lastPicked.current === jobId) detailRef.current?.focus({ preventScroll: true });
    } else if (lastPicked.current) {
      const row = document.querySelector<HTMLElement>(`[data-testid="pt-job-${CSS.escape(lastPicked.current)}"]`);
      row?.scrollIntoView({ block: "center" });
      row?.focus({ preventScroll: true });
    }
  }, [jobId]);

  const shortlisted = shown ? workspace.shortlist.includes(shown.id) : false;
  const toggleShortlist = (j: JobPosting) => {
    const was = workspace.shortlist.includes(j.id);
    dispatch({ type: "shortlist/toggle", jobId: j.id });
    announce(was ? `Removed ${j.title} from your shortlist.` : `Shortlisted ${j.title}.`);
  };
  const saveToTracker = (j: JobPosting) => {
    const appId = newId("app");
    const refused = dispatch({ type: "event/append", event: userEvent({ application_id: appId, job_id: j.id, event_type: "saved", notes: "Saved from Discover." }) });
    if (refused) return announce(refused);
    announce(`Saved ${j.title} to your tracker. Nothing was sent to the employer.`);
    navigate(`/applications/${encodeURIComponent(appId)}`);
  };
  const back = () => ((location.state as { fromList?: boolean } | null)?.fromList ? navigate(-1) : navigate("/jobs"));

  return (
    <Screen
      title="Discover"
      lead={
        bundle.matches.length > 0
          ? `${bundle.jobs.length} fictional postings, ${scored} ranked by the engine against your evidence. Pick one to see why it could fit, what stands in the way, and what to do next.`
          : `${bundle.jobs.length} fictional postings. The matching engine has not ranked them, so fit cannot be explained yet.`
      }
    >
      <div className="pt-discover" data-view={jobId ? "detail" : "list"}>
        <section className="pt-list" aria-label="Postings">
          <div className="pt-list__tools">
            <label className="pt-search">
              <Icon name="discover" size={16} />
              <span className="ct-sr-only">Search postings</span>
              <input className="ct-input" type="search" placeholder="Search title, employer, skill…" value={query} onChange={(e) => setQuery(e.target.value)} data-testid="pt-search" />
            </label>
            <div className="pt-chips" role="group" aria-label="Views">
              {VIEWS.map((v) => (
                <button key={v.id} type="button" className="pt-chip" aria-pressed={view === v.id} onClick={() => setView(v.id)}>
                  {v.label}
                  {v.id === "shortlist" && workspace.shortlist.length > 0 ? <span className="pt-chip__n">{workspace.shortlist.length}</span> : null}
                </button>
              ))}
            </div>
            <details className="pt-filters" data-testid="filters">
              <summary>Filters{activeFilters > 0 ? ` (${activeFilters} on)` : ""}</summary>
              <div className="pt-filters__grid">
                <SelectField label="Role family" value={family} onChange={(e) => setFamily(e.target.value)} options={[{ value: "all", label: "All role families" }, ...families.map((f) => ({ value: f, label: familyLabel(f) }))]} data-testid="filter-family" />
                <SelectField label="Work style" value={remote} onChange={(e) => setRemote(e.target.value)} options={[{ value: "all", label: "Any work style" }, ...["remote", "hybrid", "onsite", "unknown"].map((r) => ({ value: r, label: r === "unknown" ? "Not stated" : humanise(r) }))]} data-testid="filter-remote" />
                <SelectField label="Captured" value={fresh} onChange={(e) => setFresh(e.target.value)} options={[{ value: "all", label: "Any time" }, { value: "fresh", label: "Within 7 days" }, { value: "aging", label: "8 to 21 days ago" }, { value: "stale", label: "Over 21 days ago" }]} data-testid="filter-fresh" />
                <Checkbox label="Hide likely duplicates" checked={collapse} onChange={(e) => setCollapse(e.target.checked)} data-testid="filter-dupes" />
              </div>
              {activeFilters > 0 ? (
                <Button size="sm" variant="ghost" onClick={() => { setFamily("all"); setRemote("all"); setFresh("all"); setCollapse(false); }}>
                  Clear filters
                </Button>
              ) : null}
            </details>
            {(bundle.reports.find((r) => r.adapter.id === "jobs")?.rejected.length ?? 0) > 0 ? (
              <p className="pt-muted" data-testid="jobs-rejected">
                {bundle.reports.find((r) => r.adapter.id === "jobs")!.rejected.length} feed records did not match the job-posting contract and are not shown. Details are in Settings.
              </p>
            ) : null}
            <p className="pt-muted pt-count" role="status" data-testid="job-count">
              {list.length} posting{list.length === 1 ? "" : "s"}
              {view === "best" ? ", closest first" : view === "strategic" ? ", most strategic first" : ""}
            </p>
          </div>
          {bundle.jobs.length === 0 ? (
            <EmptyState title="The job feed is empty.">
              <p>No postings were provided. No live job source is connected in this build.</p>
            </EmptyState>
          ) : list.length === 0 ? (
            <EmptyState title={view === "shortlist" ? "Nothing shortlisted yet." : "No postings match."}>
              <p>{view === "shortlist" ? "Shortlist a posting from its detail to keep it here." : "Try another view or clear the search."}</p>
            </EmptyState>
          ) : (
            <ul className="ct-list-plain pt-rows">
              {list.slice(0, limit).map((j) => {
                const m = matchByJob.get(j.id);
                const st = standingOf(m, rankOf, scored);
                const reason = rowReason(m);
                const active = shown?.id === j.id;
                const comp = formatCompensation(j.compensation);
                return (
                  <li key={j.id}>
                    <Link
                      to={`/jobs/${encodeURIComponent(j.id)}`}
                      state={{ fromList: true }}
                      onClick={() => (lastPicked.current = j.id)}
                      className={active ? "pt-row pt-row--on" : "pt-row"}
                      aria-current={active ? "true" : undefined}
                      data-testid={`pt-job-${j.id}`}
                    >
                      <span className="pt-row__title">
                        {j.title}
                        {workspace.shortlist.includes(j.id) ? <Icon name="bookmark" size={14} className="pt-row__saved" /> : null}
                      </span>
                      {j.source !== "synthetic" && bundle.jobFacets[j.id]?.synthetic !== true ? <span className="pt-row__flag" data-testid={`unverified-${j.id}`}>Provenance not verified</span> : null}
                      <span className="pt-row__meta">
                        {j.organization}, {j.location ?? "location not stated"}
                        {comp.known ? "" : ", pay not disclosed"}
                      </span>
                      <span className={`pt-standing pt-standing--${st}`}>{STANDING_LABEL[st]}</span>
                      {(bundle.jobFacets[j.id]?.security_flags ?? []).length > 0 ? <span className="pt-row__flag">Flagged at ingestion: {(bundle.jobFacets[j.id]?.security_flags ?? []).map(humanise).join(", ").toLowerCase()}</span> : null}
                      {reason ? <span className={`pt-row__reason pt-tone--${reason.tone}`}>{reason.text}</span> : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
          {list.length > limit ? (
            <Button className="pt-more" onClick={() => setLimit(limit + PAGE)}>
              Show {Math.min(PAGE, list.length - limit)} more of {list.length - limit}
            </Button>
          ) : null}
        </section>

        {shown ? (
          <article key={shown.id} className="pt-detail" aria-labelledby="pt-detail-title" tabIndex={-1} ref={detailRef} data-testid="pt-detail">
            <Button variant="ghost" className="pt-back" onClick={back}>
              <Icon name="back" size={16} /> Back to postings
            </Button>
            <header className="pt-detail__head">
              <p className={`pt-standing pt-standing--${standingOf(match, rankOf, scored)}`}>{STANDING_LABEL[standingOf(match, rankOf, scored)]}</p>
              <h2 id="pt-detail-title">{shown.title}</h2>
              {(bundle.jobFacets[shown.id]?.security_flags ?? []).length > 0 ? (
                <p className="pt-row__flag">Flagged by the data lane at ingestion: {(bundle.jobFacets[shown.id]?.security_flags ?? []).map(humanise).join(", ").toLowerCase()}. The text is shown as plain text only.</p>
              ) : null}
              {shown.source !== "synthetic" && bundle.jobFacets[shown.id]?.synthetic !== true ? (
                <p className="pt-row__flag" data-testid="detail-unverified">
                  Provenance not verified: this posting does not declare itself synthetic. It is shown as plain text, and its link opens only after a warning.
                </p>
              ) : null}
              <p className="pt-detail__org">
                {shown.organization}. {shown.location ?? "Location not stated"}. {shown.remote_policy && shown.remote_policy !== "unknown" ? humanise(shown.remote_policy) : "Work style unknown"}. {shown.seniority === "unknown" ? "Seniority unknown" : humanise(shown.seniority)}, {shown.employment_type === "unknown" ? "employment type unknown" : humanise(shown.employment_type).toLowerCase()}.
              </p>
              <p className="pt-detail__facts">
                <span>{formatCompensation(shown.compensation).known ? formatCompensation(shown.compensation).text.split(" · ").join(", ") : "Pay not disclosed (unknown, not zero)"}</span>
                <span>Captured {formatDate(shown.fetched_at)}, {freshness(shown.fetched_at, clock).label.toLowerCase()}</span>
                <span>{familyLabel(shown.role_family)}</span>
              </p>
              <div className="pt-actions">
                <Button variant={shortlisted ? "secondary" : "primary"} aria-pressed={shortlisted} onClick={() => toggleShortlist(shown)} data-testid="pt-shortlist">
                  <Icon name="bookmark" size={16} /> {shortlisted ? "Shortlisted" : "Shortlist"}
                </Button>
                <Link className="ct-btn ct-btn--secondary" to={`/resume/tailor/${encodeURIComponent(shown.id)}`} data-testid="pt-tailor">
                  <Icon name="resume" size={16} /> Tailor résumé
                </Link>
                {myAppByJob.get(shown.id) ? (
                  <Link className="ct-btn ct-btn--secondary" to={`/applications/${encodeURIComponent(myAppByJob.get(shown.id)!.id)}`}>
                    <Icon name="applications" size={16} /> In your tracker
                  </Link>
                ) : (
                  <Button onClick={() => saveToTracker(shown)} data-testid="pt-save-tracker">
                    <Icon name="applications" size={16} /> Save to tracker
                  </Button>
                )}
                <Button variant="ghost" onClick={() => setLinkJob(shown)}>
                  Posting link…
                </Button>
              </div>
            </header>

            {match ? (
              <>
                <section className="pt-block" aria-labelledby="pt-why">
                  <h3 id="pt-why">Why this could fit you</h3>
                  <ReasonList items={reasonsFor(match)} empty="None of the skills this posting lists is covered by a usable claim." testId="pt-why-list" />
                </section>
                <section className="pt-block" aria-labelledby="pt-block">
                  <h3 id="pt-block">What stands in the way</h3>
                  <ReasonList items={blockersFor(match)} empty="Nothing the engine knows of: every constraint passes and every listed skill is covered." testId="pt-blockers" />
                </section>
                <section className="pt-block" aria-labelledby="pt-improve">
                  <h3 id="pt-improve">What would improve your fit</h3>
                  <ReasonList items={improvementsFor(match)} empty="The engine published no hypothetical changes for this posting." testId="pt-improve-list" />
                </section>
                <section className="pt-block pt-block--scores" aria-labelledby="pt-scores">
                  <h3 id="pt-scores">Engine scores</h3>
                  {matchesStale ? <p className="pt-tone--weak" data-testid="stale-note">These scores may be out of date: your evidence changed after the engine computed them.</p> : null}
                  <ScorePair near={match.near_term_fit.score} strategic={match.strategic_value.score} />
                  <p className="pt-muted">Relative heuristics within this feed, not a probability of being hired. Near-term fit and strategic value are never combined here.</p>
                  <details className="pt-tech">
                    <summary>How the engine computed these</summary>
                    <ul className="ct-list-plain">
                      {[...match.near_term_fit.components.map((c) => ({ ...c, g: "Near-term" })), ...match.strategic_value.components.map((c) => ({ ...c, g: "Strategic" }))].map((c) => (
                        <li key={`${c.g}-${c.key}`}>
                          <strong>{c.g}: {c.label}</strong>, {c.status === "known" && c.value !== null ? `${Math.round(c.value * 100)}%` : "unknown, excluded"}
                          {c.weight !== null ? `, weight ${Math.round(c.weight * 100)}%` : ""}. <span className="pt-muted">{c.note}</span>
                        </li>
                      ))}
                    </ul>
                    <p className="pt-muted">
                      Engine {match.engine.name} {match.engine.version}. Unknown inputs: {match.unknown_inputs.map(humanise).join(", ") || "none"}.
                    </p>
                    {match.overall ? (
                      <p data-testid="overall-score">
                        The engine also publishes a single combined score; CareerTwin shows the two parts separately instead. Overall score: {match.overall.score} / 100
                        {match.overall.interval ? ` (range ${match.overall.interval.low} to ${match.overall.interval.high})` : ""}. <span className="pt-muted">{match.overall.note}</span>
                      </p>
                    ) : null}
                    <h4 className="pt-tech__h">Your constraints</h4>
                    <ul className="ct-list-plain" data-testid="hard-constraints">
                      {match.hard_constraints.map((h) => (
                        <li key={h.key}>
                          <strong>{h.status === "pass" ? "Pass" : h.status === "fail" ? "Fail" : "Unknown"}: {h.label}.</strong> {h.detail}
                        </li>
                      ))}
                    </ul>
                    <p className="pt-muted">Constraints never change either score, so a high score can still fail one.</p>
                    {match.unknown_inputs.length > 0 ? (
                      <>
                        <h4 className="pt-tech__h">What the engine does not know</h4>
                        <ul data-testid="unknown-inputs">
                          {match.unknown_inputs.map((u) => (
                            <li key={u}>{humanise(u)}</li>
                          ))}
                        </ul>
                      </>
                    ) : null}
                    <h4 className="pt-tech__h">Skills and the evidence behind them</h4>
                    <ul className="ct-list-plain" data-testid="skill-evidence">
                      {match.skills.map((sk) => (
                        <li key={sk.skill}>
                          {skillLabel(sk.skill)}: {sk.evidence_strength === "supported" ? "backed by evidence" : sk.evidence_strength === "missing" ? "no claim" : sk.evidence_strength}
                          {sk.claim_ids.length ? ` (${sk.claim_ids.length} claim${sk.claim_ids.length === 1 ? "" : "s"})` : ""}
                        </li>
                      ))}
                    </ul>
                  </details>
                </section>
              </>
            ) : (
              <p className="pt-muted">The matching engine did not rank this posting, so CareerTwin cannot explain its fit.</p>
            )}
            {shown.requirements && shown.requirements.length > 0 ? (
              <details className="pt-tech" data-testid="pt-requirements">
                <summary>Requirements text from the posting ({shown.requirements.length})</summary>
                <ul>{shown.requirements.map((r, i) => <li key={i}>{r}</li>)}</ul>
              </details>
            ) : null}
            <p className="pt-muted pt-skills">Skills listed: {shown.skills.map(skillLabel).join(", ") || "none"}.</p>
          </article>
        ) : null}
      </div>
      {bundle.matchRefusals.length > 0 ? (
        <p className="pt-muted pt-footnote">
          {bundle.matchRefusals.length} postings were not ranked because they failed a hard constraint upstream. <Link to="/matching">See them, with their reasons, under Compare</Link>.
        </p>
      ) : null}
      <ExternalLinkDialog job={linkJob} onClose={() => setLinkJob(null)} />
    </Screen>
  );
}
