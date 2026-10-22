"""Splitting questions and data values into comparable words."""

import re

STOPWORDS = frozenset("""
a an and are as at be by can did do does for from give had has have i in is it list me of on or our over please
show tell than that the their there those to us was we were what which who whose with you your
""".split())

NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fifteen": "15", "twenty": "20", "fifty": "50",
    "hundred": "100",
}


def words(text: str) -> list[str]:
    """Lower-case word tokens with number words as digits, keeping stop words (phrases need them)."""
    tokens = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower().replace("&", " and "))
    return [NUMBER_WORDS.get(t, t) for t in tokens]


def content_words(text: str) -> list[str]:
    """`words` without stop words, with a trailing plural "s" dropped: what a question is about."""
    return [singular(t) for t in words(text) if t not in STOPWORDS]


def singular(word: str) -> str:
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def contains_phrase(haystack: list[str], phrase: list[str]) -> bool:
    n = len(phrase)
    return n > 0 and any(haystack[i:i + n] == phrase for i in range(len(haystack) - n + 1))
