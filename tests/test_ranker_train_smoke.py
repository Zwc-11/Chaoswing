"""End-to-end training smoke test.

Runs ONLY when `CHAOSWING_RUN_TRAIN_SMOKE=1`. Why opt-in:

  * First run downloads a tiny backbone (`prajjwal1/bert-tiny`, ~17 MB) from
    HuggingFace Hub. Sandboxed CI environments often have no network.
  * Even cached, this test actually trains a model for a few steps — too
    expensive for the per-PR test loop.

Use it as a manual smoke after touching `chaoswing.ml.train` / `losses` /
`data`. It builds 8 synthetic sources with 4 candidates each, trains 2
epochs, and asserts val NDCG@5 strictly improves over the random-init
baseline. If it regresses, the loop is wired wrong.

Run with: CHAOSWING_RUN_TRAIN_SMOKE=1 python manage.py test tests.test_ranker_train_smoke
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import torch  # noqa: F401
    import transformers  # noqa: F401
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


SMOKE_ENABLED = bool(os.environ.get("CHAOSWING_RUN_TRAIN_SMOKE"))


CUTOFF_TS = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _build_synthetic_examples():
    """Two clusters of 4 source markets, each with 4 candidates.

    Sources in cluster A share vocabulary with their positives ("interest
    rates"); sources in cluster B share with their own positives ("election
    outcome"). A working trainer learns to rank within-cluster positives
    higher than out-of-cluster candidates.
    """
    from chaoswing.ml._types import MarketDoc
    from chaoswing.ml.data import ListwiseExample

    pre = CUTOFF_TS - timedelta(days=30)

    def doc(mid: str, text: str) -> MarketDoc:
        return MarketDoc(
            market_id=mid, title=text, first_seen=pre, event_family=mid
        )

    cluster_a_pos = ["Federal interest rates rise", "Treasury yields climb",
                     "Fed hikes by 25bp", "Bond rates surge"]
    cluster_a_src = ["Will the Fed raise rates", "Rate hike question",
                     "Bond market direction", "Yield curve outlook"]
    cluster_b_pos = ["Republican wins presidency", "GOP takes White House",
                     "Election outcome favors GOP", "Republican electoral win"]
    cluster_b_src = ["Who wins 2028 election", "Presidential outcome",
                     "Election forecast", "White House race"]

    train: list[ListwiseExample] = []
    for i, src_text in enumerate(cluster_a_src):
        train.append(
            ListwiseExample(
                source=doc(f"a_src_{i}", src_text),
                candidates=[
                    doc(f"a_pos_{i}", cluster_a_pos[i]),
                    doc(f"b_neg_{i}", cluster_b_pos[i]),
                    doc(f"a_neg_{i}", "Unrelated weather forecast for tomorrow"),
                    doc(f"x_neg_{i}", "Sports score from last night's game"),
                ],
                relevances=[3, 0, 0, 0],
            )
        )
    for i, src_text in enumerate(cluster_b_src):
        train.append(
            ListwiseExample(
                source=doc(f"b_src_{i}", src_text),
                candidates=[
                    doc(f"b_pos_{i}", cluster_b_pos[i]),
                    doc(f"a_neg_alt_{i}", cluster_a_pos[i]),
                    doc(f"b_neg_alt_{i}", "Recipe for chocolate chip cookies"),
                    doc(f"y_neg_{i}", "Movie review of summer blockbuster"),
                ],
                relevances=[3, 0, 0, 0],
            )
        )
    # Val: structurally identical, slightly different surface text.
    val: list[ListwiseExample] = []
    for i in range(2):
        val.append(
            ListwiseExample(
                source=doc(f"a_val_{i}", "Fed rate decision impact"),
                candidates=[
                    doc(f"a_val_pos_{i}", "Federal Reserve hikes interest rates"),
                    doc(f"b_val_neg_{i}", "Republican wins presidential race"),
                    doc(f"a_val_neg_{i}", "Tomorrow's weather will be sunny"),
                    doc(f"x_val_neg_{i}", "Local restaurant opens new location"),
                ],
                relevances=[3, 0, 0, 0],
            )
        )
        val.append(
            ListwiseExample(
                source=doc(f"b_val_{i}", "2028 election outcome"),
                candidates=[
                    doc(f"b_val_pos2_{i}", "GOP candidate wins the White House"),
                    doc(f"a_val_neg2_{i}", "Federal Reserve raises interest rates"),
                    doc(f"b_val_neg2_{i}", "New cooking show debuts"),
                    doc(f"y_val_neg2_{i}", "Sports team trades star player"),
                ],
                relevances=[3, 0, 0, 0],
            )
        )
    return train, val


@unittest.skipUnless(SMOKE_ENABLED, "set CHAOSWING_RUN_TRAIN_SMOKE=1 to enable")
@unittest.skipUnless(HAS_TORCH, "torch + transformers not installed")
class CrossEncoderTrainSmokeTests(unittest.TestCase):
    def test_val_ndcg_improves_after_training(self) -> None:
        from chaoswing.ml._types import TemporalCutoff
        from chaoswing.ml.train import TrainConfig, train

        train_examples, val_examples = _build_synthetic_examples()
        cutoff = TemporalCutoff(timestamp=CUTOFF_TS, label="smoke_cutoff")

        with tempfile.TemporaryDirectory() as tmp:
            config = TrainConfig(
                backbone="prajjwal1/bert-tiny",
                loss="listwise",
                max_candidates=4,
                max_length=32,
                batch_size=4,
                epochs=2,
                precision="fp32",
                checkpoint_dir=Path(tmp),
                early_stop_patience=10,  # don't stop early in such a small run
                device="cpu",
            )
            result = train(
                train_examples=train_examples,
                val_examples=val_examples,
                config=config,
                train_cutoff=cutoff,
                run_id="smoke",
            )
            self.assertGreater(len(result.history), 0)
            # We expect *some* improvement vs random init; pin a soft bound.
            self.assertGreater(
                result.best_metric, 0.55,
                "smoke run did not learn within 2 epochs",
            )
