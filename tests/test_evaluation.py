from conftest import annonce
from jobsearch import evaluation
from jobsearch.moteur import FauxMoteur, MoteurImpossible
from jobsearch.evaluation import Alerte, Critere, Evaluation, Exigence, Salaire


def _ev(note=82, alertes=()):
    return Evaluation(
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


def test_montants_retires_du_profil():
    t = "# Critères\n## Non négociables\n- Trajet\n## Conditions matérielles\n- Plancher : 999'000\n## Lire une annonce\n- Signaux"
    out = evaluation._sans_conditions_materielles(t)
    assert "999" not in out and "Trajet" in out and "Signaux" in out
