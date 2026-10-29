# Экспериментальные раннеры

Скрипты, которыми получены числа в [`experiments/pilot/`](../pilot). Это
испытательный стенд, а не основа будущего runtime: ничего не импортируется
основной реализацией и не обещает стабильного API.

Скорер один — [`experiments/asr_eval/cli.py`](../asr_eval/cli.py). Раннеры только
производят гипотезы и метрики, которых у скорера нет (задержки, DER, обучение).

## ASR

| Файл | Что делает |
|---|---|
| `fluid-asr/` | Swift-пакет поверх FluidAudio v0.17.7: `asr` (Parakeet v3/ultra), `nemotron` (батч + hotwords), `stream-nemotron` (подача 20-мс порциями по часам), `diarize` (offline VBx + RTTM) |
| `gigaam_batch.py` | GigaAM v3 / Multilingual через пакет `gigaam` 0.2.0, torch CPU |
| `qwen3_asr_batch.py` | Qwen3-ASR-0.6B через `mlx-qwen3-asr`, с `--context` |

Сборка Swift-раннера:

```bash
cd experiments/corpus/local/tools
git clone --depth 1 --branch v0.17.7 https://github.com/FluidInference/FluidAudio.git
cd "../../runners/fluid-asr" && swift build -c release
```

FluidAudio подключается как локальная зависимость по пути
`../../corpus/local/tools/FluidAudio`; в Git попадает только обёртка.

## Метрики

| Файл | Что считает |
|---|---|
| `analyze_streaming.py` | первый partial, задержка слова, `finish()`, p50/p95 из JSON потокового прогона |
| `der.py` | DER по RTTM с collar и учётом перекрытий |
| `make_error_pairs.py` | пары «наблюдавшаяся ошибка → термин» из dev-гипотез |
| `embedding_eval.py` | Recall@k/MRR для точного, символьного и семантического поиска |
| `embedding_lora.py` | LoRA Qwen3-Embedding на dev-парах, проверка до/после и после перезагрузки |
| `correction_eval.py` | правила замены по символьному расстоянию: исправления и ложные замены |
| `correction_eval_embed.py` | то же, но кандидат выбирается эмбеддингом, а токен — строковым сходством |
| `score_all.py` | приводит JSONL Whisper и TSV остальных к одному виду и зовёт скорер |

## Данные

`experiments/corpus/prepare_veche.py` и `prepare_fleurs.py` готовят манифест,
метаданные и RTTM. Референсы — сценарный текст Veche и официальные транскрипты
FLEURS; ни один набор не получает `transcript_status=verified`, поэтому
`preflight --benchmark` на них сознательно не проходит.

## Патч библиотеки

`patches/last_token_pool.py.txt` — изменённая функция из `mlx_embeddings`, без
которой `mlx-tune` не обучает Qwen3-Embedding (ветвление по `mx.array` внутри
grad запрещено). Патч меняет форму вычисления, не результат инференса; он
применяется к установленной библиотеке вручную, а не через fork.
