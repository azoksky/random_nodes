# -*- coding: utf-8 -*-
"""
Pad-to-square + combined inpaint mask, in one node.

Takes an IMAGE and its painted MASK, resizes the image to fit a target box
(keeping aspect), pads the remainder, and emits a single combined MASK where:
  - the padded border  -> 1 (inpaint it)
  - inside the image   -> the painted mask
The pad region is derived from geometry, not by re-detecting the pad color, so
there's no Color-To-Mask threshold/lanczos-seam fragility. Replaces
Resize Image v2 + Color To Mask + Combine Masks + Grow Mask With Blur.
"""

import torch
import torch.nn.functional as F
import comfy.utils


def _parse_color(s, default=(0, 0, 0)):
    try:
        parts = [int(x.strip()) for x in str(s).split(",")]
        if len(parts) == 1:
            v = max(0, min(255, parts[0]))
            return (v, v, v)
        if len(parts) >= 3:
            return tuple(max(0, min(255, p)) for p in parts[:3])
    except Exception:
        pass
    return default


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


class AzPadSquareForInpaint:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "width": ("INT", {"default": 1024, "min": 16, "max": 8192, "step": 8}),
                "height": ("INT", {"default": 1024, "min": 16, "max": 8192, "step": 8}),
                "upscale_method": (
                    ["lanczos", "bicubic", "bilinear", "area", "nearest-exact"],
                    {"default": "lanczos"},
                ),
                "pad_color": ("STRING", {"default": "0,0,0"}),
                "crop_position": (
                    ["center", "top", "bottom", "left", "right"],
                    {"default": "center"},
                ),
                "divisible_by": ("INT", {"default": 16, "min": 1, "max": 256, "step": 1}),
                "mask_grow": ("INT", {"default": 0, "min": 0, "max": 256, "step": 1}),
                "mask_blur": ("INT", {"default": 0, "min": 0, "max": 256, "step": 1}),
            },
            "optional": {
                "mask": ("MASK",),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK", "INT", "INT")
    RETURN_NAMES = ("image", "mask", "width", "height")
    FUNCTION = "process"
    CATEGORY = "AZ_Nodes"

    def process(self, image, width, height, upscale_method, pad_color,
                crop_position, divisible_by, mask_grow, mask_blur, mask=None):
        B, H, W, C = image.shape
        device, dtype = image.device, image.dtype

        if divisible_by > 1:
            width = max(divisible_by, (width // divisible_by) * divisible_by)
            height = max(divisible_by, (height // divisible_by) * divisible_by)

        # fit inside target box, keep aspect
        scale = min(width / W, height / H)
        new_w = max(1, int(round(W * scale)))
        new_h = max(1, int(round(H * scale)))

        img_bchw = image.movedim(-1, 1)
        img_resized = comfy.utils.common_upscale(
            img_bchw, new_w, new_h, upscale_method, "disabled"
        ).movedim(1, -1)

        # painted mask -> (B,1,new_h,new_w)
        if mask is None:
            mask_resized = torch.zeros((B, 1, new_h, new_w), dtype=dtype, device=device)
        else:
            m = mask
            if m.dim() == 2:
                m = m.unsqueeze(0)
            if m.shape[0] != B:
                m = m[:1].repeat(B, 1, 1)
            mask_resized = F.interpolate(
                m.unsqueeze(1).to(dtype), size=(new_h, new_w),
                mode="bilinear", align_corners=False,
            )

        # placement offsets
        pad_w, pad_h = width - new_w, height - new_h
        x0 = 0 if crop_position == "left" else pad_w if crop_position == "right" else pad_w // 2
        y0 = 0 if crop_position == "top" else pad_h if crop_position == "bottom" else pad_h // 2

        # padded image canvas filled with pad_color
        rgb = _parse_color(pad_color)
        canvas = torch.empty((B, height, width, C), dtype=dtype, device=device)
        for ci in range(min(C, 3)):
            canvas[..., ci] = rgb[ci] / 255.0
        if C > 3:
            canvas[..., 3:] = 1.0
        canvas[:, y0:y0 + new_h, x0:x0 + new_w, :] = img_resized

        # combined mask: pad border = 1, inside = painted
        out_mask = torch.ones((B, 1, height, width), dtype=dtype, device=device)
        out_mask[:, :, y0:y0 + new_h, x0:x0 + new_w] = mask_resized.clamp(0, 1)

        if mask_grow > 0:
            out_mask = F.max_pool2d(out_mask, mask_grow * 2 + 1, stride=1, padding=mask_grow)
        if mask_blur > 0:
            out_mask = _gaussian_blur(out_mask, mask_blur)

        out_mask = out_mask.squeeze(1).clamp(0, 1)
        return (canvas, out_mask, width, height)
