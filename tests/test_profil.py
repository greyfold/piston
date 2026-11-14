import json
import stat

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import FIXTURES
from jobsearch import config, coordonnees, dossier, evaluation, prefilter, profil

AVANT = json.loads((FIXTURES / "consignes_avant_profil.json").read_text(encoding="utf-8"))


def _lire(chemin):
    return yaml.safe_load(chemin.read_text(encoding="utf-8"))


# --- Consignes : celles d'avant le profil, pour le profil de Camille ------------------


def test_consigne_de_notation_inchangee():
    assert evaluation.consigne() == AVANT["evaluation"]
    assert evaluation.modele().model_json_schema() == AVANT["schema"]


def test_consigne_du_dossier_ne_change_qu_aux_endroits_voulus():
    pieces = dossier.pieces_a_ecrire()
    # 09.11 : la base Parcours de l'application (éléments E-n) s'ajoute aux preuves P-xx.
    parcours = [("preuve P-xx ou poste", "preuve P-xx, élément E-n du parcours ou poste"),
                ("Tu peux citer P-xx ici", "Tu peux citer P-xx et E-n ici"),
                ("depuis le CV maître pour l'angle", "depuis le CV maître (ou les réalisations de la base Parcours) pour l'angle"),
                ("Aucune référence P-xx, aucun lien", "Aucune référence P-xx ni E-n, aucun lien"),
                ("Un chiffre vient de Preuves.md ou n'existe pas.",
                 "Un chiffre vient des preuves ou du parcours (Preuves.md, base Parcours) ou n'existe pas."),
                ("aucune référence P-xx, aucun tag", "aucune référence P-xx ou E-n, aucun tag")]

    def avec_parcours(t):
        for a, b in parcours:
            t = t.replace(a, b)
        return t
    AVANT_ = {k: avec_parcours(v) for k, v in AVANT["pieces"].items()}
    assert pieces["analyse"] == AVANT_["analyse"]
    # 27.10 : deux pages pour le CV et guides de Harvard, ajoutés à la fin des consignes.
    ordre = (" (profil, compétences, expérience, formation, certifications, langues), une seule colonne",
             ", dans cet ordre : profil, expérience, compétences, formation, certifications, langues ; une seule colonne")
    assert pieces["cv"].startswith(AVANT_["cv"].replace(*ordre) + " Le CV tient sur 2 pages A4 au plus")
    assert pieces["lettre"].startswith(AVANT["pieces"]["lettre"] + "\n\nRègles des guides de Harvard")
    # 09.11 : la ligne ORP et la rubrique Job-Room s'écrivent sans Claude (dossier.completer_envoi).
    envoi = AVANT["pieces"]["envoi"].replace(", assignation ORP non)", ")").split(", « ## 5. Job-Room »")[0]
    assert pieces["envoi"].startswith(envoi + ". Rien après la rubrique 4. Dans « 0. Avant d'envoyer »")
    assert "ORP" not in pieces["envoi"] and "Job-Room" not in pieces["envoi"]
    # 11.11 : les règles particulières de l'onglet Dossier, retiré, ne s'appliquent plus.
    regles = "- Brevet fédéral en cours : dans la formation, pas dans les certifications. Green Motion : rester vague sur le lien e-commerce / ERP.\n"
    assert regles in AVANT["dossier"]
    attendu = (avec_parcours(AVANT["dossier"]).replace("à laquelle il a dit Go", "à laquelle Camille a dit Go")
               .replace(regles, ""))
    assert dossier.consigne() == attendu
    assert "Camille a relu" in dossier.REVISION % dossier._valeurs()


def test_profil_vide_aucun_nom_en_dur(profil_de_test, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)                  # ni ancien coordonnees.yaml, ni CV
    profil_de_test.write_text("", encoding="utf-8")
    profil.oublier()
    textes = [evaluation.consigne(), dossier.consigne(), dossier.REVISION % dossier._valeurs(),
              *dossier.pieces_a_ecrire().values(), json.dumps(evaluation.modele().model_json_schema(), ensure_ascii=False)]
    for t in textes:
        assert "Camille" not in t and "Lausanne" not in t and "Suisse romande" not in t and "%(" not in t
    assert "évalue chacun des critères non négociables du profil" in evaluation.consigne()
    assert dossier.nom_fichier({"entreprise": "Exemple SA", "titre": "PM"}, "cv") == "Candidature-CV-Exemple-PM.docx"


def test_noms_de_fichiers_et_coordonnees_du_profil():
    assert dossier.nom_fichier({"entreprise": "Exemple SA", "titre": "PM"}, "lettre") == "Camille-Exemple-Cover-Letter-Exemple-PM.docx"
    v = coordonnees.valeurs_formulaire({}, "")
    assert v["nom_complet"] == "Camille Exemple" and v["email"] == "camille@example.ch" and v["npa"] == "1000"


def test_nouvelle_personne_partout(profil_de_test):
    p = _lire(profil_de_test)
    p["identite"].update(prenom="Anne-Sophie", nom="Dürr", ville="Sion")
    p["recherche"] = {"niveau": "confirmé", "cantons": ["TI"]}          # la région se déduit des cantons
    p["evaluation"] = {"criteres": ["trajet : depuis Sion", "langue"], "angles": ["Finance", "Contrôle de gestion"]}
    p["dossier"] = {"differenciateur": "rigueur"}
    profil.ecrire(p)
    c = evaluation.consigne()
    assert c.startswith("Tu évalues des offres d'emploi pour Anne-Sophie Dürr, qui cherche un poste confirmé en Suisse italienne.")
    assert "chacun des deux critères (trajet depuis Sion, langue)" in c and "marché du poste en Suisse italienne" in c
    schema = evaluation.modele().model_json_schema()
    assert schema["properties"]["angle"]["description"] == "Finance ou Contrôle de gestion"
    assert schema["$defs"]["Critere"]["properties"]["critere"]["description"] == "trajet, langue"
    assert "« Sion, le 26 octobre 2026 »" in dossier.pieces_a_ecrire()["lettre"]
    assert "différenciateur rigueur" in dossier.pieces_a_ecrire()["lettre"]
    assert "un des deux angles" in dossier.pieces_a_ecrire()["analyse"]
    assert dossier.nom_fichier({"entreprise": "X", "titre": "Y"}, "cv").startswith("Anne-Sophie-Durr-CV-")


# --- Superposition aux fichiers de config ------------------------------------------


def test_valeurs_par_defaut_non_recopiees(profil_de_test):
    """Enregistrer sans rien changer laisse config/ maître : le profil ne recopie pas les listes."""
    profil.enregistrer("recherche", _form("recherche"))
    assert _lire(profil_de_test)["recherche"] == {"niveau": "Senior"}           # option de la liste ; région déduite
    assert config.criteres()["familles_cibles"] == config.lire_yaml("criteres.yaml")["familles_cibles"]


def test_cantons_du_profil_changent_sourcing_et_pre_tri(profil_de_test):
    form = _form("recherche")
    form["recherche.cantons"] = "vs\nVD\n"
    form["recherche.postes"] = "Comptable\nResponsable comptabilité | Head of accounting, comptable"
    profil.enregistrer("recherche", form)
    p = _lire(profil_de_test)
    assert p["recherche"]["cantons"] == ["VS", "VD"]
    assert p["recherche"]["postes"] == [{"intitule": "Comptable", "variantes": []},
                                        {"intitule": "Responsable comptabilité", "variantes": ["Head of accounting"]}]
    assert "requetes" not in p["recherche"] and "familles" not in p["recherche"]
    c = config.criteres()
    assert c["sourcing"]["requetes"] == ["Comptable", "Responsable comptabilité", "Head of accounting"]
    assert c["sourcing"]["linkedin_requetes"] == ["Comptable", '"Responsable comptabilité" OR "Head of accounting"']
    # « responsable » et « head » sont des mots de fonction : seuls les mots du métier filtrent.
    assert c["familles_cibles"] == r"\b(comptable(?:s|e|es|aux|ale|ales)?|comptabilite(?:s|e|es|aux|ale|ales)?|" \
                                   r"accounting(?:s|e|es|aux|ale|ales)?)\b"
    assert c["sourcing"]["cantons"] == ["VS", "VD"]
    regle = next(r for r in c["regles"] if r["id"] == "canton")
    assert regle["motif"] == "Lieu : hors des cantons VS, VD"
    f = {"titre": "Responsable comptabilité", "entreprise": "X", "canton": "GE", "texte": ""}
    assert prefilter.familles().search(prefilter.plier(f["titre"]).lower())
    assert any(r["id"] == "canton" and r["_re"].search("GE") for r in prefilter.regles())
    assert not any(r["id"] == "canton" and r["_re"].search("VS") for r in prefilter.regles())


def test_liste_videe_volontairement_reste_vide(profil_de_test):
    form = _form("recherche")
    form["recherche.entreprises"] = ""
    profil.enregistrer("recherche", form)
    assert config.criteres()["sourcing"]["entreprises"] == []


def _form(ident):
    return {c["cle"]: v if c["type"] == "postes" else
            "\n".join(v) if isinstance(v, list) else str(v)
            for c in profil.etape(ident)["champs"] for v in [profil.effectif(c["cle"])]}


@pytest.mark.parametrize("cantons,region", [
    (["VD", "VS", "FR", "NE"], "Suisse romande"), (["ZH", "BE"], "Suisse alémanique"), (["TI"], "Suisse italienne"),
    (["GE", "ZH"], "Suisse"), ([], "Suisse"),
])
def test_region_deduite_des_cantons(profil_de_test, cantons, region):
    p = _lire(profil_de_test)
    p["recherche"] = {"cantons": cantons} if cantons else {}
    p["identite"]["pays"] = "Suisse"
    assert profil.region(p) == region


def test_cantons_en_pilules_et_lieux_jobup_deduits(profil_de_test, web):
    """Les cantons se cochent par leur nom ; jobup cherche dans chacun, l'ancien champ s'efface."""
    p = _lire(profil_de_test)
    p.setdefault("recherche", {})["jobup_lieux"] = ["Sion"]
    profil.ecrire(p)
    page = web.get("/profil/recherche").text
    assert 'name="recherche.cantons" value="VS"' in page and ">Valais</span>" in page
    assert "recherche.jobup_lieux" not in page
    form = _form("recherche")
    form["recherche.cantons"] = ["VS", "ne"]                 # pilules cochées, comme le formulaire web
    profil.enregistrer("recherche", form)
    p = _lire(profil_de_test)["recherche"]
    assert p["cantons"] == ["VS", "NE"] and "jobup_lieux" not in p
    assert config.criteres()["sourcing"]["jobup_lieux"] == ["Valais", "Neuchâtel"]


def test_lieu_linkedin_et_lieux_indeed_deduits(profil_de_test, web, monkeypatch):
    form = _form("recherche")
    form["recherche.postes"] = [{"intitule": "Contrôleur de gestion", "variantes": ["Controller"]}]
    form["recherche.cantons"] = "VS\nVD"
    p = _lire(profil_de_test)
    p["identite"]["ville"] = "Sion"
    p["recherche"]["linkedin_lieu"] = "Lausanne, Vaud, Switzerland"         # l'ancien champ : effacé
    profil.ecrire(p)
    assert "recherche.linkedin_lieu" not in web.get("/profil/recherche").text
    profil.enregistrer("recherche", form)
    assert "linkedin_lieu" not in _lire(profil_de_test)["recherche"]
    c = config.criteres()["sourcing"]
    assert c["linkedin_requetes"] == ['"Contrôleur de gestion" OR "Controller"']
    assert c["linkedin_lieu"] == "Sion, Switzerland"                         # l'adresse du profil
    assert profil.lieu_linkedin({"identite": {"ville": "Lyon", "pays": "France"}}) == "Lyon, France"
    # Lieux Indeed : ceux de config/ s'il y en a, sinon les cantons du profil, sinon sa localité.
    assert config.reglages()["indeed"]["lieux"] == config.lire_yaml("settings.yaml")["indeed"]["lieux"]
    vides = {"indeed": {"lieux": []}}
    assert profil.appliquer(vides, "reglages")["indeed"]["lieux"] == ["Valais", "Vaud"]
    monkeypatch.setattr(config, "lire_yaml", lambda nom: {"sourcing": {"cantons": []}})
    assert profil.lieux_indeed({"identite": {"ville": "Sion"}}) == ["Sion"]


def test_postes_saisis_en_lignes_et_anciennes_cles(web, profil_de_test):
    p = _lire(profil_de_test)
    p["recherche"].update(requetes=["UX designer"], familles=["ux"], linkedin_requetes=["x"], indeed_lieux=["Sion"])
    profil.ecrire(p)
    assert profil.postes() == [{"intitule": "UX designer", "variantes": []}]       # anciennes requêtes : un poste chacune
    page = web.get("/profil/recherche").text
    assert 'name="recherche.postes.intitule" value="UX designer"' in page and "Requêtes LinkedIn" not in page
    assert "Lieux Indeed" not in page and "Mots des intitulés visés" not in page and "pré-tri : ux, designer" in page
    form = {k: v for k, v in _form("recherche").items() if k != "recherche.postes"}
    form["recherche.postes.intitule"] = ["Product designer", "", "  "]
    form["recherche.postes.variantes"] = ["UX designer ; UI designer", "", "Orphelin"]
    r = web.post("/profil/recherche", data=form, follow_redirects=False)
    assert r.status_code == 303
    rech = _lire(profil_de_test)["recherche"]
    assert rech["postes"] == [{"intitule": "Product designer", "variantes": ["UX designer", "UI designer"]},
                              {"intitule": "Orphelin", "variantes": []}]
    assert not {"requetes", "familles", "linkedin_requetes", "indeed_lieux"} & set(rech)
    form["recherche.postes.intitule"] = [""]
    form["recherche.postes.variantes"] = [""]
    assert "Postes recherchés : obligatoire" in web.post("/profil/recherche", data=form).text


def test_indeed_sans_lieu_prend_la_ville_du_profil(profil_de_test):
    from jobsearch.sources.indeed import Indeed
    assert Indeed({"lieux": []}, ["PM"], ["VD"]).recherches() == [("PM", "Lausanne")]


def test_exclusions_par_cases_et_mots(profil_de_test):
    form = _form("pretri")
    actives = form["exclusions.regles"].split("\n")
    assert "titre-allemand" in actives and "canton" not in actives        # le canton suit l'étape Recherche
    form["exclusions.regles"] = "\n".join(i for i in actives if "allemand" not in i and i != "titre-vente")
    form["exclusions.mots_titre"] = "Comptable\nchef de projet"
    profil.enregistrer("pretri", form)
    p = _lire(profil_de_test)["exclusions"]
    assert set(p["desactivees"]) == {"titre-vente", "titre-allemand", "langue-allemand", "allemand-exige"}
    assert p["mots_titre"] == ["comptable", "chef de projet"]
    assert prefilter.evaluer({"titre": "Product Owner (m/w/d)", "entreprise": "X", "langue": "de"}) is None
    assert prefilter.evaluer({"titre": "Sales Product Manager", "entreprise": "X"}) is None
    r = prefilter.evaluer({"titre": "Chef de projet digital", "entreprise": "X"})
    assert r["id"] == "titre-profil" and r["passe"] == 0 and r["motif"].startswith("Métier exclu")
    assert prefilter.evaluer({"titre": "Software Engineer", "entreprise": "X"})["id"] == "titre-ingenierie"
    assert "intitulé en allemand" in profil.affiche(profil.etape("pretri")["champs"][0])


def test_exclusions_toutes_cochees_rien_d_ecrit(profil_de_test):
    profil.enregistrer("pretri", _form("pretri"))
    assert "exclusions" not in _lire(profil_de_test)
    assert config.criteres()["regles"] == config.lire_yaml("criteres.yaml")["regles"]


def test_mots_des_postes_pour_le_pre_tri():
    ps = profil.normaliser_postes([{"intitule": "UX designer (H/F)", "variantes": ["Chef de projet digital"]}])
    mots = profil.mots_postes(ps)
    assert mots == ["ux", "designer", "digital"]              # ni « chef » ni « projet », mots de fonction
    motif = prefilter._compiler(profil.familles_en_motif(mots))
    assert motif.search("lead ux") and motif.search("chef de projet digital") and motif.search("solutions digitales")
    assert not motif.search("comptable") and not motif.search("chef de projet construction")


def test_mots_de_fonction_seuls_ne_suffisent_pas():
    """Constat du 13.11 : « chef », « responsable », « manager » laissaient passer cheffe de cuisine,
    chef de partie et responsable de ligne de production. Un poste fait de ces seuls mots
    (« Chef de projet ») exige l'intitulé entier."""
    ps = profil.normaliser_postes([
        {"intitule": "Innovation Manager", "variantes": ["Responsable innovation", "Chef de projet innovation"]},
        {"intitule": "Product Owner", "variantes": ["Product Manager", "Responsable produit"]},
        {"intitule": "Chef de projet", "variantes": []}])
    mots = profil.mots_postes(ps)
    assert mots == ["innovation", "product", "owner", "produit", "chef projet"]
    motif = prefilter._compiler(profil.familles_en_motif(mots))
    for titre in ("Cheffe de cuisine", "Chef de Partie 100 % (H/F)", "Responsable de ligne de production",
                  "Infirmier/ère diplômé(e)", "Responsable secteur technique et nettoyage", "Chef d'atelier",
                  "Manager Medical Safety"):
        assert not motif.search(prefilter.plier(titre)), titre
    for titre in ("Head of Innovation", "PRODUCT MANAGER-MOTION SYSTEMS", "Responsable Produits et Solutions Digitales",
                  "Chef de projet CVC", "Cheffe ou chef de projet en génie civil"):
        assert motif.search(prefilter.plier(titre)), titre


def test_objectifs_et_courrier(profil_de_test):
    profil.enregistrer("objectifs", {"objectifs.semaine_min": "0", "objectifs.mois_min": ""})
    r = config.reglages()
    assert r["objectifs"]["semaine_min"] == 0
    assert r["objectifs"]["mois_min"] == 0                       # facultatif : vide, pas d'objectif
    assert profil.etape("objectifs")["titre"] == "Objectifs de postulation"
    # Les comptes de Mail se choisissent sur la page Connecteurs, plus dans Ma recherche.
    assert not [c for e in profil.etapes() for c in e["champs"] if c["cle"] == "courrier.comptes"]
    assert profil.poser_comptes_courrier(" iCloud \nicloud\n\nTravail") == ["iCloud", "Travail"]
    assert config.reglages()["courrier"]["comptes"] == ["iCloud", "Travail"] == profil.comptes_courrier()
    profil.poser_comptes_courrier("\n".join(profil.base("courrier.comptes")))
    assert "comptes" not in (profil.charger().get("courrier") or {})   # comme config/ : config/ reste maître
    assert "coffre" not in r and not [c for e in profil.etapes() for c in e["champs"] if c["cle"] == "coffre"]


# --- Validation ---------------------------------------------------------------------


@pytest.mark.parametrize("etape,cle,valeur,message", [
    ("recherche", "recherche.cantons", "XY", "abréviation de canton"),
    ("identite", "identite.email", "pas-une-adresse", "adresse invalide"),
    ("identite", "identite.prenom", "  ", "obligatoire"),
    ("objectifs", "objectifs.semaine_min", "deux", "nombre entier"),
    ("identite", "identite.date_naissance", "31.02.1980", "jj.mm.aaaa"),
])
def test_saisie_refusee_sans_rien_ecrire(profil_de_test, etape, cle, valeur, message):
    avant = profil_de_test.read_text(encoding="utf-8")
    form = _form(etape)
    form[cle] = valeur
    with pytest.raises(profil.ProfilInvalide, match=message):
        profil.enregistrer(etape, form)
    assert profil_de_test.read_text(encoding="utf-8") == avant


def test_civilite_et_date_de_naissance_vont_au_formulaire(profil_de_test):
    form = {c["cle"]: str(profil.effectif(c["cle"])) for c in profil.etape("identite")["champs"]}
    form.update({"identite.civilite": "Monsieur", "identite.date_naissance": "3/7/1980"})
    profil.enregistrer("identite", form)
    v = coordonnees.valeurs_formulaire({}, "")
    assert v["civilite"] == "Monsieur" and v["date_naissance"] == "03.07.1980"
    assert "1980" not in dossier.consigne() and "1980" not in "".join(dossier.pieces_a_ecrire().values())


def test_aucun_champ_de_salaire():
    assert not any("salaire" in c["cle"] or "plancher" in c["cle"] for e in profil.ETAPES for c in e["champs"])


def test_coordonnees_reprises_de_l_ancien_fichier(profil_de_test, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    (tmp_path / "coordonnees.yaml").write_text("prenom: Ancien\nnom: Fichier\nville: Nyon\n", encoding="utf-8")
    p = _lire(profil_de_test)
    del p["identite"]
    profil.ecrire(p)
    assert profil.nom_complet() == "Ancien Fichier"
    profil.enregistrer("objectifs", {"objectifs.semaine_min": "", "objectifs.mois_min": ""})
    assert _lire(profil_de_test)["identite"]["ville"] == "Nyon"       # reprises une fois dans le profil


def test_coordonnees_depuis_un_cv():
    c = coordonnees._depuis_cv("# Anne Exemple\n## Titre\nRue 1, 1950 Sion · +41 79 000 00 00 · anne@example.ch\n")
    assert c == {"prenom": "Anne", "nom": "Exemple", "adresse": "Rue 1", "npa": "1950", "ville": "Sion",
                 "telephone": "+41 79 000 00 00", "email": "anne@example.ch"}


# --- L'assistant dans le tableau de bord --------------------------------------------


@pytest.fixture
def web(reg, monkeypatch):
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    return TestClient(appmod.app)


def test_premier_lancement_ouvre_l_assistant(web, profil_de_test, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    profil_de_test.unlink()
    profil.oublier()
    r = web.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/bienvenue"      # les avantages, puis l'assistant
    assert web.get("/profil", follow_redirects=False).headers["location"] == "/profil/identite"
    page = web.get("/profil/identite").text
    assert 'aria-current="step"' in page and "Prénom" in page


def test_etapes_enregistrees_dans_l_ordre(web, profil_de_test):
    page = web.get("/profil/identite").text
    assert 'value="camille@example.ch"' in page and 'autocomplete="given-name"' in page
    form = {c["cle"]: str(profil.effectif(c["cle"])) for c in profil.etape("identite")["champs"]}
    form["identite.telephone"] = "+41 79 111 11 11"
    form["identite.site"] = "www.exemple.ch"
    r = web.post("/profil/identite", data=form, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/profil/recherche?msg=")
    p = _lire(profil_de_test)
    assert p["identite"]["telephone"] == "+41 79 111 11 11" and p["identite"]["site"] == "https://www.exemple.ch"
    assert stat.S_IMODE(profil_de_test.stat().st_mode) == 0o600
    assert "par défaut" in web.get("/profil/recherche").text       # listes encore celles de criteres.yaml


def test_erreur_garde_la_saisie(web, profil_de_test):
    form = {c["cle"]: str(profil.effectif(c["cle"])) for c in profil.etape("identite")["champs"]}
    form.update({"identite.email": "faux", "identite.ville": "Montreux"})
    r = web.post("/profil/identite", data=form)
    assert r.status_code == 200 and "adresse invalide" in r.text and 'value="Montreux"' in r.text
    assert _lire(profil_de_test)["identite"]["ville"] == "Lausanne"


def test_page_pre_tri_et_cases_postees(web, profil_de_test):
    page = web.get("/profil/pretri").text
    assert 'type="checkbox" name="exclusions.regles" value="allemand-exige" checked' in page
    assert "<legend>Langue" in page and "Annonces écartées d" in page and "Autres intitulés à écarter" in page
    assert "Postes recherchés" in web.get("/profil/recherche").text
    actives = [i for i in profil.effectif("exclusions.regles") if i != "taux"]
    r = web.post("/profil/pretri", data={"exclusions.regles": actives, "exclusions.mots_titre": ""},
                 follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/profil/criteres?")
    assert _lire(profil_de_test)["exclusions"] == {"desactivees": ["taux"]}
    assert 'value="taux" checked' not in web.get("/profil/pretri").text


def test_plus_de_recapitulatif(web, profil_de_test):
    """Le récapitulatif n'existe plus : ni dans le menu, ni comme page ; la dernière étape reste ouverte."""
    page = web.get("/profil/identite").text
    assert "Récapitulatif" not in page and "/profil/fin" not in page
    r = web.get("/profil/fin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/profil"
    derniere = profil.etapes()[-1]["id"]
    form = {c["cle"]: "\n".join(v) if isinstance(v := profil.affiche(c), list) else str(v or "")
            for c in profil.etape(derniere)["champs"]}
    r = web.post("/profil/%s" % derniere, data=form, follow_redirects=False)
    assert r.headers["location"].startswith("/profil/%s?" % derniere)
    p = _lire(profil_de_test)
    p["identite"]["prenom"] = ""
    profil.ecrire(p)
    assert "Profil incomplet (Prénom)" in web.get("/candidatures").text
    assert web.get("/profil/inconnue", follow_redirects=False).status_code == 303


def test_certifications_sans_doublon_et_reprises_dans_le_cv():
    form = {"dossier.certifications": ("Certified Data Professional | The Analytics Institute | 02.2025\n"
                                      "Claude Academy: Claude 101 | Anthropic | 09.2026\n"
                                      "certified data professional | The Analytics Institute | 11.2024\n")}
    profil.enregistrer("certifications", form)
    assert profil.lire(profil.charger(), "dossier.certifications") == [
        "Certified Data Professional | The Analytics Institute | 02.2025", "Claude Academy: Claude 101 | Anthropic | 09.2026"]
    cv = dossier.pieces_a_ecrire()["cv"]
    assert "- Certified Data Professional | The Analytics Institute | 02.2025\n" in cv and "11.2024" not in cv
    assert "remplace celle du CV maître" in cv and "chacune une seule fois" in cv


def test_certifications_et_langues_en_colonnes(web, profil_de_test):
    """Une ligne par certification (titre, organisme, date) et par langue (langue, niveau) ; enregistrées
    « a | b | c » comme avant, la date remise en MM.AAAA."""
    form = {"dossier.certifications.titre": ["SAFe Product Owner", "", "Scrum Master"],
            "dossier.certifications.organisme": ["Scaled Agile, Inc", "", "Scrum | Alliance"],
            "dossier.certifications.date": ["9/2024", "", "2021"]}
    assert web.post("/profil/certifications", data=form, follow_redirects=False).status_code == 303
    assert profil.lire(profil.charger(), "dossier.certifications") == [
        "SAFe Product Owner | Scaled Agile, Inc | 09.2024", "Scrum Master | Scrum / Alliance | 2021"]
    page = web.get("/profil/certifications").text
    assert "<h1>Ma recherche</h1>" in page and "<h1>Profil</h1>" not in page
    assert 'name="dossier.certifications.organisme" value="Scaled Agile, Inc"' in page
    assert 'name="dossier.certifications.date" value="09.2024"' in page and "trois-colonnes" in page
    assert "Ajouter une certification" in page
    form["dossier.certifications.date"] = ["septembre", "", "2021"]
    page = web.post("/profil/certifications", data=form).text
    assert "n&#39;est pas une date MM.AAAA" in page or "n'est pas une date MM.AAAA" in page
    assert 'name="dossier.certifications.date" value="septembre"' in page        # la saisie reste à corriger
    form = {"dossier.certifications.titre": [""], "dossier.certifications.organisme": ["Anthropic"],
            "dossier.certifications.date": [""]}
    assert "une ligne n" in web.post("/profil/certifications", data=form).text

    form = {"dossier.langues.langue": ["Anglais", "anglais", "Allemand", "Italien"],
            "dossier.langues.niveau": ["Courant (C1)", "B2", "notions", "b1"],
            "dossier.langues.attestation": ["Cambridge C1 Advanced", "", "", ""]}
    assert web.post("/profil/langues", data=form, follow_redirects=False).status_code == 303
    assert profil.lire(profil.charger(), "dossier.langues") == [
        "Anglais | Courant (C1) | Cambridge C1 Advanced", "Allemand | notions", "Italien | Intermédiaire (B1)"]
    page = web.get("/profil/langues").text
    assert '<select name="dossier.langues.niveau"' in page and "<option selected>Courant (C1)</option>" in page
    assert "<option selected>notions</option>" in page            # saisie d'avant la liste : gardée, pas convertie
    assert "Ajouter une langue" in page and "pas besoin d&#39;un certificat" in page
    cv = dossier.pieces_a_ecrire()["cv"]
    assert "- Allemand | notions\n" in cv and "auto-évaluation" in cv and "jamais « certifié »" in cv


def test_niveau_langue_sans_rien_ajouter():
    assert [profil.niveau_langue(x) for x in ("courant (C1)", "C2", "maternelle", "Bilingue", "courant", "notions",
                                              "C1, corrigé à la main")] == [
        "Courant (C1)", "Maîtrise (C2)", "Langue maternelle", "Bilingue", "courant", "notions", "C1, corrigé à la main"]


def test_mois_annee():
    assert [profil.mois_annee(x) for x in ("03.2024", "3/2024", "2024-03", "2024", "", "13.2024", "mars")] == [
        "03.2024", "03.2024", "03.2024", "2024", "", None, None]


def test_objectifs_facultatifs(profil_de_test):
    base = config.lire_yaml("settings.yaml")["objectifs"]
    profil.enregistrer_objectifs("", "12")
    assert profil.objectifs_rythme() == {"semaine": 0, "mois": 12}
    profil.enregistrer_objectifs(str(base["semaine_min"]), str(base["mois_min"]))
    assert "objectifs" not in profil.charger()                 # comme config/ : config/ reste maître
    with pytest.raises(profil.ProfilInvalide):
        profil.enregistrer_objectifs("trois", "")
    assert profil.objectifs_rythme()["semaine"] == base["semaine_min"]


def test_criteres_structures_et_ancien_format():
    """Un champ par critère ; un profil d'avant (une phrase par critère) se relit champ par champ."""
    d = {"trajet": "45", "trajet_depuis": "Morges", "teletravail": "2", "taux_min": 80, "taux_max": 100,
         "contrats": ["cdi", "cdd-long"], "langues": ["français", "anglais"], "encadrement": "non",
         "organisations": ["PME", "ONG ou associatif"], "secteurs_exclus": ["tabac", "armement"], "heures_max": 42,
         "vacances_min": 5, "deplacements": "occasionnels", "a_eviter": "trop de réunions",
         "autres": ["présence : 3 jours au bureau"]}
    lignes = profil.lignes_criteres(d)
    assert lignes[0] == "trajet : 45 minutes au plus depuis Morges" and lignes[-1] == "présence : 3 jours au bureau"
    assert "organisation : PME ou ONG ou associatif" in lignes
    assert profil.criteres_depuis_lignes(lignes) == d                       # aller-retour sans perte
    assert profil.normaliser_criteres(d) == d
    # Tout coché, 100 % : rien à vérifier, pas de phrase.
    assert profil.lignes_criteres({"contrats": list(profil.CONTRATS), "taux_min": 100, "taux_max": 100}) == []
    for mauvais in ({"trajet": "120"}, {"taux_max": "80"}, {"taux_min": "90", "taux_max": "80"}, {"heures_max": "beaucoup"}):
        with pytest.raises(profil.ProfilInvalide):
            profil.normaliser_criteres(mauvais)
    assert profil.normaliser_criteres({"langues": ["klingon", "anglais"], "autres": ["a", "a", " "]}) == {
        "langues": ["anglais"], "autres": ["a"]}
    assert "120" not in str(profil.normaliser_criteres({"a_eviter": "moins de 120k"}))      # aucun montant


def test_etape_criteres_un_champ_par_critere(web, profil_de_test):
    page = web.get("/profil/criteres").text
    assert 'name="critere.trajet"' in page and 'name="critere.contrats" value="cdi"' in page
    assert 'name="evaluation.criteres"' not in page
    # Le profil de test, d'avant les champs : ses phrases restent, cochées, dans « Autres critères ».
    assert 'name="critere.autres" value="dimension IA" checked' in page
    assert 'name="evaluation.angles" value="UX leadership" checked' in page
    form = {"critere.teletravail": "1", "critere.langues": ["français"], "critere.autres": ["dimension IA"],
            "evaluation.angles": ["UX leadership"], "evaluation.angles.ajout": "Pont métier-IA",
            "evaluation.interet": "", "evaluation.activites.ajout": ""}
    r = web.post("/profil/criteres", data=form, follow_redirects=False)
    assert r.status_code == 303
    p = _lire(profil_de_test)
    assert p["evaluation"]["criteres"] == {"teletravail": "1", "langues": ["français"], "autres": ["dimension IA"]}
    assert p["evaluation"]["angles"] == ["UX leadership", "Pont métier-IA"]
    assert profil.criteres_evaluation() == [("télétravail", "1 jour par semaine au moins"),
                                            ("langues de travail", "français"), ("dimension IA", "")]
    r = web.post("/profil/criteres", data={"critere.trajet": "120"}, follow_redirects=False)
    assert "choisir dans la liste" in r.text or "msg=" in r.headers.get("location", "")


def test_niveau_vise_en_liste(web, profil_de_test):
    """Le niveau se choisit dans une liste ; « senior » écrit avant elle y retrouve son option."""
    page = web.get("/profil/recherche").text
    assert '<select id="recherche.niveau"' in page and "<option selected>Senior</option>" in page
    assert profil.niveau_connu(" direction ") == "Direction" and profil.niveau_connu("expert") == ""
    assert "cherche un poste senior en" in evaluation.consigne()


def test_cles_obsoletes_retirees(profil_de_test):
    p = _lire(profil_de_test)
    p["dossier"].update(regles=["Une consigne"], pages_cv=3)
    p["interface"] = {"jobroom": True, "objectifs": False}
    profil.ecrire(p)
    lu = profil.charger()
    assert "regles" not in lu["dossier"] and "pages_cv" not in lu["dossier"] and lu["dossier"]["differenciateur"]
    assert lu["interface"] == {"jobroom": True}
    profil.ecrire(lu)                                              # au prochain enregistrement : hors du fichier
    assert "objectifs" not in _lire(profil_de_test)["interface"] and "regles" not in _lire(profil_de_test)["dossier"]
    assert dossier.PAGES_CV == 2 and "Une consigne" not in dossier.consigne()
