import hashlib

import torch
import comfy
import node_helpers
from comfy_api.latest import io


# Krea2's text encoder is Qwen3-VL-4B — a vision-language model. ComfyUI's krea2
# CLIP therefore accepts reference images in clip.tokenize(images=...), running them
# through the encoder's vision path so the resulting conditioning is "aware" of the
# picture. The native Qwen-Image-Edit node uses this to COPY a source image (it also
# attaches a VAE reference latent, which reproduces the source's structure/appearance).
#
# For a STYLE reference we want the opposite emphasis: keep the aesthetic (medium,
# palette, brushwork, lighting, texture, mood) of the reference, but take ALL subject
# matter and composition from the user's text. So we push the image only through the
# semantic vision path (no reference latent → structure is not copied) and steer the
# encoder with a style-only system prompt.

_STYLE_SYSTEM = (
    "You are a style-transfer conditioning encoder. You are given one or more STYLE "
    "REFERENCE images and a text prompt describing the content to render.\n"
    "Read the reference image(s) for AESTHETIC QUALITIES ONLY: artistic medium and "
    "technique (photo, oil, watercolour, 3D render, ink, etc.), brush/render texture and "
    "grain, colour palette and saturation, lighting quality and direction, contrast and "
    "tonal range, level of detail and finish, and overall mood or atmosphere.\n"
    "IGNORE the reference's subjects, objects, characters, and layout — those must NOT "
    "appear in the output. Every subject, object, and composition comes strictly from the "
    "user's text.\n"
    "Produce conditioning that renders exactly what the text describes, painted in the "
    "visual style distilled from the reference(s). When several references are given, blend "
    "their styles into one coherent aesthetic."
)

_VISION_BLOCK = "<|vision_start|><|image_pad|><|vision_end|>"

# Qwen3-VL rounds each side to a multiple of 32 (patch 16 * merge 2) and its image
# processor caps at ~1280 visual tokens = 1280 * 32 * 32 pixels. Feeding more just gets
# downscaled internally, so cap here and align to 32 to skip a redundant resize.
_ALIGN = 32
_MODEL_MAX_PX = 1280 * _ALIGN * _ALIGN  # ~1.31 MP

_CACHE = {}


def _round32(x):
    return max(_ALIGN, int(round(x / _ALIGN)) * _ALIGN)


def _scale_for_vision(image, megapixels):
    # image: IMAGE tensor [B,H,W,C] float 0-1. Downscale oversized refs toward the target
    # (never upscale beyond it), align to 32, and never exceed the model's pixel cap.
    b, h, w, c = image.shape
    target = min(int(megapixels * 1024 * 1024), _MODEL_MAX_PX)
    cur = h * w
    scale = (target / cur) ** 0.5 if cur > target else 1.0
    nw = _round32(w * scale)
    nh = _round32(h * scale)
    if nw == w and nh == h:
        return image
    s = image.movedim(-1, 1)
    s = comfy.utils.common_upscale(s, nw, nh, "area", "disabled")
    return s.movedim(1, -1)


def _crop_to_mask(image, mask):
    # image: [B,H,W,C] float 0-1. mask: [B,H,W] (or [H,W]) float 0-1.
    # Crop to the mask's bounding box so the vision encoder only "sees" the masked
    # region -- same convention as the native TextEncodeQwenImageEditPlus mask path.
    if mask is None:
        return image
    m = mask
    if m.dim() == 2:
        m = m.unsqueeze(0)
    b, h, w, c = image.shape
    if m.shape[-2:] != (h, w):
        m = comfy.utils.common_upscale(m.unsqueeze(1), w, h, "bilinear", "disabled").squeeze(1)
    sel = m[0] > 0.5
    rows = torch.any(sel, dim=1)
    cols = torch.any(sel, dim=0)
    if not rows.any() or not cols.any():
        return image  # empty mask -> use the whole image
    ys = torch.where(rows)[0]
    xs = torch.where(cols)[0]
    y0, y1 = int(ys[0]), int(ys[-1])
    x0, x1 = int(xs[0]), int(xs[-1])
    return image[:, y0:y1 + 1, x0:x1 + 1, :]


def _sig(t):
    # Cheap, stable signature of a small tensor for cache keying.
    t = t.detach().to("cpu", copy=False).contiguous()
    return hashlib.sha1(t.numpy().tobytes()).hexdigest()


class AzKrea2StyleReference(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzKrea2StyleReference",
            display_name="Krea2 Style Reference",
            category="AZ_Nodes",
            description="Local, API-free style reference for Krea 2. Pushes reference image(s) "
                        "through the Krea2 (Qwen3-VL) text encoder's vision path with a style-only "
                        "system prompt, so the output keeps the reference's aesthetic while all "
                        "content comes from the text. Feed the CLIP loaded with type 'krea2'; "
                        "route LoRA/CLIP-patch nodes' CLIP output in and their patches apply.",
            inputs=[
                io.Clip.Input("clip", tooltip="CLIP loaded with type 'krea2' (Qwen3-VL-4B). "
                                              "Connect a LoRA/patch node's CLIP output to apply its patches."),
                io.String.Input("prompt", multiline=True, default="",
                                tooltip="Describes the CONTENT to generate. The reference supplies "
                                        "only the style."),
                io.Image.Input("style_image", tooltip="Primary style reference."),
                io.Mask.Input("style_mask", optional=True,
                              tooltip="Optional mask for style_image. Only the masked region (cropped to "
                                      "its bounding box) is sent to the vision encoder."),
                io.Image.Input("style_image2", optional=True, tooltip="Optional second reference to blend."),
                io.Mask.Input("style_mask2", optional=True,
                              tooltip="Optional mask for style_image2 (same behaviour as style_mask)."),
                io.Float.Input("style_strength", default=1.0, min=0.0, max=2.0, step=0.05,
                               tooltip="Overall conditioning strength honoured by the sampler. "
                                       "1.0 = neutral; lower for a looser influence, higher to push harder."),
                io.Float.Input("vision_megapixels", default=0.5, min=0.1, max=1.3, step=0.05, optional=True,
                               tooltip="References are downscaled to about this many megapixels (aligned to 32 px, "
                                       "hard-capped at the model's ~1.31 MP). Lower = faster; style rarely needs "
                                       "more than ~0.5 MP."),
                io.String.Input("system_prompt", multiline=True, default="", optional=True,
                                tooltip="Override the built-in style-only instruction. Leave empty to use "
                                        "the default."),
            ],
            outputs=[
                io.Conditioning.Output(display_name="conditioning"),
            ],
            hidden=[io.Hidden.unique_id],
        )

    @classmethod
    def execute(cls, clip, prompt, style_image, style_mask=None, style_image2=None,
                style_mask2=None, style_strength=1.0, vision_megapixels=0.5, system_prompt=""):
        if clip is None:
            raise ValueError("Krea2 Style Reference: no CLIP. Load one with type 'krea2'.")

        pairs = [(style_image, style_mask), (style_image2, style_mask2)]
        refs = [(img, msk) for img, msk in pairs if img is not None]
        if not refs:
            raise ValueError("Krea2 Style Reference: connect at least one style_image.")

        images_vl = [_scale_for_vision(_crop_to_mask(img, msk), vision_megapixels) for img, msk in refs]

        system = (system_prompt or "").strip() or _STYLE_SYSTEM
        scaffold = "".join(f"Style reference {i + 1}: {_VISION_BLOCK}\n" for i in range(len(images_vl)))
        text = scaffold + (prompt or "").strip()
        template = (
            "<|im_start|>system\n" + system + "<|im_end|>\n"
            "<|im_start|>user\n{}<|im_end|>\n"
            "<|im_start|>assistant\n"
        )

        # Re-encoding the vision tower is the expensive step; skip it when nothing that
        # affects the encode changed (e.g. only the sampler seed moved). Keyed per node.
        node_id = cls.hidden.unique_id
        fp = (id(clip), text, tuple(_sig(v) for v in images_vl))
        cached = _CACHE.get(node_id)
        if cached and cached[0] == fp:
            cond = cached[1]
        else:
            tokens = clip.tokenize(text, images=images_vl, llama_template=template)
            cond = clip.encode_from_tokens_scheduled(tokens)
            _CACHE[node_id] = (fp, cond)

        if abs(style_strength - 1.0) > 1e-6:
            cond = node_helpers.conditioning_set_values(cond, {"strength": style_strength})

        return io.NodeOutput(cond)
