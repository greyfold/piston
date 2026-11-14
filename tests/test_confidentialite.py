"""Les coordonnées ne partent jamais vers l'IA : balises à l'aller, vraies valeurs au retour."""

from pydantic import BaseModel

from conftest import annonce
from jobsearch import confidentialite, dossier, moteur
from jobsearch.moteur import FauxMoteur, Protege

EXECUTABLE = moteur.executable
ETAT = moteur.etat
OUVRIR = moteur.ouvrir          # la vraie fonction : le conftest la remplace pendant les tests

IDENTITE = {"prenom": "Claire", "nom": "Fontaine", "adresse": "Chemin des Lilas 4", "npa": "1205",
            "ville": "Genève", "telephone": "+41 79 123 45 67", "email": "claire.fontaine@example.ch",
            "site": "https://www.clairefontaine.example", "linkedin": "https://www.linkedin.com/in/claire-fontaine-ex/"}

CV_MAITRE = """# CV maître

## Identité et contact

| Champ | Valeur |
|---|---|
| Nom | Claire Fontaine |
| Adresse | Chemin des Lilas 4, 1205 Genève |
| Téléphone | 079 123 45 67 |
| E-mail | ancienne.adresse@example.org |
| Site | www.clairefontaine.example |
| LinkedIn | linkedin.com/in/claire-fontaine-ex/ |
| Naissance | 6 février 1989 |
| Nationalité | Suisse |

## Expérience

### Responsable UX — Exemple SA, Genève (2021 – 2025)
- Claire a formé 300 personnes ; contact de référence : Paul Martin, paul.martin@exemple-sa.ch, +41 22 555 66 77.
- Sources : `CV-Claire-Fontaine.pdf`, dossier « Fontaine, Claire ».
- Une fontaine à eau dans l'open space, sans rapport.
- Né le 06.02.1989, AVS 756.1234.5678.97.
"""

VALEURS = ["Claire", "Fontaine", "Lilas", "123 45 67", "claire.fontaine@example.ch", "ancienne.adresse",
           "clairefontaine.example", "claire-fontaine-ex", "1989", "756.1234"]


def masque():
    return confidentialite.construire(IDENTITE, CV_MAITRE)


def test_aucune_coordonnee_dans_le_contexte_masque():
    texte = masque().masquer(CV_MAITRE, contexte=True)
    for v in VALEURS:
        assert v not in texte, v
    assert "| Nom | {NOM_COMPLET} |" in texte and "| Adresse | {ADRESSE} |" in texte
    assert "| Téléphone | {TELEPHONE} |" in texte and "| Naissance | {DATE_NAISSANCE} |" in texte
    assert "| E-mail | {EMAIL} |" in texte and "| Site | {SITE} |" in texte and "| LinkedIn | {LINKEDIN} |" in texte
    assert "`CV-{PRENOM}-{NOM}.pdf`" in texte and "« {NOM}, {PRENOM} »" in texte
    assert "Une fontaine à eau" in texte                    # un mot courant n'est pas un nom propre
    assert "| Nationalité | Suisse |" in texte and "Genève (2021" in texte   # utiles à l'IA, gardés


def test_un_tiers_cite_a_sa_propre_balise_rendue_telle_quelle():
    m = masque()
    texte = m.masquer(CV_MAITRE, contexte=True)
    assert "paul.martin@exemple-sa.ch" not in texte and "555 66 77" not in texte
    assert m.demasquer("{EMAIL_2} · {TELEPHONE_2}") == "paul.martin@exemple-sa.ch · +41 22 555 66 77"


def test_le_retour_rend_les_valeurs_du_profil():
    m = masque()
    m.masquer(CV_MAITRE, contexte=True)
    entete = m.demasquer("# {NOM_COMPLET}\n{ADRESSE} | {TELEPHONE} | {email} | {SITE} | {{LINKEDIN}}\n{INCONNUE}")
    assert entete == ("# Claire Fontaine\nChemin des Lilas 4, 1205 Genève | +41 79 123 45 67 | "
                      "claire.fontaine@example.ch | www.clairefontaine.example | linkedin.com/in/claire-fontaine-ex/\n"
                      "{INCONNUE}")


def test_formats_courants_reconnus():
    m = masque()
    for t in ("+41 79 123 45 67", "+41791234567", "0041 79 123 45 67", "079 123 45 67", "079.123.45.67",
              "06.02.1989", "1989-02-06", "6 février 1989", "February 6, 1989", "CLAIRE FONTAINE",
              "Chemin des Lilas 4 · 1205 Genève", "https://clairefontaine.example/"):
        assert "{" in m.masquer("Contact : %s." % t) and not any(c.isdigit() for c in m.masquer(t)), t


def test_message_sans_detection_large_mais_avs_toujours_masque():
    m = masque()
    texte = m.masquer("Postuler : rh@employeur.ch, +41 21 000 11 22. AVS 756.9999.8888.77")
    assert "rh@employeur.ch" in texte and "+41 21 000 11 22" in texte      # l'annonce garde ses contacts
    assert "756.9999" not in texte


class Fiche(BaseModel):
    signature: str
    points: list[str]


def test_protege_ne_laisse_rien_passer_et_rend_les_vraies_valeurs():
    interne = FauxMoteur([Fiche(signature="{NOM_COMPLET}, {TELEPHONE}", points=["{PRENOM} a formé 300 personnes"])])
    t = moteur.Tache("dossier", "Tu écris pour Claire Fontaine.", contexte=CV_MAITRE, schema=Fiche)
    r = Protege(interne, masque()).executer(t, "Annonce. Écrire à claire.fontaine@example.ch.")
    tache, message = interne.appels[0]
    envoye = tache.consigne + tache.contexte + message
    for v in VALEURS:
        assert v not in envoye, v
    assert "Confidentialité : les coordonnées" in tache.consigne and "{NOM_COMPLET} : nom et prénom" in tache.consigne
    assert r.objet.signature == "Claire Fontaine, +41 79 123 45 67"
    assert r.objet.points == ["Claire a formé 300 personnes"]
    assert t.contexte == CV_MAITRE                                     # la tâche d'origine n'est pas touchée


def test_protege_lit_le_profil_et_laisse_passer_une_tache_non_masquee():
    interne = FauxMoteur(["Bonjour {PRENOM}.", "Bonjour Camille."])
    p = Protege(interne)                                              # profil de test : Camille Exemple
    assert p.executer(moteur.Tache("x", "Pour Camille Exemple."), "camille@example.ch").texte == "Bonjour Camille."
    assert "Camille" not in interne.appels[0][0].consigne and interne.appels[0][1] == "{EMAIL}"
    p.executer(moteur.Tache("y", "Pour Camille.", masquer=False), "m")
    assert interne.appels[1][0].consigne == "Pour Camille."


def test_ouvrir_rend_toujours_un_moteur_protege(monkeypatch):
    from jobsearch.travailleur import client as travailleur
    monkeypatch.setattr(moteur, "executable", lambda: "/opt/claude")
    monkeypatch.setattr(travailleur, "distant", lambda: False)
    assert isinstance(OUVRIR(verifier=lambda: True), Protege)
    monkeypatch.setattr(travailleur, "distant", lambda: True)
    monkeypatch.setattr(travailleur.Distant, "depuis_reglages", classmethod(lambda cls: cls("http://127.0.0.1:1", jeton_="x")))
    monkeypatch.setattr(travailleur.Distant, "verifie", lambda self: self)
    p = OUVRIR()
    assert isinstance(p, Protege) and isinstance(p.interne, travailleur.Distant)


def test_fiche_d_envoi_completee_sans_claude(reg):
    from jobsearch.navigateur import valeurs_jobroom
    e = annonce(reg, entreprise="Exemple SA", titre="Chef·fe de projet | IA", lieu="Pully")
    f = reg.get(e)
    claude = ("## 0. Avant d'envoyer\nRien.\n## 3. Réponses aux questions standard\n| Question | Réponse |\n|---|---|\n"
              "| Disponibilité | immédiate |\n## 4. Prétentions salariales\n100 000\n## 5. Job-Room\nà ignorer\n")
    envoi = dossier.completer_envoi(claude, f)
    assert "à ignorer" not in envoi and envoi.count("## 5. Job-Room") == 1
    assert envoi.index("Assignation par l'ORP : non") < envoi.index("## 4.")
    assert "| Lieu | adresse à compléter (lieu de l'annonce : Pully) |" in envoi
    f = dict(f, postulee_le="2026-11-09", extra={})
    v = valeurs_jobroom(f, envoi)
    assert v["entreprise"] == "Exemple SA" and v["poste"] == "Chef·fe de projet / IA"


def test_une_balise_restee_dans_le_cv_est_signalee(tmp_path):
    f = {"titre": "AI Product Manager", "langue": "en", "entreprise": "Exemple SA"}
    pieces = {"cv": "# Claire Fontaine\n## AI Product Manager\n{TELEPHONE}\n", "lettre": "Dear team,\n",
              "envoi": "", "analyse": ""}
    cq = {r["cq"][:5]: r for r in dossier.controler(f, tmp_path, pieces, None, "")}
    assert not cq["CQ-01"]["ok"] and "{TELEPHONE}" in cq["CQ-01"]["detail"]


# --- Formes que le masque laissait passer ------------------------------------------------

LI = {"prenom": "Jean", "nom": "Li", "adresse": "Rue du Lac 12", "npa": "1000", "ville": "Lausanne",
      "telephone": "079 123 45 67", "email": "jean.li@example.ch", "date_naissance": "12.03.1985"}


def _masque(**plus):
    return confidentialite.construire(dict(LI, **plus))


def test_nom_de_deux_lettres_en_nom_propre_seulement():
    m = _masque()
    assert m.masquer("Contact : M. Li, Mme Li, Dr LI.") == "Contact : M. {NOM}, Mme {NOM}, Dr {NOM}."
    assert m.masquer("Jean Li et Li Jean") == "{NOM_COMPLET} et {NOM} {PRENOM}"
    assert "{NOM}" not in m.masquer("par LI.")            # mot isolé : jamais
    # Un mot courant ou une syllabe n'est pas le nom : casse et limites de mot.
    for texte in ("li ", "le multi-livre", "Lisbonne", "dali", "les lis"):
        assert "{NOM}" not in m.masquer(texte), texte


def test_nom_court_qui_est_un_mot_courant_n_est_pas_masque():
    m = _masque(nom="Le")
    for texte in ("Le poste est ouvert", "LE CDI", "Ma motivation", "le chef"):
        assert m.masquer(texte) == texte, texte
    assert m.masquer("Contact : Mme Le") == "Contact : Mme {NOM}"
    assert "{NOM}" not in _masque(prenom="Ma", nom="Rey").masquer("Ma motivation")


def test_formes_composees_et_adresse_batie_sur_le_nom():
    m = _masque()
    out = m.masquer("jean-li, jean.li, li_jean, Jean-Li")
    assert out == "{PRENOM}-{NOM}, {PRENOM}.{NOM}, {NOM}_{PRENOM}, {PRENOM}-{NOM}"
    assert m.demasquer(out) == "Jean-Li, Jean.Li, Li_Jean, Jean-Li"
    autre = m.masquer("Écrire à j.li@autre.ch ou jean.li@autre.example ou service@autre.ch")
    assert "j.li" not in autre and "jean.li" not in autre and "service@autre.ch" in autre
    assert m.demasquer(autre) == "Écrire à j.li@autre.ch ou jean.li@autre.example ou service@autre.ch"


def test_telephone_avec_indicatif_regional_entre_parentheses():
    m = _masque()
    assert m.masquer("Tél. (079) 123 45 67, ou 079 123 45 67.") == "Tél. {TELEPHONE}, ou {TELEPHONE}."
    assert _masque().masquer("Autre : (021) 555 66 77", contexte=True) == "Autre : {TELEPHONE_2}"


def test_date_de_naissance_sur_deux_chiffres():
    m = _masque()
    assert m.masquer("né le 12.03.85, ou 12.03.1985, ou 12/3/85") == "né le {DATE_NAISSANCE}, ou {DATE_NAISSANCE}, ou {DATE_NAISSANCE}"
    assert "{DATE_NAISSANCE}" not in m.masquer("le 12.03.86 et 13.03.85")
    assert "{DATE_NAISSANCE}" in _masque(date_naissance="12.03.85").masquer("12.03.1985 et 12.03.85")


def test_rue_ecrite_numero_avant_la_rue():
    m = _masque()
    assert m.masquer("Domicile : 12, rue du Lac, Lausanne.") == "Domicile : {RUE}, Lausanne."
    assert m.masquer("12, Rue du Lac, 1000 Lausanne") == "{ADRESSE}"
    assert m.masquer("Rue du Lac 12") == "{RUE}"
    assert "{RUE}" not in m.masquer("Rue du Lac 14 ; 112, rue du Lac")


def test_balise_ecrite_dans_une_annonce_n_est_pas_retablie():
    interne = FauxMoteur(["Appelez {TELEPHONE} ou {AVS} ; mon numéro est {TELEPHONE_2}."])
    p = Protege(interne, _masque())
    p.executer(moteur.Tache("x", "Consigne."), "Annonce : écrire à {TELEPHONE} et {AVS}. Tél. 079 123 45 67.")
    envoye = interne.appels[0][1]
    assert "{TELEPHONE}" not in envoye.split("Tél.")[0] and "{AVS}" not in envoye
    assert envoye.endswith("Tél. {TELEPHONE}.")                      # la vraie valeur reste masquée
    # Claude recopie la balise de l'annonce (abritée) : elle ressort telle qu'écrite, jamais en valeur.
    interne2 = FauxMoteur([envoye])
    r = Protege(interne2, _masque()).executer(moteur.Tache("x", "Consigne."), "Annonce : écrire à {TELEPHONE} et {AVS}. Tél. 079 123 45 67.")
    assert r.texte == "Annonce : écrire à {TELEPHONE} et {AVS}. Tél. 079 123 45 67."
    assert "079" not in interne2.appels[0][1]


def test_le_moteur_ne_lance_jamais_claude_hors_de_la_vm(monkeypatch):
    import pytest
    from jobsearch.travailleur import client as travailleur
    monkeypatch.delenv("JOBSEARCH_VM", raising=False)
    monkeypatch.setattr(travailleur, "distant", lambda: False)
    with pytest.raises(moteur.LancementImpossible, match="machine virtuelle"):
        EXECUTABLE()
    with pytest.raises(moteur.LancementImpossible, match="machine virtuelle"):
        OUVRIR(verifier=lambda: True)
    assert ETAT(lambda *a, **k: None)["installe"] is False
