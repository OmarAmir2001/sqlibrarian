"""Hugging Face Hub names, resolved once from the token's user.

Imports huggingface_hub only inside the functions, so code that never touches the Hub
doesn't need it installed.
"""

from functools import lru_cache

from sqlibrarian.helpers.config import get_settings


@lru_cache
def hub_user() -> str:
    from huggingface_hub import whoami

    token = get_settings().hf_token
    if not token:
        raise SystemExit("HF_TOKEN is not set: add a write token to .env (or Kaggle secrets)")
    return whoami(token=token)["name"]


def dataset_repo() -> str:
    return f"{hub_user()}/{get_settings().hf_dataset_name}"


def model_repo(run_name: str) -> str:
    """e.g. omar/sqlibrarian-sft-run1"""
    return f"{hub_user()}/{get_settings().hf_model_prefix}-{run_name}"


def dataset_revision(repo: str) -> str:
    """The Hub commit of a dataset: the data version a training run used."""
    from huggingface_hub import HfApi

    return HfApi(token=get_settings().hf_token).dataset_info(repo).sha or "unknown"
