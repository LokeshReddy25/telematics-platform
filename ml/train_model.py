"""
train_model.py
---------------
Trains the driver risk-scoring model used by ml/risk_scorer.py (and, at
runtime, by spark/stream_processor.py).

We train on SYNTHETIC per-trip feature rows rather than raw simulated events,
because the model's job is to map trip-level aggregates (avg speed, harsh
brake count, etc.) to a 0-100 risk score -- exactly the aggregates that
risk_scorer.py computes from a batch of raw events before calling the model.
Keeping the training feature schema identical to risk_scorer.py's output
schema is what lets the two files stay in sync; see FEATURE_COLUMNS below,
which risk_scorer.py imports directly to guarantee both files agree on
column names AND column order.

Usage:
    python train_model.py
Produces:
    ml/models/risk_model.pkl
"""

import os
import pickle

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

# Single source of truth for the feature schema. Imported by risk_scorer.py
# so that the DataFrame it builds from live trip aggregates has the exact
# same column names/order the model was trained on.
FEATURE_COLUMNS = [
    "avg_speed",
    "max_speed",
    "harsh_brake_count",
    "harsh_accel_count",
    "night_driving_ratio",
    "trip_duration_seconds",
    "speed_variance",
]

N_SAMPLES = 10_000
RANDOM_SEED = 42

# Population mix mirrors the simulator's driver profile weights (safe 40%,
# moderate 40%, aggressive 20%) so the model sees a realistic class balance.
PROFILE_WEIGHTS = {"safe": 0.40, "moderate": 0.40, "aggressive": 0.20}

# Per-profile distributions for each raw feature. Values are (mean, std) for
# normal draws, tuned so the resulting risk scores land in the target bands
# specified by the project spec: safe 5-30, moderate 35-65, aggressive 70-100.
PROFILE_FEATURE_DISTRIBUTIONS = {
    "safe": {
        "avg_speed": (55, 10),
        "max_speed": (85, 12),
        "harsh_brake_count": (1.0, 1.0),
        "harsh_accel_count": (1.0, 1.0),
        "night_driving_ratio": (0.08, 0.06),
        "trip_duration_seconds": (900, 300),
        "speed_variance": (40, 15),
    },
    "moderate": {
        "avg_speed": (75, 12),
        "max_speed": (115, 14),
        "harsh_brake_count": (4.0, 2.0),
        "harsh_accel_count": (4.0, 2.0),
        "night_driving_ratio": (0.20, 0.12),
        "trip_duration_seconds": (1100, 350),
        "speed_variance": (90, 25),
    },
    "aggressive": {
        "avg_speed": (100, 15),
        "max_speed": (148, 10),
        "harsh_brake_count": (9.0, 3.0),
        "harsh_accel_count": (9.0, 3.0),
        "night_driving_ratio": (0.38, 0.15),
        "trip_duration_seconds": (1300, 400),
        "speed_variance": (150, 35),
    },
}

# Target risk-score bands per profile, per the project spec.
PROFILE_RISK_RANGE = {
    "safe": (5, 30),
    "moderate": (35, 65),
    "aggressive": (70, 100),
}

# Weighted contribution of each (normalized) feature to the raw risk signal
# before it gets rescaled into the profile's target band. These weights
# encode domain intuition: harsh braking/acceleration and night driving are
# the strongest risk signals insurers actually underwrite on.
FEATURE_RISK_WEIGHTS = {
    "avg_speed": 0.15,
    "max_speed": 0.15,
    "harsh_brake_count": 0.25,
    "harsh_accel_count": 0.20,
    "night_driving_ratio": 0.15,
    "speed_variance": 0.10,
}

# Approximate max values used to normalize each feature into [0, 1] before
# applying the weights above.
FEATURE_NORMALIZATION_MAX = {
    "avg_speed": 130.0,
    "max_speed": 160.0,
    "harsh_brake_count": 15.0,
    "harsh_accel_count": 15.0,
    "night_driving_ratio": 1.0,
    "speed_variance": 200.0,
}


def generate_synthetic_dataset(n_samples: int, seed: int) -> pd.DataFrame:
    """
    Build a DataFrame of n_samples synthetic trip-level feature rows plus a
    derived risk_score label. Each row is first sampled from its assigned
    profile's feature distributions, then a weighted risk signal is computed
    from the normalized features and rescaled into that profile's target
    risk band -- this ties the label directly to the features (so the model
    has real signal to learn) while still respecting the spec's required
    risk bands per profile.
    """
    rng = np.random.default_rng(seed)
    profiles = list(PROFILE_WEIGHTS.keys())
    weights = list(PROFILE_WEIGHTS.values())

    rows = []
    for _ in range(n_samples):
        profile = rng.choice(profiles, p=weights)
        dist = PROFILE_FEATURE_DISTRIBUTIONS[profile]

        feature_values = {}
        for feat in FEATURE_COLUMNS:
            mean, std = dist[feat]
            value = rng.normal(mean, std)
            # Counts and ratios can't go negative; clip appropriately.
            if feat in ("harsh_brake_count", "harsh_accel_count"):
                value = max(0, round(value))
            elif feat == "night_driving_ratio":
                value = float(np.clip(value, 0.0, 1.0))
            else:
                value = max(0.0, value)
            feature_values[feat] = value

        # Weighted risk signal in [0, 1] from the normalized features.
        signal = 0.0
        for feat, weight in FEATURE_RISK_WEIGHTS.items():
            normalized = min(1.0, feature_values[feat] / FEATURE_NORMALIZATION_MAX[feat])
            signal += weight * normalized

        # Add small independent noise so the label isn't a perfectly
        # deterministic function of the features (more realistic to learn).
        signal = float(np.clip(signal + rng.normal(0, 0.03), 0.0, 1.0))

        low, high = PROFILE_RISK_RANGE[profile]
        risk_score = low + signal * (high - low)
        risk_score = float(np.clip(risk_score, 0, 100))

        feature_values["risk_score"] = round(risk_score, 2)
        feature_values["driver_profile"] = profile
        rows.append(feature_values)

    return pd.DataFrame(rows)


def train() -> None:
    print(f"Generating {N_SAMPLES} synthetic training samples...")
    df = generate_synthetic_dataset(N_SAMPLES, RANDOM_SEED)

    X = df[FEATURE_COLUMNS]
    y = df["risk_score"]

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=RANDOM_SEED)
    print(f"Train samples: {len(X_train)} | Test samples: {len(X_test)}")

    model = GradientBoostingRegressor(
        n_estimators=200,
        learning_rate=0.05,
        max_depth=3,
        subsample=0.9,
        random_state=RANDOM_SEED,
    )

    print("Training GradientBoostingRegressor...")
    model.fit(X_train, y_train)

    predictions = model.predict(X_test)
    mae = mean_absolute_error(y_test, predictions)
    r2 = r2_score(y_test, predictions)

    print("\n=== Evaluation on held-out test set ===")
    print(f"MAE : {mae:.3f}")
    print(f"R^2 : {r2:.3f}")

    print("\n=== Feature importances ===")
    importances = sorted(
        zip(FEATURE_COLUMNS, model.feature_importances_),
        key=lambda pair: pair[1],
        reverse=True,
    )
    for name, importance in importances:
        print(f"  {name:<24} {importance:.4f}")

    models_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    os.makedirs(models_dir, exist_ok=True)
    model_path = os.path.join(models_dir, "risk_model.pkl")

    # Persist the feature column order alongside the model itself so
    # risk_scorer.py can build its inference DataFrame with guaranteed
    # matching column order, even if FEATURE_COLUMNS ever changes.
    with open(model_path, "wb") as f:
        pickle.dump({"model": model, "feature_columns": FEATURE_COLUMNS}, f)

    print(f"\nSaved trained model to {model_path}")


if __name__ == "__main__":
    train()
    print("train_model.py completed successfully.")
