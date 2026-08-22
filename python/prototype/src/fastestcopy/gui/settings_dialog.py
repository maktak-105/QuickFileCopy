"""Tuning knobs exposed to the user - the defaults come from planner.py but
a benchmark run on a specific machine/drive may suggest better values."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QSpinBox,
)

from fastestcopy.engine import planner

from .i18n import tr


@dataclass
class CopySettings:
    small_workers: int | None = None
    large_chunk_workers: int | None = None
    buffer_mb: int = 4
    preallocate_large: bool = True


class SettingsDialog(QDialog):
    def __init__(self, settings: CopySettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("settings_title"))
        self.settings = settings

        self.small_workers_spin = QSpinBox()
        self.small_workers_spin.setRange(1, 256)
        self.small_workers_spin.setValue(settings.small_workers or planner.suggest_small_workers())

        self.large_chunk_spin = QSpinBox()
        self.large_chunk_spin.setRange(1, 64)
        self.large_chunk_spin.setValue(
            settings.large_chunk_workers or planner.suggest_large_chunk_workers()
        )

        self.buffer_spin = QSpinBox()
        self.buffer_spin.setRange(1, 256)
        self.buffer_spin.setSuffix(" MB")
        self.buffer_spin.setValue(settings.buffer_mb)

        self.preallocate_check = QCheckBox(tr("settings_preallocate"))
        self.preallocate_check.setChecked(settings.preallocate_large)

        form = QFormLayout()
        form.addRow(tr("settings_small_workers"), self.small_workers_spin)
        form.addRow(tr("settings_large_chunk"), self.large_chunk_spin)
        form.addRow(tr("settings_buffer_size"), self.buffer_spin)
        form.addRow(self.preallocate_check)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        outer = QFormLayout(self)
        outer.addRow(form)
        outer.addRow(buttons)

    def result_settings(self) -> CopySettings:
        return CopySettings(
            small_workers=self.small_workers_spin.value(),
            large_chunk_workers=self.large_chunk_spin.value(),
            buffer_mb=self.buffer_spin.value(),
            preallocate_large=self.preallocate_check.isChecked(),
        )
