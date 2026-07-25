import os
import sys
import time
from pathlib import Path
from datetime import datetime

try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
    QProgressBar, QTextEdit, QSplitter, QStatusBar, QMessageBox, QStyle,
    QComboBox, QFrame,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QTextCursor

ROOT = Path(__file__).parent
INCLUDE_EXTENSIONS = {".py", ".md", ".env.example", ".json"}
SKIP_DIRS = {".venv", "__pycache__", ".git", ".idea", "logs", "data", "migrations", ".pytest_cache"}
SKIP_DIR_PREFIXES = ("_backup",)
SKIP_FILES = {"dump.py", "dump_ui.py", "project_dump.txt"}

TOKEN_LIMITS = {
    "GPT-4o / GPT-4 (128K)":  128_000,
    "Claude 3.5 / 3 (200K)":  200_000,
    "Gemini 1.5 Pro (2M)":  2_000_000,
    "GPT-3.5 (16K)":           16_000,
}
_ROLE_META = Qt.ItemDataRole.UserRole + 1

STYLE = """
QMainWindow, QWidget {
    background-color: #0d1117;
    color: #e6edf3;
    font-family: 'Consolas', 'JetBrains Mono', 'Courier New', monospace;
    font-size: 13px;
}
QLabel { color: #8b949e; font-size: 12px; font-weight: bold; }
QLineEdit, QComboBox {
    background-color: #161b22; border: 1px solid #30363d;
    padding: 6px; border-radius: 4px; color: #cdd6f4;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background-color: #161b22; color: #cdd6f4;
    selection-background-color: #30363d;
}
QPushButton {
    background-color: #238636; color: #ffffff; font-weight: bold;
    padding: 8px 16px; border-radius: 4px;
    border: 1px solid rgba(240,246,252,0.1);
}
QPushButton:hover { background-color: #2ea043; }
QPushButton:disabled { background-color: #21262d; color: #8b949e; }
QPushButton#toolBtn {
    background-color: #21262d; color: #c9d1d9;
    border: 1px solid #30363d; padding: 6px 10px;
}
QPushButton#toolBtn:hover { background-color: #30363d; }
QTreeWidget, QTextEdit {
    background-color: #0d1117; border: 1px solid #30363d; border-radius: 4px;
}
QProgressBar {
    border: 1px solid #30363d; border-radius: 4px;
    text-align: center; background-color: #161b22;
}
QProgressBar::chunk { background-color: #238636; }
QStatusBar { border-top: 1px solid #30363d; background-color: #161b22; }
QFrame#statsBar { background-color: #161b22; border: 1px solid #30363d; border-radius: 4px; }
"""


class DumpWorker(QThread):
    log      = pyqtSignal(str)
    progress = pyqtSignal(int)
    done     = pyqtSignal(str, int, float, int)
    error    = pyqtSignal(str)

    def __init__(self, selected_files, out_path, mode="STANDARD"):
        super().__init__()
        self.selected_files = selected_files
        self.out_path = out_path
        self.mode = mode

    def get_token_count(self, text):
        if HAS_TIKTOKEN:
            try:
                return len(tiktoken.get_encoding("cl100k_base").encode(text))
            except Exception:
                pass
        return len(text) // 4

    def generate_tree_str(self):
        lines = ["Project Structure:"]
        for p in sorted(Path(f) for f in self.selected_files):
            lines.append("  " * (len(p.parts) - 1) + f"📄 {p.name}")
        return "\n".join(lines)

    def run(self):
        total_lines = 0
        total_files = len(self.selected_files)
        full_text = ""
        try:
            with open(self.out_path, "w", encoding="utf-8") as out:
                if self.mode == "REPO_PROMPT":
                    header = (
                        "### REPO TO PROMPT CONTEXT ###\n"
                        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                        "INSTRUCTIONS for LLM:\nThe following is the source code of the project.\n"
                        f"{self.generate_tree_str()}\n\n" + "=" * 40 + "\n\n"
                    )
                else:
                    header = (
                        f"# PROJECT DUMP\n# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                        f"# Files: {total_files}\n#\n# STRUCTURE:\n"
                    )
                    for f in self.selected_files:
                        header += f"#   {f}\n"
                    header += "\n"

                out.write(header)
                full_text += header

                for i, rel_str in enumerate(self.selected_files):
                    full_path = ROOT / rel_str
                    try:
                        raw = full_path.read_text(encoding="utf-8")
                        lines = raw.count("\n")
                        total_lines += lines
                        if self.mode == "REPO_PROMPT":
                            block = f'<file path="{rel_str}">\n{raw}\n</file>\n\n'
                        else:
                            sep = "=" * 60
                            block = f"\n{sep}\nFILE: {rel_str}\n{sep}\n\n{raw}\n"
                        out.write(block)
                        full_text += block
                        self.log.emit(f"✅ {rel_str} ({lines} рядків)")
                    except Exception as e:
                        msg = f"# ERROR reading {rel_str}: {e}\n"
                        out.write(msg)
                        self.log.emit(f"❌ {msg}")

                    self.progress.emit(int((i + 1) / total_files * 100))
                    time.sleep(0.01)

            tokens  = self.get_token_count(full_text)
            size_kb = os.path.getsize(self.out_path) / 1024
            self.done.emit(str(self.out_path), total_lines, size_kb, tokens)
        except Exception as e:
            self.error.emit(str(e))


class DumpApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pro Dump Builder & AI Context Manager")
        self.resize(1000, 750)
        self.setStyleSheet(STYLE)
        self._stats_dirty = False

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # ── 1. Верхня панель ──────────────────────────────────────────────────
        top = QHBoxLayout()
        top.addWidget(QLabel("Режим:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Repo-to-Prompt (AI / XML)", "Standard (Звичайний)"])
        top.addWidget(self.mode_combo)
        top.addSpacing(15)
        top.addWidget(QLabel("Файл:"))
        self.filename_input = QLineEdit("project_dump.txt")
        self.filename_input.setFixedWidth(150)
        top.addWidget(self.filename_input)
        top.addSpacing(15)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Швидкий пошук файлів...")
        self.search_input.textChanged.connect(self.filter_tree)
        top.addWidget(self.search_input)
        layout.addLayout(top)

        # ── 2. Кнопки ─────────────────────────────────────────────────────────
        tools = QHBoxLayout()
        for label, slot in [("☑ Вибрати все", self.select_all),
                             ("☐ Зняти виділення", self.deselect_all)]:
            b = QPushButton(label); b.setObjectName("toolBtn"); b.clicked.connect(slot)
            tools.addWidget(b)
        tools.addStretch()
        for label, fn in [("📂 Розгорнути", self.tree_expand),
                           ("📁 Згорнути",   self.tree_collapse)]:
            b = QPushButton(label); b.setObjectName("toolBtn"); b.clicked.connect(fn)
            tools.addWidget(b)
        layout.addLayout(tools)

        # ── 3. Сплітер ────────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("📦 Структура проєкту")
        self.tree.itemChanged.connect(self._on_item_changed)
        splitter.addWidget(self.tree)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setPlaceholderText("Логи генерації з'являться тут...")
        splitter.addWidget(self.log_edit)
        splitter.setSizes([500, 400])
        layout.addWidget(splitter)

        # ── 4. Stats bar ──────────────────────────────────────────────────────
        sf = QFrame(); sf.setObjectName("statsBar")
        sb = QHBoxLayout(sf)
        sb.setContentsMargins(14, 7, 14, 7)
        sb.setSpacing(0)

        def cap(t):
            w = QLabel(t)
            w.setStyleSheet("color:#444c56; font-size:11px; letter-spacing:0.06em;")
            return w

        def val(t, clr="#c9d1d9"):
            w = QLabel(t)
            w.setStyleSheet(f"color:{clr}; font-size:13px; font-weight:600;")
            return w

        def dot():
            w = QLabel("  ·  ")
            w.setStyleSheet("color:#2d333b; font-size:16px;")
            return w

        sb.addWidget(cap("ОБРАНО")); sb.addSpacing(8)
        self.stat_files  = val("0 файлів");      sb.addWidget(self.stat_files)
        sb.addWidget(dot())
        self.stat_lines  = val("0 рядків");      sb.addWidget(self.stat_lines)
        sb.addWidget(dot())
        sb.addWidget(QLabel("🧠")); sb.addSpacing(4)
        self.stat_tokens = val("~0 токенів", "#89b4fa"); sb.addWidget(self.stat_tokens)
        sb.addSpacing(12)

        self.token_bar = QProgressBar()
        self.token_bar.setRange(0, 100); self.token_bar.setValue(0)
        self.token_bar.setFixedWidth(110); self.token_bar.setFixedHeight(8)
        self.token_bar.setTextVisible(False)
        sb.addWidget(self.token_bar); sb.addSpacing(6)

        self.stat_pct = QLabel("0%")
        self.stat_pct.setStyleSheet("color:#444c56; font-size:11px;")
        sb.addWidget(self.stat_pct)
        sb.addStretch()

        sb.addWidget(cap("ЛІМІТ")); sb.addSpacing(8)
        self.limit_combo = QComboBox()
        self.limit_combo.addItems(list(TOKEN_LIMITS.keys()))
        self.limit_combo.setFixedWidth(210)
        self.limit_combo.currentIndexChanged.connect(self._refresh_stats)
        sb.addWidget(self.limit_combo)
        layout.addWidget(sf)

        # ── 5. Прогрес + кнопка ───────────────────────────────────────────────
        self.progress = QProgressBar(); self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.build_btn = QPushButton("🚀 ЗГЕНЕРУВАТИ ДАМП")
        self.build_btn.clicked.connect(self.start_generation)
        layout.addWidget(self.build_btn)
        self.status = QStatusBar(); self.setStatusBar(self.status)

        self.populate_tree()

    def tree_expand(self):  self.tree.expandAll()
    def tree_collapse(self): self.tree.collapseAll()

    def select_all(self):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Checked)
        self.tree.blockSignals(False); self._refresh_stats()

    def deselect_all(self):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Unchecked)
        self.tree.blockSignals(False); self._refresh_stats()

    def populate_tree(self):
        self.tree.blockSignals(True); self.tree.clear()
        paths = []
        for root, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs
                       if d not in SKIP_DIRS and not d.startswith(SKIP_DIR_PREFIXES)]
            for file in files:
                if file in SKIP_FILES: continue
                if Path(file).suffix in INCLUDE_EXTENSIONS or file.endswith(".json"):
                    paths.append(Path(root).relative_to(ROOT) / file)

        nodes = {}
        for p in sorted(paths):
            parent = self.tree.invisibleRootItem()
            curr   = Path()
            for part in p.parts[:-1]:
                curr /= part
                if curr not in nodes:
                    it = QTreeWidgetItem([part])
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                    it.setCheckState(0, Qt.CheckState.Checked)
                    it.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
                    parent.addChild(it); nodes[curr] = it
                parent = nodes[curr]

            full = ROOT / p
            try:
                txt   = full.read_text(encoding="utf-8", errors="replace")
                lc    = txt.count("\n") + (1 if txt and not txt.endswith("\n") else 0)
                cc    = len(txt)
            except Exception:
                lc = cc = 0

            fi = QTreeWidgetItem([p.name])
            fi.setFlags(fi.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            fi.setCheckState(0, Qt.CheckState.Checked)
            fi.setData(0, Qt.ItemDataRole.UserRole, str(p).replace("\\", "/"))
            fi.setData(0, _ROLE_META, (lc, cc))
            fi.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
            parent.addChild(fi)

        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._refresh_stats()
        self.status.showMessage(f"Знайдено файлів: {len(paths)}")

    def filter_tree(self, text):
        q = text.lower()
        def _f(n):
            m = q in n.text(0).lower()
            cm = any(_f(n.child(i)) for i in range(n.childCount()))
            n.setHidden(not (m or cm)); return m or cm
        for i in range(self.tree.topLevelItemCount()): _f(self.tree.topLevelItem(i))

    def _on_item_changed(self, _item, _col):
        if not self._stats_dirty:
            self._stats_dirty = True
            QTimer.singleShot(80, self._refresh_stats)

    def _refresh_stats(self):
        self._stats_dirty = False
        tf = tl = tc = 0

        def _walk(n):
            nonlocal tf, tl, tc
            if n.childCount() == 0:
                if n.checkState(0) == Qt.CheckState.Checked:
                    m = n.data(0, _ROLE_META)
                    if m: tf += 1; tl += m[0]; tc += m[1]
            else:
                for i in range(n.childCount()): _walk(n.child(i))

        for i in range(self.tree.topLevelItemCount()): _walk(self.tree.topLevelItem(i))

        est   = int(tc / 3.5) if HAS_TIKTOKEN else tc // 4
        limit = TOKEN_LIMITS.get(self.limit_combo.currentText(), 128_000)
        pct   = min(int(est / limit * 100), 100)

        if pct < 50:   bc, tc_clr = "#238636", "#89b4fa"
        elif pct < 80: bc, tc_clr = "#d29922", "#e3b341"
        else:          bc, tc_clr = "#da3633", "#f38ba8"

        self.token_bar.setStyleSheet(
            f"QProgressBar{{background:#21262d;border:1px solid #30363d;border-radius:3px;}}"
            f"QProgressBar::chunk{{background:{bc};border-radius:3px;}}"
        )
        self.stat_files.setText(f"{tf:,} файлів")
        self.stat_lines.setText(f"{tl:,} рядків")
        self.stat_tokens.setText(f"~{est:,} токенів")
        self.stat_tokens.setStyleSheet(f"color:{tc_clr}; font-size:13px; font-weight:600;")
        self.token_bar.setValue(pct)
        self.stat_pct.setText(f"{pct}%")
        self.stat_pct.setStyleSheet(f"color:{tc_clr}; font-size:11px;")

    def start_generation(self):
        selected = []
        def _col(n):
            if n.childCount() == 0 and n.checkState(0) == Qt.CheckState.Checked:
                p = n.data(0, Qt.ItemDataRole.UserRole)
                if p: selected.append(p)
            for i in range(n.childCount()): _col(n.child(i))
        for i in range(self.tree.topLevelItemCount()): _col(self.tree.topLevelItem(i))
        if not selected: return QMessageBox.warning(self, "Увага", "Виберіть хоча б один файл!")

        mode = "REPO_PROMPT" if self.mode_combo.currentIndex() == 0 else "STANDARD"
        self.log_edit.clear(); self.log_edit.append(f"<b>Режим: {mode}</b>")
        self.build_btn.setEnabled(False); self.progress.setValue(0)

        self._worker = DumpWorker(selected, ROOT / self.filename_input.text(), mode)
        self._worker.log.connect(lambda t: self.log_edit.append(t))
        self._worker.progress.connect(self.progress.setValue)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, path, lines, size_kb, tokens):
        limit = TOKEN_LIMITS.get(self.limit_combo.currentText(), 128_000)
        pct   = min(int(tokens / limit * 100), 100)
        clr   = "#89b4fa" if pct < 50 else "#e3b341" if pct < 80 else "#f38ba8"
        self.log_edit.append("<br><font color='#a6e3a1'><b>🎉 УСПІХ!</b></font>")
        self.log_edit.append(f"📄 Рядків: {lines:,}   📏 {size_kb:.1f} KB")
        self.log_edit.append(
            f"🧠 <b>Токенів: <span style='color:{clr};'>{tokens:,}</span> ({pct}% від ліміту)</b>"
        )
        if not HAS_TIKTOKEN:
            self.log_edit.append("<small>⚠️ pip install tiktoken для точності</small>")
        self.build_btn.setEnabled(True); self.progress.setValue(100)
        self.status.showMessage(f"✅ Готово! {tokens:,} токенів ({pct}%)")

    def _on_error(self, err):
        self.log_edit.append(f"<font color='#f38ba8'>❌ {err}</font>")
        self.build_btn.setEnabled(True)
        QMessageBox.critical(self, "Помилка", err)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DumpApp()
    window.show()
    sys.exit(app.exec())