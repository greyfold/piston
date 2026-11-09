import pytest

from conftest import annonce
from jobsearch import courrier


def msg(objet, texte="", expediteur="RH <rh@exemple.ch>", ident=None):
    return {"subject": objet, "content_preview": texte, "sender": expediteur, "account": "iCloud",
            "received_date": "2026-10-20T10:00:00", "internet_message_id": ident or objet}


@pytest.mark.parametrize("objet,texte,classe", [
    ("Capgemini Group - Application Status Update", "Unfortunately we decided to move forward with other candidates.", "refus"),
    ("Concerne votre candidature", "Nous ne pouvons malheureusement pas donner suite à votre candidature.", "refus"),
    ("Invitation: Interview with Sonar @ Tue Oct 13", "", "entretien"),
    ("Votre entretien", "Nous vous proposons de vous rencontrer mardi.", "entretien"),
    ("Thank you for your application with PMI", "We have received your application.", "accuse"),
    ("Votre candidature", "Nous avons bien reçu votre candidature. Malheureusement, nous avons retenu un autre profil.", "refus"),
    ("Offre", "Nous sommes heureux de vous proposer le poste ; vous trouverez la lettre d'engagement jointe.", "offre"),
])
def test_classer(objet, texte, classe):
    assert courrier.classer(objet, texte)[0] == classe


@pytest.mark.parametrize("objet,texte", [
    ("New jobs posted from Capgemini Group", "See the latest jobs."),
    ("Nexthink recrute au poste de Senior Product Manager", ""),
    ("Pas encore envoyée : votre candidature pour le poste de Spécialiste CRM", ""),
    ("Re: Home-smart transfert du nom de domaine", "Bonjour, nous avons bien reçu votre demande."),
    ("Vous avez partagé certaines données de votre compte Google avec Claude", ""),
])
def test_ce_ne_sont_pas_des_reponses(reg, objet, texte):
    annonce(reg, entreprise="Infomaniak", titre="Product Owner", statut="candidature", postulee_le="2026-09-01")
    annonce(reg, entreprise="Google", titre="UX Lead", statut="candidature", postulee_le="2026-07-01")
    annonce(reg, entreprise="Capgemini", titre="Manager", statut="candidature", postulee_le="2026-09-20")
    assert courrier.lie_aux_postes(msg(objet, texte, "Infomaniak <support@infomaniak.com>"),
                                   reg.liste(["candidature"])) is None


def test_refus_applique_et_message_non_lie_oublie(reg):
    e = annonce(reg, entreprise="Capgemini", titre="Manager - Business Technology", statut="candidature",
                postulee_le="2026-09-20", jobroom_saisie_le="2026-09-21")
    bilan = courrier.traiter(reg, [
        msg("Capgemini Group - Application Status Update",
            "Unfortunately, we have decided to move forward with other candidates.", "Capgemini <noreply@capgemini.com>"),
        msg("Votre facture du mois", "Montant dû.", "Opérateur <factures@exemple.ch>"),
    ])
    assert bilan["appliques"] == 1 and bilan["ignores"] == 1
    f = reg.get(e)
    assert f["statut"] == "refus" and f["jobroom_maj"] == "a_faire"
    assert reg.cx.execute("SELECT COUNT(*) FROM courriers").fetchone()[0] == 1   # la facture n'est pas gardée
    assert courrier.traiter(reg, [msg("Capgemini Group - Application Status Update",
                                      "Unfortunately, we have decided to move forward with other candidates.",
                                      "Capgemini <noreply@capgemini.com>")])["deja_vus"] == 1


def test_offre_et_ambigu_vont_a_trancher(reg):
    e = annonce(reg, entreprise="Swissquote Bank SA", titre="Product Manager", statut="candidature",
                postulee_le="2026-10-01")
    courrier.traiter(reg, [msg("Swissquote - votre candidature",
                               "Nous sommes heureux de vous proposer le poste, voir la lettre d'engagement.",
                               "Swissquote <jobs@swissquote.ch>")])
    assert reg.get(e)["statut"] == "candidature"
    assert [c["classe"] for c in courrier.a_trancher(reg)] == ["offre"]


def test_decision_du_tableau_de_bord_l_emporte(reg):
    e = annonce(reg, entreprise="Swissquote Bank SA", titre="Product Manager", statut="refus",
                postulee_le="2026-10-01")
    courrier.traiter(reg, [msg("Swissquote - votre candidature", "Nous vous proposons de vous rencontrer : votre entretien.",
                               "Swissquote <jobs@swissquote.ch>")])
    assert reg.get(e)["statut"] == "refus"


def test_seul_l_outil_de_recherche_est_appele():
    assert courrier.OUTIL_AUTORISE == "search_emails"


def test_meme_employeur_departage_par_l_intitule(reg):
    annonce(reg, entreprise="Banque Pictet & Cie SA", titre="Transformation Manager", statut="candidature", postulee_le="2026-10-20")
    e = annonce(reg, entreprise="Pictet Asset Management", titre="Distribution Change & Adoption Lead (12-month fixed-term contract)",
                statut="candidature", postulee_le="2026-10-01")
    m = msg("Your application at Pictet", "Thank you for applying for the position of Distribution Change & Adoption Lead.")
    assert [f["empreinte"] for f in courrier.rattacher(m, reg.liste(["candidature"]))] == [e]


@pytest.mark.parametrize("texte,motif", [
    ("Unfortunately, the position has already been filled.", "Poste pourvu"),
    ("Malheureusement, le recrutement a été suspendu.", "Poste annulé ou suspendu"),
    ("We are currently looking at other candidates whose skills are a closer match.", "Autre candidature retenue"),
    ("Votre profil ne correspond pas aux exigences du poste.", "Profil ne correspondant pas aux exigences du poste"),
    ("Après analyse, votre dossier n'a malheureusement pas été retenu.", courrier.AUCUN_MOTIF),
])
def test_motif_refus(texte, motif):
    m, phrase = courrier.motif_refus("Votre candidature", "Bonjour Camille,\n\n" + texte + "\n\nMeilleures salutations")
    assert m == motif and phrase == texte or (motif == courrier.AUCUN_MOTIF and phrase == texte)
    assert courrier.motif_court(courrier.texte_motif("Votre candidature", texte, "2026-10-29")) == motif


def test_une_hypothese_n_est_pas_un_refus():
    # Accusé de réception de Sonar (29.09), pris à tort pour un refus le 24.10.
    texte = ("Hi Camille,   Thank you for your interest in Sonar! We wanted to let you know that we have received "
             "your application for the CVP of Design role.   If you are not selected for this specific position, "
             "keep an eye on our jobs page!")
    assert courrier.classer("Thank you for your application to Sonar", texte)[0] == "accuse"


def test_refus_note_le_motif_du_mail(reg):
    e = annonce(reg, entreprise="Swissquote Bank SA", titre="Product Manager", statut="candidature",
                postulee_le="2026-10-01", jobroom_saisie_le="2026-10-02")
    courrier.traiter(reg, [msg("Your application at Swissquote", "Dear Camille,  Unfortunately, the position "
                               "has already been filled.  Kind regards", "Swissquote <jobs@swissquote.ch>")])
    f = reg.get(e)
    assert f["statut"] == "refus" and f["jobroom_maj"] == "a_faire"
    assert f["motif_refus"] == "Poste pourvu · « Unfortunately, the position has already been filled. » (mail du 2026-10-20)"


def test_completer_les_motifs_des_anciens_refus(reg):
    e = annonce(reg, entreprise="Pictet", titre="Product Owner", statut="candidature", postulee_le="2026-10-01")
    m = msg("Your application at Pictet", "Unfortunately we decided to move forward with other candidates.",
            "Pictet <jobs@pictet.com>")
    courrier.traiter(reg, [m])
    reg.maj(e, motif_refus="Refus reçu par e-mail le 2026-10-20")       # noté avant la lecture du motif
    reg.commit()
    assert courrier.completer_motifs(reg, lire=lambda *a: [m], essai=True)[0][2].startswith("Autre candidature")
    assert reg.get(e)["motif_refus"].startswith("Refus reçu")
    courrier.completer_motifs(reg, lire=lambda *a: [m])
    assert reg.get(e)["motif_refus"].startswith("Autre candidature retenue · « Unfortunately")
    assert courrier.completer_motifs(reg, lire=lambda *a: [m]) == []           # rien à refaire


JOBUP = ("Bonjour Camille,, Votre candidature pour le poste de Consultant.e IA a été reçue. L'équipe de recrutement "
         "a reçu votre candidature. Offres d'emploi similaires Business Analyst IA SpeciTec SA, Genève "
         "Stagiaire - Data, IA & Développement Losinger Marazzi, Zürich")


def _ouvertes(reg):
    annonce(reg, entreprise="Losinger Marazzi SA", titre="Coach IA 80-100%", statut="candidature", postulee_le="2026-10-20",
            url="https://www.jobs.ch/fr/offres/1")
    e = annonce(reg, entreprise="Employeur non divulgué sur Jobup", titre="Consultant.e IA", statut="candidature",
                postulee_le="2026-11-03", url="https://www.jobup.ch/fr/emplois/detail/57b6/")
    return e, reg.liste(["candidature"])


def test_les_offres_similaires_ne_rattachent_pas(reg):
    e, ouvertes = _ouvertes(reg)
    m = msg("Votre candidature en tant que Consultant.e IA a été envoyée", JOBUP, "Service jobup.ch <application_no_reply@jobup.ch>")
    m["received_date"] = "2026-11-03T09:32:28"
    assert courrier.rattacher(m, ouvertes) == []                         # Losinger n'est que recommandé
    classement = courrier.suggestions(m["subject"], JOBUP, m["sender"], "2026-11-03", ouvertes)
    assert courrier.la_plus_probable(classement)["empreinte"] == e
    assert "intitulé du poste" in classement[0][2] and "même site que l'annonce" in classement[0][2]
    assert courrier.classer(m["subject"], courrier.sans_recommandations(JOBUP))[0] == "accuse"
    courrier.traiter(reg, [m])                                           # l'accusé s'applique tout seul
    assert reg.get(e)["accuse_le"] == "2026-11-03" and courrier.a_trancher(reg) == []


@pytest.mark.parametrize("objet,texte", [
    ("Candidature spontanée", "Nous vous remercions d'avoir postulé à l'offre N° 6120. Votre candidature sera examinée avec la plus grande attention."),
    ("Votre candidature en tant que Consultant.e IA a été envoyée", ""),
])
def test_accuses_des_plateformes(objet, texte):
    assert courrier.classer(objet, texte)[0] == "accuse"


def test_sans_signal_net_rien_n_est_preselectionne(reg):
    _, ouvertes = _ouvertes(reg)
    classement = courrier.suggestions("Votre candidature", "Merci.", "rh@ailleurs.ch", "2026-11-04", ouvertes)
    assert courrier.la_plus_probable(classement) is None
