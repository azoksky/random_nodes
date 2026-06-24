# -*- coding: utf-8 -*-
"""Flux resolution calculator: width/height for a target megapixel + aspect ratio."""

from comfy_api.latest import io


class FluxResolutionNode(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="FluxResolutionNode",
            display_name="Flux Resolution Calculator",
            category="AZ_Nodes",
            description="Compute width/height for a target megapixel count at a chosen aspect ratio.",
            is_output_node=True,
            inputs=[
                io.Combo.Input(
                    "megapixel",
                    options=["0.1", "0.5", "1.0", "1.5", "2.0", "2.1", "2.2", "2.3", "2.4", "2.5"],
                    default="1.0",
                ),
                io.Combo.Input(
                    "aspect_ratio",
                    options=[
                        "1:1 (Perfect Square)",
                        "2:3 (Classic Portrait)", "3:4 (Golden Ratio)", "3:5 (Elegant Vertical)",
                        "4:5 (Artistic Frame)", "5:7 (Balanced Portrait)", "5:8 (Tall Portrait)",
                        "7:9 (Modern Portrait)", "9:16 (Slim Vertical)", "9:19 (Tall Slim)",
                        "9:21 (Ultra Tall)", "9:32 (Skyline)",
                        "3:2 (Golden Landscape)", "4:3 (Classic Landscape)", "5:3 (Wide Horizon)",
                        "5:4 (Balanced Frame)", "7:5 (Elegant Landscape)", "8:5 (Cinematic View)",
                        "9:7 (Artful Horizon)", "16:9 (Panorama)", "19:9 (Cinematic Ultrawide)",
                        "21:9 (Epic Ultrawide)", "32:9 (Extreme Ultrawide)",
                    ],
                    default="1:1 (Perfect Square)",
                ),
                io.Boolean.Input("custom_ratio", default=False, label_on="Enable", label_off="Disable"),
                io.String.Input("custom_aspect_ratio", default="1:1", optional=True),
            ],
            outputs=[
                io.Int.Output(display_name="width"),
                io.Int.Output(display_name="height"),
                io.String.Output(display_name="resolution"),
            ],
        )

    @classmethod
    def execute(cls, megapixel, aspect_ratio, custom_ratio, custom_aspect_ratio=None):
        megapixel = float(megapixel)

        if custom_ratio and custom_aspect_ratio:
            numeric_ratio = custom_aspect_ratio
        else:
            numeric_ratio = aspect_ratio.split(' ')[0]

        width_ratio, height_ratio = map(int, numeric_ratio.split(':'))

        total_pixels = megapixel * 1_000_000
        dimension = (total_pixels / (width_ratio * height_ratio)) ** 0.5
        width = int(dimension * width_ratio)
        height = int(dimension * height_ratio)

        if megapixel in [0.1, 0.5]:
            round_to = 8
        elif megapixel in [1.0, 1.5]:
            round_to = 64
        else:  # 2.0 and above
            round_to = 32

        width = round(width / round_to) * round_to
        height = round(height / round_to) * round_to

        resolution = f"{width} x {height}"

        return io.NodeOutput(width, height, resolution)
