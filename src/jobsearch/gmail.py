"""Le courrier dans Gmail, par le connecteur Gmail du compte Claude de l'utilisateur.

Même chemin qu'Indeed : une session `claude -p` sans tête dont les seuls outils
sont ceux de lecture du connecteur (search_threads, get_thread). Le modèle ne fait
qu'appeler les outils ; Python lit leurs réponses brutes dans le flux et les met
au format des messages d'Apple Mail, pour que `courrier.traiter` les classe de la
même façon. Rien n'est envoyé, marqué, déplacé ni effacé.
"""

import datetime as dt
import json
import re

from . import connecteurs

CONSIGNE_RECHERCHE = """Appelle l'outil search_threads une seule fois, avec exactement ces paramètres, sans autre outil, sans commentaire :
{params}
Quand l'appel est fait, réponds seulement « fini »."""

CONSIGNE_FILS = """Appelle l'outil get_thread une fois pour chacun de ces identifiants, avec messageFormat « PLAIN_TEXT », sans autre outil, sans commentaire :
{ids}
Quand tous les appels sont faits, réponds seulement « fini »."""


class GmailImpossible(Exception):
    pass


def requete(mots, depuis):
    """La recherche Gmail : boîte de réception, depuis la date, un des mots dans l'objet."""
    termes = ['"%s"' % m if " " in m else m for m in mots if m and '"' not in m]
    jour = dt.date.fromisoformat(depuis[:10]).strftime("%Y/%m/%d")
    return "in:inbox after:%s subject:(%s)" % (jour, " OR ".join(termes))


def _json(texte):
    try:
        return json.loads(texte or "")
    except ValueError:
        return None


def _champ(d, *noms):
    for n in noms:
        if isinstance(d, dict) and d.get(n) not in (None, ""):
            return d[n]
    return None


def fils(texte):
    """Réponse de search_threads → [(id du fil, [messages])]. Format tolérant : clés en
    camelCase ou snake_case, liste sous « threads » ou « items »."""
    d = _json(texte)
    if not isinstance(d, dict):
        return []
    out = []
    for t in d.get("threads") or d.get("items") or []:
        if isinstance(t, dict) and t.get("id"):
            out.append((t["id"], t.get("messages") or []))
    return out


def _date(v):
    if not v:
        return ""
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        return dt.datetime.fromtimestamp(int(v) / (1000 if int(v) > 10 ** 11 else 1)).strftime("%Y-%m-%d %H:%M:%S")
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", str(v))
    return "%s %s" % m.groups() if m else str(v)


def message(m, lien_fil=None, longueur=1500):
    """Un message Gmail → le format des messages d'Apple Mail que `courrier` sait classer."""
    corps = _champ(m, "plaintext_body", "plaintextBody", "body") or _champ(m, "snippet") or ""
    expediteur = _champ(m, "sender", "from") or ""
    if isinstance(expediteur, dict):
        expediteur = "%s <%s>" % (expediteur.get("name") or "", expediteur.get("email") or expediteur.get("address") or "")
    return {"subject": _champ(m, "subject") or "", "sender": str(expediteur).strip(),
            "received_date": _date(_champ(m, "date", "internalDate", "timestamp")),
            "content_preview": re.sub(r"\s+\n", "\n", str(corps))[:longueur],
            "internet_message_id": "gmail:%s" % (_champ(m, "id") or ""),
            "mail_link": _champ(m, "view_url", "viewUrl") or lien_fil or "", "account": "Gmail"}


def _session():
    from .moteur import executable
    from .moteur.session import SessionClaude
    from .config import DATA
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    return SessionClaude([executable(), "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                          "--verbose", "--no-session-persistence", "--tools", "", "--model", "haiku",
                          "--permission-mode", "dontAsk", "--allowedTools", ",".join(connecteurs.outils("gmail"))],
                         journal=DATA / "logs" / ("gmail-%s.jsonl" % dt.date.today().isoformat()),
                         erreur=GmailImpossible, etiquette="gmail")


def lire(mots, depuis, limite=40, ouvrir=None, delai_s=300, longueur=1500):
    """Messages de Gmail dont l'objet contient un des mots, reçus depuis `depuis`."""
    s = (ouvrir or _session)()
    try:
        params = json.dumps({"query": requete(mots, depuis), "pageSize": min(50, limite),
                             "view": "THREAD_VIEW_MINIMAL"}, ensure_ascii=False)
        rep = s.tour(CONSIGNE_RECHERCHE.format(params=params), delai_s)
        trouves = [x for r in rep if r["outil"].endswith("search_threads") and not r["erreur"] for x in fils(r["texte"])]
        if not trouves:
            if not any(r["outil"].endswith("search_threads") for r in rep):
                etat = next((v for k, v in (s.serveurs or {}).items() if "gmail" in (k or "").lower()), None)
                raise GmailImpossible("connecteur Gmail %s" % ("à réautoriser sur claude.ai" if etat == "needs-auth"
                                                               else "absent du compte Claude" if etat is None else etat))
            return []
        ids = [i for i, _ in trouves][:limite]
        complets = {}
        for i in range(0, len(ids), 8):
            for r in s.tour(CONSIGNE_FILS.format(ids="\n".join("- " + x for x in ids[i:i + 8])), delai_s):
                if r["outil"].endswith("get_thread") and not r["erreur"]:
                    d = _json(r["texte"]) or {}
                    complets[r["entree"].get("threadId")] = d
        out = []
        for ident, apercus in trouves[:limite]:
            fil = complets.get(ident) or {}
            msgs = fil.get("messages") or apercus
            lien = _champ(fil, "view_url", "viewUrl")
            out += [message(m, lien, longueur) for m in msgs if isinstance(m, dict)
                    and "SENT" not in (_champ(m, "label_ids", "labelIds") or [])]      # pas ses propres envois
        return out
    finally:
        s.fermer()
