"""Le planificateur de l'application et la configuration livrée."""

import datetime as dt
from pathlib import Path

import yaml

from jobsearch import config, echeances

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
    for nom in ("settings.yaml", "criteres.yaml", "sources.yaml"):
        brut = yaml.safe_load((RACINE / "config" / nom).read_text(encoding="utf-8"))
        assert config._neutraliser(nom, yaml.safe_load((RACINE / "config" / nom).read_text(encoding="utf-8"))) == brut, nom
    s = yaml.safe_load((RACINE / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert s["coffre"] == "" and s["notifications"]["ntfy_sujet"] == "" and s["web"]["hote"] == "127.0.0.1"
    for f in (RACINE / "config").iterdir():
        assert "jeremy" not in f.read_text(encoding="utf-8").lower(), f.name


def test_le_code_ne_nomme_personne():
    for f in (RACINE / "src").rglob("*"):
        if f.suffix in (".py", ".html", ".js", ".css", ".md", ".yaml"):
            texte = f.read_text(encoding="utf-8").lower()
            assert "jeremy" not in texte and "greyfold" not in texte, f
