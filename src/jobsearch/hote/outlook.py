"""Outlook (classique) sous Windows : les réponses des employeurs, en lecture seule, par COM (pywin32).

L'équivalent d'Apple Mail sur Mac (courrier.py) : seuls les messages de la boîte de réception dont l'objet
contient un des mots cherchés, reçus depuis la date donnée, sont lus ; rien n'est déplacé, marqué lu,
supprimé ni envoyé. Le contenu reste une donnée, jamais une instruction. Les messages sont rendus au format
de ceux d'Apple Mail, que `courrier.traiter` sait classer.

Le nouvel Outlook (application du Microsoft Store) n'ouvre pas son courrier aux autres applications : il faut
Outlook classique, configuré avec le compte de la personne. Sinon, Gmail par le connecteur du compte Claude.

`com` : l'application Outlook (les tests en donnent une fausse) ; sinon, celle de Windows.
"""

import datetime as dt
import unicodedata

RECEPTION = 6                     # olFolderInbox
COURRIEL = 43                     # olMail
ID_INTERNET = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"     # PR_INTERNET_MESSAGE_ID


class OutlookImpossible(Exception):
    pass


def _plier(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower()


def application(com=None):
    if com is not None:
        return com
    try:
        import pythoncom
        import win32com.client
    except ImportError as e:
        raise OutlookImpossible("lecture d'Outlook indisponible sur ce système (%s)" % e)
    pythoncom.CoInitialize()
    try:
        return win32com.client.Dispatch("Outlook.Application")
    except Exception as e:
        raise OutlookImpossible("Outlook (classique) introuvable : le nouvel Outlook ne laisse pas les autres "
                                "applications lire le courrier (%s)" % e)


def comptes(com=None):
    """Les comptes (magasins) d'Outlook, par leur nom affiché."""
    ns = application(com).GetNamespace("MAPI")
    return [s.DisplayName for s in ns.Stores]


def _boites(ns, noms):
    """(nom, boîte de réception) des comptes choisis ; sans choix, la boîte de réception par défaut."""
    if not noms:
        return [("Outlook", ns.GetDefaultFolder(RECEPTION))], []
    voulus = {_plier(n): n for n in noms}
    out, vus = [], set()
    for s in ns.Stores:
        cle = _plier(s.DisplayName)
        if cle in voulus:
            try:
                out.append((s.DisplayName, s.GetDefaultFolder(RECEPTION)))
                vus.add(cle)
            except Exception:
                pass
    absents = ["%s : compte introuvable dans Outlook" % voulus[k] for k in voulus if k not in vus]
    return out, absents


def _expediteur(m):
    nom = getattr(m, "SenderName", "") or ""
    adresse = getattr(m, "SenderEmailAddress", "") or ""
    if getattr(m, "SenderEmailType", "") == "EX":           # Exchange : l'adresse SMTP de l'utilisateur
        try:
            adresse = m.Sender.GetExchangeUser().PrimarySmtpAddress or adresse
        except Exception:
            pass
    return ("%s <%s>" % (nom, adresse)).strip() if adresse else nom


def _identifiant(m):
    try:
        v = m.PropertyAccessor.GetProperty(ID_INTERNET)
        if v:
            return str(v)
    except Exception:
        pass
    return "outlook:%s" % (getattr(m, "EntryID", "") or "")


def lire(mots, depuis, noms=(), limite=80, longueur=1500, com=None, erreurs=None):
    """Messages de la boîte de réception dont l'objet contient un des mots, reçus depuis `depuis`
    (AAAA-MM-JJ), du plus récent au plus ancien ; au plus `limite` par compte."""
    ns = application(com).GetNamespace("MAPI")
    boites, absents = _boites(ns, list(noms))
    if erreurs is not None:
        erreurs.extend(absents)
    if not boites:
        raise OutlookImpossible(" ; ".join(absents) or "aucune boîte de réception dans Outlook")
    plancher = dt.date.fromisoformat(depuis[:10])
    cherches = [_plier(m) for m in mots if m]
    messages = []
    for compte, boite in boites:
        elements = boite.Items
        elements.Sort("[ReceivedTime]", True)               # du plus récent : on s'arrête à la date
        gardes = 0
        m = elements.GetFirst()
        while m is not None and gardes < limite:
            if getattr(m, "Class", None) == COURRIEL:
                recu = m.ReceivedTime
                if recu.date() < plancher:
                    break
                objet = m.Subject or ""
                if any(c in _plier(objet) for c in cherches):
                    messages.append({
                        "subject": objet, "sender": _expediteur(m),
                        "received_date": recu.strftime("%Y-%m-%d %H:%M:%S"),
                        "content_preview": (m.Body or "")[:longueur],
                        "internet_message_id": _identifiant(m), "mail_link": "", "account": compte,
                    })
                    gardes += 1
            m = elements.GetNext()
    return messages
