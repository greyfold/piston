"""Connecteurs MCP : catalogue, activation, Gmail par le compte Claude, courrier réuni."""

import json

import pytest

from jobsearch import connecteurs, courrier, gmail


class FausseSession:
    """Une session Claude dont chaque tour rend des appels d'outils préparés."""

    def __init__(self, tours, serveurs=None):
        self.tours, self.messages = list(tours), []
        self.serveurs = serveurs if serveurs is not None else {"claude.ai Gmail": "connected"}
        self.fermee = False

    def tour(self, message, delai):
        self.messages.append(message)
        return self.tours.pop(0) if self.tours else []

    def fermer(self):
        self.fermee = True


def appel(outil, texte, **entree):
    return {"outil": "mcp__claude_ai_Gmail__" + outil, "entree": entree, "texte": json.dumps(texte), "erreur": False}


RECHERCHE = {"threads": [{"id": "t1", "messages": [{"id": "m1", "subject": "Votre candidature", "snippet": "Merci"}]}]}
FIL = {"id": "t1", "viewUrl": "https://mail.google.com/fil/t1", "messages": [
    {"id": "m1", "subject": "Votre candidature – Product Manager", "sender": "rh@exemple.ch",
     "date": "2026-11-05T09:12:00Z", "plaintextBody": "Nous avons le regret de vous informer…", "labelIds": ["INBOX"]},
    {"id": "m2", "subject": "Re: Votre candidature", "sender": "moi@exemple.ch", "date": "2026-11-05T10:00:00Z",
     "plaintextBody": "Merci pour votre retour.", "labelIds": ["SENT"]}]}


def test_activation_par_defaut_puis_dans_le_profil():
    assert {"apple-mail", "chrome"} <= connecteurs.actifs() and "gmail" not in connecteurs.actifs()
    connecteurs.activer("gmail")
    connecteurs.activer("apple-mail", False)
    assert connecteurs.actif("gmail") and not connecteurs.actif("apple-mail")
    with pytest.raises(KeyError):
        connecteurs.activer("outlook")


def test_seuls_les_outils_de_lecture():
    assert connecteurs.outils("gmail") == ["mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread"]
    assert connecteurs.outils("indeed") == ["mcp__claude_ai_Indeed__search_jobs", "mcp__claude_ai_Indeed__get_job_details"]
    assert connecteurs.outils("apple-mail") == []


def test_requete_gmail():
    q = gmail.requete(["candidature", "votre dossier", "Nexthink"], "2026-11-01")
    assert q == 'in:inbox after:2026/11/01 subject:(candidature OR "votre dossier" OR Nexthink)'


def test_gmail_lu_au_format_d_apple_mail():
    s = FausseSession([[appel("search_threads", RECHERCHE, query="q")], [appel("get_thread", FIL, threadId="t1")]])
    msgs = gmail.lire(["candidature"], "2026-11-01", ouvrir=lambda: s)
    assert len(msgs) == 1                                       # son propre envoi (SENT) n'est pas lu
    m = msgs[0]
    assert m["subject"].startswith("Votre candidature") and m["sender"] == "rh@exemple.ch"
    assert m["received_date"] == "2026-11-05 09:12:00" and "regret" in m["content_preview"]
    assert m["internet_message_id"] == "gmail:m1" and m["account"] == "Gmail"
    assert "search_threads" in s.messages[0] and "PLAIN_TEXT" in s.messages[1] and "- t1" in s.messages[1]
    assert s.fermee
    assert courrier._date(m) == "2026-11-05"


def test_gmail_absent_ou_a_reautoriser():
    with pytest.raises(gmail.GmailImpossible, match="absent du compte"):
        gmail.lire(["x"], "2026-11-01", ouvrir=lambda: FausseSession([[]], serveurs={}))
    with pytest.raises(gmail.GmailImpossible, match="réautoriser"):
        gmail.lire(["x"], "2026-11-01", ouvrir=lambda: FausseSession([[]], serveurs={"claude.ai Gmail": "needs-auth"}))
    assert gmail.lire(["x"], "2026-11-01", ouvrir=lambda: FausseSession([[appel("search_threads", {})]])) == []


def test_courrier_reunit_les_boites_et_survit_a_une_panne(reg):
    connecteurs.activer("gmail")
    apple = [{"subject": "Votre candidature", "sender": "a@b.ch", "received_date": "2026-11-04 08:00:00",
              "content_preview": "Accusé de réception", "internet_message_id": "<1@b>"}]
    vus = []
    b = courrier.relever(reg, lire=lambda mots, debut, comptes: vus.append("apple") or apple,
                         lire_gmail_=lambda mots, debut: vus.append("gmail") or [])
    assert vus == ["apple", "gmail"] and b["boites"] == ["Apple Mail", "Gmail"] and b["lus"] == 1

    def panne(mots, debut):
        raise gmail.GmailImpossible("connecteur Gmail à réautoriser")
    b = courrier.relever(reg, lire=lambda *a: [], lire_gmail_=panne)
    assert b["boites"] == ["Apple Mail"] and "réautoriser" in b["pannes"][0]
    connecteurs.activer("apple-mail", False)
    with pytest.raises(courrier.CourrierImpossible, match="réautoriser"):
        courrier.relever(reg, lire_gmail_=panne)
    connecteurs.activer("gmail", False)
    assert "aucun connecteur" in courrier.relever(reg)["ignore"]


def test_etat_d_un_connecteur_du_compte():
    c = connecteurs.CATALOGUE["indeed"]
    assert connecteurs.etat_connecteur(c, lambda c: FausseSession([[]], {"claude.ai Indeed": "connected"}))[0]
    ok, msg = connecteurs.etat_connecteur(c, lambda c: FausseSession([[]], {"claude.ai Indeed": "needs-auth"}))
    assert not ok and "réautoriser" in msg
    ok, msg = connecteurs.etat_connecteur(c, lambda c: FausseSession([[]], {}))
    assert not ok and "absent" in msg and "claude.ai" in msg


def test_page_connecteurs(monkeypatch, reg):
    from fastapi.testclient import TestClient
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    web = TestClient(appmod.app)
    page = web.get("/connecteurs").text
    assert "Gmail" in page and "Apple Mail" in page and "Claude in Chrome" in page and "c'est le cas" in page
    assert 'href="/ia"' in page and "Connecté au compte Claude" not in page     # l'IA a sa page
    r = web.post("/connecteurs/gmail/activer", follow_redirects=False)
    assert r.status_code == 303 and connecteurs.actif("gmail")
    assert web.post("/connecteurs/inconnu/activer").status_code == 404


def test_etat_pour_l_application_mac(monkeypatch, reg):
    from fastapi.testclient import TestClient
    from conftest import annonce
    from jobsearch.web import app as appmod
    monkeypatch.setattr(appmod, "_reg", lambda: type(reg)(reg.chemin))
    annonce(reg, statut="A")
    reg.commit()
    e = TestClient(appmod.app).get("/api/etat").json()
    assert e["a_trancher"] == 1 and e["mode"] == "local" and e["vm"] is None
    assert e["claude"] == {"connecte": True, "message": "Connecté au compte Claude (abonnement pro)"}
    assert set(e["passage"]) == {"niveau", "texte"} and e["donnees"]
