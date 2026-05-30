"""_yake — stdlib-only YAKE-style keyword extractor (Campos et al. 2020).

Reimplemented from the paper: "YAKE! Collection-independent Automatic Keyword
Extraction" (Campos, Mangaravite, Jatowt, Jorge, Nunes, 2020).

5-feature score: TCase, TPos, TFreq, TRel, TDif.
No third-party imports — uses only math, re, collections.defaultdict.

Public API:
    extract_keywords(text: str) -> list[str]   — up to 8 candidates

Used by:
    1. Intent classifier fallback (when classify_intent workload times out)
    2. FTS query enrichment (OR keywords into recall query)
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "both",
    "each", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "just",
    "because", "but", "and", "or", "if", "while", "about", "up", "down",
    "that", "this", "these", "those", "it", "its", "i", "me", "my", "we",
    "our", "you", "your", "he", "him", "his", "she", "her", "they", "them",
    "their", "what", "which", "who", "whom", "whose",
})

_SENTENCE_RE = re.compile(r"[.!?;\n]+")
_WORD_RE = re.compile(r"\b[a-zA-Z0-9][\w\-]*[a-zA-Z0-9]\b|\b[a-zA-Z0-9]\b")
_UPPER_RE = re.compile(r"[A-Z]")
_DIGIT_RE = re.compile(r"\d")


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _segment_sentences(text: str) -> list[str]:
    """Split text into sentences on punctuation boundaries."""
    parts = _SENTENCE_RE.split(text)
    return [s.strip() for s in parts if s.strip()]


def _tokenize_sentences(text: str) -> tuple[list[list[str]], list[str]]:
    """Return (sentence_tokens, flat_tokens)."""
    sentences = _segment_sentences(text)
    sent_tokens: list[list[str]] = []
    flat: list[str] = []
    for sent in sentences:
        tokens = [m.group(0).lower() for m in _WORD_RE.finditer(sent)]
        sent_tokens.append(tokens)
        flat.extend(tokens)
    return sent_tokens, flat


def _is_uppercase(word: str) -> bool:
    """True if word is ALL UPPERCASE (not just first-letter capitalized)."""
    return word == word.upper() and _UPPER_RE.search(word) is not None


def _is_title_case(word: str) -> bool:
    """True if word starts with uppercase and has at least one lowercase."""
    return len(word) > 1 and word[0].isupper() and word[1:].lower() != word[1:]


def _is_stopword(word: str) -> bool:
    return word.lower() in _STOP_WORDS


# ─────────────────────────────────────────────────────────────────────────────
# 5-feature YAKE scoring
# ─────────────────────────────────────────────────────────────────────────────


def _tcase(word: str, original: str) -> float:
    """TCase feature: penalize case deviations.

    Upper/title case → lower score (more likely keyword).
    All-caps → penalized (likely acronym, but also noise).
    """
    if not original or not any(c.isalpha() for c in original):
        return 1.0
    if _is_uppercase(original):
        return 0.5  # acronym — moderate signal
    if _is_title_case(original):
        return 0.7  # title case — likely proper noun
    return 1.0  # lowercase — no case signal


def _tpos(word: str, sentence_positions: dict[str, list[int]], nsent: int) -> float:
    """TPos feature: keywords tend to appear early (first sentences).

    Returns a score in (0, 1]. Lower = more keyword-like.
    """
    positions = sentence_positions.get(word, [])
    if not positions or nsent <= 0:
        return 1.0
    # Normalized position: 0 = first sentence, 1 = last
    avg_pos = sum(positions) / len(positions)
    norm = avg_pos / max(nsent - 1, 1)
    # Sigmoid-ish curve: strong bias toward early positions
    return 0.5 + 0.5 * norm


def _tfreq(word: str, word_freq: dict[str, int], total: int) -> float:
    """TFreq feature: normalized frequency. Higher freq → lower score.

    We use mean+stdev normalization (Campos et al. Eq. 3).
    """
    freq = word_freq.get(word, 0)
    if total <= 0 or freq <= 0:
        return 1.0
    mean_f = total / max(len(word_freq), 1)
    # Stdev
    var = sum((v - mean_f) ** 2 for v in word_freq.values()) / max(len(word_freq), 1)
    std_f = math.sqrt(var) if var > 0 else 1.0
    # Normalized
    z = (freq - mean_f) / std_f if std_f > 0 else 0.0
    # Clamp to avoid negative scores
    return max(0.0, 1.0 - 0.5 * z)


def _trel(word: str, context_words: set[str], word_freq: dict[str, int]) -> float:
    """TRel feature: relatedness to context (sentence-level co-occurrence).

    Higher co-occurrence → lower score (more keyword-like).
    """
    if not context_words:
        return 1.0
    # Count how many context words co-occur with this word
    co_count = sum(1 for w in context_words if w != word and word_freq.get(w, 0) > 0)
    total_ctx = len(context_words)
    if total_ctx <= 0:
        return 1.0
    ratio = co_count / total_ctx
    return max(0.1, 1.0 - ratio)


def _tdif(word: str, sentence_set: frozenset[frozenset[str]], all_sentences: list[list[str]]) -> float:
    """TDif feature: discriminative — how uniquely this word appears in
    specific sentences vs spread across many.

    Words in fewer distinct sentences → lower score (more specific = keyword).
    """
    if not all_sentences:
        return 1.0
    # Count distinct sentences containing this word
    sent_count = sum(1 for sent in all_sentences if word in sent)
    nsent = len(all_sentences)
    if nsent <= 0:
        return 1.0
    # Higher spread = less discriminative
    return max(0.1, sent_count / nsent)


# ─────────────────────────────────────────────────────────────────────────────
# N-gram generation and scoring
# ─────────────────────────────────────────────────────────────────────────────


def _build_word_stats(
    text: str,
) -> tuple[
    list[list[str]],       # sentence_tokens
    list[str],             # flat_tokens
    dict[str, int],        # word_freq
    dict[str, list[int]],  # word → sentence positions
    dict[str, str],        # lowercase → original casing
]:
    """Build all statistics needed for scoring."""
    sentences = _segment_sentences(text)
    sent_tokens: list[list[str]] = []
    flat: list[str] = []
    word_freq: dict[str, int] = defaultdict(int)
    sentence_positions: dict[str, list[int]] = defaultdict(list)
    casing: dict[str, str] = {}

    for idx, sent in enumerate(sentences):
        tokens_raw = _WORD_RE.finditer(sent)
        sent_tok: list[str] = []
        for m in tokens_raw:
            original = m.group(0)
            lower = original.lower()
            sent_tok.append(lower)
            flat.append(lower)
            word_freq[lower] += 1
            sentence_positions[lower].append(idx)
            casing[lower] = original
        sent_tokens.append(sent_tok)

    return sent_tokens, flat, dict(word_freq), dict(sentence_positions), casing


def _score_word(
    word: str,
    original: str,
    word_freq: dict[str, int],
    sentence_positions: dict[str, list[int]],
    nsent: int,
    total_words: int,
    context_words: set[str],
    all_sentences: list[list[str]],
) -> float:
    """Compute the YAKE score for a single word. Lower = more keyword-like."""
    t_case = _tcase(word, original)
    t_pos = _tpos(word, sentence_positions, nsent)
    t_freq = _tfreq(word, word_freq, total_words)
    t_rel = _trel(word, context_words, word_freq)

    # TDif: build sentence set on the fly
    sentence_set = frozenset(frozenset(s) for s in all_sentences)
    t_dif = _tdif(word, sentence_set, all_sentences)

    # YAKE combines features multiplicatively (Campos et al. Eq. 5)
    # Score = (TPos * TRel) / (TCase + (TFreq / TCase) + (TDif / TCase))
    denom = t_case + (t_freq / max(t_case, 0.01)) + (t_dif / max(t_case, 0.01))
    score = (t_pos * t_rel) / max(denom, 0.001)
    return max(score, 0.0001)


def _generate_ngrams(tokens: list[str], max_n: int = 3) -> list[tuple[str, ...]]:
    """Generate 1-gram to max_n-gram sequences, skipping stopwords at edges."""
    ngrams: list[tuple[str, ...]] = []
    n = len(tokens)
    for i in range(n):
        if _is_stopword(tokens[i]):
            continue
        # 1-gram
        ngrams.append((tokens[i],))
        # 2-gram, 3-gram
        for size in range(2, max_n + 1):
            if i + size > n:
                break
            gram = tokens[i:i + size]
            # Skip if any interior token is a stopword
            if any(_is_stopword(t) for t in gram[1:-1]):
                continue
            # Skip if first or last is a stopword
            if _is_stopword(gram[0]) or _is_stopword(gram[-1]):
                continue
            ngrams.append(tuple(gram))
    return ngrams


def _score_ngram(
    ngram: tuple[str, ...],
    word_scores: dict[str, float],
) -> float:
    """Score an n-gram: geometric mean of component word scores.

    Lower = more keyword-like. Skip if any word is missing from scores.
    """
    scores = []
    for w in ngram:
        s = word_scores.get(w)
        if s is None:
            return 1.0  # can't score → penalize
        scores.append(s)
    # Geometric mean (Campos et al. n-gram scoring)
    log_sum = sum(math.log(s) for s in scores)
    return math.exp(log_sum / len(scores))


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def extract_keywords(text: str, *, max_keywords: int = 8) -> list[str]:
    """Extract up to max_keywords keyword candidates from text.

    Uses a stdlib-only reimplementation of the YAKE algorithm
    (Campos et al. 2020): 5-feature scoring (TCase, TPos, TFreq,
    TRel, TDif) with n-gram generation.

    Returns keywords sorted by score (most relevant first).
    """
    if not text or not text.strip():
        return []

    # Build stats
    sent_tokens, flat, word_freq, sentence_positions, casing = _build_word_stats(text)

    if not flat:
        return []

    nsent = len(sent_tokens)
    total_words = len(flat)
    unique_words = set(flat)

    # Context window: words that co-occur in the same sentences
    context_words: dict[str, set[str]] = {}
    for sent in sent_tokens:
        for w in sent:
            if w not in context_words:
                context_words[w] = set()
            context_words[w].update(t for t in sent if t != w)

    # Score individual words
    word_scores: dict[str, float] = {}
    for w in unique_words:
        if _is_stopword(w):
            continue
        word_scores[w] = _score_word(
            w,
            casing.get(w, w),
            word_freq,
            sentence_positions,
            nsent,
            total_words,
            context_words.get(w, set()),
            sent_tokens,
        )

    # Generate and score n-grams
    # Flatten all sentence tokens for n-gram generation
    all_flat = [t for sent in sent_tokens for t in sent]
    ngrams = _generate_ngrams(all_flat)

    seen_ngrams: set[tuple[str, ...]] = set()
    scored_ngrams: list[tuple[tuple[str, ...], float]] = []

    for ng in ngrams:
        if ng in seen_ngrams:
            continue
        seen_ngrams.add(ng)
        score = _score_ngram(ng, word_scores)
        scored_ngrams.append((ng, score))

    # Sort by score (lower = better)
    scored_ngrams.sort(key=lambda x: x[1])

    # Deduplicate: prefer longer n-grams, skip substrings of already-selected
    results: list[str] = []
    selected_sets: list[set[str]] = []

    for ng, score in scored_ngrams:
        if len(results) >= max_keywords:
            break
        ng_set = set(ng)
        # Skip if this n-gram is a subset of an already-selected one
        if any(ng_set <= s for s in selected_sets):
            continue
        # Skip if an already-selected n-gram is a subset of this one
        # (prefer the longer one — remove the shorter, add the longer)
        dominated = [i for i, s in enumerate(selected_sets) if s < ng_set]
        if dominated:
            # Remove the shorter dominated entries
            for idx in sorted(dominated, reverse=True):
                results.pop(idx)
                selected_sets.pop(idx)
        keyword = " ".join(casing.get(w, w) for w in ng)
        results.append(keyword)
        selected_sets.append(ng_set)

    return results
