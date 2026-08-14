"""One side of the dual-pane browser, Explorer-style:
- a persistent navigation tree on the left, grouped exactly like Explorer's
  left panel: a "PC" node containing every drive letter - local/removable
  and mapped network drives alike (classified via GetDriveTypeW - see
  drives.py) - plus any letter-less "network locations" (UNC shortcuts
  added via Explorer's "Add a network location" wizard - see
  netbrowse.list_network_locations), and a "ネットワーク" node containing
  browsable network computers (via the shell namespace - see netbrowse.py),
  each expandable into its shares. Every item gets its real native shell
  icon (QFileIconProvider), same as Explorer. All of these are lazily
  populated on first expand, including subfolders, so opening a
  slow/offline network location doesn't block anything until you actually
  expand it.
- a content pane on the right showing the current folder's contents
  (double-click a folder to enter it, like Explorer's file list).

The nav tree is a plain QTreeWidget (not backed by QFileSystemModel) since
Qt's model doesn't support the PC/Network grouping Explorer's own shell
namespace has - we build that grouping ourselves and only reach for
QFileSystemModel where a real filesystem view is needed (the content pane).
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QFileInfo, Signal
from PySide6.QtWidgets import (
    QFileIconProvider,
    QFileSystemModel,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import drives, netbrowse

# Sentinel current_path value meaning "the This PC / drive list view", not a
# real filesystem path.
COMPUTER = ""

PATH_ROLE = Qt.UserRole
POPULATED_ROLE = Qt.UserRole + 1
# Marks a "ネットワーク" child as a computer (expands into its shares via
# netbrowse.list_shares) rather than a plain folder (expands via os.scandir).
COMPUTER_ROLE = Qt.UserRole + 2


class FilePane(QWidget):
    path_changed = Signal(str)

    def __init__(self, initial_path: str, title: str, parent=None):
        super().__init__(parent)

        self.model = QFileSystemModel(self)
        self.model.setRootPath("")
        self._icon_provider = QFileIconProvider()

        self.computer_button = QPushButton("PC")
        self.computer_button.setFixedWidth(36)
        self.computer_button.setToolTip("コンピューター(ドライブ一覧)を表示")

        self.up_button = QPushButton("上へ")
        self.up_button.setFixedWidth(50)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(r"パスを入力 (\\server\share もOK)")

        # Left: persistent nav tree, grouped like Explorer (PC / ネットワーク).
        self.nav_tree = QTreeWidget()
        self.nav_tree.setHeaderHidden(True)
        self.nav_tree.itemExpanded.connect(self._on_nav_item_expanded)
        self.nav_tree.itemClicked.connect(self._on_nav_item_clicked)
        self._pc_item, self._network_item = self._build_nav_root()

        # Right: content pane for the currently selected folder.
        self.content_tree = QTreeView()
        self.content_tree.setModel(self.model)
        self.content_tree.setSortingEnabled(True)
        self.content_tree.sortByColumn(0, Qt.AscendingOrder)
        self.content_tree.doubleClicked.connect(self._on_double_click)

        splitter = QSplitter()
        splitter.addWidget(self.nav_tree)
        splitter.addWidget(self.content_tree)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        top_row = QHBoxLayout()
        top_row.addWidget(self.computer_button)
        top_row.addWidget(self.up_button)
        top_row.addWidget(self.path_edit)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(splitter)
        layout.setContentsMargins(4, 4, 4, 4)

        self.path_edit.returnPressed.connect(self._on_path_entered)
        self.up_button.clicked.connect(self._on_up)
        self.computer_button.clicked.connect(self.show_computer)

        self.current_path = COMPUTER
        self.set_path(initial_path)

    # -- nav tree (PC / ネットワーク grouping, lazy-loaded) -----------------

    def _build_nav_root(self) -> tuple[QTreeWidgetItem, QTreeWidgetItem]:
        self.nav_tree.clear()
        pc_item = QTreeWidgetItem(["PC"])
        pc_item.setData(0, PATH_ROLE, None)
        pc_item.setIcon(0, self._icon_provider.icon(QFileIconProvider.IconType.Computer))
        self._add_dummy_child(pc_item)
        self.nav_tree.addTopLevelItem(pc_item)

        network_item = QTreeWidgetItem(["ネットワーク"])
        network_item.setData(0, PATH_ROLE, None)
        network_item.setIcon(0, self._icon_provider.icon(QFileIconProvider.IconType.Network))
        self._add_dummy_child(network_item)
        self.nav_tree.addTopLevelItem(network_item)

        return pc_item, network_item

    @staticmethod
    def _add_dummy_child(item: QTreeWidgetItem) -> None:
        """Placeholder child so the item shows an expand arrow before its
        real children are lazily populated.
        """
        item.addChild(QTreeWidgetItem(["..."]))

    def _on_nav_item_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, POPULATED_ROLE):
            return
        item.takeChildren()  # drop the dummy placeholder

        if item is self._pc_item:
            local, network = drives.list_drives()
            for drive in local + network:
                self._add_drive_item(item, drive)
            for name, target in netbrowse.list_network_locations():
                self._add_named_item(item, name, target)
        elif item is self._network_item:
            for name, computer_path in netbrowse.list_network_computers():
                self._add_named_item(item, name, computer_path, is_computer=True)
        elif item.data(0, COMPUTER_ROLE):
            path = item.data(0, PATH_ROLE)
            for name, share_path in netbrowse.list_shares(path):
                self._add_named_item(item, name, share_path)
        else:
            path = item.data(0, PATH_ROLE)
            if path:
                self._populate_folder_item(item, path)

        item.setData(0, POPULATED_ROLE, True)

    def _add_drive_item(self, parent: QTreeWidgetItem, drive: str) -> None:
        icon = self._icon_provider.icon(QFileInfo(drive))
        self._add_named_item(parent, drives.drive_label(drive), drive, icon=icon)

    def _add_named_item(
        self,
        parent: QTreeWidgetItem,
        label: str,
        path: str,
        is_computer: bool = False,
        icon=None,
    ) -> None:
        child = QTreeWidgetItem([label])
        child.setData(0, PATH_ROLE, path)
        if is_computer:
            child.setData(0, COMPUTER_ROLE, True)
            icon = self._icon_provider.icon(QFileIconProvider.IconType.Computer)
        elif icon is None:
            icon = self._icon_provider.icon(QFileInfo(path))
        child.setIcon(0, icon)
        self._add_dummy_child(child)
        parent.addChild(child)

    def _populate_folder_item(self, item: QTreeWidgetItem, path: str) -> None:
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    child = QTreeWidgetItem([entry.name])
                    child.setData(0, PATH_ROLE, entry.path)
                    child.setIcon(0, self._icon_provider.icon(QFileInfo(entry.path)))
                    self._add_dummy_child(child)
                    item.addChild(child)
            except OSError:
                continue

    def _on_nav_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, PATH_ROLE)
        if path and os.path.isdir(path):
            self.set_path(path)

    def _reveal_in_nav_tree(self, path: str) -> None:
        """Expand+select the nav tree down to `path` so it stays in sync
        with whatever the content pane (or path bar) navigated to.
        """
        path = os.path.normpath(path)
        drive_part, tail = os.path.splitdrive(path)

        if drive_part.startswith("\\\\"):
            # UNC path: \\server\share\... -> ネットワーク > server > share.
            unc_parts = drive_part.strip("\\").split("\\")
            if len(unc_parts) < 2:
                return
            server_path = "\\\\" + unc_parts[0]
            self.nav_tree.expandItem(self._network_item)
            server_item = self._find_child_by_path(self._network_item, server_path)
            if server_item is None:
                return
            self.nav_tree.expandItem(server_item)
            current = self._find_child_by_path(server_item, drive_part)
            if current is None:
                return
        else:
            drive_part = (drive_part + "\\").upper()
            local, network = drives.list_drives()
            if drive_part not in (d.upper() for d in local + network):
                return
            self.nav_tree.expandItem(self._pc_item)
            current = self._find_child_by_path(self._pc_item, drive_part)
            if current is None:
                return

        for part in [p for p in tail.split(os.sep) if p]:
            self.nav_tree.expandItem(current)
            next_item = self._find_child_by_name(current, part)
            if next_item is None:
                break
            current = next_item

        self.nav_tree.setCurrentItem(current)
        self.nav_tree.scrollToItem(current)

    @staticmethod
    def _find_child_by_path(item: QTreeWidgetItem, target_path: str) -> QTreeWidgetItem | None:
        for i in range(item.childCount()):
            child = item.child(i)
            child_path = child.data(0, PATH_ROLE)
            if child_path and os.path.normcase(child_path) == os.path.normcase(target_path):
                return child
        return None

    @staticmethod
    def _find_child_by_name(item: QTreeWidgetItem, name: str) -> QTreeWidgetItem | None:
        for i in range(item.childCount()):
            child = item.child(i)
            if child.text(0) == name:
                return child
        return None

    # -- content pane / address bar -----------------------------------------

    def show_computer(self) -> None:
        """Show the drive-list ("This PC") view, one level above any drive root."""
        self.model.setRootPath("")
        self.content_tree.setRootIndex(self.model.index(""))
        self.path_edit.setText("PC")
        self.current_path = COMPUTER
        self.up_button.setEnabled(False)
        self.nav_tree.clearSelection()
        self.path_changed.emit(COMPUTER)

    def set_path(self, path: str) -> None:
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return
        self.model.setRootPath(path)
        self.content_tree.setRootIndex(self.model.index(path))
        self.path_edit.setText(path)
        self.current_path = path
        self.up_button.setEnabled(True)
        # narrower Size/Type/Date columns, wider Name column
        self.content_tree.setColumnWidth(0, 260)
        self.path_changed.emit(path)
        self._reveal_in_nav_tree(path)

    def selected_path(self) -> str:
        """Directory that a copy should target: the selected row in the
        content pane if it's a directory, otherwise the pane's current
        directory (empty string while showing the "This PC" drive-list
        view - callers must treat that as "nothing usable selected").
        """
        idx = self.content_tree.currentIndex()
        if idx.isValid():
            p = self.model.filePath(idx.siblingAtColumn(0))
            if os.path.isdir(p):
                return p
        return self.current_path

    def refresh(self) -> None:
        if self.current_path == COMPUTER:
            self.show_computer()
        else:
            self.set_path(self.current_path)

    def _on_double_click(self, index) -> None:
        path = self.model.filePath(index.siblingAtColumn(0))
        if os.path.isdir(path):
            self.set_path(path)

    def _on_path_entered(self) -> None:
        text = self.path_edit.text().strip()
        if text.upper() in ("PC", "THIS PC", "コンピューター"):
            self.show_computer()
        else:
            self.set_path(text)

    def _on_up(self) -> None:
        if self.current_path == COMPUTER:
            return
        drive, _tail = os.path.splitdrive(self.current_path)
        if drive and os.path.normcase(os.path.normpath(self.current_path)) == os.path.normcase(
            os.path.normpath(drive + "\\")
        ):
            # Already at a drive root (e.g. "C:\") - go up to the drive list.
            self.show_computer()
            return
        parent = os.path.dirname(self.current_path.rstrip("\\/"))
        if parent and parent != self.current_path:
            self.set_path(parent)
        elif not drive:
            # UNC path (\\server\share\...) with no drive letter: dirname
            # eventually returns the share root itself, so stop there
            # instead of looping - already as "up" as a UNC path can go.
            return
