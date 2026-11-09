#!/bin/zsh
# Construit l'application Mac « Recherche d'emploi » dans dist/ (signature locale, pas de notarisation).
#   scripts/construire-app.sh            construit
#   scripts/construire-app.sh --ouvrir   construit et lance
#
# L'application est autoportante : elle embarque son Python (python-build-standalone, installé
# par uv), les dépendances et le code (Contents/Resources/racine : src, config, pyproject.toml).
# Rien n'est installé à côté : ni agent launchd, ni dossier de projet. Ses données vivent dans
# ~/Library/Application Support/Recherche emploi ; le passage du matin, l'alerte et la
# surveillance sont lancés par l'application elle-même (`jobsearch echeances`).
# Restent des prérequis du Mac : Claude Code (`claude`, connecté depuis l'application), et pour
# la machine virtuelle facultative, Lima.
set -e
RACINE="${0:A:h:h}"
OUVRIR=
for a in "$@"; do
  case "$a" in
    --ouvrir) OUVRIR=1 ;;
    *) print -u2 "Option inconnue : $a"; exit 2 ;;
  esac
done
APP="$RACINE/dist/Recherche emploi.app"
RES="$APP/Contents/Resources"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Version de Python exigée par le projet (requires-python = ">=3.14").
VERSION_PY="$(sed -nE 's/^requires-python *= *">= *([0-9]+\.[0-9]+).*/\1/p' "$RACINE/pyproject.toml")"

swift build -c release --package-path "$RACINE/app"
BIN="$(swift build -c release --package-path "$RACINE/app" --show-bin-path)/RechercheEmploi"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES"
cp "$BIN" "$APP/Contents/MacOS/RechercheEmploi"

# 1. Python autonome (relocalisable : ses bibliothèques sont cherchées à côté de l'exécutable).
uv python install "$VERSION_PY" --managed-python >/dev/null
PY_SRC="$(uv python find --managed-python "$VERSION_PY")"
PY_DIR="${PY_SRC:A:h:h}"
cp -R "$PY_DIR" "$RES/python"
rm -f "$RES"/python/lib/python*/EXTERNALLY-MANAGED
PY="$RES/python/bin/python3"
SITE="$("$PY" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"

# 2. Le code, tel que le travailleur et la machine virtuelle le copient.
mkdir -p "$RES/racine"
cp "$RACINE/pyproject.toml" "$RACINE/uv.lock" "$RES/racine/"
rsync -a --exclude __pycache__ --exclude '*.pyc' "$RACINE/src" "$RACINE/config" "$RES/racine/"

# 3. Les dépendances, aux versions du verrou ; le code est trouvé par un .pth relatif
#    (site-packages → ../../../../racine/src), donc l'application peut être déplacée.
uv export --project "$RACINE" --frozen --no-dev --no-emit-project --no-hashes -q -o "$TMP/dependances.txt"
uv pip install --python "$PY" --no-config -q -r "$TMP/dependances.txt"
print -r -- "../../../../racine/src" > "$SITE/jobsearch.pth"
"$PY" -c 'import jobsearch.cli, jobsearch.web.app' 2>/dev/null || { print -u2 "Le code embarqué ne se charge pas."; exit 1; }
"$PY" -m compileall -q "$RES/racine/src" "$SITE" >/dev/null || true

swift "$RACINE/app/icone.swift" "$TMP/Icone.iconset"
iconutil -c icns "$TMP/Icone.iconset" -o "$RES/Icone.icns"

VERSION="$(sed -nE 's/^version *= *"([^"]+)".*/\1/p' "$RACINE/pyproject.toml")"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Recherche emploi</string>
  <key>CFBundleDisplayName</key><string>Recherche d'emploi</string>
  <key>CFBundleIdentifier</key><string>app.rechercheemploi.mac</string>
  <key>CFBundleExecutable</key><string>RechercheEmploi</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleIconFile</key><string>Icone</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Le micro sert à dicter tes réponses aux entretiens. La voix reste sur ton Mac.</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>La reconnaissance vocale transcrit ta dictée sur ton Mac, sans rien envoyer en ligne.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST
plutil -lint "$APP/Contents/Info.plist" >/dev/null
codesign --force --deep --sign - "$APP"
print "Application construite : $APP ($(du -sh "$APP" | cut -f1))"
if [[ -n "$OUVRIR" ]]; then
  osascript -e 'quit app "Recherche emploi"' 2>/dev/null || true
  open "$APP"
fi
exit 0
