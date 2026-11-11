import { Component, lazy, Suspense, useEffect, useState, type ErrorInfo, type ReactNode } from "react";
import { HashRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { AnnouncerProvider, Badge, Banner, Button, ErrorState, LoadingState } from "@careertwin/ui";
import { totalRejected } from "@/adapters";
import { Page } from "@/components/common";
import { AppProvider, useShell } from "@/state/AppContext";

const OverviewPage = lazy(() => import("@/pages/OverviewPage"));
const JobsPage = lazy(() => import("@/pages/JobsPage"));
const MatchingPage = lazy(() => import("@/pages/MatchingPage"));
const EvidencePage = lazy(() => import("@/pages/EvidencePage"));
const ResumePage = lazy(() => import("@/pages/ResumePage"));
const ApplicationsPage = lazy(() => import("@/pages/ApplicationsPage"));
const ExperimentsPage = lazy(() => import("@/pages/ExperimentsPage"));
const AtlasPage = lazy(() => import("@/pages/AtlasPage"));
const SimulatorPage = lazy(() => import("@/pages/SimulatorPage"));
const SettingsPage = lazy(() => import("@/pages/SettingsPage"));

export const NAV = [
  { to: "/", label: "Overview" },
  { to: "/jobs", label: "Discover Jobs" },
  { to: "/matching", label: "Matching" },
  { to: "/evidence", label: "Profile Evidence" },
  { to: "/resume", label: "Resume Studio" },
  { to: "/applications", label: "Applications" },
  { to: "/experiments", label: "Experiments" },
  { to: "/atlas", label: "Career Atlas" },
  { to: "/simulator", label: "Career Simulator" },
  { to: "/settings", label: "Settings" },
] as const;

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

function NotFound() {
  return (
    <Page title="Page not found">
      <ErrorState title="There is no screen at this address.">
        <p>Use the navigation to pick a section.</p>
      </ErrorState>
    </Page>
  );
}

function Shell() {
  const shell = useShell();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  useEffect(() => setNavOpen(false), [location.pathname]);
  const rejected = shell.ready ? totalRejected(shell.ready.bundle) : 0;

  return (
    <div className="app">
      <a className="app-skip" href="#main" onClick={(e) => { e.preventDefault(); document.getElementById("main")?.focus(); }}>
        Skip to main content
      </a>
      <header className="app-header">
        <div className="ct-row">
          <Button className="app-navtoggle" aria-expanded={navOpen} aria-controls="app-nav" onClick={() => setNavOpen((o) => !o)}>
            Menu
          </Button>
          <span className="app-brand">CareerTwin OS</span>
        </div>
        <div className="ct-row">
          <Badge tone="synthetic">Synthetic demo</Badge>
          <span data-testid="connectivity">
            <Badge tone={shell.online ? "neutral" : "warning"}>{shell.online ? "Works offline" : "You are offline — everything still works"}</Badge>
          </span>
        </div>
      </header>
      <div className="app-body">
        <nav id="app-nav" className={navOpen ? "app-nav app-nav--open" : "app-nav"} aria-label="Primary">
          <ul className="ct-list-plain">
            {NAV.map((n) => (
              <li key={n.to}>
                <NavLink to={n.to} end={n.to === "/"} className={({ isActive }) => (isActive ? "app-navlink app-navlink--active" : "app-navlink")}>
                  {n.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <main id="main" className="app-main" tabIndex={-1}>
          <div className="ct-stack">
            <Banner tone="synthetic" title="Synthetic fixture mode." data-testid="synthetic-banner">
              Every employer, posting, candidate, event and outcome here is fictional. Nothing is sent anywhere, no application is
              ever submitted for you, and no live job source is connected.
            </Banner>
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
              <Banner tone="warning" data-testid="adapter-banner" title={`${rejected} upstream record${rejected === 1 ? " was" : "s were"} rejected by the adapters.`}>
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
                <Suspense fallback={<LoadingState />}>
                  <Routes>
                    <Route path="/" element={<OverviewPage />} />
                    <Route path="/jobs" element={<JobsPage />} />
                    <Route path="/matching" element={<MatchingPage />} />
                    <Route path="/evidence" element={<EvidencePage />} />
                    <Route path="/resume" element={<ResumePage />} />
                    <Route path="/applications" element={<ApplicationsPage />} />
                    <Route path="/experiments" element={<ExperimentsPage />} />
                    <Route path="/atlas" element={<AtlasPage />} />
                    <Route path="/simulator" element={<SimulatorPage />} />
                    <Route path="/settings" element={<SettingsPage />} />
                    <Route path="*" element={<NotFound />} />
                  </Routes>
                </Suspense>
              </ErrorBoundary>
            )}
          </div>
          <footer className="app-footer ct-small ct-muted">
            CareerTwin OS &middot; lane D product UI &middot; offline synthetic build. Scores are relative heuristics, not hiring
            probabilities. Map distances are not fit.
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
