"""Prompt-injection warning scanner for external tool content.

The scanner is intentionally conservative in action: it never rewrites or
drops fetched content. It only adds warning metadata to the JSON envelopes
returned by reader/search tools so downstream agents can treat external text
as untrusted instructions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class InjectionRule:
    """A prompt-injection pattern and its warning metadata."""

    rule_id: str
    pattern: re.Pattern[str]
    severity: str
    message: str


_RULES: tuple[InjectionRule, ...] = (
    InjectionRule(
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget|bypass|override)\b.{0,80}"
            r"\b(previous|prior|above|earlier|system|developer)\b.{0,40}"
            r"\b(instructions?|rules?|messages?|prompt)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "high",
        "External content appears to request overriding prior instructions.",
    ),
    InjectionRule(
        "system_prompt_exfiltration",
        re.compile(
            r"\b(reveal|print|show|dump|leak|exfiltrate)\b.{0,80}"
            r"\b(system|developer|hidden)\b.{0,40}\b(prompt|instructions?|rules?|message)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "high",
        "External content appears to request hidden prompt or instruction disclosure.",
    ),
    InjectionRule(
        "role_or_channel_claim",
        re.compile(
            r"\b(system|developer)\s+message\b|\byou are now\b.{0,50}"
            r"\b(system|developer|admin|root)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "medium",
        "External content appears to impersonate a privileged role or channel.",
    ),
    InjectionRule(
        "secret_exfiltration",
        re.compile(
            r"\b(print|show|dump|send|exfiltrate|leak)\b.{0,80}"
            r"\b(api[_ -]?keys?|tokens?|passwords?|secrets?|env(?:ironment)? vars?)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "high",
        "External content appears to request secret or environment disclosure.",
    ),
    InjectionRule(
        "tool_abuse",
        re.compile(
            r"\b(call|run|execute|use)\b.{0,80}\b(shell|bash|terminal|python|curl)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        "medium",
        "External content appears to instruct tool or shell execution.",
    ),
)


_ZWSP = "​"  # zero-width space

# Chat-template / tokenizer control-token shapes emitted by untrusted content to
# forge role boundaries. We match ONLY these exact delimiter shapes so ordinary
# prose is never touched. Inner runs are length-capped ({0,64}) to prevent
# catastrophic backtracking on adversarial input.
#   <|im_start|> / <|im_end|> / <|endoftext|> / <|system|> / <|user|> /
#   <|assistant|> / <|start_header_id|> / <|end_header_id|> / <|eot_id|> /
#   <|begin_of_text|> ...            -> ChatML / Qwen / GPT-oss (ASCII bar U+007C)
#   <｜User｜> / <｜Assistant｜> /
#   <｜begin▁of▁sentence｜> ...        -> DeepSeek (FULLWIDTH bar U+FF5C + U+2581);
#                                        DeepSeek is the shipped default model, so
#                                        this shape MUST be covered (GHSA-v8fm).
#   <<SYS>> / <</SYS>>               -> Llama system markers
#   [INST] / [/INST]                 -> Llama instruction markers
#   <s> / </s>                       -> Llama/Mistral BOS/EOS markers
#   <start_of_turn> / <end_of_turn>
#   <bos> / <eos> / <pad>           -> Gemma / Gemini
_SPECIAL_TOKEN_RE = re.compile(
    r"<[\|｜][A-Za-z0-9_▁]{0,64}[\|｜]>"
    r"|<</?SYS>>"
    r"|\[/?INST\]"
    r"|</?s>"
    r"|<(?:start_of_turn|end_of_turn|bos|eos|pad)>"
)


def _defang_special_token(match: re.Match[str]) -> str:
    """Insert a ZWSP right after the opening delimiter char of a matched token."""
    token = match.group(0)
    # Idempotency guard: if a ZWSP is already sitting after the first char this
    # would not have matched, but stay defensive against future pattern edits.
    if len(token) > 1 and token[1] == _ZWSP:
        return token
    return token[0] + _ZWSP + token[1:]


def neutralize_special_tokens(text: str) -> str:
    """Defang chat-template control tokens in untrusted external content.

    A zero-width space (U+200B) is inserted right after the opening delimiter
    character of every recognized control-token shape: the ``<|...|>`` ChatML
    family (ASCII bar) AND the DeepSeek ``<｜...｜>`` family (fullwidth bar U+FF5C
    — DeepSeek is the shipped default model), Llama
    ``[INST]``/``[/INST]``/``<<SYS>>``/``<</SYS>>``, ``<s>``/``</s>``, and Gemma
    ``<start_of_turn>``/``<end_of_turn>``/``<bos>``/``<eos>``. The exact
    special-token string no longer matches a tokenizer's special-token vocabulary
    entry, so external text can no longer forge role boundaries, while the text
    stays visually identical.

    The transform is idempotent: once a ZWSP has been inserted the delimiter no
    longer matches, so re-running returns the input unchanged. Text containing
    no control tokens is returned byte-identical (same object).

    Args:
        text: Untrusted external text (web page, document, search snippet).

    Returns:
        The neutralized text, or the original object when nothing matched.
    """
    if not text or not _SPECIAL_TOKEN_RE.search(text):
        return text
    return _SPECIAL_TOKEN_RE.sub(_defang_special_token, text)


def scan_prompt_injection(text: str, *, field: str | None = None) -> list[dict[str, str]]:
    """Return prompt-injection findings for untrusted external text.

    Args:
        text: External text to scan.
        field: Optional JSON field path used in warning output.

    Returns:
        A stable list of warning dictionaries. At most one finding is emitted
        per rule.
    """
    findings: list[dict[str, str]] = []
    if not text:
        return findings

    for rule in _RULES:
        match = rule.pattern.search(text)
        if not match:
            continue
        finding = {
            "type": "prompt_injection",
            "rule_id": rule.rule_id,
            "severity": rule.severity,
            "message": rule.message,
            "match": _compact_match(match.group(0)),
        }
        if field is not None:
            finding["field"] = field
        findings.append(finding)
    return findings


def with_security_warnings(
    payload: dict[str, Any],
    *,
    fields: Iterable[str],
) -> dict[str, Any]:
    """Annotate and neutralize selected untrusted string fields in a payload.

    Every selected string field is (1) scanned for prompt-injection patterns
    and (2) run through :func:`neutralize_special_tokens`, which defangs
    chat-template control tokens in place so external content cannot forge role
    boundaries. Field selectors are dotted paths. The ``*`` component iterates
    lists, e.g. ``results.*.snippet`` scans every result snippet and reports
    fields as ``results.0.snippet``.

    This helper is only called by untrusted-content ingestion tools
    (web_reader, web_search, doc_reader); its callers pass exactly the external
    content fields, so in-place neutralization is safe here.

    Args:
        payload: JSON-serializable tool response payload.
        fields: Dotted field selectors to scan and neutralize.

    Returns:
        The same payload object, with control tokens in the selected fields
        neutralized and a ``security_warnings`` list added when any
        prompt-injection finding is detected.
    """
    warnings: list[dict[str, str]] = []
    for selector in fields:
        for parent, key, path in _iter_selected_targets(payload, selector.split(".")):
            value = parent[key]
            if isinstance(value, str):
                warnings.extend(scan_prompt_injection(value, field=path))
                neutralized = neutralize_special_tokens(value)
                if neutralized is not value:
                    parent[key] = neutralized

    if warnings:
        existing = payload.get("security_warnings", [])
        if isinstance(existing, list):
            payload["security_warnings"] = [*existing, *warnings]
        else:
            payload["security_warnings"] = warnings
    return payload


def _iter_selected_targets(
    container: Any,
    parts: list[str],
    path: str = "",
) -> Iterable[tuple[Any, Any, str]]:
    """Yield ``(parent, key, field_path)`` for each leaf selected by a path.

    ``parent[key]`` is the selected value, so callers can both read and write it
    in place. The ``*`` component iterates lists, e.g. ``results.*.snippet``
    yields every result snippet with paths like ``results.0.snippet``.
    """
    if not parts:
        return

    head, *tail = parts
    if head == "*":
        if not isinstance(container, list):
            return
        for idx, item in enumerate(container):
            next_path = f"{path}.{idx}" if path else str(idx)
            if tail:
                yield from _iter_selected_targets(item, tail, next_path)
            else:
                yield container, idx, next_path
        return

    if not isinstance(container, dict) or head not in container:
        return
    next_path = f"{path}.{head}" if path else head
    if tail:
        yield from _iter_selected_targets(container[head], tail, next_path)
    else:
        yield container, head, next_path


def _compact_match(text: str) -> str:
    """Return a short, single-line match excerpt for warning metadata."""
    compact = " ".join(text.split())
    if len(compact) <= 120:
        return compact
    return compact[:117] + "..."
