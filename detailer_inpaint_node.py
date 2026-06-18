# -*- coding: utf-8 -*-
"""
Inpaint (Crop & Stitch).

1. take image + mask
2. crop a rectangle around the mask, with adjustable padding for context
3. upscale that crop (and its mask) to a comfortable size
4. optionally route the model through a Fun inpaint controlnet patch
5. sample (with live previews); controlnet influence set by cn_strength
6. lanczos-resize the result back to the crop's original size
7. feather/blur the mask and overlay the inpaint on the original image
8. output the stitched image (+ the blend mask)

The sampling noise mask is SOLID (binary) so the masked area is fully
regenerated; feathering is used only for the final overlay.
"""

import torch
import torch.nn.functional as F

import comfy.utils
import comfy.sample
import comfy.samplers
import latent_preview

try:
    from comfy_extras.nodes_model_patch import ZImageFunControlnet as _FunControlNode
except Exception:
    _FunControlNode = None

_MAX_SIDE = 2048  # safety cap so a big crop * upscale can't OOM


def _gaussian_blur(mask, radius):
    # mask: (B,1,H,W); separable gaussian, reflect-padded
    if radius <= 0:
        return mask
    sigma = max(0.1, radius / 2.0)
    coords = torch.arange(radius * 2 + 1, dtype=torch.float32, device=mask.device) - radius
    g = torch.exp(-(coords ** 2) / (2 * sigma * sigma))
    g = (g / g.sum()).to(mask.dtype)
    kx = g.view(1, 1, 1, -1)
    ky = g.view(1, 1, -1, 1)
    m = F.pad(mask, (radius, radius, radius, radius), mode="reflect")
    m = F.conv2d(m, kx)
    m = F.conv2d(m, ky)
    return m


def _resize_bhwc(t, w, h, method):
    return comfy.utils.common_upscale(
        t.movedim(-1, 1), w, h, method, "disabled"
    ).movedim(1, -1)


def _color_match(refined, original, keep):
    # Shift the refined crop's per-channel mean/std to the original's, measured
    # over the kept (unmasked) surroundings, so the inpaint tone matches.
    # refined/original: (H,W,C); keep: (H,W) weight in [0,1].
    w = keep
    wsum = w.sum()
    if wsum < 16:
        return refined
    eps = 1e-5
    out = refined.clone()
    for c in range(refined.shape[-1]):
        o, i = original[..., c], refined[..., c]
        o_mean = (o * w).sum() / wsum
        i_mean = (i * w).sum() / wsum
        o_std = torch.sqrt(((o - o_mean) ** 2 * w).sum() / wsum + eps)
        i_std = torch.sqrt(((i - i_mean) ** 2 * w).sum() / wsum + eps)
        out[..., c] = (i - i_mean) / i_std * o_std + o_mean
    return out.clamp(0, 1)


def _ksample(model, seed, steps, cfg, sampler_name, scheduler,
             positive, negative, latent, denoise):
    latent_image = latent["samples"]
    if hasattr(comfy.sample, "fix_empty_latent_channels"):
        latent_image = comfy.sample.fix_empty_latent_channels(model, latent_image)
    noise = comfy.sample.prepare_noise(latent_image, seed, latent.get("batch_index"))
    callback = latent_preview.prepare_callback(model, steps)
    disable_pbar = not comfy.utils.PROGRESS_BAR_ENABLED
    samples = comfy.sample.sample(
        model, noise, steps, cfg, sampler_name, scheduler,
        positive, negative, latent_image, denoise=denoise,
        noise_mask=latent.get("noise_mask"), callback=callback,
        disable_pbar=disable_pbar, seed=seed,
    )
    out = latent.copy()
    out["samples"] = samples
    return out


def _patch_funcontrol(model, model_patch, vae, up_img, up_mask, strength):
    # up_img: (1,uh,uw,C); up_mask: (1,1,uh,uw) solid, 1 == inpaint area.
    # The controlnet node inverts/rounds the mask itself.
    return _FunControlNode().diffsynth_controlnet(
        model, model_patch, vae, image=None, strength=float(strength),
        inpaint_image=up_img[:, :, :, :3], mask=up_mask[:, 0],
    )[0]


class AzInpaintCropStitch:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "positive": ("CONDITIONING",),
                "negative": ("CONDITIONING",),
                "vae": ("VAE",),
                "image": ("IMAGE",),
                "mask": ("MASK",),
                "padding": ("INT", {"default": 32, "min": 0, "max": 1024, "step": 1}),
                "upscale": ("FLOAT", {"default": 2.0, "min": 1.0, "max": 8.0, "step": 0.1}),
                "mask_expand": ("INT", {"default": 4, "min": 0, "max": 256, "step": 1}),
                "blend": ("INT", {"default": 16, "min": 0, "max": 256, "step": 1}),
                "color_match": ("BOOLEAN", {"default": False}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "steps": ("INT", {"default": 20, "min": 1, "max": 1000}),
                "cfg": ("FLOAT", {"default": 7.0, "min": 0.0, "max": 100.0, "step": 0.1}),
                "sampler_name": (comfy.samplers.KSampler.SAMPLERS,),
                "scheduler": (comfy.samplers.KSampler.SCHEDULERS,),
                "denoise": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
            },
            "optional": {
                "model_patch": ("MODEL_PATCH",),
                "cn_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.05}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "run"
    CATEGORY = "AZ_Nodes"

    def _one(self, model, positive, negative, vae, img, msk, P, model_patch, cn_strength):
        (padding, upscale, mask_expand, blend, color_match,
         seed, steps, cfg, sampler_name, scheduler, denoise) = P
        H, W, C = img.shape
        device = img.device

        # (1-2) bbox of the mask + padding, clamped to the image
        ys, xs = torch.where(msk > 0.5)
        if ys.numel() == 0:
            return img, torch.zeros((H, W), dtype=img.dtype, device=device)
        x1 = max(0, int(xs.min()) - padding)
        y1 = max(0, int(ys.min()) - padding)
        x2 = min(W, int(xs.max()) + 1 + padding)
        y2 = min(H, int(ys.max()) + 1 + padding)
        cw, ch = x2 - x1, y2 - y1

        crop_img = img[y1:y2, x1:x2, :]                       # (ch,cw,C)
        crop_msk = (msk[y1:y2, x1:x2] > 0.5).to(img.dtype)    # (ch,cw) binary

        # grown binary mask at crop resolution
        mb = crop_msk.view(1, 1, ch, cw)
        if mask_expand > 0:
            mb = F.max_pool2d(mb, mask_expand * 2 + 1, stride=1, padding=mask_expand)
        mb = (mb > 0.5).to(img.dtype)

        # (3) upscale dims (cap longer side for safety), snap to /8
        uw, uh = cw * upscale, ch * upscale
        if max(uw, uh) > _MAX_SIDE:
            f = _MAX_SIDE / max(uw, uh)
            uw, uh = uw * f, uh * f
        uw = max(8, (int(round(uw)) // 8) * 8)
        uh = max(8, (int(round(uh)) // 8) * 8)

        up_img = _resize_bhwc(crop_img.unsqueeze(0), uw, uh, "lanczos").clamp(0, 1)
        # SOLID noise mask: nearest + round so the masked area is fully denoised
        up_mask = F.interpolate(mb, size=(uh, uw), mode="nearest")
        up_mask = (up_mask > 0.5).to(img.dtype)

        latent = {"samples": vae.encode(up_img[:, :, :, :3]), "noise_mask": up_mask}

        # (4) optional controlnet patch, built from THIS crop's image+mask
        sample_model = model
        if model_patch is not None and _FunControlNode is not None:
            sample_model = _patch_funcontrol(model, model_patch, vae,
                                             up_img, up_mask, cn_strength)

        # (5) sample
        latent = _ksample(sample_model, seed, steps, cfg, sampler_name, scheduler,
                          positive, negative, latent, denoise)

        # (6) decode + lanczos back to crop size
        refined = vae.decode(latent["samples"])[:, :, :, :C].to(device)
        refined_crop = _resize_bhwc(refined, cw, ch, "lanczos").clamp(0, 1)[0]

        if color_match:
            refined_crop = _color_match(refined_crop, crop_img, (1.0 - mb)[0, 0])

        # (7) feather the mask (solid interior, soft edge) and overlay
        sm = mb
        if blend > 0:
            blurred = _gaussian_blur(sm, blend)
            core = -F.max_pool2d(-sm, blend * 2 + 1, stride=1, padding=blend)
            sm = torch.maximum(blurred, core)
        sm = sm.clamp(0, 1)[0, 0].unsqueeze(-1)              # (ch,cw,1)

        out = img.clone()
        out[y1:y2, x1:x2, :] = refined_crop * sm + crop_img * (1 - sm)

        full = torch.zeros((H, W), dtype=img.dtype, device=device)
        full[y1:y2, x1:x2] = sm[:, :, 0]
        return out, full

    def run(self, model, positive, negative, vae, image, mask,
            padding, upscale, mask_expand, blend, color_match,
            seed, steps, cfg, sampler_name, scheduler, denoise,
            model_patch=None, cn_strength=1.0):
        B, H, W, C = image.shape

        m = mask
        if m.dim() == 2:
            m = m.unsqueeze(0)
        if m.shape[0] != B:
            m = m[:1].repeat(B, 1, 1)
        if m.shape[1] != H or m.shape[2] != W:
            m = F.interpolate(
                m.unsqueeze(1).to(torch.float32), size=(H, W),
                mode="bilinear", align_corners=False,
            ).squeeze(1)
        m = m.to(image.device)

        outs, masks = [], []
        for b in range(B):
            P = (padding, upscale, mask_expand, blend, color_match,
                 seed + b, steps, cfg, sampler_name, scheduler, denoise)
            o, mm = self._one(model, positive, negative, vae,
                              image[b], m[b], P, model_patch, cn_strength)
            outs.append(o)
            masks.append(mm)
        return (torch.stack(outs, 0), torch.stack(masks, 0))
