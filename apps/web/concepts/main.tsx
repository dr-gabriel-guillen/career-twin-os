/**
 * Design-concept harness (not the product). Renders three visual directions over the SAME
 * validated synthetic bundle the product loads. URL: /concepts/?d=instrument|ledger|terrain&s=dashboard|discover|atlas&theme=light|dark
 */
import { StrictMode, useEffect, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { loadBundle } from "@/adapters";
import { createFixtureSource } from "@/adapters/fixtureSource";
import { buildModel, type ConceptModel } from "./model";
import { Instrument, type Screen } from "./instrument";
import { Ledger } from "./ledger";
import { Terrain } from "./terrain";

const params = new URLSearchParams(location.search);
const direction = params.get("d") ?? "index";
const screen = (params.get("s") ?? "dashboard") as Screen;
const theme = params.get("theme") ?? (direction === "terrain" ? "dark" : "light");

function Index() {
  const dirs = [["instrument", "A · Instrument"], ["ledger", "B · Ledger"], ["terrain", "C · Terrain"]];
  const screens: Screen[] = ["dashboard", "discover", "atlas"];
  return (
    <main style={{ font: "15px system-ui", padding: 32, maxWidth: 720 }}>
      <h1>CareerTwin design concepts</h1>
      <p>Three directions, rendered over the product’s own synthetic fixture bundle. Not the product.</p>
      {dirs.map(([d, l]) => (
        <section key={d}><h2>{l}</h2><ul>{screens.map((s) => <li key={s}><a href={`?d=${d}&s=${s}`}>{s}</a> · <a href={`?d=${d}&s=${s}&theme=dark`}>dark</a></li>)}</ul></section>
      ))}
    </main>
  );
}

function App() {
  const [model, setModel] = useState<ConceptModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    loadBundle(createFixtureSource()).then((b) => setModel(buildModel(b)), (e) => setError(String(e)));
  }, []);
  if (direction === "index") return <Index />;
  if (error) return <p role="alert">{error}</p>;
  if (!model) return <p aria-busy="true">Loading synthetic bundle…</p>;
  let view: ReactNode = null;
  if (direction === "instrument") view = <Instrument model={model} screen={screen} />;
  if (direction === "ledger") view = <Ledger model={model} screen={screen} />;
  if (direction === "terrain") view = <Terrain model={model} screen={screen} />;
  return <div data-ready="true">{view}</div>;
}

document.documentElement.dataset.theme = theme;
createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
