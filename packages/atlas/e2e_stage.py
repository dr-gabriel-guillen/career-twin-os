"""Lane C stages for lane E's integration runner (see integration-manifest.json). Offline, synthetic.

    python packages/atlas/e2e_stage.py project  --vectors DIR/vectors.json --out-dir DIR
    python packages/atlas/e2e_stage.py scenario --artifacts DIR

``project`` reads E's generic vectors bundle ``{"vocabulary": [...], "vectors": [{"ref", "entity_id",
"values"}]}`` and writes ``atlas_points.json`` (frozen AtlasPoint[], one per input vector,
``source_vector_ref`` = the input ``ref``) and ``atlas_diagnostics.json``. If ``jobs.json`` sits next to
the vectors, role families and titles are joined on ``entity_id``.

``scenario`` reads ``claims.json``, ``jobs.json`` and ``pin.json`` and writes ``scenarios.json``
(frozen TransitionScenario[]). The pinned job's ``role_family`` is the target role. Because E's
pipeline has no user-entered plan, the stage builds a DEMONSTRATION plan from the gap analysis
(study-acquirable core requirements with no evidence on file, portfolio projects, placeholder
hours) and says so in ``assumptions``. Both stages compute from their inputs; neither is a mock.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from careertwin_atlas.adapters import vocabulary_from_names  # noqa: E402
from careertwin_atlas.api import build_atlas_bundle  # noqa: E402
from careertwin_atlas.diagnostics import knn_sets  # noqa: E402
from careertwin_atlas.engine import AtlasEngine  # noqa: E402
from careertwin_atlas.errors import AtlasError, InvalidInputError  # noqa: E402
from careertwin_atlas.features import (STATE_NOT_STATED, STATE_STATED, FeatureVector, claims_to_vector,  # noqa: E402
                                       normalize_family, sanitize_label)
from careertwin_atlas.gaps import analyze_gap  # noqa: E402
from careertwin_atlas.simulator import simulate_transition  # noqa: E402
from careertwin_atlas.vocabulary import ACQ_STUDY, default_vocabulary  # noqa: E402

K = 10
NEIGHBOUR_CLAIMS = 5
MAX_INPUT_BYTES = 50_000_000


def _read(path: Path):
    if not path.is_file():
        raise InvalidInputError(f"missing input file: {path.name}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise InvalidInputError(f"{path.name} is larger than {MAX_INPUT_BYTES} bytes")
    try:
        return json.loads(path.read_text(encoding="utf-8"),
                          parse_constant=lambda c: (_ for _ in ()).throw(InvalidInputError(f"non-finite number {c}")))
    except json.JSONDecodeError as exc:
        raise InvalidInputError(f"{path.name}: malformed JSON ({exc})") from exc


def _write(path: Path, doc) -> None:
    path.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n",
                    encoding="utf-8", newline="\n")


def project(vectors_path: Path, out_dir: Path, seed: int = 0) -> dict:
    bundle = _read(vectors_path)
    if not isinstance(bundle, dict) or not isinstance(bundle.get("vectors"), list):
        raise InvalidInputError("vectors.json must be an object with 'vocabulary' and 'vectors'")
    vocab, notes = vocabulary_from_names(bundle.get("vocabulary"), "lane-e-vectors", strict=False)
    jobs = {}
    jobs_path = vectors_path.parent / "jobs.json"
    if jobs_path.is_file():
        raw = _read(jobs_path)
        if not isinstance(raw, list):
            raise InvalidInputError("jobs.json must be a list of JobPosting objects")
        jobs = {j["id"]: j for j in raw if isinstance(j, dict) and isinstance(j.get("id"), str)}
    d = len(vocab.features)
    vectors, seen = [], set()
    for pos, item in enumerate(bundle["vectors"]):
        if not isinstance(item, dict):
            raise InvalidInputError(f"vectors[{pos}] must be an object")
        ref, eid, values = item.get("ref"), item.get("entity_id"), item.get("values")
        if not (isinstance(ref, str) and ref and isinstance(eid, str) and eid):
            raise InvalidInputError(f"vectors[{pos}]: ref and entity_id must be non-empty strings")
        if eid in seen:
            raise InvalidInputError(f"vectors[{pos}]: duplicate entity_id {eid!r}")
        seen.add(eid)
        if (not isinstance(values, list) or len(values) != d
                or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in values)):
            raise InvalidInputError(f"vectors[{pos}]: values must be {d} numbers")
        arr = np.asarray(values, dtype=np.float64)
        if not np.all(np.isfinite(arr)) or arr.min() < 0.0 or arr.max() > 1.0:
            raise InvalidInputError(f"vectors[{pos}]: values must be finite and within [0, 1]")
        job = jobs.get(eid, {})
        states = np.where(arr != 0, STATE_STATED, STATE_NOT_STATED).astype(np.int8)
        vectors.append(FeatureVector(
            entity_id=eid, kind="job", label=sanitize_label(job.get("title"), eid), values=arr, states=states,
            vocab_digest=vocab.digest(), feature_names=vocab.feature_names,
            meta={"role_family": normalize_family(job.get("role_family")), "source_vector_ref": ref,
                  "source": job.get("source", "unknown"), "fetched_at": job.get("fetched_at"),
                  "status": job.get("status", "unknown"), "min_qualifications": (), "n_requirements": 0,
                  "n_unmapped_requirements": 0, "compensation_mid": None}))
    engine = AtlasEngine.from_vectors(vectors, vocab, seed=seed, n_boot=20)
    atlas = build_atlas_bundle(engine, None, k=K, seed=seed, include_clusters=False)
    points = [p for p in atlas["points"] if p["representation_type"] == "job"]
    fitted_refs = [v.vector_ref for v in engine.matrix.vectors]
    nn = knn_sets(engine.Z, min(NEIGHBOUR_CLAIMS, len(engine.matrix) - 1)) if len(engine.matrix) > 1 else None
    diag = dict(atlas["diagnostics"])
    diag.update({
        "projection_id": atlas["projection"]["projection_id"],
        "method": "pca",
        "preprocessing": "standardize" if engine.model.standardize else "center",
        "k": diag["k_neighbours"],
        "fit_refs": fitted_refs,
        "explained_variance_ratio": [float(x) for x in engine.model.explained_variance_ratio],
        "neighbor_claims_space": "full feature space (never 2-D map coordinates)",
        "neighbor_claims": [] if nn is None else [
            {"entity_id": engine.matrix.vectors[i].entity_id,
             "neighbors": [engine.matrix.vectors[j].entity_id for j in nn[i]]} for i in range(len(engine.matrix))],
        "neighborhood_quality_definition": "share of a point's k nearest 2-D neighbours that lie within its k-th "
                                           "nearest full-space distance (tie-inclusive), k = k_neighbours",
        "information_loss": atlas["information_loss"],
        "flags_bundle": atlas["flags"],
        "unplaced": atlas["unplaced"],
        "notes": notes,
        "warnings": atlas["warnings"],
    })
    out_dir.mkdir(parents=True, exist_ok=True)
    # serialise both before writing either, so a failure never leaves a half-written stage
    texts = {name: json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"
             for name, doc in (("atlas_points.json", points), ("atlas_diagnostics.json", diag))}
    for name, text in texts.items():
        (out_dir / name).write_text(text, encoding="utf-8", newline="\n")
    return {"points": len(points), "unplaced": len(atlas["unplaced"]), "trustworthiness": diag["trustworthiness"]}


def scenario(artifacts: Path, seed: int = 0, hours_per_week: float = 6.0, horizon: int = 12) -> dict:
    claims = _read(artifacts / "claims.json")
    jobs = _read(artifacts / "jobs.json")
    pin = _read(artifacts / "pin.json")
    if not isinstance(jobs, list):
        raise InvalidInputError("jobs.json must be a list of JobPosting objects")
    if not isinstance(claims, list) or not claims or not isinstance(claims[0], dict):
        raise InvalidInputError("claims.json must be a non-empty list of ProfileClaim objects")
    if not isinstance(pin, dict) or not isinstance(pin.get("job_id"), str):
        raise InvalidInputError("pin.json must be an object with a string job_id")
    subject = claims[0].get("subject_id")
    if not isinstance(subject, str) or not subject:
        raise InvalidInputError("claims must carry subject_id")
    pinned = next((j for j in jobs if isinstance(j, dict) and j.get("id") == pin["job_id"]), None)
    if pinned is None:
        raise InvalidInputError(f"pinned job {pin['job_id']!r} is not in jobs.json")
    vocab = default_vocabulary()
    engine = AtlasEngine.fit(jobs, vocab, seed=seed, n_boot=20)
    family = normalize_family(pinned.get("role_family"))
    if family not in engine.profiles:
        raise InvalidInputError(f"pinned job has role_family {pinned.get('role_family')!r}, which has no role profile "
                                "in jobs.json; a target role cannot be described")
    cand = claims_to_vector(claims, vocab, subject)
    gap = analyze_gap(engine, cand, family)
    plan = []
    for f in gap["skill_gaps"] + gap["evidence_gaps"]:
        fdef = vocab.get(f)
        if fdef.acquisition != ACQ_STUDY:
            continue
        plan.append({"skill": f, "effort_hours": round(fdef.effort_mid, 1),
                     "evidence_goal": f"portfolio-project: one fictional project evidencing {f}"})
        if len(plan) == 4:
            break
    sc = simulate_transition(engine, cand, family, plan, horizon, hours_per_week)
    sc["assumptions"].append("DEMONSTRATION PLAN generated by lane C's e2e stage from the gap analysis (up to four "
                             "study-acquirable core gaps, portfolio projects, placeholder hours). Not a user choice "
                             "and not a recommendation.")
    sc["assumptions"].append(f"Target role is the pinned job's role_family {family!r}; the pinned job "
                             f"{pin['job_id']!r} is one of {engine.profiles[family].n_postings} postings in that profile.")
    _write(artifacts / "scenarios.json", [sc])
    return {"scenario": sc["id"], "target": family, "actions": len(plan)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="e2e_stage.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("project")
    p.add_argument("--vectors", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--seed", type=int, default=0)
    s = sub.add_parser("scenario")
    s.add_argument("--artifacts", required=True)
    s.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "project":
            result = project(Path(args.vectors), Path(args.out_dir), args.seed)
        else:
            result = scenario(Path(args.artifacts), args.seed)
    except (AtlasError, OSError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
