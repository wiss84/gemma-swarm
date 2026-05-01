"""
Gemma Swarm — Token Utilities
=============================
Accurate token counting using Gemma's SentencePiece tokenizer.
Runs fully offline on CPU — zero API calls, zero quota consumed.

Setup (one-time):
    The tokenizer model file (~4MB) must be downloaded once:
        python -c "from agents_utils.token_utils import download_tokenizer; download_tokenizer()"
    This saves tokenizer.model to agents_utils/tokenizer.model.

Fallback:
    If the tokenizer file is missing or fails to load, falls back
    to the chars / 3.2 heuristic automatically — no crashes.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Path to the SentencePiece model file (downloaded once, committed to repo or gitignored)
_TOKENIZER_PATH = Path(__file__).parent / "tokenizer.model"
_CHARS_PER_TOKEN = 3.2   # fallback heuristic

# Module-level cache — loaded once on first use
_sp = None
_sp_load_attempted = False


def _get_sp():
    """
    Lazy-load the SentencePiece model.
    Returns the loaded model or None if unavailable (triggers fallback).
    """
    global _sp, _sp_load_attempted
    if _sp_load_attempted:
        return _sp
    _sp_load_attempted = True

    if not _TOKENIZER_PATH.exists():
        logger.info(
            "[token_utils] tokenizer.model not found — using chars/3.2 estimate. "
            "Run: python -c \"from agents_utils.token_utils import download_tokenizer; download_tokenizer()\""
        )
        return None

    try:
        import sentencepiece as spm
        model = spm.SentencePieceProcessor()
        model.Load(str(_TOKENIZER_PATH))
        logger.info("[token_utils] SentencePiece tokenizer loaded.")
        _sp = model
    except Exception as e:
        logger.warning(f"[token_utils] Failed to load tokenizer: {e} — using estimate.")
    return _sp


def _extract_text(messages: list) -> str:
    """Concatenate all message content into a single string for counting."""
    parts = []
    for m in messages:
        content = m.content
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
        else:
            parts.append(str(content))
    return "\n".join(parts)


def count_tokens(messages: list, model_name: str = "") -> int:
    """
    Count tokens in a list of LangChain messages.

    Uses Gemma's SentencePiece tokenizer if available (accurate),
    otherwise falls back to chars / 3.2 (estimate).
    No API calls. No quota used. Runs in <1ms for typical messages.
    """
    text = _extract_text(messages)

    sp = _get_sp()
    if sp is not None:
        try:
            return max(1, len(sp.EncodeAsIds(text)))
        except Exception as e:
            logger.warning(f"[token_utils] SentencePiece encode failed: {e} — falling back.")

    # Fallback: character estimate
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def count_text_tokens(text: str) -> int:
    """Count tokens in a plain string (no message wrapping needed)."""
    sp = _get_sp()
    if sp is not None:
        try:
            return max(1, len(sp.EncodeAsIds(text)))
        except Exception:
            pass
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


def download_tokenizer():
    """
    One-time download of Gemma's SentencePiece tokenizer model from Hugging Face.
    Saves to agents_utils/tokenizer.model (~4MB).
    All Gemma generations (2, 3, 4) share the same tokenizer vocabulary.

    Run once:
        python -c "from agents_utils.token_utils import download_tokenizer; download_tokenizer()"
    """
    import urllib.request
    import os

    # Direct download from Hugging Face (no auth needed for this file)
    url = "https://huggingface.co/google/gemma-2-2b/resolve/main/tokenizer.model"
    dest = _TOKENIZER_PATH

    print(f"Downloading Gemma tokenizer from Hugging Face...")
    print(f"Destination: {dest}")

    try:
        # urllib shows download progress
        def _progress(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                pct = min(100, downloaded * 100 // total_size)
                print(f"\r  {pct}% ({downloaded // 1024}KB / {total_size // 1024}KB)", end="", flush=True)

        urllib.request.urlretrieve(url, dest, reporthook=_progress)
        print(f"\nDone. Tokenizer saved to: {dest}")

        # Quick sanity check
        import sentencepiece as spm
        model = spm.SentencePieceProcessor()
        model.Load(str(dest))
        test_ids = model.EncodeAsIds("Hello, world!")
        print(f"Sanity check passed — 'Hello, world!' = {len(test_ids)} tokens.")

    except Exception as e:
        print(f"Download failed: {e}")
        if dest.exists():
            os.remove(dest)
        raise


if __name__ == "__main__":
    download_tokenizer()
