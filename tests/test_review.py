import pytest

from kycscreen.review import ReviewStore


def test_case_open_is_idempotent():
    st = ReviewStore()
    a = st.open_case("C-1", "Name", "WL-1", "sanctions", 88.0)
    assert st.open_case("C-1", "Name", "WL-1", "sanctions", 90.0) == a
    assert st.counts() == {"open": 1}


def test_disposition_flow():
    st = ReviewStore()
    cid = st.open_case("C-1", "Name", "WL-1", "sanctions", 88.0)
    assert st.dispose(cid, "escalated", "ana")["status"] == "escalated"
    assert st.dispose(cid, "false_positive", "lead", "DOB differs")["status"] == "closed"
    with pytest.raises(ValueError):
        st.dispose(cid, "true_match", "lead")
    with pytest.raises(ValueError):
        st.dispose(st.open_case("C-2", "N", "WL-2", "pep", 75), "maybe", "ana")
    with pytest.raises(KeyError):
        st.dispose(999, "true_match", "ana")


def test_audit_hash_chain_detects_tampering():
    st = ReviewStore()
    cid = st.open_case("C-1", "Name", "WL-1", "sanctions", 88.0)
    st.dispose(cid, "true_match", "ana")
    assert st.verify_chain()
    st.db.execute("UPDATE audit SET actor='someone' WHERE id=1")
    assert not st.verify_chain()
