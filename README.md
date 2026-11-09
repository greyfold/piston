# Recherche d'emploi — application Mac

Une application Mac autoportante qui aide une personne, quelle qu'elle soit, à chercher un
emploi en Suisse : sourcing quotidien (Job-Room, jobup, jobs.ch, pages carrière, Indeed,
LinkedIn par Apify), registre local, pré-tri, note d'adéquation et dossiers de candidature
écrits avec Claude, suivi des relances et des preuves Job-Room.

Tout tourne dans l'application. Elle embarque son Python, ses dépendances et son code ; ses
données vivent dans `~/Library/Application Support/Recherche emploi`. Rien n'est installé à
côté : pas d'agent launchd, pas de dossier de projet, pas de coffre externe.

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
  `.pth` relatif : l'application peut être déplacée.

Signature locale seulement, pas encore de notarisation.

Prérequis du Mac, hors de l'application : **Claude Code** (`claude`), que l'application connecte
au compte Claude de la personne (« Se connecter à Claude… », sans terminal), et, pour la machine
virtuelle facultative, **Lima**.

## Ce que fait l'application

- **Accueil** : un assistant pour une personne qui part de zéro (identité, recherche, pré-tri,
  critères, dossier, objectifs). Rien de personnel n'est livré : `config/` est neutre, tout vient
  du profil (`profil.yaml` dans le dossier de données), qui prime sur `config/`.
- **Ton parcours** : import du CV, puis un entretien avec l'IA poste après poste ; la base
  d'expérience qui en sort est ce que l'IA reçoit pour noter les annonces et écrire les dossiers.
- **Passage du matin** : sauvegarde, test des routes, sourcing, lecture des annonces nouvelles,
  ingestion sans doublon, fusion des doublons, pré-tri, échéances. Une étape en panne n'arrête pas
  les suivantes.
- **Planification** : l'application appelle `jobsearch echeances` toutes les cinq minutes
  (`src/jobsearch/echeances.py`) : le passage part une fois par jour dès l'heure du réglage
  `passage.heure` (Mac en veille ou application fermée : au réveil ou à l'ouverture), l'alerte du
  matin une fois le passage terminé, la surveillance au plus une fois par heure. « Ouvrir à la
  connexion » (barre des menus) lance l'application avec la session.
- **Dictée** des entretiens : reconnaissance vocale d'Apple sur l'appareil ; la voix ne quitte
  pas le Mac.

## Claude : le moteur IA

Tout ce qui demande Claude passe par Claude Code, connecté au compte Claude de la personne (son
abonnement, aucune clé API) : `src/jobsearch/moteur/`. Une `Tache` fixe la consigne, le contexte,
le schéma de la réponse, les outils permis et le délai ; Python valide la réponse avant d'écrire
quoi que ce soit. L'application a sa propre connexion (`CLAUDE_CONFIG_DIR` dans son dossier).

**Confidentialité** : nom, adresse, téléphone, e-mail, date de naissance, numéro AVS sont
remplacés par des balises avant chaque tâche (`confidentialite.py`) et rétablis sur le Mac.

Les sessions Claude dans Chrome (preuves Job-Room, formulaires) lancent `jobsearch` par le
Python embarqué (`agent_claude.commande_jobsearch`).

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
| `src/jobsearch/vm.py`, `travailleur/` | La machine virtuelle (Lima) facultative et son travailleur. |
| `src/jobsearch/web/` | Le tableau de bord (FastAPI, Jinja). |
