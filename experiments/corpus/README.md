# Публичный sample русской IT-речи — только для dev-калибровки

Статус: **unverified, dev-only**. Это не корпус V8, не бенчмарк и не набор для публичных
WER/CER. Один подкаст не описывает популяцию дикторов.

Дата получения: **2026-10-26**. Медиа и черновые расшифровки лежат в `experiments/corpus/local/`
и исключены из Git. Источник — публичный датасет на Hugging Face; ссылки ниже проверены.

## Источник

| Поле | Значение |
|---|---|
| Датасет | `bond005/podlodka_speech` — https://huggingface.co/datasets/bond005/podlodka_speech |
| Закреплённая ревизия | `4a9161485a70ea849ccaa54a72faa9bfc0a60627` (последнее изменение 2024-04-05) |
| Дерево ревизии | https://huggingface.co/datasets/bond005/podlodka_speech/tree/4a9161485a70ea849ccaa54a72faa9bfc0a60627 |
| Домен | русский IT-подкаст Podlodka: распознавание речи, observability, инженерный менеджмент |
| Страницы эпизодов (HTTP 200, заголовки совпадают) | https://podlodka.io/292 , https://podlodka.io/306 , https://podlodka.io/132 |
| Лицензия | **неизвестна**: в карточке датасета нет поля `license` и нет текста лицензии (README — 611 байт, только frontmatter) |
| Происхождение поля `transcription` | не документировано |
| Спикеры, таймстемпы внутри эпизодов | в датасете отсутствуют |

Медиа из этого набора в Git не включаются и в этой задаче не распространяются.

## Что скачано

5 сегментов test-сплита, суммарно 174.29 с и 5 578 276 байт (5.32 MiB). Формат у всех файлов
проверен `ffprobe`: `pcm_s16le`, 16 кГц, mono.

| segment_id | эпизод | название | с | байт | sha256 |
|---|---|---|---|---|---|
| podlodka-test-19 | 292 | Распознавание речи | 20.13 | 644310 | `afb9bcddd600197932a458ee9bd61cb277be8c704f1c69cea9eea9156b00510b` |
| podlodka-test-18 | 292 | Распознавание речи | 52.96 | 1694952 | `570144562be79cabfaad110a2cb7e82160a9084e76f2f94f89a474823401903a` |
| podlodka-test-17 | 292 | Распознавание речи | 59.36 | 1899660 | `ef702673e3c98f203a6b5af1200297c144cc7303d3c76f03e8979218eb81e4c7` |
| podlodka-test-10 | 306 | Observability и логирование | 24.60 | 787352 | `78409a0c5a02a4894b9fe79ca8804a946c5cc33a547e90b33b9a5a7a7c301f4d` |
| podlodka-test-02 | 132 | Дисфункции организаций | 17.24 | 552002 | `528fb973adacec57f04bca521ca80101c76bdd6757cc2d0439e2472387d2f22f` |

Латинские IT-термины в тексте этих сегментов: `Commons`, `Creative`, `Golos`, `OpenSTT`,
`Sberdevices`, `Share-alike`, `Wav2Vec`, `Wav2Vec2`, `end-to-end`, `learning`, `self-supervised`,
`Librispeech`, `observability`, `one-on-one`, `performance`, `review`. Кириллическая техническая
лексика (поды, деплой, реплики) в тех же сегментах есть, но не размечена.

## Разметка

`annotation_status = unverified`. Поле `transcription` взято у издателя датасета, его
происхождение не документировано и человеком против этих аудио не сверялось. В этой задаче
ASR-модель не запускалась и эталон ею не создавался. Черновики — в
`local/transcripts/segments.template.jsonl`: нужно прослушать сегменты и заполнить
`ref_verbatim`, `ref_normalized`, `terms[]`, после чего выставить статус `verified`.

## Как воспроизвести

Из корня workspace. Нужны `python3` (stdlib) и `ffprobe`. Скрипт читает манифест из этого файла,
сверяет ревизию датасета, докачивает только отсутствующие файлы, создаёт отсутствующий черновик
разметки и проверяет формат, размер, хэш и длительность. Существующие файлы не перезаписываются:
расхождение — ошибка.

```bash
python3 - <<'PY'
import hashlib, json, pathlib, re, subprocess, sys, urllib.parse, urllib.request

ROOT = pathlib.Path.cwd()
LOCAL = ROOT / "experiments/corpus/local"
LIMIT = 50 * 1024 * 1024

man = json.loads(re.search(
    r"<!-- manifest:start -->\n(.*?)\n<!-- manifest:end -->",
    (ROOT / "experiments/corpus/README.md").read_text(encoding="utf-8"), re.S).group(1))

def get(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read() if method == "GET" else b""

live = json.loads(get(f"https://huggingface.co/api/datasets/{man['dataset']}"))["sha"]
if live != man["revision"]:
    sys.exit(f"REVISION DRIFT: live {live} != pinned {man['revision']}. "
             f"Refusing. Exact-pin alternative: "
             f"https://huggingface.co/datasets/{man['dataset']}/resolve/{man['revision']}/"
             f"data/{man['split']}-00000-of-00001.parquet (needs a parquet reader); "
             f"re-verify hashes before updating the manifest.")

rows, offset = {}, 0
while True:
    page = json.loads(get("https://datasets-server.huggingface.co/rows?"
                           f"dataset={urllib.parse.quote(man['dataset'], safe='')}"
                           f"&config=default&split={man['split']}&offset={offset}&length=10"))
    rows.update({r["row_idx"]: r["row"] for r in page["rows"]})
    offset += len(page["rows"])
    if offset >= page["num_rows_total"]:
        break

total = 0
for s in man["segments"]:
    dst = LOCAL / s["file"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        print("present ", s["file"])
    else:
        data = get(rows[s["row_index"]]["audio"][0]["src"])
        dst.write_bytes(data)
        print("download", s["file"], len(data))
    total += dst.stat().st_size
if total > LIMIT:
    sys.exit(f"over limit: {total} > {LIMIT}")

tpl_path = LOCAL / "transcripts/segments.template.jsonl"
if not tpl_path.exists():
    tpl_path.parent.mkdir(parents=True, exist_ok=True)
    with tpl_path.open("x", encoding="utf-8") as out:
        for s in man["segments"]:
            draft = {"segment_id": s["segment_id"], "source_dataset": man["dataset"],
                     "source_revision": man["revision"], "row_index": s["row_index"],
                     "ref_verbatim": rows[s["row_index"]]["transcription"],
                     "ref_normalized": "", "terms": [], "expected_latin": [],
                     "speaker_id": "", "register": "", "mic_class": "", "noise_class": "",
                     "annotator": "", "annotation_date": "", "annotation_status": "unverified"}
            out.write(json.dumps(draft, ensure_ascii=False) + "\n")
tpl = [json.loads(l) for l in tpl_path.read_text(encoding="utf-8").splitlines()]
assert [t["segment_id"] for t in tpl] == [s["segment_id"] for s in man["segments"]]

for s, t in zip(man["segments"], tpl):
    p = LOCAL / s["file"]
    assert p.stat().st_size == s["bytes"], (s["file"], "bytes")
    assert hashlib.sha256(p.read_bytes()).hexdigest() == s["sha256"], (s["file"], "sha256")
    probe = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", str(p)]))
    st = probe["streams"][0]
    assert (st["codec_name"], int(st["sample_rate"]), st["channels"]) == ("pcm_s16le", 16000, 1), s["file"]
    assert abs(float(probe["format"]["duration"]) - s["duration_s"]) < 0.1, s["file"]
    assert s["annotation_status"] == t["annotation_status"] == "unverified", s["file"]

print("OK", len(man["segments"]), "segments", total, "bytes",
      round(sum(s["duration_s"] for s in man["segments"]), 2), "s")
PY
```

Ожидаемый вывод: пять строк `present`, затем
`OK 5 segments 5578276 bytes 174.29 s`.

<!-- manifest:start -->
{"schema":"speech-runtime/corpus-sample/v1","retrieval_date":"2026-10-26","role":"dev-only calibration; NOT the V8 ru-IT corpus; no public WER/CER","dataset":"bond005/podlodka_speech","dataset_url":"https://huggingface.co/datasets/bond005/podlodka_speech","revision":"4a9161485a70ea849ccaa54a72faa9bfc0a60627","split":"test","license":"unknown: dataset card has no license field and no license text","license_evidence":"https://huggingface.co/datasets/bond005/podlodka_speech/raw/4a9161485a70ea849ccaa54a72faa9bfc0a60627/README.md (frontmatter only, 611 bytes, no license key)","ground_truth":"dataset transcription field, publisher-supplied, provenance undocumented, not verified by a human against the audio","annotation_status":"unverified","audio_format":"pcm_s16le 16000 Hz mono","total_bytes":5578276,"total_duration_s":174.29,"segments":[{"segment_id":"podlodka-test-19","row_index":19,"episode":292,"episode_title":"Распознавание речи","file":"audio/podlodka_test19_ep292.wav","bytes":644310,"duration_s":20.13,"sha256":"afb9bcddd600197932a458ee9bd61cb277be8c704f1c69cea9eea9156b00510b","annotation_status":"unverified","latin_terms":["Commons","Creative","Golos","OpenSTT","Sberdevices","Share-alike"]},{"segment_id":"podlodka-test-18","row_index":18,"episode":292,"episode_title":"Распознавание речи","file":"audio/podlodka_test18_ep292.wav","bytes":1694952,"duration_s":52.96,"sha256":"570144562be79cabfaad110a2cb7e82160a9084e76f2f94f89a474823401903a","annotation_status":"unverified","latin_terms":["Wav2Vec","Wav2Vec2","end-to-end","learning","self-supervised"]},{"segment_id":"podlodka-test-17","row_index":17,"episode":292,"episode_title":"Распознавание речи","file":"audio/podlodka_test17_ep292.wav","bytes":1899660,"duration_s":59.36,"sha256":"ef702673e3c98f203a6b5af1200297c144cc7303d3c76f03e8979218eb81e4c7","annotation_status":"unverified","latin_terms":["Librispeech"]},{"segment_id":"podlodka-test-10","row_index":10,"episode":306,"episode_title":"Observability и логирование","file":"audio/podlodka_test10_ep306.wav","bytes":787352,"duration_s":24.6,"sha256":"78409a0c5a02a4894b9fe79ca8804a946c5cc33a547e90b33b9a5a7a7c301f4d","annotation_status":"unverified","latin_terms":["observability"]},{"segment_id":"podlodka-test-02","row_index":2,"episode":132,"episode_title":"Дисфункции организаций","file":"audio/podlodka_test02_ep132.wav","bytes":552002,"duration_s":17.24,"sha256":"528fb973adacec57f04bca521ca80101c76bdd6757cc2d0439e2472387d2f22f","annotation_status":"unverified","latin_terms":["one-on-one","performance","review"]}]}
<!-- manifest:end -->

## Ограничения

- Один подкаст, 3 эпизода, дикторы не размечены — репрезентативность не оценивалась.
- Человеческого эталона нет; WER/CER по черновикам не считается.
- Лицензия неизвестна: происхождение и условия переиспользования аудио не установлены.
- Спикеров и таймстемпов в датасете нет → speaker-ID EER и повторная нарезка из эпизодов невозможны.
- `validation`-сплит датасета дублирует `test` (те же эпизоды и тексты) → это не независимые сплиты.
- В `train` 9 меток эпизодов, часть не-IT (`Зубы`, `Бег`, `Шахматы`, `Производство лекарств`), у эпизода 275 два разных заголовка.
