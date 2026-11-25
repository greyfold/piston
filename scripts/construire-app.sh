#!/bin/zsh
# Construit l'application Mac « Piston » dans dist/ (signature locale, pas de notarisation).
#   scripts/construire-app.sh            construit
#   scripts/construire-app.sh --ouvrir   construit et lance
#
# L'application est autoportante : elle embarque son Python (python-build-standalone, installé
# par uv), les dépendances et le code (Contents/Resources/racine : src, config, pyproject.toml).
# Rien n'est installé à côté : ni agent launchd, ni dossier de projet. Ses données vivent dans
# ~/Library/Application Support/Recherche emploi ; le passage du matin, l'alerte et la
# surveillance sont lancés par l'application elle-même (`jobsearch echeances`).
# Claude Code, Google Chrome et l'écran tournent dans la machine virtuelle (Lima, embarqué ici,
# version et somme de contrôle figées dans ce script) : rien à installer sur le Mac.
#
# L'application est construite dans un dossier temporaire de dist/ (même volume) ; l'ancienne n'est
# quittée puis remplacée par un `mv` qu'une fois tout réussi, signature comprise. Un échec en cours
# de route laisse donc l'application installée intacte.
set -euo pipefail
# Lima (limactl, agent de la VM) et son agent invité sont ici pris pour Apple silicon.
[[ "$(uname -m)" == arm64 ]] || { print -u2 "Ce script construit pour un Mac Apple silicon (arm64) ; cette machine est $(uname -m)."; exit 1; }
RACINE="${0:A:h:h}"
OUVRIR=
for a in "$@"; do
  case "$a" in
    --ouvrir) OUVRIR=1 ;;
    *) print -u2 "Option inconnue : $a"; exit 2 ;;
  esac
done
APP="$RACINE/dist/Piston.app"
# Nom d'avant « Piston » : retiré de dist/ une fois la nouvelle en place.
ANCIEN_NOM="$RACINE/dist/Recherche emploi.app"
mkdir -p "$RACINE/dist"
TMP="$(mktemp -d)"
# Dossier de construction à côté de la destination (même volume : le `mv` final est un renommage).
# Le nom du bundle y est le même qu'à la fin : rien (.pth relatif, signature) ne dépend du dossier.
STAGE="$(mktemp -d "$RACINE/dist/.construction.XXXXXX")"
trap 'rm -rf "$TMP" "$STAGE"' EXIT
NOUVELLE="$STAGE/Piston.app"
RES="$NOUVELLE/Contents/Resources"

LIMA_VERSION=2.3.0
# Somme SHA-256 de lima-$LIMA_VERSION-Darwin-arm64.tar.gz, figée ici (relevée sur l'archive déjà
# téléchargée et vérifiée contre SHA256SUMS de la publication ; à recalculer à chaque changement
# de version).
LIMA_SHA256=b7af444bd8f91d3c307477e562f2cf3bd7595d62b9429185e4fa841563578cfc
# Version de Python exigée par le projet (requires-python = ">=3.14").
VERSION_PY="$(sed -nE 's/^requires-python *= *">= *([0-9]+\.[0-9]+).*/\1/p' "$RACINE/pyproject.toml")"
[[ -n "$VERSION_PY" ]] || { print -u2 "pyproject.toml : requires-python introuvable, version de Python inconnue."; exit 1; }
VERSION="$(sed -nE 's/^version *= *"([^"]+)".*/\1/p' "$RACINE/pyproject.toml")"
[[ -n "$VERSION" ]] || { print -u2 "pyproject.toml : version du projet introuvable."; exit 1; }

swift build -c release --package-path "$RACINE/app"
BIN="$(swift build -c release --package-path "$RACINE/app" --show-bin-path)/RechercheEmploi"

mkdir -p "$NOUVELLE/Contents/MacOS" "$RES"
cp "$BIN" "$NOUVELLE/Contents/MacOS/RechercheEmploi"

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

# 3. Les dépendances, aux versions et aux sommes du verrou (--require-hashes) ; le code est trouvé par un .pth relatif
#    (site-packages → ../../../../racine/src), donc l'application peut être déplacée.
uv export --project "$RACINE" --frozen --no-dev --no-emit-project -q -o "$TMP/dependances.txt"
uv pip install --python "$PY" --no-config --require-hashes -q -r "$TMP/dependances.txt"
print -r -- "../../../../racine/src" > "$SITE/jobsearch.pth"
"$PY" -c 'import jobsearch.cli, jobsearch.web.app' || { print -u2 "Le code embarqué ne se charge pas."; exit 1; }
"$PY" -m compileall -q "$RES/racine/src" "$SITE" >/dev/null || true

# 4. Lima (machine virtuelle), depuis ses publications officielles, somme de contrôle figée vérifiée.
CACHE="$RACINE/.cache/lima-$LIMA_VERSION"
ARCHIVE="lima-$LIMA_VERSION-Darwin-arm64.tar.gz"
if [[ ! -f "$CACHE/$ARCHIVE" ]]; then
  mkdir -p "$CACHE"
  curl -fsSL -o "$CACHE/$ARCHIVE.part" "https://github.com/lima-vm/lima/releases/download/v$LIMA_VERSION/$ARCHIVE"
  mv "$CACHE/$ARCHIVE.part" "$CACHE/$ARCHIVE"
fi
print -r -- "$LIMA_SHA256  $CACHE/$ARCHIVE" | shasum -a 256 -c - >/dev/null || { print -u2 "Lima : somme de contrôle fausse ($CACHE/$ARCHIVE ; supprime-le pour le retélécharger)."; exit 1; }
mkdir -p "$RES/lima"
# Seulement limactl (pilote Virtualization.framework compris) et l'agent de la VM Linux arm64.
tar -xzf "$CACHE/$ARCHIVE" -C "$RES/lima" ./bin/limactl ./share/lima/lima-guestagent.Linux-aarch64.gz
# limactl doit garder le droit d'utiliser Virtualization.framework.
cat > "$TMP/vz.entitlements" <<'ENT'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>com.apple.security.virtualization</key><true/></dict></plist>
ENT
codesign --force --sign - --entitlements "$TMP/vz.entitlements" "$RES/lima/bin/limactl"

swift "$RACINE/app/icone.swift" "$TMP/Icone.iconset"
iconutil -c icns "$TMP/Icone.iconset" -o "$RES/Icone.icns"

cat > "$NOUVELLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Piston</string>
  <key>CFBundleDisplayName</key><string>Piston</string>
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
plutil -lint "$NOUVELLE/Contents/Info.plist" >/dev/null
codesign --force --sign - "$NOUVELLE"     # sans --deep : limactl garde ses droits
codesign --verify --strict "$NOUVELLE"

# Tout a réussi : on quitte l'application en cours (elle ne doit pas tourner pendant le
# remplacement), puis l'ancienne cède la place par un renommage. Elle reste dans $STAGE, que le
# trap n'efface qu'à la sortie : si la nouvelle ne se met pas en place, l'ancienne est remise.
# Quittée par son identifiant, qui ne change pas : celle d'avant le nom « Piston » l'est aussi.
osascript -e 'quit app id "app.rechercheemploi.mac"' 2>/dev/null || true
for _ in {1..50}; do pgrep -x RechercheEmploi >/dev/null || break; sleep 0.2; done
if [[ -e "$APP" ]]; then mv "$APP" "$STAGE/ancienne.app"; fi
if ! mv "$NOUVELLE" "$APP"; then
  if [[ -e "$STAGE/ancienne.app" && ! -e "$APP" ]]; then mv "$STAGE/ancienne.app" "$APP"; fi
  print -u2 "La nouvelle application n'a pas pu être mise en place ; l'ancienne est restée à sa place."
  exit 1
fi
if [[ -e "$ANCIEN_NOM" ]]; then mv "$ANCIEN_NOM" "$STAGE/ancien-nom.app"; fi
print "Application construite : $APP ($(du -sh "$APP" | cut -f1))"
if [[ -n "$OUVRIR" ]]; then
  open "$APP"
fi
exit 0
