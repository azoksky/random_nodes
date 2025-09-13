import types
import torch
import torch.cuda
import comfy.model_management
import gc



class AnyType(str):
  """A special class that is always equal in not equal comparisons. Credit to pythongosssss"""
  def __eq__(self, __value: object) -> bool:
    return True
  def __ne__(self, __value: object) -> bool:
    return False


any = AnyType("*")

class AzInput:
    NAME = "Az_Text_Input"
    CATEGORY = "AZ_Nodes"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {
                    "multiline": True,
                    "placeholder": "Enter String"
                })
            },
        }

    RETURN_TYPES = ("STRING",)
    FUNCTION = "main"

    # OUTPUT_NODE = False  # Optional, since False is default

    def main(self, text):
        return (text,)  # Return a tuple containing the text


class OverrideDevice:
    @classmethod
    def INPUT_TYPES(s):
        devices = ["cpu", ]
        for k in range(0, torch.cuda.device_count()):
            devices.append(f"cuda:{k}")

        return {
            "required": {
                "device": (devices, {"default": "cpu"}),
            }
        }

    FUNCTION = "patch"
    CATEGORY = "AZ_Nodes"

    def override(self, model, model_attr, device):
        # set model/patcher attributes
        model.device = device
        patcher = getattr(model, "patcher", model)  #.clone()
        for name in ["device", "load_device", "offload_device", "current_device", "output_device"]:
            setattr(patcher, name, device)

        # move model to device
        py_model = getattr(model, model_attr)
        py_model.to = types.MethodType(torch.nn.Module.to, py_model)
        py_model.to(device)

        # remove ability to move model
        def to(*args, **kwargs):
            pass

        py_model.to = types.MethodType(to, py_model)
        return (model,)

    def patch(self, *args, **kwargs):
        raise NotImplementedError


class OverrideCLIPDevice(OverrideDevice):
    @classmethod
    def INPUT_TYPES(s):
        k = super().INPUT_TYPES()
        k["required"]["clip"] = ("CLIP",)
        return k

    RETURN_TYPES = ("CLIP",)
    TITLE = "Force/Set CLIP Device"

    def patch(self, clip, device):
        return self.override(clip, "cond_stage_model", torch.device(device))


class OverrideVAEDevice(OverrideDevice):
    @classmethod
    def INPUT_TYPES(s):
        k = super().INPUT_TYPES()
        k["required"]["vae"] = ("VAE",)
        return k

    RETURN_TYPES = ("VAE",)
    TITLE = "Force/Set VAE Device"

    def patch(self, vae, device):
        return self.override(vae, "first_stage_model", torch.device(device))


class OverrideMODELDevice(OverrideDevice):
    @classmethod
    def INPUT_TYPES(s):
        k = super().INPUT_TYPES()
        k["required"]["model"] = ("MODEL",)
        return k

    RETURN_TYPES = ("MODEL",)
    TITLE = "Force/Set MODEL Device"

    def patch(self, model, device):
        return self.override(model, "model", torch.device(device))


class FluxResolutionNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "megapixel": (
                    ["0.1", "0.5", "1.0", "1.5", "2.0", "2.1", "2.2", "2.3", "2.4", "2.5"], {"default": "1.0"}),
                "aspect_ratio": ([
                                     "1:1 (Perfect Square)",
                                     "2:3 (Classic Portrait)", "3:4 (Golden Ratio)", "3:5 (Elegant Vertical)",
                                     "4:5 (Artistic Frame)", "5:7 (Balanced Portrait)", "5:8 (Tall Portrait)",
                                     "7:9 (Modern Portrait)", "9:16 (Slim Vertical)", "9:19 (Tall Slim)",
                                     "9:21 (Ultra Tall)", "9:32 (Skyline)",
                                     "3:2 (Golden Landscape)", "4:3 (Classic Landscape)", "5:3 (Wide Horizon)",
                                     "5:4 (Balanced Frame)", "7:5 (Elegant Landscape)", "8:5 (Cinematic View)",
                                     "9:7 (Artful Horizon)", "16:9 (Panorama)", "19:9 (Cinematic Ultrawide)",
                                     "21:9 (Epic Ultrawide)", "32:9 (Extreme Ultrawide)"
                                 ], {"default": "1:1 (Perfect Square)"}),
                "custom_ratio": ("BOOLEAN", {"default": False, "label_on": "Enable", "label_off": "Disable"}),
            },
            "optional": {
                "custom_aspect_ratio": ("STRING", {"default": "1:1"}),
            }
        }

    RETURN_TYPES = ("INT", "INT", "STRING")
    RETURN_NAMES = ("width", "height", "resolution")
    FUNCTION = "calculate_dimensions"
    CATEGORY = "AZ_Nodes"
    OUTPUT_NODE = True

    def calculate_dimensions(self, megapixel, aspect_ratio, custom_ratio, custom_aspect_ratio=None):
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

        # Apply rounding logic based on megapixel value
        if megapixel in [0.1, 0.5]:
            round_to = 8
        elif megapixel in [1.0, 1.5]:
            round_to = 64
        else:  # 2.0 and above
            round_to = 32

        width = round(width / round_to) * round_to
        height = round(height / round_to) * round_to

        resolution = f"{width} x {height}"

        return width, height, resolution


class GetImageSizeRatio:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",)
            }
        }

    RETURN_TYPES = ("INT", "INT", "STRING")
    RETURN_NAMES = ("width", "height", "ratio")
    FUNCTION = "get_image_size_ratio"

    CATEGORY = "AZ_Nodes"

    def get_image_size_ratio(self, image):
        _, height, width, _ = image.shape

        gcd = self.greatest_common_divisor(width, height)
        ratio_width = width // gcd
        ratio_height = height // gcd

        ratio = f"{ratio_width}:{ratio_height}"

        return width, height, ratio

    def greatest_common_divisor(self, a, b):
        while b != 0:
            a, b = b, a % b
        return a


class PurgeVRAM_V2:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "anything": (any, {}),
                "purge_cache": ("BOOLEAN", {"default": True}),
                "purge_models": ("BOOLEAN", {"default": True}),
            },
            "optional": {
            }
        }
    RETURN_TYPES = (any,)
    RETURN_NAMES = ("any",)
    FUNCTION = "purge_vram_v2"
    CATEGORY = 'AZ_Nodes'
    OUTPUT_NODE = True

    def purge_vram_v2(self, anything, purge_cache, purge_models):
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        if purge_models:
            comfy.model_management.unload_all_models()
        if purge_cache:
            comfy.model_management.soft_empty_cache()
        return (anything,)
    
class PurgeVRAM:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "anything": (any, {}),
                "purge_cache": ("BOOLEAN", {"default": True}),
                "purge_models": ("BOOLEAN", {"default": True}),
            },
            "optional": {
            }
        }
    RETURN_TYPES = ()
    FUNCTION = "purge_vram"
    CATEGORY = 'AZ_Nodes'
    OUTPUT_NODE = True

    def purge_vram(self, anything, purge_cache, purge_models):
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        if purge_models:
            comfy.model_management.unload_all_models()
        if purge_cache:
            comfy.model_management.soft_empty_cache()
        return (None,)