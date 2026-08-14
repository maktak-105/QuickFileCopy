import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fastestcopy.gui import i18n


def test_every_string_has_both_languages():
    """Regression guard: a key with only one language's entry would
    silently fall back to Japanese under English, defeating the point of
    switching - catch that at test time instead of by eyeballing the UI.
    """
    missing = {
        key: sorted({"ja", "en"} - set(entry))
        for key, entry in i18n._STRINGS.items()
        if not {"ja", "en"} <= set(entry)
    }
    assert missing == {}


def test_tr_switches_with_language(monkeypatch):
    monkeypatch.setattr(i18n, "_current_lang", "ja")
    assert i18n.tr("cancel_button") == "キャンセル"
    monkeypatch.setattr(i18n, "_current_lang", "en")
    assert i18n.tr("cancel_button") == "Cancel"


def test_tr_unknown_key_returns_key_itself():
    assert i18n.tr("this_key_does_not_exist") == "this_key_does_not_exist"


def test_get_set_language_round_trips(monkeypatch, tmp_path):
    # Isolate QSettings from the real registry-backed store for this test.
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(QSettings, "setValue", lambda self, k, v: None)
    monkeypatch.setattr(i18n, "_current_lang", None)

    i18n.set_language("en")
    assert i18n.get_language() == "en"

    i18n.set_language("ja")
    assert i18n.get_language() == "ja"


def test_get_language_falls_back_to_ja_for_unrecognized_stored_value(monkeypatch):
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(i18n, "_current_lang", None)
    monkeypatch.setattr(QSettings, "value", lambda self, k, default=None: "fr")

    assert i18n.get_language() == "ja"
