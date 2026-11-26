---
paths:
  - "src/jobsearch/web/templates/**"
  - "src/jobsearch/web/static/**"
  - "src/jobsearch/web/app.py"
---

# Design system : règles strictes pour l'interface

L'interface suit le design system **Nexus**, aux couleurs, polices et arrondis de la charte **Piston Douceur**
(maquette Figma « Recherche d'emploi — écrans clés », écran « Aujourd'hui — Piston Douceur »), en **palette atténuée** (écran
« Aujourd'hui — P5 · Fond blanc, calme » de la section « palette atténuée ») : fond gris très clair, structure en gris chaud,
la couleur réservée à l'action, au sens d'un statut et à l'urgence. Le tout est traduit une fois pour toutes dans
`src/jobsearch/web/static/style.css` (jetons et composants) et `src/jobsearch/web/templates/_macros.html`
(macros Jinja). Ces deux fichiers sont la seule source de vérité. Ces règles sont obligatoires : une
interface qui ne les respecte pas est à refaire, même si elle « marche ».

## Avant d'écrire la moindre ligne d'interface

1. Chercher le composant dans le catalogue ci-dessous, puis dans `style.css` (`grep`) et `_macros.html`.
2. S'il existe : l'utiliser tel quel, avec ses classes et sa structure HTML exactes (copier une page qui
   l'utilise déjà). Ne pas le recopier, ne pas le « varier » localement.
3. S'il n'existe pas : le créer selon « Ajouter un composant » plus bas, jamais en ligne dans un gabarit.
4. Si on ne sait pas ce que Nexus prévoit pour ce cas : demander à la personne. Ne jamais inventer une
   spécification Nexus ni citer un composant Nexus qui n'est pas déjà nommé dans le code.

## Interdits

- **Aucune couleur en dur** (`#hex`, `rgb()`, nom de couleur) dans un gabarit, un JS ou une règle CSS de
  composant. Toujours `var(--…)`. Le texte sur un fond plein `--vert` ou `--alerte` prend `--sur-vert` ou
  `--sur-alerte` (blanc en clair, foncé en sombre : du blanc sur le vert clair du mode sombre ne tient pas
  le contraste AA), jamais `#fff`. Seules exceptions tolérées : le curseur blanc de l'interrupteur, et les
  ombres `rgba(0,0,0,…)` des surfaces flottantes (modale, volet, panneau).
- **Aucun attribut `style="…"`** ni bloc `<style>` dans un gabarit. Ceux qui existent sont une dette : ne
  pas en ajouter, et retirer celui qu'on croise dans les lignes qu'on modifie. Seule exception : la largeur
  calculée d'une barre `.progression` ou d'une `.jauge`, écrite `style="width: {{ … }}%"` et rien d'autre.
- **Aucune bibliothèque d'interface** : ni Tailwind, ni Bootstrap, ni React, ni police ou CDN de plus
  (Quicksand pour les titres, la marque, les chiffres et les boutons ; Nunito Sans pour le texte : jetons
  `--police-titres` et `--police-texte`). Pas de nouveau fichier CSS : tout va dans `style.css`.
- **Aucune icône hors du jeu Lucide** de la macro `icone()`. Pas d'emoji ni de glyphe Unicode comme icône
  (les `⚠` et `✕` existants sont une dette). Une icône manquante s'ajoute au dictionnaire `ICONES` de
  `_macros.html` (tracé Lucide d'origine, `viewBox 0 0 24 24`, trait `currentColor`).
- **Pas de violet, pas d'étincelle, pas de dégradé « magique » pour l'IA** (règles AI de Nexus). Pas de
  lavande non plus : `--marine` reste le Forêt de la charte (lin en sombre). Ce que
  l'IA produit est étiqueté par du texte (`badge-ia`, « Question de l'IA »), en vert ou en neutre.
- **Pas de nouvelle valeur ad hoc** quand un jeton ou une valeur du système existe (voir « Jetons »).
- **Pas de `!important`**, sauf pour `[hidden]`.

## Jetons (`:root` de `style.css`)

| Usage | Jeton |
|---|---|
| Action principale, sélection, focus, succès | `--vert` (Forêt en clair, Pêche en sombre), `--vert-survol` (survol du bouton principal), `--vert-fonce` (texte sur fond pâle), `--vert-pale` / `--ok-pale` (fond) ; texte sur fond `--vert` plein : `--sur-vert` |
| Liens, titres de carte qui mènent à l'annonce, lien ouvert du menu | `--lien` |
| Fond de page / surfaces | `--fond` (gris très clair), `--surface` (carte, champ, menu), `--surface-2` (fond neutre d'un gris chaud : survol, lien ouvert du menu ; jamais de sauge pour la structure) |
| Bordures et séparateurs | `--bord` (cartes, séparateurs : décor seul) ; contour des boutons secondaires : `--contour` ; contour de ce qui se remplit ou se coche (champs, compositeur, pilules à cocher, étiquettes) : `--contour-champ` (3:1 au moins, WCAG 1.4.11), survol `--texte` |
| Texte | `--texte` (principal), `--texte-2` (secondaire), `--texte-3` (indications, en-têtes de tableau ; AA tenu sur `--fond` et `--surface` dans les deux modes : ne pas l'éclaircir) |
| Réussite, en service, connecté | `--reussite` (texte, vert de la charte) sur `--ok-pale` (sauge) : toujours un vert, jamais le beurre de `--vert-pale` |
| Erreur, destructif, en retard | `--alerte`, `--alerte-pale` ; texte sur fond `--alerte` plein : `--sur-alerte` |
| Avertissement, à faire bientôt | `--attention`, `--attention-pale` |
| Accent de marque, statuts « candidature » | `--marine` (texte), `--marine-pale` (fond de la pilule « envoyée ») |
| Rayon des cartes et blocs | `--rayon` (28px : cartes, tableaux, bandeaux, modale, compositeur) ; `--rayon-champ` (18px : champs, panneaux, blocs intérieurs) ; 999px pour boutons, pilules, délais et notes |
| Ombre des cartes | `--ombre` (nulle : la charte n'en veut pas) |

- Tailles en `rem`/`em`, sur l'échelle déjà en usage (.72, .75, .78, .8, .82, .85, .88, .9, .92, .95, 1,
  1.05, 1.15, 1.3, 1.45 rem). Hauteur des champs : 42px. Largeur du contenu : `main` (1120px, 40px de marge de
  chaque côté à droite du menu), cartes à 20 × 24px d'intérieur, formulaires et
  colonnes de lecture ~46–52rem, texte courant ≤ 72ch.
- Points de rupture existants seulement : 560, 640, 760, 900, 960px.
- **Mode sombre obligatoire** : un nouveau jeton se déclare dans `:root` **et** dans le bloc
  `@media (prefers-color-scheme: dark)`. Une règle qui n'utilise que des jetons n'a rien de plus à faire.

## Catalogue : composant Nexus → ce qu'il faut utiliser

| Besoin | À utiliser |
|---|---|
| Bouton | `button` / `a.bouton` ; principal `.go` (un seul par écran : sur le tableau de bord, « Trancher maintenant » ; les actions des cartes et des tableaux restent secondaires), destructif `.non`, discret `.discret`, tailles `.petit` / `.grand` ; action tertiaire en lien `.lien-neutre`, suppression en lien `.lien-danger`. Rangée de boutons : `.boutons` (`.boutons.pied` en bas de formulaire, à droite) |
| Action qui lance Claude | macro `claude()` (bouton bloqué `.occupe` pendant la session), échec via `etat_tache()` |
| Message après une action (FlashMessage) | `msg`/`ok` passés au gabarit → macro `flash_message()`, rendue par `base.html` sur toutes les pages : flottant en bas à droite, le succès se referme seul, le refus reste jusqu'à sa croix. Ne pas en fabriquer d'autre ni surcharger le bloc `message`. `.flash.ko` ne sert plus qu'aux erreurs écrites par le JS à côté d'une saisie (`data-erreur`) et à la ligne d'état du tri |
| Bandeau (Banner) | `.bandeau.ok` / `.attention` / `.alerte` / `.info` avec `<span class="point">` : une ligne de texte dont le point porte le sens ; seul `.alerte` (erreur) garde un fond |
| Carte | `.carte` (`.tete`, `.meta`, `.extrait`) dans `.cartes` / `.cartes.deux` |
| Chiffres clés | `.chiffres` > `.chiffre` (sans cadre : le chiffre et son libellé sur le fond) ; un chiffre qui mène à sa section : `a.chiffre` (lien `#ancre`), et la section ne répète pas le compte dans son titre ; jauge `.jauge` ; progression `.progression` |
| Pilule, statut (Badge) | `.puce` et ses variantes (`A`, `B`, `C`, `nouvelle`, `retenue`, `entretien`, `refus`, `retard`…) ; délais `.delai`. Pilules, délais et notes (`.note-glob`) sont du texte coloré, sans aplat. Un statut d'annonce a sa couleur de texte : à trancher (`nouvelle`, ambre), oui (`retenue`, `--vert-fonce`), envoyée (`candidature`, marine), entretien et offre (`--vert`), refus et non (neutre), retard en `--alerte`. L'avis de l'IA s'écrit « IA : oui / peut-être / non » (macro `notes`), pour ne pas se confondre avec la décision de la personne. Un nom de statut ne sert jamais de nom de bloc ailleurs dans `style.css` (`.entretien` seul a déformé `.puce.entretien`) |
| État d'un réglage (connecteur, clé, fonction, IA) | `.puce.B` : en service, connecté (texte `--reussite`) ; `.puce` neutre : éteint par choix ou facultatif (« inactif », « fermée », « absente ») ; `.puce.nouvelle` : à faire, sans rien bloquer (« à configurer », « à compléter ») ; `.puce.retard` : bloquant seulement (« non connecté ») |
| Ce que l'IA a produit | `.badge-ia` avec `icone("modele")` et un texte (« Question de l'IA », « Proposés par l'IA ») ; une seule étiquette IA, et jamais sur ce qui ne vient pas de l'IA |
| État vide (EmptyState) | macro `etat_vide()` uniquement (test dans `tests/test_pipeline_web.py`) |
| Champ (TextField, SelectField) | `.champ` > `label` + contrôle + `.aide`, dans `.grille-champs` (`.champ.large` pour toute la largeur) ; erreur `.erreur-champ` ; préfixe `.avec-prefixe` |
| Liste libre, une valeur par champ (au lieu d'une zone de texte « une par ligne ») | macro `champ_lignes` : une colonne pour une liste simple (type « liste » du profil), plusieurs pour des lignes à plusieurs parties ; croix pour retirer, « Ajouter » pour ouvrir un champ |
| Liste déroulante avec recherche (SelectField qui filtre) | `<select data-liste-recherche>` : `champs.js` le cache derrière `.liste-recherche` (saisie + `.panneau-liste`) ; dans des lignes répétables, colonne `(…, choix, "recherche")` de `champ_lignes`, où une valeur déjà choisie dans une autre ligne n'est plus proposée |
| Téléphone, date (PhoneField, DateField) | structures `.telephone` et `.date-saisie` pilotées par `champs.js` (voir `_champs_profil.html`) |
| Choix | cartes radio `.carte-choix`, pilules à cocher `.puce-canton`, étiquettes `.tags`, cases `.cases` |
| Interrupteur (Toggle) | `button.interrupteur` (réglage immédiat, sans bouton Enregistrer) |
| Étapes (Stepper), Checklist | `.stepper` ; `.checklist` ; onboarding en `.carte-accueil` (une action principale par écran) |
| Modale (Modal) | `dialog.modale` (`.modale-tete`, `.actions-modale`) ; confirmation d'une action destructrice : macro `confirmation()`, jamais `confirm()` du navigateur |
| Ajout (« Ajouter… », « Noter… ») | **jamais un accordéon** (`details`). Par défaut une modale : bouton `bouton_ajout(id, libelle)` et `{% call modale_ajout(id, titre, action) %}` les champs `{% endcall %}` (`dialog.modale.large`, avec « Annuler » et le bouton d'envoi). Quand l'ajout se poursuit en conversation avec l'IA (un élément de la base du parcours) : dans la salle de l'entretien, comme `parcours_ajout.html` — une question fixe dans le fil, la réponse en champs structurés (`reponse_question`, `.reponse-guidee`), puis l'entretien sans changer d'écran. L'ajout d'une ligne dans `champ_lignes` ou d'une étiquette dans `champ_puces` reste sur place (test dans `tests/test_design_system.py`) |
| Volet (SlideOver) | `dialog.volet` (`.tete-volet`, `.corps-volet`, `.pied-volet`, `.fermer-volet`) ; à gauche par défaut, `.droite` pour entrer par la droite |
| Navigation | menu latéral `aside.menu-lateral` (ResponsiveNavLink, `_menu_lateral.html`, inclus par `base.html`) : les espaces de travail et, en bas, un seul lien « Réglages ». Les réglages n'entrent pas dans le menu : ils ont leur page `/reglages` (`reglages.html`), et chaque page de réglage reçoit de `base.html` un retour `.retour` vers elle ; onglets de page `nav.onglets-annonce` (vues d'une annonce) ; « Ma recherche » liste ses étapes en lignes de réglage (`profil_etapes.html`), chaque étape ayant un retour vers elle ; tri `.tri` ; retour `.retour` |
| Notifications (passages) | volet `dialog.volet.droite#notifications` (`_notifications.html`), ouvert par `[data-ouvrir-notifications]` ; une `li.notification` (`.ok` / `.attention` / `.alerte` / `.en-cours`, `.nouvelle`) par passage. Pas de bandeau d'état du passage dans les pages |
| En-tête d'une page de réglage | macro `entete_reglage(reglage)` avec l'introduction en `{% call %}` : le `h1` et le titre d'onglet viennent de `GROUPES_REGLAGES` (`app.py`), la même liste que la page Réglages ; ne pas écrire de `h1` ni de `block titre` à la main (test dans `tests/test_reglages.py`) |
| Ligne de réglage | `ul.reglages-liste` > `li` > `.reglage-ligne` (`.texte` > `.titre` + `.description`, action à droite) ; ligne qui ouvre une page : macro `ligne_lien()` (`a.reglage-ligne` : icône, texte, chevron `suivant`) |
| Tableau | `.tableau` > `table` ; nombres `td.nb` ; boutons d'une ligne dans `td.actions` (à droite, sans retour à la ligne), sous un `th.actions` au libellé `.cache` ; en-têtes dans les mots de l'application, pas ceux d'un formulaire administratif |
| Filtres d'une liste | `form.filtres[data-filtre-auto]` : un choix dans une liste déroulante s'applique tout de suite (`champs.js`), la recherche à Entrée ; pas de bouton « Filtrer » |
| Repli (ExplainDisclosure) | `details` > `summary` terminé par `icone("chevron", "icone chevron")` : le triangle du navigateur est masqué partout, le chevron se retourne à l'ouverture |
| Liste de vérifications | `.verifs` (`li.ok` / `li.ko`) |
| Tâche en cours / attente | `.etat-tache` (`.ok`, `.erreur`) avec `.spinner` ; attente IA `.statut-ia` |
| Entretien avec l'IA | macros `fil_entretien()` (fil, ExplainDisclosure) et `compositeur()` (AIComposer, AIStatus, avertissement `.avert-ia`) |
| Icône | macro `icone(nom)` |
| Titre de section en petites capitales | `.section-base` (ne pas redéclarer majuscules + `--texte-3` ailleurs) |
| Lien d'action discret (« Modifier », « Annuler ») | `.lien-neutre` (`.petit`) ; destructif `.lien-danger` |
| Utilitaires | `.muet`, `.petit`, `.fort`, `.pile`, `.cache` (texte pour lecteur d'écran ; ne pas utiliser `.lecteur`, doublon) |

## Ajouter un composant (seulement si rien ci-dessus ne convient)

1. Le dire d'abord à la personne et nommer le composant Nexus qu'il suit.
2. L'écrire dans `style.css`, dans une section à lui, précédée d'un commentaire en français :
   `/* Nom (Nexus NomDuComposant) : ce qu'il fait, en une phrase. */`. Classes en français, minuscules,
   avec tirets, dans l'esprit des noms existants.
3. Uniquement des jetons et des valeurs de l'échelle. Survol avec `--texte-3` sur la bordure ou
   `--surface-2` en fond ; état actif ou choisi `--vert-pale` + `--vert-fonce` (ou `--vert` + `--sur-vert`).
   Les boutons s'enfoncent de 1px à l'appui (règle globale de `button` et `.bouton`) : ne pas la retirer.
4. S'il sert à plus d'un endroit ou porte une structure HTML : une macro dans `_macros.html`, avec un
   commentaire `{# … #}` qui dit quand l'utiliser.
5. Un test dans `tests/` si son rendu est une règle (comme l'état vide).

## Accessibilité et mouvement (non négociable)

- Tout élément interactif a un focus visible : `outline: 2px solid var(--vert); outline-offset: 2px`
  (1px pour les champs), sur `:focus-visible`.
- Toute animation ou transition a sa variante `@media (prefers-reduced-motion: reduce)`. Durées courtes :
  150–220ms, `ease-out` à l'entrée.
- HTML sémantique d'abord (`button`, `dialog`, `details`, `fieldset`/`legend`, `label for`). Un bouton à
  icône seule a un `aria-label` ; un état qui change a `aria-pressed`, `aria-busy` ou `role="status"`.
- Cible cliquable d'au moins 24px.

## Textes de l'interface

- L'application se nomme « l'application », jamais « la plateforme » ; le retour vers l'accueil s'appelle
  « Aujourd'hui », comme dans le menu, jamais « Tableau de bord ».
- Une introduction tient en une phrase. Si l'écran a besoin de plus d'explication, c'est l'écran qu'il faut
  changer.

- En français, en **tutoyant** (« Ton parcours », « Ta réponse ») ; jamais de vouvoiement.
- Majuscule au premier mot seulement ; guillemets « » ; espace insécable avant `: ; ? !`.
- Court et concret : ce qui se passe, puis ce que la personne peut faire. Pas de jargon technique.
- Rien de propre à une personne dans un gabarit (règle générale de `CLAUDE.md`).

## Vérifier avant de dire « c'est fait »

1. `uv run pytest`. `tests/test_design_system.py` fait échouer tout nouvel écart : `style=`, `<style>`,
   couleur en dur, jeton inconnu ou sans valeur sombre, `!important`, SVG ou glyphe hors `icone()`, icône
   absente d'`ICONES`, ressource externe, vouvoiement. Les écarts anciens y sont comptés comme une dette :
   en corriger un oblige à baisser le chiffre ; ne jamais monter un chiffre pour faire passer un test.
2. `scripts/construire-app.sh --ouvrir`, puis regarder la page en **mode clair et en mode sombre**, et à
   une largeur étroite (≤ 640px).
3. Relire le diff : aucune couleur en dur, aucun `style=`, aucun nouveau composant qui double un existant.
