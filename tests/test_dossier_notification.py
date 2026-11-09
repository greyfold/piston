import json

import pytest

from conftest import annonce
from jobsearch import dossier


@pytest.fixture
def envois(monkeypatch):
    liste = []
    monkeypatch.setattr("jobsearch.notify.notifier", lambda t, x, **k: liste.append((t, x, k)) or ["macos"])
    return liste


def _poser(reg, emp, etat, controles=(), erreur=None):
    dossier._schema(reg)
    reg.cx.execute("INSERT OR REPLACE INTO dossiers (empreinte, etat, debut, fin, controles, erreur) "
                   "VALUES (?,?,?,?,?,?)", (emp, etat, "2026-11-02 09:00", "2026-11-02 09:04",
                                            json.dumps(list(controles)), erreur))
    reg.commit()


def test_pret_ouvre_le_dossier(reg, envois):
    emp = annonce(reg, entreprise="Nexthink", titre="Product Manager", statut="retenue")
    _poser(reg, emp, "pret", [{"cq": "CQ-01", "ok": True}])
    dossier.prevenir(reg, emp)
    (t, x, k), = envois
    assert t == "Dossier prêt" and x == "Nexthink · Product Manager"
    assert k["lien"] == "/dossier/%s" % emp and not k["urgent"]


def test_a_revoir_dit_quels_controles(reg, envois):
    emp = annonce(reg, entreprise="Nexthink", titre="Product Manager", statut="retenue")
    _poser(reg, emp, "a_revoir", [{"cq": "CQ-02 aucun tiret cadratin", "ok": False}, {"cq": "CQ-01", "ok": True}])
    dossier.prevenir(reg, emp)
    assert envois[0][0] == "Dossier à revoir avant envoi"
    assert "À revoir : CQ-02 aucun tiret cadratin" in envois[0][1] and "CQ-01" not in envois[0][1]


def test_echec_est_urgent_et_dit_pourquoi(reg, envois):
    emp = annonce(reg, entreprise="Nexthink", titre="Product Manager", statut="retenue")
    _poser(reg, emp, "echec", erreur="API Claude injoignable")
    dossier.prevenir(reg, emp, " · CV révisé")
    (t, x, k), = envois
    assert t == "Dossier en échec · CV révisé" and "API Claude injoignable" in x and k["urgent"]


def test_sans_dossier_rien_et_jamais_d_exception(reg, envois, monkeypatch):
    emp = annonce(reg, statut="retenue")
    assert dossier.prevenir(reg, emp) == [] and not envois
    _poser(reg, emp, "pret")
    monkeypatch.setattr("jobsearch.notify.notifier", lambda *a, **k: 1 / 0)
    assert dossier.prevenir(reg, emp) == []


def test_go_en_tache_previent_meme_en_echec(reg, envois, monkeypatch):
    emp = annonce(reg, statut="retenue")
    monkeypatch.setattr(dossier, "demarrer", lambda r, e: _poser(r, e, "en_cours"))
    monkeypatch.setattr(dossier, "Registre", lambda: reg)
    monkeypatch.setattr(reg, "fermer", lambda: None)

    def produire(r, e):
        _poser(r, e, "echec", erreur="quota dépassé")
        raise RuntimeError("quota dépassé")
    monkeypatch.setattr(dossier, "produire", produire)
    fils = []
    monkeypatch.setattr(dossier.threading, "Thread", lambda target, **k: fils.append(target) or type(
        "T", (), {"start": lambda self: None})())
    dossier.produire_en_tache(emp, reg)
    fils[0]()
    assert envois[0][0] == "Dossier en échec" and "quota dépassé" in envois[0][1]
