import { Component, useEffect, useState, type ErrorInfo, type ReactNode } from "react";
import { HashRouter, NavLink, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { AnnouncerProvider, Badge, Banner, Button, ErrorState, Icon, LoadingState, type IconName } from "@careertwin/ui";
import { formatDate } from "@/lib/format";
import { totalRejected, type DataBundle } from "@/adapters";
import type { ProfileClaim } from "@/contracts/types";
import { Page } from "@/components/common";
import { AppProvider, artifactMode, useShell } from "@/state/AppContext";

import OverviewPage from "@/pages/OverviewPage";
import EvidencePage from "@/pages/EvidencePage";
import ResumePage from "@/pages/ResumePage";
import ExperimentsPage from "@/pages/ExperimentsPage";
import SimulatorPage from "@/pages/SimulatorPage";
import SettingsPage from "@/pages/SettingsPage";
import DiscoverJourney from "@/journeys/Discover";
import TailorJourney from "@/journeys/Tailor";
import TrackJourney from "@/journeys/Track";
import AtlasJourney from "@/journeys/Atlas";
import CompareJourney from "@/journeys/Compare";

export const NAV = [
  { to: "/", label: "Overview", icon: "overview", group: "Search" },
  { to: "/jobs", label: "Discover", icon: "discover", group: "Search" },
  { to: "/matching", label: "Compare", icon: "match", group: "Search" },
  { to: "/applications", label: "Applications", icon: "applications", group: "Search" },
  { to: "/resume", label: "Résumé Studio", icon: "resume", group: "Profile" },
  { to: "/evidence", label: "Profile Evidence", icon: "evidence", group: "Profile" },
  { to: "/atlas", label: "Career Atlas", icon: "atlas", group: "Strategy" },
  { to: "/simulator", label: "Career Simulator", icon: "simulator", group: "Strategy" },
  { to: "/experiments", label: "Experiments", icon: "experiments", group: "Strategy" },
  { to: "/settings", label: "Settings", icon: "settings", group: "System" },
] as const satisfies readonly { to: string; label: string; icon: IconName; group: string }[];

const NAV_GROUPS = [...new Set(NAV.map((n) => n.group))];

class ErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { error: Error | null }> {
  override state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  override componentDidCatch(error: Error, info: ErrorInfo) {
    // Local console only. The app has no telemetry.
    console.error("CareerTwin UI error", error, info.componentStack);
  }
  override componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }
  override render() {
    if (this.state.error) {
      return (
        <ErrorState title="This screen hit an unexpected error." action={<Button onClick={() => this.setState({ error: null })}>Try again</Button>}>
          <p>Your stored data was not changed. Details (local only): {this.state.error.message}</p>
        </ErrorState>
      );
    }
    return this.props.children;
  }
}

/** Older addresses (the separate prototype pages and query-string selections) keep working. */
function Redirect({ to }: { to: string }) {
  const { id } = useParams();
  return <Navigate to={id ? `${to}/${encodeURIComponent(id)}` : to} replace />;
}

function NotFound() {
  return (
    <Page title="Page not found">
      <ErrorState title="There is no screen at this address.">
        <p>Use the navigation to pick a section.</p>
      </ErrorState>
    </Page>
  );
}

/** Plain-language file names for pipeline notes; the file name itself is kept as secondary text. */
const ARTIFACT_NAMES: Record<string, string> = {
  "resume_variant.json": "Résumé variant",
  "jobs.json": "Job feed",
  "jobs_ext.json": "Job feed details",
  "matches.json": "Match explanations",
  "matches_full.json": "Match explanations",
  "claims.json": "Evidence vault",
  "claims_vault.json": "Evidence vault",
  "events.json": "Application events",
  "experiment.json": "Experiment",
  "experiment_events.json": "Experiment outcomes",
  "analysis.json": "Experiment analysis",
  "atlas.pca.json": "Career atlas",
  "atlas_points.json": "Career atlas",
  "scenarios.json": "Transition scenarios",
};

/**
 * Rewrites a pipeline note for people: claim ids become the claim's own words where the vault has
 * them, and machine codes in capitals are dropped from the front. Nothing is removed: the source
 * file stays visible beside the note and the raw note is kept in the element's title.
 */
function readableNote(note: string, claimText: Map<string, string>): { text: string; file: string | null } {
  const m = /^([\w.-]+\.json):\s*/.exec(note);
  const file = m ? m[1]! : null;
  let text = m ? note.slice(m[0].length) : note;
  text = text.replace(/^[A-Z][A-Z_]{3,}:\s*/, "");
  text = text.replace(/\bcl_[0-9a-f]{6,}\b/g, (id) => (claimText.has(id) ? `“${claimText.get(id)}”` : id));
  return { text: text.charAt(0).toUpperCase() + text.slice(1), file };
}

/**
 * The data-source line under the top bar. Always visible and short; the safety facts (fictional data,
 * stub or mock sources, missing files) stay on screen, and the longer pipeline notes sit behind a
 * disclosure so they never outweigh the page.
 */
function SourceStatus({ artifacts, provenance, notes, claims }: { artifacts: boolean; provenance: DataBundle["provenance"] | null; notes: string[]; claims: ProfileClaim[] }) {
  const claimText = new Map(claims.map((c) => [c.id, c.claim_text]));
  if (!artifacts) {
    return (
      <section className="app-source" role="note" aria-label="Data source" data-testid="synthetic-banner">
        <span className="app-source__mode">
          <strong>Synthetic fixture mode.</strong>
        </span>
        <span className="app-source__text">Every employer, posting, candidate, event and outcome here is fictional. Nothing is sent anywhere, no application is ever submitted for you, and no live job source is connected.</span>
      </section>
    );
  }
  return (
    <section className="app-source app-source--artifacts" role="note" aria-label="Data source" data-testid="artifact-banner">
      <span className="app-source__mode">
        <strong>Pipeline artifacts mode (synthetic data only).</strong> Read-only.
      </span>
      {provenance ? (
        <span className="app-source__facts" data-testid="artifact-provenance">
          <span data-testid="mock-sources">Sources that label themselves stub or mock: {provenance.mock_sources.length === 0 ? "none" : provenance.mock_sources.join(", ")}</span>
          <span data-testid="missing-artifacts">Files the pipeline did not provide: {provenance.missing.length === 0 ? "none" : provenance.missing.join(", ")}</span>
        </span>
      ) : null}
      <details className="app-source__more" data-testid="source-details">
        <summary>About this data{notes.length > 0 ? ` (${notes.length} pipeline note${notes.length === 1 ? "" : "s"})` : ""}</summary>
        <div className="app-source__panel">
          <p>
            These screens show what the data, experiment and analytics lanes produced in an integration run, not lane D’s own fixtures. This build accepts synthetic data only; it cannot verify provenance
            itself. Nothing is sent anywhere and no application is ever submitted for you. Changes are not saved in this mode.
          </p>
          {notes.length > 0 ? (
            <ul className="app-source__notes" data-testid="source-notes">
              {notes.map((n) => {
                const r = readableNote(n, claimText);
                return (
                  <li key={n} title={n}>
                    {r.text}
                    {r.file ? <span className="ct-muted"> {ARTIFACT_NAMES[r.file] ?? "Pipeline file"} ({r.file})</span> : null}
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      </details>
    </section>
  );
}

function Shell() {
  const shell = useShell();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => setNavOpen(false), [location.pathname]);
  const rejected = shell.ready ? totalRejected(shell.ready.bundle) : 0;
  const artifacts = artifactMode();
  const provenance = shell.ready?.bundle.provenance ?? null;

  const clock = shell.ready?.bundle.clock ?? null;

  return (
    <div className="app" data-source={provenance?.data_source ?? (artifacts ? "artifacts" : "fixtures")}>
      <a className="app-skip" href="#main" onClick={(e) => { e.preventDefault(); document.getElementById("main")?.focus(); }}>
        Skip to main content
      </a>
      <aside className={navOpen ? "app-rail app-rail--open" : "app-rail"}>
        <div className="app-brand app-brand--rail">
          <span className="app-mark" aria-hidden="true" />
          <span className="app-brand__text">CareerTwin <span className="app-brand__os">OS</span></span>
        </div>
        <nav id="app-nav" className="app-nav" aria-label="Primary">
          {NAV_GROUPS.map((g) => (
            <div key={g} className="app-navgroup">
              <p className="app-navgroup__label" aria-hidden="true">{g}</p>
              <ul className="ct-list-plain">
                {NAV.filter((n) => n.group === g).map((n) => (
                  <li key={n.to}>
                    <NavLink to={n.to} end={n.to === "/"} className={({ isActive }) => (isActive ? "app-navlink app-navlink--active" : "app-navlink")} title={n.label}>
                      <Icon name={n.icon} size={17} />
                      <span className="app-navlink__text">{n.label}</span>
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
      </aside>
      {navOpen ? <div className="app-scrim" aria-hidden="true" onClick={() => setNavOpen(false)} /> : null}
      <div className="app-body">
        <header className="app-header">
          <div className="app-header__lead">
            <Button className="app-navtoggle" variant="ghost" aria-expanded={navOpen} aria-controls="app-nav" onClick={() => setNavOpen((o) => !o)}>
              <Icon name={navOpen ? "cross" : "menu"} size={18} />
              Menu
            </Button>
            <span className="app-brand app-brand--bar">
              <span className="app-mark" aria-hidden="true" />
              <span className="app-brand__text">CareerTwin <span className="app-brand__os">OS</span></span>
            </span>
            {clock ? (
              <span className="app-snapshot ct-small" title="Ages are measured against this fixed fixture date, so the demo is reproducible.">
                <Icon name="clock" size={14} />
                Snapshot {formatDate(clock)}
              </span>
            ) : null}
          </div>
          <div className="app-header__status">
            <span data-testid="connectivity" className="app-connectivity">
              <Badge tone={shell.online ? "neutral" : "warning"}>{shell.online ? "Works offline" : "You are offline — everything still works"}</Badge>
            </span>
          </div>
        </header>
        <main id="main" className="app-main" tabIndex={-1}>
          <div className="app-notices">
            <SourceStatus artifacts={artifacts} provenance={provenance} notes={shell.ready?.bundle.sourceNotes ?? []} claims={shell.ready?.bundle.claims ?? []} />
            {shell.settings.simulate !== "none" ? (
              <Banner tone="warning" title={`QA simulation is on: “${shell.settings.simulate}”.`} data-testid="qa-banner">
                Changes are not saved while a simulation is active. Turn it off in Settings.
              </Banner>
            ) : null}
            {shell.storageNotice ? (
              <Banner tone="warning" role="alert" title="Storage notice" actions={<Button size="sm" onClick={shell.dismissStorageNotice}>Dismiss</Button>} data-testid="storage-notice">
                {shell.storageNotice}
              </Banner>
            ) : null}
            {rejected > 0 ? (
              <Banner tone="warning" className="app-sourcebar app-sourcebar--warn" data-testid="adapter-banner" title={`${rejected} upstream record${rejected === 1 ? " was" : "s were"} rejected by the adapters.`}>
                Rejected records are never shown or repaired. See <NavLink to="/settings">Settings &rarr; Data sources</NavLink> for details.
              </Banner>
            ) : null}
          </div>
          <div className="app-content">
            {shell.status === "loading" ? (
              <LoadingState label="Loading synthetic fixtures…" />
            ) : shell.status === "error" && location.pathname !== "/settings" ? (
              <ErrorState title="The data could not be loaded." action={<Button variant="primary" onClick={shell.reload}>Retry</Button>}>
                <p>{shell.error}</p>
                <p>
                  Nothing was changed. If a QA simulation is active, turn it off in <NavLink to="/settings">Settings</NavLink>.
                </p>
              </ErrorState>
            ) : (
              <ErrorBoundary resetKey={location.pathname}>
                  <Routes>
                    <Route path="/" element={<OverviewPage />} />
                    <Route path="/jobs" element={<DiscoverJourney />} />
                    <Route path="/jobs/:jobId" element={<DiscoverJourney />} />
                    <Route path="/matching" element={<CompareJourney />} />
                    <Route path="/evidence" element={<EvidencePage />} />
                    <Route path="/resume" element={<ResumePage />} />
                    <Route path="/resume/tailor" element={<TailorJourney />} />
                    <Route path="/resume/tailor/:jobId" element={<TailorJourney />} />
                    <Route path="/applications" element={<TrackJourney />} />
                    <Route path="/applications/:appId" element={<TrackJourney />} />
                    <Route path="/experiments" element={<ExperimentsPage />} />
                    <Route path="/atlas" element={<AtlasJourney />} />
                    <Route path="/atlas/map" element={<AtlasJourney />} />
                    <Route path="/discover/:id?" element={<Redirect to="/jobs" />} />
                    <Route path="/tailor/:id?" element={<Redirect to="/resume/tailor" />} />
                    <Route path="/track/:id?" element={<Redirect to="/applications" />} />
                    <Route path="/simulator" element={<SimulatorPage />} />
                    <Route path="/settings" element={<SettingsPage />} />
                    <Route path="*" element={<NotFound />} />
                  </Routes>
              </ErrorBoundary>
            )}
          </div>
          <footer className="app-footer ct-small ct-muted">
            Offline synthetic build. Scores are relative heuristics, not hiring probabilities. Map distances are not fit.
          </footer>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AnnouncerProvider>
      <AppProvider>
        <HashRouter>
          <Shell />
        </HashRouter>
      </AppProvider>
    </AnnouncerProvider>
  );
}
