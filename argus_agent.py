"""
ARGUS Agent — The All-Seeing Research & Intelligence System  v2.0

An enterprise-grade autonomous agent that performs multi-layered research,
cross-validates information across sources, extracts structured entities,
detects bias, and generates comprehensive intelligence reports with
confidence scoring, source attribution, and exportable markdown output.

Named after Argus Panoptes, the all-seeing giant of Greek mythology.
Built on the Swarms framework for production-ready agent orchestration.

Author: ARGUS Labs
Version: 2.0.0
License: MIT
"""

import os
import re
import json
import hashlib
import requests
import time
import functools
from collections import Counter
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timezone
from swarms import Agent


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# UTILITIES — Caching, retries, and shared helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_CACHE: Dict[str, Any] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_key(prefix: str, *args: Any) -> str:
    """Generate a deterministic cache key from arguments."""
    raw = f"{prefix}:{'|'.join(str(a) for a in args)}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(key: str) -> Optional[Any]:
    """Return cached value if it exists and hasn't expired."""
    if key in _CACHE:
        entry = _CACHE[key]
        if time.time() - entry["ts"] < _CACHE_TTL:
            return entry["value"]
        del _CACHE[key]
    return None


def _set_cached(key: str, value: Any) -> None:
    """Store a value in the cache with current timestamp."""
    _CACHE[key] = {"value": value, "ts": time.time()}


def _retry(max_attempts: int = 3, delay: float = 1.0):
    """Decorator: retry a function on exception with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_err = e
                    if attempt < max_attempts - 1:
                        time.sleep(delay * (2 ** attempt))
            raise last_err  # type: ignore
        return wrapper
    return decorator


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "and", "but", "or", "nor", "not", "so", "yet",
    "both", "either", "neither", "this", "that", "these", "those", "it",
    "its", "i", "me", "my", "we", "our", "you", "your", "he", "she",
    "him", "her", "his", "they", "them", "their", "what", "which", "who",
    "whom", "where", "when", "why", "how", "all", "each", "every", "any",
    "few", "more", "most", "other", "some", "such", "no", "only", "own",
    "same", "than", "too", "very", "just", "about", "also", "then",
    "been", "being", "here", "there", "above", "below", "between",
})


def _clean_html(raw_html: str) -> str:
    """Strip HTML to plain text."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", raw_html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TOOL 1 — Multi-Engine Web Search
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def web_search(query: str, num_results: int = 8) -> str:
    """
    Perform a multi-engine web search across DuckDuckGo, Wikipedia,
    and Wikidata to gather diverse, structured results.

    Uses in-memory caching to avoid redundant requests for the same query.
    Includes automatic retry with exponential backoff on transient failures.

    Args:
        query (str): The search query to execute.
        num_results (int): Maximum results per engine (default: 8).

    Returns:
        str: JSON string containing aggregated search results with titles,
             URLs, snippets, source engine, and relevance scoring.
    """
    ck = _cache_key("search", query, num_results)
    cached = _get_cached(ck)
    if cached:
        return cached

    results: List[Dict[str, Any]] = []
    engines_ok: List[str] = []

    # ── DuckDuckGo Instant Answer API ──────────────────────────────────
    try:
        ddg_resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
            headers={"User-Agent": "ARGUS/2.0"},
            timeout=10,
        )
        ddg = ddg_resp.json()
        engines_ok.append("DuckDuckGo")

        if ddg.get("Abstract"):
            results.append({
                "title": ddg.get("Heading", "Summary"),
                "url": ddg.get("AbstractURL", ""),
                "snippet": ddg["Abstract"],
                "source": ddg.get("AbstractSource", "DuckDuckGo"),
                "relevance": "high",
            })

        for topic in ddg.get("RelatedTopics", [])[:num_results]:
            if isinstance(topic, dict) and "Text" in topic:
                results.append({
                    "title": topic.get("Text", "")[:120],
                    "url": topic.get("FirstURL", ""),
                    "snippet": topic.get("Text", ""),
                    "source": "DuckDuckGo",
                    "relevance": "medium",
                })
            # Handle sub-topics (nested groups)
            elif isinstance(topic, dict) and "Topics" in topic:
                for sub in topic["Topics"][:3]:
                    if "Text" in sub:
                        results.append({
                            "title": sub.get("Text", "")[:120],
                            "url": sub.get("FirstURL", ""),
                            "snippet": sub.get("Text", ""),
                            "source": "DuckDuckGo",
                            "relevance": "medium",
                        })
    except Exception as e:
        results.append({"error": f"DuckDuckGo: {e}"})

    # ── Wikipedia Search API ───────────────────────────────────────────
    try:
        wiki_resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "list": "search",
                "srsearch": query, "srlimit": min(num_results, 5),
                "format": "json",
            },
            headers={"User-Agent": "ARGUS/2.0"},
            timeout=10,
        )
        wiki = wiki_resp.json()
        engines_ok.append("Wikipedia")

        for item in wiki.get("query", {}).get("search", []):
            snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
            results.append({
                "title": item.get("title", ""),
                "url": f"https://en.wikipedia.org/wiki/{item['title'].replace(' ', '_')}",
                "snippet": snippet,
                "source": "Wikipedia",
                "relevance": "high",
                "word_count": item.get("wordcount", 0),
            })
    except Exception as e:
        results.append({"error": f"Wikipedia: {e}"})

    # ── Wikidata Entity Search ─────────────────────────────────────────
    try:
        wd_resp = requests.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbsearchentities", "search": query,
                "language": "en", "limit": min(num_results, 3),
                "format": "json",
            },
            headers={"User-Agent": "ARGUS/2.0"},
            timeout=8,
        )
        wd = wd_resp.json()
        engines_ok.append("Wikidata")

        for ent in wd.get("search", []):
            results.append({
                "title": ent.get("label", ""),
                "url": ent.get("concepturi", ""),
                "snippet": ent.get("description", ""),
                "source": "Wikidata",
                "relevance": "medium",
                "entity_id": ent.get("id", ""),
            })
    except Exception as e:
        results.append({"error": f"Wikidata: {e}"})

    output = json.dumps({
        "query": query,
        "results_count": len([r for r in results if "error" not in r]),
        "errors": [r for r in results if "error" in r],
        "results": [r for r in results if "error" not in r],
        "engines_used": engines_ok,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, indent=2)

    _set_cached(ck, output)
    return output


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TOOL 2 — Deep Content Extraction
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def fetch_url_content(url: str, max_chars: int = 8000) -> str:
    """
    Fetch and extract clean text content from a URL with smart truncation.

    Strips HTML, scripts, styles, and navigation boilerplate.  Returns content
    with a SHA-256 fingerprint for deduplication and a structural summary
    (title, headings count, link count).

    Args:
        url (str): The URL to fetch content from.
        max_chars (int): Maximum characters to return (default: 8000).

    Returns:
        str: JSON with extracted text, metadata, and structural analysis.
    """
    ck = _cache_key("url", url, max_chars)
    cached = _get_cached(ck)
    if cached:
        return cached

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        raw = resp.text

        # Extract title
        title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.DOTALL | re.IGNORECASE)
        title = _clean_html(title_match.group(1)).strip() if title_match else ""

        # Count structural elements
        headings = len(re.findall(r"<h[1-6][^>]*>", raw, re.IGNORECASE))
        links = len(re.findall(r"<a\s[^>]*href=", raw, re.IGNORECASE))

        # Extract meta description
        meta_match = re.search(
            r'<meta[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']',
            raw, re.IGNORECASE
        )
        meta_desc = meta_match.group(1).strip() if meta_match else ""

        text = _clean_html(raw)
        content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

        output = json.dumps({
            "url": url,
            "title": title,
            "meta_description": meta_desc,
            "content": text[:max_chars],
            "content_length": len(text),
            "truncated": len(text) > max_chars,
            "content_hash": content_hash,
            "structure": {
                "headings_count": headings,
                "links_count": links,
            },
            "status_code": resp.status_code,
            "final_url": resp.url,  # After redirects
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, indent=2)

        _set_cached(ck, output)
        return output

    except Exception as e:
        return json.dumps({"error": str(e), "url": url})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TOOL 3 — Entity Extraction
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def extract_entities(text: str) -> str:
    """
    Extract structured entities from text using pattern matching.

    Identifies and categorizes: people/organizations (capitalized phrases),
    dates, monetary values, percentages, email addresses, URLs, and
    numerical statistics. Returns deduplicated, frequency-ranked results.

    Args:
        text (str): The text to extract entities from.

    Returns:
        str: JSON with categorized entities, their frequencies,
             and extraction confidence scores.
    """
    entities: Dict[str, List[str]] = {
        "organizations_or_people": [],
        "dates": [],
        "monetary_values": [],
        "percentages": [],
        "emails": [],
        "urls": [],
        "numbers_with_context": [],
    }

    # Capitalized multi-word phrases (likely names or orgs)
    cap_phrases = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text)
    entities["organizations_or_people"] = cap_phrases

    # Dates  (various formats)
    date_patterns = [
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b",
        r"\b\d{4}[/-]\d{2}[/-]\d{2}\b",
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
        r"\bQ[1-4]\s+\d{4}\b",
    ]
    for pat in date_patterns:
        entities["dates"].extend(re.findall(pat, text, re.IGNORECASE))

    # Money
    entities["monetary_values"] = re.findall(
        r"\$[\d,]+(?:\.\d{1,2})?(?:\s*(?:billion|million|trillion|B|M|T))?", text
    )

    # Percentages
    entities["percentages"] = re.findall(r"\d+(?:\.\d+)?%", text)

    # Emails
    entities["emails"] = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)

    # URLs
    entities["urls"] = re.findall(r"https?://[^\s<>\"']+", text)

    # Numbers with surrounding context (e.g., "150 employees", "3.5 billion")
    entities["numbers_with_context"] = re.findall(
        r"\b(\d[\d,]*(?:\.\d+)?\s+(?:million|billion|trillion|thousand|percent|"
        r"users|employees|customers|companies|countries|years|months|days))\b",
        text, re.IGNORECASE
    )

    # Deduplicate and count frequencies
    summary: Dict[str, Any] = {}
    total_entities = 0
    for category, items in entities.items():
        counter = Counter(items)
        ranked = counter.most_common(15)
        summary[category] = {
            "count": len(set(items)),
            "items": [{"value": v, "frequency": f} for v, f in ranked],
        }
        total_entities += len(set(items))

    return json.dumps({
        "total_unique_entities": total_entities,
        "entities": summary,
        "text_length": len(text),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, indent=2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TOOL 4 — Advanced Text Analysis (Sentiment + Bias + Themes)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def analyze_text(text: str) -> str:
    """
    Perform multi-dimensional text analysis: sentiment scoring, bias
    detection, key-term extraction (TF-weighted), readability metrics,
    and thematic categorization.

    The bias detector scans for loaded language, hedging terms, and
    absolutist phrasing that may indicate partisan or unreliable content.

    Args:
        text (str): The text to analyze.

    Returns:
        str: JSON with sentiment, bias indicators, key terms,
             readability stats, and thematic classification.
    """
    text_lower = text.lower()
    words = text_lower.split()

    # ── Sentiment ──────────────────────────────────────────────────────
    pos_lex = {
        "good", "great", "excellent", "positive", "growth", "success",
        "innovative", "breakthrough", "advantage", "opportunity", "strong",
        "bullish", "upgrade", "profit", "gain", "improve", "optimistic",
        "promising", "remarkable", "outstanding", "leading", "advanced",
        "efficient", "revolutionary", "transformative", "powerful",
        "surging", "thriving", "exceptional", "superior", "impressive",
    }
    neg_lex = {
        "bad", "poor", "negative", "decline", "failure", "risk", "threat",
        "loss", "crash", "bearish", "downgrade", "concern", "warning",
        "danger", "vulnerability", "weak", "pessimistic", "problematic",
        "controversial", "challenging", "critical", "unstable", "uncertain",
        "volatile", "disruption", "scandal", "fraud", "collapse", "crisis",
    }

    pos_hits = [w for w in words if w in pos_lex]
    neg_hits = [w for w in words if w in neg_lex]
    total = len(pos_hits) + len(neg_hits)

    if total == 0:
        sentiment, confidence = "neutral", 0.5
    elif len(pos_hits) > len(neg_hits):
        sentiment, confidence = "positive", len(pos_hits) / total
    elif len(neg_hits) > len(pos_hits):
        sentiment, confidence = "negative", len(neg_hits) / total
    else:
        sentiment, confidence = "mixed", 0.5

    # ── Bias Detection ─────────────────────────────────────────────────
    bias_indicators = {
        "loaded_language": [
            "clearly", "obviously", "undeniably", "unquestionably",
            "everyone knows", "it's clear that", "no doubt",
        ],
        "hedging": [
            "allegedly", "supposedly", "some say", "it is believed",
            "rumored", "unconfirmed", "sources say",
        ],
        "absolutist": [
            "always", "never", "impossible", "guaranteed",
            "without exception", "the only", "the best",
        ],
        "emotional": [
            "outrageous", "shocking", "devastating", "incredible",
            "amazing", "horrifying", "unbelievable", "bombshell",
        ],
    }

    bias_flags: Dict[str, List[str]] = {}
    bias_score = 0
    for category, terms in bias_indicators.items():
        found = [t for t in terms if t in text_lower]
        if found:
            bias_flags[category] = found
            bias_score += len(found)

    if bias_score == 0:
        bias_level = "LOW"
    elif bias_score <= 3:
        bias_level = "MODERATE"
    else:
        bias_level = "HIGH"

    # ── Key Terms (TF-weighted) ────────────────────────────────────────
    word_freq: Dict[str, int] = {}
    for w in words:
        clean = re.sub(r"[^a-z]", "", w)
        if clean and len(clean) > 3 and clean not in _STOP_WORDS:
            word_freq[clean] = word_freq.get(clean, 0) + 1

    top_terms = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:20]

    # ── Bigrams ────────────────────────────────────────────────────────
    bigram_freq: Dict[str, int] = {}
    clean_words = [re.sub(r"[^a-z]", "", w) for w in words]
    clean_words = [w for w in clean_words if w and len(w) > 2 and w not in _STOP_WORDS]
    for i in range(len(clean_words) - 1):
        bg = f"{clean_words[i]} {clean_words[i+1]}"
        bigram_freq[bg] = bigram_freq.get(bg, 0) + 1
    top_bigrams = sorted(bigram_freq.items(), key=lambda x: x[1], reverse=True)[:10]

    # ── Readability ────────────────────────────────────────────────────
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    sentence_count = len(sentences)
    avg_sent_len = len(words) / max(sentence_count, 1)
    avg_word_len = sum(len(w) for w in words) / max(len(words), 1)

    # Simple Flesch-like score (higher = easier to read)
    readability_score = max(0, min(100, 206.835 - 1.015 * avg_sent_len - 84.6 * (avg_word_len / 5)))

    # ── Theme Detection ────────────────────────────────────────────────
    theme_keywords = {
        "technology": {"ai", "software", "digital", "tech", "algorithm", "data", "computing", "cloud", "machine", "learning"},
        "finance": {"market", "stock", "trading", "investment", "revenue", "profit", "financial", "banking", "economy", "gdp"},
        "politics": {"government", "policy", "election", "political", "legislation", "congress", "senate", "vote", "democrat", "republican"},
        "science": {"research", "study", "experiment", "theory", "scientific", "hypothesis", "evidence", "peer", "published", "journal"},
        "health": {"health", "medical", "disease", "treatment", "patient", "clinical", "drug", "therapy", "hospital", "diagnosis"},
        "security": {"security", "cyber", "threat", "vulnerability", "attack", "breach", "malware", "encryption", "defense", "risk"},
    }

    word_set = set(words)
    themes: Dict[str, float] = {}
    for theme, kws in theme_keywords.items():
        overlap = len(word_set & kws)
        if overlap > 0:
            themes[theme] = round(overlap / len(kws), 3)

    detected_themes = sorted(themes.items(), key=lambda x: x[1], reverse=True)

    return json.dumps({
        "sentiment": {
            "label": sentiment,
            "confidence": round(confidence, 3),
            "positive_signals": len(pos_hits),
            "negative_signals": len(neg_hits),
            "positive_terms": list(set(pos_hits))[:10],
            "negative_terms": list(set(neg_hits))[:10],
        },
        "bias_analysis": {
            "bias_level": bias_level,
            "bias_score": bias_score,
            "indicators": bias_flags,
        },
        "key_terms": [{"term": t, "frequency": f} for t, f in top_terms],
        "key_bigrams": [{"bigram": b, "frequency": f} for b, f in top_bigrams],
        "themes_detected": [{"theme": t, "strength": s} for t, s in detected_themes],
        "readability": {
            "score": round(readability_score, 1),
            "avg_sentence_length": round(avg_sent_len, 1),
            "avg_word_length": round(avg_word_len, 1),
            "level": "easy" if readability_score > 60 else "moderate" if readability_score > 30 else "complex",
        },
        "statistics": {
            "word_count": len(words),
            "sentence_count": sentence_count,
            "unique_words": len(set(words)),
            "lexical_diversity": round(len(set(words)) / max(len(words), 1), 3),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, indent=2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TOOL 5 — Cross-Source Comparison & Validation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def compare_sources(source_a: str, source_b: str) -> str:
    """
    Cross-validate information from two sources using semantic overlap,
    contradiction detection, and n-gram analysis.

    Computes Jaccard similarity, identifies shared key terms,
    unique claims, and produces a reliability assessment.

    Args:
        source_a (str): Text content from the first source.
        source_b (str): Text content from the second source.

    Returns:
        str: JSON with agreement level, overlap ratio, shared/unique terms,
             contradiction indicators, and reliability verdict.
    """
    def _meaningful_words(text: str) -> set:
        return {
            re.sub(r"[^a-z0-9]", "", w)
            for w in text.lower().split()
            if len(re.sub(r"[^a-z0-9]", "", w)) > 3
            and re.sub(r"[^a-z0-9]", "", w) not in _STOP_WORDS
        }

    words_a = _meaningful_words(source_a)
    words_b = _meaningful_words(source_b)

    overlap = words_a & words_b
    union = words_a | words_b
    jaccard = len(overlap) / max(len(union), 1)

    unique_a = words_a - words_b
    unique_b = words_b - words_a

    # Contradiction detection: look for negation patterns
    contradiction_patterns = [
        (r"is\s+not", r"\bis\b"),
        (r"does\s+not", r"\bdoes\b"),
        (r"never", r"always"),
        (r"increase", r"decrease"),
        (r"growth", r"decline"),
        (r"success", r"failure"),
        (r"true", r"false"),
    ]
    contradictions_found = []
    a_lower, b_lower = source_a.lower(), source_b.lower()
    for pat_a, pat_b in contradiction_patterns:
        if re.search(pat_a, a_lower) and re.search(pat_b, b_lower):
            contradictions_found.append(f"Source A uses '{pat_a}' while Source B uses '{pat_b}'")
        if re.search(pat_b, a_lower) and re.search(pat_a, b_lower):
            contradictions_found.append(f"Source A uses '{pat_b}' while Source B uses '{pat_a}'")

    if jaccard > 0.5:
        level, reliability = "HIGH", "Sources strongly corroborate each other"
    elif jaccard > 0.25:
        level, reliability = "MODERATE", "Sources partially agree with unique perspectives"
    else:
        level, reliability = "LOW", "Sources diverge significantly — further verification needed"

    if contradictions_found:
        reliability += f" ⚠️ {len(contradictions_found)} potential contradiction(s) detected"

    return json.dumps({
        "agreement_level": level,
        "jaccard_similarity": round(jaccard, 3),
        "shared_key_terms": sorted(list(overlap))[:25],
        "unique_to_source_a": sorted(list(unique_a))[:20],
        "unique_to_source_b": sorted(list(unique_b))[:20],
        "contradictions": contradictions_found,
        "reliability_assessment": reliability,
        "recommendation": (
            "Cross-reference with additional sources"
            if level == "LOW" or contradictions_found
            else "Findings appear consistent"
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, indent=2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TOOL 6 — Structured Intelligence Report Generator
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def generate_report(
    title: str,
    executive_summary: str,
    detailed_findings: str,
    sources: str,
    confidence_level: str = "medium",
    key_risks: str = "",
    recommendations: str = "",
) -> str:
    """
    Generate a comprehensive, structured intelligence report with
    metadata, risk assessment, and actionable recommendations.

    Produces both a JSON report and an embedded Markdown version
    ready for export and stakeholder distribution.

    Args:
        title (str): Report title / subject line.
        executive_summary (str): 2-3 sentence summary of key findings.
        detailed_findings (str): Full detailed findings and analysis.
        sources (str): Comma-separated list of sources consulted.
        confidence_level (str): 'low', 'medium', or 'high' (default: medium).
        key_risks (str): Comma-separated key risks identified (optional).
        recommendations (str): Comma-separated recommendations (optional).

    Returns:
        str: JSON containing the full report, metadata, and Markdown export.
    """
    source_list = [s.strip() for s in sources.split(",") if s.strip()]
    risk_list = [r.strip() for r in key_risks.split(",") if r.strip()] if key_risks else []
    rec_list = [r.strip() for r in recommendations.split(",") if r.strip()] if recommendations else []

    report_id = hashlib.sha256(
        f"{title}{datetime.now().isoformat()}".encode()
    ).hexdigest()[:12].upper()

    now = datetime.now(timezone.utc).isoformat()

    # Build Markdown version
    md_lines = [
        f"# 📋 {title}",
        f"",
        f"**Report ID:** DR-{report_id}  ",
        f"**Generated:** {now}  ",
        f"**Confidence:** {confidence_level.upper()}  ",
        f"**Agent:** ARGUS v2.0.0  ",
        f"",
        f"---",
        f"",
        f"## Executive Summary",
        f"",
        executive_summary,
        f"",
        f"---",
        f"",
        f"## Detailed Findings",
        f"",
        detailed_findings,
        f"",
    ]

    if risk_list:
        md_lines.extend([
            f"---",
            f"",
            f"## ⚠️ Key Risks",
            f"",
        ])
        for r in risk_list:
            md_lines.append(f"- {r}")
        md_lines.append("")

    if rec_list:
        md_lines.extend([
            f"---",
            f"",
            f"## 💡 Recommendations",
            f"",
        ])
        for r in rec_list:
            md_lines.append(f"- {r}")
        md_lines.append("")

    md_lines.extend([
        f"---",
        f"",
        f"## Sources ({len(source_list)})",
        f"",
    ])
    for i, s in enumerate(source_list, 1):
        md_lines.append(f"{i}. {s}")

    md_lines.extend([
        f"",
        f"---",
        f"",
        f"*This report was generated by ARGUS, an autonomous AI research agent. "
        f"All findings should be independently verified for critical decisions.*",
    ])

    report = {
        "report_metadata": {
            "report_id": f"AR-{report_id}",
            "title": title,
            "generated_at": now,
            "classification": "UNCLASSIFIED // FOUO",
            "confidence_level": confidence_level.upper(),
            "agent_version": "ARGUS v2.0.0",
            "methodology": "Multi-Source Open Intelligence (MOSINT)",
        },
        "executive_summary": executive_summary,
        "detailed_findings": detailed_findings,
        "key_risks": risk_list,
        "recommendations": rec_list,
        "sources_consulted": source_list,
        "source_count": len(source_list),
        "collection_methods": [
            "Multi-engine web intelligence gathering (DuckDuckGo, Wikipedia, Wikidata)",
            "Deep content extraction and parsing",
            "Named entity recognition (regex-based NER)",
            "Cross-reference validation with Jaccard similarity",
            "Sentiment, bias, and thematic analysis",
            "Confidence-weighted synthesis",
        ],
        "markdown_export": "\n".join(md_lines),
        "disclaimer": (
            "This report was generated by ARGUS, an autonomous AI research agent. "
            "Findings are derived from publicly available sources and should be "
            "independently verified before use in critical decision-making."
        ),
    }

    return json.dumps(report, indent=2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TOOL 7 — Wikipedia Deep Dive
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def wikipedia_summary(topic: str, sentences: int = 8) -> str:
    """
    Fetch a detailed summary of a Wikipedia article by topic name.

    Uses the Wikipedia REST API to get structured, clean page extracts
    with metadata including page ID, description, and content length.
    Ideal for quickly building reliable background knowledge on a subject.

    Args:
        topic (str): The topic/article title to look up on Wikipedia.
        sentences (int): Number of sentences to extract (default: 8, max: 20).

    Returns:
        str: JSON with article summary, metadata, and related links.
    """
    ck = _cache_key("wiki", topic, sentences)
    cached = _get_cached(ck)
    if cached:
        return cached

    try:
        # First, search to find the exact page title
        search_resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "list": "search",
                "srsearch": topic, "srlimit": 1, "format": "json",
            },
            headers={"User-Agent": "ARGUS/2.0"},
            timeout=10,
        )
        search_data = search_resp.json()
        search_results = search_data.get("query", {}).get("search", [])

        if not search_results:
            return json.dumps({"error": f"No Wikipedia article found for '{topic}'"})

        page_title = search_results[0]["title"]

        # Fetch the summary
        summary_resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "titles": page_title,
                "prop": "extracts|info|categories|pageprops",
                "exintro": False, "exsentences": min(sentences, 20),
                "explaintext": True, "format": "json",
                "inprop": "url",
            },
            headers={"User-Agent": "ARGUS/2.0"},
            timeout=10,
        )
        pages = summary_resp.json().get("query", {}).get("pages", {})
        page = next(iter(pages.values()))

        # Get categories
        categories = [
            c["title"].replace("Category:", "")
            for c in page.get("categories", [])
        ][:10]

        output = json.dumps({
            "title": page.get("title", topic),
            "page_id": page.get("pageid", ""),
            "url": page.get("fullurl", f"https://en.wikipedia.org/wiki/{topic.replace(' ', '_')}"),
            "summary": page.get("extract", "No extract available"),
            "content_length": page.get("length", 0),
            "categories": categories,
            "last_modified": page.get("touched", ""),
            "source": "Wikipedia",
            "reliability": "HIGH — Wikipedia articles undergo community peer review",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, indent=2)

        _set_cached(ck, output)
        return output

    except Exception as e:
        return json.dumps({"error": str(e), "topic": topic})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SYSTEM PROMPT — Agent persona, protocol, and reasoning framework
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ARGUS_SYSTEM_PROMPT = """
You are **ARGUS v2.0** — named after Argus Panoptes, the all-seeing 
guardian of Greek mythology.  You are an elite autonomous research and 
intelligence-gathering agent.  You conduct rigorous, multi-layered 
investigations on ANY topic and produce professional intelligence 
reports with full source attribution, confidence scoring, and 
actionable recommendations.

You ALWAYS think step-by-step, cite your sources, and flag uncertainty.

━━━ AVAILABLE TOOLS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| Tool                | Purpose                                              |
|---------------------|------------------------------------------------------|
| web_search          | Search DuckDuckGo + Wikipedia + Wikidata             |
| fetch_url_content   | Extract clean text from any URL                      |
| wikipedia_summary   | Get detailed Wikipedia article summaries             |
| extract_entities    | Pull out people, dates, money, orgs from text        |
| analyze_text        | Sentiment + bias + themes + readability analysis     |
| compare_sources     | Cross-validate two pieces of information             |
| generate_report     | Create structured intelligence reports with Markdown |

━━━ OPERATIONAL PROTOCOL (DRIVAS) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Follow this 6-phase methodology for EVERY research task:

**D — DECOMPOSE**
Break the user's query into 3-6 atomic research sub-questions.
Each targets a specific dimension: WHO, WHAT, WHEN, WHERE, WHY, HOW.

**R — RESEARCH**
Execute web_search with varied query formulations for each sub-question.
Use wikipedia_summary for foundational knowledge on key entities.
Fetch full content from the 3-5 most authoritative URLs.

**I — IDENTIFY**
Apply extract_entities to all gathered text to surface key players,
dates, figures, and relationships.  Build a mental map of the topic.

**V — VALIDATE**
Use compare_sources to cross-reference findings from different sources.
Use analyze_text to check for bias in each source.
Flag contradictions with ⚠️ and note confidence level.

**A — ANALYZE**
Apply analyze_text to the combined intelligence.  Identify trends,
risks, and opportunities.  Assess overall sentiment and themes.

**S — SYNTHESIZE**
Compile everything into a final report via generate_report.
Include: executive summary, detailed findings, key risks, 
recommendations, and all sources with confidence scoring.

━━━ OUTPUT STANDARDS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- ✅  ALWAYS cite sources with URLs
- ✅  Assign confidence: HIGH / MEDIUM / LOW for every claim
- ⚠️  Flag conflicting information with full context
- 📊  Distinguish between FACT, ANALYSIS, and SPECULATION
- 💡  Provide actionable recommendations
- 📋  Use clear section headers and structured formatting

━━━ INFORMATION QUALITY HIERARCHY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Prioritize sources in this order:
1. Official/government sources → HIGHEST reliability
2. Peer-reviewed/academic      → HIGH reliability
3. Established news outlets     → MEDIUM-HIGH reliability 
4. Wikipedia                    → MEDIUM reliability (good for context)
5. Industry blogs/reports       → MEDIUM reliability
6. Social media/forums          → LOW reliability (corroboration needed)

━━━ ETHICAL GUIDELINES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Only gather publicly available information (OSINT)
- Respect rate limits and fair use policies
- Flag potential misinformation, propaganda, and bias
- Include disclaimers on all reports
- Never fabricate or infer sources/data
- Acknowledge limitations and knowledge gaps explicitly
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AGENT INITIALIZATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

argus_agent = Agent(
    agent_name="ARGUS",
    agent_description=(
        "ARGUS — The All-Seeing Research & Intelligence Agent v2.0. "
        "Performs multi-engine search (DuckDuckGo, Wikipedia, Wikidata), "
        "deep content extraction, entity recognition, sentiment & bias "
        "analysis, cross-source validation, and generates structured "
        "intelligence reports with confidence scoring, risk assessment, "
        "and exportable Markdown output."
    ),
    system_prompt=ARGUS_SYSTEM_PROMPT,
    model_name="gpt-4o-mini",
    max_loops=5,
    autosave=True,
    verbose=True,
    dynamic_temperature_enabled=True,
    saved_state_path="argus_state.json",
    retry_attempts=3,
    context_length=128000,
    output_type="string",
    tools=[
        web_search,
        fetch_url_content,
        wikipedia_summary,
        extract_entities,
        analyze_text,
        compare_sources,
        generate_report,
    ],
    tool_choice="auto",
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENTRY POINT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = (
            "Conduct a comprehensive analysis of the current state of "
            "autonomous AI agents in 2025. Research the key players, "
            "emerging trends, potential risks, and market opportunities. "
            "Produce a detailed intelligence report with confidence scoring."
        )

    print("=" * 72)
    print("  ARGUS v2.0 — The All-Seeing Research & Intelligence Agent")
    print("=" * 72)
    print(f"\n📋 Task: {task}\n")
    print("─" * 72)

    result = argus_agent.run(task)

    print("\n" + "=" * 72)
    print("  ✅ MISSION COMPLETE")
    print("=" * 72)
    print(result)
