import pytest

from kycscreen.normalize import fold, normalize_name


def test_fold_strips_diacritics_and_special_letters():
    assert fold("José Pérez Ibáñez") == "jose perez ibanez"
    assert fold("Søren Bjørn Grünewald Straße") == "soren bjorn grunewald strasse"


def test_honorifics_stripped_from_the_front():
    assert normalize_name("His Excellency Sheikh Dr. Khalid Haddad") == ["khalid", "haddad"]


def test_single_token_honorific_is_kept():
    assert normalize_name("Sheikh") == ["sheikh"]


def test_arabic_particles_and_hyphens():
    assert normalize_name("Umar bin Laith Al-Masri") == ["umar", "laith", "masri"]
    assert normalize_name("Umar El Masri") == normalize_name("Omar al-Masri")


@pytest.mark.parametrize("variant", ["Mohammed", "Mohamed", "Mohammad", "Muhammed", "Mohamad", "MUHAMMAD"])
def test_muhammad_variants_canonicalize(variant):
    assert normalize_name(f"{variant} Haddad") == ["muhammad", "haddad"]


def test_cyrillic_ending_rules():
    assert normalize_name("Dmitri Kovalevskiy") == normalize_name("Dmitry Kovalevsky")
    assert normalize_name("Sergei Smirnoff") == ["sergey", "smirnov"]


def test_wade_giles_surname_maps_to_pinyin():
    assert normalize_name("Chang Wei") == ["zhang", "wei"]
    assert normalize_name("Hsu Hsin") == ["xu", "xin"]


def test_entity_legal_forms_and_dotted_acronyms():
    assert normalize_name("Al Noor General Trading L.L.C.", entity=True) == ["noor", "general", "trading"]
    assert normalize_name("The Northgate Shipping & Logistics GmbH", entity=True) == ["northgate", "shipping", "logistics"]
    assert normalize_name("Northgate Shipping Ltd", entity=True) == normalize_name("NORTHGATE SHIPPING LIMITED", entity=True)


def test_legal_forms_not_stripped_from_person_names():
    assert "co" in normalize_name("Co Lin", entity=False)


def test_spanish_compound_surnames():
    assert normalize_name("María de la Cruz García-López") == ["maria", "cruz", "garcia", "lopez"]
    assert normalize_name("José García y Peña") == ["jose", "garcia", "pena"]
