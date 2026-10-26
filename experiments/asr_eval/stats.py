"""Статистика различий (V8 §2.6): парный bootstrap и биномиальный ДИ.

Только стандартная библиотека; ГСЧ детерминирован (`random.Random(seed)`), поэтому
числа воспроизводимы между прогонами. Это не отменяет требования V8 §2.6: разрешающая
способность набора зависит от числа высказываний, а не от B.
"""

from __future__ import annotations

import math
import random


def binomial_halfwidth(p: float, n: int, z: float = 1.96) -> float | None:
    """Полуширина 95 % ДИ доли (нормальное приближение), в долях единицы."""
    if n <= 0:
        return None
    return z * math.sqrt(p * (1.0 - p) / n)


def percentile(sorted_values: list[float], q: float) -> float:
    """Эмпирический квантиль по сортированному списку (q ∈ [0, 1])."""
    if not sorted_values:
        raise ValueError("percentile: пустой список")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def paired_bootstrap(
    errors_a: list[int],
    words: list[int],
    errors_b: list[int],
    *,
    replications: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict:
    """Парный bootstrap разницы WER (A − B) по одним и тем же высказываниям.

    errors_*: число ошибок (S+D+I) на высказывание; words: длина референса в словах.
    """
    if not (len(errors_a) == len(errors_b) == len(words)):
        raise ValueError("paired_bootstrap: длины выборок должны совпадать")
    if not words:
        raise ValueError("paired_bootstrap: пустая выборка")
    if replications <= 0:
        raise ValueError(f"paired_bootstrap: replications должно быть > 0, получено {replications}")
    if not 0 < alpha < 1:
        raise ValueError(f"paired_bootstrap: alpha должно быть в (0, 1), получено {alpha}")
    if any(not isinstance(value, int) or value < 0 for value in words):
        raise ValueError("paired_bootstrap: words должны быть неотрицательными целыми")
    if any(not isinstance(value, int) or value < 0 for value in list(errors_a) + list(errors_b)):
        raise ValueError("paired_bootstrap: errors должны быть неотрицательными целыми")
    n = len(words)
    total_words = sum(words)
    if total_words == 0:
        raise ValueError("paired_bootstrap: сумма слов равна нулю — WER не определён")
    wer_a = sum(errors_a) / total_words
    wer_b = sum(errors_b) / total_words
    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(replications):
        idx = [rng.randrange(n) for _ in range(n)]
        w = sum(words[i] for i in idx)
        if w == 0:
            continue
        diffs.append((sum(errors_a[i] for i in idx) - sum(errors_b[i] for i in idx)) / w)
    if not diffs:
        raise ValueError("paired_bootstrap: все реплики дали нулевую длину")
    diffs.sort()
    lo = percentile(diffs, alpha / 2)
    hi = percentile(diffs, 1 - alpha / 2)
    non_positive = sum(1 for d in diffs if d <= 0) / len(diffs)
    non_negative = sum(1 for d in diffs if d >= 0) / len(diffs)
    p_two_sided = min(1.0, 2 * min(non_positive, non_negative))
    return {
        "utterances": n,
        "wer_a": wer_a,
        "wer_b": wer_b,
        "difference": wer_a - wer_b,
        "ci_low": lo,
        "ci_high": hi,
        "alpha": alpha,
        "replications": len(diffs),
        "seed": seed,
        "p_two_sided": p_two_sided,
    }
