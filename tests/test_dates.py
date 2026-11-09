import datetime as dt

import pytest

from conftest import annonce
from jobsearch.dates import delai_postulation, fin_publication
from jobsearch.ingest import ingerer
from jobsearch.sources.base import Annonce

REF = dt.date(2026, 10, 24)


@pytest.mark.parametrize("texte,attendu", [
    ("Délai de postulation : 27 septembre 2026. Entrée en fonction : 01/12/2026.", "2026-09-27"),
    ("- Délai de candidature : 23.09.2026", "2026-09-23"),
    ("Classe 18. Délai de postulation 24.10.2026, en ligne uniquement.", "2026-10-24"),
    ("Postuler jusqu'au 1er novembre", "2026-11-01"),
    ("Application deadline: November 15, 2026", "2026-11-15"),
    ("Please apply by 30 November 2026", "2026-11-30"),
    ("Bewerbungsfrist: 30.11.2026", "2026-11-30"),
    ("Merci d'envoyer votre dossier complet d'ici au 12 novembre 2026.", "2026-11-12"),
    ("Les candidatures sont à adresser au plus tard le 5.11.26", "2026-11-05"),
    ("Délai de postulation : 15 janvier", "2027-01-15"),             # sans année, écrit en octobre
    ("Entrée en fonction : 01/12/2026.", None),                        # une date, mais pas un délai
    ("Délai de réponse : 2 semaines.", None),
    ("", None),
])
def test_delai(texte, attendu):
    assert delai_postulation(texte, REF) == attendu


def test_fin_publication_plausible():
    assert fin_publication("2026-12-21", "2026-10-22") == "2026-12-21"
    assert fin_publication("2054-02-16", "2026-09-30") is None              # valeur bouche-trou de jobup
    assert fin_publication("", None) is None


def test_ingestion_pose_delai_et_fin(reg):
    texte = "We are hiring. Application deadline: 15 November 2026. " * 3
    ingerer(reg, [Annonce("job-room", "1", "Exemple SA", "AI Product Manager", texte=texte,
                          publiee_le="2026-10-22", fin_publication="2026-12-21", detail_charge=True)])
    f = reg.liste()[0]
    assert f["delai_postulation"] == "2026-11-15" and f["fin_publication"] == "2026-12-21"


def test_priorite_delai_proche_d_abord():
    from jobsearch.web.app import priorite
    auj = dt.date.today()
    d = lambda n: (auj + dt.timedelta(days=n)).isoformat()
    urgente = {"delai_postulation": d(3), "publiee_le": d(-20)}
    bien_notee = {"publiee_le": d(-1)}
    depassee = {"delai_postulation": d(-2), "publiee_le": d(-1)}
    rangees = sorted([depassee, bien_notee, urgente], key=lambda f: priorite(f, 90 if f is bien_notee else None))
    assert rangees == [urgente, bien_notee, depassee]
