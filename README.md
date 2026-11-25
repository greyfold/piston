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
suivant). À l'ouverture, `vm demarrer` la rallume pendant un écran d'attente qui montre ses étapes
(lignes « étape : … ») ; le tableau de bord s'ouvre ensuite, ou tout de suite avec « Continuer sans
attendre ». Le code n'est réinstallé que s'il a changé : son empreinte (`vm.empreinte_code`) est
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
l'import du paquet) ; tableau de bord sur 127.0.0.1, qui exige désormais son jeton pour toute
requête, même locale ; l'application le pose elle-même dans sa fenêtre (`web/auth.py`) ;
l'écran de la VM ne s'ouvre que depuis le Mac.

## Ce que l'application ne fait pas

Elle n'envoie rien, ne soumet aucun formulaire, n'utilise aucun compte LinkedIn personnel et ne
supprime rien. Les notes d'adéquation et les dossiers sont des aides : la personne tranche, relit
et envoie.

## Développer

```bash
uv sync
uv run pytest                    # -m live : les vrais sites ; -m navigateur : Chrome
uv run jobsearch web             # tableau de bord sur http://localhost:8775, données dans data/
```

Dans le dépôt, les données vont dans `data/` (hors git). Les tests tournent sur une installation
d'exemple (`tests/fixtures/config`, valeurs fictives), jamais sur `config/` ni sur `data/`.

| Où | Quoi |
|---|---|
| `app/` | L'enveloppe SwiftUI (`Etat.swift` : Python embarqué, tableau de bord, échéances). |
| `scripts/construire-app.sh` | Construit l'application autoportante. |
| `config/` | Valeurs par défaut, neutres (vérifié par `tests/test_echeances.py`). |
| `src/jobsearch/echeances.py` | Ce qui est dû maintenant : passage, alerte, surveillance. |
| `src/jobsearch/moteur/` | Le moteur IA : Claude Code, Codex, Antigravity CLI, Grok Build (`fournisseurs.py`). |
| `src/jobsearch/mcp_vm.py` | Les deux serveurs MCP des sessions Chrome, dans la VM. |
| `src/jobsearch/sources/` | Les sources ; `catalogue.py` construit la liste du passage. |
| `src/jobsearch/evaluation.py`, `adequation.py` | La note d'adéquation et son analyse par catégorie. |
| `src/jobsearch/profil.py` | Le profil, qui prime sur `config/`. |
| `src/jobsearch/parcours.py` | La base d'expérience et ses entretiens. |
| `src/jobsearch/vm.py`, `travailleur/` | La machine virtuelle (Lima embarqué), son écran et son travailleur. |
| `src/jobsearch/agent_claude.py`, `session_vm.py`, `preremplir_vm.py` | Les tâches dans le Chrome de la VM et leur relève. |
| `src/jobsearch/web/` | Le tableau de bord (FastAPI, Jinja). |
