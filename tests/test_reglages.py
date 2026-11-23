"""Les pages de réglage portent partout le nom de leur ligne de la page Réglages."""

import re

from fastapi.testclient import TestClient


def test_meme_nom_dans_la_liste_le_titre_et_l_onglet(reg, monkeypatch):
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    web = TestClient(appmod.app)
    liste = web.get("/reglages").text
    assert len(appmod.PAGES_REGLAGES) == 7
    for r in appmod.PAGES_REGLAGES:
        assert 'href="%s"' % r["href"] in liste and r["nom"] in liste
        page = web.get(r["href"]).text
        assert re.search(r"<h1>(.*?)</h1>", page).group(1) == r["nom"], r["href"]
        assert re.search(r"<title>(.*?)</title>", page).group(1).endswith("%s · Réglages · Recherche d'emploi" % r["nom"]), r["href"]
        assert 'class="retour" href="/reglages"' in page
