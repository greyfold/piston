# Recherche d'emploi — application Mac

Une application Mac autoportante qui aide une personne, quelle qu'elle soit, à chercher un
emploi en Suisse : sourcing quotidien (Job-Room, jobup, jobs.ch, pages carrière, Indeed,
LinkedIn par Apify), registre local, pré-tri, note d'adéquation et dossiers de candidature
écrits avec Claude, suivi des relances et des preuves Job-Room.

Tout tourne dans l'application. Elle embarque son Python, ses dépendances, son code et Lima ;
Claude Code et Google Chrome tournent dans sa machine virtuelle, jamais sur le Mac. Ses données
vivent dans `~/Library/Application Support/Recherche emploi` (dossier réservé au compte). Rien
n'est installé à côté : pas d'agent launchd, pas de dossier de projet, pas de coffre externe.

## Construire et lancer

```bash
scripts/construire-app.sh --ouvrir
```

Le script construit `dist/Recherche emploi.app` :

- l'enveloppe SwiftUI (`app/`) : une fenêtre sur le tableau de bord, une icône dans la barre
  des menus (état du passage, annonces à trancher, connexion à Claude, machine virtuelle) ;
- un Python autonome (python-build-standalone, installé par uv) dans `Contents/Resources/python`,
  avec les dépendances aux versions de `uv.lock` ;
- le code dans `Contents/Resources/racine` (`src/`, `config/`, `pyproject.toml`), trouvé par un
  `.pth` relatif : l'application peut être déplacée ;
- Lima 2.3.0 (`limactl` et l'agent de la VM seulement), somme de contrôle vérifiée, signé avec
  le droit `com.apple.security.virtualization`.

Signature locale seulement, pas encore de notarisation. Rien à installer sur le Mac.

## La machine virtuelle : Claude Code et Chrome

`vm.py` crée la VM (Ubuntu, Virtualization.framework d'Apple) au premier démarrage, dans
`LIMA_HOME` = dossier des données, à part de tout autre Lima. Elle installe depuis leurs sources
officielles Python, uv, Claude Code, Google Chrome (arm64), Node.js 22 (somme vérifiée) et le
serveur MCP de Chrome (`chrome-devtools-mcp`, version figée), plus un écran : TigerVNC, Openbox,
noVNC. Le **travailleur** (`travailleur/`) y répond sur 127.0.0.1:8766 (jeton) ; l'écran sur
127.0.0.1:8767 (mot de passe), montré par la page **Navigateur** de l'application.

- **Ce que la VM voit** : le seul sous-dossier `partage-vm/` des données (jeton, mot de passe de
  l'écran, journaux du travailleur, et pour chaque tâche Chrome `taches/<id>/` : ce que le Mac lui
  confie). Ni la base, ni le profil, ni les sauvegardes, ni les dossiers de candidature.
- **L'IA** : toutes les tâches du moteur (masquées par `Protege` avant de partir), Indeed, Gmail.
- **Chrome** : les sessions Claude (preuves Job-Room, formulaires, `agent_claude.py`) et le
  pré-remplissage sans Claude (`navigateur.py`, `preremplir_vm.py`) utilisent le Chrome de la VM
  et son profil : la personne s'y connecte une fois aux sites, dans **Navigateur**. Une session n'a
  que les outils du serveur MCP de Chrome (sans exécution de script ni réseau) et la commande
  `jobsearch-session` (`session_vm.py`) : elle lit ce que le Mac a préparé et note ses événements ;
  le Mac les relève (`agent_claude.relever`, à chaque `/api/etat`), les vérifie et les applique.
- Le rendu des pages en JavaScript et l'impression des PDF passent aussi par ce Chrome
  (`POST /chrome/rendre`, `/chrome/pdf`), dans un contexte isolé sans les cookies du profil.

« Se connecter à Claude… » lance `claude auth login` dans la VM : les identifiants y restent.

## Ce que fait l'application

- **Accueil** : un assistant pour une personne qui part de zéro (identité, recherche, pré-tri,
  critères, dossier, objectifs). Rien de personnel n'est livré : `config/` est neutre, tout vient
  du profil (`profil.yaml` dans le dossier de données), qui prime sur `config/`.
- **Ton parcours** : import du CV, puis un entretien avec l'IA poste après poste ; la base
  d'expérience qui en sort est ce que l'IA reçoit pour noter les annonces et écrire les dossiers.
- **Passage du matin** : sauvegarde, test des routes, sourcing, lecture des annonces nouvelles,
  ingestion sans doublon, fusion des doublons, pré-tri, échéances. Une étape en panne n'arrête pas
  les suivantes.
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

## Claude : le moteur IA

Tout ce qui demande Claude passe par Claude Code, dans la VM, connecté au compte Claude de la
personne (son abonnement, aucune clé API) : `src/jobsearch/moteur/`. Une `Tache` fixe la consigne,
le contexte, le schéma de la réponse, les outils permis et le délai ; Python valide la réponse
avant d'écrire quoi que ce soit.

**Confidentialité** : nom, adresse, téléphone, e-mail, date de naissance, numéro AVS sont
remplacés par des balises avant chaque tâche (`confidentialite.py`) et rétablis sur le Mac.

Exception, que la personne déclenche : pour remplir un formulaire ou une preuve Job-Room, la
session Claude reçoit les vraies coordonnées (elle doit les taper). Le pré-remplissage sans Claude
les saisit sans l'IA.

**Sécurité des données** : dossier des données en 0700 et fichiers créés en 0600 (`umask` posé à
l'import du paquet) ; tableau de bord sur 127.0.0.1, jeton exigé dès que la connexion ou le nom
d'hôte demandé ne sont pas locaux (protection contre le « DNS rebinding », `web/auth.py`) ;
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
| `src/jobsearch/moteur/` | Le moteur IA (Claude Code). |
| `src/jobsearch/sources/` | Les sources ; `catalogue.py` construit la liste du passage. |
| `src/jobsearch/profil.py` | Le profil, qui prime sur `config/`. |
| `src/jobsearch/parcours.py` | La base d'expérience et ses entretiens. |
| `src/jobsearch/vm.py`, `travailleur/` | La machine virtuelle (Lima embarqué), son écran et son travailleur. |
| `src/jobsearch/agent_claude.py`, `session_vm.py`, `preremplir_vm.py` | Les tâches dans le Chrome de la VM et leur relève. |
| `src/jobsearch/web/` | Le tableau de bord (FastAPI, Jinja). |
