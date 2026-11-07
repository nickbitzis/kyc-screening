import pytest

from kycscreen.similarity import jaro, jaro_winkler, levenshtein, levenshtein_ratio, ngrams, phonetic_key


@pytest.mark.parametrize("a,b,expected", [
    ("MARTHA", "MARHTA", 0.9611),
    ("DWAYNE", "DUANE", 0.84),
    ("DIXON", "DICKSONX", 0.8133),
    ("JELLYFISH", "SMELLYFISH", 0.8963),
])
def test_jaro_winkler_known_values(a, b, expected):
    assert jaro_winkler(a, b) == pytest.approx(expected, abs=1e-3)


def test_jaro_known_value_and_edges():
    assert jaro("MARTHA", "MARHTA") == pytest.approx(0.9444, abs=1e-3)
    assert jaro("abc", "abc") == 1.0
    assert jaro("", "abc") == 0.0
    assert jaro("abc", "xyz") == 0.0


def test_jaro_winkler_symmetric_and_bounded():
    for a, b in [("hassan", "hasan"), ("volkov", "volkoff"), ("li", "lee")]:
        assert jaro_winkler(a, b) == pytest.approx(jaro_winkler(b, a))
        assert 0.0 <= jaro_winkler(a, b) <= 1.0


def test_levenshtein():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3
    assert levenshtein_ratio("kitten", "sitting") == pytest.approx(1 - 3 / 7)


def test_levenshtein_early_exit():
    assert levenshtein("aaaaaaaa", "bbbbbbbb", max_dist=2) == 3
    assert levenshtein_ratio("aaaaaaaa", "bbbbbbbb", floor=0.5) == 0.0


def test_phonetic_key_groups_transliterations():
    keys = {phonetic_key(x) for x in ["muhammad", "mohammed", "mohamed", "muhamad"]}
    assert len(keys) == 1
    assert phonetic_key("hussein") == phonetic_key("husain")
    assert phonetic_key("haddad") != phonetic_key("mansour")


def test_ngrams_are_padded():
    assert ngrams("ali") == {"^al", "ali", "li$"}
