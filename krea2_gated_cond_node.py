# -*- coding: utf-8 -*-
"""
Krea2 Timestep-Gated Conditioning.

Diffusion is coarse-to-fine: early (high-noise) steps lock composition/geometry
(clothed-vs-nude) irreversibly; late (low-noise) steps render texture. So feed the
rebalanced/"uncensor" conditioning ONLY to the early steps to bake in the content,
then hand the clean conditioning to the late steps for faithful, non-plasticky detail
-- the frozen early structure means the clean cond can't re-clothe the subject.

This is timestep-gated prompt scheduling done in one node: it sets the timestep range
on each conditioning and combines them (same primitives as ConditioningSetTimestepRange
+ ConditioningCombine).

    cond_early  -> active [0, crossover]            (rebalanced / NSFW)
    cond_late   -> active [crossover-overlap?, 1]    (clean prompt)
"""

import node_helpers


class AzKrea2GatedConditioning:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "cond_early": ("CONDITIONING", {"tooltip": "Rebalanced/NSFW conditioning. Drives early (structure) steps."}),
                "cond_late": ("CONDITIONING", {"tooltip": "Clean prompt conditioning. Drives late (detail) steps."}),
                "crossover": ("FLOAT", {
                    "default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01,
                    "tooltip": "Switch point (fraction of steps). Higher = hold NSFW cond longer "
                               "(stronger content); lower = more clean steps (less plasticky).",
                }),
            },
            "optional": {
                "overlap": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 0.5, "step": 0.01,
                    "tooltip": "Soften the seam: both conds stay active +/- this much around the "
                               "crossover. 0 = hard switch.",
                }),
            },
        }

    RETURN_TYPES = ("CONDITIONING",)
    RETURN_NAMES = ("conditioning",)
    FUNCTION = "apply"
    CATEGORY = "AZ_Nodes"
    DESCRIPTION = "Gate rebalanced cond to early steps + clean cond to late steps, combined (anti-plasticky)."

    def apply(self, cond_early, cond_late, crossover, overlap=0.0):
        early_end = min(1.0, crossover + overlap)
        late_start = max(0.0, crossover - overlap)
        early = node_helpers.conditioning_set_values(
            cond_early, {"start_percent": 0.0, "end_percent": early_end})
        late = node_helpers.conditioning_set_values(
            cond_late, {"start_percent": late_start, "end_percent": 1.0})
        return (early + late,)


NODE_CLASS_MAPPINGS = {"AzKrea2GatedConditioning": AzKrea2GatedConditioning}
NODE_DISPLAY_NAME_MAPPINGS = {"AzKrea2GatedConditioning": "Krea2 Timestep-Gated Conditioning"}
