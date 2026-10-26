"""Выравнивание последовательностей токенов (Levenshtein) с операциями S/I/D.

Используется и для WER (wer.py), и для позиционного сопоставления терминов (terms.py):
окно ±N слов вокруг термина в референсе требует карты «индекс референса → индекс
гипотезы», которую даёт только выравнивание (V8 §3.2).
"""

from __future__ import annotations

MATCH = "match"
SUB = "sub"
DEL = "del"
INS = "ins"


def align(ref: list[str], hyp: list[str]) -> tuple[list[tuple[str, int | None, int | None]], dict[int, int]]:
    """Выравнивает ref к hyp.

    Возвращает (ops, ref_to_hyp):
    - ops: список (op, ref_idx, hyp_idx); op ∈ {match, sub, del, ins};
    - ref_to_hyp: индекс референса → индекс гипотезы для match/sub.
    """
    n, m = len(ref), len(hyp)
    # dp[i][j] — стоимость выравнивания ref[:i] и hyp[:j]
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i
    for j in range(1, m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        ri = ref[i - 1]
        row, prev = dp[i], dp[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ri == hyp[j - 1] else 1
            row[j] = min(prev[j - 1] + cost, prev[j] + 1, row[j - 1] + 1)

    ops: list[tuple[str, int | None, int | None]] = []
    ref_to_hyp: dict[int, int] = {}
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            if dp[i][j] == dp[i - 1][j - 1] + cost:
                op = MATCH if cost == 0 else SUB
                ops.append((op, i - 1, j - 1))
                ref_to_hyp[i - 1] = j - 1
                i, j = i - 1, j - 1
                continue
        if i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            ops.append((DEL, i - 1, None))
            i -= 1
            continue
        ops.append((INS, None, j - 1))
        j -= 1
    ops.reverse()
    return ops, ref_to_hyp


def edit_counts(ops: list[tuple[str, int | None, int | None]]) -> tuple[int, int, int]:
    """(substitutions, deletions, insertions) по списку операций."""
    sub = sum(1 for op, _, _ in ops if op == SUB)
    dele = sum(1 for op, _, _ in ops if op == DEL)
    ins = sum(1 for op, _, _ in ops if op == INS)
    return sub, dele, ins


def char_distance(a: str, b: str) -> int:
    """Расстояние Левенштейна по символам (для `corrupt`, V8 §3.2)."""
    return distance(list(a), list(b))


def distance(a: list[str], b: list[str]) -> int:
    """Расстояние Левенштейна на уровне токенов."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ta in enumerate(a, 1):
        cur = [i]
        for j, tb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ta != tb)))
        prev = cur
    return prev[-1]
