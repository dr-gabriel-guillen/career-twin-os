import { useState } from "react";
import { Badge, Banner, Button, Card, Dialog, SelectField, TableRegion, TextField, useAnnounce } from "@careertwin/ui";
import { Page } from "@/components/common";
import { formatDateTime } from "@/lib/format";
import { downloadText } from "@/lib/safety";
import { useShell } from "@/state/AppContext";
import { MOTIONS, SETTINGS_KEY, SIMULATIONS, STORAGE_KEY, THEMES, type Settings } from "@/state/workspace";

const CONNECTORS = [
  { name: "Greenhouse job boards", note: "Public ATS feed. Needs owner approval of the source and its terms." },
  { name: "Lever postings", note: "Public ATS feed. Needs owner approval of the source and its terms." },
  { name: "Ashby job boards", note: "Public ATS feed. Needs owner approval of the source and its terms." },
  { name: "LinkedIn data export import", note: "Only a file you export and choose yourself. Never scraping, never automated sign-in." },
  { name: "Sign in / accounts", note: "No accounts exist in this build. Everything stays in this browser." },
];

const GATES = [
  ["Submitting applications", "Never automatic. You apply yourself and record it."],
  ["Messaging recruiters or employers", "Never. The app has no outbound channel."],
  ["Paid or external AI services", "None are called. The build makes no network requests beyond loading its own files."],
  ["Telemetry and analytics", "None."],
  ["Real personal data", "Not accepted in this public, synthetic build."],
];

export default function SettingsPage() {
  const shell = useShell();
  const announce = useAnnounce();
  const ready = shell.ready;
  const [wipeOpen, setWipeOpen] = useState(false);
  const [wipeText, setWipeText] = useState("");
  const [wipeSeed, setWipeSeed] = useState<"fixture" | "empty">("fixture");
  const update = (patch: Partial<Settings>) => shell.setSettings({ ...shell.settings, ...patch });

  const exportAll = () => {
    if (!ready) return;
    const payload = {
      export_schema: "careertwin-workspace-export/1",
      exported_at: new Date().toISOString(),
      notice: "Synthetic demo workspace exported from this browser. Contains no real personal data unless you typed some in.",
      settings: shell.settings,
      workspace: ready.workspace,
    };
    downloadText("careertwin-workspace-export.json", JSON.stringify(payload, null, 2), "application/json");
    announce("Your workspace was exported to a file on this device.");
  };

  const closeWipe = () => {
    setWipeOpen(false);
    setWipeText("");
  };

  return (
    <Page title="Settings" lead="Appearance, where the data comes from, what is switched off, and full control over what this browser stores.">
      <Card title="Appearance and motion">
        <div className="app-toolbar">
          <SelectField label="Theme" value={shell.settings.theme} onChange={(e) => update({ theme: e.target.value as Settings["theme"] })} options={THEMES.map((t) => ({ value: t, label: t === "system" ? "Match my device" : t === "light" ? "Light" : "Dark" }))} data-testid="set-theme" />
          <SelectField label="Motion" hint="Reduce turns off animated transitions and loading shimmer." value={shell.settings.motion} onChange={(e) => update({ motion: e.target.value as Settings["motion"] })} options={MOTIONS.map((m) => ({ value: m, label: m === "system" ? "Match my device" : m === "reduce" ? "Reduce motion" : "Allow motion" }))} data-testid="set-motion" />
        </div>
      </Card>

      <Card title="Your data">
        <p>
          Everything you create here is stored only in this browser, under the keys <code>{STORAGE_KEY}</code> and <code>{SETTINGS_KEY}</code>. Nothing is uploaded. Clearing your browser data also removes it.
        </p>
        <div className="ct-row">
          <Button onClick={exportAll} disabled={!ready} data-testid="export-all">
            Export everything (JSON)
          </Button>
          <Button variant="danger" onClick={() => setWipeOpen(true)} disabled={!ready} data-testid="wipe-open">
            Erase local data…
          </Button>
        </div>
        {ready ? (
          <p className="ct-small ct-muted" data-testid="storage-summary">
            Stored now: {ready.workspace.claims.length} claims, {ready.workspace.resumeVariants.length} résumé variants, {ready.workspace.applicationEvents.length} application events, {ready.workspace.experiments.length} experiments, {ready.workspace.assignments.length} assignments, {ready.workspace.shortlist.length} shortlisted postings, {ready.workspace.reminders.length} reminders.
          </p>
        ) : null}
      </Card>

      <Card title="Data sources and adapters">
        <p>
          Source in use: <Badge tone="synthetic">{ready?.bundle.sourceLabel ?? "Offline synthetic fixtures"}</Badge>
          {ready?.bundle.manifest ? <> · snapshot {formatDateTime(ready.bundle.manifest.fixture_clock)} · generator {ready.bundle.manifest.generator.name} {ready.bundle.manifest.generator.version}</> : null}
        </p>
        <p className="ct-small ct-muted">
          The UI reads upstream data only through versioned adapters. “Frozen contract” means the shape is fixed in <code>/contracts</code>. “Proposed stand-in” means the owning lane has not published a schema yet, so a lane-D fixture stands in until it does.
        </p>
        {ready ? (
          <TableRegion label="Adapters">
            <table className="ct-table" data-testid="adapter-table">
              <thead>
                <tr>
                  <th scope="col">Adapter</th>
                  <th scope="col">Lane</th>
                  <th scope="col">Contract</th>
                  <th scope="col">Basis</th>
                  <th scope="col" className="ct-num">Accepted</th>
                  <th scope="col" className="ct-num">Rejected</th>
                </tr>
              </thead>
              <tbody>
                {ready.bundle.reports.map((r) => {
                  const problems = r.rejected.length + r.fatal.length + r.integrity.length;
                  return (
                    <tr key={r.adapter.id} data-testid={`adapter-${r.adapter.id}`}>
                      <th scope="row">
                        {r.adapter.id} <span className="ct-muted ct-small">v{r.adapter.version}</span>
                        <div className="ct-small ct-muted">{r.adapter.note}</div>
                        {problems > 0 ? (
                          <details>
                            <summary>
                              {problems} problem{problems === 1 ? "" : "s"}
                            </summary>
                            <ul className="ct-small">
                              {r.fatal.map((f, i) => (
                                <li key={`f${i}`}>Payload: {f.path} — {f.message}</li>
                              ))}
                              {r.rejected.map((x) => (
                                <li key={`r${x.index}`}>
                                  Record #{x.index + 1}{x.id ? ` (${x.id})` : ""}: {x.issues.map((i) => `${i.path} — ${i.message}`).join("; ")}
                                </li>
                              ))}
                              {r.integrity.map((m, i) => (
                                <li key={`i${i}`}>{m}</li>
                              ))}
                            </ul>
                          </details>
                        ) : null}
                      </th>
                      <td>{r.adapter.lane}</td>
                      <td className="ct-small">{r.adapter.contract}</td>
                      <td>
                        <Badge tone={r.adapter.basis === "frozen-contract" ? "success" : "warning"}>{r.adapter.basis === "frozen-contract" ? "Frozen contract" : "Proposed stand-in"}</Badge>
                      </td>
                      <td className="ct-num">{r.accepted}</td>
                      <td className="ct-num">{problems}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableRegion>
        ) : (
          <p className="ct-muted">Data is not loaded, so adapter results are not available.</p>
        )}
      </Card>

      <Card title="Connectors">
        <ul className="ct-list-plain ct-stack" data-testid="connectors">
          {CONNECTORS.map((c) => (
            <li key={c.name} className="ct-row ct-row--between">
              <span>
                <strong>{c.name}</strong>
                <span className="ct-muted ct-small"> — {c.note}</span>
              </span>
              <span className="ct-row">
                <Badge>Unavailable in this build</Badge>
                <Button size="sm" disabled aria-disabled="true">
                  Connect
                </Button>
              </span>
            </li>
          ))}
        </ul>
      </Card>

      <Card title="Safety gates">
        <TableRegion label="Safety gates">
          <table className="ct-table">
            <thead>
              <tr>
                <th scope="col">Capability</th>
                <th scope="col">State in this build</th>
              </tr>
            </thead>
            <tbody>
              {GATES.map(([k, v]) => (
                <tr key={k}>
                  <th scope="row">{k}</th>
                  <td>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableRegion>
      </Card>

      <Card title="QA: simulate a data state">
        <SelectField label="Simulation" hint="For testing loading, error and empty screens. While a simulation is on, your stored workspace is neither read nor changed." value={shell.settings.simulate} onChange={(e) => update({ simulate: e.target.value as Settings["simulate"] })} options={SIMULATIONS.map((s) => ({ value: s, label: s === "none" ? "Off (normal)" : s === "slow" ? "Slow load" : s === "error" ? "Load failure" : "Empty data" }))} data-testid="set-simulate" />
        {shell.status === "error" ? (
          <Banner tone="danger" title="Data failed to load." role="alert">
            {shell.error}
          </Banner>
        ) : null}
      </Card>

      <Dialog
        open={wipeOpen}
        onClose={closeWipe}
        title="Erase everything stored by CareerTwin in this browser?"
        tone="danger"
        testId="wipe-dialog"
        actions={
          <>
            <Button onClick={closeWipe}>Cancel</Button>
            <Button
              variant="danger"
              disabled={wipeText.trim().toUpperCase() !== "ERASE"}
              onClick={() => {
                ready?.wipe(wipeSeed);
                closeWipe();
              }}
              data-testid="wipe-confirm"
            >
              Erase local data
            </Button>
          </>
        }
      >
        <p>This removes your shortlist, claims, résumé variants, application events, reminders, experiments and scenario edits from this browser. It cannot be undone. Export first if you want a copy.</p>
        <SelectField label="Afterwards" value={wipeSeed} onChange={(e) => setWipeSeed(e.target.value as "fixture" | "empty")} options={[{ value: "fixture", label: "Restore the synthetic example data" }, { value: "empty", label: "Start with an empty workspace" }]} data-testid="wipe-seed" />
        <TextField label="Type ERASE to confirm" value={wipeText} onChange={(e) => setWipeText(e.target.value)} autoComplete="off" data-testid="wipe-text" />
      </Dialog>
    </Page>
  );
}
