# Piston — application Mac de recherche d'emploi

Une application Mac autoportante qui aide une personne, quelle qu'elle soit, à chercher un
emploi en Suisse : sourcing quotidien (Job-Room, jobup, jobs.ch, pages carrière, Indeed,
LinkedIn par Apify), registre local, pré-tri, note d'adéquation et dossiers de candidature
écrits avec l'IA de la personne (Claude, Codex, Antigravity ou Grok), suivi des relances.
Les preuves Job-Room sont hors de cette version (`profil.HORS_VERSION`) : le code reste, l'option
est éteinte. Les changements de chaque version sont dans `CHANGELOG.md`.

Tout tourne dans l'application. Elle embarque son Python, ses dépendances, son code et Lima ;
le moteur d'IA et Google Chrome tournent dans sa machine virtuelle, jamais sur le Mac. Ses données
vivent dans `~/Library/Application Support/Recherche emploi` (dossier réservé au compte). Rien
n'est installé à côté : pas d'agent launchd, pas de dossier de projet, pas de coffre externe.

## Télécharger

Piston se télécharge gratuitement sur [greyfold.ch](https://greyfold.ch).
<!-- Adresse exacte de la page de téléchargement à confirmer. -->

- **Mac** : Apple silicon (M1 ou plus récent) et macOS 14 ou plus.
- **IA** : un abonnement de la personne à l'un des moteurs proposés (Claude Pro ou Max, ChatGPT,
  Google AI Pro ou Ultra, SuperGrok). Aucune clé API, aucun compte chez Greyfold.
- **Premier lancement** : l'application n'est pas encore notarisée ; macOS la bloque une première
  fois. L'autoriser dans Réglages Système › Confidentialité et sécurité, puis la rouvrir.
- **Premier démarrage** : l'application crée sa machine virtuelle et y installe Ubuntu, Chrome,
  Node.js et les moteurs d'IA. Compter plusieurs minutes et quelques gigaoctets ; l'assistant
  d'accueil se remplit pendant ce temps.

## Architecture

Trois couches, chacune avec un rôle et des droits précis :

```
┌──────────────────────── Mac ─────────────────────────────────────────────┐
│  Piston.app (SwiftUI, app/)                                              │
│   fenêtre WebKit · barre des menus · dictée · minuteries                 │
│        │ lance                                                           │
│        ▼                                                                 │
│  Python embarqué : python -m jobsearch …                                 │
│   web        tableau de bord FastAPI, 127.0.0.1:8775, jeton exigé        │
│   echeances  toutes les 5 min : passage, alerte, rattrapage, lettres     │
│   vm …       crée, démarre, arrête la machine virtuelle (Lima)           │
│        │                                                                 │
│   données : jobsearch.db, profil.yaml, dossiers/, sauvegardes/, logs/    │
└────────┼─────────────────────────────────────────────────────────────────┘
         │ HTTP 127.0.0.1:8776 (jeton) · écran 127.0.0.1:8777 (mot de passe)
         │ un seul dossier partagé : partage-vm/  →  /donnees
┌────────▼──────── Machine virtuelle (Ubuntu, Virtualization.framework) ───┐
│  travailleur (jobsearch travailleur)                                     │
│   moteurs d'IA : Claude Code · Codex · Antigravity CLI · Grok Build      │
│   Google Chrome + chrome-devtools-mcp, derrière le filtre de mcp_vm.py   │
│   écran : TigerVNC, Openbox, noVNC                                       │
└──────────────────────────────────────────────────────────────────────────┘
```

### Les processus

L'enveloppe SwiftUI (`app/Sources/RechercheEmploi/Etat.swift`) ne contient aucune logique métier :
elle lance des processus Python embarqués (`python3 -m jobsearch <commande>`) avec les variables
`JOBSEARCH_HOME` (le code), `JOBSEARCH_DATA` (les données), `JOBSEARCH_PORT` et `JOBSEARCH_LIMACTL`.

| Quand | Commande | Rôle |
|---|---|---|
| Ouverture | `jobsearch vm demarrer` | Crée ou rallume la VM ; ses lignes « étape : … » nourrissent `/demarrage`. |
| Ouverture | `jobsearch web --hote 127.0.0.1 --port 8775` | Le tableau de bord, montré dans la fenêtre. |
| Toutes les 30 s | lecture de `/api/etat` | Barre des menus ; relève des événements des tâches Chrome. |
| Toutes les 5 min | `jobsearch echeances` | Ce qui est dû : passage, alerte, rattrapage de l'IA, surveillance. |
| Fermeture | `jobsearch vm arreter` | Éteint la VM avant de quitter. |

Un seul tableau de bord par port : au lancement, l'enveloppe vérifie qui écoute sur 8775 et refuse
celui d'une autre installation. Un seul passage à la fois : il tient un verrou `flock` sur
`passage.lock` ; un passage resté « démarré » sans verrou est clos « interrompu ».

### La frontière Mac / VM

Le **Mac** garde tout ce qui est à la personne et tout ce qui écrit : la base, le profil, les
dossiers de candidature, les sauvegardes, le trousseau (clé Apify), Apple Mail, le tableau de bord
et l'orchestration du passage. Une base SQLite ne se partage pas entre le Mac et la VM (les verrous
ne traversent pas le dossier partagé) : seul le Mac l'ouvre.

La **VM** exécute ce qui doit être isolé : les moteurs d'IA, Chrome et les sources qui passent par
eux (Indeed, pages carrière lues par l'IA). Elle ne voit que `partage-vm/` et répond au Mac par le
**travailleur** (`travailleur/serveur.py`), derrière un jeton partagé :

- `GET /etat` : version, empreinte du code installé, connexion de chaque moteur ;
- `POST /moteur` : une tâche du moteur ;
- `POST /sources/{nom}/tester|offres|detail` : une source confiée à la VM ;
- `POST /connecteurs/{nom}/tester`, courrier Gmail, connexion des moteurs ;
- `POST /chrome/rendre`, `/chrome/pdf` : rendu d'une page, impression d'un PDF ;
- `POST /preremplir` et les sessions Chrome de l'IA.

Côté Mac, `travailleur/client.py` donne à ces services la même interface que leur version locale :
`Distant` se comporte comme un moteur (`executer(tache, message)`), `SourceDistante` comme une
source (`tester`, `offres`, `detail`). L'évaluation, les dossiers et le passage ne savent pas où
tourne ce qu'ils appellent.

### Le chemin d'une tâche d'IA

1. Le module métier (`evaluation.py`, `dossier.py`, `parcours.py`…) construit une `Tache`
   (`moteur/__init__.py`) : consigne fixe, contexte, schéma JSON de la réponse, outils permis,
   modèle, effort, délai.
2. `moteur.ouvrir()` rend un moteur `Protege` : nom, adresse, téléphone, e-mail, date de naissance
   et numéro AVS deviennent des balises (`confidentialite.py`).
3. `Distant` envoie la tâche au travailleur (`POST /moteur`), qui lance la commande du fournisseur
   choisi sans tête (`moteur/fournisseurs.py`) ; la consigne passe par un fichier lisible du seul
   compte, la demande par l'entrée standard, pour que rien n'apparaisse dans la liste des processus.
4. La réponse revient au Mac, les balises reprennent leur valeur, Python valide la réponse contre
   son schéma et ses propres règles (une offre dont le lien n'est pas dans la page tombe, un accès
   « permis » sans conditions lues aussi), puis seulement écrit dans la base.

Le contexte (profil, parcours, méthode) est identique d'une annonce à l'autre : le moteur le garde
en cache pendant un passage.

### Le chemin d'une session Chrome

Une session (formulaire d'employeur, pré-remplissage sans IA) n'a ni la base ni le profil :

1. Le Mac prépare `partage-vm/taches/<id>/donnees.json` (valeurs du formulaire, chemins des
   documents) et copie le CV et la lettre dans `taches/<id>/documents/`.
2. Le travailleur lance la session dans le Chrome de la VM. Une session d'IA n'a que les deux
   serveurs MCP de l'application (`mcp_vm.py`) : un filtre devant chrome-devtools-mcp (ni exécution
   de script, ni réseau au-delà de la page) et les commandes de sa tâche (`session_vm.py`).
3. La session note ce qu'elle a fait dans `taches/<id>/evenements.jsonl`.
4. Le Mac relève ces lignes (`agent_claude.relever`, à chaque `/api/etat`), vérifie chacune et
   n'applique que ce qui est permis. Une page de confirmation vue par la session ne vaut jamais
   envoi : c'est la personne qui note l'envoi.

### Le passage du matin

`pipeline.py` enchaîne des étages indépendants ; chacun écrit sa ligne dans `run_etapes` dès
qu'il démarre, une panne est notée et les suivants tournent quand même :

`sauvegarde` → `test_routes` → `courrier` → `sourcing` → `details` → `ingestion` → `doublons` →
`pre_tri` → `originaux` → `evaluation` → `dossiers` → `echeances`

- **Sourcing** : chaque source a la même interface (`sources/base.py`) et `sources/catalogue.py`
  construit la liste du passage ; celles que les réglages confient à la VM sont remplacées par leur
  double distant. Familles : routes publiques des plateformes (`jobroom.py`, `jobup.py` pour jobup
  et jobs.ch), pages carrière servies par un logiciel connu (`logiciels.py` : Workday, Greenhouse,
  Lever, SmartRecruiters, Recruitee, Personio), page quelconque lue par l'IA (`page_ia.py`),
  Indeed par le connecteur du compte Claude, LinkedIn par Apify, alertes e-mail (`alertes.py`).
- **Ingestion** (`ingest.py`) : une annonce vue ne revient jamais. Rapprochement par l'identifiant
  de la plateforme, puis par l'empreinte employeur + intitulé (`fingerprint.py`) ; `doublons.py`
  fusionne ensuite les doublons sûrs écrits autrement d'une plateforme à l'autre.
- **Sorties** : `runs/<date>-<n>/` garde ce que chaque passage a lu (`sourcing.json`), `logs/latest`
  le journal du dernier passage.

### Les données

Tout vit dans `~/Library/Application Support/Recherche emploi` (`config.DATA`, ou
`JOBSEARCH_DATA`), en 0700, fichiers en 0600 :

| Chemin | Contenu |
|---|---|
| `jobsearch.db` | Le registre SQLite, seule source de vérité (voir plus bas). |
| `profil.yaml` | Le profil : identité, recherche, critères, dossier, objectifs, moteur choisi. |
| `dossiers/` | Les dossiers de candidature (analyse, CV, lettre, fiche d'envoi ; Markdown et Word). |
| `entretiens/` | Une préparation d'entretien par annonce (rendez-vous en YAML, puis Markdown). |
| `sauvegardes/` | Les copies de la base faites au début de chaque passage. |
| `runs/`, `logs/` | Ce que chaque passage a lu, et les journaux. |
| `jeton` | Le jeton du tableau de bord. |
| `lima/`, `vm/` | La machine virtuelle (`LIMA_HOME`) et l'état de son démarrage (`vm/demarrage.json`). |
| `partage-vm/` | Le seul dossier que voit la VM : jeton du travailleur, mot de passe de l'écran, journaux, `taches/<id>/`. |

La base (`db.py` et les modules qui la complètent) tient : `annonces` et `ids_source` (le
registre), `journal` (en ajout seul), `actions` (les clics de la personne, appliqués dans la même
transaction par `actions.py`), `runs` et `run_etapes` (les passages, battement de cœur de la
surveillance), `evaluations`, `dossiers`, `parcours*` (la base d'expérience), `repertoire` (les
sources ajoutées), `courriers`, `doublons_verdicts`, `taches_claude`, `ce_qui_compte`,
`postes_proposes`, `veilles`, `meta`. Seul le code de `jobsearch` l'écrit.

### Configuration et profil

`config/` (`settings.yaml`, `criteres.yaml`, `sources.yaml`) donne les valeurs par défaut, neutres
pour tout le monde. `profil.yaml` ne garde que ce qui en diffère ; `config.reglages()` et
`config.criteres()` rendent la superposition des deux. Aucune valeur personnelle n'est dans le
code, `config/` ou les gabarits (`tests/test_echeances.py` le vérifie).

### Le tableau de bord

FastAPI et Jinja (`web/app.py`), pages rendues par le serveur ; un peu de JavaScript sans
bibliothèque (`web/static/*.js`) pour le tri, l'entretien et les champs. L'interface suit le design
system Nexus traduit dans `web/static/style.css` et `web/templates/_macros.html` ; les polices sont
servies par l'application. L'état de la VM et des moteurs, coûteux à lire, est gardé en mémoire et
relu en arrière-plan (`web/etat_distant.py`) : aucune page n'attend la VM.

## Construire et lancer

```bash
scripts/construire-app.sh --ouvrir
```

Le script construit `dist/Piston.app` :

- l'enveloppe SwiftUI (`app/`) : une fenêtre sur le tableau de bord, une icône dans la barre
  des menus (état du passage, annonces à trancher, connexion à l'IA, machine virtuelle) ;
- un Python autonome (python-build-standalone, installé par uv) dans `Contents/Resources/python`,
  avec les dépendances aux versions de `uv.lock` ;
- le code dans `Contents/Resources/racine` (`src/`, `config/`, `pyproject.toml`), trouvé par un
  `.pth` relatif : l'application peut être déplacée ;
- Lima 2.3.0 (`limactl` et l'agent de la VM seulement), somme de contrôle vérifiée, signé avec
  le droit `com.apple.security.virtualization`.

Signature locale seulement, pas encore de notarisation. Rien à installer sur le Mac.

## Désinstaller

Mettre `Piston.app` à la corbeille ne retire que l'application. Quitter Piston d'abord (elle éteint
sa machine virtuelle), puis, selon ce qu'on veut effacer :

```bash
rm -rf ~/Library/Application\ Support/Recherche\ emploi/lima   # la VM seule (plusieurs Go), recréée au lancement
rm -rf ~/Library/Caches/lima                                    # l'image Ubuntu en cache (si Lima ne sert à rien d'autre)
rm -rf ~/Library/Application\ Support/Recherche\ emploi        # tout, données comprises
```

## La machine virtuelle : le moteur d'IA et Chrome

`vm.py` crée la VM (Ubuntu, Virtualization.framework d'Apple) au premier démarrage, dans
`LIMA_HOME` = dossier des données, à part de tout autre Lima. Elle installe depuis leurs sources
officielles Python, uv, Claude Code, Google Chrome (arm64), Node.js 22 (somme vérifiée) et le
serveur MCP de Chrome (`chrome-devtools-mcp`, version figée), puis à chaque démarrage, s'ils
manquent, Codex, Antigravity CLI et Grok Build (versions figées, `moteur/fournisseurs.py`), plus un écran : TigerVNC, Openbox,
noVNC. Le **travailleur** (`travailleur/`) y répond sur 127.0.0.1:8776 (jeton) ; l'écran sur
127.0.0.1:8777 (mot de passe), montré par la page **Navigateur** de l'application.

La VM ne tourne que pendant que l'application est ouverte. Quitter l'éteint d'abord (`vm arreter`,
écran « Fermeture de l'application » ; un passage en cours est interrompu et reprend au lancement
suivant). À l'ouverture, `vm demarrer` la rallume ; dès sa première étape (lignes « étape : … »), la
fenêtre montre la page `/demarrage` : les étapes, une barre d'avancement et le temps qui reste
(`vm.avancement`, d'après `vm/demarrage.json` et la durée de chaque étape la dernière fois). La toute
première fois, cette page est aussi l'écran de bienvenue, avant l'assistant. « Continuer sans
attendre » ouvre l'application : la VM finit de démarrer en arrière-plan, un bandeau des pages suit
l'avancement, les échéances attendent la fin, et ce qui demande l'IA (tâches, connexion) répond que
la machine démarre encore (`vm.en_demarrage`, verrou de `vm demarrer`) au lieu d'échouer. Le code n'est réinstallé que s'il a changé : son empreinte (`vm.empreinte_code`) est
posée dans la VM à la fin de l'installation (`vm.code_installe`) et rendue par le travailleur dans
`/etat` ; le passage vérifie aussi avant de confier une source (`vm.assurer_a_jour`).

- **Ce que la VM voit** : le seul sous-dossier `partage-vm/` des données (jeton, mot de passe de
  l'écran, journaux du travailleur, et pour chaque tâche Chrome `taches/<id>/` : ce que le Mac lui
  confie). Ni la base, ni le profil, ni les sauvegardes, ni les dossiers de candidature.
- **L'IA** : toutes les tâches du moteur (masquées par `Protege` avant de partir), Indeed, Gmail.
- **Chrome** : les sessions de l'IA (formulaires, `agent_claude.py`) et le
  pré-remplissage sans IA (`navigateur.py`, `preremplir_vm.py`) utilisent le Chrome de la VM
  et son profil : la personne s'y connecte une fois aux sites, dans **Navigateur**. Quel que soit
  le moteur, une session n'a que les deux serveurs MCP de l'application (`mcp_vm.py`) : un filtre
  devant celui de Chrome (sans exécution de script ni réseau) et les commandes de la tâche ; ni
  terminal, ni fichiers. Elle lit ce que le Mac a préparé et note ses événements ; le Mac les relève (`agent_claude.relever`, à chaque `/api/etat`), les vérifie et les applique.
- Le rendu des pages en JavaScript et l'impression des PDF passent aussi par ce Chrome
  (`POST /chrome/rendre`, `/chrome/pdf`), dans un contexte isolé sans les cookies du profil.

« Se connecter » (page IA) lance la connexion du moteur dans la VM (`claude auth login`,
`codex login --device-auth`, Antigravity, `grok login --device-code`) : les identifiants y restent.
Codex, Antigravity et Grok ont chacun leur dossier personnel dans la VM (`~/moteurs/<id>`).

## Ce que fait l'application

- **Accueil** : un assistant pour une personne qui part de zéro (identité, recherche, pré-tri,
  critères, dossier, objectifs). Rien de personnel n'est livré : `config/` est neutre, tout vient
  du profil (`profil.yaml` dans le dossier de données), qui prime sur `config/`.
- **Ton parcours** : import du CV, puis un entretien avec l'IA poste après poste ; la base
  d'expérience qui en sort est ce que l'IA reçoit pour noter les annonces et écrire les dossiers.
- **Passage du matin** : sauvegarde, test des routes, sourcing, lecture des annonces nouvelles,
  ingestion sans doublon, fusion des doublons, pré-tri, note et pré-sélection par l'IA, échéances.
  Une étape en panne n'arrête pas les suivantes.
- **Pré-tri en trois passes** : les passes 0 et 1 sont des règles (`prefilter.py`, `config/criteres.yaml`,
  postes et exclusions du profil) ; l'intitulé doit porter un mot du métier des postes visés, pas
  seulement un mot de fonction (« chef », « responsable », « manager »). La passe 2 est l'IA
  (`evaluation.py`) : en notant chaque annonce à trancher, elle dit si le métier est dans, à la limite
  ou hors des postes visés ; « hors » écarte l'annonce en « limite », visible et réversible dans
  « Écartées limites ». Le doute profite à l'annonce.
- **Oui, peut-être, non** (`evaluation.avis`) : l'avis sur une annonce se calcule sur la moyenne de la
  note globale et de l'adéquation du profil : oui dès 50 % ; peut-être un peu en dessous, ou quand les
  deux chiffres divergent (le profil couvre, la note est basse, ou l'inverse) ; non sinon. L'IA donne les
  raisons pour, contre, et ce qui doit faire trancher. Une annonce nouvelle dont l'avis est non n'est plus
  proposée : elle reste dans Offres (filtre « Non »), d'où elle se remet à trancher d'un clic. Les
  statuts A, B, C se lisent oui, peut-être, non ; les boutons du tri sont Oui, Peut-être, Non. Si l'IA n'a pas répondu pendant le passage, le
  bilan le dit et `echeances.py` rattrape les annonces sans note dès qu'elle répond.
- **Adéquation par catégorie** (`adequation.py`) : dans la même tâche que la note, l'IA analyse
  l'adéquation au poste en compétences, expérience, formation et langues : une note sur 100 par
  catégorie, les atouts du profil et les écarts, chaque exigence rangée dans sa catégorie. Une
  catégorie où l'annonce n'exige rien n'a pas de note. La page de l'annonce le montre ; le dossier
  de candidature le reçoit avec l'évaluation.
- **Examen d'une source** (page Sources, ou `jobsearch source examiner <id>`) : l'IA lit sur le web
  les conditions générales et le robots.txt de la source, et cherche une API publique, un serveur MCP
  ou des alertes e-mail. Elle conseille un accès, que Python vérifie (`sources/examen.py`) : sans
  conditions lues, rien n'est « permis ». L'examen ne branche rien ; la personne décide.
- **Planification** : l'application appelle `jobsearch echeances` toutes les cinq minutes
  (`src/jobsearch/echeances.py`) : le passage part une fois par jour dès l'heure du réglage
  `passage.heure` (Mac en veille ou application fermée : au réveil ou à l'ouverture), l'alerte du
  matin une fois le passage terminé, la surveillance au plus une fois par heure. « Ouvrir à la
  connexion » (barre des menus) lance l'application avec la session.
- **Dictée** des entretiens : reconnaissance vocale d'Apple sur l'appareil ; la voix ne quitte
  pas le Mac.

## Le moteur IA : Claude, Codex, Antigravity ou Grok

Tout ce qui demande l'IA passe par la commande de code du fournisseur choisi, dans la VM,
connectée au compte de la personne (son abonnement, aucune clé API) : Claude Code (Claude Pro ou
Max), Codex (ChatGPT), Antigravity CLI (Google AI Pro ou Ultra) ou Grok Build (SuperGrok) ;
`src/jobsearch/moteur/`. Un seul est connecté à la fois ; le dernier connecté devient celui de
l'application (profil : `ia.fournisseur`). Gmail et Indeed sont des connecteurs du compte Claude :
ils ne servent qu'avec Claude. Indeed est une source : il s'active et se teste sur sa page de
source (`/sources/indeed`), pas sur la page Connecteurs. LinkedIn passe par l'API Apify : le jeton
(trousseau du Mac), l'activation et le plafond par recherche se règlent sur `/sources/linkedin`,
qui lit le compte Apify (offre, crédit du mois, coût des dernières recherches) pour proposer ce
plafond (`linkedin.compte`, `linkedin.conseil`). Une `Tache` fixe la consigne,
le contexte, le schéma de la réponse, les outils permis et le délai ; Python valide la réponse
avant d'écrire quoi que ce soit.

**Confidentialité** : nom, adresse, téléphone, e-mail, date de naissance, numéro AVS sont
remplacés par des balises avant chaque tâche (`confidentialite.py`) et rétablis sur le Mac.

Exception, que la personne déclenche : pour remplir un formulaire, la
session de l'IA reçoit les vraies coordonnées (elle doit les taper). Le pré-remplissage sans IA
les saisit sans l'IA.

**Sécurité des données** : dossier des données en 0700 et fichiers créés en 0600 (`umask` posé à
l'import du paquet) ; tableau de bord sur 127.0.0.1, qui exige son jeton pour toute
requête, même locale ; l'application le pose elle-même dans sa fenêtre (`web/auth.py`) ;
l'écran de la VM ne s'ouvre que depuis le Mac. Une clé (Apify) ne vit que dans le trousseau, jamais
dans un fichier, un journal ou une ligne de commande (`cles.py`).

## Ce que l'application ne fait pas

Elle n'envoie rien, ne soumet aucun formulaire, n'utilise aucun compte LinkedIn personnel et ne
supprime rien. Les notes d'adéquation et les dossiers sont des aides : la personne tranche, relit
et envoie.

## Développer

```bash
uv sync
uv run pytest                    # -m live : les vrais sites ; -m navigateur : Chrome
uv run jobsearch web             # tableau de bord sur http://localhost:8775, données dans data/
uv run jobsearch jeton           # le jeton à coller une fois sur /connexion
```

Dans le dépôt, les données vont dans `data/` (hors git). Les tests tournent sur une installation
d'exemple (`tests/fixtures/config`, valeurs fictives), jamais sur `config/` ni sur `data/`.

| Où | Quoi |
|---|---|
| `app/` | L'enveloppe SwiftUI (`Etat.swift` : Python embarqué, tableau de bord, échéances). |
| `scripts/construire-app.sh` | Construit l'application autoportante. |
| `config/` | Valeurs par défaut, neutres (vérifié par `tests/test_echeances.py`). |
| `src/jobsearch/echeances.py` | Ce qui est dû maintenant : passage, alerte, surveillance, date des lettres. |
| `src/jobsearch/cli.py` | La commande `jobsearch` : tout ce que l'enveloppe et le développement lancent. |
| `src/jobsearch/pipeline.py` | Le passage du matin, étage par étage. |
| `src/jobsearch/db.py`, `actions.py` | Le registre SQLite et les décisions de la personne. |
| `src/jobsearch/ingest.py`, `fingerprint.py`, `doublons.py` | Ingestion sans doublon, empreinte, fusion des doublons. |
| `src/jobsearch/prefilter.py` | Les passes 0 et 1 du pré-tri (règles de `config/criteres.yaml`). |
| `src/jobsearch/confidentialite.py` | Le masque des coordonnées avant chaque tâche d'IA. |
| `src/jobsearch/dossier.py`, `lettre.py`, `modeles/` | Le dossier de candidature, la date de la lettre, les modèles Word et la méthode. |
| `src/jobsearch/courrier.py`, `gmail.py` | Les réponses des employeurs (Apple Mail, Gmail), en lecture seule. |
| `src/jobsearch/followups.py`, `matin.py` | Relances, rythme, alerte du matin. |
| `src/jobsearch/moteur/` | Le moteur IA : Claude Code, Codex, Antigravity CLI, Grok Build (`fournisseurs.py`). |
| `src/jobsearch/mcp_vm.py` | Les deux serveurs MCP des sessions Chrome, dans la VM. |
| `src/jobsearch/sources/` | Les sources ; `catalogue.py` construit la liste du passage. |
| `src/jobsearch/evaluation.py`, `adequation.py` | La note d'adéquation et son analyse par catégorie. |
| `src/jobsearch/profil.py` | Le profil, qui prime sur `config/`. |
| `src/jobsearch/parcours.py` | La base d'expérience et ses entretiens. |
| `src/jobsearch/vm.py`, `travailleur/` | La machine virtuelle (Lima embarqué), son écran et son travailleur. |
| `src/jobsearch/agent_claude.py`, `session_vm.py`, `preremplir_vm.py` | Les tâches dans le Chrome de la VM et leur relève. |
| `src/jobsearch/web/` | Le tableau de bord (FastAPI, Jinja). |
