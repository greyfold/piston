"""Le planificateur de l'application et la configuration livrée."""

import datetime as dt
from pathlib import Path

import yaml

from jobsearch import echeances

RACINE = Path(__file__).resolve().parents[1]


def _passage(reg, debut, statut="complet", declencheur="application"):
    reg.cx.execute("INSERT INTO runs (debut, fin, statut, declencheur) VALUES (?,?,?,?)",
                   (debut, debut, statut, declencheur))
    reg.commit()


def _faux(appels):
    return {nom: (lambda reg, now, nom=nom: appels.append(nom) or {"ok": nom}) for nom in echeances.TACHES}


def test_rien_avant_l_heure_du_passage(reg):
    reg.poser_meta("echeance_surveillance", "2026-11-10 05:50")
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 6, 0)) == []


def test_passage_une_fois_par_jour_puis_l_alerte(reg):
    matin = dt.datetime(2026, 11, 10, 6, 35)
    assert echeances.dues(reg, matin) == ["passage", "surveillance"]
    _passage(reg, "2026-11-10 06:35", statut="demarre")
    reg.poser_meta("echeance_surveillance", "2026-11-10 06:35")
    assert echeances.dues(reg, matin + dt.timedelta(minutes=5)) == []        # en cours : rien
    reg.cx.execute("UPDATE runs SET statut='partiel'")
    assert echeances.dues(reg, matin + dt.timedelta(minutes=10)) == ["alerte"]
    reg.poser_meta("alerte_matin_le", "2026-11-10 06:50")
    assert echeances.dues(reg, matin + dt.timedelta(minutes=15)) == []
    # Le lendemain, avant l'heure : rien ; la surveillance revient chaque heure.
    assert echeances.dues(reg, dt.datetime(2026, 11, 11, 6, 0)) == ["surveillance"]


def test_le_passage_leger_de_l_accueil_ne_compte_pas(reg):
    _passage(reg, "2026-11-10 06:00", declencheur="accueil")
    reg.poser_meta("echeance_surveillance", "2026-11-10 07:00")
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 7, 1)) == ["passage"]


def test_mac_en_veille_a_l_heure_dite_le_passage_part_au_reveil(reg):
    reg.poser_meta("echeance_surveillance", "2026-11-10 21:30")
    assert echeances.dues(reg, dt.datetime(2026, 11, 10, 22, 0)) == ["passage"]


def test_executer_note_la_surveillance_et_continue_apres_un_echec(reg):
    appels = []
    taches = _faux(appels)
    taches["passage"] = lambda reg, now: (_ for _ in ()).throw(RuntimeError("panne"))
    bilan = echeances.executer(reg, dt.datetime(2026, 11, 10, 7, 0), taches)
    assert bilan["passage"] == {"erreur": "panne"} and appels == ["surveillance"]
    assert reg.meta("echeance_surveillance") == "2026-11-10 07:00"


def test_heure_illisible_repli_sur_6h30():
    assert echeances._heure("pas une heure") == dt.time(6, 30) and echeances._heure("7:05") == dt.time(7, 5)


def test_la_configuration_livree_ne_contient_rien_de_personnel():
    lire = lambda nom: yaml.safe_load((RACINE / "config" / nom).read_text(encoding="utf-8"))
    s, c = lire("settings.yaml"), lire("criteres.yaml")
    assert "coffre" not in s and s["notifications"]["ntfy_sujet"] == "" and s["web"]["hote"] == "127.0.0.1"
    assert s["objectifs"]["semaine_min"] == 0 and s["objectifs"]["mois_min"] == 0
    assert s["courrier"]["comptes"] == [] and s["indeed"]["lieux"] == []
    src = c["sourcing"]
    assert src["cantons"] == [] and src["jobup_lieux"] == [] and src["requetes"] == [] and src["entreprises"] == []
    assert src["linkedin_requetes"] == [] and src["linkedin_lieu"] == "" and c["familles_cibles"] == ""
    assert not [r for r in c["regles"] if r["motif"].startswith("Métier exclu")]
    canton = next(r for r in c["regles"] if r["id"] == "canton")
    assert canton["regex"] == "(?!)"                    # sans cantons choisis, la règle n'écarte rien
    assert set(lire("sources.yaml")) == {"job-room", "jobup", "linkedin", "mails"}
    for f in (RACINE / "config").iterdir():
        assert "jeremy" not in f.read_text(encoding="utf-8").lower(), f.name


def test_le_code_ne_nomme_personne():
    for f in (RACINE / "src").rglob("*"):
        if f.suffix in (".py", ".html", ".js", ".css", ".md", ".yaml"):
            texte = f.read_text(encoding="utf-8").lower()
            assert "jeremy" not in texte and "greyfold" not in texte, f


def test_dns_rebinding_exige_le_jeton():
    """Connexion locale mais nom d'hôte étranger (un site qui fait pointer son domaine sur
    127.0.0.1) : traitée comme venant du réseau, donc jeton exigé."""
    from fastapi.testclient import TestClient

    from jobsearch.web.app import app
    web = TestClient(app)
    assert web.get("/api/etat").status_code == 200
    r = web.get("/api/etat", headers={"host": "attaquant.example:8775"}, follow_redirects=False)
    assert r.status_code in (302, 303, 401, 403)
    assert web.get("/api/etat", headers={"host": "localhost:8775"}).status_code == 200
    assert web.get("/api/etat", headers={"host": "[::1]:8775"}).status_code == 200


def test_fichiers_crees_lisibles_par_le_seul_compte(tmp_path):
    import jobsearch  # noqa: F401  (pose le masque à l'import)
    from jobsearch.db import Registre
    r = Registre(tmp_path / "base.db")
    r.fermer()
    assert (tmp_path / "base.db").stat().st_mode & 0o077 == 0
