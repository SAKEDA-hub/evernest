#!/usr/bin/env bash
# Full hackathon pipeline on the partner files.
# Put intl_ru.arb, intl_kk.arb, intl_uz.arb, intl_en.arb into ./data (git-ignored: never commit them).
#
#   scripts/run_pipeline.sh detect   # reports, glossary candidates, review sheet, self-evaluation
#   scripts/run_pipeline.sh apply    # after the native speaker filled out/review_kk.csv
#
# Optional: KK_VOCAB=path/to/kazakh_words.txt for the spelling check, LLM=anthropic|openai
# (with LOCQA_LLM_* env vars, see README) to add the LLM pass.
set -euo pipefail

DATA=${DATA:-data}
OUT=${OUT:-out}
GLOSSARY=${GLOSSARY:-glossary.csv}
mkdir -p "$OUT"

for f in intl_ru.arb intl_kk.arb; do
  [ -f "$DATA/$f" ] || { echo "нет $DATA/$f" >&2; exit 2; }
done

refs=()
for f in intl_uz.arb intl_en.arb; do [ -f "$DATA/$f" ] && refs+=(--ref "$DATA/$f"); done
extra=()
[ -n "${KK_VOCAB:-}" ] && extra+=(--vocab "$KK_VOCAB")
[ -n "${LLM:-}" ] && extra+=(--llm "$LLM")

case "${1:-detect}" in
  detect)
    locqa check --source "$DATA/intl_ru.arb" --target "$DATA/intl_kk.arb" --glossary "$GLOSSARY" \
      "${refs[@]}" "${extra[@]}" --out "$OUT/report_kk.json"
    if [ -f "$DATA/intl_uz.arb" ]; then
      locqa check --source "$DATA/intl_ru.arb" --target "$DATA/intl_uz.arb" --glossary "$GLOSSARY" \
        --ref "$DATA/intl_kk.arb" "${refs[@]}" --out "$OUT/report_uz.json" || true
    fi
    locqa mine-terms --source "$DATA/intl_ru.arb" --target "$DATA/intl_kk.arb" --out "$OUT/term_candidates.csv"
    sugg=()
    [ -f "$OUT/suggestions.json" ] && sugg=(--suggestions "$OUT/suggestions.json")  # LLM/colleague proposals
    locqa export-review --report "$OUT/report_kk.json" "${sugg[@]}" --out "$OUT/review_kk.csv"
    # draft for the native speaker to look at: every change marked x-reviewed=false
    locqa apply --target "$DATA/intl_kk.arb" --review "$OUT/review_kk.csv" --source "$DATA/intl_ru.arb" \
      --include-unreviewed --out "$OUT/intl_kk_fixed.draft.arb"
    # self-evaluation on synthetic errors (optimistic proxy for recall)
    locqa seed --source "$DATA/intl_ru.arb" --target "$DATA/intl_kk.arb" --glossary "$GLOSSARY" \
      "${refs[@]}" --n 100 --out "$OUT/seeded_kk.arb" --answers "$OUT/seeded_answers.json"
    locqa check --source "$DATA/intl_ru.arb" --target "$OUT/seeded_kk.arb" --glossary "$GLOSSARY" \
      "${refs[@]}" --out "$OUT/report_seeded.json" --html - >/dev/null
    locqa eval --report "$OUT/report_seeded.json" --answers "$OUT/seeded_answers.json" > "$OUT/self_eval.json"
    echo "готово: $OUT/report_kk.html, $OUT/review_kk.csv (отдать носителю), $OUT/self_eval.json"
    ;;
  apply)
    locqa apply --target "$DATA/intl_kk.arb" --review "$OUT/review_kk.csv" --source "$DATA/intl_ru.arb" \
      --out "$OUT/intl_kk_fixed.arb"
    if ! locqa validate "$OUT/intl_kk_fixed.arb" --source "$DATA/intl_ru.arb"; then
      echo "ВНИМАНИЕ: в intl_kk_fixed.arb остались строки, ломающие gen-l10n (список выше)." >&2
      echo "          Их нужно провести через ревью до сдачи." >&2
    fi
    locqa check --source "$DATA/intl_ru.arb" --target "$DATA/intl_kk.arb" --glossary "$GLOSSARY" \
      "${refs[@]}" --review "$OUT/review_kk.csv" --out "$OUT/report_kk.json"
    locqa review-stats --review "$OUT/review_kk.csv" --report "$OUT/report_kk.json" --out "$OUT/review_stats.json"
    echo "готово: $OUT/intl_kk_fixed.arb; цифры для METHODOLOGY.md — $OUT/review_stats.json, $OUT/self_eval.json"
    echo "дальше: bash scripts/check_gen_l10n.sh <папка с intl_ru.arb и intl_kk_fixed.arb> intl_ru.arb"
    ;;
  *)
    echo "usage: $0 detect|apply" >&2
    exit 2
    ;;
esac
