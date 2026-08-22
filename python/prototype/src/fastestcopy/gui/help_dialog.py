"""Read-only Help dialog, rendering the current UI language's help HTML
(see help_content.py) in a QTextBrowser.
"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QPushButton, QTextBrowser, QVBoxLayout

from .help_content import HELP_EN, HELP_JA
from .i18n import get_language, tr


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("menu_help_show"))
        self.resize(560, 640)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(HELP_JA if get_language() == "ja" else HELP_EN)

        close_button = QPushButton(tr("close_button"))
        close_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(browser)
        layout.addWidget(close_button)
