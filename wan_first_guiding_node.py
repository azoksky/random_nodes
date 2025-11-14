import torch
import comfy
import node_helpers


class WanFirstGuidingFrameToVideo:
    """
    Supports two modes:

    1) batch_mode = False   (DEFAULT)
       - start_images (batch) placed at frames 0..N-1
       - guiding_image placed at ABSOLUTE frame index = guiding_index
       - full-sequence image (len = "length") is encoded with VAE

    2) batch_mode = True
       - start_images are encoded as a BATCH
       - guiding_image is encoded separately
       - guiding latent INSERTED into the batch at guiding_index % batch_size
       - batch is then expanded to match the required video-latent temporal shape

    In both cases: returns conditioning:
      - concat_latent_image
      - concat_mask
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive": ("CONDITIONING",),
                "negative": ("CONDITIONING",),
                "vae": ("VAE",),

                "width": ("INT", {"default": 832, "min": 16, "max": 4096, "step": 16}),
                "height": ("INT", {"default": 480, "min": 16, "max": 4096, "step": 16}),
                "length": ("INT", {"default": 81, "min": 1, "max": 4096}),
                "batch_size": ("INT", {"default": 1, "min": 1, "max": 4096}),
            },
            "optional": {
                "start_images": ("IMAGE",),
                "guiding_image": ("IMAGE",),

                "guiding_index": ("INT", {"default": 0, "min": 0, "max": 4096}),
                "batch_mode": ("BOOL", {"default": False}),

                "clip_vision_start_images": ("CLIP_VISION_OUTPUT",),
                "clip_vision_guiding_image": ("CLIP_VISION_OUTPUT",),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "CONDITIONING", "LATENT")
    RETURN_NAMES = ("positive", "negative", "samples")
    FUNCTION = "process"
    CATEGORY = "AZ_Nodes"
    OUTPUT_NODE = True


    # -------------------------------------------------------------------------
    # MAIN PROCESS
    # -------------------------------------------------------------------------
    def process(
        self,
        positive,
        negative,
        vae,
        width,
        height,
        length,
        batch_size,
        start_images=None,
        guiding_image=None,
        guiding_index=0,
        batch_mode=False,
        clip_vision_start_images=None,
        clip_vision_guiding_image=None,
    ):

        device = comfy.model_management.intermediate_device()
        spacial = vae.spacial_compression_encode()

        # Outgoing diffusion latent (placeholder; real insertion downstream)
        latent = torch.zeros(
            [batch_size, vae.latent_channels,
             ((length - 1) // 4) + 1,
             height // spacial,
             width // spacial],
            device=device
        )

        # ---------------------------------------------------------------------
        # Helper — normalize image into NHWC float32 on device
        # ---------------------------------------------------------------------
        def to_nhwc(img):
            if not isinstance(img, torch.Tensor):
                img = torch.tensor(img)

            if img.ndim == 3 and img.shape[0] == 3:     # CHW → HWC
                img = img.movedim(0, -1)

            if img.ndim == 4 and img.shape[1] == 3:     # NCHW → NHWC
                img = img.movedim(1, -1)

            return img.float().to(device)


        # ---------------------------------------------------------------------
        # START IMAGES PROCESSING
        # ---------------------------------------------------------------------
        start_seq = None
        num_start = 0

        if start_images is not None:
            si = to_nhwc(start_images)

            if si.ndim == 3:
                si = si.unsqueeze(0)

            num_start = min(si.shape[0], length)

            si_up = comfy.utils.common_upscale(
                si[:num_start].movedim(-1, 1),
                width, height, "bilinear", "center"
            ).movedim(1, -1)

            start_seq = si_up


        # ---------------------------------------------------------------------
        # GUIDING IMAGE PROCESSING
        # ---------------------------------------------------------------------
        guiding_up = None

        if guiding_image is not None:
            gi = guiding_image

            if not isinstance(gi, torch.Tensor):
                gi = torch.tensor(gi)

            if gi.ndim == 3 and gi.shape[0] == 3:  # CHW → HWC
                gi = gi.movedim(0, -1)

            if gi.ndim == 3:
                gi_nchw = gi.movedim(-1, 0).unsqueeze(0)
            else:
                gi_nchw = gi.movedim(-1, 1)

            guiding_up = comfy.utils.common_upscale(
                gi_nchw, width, height, "bilinear", "center"
            ).movedim(1, -1)[0]

            guiding_up = guiding_up.float().to(device)


        # =========================================================================
        # MODE 1: batch_mode=True (insert guiding image INTO the batch)
        # =========================================================================
        if batch_mode and start_seq is not None:

            # Encode batch
            start_latent = vae.encode(start_seq.movedim(-1, 1))     # (B, C, T, H, W)

            if guiding_up is not None:
                guiding_latent = vae.encode(guiding_up.movedim(-1, 0).unsqueeze(0))
            else:
                guiding_latent = None

            concat_latent = start_latent.clone()

            # Calculate insert index
            idx = guiding_index % concat_latent.shape[0]

            # Insert guiding latent
            if guiding_latent is not None:
                concat_latent[idx] = guiding_latent[0]

            # Create mask: zeros where fixed, ones elsewhere
            concat_mask = torch.ones_like(concat_latent[:, :1])  # shape (B,1,T,H,W)
            concat_mask[:] = 0.0  # all start frames are fixed

            # Register conditioning
            positive = node_helpers.conditioning_set_values(positive, {
                "concat_latent_image": concat_latent,
                "concat_mask": concat_mask,
            })
            negative = node_helpers.conditioning_set_values(negative, {
                "concat_latent_image": concat_latent,
                "concat_mask": concat_mask,
            })

            return (positive, negative, {"samples": latent})


        # =========================================================================
        # MODE 2: batch_mode=False (DEFAULT — build full 81-frame sequence)
        # =========================================================================

        # Build neutral sequence
        image = torch.ones((length, height, width, 3), device=device) * 0.5

        # Place start batch at front
        if start_seq is not None:
            image[:num_start] = start_seq

        # Place guiding at absolute frame index
        if guiding_up is not None:
            gi = max(0, min(length - 1, int(guiding_index)))
            image[gi] = guiding_up

        # Mask: 0 → fixed, 1 → free
        latent_time = ((length - 1) // 4) + 1

        mask = torch.ones(
            (1, 1, latent_time * 4,
             height // spacial,
             width // spacial),
            device=device
        )

        # Zero mask over start-frames (plus 3-frame pad)
        pad = 3
        if num_start > 0:
            mask[:, :, :min(length, num_start + pad)] = 0.0

        # Zero mask for guiding frame
        if guiding_up is not None:
            mask[:, :, gi:gi + 1] = 0.0

        # Encode whole sequence with VAE
        try:
            concat_latent_image = vae.encode(image[:, :, :, :3])
        except:
            concat_latENT_image = vae.encode(image.movedim(-1, 1))

        # Reshape mask from 4× grouping
        concat_mask = mask.view(
            1,
            mask.shape[2] // 4,
            4,
            mask.shape[3],
            mask.shape[4]
        ).transpose(1, 2)

        # Register conditioning
        positive = node_helpers.conditioning_set_values(positive, {
            "concat_latent_image": concat_latent_image,
            "concat_mask": concat_mask,
        })
        negative = node_helpers.conditioning_set_values(negative, {
            "concat_latent_image": concat_latent_image,
            "concat_mask": concat_mask,
        })


        # Return final latent
        return (positive, negative, {"samples": latent})
