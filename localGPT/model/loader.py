"""
localGPT/model.py

This module contains the ModelLoader class for loading models and creating
pipelines for text generation.

Classes:
- ModelLoader: A class for loading models and creating text generation pipelines.

Note: The module relies on imports from localGPT and other external libraries.
"""

import logging
import sys
from typing import Any

import torch
from auto_gptq import AutoGPTQForCausalLM
from langchain.llms import HuggingFacePipeline
from llama_cpp import Llama
from torch.cuda import OutOfMemoryError
from transformers import (
    AutoConfig,
    AutoModelForCausalLM,
    AutoTokenizer,
    GenerationConfig,
    LlamaForCausalLM,
    LlamaTokenizer,
    pipeline,
)

from localGPT import DEFAULT_DEVICE_TYPE, DEFAULT_MODEL_REPOSITORY, DEFAULT_MODEL_SAFETENSORS, DEFAULT_MODEL_TYPE


class ModelLoader:
    """
    A class for loading models and creating text generation pipelines.

    Methods:
    - __init__: Initializes the ModelLoader with optional device type, model ID,
      and model basename.
    - load_quantized_model: Loads a quantized model for text generation.
    - load_full_model: Loads a full model for text generation.
    - load_llama_model: Loads a Llama model for text generation.
    - create_pipeline: Creates a text generation pipeline.
    - load_model: Loads the appropriate model based on the configuration.
    """

    def __init__(
        self,
        device_type: str | None,
        model_type: str | None,
        model_repository: str | None,
        model_safetensors: str | None,
        use_triton: bool | None,
    ):
        """
        Initializes the ModelLoader with optional device type, model ID, and
        model basename.

        Args:
        - device_type (str, optional): The device type for model loading.
          Defaults to DEFAULT_DEVICE_TYPE.
        - model_repository (str, optional): The model ID.
          Defaults to DEFAULT_MODEL_REPOSITORY.
        - model_safetensors (str, optional): The model basename.
          Defaults to DEFAULT_MODEL_SAFETENSORS.
        """
        self.device_type = (device_type or DEFAULT_DEVICE_TYPE).lower()
        self.model_type = (model_type or DEFAULT_MODEL_TYPE).lower()
        self.model_repository = model_repository or DEFAULT_MODEL_REPOSITORY
        self.model_safetensors = model_safetensors or DEFAULT_MODEL_SAFETENSORS
        self.use_triton = use_triton or False

    def load_huggingface_model(self):
        """
        Loads a full model for text generation.

        Returns:
        - model: The loaded full model.
        - tokenizer: The tokenizer associated with the model.
        """
        logging.info("Using AutoModelForCausalLM for full models")

        config = AutoConfig.from_pretrained(self.model_repository)
        logging.info(f"Configuration loaded for {self.model_repository}")

        tokenizer = AutoTokenizer.from_pretrained(self.model_repository)
        logging.info(f"Tokenizer loaded for {self.model_repository}")

        kwargs: dict[str, Any] = {
            "low_cpu_mem_usage": True,
            "resume_download": True,
            "trust_remote_code": False,
            # NOTE: Uncomment this line if you encounter CUDA out of memory errors
            # "max_memory": {0: "7GB"},
            # NOTE: According to the Hugging Face documentation, `output_loading_info` is
            # for when you want to return a tuple with the pretrained model and a dictionary
            # containing the loading information.
            # "output_loading_info": True,
        }

        if self.device_type != "cpu":
            kwargs["device_map"] = self.device_type
            # NOTE: This loads at half precision: 32 / 2 = 16
            kwargs["torch_dtype"] = torch.float16

        try:
            model = AutoModelForCausalLM.from_pretrained(self.model_repository, config=config, **kwargs)
        except (OutOfMemoryError,) as e:
            logging.error("Encountered CUDA out of memory error while loading the model.")
            logging.error(str(e))
            sys.exit(1)

        logging.info(f"Model loaded for {self.model_repository}")

        if not isinstance(model, tuple):
            model.tie_weights()
            logging.warning("Model Weights Tied: Effectiveness depends on the specific type of model.")

        return model, tokenizer

    def load_huggingface_llama_model(self):
        """
        Loads a Llama model for text generation.

        Returns:
        - model: The loaded Llama model.
        - tokenizer: The tokenizer associated with the model.
        """
        logging.info("Using LlamaTokenizer")
        # vocab_file (str) — Path to the vocabulary file.
        # NOTE: Path to the tokenizer
        tokenizer = LlamaTokenizer.from_pretrained(self.model_repository)
        logging.info(f"Tokenizer loaded for {self.model_repository}")
        # NOTE: Path to the pytorch bin
        model = LlamaForCausalLM.from_pretrained(self.model_repository)
        logging.info(f"Model loaded for {self.model_repository}")
        return model, tokenizer

    def load_ggml_model(self):
        # TODO: Implement supporting 4, 5, and 8, -bit quant model support
        # NOTE: This method potentially supersedes `load_gptq_model`
        pass

    def load_gptq_model(self):
        """
        Loads a quantized model for text generation.

        Returns:
        - model: The loaded quantized model.
        - tokenizer: The tokenizer associated with the model.
        """
        # NOTE: The code supports all huggingface models that ends with GPTQ and
        # have some variation of .no-act.order or .safetensors in their HF repo.
        logging.info("Using AutoGPTQForCausalLM for quantized models")
        logging.warning("GGML models may supersede GPTQ models in future releases")

        if self.model_safetensors.endswith(".safetensors"):
            split_string = self.model_safetensors.split(".")
            self.model_safetensors = ".".join(split_string[:-1])
            logging.info(f"Stripped {self.model_safetensors}. Moving on.")

        tokenizer = AutoTokenizer.from_pretrained(self.model_repository, use_fast=True)
        logging.info(f"Tokenizer loaded for {self.model_repository}")

        kwargs: dict[str, Any] = {
            "low_cpu_mem_usage": True,
            "resume_download": True,
            "trust_remote_code": False,
            "use_safetensors": True,
            "device_map": "auto",
            "model_safetensors": self.model_safetensors,
            # NOTE: Uncomment this line if you encounter CUDA out of memory errors
            # "max_memory": {0: "7GB"},
            # NOTE: According to the Hugging Face documentation, `output_loading_info` is
            # for when you want to return a tuple with the pretrained model and a dictionary
            # containing the loading information.
            # "output_loading_info": True,
        }

        if self.device_type != "cpu":
            kwargs["use_cuda_fp16"] = True
            kwargs["use_triton"] = self.use_triton
            kwargs["device"] = f"{self.device_type}:0"

        model = AutoGPTQForCausalLM.from_quantized(self.model_repository, **kwargs)
        logging.info(f"Model loaded for {self.model_repository}")

        return model, tokenizer

    @staticmethod
    def create_pipeline(model, tokenizer, generation_config):
        """
        Creates a text generation pipeline.

        Args:
        - model: The model for text generation.
        - tokenizer: The tokenizer associated with the model.
        - generation_config: The generation configuration.

        Returns:
        - pipeline: The created text generation pipeline.
        """
        pipe = pipeline(
            "text-generation",
            model=model,
            tokenizer=tokenizer,
            max_length=512,
            temperature=0,
            top_p=0.95,
            repetition_penalty=1.15,
            generation_config=generation_config,
        )
        return HuggingFacePipeline(pipeline=pipe)

    def load_model(self):
        """
        Loads the appropriate model based on the configuration.

        Returns:
        - local_llm: The loaded local language model (LLM).
        """
        # NOTE: This should be replaced with mapping for smooth extensibility
        if self.model_type.lower() == "huggingface":
            model, tokenizer = self.load_huggingface_model()
        elif self.model_type.lower() == "huggingface-llama":
            model, tokenizer = self.load_huggingface_llama_model()
        elif self.model_type.lower() == "gptq":
            model, tokenizer = self.load_gptq_model()
        elif self.model_type.lower() == "ggml":
            raise NotImplementedError("GGML support is in research and development")
        else:
            raise AttributeError(
                "Unsupported model type given. "
                "Expected one of: "
                "huggingface, "
                "huggingface-llama, "
                "ggml, "
                "gptq"
            )

        # Load configuration from the model to avoid warnings
        generation_config = GenerationConfig.from_pretrained(self.model_repository)
        # see here for details:
        # https://huggingface.co/docs/transformers/main_classes/text_generation#transformers.GenerationConfig.from_pretrained.returns

        # Create a pipeline for text generation
        local_llm = self.create_pipeline(model, tokenizer, generation_config)

        logging.info("Local LLM Loaded")

        return local_llm