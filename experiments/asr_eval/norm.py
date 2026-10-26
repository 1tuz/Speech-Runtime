"""norm-v1 — детерминированная нормализация текста для оценки ASR.

Источник правил: research/wave2/V8-eval-plan.md §2.5 (таблица «Правила нормализации»).
Версия замораживается: изменение любого правила требует новой версии (norm-v2) и
повторного прогона всех чисел, иначе результаты несравнимы (V8 §10, шаг 0).

Решения, принятые при переносе §2.5 в код (там, где текст V8 допускает варианты):

1. Числа. V8 предлагает `num2words(lang="ru")`. Внешняя зависимость (LGPL) не
   добавляется: реализован детерминированный эквивалент — количественные числительные
   мужского рода в именительном падеже для 0…10^12−1, «тысячи» в женском роде.
   Дробная часть читается как число после слова «точка» (`1.29` → `один точка
   двадцать девять`, как в примере V8 §2.5), потому что модель, пишущая `1.29` и
   «один точка двадцать девять», не должна штрафоваться дважды.
   Порядковые суффиксы (`500-е`, `99-м`) отделяются как отдельный токен
   (`пятьсот` + `е`) — осознанное ограничение norm-v1.
2. Токены с ведущими нулями (`007`) читаются по цифрам, чтобы не терять нули.
2a. Версии и CIDR (две и более точки: `1.29.0`, `10.0.0.0/8`) остаются цифрами: это
   термины из §2.3 класс 5, их расширение в слова уничтожило бы саму метрику.
2b. Цифры внутри буквенно-цифровых токенов (`k8s`, `S3`, `p50`) не трогаются: §2.5
   прямо запрещает схлопывать `k8s` и `kubernetes`.
3. Пунктуация удаляется на этапе токенизации; `/` и `-` работают как разделители.
   Поэтому `CI/CD` → `ci cd` → `cicd`, `ci-cd` → `cicd`, а `postgresql` остаётся
   `postgresql` и не склеивается с `postgre sql` (V8 §2.5, правило «дефис/пробел»).
4. Склейка `ci cd` → `cicd` выполняется только для правил из GLUE_RULES и только
   внутри соседней пары токенов: свободная склейка латинского прогона склеила бы
   `kubectl CI/CD Helm` → `kubectl cicdhelm` и замаскировала реальные ошибки.
5. Латиница не транслитерируется и регистр для WER не сохраняется (lowercasing).
   Case-sensitive проверка терминов живёт отдельно, в terms.py, по `tokens(keep_case=True)`.
6. Филлеры удаляются из гипотезы только если их нет в референсе (V8 §2.5).

Только стандартная библиотека.
"""

from __future__ import annotations

import re
import unicodedata

VERSION = "norm-v1"

_CHATML_RE = re.compile(r"<\|[^|]*\|>")
#: Версии/CIDR (две и более точек) остаются цифрами: это термины, а не количества.
_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){2,}(?:/[0-9]+)?")
_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+(?:[.,][0-9]+)?")
_NUM_RE = re.compile(r"^[0-9]+(?:[.,][0-9]+)?$")
_NON_WORD_RE = re.compile(r"[^0-9A-Za-zА-Яа-я]+")

#: Разрешённые склейки (V8 §2.5: `ci cd` = `cicd`). Ключ — последовательность
#: нормализованных токенов, значение — результат склейки. Других склеек нет:
#: `postgresql` ≠ `postgre sql`, `kubectl` ≠ `kube ctl`.
GLUE_RULES: dict[tuple[str, ...], str] = {
    ("ci", "cd"): "cicd",
}
#: Совместимое представление результата склейки (для документации и внешних проверок).
GLUE_ALLOWLIST = frozenset(GLUE_RULES.values())

#: Односложные/двухсловные филлеры, удаляемые из гипотезы, если их нет в референсе.
FILLER_UNIGRAMS = frozenset({"э", "ээ", "эээ", "мм", "ммм", "ну", "ага", "угу", "типа", "вот"})
FILLER_BIGRAMS = frozenset({("как", "бы")})

#: Фиксированный список дисфluency-маркеров для `ref_normalized` в R3 (V8 §2.5).
DISFLUENCIES = frozenset({"э", "ээ", "эээ", "мм", "ммм", "ну", "типа", "вот"})

_ONES = (
    "ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять",
    "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
    "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать",
)
_TENS = {
    2: "двадцать", 3: "тридцать", 4: "сорок", 5: "пятьдесят",
    6: "шестьдесят", 7: "семьдесят", 8: "восемьдесят", 9: "девяносто",
}
_HUNDREDS = {
    1: "сто", 2: "двести", 3: "триста", 4: "четыреста", 5: "пятьсот",
    6: "шестьсот", 7: "семьсот", 8: "восемьсот", 9: "девятьсот",
}
_SCALES = (
    (10 ** 9, ("миллиард", "миллиарда", "миллиардов"), False),
    (10 ** 6, ("миллион", "миллиона", "миллионов"), False),
    (10 ** 3, ("тысяча", "тысячи", "тысяч"), True),
)


def _plural_form(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def _under_1000(n: int, *, feminine: bool = False) -> list[str]:
    out: list[str] = []
    hundreds, rest = divmod(n, 100)
    if hundreds:
        out.append(_HUNDREDS[hundreds])
    if rest < 20:
        if rest or not out:
            out.append(_ONES[rest])
    else:
        tens, units = divmod(rest, 10)
        out.append(_TENS[tens])
        if units:
            out.append(_ONES[units])
    if feminine:
        out = ["одна" if w == "один" else "две" if w == "два" else w for w in out]
    return out


def number_to_words_ru(n: int) -> list[str]:
    """Целое 0 ≤ n < 10^12 → токены количественного числительного (мужской род)."""
    if n < 0:
        raise ValueError("norm-v1: отрицательные числа не поддерживаются")
    if n == 0:
        return ["ноль"]
    if n >= 10 ** 12:
        raise ValueError("norm-v1: числа ≥ 10^12 не поддерживаются")
    out: list[str] = []
    rest = n
    for scale, forms, feminine in _SCALES:
        group, rest = divmod(rest, scale)
        if not group:
            continue
        out.extend(_under_1000(group, feminine=feminine))
        out.append(_plural_form(group, forms))
    if rest:
        out.extend(_under_1000(rest))
    return out


def _digits_to_words(digits: str) -> list[str]:
    return [_ONES[int(ch)] for ch in digits if ch.isdigit()]


def _number_token(token: str) -> list[str]:
    """`29` → двадцать девять; `1.29` → один точка двадцать девять; `007` → ноль ноль семь."""
    if "," in token:
        token = token.replace(",", ".")
    int_part, _, frac_part = token.partition(".")
    if len(int_part) > 1 and int_part.startswith("0"):
        return _digits_to_words(int_part + frac_part)
    out = number_to_words_ru(int(int_part))
    if frac_part:
        out.append("точка")
        out.extend(number_to_words_ru(int(frac_part)) if len(frac_part) <= 2 else _digits_to_words(frac_part))
    return out


def _glue(tokens: list[str]) -> list[str]:
    """Склеивает только разрешённые пары (`ci` `cd` → `cicd`), не трогая соседей.

    Правила проверяются на каждой позиции отдельно, поэтому результат не зависит от
    ASCII-контекста вокруг: `kubectl CI/CD Helm` → `kubectl cicd helm`.
    """
    out: list[str] = []
    rules = sorted(GLUE_RULES.items(), key=lambda item: len(item[0]), reverse=True)
    index = 0
    while index < len(tokens):
        applied = False
        for sequence, glued in rules:
            size = len(sequence)
            window = tokens[index:index + size]
            if (
                len(window) == size
                and all(tok.isascii() and tok.isalnum() for tok in window)
                and tuple(tok.lower() for tok in window) == sequence
            ):
                out.append("".join(window))
                index += size
                applied = True
                break
        if not applied:
            out.append(tokens[index])
            index += 1
    return out



def tokens(text: str, *, keep_case: bool = False) -> list[str]:
    """Нормализованные токены (пунктуация удалена, числа развёрнуты, ё → е).

    При `keep_case=True` регистр сохраняется: поток токенов совпадает по индексам с
    обычным режимом, что позволяет считать case accuracy терминов (terms.py).
    """
    s = _CHATML_RE.sub(" ", text)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("ё", "е").replace("Ё", "Е")
    if not keep_case:
        s = s.lower()
    out: list[str] = []
    for token in _split_tokens(s):
        if _NUM_RE.match(token):
            out.extend(_number_token(token))
        else:
            out.append(token)
    return _glue(out)


def _split_tokens(text: str) -> list[str]:
    """Токенизация с сохранением версий/CIDR как одного цифрового токена."""
    out: list[str] = []
    pos = 0
    for match in _VERSION_RE.finditer(text):
        out.extend(_TOKEN_RE.findall(text[pos:match.start()]))
        out.append(match.group(0))
        pos = match.end()
    out.extend(_TOKEN_RE.findall(text[pos:]))
    return out


def strip_separators(form: str) -> str:
    """`CI/CD` → `CICD`: форма без разделителей, для сравнения регистра (terms.py)."""
    return _NON_WORD_RE.sub("", form)


def normalize(text: str) -> str:
    """Строка для WER/CER: токены через пробел, lowercased, пунктуация удалена."""
    return " ".join(t.lower() for t in tokens(text))


def _bigrams(seq: list[str]) -> set[tuple[str, str]]:
    return {(seq[i], seq[i + 1]) for i in range(len(seq) - 1)}


def strip_foreign_fillers(
    hyp_tokens: list[str], ref_tokens: list[str]
) -> tuple[list[str], list[int]]:
    """Удаляет из гипотезы филлеры, которых нет в референсе (V8 §2.5).

    Возвращает (очищенные токены, индексы сохранённых токенов в исходной гипотезе).
    """
    ref_set = set(ref_tokens)
    ref_bigrams = _bigrams(ref_tokens)
    kept: list[str] = []
    kept_idx: list[int] = []
    i = 0
    while i < len(hyp_tokens):
        if i + 1 < len(hyp_tokens):
            bigram = (hyp_tokens[i], hyp_tokens[i + 1])
            if bigram in FILLER_BIGRAMS and bigram not in ref_bigrams:
                i += 2
                continue
        token = hyp_tokens[i]
        if token in FILLER_UNIGRAMS and token not in ref_set:
            i += 1
            continue
        kept.append(token)
        kept_idx.append(i)
        i += 1
    return kept, kept_idx


def normalize_pair(ref_text: str, hyp_text: str) -> tuple[list[str], list[str]]:
    """Пара (референс, гипотеза) для WER: оба нормализованы, филлеры гипотезы сняты."""
    ref_tokens = tokens(ref_text)
    hyp_tokens = tokens(hyp_text)
    hyp_tokens, _ = strip_foreign_fillers(hyp_tokens, ref_tokens)
    return ref_tokens, hyp_tokens


def remove_disfluencies(ref_tokens: list[str]) -> list[str]:
    """`ref_normalized` для R3: удаление маркеров из фиксированного списка."""
    return [t for t in ref_tokens if t not in DISFLUENCIES]
