
#from .generate_clip_prompt_node import GenerateCLIPPromptNode
from .extra_node import AzInput, OverrideCLIPDevice, FluxResolutionNode, GetImageSizeRatio, OverrideVAEDevice, OverrideMODELDevice,PurgeVRAM, PurgeVRAM_V2, AnyType
from .path_uploader import PathUploader
from .Downloader_helper import Aria2Downloader
from .hf_hub_downloader import hf_hub_downloader
from .hf_list_downloader import HFListDownloader


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
    
}

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

