# Результаты прогона на данных Grand Mobile

Файлы результатов в репозиторий **не коммитятся**. По соглашению Decentrathon данные партнёра и производные от них выгрузки не хранятся в репозитории: `intl_kk_fixed.arb` — это полная копия казахского файла, отчёты и `review_kk.csv` содержат строки из него. Результаты сдаются организаторам отдельно.

| Файл | Что это |
|---|---|
| `report_kk.json`, `report_kk.html` | Находки по `intl_kk.arb` (схема из брифа) |
| `report_uz.json`, `report_uz.html` | Бонус: анализ `intl_uz.arb` |
| `review_kk.csv` | Таблица ревью: находки, предложения, решения |
| `intl_kk_fixed.arb` (+ `.review.json`) | Исправленный файл, у каждой изменённой строки флаг `x-reviewed` |
| `term_candidates.csv` | Кандидаты в глоссарий из пар строк |
| `self_eval.json` | Recall на 100 синтетических ошибках |

Как воспроизвести (данные положить в `data/`):

```bash
pip install -e .
cp <предложения LLM/ревью>.json out/suggestions.json   # необязательно
scripts/run_pipeline.sh detect
scripts/run_pipeline.sh apply      # после заполнения out/review_kk.csv носителем
```

Цифры и разбор приведены в [METHODOLOGY.md](../METHODOLOGY.md).
