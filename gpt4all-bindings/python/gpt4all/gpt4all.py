"""
Python only API for running all GPT4All models.
"""
from __future__ import annotations

import os
import re
import sys
import time
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Literal, overload

import requests
from requests.exceptions import ChunkedEncodingError
from tqdm import tqdm
from urllib3.exceptions import IncompleteRead, ProtocolError

from . import _pyllmodel
from ._pyllmodel import EmbedResult as EmbedResult

if TYPE_CHECKING:
    from typing import TypeAlias

# TODO: move to config
DEFAULT_MODEL_DIRECTORY = Path.home() / ".cache" / "gpt4all"

DEFAULT_PROMPT_TEMPLATE = "### Human:\n{0}\n\n### Assistant:\n"

ConfigType: TypeAlias = 'dict[str, str]'
MessageType: TypeAlias = 'dict[str, str]'


class Embed4All:
    """
    Python class that handles embeddings for GPT4All.
    """

    MIN_DIMENSIONALITY = 64

    def __init__(self, model_name: str | None = None, n_threads: int | None = None, **kwargs):
        """
        Constructor

        Args:
            n_threads: number of CPU threads used by GPT4All. Default is None, then the number of threads are determined automatically.
        """
        if model_name is None:
            model_name = 'all-MiniLM-L6-v2.gguf2.f16.gguf'
        self.gpt4all = GPT4All(model_name, n_threads=n_threads, **kwargs)

    # return_dict=False
    @overload
    def embed(
        self, text: str, *, prefix: str | None = ..., dimensionality: int | None = ..., long_text_mode: str = ...,
        return_dict: Literal[False] = ..., atlas: bool = ...,
    ) -> list[float]: ...
    @overload
    def embed(
        self, text: list[str], *, prefix: str | None = ..., dimensionality: int | None = ..., long_text_mode: str = ...,
        return_dict: Literal[False] = ..., atlas: bool = ...,
    ) -> list[list[float]]: ...
    @overload
    def embed(
        self, text: str | list[str], *, prefix: str | None = ..., dimensionality: int | None = ...,
        long_text_mode: str = ..., return_dict: Literal[False] = ..., atlas: bool = ...,
    ) -> list[Any]: ...

    # return_dict=True
    @overload
    def embed(
        self, text: str, *, prefix: str | None = ..., dimensionality: int | None = ..., long_text_mode: str = ...,
        return_dict: Literal[True], atlas: bool = ...,
    ) -> EmbedResult[list[float]]: ...
    @overload
    def embed(
        self, text: list[str], *, prefix: str | None = ..., dimensionality: int | None = ..., long_text_mode: str = ...,
        return_dict: Literal[True], atlas: bool = ...,
    ) -> EmbedResult[list[list[float]]]: ...
    @overload
    def embed(
        self, text: str | list[str], *, prefix: str | None = ..., dimensionality: int | None = ...,
        long_text_mode: str = ..., return_dict: Literal[True], atlas: bool = ...,
    ) -> EmbedResult[list[Any]]: ...

    # return type unknown
    @overload
    def embed(
        self, text: str | list[str], *, prefix: str | None = ..., dimensionality: int | None = ...,
        long_text_mode: str = ..., return_dict: bool = ..., atlas: bool = ...,
    ) -> Any: ...

    def embed(
        self, text: str | list[str], *, prefix: str | None = None, dimensionality: int | None = None,
        long_text_mode: str = "mean", return_dict: bool = False, atlas: bool = False,
    ) -> Any:
        """
        Generate one or more embeddings.

        Args:
            text: A text or list of texts to generate embeddings for.
            prefix: The model-specific prefix representing the embedding task, without the trailing colon. For Nomic
                Embed, this can be `search_query`, `search_document`, `classification`, or `clustering`. Defaults to
                `search_document` or equivalent if known; otherwise, you must explicitly pass a prefix or an empty
                string if none applies.
            dimensionality: The embedding dimension, for use with Matryoshka-capable models. Defaults to full-size.
            long_text_mode: How to handle texts longer than the model can accept. One of `mean` or `truncate`.
            return_dict: Return the result as a dict that includes the number of prompt tokens processed.
            atlas: Try to be fully compatible with the Atlas API. Currently, this means texts longer than 8192 tokens
                with long_text_mode="mean" will raise an error. Disabled by default.

        Returns:
            With return_dict=False, an embedding or list of embeddings of your text(s).
            With return_dict=True, a dict with keys 'embeddings' and 'n_prompt_tokens'.
        """
        if dimensionality is None:
            dimensionality = -1
        else:
            if dimensionality <= 0:
                raise ValueError(f'Dimensionality must be None or a positive integer, got {dimensionality}')
            if dimensionality < self.MIN_DIMENSIONALITY:
                warnings.warn(
                    f'Dimensionality {dimensionality} is less than the suggested minimum of {self.MIN_DIMENSIONALITY}.'
                    ' Performance may be degraded.'
                )
        try:
            do_mean = {"mean": True, "truncate": False}[long_text_mode]
        except KeyError:
            raise ValueError(f"Long text mode must be one of 'mean' or 'truncate', got {long_text_mode!r}")
        result = self.gpt4all.model.generate_embeddings(text, prefix, dimensionality, do_mean, atlas)
        return result if return_dict else result['embeddings']


class GPT4All:
    """
    Python class that handles instantiation, downloading, generation and chat with GPT4All models.
    """

    def __init__(
        self,
        model_name: str,
        model_path: str | os.PathLike[str] | None = None,
        model_type: str | None = None,
        allow_download: bool = True,
        n_threads: int | None = None,
        device: str | None = "cpu",
        n_ctx: int = 2048,
        ngl: int = 100,
        verbose: bool = False,
    ):
        """
        Constructor

        Args:
            model_name: Name of GPT4All or custom model. Including ".gguf" file extension is optional but encouraged.
            model_path: Path to directory containing model file or, if file does not exist, where to download model.
                Default is None, in which case models will be stored in `~/.cache/gpt4all/`.
            model_type: Model architecture. This argument currently does not have any functionality and is just used as
                descriptive identifier for user. Default is None.
            allow_download: Allow API to download models from gpt4all.io. Default is True.
            n_threads: number of CPU threads used by GPT4All. Default is None, then the number of threads are determined automatically.
            device: The processing unit on which the GPT4All model will run. It can be set to:
                - "cpu": Model will run on the central processing unit.
                - "gpu": Model will run on the best available graphics processing unit, irrespective of its vendor.
                - "amd", "nvidia", "intel": Model will run on the best available GPU from the specified vendor.
                Alternatively, a specific GPU name can also be provided, and the model will run on the GPU that matches the name if it's available.
                Default is "cpu".

                Note: If a selected GPU device does not have sufficient RAM to accommodate the model, an error will be thrown, and the GPT4All instance will be rendered invalid. It's advised to ensure the device has enough memory before initiating the model.
            n_ctx: Maximum size of context window
            ngl: Number of GPU layers to use (Vulkan)
            verbose: If True, print debug messages.
        """
        self.model_type = model_type
        # Retrieve model and download if allowed
        self.config: ConfigType = self.retrieve_model(model_name, model_path=model_path, allow_download=allow_download, verbose=verbose)
        self.model = _pyllmodel.LLModel(self.config["path"], n_ctx, ngl)
        if device is not None and device != "cpu":
            self.model.init_gpu(device)
        self.model.load_model()
        # Set n_threads
        if n_threads is not None:
            self.model.set_thread_count(n_threads)

        self._history: list[MessageType] | None = None
        self._current_prompt_template: str = "{0}"

    @property
    def current_chat_session(self) -> list[MessageType] | None:
        return None if self._history is None else list(self._history)

    @staticmethod
    def list_models() -> list[ConfigType]:
        """
        Fetch model list from https://gpt4all.io/models/models3.json.

        Returns:
            Model list in JSON format.
        """
        resp = requests.get("https://gpt4all.io/models/models3.json")
        if resp.status_code != 200:
            raise ValueError(f'Request failed: HTTP {resp.status_code} {resp.reason}')
        return resp.json()

    @classmethod
    def retrieve_model(
        cls,
        model_name: str,
        model_path: str | os.PathLike[str] | None = None,
        allow_download: bool = True,
        verbose: bool = False,
    ) -> ConfigType:
        """
        Find model file, and if it doesn't exist, download the model.

        Args:
            model_name: Name of model.
            model_path: Path to find model. Default is None in which case path is set to
                ~/.cache/gpt4all/.
            allow_download: Allow API to download model from gpt4all.io. Default is True.
            verbose: If True (default), print debug messages.

        Returns:
            Model config.
        """

        model_filename = append_extension_if_missing(model_name)

        # get the config for the model
        config: ConfigType = {}
        if allow_download:
            available_models = cls.list_models()

            for m in available_models:
                if model_filename == m["filename"]:
                    tmpl = m.get("promptTemplate", DEFAULT_PROMPT_TEMPLATE)
                    # change to Python-style formatting
                    m["promptTemplate"] = tmpl.replace("%1", "{0}", 1).replace("%2", "{1}", 1)
                    config.update(m)
                    break

        # Validate download directory
        if model_path is None:
            try:
                os.makedirs(DEFAULT_MODEL_DIRECTORY, exist_ok=True)
            except OSError as e:
                raise RuntimeError("Failed to create model download directory") from e
            model_path = DEFAULT_MODEL_DIRECTORY
        else:
            model_path = Path(model_path)

        if not model_path.exists():
            raise FileNotFoundError(f"Model directory does not exist: {model_path!r}")

        model_dest = model_path / model_filename
        if model_dest.exists():
            config["path"] = str(model_dest)
            if verbose:
                print(f"Found model file at {str(model_dest)!r}", file=sys.stderr)
        elif allow_download:
            # If model file does not exist, download
            config["path"] = str(cls.download_model(model_filename, model_path, verbose=verbose, url=config.get("url")))
        else:
            raise FileNotFoundError(f"Model file does not exist: {model_dest!r}")

        return config

    @staticmethod
    def download_model(
        model_filename: str,
        model_path: str | os.PathLike[str],
        verbose: bool = True,
        url: str | None = None,
    ) -> str | os.PathLike[str]:
        """
        Download model from https://gpt4all.io.

        Args:
            model_filename: Filename of model (with .gguf extension).
            model_path: Path to download model to.
            verbose: If True (default), print debug messages.
            url: the models remote url (e.g. may be hosted on HF)

        Returns:
            Model file destination.
        """

        # Download model
        download_path = Path(model_path) / model_filename
        if url is None:
            url = f"https://gpt4all.io/models/gguf/{model_filename}"

        def make_request(offset=None):
            headers = {}
            if offset:
                print(f"\nDownload interrupted, resuming from byte position {offset}", file=sys.stderr)
                headers['Range'] = f'bytes={offset}-'  # resume incomplete response
            response = requests.get(url, stream=True, headers=headers)
            if response.status_code not in (200, 206):
                raise ValueError(f'Request failed: HTTP {response.status_code} {response.reason}')
            if offset and (response.status_code != 206 or str(offset) not in response.headers.get('Content-Range', '')):
                raise ValueError('Connection was interrupted and server does not support range requests')
            return response

        response = make_request()

        total_size_in_bytes = int(response.headers.get("content-length", 0))
        block_size = 2**20  # 1 MB

        with open(download_path, "wb") as file, \
                tqdm(total=total_size_in_bytes, unit="iB", unit_scale=True) as progress_bar:
            try:
                while True:
                    last_progress = progress_bar.n
                    try:
                        for data in response.iter_content(block_size):
                            file.write(data)
                            progress_bar.update(len(data))
                    except ChunkedEncodingError as cee:
                        if cee.args and isinstance(pe := cee.args[0], ProtocolError):
                            if len(pe.args) >= 2 and isinstance(ir := pe.args[1], IncompleteRead):
                                assert progress_bar.n <= ir.partial  # urllib3 may be ahead of us but never behind
                                # the socket was closed during a read - retry
                                response = make_request(progress_bar.n)
                                continue
                        raise
                    if total_size_in_bytes != 0 and progress_bar.n < total_size_in_bytes:
                        if progress_bar.n == last_progress:
                            raise RuntimeError('Download not making progress, aborting.')
                        # server closed connection prematurely - retry
                        response = make_request(progress_bar.n)
                        continue
                    break
            except Exception:
                if verbose:
                    print("Cleaning up the interrupted download...", file=sys.stderr)
                try:
                    os.remove(download_path)
                except OSError:
                    pass
                raise

        if os.name == 'nt':
            time.sleep(2)  # Sleep for a little bit so Windows can remove file lock

        if verbose:
            print(f"Model downloaded to {str(download_path)!r}", file=sys.stderr)
        return download_path

    @overload
    def generate(
        self, prompt: str, *, max_tokens: int = ..., temp: float = ..., top_k: int = ..., top_p: float = ...,
        min_p: float = ..., repeat_penalty: float = ..., repeat_last_n: int = ..., n_batch: int = ...,
        n_predict: int | None = ..., streaming: Literal[False] = ..., callback: _pyllmodel.ResponseCallbackType = ...,
    ) -> str: ...
    @overload
    def generate(
        self, prompt: str, *, max_tokens: int = ..., temp: float = ..., top_k: int = ..., top_p: float = ...,
        min_p: float = ..., repeat_penalty: float = ..., repeat_last_n: int = ..., n_batch: int = ...,
        n_predict: int | None = ..., streaming: Literal[True], callback: _pyllmodel.ResponseCallbackType = ...,
    ) -> Iterable[str]: ...
    @overload
    def generate(
        self, prompt: str, *, max_tokens: int = ..., temp: float = ..., top_k: int = ..., top_p: float = ...,
        min_p: float = ..., repeat_penalty: float = ..., repeat_last_n: int = ..., n_batch: int = ...,
        n_predict: int | None = ..., streaming: bool, callback: _pyllmodel.ResponseCallbackType = ...,
    ) -> Any: ...

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 200,
        temp: float = 0.7,
        top_k: int = 40,
        top_p: float = 0.4,
        min_p: float = 0.0,
        repeat_penalty: float = 1.18,
        repeat_last_n: int = 64,
        n_batch: int = 8,
        n_predict: int | None = None,
        streaming: bool = False,
        callback: _pyllmodel.ResponseCallbackType = _pyllmodel.empty_response_callback,
    ) -> Any:
        """
        Generate outputs from any GPT4All model.

        Args:
            prompt: The prompt for the model the complete.
            max_tokens: The maximum number of tokens to generate.
            temp: The model temperature. Larger values increase creativity but decrease factuality.
            top_k: Randomly sample from the top_k most likely tokens at each generation step. Set this to 1 for greedy decoding.
            top_p: Randomly sample at each generation step from the top most likely tokens whose probabilities add up to top_p.
            min_p: Randomly sample at each generation step from the top most likely tokens whose probabilities are at least min_p.
            repeat_penalty: Penalize the model for repetition. Higher values result in less repetition.
            repeat_last_n: How far in the models generation history to apply the repeat penalty.
            n_batch: Number of prompt tokens processed in parallel. Larger values decrease latency but increase resource requirements.
            n_predict: Equivalent to max_tokens, exists for backwards compatibility.
            streaming: If True, this method will instead return a generator that yields tokens as the model generates them.
            callback: A function with arguments token_id:int and response:str, which receives the tokens from the model as they are generated and stops the generation by returning False.

        Returns:
            Either the entire completion or a generator that yields the completion token by token.
        """

        # Preparing the model request
        generate_kwargs: dict[str, Any] = dict(
            temp=temp,
            top_k=top_k,
            top_p=top_p,
            min_p=min_p,
            repeat_penalty=repeat_penalty,
            repeat_last_n=repeat_last_n,
            n_batch=n_batch,
            n_predict=n_predict if n_predict is not None else max_tokens,
        )

        if self._history is not None:
            # check if there is only one message, i.e. system prompt:
            reset = len(self._history) == 1
            generate_kwargs["reset_context"] = reset
            self._history.append({"role": "user", "content": prompt})

            fct_func = self._format_chat_prompt_template.__func__  # type: ignore[attr-defined]
            if fct_func is GPT4All._format_chat_prompt_template:
                if reset:
                    # ingest system prompt
                    self.model.prompt_model(self._history[0]["content"], "%1",
                                            _pyllmodel.empty_response_callback,
                                            n_batch=n_batch, n_predict=0, special=True)
                prompt_template = self._current_prompt_template.format("%1", "%2")
            else:
                warnings.warn(
                    "_format_chat_prompt_template is deprecated. Please use a chat session with a prompt template.",
                    DeprecationWarning,
                )
                # special tokens won't be processed
                prompt = self._format_chat_prompt_template(
                    self._history[-1:],
                    self._history[0]["content"] if reset else "",
                )
                prompt_template = "%1"
        else:
            prompt_template = "%1"
            generate_kwargs["reset_context"] = True

        # Prepare the callback, process the model response
        output_collector: list[MessageType]
        output_collector = [
            {"content": ""}
        ]  # placeholder for the self._history if chat session is not activated

        if self._history is not None:
            self._history.append({"role": "assistant", "content": ""})
            output_collector = self._history

        def _callback_wrapper(
            callback: _pyllmodel.ResponseCallbackType,
            output_collector: list[MessageType],
        ) -> _pyllmodel.ResponseCallbackType:
            def _callback(token_id: int, response: str) -> bool:
                nonlocal callback, output_collector

                output_collector[-1]["content"] += response

                return callback(token_id, response)

            return _callback

        # Send the request to the model
        if streaming:
            return self.model.prompt_model_streaming(
                prompt,
                prompt_template,
                _callback_wrapper(callback, output_collector),
                **generate_kwargs,
            )

        self.model.prompt_model(
            prompt,
            prompt_template,
            _callback_wrapper(callback, output_collector),
            **generate_kwargs,
        )

        return output_collector[-1]["content"]

    @contextmanager
    def chat_session(
        self,
        system_prompt: str | None = None,
        prompt_template: str | None = None,
    ):
        """
        Context manager to hold an inference optimized chat session with a GPT4All model.

        Args:
            system_prompt: An initial instruction for the model.
            prompt_template: Template for the prompts with {0} being replaced by the user message.
        """

        if system_prompt is None:
            system_prompt = self.config.get("systemPrompt", "")

        if prompt_template is None:
            if (tmpl := self.config.get("promptTemplate")) is None:
                warnings.warn("Use of a sideloaded model or allow_download=False without specifying a prompt template "
                              "is deprecated. Defaulting to Alpaca.", DeprecationWarning)
                tmpl = DEFAULT_PROMPT_TEMPLATE
            prompt_template = tmpl

        if re.search(r"%1(?![0-9])", prompt_template):
            raise ValueError("Prompt template containing a literal '%1' is not supported. For a prompt "
                             "placeholder, please use '{0}' instead.")

        self._history = [{"role": "system", "content": system_prompt}]
        self._current_prompt_template = prompt_template
        try:
            yield self
        finally:
            self._history = None
            self._current_prompt_template = "{0}"

    def _format_chat_prompt_template(
        self,
        messages: list[MessageType],
        default_prompt_header: str = "",
        default_prompt_footer: str = "",
    ) -> str:
        """
        Helper method for building a prompt from list of messages using the self._current_prompt_template as a template for each message.

        Args:
            messages:  List of dictionaries. Each dictionary should have a "role" key
                with value of "system", "assistant", or "user" and a "content" key with a
                string value. Messages are organized such that "system" messages are at top of prompt,
                and "user" and "assistant" messages are displayed in order. Assistant messages get formatted as
                "Response: {content}".

        Returns:
            Formatted prompt.
        """

        full_prompt = default_prompt_header + "\n\n" if default_prompt_header != "" else ""

        for message in messages:
            if message["role"] == "user":
                user_message = self._current_prompt_template.format(message["content"])
                full_prompt += user_message
            if message["role"] == "assistant":
                assistant_message = message["content"] + "\n"
                full_prompt += assistant_message

        full_prompt += "\n\n" + default_prompt_footer if default_prompt_footer != "" else ""

        return full_prompt


def append_extension_if_missing(model_name):
    if not model_name.endswith((".bin", ".gguf")):
        model_name += ".gguf"
    return model_name
