import pytest

from conftest import annonce
from jobsearch import actions
from jobsearch.actions import ActionRefusee, appliquer


def test_go_passe_en_retenue_avec_raisons(reg):
    e = annonce(reg, statut="A")
    appliquer(reg, e, "go", {"raisons": ["angle fort"], "note": "belle boîte"})
    f = reg.get(e)
    assert f["statut"] == "retenue"
    assert f["raison_decision"] == ["angle fort"] and f["raison_note"] == "belle boîte"
    assert f["decision_le"]


def test_go_contradictoire_refuse_cas_deloitte(reg):
    e = annonce(reg, statut="B")
    with pytest.raises(ActionRefusee):
        appliquer(reg, e, "go", {"note": "pas go, trop loin"})
    assert reg.get(e)["statut"] == "B"
    trace = reg.cx.execute("SELECT resultat FROM actions").fetchall()
    assert [t[0] for t in trace] == ["refusee"]


def test_tri_refuse_sur_candidature_partie(reg):
    e = annonce(reg, statut="candidature", postulee_le="2026-10-01")
    for t in ("go", "pasgo", "attendre"):
        with pytest.raises(ActionRefusee):
            appliquer(reg, e, t)
    assert reg.get(e)["statut"] == "candidature"


def test_marquer_envoyee_pose_relance_j10(reg):
    e = annonce(reg, statut="retenue")
    appliquer(reg, e, "candidature", {"date": "2026-10-20", "canal": "portail"})
    f = reg.get(e)
    assert (f["statut"], f["postulee_le"], f["relance_le"], f["canal_envoi"]) == \
        ("candidature", "2026-10-20", "2026-10-30", "portail")


def test_rejouer_go_ne_change_rien(reg):
    e = annonce(reg, statut="A")
    appliquer(reg, e, "go")
    avant = reg.get(e)
    appliquer(reg, e, "go")
    apres = reg.get(e)
    assert {k: v for k, v in avant.items() if k != "maj_le"} == {k: v for k, v in apres.items() if k != "maj_le"}


def test_refus_apres_preuve_jobroom_demande_le_report(reg):
    e = annonce(reg, statut="candidature", postulee_le="2026-10-01", jobroom_saisie_le="2026-10-02")
    appliquer(reg, e, "refus", {"motif": "autre profil retenu"})
    f = reg.get(e)
    assert f["statut"] == "refus" and f["jobroom_maj"] == "a_faire" and f["motif_refus"] == "autre profil retenu"
    appliquer(reg, e, "jobroom_refus")
    assert reg.get(e)["jobroom_maj"] == "fait"


def test_pas_de_preuve_jobroom_sans_envoi(reg):
    e = annonce(reg, statut="A")
    with pytest.raises(ActionRefusee):
        appliquer(reg, e, "jobroom_saisie")


def test_deux_relances_puis_silence(reg):
    e = annonce(reg, statut="candidature", postulee_le="2026-10-01")
    appliquer(reg, e, "relance_faite")
    assert reg.get(e)["prochaine_action"].startswith("Seconde relance")
    appliquer(reg, e, "relance_faite")
    f = reg.get(e)
    assert f["prochaine_action"] == "Considérer comme sans réponse" and f["relance_le"] is None


def test_date_illisible_refusee(reg):
    e = annonce(reg, statut="retenue")
    with pytest.raises(ActionRefusee):
        appliquer(reg, e, "candidature", {"date": "20.10.2026"})
    assert reg.get(e)["statut"] == "retenue"


def test_type_inconnu(reg):
    e = annonce(reg)
    with pytest.raises(ActionRefusee):
        appliquer(reg, e, "envoyer_tout")
    assert "envoyer_tout" not in actions.TYPES


def test_sans_suite_ne_touche_pas_jobroom(reg):
    e = annonce(reg, statut="candidature", postulee_le="2026-08-01", jobroom_saisie_le="2026-08-02")
    appliquer(reg, e, "sans_suite")
    f = reg.get(e)
    assert f["statut"] == "refus" and f["jobroom_maj"] == "non_concerne" and f["reponse_le"] is None


def test_annuler_un_go(reg):
    e = annonce(reg, statut="retenue", cv_genere=True, dossier="2026-10-25 - X")
    appliquer(reg, e, "annuler_go", {"raisons": ["exigence que je n'ai pas"], "note": "allemand exigé"})
    f = reg.get(e)
    assert f["statut"] == "C" and "Oui annulé" in f["motif"] and "allemand exigé" in f["motif"]
    assert f["raison_decision"] == ["exigence que je n'ai pas"] and f["dossier"] == "2026-10-25 - X"
    with pytest.raises(ActionRefusee):
        appliquer(reg, e, "annuler_go")                       # plus retenue


def test_go_remis_a_trancher(reg):
    e = annonce(reg, statut="retenue")
    appliquer(reg, e, "attendre")
    assert reg.get(e)["statut"] == "B"


def test_pas_d_annulation_apres_envoi(reg):
    e = annonce(reg, statut="candidature", postulee_le="2026-10-20")
    with pytest.raises(ActionRefusee):
        appliquer(reg, e, "annuler_go")


def test_annuler_un_refus_note_a_tort(reg):
    e = annonce(reg, statut="candidature", postulee_le="2026-06-04")
    actions.appliquer(reg, e, "refus", {"date": "2026-09-29", "motif": "Refus reçu par e-mail le 2026-09-29"})
    actions.appliquer(reg, e, "annuler_refus", {"note": "accusé de réception pris pour un refus"})
    f = reg.get(e)
    assert f["statut"] == "candidature" and not f["motif_refus"] and not f["reponse_le"] and not f["jobroom_maj"]
    assert f["relance_le"] == "2026-06-14"
    with pytest.raises(actions.ActionRefusee):
        actions.appliquer(reg, e, "annuler_refus", {})              # plus un refus


def test_refus_reporte_sur_jobroom_ne_s_annule_pas_ici(reg):
    e = annonce(reg, statut="candidature", postulee_le="2026-06-04", jobroom_saisie_le="2026-06-05")
    actions.appliquer(reg, e, "refus", {"date": "2026-09-29"})
    actions.appliquer(reg, e, "jobroom_refus", {})
    with pytest.raises(actions.ActionRefusee):
        actions.appliquer(reg, e, "annuler_refus", {})


def test_employeur_non_divulgue_et_adresse_saisis(reg):
    from jobsearch.navigateur import valeurs_jobroom
    e = annonce(reg, entreprise="?", titre="Consultant.e IA", lieu="Genève", statut="candidature", postulee_le="2026-11-03")
    with pytest.raises(actions.ActionRefusee, match="manquant"):
        actions.appliquer(reg, e, "employeur", {"ville": "Genève"})
    with pytest.raises(actions.ActionRefusee, match="NPA"):
        actions.appliquer(reg, e, "employeur", {"entreprise": "X", "npa": "12", "ville": "Genève"})
    actions.appliquer(reg, e, "employeur", {"entreprise": "Employeur non divulgué sur Jobup", "npa": "1201", "ville": "Genève"})
    f = reg.get(e)
    assert f["entreprise"] == "Employeur non divulgué sur Jobup" and f["statut"] == "candidature"
    v = valeurs_jobroom(f, "")
    assert (v["npa"], v["ville"]) == ("1201", "Genève") and "rue" not in v
    assert any("employeur · Employeur non divulgué sur Jobup · 1201 Genève" in j["texte"] for j in reg.journal(e))


def _ompi(reg):
    """La candidature OMPI du 29.10, et la même offre ajoutée le 05.11 sous le nom français."""
    a = annonce(reg, entreprise="World Intellectual Property Organization", titre="UX Lead", statut="candidature",
                postulee_le="2026-10-29", jobroom_saisie_le="2026-11-03")
    b = annonce(reg, entreprise="Organisation Mondiale de la Propriété Intellectuelle (OMPI / WIPO)",
                titre="UX Lead", statut="B")
    return a, b


def test_go_refuse_sur_un_poste_deja_postule(reg):
    a, b = _ompi(reg)
    with pytest.raises(ActionRefusee, match="même poste déjà engagé"):
        appliquer(reg, b, "go")
    assert reg.get(b)["statut"] == "B"


def test_envoi_du_meme_poste_note_avec_alerte_et_preuve_refusee(reg):
    from jobsearch import followups
    a, b = _ompi(reg)
    reg.maj(b, statut="retenue")
    reg.commit()
    msg = appliquer(reg, b, "candidature", {"date": "2026-11-11"})
    assert "même poste déjà engagé" in msg and reg.get(b)["statut"] == "candidature"
    with pytest.raises(ActionRefusee, match="déjà saisie pour le même poste"):
        appliquer(reg, b, "jobroom_saisie")
    jr = followups.jobroom(reg)
    assert b not in [f["empreinte"] for f in jr["a_saisir"] + jr["a_verifier"]]
    assert [f["empreinte"] for f in jr["meme_poste"]] == [b]


def test_paire_dite_differente_libere_le_go(reg):
    from jobsearch import doublons
    a, b = _ompi(reg)
    doublons.differentes(reg, a, b)
    appliquer(reg, b, "go")
    assert reg.get(b)["statut"] == "retenue"
