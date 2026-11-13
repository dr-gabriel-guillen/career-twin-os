import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import "@fontsource-variable/instrument-sans";
import "@fontsource-variable/source-serif-4";
import "../../../packages/ui/src/styles.css";
import "../src/app.css";
import "./proto.css";
import { AnnouncerProvider, Badge, ErrorState, Icon, LoadingState, type IconName } from "@careertwin/ui";
import { AppProvider, artifactMode, useShell } from "@/state/AppContext";
import { formatDate } from "@/lib/format";
import DiscoverJourney from "./Discover";
import TailorJourney from "./Tailor";
import TrackJourney from "./Track";
import AtlasConcept from "./Atlas";

/**
 * Journey prototypes for owner review. They run on the same validated bundle, workspace reducer and
 * safety gates as the product. In pipeline-artifact mode every change stays in this tab only.
 */
const NAV: { to: string; label: string; icon: IconName; hint: string }[] = [
  { to: "/discover", label: "Discover", icon: "discover", hint: "Find and decide" },
  { to: "/tailor", label: "Tailor résumé", icon: "resume", hint: "Adapt, review, approve" },
  { to: "/track", label: "Track", icon: "applications", hint: "Next action, follow-up" },
  { to: "/atlas", label: "Career paths", icon: "atlas", hint: "Transitions concept" },
];

function Shell() {
  const shell = useShell();
  const location = useLocation();
  const artifacts = artifactMode();
  const clock = shell.ready?.bundle.clock;
  return (
    <div className="pt-app" data-source={artifacts ? "artifacts" : "fixtures"} data-ready={shell.status === "ready" ? "" : undefined}>
      <a className="app-skip" href="#pt-main" onClick={(e) => { e.preventDefault(); document.getElementById("pt-main")?.focus(); }}>
        Skip to main content
      </a>
      <header className="pt-top">
        <div className="pt-top__brand">
          <span className="app-mark" aria-hidden="true" />
          <span>CareerTwin</span>
          <Badge tone="info">Prototype</Badge>
        </div>
        <nav className="pt-nav" aria-label="Journeys">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive || location.pathname.startsWith(n.to) ? "pt-nav__link pt-nav__link--on" : "pt-nav__link")}>
              <Icon name={n.icon} size={16} />
              <span className="pt-nav__label">{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="pt-top__status">
          {clock ? <span className="pt-top__clock">Snapshot {formatDate(clock)}</span> : null}
          <a className="pt-top__exit" href={`./${artifacts ? "?source=artifacts" : ""}#/`}>
            Current app
          </a>
        </div>
      </header>
      <p className="pt-source" role="note" data-testid={artifacts ? "artifact-banner" : "synthetic-banner"}>
        <strong>{artifacts ? "Pipeline artifacts (synthetic data only)." : "Synthetic fixtures."}</strong>{" "}
        {artifacts
          ? "Real output of the A/B/C pipeline run. Changes you make here stay in this tab and are never saved or sent."
          : "Every employer, posting and outcome is fictional. Changes are kept in this browser only."}{" "}
        No live job source, AI writing or application submission exists in this build.
      </p>
      <main id="pt-main" className="pt-main" tabIndex={-1}>
        {shell.status === "loading" ? (
          <LoadingState label="Loading synthetic data…" />
        ) : shell.status === "error" ? (
          <ErrorState title="The data could not be loaded.">
            <p>{shell.error}</p>
          </ErrorState>
        ) : (
          <Routes>
            <Route path="/" element={<Navigate to="/discover" replace />} />
            <Route path="/discover" element={<DiscoverJourney />} />
            <Route path="/discover/:jobId" element={<DiscoverJourney />} />
            <Route path="/tailor" element={<TailorJourney />} />
            <Route path="/tailor/:jobId" element={<TailorJourney />} />
            <Route path="/track" element={<TrackJourney />} />
            <Route path="/track/:appId" element={<TrackJourney />} />
            <Route path="/atlas" element={<AtlasConcept />} />
            <Route path="*" element={<Navigate to="/discover" replace />} />
          </Routes>
        )}
      </main>
    </div>
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");
createRoot(root).render(
  <StrictMode>
    <AnnouncerProvider>
      <AppProvider>
        <HashRouter>
          <Shell />
        </HashRouter>
      </AppProvider>
    </AnnouncerProvider>
  </StrictMode>,
);
