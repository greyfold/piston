---
paths:
  - "src/jobsearch/web/templates/**"
  - "src/jobsearch/web/static/**"
  - "src/jobsearch/web/app.py"
---

# Design system : règles strictes pour l'interface

L'interface suit le design system **Nexus**, traduit une fois pour toutes dans
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
  composant. Toujours `var(--…)`. Seules exceptions tolérées : `#fff` sur un fond `--vert`/`--alerte`, et
  les ombres `rgba(0,0,0,…)` des surfaces flottantes (modale, volet, panneau).
- **Aucun attribut `style="…"`** ni bloc `<style>` dans un gabarit. Ceux qui existent sont une dette : ne
  pas en ajouter, et retirer celui qu'on croise dans les lignes qu'on modifie. Seule exception : la largeur
  calculée d'une barre `.progression` ou d'une `.jauge`, écrite `style="width: {{ … }}%"` et rien d'autre.
- **Aucune bibliothèque d'interface** : ni Tailwind, ni Bootstrap, ni React, ni police ou CDN de plus
  (Figtree est la seule police). Pas de nouveau fichier CSS : tout va dans `style.css`.
- **Aucune icône hors du jeu Lucide** de la macro `icone()`. Pas d'emoji ni de glyphe Unicode comme icône
  (les `⚠` et `✕` existants sont une dette). Une icône manquante s'ajoute au dictionnaire `ICONES` de
  `_macros.html` (tracé Lucide d'origine, `viewBox 0 0 24 24`, trait `currentColor`).
- **Pas de violet, pas d'étincelle, pas de dégradé « magique » pour l'IA** (règles AI de Nexus). Ce que
  l'IA produit est étiqueté par du texte (`badge-ia`, « Question de l'IA »), en vert ou en neutre.
- **Pas de nouvelle valeur ad hoc** quand un jeton ou une valeur du système existe (voir « Jetons »).
- **Pas de `!important`**, sauf pour `[hidden]`.

## Jetons (`:root` de `style.css`)

| Usage | Jeton |
|---|---|
| Action principale, sélection, focus, succès | `--vert`, `--vert-fonce` (texte sur fond pâle), `--vert-pale` / `--ok-pale` (fond) |
| Fond de page / surfaces | `--fond`, `--surface` (carte, champ), `--surface-2` (fond neutre, survol) |
| Bordures et séparateurs | `--bord` |
| Texte | `--texte` (principal), `--texte-2` (secondaire), `--texte-3` (indications, jamais pour un texte indispensable sur `--fond` : contraste AA insuffisant) |
| Erreur, destructif, en retard | `--alerte`, `--alerte-pale` |
| Avertissement, à faire bientôt | `--attention`, `--attention-pale` |
| Accent de marque, statuts « candidature » | `--marine` |
| Rayon des cartes et blocs | `--rayon` (10px) ; 8px pour boutons et champs ; 12px pour modale, compositeur, grandes cartes ; 999px pour pilules |
| Ombre des cartes | `--ombre` (nulle en mode sombre) |

- Tailles en `rem`/`em`, sur l'échelle déjà en usage (.72, .75, .78, .8, .82, .85, .88, .9, .92, .95, 1,
  1.05, 1.15, 1.3, 1.45 rem). Hauteur des champs : 42px. Largeur du contenu : `main` (1120px), formulaires et
  colonnes de lecture ~46–52rem, texte courant ≤ 72ch.
- Points de rupture existants seulement : 560, 640, 760, 900, 960px.
- **Mode sombre obligatoire** : un nouveau jeton se déclare dans `:root` **et** dans le bloc
  `@media (prefers-color-scheme: dark)`. Une règle qui n'utilise que des jetons n'a rien de plus à faire.

## Catalogue : composant Nexus → ce qu'il faut utiliser

| Besoin | À utiliser |
|---|---|
| Bouton | `button` / `a.bouton` ; principal `.go` (un seul par écran ou par formulaire), destructif `.non`, discret `.discret`, tailles `.petit` / `.grand` ; action tertiaire en lien `.lien-neutre`, suppression en lien `.lien-danger`. Rangée de boutons : `.boutons` (`.boutons.pied` en bas de formulaire, à droite) |
| Action qui lance Claude | macro `claude()` (bouton bloqué `.occupe` pendant la session), échec via `etat_tache()` |
| Message après une action (FlashMessage) | `msg`/`ok` passés au gabarit → macro `flash_message()`, rendue par `base.html` sur toutes les pages : flottant en bas à droite, le succès se referme seul, le refus reste jusqu'à sa croix. Ne pas en fabriquer d'autre ni surcharger le bloc `message`. `.flash.ko` ne sert plus qu'aux erreurs écrites par le JS à côté d'une saisie (`data-erreur`) et à la ligne d'état du tri |
| Bandeau (Banner) | `.bandeau.ok` / `.attention` / `.alerte` / `.info` avec `<span class="point">` |
| Carte | `.carte` (`.tete`, `.meta`, `.extrait`) dans `.cartes` / `.cartes.deux` |
| Chiffres clés | `.chiffres` > `.chiffre` ; jauge `.jauge` ; progression `.progression` |
| Pilule, statut (Badge) | `.puce` et ses variantes (`A`, `B`, `C`, `nouvelle`, `retenue`, `entretien`, `refus`, `retard`…) ; délais `.delai` |
| État d'un réglage (connecteur, clé, fonction, IA) | `.puce.B` : en service ; `.puce` neutre : éteint par choix ou facultatif (« inactif », « fermée », « absente ») ; `.puce.nouvelle` : à faire, sans rien bloquer (« à configurer », « à compléter ») ; `.puce.retard` : bloquant seulement (« non connecté ») |
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
| Tableau | `.tableau` > `table` ; nombres `td.nb` |
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
   `--surface-2` en fond ; état actif ou choisi `--vert-pale` + `--vert-fonce` (ou `--vert` + `#fff`).
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
