from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel as C
from sklearn.preprocessing import StandardScaler


@dataclass
class GPBoundaryModel:
    model: GaussianProcessRegressor
    scaler: StandardScaler
    feature_names: list[str]

    def predict_uncertainty(self, x_new):
        x_scaled = self.scaler.transform(x_new)
        return self.model.predict(x_scaled, return_std=True)


@dataclass
class GPBoundaryService:
    """Compatibility wrapper around the canonical GP boundary function API."""

    feature_names: list[str]
    boundary_model: GPBoundaryModel | None = None

    def train(
        self,
        df_dataset: pd.DataFrame | None,
        *,
        target_column: str,
        alpha: float = 0.01,
        n_restarts_optimizer: int = 5,
        random_state: int = 42,
        max_train_samples: int = 1500,
    ) -> bool:
        self.boundary_model = fit_gp_boundary_model(
            df_dataset,
            target_column=target_column,
            feature_names=self.feature_names,
            alpha=alpha,
            n_restarts_optimizer=n_restarts_optimizer,
            random_state=random_state,
            max_train_samples=max_train_samples,
        )
        return self.boundary_model is not None

    def predict_uncertainty(self, x_new):
        if self.boundary_model is None:
            return None, None
        return self.boundary_model.predict_uncertainty(x_new)


def prepare_training_data_frame(
    df_dataset: pd.DataFrame | None,
    *,
    target_column: str,
    feature_names: Sequence[str],
    max_train_samples: int = 1500,
) -> pd.DataFrame | None:
    if df_dataset is None or target_column not in df_dataset.columns:
        return None

    required_columns = {"loop_num", target_column, *feature_names}
    if not required_columns.issubset(df_dataset.columns):
        return None

    working_df = df_dataset.copy()
    for col in working_df.columns:
        if col.startswith("c_") or col.startswith("formula_"):
            working_df = working_df[~working_df[col].isin([-1, "-1", -1.0])]

    if "c_collision" in working_df.columns:
        collision_mask = working_df["c_collision"].isin([1, "1", 1.0])
        for col in working_df.columns:
            if col.startswith("c_ttc_"):
                working_df.loc[collision_mask, col] = 1

    ttc_cols = sorted(
        [c for c in working_df.columns if c.startswith("c_ttc_")],
        key=lambda x: float(x.split("_")[-1]),
    )
    for i in range(len(ttc_cols) - 1):
        working_df.loc[
            working_df[ttc_cols[i]].isin([1, "1", 1.0]),
            ttc_cols[i + 1],
        ] = 1

    for col in feature_names:
        working_df[col] = pd.to_numeric(working_df[col], errors="coerce")

    essential_cols = ["loop_num", target_column, *feature_names]
    working_df = working_df.dropna(subset=essential_cols)
    valid_df = working_df[working_df[target_column].isin([0, 1])]
    if len(valid_df) < 2 or valid_df[target_column].nunique() < 2:
        return None

    if len(valid_df) > max_train_samples:
        is_critical = (
            (valid_df.get("c_collision", 0) == 1)
            | (valid_df.get("min_ttc", 999.0) < 1.5)
            | (valid_df.get("min_distance", 999.0) < 2.0)
        )
        recent_threshold = valid_df["loop_num"].max() - 500
        is_recent = valid_df["loop_num"] > recent_threshold
        must_keep_mask = is_critical | is_recent
        must_keep_df = valid_df[must_keep_mask]
        others_df = valid_df[~must_keep_mask]
        remain_count = max_train_samples - len(must_keep_df)
        if remain_count > 0 and len(others_df) > remain_count:
            sampled_others = others_df.sample(n=remain_count, random_state=42)
            valid_df = pd.concat([must_keep_df, sampled_others])
        else:
            valid_df = must_keep_df

    return valid_df


def fit_gp_boundary_model(
    df_dataset: pd.DataFrame | None,
    *,
    target_column: str,
    feature_names: Sequence[str],
    alpha: float = 0.01,
    n_restarts_optimizer: int = 5,
    random_state: int = 42,
    max_train_samples: int = 1500,
) -> GPBoundaryModel | None:
    training_df = prepare_training_data_frame(
        df_dataset,
        target_column=target_column,
        feature_names=feature_names,
        max_train_samples=max_train_samples,
    )
    if training_df is None:
        return None

    x = training_df[list(feature_names)].values
    y = training_df[target_column].values
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x)
    kernel = C(1.0, (1e-3, 1e3)) * RBF(1.0, (1e-2, 1e2))
    model = GaussianProcessRegressor(
        kernel=kernel,
        alpha=alpha,
        n_restarts_optimizer=n_restarts_optimizer,
        random_state=random_state,
    )
    model.fit(x_scaled, y)
    return GPBoundaryModel(
        model=model,
        scaler=scaler,
        feature_names=list(feature_names),
    )


def predict_uncertainty(boundary_model: GPBoundaryModel, x_new):
    return boundary_model.predict_uncertainty(x_new)


__all__ = [
    "GPBoundaryModel",
    "GPBoundaryService",
    "fit_gp_boundary_model",
    "predict_uncertainty",
    "prepare_training_data_frame",
]
