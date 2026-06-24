# -*- coding: utf-8 -*-
"""
Krea2 Text-Fusion Rebalance (Projector).

Krea2's DiT fuses the 12 tapped Qwen3-VL layers with a learned Linear(12->1)
`txtfusion.projector` (weight shape [1,12]). This node adds a per-layer diff to
those 12 coefficients as a reversible model patch -- the same lever as the catbox
"uncensor" LoRA, but tunable via `strength` and with no file to load.

The NSFW signal lives in the specific directional offsets (e.g. large negative on
layer 8); uniform weights do nothing. Keep `strength` small (the default values are
large) or it overflows to a black image.

Prints the current projector weights so you can see what you're tuning.
"""

import torch
import comfy.utils

PROJECTOR_KEY = "diffusion_model.txtfusion.projector.weight"  # Linear(12->1) -> [1,12]
N_LAYERS = 12


class AzKrea2ProjectorRebalance:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "weights": ("STRING", {
                    "default": "-24.195,-32.266,92.695,125.977,176.379,98.633,99.555,-359.75,-127.92,-190.32,-152.17,28.199",
                    "multiline": False,
                    "tooltip": "12 comma-separated per-layer diffs (layer 0..11). "
                               "Defaults = the catbox LoRA's projector offsets.",
                }),
                "strength": ("FLOAT", {
                    "default": 0.05, "min": -10.0, "max": 10.0, "step": 0.01,
                    "tooltip": "Scales the whole patch. With the large default values keep "
                               "this small (~0.05); raise until output destabilizes.",
                }),
            },
        }

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "apply"
    CATEGORY = "AZ_Nodes"
    DESCRIPTION = "Add a tunable per-layer diff to Krea2's text-fusion projector (LoRA-style, file-free)."

    def apply(self, model, weights, strength):
        vals = [v for v in (w.strip() for w in weights.split(",")) if v != ""]
        if len(vals) != N_LAYERS:
            raise ValueError(f"'weights' must have {N_LAYERS} comma-separated numbers, got {len(vals)}")
        try:
            gains = torch.tensor([float(v) for v in vals], dtype=torch.float32).reshape(1, N_LAYERS)
        except ValueError as e:
            raise ValueError(f"non-numeric value in 'weights': {e}")

        try:
            orig = comfy.utils.get_attr(model.model, PROJECTOR_KEY)
        except Exception:
            raise RuntimeError(
                f"'{PROJECTOR_KEY}' not found on this model. Is it a Krea2 diffusion model?")
        if tuple(orig.shape) != (1, N_LAYERS):
            raise RuntimeError(f"projector weight shape {tuple(orig.shape)} != (1, {N_LAYERS})")

        print("[AzKrea2ProjectorRebalance] current projector weights:",
              [round(x, 4) for x in orig.detach().float().cpu().flatten().tolist()])

        m = model.clone()
        m.add_patches({PROJECTOR_KEY: ("diff", (gains,))}, strength)
        return (m,)


NODE_CLASS_MAPPINGS = {"AzKrea2ProjectorRebalance": AzKrea2ProjectorRebalance}
NODE_DISPLAY_NAME_MAPPINGS = {"AzKrea2ProjectorRebalance": "Krea2 Text-Fusion Rebalance (Projector)"}
