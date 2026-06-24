# -*- coding: utf-8 -*-
"""
Krea2 Text-Fusion Rebalance (Projector).

Krea2's DiT fuses the 12 tapped Qwen3-VL layers with a learned Linear(12->1)
`txtfusion.projector` (weight shape [1,12]), then refines + RMSNorm-normalizes the
result. Rebalancing HERE (vs scaling raw conditioning) keeps magnitude in check, so
it avoids the plasticky/low-detail look of conditioning-level scaling and the
NaN/black collapse of dumping huge additive diffs onto the projector.

multiply : scale each layer's existing learned weight (1.0 = no change)  <- safe default
add      : add to the 12 coefficients (LoRA-style; use small values)

Applied as a reversible model patch; prints the current projector weights so you can
see what you're tuning.
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
                "mode": (["multiply", "add"], {"default": "multiply"}),
                "weights": ("STRING", {
                    "default": "1,1,1,1,1,1,1,1,1,1,1,1",
                    "multiline": False,
                    "tooltip": "12 comma-separated per-layer values (layer 0..11).",
                }),
                "strength": ("FLOAT", {
                    "default": 1.0, "min": -10.0, "max": 10.0, "step": 0.05,
                    "tooltip": "Scales the whole patch. Lower if output destabilizes.",
                }),
            },
        }

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "apply"
    CATEGORY = "AZ_Nodes"
    DESCRIPTION = "Rebalance Krea2's 12-layer text-fusion at the projector (magnitude-safe)."

    def apply(self, model, mode, weights, strength):
        vals = [v for v in (w.strip() for w in weights.split(",")) if v != ""]
        if len(vals) != N_LAYERS:
            raise ValueError(f"'weights' must have {N_LAYERS} comma-separated numbers, got {len(vals)}")
        try:
            gains = torch.tensor([float(v) for v in vals], dtype=torch.float32)
        except ValueError as e:
            raise ValueError(f"non-numeric value in 'weights': {e}")

        try:
            orig = comfy.utils.get_attr(model.model, PROJECTOR_KEY)
        except Exception:
            raise RuntimeError(
                f"'{PROJECTOR_KEY}' not found on this model. Is it a Krea2 diffusion model?")
        if tuple(orig.shape) != (1, N_LAYERS):
            raise RuntimeError(f"projector weight shape {tuple(orig.shape)} != (1, {N_LAYERS})")

        orig_f = orig.detach().float().cpu().flatten()
        print("[AzKrea2ProjectorRebalance] current projector weights:",
              [round(x, 4) for x in orig_f.tolist()])

        g = gains.reshape(1, N_LAYERS)
        if mode == "multiply":
            diff = (g - 1.0) * orig.detach().float().cpu()   # weight + s*(g-1)*orig == s-blend toward g*orig
        else:
            diff = g                                         # weight + s*g

        m = model.clone()
        m.add_patches({PROJECTOR_KEY: ("diff", (diff,))}, strength)
        return (m,)


NODE_CLASS_MAPPINGS = {"AzKrea2ProjectorRebalance": AzKrea2ProjectorRebalance}
NODE_DISPLAY_NAME_MAPPINGS = {"AzKrea2ProjectorRebalance": "Krea2 Text-Fusion Rebalance (Projector)"}
