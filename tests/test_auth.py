"""Accès au tableau de bord (web/auth.py) : le jeton est exigé de toute requête, même locale."""
from fastapi.testclient import TestClient

from jobsearch.web import auth


def _client():
    from jobsearch.web import app as appmod
    return TestClient(appmod.app)


def test_sans_jeton_meme_en_local_redirige_vers_la_connexion():
    c = _client()
    c.cookies.clear()
    for hote in ("127.0.0.1:8765", "localhost", "host.lima.internal", "rebond.example"):    # Host forgé : aucune dispense
        r = c.get("/", headers={"host": hote}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/connexion"


def test_api_et_en_tete_de_jeton_refuses_en_401():
    c = _client()
    c.cookies.clear()
    for hote in ("127.0.0.1:8765", "rebond.example"):
        r = c.get("/api/etat", headers={"host": hote}, follow_redirects=False)
        assert r.status_code == 401 and "location" not in r.headers
    r = c.get("/", headers={"X-Jobsearch-Jeton": "faux"}, follow_redirects=False)
    assert r.status_code == 401


def test_jeton_par_cookie_ou_par_en_tete():
    c = _client()
    c.cookies.clear()
    assert c.get("/api/etat", headers={"X-Jobsearch-Jeton": auth.jeton()}).status_code == 200
    assert c.get("/api/etat", headers={"X-Jobsearch-Jeton": "faux"}, follow_redirects=False).status_code == 401
    c.cookies.set(auth.COOKIE, auth.jeton())
    assert c.get("/api/etat").status_code == 200
    assert not auth.jeton_valide("é" * 10) and not auth.jeton_valide("") and not auth.jeton_valide(None)


def test_le_jeton_existe_des_le_demarrage():
    auth.chemin_jeton().unlink(missing_ok=True)
    c = _client()
    auth.chemin_jeton().unlink()                               # (le client de test l'a lu pour son cookie)
    with c:                                            # lifespan : le démarrage du serveur
        assert auth.chemin_jeton().exists()
        from conftest import POSIX
        assert not POSIX or oct(auth.chemin_jeton().stat().st_mode & 0o777) == "0o600"


def test_post_sans_origine_accepte_avec_jeton_refuse_sans():
    c = _client()
    r = c.post("/premiers-pas/masquer", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    c.cookies.clear()
    r = c.post("/premiers-pas/masquer", follow_redirects=False)
    assert r.headers["location"] == "/connexion"
    c.cookies.set(auth.COOKIE, auth.jeton())
    r = c.post("/premiers-pas/masquer", headers={"origin": "https://malveillant.example"}, follow_redirects=False)
    assert r.status_code == 403


def test_connexion_par_jeton_colle():
    c = _client()
    c.cookies.clear()
    assert "Jeton incorrect" in c.post("/connexion", data={"jeton_saisi": "faux"}).text
    r = c.post("/connexion", data={"jeton_saisi": " %s " % auth.jeton()}, follow_redirects=False)
    assert r.status_code == 303 and auth.COOKIE in r.headers["set-cookie"]


def test_page_de_connexion_ne_montre_aucune_donnee(reg, monkeypatch):
    from conftest import annonce
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    annonce(reg, entreprise="Employeur Secret SA", statut="A")
    reg.cx.execute("INSERT INTO runs (debut, fin, statut, resume) VALUES ('2026-11-01T06:00', '2026-11-01T06:10',"
                   " 'complet', 'Employeur Secret SA : une annonce')")
    reg.commit()
    c = _client()
    assert "Employeur Secret SA" in c.get("/").text            # avec le jeton : la notification est là
    c.cookies.clear()
    for r in (c.get("/connexion"), c.post("/connexion", data={"jeton_saisi": "faux"})):
        assert r.status_code == 200
        assert "Employeur Secret SA" not in r.text
        assert 'id="notifications"' not in r.text and "menu-lateral" not in r.text
        assert "Profil incomplet" not in r.text


def test_le_code_ne_connait_pas_le_client_de_test():
    texte = open(auth.__file__, encoding="utf-8").read()       # la fixture de conftest l'ajoute, pas le code
    assert "testclient" not in texte and "testserver" not in texte
