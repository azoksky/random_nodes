# -*- coding: utf-8 -*-
"""
Timestep-gated LoRA loader (model + clip), Krea2-style crossover/overlap.

A normal LoRA bakes its weights for the whole schedule. This loader instead
attaches the model-side LoRA as a ComfyUI weight *hook* whose strength is
keyframed by sampling percent, so the LoRA is only active during the early
(0 -> crossover) or late (crossover -> 1) part of the schedule, with an
optional `overlap` band that linearly ramps the seam instead of hard-switching.

Drop it where a normal LoraLoader goes: MODEL/CLIP in, MODEL/CLIP out, then
feed the CLIP into CLIP Text Encode. The hook rides the conditioning that the
returned CLIP produces; the sampler registers the gated model patch from it
per timestep (sampler_helpers.prepare_sampling). CLIP-side LoRA cannot be
timestep-gated (text encode is one-shot) so `strength_clip` applies constantly.
"""

import folder_paths
import comfy.utils
import comfy.hooks
from comfy_api.latest import io

_RAMP_STEPS = 8


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _build_keyframes(crossover, overlap, apply_during):
    cross = _clamp(crossover, 0.0, 1.0)
    ov = _clamp(overlap, 0.0, 0.5)
    lo = max(0.0, cross - ov)
    hi = min(1.0, cross + ov)
    early = apply_during == "early"
    kf = comfy.hooks.HookKeyframeGroup()

    if ov <= 1e-6:
        kf.add(comfy.hooks.HookKeyframe(
            strength=1.0 if early else 0.0, start_percent=0.0, guarantee_steps=1))
        kf.add(comfy.hooks.HookKeyframe(
            strength=0.0 if early else 1.0, start_percent=cross))
        return kf

    if lo > 0.0:
        kf.add(comfy.hooks.HookKeyframe(
            strength=1.0 if early else 0.0, start_percent=0.0, guarantee_steps=1))
    for i in range(_RAMP_STEPS + 1):
        frac = i / _RAMP_STEPS
        p = lo + (hi - lo) * frac
        s = (1.0 - frac) if early else frac
        kf.add(comfy.hooks.HookKeyframe(
            strength=round(s, 4),
            start_percent=round(min(0.999, p), 4),
            guarantee_steps=1 if (i == 0 and lo <= 0.0) else 0))
    return kf


class AzGatedLoraLoader(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzGatedLoraLoader",
            display_name="Gated LoRA Loader (timestep)",
            category="AZ_Nodes",
            description="Load a LoRA whose MODEL side is active only in the early or late "
                        "part of the schedule (Krea2-style crossover + overlap). CLIP side "
                        "applies constantly. Feed CLIP into CLIP Text Encode.",
            inputs=[
                io.Model.Input("model", tooltip="Passed through unchanged; the gated patch is "
                                                "registered from the conditioning at sample time."),
                io.Clip.Input("clip", tooltip="Returned CLIP carries the hook; encode your prompt with it."),
                io.Combo.Input("lora_name", options=folder_paths.get_filename_list("loras"),
                               tooltip="LoRA file."),
                io.Float.Input("strength_model", default=1.0, min=-20.0, max=20.0, step=0.01,
                               tooltip="LoRA strength on the UNet (the gated part)."),
                io.Float.Input("strength_clip", default=1.0, min=-20.0, max=20.0, step=0.01,
                               tooltip="LoRA strength on CLIP (constant, not gated)."),
                io.Combo.Input("apply_during", options=["early", "late"], default="early",
                               tooltip="early = active 0..crossover (locks composition); "
                                       "late = active crossover..1 (refines detail)."),
                io.Float.Input("crossover", default=0.5, min=0.0, max=1.0, step=0.01,
                               tooltip="Switch point between active and inactive."),
                io.Float.Input("overlap", default=0.0, min=0.0, max=0.5, step=0.01, optional=True,
                               tooltip="Soft seam: linearly ramp strength across +/- this around "
                                       "crossover. 0 = hard switch."),
            ],
            outputs=[
                io.Model.Output(display_name="model"),
                io.Clip.Output(display_name="clip"),
            ],
        )

    @classmethod
    def execute(cls, model, clip, lora_name, strength_model, strength_clip,
                apply_during, crossover, overlap=0.0):
        if strength_model == 0.0 and strength_clip == 0.0:
            return io.NodeOutput(model, clip)

        lora_path = folder_paths.get_full_path("loras", lora_name)
        if lora_path is None:
            raise FileNotFoundError(f"LoRA not found: {lora_name}")
        lora = comfy.utils.load_torch_file(lora_path, safe_load=True)

        hooks = comfy.hooks.create_hook_lora(
            lora=lora, strength_model=strength_model, strength_clip=strength_clip)
        hooks.set_keyframes_on_hooks(_build_keyframes(crossover, overlap, apply_during))

        clip = clip.clone(disable_dynamic=True)
        clip.apply_hooks_to_conds = hooks
        clip.patcher.forced_hooks = hooks.clone()
        clip.use_clip_schedule = False
        clip.patcher.forced_hooks.set_keyframes_on_hooks(None)
        clip.patcher.register_all_hook_patches(
            hooks, comfy.hooks.create_target_dict(comfy.hooks.EnumWeightTarget.Clip))

        return io.NodeOutput(model, clip)
