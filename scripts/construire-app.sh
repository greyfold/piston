#!/bin/zsh
# Construit l'application Mac « Piston » dans dist/.
#   scripts/construire-app.sh             construit (signature locale)
#   scripts/construire-app.sh --ouvrir    construit et lance
#   scripts/construire-app.sh --publier   construit une version à distribuer : signée Developer ID avec le
#                                         runtime renforcé, notarisée par Apple, en image disque, et ajoutée
#                                         au flux de mises à jour (Sparkle) ; voir « Publier une version »
#                                         dans le README ; sans --ouvrir, l'application de dist/ n'est ni
#                                         quittée ni remplacée : seule l'image disque est produite
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
PUBLIER=
for a in "$@"; do
  case "$a" in
    --ouvrir) OUVRIR=1 ;;
    --publier) PUBLIER=1 ;;
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

# Mises à jour (Sparkle) : le flux que l'application consulte chaque jour, et la clé publique qui vérifie
# chaque mise à jour. La clé privée reste dans le trousseau du Mac qui publie (`generate_keys`) : la
# perdre, c'est ne plus pouvoir mettre à jour les installations existantes. À sauvegarder.
ADRESSE_MAJ="https://greyfold.ch/downloads/piston"          # appcast.xml, images disque et deltas y sont déposés
FLUX_MAJ="$ADRESSE_MAJ/appcast.xml"
PAGE_PRODUIT="https://greyfold.ch/product/piston/"
CLE_PUBLIQUE_MAJ="V/UHWY7cYVp+2Tjrq9EAgx8rJIJBepBiXEel4NbR8tk="   # sortie de `generate_keys -p`
# Identifiants de notarisation, rangés une fois dans le trousseau par
# `xcrun notarytool store-credentials piston-notarisation`.
PROFIL_NOTARISATION="piston-notarisation"
SPARKLE_BIN="$RACINE/app/.build/artifacts/sparkle/Sparkle/bin"
VERSIONS="$RACINE/dist/versions"                  # toutes les images publiées : les deltas en partent. À garder.
NOTES="$TMP/notes.md"
# La section de CHANGELOG.md pour cette version : les nouveautés que montre la fenêtre de mise à jour.
awk -v v="$VERSION" '/^## /{ if (dedans) exit; if (index($0, "## " v " ") == 1) { dedans = 1; next } } dedans' \
  "$RACINE/CHANGELOG.md" > "$NOTES"

IDENTITE=
if [[ -n "$PUBLIER" ]]; then
  # Tout ce qui peut faire échouer la publication est vérifié avant la construction.
  IDENTITE="${PISTON_IDENTITE:-$(security find-identity -v -p codesigning | sed -nE 's/.*"(Developer ID Application: [^"]+)".*/\1/p' | head -1)}"
  [[ -n "$IDENTITE" ]] || { print -u2 "Aucun certificat « Developer ID Application » dans le trousseau."; exit 1; }
  [[ -n "$CLE_PUBLIQUE_MAJ" ]] || { print -u2 "CLE_PUBLIQUE_MAJ est vide : la poser d'après \`$SPARKLE_BIN/generate_keys -p\`."; exit 1; }
  [[ -s "$NOTES" ]] || { print -u2 "CHANGELOG.md : pas de section « ## $VERSION — … » ; elle sert de notes de version."; exit 1; }
  [[ ! -e "$VERSIONS/Piston-$VERSION.dmg" ]] || { print -u2 "La version $VERSION est déjà publiée ($VERSIONS) : changer la version dans pyproject.toml."; exit 1; }
  xcrun notarytool history --keychain-profile "$PROFIL_NOTARISATION" >/dev/null \
    || { print -u2 "Profil de notarisation « $PROFIL_NOTARISATION » introuvable : xcrun notarytool store-credentials $PROFIL_NOTARISATION"; exit 1; }
fi

# Signe un binaire : Developer ID et runtime renforcé avec --publier, signature locale sinon.
#   signer <chemin> [droits.plist]
signer() {
  local droits=()
  [[ -n "${2:-}" ]] && droits=(--entitlements "$2")
  if [[ -n "$PUBLIER" ]]; then
    codesign --force --options runtime --timestamp --sign "$IDENTITE" "${droits[@]}" "$1"
  else
    codesign --force --sign - "${droits[@]}" "$1"
  fi
}

swift build -c release --package-path "$RACINE/app"
BIN="$(swift build -c release --package-path "$RACINE/app" --show-bin-path)/RechercheEmploi"

mkdir -p "$NOUVELLE/Contents/MacOS" "$NOUVELLE/Contents/Frameworks" "$RES"
cp "$BIN" "$NOUVELLE/Contents/MacOS/RechercheEmploi"
# Sparkle (mises à jour) : le cadriciel dans Contents/Frameworks, trouvé par un rpath. Ses services XPC ne
# servent qu'aux applications en bac à sable : retirés.
ditto "${BIN:h}/Sparkle.framework" "$NOUVELLE/Contents/Frameworks/Sparkle.framework"
rm -rf "$NOUVELLE/Contents/Frameworks/Sparkle.framework/Versions/B/XPCServices" \
       "$NOUVELLE/Contents/Frameworks/Sparkle.framework/XPCServices"
install_name_tool -add_rpath @executable_path/../Frameworks "$NOUVELLE/Contents/MacOS/RechercheEmploi" 2>/dev/null

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
signer "$RES/lima/bin/limactl" "$TMP/vz.entitlements"

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
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleIconFile</key><string>Icone</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Le micro sert à dicter tes réponses aux entretiens. La voix reste sur ton Mac.</string>
  <key>NSSpeechRecognitionUsageDescription</key><string>La reconnaissance vocale transcrit ta dictée sur ton Mac, sans rien envoyer en ligne.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PLIST
# Sparkle : flux et clé publique dès que la clé est connue (« Rechercher les mises à jour… » marche alors
# aussi dans une construction locale) ; la vérification quotidienne, seulement dans une version publiée.
if [[ -n "$CLE_PUBLIQUE_MAJ" ]]; then
  plutil -insert SUFeedURL -string "$FLUX_MAJ" "$NOUVELLE/Contents/Info.plist"
  plutil -insert SUPublicEDKey -string "$CLE_PUBLIQUE_MAJ" "$NOUVELLE/Contents/Info.plist"
  plutil -insert SUEnableAutomaticChecks -bool "$([[ -n "$PUBLIER" ]] && print YES || print NO)" "$NOUVELLE/Contents/Info.plist"
fi
plutil -lint "$NOUVELLE/Contents/Info.plist" >/dev/null

# Le runtime renforcé (--publier) coupe le micro sauf droit explicite : la dictée en a besoin.
cat > "$TMP/app.entitlements" <<'ENT'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>com.apple.security.device.audio-input</key><true/></dict></plist>
ENT
if [[ -n "$PUBLIER" ]]; then
  # La notarisation exige que chaque binaire porte la signature Developer ID : Python et les bibliothèques
  # des dépendances d'abord, puis ses exécutables.
  find "$RES/python" -type f \( -name '*.so' -o -name '*.dylib' \) -print0 | while IFS= read -r -d '' f; do signer "$f"; done
  find "$RES/python" -type f -perm +111 ! -name '*.so' ! -name '*.dylib' -print0 | while IFS= read -r -d '' f; do
    [[ "$(file -b --mime-type "$f")" == application/x-mach-binary ]] && signer "$f"
  done
fi
# Sparkle, de l'intérieur vers l'extérieur (ses services XPC retirés, sa signature d'origine ne tient plus).
SPK="$NOUVELLE/Contents/Frameworks/Sparkle.framework"
signer "$SPK/Versions/B/Autoupdate"
signer "$SPK/Versions/B/Updater.app"
signer "$SPK"
if [[ -n "$PUBLIER" ]]; then
  signer "$NOUVELLE" "$TMP/app.entitlements"   # sans --deep : chaque binaire garde ses droits
else
  codesign --force --sign - "$NOUVELLE"        # sans --deep : limactl garde ses droits
fi
codesign --verify --strict --deep "$NOUVELLE"

if [[ -n "$PUBLIER" ]]; then
  # Soumet à Apple et attend ; un refus arrête tout, avec le journal d'Apple.
  notariser() {
    local r id statut
    r="$(xcrun notarytool submit "$1" --keychain-profile "$PROFIL_NOTARISATION" --wait --output-format json)"
    id="$(print -r -- "$r" | "$PY" -c 'import json, sys; print(json.load(sys.stdin)["id"])')"
    statut="$(print -r -- "$r" | "$PY" -c 'import json, sys; print(json.load(sys.stdin)["status"])')"
    if [[ "$statut" != Accepted ]]; then
      xcrun notarytool log "$id" --keychain-profile "$PROFIL_NOTARISATION" >&2 || true
      print -u2 "Notarisation refusée ($statut) : ${1:t}"
      exit 1
    fi
  }
  print "Notarisation de l'application (quelques minutes)…"
  ditto -c -k --keepParent "$NOUVELLE" "$TMP/Piston.zip"
  notariser "$TMP/Piston.zip"
  xcrun stapler staple -q "$NOUVELLE"
  # L'image disque : l'application et un raccourci vers Applications (Sparkle ne remplace pas une
  # application lancée depuis Téléchargements ou depuis l'image).
  mkdir -p "$TMP/image"
  ditto "$NOUVELLE" "$TMP/image/Piston.app"
  ln -s /Applications "$TMP/image/Applications"
  DMG="$TMP/Piston-$VERSION.dmg"
  hdiutil create -quiet -volname Piston -srcfolder "$TMP/image" -fs APFS -format ULFO "$DMG"
  codesign --force --timestamp --sign "$IDENTITE" "$DMG"
  print "Notarisation de l'image disque…"
  notariser "$DMG"
  xcrun stapler staple -q "$DMG"
  # Le flux : generate_appcast signe l'image avec la clé privée (trousseau), calcule les deltas depuis
  # les versions précédentes de $VERSIONS et y réécrit appcast.xml.
  mkdir -p "$VERSIONS"
  cp "$DMG" "$VERSIONS/"
  cp "$NOTES" "$VERSIONS/Piston-$VERSION.md"
  if ! "$SPARKLE_BIN/generate_appcast" --download-url-prefix "$ADRESSE_MAJ/" --link "$PAGE_PRODUIT" \
       --embed-release-notes "$VERSIONS"; then
    rm -f "$VERSIONS/Piston-$VERSION.dmg" "$VERSIONS/Piston-$VERSION.md"
    print -u2 "Flux de mises à jour non généré : la version $VERSION n'est pas publiée."
    exit 1
  fi
  cp "$DMG" "$RACINE/dist/Piston.dmg"              # le lien fixe de la page produit
fi

# Une publication seule ne touche pas l'application du Mac : la version part dans l'image disque et le flux.
if [[ -n "$PUBLIER" && -z "$OUVRIR" ]]; then
  print "\nVersion $VERSION prête : $VERSIONS/Piston-$VERSION.dmg (l'application de dist/ n'a pas été remplacée)"
  print "Mise en ligne dans $ADRESSE_MAJ/, depuis le dépôt du site (~/Projets/greyfold) :"
  print "  npm run deploy:piston     (images et deltas, Piston.dmg, puis appcast.xml en dernier)"
  print "puis committer src/data/releases.json et npm run deploy (bouton et version de la page produit)."
  exit 0
fi

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
if [[ -n "$PUBLIER" ]]; then
  print "\nVersion $VERSION prête : $VERSIONS/Piston-$VERSION.dmg"
  print "Mise en ligne dans $ADRESSE_MAJ/, depuis le dépôt du site (~/Projets/greyfold) :"
  print "  npm run deploy:piston     (images et deltas, Piston.dmg, puis appcast.xml en dernier)"
  print "puis committer src/data/releases.json et npm run deploy (bouton et version de la page produit)."
fi
if [[ -n "$OUVRIR" ]]; then
  open "$APP"
fi
exit 0
