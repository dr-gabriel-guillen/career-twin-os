"""Small, explicit mutation-testing harness (no third-party tool).

Each mutant is one hand-picked semantic change to lane A's source (a flipped comparison,
a removed guard, a changed constant). For every mutant the harness patches the file,
runs the test-suite in a subprocess and records whether the tests noticed ("killed").
Surviving mutants are real gaps in the tests and are listed in the report.

Run: ``python packages/data-matching/tools/mutation_test.py [--out FILE] [--only N]``.
The source tree is always restored, also on Ctrl+C.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parents[1]
SRC = PKG / "src" / "ct_matching"
CON = REPO / "connectors" / "ct_connectors"

# (file, original snippet, mutated snippet, what the mutant breaks)
MUTANTS: list[tuple[Path, str, str, str]] = [
    (SRC / "matching.py", 'if p["status"] == "expired":', 'if p["status"] == "expired-never":', "expired postings become rankable"),
    (SRC / "matching.py", 'out.append(_constraint("availability", "unknown" if allowed else "fail"', 'out.append(_constraint("availability", "unknown" if True else "fail"', "unknown availability always allowed"),
    (SRC / "matching.py", "if top < floor[\"amount\"]:", "if top > floor[\"amount\"]:", "compensation floor inverted"),
    (SRC / "matching.py", 'strict = prefs.get("require_known_compensation", False)', "strict = True", "unknown salary treated as failing"),
    (SRC / "matching.py", "if not dedup[\"is_canonical\"]:", "if False:", "duplicates ranked twice"),
    (SRC / "matching.py", "if fill is None or sig[name].get(\"na\"):", "if fill is None:", "no-preference signals widen interval"),
    (SRC / "matching.py", "num = sum(1 for s in required if s in have) + 0.5 * sum(1 for s in preferred if s in have)", "num = sum(1 for s in required if s in have) + 1.0 * sum(1 for s in preferred if s in have)", "preferred skills weighted as required"),
    (SRC / "matching.py", 'SCORE_TYPE = "relative_heuristic_not_offer_probability"', 'SCORE_TYPE = "offer_probability"', "score relabelled as probability"),
    (SRC / "matching.py", "ok = p[\"remote_policy\"] in prefs[\"remote\"]", "ok = True", "remote constraint disabled"),
    (SRC / "matching.py", "ok = bool(set(have) & set(prefs[\"jurisdictions\"]))", "ok = True", "jurisdiction constraint disabled"),
    (SRC / "matching.py", "if bad_flags and not prefs.get(\"allow_flagged_content\", False):", "if False:", "injection-flagged postings ranked"),
    (SRC / "normalize.py", "if cand[\"currency\"] is None:", "if False:", "ambiguous currency accepted"),
    (SRC / "normalize.py", "if cand[\"period\"] != \"year\":", "if False:", "hourly pay passed off as annual"),
    (SRC / "normalize.py", "if comparable and not _overlap(cand, other):", "if False:", "structured-vs-text salary conflict ignored"),
    (SRC / "normalize.py", "if any(i[\"min\"] is not None and i[\"max\"] is not None and i[\"min\"] > i[\"max\"] for i in stated):", "if False:", "inverted salary range accepted"),
    (SRC / "normalize.py", "if (cand[\"min\"] or 0) == 0 and (cand[\"max\"] or 0) == 0:", "if False:", "zero salary accepted"),
    (SRC / "normalize.py", "if as_of - fetched > timedelta(days=ttl_days):", "if False:", "stale captures stay active"),
    (SRC / "normalize.py", "if expires and expires <= as_of:", "if expires and expires <= as_of and False:", "source expiry ignored"),
    (SRC / "normalize.py", "if ext.get(\"listed\") is False:", "if False:", "delisted postings stay active"),
    (SRC / "normalize.py", "if last.upper() in _AMBIGUOUS_STATE and explicit != \"US\":", "if False:", "ambiguous 'CA' guessed as US"),
    (SRC / "normalize.py", "return (stated or from_loc or \"unknown\"), None", "return (stated or from_loc or \"remote\"), None", "unknown workplace policy guessed as remote"),
    (SRC / "normalize.py", 'for name in ("required", "unsectioned"):', 'for name in ("required", "unsectioned", "other"):', "benefits text creates requirements"),
    (SRC / "store.py", "if utc(at) < utc(latest[\"posting\"][\"fetched_at\"]):", "if False:", "stale capture overwrites newer state"),
    (SRC / "store.py", "if utc(at) <= utc(p[\"fetched_at\"]):", "if False:", "old snapshot revokes newer observation"),
    (SRC / "store.py", "if uid and head_uid and uid != head_uid:", "if False:", "distinct employers with same name merged"),
    (SRC / "store.py", "if _jaccard(tok, head_tok) >= DEDUP_THRESHOLD:", "if True:", "different roles with same title merged"),
    (SRC / "store.py", "ok = entry[\"seq\"] == idx and entry[\"prev\"] == prev and entry[\"hash\"] == self._digest(entry)", "ok = entry[\"seq\"] == idx and entry[\"prev\"] == prev", "ledger content edits undetected"),
    (SRC / "store.py", "if result.seen_source_ids or result.records:", "if True:", "empty snapshot mass-revokes"),
    (SRC / "vault.py", "needed = STRICT_KINDS if claim[\"claim_type\"] in STRICT_TYPES else VERIFYING_KINDS", "needed = VERIFYING_KINDS", "credentials supported by a work sample"),
    (SRC / "vault.py", "if any(e[\"kind\"] in needed and e[\"sha256\"] for e in live):", "if any(e[\"kind\"] in needed for e in live):", "evidence without hash supports claims"),
    (SRC / "vault.py", "if any(not c[\"resolved\"] for c in meta[\"conflicts\"]):", "if False:", "contradicted claims stay usable"),
    (SRC / "vault.py", "if claim[\"status\"] != \"supported\":\n                raise VaultError(\"not-supported\"", "if False:\n                raise VaultError(\"not-supported\"", "unsupported claims can be made public"),
    (SRC / "vault.py", "live = [self.evidence[e] for e in claim[\"evidence_ids\"] if not self.evidence[e][\"revoked\"]]", "live = [self.evidence[e] for e in claim[\"evidence_ids\"]]", "revoked evidence still supports"),
    (SRC / "vault.py", "elif self.is_expired(cid, as_of):", "elif False:", "expired claims exported"),
    (SRC / "vault.py", "elif mode == \"resume-draft\" and claim[\"visibility\"] == \"private\":", "elif False:", "private claims leak into resume draft"),
    (CON / "textsafe.py", "if parts.scheme.lower() not in (\"http\", \"https\"):", "if False:", "javascript:/file: URLs accepted"),
    (CON / "textsafe.py", "if not ip.is_global:", "if False:", "private/metadata IP URLs accepted"),
    (CON / "textsafe.py", "if parts.username is not None or parts.password is not None or \"@\" in parts.netloc:", "if False:", "credential-bearing URLs accepted"),
    (CON / "textsafe.py", "hidden = (tag in _SKIP_TAGS or \"hidden\" in amap", "hidden = (False or \"hidden\" in amap", "script/style text leaks into descriptions"),
    (CON / "textsafe.py", "or bool(_HIDDEN_STYLE.search(amap.get(\"style\", \"\")))", "or False", "display:none text kept"),
    (CON / "textsafe.py", "if detect_injection(text) or detect_injection(hidden_text):", "if detect_injection(text):", "hidden prompt injection not flagged"),
    (CON / "_common.py", "if rec.source_id in ids:", "if False:", "duplicate ids inside one payload accepted"),
    (CON / "_common.py", "if isinstance(value, bool) or not isinstance(value, (int, float)):\n        return None", "if isinstance(value, bool):\n        return None", "numeric strings coerced to pay"),
    (CON / "base.py", "if dt.tzinfo is None:\n        return None", "if dt.tzinfo is None:\n        dt = dt.replace(tzinfo=timezone.utc)", "naive timestamps assumed UTC"),
    # guards added in the second adversarial review cycle
    (SRC / "store.py", 'counts["revoked"] += self._late_revoke(env["posting"]["id"])', "pass", "late snapshot revocation disabled"),
    (SRC / "store.py", 'if latest["ext"].get("revoked") and utc(at) > utc(latest["ext"]["revoked_at"]):', 'if latest["ext"].get("revoked"):', "older capture re-lists revoked job"),
    (SRC / "store.py", "problem = store.state_problem()", "problem = None", "store state not bound to ledger"),
    (SRC / "vault.py", "problem = vault.state_problem()", "problem = None", "vault state not bound to ledger"),
    (SRC / "normalize.py", "if _NOT_PAY.search(context):", "if False:", "bonus text read as salary"),
    (CON / "_common.py", 'return found.pop() if len(found) == 1 else "unknown"', 'return sorted(found)[-1] if found else "unknown"', "ambiguous pay period resolved to annual"),
    (SRC / "e2e.py", 'if posting["status"] == "expired" and not ext.get("revoked"):', "if False:", "sidecar un-expires a posting"),
    (SRC / "e2e.py", 'elif status == "supported" and not self.verified:', "elif False:", "caller-asserted supported trusted"),
    (CON / "textsafe.py", 'or bool(set(amap.get("class", "").split()) & self.hidden_classes)', "or False", "class-hidden text kept"),
    (CON / "_common.py", "if text and FLAG_INJECTION not in sflags:", "if text:", "injection text accepted as a skill"),
]


def run_tests() -> tuple[bool, str]:
    proc = subprocess.run([sys.executable, "-m", "pytest", "packages/data-matching", "connectors", "-q", "-x", "-p", "no:cacheprovider"],
                          cwd=REPO, capture_output=True, text=True, timeout=900)
    tail = (proc.stdout.strip().splitlines() or [""])[-1]
    return proc.returncode == 0, tail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(PKG / "benchmarks" / "mutation_report.json"))
    parser.add_argument("--only", type=int, nargs="*")
    args = parser.parse_args()
    ok, tail = run_tests()
    if not ok:
        print("baseline test-suite is not green; aborting:", tail, file=sys.stderr)
        return 2
    results = []
    started = time.time()
    for idx, (path, original, mutated, meaning) in enumerate(MUTANTS):
        if args.only and idx not in args.only:
            continue
        source = path.read_bytes()
        text = source.decode("utf-8")
        if text.count(original) != 1:
            results.append({"id": idx, "file": path.name, "meaning": meaning, "outcome": "not-applied",
                            "detail": f"snippet occurs {text.count(original)} times"})
            continue
        try:
            path.write_bytes(text.replace(original, mutated).encode("utf-8"))
            passed, tail = run_tests()
        finally:
            path.write_bytes(source)
        results.append({"id": idx, "file": path.name, "meaning": meaning, "outcome": "survived" if passed else "killed",
                        "detail": tail[-160:]})
        print(f"[{idx:02d}] {results[-1]['outcome']:<9} {path.name:<13} {meaning}", flush=True)
    killed = sum(r["outcome"] == "killed" for r in results)
    applied = sum(r["outcome"] != "not-applied" for r in results)
    report = {"tool": "packages/data-matching/tools/mutation_test.py", "mutants_defined": len(MUTANTS), "applied": applied,
              "killed": killed, "survived": [r for r in results if r["outcome"] == "survived"],
              "not_applied": [r for r in results if r["outcome"] == "not-applied"],
              "kill_rate": round(killed / applied, 4) if applied else None, "seconds": round(time.time() - started, 1),
              "results": results,
              "note": "Hand-picked semantic mutants over safety-relevant branches; not exhaustive operator coverage."}
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: report[k] for k in ("applied", "killed", "kill_rate", "seconds")}))
    ok, tail = run_tests()
    print("post-run baseline:", "green" if ok else "RED", tail)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
