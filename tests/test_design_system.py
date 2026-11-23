"""Le design system (.claude/rules/design-system.md) tenu par des tests.

Les écarts qui existaient quand ces tests ont été écrits sont listés dans les BASE_… : c'est une dette.
Un test échoue si un fichier en a plus (nouvel écart : utiliser un jeton ou un composant existant) ou
moins (écart corrigé : baisser le chiffre ici, pour que la dette ne remonte pas).
"""

import re
from collections import Counter
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
WEB = RACINE / "src" / "jobsearch" / "web"
GABARITS = WEB / "templates"
STATIC = WEB / "static"
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def _gabarits():
    return sorted(GABARITS.glob("*.html"))


def _scripts():
    return sorted(STATIC.glob("*.js"))


def _compter(fichiers, motif):
    return Counter({f.name: n for f in fichiers if (n := len(re.findall(motif, f.read_text(encoding="utf-8"))))})


def _comparer(releve, base, quoi, conseil):
    trop = {f: (n, base.get(f, 0)) for f, n in releve.items() if n > base.get(f, 0)}
    assert not trop, (
        f"Nouveau {quoi} : "
        + ", ".join(f"{f} ({n} au lieu de {b})" for f, (n, b) in sorted(trop.items()))
        + f". {conseil} Voir .claude/rules/design-system.md."
    )
    moins = {f: (releve.get(f, 0), b) for f, b in base.items() if releve.get(f, 0) < b}
    assert not moins, (
        f"Dette réduite ({quoi}) : "
        + ", ".join(f"{f} ({n} au lieu de {b})" for f, (n, b) in sorted(moins.items()))
        + ". Baisser le chiffre dans tests/test_design_system.py."
    )


def _regles_css():
    """(sélecteur, déclarations) des règles les plus internes, commentaires retirés."""
    texte = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    return [(s.strip(), d) for s, d in re.findall(r"([^{}]+)\{([^{}]*)\}", texte)]


COULEUR = r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\("


# Attributs style="…" dans les gabarits : la mise en forme passe par une classe de style.css.
BASE_STYLE_EN_LIGNE = {
    "_macros.html": 5, "accueil.html": 3, "ajout.html": 2,
    "connexion.html": 2,
}


# Seule exception : la largeur calculée d'une barre de progression ou d'une jauge, style="width: {{ … }}%".
LARGEUR_CALCULEE = r"""width:\s*\{\{[^}]*\}\}%["']"""


def test_pas_de_nouvel_attribut_style():
    _comparer(_compter(_gabarits(), r"""\sstyle\s*=\s*["'](?!""" + LARGEUR_CALCULEE + ")"), BASE_STYLE_EN_LIGNE,
              "attribut style=", "Utiliser une classe existante ou en ajouter une dans style.css.")


def test_pas_de_bloc_style_dans_les_gabarits():
    _comparer(_compter(_gabarits(), r"<style\b"), {},
              "bloc <style>", "Tout le CSS va dans static/style.css.")


def test_pas_de_couleur_en_dur_dans_les_gabarits_ni_le_js():
    _comparer(_compter(_gabarits() + _scripts(), COULEUR), {},
              "couleur en dur", "Utiliser une classe de style.css, qui prend un jeton var(--…).")


def test_css_couleurs_par_jetons():
    """Hors des blocs :root, pas de couleur en dur : le texte sur fond vert ou rouge prend --sur-vert ou
    --sur-alerte. Seuls le #fff du curseur de l'interrupteur et les rgba() des ombres et des fonds assombris
    (::backdrop) sont admis."""
    ecarts = []
    for selecteur, decl in _regles_css():
        if selecteur == ":root":
            continue
        for prop, valeur in re.findall(r"([a-z-]+)\s*:\s*([^;]+)", decl):
            for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", valeur):
                if not (c.lower() in ("#fff", "#ffffff") and selecteur == "button.interrupteur .curseur"):
                    ecarts.append(f"{selecteur} {{ {prop}: {c} }}")
            if re.search(r"\b(?:rgba?|hsla?)\(", valeur):
                ombre = prop == "box-shadow" or (prop == "background" and "::backdrop" in selecteur)
                if not ombre:
                    ecarts.append(f"{selecteur} {{ {prop}: {valeur.strip()} }}")
    # Dette : fond de l'aperçu PDF.
    _comparer(Counter({"style.css": len(ecarts)}) - Counter(), {"style.css": 1},
              "couleur en dur dans style.css (" + " ; ".join(ecarts) + ")",
              "Utiliser un jeton, ou en ajouter un dans :root et dans le bloc sombre.")


def test_chaque_jeton_de_couleur_a_sa_valeur_sombre():
    racines = [d for s, d in _regles_css() if s == ":root"]
    assert len(racines) == 2, "style.css doit avoir un :root clair et un :root sombre"
    jetons = lambda d: dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+)", d))
    clair, sombre = jetons(racines[0]), jetons(racines[1])
    couleurs = {j for j, v in clair.items() if re.search(COULEUR, v)}
    manquants = sorted(couleurs - sombre.keys())
    assert not manquants, f"Jetons sans valeur pour le mode sombre : {manquants}"


def test_var_ne_cite_que_des_jetons_definis():
    definis = set(re.findall(r"(--[a-z0-9-]+)\s*:", CSS))
    fichiers = [STATIC / "style.css"] + _gabarits() + _scripts()
    cites = {(f.name, v) for f in fichiers for v in re.findall(r"var\((--[a-z0-9-]+)", f.read_text(encoding="utf-8"))}
    inconnus = sorted(f"{f} : {v}" for f, v in cites if v not in definis)
    assert not inconnus, f"Jetons inexistants (en choisir un du tableau des jetons) : {inconnus}"


def test_important_reserve_a_hidden():
    n = sum(d.count("!important") for s, d in _regles_css() if s != "[hidden]")
    # Dette : .checklist .libelle.sans-barre (trois déclarations).
    _comparer(Counter({"style.css": n}) - Counter(), {"style.css": 3}, "!important", "Augmenter la spécificité.")


def test_un_seul_fichier_css_et_pas_de_ressource_externe():
    assert sorted(f.name for f in STATIC.glob("*.css")) == ["style.css"], "Tout le CSS va dans style.css."
    assert "@import" not in CSS
    externes = []
    for f in _gabarits():
        for balise in re.findall(r"<(?:link|script)\b[^>]*>", f.read_text(encoding="utf-8")):
            url = re.search(r"""(?:href|src)\s*=\s*["'](https?:)?//([^/"']+)""", balise)
            if url and url.group(2) not in ("fonts.googleapis.com", "fonts.gstatic.com"):
                externes.append(f"{f.name} : {balise}")
    assert not externes, f"Ni bibliothèque ni CDN : seule la police Figtree vient de l'extérieur. {externes}"


def test_icones_par_la_macro():
    # SVG écrits à la main hors de la macro icone() : chevrons et coches dessinés avant le jeu Lucide.
    base_svg = {"accueil.html": 1, "bienvenue.html": 1}
    gabarits = [f for f in _gabarits() if f.name != "_macros.html"]
    _comparer(_compter(gabarits, r"<svg\b"), base_svg, "<svg> écrit à la main",
              "Utiliser icone(nom) ; une icône manquante s'ajoute à ICONES dans _macros.html.")
    _comparer(_compter(_gabarits(), r"[⚠✕✓✗✔✘❌✅]"), {"_macros.html": 2},
              "glyphe utilisé comme icône", "Utiliser icone(nom).")


def test_les_icones_citees_existent():
    macros = (GABARITS / "_macros.html").read_text(encoding="utf-8")
    bloc = re.search(r"\{% set ICONES = \{(.*?)\} %\}", macros, re.S).group(1)
    connues = set(re.findall(r'^\s*"([a-z_]+)"\s*:', bloc, re.M))
    citees = {n for f in _gabarits() for n in re.findall(r"""\bicone\(\s*["']([a-z_]+)["']""", f.read_text(encoding="utf-8"))}
    assert citees - connues == set(), f"Icônes absentes de ICONES (la macro dessinerait un vide) : {sorted(citees - connues)}"


def test_pas_de_vouvoiement():
    vous = _compter(_gabarits(), r"(?<![\w-])(?:[Vv]ous|[Vv]otre|[Vv]os)\b")  # pas « rendez-vous »
    assert not vous, f"L'interface tutoie : {dict(vous)}"


ECHELLE = {".72", ".75", ".78", ".8", ".82", ".85", ".88", ".9", ".92", ".95", "1", "1.05", "1.15", "1.3", "1.45"}


def test_tailles_de_texte_sur_l_echelle():
    hors = [t for t in re.findall(r"font-size:\s*([0-9.]+)rem", CSS) if t not in ECHELLE]
    # Dette : .7 (×3), .76 (×2), .84 (×3), .86, .98, 1.1, 1.35 (avant ces règles).
    _comparer(Counter({"style.css": len(hors)}) - Counter(), {"style.css": 12},
              "taille de texte hors de l'échelle (%s)" % ", ".join(sorted(set(hors))),
              "Prendre la valeur voisine de l'échelle.")


def test_pas_de_confirm_du_navigateur():
    # Dette : confirmations déjà là avant la macro confirmation() (Nexus Modal).
    _comparer(_compter(_gabarits() + _scripts(), r"\bconfirm\("),
              {"parcours.html": 1, "entretien.js": 1},
              "confirm() du navigateur", "Utiliser la macro confirmation() de _macros.html.")


def test_une_seule_etiquette_ia():
    anciennes = [c for c in (".tag-ia", ".puce.ia") if c in CSS]
    assert not anciennes, f"Étiquette IA en double : {anciennes}. Utiliser .badge-ia."


def test_message_apres_action_un_seul_composant():
    """FlashMessage : rendu par base.html seulement ; aucun gabarit ne refait le message `msg` à sa façon."""
    import pathlib, re
    gabarits = pathlib.Path(__file__).parents[1] / "src/jobsearch/web/templates"
    base = (gabarits / "base.html").read_text()
    assert "{{ flash_message(msg, ok) }}" in base
    for f in gabarits.glob("*.html"):
        texte = f.read_text()
        if f.name == "trier.html":              # ligne d'état du tri, réécrite par tri.js à chaque décision
            continue
        assert "block message" not in texte or f.name == "base.html", f.name
        assert not re.search(r'class="flash[^"]*"[^>]*>\s*\{\{\s*msg', texte), f.name
        assert "flash_message(" not in texte or f.name in ("base.html", "_macros.html"), f.name


def test_ajout_en_modale_jamais_en_accordeon():
    """Une fonction d'ajout (« Ajouter… », « Noter… ») s'ouvre dans une modale (macro modale_ajout), jamais
    dans un accordéon : aucun <summary> ne commence par un de ces verbes, et aucun formulaire d'ajout
    (action en /ajouter) ne vit dans un <details>."""
    ecarts = []
    for f in _gabarits():
        texte = f.read_text(encoding="utf-8")
        for s in re.findall(r"<summary[^>]*>(.*?)</summary>", texte, flags=re.S):
            if re.match(r"\s*(?:<[^>]+>\s*)*(?:Ajouter|Noter)\b", s):
                ecarts.append(f"{f.name} : accordéon « {re.sub(r'<[^>]+>', '', s).strip()} »")
        for bloc in re.findall(r"<details\b.*?</details>", texte, flags=re.S):
            if re.search(r'action="[^"]*/ajouter"', bloc):
                ecarts.append(f"{f.name} : formulaire d'ajout dans un <details>")
    assert not ecarts, "Ajout en accordéon (utiliser bouton_ajout + modale_ajout) : " + " ; ".join(ecarts)
