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


def _scale_for_vision(image, megapixels):
    # image: IMAGE tensor [B,H,W,C] float 0-1. Downscale oversized refs to ~megapixels
    # so the vision tower isn't fed a needlessly huge frame; never upscale.
    b, h, w, c = image.shape
    target = max(1, int(megapixels * 1024 * 1024))
    if h * w <= target:
        return image
    scale = (target / (h * w)) ** 0.5
    nw = max(1, round(w * scale))
    nh = max(1, round(h * scale))
    s = image.movedim(-1, 1)
    s = comfy.utils.common_upscale(s, nw, nh, "area", "disabled")
    return s.movedim(1, -1)


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
                        "content comes from the text. Feed the CLIP loaded with type 'krea2'.",
            inputs=[
                io.Clip.Input("clip", tooltip="CLIP loaded with type 'krea2' (Qwen3-VL-4B)."),
                io.String.Input("prompt", multiline=True, default="",
                                tooltip="Describes the CONTENT to generate. The reference supplies "
                                        "only the style."),
                io.Image.Input("style_image", tooltip="Primary style reference."),
                io.Image.Input("style_image2", optional=True, tooltip="Optional second reference to blend."),
                io.Image.Input("style_image3", optional=True, tooltip="Optional third reference to blend."),
                io.Float.Input("style_strength", default=1.0, min=0.0, max=2.0, step=0.05,
                               tooltip="Overall conditioning strength honoured by the sampler. "
                                       "1.0 = neutral; lower for a looser influence, higher to push harder."),
                io.Float.Input("vision_megapixels", default=1.0, min=0.1, max=8.0, step=0.1, optional=True,
                               tooltip="References are downscaled to about this many megapixels before "
                                       "the vision encoder. Higher keeps finer texture detail."),
                io.String.Input("system_prompt", multiline=True, default="", optional=True,
                                tooltip="Override the built-in style-only instruction. Leave empty to use "
                                        "the default."),
            ],
            outputs=[
                io.Conditioning.Output(display_name="conditioning"),
            ],
        )

    @classmethod
    def execute(cls, clip, prompt, style_image, style_image2=None, style_image3=None,
                style_strength=1.0, vision_megapixels=1.0, system_prompt=""):
        if clip is None:
            raise ValueError("Krea2 Style Reference: no CLIP. Load one with type 'krea2'.")

        refs = [img for img in (style_image, style_image2, style_image3) if img is not None]
        if not refs:
            raise ValueError("Krea2 Style Reference: connect at least one style_image.")

        images_vl = [_scale_for_vision(img, vision_megapixels) for img in refs]

        system = (system_prompt or "").strip() or _STYLE_SYSTEM
        scaffold = "".join(f"Style reference {i + 1}: {_VISION_BLOCK}\n" for i in range(len(images_vl)))
        text = scaffold + (prompt or "").strip()
        template = (
            "<|im_start|>system\n" + system + "<|im_end|>\n"
            "<|im_start|>user\n{}<|im_end|>\n"
            "<|im_start|>assistant\n"
        )

        tokens = clip.tokenize(text, images=images_vl, llama_template=template)
        cond = clip.encode_from_tokens_scheduled(tokens)

        if abs(style_strength - 1.0) > 1e-6:
            cond = node_helpers.conditioning_set_values(cond, {"strength": style_strength})

        return io.NodeOutput(cond)
