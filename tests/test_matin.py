from conftest import annonce
from jobsearch import matin

HIER = "2026-11-01 07:00"


def test_bilan_ecarte_doublons_et_postes_deja_postules(reg):
    annonce(reg, entreprise="ACG", titre="Responsable transformation digitale", statut="candidature",
            cree_le="2026-10-20 06:40")
    neuve = annonce(reg, entreprise="Nexthink", titre="Product Manager", cree_le="2026-11-02 06:40")
    # Même poste republié sous un intitulé un peu différent (même intitulé exact : même empreinte,
    # l'ingestion le rattache déjà à la candidature et rien de nouveau n'est créé).
    redite = annonce(reg, entreprise="ACG SA", titre="Responsable de la transformation digitale – Lausanne",
                     sources=["indeed"], cree_le="2026-11-02 06:41")
    annonce(reg, entreprise="Richemont", titre="Product Owner", statut="doublon", cree_le="2026-11-02 06:42")
    annonce(reg, entreprise="Exemple SA", titre="AI Engineer", statut="C", cree_le="2026-11-02 06:43")
    annonce(reg, entreprise="Ancienne SA", titre="Product Lead", cree_le="2026-10-30 06:40")   # avant
    b = matin.bilan(reg, HIER)
    assert [f["empreinte"] for f in b["nouvelles"]] == [neuve]
    assert [(f["empreinte"], e["statut"]) for f, e in b["deja_postule"]] == [(redite, "candidature")]
    assert len(b["doublons"]) == 1 and len(b["ecartees"]) == 1 and b["total"] == 4


def test_autre_poste_chez_le_meme_employeur_reste_nouveau(reg):
    annonce(reg, entreprise="ACG", titre="Responsable transformation digitale", statut="candidature",
            cree_le="2026-10-20 06:40")
    autre = annonce(reg, entreprise="ACG", titre="Comptable", cree_le="2026-11-02 06:40")
    b = matin.bilan(reg, HIER)
    assert [f["empreinte"] for f in b["nouvelles"]] == [autre] and not b["deja_postule"]


def test_message():
    f = {"empreinte": "x", "entreprise": "Nexthink", "titre": "Product Manager", "delai_postulation": "2026-11-15"}
    e = {"titre": "Responsable transformation digitale", "statut": "candidature"}
    b = {"nouvelles": [f], "deja_postule": [({"entreprise": "ACG"}, e)], "doublons": [{}, {}], "ecartees": [{}]}
    titre, texte = matin.message(None, b, {"statut": "complet"})
    assert titre == "1 nouvelle offre ce matin"
    assert "• Nexthink — Product Manager · avant le 15.11" in texte
    assert "Déjà postulé ? 1 : ACG" in texte
    assert "2 déjà connues sur une autre plateforme, 1 écartée par le pré-tri" in texte
    titre, texte = matin.message(None, {"nouvelles": [], "deja_postule": [], "doublons": [], "ecartees": []},
                                 {"statut": "partiel", "resume": "jobup en panne"})
    assert titre == "Aucune nouvelle offre ce matin" and texte.startswith("⚠ Passage partiel : jobup en panne")


def test_alerter_envoie_et_note_la_periode(reg):
    annonce(reg, entreprise="Nexthink", titre="Product Manager", cree_le="2099-01-01 06:40")
    envois = []
    r = matin.alerter(reg, envoyer=lambda t, x, **k: envois.append((t, x)), attendre_min=0)
    assert r["nouvelles"] == 1 and envois[0][0] == "Piston · 1 nouvelle offre ce matin"
    assert "aucun passage ce matin" in envois[0][1]
    assert reg.meta("alerte_matin_le")
    # Le lendemain, la même annonce n'est plus comptée : la période part de l'alerte.
    reg.poser_meta("alerte_matin_le", "2099-01-02 07:00")
    assert matin.alerter(reg, envoyer=lambda t, x, **k: None, attendre_min=0)["nouvelles"] == 0


def test_attendre_passage(reg):
    class Horloge:
        t = 0.0
        def monotonic(self): return self.t
        def sleep(self, s):
            self.t += s
            if self.t >= 120:
                reg.cx.execute("INSERT INTO runs (debut, statut, resume) VALUES (?, 'complet', 'ok')",
                               (matin.maintenant(),))
    assert matin.attendre_passage(reg, minutes=90, horloge=Horloge())["statut"] == "complet"
    reg.cx.execute("DELETE FROM runs")
    h = Horloge(); h.sleep = lambda s: setattr(h, "t", h.t + s)
    assert matin.attendre_passage(reg, minutes=5, horloge=h) is None and h.t >= 300


def test_l_alerte_dit_ce_que_le_courrier_a_mis_a_jour(reg):
    from jobsearch import courrier
    annonce(reg, entreprise="Swissquote Bank SA", titre="Product Manager", statut="candidature", postulee_le="2026-10-01")
    courrier.traiter(reg, [{"subject": "Your application at Swissquote", "sender": "Swissquote <jobs@swissquote.ch>",
                            "content_preview": "Unfortunately, the position has already been filled.",
                            "received_date": "2026-11-02T08:00:00", "internet_message_id": "a"}])
    b = matin.bilan(reg, "2000-01-01 00:00")
    assert b["courrier"] == ["refus Swissquote Bank SA (poste pourvu)"]
    assert "Courrier : refus Swissquote Bank SA (poste pourvu)" in matin.message(reg, b, {"statut": "complet"})[1]
