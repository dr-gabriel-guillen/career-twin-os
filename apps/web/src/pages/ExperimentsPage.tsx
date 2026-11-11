import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Banner, Button, Card, Checkbox, EmptyState, Facts, SelectField, TabPanel, TableRegion, Tabs, TextArea, TextField, useAnnounce, type Tone } from "@careertwin/ui";
import { ConfirmDialog, Page } from "@/components/common";
import { ASSIGNMENT_SCHEMA, OUTCOME_SCHEMA, type AnalysisVerdict, type ExperimentAssignment, type ExperimentOutcome } from "@/adapters/proposed";
import { ASSIGNMENT_UNITS, type AssignmentUnit, type ExperimentSpec, type ExperimentStatus } from "@/contracts/types";
import { ASSIGNMENT_ALGORITHM, assignVariant } from "@/lib/assign";
import { designProblems, plannedPerArmFromRule, summariseExperiment } from "@/lib/experimentView";
import { formatDateTime, humanise, percent, truncate } from "@/lib/format";
import { mulberry32, seedFromString } from "@/lib/prng";
import { newId } from "@/lib/safety";
import { sha256Hex } from "@/lib/sha256";
import { MIN_OBSERVED_PER_ARM, minimumDetectableIncrease, sampleSizePerArm, type EvidenceLevel } from "@/lib/stats";
import { useApp } from "@/state/AppContext";
import { useIndexes } from "@/state/selectors";

const STATUS_TONE: Record<ExperimentStatus, Tone> = { draft: "neutral", preregistered: "info", running: "success", paused: "warning", closed: "neutral" };
const METRICS = [
  { value: "recruiter_screen_within_window", label: "Recruiter screen within the outcome window" },
  { value: "any_reply_within_window", label: "Any reply within the outcome window" },
  { value: "interview_within_window", label: "Interview within the outcome window" },
];
const STRATA = [
  { value: "role_family", label: "Role family" },
  { value: "seniority", label: "Seniority" },
  { value: "remote_policy", label: "Work style" },
];
const EVIDENCE_TEXT: Record<EvidenceLevel, { title: string; tone: Tone }> = {
  "no-data": { title: "No observed outcomes yet.", tone: "neutral" },
  insufficient: { title: "Insufficient evidence. No conclusion can be drawn.", tone: "warning" },
  inconclusive: { title: "Inconclusive. The data do not separate the variants.", tone: "warning" },
  "meets-preregistered-criterion": { title: "The preregistered criterion is met.", tone: "info" },
};

const UPSTREAM_VERDICT: Record<AnalysisVerdict, string> = {
  EVIDENCE_FOR_DIFFERENCE: "The preregistered criterion is met.",
  INCONCLUSIVE: "Inconclusive. This is not evidence of no effect.",
  INTERIM_NO_CLAIM: "Interim look. No result can be claimed yet.",
  EXPLORATORY_NO_CLAIM: "Exploratory only. No claim is made.",
  NOT_ANALYZABLE: "Not analyzable as designed.",
  OBSERVATIONAL_ONLY: "Observational history, not an experiment. No causal reading.",
};

interface Unit {
  id: string;
  label: string;
  stratum: string;
}

export default function ExperimentsPage() {
  const { bundle, workspace, dispatch } = useApp();
  const { applications, jobById } = useIndexes();
  const announce = useAnnounce();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState("design");
  const selected = workspace.experiments.find((e) => e.id === params.get("exp")) ?? workspace.experiments[0] ?? null;
  const [draft, setDraft] = useState<ExperimentSpec | null>(selected);
  const [variantsText, setVariantsText] = useState(selected?.variants.join(", ") ?? "");
  const [plannedUnits, setPlannedUnits] = useState("40");
  const [lockOpen, setLockOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [simRates, setSimRates] = useState<Record<string, string>>({});
  const [simCensored, setSimCensored] = useState("20");
  const [baseline, setBaseline] = useState("10");
  const [target, setTarget] = useState("20");
  const [volume, setVolume] = useState("20");

  // Re-seed the form whenever a different experiment (or a saved revision of it) is selected.
  useEffect(() => {
    setDraft(selected);
    setVariantsText(selected?.variants.join(", ") ?? "");
    const planned = selected ? plannedPerArmFromRule(selected) : null;
    if (selected && planned !== null) setPlannedUnits(String(planned * selected.variants.length));
    setNotice(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id, selected?.design_locked_at, selected?.status]);

  const locked = selected !== null && selected.design_locked_at !== null;
  const hasSalt = selected !== null && selected.id in workspace.experimentSalts;
  const assignments = selected ? workspace.assignments.filter((a) => a.experiment_id === selected.id) : [];
  const plannedPerArm = selected ? plannedPerArmFromRule(selected) : null;
  const results = useMemo(() => (selected ? summariseExperiment(selected, workspace.assignments, workspace.outcomes, plannedPerArm) : null), [selected, workspace.assignments, workspace.outcomes, plannedPerArm]);
  const upstream = selected ? bundle.analyses.find((a) => a.experiment_id === selected.id) ?? null : null;

  const candidateFromDraft = (): ExperimentSpec | null => {
    if (!draft) return null;
    const variants = variantsText.split(",").map((v) => v.trim()).filter(Boolean);
    const n = Number(plannedUnits);
    return {
      ...draft,
      variants,
      stopping_rule: Number.isInteger(n) && n > 0 ? `Stop when ${n} units have a closed outcome window. No interim decisions.` : "",
    };
  };
  const problems = useMemo(() => {
    const c = candidateFromDraft();
    return c ? designProblems(c) : [];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, variantsText, plannedUnits]);

  const select = (id: string) => {
    setParams({ exp: id });
    setTab("design");
  };

  const createExperiment = () => {
    const id = newId("exp");
    const spec: ExperimentSpec = {
      id,
      title: "",
      hypothesis: "",
      status: "draft",
      variants: ["variant-a", "variant-b"],
      assignment_unit: "employer-cluster",
      strata: ["role_family"],
      primary_metric: METRICS[0]!.value,
      design_locked_at: null,
      stopping_rule: "",
      consent_required: true,
      outcome_window_days: 21,
      secondary_metrics: [],
      analysis_plan: "",
    };
    // The salt never leaves this browser; only its digest is recorded in the design.
    dispatch({ type: "experiment/upsert", spec, salt: newId("salt") });
    select(id);
    announce("New draft experiment created.");
  };

  const saveDraft = () => {
    const c = candidateFromDraft();
    if (!c) return;
    if (c.variants.length < 2 || new Set(c.variants).size !== c.variants.length) return setNotice("A draft needs at least two distinct variant names before it can be saved.");
    const refused = dispatch({ type: "experiment/upsert", spec: c });
    setNotice(refused);
    if (!refused) announce("Draft saved.");
  };

  const lock = () => {
    const c = candidateFromDraft();
    if (!c || !selected) return;
    const salt = workspace.experimentSalts[selected.id];
    if (!salt) return setNotice("This experiment has no local salt, so it cannot be locked here.");
    const refused = dispatch({ type: "experiment/upsert", spec: { ...c, status: "preregistered", design_locked_at: new Date().toISOString(), seed_digest: sha256Hex(salt) } });
    setNotice(refused);
    setLockOpen(false);
    if (!refused) {
      announce("Design locked and preregistered. It can no longer be edited.");
      setTab("assign");
    }
  };

  const setStatus = (status: ExperimentStatus) => {
    if (!selected) return;
    const refused = dispatch({ type: "experiment/upsert", spec: { ...selected, status } });
    setNotice(refused);
    if (!refused) announce(`Experiment is now ${status}.`);
  };

  // Units eligible for assignment, derived from the unit type in the locked design.
  const units: Unit[] = useMemo(() => {
    if (!selected) return [];
    const stratumOf = (jobId: string) => {
      const j = jobById.get(jobId);
      if (!j) return "unknown";
      return selected.strata.map((s) => (s === "role_family" ? j.role_family ?? "unknown" : s === "seniority" ? j.seniority : s === "remote_policy" ? j.remote_policy ?? "unknown" : "unknown")).join("|") || "all";
    };
    if (selected.assignment_unit === "job") return bundle.jobs.map((j) => ({ id: `job:${j.id}`, label: `${j.title} — ${j.organization}`, stratum: stratumOf(j.id) }));
    if (selected.assignment_unit === "application") return applications.map((a) => ({ id: `application:${a.id}`, label: `${jobById.get(a.job_id)?.title ?? a.job_id} (${a.id})`, stratum: stratumOf(a.job_id) }));
    const firstByOrg = new Map<string, string>();
    for (const j of bundle.jobs) if (!firstByOrg.has(j.organization)) firstByOrg.set(j.organization, j.id);
    return [...firstByOrg].map(([org, jobId]) => ({ id: `employer:${org.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`, label: org, stratum: stratumOf(jobId) }));
  }, [selected, bundle.jobs, applications, jobById]);
  const assignedIds = new Set(assignments.map((a) => a.unit_id));
  const unassigned = units.filter((u) => !assignedIds.has(u.id));
  const canAssign = locked && hasSalt && (selected?.status === "preregistered" || selected?.status === "running");

  const assign = (toAssign: Unit[]) => {
    if (!selected || toAssign.length === 0) return;
    const salt = workspace.experimentSalts[selected.id];
    if (!salt) return;
    const now = new Date().toISOString();
    const records: ExperimentAssignment[] = toAssign.map((u) => ({
      schema_version: ASSIGNMENT_SCHEMA,
      id: newId("asg"),
      experiment_id: selected.id,
      unit_id: u.id,
      unit_label: u.label,
      stratum: u.stratum,
      variant: assignVariant(selected.id, u.stratum, u.id, selected.variants, salt),
      assigned_at: now,
      algorithm: ASSIGNMENT_ALGORITHM,
      seed_digest: selected.seed_digest ?? sha256Hex(salt),
      exposure_logged_at: null,
    }));
    const refused = dispatch({ type: "experiment/assign", assignments: records });
    setNotice(refused);
    if (!refused) announce(`${records.length} unit${records.length === 1 ? "" : "s"} assigned and stored before any exposure.`);
  };

  const simulate = () => {
    if (!selected) return;
    const censorShare = Math.min(90, Math.max(0, Number(simCensored) || 0)) / 100;
    const rng = mulberry32(seedFromString(`${selected.id}|${assignments.length}|${JSON.stringify(simRates)}|${simCensored}`));
    const now = new Date().toISOString();
    const outcomes: ExperimentOutcome[] = assignments.map((a) => {
      const rate = Math.min(1, Math.max(0, (Number(simRates[a.variant] ?? "20") || 0) / 100));
      const censored = rng() < censorShare;
      const success = rng() < rate;
      return {
        schema_version: OUTCOME_SCHEMA,
        assignment_id: a.id,
        experiment_id: selected.id,
        metric: selected.primary_metric,
        status: censored ? "censored" : "observed",
        value: censored ? null : success,
        recorded_at: now,
        data_origin: "synthetic-simulation",
      };
    });
    const refused = dispatch({ type: "experiment/outcomes", outcomes });
    setNotice(refused);
    if (!refused) announce("Synthetic outcomes generated. They are simulated, not real results.");
  };

  const p1 = Number(baseline) / 100;
  const p2 = Number(target) / 100;
  const validRates = p1 > 0 && p1 < 1 && p2 > 0 && p2 < 1 && p1 !== p2;
  const needed = validRates ? sampleSizePerArm(p1, p2) : null;
  const perArmVolume = Math.floor((Number(volume) || 0) / 2);
  const mde = p1 > 0 && p1 < 1 ? minimumDetectableIncrease(p1, perArmVolume) : null;

  return (
    <Page
      title="Experiments"
      lead="Design a fair comparison before you look at any results: one hypothesis, a locked design, random assignment, a fixed outcome window, and results reported with their uncertainty. All outcomes in this build are simulated."
      actions={<Button variant="primary" onClick={createExperiment} data-testid="exp-new">New experiment</Button>}
    >
      {notice ? (
        <Banner tone="warning" role="alert" title="Not done" actions={<Button size="sm" onClick={() => setNotice(null)}>Dismiss</Button>} data-testid="exp-notice">
          {notice}
        </Banner>
      ) : null}
      <div className="app-split">
        <Card title="Experiments">
          {workspace.experiments.length === 0 ? (
            <EmptyState title="No experiments yet." action={<Button onClick={createExperiment}>New experiment</Button>}>
              <p>An experiment compares two ways of applying, decided by a random draw rather than by you.</p>
            </EmptyState>
          ) : (
            <ul className="ct-list-plain ct-stack" data-testid="exp-list">
              {workspace.experiments.map((e) => (
                <li key={e.id}>
                  <button type="button" className="app-pick" aria-current={selected?.id === e.id ? "true" : undefined} onClick={() => select(e.id)} data-testid={`exp-${e.id}`}>
                    <strong>{e.title || "Untitled draft"}</strong>
                    <span className="ct-row ct-small">
                      <Badge tone={STATUS_TONE[e.status]}>{humanise(e.status)}</Badge>
                      {e.design_locked_at ? <Badge tone="info">Design locked</Badge> : <Badge>Editable</Badge>}
                      <span className="ct-muted">{workspace.assignments.filter((a) => a.experiment_id === e.id).length} units assigned</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {selected && draft && results ? (
          <Card title={selected.title || "Untitled draft"} data-testid="exp-detail">
            <div className="ct-row">
              <Badge tone={STATUS_TONE[selected.status]}>{humanise(selected.status)}</Badge>
              {locked ? <Badge tone="info">Locked {formatDateTime(selected.design_locked_at)}</Badge> : null}
              {!hasSalt ? <Badge tone="synthetic">Fixture example (read-only assignment)</Badge> : null}
            </div>
            <Tabs idBase="exp" label="Experiment" active={tab} onChange={setTab} tabs={[{ id: "design", label: "Design" }, { id: "assign", label: `Assignment (${assignments.length})` }, { id: "results", label: "Results" }, { id: "power", label: "Sample size" }]} />

            <TabPanel idBase="exp" id="design" active={tab}>
              <div className="ct-stack">
                {locked ? (
                  <Banner tone="info" title="This design is preregistered and locked." data-testid="exp-locked">
                    Nothing below can be edited any more. That is the point: the plan was fixed before any outcome existed. Seed digest:{" "}
                    <code>{truncate(selected.seed_digest ?? "none", 20)}</code>
                  </Banner>
                ) : null}
                <TextField label="Title" value={draft.title} disabled={locked} onChange={(e) => setDraft({ ...draft, title: e.target.value })} maxLength={120} data-testid="exp-title" />
                <TextArea label="Hypothesis" hint="One falsifiable statement about one change." value={draft.hypothesis} disabled={locked} onChange={(e) => setDraft({ ...draft, hypothesis: e.target.value })} maxLength={500} data-testid="exp-hypothesis" />
                <TextField label="Variants (cohorts), comma separated" hint="Two variants keep the analysis simple. More need a multiple-comparison plan." value={variantsText} disabled={locked} onChange={(e) => setVariantsText(e.target.value)} data-testid="exp-variants" />
                <SelectField label="Assignment unit" hint="What gets randomised. Employer clusters avoid sending two variants to the same employer." value={draft.assignment_unit} disabled={locked} onChange={(e) => setDraft({ ...draft, assignment_unit: e.target.value as AssignmentUnit })} options={ASSIGNMENT_UNITS.map((u) => ({ value: u, label: humanise(u) }))} data-testid="exp-unit" />
                <fieldset className="ct-stack" disabled={locked}>
                  <legend className="ct-field__label">Stratify the random draw by</legend>
                  <div className="ct-row">
                    {STRATA.map((s) => (
                      <Checkbox key={s.value} label={s.label} checked={draft.strata.includes(s.value)} onChange={(e) => setDraft({ ...draft, strata: e.target.checked ? [...draft.strata, s.value] : draft.strata.filter((x) => x !== s.value) })} />
                    ))}
                  </div>
                </fieldset>
                <SelectField label="Primary metric (exactly one)" value={draft.primary_metric} disabled={locked} onChange={(e) => setDraft({ ...draft, primary_metric: e.target.value })} options={METRICS.some((m) => m.value === draft.primary_metric) ? METRICS : [...METRICS, { value: draft.primary_metric, label: draft.primary_metric }]} />
                <fieldset className="ct-stack" disabled={locked}>
                  <legend className="ct-field__label">Secondary metrics (descriptive only)</legend>
                  <div className="ct-row">
                    {METRICS.filter((m) => m.value !== draft.primary_metric).map((m) => (
                      <Checkbox key={m.value} label={m.label} checked={(draft.secondary_metrics ?? []).includes(m.value)} onChange={(e) => setDraft({ ...draft, secondary_metrics: e.target.checked ? [...(draft.secondary_metrics ?? []), m.value] : (draft.secondary_metrics ?? []).filter((x) => x !== m.value) })} />
                    ))}
                  </div>
                </fieldset>
                {(draft.secondary_metrics ?? []).length > 0 ? (
                  <Banner tone="warning" title="Multiple metrics multiply false alarms.">
                    With {(draft.secondary_metrics ?? []).length + 1} metrics, the chance that at least one looks “significant” by luck alone rises. Only the primary metric counts.
                  </Banner>
                ) : null}
                <div className="app-toolbar">
                  <TextField label="Outcome window (days)" type="number" min={1} max={365} value={draft.outcome_window_days ?? ""} disabled={locked} onChange={(e) => setDraft({ ...draft, outcome_window_days: Math.max(1, Math.round(Number(e.target.value) || 1)) })} hint="Outcomes still inside the window are censored, not failures." data-testid="exp-window" />
                  <TextField label="Planned units in total" type="number" min={2} value={plannedUnits} disabled={locked} onChange={(e) => setPlannedUnits(e.target.value)} hint="Fixes the stopping rule. See the Sample size tab." data-testid="exp-planned" />
                </div>
                <TextArea label="Analysis plan" hint="How the primary metric will be compared, and what happens to censored and missing units." value={draft.analysis_plan ?? ""} disabled={locked} onChange={(e) => setDraft({ ...draft, analysis_plan: e.target.value })} maxLength={800} data-testid="exp-plan" />
                <Checkbox label="Consent required" hint="You confirm each use of a variant yourself. Required." checked={draft.consent_required} disabled={locked} onChange={(e) => setDraft({ ...draft, consent_required: e.target.checked })} />
                <Facts items={[{ label: "Stopping rule", value: locked ? selected.stopping_rule : candidateFromDraft()?.stopping_rule || "Not set" }]} />
                {!locked ? (
                  <>
                    {problems.length > 0 ? (
                      <Banner tone="warning" title="Before this design can be locked" data-testid="exp-problems">
                        <ul>{problems.map((p) => <li key={p}>{p}</li>)}</ul>
                      </Banner>
                    ) : null}
                    <div className="ct-row">
                      <Button onClick={saveDraft} data-testid="exp-save">Save draft</Button>
                      <Button variant="primary" onClick={() => setLockOpen(true)} disabled={problems.length > 0 || !hasSalt} data-testid="exp-lock">
                        Lock design (preregister)…
                      </Button>
                      <Button variant="danger" onClick={() => { const refused = dispatch({ type: "experiment/remove", id: selected.id }); setNotice(refused); if (!refused) { setParams({}); announce("Draft deleted."); } }}>
                        Delete draft
                      </Button>
                    </div>
                  </>
                ) : (
                  <div className="ct-row">
                    {selected.status === "preregistered" || selected.status === "paused" ? <Button onClick={() => setStatus("running")} data-testid="exp-start">Start running</Button> : null}
                    {selected.status === "running" ? <Button onClick={() => setStatus("paused")}>Pause</Button> : null}
                    {selected.status !== "closed" ? <Button onClick={() => setStatus("closed")} data-testid="exp-close">Close experiment</Button> : null}
                  </div>
                )}
              </div>
            </TabPanel>

            <TabPanel idBase="exp" id="assign" active={tab}>
              <div className="ct-stack">
                {!locked ? (
                  <Banner tone="warning" title="Lock the design first." data-testid="assign-blocked">Units can only be assigned after the design is preregistered, so the plan cannot be shaped by who landed where.</Banner>
                ) : !hasSalt ? (
                  <Banner tone="synthetic" title="These assignments came with the synthetic fixture.">This browser does not hold the secret salt for this experiment, so it cannot assign more units to it. Create your own experiment to try assignment.</Banner>
                ) : (
                  <Banner tone="info" title="Assignment is deterministic and stored before exposure.">
                    Each unit’s variant is derived from a hash of the secret salt, the experiment, the stratum and the unit ({ASSIGNMENT_ALGORITHM}). A unit is assigned once and never crosses over.
                  </Banner>
                )}
                {canAssign ? (
                  <div className="ct-row">
                    <Button variant="primary" onClick={() => assign(unassigned)} disabled={unassigned.length === 0} data-testid="assign-all">
                      Assign {unassigned.length} eligible unit{unassigned.length === 1 ? "" : "s"}
                    </Button>
                    <Button onClick={() => assign(unassigned.slice(0, 1))} disabled={unassigned.length === 0} data-testid="assign-one">
                      Assign next unit
                    </Button>
                  </div>
                ) : null}
                {assignments.length === 0 ? (
                  <EmptyState title="No units assigned yet." />
                ) : (
                  <TableRegion label="Assignments">
                    <table className="ct-table" data-testid="assign-table">
                      <thead>
                        <tr>
                          <th scope="col">Unit</th>
                          <th scope="col">Stratum</th>
                          <th scope="col">Variant</th>
                          <th scope="col">Assigned</th>
                          <th scope="col">Exposure</th>
                        </tr>
                      </thead>
                      <tbody>
                        {assignments.map((a) => (
                          <tr key={a.id}>
                            <th scope="row">{truncate(a.unit_label, 60)}</th>
                            <td>{a.stratum}</td>
                            <td><Badge tone="info">{a.variant}</Badge></td>
                            <td>{formatDateTime(a.assigned_at)}</td>
                            <td>
                              {a.exposure_logged_at ? formatDateTime(a.exposure_logged_at) : hasSalt && selected.status === "running" ? (
                                <Button size="sm" onClick={() => { const refused = dispatch({ type: "experiment/exposure", assignmentId: a.id, at: new Date().toISOString() }); setNotice(refused); }} aria-label={`Log exposure for ${a.unit_label}`}>
                                  Log exposure
                                </Button>
                              ) : "Not exposed"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableRegion>
                )}
              </div>
            </TabPanel>

            <TabPanel idBase="exp" id="results" active={tab}>
              {upstream ? (
                <div className="ct-stack" data-testid="exp-results">
                  <Banner tone={upstream.verdict === "EVIDENCE_FOR_DIFFERENCE" ? "info" : "warning"} title={UPSTREAM_VERDICT[upstream.verdict]} data-testid="upstream-verdict">
                    Analysis computed upstream ({upstream.producer}){upstream.estimand ? `, estimand ${upstream.estimand}` : ""}. It is shown as given; nothing here is recalculated in the browser.
                    {upstream.verdict === "EVIDENCE_FOR_DIFFERENCE" ? " This is one experiment on synthetic data; it supports a difference under the preregistered plan, nothing more." : " No variant is declared better."}
                  </Banner>
                  <TableRegion label="Upstream analysis by variant">
                    <table className="ct-table" data-testid="upstream-arms">
                      <thead>
                        <tr>
                          <th scope="col">Variant</th>
                          <th scope="col" className="ct-num">Assigned</th>
                          <th scope="col" className="ct-num">Positive</th>
                          <th scope="col" className="ct-num">Censored</th>
                          <th scope="col" className="ct-num">Missing</th>
                        </tr>
                      </thead>
                      <tbody>
                        {upstream.arms.map((a) => (
                          <tr key={a.variant}>
                            <th scope="row">{a.variant}</th>
                            <td className="ct-num">{a.n_assigned}</td>
                            <td className="ct-num">{a.n_success ?? "Not reported"}</td>
                            <td className="ct-num">{a.n_censored ?? "Not reported"}</td>
                            <td className="ct-num">{a.n_missing ?? "Not reported"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableRegion>
                  <Facts
                    items={[
                      { label: "Phase", value: upstream.phase ?? "Not stated" },
                      {
                        label: "Difference",
                        value: upstream.effect
                          ? `${upstream.effect.contrast}: ${(upstream.effect.estimate * 100).toFixed(1)} points (${Math.round(upstream.effect.ci_level * 100)}% interval ${(upstream.effect.ci_low * 100).toFixed(1)} to ${(upstream.effect.ci_high * 100).toFixed(1)} points; ${upstream.effect.method})`
                          : "Not reported",
                      },
                    ]}
                  />
                  {[["Why no claim is made", upstream.reasons_withheld], ["Warnings", upstream.warnings], ["Limits of interpretation", upstream.interpretation_limits]].map(([title, list]) =>
                    (list as string[]).length > 0 ? (
                      <div key={title as string}>
                        <h3>{title as string}</h3>
                        <ul className="ct-small">{(list as string[]).map((x) => <li key={x}>{x}</li>)}</ul>
                      </div>
                    ) : null,
                  )}
                </div>
              ) : (
              <div className="ct-stack" data-testid="exp-results">
                <Banner tone="synthetic" title="Simulated outcomes." data-testid="results-synthetic">
                  {results.origins.includes("user-recorded") ? "Some outcomes were recorded by you; the rest are simulated." : "Every outcome shown here was generated by a seeded simulator, not observed."}
                  {!hasSalt && bundle.manifest && Number.isFinite(bundle.manifest.experiment.synthetic_true_rate_all_variants) ? ` The fixture generator used the same true rate (${percent(bundle.manifest.experiment.synthetic_true_rate_all_variants)}) for every variant, so any gap you see is noise.` : ""}
                </Banner>
                <Banner tone={EVIDENCE_TEXT[results.evidence.level].tone} title={EVIDENCE_TEXT[results.evidence.level].title} data-testid="evidence-level">
                  <ul>{results.evidence.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
                  {results.evidence.level === "meets-preregistered-criterion" ? <p>This supports a difference on the primary metric under the locked plan. It is still one experiment on a small, specific set of employers.</p> : <p>No variant is declared better.</p>}
                </Banner>
                <TableRegion label="Results by variant">
                  <table className="ct-table" data-testid="results-table">
                    <caption>Primary metric: {humanise(selected.primary_metric)}</caption>
                    <thead>
                      <tr>
                        <th scope="col">Variant</th>
                        <th scope="col" className="ct-num">Assigned</th>
                        <th scope="col" className="ct-num">Observed</th>
                        <th scope="col" className="ct-num">Positive</th>
                        <th scope="col" className="ct-num">Censored</th>
                        <th scope="col" className="ct-num">Missing</th>
                        <th scope="col" className="ct-num">Rate</th>
                        <th scope="col">95% interval</th>
                      </tr>
                    </thead>
                    <tbody>
                      {results.arms.map((a) => (
                        <tr key={a.variant}>
                          <th scope="row">{a.variant}</th>
                          <td className="ct-num">{a.assigned}</td>
                          <td className="ct-num">{a.observed}</td>
                          <td className="ct-num">{a.successes}</td>
                          <td className="ct-num">{a.censored}</td>
                          <td className="ct-num">{a.missing}</td>
                          <td className="ct-num">{a.rate === null ? "No data" : percent(a.rate)}</td>
                          <td>{a.interval ? `${percent(a.interval.lower)} to ${percent(a.interval.upper)}` : "No data"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableRegion>
                <Facts
                  items={[
                    { label: "Difference", value: results.difference && results.differenceLabel ? `${results.differenceLabel}: ${(results.difference.estimate * 100).toFixed(0)} points (95% interval ${(results.difference.lower * 100).toFixed(0)} to ${(results.difference.upper * 100).toFixed(0)} points)` : "Not available" },
                    { label: "Censored", value: `${results.totalCensored} unit(s) still inside the ${selected.outcome_window_days ?? "?"}-day window. Excluded from rates, not counted as failures.` },
                    { label: "Missing", value: `${results.totalMissing} unit(s) with no recorded outcome. Excluded from rates and reported here.` },
                    { label: "Exposed", value: `${results.exposed} of ${results.totalAssigned} assigned units had the variant actually used.` },
                    { label: "Planned", value: plannedPerArm === null ? "No planned sample recorded" : `${plannedPerArm} per variant (minimum of ${MIN_OBSERVED_PER_ARM} observed per variant before any comparison)` },
                  ]}
                />
                {hasSalt && locked ? (
                  <Card title="Generate synthetic outcomes" headingLevel={3}>
                    <p className="ct-small">For trying the results view only. You choose the true rates, so you already know the answer; watch how often small samples mislead.</p>
                    <div className="app-toolbar">
                      {selected.variants.map((v) => (
                        <TextField key={v} label={`True rate for ${v} (%)`} type="number" min={0} max={100} value={simRates[v] ?? "20"} onChange={(e) => setSimRates({ ...simRates, [v]: e.target.value })} data-testid={`sim-rate-${v}`} />
                      ))}
                      <TextField label="Share still censored (%)" type="number" min={0} max={90} value={simCensored} onChange={(e) => setSimCensored(e.target.value)} />
                      <Button onClick={simulate} disabled={assignments.length === 0} data-testid="simulate">
                        Generate synthetic outcomes
                      </Button>
                    </div>
                  </Card>
                ) : null}
                <p className="ct-small ct-muted">Application outcomes you record without random assignment are observational. They cannot be shown as experiment results and are not mixed into this table.</p>
              </div>
              )}
            </TabPanel>

            <TabPanel idBase="exp" id="power" active={tab}>
              <div className="ct-stack" data-testid="power-panel">
                <p>How many units would it take to tell two rates apart? (Two-sided test, 5% false-alarm rate, 80% power.)</p>
                <div className="app-toolbar">
                  <TextField label="Baseline rate (%)" type="number" min={1} max={99} value={baseline} onChange={(e) => setBaseline(e.target.value)} data-testid="power-baseline" />
                  <TextField label="Rate you hope to reach (%)" type="number" min={1} max={99} value={target} onChange={(e) => setTarget(e.target.value)} data-testid="power-target" />
                  <TextField label="Applications you can realistically send" type="number" min={2} value={volume} onChange={(e) => setVolume(e.target.value)} data-testid="power-volume" />
                </div>
                {needed === null ? (
                  <Banner tone="warning" title="Enter two different rates between 1% and 99%." />
                ) : (
                  <Banner tone="info" title={`About ${needed} units per variant (${needed * 2} in total).`} data-testid="power-needed">
                    To detect a move from {baseline}% to {target}%.
                  </Banner>
                )}
                <Banner tone={mde === null || (validRates && needed !== null && needed > perArmVolume) ? "warning" : "info"} title={`With ${perArmVolume} units per variant:`} data-testid="power-mde">
                  {mde === null
                    ? "Even a jump to nearly 100% could not be detected reliably. An experiment of this size cannot answer the question."
                    : `Only an increase of at least ${(mde * 100).toFixed(0)} percentage points over ${baseline}% is detectable. Smaller real effects will usually look like “no difference”.`}
                </Banner>
                <p className="ct-small ct-muted">These are planning approximations (normal approximation, equal allocation). They are guidance for the design, not a guarantee.</p>
              </div>
            </TabPanel>
          </Card>
        ) : (
          <EmptyState title="Select or create an experiment." />
        )}
      </div>

      <ConfirmDialog open={lockOpen} title="Lock and preregister this design?" confirmLabel="Lock design" testId="lock-dialog" onClose={() => setLockOpen(false)} onConfirm={lock}>
        <p>After locking you cannot change the hypothesis, variants, unit, strata, metric, window, planned sample or analysis plan. You can still pause or close the experiment.</p>
      </ConfirmDialog>
    </Page>
  );
}
