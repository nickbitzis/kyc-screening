from kycscreen.scoring import NameFrequency, dob_feature, name_similarity, score_pair

F = NameFrequency()


def s(q, r, qd=None, rd=None, qn=None, rn=None, qk="person", rk="person", freq=F):
    return score_pair(q.split(), qk, qd, qn, r.split(), rk, rd, rn, freq)


def test_name_score_is_monotone_in_closeness():
    exact = s("khalid haddad", "khalid haddad").total
    typo = s("khalid hadad", "khalid haddad").total
    partial = s("khalid", "khalid haddad").total
    other = s("igor volkov", "khalid haddad").total
    assert exact > typo > partial > other


def test_dob_evidence_is_ordered():
    base = ("khalid haddad", "khalid haddad")
    exact = s(*base, "1970-05-04", "1970-05-04").total
    year = s(*base, "1970-05-04", "1970").total
    unknown = s(*base, None, "1970-05-04").total
    wrong = s(*base, "1990-01-01", "1970-05-04").total
    assert exact > year > unknown > wrong


def test_nationality_evidence_is_ordered():
    base = ("khalid haddad", "khalid haddad", None, None)
    assert s(*base, "JO", "JO").total > s(*base, None, "JO").total > s(*base, "GB", "JO").total


def test_record_type_mismatch_penalized():
    assert s("orion trading", "orion trading", qk="person", rk="entity").total < s("orion trading", "orion trading").total - 30


def test_contributions_add_up_to_total():
    r = s("khalid hadad", "khalid haddad", "1970-05-04", "1970-05-04", "JO", "LB")
    assert abs(sum(c["points"] for c in r.contributions) - r.total) < 0.11


def test_common_tokens_count_less():
    freq = NameFrequency([["muhammad", f"x{i}"] for i in range(2000)] + [["rarename"]])
    common = name_similarity(["muhammad", "zzz"], ["muhammad", "qqq"], freq)[0]
    rare = name_similarity(["rarename", "zzz"], ["rarename", "qqq"], freq)[0]
    assert rare > common


def test_initials_and_order_swaps():
    assert s("k haddad", "khalid haddad").total > 70
    assert s("haddad khalid", "khalid haddad").total == s("khalid haddad", "khalid haddad").total


def test_dob_feature_cases():
    assert dob_feature("1970-05-04", "1970-04-05") == "near"
    assert dob_feature("1971-05-04", "1970-05-04") == "near"
    assert dob_feature("1970-05-04", "1970") == "same_year"
    assert dob_feature(None, "1970") == "unknown"
    assert dob_feature("1950-01-01", "1970-05-04") == "mismatch"
