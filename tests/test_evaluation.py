from conftest import annonce
from jobsearch import adequation, evaluation
from jobsearch.adequation import Categorie
from jobsearch.moteur import FauxMoteur, MoteurImpossible
from jobsearch.evaluation import Alerte, Critere, Evaluation, Exigence, Salaire


CATEGORIES = (
    Categorie(categorie="langues", demande=True, note=140, synthese="Anglais courant demandé, acquis.",
              atouts=["Anglais C1 (certification)"], ecarts=[]),
    Categorie(categorie="competences", demande=True, note=85, synthese=" Feuille de route produit et IA, couvertes. ",
              atouts=["Feuille de route produit (P-03)", " "], ecarts=["Pas de MLOps"]),
    Categorie(categorie="experience", demande=True, note=55, synthese="Huit ans demandés, six au profil.",
              atouts=[], ecarts=["Huit ans demandés", "b", "c", "d", "e"]),
    Categorie(categorie="formation", demande=False, note=70, synthese="Aucun diplôme exigé.",
              atouts=["Master"], ecarts=[]),
    Categorie(categorie="competences", demande=True, note=10, synthese="Doublon.", atouts=[], ecarts=[]),
)


def _ev(note=82, alertes=(), perimetre="dans", perimetre_motif="Poste produit, dans les postes visés.", categories=None):
    return Evaluation(
        perimetre=perimetre, perimetre_motif=perimetre_motif,
        note_globale=note, adequation_profil=70, angle="Produit et IA",
        pour=["Poste produit et IA", " "], contre=[], arbitrage="Le profil couvre l'essentiel.",
        resume="Bon alignement produit et IA.",
        criteres=[Critere(critere="langue", etat="ok", note="Anglais suffit.")],
        exigences=[Exigence(exigence="Own the AI product roadmap", couverture="oui", categorie="experience", appui="P-03")],
        categories=list(categories if categories is not None else CATEGORIES),
        alertes=list(alertes),
        salaire=Salaire(annonce_l_indique=False, minimum=130000, maximum=150000, extrait="",
                        raisonnement="Poste senior produit, grand groupe, Lausanne.", confiance="moyenne"))


def test_evaluer_enregistre_et_ne_refait_pas(reg):
    e = annonce(reg, statut="A", texte="We are looking for an AI Product Manager.", texte_origine="plateforme")
    alerte = Alerte(gravite="moyenne", signal="Disponibilité permanente attendue", extrait="available 24/7")
    c = FauxMoteur([_ev(alertes=[alerte])])
    bilan = evaluation.evaluer_lot(reg, evaluation.a_evaluer(reg, texte_profil="P"), c=c, texte_profil="P")
    assert bilan["evaluees"] == 1 and bilan["alertes"] == 1
    r = evaluation.lire(reg, e)
    assert r["note_globale"] == 82 and r["adequation_profil"] == 70 and r["nb_alertes"] == 1
    assert r["resultat"]["exigences"][0]["exigence"] == "Own the AI product roadmap"
    assert evaluation.a_evaluer(reg, texte_profil="P") == []           # à jour : pas de nouvel appel
    assert evaluation.a_evaluer(reg, texte_profil="P modifié") != []   # profil changé : à refaire
    assert reg.get(e)["statut"] == "A"                                  # la note ne décide rien


def test_pre_selection_ia_ecarte_un_autre_metier(reg):
    """Constat du 13.11 : cheffe de cuisine et infirmier parmi les annonces à trancher. L'IA écarte
    une « nouvelle » hors des postes visés (C, passe 2, limite : visible et réversible) ; le doute
    (« limite ») et les annonces déjà classées ne bougent pas."""
    cuisine = annonce(reg, titre="Cheffe de cuisine", entreprise="A Resto", texte="Brigade de 12, cuisine de saison.")
    voisin = annonce(reg, titre="Chef de projet SI", entreprise="B Conseil", texte="Pilotage de projets informatiques.")
    classee = annonce(reg, titre="Chef de partie", entreprise="C Brigade", statut="B", texte="Cuisine.")   # le lot suit l'ordre des entreprises
    c = FauxMoteur([_ev(perimetre="hors", perimetre_motif="Poste de cuisine en restauration."),
                    _ev(perimetre="limite"), _ev(perimetre="hors", perimetre_motif="Cuisine.")])
    bilan = evaluation.evaluer_lot(reg, evaluation.a_evaluer(reg, texte_profil="P"), c=c, texte_profil="P")
    assert bilan["evaluees"] == 3 and bilan["ecartees"] == 1
    f = reg.get(cuisine)
    assert f["statut"] == "C" and f["passe_ecart"] == evaluation.PASSE_IA and f["limite"]
    assert f["motif"] == "Métier hors des postes visés, selon l'IA : Poste de cuisine en restauration."
    assert "pré-sélection IA" in reg.journal(cuisine)[-1]["texte"]
    assert reg.get(voisin)["statut"] == "nouvelle" and reg.get(classee)["statut"] == "B"
    assert evaluation.lire(reg, cuisine)["resultat"]["perimetre"] == "hors"       # la note reste lisible


def test_consigne_nomme_les_postes_vises_et_le_perimetre():
    consigne = evaluation.consigne()
    assert "Postes visés : UX Lead, Head of Design" in consigne           # ceux de l'installation d'exemple
    assert "perimetre" in consigne and "jamais « hors » sur un doute" in consigne
    schema = evaluation.modele().model_json_schema()
    assert set(["perimetre", "perimetre_motif"]) <= set(schema["required"])


def test_tache_cadree_profil_en_contexte(reg):
    annonce(reg, statut="B", entreprise="Exemple SA", texte="Texte de l'annonce.")
    c = FauxMoteur([_ev()])
    evaluation.evaluer_lot(reg, evaluation.a_evaluer(reg, texte_profil="P"), c=c, texte_profil="P")
    tache, message = c.appels[0]
    assert tache.modele == "claude-opus-5-5" and tache.effort == "high"
    assert tache.contexte.startswith("P") and "Tu évalues des offres" in tache.consigne   # fixe : en cache
    assert issubclass(tache.schema, Evaluation)                # critères et angles du profil dans le schéma
    assert tache.outils == [] and tache.autorises == [] and tache.sans_reglages      # aucun outil, aucun réglage
    assert "Exemple SA" in message and "Texte de l'annonce." in message and "Exemple SA" not in tache.contexte
    jetons = __import__("json").loads(reg.cx.execute("SELECT cout_jetons FROM evaluations").fetchone()[0])
    assert jetons["input_tokens"] == 900


def test_refus_compte_comme_echec(reg):
    annonce(reg, statut="B", texte="Texte.")
    bilan = evaluation.evaluer_lot(reg, evaluation.a_evaluer(reg, texte_profil="P"),
                                   c=FauxMoteur([MoteurImpossible("évaluation : refus du modèle")]), texte_profil="P")
    assert bilan["evaluees"] == 0 and bilan["echecs"] == 1


def test_claude_deconnecte_arrete_le_lot(reg):
    for i in range(3):
        annonce(reg, statut="B", entreprise="E%d" % i, texte="Texte.")
    c = FauxMoteur([MoteurImpossible("Claude Code n'est pas connecté", bloquant=True), _ev(), _ev()])
    bilan = evaluation.evaluer_lot(reg, evaluation.a_evaluer(reg, texte_profil="P"), c=c, texte_profil="P")
    assert bilan["echecs"] == 1 and bilan["evaluees"] == 0 and len(c.appels) == 1


def test_adequation_par_categorie_verifiee_avant_d_etre_ecrite(reg):
    """L'analyse par catégorie : Python ne garde qu'une entrée par catégorie, dans l'ordre fixe, note
    bornée, quatre points au plus ; une catégorie où l'annonce n'exige rien n'a ni note ni points."""
    e = annonce(reg, statut="A", texte="We are looking for an AI Product Manager.")
    evaluation.evaluer_lot(reg, evaluation.a_evaluer(reg, texte_profil="P"), c=FauxMoteur([_ev()]), texte_profil="P")
    resultat = evaluation.lire(reg, e)["resultat"]
    assert [c["categorie"] for c in resultat["categories"]] == list(adequation.CLES)
    cats = {c["categorie"]: c for c in adequation.lire(resultat)}
    assert cats["langues"]["note"] == 100 and cats["langues"]["niveau"] == "haute"
    assert cats["competences"]["note"] == 85 and cats["competences"]["atouts"] == ["Feuille de route produit (P-03)"]
    assert cats["competences"]["synthese"] == "Feuille de route produit et IA, couvertes."
    assert cats["experience"]["niveau"] == "basse" and len(cats["experience"]["ecarts"]) == adequation.MAX_POINTS
    assert not cats["formation"]["demande"] and cats["formation"]["note"] == 0 and cats["formation"]["atouts"] == []
    assert cats["experience"]["libelle"] == "Expérience"
    assert adequation.resume(resultat) == "compétences 85 · expérience 55 · langues 100"
    assert resultat["exigences"][0]["categorie"] == "experience"


def test_consigne_et_schema_portent_les_categories():
    consigne = evaluation.consigne()
    for cle in adequation.CLES:
        assert cle in consigne
    assert "%(" not in consigne
    schema = evaluation.modele().model_json_schema()
    assert "categories" in schema["required"]
    assert schema["$defs"]["Categorie"]["properties"]["categorie"]["enum"] == list(adequation.CLES)


def test_evaluation_d_avant_l_analyse_reste_lisible():
    assert adequation.lire({"note_globale": 80}) == [] and adequation.lire(None) == []
    assert adequation.resume({}) == ""


def test_avis_oui_peut_etre_non_autour_de_50():
    """20.11 : plus de A/B/C. L'avis se tire de la moyenne de la note et du profil : oui dès 50 % ; un peu
    en dessous, ou des chiffres qui divergent (profil haut, note basse), peut-être ; le reste, non."""
    avis = lambda g, p, crit=(): evaluation.avis(g, p, crit)["cle"]
    assert avis(68, 60) == "oui" and avis(50, 50) == "oui"
    assert avis(38, 55) == "peut-etre" and avis(45, 40) == "peut-etre"
    assert avis(30, 75) == "peut-etre" and evaluation.avis(30, 75)["divergence"]   # profil haut, note basse
    assert avis(35, 70) == "peut-etre"                                              # moyenne 52, un chiffre bas
    assert avis(34, 25) == "non" and avis(10, 20) == "non"
    assert avis(70, 70, [{"etat": "ko"}]) == "peut-etre"                            # critère violé : jamais oui d'office
    a = evaluation.avis(68, 60)
    assert (a["classe"], a["libelle"], a["moyenne"]) == ("A", "Oui", 64)


def test_avis_non_ecarte_la_nouvelle_mais_pas_le_peut_etre(reg):
    """Ce qui est non n'est plus proposé : passe 2, limite, consultable dans Offres et réversible ; le
    peut-être reste à trancher avec ses raisons."""
    non = annonce(reg, titre="Assistant administratif", entreprise="A Bureau", texte="Saisie.")
    doute = annonce(reg, titre="Chef de projet SI", entreprise="B Conseil", texte="Projets.")
    ev_non, ev_doute = _ev(note=20), _ev(note=30)          # profil 70 : moyennes 45 et 50
    ev_non.adequation_profil = 25
    ev_doute.adequation_profil = 65
    c = FauxMoteur([ev_non, ev_doute])
    bilan = evaluation.evaluer_lot(reg, evaluation.a_evaluer(reg, texte_profil="P"), c=c, texte_profil="P")
    assert bilan["ecartees"] == 1
    f = reg.get(non)
    assert f["statut"] == "C" and f["passe_ecart"] == evaluation.PASSE_IA and f["limite"]
    assert f["motif"].startswith("Avis non : moyenne 22 %")
    assert reg.get(doute)["statut"] == "nouvelle"
    lue = evaluation.lire(reg, doute)
    assert lue["avis"]["cle"] == "peut-etre" and lue["avis"]["divergence"]
    assert lue["resultat"]["pour"] == ["Poste produit et IA"] and lue["resultat"]["arbitrage"]
