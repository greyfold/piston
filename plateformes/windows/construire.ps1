# Construit Piston pour Windows : l'installateur dist\windows\Piston-<version>-installation.exe.
#   plateformes\windows\construire.ps1              construit (non signé)
#   plateformes\windows\construire.ps1 -Publier     construit une version à distribuer : signée (Authenticode, si
#                                                   PISTON_SIGNATURE est réglé), avec son flux de mises à jour
#                                                   (latest.json, signé par la clé de publication)
#
# Comme l'application Mac (plateformes/macos/construire.sh), elle est autoportante : son Python
# (python-build-standalone, installé par uv), les dépendances aux versions et sommes de uv.lock, et le code
# (src, config, pyproject.toml) sont dans ses ressources, trouvés par un .pth relatif. La machine virtuelle
# est une distribution WSL2 créée au premier lancement (src/jobsearch/vm/wsl.py) : rien d'autre à installer.
#
# Il faut : uv, Rust (stable, MSVC), Node.js (pour l'outil de Tauri), CMake et LLVM (pour whisper.cpp, la
# dictée). Les runners Windows de GitHub les ont (.github/workflows/windows.yml).
#
# Variables, toutes facultatives :
#   PISTON_CLE_MAJ                 clé publique des mises à jour (minisign) : sans elle, pas de mises à jour
#   TAURI_SIGNING_PRIVATE_KEY      clé privée correspondante (et _PASSWORD) : signe l'installateur pour le flux
#   PISTON_SIGNATURE               commande de signature Authenticode (%1 = fichier), p. ex. Azure Trusted Signing
#   PISTON_SANS_DICTEE=1           construit sans la dictée (pas de CMake ni de LLVM)
param([switch]$Publier)
$ErrorActionPreference = "Stop"
$Racine = (Resolve-Path "$PSScriptRoot\..\..").Path
$App = Join-Path $PSScriptRoot "app"
$Res = Join-Path $App "ressources"
$Sortie = Join-Path $Racine "dist\windows"
$AdresseMaj = "https://greyfold.ch/downloads/piston/windows"

function Lire($motif) {
  $m = Select-String -Path (Join-Path $Racine "pyproject.toml") -Pattern $motif | Select-Object -First 1
  if (-not $m) { throw "pyproject.toml : $motif introuvable" }
  return $m.Matches[0].Groups[1].Value
}
$Version = Lire '^version\s*=\s*"([^"]+)"'
$VersionPy = Lire 'requires-python\s*=\s*">=\s*([0-9]+\.[0-9]+)'
Write-Host "Piston $Version pour Windows (Python $VersionPy)"

if ($Publier) {
  if (-not $env:PISTON_CLE_MAJ) { throw "PISTON_CLE_MAJ manquante : une version publiée doit pouvoir se mettre à jour." }
  if (-not $env:TAURI_SIGNING_PRIVATE_KEY) { throw "TAURI_SIGNING_PRIVATE_KEY manquante : le flux de mises à jour ne serait pas signé." }
  if (-not $env:PISTON_SIGNATURE) { Write-Warning "PISTON_SIGNATURE absente : l'installateur ne sera pas signé (SmartScreen avertira)." }
}

# 0. Les ressources repartent de zéro.
if (Test-Path $Res) { Remove-Item -Recurse -Force $Res }
New-Item -ItemType Directory -Force (Join-Path $Res "racine") | Out-Null

# 1. Python autonome (relocalisable).
uv python install $VersionPy --managed-python | Out-Null
$PySrc = (uv python find --managed-python $VersionPy).Trim()
Copy-Item -Recurse (Split-Path $PySrc) (Join-Path $Res "python")
Remove-Item -ErrorAction SilentlyContinue (Join-Path $Res "python\Lib\EXTERNALLY-MANAGED")
$Py = Join-Path $Res "python\python.exe"
$Site = (& $Py -c "import sysconfig; print(sysconfig.get_path('purelib'))").Trim()

# 2. Le code, tel que la machine virtuelle le copie.
Copy-Item (Join-Path $Racine "pyproject.toml"), (Join-Path $Racine "uv.lock") (Join-Path $Res "racine")
foreach ($d in "src", "config") {
  robocopy (Join-Path $Racine $d) (Join-Path $Res "racine\$d") /E /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "copie de $d impossible" }
}
$global:LASTEXITCODE = 0

# 3. Les dépendances, aux versions et aux sommes du verrou ; le code par un .pth relatif
#    (python\Lib\site-packages → ..\..\..\racine\src) : l'application peut être déplacée.
$Deps = Join-Path $env:TEMP "piston-dependances.txt"
uv export --project $Racine --frozen --no-dev --no-emit-project -q -o $Deps
uv pip install --python $Py --no-config --require-hashes -q -r $Deps
Set-Content -Path (Join-Path $Site "jobsearch.pth") -Value "..\..\..\racine\src" -Encoding ascii
& $Py -c "import jobsearch.cli, jobsearch.web.app, jobsearch.hote; assert jobsearch.hote.SYSTEME == 'windows'"
if ($LASTEXITCODE -ne 0) { throw "Le code embarqué ne se charge pas." }
& $Py -m compileall -q (Join-Path $Res "racine\src") $Site | Out-Null

# 4. Les icônes, tirées de l'icône maîtresse commune (ressources\icone.png).
Push-Location $App
npx --yes @tauri-apps/cli@2 icon (Join-Path $Racine "ressources\icone.png") -o icons | Out-Null

# 5. L'enveloppe et l'installateur (NSIS, par utilisateur, sans droits d'administrateur).
$config = @{ version = $Version; bundle = @{ createUpdaterArtifacts = [bool]$env:TAURI_SIGNING_PRIVATE_KEY } }
if ($env:PISTON_SIGNATURE) { $config.bundle.windows = @{ signCommand = $env:PISTON_SIGNATURE } }
$fichierConfig = Join-Path $env:TEMP "piston-tauri.json"
$config | ConvertTo-Json -Depth 5 | Set-Content -Path $fichierConfig -Encoding utf8
$options = @("build", "--bundles", "nsis", "--config", $fichierConfig)
if ($env:PISTON_SANS_DICTEE) { $options += @("--", "--no-default-features") }
npx --yes @tauri-apps/cli@2 @options
if ($LASTEXITCODE -ne 0) { throw "construction de l'enveloppe en échec" }
Pop-Location

# 6. Dans dist\windows : l'installateur, et pour une publication, latest.json (le flux des mises à jour).
New-Item -ItemType Directory -Force $Sortie | Out-Null
$nsis = Get-ChildItem (Join-Path $App "target\release\bundle\nsis") -Filter "*.exe" | Select-Object -First 1
$installateur = Join-Path $Sortie "Piston-$Version-installation.exe"
Copy-Item $nsis.FullName $installateur -Force
Write-Host "Installateur : $installateur"
$sig = "$($nsis.FullName).sig"
if (Test-Path $sig) {
  Copy-Item $sig "$installateur.sig" -Force
  $notes = ""
  $chg = Get-Content (Join-Path $Racine "CHANGELOG.md") -Encoding utf8
  $dedans = $false
  foreach ($l in $chg) {
    if ($l -match '^## ') { if ($dedans) { break }; if ($l.StartsWith("## $Version ")) { $dedans = $true; continue } }
    if ($dedans) { $notes += "$l`n" }
  }
  $flux = @{
    version = $Version
    notes = $notes.Trim()
    pub_date = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    platforms = @{ "windows-x86_64" = @{ signature = (Get-Content $sig -Raw).Trim(); url = "$AdresseMaj/Piston-$Version-installation.exe" } }
  }
  $flux | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $Sortie "latest.json") -Encoding utf8
  Write-Host "Flux des mises à jour : $(Join-Path $Sortie 'latest.json') (à déposer en dernier dans $AdresseMaj/)"
}
