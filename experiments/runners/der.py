#!/usr/bin/env python3
"""DER/JER по RTTM: гипотеза против эталона из CSV Veche-bench.

Реализует стандартный DER из NIST md-eval: ошибка = (пропуск + ложная речь +
перепутанный говорящий) / длительность эталонной речи, с collar вокруг границ
и опцией подсчёта перекрытий. Коллар нужен, потому что границы реплик в CSV
заданы сценарием, а не разметкой прослушиванием.

    python3 experiments/runners/der.py --ref rttm/ref.rttm --hyp diar/x.rttm
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict


def load_rttm(path: str) -> dict[str, list[tuple[float, float, str]]]:
    out: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    for line in open(path, encoding="utf-8"):
        fields = line.split()
        if len(fields) < 8 or fields[0] != "SPEAKER":
            continue
        start = float(fields[3])
        out[fields[1]].append((start, start + float(fields[4]), fields[7]))
    return out


def timeline(segments: list[tuple[float, float, str]], step: float) -> list[set[str]]:
    if not segments:
        return []
    end = max(e for _, e, _ in segments)
    slots = int(end / step) + 2
    frames: list[set[str]] = [set() for _ in range(slots)]
    for start, stop, speaker in segments:
        first = int(start / step)
        last = min(int(stop / step), slots - 1)
        for index in range(first, last + 1):
            frames[index].add(speaker)
    return frames



def jaccard_error(ref_frames: list[set[str]], hyp_frames: list[set[str]]) -> dict:
    """JER = 1 − средний Jaccard по говорящим эталона (Davidson et al., 2019).

    Для каждого говорящего эталона берётся говорящий гипотезы с максимальным
    пересечением кадров (жадно, без повторного использования). Jaccard —
    |пересечение| / |объединение|; при пустом объединении вклад 0.
    """
    ref_by: dict[str, set[int]] = defaultdict(set)
    hyp_by: dict[str, set[int]] = defaultdict(set)
    for index, (ref_set, hyp_set) in enumerate(zip(ref_frames, hyp_frames)):
        for speaker in ref_set:
            ref_by[speaker].add(index)
        for speaker in hyp_set:
            hyp_by[speaker].add(index)

    pairs = []
    for r, r_frames in ref_by.items():
        for h, h_frames in hyp_by.items():
            inter = len(r_frames & h_frames)
            if inter:
                pairs.append((inter, r, h))
    pairs.sort(reverse=True)
    matched: dict[str, str] = {}
    used: set[str] = set()
    for _, r, h in pairs:
        if r in matched or h in used:
            continue
        matched[r] = h
        used.add(h)

    scores = []
    for speaker, r_frames in ref_by.items():
        partner = matched.get(speaker)
        h_frames = hyp_by.get(partner, set()) if partner else set()
        union = len(r_frames | h_frames)
        scores.append(len(r_frames & h_frames) / union if union else 0.0)
    mean = sum(scores) / len(scores) if scores else 0.0
    return {"jer": 1.0 - mean, "mean_jaccard": mean, "speakers_scored": len(scores)}


def score(ref: list[tuple[float, float, str]], hyp: list[tuple[float, float, str]],
          step: float, collar: float, ignore_overlap: bool) -> dict:
    ref_frames = timeline(ref, step)
    hyp_frames = timeline(hyp, step)
    length = max(len(ref_frames), len(hyp_frames))
    ref_frames += [set() for _ in range(length - len(ref_frames))]
    hyp_frames += [set() for _ in range(length - len(hyp_frames))]

    # Сопоставление говорящих по максимуму пересечения — как в md-eval.
    overlap: dict[tuple[str, str], int] = defaultdict(int)
    for ref_set, hyp_set in zip(ref_frames, hyp_frames):
        for r in ref_set:
            for h in hyp_set:
                overlap[(r, h)] += 1
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for (r, h), count in sorted(overlap.items(), key=lambda item: -item[1]):
        if r in mapping or h in used:
            continue
        mapping[r] = h
        used.add(h)

    collar_frames = int(collar / step)
    speech = miss = false_alarm = confusion = 0
    for index, (ref_set, hyp_set) in enumerate(zip(ref_frames, hyp_frames)):
        if collar_frames and any(
            abs(index - int(start / step)) <= collar_frames or abs(index - int(stop / step)) <= collar_frames
            for start, stop, _ in ref
        ):
            continue
        if ignore_overlap and len(ref_set) > 1:
            continue
        if not ref_set and not hyp_set:
            continue
        if not ref_set:
            false_alarm += 1
            continue
        speech += 1
        if not hyp_set:
            miss += 1
            continue
        matched = {mapping.get(r) for r in ref_set}
        if not (matched & hyp_set):
            confusion += 1
        elif len(ref_set) > len(hyp_set & matched):
            miss += 1

    total = max(speech, 1)
    overlap_frames = sum(1 for ref_set in ref_frames if len(ref_set) > 1)
    return {
        "ref_speech_s": round(speech * step, 2),
        "miss_s": round(miss * step, 2),
        "false_alarm_s": round(false_alarm * step, 2),
        "confusion_s": round(confusion * step, 2),
        "der": (miss + false_alarm + confusion) / total,
        "collar_s": collar,
        "ignore_overlap": ignore_overlap,
        "speakers_ref": len({s for _, _, s in ref}),
        "speakers_hyp": len({s for _, _, s in hyp}),
        "ref_overlap_s": round(overlap_frames * step, 2),
        **jaccard_error(ref_frames, hyp_frames),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", required=True)
    parser.add_argument("--hyp", required=True)
    parser.add_argument("--step", type=float, default=0.01)
    parser.add_argument("--collar", type=float, default=0.25)
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    ref = load_rttm(args.ref)
    hyp = load_rttm(args.hyp)
    reports = {}
    for key in sorted(set(ref) | set(hyp)):
        for ignore in (False, True):
            label = "with_overlap" if not ignore else "no_overlap"
            reports[f"{key}/{label}"] = score(ref.get(key, []), hyp.get(key, []),
                                              args.step, args.collar, ignore)
    text = json.dumps(reports, ensure_ascii=False, indent=2)
    if args.json:
        open(args.json, "w", encoding="utf-8").write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
