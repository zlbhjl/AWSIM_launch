import numpy as np
import pandas as pd

from evaluation.gp_boundary import (
    GPBoundaryService,
    fit_gp_boundary_model,
    predict_uncertainty,
    prepare_training_data_frame,
)


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "loop_num": [1, 2, 3, 4],
            "dx0": [10.0, 11.0, 12.0, 13.0],
            "ego_speed": [30.0, 31.0, 32.0, 33.0],
            "c_collision": [0, 1, 0, 1],
            "min_ttc": [2.0, 1.0, 3.0, 0.8],
            "min_distance": [4.0, 2.0, 5.0, 1.5],
        }
    )


def test_prepare_training_data_frame_returns_boolean_training_rows() -> None:
    training_df = prepare_training_data_frame(
        _sample_df(),
        target_column="c_collision",
        feature_names=["dx0", "ego_speed"],
    )

    assert training_df is not None
    assert len(training_df) == 4
    assert set(training_df["c_collision"].unique()) == {0, 1}


def test_prepare_training_data_frame_returns_none_when_feature_column_missing() -> None:
    training_df = prepare_training_data_frame(
        _sample_df().drop(columns=["ego_speed"]),
        target_column="c_collision",
        feature_names=["dx0", "ego_speed"],
    )

    assert training_df is None


def test_prepare_training_data_frame_returns_none_when_loop_num_missing() -> None:
    training_df = prepare_training_data_frame(
        _sample_df().drop(columns=["loop_num"]),
        target_column="c_collision",
        feature_names=["dx0", "ego_speed"],
    )

    assert training_df is None


def test_fit_gp_boundary_model_trains_and_predicts_uncertainty() -> None:
    boundary_model = fit_gp_boundary_model(
        _sample_df(),
        target_column="c_collision",
        feature_names=["dx0", "ego_speed"],
        n_restarts_optimizer=0,
    )

    assert boundary_model is not None
    mean, std = predict_uncertainty(
        boundary_model,
        np.array([[10.5, 30.5], [12.5, 32.5]]),
    )
    assert mean.shape == (2,)
    assert std.shape == (2,)


def test_fit_gp_boundary_model_returns_none_when_classes_missing() -> None:
    df = _sample_df().copy()
    df["c_collision"] = 0

    boundary_model = fit_gp_boundary_model(
        df,
        target_column="c_collision",
        feature_names=["dx0", "ego_speed"],
        n_restarts_optimizer=0,
    )

    assert boundary_model is None


def test_fit_gp_boundary_model_returns_none_when_training_rows_too_few() -> None:
    df = _sample_df().iloc[:1].copy()

    boundary_model = fit_gp_boundary_model(
        df,
        target_column="c_collision",
        feature_names=["dx0", "ego_speed"],
        n_restarts_optimizer=0,
    )

    assert boundary_model is None


def test_gp_boundary_service_trains_and_predicts() -> None:
    service = GPBoundaryService(
        feature_names=["dx0", "ego_speed"],
    )

    trained = service.train(
        _sample_df(),
        target_column="c_collision",
        n_restarts_optimizer=0,
    )

    assert trained is True
    mean, std = service.predict_uncertainty(
        np.array([[10.5, 30.5], [12.5, 32.5]]),
    )
    assert len(mean) == 2
    assert len(std) == 2


def test_gp_boundary_service_returns_none_when_not_trained() -> None:
    service = GPBoundaryService(
        feature_names=["dx0", "ego_speed"],
    )

    mean, std = service.predict_uncertainty(
        np.array([[10.5, 30.5]]),
    )

    assert mean is None
    assert std is None
