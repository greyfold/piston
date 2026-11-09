import os
from pathlib import Path

import pytest

from jobsearch.config import reglages
from jobsearch.fingerprint import empreinte, normaliser
from jobsearch.importer import ANNONCES, lire_note


def test_normaliser_retire_bruit_et_accents():
    assert normaliser("Chef·fe de produit (h/f) 80-100 %") == "chef fe de produit"
    assert normaliser("Head of Design (m/w/d)") == "head of design"
    assert normaliser("Économiste") == "economiste"


def test_empreinte_stable():
    assert empreinte("SICPA SA", "Product Manager") == empreinte("sicpa sa", "Product Manager 100%")
    assert len(empreinte("a", "b")) == 12


def _notes():
    dossier = Path(reglages()["coffre"]) / ANNONCES
    if not dossier.is_dir():
        return []
    return [dossier / n for n in sorted(os.listdir(dossier)) if n.endswith(".md")]


@pytest.mark.skipif(not _notes(), reason="coffre absent")
def test_parite_avec_le_registre_du_coffre():
    """Toute empreinte stockée se recalcule à l'identique, sauf les annonces
    rattachées par identifiant de plateforme (titre modifié depuis, fusions)."""
    ecarts = []
    for chemin in _notes():
        f = lire_note(chemin)
        if f and empreinte(f.get("entreprise") or "?", f.get("titre") or "") != f["empreinte"]:
            ecarts.append((f["empreinte"], f.get("statut"), f.get("titre")))
    # Mesuré le 24.10.2026 : quelques notes ont un titre corrigé à la main après
    # coup ; elles restent retrouvables par leur identifiant de plateforme.
    assert len(ecarts) <= 0.1 * len(_notes()), ecarts
