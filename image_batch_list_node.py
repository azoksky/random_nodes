# -*- coding: utf-8 -*-
"""
Image <-> list conversion, matching ComfyUI-Impact-Pack's Image Batch to Image
List / Image List to Image Batch behavior.

Batch->List: splits an (N,H,W,C) IMAGE batch into N individual (1,H,W,C)
tensors emitted as a list output.

List->Batch: concatenates a list of IMAGE tensors back into one batch. Frames
whose H/W don't match the first frame are resized (lanczos, center-crop) to
match before concatenation, since torch.cat requires equal spatial dims.
"""

import torch
import comfy.utils
from comfy_api.latest import io


class AzImageBatchToList(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzImageBatchToList",
            display_name="Image Batch to Image List",
            category="AZ_Nodes",
            description="Splits an IMAGE batch into a list of single-image tensors.",
            inputs=[
                io.Image.Input("image"),
            ],
            outputs=[
                io.Image.Output(display_name="IMAGE", is_output_list=True),
            ],
        )

    @classmethod
    def execute(cls, image):
        images = [image[i:i + 1] for i in range(image.shape[0])]
        return io.NodeOutput(images)


class AzImageListToBatch(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzImageListToBatch",
            display_name="Image List to Image Batch",
            category="AZ_Nodes",
            description="Concatenates a list of IMAGE tensors back into a single batch.",
            inputs=[
                io.Image.Input("images"),
            ],
            outputs=[
                io.Image.Output(display_name="IMAGE"),
            ],
            is_input_list=True,
        )

    @classmethod
    def execute(cls, images):
        if len(images) <= 1:
            return io.NodeOutput(images[0])

        first = images[0]
        out = [first]
        for img in images[1:]:
            if img.shape[1:3] != first.shape[1:3]:
                img = comfy.utils.common_upscale(
                    img.movedim(-1, 1), first.shape[2], first.shape[1], "lanczos", "center"
                ).movedim(1, -1)
            out.append(img)
        return io.NodeOutput(torch.cat(out, dim=0))
