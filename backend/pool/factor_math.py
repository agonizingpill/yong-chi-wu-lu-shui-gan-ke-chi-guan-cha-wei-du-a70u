from hashlib import sha256
import json
import warnings

import numpy as np
from sklearn.decomposition import FactorAnalysis
from sklearn.exceptions import ConvergenceWarning
from sklearn.preprocessing import StandardScaler

SEED = 1084
MIN_PRIMARY_LOADING = 0.45


class UnfitObservation(ValueError):
    pass


def _reverse_scores(items, surveys):
    """Return the raw (reverse-coded) matrix in the supplied item order."""
    numeric = []
    for survey in surveys:
        scores = {answer.item_id: answer.raw_value for answer in survey.marks}
        numeric.append([6 - scores[item.id] if item.reversed else scores[item.id] for item in items])
    return np.asarray(numeric, dtype=float)


def identify_factors(items, surveys):
    """Fit the two formal factors with formal items only.

    Returns the report findings plus the fixed factor scores of every answer
    sheet, aligned row-for-row with ``surveys``. Trial items never enter this
    fit, so the formal factors stay independent of试填数据.
    """
    data = _reverse_scores(items, surveys)
    if np.any(np.ptp(data, axis=0) == 0):
        raise UnfitObservation("有条目没有组内变化，无法拟合。请补采不同评分。")
    standardized = StandardScaler().fit_transform(data)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always", ConvergenceWarning)
        factor = FactorAnalysis(n_components=2, rotation="varimax", random_state=SEED,
                                svd_method="lapack", max_iter=1000).fit(standardized)
    if any(issubclass(w.category, ConvergenceWarning) for w in seen):
        raise UnfitObservation("因子估计未收敛，不能据此修订观察表。")
    raw_components = factor.components_
    # Pin each factor's sign from its leading item, then order factors by it.
    signs = np.array([-1.0 if row[np.argmax(abs(row))] < 0 else 1.0
                      for row in raw_components])
    components = raw_components.copy() * signs[:, None]
    order = np.argsort([int(np.argmax(abs(row))) for row in components])
    components = components[order]
    # Fixed scores keep the same sign flips and column order as the loadings.
    scores = factor.transform(standardized) * signs[None, :]
    scores = scores[:, order]
    details = []
    for i, (item, load) in enumerate(zip(items, components.T, strict=True)):
        main = int(np.argmax(abs(load))) + 1
        details.append({"item_id": item.id, "code": item.code, "reverse": item.reversed,
                        "loadings": [round(float(value), 4) for value in load],
                        "primary": main, "strong": bool(abs(load[main - 1]) >= MIN_PRIMARY_LOADING),
                        "communality": round(float(sum(load ** 2)), 4),
                        "residual": round(float(factor.noise_variance_[i]), 4)})
    findings = {"method": "FactorAnalysis / varimax", "dimensions": 2, "seed": SEED,
                "count": len(data), "iterations": int(factor.n_iter_),
                "log_likelihood": round(float(factor.loglike_[-1]), 4),
                "input_sha256": sha256(json.dumps(data.tolist(), separators=(",", ":")).encode()).hexdigest(),
                "items": details}
    return findings, scores


def estimate_trial_loading(item, surveys, factor_scores, eligible_survey_ids):
    """Estimate one trial item's loadings on the batch's fixed factor scores.

    Only sheets with an answer for the trial item participate. The formal
    factor scores are held fixed, so trial data never refits the factors.
    Blank trial answers are excluded rather than imputed. Both sides are
    standardized first so the coefficients are comparable to the 0.45
    loading threshold used for formal items.
    """
    rows, vectors = [], []
    for survey, score in zip(surveys, factor_scores, strict=True):
        if survey.id not in eligible_survey_ids:
            continue
        answer = next((mark for mark in survey.marks if mark.item_id == item.id), None)
        if answer is None:
            continue
        rows.append(6 - answer.raw_value if item.reversed else answer.raw_value)
        vectors.append(score)
    values = np.asarray(rows, dtype=float)
    design = np.asarray(vectors, dtype=float)
    if values.size < 3 or np.ptp(values) == 0:
        return None
    x_scale = design.std(axis=0)
    if np.any(x_scale == 0):
        return None
    x_std = (design - design.mean(axis=0)) / x_scale
    y_std = (values - values.mean()) / values.std()
    try:
        loadings, *_ = np.linalg.lstsq(x_std, y_std, rcond=None)
    except np.linalg.LinAlgError:
        return None
    if not np.all(np.isfinite(loadings)):
        return None
    main = int(np.argmax(abs(loadings))) + 1
    return {"item_id": item.id, "code": item.code, "reverse": item.reversed,
            "loadings": [round(float(value), 4) for value in loadings],
            "primary": main, "strong": bool(abs(loadings[main - 1]) >= MIN_PRIMARY_LOADING),
            "count": int(values.size)}
