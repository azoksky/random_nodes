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

import logging

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
                    options=["nearest", "blend"],
                    default="nearest",
                    tooltip=(
                        "nearest: picks the closest single source frame. Every output "
                        "frame stays sharp; timing jitters by up to half a source frame. "
                        "blend: cross-fades the two neighbouring frames to hit the exact "
                        "timestamp, but the blend weight cycles, so sharpness visibly "
                        "pulses -- and it ghosts doubly on RIFE-generated frames. "
                        "Prefer nearest, and pick a source_fps that is an integer "
                        "multiple of target_fps so neither artifact occurs."
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

        est_gib = n_out * images[0].nelement() * images.element_size() / 1024 ** 3
        if est_gib > 4.0:
            logging.warning(
                f"AzFrameRateConvert: {n_out} output frames need ~{est_gib:.1f} GiB "
                f"of RAM. Process the source in chunks if that exceeds available memory."
            )

        # Write straight into a preallocated buffer. Collecting frames in a list
        # and torch.cat-ing needs a second full copy of the result, and in blend
        # mode every frame is a freshly allocated tensor -- together that's two
        # copies of a video-length batch.
        out = torch.empty((n_out,) + tuple(images.shape[1:]), dtype=images.dtype)
        for i in range(n_out):
            src_pos = i * ratio
            if mode == "nearest":
                # floor(x+0.5), not round(): round() is banker's rounding, so
                # exact .5 positions alternate down/up and the cadence stutters.
                out[i] = images[min(n_in - 1, int(src_pos + 0.5))]
            else:
                idx0 = min(n_in - 1, int(src_pos))
                idx1 = min(n_in - 1, idx0 + 1)
                torch.lerp(images[idx0], images[idx1], src_pos - idx0, out=out[i])

        return io.NodeOutput(out)
