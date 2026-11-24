import numpy as np

from edhrec_affinity.synergy_prototype.interaction_ranker import InteractionDeckRanker


def test_interaction_ranker_reads_the_same_deck_differently_for_each_candidate():
    ranker = InteractionDeckRanker.__new__(InteractionDeckRanker)
    ranker.empty = np.zeros(2, dtype=np.float32)
    ranker.features = np.eye(2, dtype=np.float32)
    ranker.pair_features = ranker.features
    ranker.projected_pairs = 2 * ranker.features
    candidates = np.array([[0, 1], [0, 1]])

    attended = ranker._attended_context(candidates, np.array([[0, 1, -1], [-1, -1, -1]]))
    reordered = ranker._attended_context(candidates, np.array([[1, 0], [-1, -1]]))

    assert attended[0, 0, 0] > attended[0, 1, 0]
    np.testing.assert_allclose(attended, reordered)
    np.testing.assert_array_equal(attended[1], np.zeros((2, 2)))
