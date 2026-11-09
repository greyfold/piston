"""Le registre : une base SQLite, unique source de vérité du pipeline.

Une ligne par annonce (mêmes champs que les notes de l'ancien registre, plus
ceux décidés le 24.10), un journal en ajout seul, les clics de la personne, et
l'historique des passages qui sert de battement de cœur.
"""

import datetime as dt
import json
import sqlite3
from pathlib import Path

from .config import chemin_db

STATUTS = ["nouvelle", "A", "B", "C", "retenue", "candidature",
           "entretien", "offre", "refus", "expiree", "doublon"]
PIPELINE = ["retenue", "candidature", "entretien", "offre"]
ENVOYEES = ["candidature", "entretien", "offre", "refus"]

# Colonnes texte ou nombre ; LISTES sont stockées en JSON.
COLONNES = [
    "empreinte", "entreprise", "titre", "lieu", "canton", "statut", "passe_ecart",
    "motif", "limite", "angle", "exigences", "sources", "ids_source", "requetes",
    "url", "url_postuler", "publiee_le", "vue_le", "revue_le", "occurrences",
    "texte", "texte_origine", "delai_postulation", "fin_publication", "langue", "taux_min", "taux_max", "permanent",
    "cv_genere", "dossier", "postulee_le", "canal_envoi", "relance_le",
    "prochaine_action", "echeance", "accuse_le", "reponse_le", "flag_rh",
    "motif_refus", "jobroom_saisie_le", "jobroom_maj",
    "raison_decision", "raison_note", "decision_le",
    "extra", "cree_le", "maj_le",
]
LISTES = {"exigences", "sources", "ids_source", "requetes", "raison_decision"}
BOOLEENS = {"limite", "permanent", "cv_genere"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS annonces (
  empreinte TEXT PRIMARY KEY,
  entreprise TEXT, titre TEXT, lieu TEXT, canton TEXT,
  statut TEXT NOT NULL DEFAULT 'nouvelle',
  passe_ecart INTEGER, motif TEXT, limite INTEGER DEFAULT 0,
  angle TEXT, exigences TEXT DEFAULT '[]',
  sources TEXT DEFAULT '[]', ids_source TEXT DEFAULT '[]', requetes TEXT DEFAULT '[]',
  url TEXT, url_postuler TEXT, publiee_le TEXT, vue_le TEXT, revue_le TEXT,
  occurrences INTEGER DEFAULT 0,
  texte TEXT, texte_origine TEXT, delai_postulation TEXT, fin_publication TEXT, langue TEXT, taux_min INTEGER, taux_max INTEGER, permanent INTEGER,
  cv_genere INTEGER DEFAULT 0, dossier TEXT,
  postulee_le TEXT, canal_envoi TEXT, relance_le TEXT,
  prochaine_action TEXT, echeance TEXT, accuse_le TEXT, reponse_le TEXT, flag_rh TEXT,
  motif_refus TEXT, jobroom_saisie_le TEXT, jobroom_maj TEXT,
  raison_decision TEXT DEFAULT '[]', raison_note TEXT, decision_le TEXT,
  extra TEXT DEFAULT '{}', cree_le TEXT, maj_le TEXT
);
CREATE INDEX IF NOT EXISTS annonces_statut ON annonces(statut);
CREATE TABLE IF NOT EXISTS ids_source (
  cle TEXT PRIMARY KEY, empreinte TEXT NOT NULL REFERENCES annonces(empreinte)
);
CREATE TABLE IF NOT EXISTS journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  empreinte TEXT NOT NULL, quand TEXT NOT NULL, texte TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS journal_emp ON journal(empreinte);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  empreinte TEXT, type TEXT NOT NULL, donnees TEXT NOT NULL,
  cree_le TEXT NOT NULL, resultat TEXT NOT NULL, message TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  debut TEXT NOT NULL, fin TEXT, statut TEXT NOT NULL, declencheur TEXT, resume TEXT
);
CREATE TABLE IF NOT EXISTS run_etapes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id), etape TEXT NOT NULL,
  debut TEXT NOT NULL, fin TEXT, statut TEXT NOT NULL, compteurs TEXT, erreur TEXT
);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, nom TEXT, nature TEXT, acces TEXT, etat TEXT,
  dernier_test_le TEXT, dernier_test_ok INTEGER, derniere_reussite_le TEXT, remarques TEXT
);
CREATE TABLE IF NOT EXISTS veilles (date TEXT PRIMARY KEY, origine TEXT);
CREATE TABLE IF NOT EXISTS meta (cle TEXT PRIMARY KEY, valeur TEXT);
"""


def aujourdhui():
    return dt.date.today().isoformat()


def maintenant():
    return dt.datetime.now().isoformat(sep=" ", timespec="minutes")


def plus_jours(date_iso, n):
    return (dt.date.fromisoformat(str(date_iso)[:10]) + dt.timedelta(days=n)).isoformat()


def _encode(k, v):
    if k in LISTES:
        return json.dumps(v or [], ensure_ascii=False)
    if k == "extra":
        return json.dumps(v or {}, ensure_ascii=False)
    if k in BOOLEENS:
        return None if v is None else int(bool(v))
    return v


def _decode(row):
    d = dict(row)
    for k in LISTES:
        if k in d:
            d[k] = json.loads(d[k] or "[]")
    if "extra" in d:
        d["extra"] = json.loads(d["extra"] or "{}")
    for k in BOOLEENS:
        if k in d and d[k] is not None:
            d[k] = bool(d[k])
    return d


class Registre:
    def __init__(self, chemin=None):
        self.chemin = Path(chemin or chemin_db())
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self.cx = sqlite3.connect(self.chemin, timeout=30)
        self.cx.row_factory = sqlite3.Row
        self.cx.execute("PRAGMA journal_mode=WAL")
        self.cx.execute("PRAGMA foreign_keys=ON")
        self.cx.executescript(SCHEMA)
        self._migrer()

    def _migrer(self):
        """Colonnes ajoutées après la première version de la base."""
        existantes = {r[1] for r in self.cx.execute("PRAGMA table_info(annonces)")}
        if "texte_origine" not in existantes:
            # Base d'avant le 24.10 au soir : texte lu sur la plateforme (complet si assez
            # long, sinon extrait).
            self.cx.execute("ALTER TABLE annonces ADD COLUMN texte_origine TEXT")
            self.cx.execute("UPDATE annonces SET texte_origine = CASE "
                            "WHEN COALESCE(texte,'') = '' THEN NULL "
                            "WHEN LENGTH(texte) >= 600 THEN 'plateforme' ELSE 'extrait' END")
            from .prefilter import langue
            for emp, texte in self.cx.execute(
                    "SELECT empreinte, texte FROM annonces WHERE langue IS NULL AND texte != ''").fetchall():
                self.cx.execute("UPDATE annonces SET langue=? WHERE empreinte=?", (langue(texte), emp))
        if "delai_postulation" not in existantes:
            # 25.10 : délai de postulation lu dans le texte, fin de publication de la plateforme.
            self.cx.execute("ALTER TABLE annonces ADD COLUMN delai_postulation TEXT")
            self.cx.execute("ALTER TABLE annonces ADD COLUMN fin_publication TEXT")
            from .dates import delai_postulation
            for emp, texte, pub in self.cx.execute(
                    "SELECT empreinte, texte, publiee_le FROM annonces WHERE texte != ''").fetchall():
                ref = dt.date.fromisoformat(pub[:10]) if pub and len(pub) >= 10 else None
                self.cx.execute("UPDATE annonces SET delai_postulation=? WHERE empreinte=?",
                                (delai_postulation(texte, ref), emp))
        self.cx.commit()

    def fermer(self):
        self.cx.close()

    # --- Annonces -----------------------------------------------------------

    def get(self, emp):
        r = self.cx.execute("SELECT * FROM annonces WHERE empreinte=?", (emp,)).fetchone()
        return _decode(r) if r else None

    def resoudre(self, prefixe):
        """Empreinte complète depuis un préfixe unique, sinon None."""
        rows = self.cx.execute("SELECT empreinte FROM annonces WHERE empreinte LIKE ?",
                               (prefixe + "%",)).fetchall()
        return rows[0][0] if len(rows) == 1 else None

    def par_id_source(self, cle):
        r = self.cx.execute("SELECT empreinte FROM ids_source WHERE cle=?", (cle,)).fetchone()
        return r[0] if r else None

    def enregistrer(self, f):
        """Insère ou remplace une annonce complète, et son index d'identifiants."""
        f = dict(f)
        f.setdefault("cree_le", maintenant())
        f["maj_le"] = maintenant()
        cols = [c for c in COLONNES if c in f]
        self.cx.execute(
            "INSERT INTO annonces (%s) VALUES (%s) ON CONFLICT(empreinte) DO UPDATE SET %s" % (
                ",".join(cols), ",".join("?" * len(cols)),
                ",".join("%s=excluded.%s" % (c, c) for c in cols if c != "empreinte")),
            [_encode(c, f[c]) for c in cols])
        for cle in f.get("ids_source") or []:
            self.cx.execute("INSERT OR IGNORE INTO ids_source (cle, empreinte) VALUES (?,?)",
                            (cle, f["empreinte"]))

    def maj(self, emp, **champs):
        champs["maj_le"] = maintenant()
        self.cx.execute("UPDATE annonces SET %s WHERE empreinte=?" % ",".join(
            "%s=?" % k for k in champs), [_encode(k, v) for k, v in champs.items()] + [emp])

    def liste(self, statuts=None, ordre="vue_le DESC, entreprise"):
        if statuts:
            q = "SELECT * FROM annonces WHERE statut IN (%s) ORDER BY %s" % (
                ",".join("?" * len(statuts)), ordre)
            rows = self.cx.execute(q, list(statuts))
        else:
            rows = self.cx.execute("SELECT * FROM annonces ORDER BY %s" % ordre)
        return [_decode(r) for r in rows]

    def toutes(self):
        return self.liste()

    # --- Journal ------------------------------------------------------------

    def noter(self, emp, texte, quand=None):
        self.cx.execute("INSERT INTO journal (empreinte, quand, texte) VALUES (?,?,?)",
                        (emp, quand or maintenant(), texte))

    def journal(self, emp):
        return [dict(r) for r in self.cx.execute(
            "SELECT quand, texte FROM journal WHERE empreinte=? ORDER BY quand, id", (emp,))]

    # --- Stats --------------------------------------------------------------

    def stats(self):
        par_statut = {r[0]: r[1] for r in self.cx.execute(
            "SELECT statut, COUNT(*) FROM annonces GROUP BY statut")}
        par_source = {}
        for (srcs,) in self.cx.execute("SELECT sources FROM annonces"):
            for s in json.loads(srcs or "[]"):
                par_source[s] = par_source.get(s, 0) + 1
        total = sum(par_statut.values())
        triees = total - par_statut.get("nouvelle", 0)
        ratio = (100.0 * (par_statut.get("A", 0) + par_statut.get("retenue", 0)) / triees
                 if triees else None)
        return {"total": total, "par_statut": par_statut,
                "par_source": dict(sorted(par_source.items(), key=lambda x: -x[1])),
                "ratio_a": ratio}

    # --- Méta ---------------------------------------------------------------

    def meta(self, cle, defaut=None):
        r = self.cx.execute("SELECT valeur FROM meta WHERE cle=?", (cle,)).fetchone()
        return r[0] if r else defaut

    def poser_meta(self, cle, valeur):
        self.cx.execute("INSERT INTO meta (cle, valeur) VALUES (?,?) "
                        "ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur", (cle, valeur))

    def commit(self):
        self.cx.commit()
