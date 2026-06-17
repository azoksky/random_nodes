# -*- coding: utf-8 -*-
"""
Seamless stitch: paste inpainted pixels back onto the ORIGINAL sharp image.

ComfyUI's normal flow VAE-decodes the whole canvas, so the untouched photo loses
sharpness and any tone mismatch at the mask edge shows as a line. This node keeps
the original pixels everywhere the mask is 0 and only takes the (sharp) inpainted
pixels where the mask is 1, blending across a feathered seam.

Feed it:
  original  = pad_square 'image' output (sharp, canvas-size)
  inpainted = your VAE-decoded result
  mask      = pad_square 'mask_full' output (every regenerated pixel)
"""

import torch
import torch.nn.functional as F
import comfy.utils


def _gaussian_blur(mask, radius):
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


def _color_match(inp, orig, keep):
    # Match inpaint tone to original using stats over the kept (original) region
    # so brightness/tone doesn't jump across the seam. inp/orig: (B,H,W,C),
    # keep: (B,H,W,1) weight in [0,1] of "trust original here".
    out = inp.clone()
    eps = 1e-5
    for b in range(inp.shape[0]):
        w = keep[b, ..., 0]
        if w.sum() < 16:
            continue
        wsum = w.sum()
        for c in range(inp.shape[-1]):
            o = orig[b, ..., c]
            i = inp[b, ..., c]
            o_mean = (o * w).sum() / wsum
            i_mean = (i * w).sum() / wsum
            o_std = torch.sqrt(((o - o_mean) ** 2 * w).sum() / wsum + eps)
            i_std = torch.sqrt(((i - i_mean) ** 2 * w).sum() / wsum + eps)
            out[b, ..., c] = (i - i_mean) / i_std * o_std + o_mean
    return out.clamp(0, 1)


class AzSeamlessStitch:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "original": ("IMAGE",),
                "inpainted": ("IMAGE",),
                "mask": ("MASK",),
                "expand": ("INT", {"default": 0, "min": -256, "max": 256, "step": 1}),
                "feather": ("INT", {"default": 24, "min": 0, "max": 512, "step": 1}),
                "color_match": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "blend_mask")
    FUNCTION = "stitch"
    CATEGORY = "AZ_Nodes"

    def stitch(self, original, inpainted, mask, expand, feather, color_match):
        B, H, W, C = original.shape
        device, dtype = original.device, original.dtype

        # align inpainted to original size
        inp = inpainted.to(device)
        if inp.shape[1] != H or inp.shape[2] != W:
            inp = comfy.utils.common_upscale(
                inp.movedim(-1, 1), W, H, "bilinear", "disabled"
            ).movedim(1, -1)
        if inp.shape[0] != B:
            inp = inp[:1].repeat(B, 1, 1, 1)

        # mask -> (B,1,H,W) at original size
        m = mask.to(device)
        if m.dim() == 2:
            m = m.unsqueeze(0)
        if m.shape[0] != B:
            m = m[:1].repeat(B, 1, 1)
        m = m.unsqueeze(1).to(dtype)
        if m.shape[2] != H or m.shape[3] != W:
            m = F.interpolate(m, size=(H, W), mode="bilinear", align_corners=False)

        # expand(+) / contract(-) the take-inpaint region, then feather the seam
        if expand > 0:
            m = F.max_pool2d(m, expand * 2 + 1, stride=1, padding=expand)
        elif expand < 0:
            e = -expand
            m = -F.max_pool2d(-m, e * 2 + 1, stride=1, padding=e)
        if feather > 0:
            m = _gaussian_blur(m, feather)
        m = m.clamp(0, 1)

        blend = m.movedim(1, -1)  # (B,H,W,1)
        if color_match:
            inp = _color_match(inp, original, (1.0 - blend))

        out = inp * blend + original * (1.0 - blend)
        return (out.clamp(0, 1), m.squeeze(1))
