"""WER / CER по нормализованному тексту (V8 §3.1).

Corpus-level: сумма ошибок / сумма слов, не среднее по высказываниям.
CER считается по нормализованным токенам, склеенным без пробелов.
"""

from __future__ import annotations

import align
import norm


def score_pair(reference: str, hypothesis: str) -> dict:
    ref_tokens, hyp_tokens = norm.normalize_pair(reference, hypothesis)
    ops, _ = align.align(ref_tokens, hyp_tokens)
    sub, dele, ins = align.edit_counts(ops)
    ref_chars = "".join(ref_tokens)
    hyp_chars = "".join(hyp_tokens)
    char_errors = align.distance(list(ref_chars), list(hyp_chars))
    return {
        "reference": " ".join(ref_tokens),
        "hypothesis": " ".join(hyp_tokens),
        "words": len(ref_tokens),
        "substitutions": sub,
        "deletions": dele,
        "insertions": ins,
        "errors": sub + dele + ins,
        "chars": len(ref_chars),
        "char_errors": char_errors,
    }


def corpus(utterances: list[dict]) -> dict:
    """`utterances`: [{"id": str, "reference": str, "hypothesis": str}, …]."""
    per_utterance = []
    words = errors = chars = char_errors = sub = dele = ins = 0
    for item in utterances:
        row = score_pair(item["reference"], item["hypothesis"])
        row["id"] = item.get("id", "")
        per_utterance.append(row)
        words += row["words"]
        errors += row["errors"]
        chars += row["chars"]
        char_errors += row["char_errors"]
        sub += row["substitutions"]
        dele += row["deletions"]
        ins += row["insertions"]
    return {
        "utterances": len(per_utterance),
        "words": words,
        "errors": errors,
        "substitutions": sub,
        "deletions": dele,
        "insertions": ins,
        "wer": (errors / words) if words else None,
        "cer": (char_errors / chars) if chars else None,
        "per_utterance": per_utterance,
    }
