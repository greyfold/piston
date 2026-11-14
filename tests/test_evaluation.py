from conftest import annonce
from jobsearch import evaluation
from jobsearch.moteur import FauxMoteur, MoteurImpossible
from jobsearch.evaluation import Alerte, Critere, Evaluation, Exigence, Salaire


def _ev(note=82, alertes=(), perimetre="dans", perimetre_motif="Poste produit, dans les postes visés."):
    return Evaluation(
        perimetre=perimetre, perimetre_motif=perimetre_motif,
        note_globale=note, adequation_profil=70, classe_suggeree="A", angle="Produit et IA",
        resume="Bon alignement produit et IA.",
        criteres=[Critere(critere="langue", etat="ok", note="Anglais suffit.")],
        exigences=[Exigence(exigence="Own the AI product roadmap", couverture="oui", appui="P-03")],
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
