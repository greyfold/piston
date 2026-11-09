import datetime as dt
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from conftest import annonce
from jobsearch import followups, pipeline, profil
from jobsearch.sources.base import Annonce, RouteCassee

FR = ("Nous recherchons une personne pour notre équipe produit et la stratégie avec les équipes. "
      "Vous êtes à l'aise dans le domaine et vous avez de l'expérience. ") * 4


class FausseSource:
    def __init__(self, nom, annonces=(), panne=False):
        self.nom, self.annonces, self.panne, self.details = nom, list(annonces), panne, 0

    def tester(self):
        if self.panne:
            raise RouteCassee("%s → HTTP 503" % self.nom)
        return 1

    def chercher(self, mots, *args, requete=None, **kw):
        return [Annonce(**dict(a.__dict__, requetes=[requete])) for a in self.annonces if mots in a.titre]

    def detail(self, a):
        self.details += 1
        a.texte, a.detail_charge = FR, True
        return a


@pytest.fixture(autouse=True)
def sans_api(monkeypatch):
    from jobsearch import moteur
    def absente():
        raise moteur.MoteurImpossible("Claude Code absent (test)", bloquant=True)
    monkeypatch.setattr(moteur, "ouvrir", absente)


@pytest.fixture(autouse=True)
def sans_courrier(monkeypatch):
    from jobsearch import courrier
    monkeypatch.setattr(courrier, "lire_boite", lambda *a, **k: [])     # jamais le vrai Mail en test


@pytest.fixture(autouse=True)
def sans_notification(monkeypatch):
    envoyees = []
    monkeypatch.setattr(pipeline, "notifier", lambda *a, **k: envoyees.append(a))
    return envoyees


def test_passage_complet(reg):
    jr = FausseSource("job-room", [
        Annonce("job-room", "1", "Exemple SA", "AI Product Manager"),
        Annonce("job-room", "2", "Exemple SA", "Senior Software Engineer Product Manager"),
    ])
    r = pipeline.Passage(reg, sources={"job-room": jr, "jobup": FausseSource("jobup")}).lancer()
    assert r["statut"] == "complet", r
    st = reg.stats()["par_statut"]
    assert st == {"nouvelle": 1, "C": 1}
    assert jr.details == 1           # le texte n'est lu que pour la survivante
    etapes = [e[0] for e in reg.cx.execute("SELECT statut FROM run_etapes WHERE run_id=?", (r["id"],))]
    assert etapes == ["ok"] * 12


def test_route_en_panne_passage_partiel_mais_termine(reg, sans_notification):
    jr = FausseSource("job-room", [Annonce("job-room", "1", "Exemple SA", "AI Product Manager")])
    r = pipeline.Passage(reg, sources={"job-room": jr, "jobup": FausseSource("jobup", panne=True)}).lancer()
    assert r["statut"] == "partiel" and "jobup" in r["resume"]
    assert reg.stats()["par_statut"] == {"nouvelle": 1}
    assert sans_notification and sans_notification[0][0].endswith("partiel")


class SourceCoupee(FausseSource):
    """Coupe la connexion sur certaines recherches, comme jobup le 28.10."""
    def __init__(self, nom, annonces=(), coupees=()):
        super().__init__(nom, annonces)
        self.coupees, self.appels = set(coupees), 0

    def chercher(self, mots, *args, requete=None, **kw):
        self.appels += 1
        if "*" in self.coupees or mots in self.coupees:
            raise ConnectionResetError("[Errno 54] Connection reset by peer")
        return super().chercher(mots, *args, requete=requete, **kw)


def test_coupure_d_une_requete_n_arrete_pas_le_sourcing(reg, monkeypatch):
    monkeypatch.setattr(pipeline, "criteres", lambda: {"sourcing": {
        "requetes": ["UX Lead", "Product Manager"], "entreprises": [], "cantons": ["VD"], "jobup_lieux": ["Vaud"]}})
    jobup = SourceCoupee("jobup", [Annonce("jobup", "9", "Autre SA", "Senior Product Manager")], coupees={"UX Lead"})
    jr = FausseSource("job-room", [Annonce("job-room", "1", "Exemple SA", "AI Product Manager")])
    r = pipeline.Passage(reg, sources={"jobup": jobup, "job-room": jr}).lancer()
    statuts = dict(reg.cx.execute("SELECT etape, statut FROM run_etapes WHERE run_id=?", (r["id"],)))
    assert statuts["sourcing"] == "ok"
    assert r["statut"] == "partiel" and "1 requête(s) ratée(s)" in r["resume"]
    assert reg.stats()["par_source"] == {"jobup": 1, "job-room": 1}       # la requête suivante et Job-Room passent


def test_source_muette_abandonnee_les_autres_continuent(reg, monkeypatch):
    monkeypatch.setattr(pipeline, "criteres", lambda: {"sourcing": {
        "requetes": ["A", "B", "C", "D", "Product Manager"], "entreprises": [], "cantons": ["VD"]}})
    jobup = SourceCoupee("jobup", coupees={"*"})
    jr = FausseSource("job-room", [Annonce("job-room", "1", "Exemple SA", "AI Product Manager")])
    r = pipeline.Passage(reg, sources={"jobup": jobup, "job-room": jr}).lancer()
    assert jobup.appels == 3 and "puis abandon" in r["resume"]
    assert reg.cx.execute("SELECT etat FROM sources WHERE id='jobup'").fetchone()[0] == "en panne"
    assert reg.stats()["par_statut"] == {"nouvelle": 1}

def test_toutes_routes_en_panne(reg):
    r = pipeline.Passage(reg, sources={"job-room": FausseSource("job-room", panne=True)}).lancer()
    assert r["statut"] == "partiel"
    statuts = dict(reg.cx.execute("SELECT etape, statut FROM run_etapes"))
    assert statuts["sourcing"] == "echec" and statuts["echeances"] == "ok"


def test_surveillance(reg, sans_notification):
    assert pipeline.surveiller(reg)["alerte"]          # jamais de passage
    pipeline.Passage(reg, sources={"job-room": FausseSource("job-room")}).lancer()
    assert not pipeline.surveiller(reg)["alerte"]
    plus_tard = dt.datetime.now() + dt.timedelta(hours=30)
    n = len(sans_notification)
    assert pipeline.surveiller(reg, plus_tard)["envoyee"]
    assert not pipeline.surveiller(reg, plus_tard)["envoyee"]   # pas de rafale
    assert len(sans_notification) == n + 1


def test_echeance_jobroom():
    assert followups.echeance_jobroom("2026-10-20") == dt.date(2026, 11, 5)
    assert followups.echeance_jobroom("2026-12-31") == dt.date(2027, 1, 5)


@pytest.fixture
def web(reg, monkeypatch):
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    return TestClient(appmod.app)


def test_pages(web, reg):
    e = annonce(reg, statut="A", texte=FR)
    for u in ("/", "/candidatures", "/offres?q=exemple", "/reglages", "/sources", "/sante", "/annonce/" + e[:6]):
        assert web.get(u).status_code == 200, u


def test_anciennes_adresses_redirigees(web, reg):
    for ancienne, nouvelle in (("/pipeline", "/candidatures"), ("/registre?statut=A&q=ux", "/offres?statut=A&q=ux")):
        r = web.get(ancienne, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == nouvelle


def test_barre_et_reglages(web, reg):
    html = web.get("/").text
    for href in ('href="/offres"', 'href="/candidatures"', 'href="/reglages"'):
        assert href in html
    assert 'href="/connecteurs"' not in html.split("</header>")[0]      # rangés sous Réglages
    reglages = web.get("/reglages").text
    for href in ("/profil", "/interface", "/sources", "/ia", "/connecteurs", "/sante#cles", "/sante"):
        assert 'href="%s"' % href in reglages
    assert "Fournisseur d'IA" in reglages and "Connexions" not in reglages      # IA à part des connecteurs
    assert 'class="lien-reglages actif"' in web.get("/ia").text
    sources = web.get("/sources").text
    assert 'class="lien-reglages actif"' in sources and "← Réglages" in sources


def test_registre_montre_la_source(web, reg):
    annonce(reg, titre="UX Lead", sources=["jobup", "linkedin"], url="https://www.jobup.ch/fr/emplois/detail/1/")
    annonce(reg, titre="Head of Design", sources=[])
    html = web.get("/offres").text
    assert "<th>Source</th>" in html
    assert '<a href="?source=jobup">jobup</a>, <a href="?source=linkedin">linkedin</a>' in html
    assert 'href="https://www.jobup.ch/fr/emplois/detail/1/"' in html

def test_clic_go_ecrit_tout_de_suite(web, reg):
    e = annonce(reg, statut="A")
    r = web.post("/action", data={"empreinte": e, "type": "go", "raisons": ["angle fort", "employeur visé"],
                                  "retour": "/#trancher"}, follow_redirects=False)
    assert r.status_code == 303 and "ok=1" in r.headers["location"]
    reg.cx.rollback()
    f = reg.get(e)
    assert f["statut"] == "retenue" and f["raison_decision"] == ["angle fort", "employeur visé"]


def test_post_autre_origine_refuse(web, reg):
    e = annonce(reg, statut="A")
    r = web.post("/action", data={"empreinte": e, "type": "go"},
                 headers={"origin": "https://malveillant.example"}, follow_redirects=False)
    assert r.status_code == 403


def test_reseau_sans_jeton_redirige(reg, monkeypatch):
    from jobsearch.web import app as appmod
    monkeypatch.setattr("jobsearch.web.auth.depuis_le_mac", lambda req: False)
    monkeypatch.setattr(appmod, "autorise", lambda req: False)
    c = TestClient(appmod.app)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/connexion"


def test_ajouter_et_retirer_une_source(web, reg):
    from jobsearch import repertoire
    r = web.post("/sources/ajouter", data={"nom": "Page carrière Nexthink", "url": "https://www.nexthink.com/careers",
                                            "nature": "page carrière", "acces": "à brancher"}, follow_redirects=False)
    assert r.status_code == 303 and "ok=1" in r.headers["location"]
    fiches = repertoire.lister(reg)
    assert fiches["page-carriere-nexthink"]["manuelle"] and fiches["job-room"]["manuelle"] is False
    assert "Page carrière Nexthink" in web.get("/sources").text
    r = web.post("/sources/ajouter", data={"nom": "Page carrière Nexthink"}, follow_redirects=False)
    assert "ok=0" in r.headers["location"]                      # doublon refusé
    r = web.post("/sources/retirer", data={"id": "job-room"}, follow_redirects=False)
    assert "ok=0" in r.headers["location"]                      # source du fichier : pas ici
    web.post("/sources/retirer", data={"id": "page-carriere-nexthink"})
    assert "page-carriere-nexthink" not in repertoire.lister(reg)


def test_source_adresse_et_acces_verifies(reg):
    from jobsearch import repertoire
    with pytest.raises(repertoire.SourceRefusee):
        repertoire.ajouter(reg, "X", url="pas une adresse")
    with pytest.raises(repertoire.SourceRefusee):
        repertoire.ajouter(reg, "X", acces="automatique")


def test_original_remplace_le_resume_du_coffre(reg):
    from jobsearch import originaux
    e = annonce(reg, statut="A", texte="Résumé en français écrit par l'ancienne chaîne.",
                texte_origine="coffre", ids_source=["job-room:1"])
    src = FausseSource("job-room")
    src.detail = lambda a: (setattr(a, "texte", "We are looking for a Product Owner. " * 20),
                            setattr(a, "detail_charge", True))
    assert [f["empreinte"] for f in originaux.a_recharger(reg)] == [e]
    assert originaux.recharger(reg, reg.get(e), {"job-room": src})
    f = reg.get(e)
    assert f["texte"].startswith("We are looking") and f["texte_origine"] == "plateforme"
    assert f["langue"] == "en" and f["extra"]["resume_coffre"].startswith("Résumé")
    assert originaux.a_recharger(reg) == []


def test_original_introuvable_n_est_pas_retente(reg):
    from jobsearch import originaux
    e = annonce(reg, statut="B", texte="résumé", texte_origine="coffre", ids_source=["jobup:9"])
    src = FausseSource("jobup")
    def retiree(a):
        raise RouteCassee("GET → HTTP 404")
    src.detail = retiree
    assert not originaux.recharger(reg, reg.get(e), {"jobup": src})
    assert reg.get(e)["texte_origine"] == "introuvable" and reg.get(e)["texte"] == "résumé"
    assert originaux.a_recharger(reg) == []


def test_cartes_montrent_note_et_alertes(web, reg):
    from jobsearch import evaluation
    from test_evaluation import FauxMoteur, _ev
    from jobsearch.evaluation import Alerte
    e = annonce(reg, statut="A", texte=FR)
    al = Alerte(gravite="forte", signal="Heures supplémentaires attendues", extrait="flexibilité horaire totale exigée")
    evaluation.evaluer_lot(reg, [reg.get(e)], c=FauxMoteur([_ev(note=84, alertes=[al])]), texte_profil="P")
    page = web.get("/").text
    assert "84<small>/100</small>" in page and "Profil 70 %" in page
    assert "Heures supplémentaires attendues" in page and "flexibilité horaire totale exigée" in page
    assert "130–150 k CHF" in page
    detail = web.get("/annonce/" + e).text
    assert "Critères et exigences" in detail and "130’000 – 150’000 CHF" in detail


def test_go_lance_le_dossier(web, reg, monkeypatch):
    from jobsearch import dossier
    lances = []
    monkeypatch.setattr(dossier, "produire_en_tache", lambda emp, reg=None: lances.append(emp))
    e = annonce(reg, statut="A", texte=FR)
    r = web.post("/action", data={"empreinte": e, "type": "go"}, follow_redirects=False)
    assert lances == [e] and "dossier" in r.headers["location"]


def test_go_sans_texte_dit_que_le_dossier_est_impossible(web, reg):
    e = annonce(reg, statut="A", texte="")
    r = web.post("/action", data={"empreinte": e, "type": "go"}, follow_redirects=False)
    assert "impossible" in unquote(r.headers["location"]) and reg.get(e)["statut"] == "retenue"


def test_avancement_du_dossier_suivi_en_direct(web, reg, tmp_path, monkeypatch):
    from jobsearch import dossier
    monkeypatch.setattr(dossier, "DATA", tmp_path)
    e = annonce(reg, statut="retenue", texte=FR)
    dossier.demarrer(reg, e)
    dossier._etape(reg, e, "cv")
    assert web.get("/dossier/%s/etat" % e).json() == {
        "etat": "en_cours", "etape": "2/5 · CV adapté",
        "libelle": dossier.libelle(dossier.etat(reg, e))}
    for page in ("/", "/annonce/" + e, "/dossier/" + e):
        html = web.get(page).text
        assert 'data-suivi-dossier="%s"' % e in html and "2/5 · CV adapté" in html, page
    assert web.get("/dossier/inconnu/etat").status_code == 404


def test_carte_affiche_delai_et_publication(web, reg):
    import datetime as dt
    demain = (dt.date.today() + dt.timedelta(days=5)).isoformat()
    annonce(reg, statut="B", texte=FR, delai_postulation=demain, publiee_le="2026-10-20")
    page = web.get("/").text
    assert "Postuler avant le" in page and "· 5 j" in page and "Publiée le 20 oct." in page
    assert web.get("/?tri=note").status_code == 200 and web.get("/?tri=nimporte").status_code == 200


def test_lien_vers_le_dossier_du_coffre(web, reg, tmp_path, monkeypatch):
    from jobsearch.web import app as appmod
    from jobsearch.importer import CANDIDATURES
    coffre = tmp_path / "coffre"
    d = coffre / CANDIDATURES / "2026-10-20 - Exemple - Product Manager"
    d.mkdir(parents=True)
    (d / "CV.md").write_text("# Camille Exemple\n## Product Manager\n", encoding="utf-8")
    (d / "Camille-Exemple-CV-Exemple.pdf").write_bytes(b"%PDF-1.4")
    (d / ".~lock.x#").write_text("verrou")
    monkeypatch.setattr(appmod, "reglages", lambda: {"coffre": coffre, "surveillance": {}})
    monkeypatch.setattr(appmod, "coffre_actif", lambda chemin=None: coffre)
    e = annonce(reg, statut="retenue", texte=FR, dossier=d.name)
    assert 'href="/dossier/%s"' % e in web.get("/annonce/" + e).text
    assert 'href="/dossier/%s"' % e in web.get("/").text
    page = web.get("/dossier/" + e).text
    assert "Camille-Exemple-CV-Exemple.pdf" in page and ".~lock" not in page and "ancienne chaîne" in page
    r = web.get("/dossier/%s/fichier/Camille-Exemple-CV-Exemple.pdf" % e)
    assert r.status_code == 200 and r.content == b"%PDF-1.4"
    assert web.get("/dossier/%s/fichier/..%%2Fsecret.pdf" % e).status_code == 404


def test_apercu_et_mots_cles_dans_la_page(web, reg, tmp_path, monkeypatch):
    import json as _json
    from jobsearch import dossier as dmod
    e = annonce(reg, statut="retenue", texte=FR, langue="fr")
    d = tmp_path / "dossiers" / "x"
    d.mkdir(parents=True)
    dmod.rendre_docx("# Camille Exemple\n## Product Manager\n- Une puce\n", d / "Camille-Exemple-CV-Exemple-PM.docx", "cv")
    (d / "Camille-Exemple-CV-Exemple-PM.pdf").write_bytes(b"%PDF-1.4")
    (d / "CV.md").write_text("# Camille Exemple\n", encoding="utf-8")
    (d / "motscles.json").write_text(_json.dumps({"taux": 64, "presents": [["roadmap", 3]],
                                                  "absents": [["discovery", 4]], "format": []}), encoding="utf-8")
    reg.cx.executescript(dmod.SCHEMA)
    reg.cx.execute("INSERT INTO dossiers (empreinte, etat, chemin, couverture) VALUES (?,?,?,?)", (e, "pret", str(d), 64))
    reg.commit()
    page = web.get("/dossier/" + e).text
    assert "64 %" in page and 'value="discovery"' in page and "roadmap" in page
    assert "Camille-Exemple-CV-Exemple-PM.pdf" in page                      # le PDF est l'aperçu par défaut
    r = web.get("/dossier/%s/apercu/Camille-Exemple-CV-Exemple-PM.pdf" % e)
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("inline")
    assert 'class="feuille"' in web.get("/dossier/%s/apercu/Camille-Exemple-CV-Exemple-PM.docx" % e).text
    lances = []
    monkeypatch.setattr(dmod, "reviser_en_tache", lambda emp, t, p="": lances.append((emp, t, p)))
    r = web.post("/dossier/%s/mots-cles" % e, data={"termes": ["discovery"], "precision": "vrai"}, follow_redirects=False)
    assert lances == [(e, ["discovery"], "vrai")] and "ok=1" in r.headers["location"]


def test_carte_go_montre_adequation_et_criteres(web, reg):
    from jobsearch import evaluation
    from jobsearch.evaluation import Critere
    from test_evaluation import FauxMoteur, _ev
    e = annonce(reg, statut="retenue", texte=FR)
    ev = _ev(note=35)
    ev.criteres = [Critere(critere="langue", etat="ko", note="Allemand B2–C1 exigé."),
                   Critere(critere="présence", etat="incertain", note="Rien sur le télétravail.")]
    evaluation.evaluer_lot(reg, [reg.get(e)], c=FauxMoteur([ev]), texte_profil="P")
    page = web.get("/").text
    carte = page[page.index('id="retenues"'):]
    assert "35<small>/100</small>" in carte and "✕ langue" in carte and "? présence" in carte
    assert "130–150 k CHF" in carte


def test_rythme_et_alerte_du_vendredi(reg, sans_notification):
    lundi = dt.date(2026, 10, 26)
    annonce(reg, titre="Un", statut="candidature", postulee_le="2026-10-27")
    annonce(reg, titre="Deux", statut="refus", postulee_le="2026-10-02")
    annonce(reg, titre="Trois", statut="doublon", postulee_le="2026-10-28")
    annonce(reg, titre="Quatre", statut="candidature", postulee_le="2026-10-25")   # dimanche d'avant
    r = followups.rythme(reg, lundi + dt.timedelta(days=3))
    assert (r["semaine"]["n"], r["semaine"]["manque"], r["mois"]["n"]) == (1, 1, 3)
    assert not r["alerte"]                                             # jeudi
    assert followups.rythme(reg, lundi + dt.timedelta(days=4))["alerte"]
    vendredi = dt.datetime(2026, 10, 30, 8, 0)
    assert not pipeline.rappel_rythme(reg, vendredi)["alerte"]        # avant l'heure
    assert pipeline.rappel_rythme(reg, vendredi.replace(hour=10))["envoyee"]
    assert not pipeline.rappel_rythme(reg, vendredi.replace(hour=11))["envoyee"]   # une fois par semaine
    assert len(sans_notification) == 1
    annonce(reg, titre="Cinq", statut="candidature", postulee_le="2026-10-30")
    assert not pipeline.rappel_rythme(reg, vendredi + dt.timedelta(days=1))["alerte"]


def test_accueil_montre_le_rythme(web, reg):
    annonce(reg, statut="candidature", postulee_le=dt.date.today().isoformat())
    html = web.get("/").text
    assert "postulations cette semaine" in html and "postulations ce mois" in html


def test_objectifs_choisis_par_periode(web, reg, sans_notification):
    r = web.post("/objectifs", data={"semaine": "", "mois": "10"}, follow_redirects=False)
    assert r.status_code == 303 and "ok=1" in r.headers["location"]
    html = web.get("/").text
    assert "postulations ce mois" in html and "postulations cette semaine" not in html
    vendredi = dt.datetime(2026, 10, 30, 10, 0)
    assert not followups.rythme(reg, vendredi.date())["alerte"]      # pas d'objectif de semaine : pas de rappel
    assert not pipeline.rappel_rythme(reg, vendredi)["alerte"] and not sans_notification
    assert 'value="10"' in web.get("/interface").text
    web.post("/objectifs", data={"semaine": "", "mois": ""})
    assert "postulations ce mois" not in web.get("/").text
    assert "nombre entier" in __import__("urllib.parse").parse.unquote(
        web.post("/objectifs", data={"semaine": "x", "mois": ""}, follow_redirects=False).headers["location"])


def test_option_coupee_sans_rappel(web, reg, sans_notification):
    profil.poser_option("objectifs", False)
    vendredi = dt.datetime(2026, 10, 30, 10, 0)
    assert not followups.rythme(reg, vendredi.date())["actif"]
    assert not pipeline.rappel_rythme(reg, vendredi)["alerte"]
    assert "postulations cette semaine" not in web.get("/").text


def test_marquer_envoyee_depuis_le_tableau_de_bord_lance_claude(web, reg):
    from jobsearch import agent_claude
    e = annonce(reg, statut="retenue")
    r = web.post("/action", data={"empreinte": e, "type": "candidature", "date": "2026-10-26", "canal": "portail"},
                 follow_redirects=False)
    assert "Claude" in r.headers["location"] or "Claude" in __import__("urllib.parse").parse.unquote(r.headers["location"])
    reg.cx.rollback()
    assert agent_claude.derniere(reg, "jobroom")["session"] == "abc12345"


def test_entretien_onglet_et_accueil(web, reg, tmp_path):
    import datetime as dt
    e = annonce(reg, statut="entretien", texte=FR)
    demain = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    (tmp_path / "entretiens").mkdir()
    (tmp_path / "entretiens" / ("%s.md" % e)).write_text(
        "---\ndate: %s\nheure: 13:30\nlieu: Hôtel de Ville\navec: Mme Exemple\n---\n"
        "## Pitch\n\n| Objection | Réponse |\n|---|---|\n| a | b |\n" % demain, encoding="utf-8")
    page = web.get("/annonce/%s" % e).text
    assert "?onglet=entretien" in page and "## Pitch" not in page and "<h2>Pitch</h2>" not in page
    page = web.get("/annonce/%s?onglet=entretien" % e).text
    assert "<h2>Pitch</h2>" in page and "<table>" in page and "demain à 13:30" in page
    accueil = web.get("/").text
    assert "Entretiens à venir" in accueil and "demain 13:30" in accueil and "Hôtel de Ville" in accueil
    # passé ou refusé : plus sur l'accueil
    reg.cx.execute("UPDATE annonces SET statut='refus' WHERE empreinte=?", (e,)); reg.commit()
    assert "Entretiens à venir" not in web.get("/").text


def test_sans_fiche_pas_d_onglet(web, reg):
    e = annonce(reg, statut="entretien", texte=FR)
    page = web.get("/annonce/%s?onglet=entretien" % e).text
    assert "onglets-annonce" not in page and "rendez-vous" not in page


def test_bouton_claude_bloque_pendant_la_session_et_libre_apres_un_echec(web, reg, tmp_path):
    from jobsearch import agent_claude, dossier as dmod
    e = annonce(reg, statut="retenue", url="https://exemple.ch/postuler")
    reg.cx.executescript(dmod.SCHEMA)
    reg.cx.execute("INSERT INTO dossiers (empreinte, etat, chemin) VALUES (?,?,?)", (e, "pret", str(tmp_path)))
    reg.commit()
    page = web.get("/").text
    assert "Remplir avec Claude" in page and 'data-occupe="Claude démarre…"' in page and "data-suivi-tache=" not in page
    t = agent_claude.lancer(reg, "formulaire", e)
    page = web.get("/").text
    assert 'disabled aria-busy="true" data-suivi-tache="%d"' % t in page and "Claude travaille…" in page
    assert web.get("/tache/%d/etat" % t).json() == {"etat": "en_cours", "libelle": "Claude travaille…", "active": True}
    agent_claude.noter(reg, t, "arret", "Le portail a refusé la page")
    page = web.get("/").text
    assert "data-suivi-tache=" not in page and "Échec de Claude : Le portail a refusé la page" in page
    assert ">Remplir avec Claude</button>" in page
    assert web.get("/tache/%d/etat" % t).json()["active"] is False
    assert web.get("/tache/999/etat").status_code == 404


def test_courrier_a_trancher_propose_et_se_cherche(web, reg):
    from jobsearch import courrier
    annonce(reg, entreprise="Losinger Marazzi SA", titre="Coach IA 80-100%", statut="candidature", postulee_le="2026-10-20")
    e = annonce(reg, entreprise="Employeur non divulgué sur Jobup", titre="Consultant.e IA", statut="candidature",
                postulee_le="2026-11-03", url="https://www.jobup.ch/fr/emplois/detail/57b6/")
    courrier._schema(reg)
    reg.cx.execute("INSERT INTO courriers (id, recu_le, expediteur, objet, extrait, empreinte, classe, etat, message, lu_le) "
                   "VALUES ('c1','2026-11-03','Service jobup.ch <application_no_reply@jobup.ch>',"
                   "'Votre candidature en tant que Consultant.e IA a été envoyée','Votre candidature pour le poste de "
                   "Consultant.e IA a été reçue.',(SELECT empreinte FROM annonces WHERE entreprise LIKE 'Losinger%'),"
                   "'inconnu','a_trancher','pas de réponse claire','2026-11-03')")
    reg.commit()
    page = web.get("/").text
    bloc = page[page.index('id="courrier"'):page.index('id="jobroom"')]
    assert 'data-filtre="cand-c1"' in bloc and 'optgroup label="Les plus probables"' in bloc
    assert ('<option value="%s" selected' % e) in bloc                  # l'ancien rattachement à Losinger est oublié
    assert '<option value="accuse" selected>Accusé de réception</option>' in bloc
    assert bloc.count('value="%s"' % e) == 1 and 'Ignorer</button>' in bloc and "formnovalidate" in bloc


def test_premiere_recherche_legere(reg, sans_notification):
    from jobsearch import premiere
    jr = FausseSource("job-room", [
        Annonce("job-room", "1", "Exemple SA", "AI Product Manager"),
        Annonce("job-room", "2", "Exemple SA", "Senior Software Engineer Product Manager"),
    ])
    premiere._poser(etat="aucune")
    assert premiere.lancer(reg.chemin, sources={"job-room": jr, "jobup": FausseSource("jobup")}, attendre=True)
    e = premiere.etat()
    assert e["etat"] == "finie", e
    assert e["trouvees"] == 2 and e["gardees"] == 1 and e["ecartees"] == 1 and e["sources"] == ["Job-Room", "jobup.ch"]
    assert e["apercu"][0]["titre"] == "AI Product Manager"
    etapes = [x[0] for x in reg.cx.execute("SELECT etape FROM run_etapes WHERE run_id=?", (e["run"],))]
    assert "evaluation" not in etapes and "courrier" in etapes and "dossiers" not in etapes   # ni IA ni dossiers
    assert not sans_notification


def test_page_confidentialite_montre_ce_que_l_ia_recoit(web, monkeypatch):
    from jobsearch import dossier
    monkeypatch.setattr(dossier, "contexte_fixe", lambda: (
        "## Identité et contact\n| Champ | Valeur |\n|---|---|\n| Téléphone | +41 79 000 00 00 |\n"
        "## Expérience\n- Contact : Paul, paul@exemple-sa.ch\n- Camille a formé 300 personnes.\n"))
    r = web.get("/confidentialite")
    assert r.status_code == 200
    html = r.text
    assert "L'IA écrit tes candidatures sans connaître ton nom" in html
    assert "{NOM_COMPLET} · {ADRESSE} · {TELEPHONE} · {EMAIL}" in html          # profil de test
    assert "| Téléphone | {TELEPHONE} |" in html and "{PRENOM} a formé 300 personnes." in html
    assert "paul@exemple-sa.ch" not in html.split("extrait-masque")[1].split("</pre>")[0]
    assert "(1 dans ton profil)" in html
