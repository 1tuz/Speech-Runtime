"""CLI офлайн-скорера: validate / score / compare.

Примеры:
    python3 experiments/asr_eval/cli.py validate --dataset datasets/ru-it
    python3 experiments/asr_eval/cli.py score --dataset datasets/ru-it --hypotheses hyp.tsv
    python3 experiments/asr_eval/cli.py compare --dataset datasets/ru-it --a a.tsv --b b.tsv
    python3 experiments/asr_eval/cli.py score --report asr-bench-report.json --terms terms.json

Формат гипотез внешнего раннера (V8 §7.1): `<id>\\t<текст>`, id = имя wav без расширения.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import json
import sys

import dataset as dataset_mod
import norm
import stats
import terms as terms_mod
import wer as wer_mod


def _read_hypotheses(path: str) -> dict[str, str]:
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


def _check_coverage(refs: dict[str, str], hypotheses: dict[str, str], label: str) -> None:
    """Гипотезы и референсы должны совпадать по набору id: иначе скор тихо неполон."""
    missing = sorted(set(refs) - set(hypotheses))
    if missing:
        raise SystemExit(
            f"{label}: нет гипотез для {len(missing)} высказываний, первые: {', '.join(missing[:5])}"
        )
    extra = sorted(set(hypotheses) - set(refs))
    if extra:
        raise SystemExit(
            f"{label}: гипотезы для {len(extra)} неизвестных id, первые: {', '.join(extra[:5])}"
        )


def _pairs_from_dataset(dataset_dir: str, hypotheses: dict[str, str]) -> list[dict]:
    refs = dataset_mod.load_manifest_map(dataset_dir)
    _check_coverage(refs, hypotheses, "гипотезы")
    return [{"id": seg_id, "reference": refs[seg_id], "hypothesis": hypotheses[seg_id]} for seg_id in refs]


def _pairs_from_report(path: str) -> list[dict]:
    """Ровно одна система в отчёте: смешение движков в одном WER бессмысленно."""
    with open(path, encoding="utf-8") as fh:
        report = json.load(fh)
    results = report.get("results")
    if not isinstance(results, list) or not results:
        raise SystemExit(f"{path}: не найдено results[*]")
    if len(results) != 1:
        names = [str(result.get("engine", result.get("model", f"#{index}")))
                 for index, result in enumerate(results)]
        raise SystemExit(
            f"{path}: в отчёте {len(results)} систем ({', '.join(names)}); "
            "нужен отчёт с одной системой — иначе WER смешивает движки"
        )
    pairs: list[dict] = []
    seen: set[str] = set()
    for utt in results[0].get("utteranceResults", []):
        if "reference" not in utt or "hypothesis" not in utt:
            raise SystemExit(f"{path}: utteranceResults без reference/hypothesis")
        seg_id = utt.get("id")
        if not isinstance(seg_id, str) or not seg_id.strip() or seg_id in seen:
            raise SystemExit(f"{path}: пустой или повторный id")
        seen.add(seg_id)
        pairs.append(
            {
                "id": seg_id,
                "reference": utt["reference"],
                "hypothesis": utt["hypothesis"],
            }
        )
    if not pairs:
        raise SystemExit(f"{path}: пустой utteranceResults")
    return pairs


SCORER_VERSION = "asr-eval-v2"


def _sha(path: str | None) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def command_accuracy(pairs: list[dict]) -> dict:
    """Доля размеченных команд, точно воспроизведённых как непрерывная последовательность токенов."""
    total = correct = 0
    for pair in pairs:
        commands = pair.get("commands", [])
        if not isinstance(commands, list) or any(not isinstance(c, str) or not c.strip() for c in commands):
            raise ValueError("commands должно быть списком непустых строк")
        ref = pair["reference"].split()
        hyp = pair["hypothesis"].split()
        ref_used = [False] * len(ref)
        hyp_used = [False] * len(hyp)
        for command in commands:
            tokens = command.split()
            def claim(source, used):
                for start in range(len(source) - len(tokens) + 1):
                    end = start + len(tokens)
                    if source[start:end] == tokens and not any(used[start:end]):
                        used[start:end] = [True] * len(tokens)
                        return True
                return False
            if not claim(ref, ref_used):
                raise ValueError(f"размеченная команда отсутствует в референсе: {command}")
            total += 1
            correct += claim(hyp, hyp_used)
    return {"version": "command-exact-v1", "total": total, "correct": correct,
            "accuracy": correct / total if total else None}


def _score(pairs: list[dict], vocab: terms_mod.Vocabulary | None) -> dict:
    wer_report = wer_mod.corpus(pairs)
    out = {
        "scorer_version": SCORER_VERSION,
        "norm_version": norm.VERSION,
        "commands": command_accuracy(pairs),
        "term_metric_version": "term-v2",
        "vocabulary": vocab.identity() if vocab is not None else None,
        "utterances": wer_report["utterances"],
        "wer": wer_report["wer"],
        "cer": wer_report["cer"],
        "substitutions": wer_report["substitutions"],
        "deletions": wer_report["deletions"],
        "insertions": wer_report["insertions"],
        "words": wer_report["words"],
        "terms": None,
    }
    if vocab is not None:
        rows = [terms_mod.classify(p["reference"], p["hypothesis"], vocab) for p in pairs]
        out["terms"] = terms_mod.aggregate(rows)
    return out


def _print_summary(name: str, report: dict) -> None:
    wer = report["wer"]
    cer = report["cer"]
    vocab = report.get("vocabulary")
    vocab_label = "нет" if vocab is None else f"{vocab['version']} sha256={vocab['sha256'][:12]}"
    print(
        f"== {name}: utterances={report['utterances']} words={report['words']} "
        f"norm={report['norm_version']} terms={vocab_label}"
    )
    print(
        "WER={:.2f}% CER={:.2f}% S={} D={} I={}".format(
            100 * wer if wer is not None else float("nan"),
            100 * cer if cer is not None else float("nan"),
            report["substitutions"],
            report["deletions"],
            report["insertions"],
        )
    )
    commands = report["commands"]
    accuracy = commands["accuracy"]
    print(f"commands: correct={commands['correct']} total={commands['total']} accuracy={accuracy if accuracy is not None else 'n/a'}")
    terms_report = report.get("terms")
    if terms_report:
        def fmt(value: float | None) -> str:
            return "n/a" if value is None else f"{100 * value:.1f}%"

        print(
            "terms: n={} exact={} translit={} corrupt={} miss={} recall_exact={} "
            "recall_any={} latin_form={} precision={} f1={} case={}".format(
                terms_report["occurrences"],
                terms_report["exact_latin"],
                terms_report["translit"],
                terms_report["corrupt"],
                terms_report["miss"],
                fmt(terms_report["recall_exact"]),
                fmt(terms_report["recall_any"]),
                fmt(terms_report["latin_form_accuracy"]),
                fmt(terms_report["term_precision"]),
                fmt(terms_report["term_f1"]),
                fmt(terms_report["case_accuracy"]),
            )
        )


def cmd_validate(args: argparse.Namespace) -> int:
    report = dataset_mod.validate_dataset(args.dataset, args.terms)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def cmd_preflight(args: argparse.Namespace) -> int:
    report = dataset_mod.benchmark_preflight(args.dataset)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def require_benchmark_ready(args: argparse.Namespace) -> None:
    if not args.benchmark:
        return
    if not args.dataset or not args.split:
        raise ValueError("--benchmark требует --dataset и явный --split")
    report = dataset_mod.benchmark_preflight(args.dataset)
    if not report["ok"]:
        raise ValueError("preflight не пройден: " + "; ".join(report["errors"][:3]))


def _selected_hypotheses(refs, metadata, hypotheses, label):
    # Допускается полный набор гипотез либо ровно выбранный split; посторонние ID — ошибка.
    if metadata and set(hypotheses) == set(metadata):
        hypotheses = {key: hypotheses[key] for key in refs}
    _check_coverage(refs, hypotheses, label)
    return hypotheses


def cmd_score(args: argparse.Namespace) -> int:
    require_benchmark_ready(args)
    vocab = terms_mod.Vocabulary.from_path(args.terms) if args.terms else None
    if args.report:
        pairs = _pairs_from_report(args.report)
        provenance = {"input_sha256": _sha(args.report), "split": "unknown", "dataset_sha256": None}
    else:
        refs, metadata, split = dataset_mod.load_scoring_data(args.dataset, args.split)
        hypotheses = _selected_hypotheses(refs, metadata, _read_hypotheses(args.hypotheses), "гипотезы")
        pairs = [{"id": key, "reference": ref, "hypothesis": hypotheses[key],
                  "commands": metadata.get(key, {}).get("commands", [])} for key, ref in refs.items()]
        provenance = {"split": split, "dataset_sha256": _sha(os.path.join(args.dataset, "manifest.tsv")),
                      "metadata_sha256": _sha(os.path.join(args.dataset, "metadata.jsonl")),
                      "input_sha256": _sha(args.hypotheses)}
    provenance["parameters"] = {"mode": "score", "split": args.split, "benchmark": args.benchmark}
    report = _score(pairs, vocab)
    report["provenance"] = provenance
    _print_summary(args.report or args.dataset, report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    require_benchmark_ready(args)
    refs, metadata, split = dataset_mod.load_scoring_data(args.dataset, args.split)
    hyp_a = _read_hypotheses(args.a)
    hyp_b = _read_hypotheses(args.b)
    hyp_a = _selected_hypotheses(refs, metadata, hyp_a, f"система A ({args.a})")
    hyp_b = _selected_hypotheses(refs, metadata, hyp_b, f"система B ({args.b})")
    errors_a, errors_b, words = [], [], []
    for seg_id, reference in refs.items():
        row_a = wer_mod.score_pair(reference, hyp_a[seg_id])
        row_b = wer_mod.score_pair(reference, hyp_b[seg_id])
        errors_a.append(row_a["errors"])
        errors_b.append(row_b["errors"])
        words.append(row_a["words"])
    if args.group_by and not metadata:
        raise ValueError("для группировки нужны metadata.jsonl")
    groups = [metadata[key][args.group_by] for key in refs] if args.group_by else None
    result = stats.paired_bootstrap(
        errors_a, words, errors_b, replications=args.replications, seed=args.seed, groups=groups
    )
    result["scorer_version"] = SCORER_VERSION
    result["norm_version"] = norm.VERSION
    result["provenance"] = {
        "split": split, "dataset_sha256": _sha(os.path.join(args.dataset, "manifest.tsv")),
        "metadata_sha256": _sha(os.path.join(args.dataset, "metadata.jsonl")),
        "a_sha256": _sha(args.a), "b_sha256": _sha(args.b),
        "parameters": {"mode": "compare", "split": args.split, "group_by": args.group_by,
                       "replications": args.replications, "seed": args.seed, "benchmark": args.benchmark},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Офлайн-скоринг ru-IT (V8 §2.5–§3.2)")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="проверить набор против порогов V8")
    validate.add_argument("--dataset", required=True)
    validate.add_argument("--terms", default=None)
    validate.set_defaults(func=cmd_validate)

    preflight = sub.add_parser("preflight", help="проверить допуск корпуса к бенчмарку")
    preflight.add_argument("--dataset", required=True)
    preflight.set_defaults(func=cmd_preflight)

    score = sub.add_parser("score", help="WER/CER + терминные метрики")
    score.add_argument("--dataset", default=None)
    score.add_argument("--hypotheses", default=None)
    score.add_argument("--report", default=None, help="JSON-отчёт asr-bench")
    score.add_argument("--terms", default=None)
    score.add_argument("--json", default=None)
    score.add_argument("--split", choices=("dev", "test"))
    score.add_argument("--benchmark", action="store_true")
    score.set_defaults(func=cmd_score)

    compare = sub.add_parser("compare", help="парный bootstrap разницы WER двух систем")
    compare.add_argument("--dataset", required=True)
    compare.add_argument("--a", required=True)
    compare.add_argument("--b", required=True)
    compare.add_argument("--split", choices=("dev", "test"))
    compare.add_argument("--benchmark", action="store_true")
    compare.add_argument("--group-by", choices=("speaker_id", "recording_id"))
    compare.add_argument("--json", default=None)
    compare.add_argument("--replications", type=int, default=1000)
    compare.add_argument("--seed", type=int, default=0)
    compare.set_defaults(func=cmd_compare)

    args = parser.parse_args(argv)
    if args.command == "score" and not args.report and not (args.dataset and args.hypotheses):
        parser.error("score: нужен либо --report, либо --dataset вместе с --hypotheses")
    if args.command == "score" and args.report and args.split:
        parser.error("score: --split недоступен для --report")
    if args.command == "score" and args.report and (args.dataset or args.hypotheses):
        parser.error("score: --report несовместим с --dataset и --hypotheses")
    try:
        return args.func(args)
    except (ValueError, KeyError) as exc:
        # Ошибка входных данных — понятное сообщение и код 2, без traceback.
        print(f"ошибка входа: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
