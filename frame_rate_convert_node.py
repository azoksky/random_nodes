# -*- coding: utf-8 -*-
"""
Retimes an IMAGE batch from source_fps to target_fps by mapping every output
frame to its correct point in time (duration-preserving), instead of just
re-tagging the frame rate (which stretches/slow-motions playback) or dropping
frames on a fixed stride (which beats/judders when the ratio isn't integer,
e.g. 48 -> 30).

Each output frame i sits at time i / target_fps; we look up the source frame(s)
at that same time (src_pos = i * source_fps / target_fps) so drops/duplicates
land exactly where the timeline needs them, spread evenly rather than
periodically.
"""

import torch
from comfy_api.latest import io


class AzFrameRateConvert(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzFrameRateConvert",
            display_name="Frame Rate Convert (Retime)",
            category="AZ_Nodes",
            description=(
                "Resamples an IMAGE batch from source_fps to target_fps, preserving "
                "duration (no slow-motion stretch) with evenly-distributed frame "
                "drops instead of a fixed stride."
            ),
            inputs=[
                io.Image.Input("images"),
                io.Float.Input("source_fps", default=48.0, min=1.0, max=240.0, step=0.001),
                io.Float.Input("target_fps", default=30.0, min=1.0, max=240.0, step=0.001),
                io.Combo.Input(
                    "mode",
                    options=["blend", "nearest"],
                    default="blend",
                    tooltip=(
                        "blend: linearly cross-fades the two nearest source frames "
                        "(smoothest, slight ghosting on fast motion). "
                        "nearest: picks the closest single source frame "
                        "(sharp, no ghosting, marginally less smooth)."
                    ),
                ),
            ],
            outputs=[
                io.Image.Output(display_name="IMAGE"),
            ],
        )

    @classmethod
    def execute(cls, images, source_fps, target_fps, mode):
        n_in = images.shape[0]
        if n_in == 0 or source_fps <= 0 or target_fps <= 0:
            return io.NodeOutput(images)

        duration = n_in / source_fps
        n_out = max(1, round(duration * target_fps))
        ratio = source_fps / target_fps

        out_frames = []
        for i in range(n_out):
            src_pos = i * ratio
            if mode == "nearest":
                idx = min(n_in - 1, round(src_pos))
                out_frames.append(images[idx:idx + 1])
            else:
                idx0 = min(n_in - 1, int(src_pos))
                idx1 = min(n_in - 1, idx0 + 1)
                frac = src_pos - idx0
                frame = torch.lerp(images[idx0], images[idx1], frac)
                out_frames.append(frame.unsqueeze(0))

        return io.NodeOutput(torch.cat(out_frames, dim=0))
