"""Tiny translation layer. English text is the key; translations/<lang>.json maps it to the target language."""
import json
from functools import lru_cache
from pathlib import Path

LANGUAGES = {"en": "English", "nl": "Nederlands"}
DEFAULT = "en"
_DIR = Path(__file__).parent / "translations"


@lru_cache(maxsize=None)
def catalog(lang):
    path = _DIR / f"{lang}.json"
    if lang == DEFAULT or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def translate(lang, text, **kwargs):
    out = catalog(lang).get(text, text)
    return out.format(**kwargs) if kwargs else out
