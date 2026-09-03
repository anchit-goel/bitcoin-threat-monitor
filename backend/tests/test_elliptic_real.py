"""Guards on the real-data benchmark.

The point of this benchmark is that its numbers can be trusted, so the tests
here are mostly about provenance and leakage rather than accuracy. If the
Elliptic files are not present the whole module is skipped, so a fresh clone
still runs a green suite.
"""

from __future__ import annotations

import networkx as nx
import pandas as pd
import pytest

from app.services import elliptic_real as er

pytestmark = pytest.mark.skipif(
    not er.files_present(),
    reason="Elliptic CSVs not downloaded; see data/README.md",
)


@pytest.fixture(scope="module")
def loaded():
    return er.load_graph()


def test_dataset_matches_the_published_figures(loaded):
    """A truncated or substituted download must not pass silently.

    Both counts come from the Elliptic paper. Every number this benchmark
    reports is meaningless if the input is not the real dataset.
    """
    graph, classes = loaded
    assert graph.number_of_nodes() == 203_769
    assert graph.number_of_edges() == 234_355
    counts = classes["class"].value_counts()
    assert counts[er.ILLICIT] == 4_545
    assert counts[er.LICIT] == 42_019


def test_every_edge_endpoint_is_a_known_node(loaded):
    graph, classes = loaded
    known = set(classes["txId"])
    assert all(u in known and v in known for u, v in graph.edges())


def test_graph_has_the_49_time_step_components(loaded):
    """The split depends on this, so it is checked rather than assumed."""
    graph, _ = loaded
    assert nx.number_connected_components(graph.to_undirected()) == 49


def test_features_are_finite_and_aligned(loaded):
    graph, classes = loaded
    sub = graph.subgraph(list(graph.nodes())[:4000]).copy()
    frame = er.structural_features(sub)
    assert list(frame.columns) == er.STRUCTURAL_FEATURES
    assert len(frame) == sub.number_of_nodes()
    assert not frame.isna().any().any()
    assert frame.to_numpy().max() < float("inf")


def test_component_split_leaks_no_edges(loaded):
    """The whole claim of the benchmark rests on this.

    If a single edge crossed the split, a test node's neighbourhood could
    carry its label across, and the score would be inflated.
    """
    graph, _ = loaded
    components = er.component_of(graph)
    crossing = [
        (u, v) for u, v in graph.edges() if components[u] != components[v]
    ]
    assert crossing == [], f"{len(crossing)} edges cross component boundaries"


def test_structural_feature_set_is_a_subset_of_our_own():
    """This benchmark must test our real features, not a parallel invention."""
    from app.services.feature_extraction import FEATURE_NAMES

    assert set(er.STRUCTURAL_FEATURES) <= set(FEATURE_NAMES)
    assert set(er.UNTESTED_FEATURES) <= set(FEATURE_NAMES)
    # Together they should account for the entire feature set, so nothing is
    # quietly dropped from the honesty accounting.
    assert set(er.STRUCTURAL_FEATURES) | set(er.UNTESTED_FEATURES) == set(FEATURE_NAMES)
