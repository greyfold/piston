from conftest import annonce
from jobsearch import doublons
from jobsearch.ingest import ingerer
from jobsearch.sources.base import Annonce

TEXTE = " ".join("mot%d" % i for i in range(200))
AUTRE = " ".join("autre%d" % i for i in range(200))


def _f(**kw):
    base = {"empreinte": "x", "entreprise": "Exemple SA", "titre": "Product Manager", "statut": "C"}
    base.update(kw)
    return base


def test_cle_entreprise_sans_forme_juridique_ni_intermediaire():
    assert doublons.cle_entreprise("Logitech Europe S.A. (via ictjobs)") == doublons.cle_entreprise("Logitech")
    assert doublons.cle_entreprise("Michael Page Switzerland") == doublons.cle_entreprise("Michael Page")
    assert doublons.cle_entreprise("JT International AG (JTI)") >= doublons.cle_entreprise("JT International SA")


def test_niveaux():
    c = doublons.comparer
    assert c(_f(entreprise="Etel", titre="R&amp;D Electronics Engineer"),
             _f(entreprise="Etel S.A.", titre="R&D ELECTRONICS ENGINEER"))[0] == "sur"
    assert c(_f(titre="Career Opportunities: Business Process Manager"),
             _f(titre="Business Process Manager"))[0] == "sur"
    assert c(_f(titre="Business Process Manager (124903)"), _f(titre="Business Process Manager"))[0] == "sur"
    # Numéros contradictoires : deux postes.
    assert c(_f(titre="Poste à préciser (envoi du 09.07)"), _f(titre="Poste à préciser (envoi du 23.03)"))[0] is None
    # Un mot qui change le poste : à vérifier, pas fusionné.
    assert c(_f(titre="Senior Product Manager, Platform"), _f(titre="Product Manager"))[0] == "a_verifier"
    # Textes complets sans rien en commun : deux postes.
    assert c(_f(titre="Senior Product Manager", texte=TEXTE), _f(titre="Product Manager", texte=AUTRE))[0] is None
    # Même intitulé, textes différents selon la plateforme (résumé Indeed) : la même offre.
    assert c(_f(entreprise="Richemont", titre="Product Development Product Owner", texte=TEXTE),
             _f(entreprise="Richemont International SA", titre="Product Development Product Owner", texte=AUTRE))[0] == "sur"
    # Employeurs différents.
    assert c(_f(entreprise="Nexthink SA"), _f(entreprise="Nestlé SA"))[0] is None


def test_meme_intitule_mais_deux_cantons_ou_deux_candidatures():
    assert doublons.comparer(_f(canton="VD"), _f(canton="GE"))[0] == "a_verifier"
    assert doublons.comparer(_f(statut="candidature"), _f(statut="refus"))[0] == "a_verifier"
    assert doublons.comparer(_f(statut="candidature"), _f(statut="C"))[0] == "sur"


def test_fusion_garde_la_plus_avancee_et_reprend_sources(reg):
    p = annonce(reg, entreprise="Nexthink SA", titre="Senior Product Manager - AI Agent", statut="candidature",
                sources=["jobup"], ids_source=["jobup:1"])
    s = annonce(reg, entreprise="Nexthink", titre="Senior Product Manager - AI Agent", statut="nouvelle",
                sources=["indeed"], ids_source=["indeed:9"], url_postuler="https://nexthink.example/apply")
    res = doublons.traiter(reg)
    assert res["fusionnees"] == [s]
    fp, fs = reg.get(p), reg.get(s)
    assert fs["statut"] == "doublon" and fs["motif"].startswith("Même poste que %s" % p[:12])
    assert fp["statut"] == "candidature" and fp["sources"] == ["jobup", "indeed"]
    assert fp["url_postuler"] == "https://nexthink.example/apply"
    assert reg.par_id_source("indeed:9") == p
    # La même offre revue par Indeed revient sur la fiche principale.
    b = ingerer(reg, [Annonce("indeed", "9", "Nexthink", "Senior Product Manager - AI Agent")])
    assert b["revues"] == [p] and not b["nouvelles"]
    # Plus rien à fusionner ensuite.
    assert doublons.traiter(reg)["fusionnees"] == []


def test_doublon_ancien_registre_par_le_motif(reg):
    p = annonce(reg, entreprise="Swissquote Bank SA", titre="Search & AI Visibility Specialist")
    annonce(reg, entreprise="Swissquote Bank SA", titre="Search &amp; AI Visibility Specialist", statut="doublon",
            motif="Même poste que %s (Search & AI Visibility Specialist)" % p)
    b = ingerer(reg, [Annonce("jobup", "7", "Swissquote Bank SA", "Search &amp; AI Visibility Specialist")])
    assert b["revues"] == [p] and not b["nouvelles"]


def test_separer_defait_tout(reg):
    p = annonce(reg, entreprise="Etel S.A.", titre="R&D Electronics Engineer", sources=["jobup"], ids_source=["jobup:1"])
    s = annonce(reg, entreprise="Etel", titre="R&amp;D Electronics Engineer", statut="C", motif="hors familles",
                sources=["job-room"], ids_source=["job-room:2"])
    doublons.fusionner(reg, p, s, "même intitulé", auto=True)
    assert doublons.separer(reg, s) == "C"
    fp, fs = reg.get(p), reg.get(s)
    assert fs["statut"] == "C" and fs["motif"] == "hors familles" and "doublon_de" not in (fs.get("extra") or {})
    assert fp["sources"] == ["jobup"] and reg.par_id_source("job-room:2") == s
    # Séparées par Camille : plus jamais proposées.
    assert doublons.detecter(reg) == {"surs": [], "a_verifier": []}


def test_differentes_n_est_plus_propose(reg):
    a = annonce(reg, entreprise="Nexthink SA", titre="Senior Product Manager, Platform", statut="nouvelle")
    b = annonce(reg, entreprise="Nexthink SA", titre="Product Manager", statut="C")
    assert len(doublons.detecter(reg)["a_verifier"]) == 1
    doublons.differentes(reg, a, b)
    assert doublons.detecter(reg)["a_verifier"] == []


def test_paires_ecartees_pas_montrees(reg):
    annonce(reg, entreprise="Nexthink SA", titre="Senior Product Manager, Platform", statut="C")
    annonce(reg, entreprise="Nexthink SA", titre="Product Manager", statut="C")
    assert doublons.detecter(reg)["a_verifier"] == []


def test_pas_de_chaine_dans_un_passage(reg):
    for i, nom in enumerate(["Deloitte", "Deloitte SA", "Deloitte - Switzerland"]):
        annonce(reg, entreprise=nom, titre="Manager - AI & Data", empreinte="e%d" % i, cree_le="2026-10-0%d" % (i + 1))
    assert sorted(doublons.traiter(reg)["fusionnees"]) == ["e1", "e2"]
    assert {reg.get(e)["extra"]["doublon_de"] for e in ("e1", "e2")} == {"e0"}


def test_sigle_d_employeur():
    assert doublons.sigle("World Intellectual Property Organization") == "wipo"
    assert doublons.sigle("Organisation Mondiale de la Propriété Intellectuelle (OMPI / WIPO)") == "ompi"
    assert doublons.sigle("Nexthink SA") is None


def test_meme_employeur_par_le_sigle_cas_ompi():
    # Doublon du 05.11 : la candidature du 29.10 venait d'Indeed en anglais, l'ajout à la main en français.
    assert doublons.meme_employeur("World Intellectual Property Organization",
                                   "Organisation Mondiale de la Propriété Intellectuelle (OMPI / WIPO)")
    assert doublons.comparer(_f(entreprise="World Intellectual Property Organization", titre="UX Lead"),
                             _f(entreprise="Organisation Mondiale de la Propriété Intellectuelle (OMPI / WIPO)",
                                titre="UX Lead"))[0] == "sur"
    # Des mots en commun sans inclusion : deux employeurs, même si un sigle tombe juste.
    assert not doublons.meme_employeur("Banque Cantonale Vaudoise (BCV)", "Banque Cantonale du Valais")
    assert not doublons.meme_employeur("World Intellectual Property Organization", "World Health Organization")


def test_detecter_trouve_la_paire_au_sigle(reg):
    a = annonce(reg, entreprise="World Intellectual Property Organization", titre="UX Lead", statut="candidature",
                postulee_le="2026-10-29")
    b = annonce(reg, entreprise="Organisation Mondiale de la Propriété Intellectuelle (OMPI / WIPO)",
                titre="UX Lead", statut="nouvelle", vue_le="2026-11-05")
    surs = doublons.detecter(reg)["surs"]
    assert [(p["empreinte"], s["empreinte"]) for p, s, _ in surs] == [(a, b)]


def test_meme_poste_engage(reg):
    a = annonce(reg, entreprise="World Intellectual Property Organization", titre="UX Lead", statut="candidature",
                postulee_le="2026-10-29", jobroom_saisie_le="2026-11-03")
    b = annonce(reg, entreprise="Organisation Mondiale de la Propriété Intellectuelle (OMPI / WIPO)",
                titre="UX Lead", statut="retenue")
    c = annonce(reg, entreprise="World Intellectual Property Organization", titre="Senior Business Analyst",
                statut="nouvelle")
    assert [g["empreinte"] for g, _ in doublons.meme_poste_engage(reg, reg.get(b))] == [a]
    assert doublons.meme_poste_engage(reg, reg.get(c)) == []
    doublons.differentes(reg, a, b)                     # la personne a tranché : deux postes
    assert doublons.meme_poste_engage(reg, reg.get(b)) == []


def test_acceptees_meme_employeur(reg):
    """Les autres annonces acceptées (oui donné, envoyées) chez le même employeur, à l'écriture du nom près ;
    ni les annonces non acceptées, ni les autres employeurs, ni les annonces anonymes."""
    oui = annonce(reg, entreprise="Exemple SA", titre="Chef de produit", statut="retenue")
    envoyee = annonce(reg, entreprise="Exemple", titre="Responsable produit", statut="candidature",
                      postulee_le="2026-11-02")
    annonce(reg, entreprise="Exemple SA", titre="Analyste", statut="C")                    # dit non : ne compte pas
    annonce(reg, entreprise="Autre Maison AG", titre="Chef de produit", statut="retenue")
    nouvelle = annonce(reg, entreprise="Exemple S.A. (via un portail)", titre="Product Owner", statut="nouvelle")
    seule = annonce(reg, entreprise="Autre Maison AG", titre="Analyste", statut="nouvelle")
    anonymes = [annonce(reg, entreprise="Entreprise confidentielle", titre=t, statut="retenue") for t in ("Un", "Deux")]
    fiches = [reg.get(e) for e in (oui, envoyee, nouvelle, seule, *anonymes)]
    m = doublons.acceptees_meme_employeur(reg, fiches)
    assert [g["empreinte"] for g in m[nouvelle]] == [envoyee, oui]                 # l'envoyée d'abord
    assert [g["empreinte"] for g in m[oui]] == [envoyee]
    assert [g["empreinte"] for g in m[envoyee]] == [oui]
    assert len(m[seule]) == 1 and m[seule][0]["entreprise"] == "Autre Maison AG"
    assert not any(e in m for e in anonymes)
