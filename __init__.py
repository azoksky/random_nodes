
#from .generate_clip_prompt_node import GenerateCLIPPromptNode
from .extra_node import AzInput, OverrideCLIPDevice, FluxResolutionNode, GetImageSizeRatio, OverrideVAEDevice, OverrideMODELDevice,PurgeVRAM, PurgeVRAM_V2, AnyType
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
from .krea2_gated_cond_node import AzKrea2GatedConditioning
from .krea2_gated_rebalance_node import AzKrea2GatedRebalance


NODE_CLASS_MAPPINGS = {
    #"GenerateCLIPPromptNode": GenerateCLIPPromptNode,
    "Aria2Downloader": Aria2Downloader,
    "AzInput": AzInput,
    "OverrideCLIPDevice": OverrideCLIPDevice,
    "OverrideVAEDevice": OverrideVAEDevice,
    "OverrideMODELDevice": OverrideMODELDevice,
    "FluxResolutionNode": FluxResolutionNode,
    "GetImageSizeRatio": GetImageSizeRatio,
    "PurgeVRAM_V1": PurgeVRAM,
    "PurgeVRAM_V2": PurgeVRAM_V2,
    "PathUploader": PathUploader,
    "hf_hub_downloader":hf_hub_downloader,
    "hf_list_downloader": HFListDownloader,
    "hf_list_aria2": HFListAria2Downloader,
    "WanFirstGuidingFrameToVideo": WanFirstGuidingFrameToVideo,
    "AzIterativeString": AzIterativeString,
    "AzPadSquareForInpaint": AzPadSquareForInpaint,
    "AzSeamlessStitch": AzSeamlessStitch,
    "AzDetailerInpaint": AzInpaintCropStitch,
    "AzKrea2ProjectorRebalance": AzKrea2ProjectorRebalance,
    "AzKrea2GatedConditioning": AzKrea2GatedConditioning,
    "AzKrea2GatedRebalance": AzKrea2GatedRebalance,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    # "GenerateCLIPPromptNode": "Generate CLIP Prompt",
    "Aria2Downloader": "Aria2 Downloader",
    "AzInput": "Input String",
    "OverrideCLIPDevice": "Force/Set CLIP Device",
    "OverrideVAEDevice": "Force/Set VAE Device",
    "OverrideMODELDevice": "Force/Set MODEL Device",
    "FluxResolutionNode": "Flux Resolution Calc",
    "GetImageSizeRatio": "Get Image Size Ratio",
    "PurgeVRAM": "Purge VRAM V1",
    "PurgeVRAM_V2": "Purge VRAM V2",
    "PathUploader": "Path Uploader",
    "hf_hub_downloader":"HF Downloader",
    "hf_list_downloader": "HF List Downloader",
    "hf_list_aria2": "HF List Downloader (aria2)",
    "WanFirstGuidingFrameToVideo": "Wan First Guiding Frame To Video",
    "AzIterativeString": "Iterative String",
    "AzPadSquareForInpaint": "Pad Square For Inpaint",
    "AzSeamlessStitch": "Seamless Stitch",
    "AzDetailerInpaint": "Inpaint (Crop & Stitch)",
    "AzKrea2ProjectorRebalance": "Krea2 Text-Fusion Rebalance (Projector)",
    "AzKrea2GatedConditioning": "Krea2 Timestep-Gated Conditioning",
    "AzKrea2GatedRebalance": "Krea2 Gated Rebalance (all-in-one)",
}

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
