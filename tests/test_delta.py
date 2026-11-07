from kycscreen.data import evolve_watchlist
from kycscreen.delta import CustomerIndex, delta_rescreen, full_rescreen

T = 71.5


def test_delta_matches_full_rescreen(small_ds):
    prev, _ = full_rescreen(small_ds.customers, small_ds.watchlist, small_ds.freq, T)
    new_wl, change = evolve_watchlist(small_ds.watchlist, n_add=12, n_modify=8, n_remove=5)
    full, _ = full_rescreen(small_ds.customers, new_wl, small_ds.freq, T)
    delta, stats = delta_rescreen(prev, small_ds.customers, new_wl, change, small_ds.freq, T)
    assert delta == full
    assert stats["customers_rechecked"] < len(small_ds.customers) / 2


def test_removed_entries_clear_their_alerts(small_ds):
    prev, _ = full_rescreen(small_ds.customers, small_ds.watchlist, small_ds.freq, T)
    victim = next(iter(prev))[1]
    kept = [e for e in small_ds.watchlist if e.id != victim]
    change = {"added": [], "modified": [], "removed": [victim]}
    delta, stats = delta_rescreen(prev, small_ds.customers, kept, change, small_ds.freq, T)
    assert all(k[1] != victim for k in delta)
    assert stats["cleared_alerts"]


def test_new_entry_is_found_for_matching_customer(small_ds):
    from kycscreen.data import Entry
    c = next(x for x in small_ds.customers if x.kind == "person" and not x.true_entry)
    new = Entry("WL-99999", "person", "sanctions", c.name, [], c.dob, c.nationality)
    prev, _ = full_rescreen(small_ds.customers, small_ds.watchlist, small_ds.freq, T)
    cindex = CustomerIndex(small_ds.customers, small_ds.freq)
    delta, stats = delta_rescreen(prev, small_ds.customers, [*small_ds.watchlist, new],
                                  {"added": [new.id], "modified": [], "removed": []}, small_ds.freq, T, cindex)
    assert (c.id, new.id) in delta and (c.id, new.id) in stats["new_alerts"]
