"""Sensitivity / fair measurement (workstream 10), the CLI and the offline demo (workstream 12)."""
import hashlib
import json

import pytest

from careertwin_atlas.__main__ import main
from careertwin_atlas.errors import InvalidInputError
from careertwin_atlas.report import held_out_check, run_demo
from careertwin_atlas.sensitivity import (COMPONENTS, SensitivityInputs, freshness_sensitivity,
                                          lexicon_sensitivity, missing_salary_sensitivity,
                                          normalization_sensitivity, run_sensitivity, seniority_sensitivity,
                                          source_volume_sensitivity, utility_components, weight_sensitivity)

from conftest import AS_OF, CANDIDATE_REF, find_forbidden_keys


@pytest.fixture(scope="module")
def inputs(jobs, claims, vocab):
    return SensitivityInputs(jobs, claims, CANDIDATE_REF, AS_OF, vocab, seed=3, n_boot=5)


def test_utility_components_are_bounded_and_unknown_stays_unknown(engine, candidate, jobs, vocab, claims):
    comps = utility_components(engine, candidate)
    assert set(comps) == set(engine.profiles)
    for c in comps.values():
        assert set(c) == set(COMPONENTS)
        assert all(v is None or 0.0 <= v <= 1.0 for v in c.values())
    from careertwin_atlas.engine import AtlasEngine
    from careertwin_atlas.features import claims_to_vector
    no_pay = AtlasEngine.fit([{**j, "compensation": None} for j in jobs], vocab, n_boot=2)
    blind = utility_components(no_pay, claims_to_vector(claims, vocab, CANDIDATE_REF))
    assert all(c["compensation"] is None for c in blind.values())          # never zero, never guessed


def test_weight_sensitivity_reports_stability_not_odds(engine, candidate):
    res = weight_sensitivity(engine, candidate, n_draws=120, seed=1)
    assert res == weight_sensitivity(engine, candidate, n_draws=120, seed=1)        # seeded
    assert sorted(res["baseline_ordering"]) == sorted(engine.profiles)
    assert 0.0 <= res["top_choice_unchanged_share"] <= 1.0
    assert -1.0 <= res["kendall_tau_min"] <= res["kendall_tau_mean"] <= 1.0
    for r in res["rank_range"].values():
        assert 1 <= r["min"] <= r["baseline"] <= r["max"] <= 8
    assert res["verdict"] in ("stable", "sensitive")
    assert "not a likelihood of any outcome" in res["interpretation"]
    assert sum(res["weights_normalised"].values()) == pytest.approx(1.0, abs=1e-3)


def test_tight_perturbations_are_stable_and_loose_ones_are_not(engine, candidate):
    tight = weight_sensitivity(engine, candidate, n_draws=80, concentration=1e6, seed=0)
    loose = weight_sensitivity(engine, candidate, n_draws=80, concentration=1.0, seed=0)
    assert tight["top_choice_unchanged_share"] == 1.0 and tight["kendall_tau_min"] == 1.0
    assert loose["kendall_tau_mean"] < tight["kendall_tau_mean"]
    single = weight_sensitivity(engine, candidate, {"skills": 1.0}, n_draws=40, seed=0)
    assert single["weights_normalised"] == {"skills": 1.0, "effort": 0.0, "compensation": 0.0, "leadership": 0.0}


@pytest.mark.parametrize("weights", [{"luck": 1.0}, {"skills": -1.0}, {"skills": float("nan")},
                                     {"skills": 0.0, "effort": 0.0}, {"skills": True}, "skills"])
def test_invalid_weights_are_rejected(engine, candidate, weights):
    with pytest.raises(InvalidInputError):
        weight_sensitivity(engine, candidate, weights)


def test_missing_salary_gives_rank_bounds_instead_of_guesses(engine, candidate):
    res = missing_salary_sensitivity(engine, candidate)
    assert "not a currency" in res["compensation_unit"]
    bounds = res["rank_bounds_if_compensation_fully_unknown"]
    assert all(1 <= b["best_possible_rank"] <= b["worst_possible_rank"] <= 8 for b in bounds.values())
    assert sorted(res["ordering_with_compensation_dropped"]) == sorted(engine.profiles)
    assert isinstance(res["top_choice_identifiable_without_compensation"], bool)
    assert "never set to zero or guessed" in res["handling"]
    heavy = missing_salary_sensitivity(engine, candidate, {"compensation": 0.9, "skills": 0.1})
    light = missing_salary_sensitivity(engine, candidate, {"compensation": 0.0, "skills": 1.0})
    spread = lambda r: sum(b["worst_possible_rank"] - b["best_possible_rank"]          # noqa: E731
                           for b in r["rank_bounds_if_compensation_fully_unknown"].values())
    assert spread(heavy) > spread(light) == 0                 # no weight on pay: nothing is unidentifiable
    assert light["top_choice_identifiable_without_compensation"] is True


def test_data_perturbations_report_ordering_changes(inputs):
    sen = seniority_sensitivity(inputs)
    assert set(sen["variants"]) == {"all_seniority_unknown", "20pct_bumped_one_level"}
    assert 0.0 <= sen["variants"]["all_seniority_unknown"]["map_knn_overlap_with_baseline"] <= 1.0
    fresh = freshness_sensitivity(inputs, windows_days=(90, 2))
    assert fresh["variants"]["last_90_days"]["n_postings"] < fresh["n_postings_total"]
    tiny = fresh["variants"]["last_2_days"]
    assert tiny.get("status") == "not-assessable" or tiny["roles_lost"] or tiny["unstable_roles"]
    vol = source_volume_sensitivity(inputs, fractions=(0.5, 0.05), repeats=3)
    half, thin = vol["variants"]["50%_of_postings"], vol["variants"]["5%_of_postings"]
    assert thin["centroid_displacement_share_mean"] > half["centroid_displacement_share_mean"]
    assert thin["unstable_roles_mean"] >= half["unstable_roles_mean"]
    lex = lexicon_sensitivity(inputs, repeats=2)
    assert lex["variants"]["aliases_disabled"]["unmapped_terms_without_aliases"] > \
        lex["variants"]["aliases_disabled"]["unmapped_terms_baseline"]
    assert len(lex["variants"]["random_10%_of_features_dropped"]) == 2
    norm = normalization_sensitivity(inputs)
    assert norm["unstandardised"]["explained_variance_cumulative"] != norm["standardised"]["explained_variance_cumulative"]
    assert 0.0 <= norm["map_knn_overlap_between_scalings"] <= 1.0
    for res in (sen, fresh, vol, lex, norm):
        assert res["verdict"] in ("stable", "sensitive") and "convention" in res


def test_freshness_without_dates_is_not_assessable(jobs, claims, vocab):
    undated = [{k: v for k, v in j.items() if k != "fetched_at"} for j in jobs[:60]]
    res = freshness_sensitivity(SensitivityInputs(undated, claims, CANDIDATE_REF, AS_OF, vocab, n_boot=2))
    assert res["status"] == "not-assessable"


def test_full_sensitivity_report_has_no_outcome_probabilities(inputs):
    report = run_sensitivity(inputs)
    assert set(report) == {"utility_weights", "missing_salary", "seniority", "job_freshness", "source_volume",
                           "lexicon", "normalization", "summary"}
    assert set(report["summary"]["sensitive_to"]).isdisjoint(report["summary"]["stable_under"])
    assert find_forbidden_keys(report, ("probab", "odds", "chance", "likelihood")) == []
    json.dumps(report, allow_nan=False)


def test_held_out_postings_show_what_the_map_loses(jobs):
    res = held_out_check(jobs, seed=1)
    assert res["n_train"] + res["n_held_out"] == len(jobs)
    assert res["nearest_centroid_agreement_full_space"] > 0.95
    assert res["nearest_centroid_agreement_map_only"] < res["nearest_centroid_agreement_full_space"]
    assert abs(res["held_out_residual_share_median"] - res["train_residual_share_median"]) < 0.1


# ---------------------------------------------------------------- CLI and demo --------
def test_cli_simulate_prints_a_hypothetical_scenario(capsys):
    code = main(["simulate", "--target", "ai-architect", "--n", "200", "--months", "6",
                 "--action", "mlops:80:portfolio-project", "--action", "rag:40"])
    assert code == 0
    scenario = json.loads(capsys.readouterr().out)
    assert scenario["status"] == "hypothetical" and scenario["target_role"] == "ai-architect"
    assert len(scenario["planned_actions"]) == 2 and scenario["time_horizon_months"] == 6


def test_cli_reports_errors_with_exit_code_2(capsys):
    assert main(["simulate", "--target", "astronaut", "--n", "100"]) == 2
    assert "unknown target_role" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["simulate", "--target", "backend", "--action", "python"])          # malformed action
    with pytest.raises(SystemExit):
        main(["launch-rocket"])


def test_cli_stress_runs(capsys):
    assert main(["stress", "--n", "1100"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n"] == 1100 and out["points"] == 1108 and out["features"] == 52


def test_demo_writes_reproducible_artifacts(tmp_path):
    summary = run_demo(n=300, seed=5, out_dir=tmp_path, stress_n=1100)
    manifest = json.loads((tmp_path / "MANIFEST.json").read_text(encoding="utf-8"))
    expected = {"atlas_bundle.sample.json", "pca_model.json", "projection_comparison.json", "gap_field_cto.json",
                "transition_graph.json", "scenario_field_cto.json", "sensitivity.json",
                "held_out_and_drift.json", "atlas_map.svg", "DIAGNOSTICS_REPORT.md"}
    assert set(manifest["files"]) == expected
    for name, sha in manifest["files"].items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == sha
    report = (tmp_path / "DIAGNOSTICS_REPORT.md").read_text(encoding="utf-8")
    assert "not a match score and not a hiring probability" in report
    assert "NOT EXECUTED" in report or "| umap | executed" in report        # UMAP status is always explicit
    assert summary["stress"]["n_postings"] == 1100
    svg = (tmp_path / "atlas_map.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg") and "fictional" in svg
    again = run_demo(n=300, seed=5, stress_n=1100)
    assert again["scenario"] == summary["scenario"]                          # same seed, same scenario
    assert again["bundle"]["diagnostics"] == summary["bundle"]["diagnostics"]
    assert summary["drift"]["no_drift_control"]["drifted_families"] == []
