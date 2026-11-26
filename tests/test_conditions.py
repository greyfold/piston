"""Les conditions d'utilisation : acceptées une fois par version, avant Aujourd'hui."""
from fastapi.testclient import TestClient


def test_aujourdhui_attend_les_conditions_puis_les_retient(reg, monkeypatch):
    from jobsearch import profil
    from jobsearch.web import app as appmod
    # conftest les tient pour acceptées ; ici, la vraie règle (celle de profil.conditions_a_accepter).
    vraie = lambda p=None: ((p or profil.charger()).get(profil.CONDITIONS) or {}).get("version") != profil.VERSION_CONDITIONS
    monkeypatch.setattr(profil, "conditions_a_accepter", vraie)
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    web = TestClient(appmod.app)
    r = web.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/conditions"
    page = web.get("/conditions").text
    assert "Limitation de responsabilité" in page and "art. 100 du Code des obligations" in page
    assert 'action="/conditions/accepter"' in page and 'class="menu-lateral"' not in page   # sans menu
    en, de = web.get("/conditions?langue=en").text, web.get("/conditions?langue=de").text
    assert "Limitation of liability" in en and 'lang="en"' in en and "the French text prevails" in en
    assert "Haftungsbeschränkung" in de and "Art. 100 OR" in de and 'action="/conditions/accepter"' in de
    assert "Limitation de responsabilité" in web.get("/conditions?langue=xx").text       # inconnue : le français
    assert web.post("/conditions/accepter", follow_redirects=False).headers["location"] == "/"
    assert not vraie()
    assert web.get("/", follow_redirects=False).headers.get("location") != "/conditions"
    page = web.get("/conditions").text                    # acceptées : une page de réglage, sans le bouton
    assert 'action="/conditions/accepter"' not in page and 'class="menu-lateral"' in page
