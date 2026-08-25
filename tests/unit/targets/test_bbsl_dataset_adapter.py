from pathlib import Path

from adapters.bbsl.dataset_adapter import BBSLExperimentAdapter as LegacyBBSLExperimentAdapter
from targets.bbsl.dataset_adapter import BBSLExperimentAdapter


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "bbsl"


def test_dataset_adapter_loads_fixture_and_extracts_conditions() -> None:
    adapter = BBSLExperimentAdapter()
    payload = adapter.load_output(FIXTURES / "experiment_all_raw_result_mini.json")

    assert adapter.active_conditions(payload) == [
        "clean",
        "salt_pepper",
        "occlusion",
        "blur",
    ]
    assert len(adapter.universal_dataset(payload)) == 32
    assert adapter.sigma_pf_assumptions(payload)["SALT_PEPPER"] == 0.1


def test_dataset_adapter_matches_legacy_adapter_for_core_path_helpers(tmp_path: Path) -> None:
    target_repo = tmp_path / "bbsl_repo"
    (target_repo / "output" / "batches").mkdir(parents=True)
    (target_repo / "output" / "experiment_all_raw_result_mini.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (target_repo / "output" / "batches" / "noisy_batch_0002_mini.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (target_repo / "output" / "batches" / "noisy_batch_0001_mini.json").write_text(
        "{}",
        encoding="utf-8",
    )

    legacy = LegacyBBSLExperimentAdapter()
    current = BBSLExperimentAdapter()

    assert current.batch_output_dir(target_repo) == legacy.batch_output_dir(str(target_repo))
    assert current.default_output_path(target_repo, mini=True) == legacy.default_output_path(
        str(target_repo),
        mini=True,
    )
    assert current.clean_baseline_output_path(target_repo, mini=True) == legacy.clean_baseline_output_path(
        str(target_repo),
        mini=True,
    )
    assert current.clean_success_path(target_repo, mini=True) == legacy.clean_success_path(
        str(target_repo),
        mini=True,
    )
    assert current.noisy_batch_output_path(target_repo, batch_id=3, mini=True) == legacy.noisy_batch_output_path(
        str(target_repo),
        batch_id=3,
        mini=True,
    )
    assert current.list_noisy_batch_output_paths(target_repo, mini=True) == legacy.list_noisy_batch_output_paths(
        str(target_repo),
        mini=True,
    )
    assert current.noisy_batch_id_from_path("noisy_batch_0002_mini.json") == 2
