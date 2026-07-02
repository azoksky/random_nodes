# -*- coding: utf-8 -*-
"""
Krea2 Filter Knobs (9/10/11).

Krea 2's safety filter lives in just a few of the 12 tapped Qwen3-VL projector
"knobs": knob 9 and knob 10 are the primary refusal, knob 11 is a secondary
refusal that also stiffens human rendering. Knobs 1-8 and 12 are style/anatomy
priors -- touching them is what warps faces/skin at high strength.

This node exposes only knobs 9/10/11 as direct per-layer multipliers and leaves
every other knob at its natural value. A knob set to 0 is IGNORED (that layer is
left untouched), matching the LoRA convention where 0 means "no change" -- so you
can never accidentally delete a layer. Set knob 11 to 0 normally; only give it a
value for the unusual prompts that still refuse on knobs 9/10 alone.

Translating a community LoRA's per-knob delta d into this node: value = 1 + s*d,
where s is the LoRA strength you'd have used. FB2 (knobs 9/10 only):
  d9 = -0.5117, d10 = -0.8906
  s=1 -> 0.49 / 0.11      s=3 -> -0.54 / -1.67      s=5 -> -1.56 / -3.45
"""

import torch
from comfy_api.latest import io

N_LAYERS = 12

# 1-indexed knob -> 0-indexed layer in the 12*2560 stack.
_KNOB_LAYER = {9: 8, 10: 9, 11: 10}


def _apply_knobs(conditioning, knob_map):
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
        out.append([x.reshape(t.shape).to(orig), d.copy()])
    return out


class AzKrea2FilterKnobs(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzKrea2FilterKnobs",
            display_name="Krea2 Filter Knobs (9/10/11)",
            category="AZ_Nodes",
            description="Multiply only the Krea 2 refusal knobs (9/10 primary, 11 secondary). "
                        "0 = leave that knob untouched. Style/anatomy priors are never touched, "
                        "so no plasticky/warped drift. Feed clean krea2 conditioning in.",
            inputs=[
                io.Conditioning.Input("conditioning", tooltip="Clean krea2 conditioning (CLIP Text Encode)."),
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
                    tooltip="Secondary refusal knob 11. Leave 0 (untouched); only set it for prompts that "
                            "still refuse on 9/10 alone. Non-zero values also stiffen human rendering.",
                ),
            ],
            outputs=[
                io.Conditioning.Output(display_name="conditioning"),
            ],
        )

    @classmethod
    def execute(cls, conditioning, knob9=0.4883, knob10=0.1094, knob11=0.0):
        knob_map = {}
        for knob, val in ((9, knob9), (10, knob10), (11, knob11)):
            if val != 0.0:
                knob_map[_KNOB_LAYER[knob]] = val
        if not knob_map:
            return io.NodeOutput(conditioning)
        return io.NodeOutput(_apply_knobs(conditioning, knob_map))
