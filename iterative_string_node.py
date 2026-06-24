# -*- coding: utf-8 -*-
"""
Iterative String node for ComfyUI.

One text box ("name"); outputs "<name>_<n>" where n increments by 1 on every
run. The counter is per node instance (keyed by the node's unique id) and lives
in memory, so it resets when ComfyUI restarts.
"""

from comfy_api.latest import io


class AzIterativeString(io.ComfyNode):
    # unique_id -> last emitted counter value
    _counters = {}

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="AzIterativeString",
            display_name="Auto-Increment Filename",
            category="AZ_Nodes",
            description="Outputs <name>_<n>, incrementing n by 1 each run (per node, resets on restart).",
            inputs=[
                io.String.Input("name", default="output", multiline=False),
            ],
            outputs=[
                io.String.Output(display_name="string"),
            ],
            hidden=[io.Hidden.unique_id],
            not_idempotent=True,
        )

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        # Force a re-run every queue so the counter actually advances; without
        # this ComfyUI caches the node when inputs are unchanged.
        return float("nan")

    @classmethod
    def execute(cls, name):
        unique_id = cls.hidden.unique_id
        key = str(unique_id) if unique_id is not None else "default"
        n = cls._counters.get(key, 0) + 1
        cls._counters[key] = n
        out = f"{name}_{n}"
        # "ui" feeds the JS preview (onExecuted); the positional arg is the graph output.
        return io.NodeOutput(out, ui={"text": [out]})
