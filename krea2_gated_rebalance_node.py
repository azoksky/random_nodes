# -*- coding: utf-8 -*-
"""
Krea2 Gated Rebalance (filter knobs + step gating, all-in-one).

Krea 2's safety filter lives in a few of the 12 tapped Qwen3-VL projector "knobs":
knob 9 and knob 10 are the primary refusal, knob 11 is a secondary refusal that
also stiffens human rendering. Knobs 1-8 and 12 are style/anatomy priors -- touching
them is what warps faces/skin. So this node touches ONLY knobs 9/10/11 and leaves
every other knob at its natural value.

It then gates: the rebalanced cond drives the EARLY steps (locks composition), and
your ORIGINAL clean cond drives the LATE steps (faithful, non-plasticky detail).
Because early structure is irreversible in diffusion, the clean late steps refine
texture without re-censoring the subject.

A knob set to 0 is IGNORED (that layer is left untouched), matching the LoRA
convention where 0 means "no change". The knob value is `1 + d` for a per-knob
delta d; `multiplier` is the LoRA strength, applied around the untouched pivot 1.0:
  effective = 1 + multiplier * (knob - 1)
So the knob holds the s=1 value and you dial strength with `multiplier` (no hand math).
FB2 (knobs 9/10 only): d9 = -0.5117 (knob 0.4883), d10 = -0.8906 (knob 0.1094)
  strength 1 -> 0.49 / 0.11    3 -> -0.54 / -1.67    5 -> -1.56 / -3.45

Feed clean `CLIP Text Encode` -> this node -> KSampler positive.
"""

import torch
import node_helpers
from comfy_api.latest import io

N_LAYERS = 12

# 1-indexed knob -> 0-indexed layer in the 12*2560 stack.
_KNOB_LAYER = {9: 8, 10: 9, 11: 10}


def _apply_knobs(conditioning, knob_map, clamp=0.0):
    out = []
    for t, d in conditioning:
        if t.shape[-1] % N_LAYERS != 0:
            raise ValueError(
                f"conditioning last dim {t.shape[-1]} not divisible by {N_LAYERS} "
                "-- is this a Krea2 (CLIPLoader type=krea2) conditioning?")
        layer_dim = t.shape[-1] // N_LAYERS
        orig = t.dtype
        x = t.float().view(*t.shape[:-1], N_LAYERS, layer_dim)
        for idx, m in knob_map.items():
            x[..., idx, :] = x[..., idx, :] * m
        # Vision tokens (Krea2 Style Reference) carry much larger norms than text; a big
        # knob value can push them to inf -> NaN -> black image. Clamp guards that.
        if clamp and clamp > 0:
            x = torch.clamp(x, -clamp, clamp)
        out.append([x.reshape(t.shape).to(orig), d.copy()])
    return out


class AzKrea2GatedRebalance(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzKrea2GatedRebalance",
            display_name="Krea2 Gated Rebalance",
            category="AZ_Nodes",
            description="Multiply only the refusal knobs (9/10 primary, 11 secondary), gate the "
                        "rebalanced cond to early steps and the clean cond to late steps. Style/anatomy "
                        "priors are never touched (no plasticky drift). 0 = leave that knob untouched.",
            inputs=[
                io.Conditioning.Input("conditioning", tooltip="Clean prompt conditioning (CLIP Text Encode, krea2)."),
                io.Float.Input(
                    "knob9", default=0.4883, min=-100.0, max=100.0, step=0.0001,
                    tooltip="Primary refusal knob 9. 0 = untouched. LoRA map: 1 + strength*(-0.5117).",
                ),
                io.Float.Input(
                    "knob10", default=0.1094, min=-100.0, max=100.0, step=0.0001,
                    tooltip="Primary refusal knob 10 (stronger). 0 = untouched. LoRA map: 1 + strength*(-0.8906).",
                ),
                io.Float.Input(
                    "knob11", default=0.0, min=-100.0, max=100.0, step=0.0001,
                    tooltip="Secondary refusal knob 11. Leave 0; only set it for prompts that still refuse "
                            "on 9/10 alone. Non-zero values also stiffen human rendering.",
                ),
                io.Float.Input(
                    "multiplier", default=1.0, min=-100.0, max=100.0, step=0.05,
                    tooltip="LoRA strength: scales each engaged knob's distance from 1.0 "
                            "(effective = 1 + multiplier*(knob-1)). 1 = knob as-is; try 3-5 for full "
                            "bypass. Untouched (0) knobs stay untouched.",
                ),
                io.Float.Input(
                    "crossover", default=0.5, min=0.0, max=1.0, step=0.01,
                    tooltip="Switch point. Higher = hold the rebalanced cond longer (stronger content); "
                            "lower = more clean steps (less plasticky).",
                ),
                io.Float.Input(
                    "overlap", default=0.0, min=0.0, max=0.5, step=0.01, optional=True,
                    tooltip="Soften the seam: both conds active +/- this around crossover. 0 = hard switch.",
                ),
                io.Float.Input(
                    "clamp", default=0.0, min=0.0, max=1000.0, step=1.0, optional=True,
                    tooltip="Clamp |rebalanced values| to this. 0 = off. Set ~30-60 when the input "
                            "comes from Krea2 Style Reference: image tokens have large norms and a big "
                            "knob value can overflow to NaN (black image).",
                ),
            ],
            outputs=[
                io.Conditioning.Output(display_name="conditioning"),
            ],
        )

    @classmethod
    def execute(cls, conditioning, knob9=0.4883, knob10=0.1094, knob11=0.0,
                multiplier=1.0, crossover=0.5, overlap=0.0, clamp=0.0):
        knob_map = {}
        for knob, val in ((9, knob9), (10, knob10), (11, knob11)):
            if val != 0.0:
                knob_map[_KNOB_LAYER[knob]] = 1.0 + multiplier * (val - 1.0)

        # No knobs engaged -> nothing to gate, pass the clean cond straight through.
        if not knob_map:
            return io.NodeOutput(conditioning)

        rebalanced = _apply_knobs(conditioning, knob_map, clamp)

        early_end = min(1.0, crossover + overlap)
        late_start = max(0.0, crossover - overlap)
        early = node_helpers.conditioning_set_values(
            rebalanced, {"start_percent": 0.0, "end_percent": early_end})
        late = node_helpers.conditioning_set_values(
            conditioning, {"start_percent": late_start, "end_percent": 1.0})
        return io.NodeOutput(early + late)
