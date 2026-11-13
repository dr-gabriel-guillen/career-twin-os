/**
 * Tiny, dependency-free runtime validation used by every adapter.
 *
 * Why not Ajv at runtime: Ajv compiles validators with `new Function`, which the
 * production CSP (script-src 'self') forbids. Ajv is used in unit tests instead,
 * to prove these hand-written validators agree with the frozen JSON Schemas.
 */

export interface AdapterIssue {
  /** JSON-pointer-ish path, e.g. `[3].compensation.min`. */
  path: string;
  message: string;
}

export class Checker {
  readonly issues: AdapterIssue[];
  private readonly base: string;

  constructor(base = "", issues: AdapterIssue[] = []) {
    this.base = base;
    this.issues = issues;
  }

  /** Child checker that reports into the same issue list. */
  at(segment: string | number): Checker {
    const path =
      typeof segment === "number" ? `${this.base}[${segment}]` : this.base ? `${this.base}.${segment}` : segment;
    return new Checker(path, this.issues);
  }

  fail(message: string): false {
    this.issues.push({ path: this.base || "$", message });
    return false;
  }

  isObject(v: unknown): v is Record<string, unknown> {
    if (typeof v !== "object" || v === null || Array.isArray(v)) return this.fail("expected object");
    return true;
  }

  /** Reject keys outside `allowed` (mirrors `additionalProperties: false`). */
  noExtraKeys(v: Record<string, unknown>, allowed: readonly string[]): boolean {
    let ok = true;
    for (const k of Object.keys(v)) {
      if (!allowed.includes(k)) {
        this.at(k).fail("unknown property (contract has additionalProperties:false)");
        ok = false;
      }
    }
    return ok;
  }

  string(v: unknown, opts: { minLength?: number } = {}): v is string {
    if (typeof v !== "string") return this.fail("expected string");
    if (opts.minLength !== undefined && v.length < opts.minLength) {
      return this.fail(`expected at least ${opts.minLength} character(s)`);
    }
    return true;
  }

  number(v: unknown, opts: { min?: number; max?: number; integer?: boolean } = {}): v is number {
    if (typeof v !== "number" || !Number.isFinite(v)) return this.fail("expected finite number");
    if (opts.integer && !Number.isInteger(v)) return this.fail("expected integer");
    if (opts.min !== undefined && v < opts.min) return this.fail(`expected >= ${opts.min}`);
    if (opts.max !== undefined && v > opts.max) return this.fail(`expected <= ${opts.max}`);
    return true;
  }

  boolean(v: unknown): v is boolean {
    if (typeof v !== "boolean") return this.fail("expected boolean");
    return true;
  }

  oneOf<T extends string>(v: unknown, allowed: readonly T[]): v is T {
    if (typeof v !== "string" || !(allowed as readonly string[]).includes(v)) {
      return this.fail(`expected one of: ${allowed.join(", ")}`);
    }
    return true;
  }

  dateTime(v: unknown): v is string {
    if (typeof v !== "string") return this.fail("expected date-time string");
    if (!isRfc3339(v)) return this.fail("expected RFC 3339 date-time");
    return true;
  }

  stringArray(v: unknown, opts: { unique?: boolean; minItems?: number } = {}): v is string[] {
    if (!Array.isArray(v)) return this.fail("expected array");
    let ok = true;
    v.forEach((item, i) => {
      if (typeof item !== "string") {
        this.at(i).fail("expected string");
        ok = false;
      }
    });
    if (opts.minItems !== undefined && v.length < opts.minItems) {
      ok = this.fail(`expected at least ${opts.minItems} item(s)`);
    }
    if (ok && opts.unique && new Set(v).size !== v.length) ok = this.fail("expected unique items");
    return ok;
  }

  array(v: unknown): v is unknown[] {
    if (!Array.isArray(v)) return this.fail("expected array");
    return true;
  }
}

const RFC3339 = /^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$/;

/** Strict RFC 3339 date-time check. */
export function isRfc3339(v: string): boolean {
  const m = RFC3339.exec(v);
  if (!m) return false;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  const h = Number(m[4]);
  const mi = Number(m[5]);
  const s = Number(m[6]);
  if (mo < 1 || mo > 12 || h > 23 || mi > 59 || s > 59) return false;
  const offset = m[8]!;
  if (offset.length === 6 && (Number(offset.slice(1, 3)) > 23 || Number(offset.slice(4, 6)) > 59)) return false;
  const daysInMonth = new Date(Date.UTC(y, mo, 0)).getUTCDate();
  return d >= 1 && d <= daysInMonth;
}

export interface RejectedItem {
  index: number;
  /** Best-effort identifier of the rejected record, for display. Never trusted. */
  id: string | null;
  issues: AdapterIssue[];
}

export interface ListParse<T> {
  items: T[];
  rejected: RejectedItem[];
  /** Set when the payload as a whole is unusable (e.g. not an array). */
  fatal: AdapterIssue[];
}

export type ItemValidator<T> = (v: unknown, c: Checker) => v is T;

/**
 * Validate a list payload item by item. Invalid items are rejected and reported;
 * valid items are kept. An adapter never "repairs" upstream data.
 */
export function parseList<T>(payload: unknown, validateItem: ItemValidator<T>): ListParse<T> {
  if (!Array.isArray(payload)) {
    return { items: [], rejected: [], fatal: [{ path: "$", message: "expected an array payload" }] };
  }
  const items: T[] = [];
  const rejected: RejectedItem[] = [];
  payload.forEach((raw, index) => {
    const c = new Checker(`[${index}]`);
    const typed = validateItem(raw, c);
    if (typed && c.issues.length === 0) {
      items.push(raw);
    } else {
      const rawId = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>).id : null;
      rejected.push({ index, id: typeof rawId === "string" ? rawId.slice(0, 80) : null, issues: c.issues });
    }
  });
  return { items, rejected, fatal: [] };
}

/** Run a single-object validator. */
export function parseOne<T>(
  payload: unknown,
  validate: ItemValidator<T>,
): { ok: true; value: T } | { ok: false; issues: AdapterIssue[] } {
  const c = new Checker("");
  const typed = validate(payload, c);
  if (typed && c.issues.length === 0) return { ok: true, value: payload };
  return { ok: false, issues: c.issues };
}
