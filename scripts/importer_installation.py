#!/usr/bin/env python3
"""Reprend dans l'application les données d'une installation de développement (dossier `data/`
d'un projet « Job Search »), sans rien perdre de ce que l'application contient déjà.

    uv run python scripts/importer_installation.py --source "/chemin/Job Search/data"
    uv run python scripts/importer_installation.py --source "/chemin/Job Search/data" --appliquer

Sans `--appliquer`, tout est simulé : le rapport donne les effectifs exacts, rien n'est écrit.
Avec `--appliquer`, l'application doit être quittée ; sa base est d'abord copiée dans
`sauvegardes/jobsearch-avant-import-<date>.db`.

Ce qui est repris : annonces, identifiants de source, journal, actions, évaluations, courriers,
dossiers (table et fichiers), verdicts de doublons, répertoire des sources, passages et leurs
étapes, veilles, fiches d'entretien, sorties brutes des passages ; dans le profil, les
certifications et les entreprises visées qui manquent.

Ce qui reste à l'application : son parcours, « Ce qui compte », ses postes proposés, son profil
(format structuré), l'état de ses sources, sa méta, ses tâches Claude, son jeton, sa connexion
à Claude et sa machine virtuelle.

Une annonce présente des deux côtés : un statut décidé par la personne (candidature, refus,
entretien, retenue…) l'emporte sur un statut mécanique (nouvelle, C, doublon) ; deux décisions,
la plus récente gagne ; deux statuts mécaniques, la source gagne.
"""
import argparse
import datetime as dt
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

CIBLE_DEFAUT = Path.home() / "Library" / "Application Support" / "Recherche emploi"
MECANIQUES = {"nouvelle", "C", "doublon"}
SOUS_DOSSIERS = ("dossiers", "entretiens", "runs")
IGNORES_FICHIERS = {".DS_Store"}


def colonnes(cx, schema, table):
    return [r[1] for r in cx.execute(f"PRAGMA {schema}.table_info({table})")]


def communes(cx, table):
    m = colonnes(cx, "main", table)
    s = set(colonnes(cx, "src", table))
    return [c for c in m if c in s]


def app_ouverte():
    r = subprocess.run(["pgrep", "-f", "RechercheEmploi|jobsearch web .*--port 8775"],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def sauvegarder(cible_db, cible):
    dossier = cible / "sauvegardes"
    dossier.mkdir(exist_ok=True)
    dest = dossier / ("jobsearch-avant-import-%s.db" % dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    src = sqlite3.connect(cible_db)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return dest


def decide(statut):
    return statut not in MECANIQUES


def plus_recent(row):
    return row["decision_le"] or row["maj_le"] or row["cree_le"] or ""


def fusionner_annonces(cx, rapport):
    cols = communes(cx, "annonces")
    liste = ", ".join(cols)
    ajout = remplace = garde = 0
    conflits = []
    for s in cx.execute(f"SELECT {liste} FROM src.annonces").fetchall():
        m = cx.execute("SELECT * FROM main.annonces WHERE empreinte=?", (s["empreinte"],)).fetchone()
        if m is None:
            cx.execute(f"INSERT INTO main.annonces ({liste}) VALUES ({', '.join('?' * len(cols))})",
                       tuple(s[c] for c in cols))
            ajout += 1
            continue
        if s["statut"] == m["statut"]:
            # même statut : la source est la référence, on reprend sa fiche telle quelle
            gagnant = "source"
        elif decide(s["statut"]) and not decide(m["statut"]):
            gagnant = "source"
        elif decide(m["statut"]) and not decide(s["statut"]):
            gagnant = "cible"
        elif decide(s["statut"]) and decide(m["statut"]):
            gagnant = "source" if plus_recent(s) >= plus_recent(m) else "cible"
        else:
            gagnant = "source"
        if s["statut"] != m["statut"]:
            conflits.append((s["empreinte"], s["entreprise"], s["titre"], s["statut"], m["statut"], gagnant))
        if gagnant == "source":
            cx.execute("UPDATE main.annonces SET " + ", ".join(f"{c}=?" for c in cols if c != "empreinte")
                       + " WHERE empreinte=?",
                       tuple(s[c] for c in cols if c != "empreinte") + (s["empreinte"],))
            remplace += 1
        else:
            garde += 1
    rapport["annonces"] = {"ajoutées": ajout, "reprises de la source": remplace,
                           "gardées de l'application": garde, "conflits": conflits}


def inserer_manquants(cx, table, cles, rapport, remplacer=False, sans_id=False):
    cols = [c for c in communes(cx, table) if not (sans_id and c == "id")]
    liste = ", ".join(cols)
    if remplacer:
        cur = cx.execute(f"INSERT OR REPLACE INTO main.{table} ({liste}) SELECT {liste} FROM src.{table}")
    else:
        cond = " AND ".join(f"m.{k} IS s.{k}" for k in cles)
        cur = cx.execute(f"INSERT INTO main.{table} ({liste}) SELECT {liste} FROM src.{table} s "
                         f"WHERE NOT EXISTS (SELECT 1 FROM main.{table} m WHERE {cond})")
    rapport[table] = {"ajoutées": cur.rowcount}


def fusionner_par_date(cx, table, cle, date, rapport, remplacer_chemin=None):
    cols = communes(cx, table)
    liste = ", ".join(cols)
    ajout = remplace = 0
    for s in cx.execute(f"SELECT {liste} FROM src.{table}").fetchall():
        valeurs = dict(s)
        if remplacer_chemin and valeurs.get("chemin"):
            valeurs["chemin"] = valeurs["chemin"].replace(*remplacer_chemin)
        m = cx.execute(f"SELECT {date} FROM main.{table} WHERE {cle}=?", (s[cle],)).fetchone()
        if m is None:
            cx.execute(f"INSERT INTO main.{table} ({liste}) VALUES ({', '.join('?' * len(cols))})",
                       tuple(valeurs[c] for c in cols))
            ajout += 1
        elif (s[date] or "") > (m[0] or ""):
            cx.execute(f"UPDATE main.{table} SET " + ", ".join(f"{c}=?" for c in cols if c != cle)
                       + f" WHERE {cle}=?", tuple(valeurs[c] for c in cols if c != cle) + (s[cle],))
            remplace += 1
    rapport[table] = {"ajoutées": ajout, "remplacées (plus récentes)": remplace}


def fusionner_runs(cx, rapport):
    cols_r = communes(cx, "runs")
    cols_e = communes(cx, "run_etapes")
    ajout_r = ajout_e = 0
    for r in cx.execute("SELECT * FROM src.runs").fetchall():
        m = cx.execute("SELECT id FROM main.runs WHERE debut=? AND declencheur IS ?",
                       (r["debut"], r["declencheur"])).fetchone()
        if m is None:
            cols = [c for c in cols_r if c != "id"]
            cur = cx.execute(f"INSERT INTO main.runs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                             tuple(r[c] for c in cols))
            nouveau, ajout_r = cur.lastrowid, ajout_r + 1
        else:
            nouveau = m[0]
        cols = [c for c in cols_e if c != "id"]
        for e in cx.execute("SELECT * FROM src.run_etapes WHERE run_id=?", (r["id"],)).fetchall():
            if cx.execute("SELECT 1 FROM main.run_etapes WHERE run_id=? AND etape=? AND debut=?",
                          (nouveau, e["etape"], e["debut"])).fetchone():
                continue
            v = dict(e)
            v["run_id"] = nouveau
            cx.execute(f"INSERT INTO main.run_etapes ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                       tuple(v[c] for c in cols))
            ajout_e += 1
    rapport["runs"] = {"ajoutées": ajout_r}
    rapport["run_etapes"] = {"ajoutées": ajout_e}


def fusionner_base(source, cible, appliquer, rapport):
    cible_db = cible / "jobsearch.db"
    cx = sqlite3.connect(cible_db, timeout=30)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys=ON")
    cx.execute("ATTACH DATABASE ? AS src", (str(source / "jobsearch.db"),))
    tables = {r[0] for r in cx.execute("SELECT name FROM main.sqlite_master WHERE type='table'")}
    for t in ("annonces", "ids_source", "journal", "actions", "runs", "run_etapes", "evaluations",
              "courriers", "dossiers", "doublons_verdicts", "repertoire", "veilles"):
        if t not in tables:
            sys.exit(f"La base de l'application n'a pas la table {t} : lancer l'application une fois d'abord.")
    cx.execute("BEGIN")
    try:
        fusionner_annonces(cx, rapport)
        inserer_manquants(cx, "ids_source", ["cle"], rapport)
        inserer_manquants(cx, "journal", ["empreinte", "quand", "texte"], rapport, sans_id=True)
        inserer_manquants(cx, "actions", ["empreinte", "type", "cree_le", "donnees"], rapport, sans_id=True)
        fusionner_runs(cx, rapport)
        fusionner_par_date(cx, "evaluations", "empreinte", "cree_le", rapport)
        inserer_manquants(cx, "courriers", ["id"], rapport)
        fusionner_par_date(cx, "dossiers", "empreinte", "debut", rapport,
                           remplacer_chemin=(str(source / "dossiers"), str(cible / "dossiers")))
        inserer_manquants(cx, "doublons_verdicts", ["a", "b"], rapport, remplacer=True)
        inserer_manquants(cx, "repertoire", ["id"], rapport)
        inserer_manquants(cx, "veilles", ["date"], rapport)
        if appliquer:
            cx.execute("COMMIT")
        else:
            cx.execute("ROLLBACK")
    except Exception:
        cx.execute("ROLLBACK")
        raise
    finally:
        cx.close()


def copier_fichiers(source, cible, appliquer, rapport):
    for sous in SOUS_DOSSIERS:
        copies = deja = 0
        racine = source / sous
        if not racine.is_dir():
            rapport["fichiers " + sous] = {"absent de la source": True}
            continue
        for f in sorted(racine.rglob("*")):
            if not f.is_file() or f.name in IGNORES_FICHIERS:
                continue
            dest = cible / sous / f.relative_to(racine)
            if dest.exists():
                deja += 1
                continue
            if appliquer:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
            copies += 1
        rapport["fichiers " + sous] = {"copiés": copies, "déjà présents (gardés)": deja}


def lire_yaml(p):
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {} if p.exists() else {}


def chemin(d, cle):
    for k in cle.split("."):
        d = d.get(k) if isinstance(d, dict) else None
        if d is None:
            return None
    return d


def poser(d, cle, v):
    ks = cle.split(".")
    for k in ks[:-1]:
        d = d.setdefault(k, {})
    d[ks[-1]] = v


def nom(x):
    """La partie qui identifie une entrée : avant le premier « | » (« Nom | Organisme | Date »)."""
    return str(x).split("|")[0].strip().casefold()


def union(base, ajouts):
    """Ajoute à `base` ce qui n'y est pas ; une entrée de même nom mais de libellé différent
    (autre date, autre organisme) n'est pas ajoutée et revient comme quasi-doublon à relire."""
    vus = {nom(x) for x in base}
    resultat, proches = list(base), []
    for x in ajouts:
        if nom(x) in vus:
            if str(x) not in base:
                proches.append(x)
            continue
        vus.add(nom(x))
        resultat.append(x)
    return resultat, proches


def fusionner_profil(source, cible, appliquer, rapport):
    ps = lire_yaml(source / "profil.yaml")
    crit = lire_yaml(source.parent / "config" / "criteres.yaml")
    fichier = cible / "profil.yaml"
    pc = lire_yaml(fichier)
    ajouts, a_relire = {}, {}
    for cle, venant in (("dossier.certifications", chemin(ps, "dossier.certifications") or []),
                        ("recherche.entreprises", (chemin(ps, "recherche.entreprises") or [])
                         + (chemin(crit, "sourcing.entreprises") or []))):
        avant = list(chemin(pc, cle) or [])
        apres, proches = union(avant, venant)
        if len(apres) > len(avant):
            ajouts[cle] = apres[len(avant):]
            poser(pc, cle, apres)
        if proches:
            a_relire[cle] = proches
    rapport["profil"] = {"ajouts": ajouts} if ajouts else {"rien à ajouter": True}
    if a_relire:
        rapport["profil"]["déjà présents sous un autre libellé (non ajoutés, à relire)"] = a_relire
    if appliquer and ajouts:
        entete = []
        if fichier.exists():
            for ligne in fichier.read_text(encoding="utf-8").splitlines():
                if ligne.startswith("#"):
                    entete.append(ligne)
                else:
                    break
        corps = yaml.safe_dump(pc, allow_unicode=True, sort_keys=False, width=1000)
        fichier.write_text("\n".join(entete + [corps]) if entete else corps, encoding="utf-8")


def afficher(rapport, appliquer):
    print("=== %s ===" % ("Import appliqué" if appliquer else "Simulation : rien n'a été écrit"))
    for table, r in rapport.items():
        details = {k: v for k, v in r.items() if k != "conflits"}
        print("%-22s %s" % (table, ", ".join(f"{k} : {v}" for k, v in details.items())))
        for c in r.get("conflits", []):
            emp, ent, titre, s, m, g = c
            print("    conflit %s  %s — %s : source « %s », application « %s » → %s"
                  % (emp, ent, (titre or "")[:50], s, m, "la source gagne" if g == "source" else "l'application garde sa fiche"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="dossier data/ de l'installation à reprendre")
    ap.add_argument("--cible", default=str(CIBLE_DEFAUT), help="dossier de données de l'application")
    ap.add_argument("--appliquer", action="store_true", help="écrit vraiment (sinon simulation)")
    a = ap.parse_args()
    source, cible = Path(a.source).expanduser(), Path(a.cible).expanduser()
    if not (source / "jobsearch.db").exists():
        sys.exit(f"Pas de base dans {source}")
    if not (cible / "jobsearch.db").exists():
        sys.exit(f"Pas de base dans {cible} : lancer l'application une fois d'abord.")
    if a.appliquer and app_ouverte():
        sys.exit("L'application Recherche emploi est ouverte : la quitter d'abord (menu de la barre des menus).")
    rapport = {}
    if a.appliquer:
        print("Sauvegarde :", sauvegarder(cible / "jobsearch.db", cible))
    fusionner_base(source, cible, a.appliquer, rapport)
    copier_fichiers(source, cible, a.appliquer, rapport)
    fusionner_profil(source, cible, a.appliquer, rapport)
    afficher(rapport, a.appliquer)
    if not a.appliquer:
        print("\nPour écrire : quitter l'application, puis relancer avec --appliquer.")


if __name__ == "__main__":
    main()
