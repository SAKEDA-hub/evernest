#!/usr/bin/env bash
# Compile ARB files with the real `flutter gen-l10n` in a throwaway Flutter project.
# Usage: scripts/check_gen_l10n.sh <arb_dir> <template.arb>
# Requires Flutter on PATH. Exit code is gen-l10n's.
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
cat > l10n.yaml <<EOF
arb-dir: lib/l10n
template-arb-file: $TEMPLATE
output-localization-file: app_localizations.dart
EOF

flutter gen-l10n
echo "flutter gen-l10n: OK"
