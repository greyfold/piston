# Piston — application Mac générique de recherche d'emploi

Lire `README.md` pour l'architecture. Écrire en français.

## Règles

- L'application est générique, pour n'importe quelle personne : aucun nom, aucune ville, aucun
  employeur, aucun critère personnel dans le code, `config/` ou les gabarits. Tout ce qui est
  propre à la personne vient du profil (`profil.py`). `tests/test_echeances.py` le vérifie.
- Tout tourne dans l'application : pas d'agent launchd, pas de dépendance à un dossier extérieur.
  Une tâche planifiée passe par `echeances.py`, lancé par l'application.
- Les moteurs d'IA (Claude Code, Codex, Antigravity CLI, Grok Build) et Chrome ne tournent que dans la
  machine virtuelle. Le Mac ne lance jamais `claude`, `codex`, `agy`, `grok` ni Chrome ; la VM
  ne voit que `partage-vm/`. Une tâche Chrome reçoit ce qu'elle doit savoir dans
  `taches/<id>/` et ne rend que des événements, que le Mac vérifie avant de toucher la base.
- Rien n'est envoyé, aucun formulaire n'est soumis, aucun compte LinkedIn personnel n'est utilisé.
- Aucun montant de salaire plancher nulle part : ni code, ni configuration, ni journaux, ni ce qui
  part vers l'IA.
- La base (`jobsearch.db` du dossier de données) ne se modifie que par le code de `jobsearch`.
- Les tests ne lancent jamais de moteur d'IA, ne lisent pas le trousseau, n'écrivent pas dans `data/`
  (`tests/conftest.py`).
- Interface : design system Nexus obligatoire, tel que traduit dans `web/static/style.css` et
  `web/templates/_macros.html`. Avant de toucher un gabarit, le CSS ou le JS de l'interface, lire et
  suivre à la lettre `.claude/rules/design-system.md` : réutiliser un composant existant, jamais de
  couleur en dur, de `style="…"`, de bibliothèque d'interface ni d'icône hors `icone()`.
- Après une modification : `uv run pytest`, puis `scripts/construire-app.sh --ouvrir` pour voir
  le changement dans l'application.
