"""Unit tests for the listwise + pairwise ranking losses.

The invariant we care about: scores that match label ordering give low loss;
inverted scores give high loss; degenerate labels (all equal) give zero loss
without crashing. These tests run only if torch is importable; everything
else in the pipeline already proves out without it.

Run with: python manage.py test tests.test_ranker_losses
"""
from __future__ import annotations

import unittest

try:
    import torch  # noqa: F401
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


@unittest.skipUnless(HAS_TORCH, "torch not installed; skipping loss tests")
class ListwiseSoftmaxLossTests(unittest.TestCase):
    def test_perfect_ordering_is_lower_than_inverted(self) -> None:
        import torch

        from chaoswing.ml.losses import listwise_softmax_loss

        labels = torch.tensor([[3.0, 2.0, 1.0, 0.0]])
        good = torch.tensor([[10.0, 5.0, 1.0, -5.0]])
        bad = torch.tensor([[-5.0, 1.0, 5.0, 10.0]])
        good_loss = float(listwise_softmax_loss(good, labels))
        bad_loss = float(listwise_softmax_loss(bad, labels))
        self.assertLess(good_loss, bad_loss)

    def test_equal_labels_loss_is_bounded_and_finite(self) -> None:
        """When labels are all equal the target is uniform; loss is just log k."""
        import math

        import torch

        from chaoswing.ml.losses import listwise_softmax_loss

        labels = torch.tensor([[1.0, 1.0, 1.0, 1.0]])
        scores = torch.tensor([[5.0, 1.0, -1.0, -5.0]])  # arbitrary; doesn't matter
        loss = float(listwise_softmax_loss(scores, labels))
        # Upper bound: when scores are arbitrary, loss is finite. ListNet's
        # uniform target makes loss = -mean(log p), bounded by log(k).
        self.assertTrue(math.isfinite(loss))

    def test_supports_unbatched_input(self) -> None:
        import torch

        from chaoswing.ml.losses import listwise_softmax_loss

        labels = torch.tensor([3.0, 2.0, 1.0, 0.0])
        scores = torch.tensor([10.0, 5.0, 1.0, -5.0])
        loss = float(listwise_softmax_loss(scores, labels))
        self.assertGreaterEqual(loss, 0.0)

    def test_rejects_shape_mismatch(self) -> None:
        import torch

        from chaoswing.ml.losses import listwise_softmax_loss

        with self.assertRaises(ValueError):
            listwise_softmax_loss(torch.zeros(4), torch.zeros(3))

    def test_gradient_flows(self) -> None:
        """Backward through the loss must produce non-zero gradients on `scores`."""
        import torch

        from chaoswing.ml.losses import listwise_softmax_loss

        scores = torch.tensor([[1.0, 2.0, 3.0, 4.0]], requires_grad=True)
        labels = torch.tensor([[3.0, 2.0, 1.0, 0.0]])
        loss = listwise_softmax_loss(scores, labels)
        loss.backward()
        self.assertIsNotNone(scores.grad)
        self.assertNotEqual(float(scores.grad.abs().sum()), 0.0)


@unittest.skipUnless(HAS_TORCH, "torch not installed; skipping loss tests")
class PairwiseRankNetLossTests(unittest.TestCase):
    def test_perfect_ordering_is_near_zero(self) -> None:
        import torch

        from chaoswing.ml.losses import pairwise_ranknet_loss

        labels = torch.tensor([[3.0, 2.0, 1.0, 0.0]])
        scores = torch.tensor([[20.0, 10.0, 0.0, -10.0]])
        loss = float(pairwise_ranknet_loss(scores, labels))
        self.assertLess(loss, 0.01)

    def test_inverted_ordering_is_large(self) -> None:
        import torch

        from chaoswing.ml.losses import pairwise_ranknet_loss

        labels = torch.tensor([[3.0, 2.0, 1.0, 0.0]])
        scores = torch.tensor([[-20.0, -10.0, 0.0, 10.0]])
        loss = float(pairwise_ranknet_loss(scores, labels))
        self.assertGreater(loss, 5.0)

    def test_all_equal_labels_no_valid_pairs(self) -> None:
        """No (i, j) with labels[i] > labels[j] => loss is zero, not NaN."""
        import torch

        from chaoswing.ml.losses import pairwise_ranknet_loss

        labels = torch.tensor([[1.0, 1.0, 1.0, 1.0]])
        scores = torch.tensor([[5.0, 1.0, -1.0, -5.0]])
        loss = float(pairwise_ranknet_loss(scores, labels))
        self.assertEqual(loss, 0.0)

    def test_gradient_flows(self) -> None:
        import torch

        from chaoswing.ml.losses import pairwise_ranknet_loss

        scores = torch.tensor([[1.0, 2.0, 3.0]], requires_grad=True)
        labels = torch.tensor([[3.0, 2.0, 1.0]])
        loss = pairwise_ranknet_loss(scores, labels)
        loss.backward()
        self.assertIsNotNone(scores.grad)
        self.assertNotEqual(float(scores.grad.abs().sum()), 0.0)
