"""Cross-encoder training loop.

This is the headline module of the project (CLAUDE.md golden rule #4). We
initialize from a pretrained backbone, full-fine-tune every layer with a
fresh single-logit ranking head, and train with a **listwise softmax** loss
over per-source candidate lists. NDCG@5 on the val split is the early-stop
signal; best checkpoint goes to `models/cross_encoder/<run>/`.

What's deliberately NOT here:
  * Django imports — this file is pure torch/transformers (CLAUDE.md
    "Repo conventions"). The management command does the ORM work and hands
    off materialized example lists.
  * Trainer-class indirection — we hand-write the loop. It's ~200 lines, easy
    to read, and makes precision / loss / eval choices explicit.

Leakage discipline transfer:
  * The model is text-only — no timestamps reach the encoder.
  * The training-data selection step (in the command) already filtered by
    `TemporalSplit`. `validate_training_examples` is a defensive pre-flight
    that re-checks the source `first_seen` against the cutoff before any
    gradient flows. Belt-and-suspenders.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Sequence

from chaoswing.ml._types import TemporalCutoff
from chaoswing.ml.eval import ndcg_at_k
from chaoswing.ml.leakage import LeakageError, assert_before_cutoff

if TYPE_CHECKING:
    from chaoswing.ml.data import ListwiseExample


logger = logging.getLogger(__name__)


LossKind = Literal["pairwise", "listwise"]
Precision = Literal["fp32", "fp16", "bf16"]


# ---------------------------------------------------------------------------
# Config + result types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TrainConfig:
    backbone: str = "microsoft/deberta-v3-base"
    loss: LossKind = "listwise"
    max_candidates: int = 16
    max_length: int = 128
    learning_rate: float = 2e-5
    weight_decay: float = 0.01
    warmup_ratio: float = 0.1
    batch_size: int = 16
    grad_accum_steps: int = 1
    epochs: int = 3
    precision: Precision = "bf16"
    early_stop_metric: str = "ndcg_at_5"
    early_stop_patience: int = 2
    checkpoint_dir: Path = Path("models/cross_encoder")
    seed: int = 1729
    mlflow_run_name: str | None = None
    device: str | None = None  # autodetect when None


@dataclass(slots=True)
class TrainResult:
    best_metric: float
    best_epoch: int
    checkpoint_path: Path
    history: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pre-flight: leakage check on training examples
# ---------------------------------------------------------------------------


def validate_training_examples(
    examples: Sequence["ListwiseExample"],
    cutoff: TemporalCutoff,
) -> None:
    """Raise `LeakageError` if any source or candidate is at or past the cutoff.

    The trainer runs this before allocating the model. The test suite calls
    it directly to prove no leakage path bypasses the splitter.
    """
    for ex in examples:
        if ex.source.first_seen is None:
            raise LeakageError(
                f"training source {ex.source.market_id} has no first_seen; "
                f"cannot prove it respects {cutoff.label}"
            )
        assert_before_cutoff(
            ex.source.first_seen,
            cutoff,
            what=f"training source {ex.source.market_id}.first_seen",
        )
        for cand in ex.candidates:
            if cand.first_seen is None:
                continue  # candidates without timestamps are tolerated; loss is text-only
            assert_before_cutoff(
                cand.first_seen,
                cutoff,
                what=f"training candidate {cand.market_id}.first_seen",
            )


# ---------------------------------------------------------------------------
# Trainer
# ---------------------------------------------------------------------------


def _pick_dtype(precision: Precision, device: str):
    """Map a `precision` string to a torch dtype, falling back if unsupported."""
    import torch

    if precision == "fp32":
        return torch.float32
    if precision == "bf16":
        if device == "cuda" and torch.cuda.is_bf16_supported():
            return torch.bfloat16
        logger.warning("bf16 requested but not available on %s; falling back to fp32", device)
        return torch.float32
    if precision == "fp16":
        if device == "cuda":
            return torch.float16
        logger.warning("fp16 requested but cuda unavailable; falling back to fp32")
        return torch.float32
    return torch.float32


def _resolve_device(requested: str | None) -> str:
    import torch

    if requested:
        return requested
    return "cuda" if torch.cuda.is_available() else "cpu"


def _build_optimizer_and_scheduler(model, *, config: TrainConfig, total_steps: int):
    from torch.optim import AdamW
    from transformers import get_linear_schedule_with_warmup

    optimizer = AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    warmup_steps = max(1, int(total_steps * config.warmup_ratio))
    scheduler = get_linear_schedule_with_warmup(
        optimizer, num_warmup_steps=warmup_steps, num_training_steps=total_steps
    )
    return optimizer, scheduler


def _loss_fn(kind: LossKind):
    from chaoswing.ml.losses import listwise_softmax_loss, pairwise_ranknet_loss

    if kind == "pairwise":
        return pairwise_ranknet_loss
    return listwise_softmax_loss


def _forward_listwise(model, batch, *, device: str, autocast_dtype):
    """Forward a listwise batch through the model and return (scores, labels).

    `batch` is the default-collated dict from `ListwiseDataset`:
      input_ids:      (B, k, seq_len)
      attention_mask: (B, k, seq_len)
      labels:         (B, k)

    Reshape to (B*k, seq_len) for the encoder, then back to (B, k) scores.
    """
    import torch

    input_ids = batch["input_ids"].to(device)
    attention_mask = batch["attention_mask"].to(device)
    labels = batch["labels"].to(device)
    bsz, k, seq_len = input_ids.shape
    flat_ids = input_ids.view(bsz * k, seq_len)
    flat_mask = attention_mask.view(bsz * k, seq_len)
    with torch.autocast(device_type=device, dtype=autocast_dtype, enabled=autocast_dtype != torch.float32):
        out = model(input_ids=flat_ids, attention_mask=flat_mask)
        logits = out.logits  # (B*k, 1)
    scores = logits.view(bsz, k).float()
    return scores, labels


def _evaluate_ndcg(model, dataloader, *, device, autocast_dtype, k: int = 5) -> float:
    """Mean NDCG@k over the val set."""
    import torch

    model.eval()
    scores_acc: list[list[float]] = []
    labels_acc: list[list[int]] = []
    with torch.no_grad():
        for batch in dataloader:
            scores, labels = _forward_listwise(
                model, batch, device=device, autocast_dtype=autocast_dtype
            )
            for row_scores, row_labels in zip(scores.cpu().tolist(), labels.cpu().tolist(), strict=True):
                scores_acc.append(row_scores)
                labels_acc.append([int(l) for l in row_labels])
    if not scores_acc:
        return 0.0
    total = 0.0
    for row_scores, row_labels in zip(scores_acc, labels_acc, strict=True):
        total += ndcg_at_k(row_scores, row_labels, k)
    return total / len(scores_acc)


def _save_checkpoint(model, tokenizer, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(path)
    tokenizer.save_pretrained(path)


def train(
    *,
    train_examples: Sequence["ListwiseExample"],
    val_examples: Sequence["ListwiseExample"],
    config: TrainConfig,
    train_cutoff: TemporalCutoff,
    run_id: str,
) -> TrainResult:
    """Fine-tune the cross-encoder. Returns a `TrainResult` with the best run.

    Pre-flight:
      1. `validate_training_examples` checks every source against `train_cutoff`.
      2. We require >= 1 train and val example each — otherwise the run can't
         possibly report a metric.
    """
    if not train_examples:
        raise ValueError("train_examples is empty")
    if not val_examples:
        raise ValueError("val_examples is empty")

    validate_training_examples(train_examples, train_cutoff)

    import torch
    from torch.utils.data import DataLoader
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    from chaoswing.ml.data import ListwiseDataset

    torch.manual_seed(config.seed)
    device = _resolve_device(config.device)
    autocast_dtype = _pick_dtype(config.precision, device)

    logger.info(
        "train: backbone=%s loss=%s device=%s dtype=%s epochs=%d batch=%d",
        config.backbone, config.loss, device, autocast_dtype, config.epochs, config.batch_size,
    )

    tokenizer = AutoTokenizer.from_pretrained(config.backbone)
    model = AutoModelForSequenceClassification.from_pretrained(
        config.backbone, num_labels=1
    ).to(device)

    train_ds = ListwiseDataset(train_examples, tokenizer, max_length=config.max_length)
    val_ds = ListwiseDataset(val_examples, tokenizer, max_length=config.max_length)
    train_loader = DataLoader(train_ds, batch_size=config.batch_size, shuffle=True, drop_last=False)
    val_loader = DataLoader(val_ds, batch_size=config.batch_size, shuffle=False, drop_last=False)

    steps_per_epoch = max(1, math.ceil(len(train_ds) / config.batch_size))
    total_steps = steps_per_epoch * config.epochs
    optimizer, scheduler = _build_optimizer_and_scheduler(
        model, config=config, total_steps=total_steps
    )
    loss_fn = _loss_fn(config.loss)

    checkpoint_path = config.checkpoint_dir / run_id
    history: list[dict] = []
    best_metric = -math.inf
    best_epoch = -1
    last_improvement_epoch = -1

    for epoch in range(config.epochs):
        model.train()
        running_loss = 0.0
        step = 0
        optimizer.zero_grad(set_to_none=True)
        for batch_idx, batch in enumerate(train_loader):
            scores, labels = _forward_listwise(
                model, batch, device=device, autocast_dtype=autocast_dtype
            )
            loss = loss_fn(scores, labels) / config.grad_accum_steps
            loss.backward()
            running_loss += float(loss.detach()) * config.grad_accum_steps
            step += 1
            if (batch_idx + 1) % config.grad_accum_steps == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
        mean_loss = running_loss / max(step, 1)
        val_ndcg = _evaluate_ndcg(
            model, val_loader, device=device, autocast_dtype=autocast_dtype, k=5
        )
        history.append({"epoch": epoch, "train_loss": mean_loss, "val_ndcg_at_5": val_ndcg})
        logger.info(
            "epoch %d: train_loss=%.4f val_ndcg_at_5=%.4f", epoch, mean_loss, val_ndcg
        )

        if val_ndcg > best_metric:
            best_metric = val_ndcg
            best_epoch = epoch
            last_improvement_epoch = epoch
            _save_checkpoint(model, tokenizer, checkpoint_path)
        elif epoch - last_improvement_epoch >= config.early_stop_patience:
            logger.info(
                "early stop at epoch %d (no improvement since epoch %d)",
                epoch, last_improvement_epoch,
            )
            break

    return TrainResult(
        best_metric=best_metric if best_metric > -math.inf else 0.0,
        best_epoch=best_epoch,
        checkpoint_path=checkpoint_path,
        history=history,
    )
