"""Name normalization: unicode folding, honorific and legal-suffix stripping, transliteration.

Every name on both sides (watchlist and customer) goes through `normalize_name`, which
returns a list of canonical tokens. Matching quality depends more on this step than on
the similarity function: "Dr. Mohamed El-Hassan" and "Muhammad Hassan" should share
two identical tokens before any fuzzy scoring runs.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

# Letters that NFKD does not decompose into a base letter plus a mark.
SPECIAL_LETTERS = str.maketrans({
    "ß": "ss", "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
    "ł": "l", "Ł": "l", "đ": "d", "Đ": "d", "ı": "i", "þ": "th", "ð": "d",
    "’": "", "'": "", "`": "", "ʼ": "", "ʻ": "",
})

# Removed wherever they appear as whole tokens at the start of a name.
HONORIFICS = {
    "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "professor", "sir", "dame", "lord", "lady",
    "sheikh", "shaikh", "shaykh", "sheik", "haji", "hajji", "hadji", "sayyid", "sayed", "syed",
    "general", "gen", "colonel", "col", "major", "maj", "captain", "capt", "hon", "honorable",
    "excellency", "his", "her", "he", "rev", "senor", "sr", "sra", "don", "dona", "herr", "frau",
    "gospodin", "mister", "madam", "mme", "mlle", "eng", "engineer",
}

# Name particles that carry little identifying signal and are often dropped or merged.
PARTICLES = {"al", "el", "bin", "ibn", "bint", "ben", "de", "del", "la", "las", "los", "y", "da", "dos",
             "van", "von", "der", "den", "le", "di"}

# Legal-form suffixes and filler words for entity names.
LEGAL_FORMS = {
    "llc", "ltd", "limited", "gmbh", "inc", "incorporated", "corp", "corporation", "co", "company",
    "plc", "sa", "sarl", "ag", "bv", "nv", "srl", "spa", "oy", "ab", "llp", "lp", "jsc", "ojsc",
    "pjsc", "cjsc", "ooo", "zao", "oao", "fze", "fzco", "fzc", "pte", "pty", "sdn", "bhd", "kg",
    "kft", "sro", "doo", "as", "asa", "the", "and", "of",
}

# Token level transliteration table: common variant spellings to one canonical form.
# Real systems keep tables like this per script (Arabic, Cyrillic, Chinese, Persian).
TRANSLIT = {
    # Arabic
    "mohammed": "muhammad", "mohamed": "muhammad", "mohammad": "muhammad", "muhammed": "muhammad",
    "mohamad": "muhammad", "muhamad": "muhammad", "mohamud": "muhammad", "mohammod": "muhammad",
    "ahmed": "ahmad", "ahmet": "ahmad", "hussein": "husayn", "hussain": "husayn", "husain": "husayn",
    "husein": "husayn", "hossein": "husayn", "hosein": "husayn", "yousef": "yusuf", "youssef": "yusuf",
    "yusef": "yusuf", "yousif": "yusuf", "yosef": "yusuf", "abdel": "abd", "abdul": "abd", "abdal": "abd",
    "abdoul": "abd", "omar": "umar", "omer": "umar", "osman": "uthman", "othman": "uthman",
    "khaled": "khalid", "khalid": "khalid", "said": "saeed", "sayeed": "saeed", "saied": "saeed",
    "rachid": "rashid", "rasheed": "rashid", "rashed": "rashid", "hasan": "hassan", "hasaan": "hassan",
    "karim": "karim", "kareem": "karim", "abdallah": "abdullah", "abdulla": "abdullah", "jamal": "jamal",
    "gamal": "jamal", "ibrahim": "ibrahim", "ebrahim": "ibrahim", "ibraheem": "ibrahim", "mahmoud": "mahmud",
    "mahmood": "mahmud", "mustafa": "mustafa", "moustafa": "mustafa", "mostafa": "mustafa",
    "faisal": "faysal", "faycal": "faysal", "feisal": "faysal", "nasser": "nasir", "naser": "nasir",
    "nassir": "nasir", "tariq": "tariq", "tarek": "tariq", "tareq": "tariq", "walid": "walid", "waleed": "walid",
    # Cyrillic
    "aleksandr": "alexander", "aleksander": "alexander", "alexandr": "alexander", "oleksandr": "alexander",
    "sergei": "sergey", "serguei": "sergey", "sergiy": "sergey", "serhiy": "sergey", "yuri": "yuriy",
    "yury": "yuriy", "iouri": "yuriy", "jurij": "yuriy", "dmitri": "dmitriy", "dmitry": "dmitriy",
    "dmitrii": "dmitriy", "mikhail": "mikhail", "mihail": "mikhail", "michail": "mikhail",
    "andrei": "andrey", "andriy": "andrey", "andrej": "andrey", "alexei": "alexey", "aleksei": "alexey",
    "aleksey": "alexey", "oleksiy": "alexey", "nikolai": "nikolay", "nikolaj": "nikolay", "mykola": "nikolay",
    "evgeny": "yevgeniy", "evgeni": "yevgeniy", "yevgeny": "yevgeniy", "evgenii": "yevgeniy",
    "vladimir": "vladimir", "volodymyr": "vladimir", "valeri": "valeriy", "valery": "valeriy",
    "georgi": "georgiy", "georgy": "georgiy", "grigori": "grigoriy", "grigory": "grigoriy",
    "anatoli": "anatoliy", "anatoly": "anatoliy", "ekaterina": "yekaterina", "jekaterina": "yekaterina",
    "tatiana": "tatyana", "tatjana": "tatyana", "natalia": "natalya", "natalja": "natalya",
    "olga": "olga", "yelena": "elena", "jelena": "elena", "ioulia": "yulia", "julia": "yulia",
    # Chinese (Wade-Giles and Cantonese-style spellings to pinyin)
    "chang": "zhang", "hsu": "xu", "chou": "zhou", "chen": "chen", "chien": "qian", "hsieh": "xie",
    "tsai": "cai", "chao": "zhao", "huang": "huang", "wong": "huang", "lee": "li", "lie": "li",
    "liou": "liu", "lau": "liu", "kuo": "guo", "hsiao": "xiao", "tseng": "zeng", "cheung": "zhang",
    "hsin": "xin", "chih": "zhi", "chiang": "qiang", "jui": "rui", "teng": "deng", "tsao": "cao",
    "sung": "song", "yeung": "yang", "woo": "wu", "chu": "zhu",
    # Hispanic and Portuguese
    "jose": "jose", "joseph": "jose", "gonzales": "gonzalez", "rodrigues": "rodriguez",
    "fernandes": "fernandez", "hernandes": "hernandez", "alvares": "alvarez", "gomes": "gomez",
    "martines": "martinez", "lopes": "lopez", "peres": "perez", "sanches": "sanchez",
}

# Rule based ending rewrites, applied after the table (order matters).
ENDING_RULES = [
    (re.compile(r"(sk|tsk|zk)(iy|ii|ij|yi|i)$"), r"\1y"),   # Kovalevskiy -> kovalevsky, -ski -> -sky
    (re.compile(r"off$"), "ov"),                           # Smirnoff -> smirnov
    (re.compile(r"eff$"), "ev"),
    (re.compile(r"ow$"), "ov"),                            # German style Ivanow -> ivanov
    (re.compile(r"^tch"), "ch"),
]

_DOTTED_ACRONYM = re.compile(r"\b((?:[a-z]\.){2,})")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def fold(text: str) -> str:
    """Unicode fold: strip diacritics, map special letters, lowercase."""
    text = text.translate(SPECIAL_LETTERS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def canonical_token(tok: str) -> str:
    tok = TRANSLIT.get(tok, tok)
    for pat, rep in ENDING_RULES:
        tok = pat.sub(rep, tok)
    return TRANSLIT.get(tok, tok)


@lru_cache(maxsize=200_000)
def _normalize(name: str, entity: bool) -> tuple[str, ...]:
    s = fold(name)
    s = s.replace("&", " and ")
    s = _DOTTED_ACRONYM.sub(lambda m: m.group(1).replace(".", "") + " ", s)  # L.L.C. -> llc
    s = _NON_ALNUM.sub(" ", s.replace("-", " "))
    raw = s.split()
    # Strip leading honorifics ("His Excellency Sheikh Dr ...").
    i = 0
    while i < len(raw) - 1 and raw[i] in HONORIFICS:
        i += 1
    raw = raw[i:]
    out: list[str] = []
    for tok in raw:
        if tok in PARTICLES and len(raw) > 1:
            continue
        if entity and tok in LEGAL_FORMS and len(raw) > 1:
            continue
        out.append(canonical_token(tok))
    return tuple(out)


def normalize_name(name: str, entity: bool = False) -> list[str]:
    """Canonical tokens for a person or entity name."""
    return list(_normalize(name, entity))


def normalized_string(name: str, entity: bool = False) -> str:
    return " ".join(_normalize(name, entity))
