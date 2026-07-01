# -*- coding: utf-8 -*-
"""
Krea2 Gated Rebalance (all-in-one).

One node, one conditioning input, no chaining. Internally it:
  1. builds a rebalanced copy of your conditioning by scaling each of the 12 tapped
     Qwen3-VL layers (the 12*2560 stack) per `per_layer_weights` -- this surfaces the
     NSFW signal but on its own looks plasticky;
  2. feeds that rebalanced cond to the EARLY steps only (locks composition/content),
     and your ORIGINAL clean cond to the LATE steps (faithful, non-plasticky detail).
Because early structure is irreversible in diffusion, the clean late steps refine
texture without re-clothing the subject.

Feed clean `CLIP Text Encode` -> this node -> KSampler positive. That's it.
"""

import torch
import node_helpers
from comfy_api.latest import io

N_LAYERS = 12


def _scale_conditioning(conditioning, gains, multiplier, clamp=0.0):
    g = gains.reshape(N_LAYERS)
    out = []
    for t, d in conditioning:
        if t.shape[-1] % N_LAYERS != 0:
            raise ValueError(
                f"conditioning last dim {t.shape[-1]} not divisible by {N_LAYERS} "
                "-- is this a Krea2 (CLIPLoader type=krea2) conditioning?")
        layer_dim = t.shape[-1] // N_LAYERS
        x = t.view(*t.shape[:-1], N_LAYERS, layer_dim)
        gv = g.view(*([1] * (x.dim() - 2)), N_LAYERS, 1).to(dtype=x.dtype, device=x.device)
        x = (x * gv * multiplier).reshape(t.shape)
        # Vision tokens (Krea2 Style Reference) carry much larger norms than text; a big
        # per-layer boost can push them to inf -> NaN -> black image. Clamp guards that.
        if clamp and clamp > 0:
            x = torch.clamp(x, -clamp, clamp)
        out.append([x, d.copy()])
    return out


class AzKrea2GatedRebalance(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzKrea2GatedRebalance",
            display_name="Krea2 Gated Rebalance",
            category="AZ_Nodes",
            description="All-in-one: rebalance the 12-layer cond, gate it to early steps, clean cond late (anti-plasticky).",
            inputs=[
                io.Conditioning.Input("conditioning", tooltip="Clean prompt conditioning (CLIP Text Encode, krea2)."),
                io.String.Input(
                    "per_layer_weights",
                    default="1,1,1,1,1,1,1,1,2.5,5,1,4",
                    multiline=False,
                    tooltip="12 per-layer multipliers (layer 0..11). 1=unchanged. "
                            "Boost the NSFW-carrying layers (~8/9/11).",
                ),
                io.Float.Input(
                    "multiplier", default=1.0, min=0.0, max=10.0, step=0.05,
                    tooltip="Global scale on the rebalanced (early) cond.",
                ),
                io.Float.Input(
                    "crossover", default=0.5, min=0.0, max=1.0, step=0.01,
                    tooltip="Switch point. Higher = hold NSFW cond longer (stronger content); "
                            "lower = more clean steps (less plasticky).",
                ),
                io.Float.Input(
                    "overlap", default=0.0, min=0.0, max=0.5, step=0.01, optional=True,
                    tooltip="Soften the seam: both conds active +/- this around crossover. 0 = hard switch.",
                ),
                io.Float.Input(
                    "clamp", default=0.0, min=0.0, max=1000.0, step=1.0, optional=True,
                    tooltip="Clamp |rebalanced values| to this. 0 = off. Set ~30-60 when the input "
                            "comes from Krea2 Style Reference: image tokens have large norms and the "
                            "per-layer boost can overflow to NaN (black image).",
                ),
            ],
            outputs=[
                io.Conditioning.Output(display_name="conditioning"),
            ],
        )

    @classmethod
    def execute(cls, conditioning, per_layer_weights, multiplier, crossover, overlap=0.0, clamp=0.0):
        vals = [v for v in (w.strip() for w in per_layer_weights.split(",")) if v != ""]
        if len(vals) != N_LAYERS:
            raise ValueError(f"'per_layer_weights' must have {N_LAYERS} numbers, got {len(vals)}")
        try:
            gains = torch.tensor([float(v) for v in vals], dtype=torch.float32)
        except ValueError as e:
            raise ValueError(f"non-numeric value in 'per_layer_weights': {e}")

        rebalanced = _scale_conditioning(conditioning, gains, multiplier, clamp)

        early_end = min(1.0, crossover + overlap)
        late_start = max(0.0, crossover - overlap)
        early = node_helpers.conditioning_set_values(
            rebalanced, {"start_percent": 0.0, "end_percent": early_end})
        late = node_helpers.conditioning_set_values(
            conditioning, {"start_percent": late_start, "end_percent": 1.0})
        return io.NodeOutput(early + late)
