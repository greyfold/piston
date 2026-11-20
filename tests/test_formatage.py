from jobsearch.formatage import annonce_html, blocs, nettoyer
from jobsearch.sources.base import html_vers_texte


def genres(texte):
    return [g for g, _ in blocs(texte)]


def test_texte_de_plateforme_intertitres_et_puces():
    t = ("Your team\n\nWe provide custody and fund services for institutional clients worldwide.\n\n"
         "Your role\n\n• Provide functional support.\n• Coordinate incident follow-up.\n\n"
         "Note\nFH/TM/ASAS\nWe will not accept any CVs via agencies\n\n"
         "Diversity & Inclusion\nPictet is an equal opportunity employer and is committed to it.")
    b = blocs(t)
    assert genres(t) == ["h", "p", "h", "ul", "p", "p", "p", "h", "p"]
    assert b[3][1] == ["Provide functional support.", "Coordinate incident follow-up."]


def test_une_ligne_par_paragraphe():
    t = "The ICRC manages vast amounts of information.\nThe Head of Information Governance leads the strategy."
    assert genres(t) == ["p", "p"]


def test_ligne_coupee_recollee():
    assert genres("Nous cherchons une personne capable de piloter\nla stratégie produit.") == ["p"]


def test_resume_a_rubriques_de_l_ancienne_chaine():
    t = ("SICPA SA, Prilly. CDI 100 %. Entreprise technologique suisse fondée en 1927 et active dans "
         "la sécurisation des billets, des documents et des recettes publiques depuis longtemps. "
         "ROLE : définir le backlog ; collaborer avec les clients ; traduire les besoins ; porter la roadmap. "
         "PROFIL : 5 ans comme Product Owner ; Agile ; anglais courant.")
    b = blocs(t)
    assert [g for g, _ in b] == ["p", "h", "ul", "h", "ul"]
    assert b[1][1] == "Role" and len(b[2][1]) == 4 and b[4][1][-1] == "anglais courant"


def test_markdown_job_room():
    t = "**Description du poste**\n\nSous la responsabilité du Senior Director Product, vous jouerez un rôle clé."
    assert genres(t) == ["h", "p"]


def test_liste_html_jobup_reste_une_seule_liste():
    t = html_vers_texte("<h2>Vos missions</h2><ul><li><strong>Piloter</strong> la roadmap</li>"
                        "<li>Animer la discovery</li></ul><p>Rejoignez-nous.</p>")
    b = blocs(t)
    assert [g for g, _ in b] == ["h", "ul", "p"]
    assert b[1][1] == ["**Piloter** la roadmap", "Animer la discovery"]


def test_html_echappe_et_liens():
    h = str(annonce_html("Postuler sur https://exemple.ch/offre/42 <script>alert(1)</script>"))
    assert "<script>" not in h and "&lt;script&gt;" in h
    assert '<a href="https://exemple.ch/offre/42"' in h


def test_url_piegee_n_injecte_aucun_attribut():
    for piege in ('https://a.b/"onmouseover=alert(1)//x', "https://a.b/'onmouseover=alert(1)//x",
                  "https://a.b/`onmouseover=alert(1)//x"):
        h = str(annonce_html("Voir " + piege))
        assert h.count("<a ") == 1
        assert " onmouseover=" not in h.split("<a ", 1)[1].split(">", 1)[0]
        assert 'href="https://a.b/"' in h or "href=\"https://a.b/" in h


def test_url_normale_avec_esperluette_et_point_d_interrogation():
    h = str(annonce_html("Postuler : https://exemple.ch/offre?id=42&lang=fr."))
    assert '<a href="https://exemple.ch/offre?id=42&amp;lang=fr" target="_blank"' in h
    assert "&amp;amp;" not in h and h.endswith("</a>.</p>")


def test_etiquette_en_gras_et_titre_repete_retire():
    h = str(annonce_html("Product Owner\nTrade Execution: Partner directly with the Portfolio Manager on views.",
                         titre="Product Owner"))
    assert "<strong>Trade Execution :</strong>" in h and "<h3>Product Owner" not in h


def test_gras_coupe_par_un_retour_a_la_ligne():
    h = str(annonce_html("**STAT19\n\n**\n\nService de statistique (STAT)\nLe STAT constitue le pôle cantonal en matière de science des données."))
    assert "**" not in h and "STAT19" in h


def test_nettoyer_le_markdown_echappe_les_logos_et_le_suivi():
    """Constat du 20.11 : des textes Job-Room finissent par « jpid…jm jpit…jm jpiy…jm », avec du Markdown
    échappé aplati sur une ligne (« \\* », « \\[ \\](\\< \\>) », logos « ![Entreprise]( ) »)."""
    t = ("**Délai de postulation : 3 décembre 2026**\n\njpidc0c4873jm jpit1147jm jpiy26jm")
    assert nettoyer(t) == "**Délai de postulation : 3 décembre 2026**"
    t = ("Profil \\* Formation supérieure \\* Sept ans d'expérience \\*\\*Ce que nous offrons\\*\\* "
         "\\* Cinq semaines de vacances \\* Une LPP avantageuse \\#LI\\-JF1 !\\[Exemple SA\\]( ) "
         "\\* Exemple SA Route de France 16 \\* \\[ \\](\\< \\>) \\[Postuler\\](\\< \\>) jpid1cca9d5jm jpit1147jm")
    propre = nettoyer(t)
    assert "jpi" not in propre and "\\" not in propre and "![" not in propre and "#LI" not in propre
    assert "- Formation supérieure" in propre and "\n**Ce que nous offrons**\n" in propre
    assert propre.endswith("Postuler")
    html = str(annonce_html(t))
    assert "<h3>Ce que nous offrons</h3>" in html and "<li>Cinq semaines de vacances</li>" in html


def test_nettoyer_garde_le_texte_ordinaire():
    t = "Le poste * clé de l'équipe, 5 * 8 heures.\n\nUne [page](https://exemple.ch) reste un lien."
    assert nettoyer(t) == t
