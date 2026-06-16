# -*- coding: utf-8 -*-
"""
Iterative String node for ComfyUI.

One text box ("name"); outputs "<name>_<n>" where n increments by 1 on every
run. The counter is per node instance (keyed by the node's unique id) and lives
in memory, so it resets when ComfyUI restarts.
"""


class AzIterativeString:
    # unique_id -> last emitted counter value
    _counters = {}

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "name": ("STRING", {"default": "output", "multiline": False}),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("string",)
    FUNCTION = "generate"
    CATEGORY = "AZ_Nodes"

    @classmethod
    def IS_CHANGED(cls, name, unique_id=None):
        # Force a re-run every queue so the counter actually advances; without
        # this ComfyUI caches the node when inputs are unchanged.
        return float("nan")

    def generate(self, name, unique_id=None):
        key = str(unique_id) if unique_id is not None else "default"
        n = AzIterativeString._counters.get(key, 0) + 1
        AzIterativeString._counters[key] = n
        out = f"{name}_{n}"
        # "ui" feeds the JS preview (onExecuted); "result" is the graph output.
        return {"ui": {"text": [out]}, "result": (out,)}
