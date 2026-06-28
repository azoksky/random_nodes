from comfy_api.latest import ComfyExtension, io

from .flux_resolution_node import FluxResolutionNode
from .path_uploader import PathUploader
from .Downloader_helper import Aria2Downloader
from .hf_hub_downloader import hf_hub_downloader
from .hf_list_downloader import HFListDownloader
from .hf_list_aria2 import HFListAria2Downloader
from .wan_first_guiding_node import WanFirstGuidingFrameToVideo
from .iterative_string_node import AzIterativeString
from .pad_square_node import AzPadSquareForInpaint
from .seamless_stitch_node import AzSeamlessStitch
from .detailer_inpaint_node import AzInpaintCropStitch
from .krea2_rebalance_node import AzKrea2ProjectorRebalance
from .krea2_gated_rebalance_node import AzKrea2GatedRebalance
from .gated_lora_node import AzGatedLoraLoader
from .gated_lora_sampler_node import AzGatedLoraSampler


class RandomNodesExtension(ComfyExtension):
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [
            FluxResolutionNode,
            PathUploader,
            Aria2Downloader,
            hf_hub_downloader,
            HFListDownloader,
            HFListAria2Downloader,
            WanFirstGuidingFrameToVideo,
            AzIterativeString,
            AzPadSquareForInpaint,
            AzSeamlessStitch,
            AzInpaintCropStitch,
            AzKrea2ProjectorRebalance,
            AzKrea2GatedRebalance,
            AzGatedLoraLoader,
            AzGatedLoraSampler,
        ]


async def comfy_entrypoint() -> ComfyExtension:
    return RandomNodesExtension()


WEB_DIRECTORY = "./js"

__all__ = ["comfy_entrypoint", "WEB_DIRECTORY"]
