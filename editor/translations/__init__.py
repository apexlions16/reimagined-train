"""
Translations package for OutlastTrials AudioEditor.
Contains language dictionaries for: en, ru, pl (and optional es_MX, tr).
"""
from .en import EN_TRANSLATIONS
from .ru import RU_TRANSLATIONS
from .pl import PL_TRANSLATIONS

# Optional language packs
try:
    from .es import ES_TRANSLATIONS
except ImportError:
    ES_TRANSLATIONS = {}

try:
    from .tr import TR_TRANSLATIONS
except ImportError:
    TR_TRANSLATIONS = {}

TRANSLATIONS = {
    "en": EN_TRANSLATIONS,
    "ru": RU_TRANSLATIONS,
    "pl": PL_TRANSLATIONS,
    "es_MX": ES_TRANSLATIONS,
    "tr": TR_TRANSLATIONS,
}


def tr(key, lang="en", **kwargs):
    """Translate a key to the given language, falling back to English."""
    lang_dict = TRANSLATIONS.get(lang, TRANSLATIONS.get("en", {}))
    text = lang_dict.get(key, TRANSLATIONS.get("en", {}).get(key, key))
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text
