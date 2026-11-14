"""Installation neuve (l'application Mac) : le config/ livré, sans profil. Rien de personnel."""

import pytest

from jobsearch import config, prefilter, profil, repertoire

# Ce qui serait propre à une personne : vide dans le config/ livré, rempli par le profil.
PERSONNELS = {
    "settings.yaml": {"objectifs.semaine_min": 0, "objectifs.mois_min": 0, "courrier.comptes": [],
                      "indeed.lieux": [], "notifications.ntfy_sujet": ""},
    "criteres.yaml": {"sourcing.cantons": [], "sourcing.jobup_lieux": [], "sourcing.requetes": [],
                      "sourcing.entreprises": [], "sourcing.linkedin_requetes": [],
                      "sourcing.linkedin_lieu": "", "familles_cibles": ""},
}
SOURCES_DE_BASE = {"job-room", "jobup", "linkedin", "mails"}


@pytest.fixture
def neuve(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG", config.RACINE / "config")                   # le config/ livré
    monkeypatch.setattr("jobsearch.travailleur.client.distant", lambda: False)      # pas de vraie VM dans les tests
    monkeypatch.setenv("JOBSEARCH_PROFIL", str(tmp_path / "neuf" / "profil.yaml"))     # pas encore de profil
    profil.oublier()
    yield
    profil.oublier()


def test_valeurs_personnelles_vides(neuve):
    s, c = config.reglages(), config.criteres()
    for cle, v in PERSONNELS["settings.yaml"].items():
        assert profil.lire(s, cle) == v
    for cle, v in PERSONNELS["criteres.yaml"].items():
        assert profil.lire(c, cle) == v
    assert "coffre" not in s
    assert profil.effectif("recherche.cantons") == [] and profil.effectif("recherche.postes") == []
    # Le reste des réglages (passage, moteur, sources de base) est gardé.
    assert s["passage"] and s["moteur"]


def test_pre_tri_sans_metiers_exclus_ni_cantons(neuve):
    regles = config.criteres()["regles"]
    assert not [r for r in regles if r["motif"].startswith("Métier exclu")]
    canton = next(r for r in regles if r["id"] == "canton")
    assert not prefilter._compiler(canton["regex"]).search("VD")
    assert prefilter.familles().search("n'importe quel intitulé")


def test_cantons_du_profil_appliques(neuve, tmp_path):
    (tmp_path / "neuf").mkdir(); (tmp_path / "neuf" / "profil.yaml").write_text("recherche:\n  cantons: [GE]\n", encoding="utf-8")
    profil.oublier()
    canton = next(r for r in config.criteres()["regles"] if r["id"] == "canton")
    assert prefilter._compiler(canton["regex"]).search("VD") and not prefilter._compiler(canton["regex"]).search("GE")


def test_repertoire_de_base(neuve):
    assert set(repertoire.du_fichier()) == SOURCES_DE_BASE


def test_port_propre_a_l_application(monkeypatch):
    monkeypatch.setenv("JOBSEARCH_PORT", "8775")
    config.reglages.cache_clear()
    assert config.reglages()["web"]["port"] == 8775
    monkeypatch.delenv("JOBSEARCH_PORT")
    config.reglages.cache_clear()


def test_accueil_recherche_ia_offres_puis_checklist(neuve, monkeypatch, reg):
    from fastapi.testclient import TestClient
    from jobsearch import premiere
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    lances = []
    monkeypatch.setattr(premiere, "lancer", lambda *a, **k: lances.append(1) or True)
    monkeypatch.setattr(premiere, "etat", lambda: {"etat": "finie", "sources": ["Job-Room", "jobs.ch"], "trouvees": 12,
                                                  "gardees": 3, "ecartees": 9, "motifs": [("Taux : moins de 100 %", 5)],
                                                  "apercu": [{"titre": "Product designer", "entreprise": "Acme",
                                                              "lieu": "Genève", "url": ""}]})
    web = TestClient(appmod.app)
    assert profil.en_accueil()
    assert web.get("/", follow_redirects=False).headers["location"] == "/bienvenue"
    assert web.get("/bienvenue", follow_redirects=False).headers["location"] == "/bienvenue/recherche"   # la recherche d'abord
    page = web.get("/bienvenue/recherche").text
    assert 'class="onglets"' not in page and 'aria-current="step"' in page and "Ta recherche" in page
    assert page.index("Ta recherche") < page.index("Tes sources") < page.index("Ton IA") < page.index("Premières offres")
    assert "Passer pour l'instant" in page and page.count('class="carte-choix"') == 3

    r = web.post("/bienvenue/recherche", data={"objectif": "partiel", "poste": "", "cantons": ["GE"]})
    assert "À compléter" in r.text and not lances
    r = web.post("/bienvenue/recherche", data={"objectif": "partiel", "poste": "Product designer", "cantons": ["GE", "VD"]},
                 follow_redirects=False)
    assert r.headers["location"] == "/bienvenue/sources" and not lances
    page = web.get("/bienvenue/sources").text
    assert page.count('name="sources"') == 3 and "Indeed" in page and "Lancer ma recherche" in page
    assert "Choisis au moins un site" in web.post("/bienvenue/sources", data={}).text and not lances
    r = web.post("/bienvenue/sources", data={"sources": ["job-room", "jobs-ch"]}, follow_redirects=False)
    assert r.headers["location"] == "/bienvenue/ia" and lances                     # la recherche part pendant l'étape IA
    from jobsearch.sources import catalogue
    assert catalogue.socle_choisi() == ["job-room", "jobs-ch"]
    assert set(catalogue.construire()) >= {"job-room", "jobs-ch"} and "jobup" not in catalogue.construire()
    assert "romande-energie" not in catalogue.construire()                          # pas les pages carrière de Camille
    p = profil.charger()
    assert p["recherche"]["postes"] == [{"intitule": "Product designer", "variantes": []}]
    assert p["recherche"]["cantons"] == ["GE", "VD"]
    assert "jobup_lieux" not in p["recherche"] and profil.objectif(p) == "partiel"
    assert config.criteres()["sourcing"]["jobup_lieux"] == ["Genève", "Vaud"]      # déduits des cantons
    assert set(p["exclusions"]["desactivees"]) == {"taux", "contrat-freelance"}
    assert "familles" not in p["recherche"] and profil.mots_postes(profil.postes(p)) == ["product", "designer"]
    assert profil.mots_intitule("Responsable de la communication (H/F)") == ["responsable", "communication"]

    # L'IA est connectée (conftest) : l'étape est déjà faite, on passe aux offres.
    assert web.get("/bienvenue/ia", follow_redirects=False).headers["location"] == "/bienvenue/offres"
    page = web.get("/bienvenue/offres").text
    assert "3 offres pour toi" in page and "Product designer" in page and "Voir mes offres" in page
    r = web.post("/bienvenue/terminer", data={"suite": "/"}, follow_redirects=False)
    assert r.headers["location"] == "/" and not profil.en_accueil()
    pas = profil.premiers_pas(ia_connectee=True)
    assert [x[0] for x in pas] == ["ia", "recherche", "identite", "dossier", "criteres"]
    assert [x[5] for x in pas][:3] == [True, True, False]
    accueil = web.get("/").text
    assert "Bien démarrer" in accueil and "2 sur 5 faits" in accueil and 'class="onglets"' in accueil
    assert "Profil incomplet" not in accueil                                       # la checklist remplace le bandeau
    web.post("/premiers-pas/masquer")
    assert "Bien démarrer" not in web.get("/").text
    assert web.get("/bienvenue/recherche", follow_redirects=False).headers["location"] == "/"


def test_profil_d_avant_l_assistant_jamais_en_accueil():
    assert not profil.en_accueil()          # le profil de test (comme celui de Camille) n'a pas la clé


def test_civilite_et_adresses_prefixees():
    c = {x["cle"]: x for x in profil.etape("identite")["champs"]}
    assert profil._convertir(c["identite.civilite"], "Madame") == "Madame"
    with pytest.raises(profil.ProfilInvalide):
        profil._convertir(c["identite.civilite"], "Docteur")
    li = c["identite.linkedin"]
    for saisi in ("prenom-nom", "https://ch.linkedin.com/in/prenom-nom/?trk=x", "linkedin.com/in/prenom-nom", "in/prenom-nom"):
        assert profil._convertir(li, saisi) == "https://www.linkedin.com/in/prenom-nom"
    assert profil.en_saisie(li, "https://www.linkedin.com/in/prenom-nom") == "prenom-nom"
    site = c["identite.site"]
    assert profil._convertir(site, "mon-site.ch") == "https://mon-site.ch"
    assert profil.en_saisie(site, "https://mon-site.ch") == "mon-site.ch"


def test_identite_listes_calendrier_et_sans_exemples():
    from jobsearch import pays
    c = {x["cle"]: x for x in profil.etape("identite")["champs"]}
    assert not [k for k, x in c.items() if x["exemple"]]                  # aucun exemple dans les champs
    assert "Mx" in c["identite.civilite"]["choix"] and c["identite.civilite"]["vide"] == "Ne pas préciser"
    assert c["identite.pays"]["choix"] == ["Suisse", "France"]
    assert len(pays.TOUS) == 197 and {"Suisse", "Portugal", "Côte d'Ivoire"} <= set(pays.TOUS)
    with pytest.raises(profil.ProfilInvalide):
        profil._convertir(c["identite.nationalite"], "Atlantide")
    d = c["identite.date_naissance"]
    assert profil._convertir(d, "1980-12-31") == profil._convertir(d, "31.12.1980") == "31.12.1980"
    assert profil.en_saisie(d, "31.12.1980") == "1980-12-31"


def test_pays_d_apres_la_region_de_l_ordinateur():
    from jobsearch import pays
    assert pays.region_systeme({}, lambda: "fr_CH\n") == "CH"
    assert pays.region_systeme({}, lambda: "en_FR") == "FR"
    assert pays.region_systeme({"LANG": "de_DE.UTF-8"}, lambda: "") == "DE"
    assert pays.region_systeme({}, lambda: "fr") == ""
    assert pays.PAYS_DE_RECHERCHE.get("DE", "") == ""                    # hors Suisse et France : vide


def test_telephone_au_format_phonefield():
    from jobsearch import telephone as t
    assert t.normaliser("+41791234567") == "+41 79 123 45 67"
    assert t.normaliser("079 123 45 67", defaut="CH") == "+41 79 123 45 67"
    assert t.normaliser("+33 6 12 34 56 78") == "+33 6 12 34 56 78"
    assert t.normaliser("0041 79 123 45 67") == "+41 79 123 45 67"
    assert t.normaliser("+1 415 555 0100") == "+1 (415) 555-0100"
    assert t.normaliser("") == "" and t.normaliser("+41") == ""
    assert t.decomposer("+41 79 123 45 67") == ("CH", "791234567")
    assert t.decomposer("+1 (415) 555-0100", "CA") == ("CA", "4155550100")    # indicatif partagé : le pays par défaut
    c = {x["cle"]: x for x in profil.etape("identite")["champs"]}
    assert profil._convertir(c["identite.telephone"], "+41 79 123 45 67") == "+41 79 123 45 67"


def test_connexion_ia_un_bouton_par_fournisseur(neuve, monkeypatch, reg):
    from fastapi.testclient import TestClient
    from jobsearch import moteur
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    monkeypatch.setattr(appmod, "etat_claude", lambda: {"connecte": False, "message": "pas connecté"})
    profil.enregistrer_recherche("fixe", "Product designer", ["GE"])
    profil.enregistrer_sources(["job-room"])
    page = TestClient(appmod.app).get("/bienvenue/ia").text
    assert [f["id"] for f in moteur.FOURNISSEURS] == ["claude"]
    assert page.count('class="fournisseur"') == 1 and "Se connecter avec Claude" in page
    assert 'data-connexion-ia="claude"' in page and 'id="modale-ia"' in page and "Passer pour l'instant" in page


def test_connexion_ia_pilotee_par_le_tableau_de_bord(monkeypatch):
    import io
    import time
    from jobsearch import connexion_ia

    class FauxProcessus:
        def __init__(self, *a, **k):
            self.stdout = io.BytesIO(b"Opening browser...\nVisit https://claude.ai/oauth/authorize?code=true&x=1 to sign in\n")
            self.stdin = io.BytesIO()
            self.code = None

        def poll(self):
            return self.code

        def wait(self):
            while b"ABC-123" not in self.stdin.getvalue():
                time.sleep(.01)
            self.code = 0
            return 0

        def terminate(self):
            self.code = -15

    connexion_ia.annuler()
    e = connexion_ia.demarrer("claude", popen=FauxProcessus)
    assert e["etat"] in ("preparation", "attente_code")
    for _ in range(100):
        if connexion_ia.etat()["etat"] == "attente_code":
            break
        time.sleep(.01)
    assert connexion_ia.etat()["url"] == "https://claude.ai/oauth/authorize?code=true&x=1"
    assert connexion_ia.envoyer_code("  ABC-123 ")
    for _ in range(100):
        if connexion_ia.etat()["etat"] == "connecte":
            break
        time.sleep(.01)
    assert connexion_ia.etat()["etat"] == "connecte"
    with pytest.raises(ValueError):
        connexion_ia.commande("autre")


def test_taches_de_la_checklist_sans_assistant(neuve, monkeypatch, reg):
    from fastapi.testclient import TestClient
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    profil.enregistrer_recherche("fixe", "Product designer", ["GE"])
    profil.terminer_accueil()
    web = TestClient(appmod.app)
    liens = [x[4] for x in profil.premiers_pas(ia_connectee=True)]
    assert liens[2:] == ["/premiers-pas/identite", "/premiers-pas/dossier", "/premiers-pas/criteres"]   # le parcours d'abord
    page = web.get("/premiers-pas/identite").text
    assert "Tes coordonnées" in page and "Bien démarrer · 3 sur 5" in page and 'class="etapes"' not in page   # pas l'assistant
    assert 'name="identite.prenom"' in page and 'name="recherche.cantons"' not in page
    r = web.post("/premiers-pas/identite", data={"identite.prenom": "Alex"})
    assert "À corriger" in r.text                                                    # nom, localité, e-mail manquent
    r = web.post("/premiers-pas/identite", data={"identite.prenom": "Alex", "identite.nom": "Test", "identite.ville": "Genève",
                                                 "identite.email": "alex@exemple.ch"}, follow_redirects=False)
    assert r.headers["location"].startswith("/?msg=") and r.headers["location"].endswith("#premiers-pas")
    assert [x[5] for x in profil.premiers_pas(ia_connectee=True)][2] is True
    # Une étape vue faite le reste : l'IA déconnectée ne rouvre pas « Connecter ton IA ».
    assert [x[5] for x in profil.premiers_pas(ia_connectee=False)][0] is False
    profil.noter_premiers_pas(profil.premiers_pas(ia_connectee=True))
    assert profil.charger()[profil.PREMIERS_PAS_FAITS] == ["ia", "identite", "recherche"]
    assert [x[5] for x in profil.premiers_pas(ia_connectee=False)][0] is True
    assert web.post("/premiers-pas/masquer", follow_redirects=False).headers["location"] == "/"
    assert profil.charger()[profil.PREMIERS_PAS] == "masquee"


def test_interface_qui_s_ouvre_et_options(neuve, monkeypatch, reg):
    from fastapi.testclient import TestClient
    from conftest import annonce
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    monkeypatch.setattr(appmod, "ia_connectee", lambda: True)
    annonce(reg, statut="A")
    reg.commit()
    profil.enregistrer_recherche("fixe", "Product designer", ["GE"])
    profil.enregistrer_sources(["job-room"])
    profil.terminer_accueil()
    f = profil.fonctions(ia_connectee=True)
    assert f["offres"] and not f["postuler"] and not f["noter"] and not f["jobroom"] and not f["objectifs"]
    assert [i for i, _, _ in f["manque"]["postuler"]] == ["identite", "dossier"]
    web = TestClient(appmod.app)
    tri = web.get("/").text                                  # à l'ouverture : d'abord le tri
    assert "Pour postuler, il reste" in tri and 'disabled aria-describedby="verrou-tri"' in tri   # voir, pas postuler
    page = web.get("/?ensuite=1").text
    assert 'href="/candidatures"' not in page and 'href="/offres"' in page and 'id="jobroom"' not in page and "preuves Job-Room" not in page
    assert 'value="go"' not in page
    emp = reg.liste(["A"])[0]["empreinte"]
    r = web.post("/action", data={"empreinte": emp, "type": "go", "retour": "/"}, follow_redirects=False)
    assert "postuler" in unquote(r.headers["location"]) and reg.get(emp)["statut"] == "A"   # refusé côté serveur aussi
    web.post("/fonctions/jobroom", data={"actif": "1"})
    page = web.get("/").text                                       # option ouverte : le compteur ; rien à saisir :
    assert profil.option("jobroom") and "preuves Job-Room" in page and 'id="jobroom"' not in page   # pas de section vide
    assert "<h1>Fonctions</h1>" in web.get("/fonctions").text
    assert web.get("/interface", follow_redirects=False).headers["location"] == "/fonctions"   # ancienne page
    # Le profil de Camille (d'avant l'accueil) garde tout ; le suivi des objectifs suit les objectifs saisis.
    assert all(profil.fonctions({"identite": {"prenom": "J"}})[k] for k in ("offres", "noter", "postuler", "jobroom"))


from urllib.parse import unquote  # noqa: E402


def test_bien_demarrer_disparait_une_fois_tout_fait_et_etat_vide(neuve, monkeypatch, reg):
    """Les cinq étapes faites : plus de checklist. Rien à trancher : un état vide qui renvoie aux Offres."""
    from fastapi.testclient import TestClient
    from conftest import annonce
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    profil.ecrire({profil.PREMIERS_PAS: "affichee", "recherche": {"requetes": ["Product designer"], "cantons": ["GE"]}})
    etapes = ["ia", "recherche", "identite", "dossier", "criteres"]
    faites = lambda n: [(i, i, "", "1 min", "/", k < n) for k, i in enumerate(etapes)]
    web = TestClient(appmod.app)
    monkeypatch.setattr(profil, "premiers_pas", lambda p=None, ia_connectee=False: faites(4))
    assert "Bien démarrer" in web.get("/").text
    assert profil.charger()[profil.PREMIERS_PAS_FAITS] == ["dossier", "ia", "identite", "recherche"]   # retenues
    monkeypatch.setattr(profil, "premiers_pas", lambda p=None, ia_connectee=False: faites(5))
    page = web.get("/").text
    assert "Bien démarrer" not in page
    assert profil.charger()[profil.PREMIERS_PAS] == "terminee" and profil.PREMIERS_PAS_FAITS not in profil.charger()
    # L'IA se déconnecte ensuite : la checklist ne revient pas, le bandeau de l'accueil prend le relais.
    monkeypatch.setattr(profil, "premiers_pas", lambda p=None, ia_connectee=False: faites(4))
    monkeypatch.setattr(appmod, "etat_claude", lambda: {"installe": True, "connecte": False, "message": "pas connecté"})
    page = web.get("/").text
    assert "Bien démarrer" not in page and 'id="ia-deconnectee"' in page
    assert "Ton IA n'est plus connectée" in page and 'data-connexion-ia="claude"' in page
    monkeypatch.setattr(appmod, "etat_claude", lambda: {"installe": True, "connecte": True, "message": "connecté"})
    page = web.get("/").text
    assert 'id="ia-deconnectee"' not in page
    assert "Aucun poste identifié pour le moment" in page and 'href="/offres">Voir les offres' in page
    # Rien à faire : l'état vide remplace la page ; aucune section vide, pas de courrier sans candidature.
    for titre in ("À faire aujourd'hui", "À trancher", "Go donné", "Courrier des employeurs", "Relances dues",
                  "Écartées limites", 'class="chiffres"'):
        assert titre not in page
    annonce(reg, statut="A")
    reg.commit()
    page = web.get("/").text
    assert "À trancher" in page and "Aucun poste identifié" not in page
    assert "Go donné" not in page and "Courrier des employeurs" not in page and "Relances dues" not in page
