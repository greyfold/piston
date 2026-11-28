"""Outlook (classique) sous Windows, en lecture seule : sans Outlook, par de faux objets COM."""

import datetime as dt

import pytest

from jobsearch import connecteurs, courrier, hote
from jobsearch.hote import outlook


class Message:
    Class = outlook.COURRIEL

    def __init__(self, objet, recu, expediteur="RH <rh@exemple.ch>", corps="Merci pour ta candidature.", ident="<1@x>"):
        self.Subject, self.ReceivedTime, self.Body = objet, recu, corps
        self.SenderName, _, adr = expediteur.partition(" <")
        self.SenderEmailAddress, self.SenderEmailType = adr.rstrip(">"), "SMTP"
        self.EntryID = "E" + objet[:3]
        self._ident = ident
        self.lu = False

    class _Proprietes:
        def __init__(self, v):
            self.v = v

        def GetProperty(self, nom):
            assert nom == outlook.ID_INTERNET
            return self.v

    @property
    def PropertyAccessor(self):
        return self._Proprietes(self._ident)


class Elements:
    def __init__(self, messages):
        self.messages, self.i = list(messages), 0
        self.tri = None

    def Sort(self, champ, descendant):
        self.tri = (champ, descendant)
        self.messages.sort(key=lambda m: m.ReceivedTime, reverse=descendant)

    def GetFirst(self):
        self.i = 0
        return self.GetNext()

    def GetNext(self):
        if self.i >= len(self.messages):
            return None
        self.i += 1
        return self.messages[self.i - 1]


class Boite:
    def __init__(self, messages):
        self.Items = Elements(messages)


class Magasin:
    def __init__(self, nom, messages):
        self.DisplayName, self.boite = nom, Boite(messages)

    def GetDefaultFolder(self, n):
        assert n == outlook.RECEPTION
        return self.boite


class Outlook:
    def __init__(self, magasins):
        self.Stores = magasins

    def GetNamespace(self, nom):
        assert nom == "MAPI"
        return self

    def GetDefaultFolder(self, n):
        return self.Stores[0].GetDefaultFolder(n)


J = lambda j: dt.datetime(2026, 11, j, 9, 30)


def test_lit_les_objets_cherches_depuis_la_date():
    a = Message("Votre candidature chez Exemple SA", J(20))
    b = Message("Newsletter de la semaine", J(21))
    c = Message("Invitation à un entretien", J(10))            # avant la date : la lecture s'arrête
    com = Outlook([Magasin("camille@exemple.ch", [a, b, c])])
    m = outlook.lire(["candidature", "entretien"], "2026-11-15", com=com)
    assert [x["subject"] for x in m] == ["Votre candidature chez Exemple SA"]
    assert m[0] == {"subject": "Votre candidature chez Exemple SA", "sender": "RH <rh@exemple.ch>",
                    "received_date": "2026-11-20 09:30:00", "content_preview": "Merci pour ta candidature.",
                    "internet_message_id": "<1@x>", "mail_link": "", "account": "Outlook"}
    assert com.Stores[0].boite.Items.tri == ("[ReceivedTime]", True)
    assert not a.lu                                              # rien n'est marqué lu


def test_comptes_choisis_et_compte_absent():
    com = Outlook([Magasin("Pro", [Message("Ta candidature", J(20))]), Magasin("Perso", [Message("Candidature", J(21))])])
    erreurs = []
    m = outlook.lire(["candidature"], "2026-11-01", ["perso", "Ancien"], com=com, erreurs=erreurs)
    assert [x["account"] for x in m] == ["Perso"] and erreurs == ["Ancien : compte introuvable dans Outlook"]
    with pytest.raises(outlook.OutlookImpossible, match="Ancien"):
        outlook.lire(["candidature"], "2026-11-01", ["Ancien"], com=com)


def test_sans_pywin32_un_message_clair(monkeypatch):
    import builtins
    vrai = builtins.__import__

    def sans(nom, *a, **k):
        if nom in ("pythoncom", "win32com.client"):
            raise ImportError("No module named %r" % nom)
        return vrai(nom, *a, **k)
    monkeypatch.setattr(builtins, "__import__", sans)
    with pytest.raises(outlook.OutlookImpossible, match="indisponible"):
        outlook.application()


def test_releve_par_outlook(reg, monkeypatch):
    monkeypatch.setattr(connecteurs, "actifs", lambda p=None: {"outlook"})
    vus = []
    bilan = courrier.relever(reg, lire=lambda mots, debut, comptes: vus.append(comptes) or [])
    assert bilan["boites"] == ["Outlook"] and len(vus) == 1          # les comptes choisis dans le profil


def test_catalogue_selon_le_systeme(monkeypatch):
    monkeypatch.setattr(hote, "SYSTEME", "windows")
    ids = [c["id"] for c in connecteurs.liste()]
    assert "outlook" in ids and "apple-mail" not in ids and connecteurs.courrier_local() == "outlook"
    monkeypatch.setattr(hote, "SYSTEME", "macos")
    ids = [c["id"] for c in connecteurs.liste()]
    assert "apple-mail" in ids and "outlook" not in ids
    monkeypatch.setattr(hote, "SYSTEME", "linux")
    assert connecteurs.courrier_local() is None
