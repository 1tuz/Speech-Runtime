#!/usr/bin/env python3
"""Триаж гипотез пилота без референсов: N систем на одних и тех же сегментах.

Инструмент не измеряет качество. Референса нет, поэтому единственное, что можно
утверждать — где и насколько системы расходятся между собой. WER между двумя
системами — расстояние между ними, а не доказательство того, что одна из них права.

Что считается:
1. Попарный WER/CER между системами по сегментам и по корпусу (`wer.py`).
2. Сайты расхождений: кластеры операций sub/del/ins выравнивания (множественное
   выравнивание через базовую систему = первая `--systems`) с окном контекста ±3.
3. Формы терминов по системам по словарю `terms.json` (`terms.py`): exact_latin /
   exact_other (кириллица) / translit / corrupt / miss.
4. Очередь ручной выверки, отсортированная по числу расходящихся систем и весу термина.

Нормализация, выравнивание и терминология переиспользуются из `experiments/asr_eval`
без копирования логики: `norm.py` (norm-v2), `align.py`, `wer.py`, `terms.py`.

Запуск:
    python3 experiments/pilot/review.py \\
        --systems gigaam=…/gigaam-hypotheses.tsv \\
        --systems parakeet=…/parakeet-hypotheses.tsv \\
        --systems whisper=…/whisper-hypotheses.tsv \\
        --out-dir …/pilot-review \\
        --terms experiments/asr_eval/terms.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TOOL_VERSION = "pilot-triage-v1"
HERE = Path(__file__).resolve().parent
ASR_EVAL = HERE.parent / "asr_eval"
if str(ASR_EVAL) not in sys.path:
    sys.path.insert(0, str(ASR_EVAL))

import align  # noqa: E402
import norm  # noqa: E402
import terms as terms_mod  # noqa: E402
import wer as wer_mod  # noqa: E402

#: Окно контекста — то же ±3 токена, что в `terms.WINDOW`, чтобы сайт термина и
#: контекст вокруг него читались в одних единицах.
WINDOW = terms_mod.WINDOW

#: Вес термина для сортировки очереди. Это порядок триажа, а не метрика качества:
#: инструменты и команды важнее служебных словоформ и чисел при ручной выверке.
CLASS_WEIGHT = {"tools": 4, "command": 4, "abbrev": 3, "translit": 2, "wordform": 1, "number": 1}
DEFAULT_WEIGHT = 2

#: Порядок доминирования вердиктов при свёртке нескольких вхождений термина.
KIND_RANK = {
    terms_mod.EXACT_LATIN: 4,
    terms_mod.EXACT_OTHER: 3,
    terms_mod.TRANSLIT: 2,
    terms_mod.CORRUPT: 1,
    terms_mod.MISS: 0,
}

SYSTEM_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
EMPTY_SPAN = "\u2205"  # ∅ — система не сказала ничего на этом участке


# ---------------------------------------------------------------- вход


def read_hypotheses(path: Path) -> dict[str, str]:
    """`<id>\\t<текст>`; дубликат или пустой id — ошибка входа, а не тихая перезапись."""
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                raise SystemExit(f"{path}:{lineno}: ожидались 2 поля через TAB, получено {len(parts)}")
            seg_id = parts[0].strip()
            if not seg_id:
                raise SystemExit(f"{path}:{lineno}: пустой id")
            if seg_id in out:
                raise SystemExit(f"{path}:{lineno}: дубликат id '{seg_id}'")
            out[seg_id] = parts[1]
    return out


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_system(spec: str) -> tuple[str, Path]:
    name, sep, raw_path = spec.partition("=")
    name = name.strip()
    raw_path = raw_path.strip()
    if not sep or not name or not raw_path:
        raise SystemExit(f"--systems {spec!r}: ожидалось имя=путь")
    if not SYSTEM_NAME_RE.match(name):
        raise SystemExit(f"--systems {spec!r}: имя должно быть [A-Za-z0-9_.-]+, без TAB и пробелов")
    path = Path(raw_path)
    if not path.is_file():
        raise SystemExit(f"--systems {spec!r}: файл не найден: {path}")
    return name, path


# ------------------------------------------------------- выравнивание


def pair_sites(base_tokens: list[str], other_tokens: list[str]) -> list[dict]:
    """Кластеры подряд идущих sub/del/ins → сайты расхождения в координатах base.

    Возвращает [{br0, br1, h0, h1}], где [br0, br1) — спав в базовых токенах
    (может быть пустым для чистой вставки), [h0, h1) — спав в токенах системы.
    """
    ops, _ = align.align(base_tokens, other_tokens)
    sites: list[dict] = []
    cur: dict | None = None
    ref_pos = 0  # сколько базовых токенов пройдено
    hyp_pos = 0
    for op, i, j in ops:
        if op == align.MATCH:
            if cur is not None:
                sites.append(_close_run(cur, ref_pos, hyp_pos))
                cur = None
            if i is not None:
                ref_pos = i + 1
            if j is not None:
                hyp_pos = j + 1
            continue
        if cur is None:
            cur = {"refs": [], "hyps": [], "ref_at": ref_pos, "hyp_at": hyp_pos}
        if i is not None:
            cur["refs"].append(i)
            ref_pos = i + 1
        if j is not None:
            cur["hyps"].append(j)
            hyp_pos = j + 1
    if cur is not None:
        sites.append(_close_run(cur, ref_pos, hyp_pos))
    return sites


def _close_run(run: dict, ref_pos: int, hyp_pos: int) -> dict:
    refs, hyps = run["refs"], run["hyps"]
    if refs:
        br0, br1 = min(refs), max(refs) + 1
    else:
        br0 = br1 = run["ref_at"]
    if hyps:
        h0, h1 = min(hyps), max(hyps) + 1
    else:
        h0 = h1 = run["hyp_at"]
    return {"br0": br0, "br1": br1, "h0": h0, "h1": h1}


def intersects(a0: int, a1: int, b0: int, b1: int) -> bool:
    """Пересечение спавов; пустой спав считается точкой на границе."""
    if a0 == a1 and b0 == b1:
        return a0 == b0
    if a0 == a1:
        return b0 <= a0 <= b1
    if b0 == b1:
        return a0 <= b0 <= a1
    return max(a0, b0) < min(a1, b1)


def project_span(
    start: int,
    end: int,
    ref_to_hyp: dict[int, int],
    extra: list[tuple[int, int]],
    out_len: int,
) -> tuple[int, int]:
    """Спав базовой системы → спав этой системы.

    Учитываются и отображённые индексы выравнивания, и собственные спавны сайтов
    этой системы (нужны для вставок, у которых базового спава нет).
    """
    points: list[int] = []
    for index in range(start, end):
        mapped = ref_to_hyp.get(index)
        if mapped is not None:
            points.append(mapped)
    for lo, hi in extra:
        if hi > lo:
            points.extend((lo, hi - 1))
    if points:
        lo, hi = min(points), max(points) + 1
        return max(0, min(lo, out_len)), max(0, min(hi, out_len))
    if extra:
        lo, hi = extra[0]
        return max(0, min(lo, out_len)), max(0, min(hi, out_len))
    # Пустой спав: проецируем границу на ближайшее отображённое слева.
    boundary = 0
    for index in range(start - 1, -1, -1):
        mapped = ref_to_hyp.get(index)
        if mapped is not None:
            boundary = mapped + 1
            break
    boundary = max(0, min(boundary, out_len))
    return boundary, boundary


# ------------------------------------------------------------- термины


def term_scan(
    vocab: terms_mod.Vocabulary,
    tokens: list[str],
    raw_tokens: list[str],
    wanted: set[str] | None = None,
) -> dict[str, dict]:
    """Формы словаря в одном тексте → {term_id: {kind, forms, spans}}.

    Позиционного окна здесь нет: референса для окна не существует. Совпадение
    словарной формы ищется по всему сегменту (`Vocabulary.occurrences`), искажение —
    по латинским токенам, которых нет среди найденных форм.
    """
    found: dict[str, dict] = {}
    occurrences = vocab.occurrences(tokens)
    for occ in occurrences:
        if wanted is not None and occ.term_id not in wanted:
            continue
        entry = found.setdefault(occ.term_id, {"kind": terms_mod.MISS, "forms": [], "spans": []})
        surface = " ".join(raw_tokens[occ.start:occ.end])
        entry["spans"].append((occ.start, occ.end))
        if surface not in entry["forms"]:
            entry["forms"].append(surface)
        if KIND_RANK[occ.form_kind] > KIND_RANK[entry["kind"]]:
            entry["kind"] = occ.form_kind
    # Искажения: только по терминам, которых в этом тексте нет вовсе, и только по
    # латинским токенам — `corrupt_matches` уже ограничивает сравнение формами термина.
    candidates = [tok for tok in tokens if tok.isascii() and tok.isalnum() and len(tok) >= 3]
    if candidates:
        # Фильтры триажа поверх эвристики terms.py: без позиционного окна (референса
        # нет) `corrupt` срабатывает на чужих словах. Токен, который сам является
        # формой другого термина, — подмена термина, а не искажение. Токен с другой
        # первой буквой не считается искажением своего термина.
        known_forms = {form[0] for form, _, _ in vocab.forms if len(form) == 1}
        usable = [tok for tok in candidates if tok not in known_forms]
        for term in vocab.terms:
            if term.id in found or (wanted is not None and term.id not in wanted):
                continue
            initials = {
                form[0][0]
                for form in term.accept_norm
                if len(form) == 1 and form[0].isascii()
            }
            for tok in usable:
                if initials and tok[0] not in initials:
                    continue
                if vocab.corrupt_matches(term, tok):
                    entry = found.setdefault(term.id, {"kind": terms_mod.CORRUPT, "forms": [], "spans": []})
                    if tok not in entry["forms"]:
                        entry["forms"].append(tok)
                    break
    return found


def term_weight(vocab: terms_mod.Vocabulary, term_id: str | None) -> int:
    if not term_id:
        return 0
    return CLASS_WEIGHT.get(vocab.by_id[term_id].cls, DEFAULT_WEIGHT)


# --------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Триаж гипотез без референса: сайты расхождений и формы терминов.",
    )
    parser.add_argument(
        "--systems",
        action="append",
        required=True,
        metavar="NAME=PATH.tsv",
        help="система гипотез `<id>\\t<текст>`; минимум две, первая — база выравнивания",
    )
    parser.add_argument(
        "--out-dir",
        default="pilot-review",
        metavar="DIR",
        help="каталог для review-queue.tsv и review-summary.json (по умолчанию ./pilot-review)",
    )
    parser.add_argument(
        "--terms",
        default=str(ASR_EVAL / "terms.json"),
        metavar="terms.json",
        help="словарь терминов (по умолчанию experiments/asr_eval/terms.json)",
    )
    args = parser.parse_args(argv)

    systems: list[str] = []
    paths: dict[str, Path] = {}
    for spec in args.systems:
        name, path = parse_system(spec)
        if name in paths:
            raise SystemExit(f"--systems: дубликат имени '{name}'")
        systems.append(name)
        paths[name] = path
    if len(systems) < 2:
        raise SystemExit("--systems: нужны минимум две системы")

    terms_path = Path(args.terms)
    if not terms_path.is_file():
        raise SystemExit(f"--terms: файл не найден: {terms_path}")
    vocab = terms_mod.Vocabulary.from_path(str(terms_path))

    texts = {name: read_hypotheses(paths[name]) for name in systems}
    base = systems[0]
    seg_ids = sorted(texts[base])
    if not seg_ids:
        raise SystemExit(f"{paths[base]}: нет сегментов")
    for name in systems[1:]:
        missing = sorted(set(seg_ids) - set(texts[name]))
        extra = sorted(set(texts[name]) - set(seg_ids))
        if missing or extra:
            detail = []
            if missing:
                detail.append(f"нет {len(missing)}: {', '.join(missing[:5])}")
            if extra:
                detail.append(f"лишние {len(extra)}: {', '.join(extra[:5])}")
            raise SystemExit(f"{paths[name]}: набор id не совпадает с '{base}' ({'; '.join(detail)})")

    tokens_by = {name: {sid: norm.tokens(texts[name][sid]) for sid in seg_ids} for name in systems}
    raw_by = {name: {sid: norm.tokens(texts[name][sid], keep_case=True) for sid in seg_ids} for name in systems}

    # --- (a) попарный WER между системами: расстояние, не качество ---
    pairs: list[dict] = []
    for ref_name in systems:
        for hyp_name in systems:
            if ref_name == hyp_name:
                continue
            utterances = [
                {"id": sid, "reference": texts[ref_name][sid], "hypothesis": texts[hyp_name][sid]}
                for sid in seg_ids
            ]
            scored = wer_mod.corpus(utterances)
            rows = [
                {
                    "segment_id": row["id"],
                    "wer": _round(row["errors"] / row["words"]) if row["words"] else None,
                    "cer": _round(row["char_errors"] / row["chars"]) if row["chars"] else None,
                    "words": row["words"],
                    "errors": row["errors"],
                }
                for row in scored["per_utterance"]
            ]
            pairs.append(
                {
                    "a": ref_name,
                    "b": hyp_name,
                    "note": "расстояние между системами; направление асимметрично из-за снятия филлеров",
                    "corpus_wer": _round(scored["wer"]),
                    "corpus_cer": _round(scored["cer"]),
                    "words": scored["words"],
                    "errors": scored["errors"],
                    "segments": rows,
                }
            )

    # --- (b) сайты расхождений + (c) формы терминов + (d) очередь ---
    queue: list[dict] = []
    sites_per_segment: dict[str, int] = {}
    kind_cells: dict[str, dict[str, int]] = {name: {kind: 0 for kind in KIND_RANK} for name in systems}
    kind_totals: dict[str, int] = {kind: 0 for kind in KIND_RANK}
    term_table: dict[str, dict] = {}

    for sid in seg_ids:
        seg_tokens = {name: tokens_by[name][sid] for name in systems}
        seg_raw = {name: raw_by[name][sid] for name in systems}
        scans = {name: term_scan(vocab, seg_tokens[name], seg_raw[name]) for name in systems}

        # Формы терминов по системам — только по терминам, найденным хоть где-то.
        present = sorted({tid for name in systems for tid in scans[name]})
        for tid in present:
            record = term_table.setdefault(tid, {"term_id": tid, "class": vocab.by_id[tid].cls, "systems": {}})
            for name in systems:
                entry = scans[name].get(tid)
                if entry is None:
                    record["systems"][name] = {"kind": terms_mod.MISS, "forms": []}
                    kind_cells[name][terms_mod.MISS] += 1
                    kind_totals[terms_mod.MISS] += 1
                    continue
                record["systems"][name] = {"kind": entry["kind"], "forms": list(entry["forms"])}
                kind_cells[name][entry["kind"]] += 1
                kind_totals[entry["kind"]] += 1

        ref_to_hyp_by: dict[str, dict[int, int]] = {}
        entries: list[dict] = []
        for name in systems[1:]:
            _, ref_to_hyp = align.align(seg_tokens[base], seg_tokens[name])
            ref_to_hyp_by[name] = ref_to_hyp
            for site in pair_sites(seg_tokens[base], seg_tokens[name]):
                entries.append({"sys": name, **site})

        # Множественное выравнивание: объединяем сайты пар с пересекающимися
        # базовыми спавами. Пересечение транзитивно склеивает цепочки.
        clusters: list[dict] = []
        order = {name: index for index, name in enumerate(systems)}
        entries.sort(key=lambda e: (e["br0"], e["br1"], order[e["sys"]], e["h0"], e["h1"]))
        for entry in entries:
            hits = [
                index
                for index, cluster in enumerate(clusters)
                if any(intersects(entry["br0"], entry["br1"], s, e) for s, e in cluster["base_spans"])
            ]
            if not hits:
                clusters.append({"base_spans": [(entry["br0"], entry["br1"])], "members": [entry]})
                continue
            first = clusters[hits[0]]
            first["base_spans"].append((entry["br0"], entry["br1"]))
            first["members"].append(entry)
            for index in reversed(hits[1:]):
                other = clusters.pop(index)
                first["base_spans"].extend(other["base_spans"])
                first["members"].extend(other["members"])

        for cluster in clusters:
            starts = [s for s, _ in cluster["base_spans"]]
            ends = [e for _, e in cluster["base_spans"]]
            b_start, b_end = min(starts), max(ends)

            spans: dict[str, tuple[int, int]] = {base: (b_start, b_end)}
            for name in systems[1:]:
                extra = [
                    (member["h0"], member["h1"])
                    for member in cluster["members"]
                    if member["sys"] == name
                ]
                spans[name] = project_span(
                    b_start, b_end, ref_to_hyp_by[name], extra, len(seg_tokens[name])
                )

            forms = {
                name: (
                    " ".join(seg_tokens[name][spans[name][0]:spans[name][1]])
                    if spans[name][1] > spans[name][0]
                    else EMPTY_SPAN
                )
                for name in systems
            }
            counts: dict[str, int] = {}
            for name in systems:
                counts[forms[name]] = counts.get(forms[name], 0) + 1
            top = max(counts.values())
            # Число расходящихся систем: все, кто не входит в самую большую группу
            # совпавших. Если не совпал никто (все формы разные) — расходятся все.
            n_varying = len(systems) if top == 1 else len(systems) - top

            term_ids = _site_terms(scans, spans, systems)
            chosen = (
                sorted(term_ids, key=lambda tid: (-term_weight(vocab, tid), tid))[0] if term_ids else ""
            )

            before = seg_tokens[base][max(0, b_start - WINDOW):b_start]
            after = seg_tokens[base][b_end:b_end + WINDOW]
            site_text = forms[base]
            context = " ".join(before + ["[" + site_text + "]"] + after)

            queue.append(
                {
                    "segment_id": sid,
                    "position": b_start + 1,
                    "context": context,
                    "forms": forms,
                    "n_varying": n_varying,
                    "term": chosen,
                    "_weight": term_weight(vocab, chosen or None),
                }
            )

        sites_per_segment[sid] = len(clusters)

    queue.sort(
        key=lambda row: (
            -row["n_varying"],
            -row["_weight"],
            row["segment_id"],
            row["position"],
            row["term"],
        )
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    queue_path = out_dir / "review-queue.tsv"
    summary_path = out_dir / "review-summary.json"

    header = ["segment_id", "position", "context", *systems, "n_varying", "term"]
    lines = ["\t".join(header)]
    for row in queue:
        fields = [
            row["segment_id"],
            str(row["position"]),
            row["context"],
            *[row["forms"][name] for name in systems],
            str(row["n_varying"]),
            row["term"],
        ]
        lines.append("\t".join(_clean(field) for field in fields))
    queue_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    by_varying: dict[str, int] = {}
    for row in queue:
        key = str(row["n_varying"])
        by_varying[key] = by_varying.get(key, 0) + 1

    summary = {
        "tool": TOOL_VERSION,
        "norm_version": norm.VERSION,
        "terms_version": vocab.version or None,
        "base_system": base,
        "systems": systems,
        "system_count": len(systems),
        "segments": len(seg_ids),
        "segment_ids": seg_ids,
        "inputs": {
            name: {"path": paths[name].name, "sha256": sha256_path(paths[name])} for name in systems
        },
        "terms_input": {
            "path": terms_path.name,
            "version": vocab.version or None,
            "sha256": sha256_path(terms_path),
        },
        "pairwise_wer": pairs,
        "sites": {
            "total": len(queue),
            "with_term": sum(1 for row in queue if row["term"]),
            "by_n_varying": dict(sorted(by_varying.items())),
            "per_segment": dict(sorted(sites_per_segment.items())),
        },
        "term_forms": {
            "terms": len(term_table),
            "cells": kind_totals,
            "per_system": kind_cells,
            "table": [term_table[tid] for tid in sorted(term_table)],
        },
        "notes": [
            "Референсов нет: числа описывают расхождения между системами, а не качество.",
            "WER пары асимметричен: филлеры снимаются из гипотезы, но не из референса.",
            "Сайты расхождения получены множественным выравниванием через базовую систему.",
            "Формы терминов ищутся по всему сегменту: позиционного окна без референса нет.",
            "Искажение (corrupt) — эвристика terms.py; поверх неё отсечены токены-формы других терминов и токены с другой первой буквой.",
        ],
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"segments={len(seg_ids)} systems={len(systems)} sites={len(queue)} terms={len(term_table)}")
    for pair in pairs:
        print(f"  wer {pair['a']}->{pair['b']}: {pair['corpus_wer']}")
    print(f"queue: {queue_path}")
    print(f"summary: {summary_path}")
    return 0


def _site_terms(scans: dict[str, dict], spans: dict[str, tuple[int, int]], systems: list[str]) -> set[str]:
    """Термины, чьи вхождения попадают в спавн сайта хотя бы в одной системе."""
    found: set[str] = set()
    for name in systems:
        lo, hi = spans[name]
        for term_id, entry in scans[name].items():
            for start, end in entry["spans"]:
                if intersects(start, end, lo, hi):
                    found.add(term_id)
                    break
    return found


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _clean(field: str) -> str:
    return field.replace("\t", " ").replace("\n", " ")


if __name__ == "__main__":
    sys.exit(main())
