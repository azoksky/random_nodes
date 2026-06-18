# -*- coding: utf-8 -*-
"""
Detailer Inpaint (Crop & Stitch) — self-contained, no Impact-Pack deps.

Crops the masked region (+context), upscales that crop to a comfortable
sampling size, runs a real KSampler (with live previews) inpainting only the
masked area, decodes, scales back, and feather-stitches onto the ORIGINAL sharp
image. Mirrors Impact-Pack's enhance_detail geometry; improves on it by pasting
over the untouched original pixels so nothing outside the mask is VAE-softened.
"""

import torch
import torch.nn.functional as F

import comfy.utils
import comfy.sample
import comfy.samplers
import latent_preview


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


def _ksample(model, seed, steps, cfg, sampler_name, scheduler,
             positive, negative, latent, denoise):
    latent_image = latent["samples"]
    if hasattr(comfy.sample, "fix_empty_latent_channels"):
        latent_image = comfy.sample.fix_empty_latent_channels(model, latent_image)
    noise = comfy.sample.prepare_noise(latent_image, seed, latent.get("batch_index"))
    noise_mask = latent.get("noise_mask")
    callback = latent_preview.prepare_callback(model, steps)
    disable_pbar = not comfy.utils.PROGRESS_BAR_ENABLED
    samples = comfy.sample.sample(
        model, noise, steps, cfg, sampler_name, scheduler,
        positive, negative, latent_image, denoise=denoise,
        noise_mask=noise_mask, callback=callback,
        disable_pbar=disable_pbar, seed=seed,
    )
    out = latent.copy()
    out["samples"] = samples
    return out


class AzDetailerInpaint:
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
                "crop_factor": ("FLOAT", {"default": 1.5, "min": 1.0, "max": 5.0, "step": 0.1}),
                "guide_size": ("INT", {"default": 512, "min": 128, "max": 2048, "step": 8}),
                "max_size": ("INT", {"default": 1024, "min": 256, "max": 4096, "step": 8}),
                "mask_blur": ("INT", {"default": 16, "min": 0, "max": 256, "step": 1}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "steps": ("INT", {"default": 20, "min": 1, "max": 1000}),
                "cfg": ("FLOAT", {"default": 7.0, "min": 0.0, "max": 100.0, "step": 0.1}),
                "sampler_name": (comfy.samplers.KSampler.SAMPLERS,),
                "scheduler": (comfy.samplers.KSampler.SCHEDULERS,),
                "denoise": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.01}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    FUNCTION = "run"
    CATEGORY = "AZ_Nodes"

    def _crop_region(self, msk, W, H, crop_factor):
        ys, xs = torch.where(msk > 0.5)
        if ys.numel() == 0:
            return None
        x1, x2 = int(xs.min()), int(xs.max()) + 1
        y1, y2 = int(ys.min()), int(ys.max()) + 1
        bw, bh = x2 - x1, y2 - y1
        cw, ch = bw * crop_factor, bh * crop_factor
        cx, cy = x1 + bw / 2.0, y1 + bh / 2.0
        nx1 = max(0, int(cx - cw / 2.0))
        ny1 = max(0, int(cy - ch / 2.0))
        nx2 = min(W, int(cx + cw / 2.0))
        ny2 = min(H, int(cy + ch / 2.0))
        if nx2 - nx1 < 8 or ny2 - ny1 < 8:
            return None
        return nx1, ny1, nx2, ny2

    def _one(self, model, positive, negative, vae, img, msk, params):
        (crop_factor, guide_size, max_size, mask_blur,
         seed, steps, cfg, sampler_name, scheduler, denoise) = params
        H, W, C = img.shape
        device = img.device

        region = self._crop_region(msk, W, H, crop_factor)
        if region is None:
            return img, torch.zeros((H, W), dtype=img.dtype, device=device)
        x1, y1, x2, y2 = region
        cw, ch = x2 - x1, y2 - y1

        crop_img = img[y1:y2, x1:x2, :]          # (ch,cw,C)
        crop_msk = msk[y1:y2, x1:x2]             # (ch,cw)

        # Impact-style scaling: bring the short edge to guide_size, cap by max_size.
        scale = guide_size / max(1, min(cw, ch))
        if max(cw, ch) * scale > max_size:
            scale = max_size / max(cw, ch)
        uw = max(8, (int(round(cw * scale)) // 8) * 8)
        uh = max(8, (int(round(ch * scale)) // 8) * 8)

        up_img = _resize_bhwc(crop_img.unsqueeze(0), uw, uh, "lanczos").clamp(0, 1)
        up_msk = F.interpolate(
            crop_msk.view(1, 1, ch, cw).to(torch.float32),
            size=(uh, uw), mode="bilinear", align_corners=False,
        ).to(img.dtype)

        latent = {"samples": vae.encode(up_img[:, :, :, :3])}
        # noise mask = masked area to (re)generate; feathered so the sampler blends
        # into the preserved context instead of leaving a hard inner edge.
        nm = _gaussian_blur(up_msk, mask_blur).clamp(0, 1)
        latent["noise_mask"] = nm

        latent = _ksample(model, seed, steps, cfg, sampler_name, scheduler,
                           positive, negative, latent, denoise)
        refined = vae.decode(latent["samples"])      # (1,uh,uw,C')
        refined = refined[:, :, :, :C].to(device)

        refined_crop = _resize_bhwc(refined, cw, ch, "lanczos").clamp(0, 1)[0]

        # feather the crop mask, keep the interior solid so only the boundary fades
        cm = crop_msk.view(1, 1, ch, cw).to(img.dtype)
        if mask_blur > 0:
            blurred = _gaussian_blur(cm, mask_blur)
            core = -F.max_pool2d(-cm, mask_blur * 2 + 1, stride=1, padding=mask_blur)
            cm = torch.maximum(blurred, core)
        cm = cm.clamp(0, 1)[0, 0]                     # (ch,cw)

        out = img.clone()
        blend = cm.unsqueeze(-1)
        out[y1:y2, x1:x2, :] = refined_crop * blend + crop_img * (1 - blend)

        full_mask = torch.zeros((H, W), dtype=img.dtype, device=device)
        full_mask[y1:y2, x1:x2] = cm
        return out, full_mask

    def run(self, model, positive, negative, vae, image, mask,
            crop_factor, guide_size, max_size, mask_blur,
            seed, steps, cfg, sampler_name, scheduler, denoise):
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
            p = (crop_factor, guide_size, max_size, mask_blur,
                 seed + b, steps, cfg, sampler_name, scheduler, denoise)
            o, mm = self._one(model, positive, negative, vae,
                              image[b], m[b], p)
            outs.append(o)
            masks.append(mm)
        return (torch.stack(outs, 0), torch.stack(masks, 0))
