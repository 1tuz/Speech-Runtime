"""Валидация набора ru-IT против требований V8 §2.2–§2.6.

Проверяет то, что можно проверить механически: структуру манифеста и метаданных,
утечку дикторов/встреч между dev и test, покрытие терминов, регистров, микрофонов,
шума и объёма. Всё, что V8 задаёт как «предлагаемый» порог, лежит в THRESHOLDS с
ссылкой на раздел.

Ожидаемая раскладка каталога набора:
    manifest.tsv    <путь к wav относительно каталога>\t<референс>
    metadata.jsonl  по одному JSON на сегмент (§2.4)
    terms.json      словарь терминов набора (или --terms)
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter

import terms as terms_mod

REGISTERS = ("R1", "R2", "R3", "R4")
SPLITS = ("dev", "test")
MIC_CLASSES = ("headset", "builtin", "lavalier", "phone")
REQUIRED_FIELDS = (
    "segment_id",
    "recording_id",
    "register",
    "speaker_id",
    "mic_class",
    "noise_class",
    "duration_s",
    "split",
)

#: Пороги V8; каждое число — из конкретного раздела, не «на глаз».
THRESHOLDS = {
    "unique_terms_min": (300, "§2.3"),
    "term_occurrences_total_min": (1500, "§2.3"),
    "term_occurrences_test_min": (400, "§2.3/§2.6"),
    "test_hours_min": (5.0, "§2.6"),
    "test_hours_max": (7.0, "§2.6"),
    "total_hours_min": (8.0, "§2.6"),
    "total_hours_max": (11.0, "§2.6"),
    "test_utterances_min": (500, "§2.6"),
    "speakers_r1_r3_min": (10, "§2.2"),
    "groups_r2_min": (8, "§2.2"),
    "mic_classes_min": (3, "§2.2"),
    "noise_share_min": (0.15, "§2.2"),
    "keyboard_fan_share_min": (0.10, "§2.2"),
    "non_native_share_min": (0.30, "§2.2"),
}

TERM_CLASSES = ("tools", "abbrev", "command", "wordform", "number", "translit")


def _load_manifest(path: str, errors: list[str], warnings: list[str]) -> dict[str, str]:
    entries: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 2:
                errors.append(f"manifest.tsv:{lineno}: ожидались 2 поля через TAB, получено {len(parts)}")
                continue
            audio, reference = parts[0].strip(), parts[1].strip()
            if not reference:
                errors.append(f"manifest.tsv:{lineno}: пустой референс")
                continue
            seg_id = os.path.splitext(os.path.basename(audio))[0]
            if not seg_id:
                errors.append(f"manifest.tsv:{lineno}: не удалось вывести id из пути '{audio}'")
                continue
            if seg_id in entries:
                errors.append(f"manifest.tsv:{lineno}: дубликат id '{seg_id}'")
                continue
            entries[seg_id] = reference
            if not os.path.exists(os.path.join(os.path.dirname(path), audio)):
                warnings.append(f"manifest.tsv:{lineno}: аудио отсутствует локально: {audio}")
    return entries


def _load_metadata(path: str, errors: list[str]) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"metadata.jsonl:{lineno}: невалидный JSON ({exc.msg})")
                continue
            if not isinstance(row, dict):
                errors.append(f"metadata.jsonl:{lineno}: ожидался JSON-объект")
                continue
            errors_before = len(errors)
            missing = [f for f in REQUIRED_FIELDS if f not in row]
            if missing:
                errors.append(f"metadata.jsonl:{lineno}: нет полей {', '.join(missing)}")
                continue
            bad_ids = [
                field for field in ("segment_id", "recording_id", "speaker_id")
                if not isinstance(row[field], str) or not row[field].strip()
            ]
            if bad_ids:
                errors.append(
                    f"metadata.jsonl:{lineno}: идентификаторы должны быть непустыми строками: {', '.join(bad_ids)}"
                )
                continue
            if row["register"] not in REGISTERS:
                errors.append(f"metadata.jsonl:{lineno}: register '{row['register']}' вне {REGISTERS}")
            if row["split"] not in SPLITS:
                errors.append(f"metadata.jsonl:{lineno}: split '{row['split']}' вне {SPLITS}")
            duration = row["duration_s"]
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) \
                    or not math.isfinite(duration) or duration <= 0:
                errors.append(f"metadata.jsonl:{lineno}: duration_s должно быть конечным числом > 0")
            if not isinstance(row.get("speaker_native", True), bool):
                errors.append(f"metadata.jsonl:{lineno}: speaker_native должно быть bool")
            segment_terms = row.get("terms", [])
            if not isinstance(segment_terms, list):
                errors.append(f"metadata.jsonl:{lineno}: terms должно быть списком")
                continue
            for entry in segment_terms:
                term_id = entry.get("term_id") if isinstance(entry, dict) else entry
                if not isinstance(term_id, str) or not term_id:
                    errors.append(f"metadata.jsonl:{lineno}: элемент terms без строкового term_id")
                    break
            if row["segment_id"] in seen:
                errors.append(f"metadata.jsonl:{lineno}: дубликат segment_id '{row['segment_id']}'")
            if len(errors) != errors_before:
                # Строка с ошибкой не попадает в coverage: иначе NaN/битый duration
                # отравляет агрегаты (часы, доли) при формально «невалидном» наборе.
                continue
            seen.add(row["segment_id"])
            rows.append(row)
    return rows


def _check_terms(path: str, errors: list[str]) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        errors.append(f"terms: файл не найден: {path}")
        return []
    except json.JSONDecodeError as exc:
        errors.append(f"terms: невалидный JSON ({exc.msg})")
        return []
    raw_terms = data.get("terms")
    if not isinstance(raw_terms, list):
        errors.append("terms: отсутствует список 'terms'")
        return []
    ids: set[str] = set()
    forms: dict[str, str] = {}
    for index, term in enumerate(raw_terms):
        where = f"terms[{index}]"
        if not isinstance(term, dict):
            errors.append(f"{where}: ожидался объект")
            continue
        term_id = term.get("id")
        if not term_id:
            errors.append(f"{where}: нет id")
            continue
        if term_id in ids:
            errors.append(f"{where}: дубликат id '{term_id}'")
        ids.add(term_id)
        if term.get("class") not in TERM_CLASSES:
            errors.append(f"{where} ({term_id}): class '{term.get('class')}' вне {TERM_CLASSES}")
        accept = term.get("accept")
        if not isinstance(accept, list) or not accept:
            errors.append(f"{where} ({term_id}): пустой accept")
            continue
        for form in accept:
            key = " ".join(form.lower().split())
            owner = forms.get(key)
            if owner is not None and owner != term_id:
                errors.append(f"{where}: форма '{form}' уже принадлежит термину '{owner}'")
            forms[key] = term_id
    return raw_terms


def validate_dataset(dataset_dir: str, terms_path: str | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = os.path.join(dataset_dir, "manifest.tsv")
    metadata_path = os.path.join(dataset_dir, "metadata.jsonl")
    if terms_path is None:
        terms_path = os.path.join(dataset_dir, "terms.json")

    raw_terms = _check_terms(terms_path, errors)
    if not os.path.exists(manifest_path):
        errors.append(f"нет файла {manifest_path}")
    if not os.path.exists(metadata_path):
        errors.append(f"нет файла {metadata_path}")
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "coverage": {}, "thresholds": THRESHOLDS}

    entries = _load_manifest(manifest_path, errors, warnings)
    rows = _load_metadata(metadata_path, errors)

    known_terms = {t.get("id") for t in raw_terms if isinstance(t, dict)}
    ids_manifest = set(entries)
    ids_metadata = {row["segment_id"] for row in rows}
    for seg_id in sorted(ids_manifest - ids_metadata):
        warnings.append(f"сегмент '{seg_id}' есть в манифесте, но не в метаданных")
    for seg_id in sorted(ids_metadata - ids_manifest):
        errors.append(f"сегмент '{seg_id}' есть в метаданных, но не в манифесте")

    speaker_splits: dict[str, set[str]] = {}
    recording_splits: dict[str, set[str]] = {}
    split_seconds: Counter[str] = Counter()
    split_segments: Counter[str] = Counter()
    register_seconds: Counter[str] = Counter()
    mic_classes: set[str] = set()
    noise_segments = keyboard_segments = 0
    term_occurrences: Counter[str] = Counter()
    term_occurrences_total = 0
    encountered_terms: set[str] = set()
    speaker_native: dict[str, bool] = {}
    for row in rows:
        split = row["split"]
        split_seconds[split] += float(row["duration_s"])
        split_segments[split] += 1
        register_seconds[row["register"]] += float(row["duration_s"])
        speaker_splits.setdefault(row["speaker_id"], set()).add(split)
        recording_splits.setdefault(row["recording_id"], set()).add(split)
        mic_classes.add(row["mic_class"])
        noise = str(row["noise_class"]).lower()
        if noise and noise not in ("none", "clean", "тишина"):
            noise_segments += 1
        if "keyboard" in noise or "fan" in noise or "клавиат" in noise or "вентил" in noise:
            keyboard_segments += 1
        if "speaker_native" in row:
            previous = speaker_native.get(row["speaker_id"])
            if previous is not None and previous != row["speaker_native"]:
                errors.append(
                    f"диктор '{row['speaker_id']}': противоречивые speaker_native "
                    f"({previous} и {row['speaker_native']})"
                )
            speaker_native[row["speaker_id"]] = row["speaker_native"]
        seg_terms = row.get("terms") or []
        for entry in seg_terms:
            term_id = entry.get("term_id") if isinstance(entry, dict) else entry
            if term_id is None:
                continue
            if known_terms and term_id not in known_terms:
                errors.append(f"segment '{row['segment_id']}': неизвестный term_id '{term_id}'")
                continue
            term_occurrences[split] += 1
            term_occurrences_total += 1
            encountered_terms.add(term_id)

    for speaker, splits in speaker_splits.items():
        if len(splits) > 1:
            errors.append(f"утечка: диктор '{speaker}' встречается в {sorted(splits)}")
    for recording, splits in recording_splits.items():
        if len(splits) > 1:
            errors.append(f"утечка: запись '{recording}' встречается в {sorted(splits)}")

    total_seconds = sum(split_seconds.values())
    test_seconds = split_seconds.get("test", 0.0)
    total_rows = len(rows) or 1
    speakers_r1_r3 = {
        row["speaker_id"] for row in rows if row["register"] in ("R1", "R3")
    }
    groups_r2 = {row["recording_id"] for row in rows if row["register"] == "R2"}
    coverage = {
        "segments": len(rows),
        "hours_total": total_seconds / 3600,
        "hours_test": test_seconds / 3600,
        "segments_test": split_segments.get("test", 0),
        "register_share": {
            reg: (register_seconds[reg] / total_seconds) if total_seconds else 0.0 for reg in REGISTERS
        },
        # Словарь и фактически размеченные термины — разные величины: 300 записей в
        # terms.json не доказывают 300 разных терминов в аудио (V8 §2.3 требует второе).
        "dictionary_terms": len(known_terms),
        "unique_terms": len(encountered_terms),
        "term_occurrences_total": term_occurrences_total,
        "term_occurrences_test": term_occurrences.get("test", 0),
        "speakers_r1_r3": len(speakers_r1_r3),
        "groups_r2": len(groups_r2),
        "mic_classes": sorted(mic_classes),
        "noise_share": noise_segments / total_rows,
        "keyboard_fan_share": keyboard_segments / total_rows,
        # V8 §2.2: доля неродных — по УНИКАЛЬНЫМ дикторам, не по сегментам.
        "non_native_share": (
            sum(1 for flag in speaker_native.values() if not flag) / len(speaker_native)
        ) if speaker_native else None,
    }

    def warn_if(condition: bool, message: str) -> None:
        if condition:
            warnings.append(message)

    th = THRESHOLDS
    warn_if(coverage["unique_terms"] < th["unique_terms_min"][0],
            f"уникальных терминов в разметке {coverage['unique_terms']} < {th['unique_terms_min'][0]} "
            f"({th['unique_terms_min'][1]}; словарь содержит {coverage['dictionary_terms']})")
    warn_if(term_occurrences_total < th["term_occurrences_total_min"][0],
            f"вхождений терминов {term_occurrences_total} < {th['term_occurrences_total_min'][0]}")
    warn_if(coverage["term_occurrences_test"] < th["term_occurrences_test_min"][0],
            f"вхождений терминов в test {coverage['term_occurrences_test']} < {th['term_occurrences_test_min'][0]}")
    warn_if(coverage["hours_test"] < th["test_hours_min"][0],
            f"test {coverage['hours_test']:.2f} ч < {th['test_hours_min'][0]} ч")
    warn_if(coverage["hours_test"] > th["test_hours_max"][0],
            f"test {coverage['hours_test']:.2f} ч > {th['test_hours_max'][0]} ч")
    warn_if(coverage["hours_total"] < th["total_hours_min"][0],
            f"весь набор {coverage['hours_total']:.2f} ч < {th['total_hours_min'][0]} ч")
    warn_if(coverage["hours_total"] > th["total_hours_max"][0],
            f"весь набор {coverage['hours_total']:.2f} ч > {th['total_hours_max'][0]} ч")
    warn_if(coverage["segments_test"] < th["test_utterances_min"][0],
            f"test высказываний {coverage['segments_test']} < {th['test_utterances_min'][0]}")
    warn_if(len(speakers_r1_r3) < th["speakers_r1_r3_min"][0],
            f"дикторов в R1/R3 {len(speakers_r1_r3)} < {th['speakers_r1_r3_min'][0]}")
    warn_if(len(groups_r2) < th["groups_r2_min"][0],
            f"групп в R2 {len(groups_r2)} < {th['groups_r2_min'][0]}")
    warn_if(len(mic_classes) < th["mic_classes_min"][0],
            f"классов микрофонов {len(mic_classes)} < {th['mic_classes_min'][0]}")
    warn_if(coverage["noise_share"] < th["noise_share_min"][0],
            f"доля сегментов с шумом {coverage['noise_share']:.0%} < {th['noise_share_min'][0]:.0%}")
    warn_if(coverage["keyboard_fan_share"] < th["keyboard_fan_share_min"][0],
            f"доля сегментов с клавиатурой/вентилятором {coverage['keyboard_fan_share']:.0%} < {th['keyboard_fan_share_min'][0]:.0%}")
    if coverage["non_native_share"] is None:
        warnings.append("доля неродных дикторов не проверяема: в метаданных нет поля 'speaker_native' (§2.2 требует ≥30 %)")
    else:
        warn_if(coverage["non_native_share"] < th["non_native_share_min"][0],
                f"доля неродных дикторов {coverage['non_native_share']:.0%} < {th['non_native_share_min'][0]:.0%}")
    for reg in REGISTERS:
        warn_if(coverage["register_share"][reg] == 0.0, f"регистр {reg} отсутствует в наборе (§2.2)")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "coverage": coverage,
        "thresholds": THRESHOLDS,
    }


def load_manifest_map(dataset_dir: str) -> dict[str, str]:
    """Манифест как {id: референс}; невалидный манифест — ошибка, а не усечённый набор.

    Молчаливый возврат части манифеста приводил бы к скору только части входа.
    """
    path = os.path.join(dataset_dir, "manifest.tsv")
    errors: list[str] = []
    warnings: list[str] = []
    entries = _load_manifest(path, errors, warnings)
    if errors:
        raise ValueError(
            f"манифест невалиден ({len(errors)} ошибок): " + "; ".join(errors[:3])
        )
    return entries


def vocabulary_for(dataset_dir: str, terms_path: str | None = None) -> terms_mod.Vocabulary:
    path = terms_path or os.path.join(dataset_dir, "terms.json")
    return terms_mod.Vocabulary.from_path(path)
