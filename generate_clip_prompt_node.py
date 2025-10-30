import asyncio
import aiohttp
import json
import re


class GenerateCLIPPromptNode:
    """
    Node to generate a CLIP-style prompt from a detailed input using llama3.2.
    Accepts a variable as input and displays the result in an always visible textbox.
    Allows specifying the API endpoint and the word limit for the generated prompt.
    Outputs the total word count of the generated prompt as an integer.
    """

    def __init__(self):
        self.actual_response = ""
        self.word_count = 0

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "optional": {
                "opt_recondition": ("STRING", {"forceInput": True}),
                "prefix_words": ("STRING", {
                    "multiline": False,
                    "placeholder": "Enter prefix text"
                })
            },
            "required": {
                "t5_prompt": ("STRING", {"forceInput": True}),
                "api_endpoint": ("STRING", {
                    "default": "https://azoksky.loca.lt",
                    "multiline": False,
                    "placeholder": "Enter API endpoint"
                }),
                "word_limit": ("INT", {
                    "default": 30,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "display": "number"
                }),
                "time_out": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.50,
                    "max": 20.0,
                    "step": 0.25,
                    "display": "time_out"
                }),
            },
        }

    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("clip_prompt", "word_count")
    FUNCTION = "generate_clip_prompt"
    CATEGORY = "AZ_Nodes"

    def generate_clip_prompt(self, t5_prompt, api_endpoint, word_limit, time_out, opt_recondition=None, prefix_words=None):
        async def check_model_running():
            url = api_endpoint
            timeout = aiohttp.ClientTimeout(total=time_out)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        pass  # We just need to confirm connection
                return True
            except Exception:
                return False

        async def main():
            model_running = await check_model_running()
            if not model_running:
                raise ConnectionError(f"Cannot connect to the LLaMA model at {api_endpoint}")
            if opt_recondition:
                prompt = opt_recondition.format(
                    t5_prompt=t5_prompt,
                    word_limit=word_limit,
                    prefix_words=prefix_words
                )
            else:
                prompt = (
                    f"Please convert the following detailed description into a concise, {word_limit}-word CLIP-style prompt "
                    f"Adhere strictly to the following guidelines:\n"
                    f"- Use short, descriptive phrases (1-3 words each) separated by commas.\n"
                    f"- Focus on key visual elements and concepts from the detailed description.\n"
                    f"- **Preserve important details and avoid losing context(e.g., use 'wearing blue shirt',"
                    f"instead of just 'blue shirt').**\n"
                    f"- **Use precise language to accurately depict attributes (e.g., 'blue-haired woman' instead of "
                    f"'blue woman').**\n"
                    f"- **Avoid ambiguous or generalized terms**\n"
                    f"- Do not include any unnecessary words or long sentences.\n"
                    f"- Ensure each phrase is meaningful and captures important aspects of the scene.\n"
                    f"- The final prompt should not exceed {word_limit} words.\n"
                    f"Provide only the final prompt in the specified format and nothing else.\n\n"
                    f"Here is the detailed description:\n{t5_prompt}"
                )

            url = f"{api_endpoint}/api/generate"
            data = {"model": "llama3.2", "prompt": prompt, "stream": False}

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=data) as response:
                        if response.status == 200:
                            response_text = await response.text()
                            response_data = json.loads(response_text)
                            # Properly parse the 'response' field
                            response_content = response_data.get("response", "")
                            try:
                                # In case 'response' is a JSON-encoded string
                                self.actual_response = json.loads(response_content)
                            except json.JSONDecodeError:
                                # If not, just use the string and strip quotes
                                self.actual_response = response_content.strip('"')
                        else:
                            error_text = await response.text()
                            raise RuntimeError(f"Error from API: {response.status}, {error_text}")
            except Exception as e:
                raise RuntimeError(f"Error during API request: {str(e)}")
            if not opt_recondition and prefix_words:
                self.actual_response = f'{prefix_words} {response_content}'
            # Calculate word count excluding commas and spaces
            cleaned_text = re.sub(r'[,\s]+', ' ', self.actual_response).strip()
            words = cleaned_text.split()
            self.word_count = len(words)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())

        return self.actual_response, self.word_count

    def display(self):
        # Display the generated prompt and word count in the node's UI
        return {
            "type": "markdown",
            "content": f"**Generated CLIP Prompt:**\n```\n{self.actual_response}\n```\n\n**Word Count:** {self.word_count}"
        }
