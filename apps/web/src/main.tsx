import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/instrument-sans";
import "@fontsource-variable/source-serif-4";
import "../../../packages/ui/src/styles.css";
import "./app.css";
import App from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root element");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// The app must keep working offline after its first load. Browsers fetch web fonts lazily, the
// first time a face is used, so a screen visited later (the serif résumé view) could need the
// network. Load both self-hosted faces up front; failure is harmless (system fonts are the fallback).
if (typeof document !== "undefined" && document.fonts) {
  for (const face of ['400 1em "Instrument Sans Variable"', '400 1em "Source Serif 4 Variable"']) {
    document.fonts.load(face).catch(() => undefined);
  }
}
