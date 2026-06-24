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

N_LAYERS = 12


def _scale_conditioning(conditioning, gains, multiplier):
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
        out.append([x, d.copy()])
    return out


class AzKrea2GatedRebalance:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "conditioning": ("CONDITIONING", {"tooltip": "Clean prompt conditioning (CLIP Text Encode, krea2)."}),
                "per_layer_weights": ("STRING", {
                    "default": "1,1,1,1,1,1,1,1,2.5,5,1,4",
                    "multiline": False,
                    "tooltip": "12 per-layer multipliers (layer 0..11). 1=unchanged. "
                               "Boost the NSFW-carrying layers (~8/9/11).",
                }),
                "multiplier": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 10.0, "step": 0.05,
                    "tooltip": "Global scale on the rebalanced (early) cond.",
                }),
                "crossover": ("FLOAT", {
                    "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "Switch point. Higher = hold NSFW cond longer (stronger content); "
                               "lower = more clean steps (less plasticky).",
                }),
            },
            "optional": {
                "overlap": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 0.5, "step": 0.01,
                    "tooltip": "Soften the seam: both conds active +/- this around crossover. 0 = hard switch.",
                }),
            },
        }

    RETURN_TYPES = ("CONDITIONING",)
    RETURN_NAMES = ("conditioning",)
    FUNCTION = "apply"
    CATEGORY = "AZ_Nodes"
    DESCRIPTION = "All-in-one: rebalance the 12-layer cond, gate it to early steps, clean cond late (anti-plasticky)."

    def apply(self, conditioning, per_layer_weights, multiplier, crossover, overlap=0.0):
        vals = [v for v in (w.strip() for w in per_layer_weights.split(",")) if v != ""]
        if len(vals) != N_LAYERS:
            raise ValueError(f"'per_layer_weights' must have {N_LAYERS} numbers, got {len(vals)}")
        try:
            gains = torch.tensor([float(v) for v in vals], dtype=torch.float32)
        except ValueError as e:
            raise ValueError(f"non-numeric value in 'per_layer_weights': {e}")

        rebalanced = _scale_conditioning(conditioning, gains, multiplier)

        early_end = min(1.0, crossover + overlap)
        late_start = max(0.0, crossover - overlap)
        early = node_helpers.conditioning_set_values(
            rebalanced, {"start_percent": 0.0, "end_percent": early_end})
        late = node_helpers.conditioning_set_values(
            conditioning, {"start_percent": late_start, "end_percent": 1.0})
        return (early + late,)


NODE_CLASS_MAPPINGS = {"AzKrea2GatedRebalance": AzKrea2GatedRebalance}
NODE_DISPLAY_NAME_MAPPINGS = {"AzKrea2GatedRebalance": "Krea2 Gated Rebalance (all-in-one)"}
