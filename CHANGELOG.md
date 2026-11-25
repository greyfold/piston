# Journal des modifications

Les versions suivent [SemVer](https://semver.org/lang/fr/) : tant que la version commence par 0,
l'application est en essai et peut changer d'une version à l'autre.

## 0.3.0 — 2026-11-25 (version d'essai)

Première version construite pour être essayée hors du poste de développement.

### Ajouté

- **Plusieurs moteurs d'IA** : Codex, Antigravity CLI et Grok Build en plus de Claude Code, tous
  dans la machine virtuelle et connectés à l'abonnement de la personne. Un seul à la fois ; la
  déconnexion se fait depuis la page IA.
- **Écran d'attente au démarrage** de la machine virtuelle, avec ses étapes, et « Continuer sans
  attendre ». À la fermeture, la VM s'éteint d'abord ; un passage en cours reprend au lancement
  suivant.
- **Adéquation par catégorie** : compétences, expérience, formation et langues, chacune notée sur
  100, avec les atouts et les écarts.
- **Oui, peut-être, non** : l'avis de l'IA sur chaque annonce, avec les raisons pour, contre, et ce
  qui doit faire trancher. Tri en mode concentré des annonces à trancher, affiché à l'ouverture.
- **Pré-tri en trois passes** : deux de règles, puis l'IA dit si le métier est dans, à la limite ou
  hors des postes visés. Les écartées limites restent visibles et réversibles.
- **Recherche déduite du profil** : postes recherchés, cantons et niveau visé suffisent ; les
  requêtes de chaque source s'en déduisent. Postes proposés par l'IA d'après le parcours.
- **Examen d'une source par l'IA** : conditions générales, robots.txt, API publique, serveur MCP ;
  l'accès conseillé est vérifié par Python et rien ne se branche sans la personne.
- **Annonce en onglets** (Adéquation, Dossier, Sources, Journal, Entretien) et « Postuler sur le
  site », qui ouvre le formulaire officiel et prépare les fichiers à joindre.
- **Ce qui compte pour toi** en questionnaire, une question à la fois ; profil avec formation et
  langues ; export et import du parcours en JSON.
- **Menu latéral**, page Réglages, volet de notifications des passages.

### Modifié

- L'application s'appelle **Piston**, avec sa charte (couleurs, polices, icône).
- Les sessions de l'IA n'ont plus que les deux serveurs MCP de l'application : ni terminal, ni
  fichiers.
- Le code de la VM n'est réinstallé que s'il a changé.
- Le tableau de bord exige son jeton pour toute requête, même locale.
- Revue de design : contraste en mode sombre (AA), un seul bouton principal par écran, statuts
  lisibles sans la couleur.
- Un lien d'annonce est vérifié (https, domaine complet) avant tout ajout.

### Retiré

- **Preuves Job-Room** : hors de cette version. L'option reste éteinte ; le code est gardé pour une
  version suivante.
- « Remplir avec Claude » et la page Fonctions.

### Limites connues

- Mac Apple silicon et macOS 14 ou plus seulement.
- Signature locale, sans notarisation : au premier lancement d'une copie téléchargée, macOS la
  bloque ; l'autoriser dans Réglages Système › Confidentialité et sécurité.
- Le premier démarrage télécharge et installe la machine virtuelle (Ubuntu, Chrome, Node.js, les
  moteurs) : compter plusieurs minutes et quelques gigaoctets.
- Codex, Antigravity et Grok sont couverts par les tests sans être lancés : à considérer comme
  expérimentaux tant qu'un vrai passage n'a pas été fait avec chacun.
- Gmail et Indeed ne fonctionnent qu'avec Claude.

### Journaux à joindre en cas de problème

Les dossiers `logs/` et `partage-vm/logs/` de `~/Library/Application Support/Recherche emploi`,
en particulier `travailleur.log`, `chrome.log`, `ecran.log` et le dossier `latest` (dernier passage).
Ils peuvent contenir des titres d'annonces et des noms d'employeurs : les relire avant de les
envoyer.

## 0.2.0 — 2026-11-09

Application Mac générique et autoportante : Python, dépendances et Lima embarqués ; Claude Code et
Chrome dans la machine virtuelle ; données dans `~/Library/Application Support/Recherche emploi`.
