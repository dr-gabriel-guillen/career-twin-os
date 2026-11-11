"""Command line interface. All output is deterministic JSON on stdout (sorted keys)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ct_connectors import ConnectorError

from . import corpus, evalbench
from .service import RequestError, Service, vault_from_profile
from .store import JobStore
from .vault import VaultError

EXIT_OK, EXIT_USAGE, EXIT_DATA = 0, 2, 3


def _emit(doc: Any, out: str | None = None) -> None:
    text = json.dumps(doc, sort_keys=True, ensure_ascii=False, indent=1) + "\n"
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.buffer.write(text.encode("utf-8"))


def _read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RequestError("file-not-found", f"no such file: {path}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RequestError("malformed-json", f"{path}: {exc}") from exc


def _service(args: argparse.Namespace, need_profile: bool = False) -> Service:
    store = JobStore.load(args.store) if getattr(args, "store", None) and Path(args.store).exists() else JobStore()
    vault = prefs = None
    profile_path = getattr(args, "profile", None)
    if profile_path == "synthetic":
        vault, prefs = vault_from_profile(corpus.SYNTHETIC_PROFILE, args.as_of)
    elif profile_path:
        vault, prefs = vault_from_profile(_read_json(profile_path), args.as_of)
    elif need_profile:
        raise RequestError("no-profile", "--profile FILE (or --profile synthetic) is required")
    return Service(store, vault, prefs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ct-matching", description="CareerTwin lane A: offline job data, evidence and matching.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("corpus", help="generate the seeded fictional corpus bundle")
    p.add_argument("--seed", type=int, default=corpus.DEFAULT_SEED)
    p.add_argument("--n", type=int, default=180)
    p.add_argument("--out")
    p.add_argument("--profile-out", help="also write the synthetic candidate profile here")

    p = sub.add_parser("import", help="import capture envelopes / bundles into a store file")
    p.add_argument("--store", required=True)
    p.add_argument("files", nargs="+")

    p = sub.add_parser("query", help="list normalized jobs")
    p.add_argument("--store", required=True)
    p.add_argument("--as-of", required=True)
    for name in ("status", "source", "family", "remote", "q", "retriever", "limit", "offset"):
        p.add_argument("--" + name)
    p.add_argument("--include-duplicates", action="store_true")

    p = sub.add_parser("rank", help="rank jobs for a candidate profile with explanations")
    p.add_argument("--store", required=True)
    p.add_argument("--profile", required=True, help="profile JSON path, or 'synthetic'")
    p.add_argument("--as-of", required=True)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--out")

    p = sub.add_parser("provenance", help="version chain and ledger events for one job")
    p.add_argument("--store", required=True)
    p.add_argument("job_id")

    p = sub.add_parser("vault-export", help="export contract ProfileClaims under an export gate")
    p.add_argument("--profile", required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--mode", default="internal-matching")

    p = sub.add_parser("skill-vectors", help="representation export for lane C")
    p.add_argument("--store", required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--profile")

    p = sub.add_parser("bench", help="frozen synthetic benchmarks")
    p.add_argument("kind", choices=("ranking", "retrieval", "perf"))
    p.add_argument("--seed", type=int, default=corpus.DEFAULT_SEED)
    p.add_argument("--n", type=int, default=180)

    p = sub.add_parser("serve", help="loopback-only HTTP API")
    p.add_argument("--store")
    p.add_argument("--profile")
    p.add_argument("--as-of", default=corpus.AS_OF)
    p.add_argument("--port", type=int, default=8765)

    p = sub.add_parser("demo", help="generate → import → rank, print a compact shortlist")
    p.add_argument("--seed", type=int, default=corpus.DEFAULT_SEED)
    p.add_argument("--n", type=int, default=180)
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "corpus":
            _emit(corpus.generate(args.seed, args.n), args.out)
            if args.profile_out:
                _emit(corpus.SYNTHETIC_PROFILE, args.profile_out)
        elif args.command == "import":
            service = _service(args)
            report = None
            for path in args.files:
                report = service.import_doc(_read_json(path))
            service.store.save(args.store)
            _emit(report)
        elif args.command == "query":
            params = {k: v for k, v in (("as_of", args.as_of), ("status", args.status), ("source", args.source),
                                        ("family", args.family), ("remote", args.remote), ("q", args.q),
                                        ("retriever", args.retriever), ("limit", args.limit), ("offset", args.offset))
                      if v is not None}
            if args.include_duplicates:
                params["include_duplicates"] = "true"
            _emit(_service(args).query(params))
        elif args.command == "rank":
            _emit(_service(args, need_profile=True).rank({"as_of": args.as_of, "top_n": args.top}), args.out)
        elif args.command == "provenance":
            _emit(_service(args).provenance(args.job_id))
        elif args.command == "vault-export":
            vault, _ = vault_from_profile(corpus.SYNTHETIC_PROFILE if args.profile == "synthetic" else _read_json(args.profile), args.as_of)
            _emit(vault.export(args.mode, args.as_of))
        elif args.command == "skill-vectors":
            _emit(_service(args).skill_vectors(args.as_of))
        elif args.command == "bench":
            fn = {"ranking": lambda: evalbench.ranking_benchmark(args.seed, args.n),
                  "retrieval": lambda: evalbench.retrieval_benchmark(args.seed, args.n),
                  "perf": lambda: evalbench.perf_benchmark(args.n, args.seed)}[args.kind]
            _emit(fn())
        elif args.command == "serve":
            from .httpapi import make_server
            server = make_server(_service(args), args.port)
            print(f"ct-matching listening on http://127.0.0.1:{server.server_address[1]} (loopback only; Ctrl+C to stop)",
                  file=sys.stderr)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        elif args.command == "demo":
            service = Service()
            report = service.import_doc(corpus.generate(args.seed, args.n))
            vault, prefs = vault_from_profile(corpus.SYNTHETIC_PROFILE, corpus.AS_OF)
            service.vault, service.preferences = vault, prefs
            result = service.rank({"as_of": corpus.AS_OF, "top_n": 10})
            _emit({"import": {"counts": report["counts"], "rejected_by_class": report["rejected_by_class"]},
                   "corpus": result["corpus"], "score_type": result["score_type"], "result_hash": result["result_hash"],
                   "shortlist": [{"rank": s["rank"], "score": s["score"], "interval": [s["score_interval"]["low"], s["score_interval"]["high"]],
                                  "title": s["title"], "organization": s["organization"],
                                  "missing_skills": s["missing_skills"], "compensation": s["compensation"]["state"],
                                  "availability": s["availability"]["status"]} for s in result["shortlist"]],
                   "disclaimers": result["disclaimers"]})
    except (RequestError, ConnectorError, VaultError) as exc:
        code = getattr(exc, "code", None) or getattr(exc, "error_class", "error")
        sys.stderr.write(json.dumps({"error": {"code": code, "message": str(exc)}}) + "\n")
        return EXIT_DATA
    return EXIT_OK


def main() -> None:
    raise SystemExit(run())
