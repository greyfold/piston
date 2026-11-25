"""La copie en mémoire de l'état de la VM : une page ne l'attend jamais, sauf la première fois."""

import threading
import time

import pytest

from jobsearch.web import etat_distant


@pytest.fixture
def copie(monkeypatch):
    lectures = []
    feu = threading.Event()
    feu.set()

    def mesurer():
        feu.wait(5)
        lectures.append(time.monotonic())
        return {"vm": {"existe": True, "statut": "Running"}, "travailleur": {"moteurs": {"n": len(lectures)}}}
    monkeypatch.setattr(etat_distant, "mesurer", mesurer)
    monkeypatch.setattr(etat_distant, "_copie", {"t": None, "vm": None, "travailleur": None})
    monkeypatch.setattr(etat_distant, "_oubliee", True)
    return lectures, feu


def _attendre(cond):
    fin = time.monotonic() + 5
    while not cond() and time.monotonic() < fin:
        time.sleep(0.01)
    assert cond()


def test_premiere_lecture_puis_copie(copie):
    lectures, _ = copie
    assert etat_distant.lire()["travailleur"]["moteurs"]["n"] == 1
    assert etat_distant.lire()["travailleur"]["moteurs"]["n"] == 1 and len(lectures) == 1


def test_copie_vieille_relue_en_fond_sans_attendre(copie, monkeypatch):
    lectures, feu = copie
    etat_distant.lire()
    monkeypatch.setattr(etat_distant, "FRAIS_S", 0)
    feu.clear()                                          # la relecture traîne (VM lente)
    debut = time.monotonic()
    assert etat_distant.lire()["travailleur"]["moteurs"]["n"] == 1   # l'ancienne copie, tout de suite
    etat_distant.lire()                                  # une seule relecture à la fois
    assert time.monotonic() - debut < 1
    feu.set()
    _attendre(lambda: etat_distant._copie["travailleur"]["moteurs"]["n"] == 2)
    time.sleep(0.05)
    assert len(lectures) == 2


def test_oublier_relit_pendant_la_requete(copie):
    lectures, _ = copie
    etat_distant.lire()
    etat_distant.oublier()
    assert etat_distant.lire()["travailleur"]["moteurs"]["n"] == 2


def test_copie_en_erreur_relue_plus_vite(copie, monkeypatch):
    monkeypatch.setattr(etat_distant, "mesurer", lambda: {"vm": {"existe": True, "statut": "Running"},
                                                          "travailleur": {"erreur": "démarre encore"}})
    etat_distant.lire()
    relances = []
    monkeypatch.setattr(etat_distant, "_relire_en_fond", lambda: relances.append(1))
    etat_distant._copie["t"] -= etat_distant.FRAIS_ERREUR_S + 1
    etat_distant.lire()
    assert relances == [1]
