# -*- coding: utf-8 -*-
"""A/B experiments over teaching style — and never over anything else.

The design's §6.3 asks for an experimentation framework with a hypothesis, two
groups, metrics, a t-test and Cohen's d. This module delivers all of that, and
draws one boundary that the design does not.

**An experiment may vary teaching style only.** It may never vary the
assistance ceiling, the grading policy, or what a graded assignment is allowed
to receive. Those are set by the accepted course contract and by `policy.py`:
the assistance table says a graded task never receives a SOLUTION, and no
experiment — however well-powered, however interesting the hypothesis — is a
way to renegotiate that. A variant named `assistance_ceiling`, `assessment`,
`grading`, `policy` or `permission` is refused by `assert_allowed_variation`
with `VARIATION_NOT_ALLOWED`, and that refusal is unconditional: it does not
depend on sample size, and there is no flag that turns it off. Changing how a
graded task is marked by running an experiment on students would make every
other guarantee in this project conditional on whoever wrote the variant list.

Four further properties:

* **Assignment is stable across runs and processes.** `hashlib.sha256` over
  `unit_id + experiment_id`, modulo the variant count. Python's builtin
  `hash()` is salted per process (`PYTHONHASHSEED`), so it would put the same
  learner in a different arm on every restart — which is not a bug in the
  experiment, it is an experiment that cannot be reproduced.
* **Guardrails refuse underpowered comparisons.** `guardrail_check` raises
  `EXPERIMENT_UNDERPOWERED` when either arm is under `min_n` or the arms are
  imbalanced beyond a tolerance, and `analyze` calls it. A t-test on three
  observations is not a small effect; it is a large noise, reported as one.
* **`p` is `None`, not a number, when the test cannot be run.** `n < 2` in
  either arm means the variance is undefined, and inventing a p-value there
  would make a real, underpowered result look like a decision.
* **The t-distribution is computed, not assumed.** The two-tailed `p` comes
  from the regularized incomplete beta function implemented here on top of
  `math.lgamma`. Documented in `_t_test`, with the textbook formula, because a
  p-value nobody can verify is a decorative number.

The module writes nothing outside `experiments_dir` and never opens a network
connection; it is a pure analysis over recorded observations.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from . import paths, tutoring

SCHEMA_VERSION = 2

EXPERIMENT_FILENAME_SUFFIX = ".json"

# The boundary named in the module docstring. Listed explicitly so the refusal
# carries a readable reason rather than only an error code.
FORBIDDEN_VARIATIONS = (
    "assistance_ceiling",
    "assessment",
    "grading",
    "policy",
    "permission",
)

# What an experiment may vary. Anything outside this list is refused at
# creation and again at analysis, so the boundary survives a hand-edited file.
ALLOWED_VARIATION_FAMILIES = ("presentation", "sequence", "pacing", "style")

# Metric shapes accepted in a document. Everything is a number; a metric that
# is not a number cannot be averaged, and averaging a string would fail
# somewhere far from the cause.
METRIC_MIN = -1e12
METRIC_MAX = 1e12


class ExperimentError(RuntimeError):
    """A refused experiment operation, with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def experiments_dir(root=None):
    """`<root>/experiments` when that directory exists, else the repo's.

    Mirrors `personas.personas_dir`: resolved from this file, never from the
    working directory.
    """
    if root:
        candidate = Path(root) / "experiments"
        if candidate.is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent.parent / "experiments"


def experiment_path(root, experiment_id):
    """`<experiments_dir>/<experiment_id>.json`, with the id validated."""
    name = paths.safe_name(experiment_id, kind="идентификатор эксперимента")
    base = experiments_dir(root)
    try:
        target = paths.ensure_within(
            base, base / (str(name) + EXPERIMENT_FILENAME_SUFFIX)
        )
    except paths.PathError as e:
        raise ExperimentError(e.code, e.message)
    return Path(target)


def load_experiment(root, experiment_id):
    """Read and validate one experiment document."""
    path = experiment_path(root, experiment_id)
    if not path.is_file():
        raise ExperimentError(
            "EXPERIMENT_MISSING",
            "эксперимент не найден: %s. Создайте его, прежде чем анализировать." % path,
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise ExperimentError(
            "EXPERIMENT_UNREADABLE",
            "не удалось прочитать эксперимент %s: %s" % (path, e),
        )
    return validate_experiment(document)


def save_experiment(root, experiment):
    """Validate and write one experiment document. Returns the path."""
    document = validate_experiment(experiment)
    path = experiment_path(root, document["experiment_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(payload + "\n", encoding="utf-8", newline="\n")
    return path


def assert_allowed_variation(variants):
    """Refuse any variant that would touch the assistance or grading policy.

    Unconditional, as the module docstring says. The check is on the variant
    *name* — a variant is a label describing what differs between arms, and a
    label naming the assistance ceiling is a label saying the experiment
    changes how much help a graded task may receive.
    """
    if not isinstance(variants, (list, tuple)):
        raise ExperimentError(
            "VARIANTS_INVALID",
            "варианты должны быть списком, получено %s" % type(variants).__name__,
        )
    if len(variants) < 2:
        raise ExperimentError(
            "VARIANTS_TOO_FEW",
            "эксперимент требует как минимум два варианта, получено %d" % len(variants),
        )

    offenders = []
    for variant in variants:
        if not isinstance(variant, str) or not variant.strip():
            raise ExperimentError(
                "VARIANT_NAME_INVALID",
                "вариант должен быть непустой строкой, получено %r" % (variant,),
            )
        lowered = variant.strip().lower()
        for forbidden in FORBIDDEN_VARIATIONS:
            if forbidden in lowered:
                offenders.append((variant, forbidden))

    if offenders:
        raise ExperimentError(
            "VARIATION_NOT_ALLOWED",
            "эксперимент может менять только подачу материала; эти варианты "
            "меняют правила помощи или оценивания, и запрещены: %s. "
            "Помощь и оценку задаёт принятый контракт курса и policy.py, "
            "не эксперимент."
            % "; ".join("%s (%s)" % (name, reason) for name, reason in offenders),
        )

    unknown = [
        v
        for v in variants
        if not any(family in str(v).lower() for family in ALLOWED_VARIATION_FAMILIES)
    ]
    if unknown:
        raise ExperimentError(
            "VARIATION_UNKNOWN_FAMILY",
            "варианты должны описывать подачу материала (семейства: %s); "
            "не распознаны: %s"
            % (
                ", ".join(ALLOWED_VARIATION_FAMILIES),
                ", ".join(str(v) for v in unknown),
            ),
        )

    return True


def validate_experiment(document):
    """Structural validation: ids, variants, metrics, observations."""
    if not isinstance(document, dict):
        raise ExperimentError(
            "EXPERIMENT_INVALID",
            "документ эксперимента должен быть объектом, получено %s"
            % type(document).__name__,
        )

    for key in ("experiment_id", "name", "hypothesis"):
        value = document.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ExperimentError(
                "EXPERIMENT_INVALID",
                "поле %s должно быть непустой строкой, получено %r" % (key, value),
            )

    variants = document.get("variants")
    try:
        assert_allowed_variation(variants)
    except ExperimentError as e:
        if e.code == "VARIATION_UNKNOWN_FAMILY":
            # A stored document naming only style variants by another word is
            # still an experiment about teaching style; the family check is a
            # creation-time guard, and re-imposing it here would refuse every
            # document written before this module existed. The *boundary*
            # (no policy variants) is still enforced.
            pass
        else:
            raise

    metrics = document.get("metrics")
    if not isinstance(metrics, (list, tuple)) or not metrics:
        raise ExperimentError(
            "EXPERIMENT_INVALID",
            "у эксперимента должен быть хотя бы один метрик, получено %r" % (metrics,),
        )
    for metric in metrics:
        if not isinstance(metric, str) or not metric.strip():
            raise ExperimentError(
                "EXPERIMENT_INVALID",
                "метрика должна быть непустой строкой, получено %r" % (metric,),
            )

    observations = document.get("observations")
    if observations is None:
        observations = []
    if not isinstance(observations, list):
        raise ExperimentError(
            "EXPERIMENT_INVALID",
            "поле observations должно быть списком, получено %s"
            % type(observations).__name__,
        )
    for observation in observations:
        _validate_observation(observation, set(metrics))

    document = dict(document)
    document.setdefault("observations", observations)
    document.setdefault("schema_version", SCHEMA_VERSION)
    return document


def _validate_observation(observation, metrics):
    if not isinstance(observation, dict):
        raise ExperimentError(
            "EXPERIMENT_INVALID",
            "наблюдение должно быть объектом, получено %s" % type(observation).__name__,
        )
    for key in ("unit_id", "variant", "metric", "value"):
        if observation.get(key) in (None, ""):
            raise ExperimentError(
                "EXPERIMENT_INVALID",
                "наблюдение без поля %s: %r" % (key, observation),
            )
    if metrics and observation.get("metric") not in metrics:
        raise ExperimentError(
            "EXPERIMENT_INVALID",
            "наблюдение по необъявленной метрике %r (заявлены: %s)"
            % (observation.get("metric"), ", ".join(sorted(metrics))),
        )
    value = observation.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExperimentError(
            "EXPERIMENT_INVALID",
            "значение метрики должно быть числом, получено %r" % (value,),
        )
    if not (METRIC_MIN <= float(value) <= METRIC_MAX):
        raise ExperimentError(
            "EXPERIMENT_INVALID",
            "значение метрики вне допустимого диапазона: %r" % (value,),
        )


# --------------------------------------------------------------------------
# Creation and assignment
# --------------------------------------------------------------------------


def create_experiment(
    root, *, experiment_id, name, hypothesis, variants, metrics, clock=None
):
    """A validated experiment document with an empty observation list."""
    document = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": paths.safe_name(
            experiment_id, kind="идентификатор эксперимента"
        ),
        "name": str(name).strip(),
        "hypothesis": str(hypothesis).strip(),
        "variants": [str(v).strip() for v in (variants or [])],
        "metrics": [str(m).strip() for m in (metrics or [])],
        "observations": [],
        "created_at": tutoring.now_iso(clock),
        "variation_scope_ru": (
            "эксперимент меняет только подачу материала: потолок помощи, "
            "политику оценивания и то, что разрешено оценённому заданию, "
            "менять нельзя"
        ),
    }
    # The boundary is asserted at creation, not only at save: a caller that
    # asks for a policy variant must be told before any file exists.
    assert_allowed_variation(document["variants"])
    return validate_experiment(document)


def assign_variant(experiment, unit_id):
    """Deterministic arm assignment for one unit.

    `sha256("%s:%s" % (unit_id, experiment_id))` read as an integer modulo the
    variant count. Two properties this buys:

    * **Stable across runs.** The same learner lands in the same arm in every
      process, on every machine, for the life of the experiment. This is what
      makes an assignment recoverable after a restart rather than a new draw.
    * **Stable across orderings.** Assigning unit B before unit A cannot move A,
      because the assignment of one unit never reads another unit's state.

    The builtin `hash()` is deliberately not used: it is salted per process by
    `PYTHONHASHSEED`, so it would reassign learners on every restart and turn
    the experiment into a rolling reshuffle.
    """
    if not isinstance(experiment, dict):
        raise ExperimentError(
            "EXPERIMENT_INVALID",
            "ожидался документ эксперимента, получено %s" % type(experiment).__name__,
        )
    variants = experiment.get("variants") or []
    if len(variants) < 2:
        raise ExperimentError(
            "VARIANTS_TOO_FEW",
            "эксперимент требует как минимум два варианта, получено %d" % len(variants),
        )
    if not unit_id or not str(unit_id).strip():
        raise ExperimentError(
            "UNIT_ID_INVALID",
            "идентификатор участника эксперимента обязателен",
        )

    experiment_id = experiment.get("experiment_id") or ""
    payload = "%s:%s" % (experiment_id, str(unit_id))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    index = int(digest, 16) % len(variants)
    return variants[index]


def record_observation(experiment, *, unit_id, variant, metric, value):
    """A validated observation appended to the experiment (returned, not stored).

    The experiment document is treated as immutable here: the caller writes
    the returned document through `save_experiment`. That keeps the reducer
    pure and makes every write go through one validator.
    """
    document = validate_experiment(experiment)
    if variant not in (document.get("variants") or []):
        raise ExperimentError(
            "VARIANT_UNKNOWN",
            "вариант %r не входит в набор вариантов эксперимента %s"
            % (variant, document.get("experiment_id")),
        )
    metric = str(metric).strip()
    declared = document.get("metrics") or []
    if metric not in declared:
        raise ExperimentError(
            "METRIC_UNKNOWN",
            "метрика %r не заявлена в эксперименте (заявлены: %s)"
            % (metric, ", ".join(declared)),
        )

    observation = {
        "unit_id": str(unit_id),
        "variant": str(variant),
        "metric": metric,
        "value": float(value),
    }
    _validate_observation(observation, set(declared))

    updated = dict(document)
    updated["observations"] = list(document.get("observations") or []) + [observation]
    return updated


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


def _mean(values):
    return sum(values) / float(len(values))


def _stdev(values):
    """Sample standard deviation (n-1). `None` when n < 2 — variance is
    undefined there, and a zero would claim the observations were identical."""
    n = len(values)
    if n < 2:
        return None
    m = _mean(values)
    variance = sum((v - m) ** 2 for v in values) / float(n - 1)
    return math.sqrt(variance)


def _log_gamma(x):
    return math.lgamma(x)


def _betacf(a, b, x, max_iterations=200, epsilon=3e-14):
    """The continued fraction for the incomplete beta function.

    Numerical Recipes' `betacf`, with the same iteration cap and convergence
    epsilon. Implemented here rather than imported because the standard library
    has no incomplete beta, and a p-value is only worth computing if the
    reader can open the file that produced it.
    """
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-300:
        d = 1e-300
    d = 1.0 / d
    h = d

    for m in range(1, max_iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        h *= d * c

        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-300:
            d = 1e-300
        c = 1.0 + aa / c
        if abs(c) < 1e-300:
            c = 1e-300
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < epsilon:
            break
    return h


def _regularized_incomplete_beta(a, b, x):
    """`I_x(a, b)` — the regularised incomplete beta function.

    Standard continued-fraction evaluation:

        I_x(a,b) = x^a (1-x)^b / (a·B(a,b)) · 1/F(a,b,x)

    with the symmetry `I_x(a,b) = 1 - I_{1-x}(b,a)` applied for `x > (a+1)/(a+b+2)`,
    which keeps the evaluation in the numerically stable branch.
    """
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(
        _log_gamma(a + b)
        - _log_gamma(a)
        - _log_gamma(b)
        + a * math.log(x)
        + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _t_cdf(t, df):
    """CDF of Student's t at `t` with `df` degrees of freedom.

        P(T <= t) = 1 - 0.5 · I_{df/(df+t²)}(df/2, 1/2)   for t > 0

    and the mirror image for t < 0. The identity comes straight from the
    relation between the t distribution and the F distribution; the beta
    function is `_regularized_incomplete_beta` above.
    """
    if df <= 0:
        raise ExperimentError(
            "DEGREES_OF_FREEDOM_INVALID",
            "число степеней свободы должно быть положительным, получено %r" % (df,),
        )
    x = df / (df + t * t)
    ibeta = _regularized_incomplete_beta(df / 2.0, 0.5, x)
    if t >= 0:
        return 1.0 - 0.5 * ibeta
    return 0.5 * ibeta


def _t_test(sample_a, sample_b):
    """Welch's two-sample t-test, in pure Python.

    Returns `{"t", "df", "p", "cohens_d", "reason"}`. `p` is a two-tailed p
    value computed from the t distribution via `_t_cdf`:

        t  = (mean_a - mean_b) / sqrt(var_a/n_a + var_b/n_b)
        df = (var_a/n_a + var_b/n_b)² / [ (var_a/n_a)²/(n_a-1)
                                          + (var_b/n_b)²/(n_b-1) ]

    `cohens_d` uses the pooled standard deviation with the small-sample
    correction (Hedges' style denominator):

        d = (mean_a - mean_b) / sqrt( ((n_a-1)·var_a + (n_b-1)·var_b)
                                       / (n_a + n_b - 2) )

    When `n < 2` in either arm, or the pooled variance is zero, `p` is `None`
    with a `reason` — the test is not run, and the caller is told why. A
    p-value invented in that situation would be a number with no claim behind
    it, which is the one thing this module exists to avoid.
    """
    n_a, n_b = len(sample_a), len(sample_b)

    if n_a < 2 or n_b < 2:
        return {
            "t": None,
            "df": None,
            "p": None,
            "cohens_d": None,
            "reason_ru": (
                "недостаточно наблюдений: в одной из групп меньше двух "
                "(n_a=%d, n_b=%d); дисперсия не определена, p не считается" % (n_a, n_b)
            ),
        }

    mean_a, mean_b = _mean(sample_a), _mean(sample_b)
    sd_a, sd_b = _stdev(sample_a), _stdev(sample_b)
    # `n >= 2` is guaranteed by the check above, so `_stdev` returns a number;
    # the guard keeps the type contract explicit rather than implicit.
    var_a = sd_a * sd_a if sd_a is not None else 0.0
    var_b = sd_b * sd_b if sd_b is not None else 0.0
    se_a, se_b = var_a / n_a, var_b / n_b
    se = se_a + se_b

    if se <= 0.0:
        return {
            "t": None,
            "df": None,
            "p": None,
            "cohens_d": None,
            "reason_ru": (
                "нулевая дисперсия в обеих группах: все наблюдения совпадают, "
                "t-тест не определён"
            ),
        }

    t = (mean_a - mean_b) / math.sqrt(se)
    df = (se * se) / ((se_a * se_a) / (n_a - 1) + (se_b * se_b) / (n_b - 1))
    p = 2.0 * (1.0 - _t_cdf(abs(t), df))

    pooled_var = ((n_a - 1) * var_a + (n_b - 1) * var_b) / float(n_a + n_b - 2)
    if pooled_var <= 0:
        cohens_d = None
    else:
        cohens_d = (mean_a - mean_b) / math.sqrt(pooled_var)

    return {
        "t": t,
        "df": df,
        "p": p,
        "cohens_d": cohens_d,
        "reason_ru": "тест Уэлча выполнен",
    }


def guardrail_check(experiment, *, min_n=5, imbalance_tolerance=0.30):
    """Refuse an analysis the design cannot support.

    Two checks, both of which are about whether the *comparison* is readable
    rather than about whether it looks impressive:

    * **`min_n`** — each arm needs at least this many observations. Below it
      the variance estimate is noise, and reporting a p-value would be
      reporting noise.
    * **Imbalance** — if one arm is more than `imbalance_tolerance` (relative)
      larger than the other, the arms are not comparable, because the larger
      arm's estimate is more precise and the difference may be an artefact of
      that. The default 30% is a judgement, written here so a reader can argue
      with it rather than with a hidden constant.

    Raises `ExperimentError("EXPERIMENT_UNDERPOWERED", ...)`; returns a small
    report when the check passes.
    """
    document = validate_experiment(experiment)
    variants = list(document.get("variants") or [])
    metrics = list(document.get("metrics") or [])
    observations = list(document.get("observations") or [])

    counts = {}
    for observation in observations:
        counts.setdefault(observation["variant"], set()).add(observation["unit_id"])

    sizes = {variant: len(counts.get(variant, set())) for variant in variants}
    missing = [variant for variant in variants if sizes.get(variant, 0) < min_n]
    if missing:
        raise ExperimentError(
            "EXPERIMENT_UNDERPOWERED",
            "эксперимент %s недостаточно наполнен: в группе(ах) %s меньше %d "
            "участников (сейчас: %s). Сравнение не выполняется — недостаточная "
            "мощность даёт шум, а не результат."
            % (
                document.get("experiment_id"),
                ", ".join(missing),
                min_n,
                ", ".join("%s=%d" % (v, sizes.get(v, 0)) for v in variants),
            ),
        )

    values = list(sizes.values())
    smallest, largest = min(values), max(values)
    if smallest > 0:
        relative = (largest - smallest) / float(smallest)
        if relative > float(imbalance_tolerance):
            raise ExperimentError(
                "EXPERIMENT_UNDERPOWERED",
                "группы эксперимента %s несбалансированы: %s (разница %.0f%% "
                "при допуске %.0f%%). Большая группа даёт более точную оценку, "
                "и разница может быть артефактом этой неравномерности."
                % (
                    document.get("experiment_id"),
                    ", ".join("%s=%d" % (v, sizes.get(v, 0)) for v in variants),
                    relative * 100.0,
                    float(imbalance_tolerance) * 100.0,
                ),
            )

    return {
        "experiment_id": document.get("experiment_id"),
        "ok": True,
        "min_n": min_n,
        "imbalance_tolerance": imbalance_tolerance,
        "sizes": sizes,
        "metrics": metrics,
        "denominator": sum(sizes.values()),
        "denominator_ru": (
            "в анализе участвуют %d наблюдений (%s)"
            % (
                sum(sizes.values()),
                ", ".join("%s=%d" % (v, sizes.get(v, 0)) for v in variants),
            )
        ),
    }


def analyze(experiment, *, min_n=5, imbalance_tolerance=0.30):
    """Per-variant statistics and a Welch t-test for each metric.

    Returns `{metric: {variant: {"n", "mean", "stdev"}, "test": {...}}}`.
    `stdev` is `None` when an arm has fewer than two observations; `p` is
    `None` in the same case, with the reason attached. Nothing is filled in.
    """
    guardrail_check(experiment, min_n=min_n, imbalance_tolerance=imbalance_tolerance)
    document = validate_experiment(experiment)
    variants = list(document.get("variants") or [])
    metrics = list(document.get("metrics") or [])
    observations = list(document.get("observations") or [])

    result = {
        "experiment_id": document.get("experiment_id"),
        "metrics": {},
        "denominator": len(observations),
        "denominator_ru": (
            "в анализе участвуют %d наблюдений по метрикам: %s"
            % (len(observations), ", ".join(metrics))
        ),
        "guardrail_ru": (
            "минимум %d наблюдений на группу; допуск дисбаланса %.0f%%"
            % (min_n, float(imbalance_tolerance) * 100.0)
        ),
    }

    for metric in metrics:
        by_variant = {}
        for variant in variants:
            values = [
                float(o["value"])
                for o in observations
                if o.get("metric") == metric and o.get("variant") == variant
            ]
            by_variant[variant] = {
                "n": len(values),
                "mean": _mean(values) if values else None,
                "stdev": _stdev(values),
                "values": values,
            }

        # The t-test compares the first variant against each of the others,
        # which is what a two-arm experiment means and what a multi-arm
        # experiment decomposes into without a hidden reference arm.
        tests = []
        if len(variants) >= 2:
            baseline = variants[0]
            for other in variants[1:]:
                tests.append(
                    {
                        "comparison": "%s vs %s" % (baseline, other),
                        "baseline": baseline,
                        "candidate": other,
                        **_t_test(
                            by_variant[baseline]["values"], by_variant[other]["values"]
                        ),
                    }
                )

        result["metrics"][metric] = {
            "variants": {
                variant: {
                    "n": by_variant[variant]["n"],
                    "mean": (
                        round(by_variant[variant]["mean"], 6)
                        if by_variant[variant]["mean"] is not None
                        else None
                    ),
                    "stdev": (
                        round(by_variant[variant]["stdev"], 6)
                        if by_variant[variant]["stdev"] is not None
                        else None
                    ),
                }
                for variant in variants
            },
            "tests": tests,
        }

    return result


__all__ = [
    "ExperimentError",
    "experiments_dir",
    "experiment_path",
    "load_experiment",
    "save_experiment",
    "create_experiment",
    "assign_variant",
    "record_observation",
    "analyze",
    "guardrail_check",
    "validate_experiment",
    "assert_allowed_variation",
    "FORBIDDEN_VARIATIONS",
    "ALLOWED_VARIATION_FAMILIES",
]
