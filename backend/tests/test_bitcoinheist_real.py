"""Guards on the ChainSentry (BitcoinHeist) real-data benchmark.

Same discipline as test_elliptic_real.py: check provenance and leakage, not
accuracy, since the whole value of this benchmark is that its numbers can be
trusted. Skipped as a module when the CSV is absent, so a fresh clone still
runs a green suite.
"""

from __future__ import annotations

import pytest

from app.services import bitcoinheist_real as bh

pytestmark = pytest.mark.skipif(
    not bh.files_present(),
    reason="BitcoinHeistData.csv not downloaded; see data/README.md",
)


@pytest.fixture(scope="module")
def df():
    return bh.load_verified()


def test_dataset_matches_the_published_figures(df):
    """A truncated or substituted download must not pass silently.

    load_verified() already raises on a mismatch; this confirms the counts
    it checks are the real published ones, not something weaker.
    """
    assert len(df) == bh.EXPECTED_ROWS == 2_916_697
    white = int((df["label"] == "white").sum())
    assert white == bh.EXPECTED_WHITE == 2_875_284
    assert len(df) - white == bh.EXPECTED_ILLICIT == 41_413


def test_load_verified_rejects_a_truncated_file(tmp_path, monkeypatch):
    """The guard this module exists to provide: a partial download must fail
    loudly rather than silently producing plausible-looking numbers."""
    truncated = tmp_path / "BitcoinHeistData.csv"
    truncated.write_text("address,year,day,length,weight,count,looped,neighbors,income,label\n"
                          "1abc,2016,1,0,1.0,1,0,1,100,white\n")
    monkeypatch.setattr(bh, "DATA_CSV", truncated)
    with pytest.raises(ValueError, match="does not match the published"):
        bh.load_verified()


def test_temporal_split_leaks_no_address(df):
    """The split's whole claim rests on this.

    If the same address appeared with rows on both sides of the cutoff, its
    label could leak from train into test even though the label itself never
    changes for an address - the model would effectively be tested on
    addresses it already has the answer for.
    """
    train, test = bh.temporal_split(df)
    overlap = set(train["address"]) & set(test["address"])
    assert overlap == set()


def test_temporal_split_is_actually_temporal(df):
    train, test = bh.temporal_split(df)
    assert train["year"].max() < bh.SPLIT_YEAR
    assert test["year"].min() >= bh.SPLIT_YEAR
    assert len(train) > 0 and len(test) > 0


def test_split_year_gives_a_harder_test_than_the_alternative(df):
    """Documents why 2016 was chosen over 2017, rather than leaving that
    claim only in a comment where it could go stale silently."""
    train_2016, test_2016 = bh.temporal_split(df, split_year=2016)
    train_2017, test_2017 = bh.temporal_split(df, split_year=2017)

    illicit_2016 = int((test_2016["label"] != "white").sum())
    illicit_2017 = int((test_2017["label"] != "white").sum())
    assert illicit_2016 > illicit_2017

    unseen_2016 = set(test_2016.loc[test_2016.label != "white", "label"]) - set(
        train_2016.loc[train_2016.label != "white", "label"]
    )
    unseen_2017 = set(test_2017.loc[test_2017.label != "white", "label"]) - set(
        train_2017.loc[train_2017.label != "white", "label"]
    )
    assert len(unseen_2016) > len(unseen_2017)


def test_an_address_never_carries_two_different_labels(df):
    """The split's leak-freedom depends on a label being a property of the
    address, not of the row - this is what makes that true."""
    per_address = df.groupby("address")["label"].nunique()
    assert (per_address == 1).all()


def test_features_used_are_bitcoinheists_own_not_ours():
    """This benchmark must not quietly drift into testing our feature space -
    that would make it a duplicate of elliptic_real.py's test, not a
    complementary address-level one."""
    from app.services.feature_extraction import FEATURE_NAMES

    assert set(bh.BH_FEATURES).isdisjoint(FEATURE_NAMES)
