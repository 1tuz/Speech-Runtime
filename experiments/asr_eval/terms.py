"""Терминные метрики: exact_latin / exact_other / translit / corrupt / miss.

Ключевое отличие от подхода FluidAudio (`\\b…\\b` по всему тексту, V8 §3.2 и таблица
проверки): сопоставление позиционное — окно ±3 слова вокруг выравненной позиции термина
в референсе. Термин, случайно попавший в другое место гипотезы, не засчитывается.

Термины ищутся в нормализованном потоке токенов, case accuracy — по
параллельному потоку с сохранённым регистром (индексы совпадают по построению).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

import align
import norm

WINDOW = 3
CORRUPT_MAX_DISTANCE = 2
MIN_CORRUPT_FORM_LEN = 3


def corrupt_threshold(form_len: int) -> int:
    """Порог edit distance для `corrupt`: V8 §3.2 даёт ≤ 2, но для коротких терминов
    (`CI`, `S3`, `NS`, `LB`) d ≤ 2 совпадает с «любым токеном». Поэтому порог
    масштабируется длиной формы: 3 символа → 1, 6+ → 2 (не больше 2)."""
    if form_len < MIN_CORRUPT_FORM_LEN:
        return 0
    return min(CORRUPT_MAX_DISTANCE, max(1, form_len // 3))


def _is_latin(token: str) -> bool:
    return bool(token) and all(ch.isascii() and ch.isalnum() for ch in token)

EXACT_LATIN = "exact_latin"
EXACT_OTHER = "exact_other"
TRANSLIT = "translit"
CORRUPT = "corrupt"
MISS = "miss"


#: Падежные окончания, которые диктор добавляет к транслиту («в гитлабе», «кубернетесом»).
#: Без них транслит-форма из `terms.yaml` не находится в живой речи.
TRANSLIT_ENDINGS = ("", "а", "у", "ом", "е", "ы", "и", "ой", "ей", "ю", "я", "ах", "ов", "ам", "ами", "ям")


@dataclass(frozen=True)
class Term:
    id: str
    cls: str
    accept: tuple[str, ...]
    accept_translit: tuple[str, ...] = ()
    accept_norm: tuple[tuple[str, ...], ...] = field(default=(), compare=False)
    translit_norm: tuple[tuple[str, ...], ...] = field(default=(), compare=False)
    has_latin: bool = field(default=False, compare=False)


@dataclass(frozen=True)
class Occurrence:
    term_id: str
    start: int
    end: int
    form_kind: str = EXACT_LATIN


def _translit_forms(forms: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    """Транслит-формы с падежными окончаниями (только однотокенные кириллические)."""
    out: list[tuple[str, ...]] = []
    for form in forms:
        toks = tuple(t.lower() for t in norm.tokens(form))
        if not toks:
            continue
        out.append(toks)
        if len(toks) == 1 and not toks[0].isascii():
            out.extend((toks[0] + ending,) for ending in TRANSLIT_ENDINGS[1:])
    return tuple(dict.fromkeys(out))


def _has_latin(forms: tuple[str, ...]) -> bool:
    return any(ch.isascii() and ch.isalpha() for form in forms for ch in form)


def _norm_forms(forms: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    out = []
    for form in forms:
        toks = tuple(t.lower() for t in norm.tokens(form))
        if toks:
            out.append(toks)
    return tuple(out)


class Vocabulary:
    def __init__(self, terms: list[Term], *, source_path: str = "", version: str = "", sha256: str = ""):
        self.terms = terms
        self.source_path = source_path
        self.version = version
        self.sha256 = sha256
        self.by_id = {t.id: t for t in terms}
        # (форма, term) — для поиска в референсе и в гипотезе
        self.forms: list[tuple[tuple[str, ...], Term, str]] = []
        for term in terms:
            for form in term.accept_norm:
                self.forms.append((form, term, EXACT_LATIN if _has_latin(form) else EXACT_OTHER))
            for form in term.translit_norm:
                self.forms.append((form, term, TRANSLIT))
        self.forms.sort(key=lambda item: len(item[0]), reverse=True)
        # Индекс для `corrupt`: латинские однотокенные формы КАЖДОГО термина,
        # сгруппированные по длине. Поиск по всему словарю запрещён: иначе `Dockerr`
        # засчитывается как искажённый `Helm` (подмена термина).
        self._corrupt_index: dict[str, dict[int, tuple[str, ...]]] = {}
        for term in terms:
            by_len: dict[int, list[str]] = {}
            for form in term.accept_norm:
                if len(form) == 1 and _is_latin(form[0]) and len(form[0]) >= MIN_CORRUPT_FORM_LEN:
                    by_len.setdefault(len(form[0]), []).append(form[0])
            self._corrupt_index[term.id] = {size: tuple(forms) for size, forms in by_len.items()}

    def identity(self) -> dict:
        """Идентичность словаря для отчёта: без неё числа несравнимы между прогонами."""
        return {
            "path": os.path.basename(self.source_path) or None,
            "version": self.version or None,
            "sha256": self.sha256,
        }

    @classmethod
    def from_path(cls, path: str) -> "Vocabulary":
        with open(path, "rb") as fh:
            raw = fh.read()
        text = raw.decode("utf-8")
        data = json.loads(text)
        terms = []
        for raw in data["terms"]:
            accept = tuple(raw["accept"])
            translit = tuple(raw.get("accept_translit", ()))
            terms.append(
                Term(
                    id=raw["id"],
                    cls=raw["class"],
                    accept=accept,
                    accept_translit=translit,
                    accept_norm=_norm_forms(accept),
                    translit_norm=_translit_forms(translit),
                    has_latin=_has_latin(accept),
                )
            )
        return cls(
            terms,
            source_path=path,
            version=str(data.get("version", "")),
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )

    def occurrences(self, tokens: list[str]) -> list[Occurrence]:
        """Все вхождения форм словаря, жадное совпадение от длинных форм к коротким."""
        found: list[Occurrence] = []
        taken = [False] * len(tokens)
        for form, term, kind in self.forms:
            size = len(form)
            for start in range(0, len(tokens) - size + 1):
                if any(taken[start:start + size]):
                    continue
                if tuple(tokens[start:start + size]) == form:
                    found.append(Occurrence(term.id, start, start + size, kind))
                    for idx in range(start, start + size):
                        taken[idx] = True
        found.sort(key=lambda occ: occ.start)
        return found

    def corrupt_matches(self, term: Term, token: str) -> bool:
        """Искажённое написание ИМЕННО этого термина (V8 §3.2).

        Ограничения, снимающие ложные `corrupt` (V8 §3.2 их не оговаривает):
        - сравнение только с формами текущего термина, не со словарём целиком;
        - только латинский токен против латинской формы: кириллический токен не может
          быть «искажённым `helm`», а русские словоформы дают малое расстояние при
          совершенно другом значении;
        - разница длин ≤ 1 и порог `corrupt_threshold`.
        """
        if not _is_latin(token) or len(token) < MIN_CORRUPT_FORM_LEN:
            return False
        index = self._corrupt_index.get(term.id, {})
        for size in (len(token) - 1, len(token), len(token) + 1):
            for form in index.get(size, ()):
                if align.char_distance(token, form) <= corrupt_threshold(size):
                    return True
        return False


def _case_matches(matched_raw: list[str], term: Term) -> bool:
    """Регистр совпал с канонической формой из `accept` (разделители не учитываются).

    `CI/CD` в референсе и `CICD`/`CI-CD` в гипотезе дают совпадение; `cicd` — нет.
    """
    cleaned = norm.strip_separators("".join(matched_raw))
    return any(norm.strip_separators(form) == cleaned for form in term.accept)


def _window(occurrence: Occurrence, ref_to_hyp: dict[int, int], ref_len: int, hyp_len: int) -> tuple[int, int]:
    mapped = [ref_to_hyp[i] for i in range(occurrence.start, occurrence.end) if i in ref_to_hyp]
    if not mapped:
        lo_ref = max(0, occurrence.start - WINDOW)
        hi_ref = min(ref_len, occurrence.end + WINDOW)
        mapped = [ref_to_hyp[i] for i in range(lo_ref, hi_ref) if i in ref_to_hyp]
        if not mapped:
            return 0, hyp_len - 1
        lo, hi = min(mapped), max(mapped)
        return max(0, lo), min(hyp_len - 1, hi)
    lo, hi = min(mapped) - WINDOW, max(mapped) + WINDOW
    return max(0, lo), min(hyp_len - 1, hi)


def _find_form(
    hyp_tokens: list[str],
    lo: int,
    hi: int,
    forms: tuple[tuple[str, ...], ...],
    consumed: list[bool] | None = None,
) -> tuple[int, int] | None:
    """Первое совпадение формы в окне, не занятое другим вхождением термина.

    Одно гипотезное совпадение засчитывается не более одного раза (V8 §3.2 не
    оговаривает, но иначе повтор термина в референсе дважды получает один токен).
    """
    for form in sorted(forms, key=len, reverse=True):
        size = len(form)
        for start in range(lo, hi - size + 2):
            if consumed is not None and any(consumed[start:start + size]):
                continue
            if tuple(hyp_tokens[start:start + size]) == form:
                return start, start + size
    return None


def classify(ref_text: str, hyp_text: str, vocab: Vocabulary) -> dict:
    ref_tokens = norm.tokens(ref_text)
    ref_raw = norm.tokens(ref_text, keep_case=True)
    hyp_tokens = norm.tokens(hyp_text)
    hyp_raw = norm.tokens(hyp_text, keep_case=True)
    hyp_tokens, kept = norm.strip_foreign_fillers(hyp_tokens, ref_tokens)
    hyp_raw = [hyp_raw[i] for i in kept]

    _, ref_to_hyp = align.align(ref_tokens, hyp_tokens)
    occurrences = vocab.occurrences(ref_tokens)

    records: list[dict] = []
    counts = {EXACT_LATIN: 0, EXACT_OTHER: 0, TRANSLIT: 0, CORRUPT: 0, MISS: 0}
    latin_translit = 0
    case_hits = case_total = 0
    consumed = [False] * len(hyp_tokens)
    windows: list[tuple[int, int]] = []
    verdicts: list[str | None] = [None] * len(occurrences)
    case_flags: list[bool | None] = [None] * len(occurrences)

    # Проход 1: точные совпадения и транслиты — сильное свидетельство, забирает токены
    # первым, чтобы короткий термин (`p95`) не был «съеден» искажением соседа (`p50`).
    for index, occ in enumerate(occurrences):
        term = vocab.by_id[occ.term_id]
        lo, hi = _window(occ, ref_to_hyp, len(ref_tokens), len(hyp_tokens))
        windows.append((lo, hi))
        hit = _find_form(hyp_tokens, lo, hi, term.accept_norm, consumed)
        if hit is not None:
            is_latin = _has_latin(hyp_tokens[hit[0]:hit[1]])
            verdicts[index] = EXACT_LATIN if is_latin else EXACT_OTHER
            if is_latin:
                case_ok = _case_matches(hyp_raw[hit[0]:hit[1]], term)
                case_flags[index] = case_ok
                case_total += 1
                case_hits += 1 if case_ok else 0
            for idx in range(hit[0], hit[1]):
                consumed[idx] = True
            continue
        translit_hit = _find_form(hyp_tokens, lo, hi, term.translit_norm, consumed)
        if translit_hit is not None:
            verdicts[index] = TRANSLIT
            if term.has_latin:
                latin_translit += 1
            for idx in range(translit_hit[0], translit_hit[1]):
                consumed[idx] = True

    # Проход 2: искажение занимает токен, чтобы его не засчитали повторно.
    for index, occ in enumerate(occurrences):
        if verdicts[index] is not None:
            continue
        term = vocab.by_id[occ.term_id]
        lo, hi = windows[index]
        verdicts[index] = MISS
        for idx in range(lo, hi + 1):
            if not consumed[idx] and vocab.corrupt_matches(term, hyp_tokens[idx]):
                verdicts[index] = CORRUPT
                consumed[idx] = True
                break

    for index, occ in enumerate(occurrences):
        term = vocab.by_id[occ.term_id]
        kind = verdicts[index] or MISS
        counts[kind] += 1
        lo, hi = windows[index]
        records.append(
            {
                "term_id": term.id,
                "class": term.cls,
                "spoken_form": {TRANSLIT: "translit", EXACT_LATIN: "latin", EXACT_OTHER: "other"}[occ.form_kind],
                "ref_span": [occ.start, occ.end],
                "hyp_window": [lo, hi],
                "verdict": kind,
                "case_ok": case_flags[index],
            }
        )

    # Один набор сопоставлений задаёт TP, FP и FN для всех трёх метрик.
    tp = len(occurrences) - counts[MISS]
    fn = counts[MISS]
    fp = sum(not any(consumed[occ.start:occ.end]) for occ in vocab.occurrences(hyp_tokens))
    total = len(occurrences)
    exact = counts[EXACT_LATIN] + counts[EXACT_OTHER]
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    latin_form_accuracy = (counts[EXACT_LATIN] / (counts[EXACT_LATIN] + latin_translit)
                           if counts[EXACT_LATIN] + latin_translit else None)
    return {
        "occurrences": total,
        "exact_latin": counts[EXACT_LATIN],
        "exact_other": counts[EXACT_OTHER],
        "translit": counts[TRANSLIT],
        "corrupt": counts[CORRUPT],
        "miss": counts[MISS],
        "recall_exact": exact / total if total else None,
        "recall_any": recall,
        "term_recall": recall,
        "latin_form_accuracy": latin_form_accuracy,
        "term_precision": precision,
        "term_f1": f1,
        "case_accuracy": (case_hits / case_total) if case_total else None,
        "case_hits": case_hits,
        "case_total": case_total,
        "term_tp": tp,
        "term_fp": fp,
        "term_fn": fn,
        "latin_translit": latin_translit,
        "per_term": records,
    }


def aggregate(per_utterance: list[dict]) -> dict:
    """Микроусреднение: сначала суммируются независимые счётчики."""
    keys = (EXACT_LATIN, EXACT_OTHER, TRANSLIT, CORRUPT, MISS,
            "term_tp", "term_fp", "term_fn", "latin_translit", "case_hits", "case_total")
    counts = {key: sum(row[key] for row in per_utterance) for key in keys}
    tp, fp, fn = counts["term_tp"], counts["term_fp"], counts["term_fn"]
    total = tp + fn
    exact = counts[EXACT_LATIN] + counts[EXACT_OTHER]
    latin_total = counts[EXACT_LATIN] + counts["latin_translit"]
    return {
        "occurrences": total,
        **counts,
        "recall_exact": exact / total if total else None,
        "recall_any": tp / total if total else None,
        "term_recall": tp / total if total else None,
        "latin_form_accuracy": counts[EXACT_LATIN] / latin_total if latin_total else None,
        "term_precision": tp / (tp + fp) if tp + fp else None,
        "term_f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
        "case_accuracy": counts["case_hits"] / counts["case_total"] if counts["case_total"] else None,
    }
