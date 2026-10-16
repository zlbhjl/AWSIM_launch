from pathlib import Path

from orchestration.resume import ResumeConfig, ResumeService


def test_resume_service_restores_base_and_uses_max_loop_from_base_and_current(
    tmp_path: Path,
) -> None:
    resume_from = tmp_path / "resume"
    traces_dir = tmp_path / "traces"
    resume_from.mkdir()
    traces_dir.mkdir()
    (resume_from / "uturn_dataset.csv").write_text(
        "loop_num,status\n5,success\n7,timeout\n",
        encoding="utf-8",
    )
    (traces_dir / "uturn_dataset.csv").write_text(
        "loop_num,status\n8,success\n",
        encoding="utf-8",
    )

    state = ResumeService().prepare(
        ResumeConfig(
            scenario_name="uturn",
            traces_dir=str(traces_dir),
            resume_from=str(resume_from),
        )
    )

    assert state.restored_base_csv == traces_dir / "uturn_dataset_base.csv"
    assert state.last_loop_num == 8
    assert state.next_loop_num == 9
    assert state.restored_base_csv.read_text(encoding="utf-8").startswith("loop_num,status")


def test_resume_service_can_ignore_restored_base_for_current_only_mode(
    tmp_path: Path,
) -> None:
    resume_from = tmp_path / "resume"
    traces_dir = tmp_path / "traces"
    resume_from.mkdir()
    traces_dir.mkdir()
    (resume_from / "uturn_dataset.csv").write_text(
        "loop_num,status\n20,success\n",
        encoding="utf-8",
    )
    (traces_dir / "uturn_dataset.csv").write_text(
        "loop_num,status\n3,success\n4,timeout\n",
        encoding="utf-8",
    )

    state = ResumeService().prepare(
        ResumeConfig(
            scenario_name="uturn",
            traces_dir=str(traces_dir),
            resume_from=str(resume_from),
            count_current_only=True,
        )
    )

    assert state.last_loop_num == 4
    assert state.next_loop_num == 5


def test_resume_service_returns_next_loop_one_when_no_dataset_exists(tmp_path: Path) -> None:
    traces_dir = tmp_path / "traces"

    state = ResumeService().prepare(
        ResumeConfig(
            scenario_name="uturn",
            traces_dir=str(traces_dir),
        )
    )

    assert state.restored_base_csv is None
    assert state.last_loop_num == 0
    assert state.next_loop_num == 1
    assert state.paths.dataset_csv == traces_dir / "uturn_dataset.csv"


def test_resume_service_can_use_explicit_dataset_paths(tmp_path: Path) -> None:
    resume_from = tmp_path / "resume"
    traces_dir = tmp_path / "traces"
    resume_from.mkdir()
    traces_dir.mkdir()
    current_dataset_csv = traces_dir / "custom_dataset.csv"
    base_dataset_csv = traces_dir / "custom_dataset_base.csv"
    (resume_from / "uturn_dataset.csv").write_text(
        "loop_num,status\n10,success\n",
        encoding="utf-8",
    )
    current_dataset_csv.write_text(
        "loop_num,status\n11,success\n",
        encoding="utf-8",
    )

    state = ResumeService().prepare(
        ResumeConfig(
            scenario_name="uturn",
            traces_dir=str(traces_dir),
            resume_from=str(resume_from),
            current_dataset_csv=str(current_dataset_csv),
            base_dataset_csv=str(base_dataset_csv),
        )
    )

    assert state.current_dataset_csv == current_dataset_csv
    assert state.base_dataset_csv == base_dataset_csv
    assert state.restored_base_csv == base_dataset_csv
    assert state.last_loop_num == 11
    assert state.next_loop_num == 12


def test_resume_service_uses_current_dataset_only_for_dkw_fixed_mode(
    tmp_path: Path,
) -> None:
    resume_from = tmp_path / "resume"
    traces_dir = tmp_path / "traces"
    resume_from.mkdir()
    traces_dir.mkdir()
    (resume_from / "uturn_dataset.csv").write_text(
        "loop_num,status\n20,success\n",
        encoding="utf-8",
    )
    (traces_dir / "uturn_dataset.csv").write_text(
        "loop_num,status\n3,success\n4,timeout\n",
        encoding="utf-8",
    )

    state = ResumeService().prepare(
        ResumeConfig(
            scenario_name="uturn",
            traces_dir=str(traces_dir),
            resume_from=str(resume_from),
            run_mode="dkw_fixed",
        )
    )

    assert state.last_loop_num == 4
    assert state.next_loop_num == 5
