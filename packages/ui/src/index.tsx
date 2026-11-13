/**
 * @careertwin/ui — small accessible component kit for CareerTwin OS (lane D).
 * No runtime dependencies beyond React. All components render text as text;
 * none accepts HTML strings.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

export type Tone = "neutral" | "info" | "success" | "warning" | "danger" | "synthetic";

const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

// ------------------------------------------------------------------ Button
interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "md" | "sm";
}
export function Button({ variant = "secondary", size = "md", className, type = "button", ...rest }: ButtonProps) {
  return <button type={type} className={cx("ct-btn", `ct-btn--${variant}`, size === "sm" && "ct-btn--sm", className)} {...rest} />;
}

// ------------------------------------------------------------------ Badge
export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={cx("ct-badge", `ct-badge--${tone}`)} title={title}>
      {children}
    </span>
  );
}

// ------------------------------------------------------------------ Card
export function Card({
  title,
  actions,
  children,
  as: Tag = "section",
  className,
  headingLevel = 2,
  ...rest
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  as?: "section" | "article" | "div" | "li";
  className?: string;
  headingLevel?: 2 | 3 | 4;
  "aria-label"?: string;
  "data-testid"?: string;
}) {
  const H = `h${headingLevel}` as "h2" | "h3" | "h4";
  return (
    <Tag className={cx("ct-card", className)} {...rest}>
      {(title || actions) && (
        <div className="ct-card__head">
          {title ? <H className="ct-card__title">{title}</H> : <span />}
          {actions ? <div className="ct-row">{actions}</div> : null}
        </div>
      )}
      {children}
    </Tag>
  );
}

// ------------------------------------------------------------------ Banner
export function Banner({
  tone = "info",
  title,
  children,
  role,
  actions,
  className,
  ...rest
}: {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  role?: "status" | "alert" | "note";
  actions?: ReactNode;
  className?: string;
  "data-testid"?: string;
}) {
  return (
    <div className={cx("ct-banner", `ct-banner--${tone}`, className)} role={role ?? "note"} {...rest}>
      <div className="ct-banner__body">
        {title ? <strong className="ct-banner__title">{title}</strong> : null}
        {children ? <div>{children}</div> : null}
      </div>
      {actions ? <div className="ct-row">{actions}</div> : null}
    </div>
  );
}

// ------------------------------------------------------------------ States
export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="ct-state" data-testid="empty-state">
      <p className="ct-state__title">{title}</p>
      {children ? <div className="ct-muted">{children}</div> : null}
      {action ? <div className="ct-state__action">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="ct-state ct-state--error" role="alert" data-testid="error-state">
      <p className="ct-state__title">{title}</p>
      {children ? <div>{children}</div> : null}
      {action ? <div className="ct-state__action">{action}</div> : null}
    </div>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="ct-state" role="status" aria-live="polite" data-testid="loading-state">
      <div className="ct-skeleton" aria-hidden="true" />
      <div className="ct-skeleton ct-skeleton--short" aria-hidden="true" />
      <p className="ct-muted">{label}</p>
    </div>
  );
}

// ------------------------------------------------------------------ Meter
/** Horizontal bar for a 0..1 value. `null` renders an explicit UNKNOWN state, never an empty bar. */
export function Meter({ value, label, tone = "info" }: { value: number | null; label: string; tone?: Tone }) {
  if (value === null) {
    return (
      <div className="ct-meter ct-meter--unknown" role="img" aria-label={`${label}: unknown`}>
        <span className="ct-meter__text">Unknown</span>
      </div>
    );
  }
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100);
  return (
    <div className="ct-meter" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
      <div className={cx("ct-meter__fill", `ct-meter__fill--${tone}`)} style={{ width: `${pct}%` }} />
    </div>
  );
}

// ------------------------------------------------------------------ Form fields
function FieldShell({ id, label, hint, error, children }: { id: string; label: ReactNode; hint?: ReactNode; error?: string | null; children: ReactNode }) {
  return (
    <div className={cx("ct-field", error && "ct-field--error")}>
      <label className="ct-field__label" htmlFor={id}>
        {label}
      </label>
      {children}
      {hint ? (
        <p className="ct-field__hint" id={`${id}-hint`}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p className="ct-field__error" id={`${id}-error`} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

const describedBy = (id: string, hint: unknown, error: unknown) =>
  [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(" ") || undefined;

interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
}
export function TextField({ label, hint, error, className, ...rest }: TextFieldProps) {
  const id = useId();
  return (
    <FieldShell id={id} label={label} hint={hint} error={error}>
      <input id={id} className={cx("ct-input", className)} aria-invalid={error ? true : undefined} aria-describedby={describedBy(id, hint, error)} {...rest} />
    </FieldShell>
  );
}

interface TextAreaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id"> {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
}
export function TextArea({ label, hint, error, className, rows = 3, ...rest }: TextAreaProps) {
  const id = useId();
  return (
    <FieldShell id={id} label={label} hint={hint} error={error}>
      <textarea id={id} rows={rows} className={cx("ct-input", className)} aria-invalid={error ? true : undefined} aria-describedby={describedBy(id, hint, error)} {...rest} />
    </FieldShell>
  );
}

interface SelectFieldProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "id"> {
  label: ReactNode;
  hint?: ReactNode;
  options: { value: string; label: string }[];
}
export function SelectField({ label, hint, options, className, ...rest }: SelectFieldProps) {
  const id = useId();
  return (
    <FieldShell id={id} label={label} hint={hint}>
      <select id={id} className={cx("ct-input", className)} aria-describedby={describedBy(id, hint, null)} {...rest}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </FieldShell>
  );
}

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "type"> {
  label: ReactNode;
  hint?: ReactNode;
}
export function Checkbox({ label, hint, ...rest }: CheckboxProps) {
  const id = useId();
  return (
    <div className="ct-check">
      <input id={id} type="checkbox" aria-describedby={hint ? `${id}-hint` : undefined} {...rest} />
      <div>
        <label htmlFor={id}>{label}</label>
        {hint ? (
          <p className="ct-field__hint" id={`${id}-hint`}>
            {hint}
          </p>
        ) : null}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Tabs
export interface TabItem {
  id: string;
  label: ReactNode;
}
/** WAI-ARIA tabs with roving tabindex and arrow-key navigation. */
export function Tabs({ tabs, active, onChange, label, idBase }: { tabs: TabItem[]; active: string; onChange: (id: string) => void; label: string; idBase: string }) {
  const refs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = tabs.findIndex((t) => t.id === active);
    let next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    if (next < 0) return;
    e.preventDefault();
    const t = tabs[next]!;
    onChange(t.id);
    refs.current.get(t.id)?.focus();
  };
  return (
    <div className="ct-tabs" role="tablist" aria-label={label} onKeyDown={onKeyDown}>
      {tabs.map((t) => (
        <button
          key={t.id}
          ref={(el) => {
            if (el) refs.current.set(t.id, el);
            else refs.current.delete(t.id);
          }}
          type="button"
          role="tab"
          id={`${idBase}-tab-${t.id}`}
          aria-selected={t.id === active}
          aria-controls={`${idBase}-panel-${t.id}`}
          tabIndex={t.id === active ? 0 : -1}
          className={cx("ct-tab", t.id === active && "ct-tab--active")}
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function TabPanel({ idBase, id, active, children }: { idBase: string; id: string; active: string; children: ReactNode }) {
  if (id !== active) return null;
  return (
    <div role="tabpanel" id={`${idBase}-panel-${id}`} aria-labelledby={`${idBase}-tab-${id}`} tabIndex={0} className="ct-tabpanel">
      {children}
    </div>
  );
}

// ------------------------------------------------------------------ Dialog
/**
 * Modal dialog on the native <dialog> element: the browser provides the focus
 * trap, Escape handling, inert background and focus return.
 */
export function Dialog({
  open,
  onClose,
  title,
  children,
  actions,
  tone,
  testId,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  actions?: ReactNode;
  tone?: "danger";
  testId?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className={cx("ct-dialog", tone === "danger" && "ct-dialog--danger")}
      aria-labelledby={titleId}
      data-testid={testId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClose={() => {
        if (open) onClose();
      }}
    >
      {open ? (
        <>
          <h2 id={titleId} className="ct-dialog__title">
            {title}
          </h2>
          <div className="ct-dialog__body">{children}</div>
          <div className="ct-dialog__actions">{actions ?? <Button onClick={onClose}>Close</Button>}</div>
        </>
      ) : null}
    </dialog>
  );
}

// ------------------------------------------------------------------ Table wrapper
/** Scrollable, keyboard-focusable region so wide tables never break small screens. */
export function TableRegion({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="ct-table-wrap" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}

// ------------------------------------------------------------------ Live announcer
const AnnounceContext = createContext<(message: string) => void>(() => undefined);

export function AnnouncerProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState("");
  const timer = useRef<number | undefined>(undefined);
  const announce = useCallback((text: string) => {
    // Clear first so repeating the same message is announced again.
    setMessage("");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMessage(text), 30);
  }, []);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const value = useMemo(() => announce, [announce]);
  return (
    <AnnounceContext.Provider value={value}>
      {children}
      <div className="ct-sr-only" role="status" aria-live="polite" aria-atomic="true" data-testid="announcer">
        {message}
      </div>
    </AnnounceContext.Provider>
  );
}

export const useAnnounce = () => useContext(AnnounceContext);

export function VisuallyHidden({ children }: { children: ReactNode }) {
  return <span className="ct-sr-only">{children}</span>;
}

/** Definition list rendered as label/value rows. */
export function Facts({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="ct-facts">
      {items.map((it) => (
        <div key={it.label} className="ct-facts__row">
          <dt>{it.label}</dt>
          <dd>{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}

// ------------------------------------------------------------------ Score bars
/**
 * One engine score on 0..100. `null` renders a hatched track and the word "unknown":
 * an unknown score is never drawn as zero. Near-term fit and strategic value use
 * different colours and are always shown as two bars, never merged into one.
 */
export function ScoreBar({ kind, value, label, testId }: { kind: "near" | "strat"; value: number | null; label?: string; testId?: string }) {
  const name = label ?? (kind === "near" ? "Near-term" : "Strategic");
  const v = value === null ? null : Math.max(0, Math.min(100, value));
  return (
    <div className={cx("ct-score", kind === "strat" && "ct-score--strat")} role="img" aria-label={v === null ? `${name}: unknown` : `${name}: ${value} of 100`} data-testid={testId}>
      <span className="ct-score__label" aria-hidden="true">{name}</span>
      <span className={cx("ct-score__track", v === null && "ct-score__track--unknown")} aria-hidden="true">
        {v === null ? null : <span className="ct-score__fill" style={{ display: "block", width: `${Math.max(2, v)}%` }} />}
      </span>
      <span className={cx("ct-score__value", v === null && "ct-score__value--unknown")} aria-hidden="true">{v === null ? "?" : Math.round(v)}</span>
    </div>
  );
}

/** The two scores together, stacked. Neither is a probability of being hired. */
export function ScorePair({ near, strategic }: { near: number | null; strategic: number | null }) {
  return (
    <div className="ct-scores">
      <ScoreBar kind="near" value={near} />
      <ScoreBar kind="strat" value={strategic} />
    </div>
  );
}

// ------------------------------------------------------------------ Icons
/** CareerTwin stroke icons on a 24px grid. Always decorative (aria-hidden); pair with visible text. */
const ICON_PATHS = {
  overview: "M4 5h16v15H4zM4 9h16M9 3v4M15 3v4",
  discover: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4",
  match: "M4 12h4l2-5 4 10 2-5h4",
  evidence: "M6 3h9l3 3v15H6zM9 11h6M9 15h6M9 7h3",
  resume: "M5 4h14v16H5zM8 8h8M8 12h8M8 16h5",
  applications: "M4 5h4v14H4zM10 5h4v9h-4zM16 5h4v6h-4z",
  experiments: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3M7.5 15h9",
  atlas: "M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2zM9 4v14M15 6v14",
  simulator: "M4 18c4 0 4-12 8-12s4 12 8 12M4 18h16",
  settings: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2",
  arrow: "M5 12h14M13 6l6 6-6 6",
  alert: "M12 4l9 16H3zM12 10v4M12 17v.5",
  check: "M5 12l4 4 10-10",
  cross: "M6 6l12 12M18 6L6 18",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2",
  bookmark: "M7 4h10v17l-5-4-5 4z",
  menu: "M4 7h16M4 12h16M4 17h16",
  offline: "M3 3l18 18M8.5 16.5a5 5 0 0 1 7 0M5 13a10 10 0 0 1 5-2.7M14 10.3a10 10 0 0 1 5 2.7M12 20h.01",
  online: "M5 13a10 10 0 0 1 14 0M8.5 16.5a5 5 0 0 1 7 0M2 9.5a15 15 0 0 1 20 0M12 20h.01",
  up: "M12 19V5M6 11l6-6 6 6",
  down: "M12 5v14M6 13l6 6 6-6",
  edit: "M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  back: "M19 12H5M11 6l-6 6 6 6",
  info: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v6M12 7.5v.5",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
} as const;
export type IconName = keyof typeof ICON_PATHS;
export function Icon({ name, size = 16, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg className={cx("ct-icon", className)} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}
