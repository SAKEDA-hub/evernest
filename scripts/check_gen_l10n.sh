#!/usr/bin/env bash
# Compile ARB files with the real `flutter gen-l10n` in a throwaway Flutter project.
# Usage: scripts/check_gen_l10n.sh <arb_dir> <template.arb>
# Requires Flutter on PATH. Exit code is gen-l10n's.
#
# DROP_INVALID_TEMPLATE_KEYS=1 — gen-l10n aborts on template keys that are not valid Dart
# getter names (e.g. a key starting with a capital letter or containing "@"; the Grand Mobile handout
# template has such keys)
# before it looks at any translation. With this flag such keys (and their @metadata) are removed
# from the temporary copies of ALL files, and the list is printed, so the translations can still
# be compiled. The originals are never modified. The template problem itself must be fixed upstream.
set -euo pipefail

ARB_DIR=$(cd "${1:?arb dir}" && pwd)
TEMPLATE=${2:?template file name, e.g. intl_ru.arb}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

cd "$WORK"
flutter create --no-pub --project-name l10n_check app >/dev/null
cd app
flutter pub add flutter_localizations --sdk=flutter >/dev/null
flutter pub add intl:any >/dev/null
# enable code generation
printf '\nflutter:\n  generate: true\n' >> pubspec.yaml

mkdir -p lib/l10n
cp "$ARB_DIR"/*.arb lib/l10n/
if [ "${DROP_INVALID_TEMPLATE_KEYS:-0}" = "1" ]; then
  python3 - "lib/l10n" "$TEMPLATE" <<'PY'
import json, re, sys
from pathlib import Path
d, template = Path(sys.argv[1]), sys.argv[2]
valid = re.compile(r"[a-z$][A-Za-z0-9_$]*")
load = lambda p: json.loads(p.read_text(encoding="utf-8-sig"))  # last duplicate wins, as in Dart
bad = sorted(k for k in load(d / template) if not k.startswith("@") and not valid.fullmatch(k))
print(f"DROP_INVALID_TEMPLATE_KEYS: исключены ключи шаблона ({len(bad)}): {', '.join(bad) or '—'}")
for p in sorted(d.glob("*.arb")):
    data = {k: v for k, v in load(p).items() if k not in bad and k.lstrip("@") not in bad}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
fi
cat > l10n.yaml <<EOF
arb-dir: lib/l10n
template-arb-file: $TEMPLATE
output-localization-file: app_localizations.dart
EOF

flutter gen-l10n
echo "flutter gen-l10n: OK"
