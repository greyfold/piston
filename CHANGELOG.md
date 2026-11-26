# Journal des modifications

Les versions suivent [SemVer](https://semver.org/lang/fr/) : tant que la version commence par 0,
l'application est en essai et peut changer d'une version à l'autre.

## 0.5.1 — 2026-11-26 (version d'essai)

### Corrigé

- **Aujourd'hui tient dans la fenêtre** : la page ne défile plus en entier, le menu reste en place, et la liste
  défile seule quand elle dépasse la fenêtre, comme le détail à droite. Plus de bande noire ou blanche en haut :
  chaque colonne monte jusqu'en haut de la fenêtre avec sa couleur.

### Modifié

- **Offres et Candidatures reviennent en tableaux**, comme en 0.4.0. Seule la page Aujourd'hui garde la liste et
  le détail côte à côte.

## 0.5.0 — 2026-11-26 (version d'essai)

### Modifié

- **Aujourd'hui, Offres et Candidatures en deux volets** : la liste à gauche, et le détail de la ligne choisie à
  droite, sans changer de page. Un clic sur une annonce ouvre son dossier, son adéquation, ses sources et son
  journal ; un courrier d'employeur à classer s'ouvre de la même façon. Sur une fenêtre étroite, la liste et le
  détail se suivent, avec un retour vers la liste.
- **Le dossier d'abord** : quand une annonce a un dossier, son onglet vient en premier et s'ouvre par défaut.
- Les boutons d'une annonce (réévaluer, recharger l'original, refaire le dossier, mots-clés) ramènent là où tu étais.

### Ajouté

- **Conditions d'utilisation**, en français (le texte qui fait foi), en anglais et en allemand : à accepter une fois
  avant de commencer (et à chaque nouvelle version du texte), puis consultables dans Réglages › Application et
  depuis la page Confidentialité.

## 0.4.0 — 2026-11-26 (version d'essai)

Première version signée et notarisée par Apple. Si tu as la 0.3.1, télécharge celle-ci une fois à la
main et remplace l'ancienne dans Applications : tes données sont gardées. Les suivantes arriveront
toutes seules.

### Ajouté

- **Mises à jour** : l'application vérifie une fois par jour s'il existe une nouvelle version, te montre
  ses nouveautés et propose de l'installer, puis se relance. « Rechercher les mises à jour… » est dans le
  menu Piston et dans celui de la barre des menus, où tu peux couper la vérification quotidienne.
- **Confidentialité** : la page dit ce que la vérification des mises à jour envoie (le numéro de version,
  rien de ton profil).

### Modifié

- **Ouverture sans détour** : l'application est signée Developer ID et notarisée ; macOS l'ouvre sans
  passer par Réglages Système › Confidentialité et sécurité.
- **Installation** par une image disque : glisser Piston dans Applications.
- **Le menu latéral revient** dans la fenêtre de l'application, à la place de la barre latérale et de la
  barre d'outils du Mac.
- **Tant que ton parcours et ce qui compte pour toi manquent**, le tri des annonces ne s'affiche pas :
  l'accueil t'invite d'abord à les remplir.

## 0.3.1 — 2026-11-25 (version d'essai)

À essayer sur un Mac neuf ou un compte neuf : il faut au moins 15 Go libres pour créer la machine
virtuelle. Pour repartir de zéro, voir « Désinstaller » dans le README.

### Ajouté

- **Écran de bienvenue** à la première ouverture, avant l'assistant : ce que fait l'application,
  comment elle protège tes données, et la préparation de la machine virtuelle qui se fait pendant ce
  temps. Ta recherche et tes sources se règlent sans l'attendre.
- **Barre d'avancement** du démarrage de la machine virtuelle (page `/demarrage`) : les étapes, un
  pourcentage et le temps qui reste, estimé d'après la durée de chaque étape la dernière fois.
- Après « Continuer sans attendre », un **bandeau** en haut des pages suit l'avancement et dit quand
  la machine virtuelle est prête.
- **Place sur le disque vérifiée** avant de créer la machine virtuelle (15 Go libres), de la démarrer
  et de connecter l'IA (3 Go) : un disque plein le dit en clair, avec la place qui reste, au lieu de
  « /bin/bash: Input/output error ».
- **Journal des connexions à l'IA** : une connexion ratée laisse sa sortie entière dans
  `logs/connexion.log`, sans les adresses de connexion ; l'écran n'en montre que la dernière ligne.
- **Désinstaller** (README) : ce que la corbeille ne retire pas (la machine virtuelle, les données, le
  cache de Lima) et comment l'effacer.

### Modifié

- Pendant le démarrage, ce qui demande l'IA le dit clairement (« la machine virtuelle démarre encore,
  42 %… ») au lieu d'une panne ; la connexion à l'IA attend que la machine soit prête, et l'étape
  « Ton IA » de l'assistant montre l'avancement au lieu de boutons qui échoueraient.
- L'écran d'attente natif du démarrage est remplacé par la page `/demarrage` du tableau de bord.
- **Pages plus rapides** : Aujourd'hui, IA, Connecteurs et Santé n'attendent plus la machine virtuelle
  (une à trois secondes à chaque ouverture). L'état des moteurs d'IA et de la VM est gardé en mémoire et
  relu en arrière-plan (`web/etat_distant.py`) ; la machine virtuelle interroge ses moteurs en même temps
  plutôt que l'un après l'autre.
- Les lettres des dossiers sont mises à la date du jour par l'échéance quotidienne (`echeances.py`) :
  l'onglet Dossier n'attend plus le PDF de la VM à sa première ouverture de la journée. Un PDF que la VM
  n'a pas pu faire est refait à la lecture suivante.
- Les polices (Quicksand, Nunito Sans, licence OFL) sont servies par l'application : plus aucune requête
  vers Google à l'ouverture d'une page. Feuille de style, scripts et polices restent en cache.

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
