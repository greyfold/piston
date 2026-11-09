from jobsearch.fingerprint import empreinte, normaliser


def test_normaliser_retire_bruit_et_accents():
    assert normaliser("Chef·fe de produit (h/f) 80-100 %") == "chef fe de produit"
    assert normaliser("Head of Design (m/w/d)") == "head of design"
    assert normaliser("Économiste") == "economiste"


def test_empreinte_stable():
    assert empreinte("SICPA SA", "Product Manager") == empreinte("sicpa sa", "Product Manager 100%")
    assert len(empreinte("a", "b")) == 12

