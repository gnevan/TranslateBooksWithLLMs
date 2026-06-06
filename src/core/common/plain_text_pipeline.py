"""
Plain-text translation pipeline used by Plain Text Mode.

Skips placeholder preservation and HTML chunking entirely. Paragraphs are
joined, chunked by token count, translated with has_placeholders=False, then
re-split on the paragraph separator.

Used by the EPUB and DOCX adapters when prompt_options['plain_text_mode'] is True.
"""
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.text_processor import split_text_into_chunks
from src.core.translator import generate_translation_request
from src.core.post_processor import clean_translated_text
from src.core.epub.translation_metrics import TranslationMetrics
from src.core.common.parallel import iter_ordered_concurrent
from src.core.llm.exceptions import RateLimitError


PARAGRAPH_SEPARATOR = "\n\n"
_RESPLIT_REGEX = re.compile(r"\n{2,}")


def _split_translated_back_to_paragraphs(translated_text: str) -> List[str]:
    """Split a translated blob into paragraphs (tolerates 2+ newlines)."""
    return [p.strip() for p in _RESPLIT_REGEX.split(translated_text) if p.strip()]


def _reconcile_paragraph_counts(
    translated_paragraphs: List[str],
    expected_count: int,
) -> List[str]:
    """
    Best-effort alignment when the LLM merged or split paragraphs.

    - translated == expected: return as-is
    - translated < expected: pad with empty strings
    - translated > expected: merge surplus into the last slot
    """
    got = len(translated_paragraphs)
    if got == expected_count:
        return translated_paragraphs
    if got < expected_count:
        return translated_paragraphs + [""] * (expected_count - got)
    head = translated_paragraphs[:expected_count - 1]
    tail = " ".join(translated_paragraphs[expected_count - 1:])
    return head + [tail]


async def translate_paragraphs_plain(
    paragraphs: List[str],
    source_language: str,
    target_language: str,
    model_name: str,
    llm_client: Any,
    max_tokens_per_chunk: int,
    log_callback: Optional[Callable] = None,
    stats_callback: Optional[Callable] = None,
    context_manager: Optional[Any] = None,
    check_interruption_callback: Optional[Callable] = None,
    prompt_options: Optional[Dict] = None,
    parallel_workers: int = 1,
) -> Tuple[List[str], TranslationMetrics, bool]:
    """
    Translate a list of plain-text paragraphs without placeholder preservation.

    Args:
        paragraphs: source paragraphs (one string per block)
        source_language, target_language: language names
        model_name, llm_client: LLM config
        max_tokens_per_chunk: chunking budget
        log_callback, stats_callback: callbacks (stats_callback receives
            file-local stats via TranslationMetrics.to_dict(); callers that
            aggregate across files are responsible for adding their global
            offset to completed_chunks).
        context_manager: AdaptiveContextManager (Ollama)
        check_interruption_callback: returns True to abort
        prompt_options: prompt customization (text_cleanup, glossary, etc.)
        parallel_workers: number of chunks translated concurrently (already
            resolved against the provider by the caller). When 1, behavior is
            identical to the legacy sequential loop, including previous-chunk
            context chaining; > 1 drops that chaining.

    Returns:
        (translated_paragraphs, stats, was_interrupted)
    """
    stats = TranslationMetrics()

    source = list(paragraphs)
    if not source or all(not (p or "").strip() for p in source):
        if stats_callback:
            stats_callback(stats.to_dict())
        return source, stats, False

    full_text = PARAGRAPH_SEPARATOR.join(source)

    chunks = split_text_into_chunks(
        text=full_text,
        max_tokens_per_chunk=max_tokens_per_chunk,
    )

    stats.total_chunks = len(chunks)
    if stats_callback:
        stats_callback(stats.to_dict())

    workers = max(1, int(parallel_workers))
    sequential = workers == 1

    # Index-addressed results so out-of-order completion still reassembles in
    # source order.
    translated_parts: List[Optional[str]] = [None] * len(chunks)
    previous_translation_context = ""

    async def _translate_chunk(i):
        """Translate one chunk. Reads previous_translation_context only in
        sequential mode (parallel runs have no stable previous chunk)."""
        main_content = chunks[i].get('main_content', '')
        if not main_content.strip():
            return ('empty', main_content)
        translated = await generate_translation_request(
            main_content=main_content,
            context_before=chunks[i].get('context_before', ''),
            context_after=chunks[i].get('context_after', ''),
            previous_translation_context=(previous_translation_context if sequential else ""),
            source_language=source_language,
            target_language=target_language,
            model=model_name,
            llm_client=llm_client,
            log_callback=log_callback,
            has_placeholders=False,
            prompt_options=prompt_options,
            context_manager=context_manager,
            placeholder_format=None,
        )
        return ('done', translated)

    def _fill_remaining_with_source():
        for j in range(len(chunks)):
            if translated_parts[j] is None:
                translated_parts[j] = chunks[j].get('main_content', '')

    pending = list(range(len(chunks)))
    rate_limit_error = None
    processed = 0

    # Continuous concurrency with in-order delivery (see iter_ordered_concurrent).
    async for i, result in iter_ordered_concurrent(
        pending, workers, _translate_chunk, check_interruption_callback
    ):
        main_content = chunks[i].get('main_content', '')

        if isinstance(result, RateLimitError):
            rate_limit_error = result
            break

        if isinstance(result, Exception):
            if log_callback:
                log_callback(
                    "plain_text_chunk_failed",
                    f"Chunk {i + 1}/{len(chunks)} failed ({result}) - keeping original text"
                )
            translated_parts[i] = main_content
            stats.failed_chunks += 1
        else:
            kind, value = result
            if kind == 'empty':
                translated_parts[i] = value
                stats.successful_first_try += 1
            elif value is None:
                if log_callback:
                    log_callback(
                        "plain_text_chunk_failed",
                        f"Chunk {i + 1}/{len(chunks)} failed - keeping original text"
                    )
                translated_parts[i] = main_content
                stats.failed_chunks += 1
            else:
                cleaned = clean_translated_text(value)
                translated_parts[i] = cleaned
                stats.successful_first_try += 1
                if sequential:
                    words = cleaned.split()
                    previous_translation_context = (
                        " ".join(words[-25:]) if len(words) > 25 else cleaned
                    )

        stats.record_processed()
        if stats_callback:
            stats_callback(stats.to_dict())
        processed += 1

    if rate_limit_error is not None:
        # Keep source text for everything not yet translated, then propagate to
        # trigger the caller's pause/resume handling.
        _fill_remaining_with_source()
        raise rate_limit_error

    # Interruption: the scheduler stopped launching new chunks; keep source text
    # for the uncommitted tail and report the interruption.
    if processed < len(chunks) and check_interruption_callback and check_interruption_callback():
        if log_callback:
            log_callback(
                "plain_text_translation_interrupted",
                f"⏸️ Plain-text translation interrupted at chunk {processed + 1}/{len(chunks)}"
            )
        _fill_remaining_with_source()
        return _finalize([p if p is not None else "" for p in translated_parts], source), stats, True

    # Any None left (shouldn't happen) falls back to empty string.
    safe_parts = [p if p is not None else "" for p in translated_parts]
    return _finalize(safe_parts, source), stats, False


def _finalize(translated_parts: List[str], source_paragraphs: List[str]) -> List[str]:
    """Reassemble translated chunks into a paragraph list aligned with the source count."""
    joined = PARAGRAPH_SEPARATOR.join(translated_parts)
    parts = _split_translated_back_to_paragraphs(joined)
    return _reconcile_paragraph_counts(parts, len(source_paragraphs))
