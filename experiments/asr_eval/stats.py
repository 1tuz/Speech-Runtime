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
    if not 0 <= q <= 1:
        raise ValueError("percentile: q должно быть в [0, 1]")
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
    groups: list[str] | None = None,
) -> dict:
    """Парный bootstrap разницы WER (A − B) по одним и тем же высказываниям.

    errors_*: число ошибок (S+D+I) на высказывание; words: длина референса в словах.
    groups: идентификатор диктора или встречи на каждое высказывание;
    выбираются группы целиком, включая все записи обеих систем.
    """
    if not (len(errors_a) == len(errors_b) == len(words)):
        raise ValueError("paired_bootstrap: длины выборок должны совпадать")
    if not words:
        raise ValueError("paired_bootstrap: пустая выборка")
    if type(replications) is not int or replications <= 0:
        raise ValueError(f"paired_bootstrap: replications должно быть > 0, получено {replications}")
    if not 0 < alpha < 1:
        raise ValueError(f"paired_bootstrap: alpha должно быть в (0, 1), получено {alpha}")
    if any(type(value) is not int or value < 0 for value in words):
        raise ValueError("paired_bootstrap: words должны быть неотрицательными целыми")
    if any(type(value) is not int or value < 0 for value in list(errors_a) + list(errors_b)):
        raise ValueError("paired_bootstrap: errors должны быть неотрицательными целыми")
    n = len(words)
    if groups is None:
        clusters = [[i] for i in range(n)]
    else:
        if len(groups) != n or any(not isinstance(group, str) or not group.strip() for group in groups):
            raise ValueError("paired_bootstrap: нужен непустой ID группы для каждого высказывания")
        by_group: dict[str, list[int]] = {}
        for i, group in enumerate(groups):
            by_group.setdefault(group, []).append(i)
        clusters = list(by_group.values())
        if len(clusters) < 2:
            raise ValueError("paired_bootstrap: нужны как минимум две группы")
    total_words = sum(words)
    if total_words == 0:
        raise ValueError("paired_bootstrap: сумма слов равна нулю — WER не определён")
    wer_a = sum(errors_a) / total_words
    wer_b = sum(errors_b) / total_words
    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(replications):
        idx = [i for _ in clusters for i in clusters[rng.randrange(len(clusters))]]
        w = sum(words[i] for i in idx)
        if w == 0:
            continue
        diffs.append((sum(errors_a[i] for i in idx) - sum(errors_b[i] for i in idx)) / w)
    if not diffs:
        raise ValueError("paired_bootstrap: все реплики дали нулевую длину")
    diffs.sort()
    lo = percentile(diffs, alpha / 2)
    hi = percentile(diffs, 1 - alpha / 2)
    return {
        "utterances": n,
        "groups": len(clusters) if groups is not None else None,
        "skipped_zero_words": replications - len(diffs),
        "wer_a": wer_a,
        "wer_b": wer_b,
        "difference": wer_a - wer_b,
        "ci_low": lo,
        "ci_high": hi,
        "alpha": alpha,
        "replications": len(diffs),
        "seed": seed,
    }
