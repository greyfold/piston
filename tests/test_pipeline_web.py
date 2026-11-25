from html import unescape
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
    for u in ("/", "/candidatures", "/offres?q=exemple", "/reglages/parcours", "/sources", "/sante", "/annonce/" + e[:6]):
        assert web.get(u).status_code == 200, u


def test_etats_vides(web, reg):
    """L'état vide Nexus (EmptyState, macro etat_vide), le même partout, adapté à chaque page."""
    offres, cands, sante, accueil = (web.get(u).text for u in ("/offres", "/candidatures", "/sante", "/"))
    assert '<h1 class="titre-vide" id="etat-vide-titre">Aucune annonce pour le moment</h1>' in offres
    assert "<table" not in offres and 'class="filtres"' not in offres and 'data-ouvrir-modale="ajout-annonce"' in offres
    assert '<h1 class="titre-vide" id="etat-vide-titre">Aucune candidature pour le moment</h1>' in cands
    assert "<table" not in cands
    assert '<div class="etat-vide compact">' in sante and '<h3 class="titre-vide" id="notifications-vide">' in sante
    for html in (offres, cands, sante, accueil):
        assert 'class="etat-vide' in html and 'class="pastille-vide" aria-hidden="true"' in html
        assert 'class="vide"' not in html and "page-vide" not in html
    annonce(reg, statut="A", texte=FR)
    filtre = web.get("/offres?q=introuvable").text
    assert '<h2 class="titre-vide" id="etat-vide-titre">Aucun résultat pour « introuvable »</h2>' in filtre
    assert 'class="filtres"' in filtre and "<table" not in filtre
    assert "Aucune annonce avec ces filtres" in web.get("/offres?statut=refus").text
    offres = web.get("/offres").text
    assert "<table" in offres and "etat-vide" not in offres.split("<main>")[1]
    annonce(reg, titre="UX Lead", statut="candidature", postulee_le="2026-11-02")
    cands = web.get("/candidatures").text
    assert "etat-vide" not in cands.split("<main>")[1] and "<table" in cands and "<h2>En cours" in cands
    assert "<h2>Refus" not in cands and 'id="sans-suite"' not in cands        # sections vides masquées


def test_cadre_natif_de_l_application_mac(web, reg):
    """Dans la vue web de l'application Mac, le menu est natif : la page ne le dessine pas, elle dit au cadre
    où elle en est (JSON #etat-page). Un navigateur ordinaire garde le menu et ne reçoit pas cet état."""
    import json
    mac = {"user-agent": "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) RechercheEmploi"}

    def etat(html):
        return json.loads(html.split('id="etat-page">')[1].split("</script>")[0])

    html = web.get("/offres", headers=mac).text
    assert 'class="menu-lateral"' not in html and 'id="notifications"' in html and "coquille" in html
    e = etat(html)
    assert e["mode"] == "normal" and e["section"] == "/offres"
    assert [x["href"] for x in e["espaces"]][0] == "/" and isinstance(e["notifications"], int)
    assert etat(web.get("/sources", headers=mac).text)["section"] == "/reglages"
    assert etat(web.get("/parcours", headers=mac).text)["mode"] == "concentre"          # mode concentré
    ordinaire = web.get("/offres").text
    assert 'class="menu-lateral"' in ordinaire and 'id="etat-page"' not in ordinaire and "coquille" not in ordinaire


def test_anciennes_adresses_redirigees(web, reg):
    for ancienne, nouvelle in (("/pipeline", "/candidatures"), ("/registre?statut=A&q=ux", "/offres?statut=A&q=ux")):
        r = web.get(ancienne, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == nouvelle


def test_menu_lateral_et_reglages(web, reg):
    """Un menu latéral sur toutes les pages : les espaces de travail, les notifications, et tous les réglages."""
    html = web.get("/").text
    menu = html.split("</aside>")[0]
    assert 'class="menu-lateral"' in menu and 'class="barre' not in html
    for href in ('href="/"', 'href="/offres"', 'href="/candidatures"', 'href="/reglages"'):
        assert href in menu
    for href in ('href="/profil"', 'href="/sources"', 'href="/connecteurs"', 'href="/sante"'):
        assert href not in menu                                          # les réglages ont leur page
    assert '<a href="/" class="actif" aria-current="page">' in menu and "data-ouvrir-notifications" in menu
    r = web.get("/reglages", follow_redirects=False)
    assert r.status_code == 200
    reglages = r.text
    contenu = reglages.split("<main>")[1]
    for href in ("/profil", "/reglages/parcours", "/sources", "/ia", "/connecteurs",
                 "/confidentialite", "/sante"):
        assert 'class="reglage-ligne" href="%s"' % href in contenu
    assert 'href="/fonctions"' not in contenu                           # pas dans la version 1
    assert "Fournisseur d'IA" in unescape(contenu) and "Connexions" not in contenu      # IA à part des connecteurs
    assert '<a href="/reglages" class="actif" aria-current="page">' in reglages
    assert 'class="retour" href="/reglages"' not in reglages             # pas de retour vers elle-même
    sources = web.get("/sources").text
    assert '<a href="/reglages" class="actif" aria-current="true">' in sources
    assert 'class="retour" href="/reglages"' in sources
    profil = web.get("/profil/identite").text
    assert "onglets-annonce" not in profil and "<h1>Identité</h1>" in profil
    assert 'class="retour" href="/profil"' in profil and 'class="retour" href="/reglages"' not in profil
    etapes = web.get("/profil").text                                      # la liste des étapes, en lignes de réglage
    assert 'class="reglage-ligne" href="/profil/langues"' in etapes and 'class="retour" href="/reglages"' in etapes
    assert 'class="reglages-contenu"' in sources and 'class="reglages-contenu"' not in html


def test_etat_passage_dit_que_le_dernier_passage_a_ete_interrompu(reg):
    import datetime as dt
    from jobsearch.web.app import etat_passage
    il_y_a = lambda h: (dt.datetime.now() - dt.timedelta(hours=h)).isoformat(sep=" ", timespec="minutes")
    reg.cx.execute("INSERT INTO runs (debut, fin, statut, declencheur, resume) VALUES (?,?,'complet','application','')",
                   (il_y_a(5), il_y_a(4)))
    reg.cx.execute("INSERT INTO runs (debut, fin, statut, declencheur, resume) VALUES (?,?,'interrompu','application','')",
                   (il_y_a(2), il_y_a(1)))
    reg.commit()
    e = etat_passage(reg)
    assert e["niveau"] == "attention" and "interrompu" in e["texte"] and "complet" not in e["texte"]


def test_passages_en_notifications(web, reg):
    """Les passages s'affichent en notifications : le plus récent d'abord, avec son résumé et ses étapes ;
    ceux qui suivent le dernier vu (cookie) sont comptés dans le menu, coloré comme le plus grave."""
    import datetime as dt
    il_y_a = lambda h: (dt.datetime.now() - dt.timedelta(hours=h)).isoformat(sep=" ", timespec="minutes")
    vide = web.get("/").text
    assert 'id="notifications"' in vide and 'id="notifications-vide"' in vide and 'class="compte-notifs' not in vide
    reg.cx.execute("INSERT INTO runs (debut, fin, statut, declencheur, resume) VALUES (?,?,'complet','application',?)",
                   (il_y_a(30), il_y_a(29), "3 nouvelles annonces, 1 écartée au pré-tri (dont 0 limites), 2 à trancher."))
    cur = reg.cx.execute("INSERT INTO runs (debut, fin, statut, declencheur, resume) VALUES (?,?,'partiel','manuel',?)",
                         (il_y_a(2), il_y_a(1), "⚠ jobup en panne\n0 nouvelles annonces."))
    reg.cx.execute("INSERT INTO run_etapes (run_id, etape, debut, statut, compteurs, erreur) VALUES (?, 'sourcing', ?, 'erreur', '{}', 'délai dépassé')",
                   (cur.lastrowid, il_y_a(2)))
    reg.commit()
    html = web.get("/").text
    volet = html[html.index('id="notifications"'):html.index("</dialog>", html.index('id="notifications"'))]
    assert volet.index("Passage terminé, une étape a échoué") < volet.index("Passage terminé<")
    assert "jobup en panne" in volet and "⚠" not in volet and "délai dépassé" in volet and "lancé à la main" in volet
    assert volet.count("notification attention nouvelle") == 1 and "notification ok nouvelle" not in volet   # sans cookie : le dernier
    assert '<span class="compte-notifs attention">1<' in html
    web.cookies.set("notifs_vues", str(cur.lastrowid))
    vu = web.get("/").text
    assert 'class="compte-notifs' not in vu and ' nouvelle"' not in vu.split('id="notifications"')[1].split("</dialog>")[0]
    web.cookies.set("notifs_vues", "0")
    assert '<span class="compte-notifs attention">2<' in web.get("/").text
    assert "Passage terminé" not in web.get("/sante").text.split("<main>")[1]       # plus de journal sur la page Système


def test_registre_montre_la_source(web, reg):
    annonce(reg, titre="UX Lead", sources=["jobup", "linkedin"], url="https://www.jobup.ch/fr/emplois/detail/1/")
    annonce(reg, titre="Head of Design", sources=[])
    html = web.get("/offres").text
    assert "<th>Source</th>" in html
    assert '>jobup</a>, <a class="lien-neutre" href="?source=linkedin"' in html
    assert 'href="https://www.jobup.ch/fr/emplois/detail/1/"' in html

def test_clic_go_ecrit_tout_de_suite(web, reg):
    e = annonce(reg, statut="A")
    r = web.post("/action", data={"empreinte": e, "type": "go", "raisons": ["angle fort", "employeur visé"],
                                  "retour": "/#trancher"}, follow_redirects=False)
    assert r.status_code == 303 and "ok=1" in r.headers["location"]
    reg.cx.rollback()
    f = reg.get(e)
    assert f["statut"] == "retenue" and f["raison_decision"] == ["angle fort", "employeur visé"]


def test_a_l_ouverture_le_tri_passe_d_abord(web, reg):
    """Des annonces à trancher : l'accueil ouvre la pile de tri, sans barre de menu ; « Tableau de bord »
    pose le cookie de la session et le tableau de bord reste ensuite accessible, avec une simple entrée."""
    import datetime as dt
    proche = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    loin = annonce(reg, statut="B", titre="Poste sans délai", texte=FR)
    presse = annonce(reg, statut="A", titre="Poste pressé", texte=FR, delai_postulation=proche)
    r = web.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/trier"
    tri = web.get("/trier").text
    assert 'class="barre"' not in tri and 'href="/?ensuite=1"' in tri and "1</span> sur 2" in tri
    assert tri.index("Poste pressé") < tri.index("Poste sans délai")          # le plus pressé d'abord
    assert 'name="empreinte" value="%s"' % presse in tri and 'data-empreinte="%s" aria-labelledby="carte-tri-2" inert' % loin in tri
    assert 'value="pasgo"' in tri and 'value="go"' in tri and 'value="attendre"' in tri
    page = web.get("/?ensuite=1")
    assert "tri_passe" in page.headers.get("set-cookie", "") and "Trancher maintenant" in page.text
    assert web.get("/", follow_redirects=False).status_code == 200              # tri passé : plus de détour
    r = web.get("/?ouverture=1", follow_redirects=False)                         # la fenêtre se rouvre : le tri d'abord
    assert r.status_code == 303 and r.headers["location"] == "/trier"


def test_trancher_en_json_puis_tout_est_tranche(web, reg):
    e = annonce(reg, statut="A", texte=FR)
    r = web.post("/action", data={"empreinte": e, "type": "pasgo", "raisons": ["salaire"], "retour": "/trier"},
                 headers={"accept": "application/json"})
    assert r.json()["ok"] is True and ": Non" in r.json()["msg"]
    reg.cx.rollback()
    assert reg.get(e)["statut"] == "C" and reg.get(e)["raison_decision"] == ["salaire"]
    r = web.post("/action", data={"empreinte": "inconnue", "type": "go"}, headers={"accept": "application/json"})
    assert r.json()["ok"] is False and "aucune annonce" in r.json()["msg"]
    r = web.get("/trier?msg=Fini&ok=1", follow_redirects=False)   # plus rien : droit à « À faire aujourd'hui »
    assert r.status_code == 303 and r.headers["location"] == "/?ensuite=1&msg=Fini&ok=1"
    assert web.get("/", follow_redirects=False).status_code == 200


def test_post_autre_origine_refuse(web, reg):
    e = annonce(reg, statut="A")
    r = web.post("/action", data={"empreinte": e, "type": "go"},
                 headers={"origin": "https://malveillant.example"}, follow_redirects=False)
    assert r.status_code == 403


def test_sans_jeton_redirige(reg):
    from jobsearch.web import app as appmod
    c = TestClient(appmod.app)
    c.cookies.clear()                                    # même en local : le jeton est exigé (web/auth.py)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/connexion"


def test_preremplir_et_claude_repondent_en_json_ou_redirigent(web, reg, monkeypatch):
    from jobsearch import agent_claude, preremplissage
    e = annonce(reg, statut="go")
    lances = []
    monkeypatch.setattr(preremplissage, "employeur", lambda reg, emp: None)
    monkeypatch.setattr(preremplissage, "lancer_en_arriere_plan", lambda emp, jobroom=False: lances.append(emp))
    monkeypatch.setattr(agent_claude, "lancer", lambda reg, type_, emp: lances.append((type_, emp)))
    json_ = {"accept": "application/json"}
    r = web.post("/annonce/%s/preremplir" % e, data={"retour": "/annonce/x#postuler"}, headers=json_)
    assert r.status_code == 200 and r.json()["ok"] is True and "Pré-remplissage" in r.json()["msg"]
    r = web.post("/annonce/%s/preremplir" % e, data={"retour": "/annonce/x#postuler"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/annonce/x?msg=")
    assert r.headers["location"].endswith("#postuler")
    r = web.post("/annonce/%s/claude" % e, data={"type": "formulaire"}, headers=json_)
    assert r.status_code == 200 and r.json()["ok"] is True and "Claude" in r.json()["msg"]
    r = web.post("/annonce/%s/claude" % e, data={"type": "formulaire", "retour": "/candidatures"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/candidatures?msg=")
    assert lances == [e, e, ("formulaire", e), ("formulaire", e)]


def test_retour_reste_sur_le_site(web, reg):
    from jobsearch.web.app import retour_sur
    for mauvais in ("//ailleurs.example", "/\\ailleurs.example", "/\\/ailleurs", "https://ailleurs.example",
                    "/a\nb", "/a\tb", "/a\x00", "", None, "ailleurs"):
        assert retour_sur(mauvais) == "/", mauvais
    assert retour_sur("/annonce/x?y=1#postuler") == "/annonce/x?y=1#postuler"
    e = annonce(reg, statut="A")
    r = web.post("/action", data={"empreinte": e, "type": "go", "retour": "/\\ailleurs.example"}, follow_redirects=False)
    assert r.headers["location"].startswith("/?msg=")


def test_api_etat_dit_quel_serveur_repond(web, reg):
    import os
    from pathlib import Path
    import jobsearch
    d = web.get("/api/etat").json()
    assert d["pid"] == os.getpid() and d["racine"] == str(Path(jobsearch.__file__).resolve().parent)


def test_texte_html_neutralise_le_html_brut():
    from jobsearch.web.app import texte_html
    h = str(texte_html("# Titre\n\n<script>alert(1)</script>\n\nTexte <img src=x onerror=alert(1)> **gras**\n\n"
                       "[lien](javascript:alert(1)) [bon](https://exemple.ch)\n\n<div onclick=x>bloc</div>"))
    assert "<script" not in h and "<img" not in h and "<div" not in h and "javascript:" not in h
    assert "&lt;script&gt;" in h and "&lt;img" in h
    assert "<h1>Titre</h1>" in h and "<strong>gras</strong>" in h and 'href="https://exemple.ch"' in h


def test_texte_html_ne_charge_aucune_image_distante():
    from jobsearch.web.app import texte_html
    h = str(texte_html("![pixel](https://exemple.invalid/?d=secret) ![](//exemple.invalid/x.png) "
                       "![local](/static/a.png) [lien](https://exemple.ch)"))
    assert "<img" in h and h.count("<img") == 1 and 'src="/static/a.png"' in h
    assert 'src="https://' not in h and 'src="//' not in h
    assert '<a href="https://exemple.invalid/?d=secret">pixel</a>' in h and 'href="https://exemple.ch"' in h


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


def test_original_remplace_l_extrait(reg):
    from jobsearch import originaux
    e = annonce(reg, statut="A", texte="Extrait en français lu dans la recherche.",
                texte_origine="extrait", ids_source=["job-room:1"])
    src = FausseSource("job-room")
    src.detail = lambda a: (setattr(a, "texte", "We are looking for a Product Owner. " * 20),
                            setattr(a, "detail_charge", True))
    assert [f["empreinte"] for f in originaux.a_recharger(reg)] == [e]
    assert originaux.recharger(reg, reg.get(e), {"job-room": src})
    f = reg.get(e)
    assert f["texte"].startswith("We are looking") and f["texte_origine"] == "plateforme"
    assert f["langue"] == "en"
    assert originaux.a_recharger(reg) == []


def test_original_introuvable_n_est_pas_retente(reg):
    from jobsearch import originaux
    e = annonce(reg, statut="B", texte="résumé", texte_origine="extrait", ids_source=["jobup:9"])
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
    assert 'aria-label="Adéquation par catégorie"' in detail and "85<small>/100</small>" in detail
    assert 'style="width: 85%"' in detail and "Pas de MLOps" in detail and "Atouts et écarts par catégorie" in detail
    assert "Rien d'exigé dans l'annonce pour : formation." in unescape(detail)


def test_annonce_evaluee_avant_l_analyse_par_categorie(web, reg):
    """Une note d'avant l'analyse par catégorie s'affiche comme avant, sans bloc vide."""
    import json
    from jobsearch import evaluation
    from test_evaluation import FauxMoteur, _ev
    e = annonce(reg, statut="A", texte=FR)
    evaluation.evaluer_lot(reg, [reg.get(e)], c=FauxMoteur([_ev()]), texte_profil="P")
    ancien = evaluation.lire(reg, e)["resultat"]
    del ancien["categories"]
    del ancien["exigences"][0]["categorie"]
    reg.cx.execute("UPDATE evaluations SET resultat=?", (json.dumps(ancien),))
    reg.commit()
    detail = web.get("/annonce/" + e).text
    assert "82<small>/100</small>" in detail and "Own the AI product roadmap" in detail
    assert "Adéquation par catégorie" not in detail and "Atouts et écarts" not in detail


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


def test_carte_go_courte_le_detail_sur_l_annonce(web, reg):
    """La carte d'un oui reste courte : note, salaire, et une ligne qui compte ce qui est à vérifier et mène à
    l'annonce ; le résumé, les pour et contre et le détail des critères restent sur la page de l'annonce.
    La date et le canal d'envoi se notent dans une modale."""
    from jobsearch import evaluation
    from jobsearch.evaluation import Alerte, Critere
    from test_evaluation import FauxMoteur, _ev
    e = annonce(reg, statut="retenue", texte=FR)
    ev = _ev(note=35, alertes=[Alerte(gravite="moyenne", signal="Disponibilité permanente", extrait="24/7")])
    ev.criteres = [Critere(critere="langue", etat="ko", note="Allemand B2–C1 exigé."),
                   Critere(critere="présence", etat="incertain", note="Rien sur le télétravail.")]
    evaluation.evaluer_lot(reg, [reg.get(e)], c=FauxMoteur([ev]), texte_profil="P")
    carte = web.get("/").text.split('id="retenues"')[1].split("<script")[0]
    assert "35<small>/100</small>" in carte and "130–150 k CHF" in carte
    assert 'class="a-verifier" href="/annonce/%s"' % e in carte
    assert "1 critère non tenu · 1 signal à vérifier" in carte
    assert "✕ langue" not in carte and "Bon alignement produit et IA." not in carte and "Le profil couvre" not in carte
    assert "Marquer envoyée" not in carte and "data-postuler" not in carte       # pas de dossier : rien à envoyer


def test_carte_go_marquer_envoyee_remplace_postuler(web, reg, tmp_path):
    """Sur la carte d'un oui, « Marquer envoyée… » n'apparaît qu'après « Postuler sur le site », et le remplace ;
    la carte n'a pas de lien vers le dossier, le titre mène à l'annonce."""
    from jobsearch import dossier as dmod
    e = annonce(reg, statut="retenue", texte=FR, url="https://exemple.ch/postuler")
    reg.cx.executescript(dmod.SCHEMA)
    reg.cx.execute("INSERT INTO dossiers (empreinte, etat, chemin) VALUES (?,?,?)", (e, "pret", str(tmp_path)))
    reg.commit()
    carte = web.get("/?ensuite=1").text.split('id="retenues"')[1].split("<script")[0]
    assert 'data-postuler="https://exemple.ch/postuler"' in carte and 'value="/#retenues"' in carte
    assert "Marquer envoyée" not in carte and "Ouvrir le dossier" not in carte
    r = web.post("/action", data={"empreinte": e, "type": "postuler", "retour": "/#retenues"}, follow_redirects=False)
    assert r.status_code == 303
    carte = web.get("/?ensuite=1").text.split('id="retenues"')[1].split("<script")[0]
    assert "data-postuler" not in carte and "Ouvrir le dossier" not in carte
    assert 'data-ouvrir-modale="envoi-%s"' % e[:12] in carte and '<dialog class="modale large" id="envoi-%s"' % e[:12] in carte


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


def test_accueil_montre_le_rythme(web, reg, jobroom_en_service):
    annonce(reg, statut="candidature", postulee_le=dt.date.today().isoformat())
    html = web.get("/").text
    assert "<span>cette semaine</span>" in html and "<span>ce mois</span>" in html


def test_objectifs_choisis_par_periode(web, reg, sans_notification):
    assert "Aucun poste identifié pour le moment" in web.get("/").text      # rien à faire : l'état vide, pas d'objectifs
    annonce(reg, statut="A")
    reg.commit()
    r = web.post("/profil/objectifs", data={"objectifs.semaine_min": "", "objectifs.mois_min": "10"},
                 follow_redirects=False)
    assert r.status_code == 303 and "ok=1" in r.headers["location"]
    html = web.get("/?ensuite=1").text
    assert "<span>ce mois</span>" in html and "<span>cette semaine</span>" not in html
    vendredi = dt.datetime(2026, 10, 30, 10, 0)
    assert not followups.rythme(reg, vendredi.date())["alerte"]      # pas d'objectif de semaine : pas de rappel
    assert not pipeline.rappel_rythme(reg, vendredi)["alerte"] and not sans_notification
    assert 'value="10"' in web.get("/profil/objectifs").text            # dans Ma recherche › Objectifs
    web.post("/profil/objectifs", data={"objectifs.semaine_min": "", "objectifs.mois_min": ""})
    assert "<span>ce mois</span>" not in web.get("/").text
    assert "nombre entier" in web.post("/profil/objectifs", data={"objectifs.semaine_min": "x",
                                                                   "objectifs.mois_min": ""}).text


def test_sans_objectif_sans_rappel(web, reg, sans_notification):
    profil.enregistrer_objectifs("", "")              # pas d'interrupteur : le suivi suit les objectifs saisis
    assert not profil.option("objectifs") and not profil.fonctions()["objectifs"]
    vendredi = dt.datetime(2026, 10, 30, 10, 0)
    assert not followups.rythme(reg, vendredi.date())["actif"]
    assert not pipeline.rappel_rythme(reg, vendredi)["alerte"]
    assert "<span>cette semaine</span>" not in web.get("/").text


def test_marquer_envoyee_depuis_le_tableau_de_bord_lance_claude(web, reg, jobroom_en_service):
    from jobsearch import agent_claude
    e = annonce(reg, statut="retenue")
    r = web.post("/action", data={"empreinte": e, "type": "candidature", "date": "2026-10-26", "canal": "portail"},
                 follow_redirects=False)
    assert "Claude" in r.headers["location"] or "Claude" in __import__("urllib.parse").parse.unquote(r.headers["location"])
    reg.cx.rollback()
    assert agent_claude.derniere(reg, "jobroom")["session"] == "vm"


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
    assert "?onglet=entretien" not in page and "rendez-vous" not in page and "?onglet=dossier" not in page
    assert 'aria-current="page">Adéquation' in page                   # onglet inconnu : l'adéquation


def test_postuler_sur_le_site_puis_les_fichiers_a_joindre(web, reg, tmp_path):
    """« Remplir avec Claude » n'est plus proposé : « Postuler sur le site » ouvre le formulaire officiel, note
    le clic, et l'onglet Dossier montre alors les fichiers à joindre, un par un ou tous ensemble."""
    import io
    import zipfile
    from jobsearch import dossier as dmod
    e = annonce(reg, statut="retenue", texte=FR, url="https://exemple.ch/postuler")
    (tmp_path / "Camille-CV-Exemple.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "Camille-Lettre-Exemple.pdf").write_bytes(b"%PDF-1.4")
    reg.cx.executescript(dmod.SCHEMA)
    reg.cx.execute("INSERT INTO dossiers (empreinte, etat, chemin) VALUES (?,?,?)", (e, "pret", str(tmp_path)))
    reg.commit()
    accueil = web.get("/?ensuite=1").text
    assert "Remplir avec Claude" not in accueil and 'data-postuler="https://exemple.ch/postuler"' in accueil
    assert "Annuler la candidature" in accueil and 'class="menu-actions"' in accueil
    page = web.get("/annonce/%s?onglet=dossier" % e).text
    assert 'data-postuler="https://exemple.ch/postuler"' in page and "Fichiers à joindre" not in page
    r = web.post("/action", data={"empreinte": e, "type": "postuler",
                                  "retour": "/annonce/%s?onglet=dossier#fichiers" % e}, follow_redirects=False)
    assert r.status_code == 303 and "onglet=dossier" in r.headers["location"] and "ok=1" in r.headers["location"]
    reg.cx.rollback()
    assert reg.get(e)["statut"] == "retenue"                     # rien ne change au registre
    page = web.get("/annonce/%s?onglet=dossier" % e).text
    assert "Fichiers à joindre" in page and "/dossier/%s/fichier/Camille-CV-Exemple.pdf" % e in page
    assert "/dossier/%s/tout.zip" % e in page
    z = web.get("/dossier/%s/tout.zip" % e)
    assert z.status_code == 200 and z.headers["content-disposition"].startswith("attachment")
    assert sorted(zipfile.ZipFile(io.BytesIO(z.content)).namelist()) == ["Camille-CV-Exemple.pdf", "Camille-Lettre-Exemple.pdf"]
    # Pas de Go : on ne postule pas.
    b = annonce(reg, statut="B", texte=FR, titre="Autre poste", url="https://exemple.ch/b")
    r = web.post("/action", data={"empreinte": b, "type": "postuler"}, headers={"accept": "application/json"})
    assert r.json()["ok"] is False


def test_annonce_en_onglets_et_suivi_en_modale(web, reg):
    e = annonce(reg, statut="retenue", texte=FR)
    page = web.get("/annonce/" + e).text
    assert 'class="entete-page"' in page and 'aria-current="page">Adéquation' in page
    for onglet in ("sources", "journal"):
        assert "?onglet=%s" % onglet in page
        assert web.get("/annonce/%s?onglet=%s" % (e, onglet)).status_code == 200
    assert 'data-ouvrir-modale="suivi"' in page and '<dialog class="modale large" id="suivi"' in page
    assert 'class="menu-actions"' in page and 'value="annuler_go"' in page
    assert web.get("/dossier/%s" % e, follow_redirects=False).headers["location"] == "/annonce/%s?onglet=dossier" % e


def test_courrier_a_trancher_propose_et_se_cherche(web, reg, jobroom_en_service):
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


def test_reglages_sans_dossier_et_comptes_mail_dans_connecteurs(web):
    profil_page = web.get("/profil").text                                         # la liste des étapes
    assert 'href="/profil/dossier"' not in profil_page and 'href="/profil/objectifs"' in profil_page
    assert "Objectifs de postulation" in profil_page
    assert 'name="courrier.comptes"' not in web.get("/profil/objectifs").text
    r = web.post("/connecteurs/apple-mail/comptes", data={"comptes": "iCloud\nTravail"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#apple-mail")
    assert profil.comptes_courrier() == ["iCloud", "Travail"]
    page = web.get("/connecteurs").text                                           # une valeur par champ (champ_lignes)
    assert 'name="comptes" value="iCloud"' in page and 'name="comptes" value="Travail"' in page and "<textarea" not in page
    web.post("/connecteurs/apple-mail/comptes", data={"comptes": ["iCloud", "", "Perso"]})
    assert profil.comptes_courrier() == ["iCloud", "Perso"]
    assert web.get("/premiers-pas/dossier?formulaire=1").status_code == 200       # toujours l'entretien


def test_passage_sans_ia_le_dit_et_le_rattrapage_resserre_le_pre_tri(reg, monkeypatch):
    """13.11 : la VM éteinte, 31 annonces restaient à trancher sans note ni pré-sélection, et le
    bilan n'en disait rien. Le passage le dit désormais ; et le pré-tri reprend les « nouvelle »
    d'avant, qu'un filtre resserré écarte."""
    from jobsearch import evaluation
    monkeypatch.setattr(evaluation, "profil", lambda: "P")
    ancienne = annonce(reg, titre="Senior Software Engineer", entreprise="Vieille SA", texte=FR)   # laissée par un passage d'avant
    jr = FausseSource("job-room", [Annonce("job-room", "1", "Exemple SA", "AI Product Manager")])
    r = pipeline.Passage(reg, sources={"job-room": jr}).lancer()
    assert r["statut"] == "partiel" and "1 annonce(s) sans note ni pré-sélection de l'IA" in r["resume"]
    assert reg.get(ancienne)["statut"] == "C" and "1 annonce(s) d'avant" in r["resume"]
    assert reg.stats()["par_statut"] == {"nouvelle": 1, "C": 1}


def test_passage_avec_ia_pre_selectionne(reg, monkeypatch):
    from jobsearch import evaluation
    from jobsearch.moteur import FauxMoteur
    from test_evaluation import _ev
    monkeypatch.setattr(evaluation, "profil", lambda: "P")
    jr = FausseSource("job-room", [Annonce("job-room", "1", "Resto SA", "Product Manager cuisine"),
                                   Annonce("job-room", "2", "Exemple SA", "AI Product Manager")])
    p = pipeline.Passage(reg, sources={"job-room": jr})
    p._moteur = FauxMoteur([_ev(), _ev(perimetre="hors", perimetre_motif="Cuisine.")])   # lot dans l'ordre des entreprises
    r = p.lancer()
    assert r["statut"] == "complet", r
    assert "IA : 2 annonce(s) notée(s), 1 écartée(s) comme hors des postes visés" in r["resume"]
    assert reg.stats()["par_statut"] == {"nouvelle": 1, "C": 1}
    c = next(f for f in reg.liste(["C"]))
    assert c["passe_ecart"] == 2 and c["limite"] and c["titre"] == "Product Manager cuisine"


def test_offres_sans_les_non_et_libelles_oui_peut_etre_non(web, reg):
    """20.11 : ce qui est non (C, doublon, expirée) reste dans la base des offres, hors de la vue par
    défaut ; les statuts se lisent oui, peut-être, non ; l'empreinte n'est plus affichée."""
    garde = annonce(reg, titre="UX Lead", statut="B", texte=FR)
    ecartee = annonce(reg, titre="Cheffe de cuisine", statut="C", motif="Hors métier")
    offres = web.get("/offres").text
    assert "/annonce/" + garde in offres and "/annonce/" + ecartee not in offres
    assert "1 écartée (non, doublons, expirées)" in offres and 'href="?statut=C"' in offres
    assert '<span class="puce B">peut-être</span>' in offres
    non = web.get("/offres?statut=C").text
    assert "/annonce/" + ecartee in non and '<span class="puce C">non</span>' in non
    assert "/annonce/" + ecartee in web.get("/offres?q=cuisine").text          # une recherche retrouve tout
    page = web.get("/annonce/" + garde).text
    assert "Empreinte" not in page                     # le statut se lit dans les boutons, pas en pastille
    assert ">Oui</button>" in page and ">Peut-être</button>" in page and ">Non</button>" in page
    page = web.get("/annonce/" + ecartee).text
    assert "Remettre à trancher" in page and "Remettre en B" not in page

