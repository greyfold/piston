# Piston pour Windows

La même application que sur Mac, sur Windows 11 (x64 d'abord, ARM64 ensuite). Tout ce qui est commun vit
à la racine du dépôt (`src/jobsearch`) ; ici, seulement ce qui est propre à Windows :

| Où | Quoi |
|---|---|
| `app/` | L'enveloppe (Tauri v2, Rust) : fenêtre WebView2, icône de la zone de notification, dictée (whisper.cpp), mises à jour, ouverture à la connexion. |
| `construire.ps1` | Construit l'installateur `dist\windows\Piston-<version>-installation.exe`. |
| `RECETTE.md` | La liste de parité avec le Mac, à dérouler sur un Windows neuf. |
| `src/jobsearch/hote/windows.py` | (commun au dépôt) Gestionnaire d'identification, notifications, ACL du dossier, verrous. |
| `src/jobsearch/vm/wsl.py` | (commun au dépôt) La machine virtuelle : une distribution WSL2 dédiée, durcie. |

## Ce qui change par rapport au Mac

- **Machine virtuelle** : une distribution WSL2 « Piston-<nom> », importée d'une image Ubuntu de Canonical
  (somme vérifiée) dans `%LOCALAPPDATA%\Recherche emploi\wsl\`. WSL isole moins que Virtualization.framework :
  root y pourrait monter les disques de Windows. D'où le durcissement de `vm/wsl.py` : ni montage automatique,
  ni interopérabilité (aucun `.exe` lancé depuis la VM), compte `piston` sans sudo pour le travailleur, les
  moteurs et Chrome (ce qui demande root vient de Windows, `wsl -u root`), seul `partage-vm` monté, Chrome et
  l'écran dans un espace de noms réseau à part (WSL relaierait sinon DevTools et VNC vers Windows). Le risque
  qui reste : une faille du noyau Linux donnerait accès au compte Windows.
- **WSL doit être activé une fois**, avec les droits d'administrateur : `wsl --install --no-distribution`,
  puis redémarrer. L'application le dit si ce n'est pas fait.
- **Processeurs et mémoire** de la VM : WSL les règle pour toutes les distributions à la fois
  (`%USERPROFILE%\.wslconfig`), que l'application ne touche pas ; `vm.cpus` et `vm.memoire` n'ont pas d'effet.
- **Dictée** : whisper.cpp sur le PC (modèle « small » multilingue, 190 Mo, téléchargé à la première dictée,
  empreinte vérifiée). La reconnaissance de Windows enverrait la voix à Microsoft.
- **Courrier** : Outlook (classique) en lecture seule à la place d'Apple Mail ; Gmail par le connecteur Claude.
- **Coffre à clés** : le gestionnaire d'identification de Windows (jeton Apify).
- **Notifications** : toasts Windows ; un clic ramène l'application (`piston://ouvrir`).

## Construire

Sur Windows, avec uv, Rust (MSVC), Node.js, CMake et LLVM :

```powershell
plateformes\windows\construire.ps1
```

Sans Windows : chaque envoi sur GitHub construit l'installateur (`.github/workflows/windows.yml`, artefact
`piston-windows` de l'exécution). Les tests du code commun tournent aussi sous Windows (`tests.yml`) et l'invité
de la VM est éprouvé sur Ubuntu amd64 et arm64 (`invite.yml`).

## Essayer l'enveloppe sans Windows

L'enveloppe tourne aussi sur Mac (sans la dictée, qui demande CMake), sur un dossier de données d'essai :

```bash
cd plateformes/windows/app
mkdir -p ressources/python ressources/racine
npx --yes @tauri-apps/cli@2 icon ../../../ressources/icone.png -o icons
cargo build --no-default-features
PISTON_PYTHON=$PWD/../../../.venv/bin/python PISTON_RACINE=<copie de la racine, config/ en mode local> \
  PISTON_DONNEES=/tmp/piston-essai PISTON_PORT=8791 ./target/debug/piston
```

## Essayer dans une VM Windows sur Mac

Une VM Windows 11 ARM (Parallels, VMware Fusion, UTM) suffit pour l'enveloppe, le superviseur, le coffre, les
notifications, la dictée et l'installateur. **WSL2 dans cette VM demande la virtualisation imbriquée** : puce
M3 ou plus récente, macOS 15 ou plus, et un hyperviseur qui la transmet à Windows. Sans elle, WSL2 ne démarre
pas : la machine virtuelle de Piston se teste alors sur un vrai PC ou une VM Windows dans le cloud avec
virtualisation imbriquée. La VM ARM teste la version arm64 ; la version x64 demande un PC x64.

## Publier

Une fois pour toutes :
- la clé des mises à jour : `npx @tauri-apps/cli signer generate -w piston-maj.key` ; la clé publique va dans
  la variable `PISTON_CLE_MAJ` (dépôt GitHub › Settings › Variables, et pour une construction locale) ; la
  clé privée reste hors du dépôt (`TAURI_SIGNING_PRIVATE_KEY`). La perdre, c'est ne plus pouvoir mettre à jour
  les installations existantes : à sauvegarder ;
- un certificat Authenticode (Azure Trusted Signing, ou un certificat OV) et sa commande dans
  `PISTON_SIGNATURE` : sans signature, SmartScreen avertit à l'installation.

Puis `plateformes\windows\construire.ps1 -Publier`, et déposer dans `https://greyfold.ch/downloads/piston/windows/`
l'installateur, puis `latest.json` en dernier (c'est lui qui annonce la version aux installations).

## Désinstaller

Quitter Piston (la VM s'éteint), puis Paramètres › Applications › Piston › Désinstaller. Les données restent dans
`%LOCALAPPDATA%\Recherche emploi` ; pour tout effacer, données et VM comprises :

```powershell
wsl --unregister Piston-emploi
Remove-Item -Recurse "$env:LOCALAPPDATA\Recherche emploi"
```
