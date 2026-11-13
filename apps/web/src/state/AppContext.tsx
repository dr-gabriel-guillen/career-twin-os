import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useAnnounce } from "@careertwin/ui";
import { errorText, loadBundle, type DataBundle } from "@/adapters";
import { createArtifactSource, createFetchReader } from "@/adapters/artifactSource";
import { createFixtureSource } from "@/adapters/fixtureSource";
import {
  DEFAULT_SETTINGS,
  SETTINGS_KEY,
  STORAGE_KEY,
  loadWorkspace,
  parseSettings,
  parseWorkspace,
  reduce,
  saveWorkspace,
  seedWorkspace,
  type Action,
  type Settings,
  type Workspace,
} from "./workspace";

export interface AppReady {
  bundle: DataBundle;
  workspace: Workspace;
  /** Reference "now" for all age/freshness maths (the fixture clock in fixture mode). */
  clock: string;
  /** Apply an action. Returns a refusal message, or null when it was applied. */
  dispatch: (action: Action) => string | null;
  /** Erase everything stored by this app in this browser and start again. */
  wipe: (seed: "fixture" | "empty") => void;
}

interface Shell {
  status: "loading" | "ready" | "error";
  error: string | null;
  ready: AppReady | null;
  settings: Settings;
  setSettings: (s: Settings) => void;
  reload: () => void;
  online: boolean;
  storageNotice: string | null;
  dismissStorageNotice: () => void;
}

const ShellContext = createContext<Shell | null>(null);

/**
 * "?source=artifacts" (before the #) renders the artifacts of an integration run,
 * served next to the app under ./artifacts/. Used by lane E's ui.e2e stage.
 */
export function artifactMode(): boolean {
  try {
    return new URLSearchParams(window.location.search).get("source") === "artifacts";
  } catch {
    return false;
  }
}

function safeGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function AppProvider({ children }: { children: ReactNode }) {
  const announce = useAnnounce();
  const [settings, setSettingsState] = useState<Settings>(() => parseSettings(safeGet(SETTINGS_KEY)));
  const [status, setStatus] = useState<Shell["status"]>("loading");
  const [error, setError] = useState<string | null>(null);
  const [bundle, setBundle] = useState<DataBundle | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const workspaceRef = useRef<Workspace | null>(null);
  const [storageNotice, setStorageNotice] = useState<string | null>(null);
  const [online, setOnline] = useState(() => navigator.onLine);
  const [attempt, setAttempt] = useState(0);
  const artifacts = artifactMode();
  // Neither QA simulations nor integration runs read or write the user's stored workspace.
  const persistRef = useRef(settings.simulate === "none" && !artifacts);
  persistRef.current = settings.simulate === "none" && !artifacts;

  // Theme / motion preferences are reflected on <html> so CSS can react.
  useEffect(() => {
    const root = document.documentElement;
    if (settings.theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", settings.theme);
    if (settings.motion === "system") root.removeAttribute("data-motion");
    else root.setAttribute("data-motion", settings.motion);
  }, [settings.theme, settings.motion]);

  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  // Two tabs share one stored workspace. When another tab saves, adopt its version here so the
  // next change in this tab is appended to it instead of overwriting it.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY || !persistRef.current || e.newValue === null) return;
      const parsed = parseWorkspace(e.newValue);
      if (!parsed.workspace) return;
      workspaceRef.current = parsed.workspace;
      setWorkspace(parsed.workspace);
      setStorageNotice("This workspace was changed in another tab. The latest saved version is now shown here.");
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setError(null);
    loadBundle(artifacts ? createArtifactSource(createFetchReader()) : createFixtureSource(settings.simulate))
      .then((loaded) => {
        if (cancelled) return;
        const jobsReport = loaded.reports.find((r) => r.adapter.id === "jobs");
        if (jobsReport && jobsReport.fatal.length > 0) {
          setStatus("error");
          setError(jobsReport.fatal[0]?.message ?? "The job feed could not be loaded.");
          return;
        }
        // QA simulations never read or write the stored workspace.
        const qa = settings.simulate !== "none" || artifacts;
        const stored = qa ? { workspace: null, error: null } : loadWorkspace(window.localStorage);
        const seed = settings.simulate === "empty" ? "empty" : "fixture";
        const ws = stored.workspace ?? seedWorkspace(loaded, seed);
        if (stored.error) {
          setStorageNotice(`${stored.error} The stored data was ignored and a fresh synthetic workspace was started. Nothing was overwritten until you make a change.`);
        }
        workspaceRef.current = ws;
        setBundle(loaded);
        setWorkspace(ws);
        setStatus("ready");
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setStatus("error");
        setError(errorText(e));
      });
    return () => {
      cancelled = true;
    };
  }, [settings.simulate, attempt, artifacts]);

  const dispatch = useCallback(
    (action: Action): string | null => {
      const current = workspaceRef.current;
      if (!current) return "The workspace is not ready.";
      const result = reduce(current, action);
      if (result.refused) {
        announce(`Not done: ${result.refused}`);
        return result.refused;
      }
      workspaceRef.current = result.state;
      setWorkspace(result.state);
      if (persistRef.current) {
        const saveError = saveWorkspace(window.localStorage, result.state);
        if (saveError) setStorageNotice(saveError);
      }
      return null;
    },
    [announce],
  );

  const wipe = useCallback(
    (seed: "fixture" | "empty") => {
      try {
        window.localStorage.removeItem(STORAGE_KEY);
      } catch {
        /* storage unavailable: nothing persisted to erase */
      }
      if (!bundle) return;
      const fresh = seedWorkspace(bundle, seed);
      workspaceRef.current = fresh;
      setWorkspace(fresh);
      setStorageNotice(null);
      // An empty workspace is persisted so a reload does not silently bring the examples back.
      if (seed === "empty" && persistRef.current) saveWorkspace(window.localStorage, fresh);
      announce(seed === "empty" ? "Local data erased. The workspace is empty." : "Local data erased. Synthetic fixtures were restored.");
    },
    [announce, bundle],
  );

  const setSettings = useCallback((next: Settings) => {
    setSettingsState(next);
    try {
      window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
    } catch {
      /* preference simply will not persist */
    }
  }, []);

  const ready = useMemo<AppReady | null>(
    () => (status === "ready" && bundle && workspace ? { bundle, workspace, clock: bundle.clock, dispatch, wipe } : null),
    [status, bundle, workspace, dispatch, wipe],
  );

  const shell = useMemo<Shell>(
    () => ({
      status,
      error,
      ready,
      settings,
      setSettings,
      reload: () => setAttempt((n) => n + 1),
      online,
      storageNotice,
      dismissStorageNotice: () => setStorageNotice(null),
    }),
    [status, error, ready, settings, setSettings, online, storageNotice],
  );

  return <ShellContext.Provider value={shell}>{children}</ShellContext.Provider>;
}

export function useShell(): Shell {
  const s = useContext(ShellContext);
  if (!s) throw new Error("useShell must be used inside <AppProvider>");
  return s;
}

/** Data access for pages. Pages only render once the bundle is ready. */
export function useApp(): AppReady {
  const s = useShell();
  if (!s.ready) throw new Error("useApp used before data was ready");
  return s.ready;
}

export { DEFAULT_SETTINGS };
