import os
import sys
import time
from pathlib import Path
from datetime import datetime

# Спробуємо імпортувати tiktoken для точного підрахунку токенів
try:
    import tiktoken

    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
    QProgressBar, QTextEdit, QSplitter, QStatusBar, QMessageBox, QStyle,
    QComboBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor

# --- НАЛАШТУВАННЯ ПРОЄКТУ ---
ROOT = Path(__file__).parent
INCLUDE_EXTENSIONS = {".py", ".md", ".env.example", ".json"}
SKIP_DIRS = {".venv", "__pycache__", ".git", ".idea", "logs", "data", "migrations", ".pytest_cache"}
SKIP_DIR_PREFIXES = ("_backup",)
SKIP_FILES = {"dump.py", "dump_ui.py", "project_dump.txt"}

# --- СТИЛІСТИКА ---
STYLE = """
QMainWindow, QWidget {
    background-color: #0d1117;
    color: #e6edf3;
    font-family: 'Consolas', 'JetBrains Mono', 'Courier New', monospace;
    font-size: 13px;
}
QLabel { color: #8b949e; font-size: 12px; font-weight: bold;}
QLineEdit, QComboBox {
    background-color: #161b22; 
    border: 1px solid #30363d; 
    padding: 6px; 
    border-radius: 4px; 
    color: #cdd6f4;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background-color: #161b22;
    color: #cdd6f4;
    selection-background-color: #30363d;
}
QPushButton {
    background-color: #238636; 
    color: #ffffff; 
    font-weight: bold; 
    padding: 8px 16px; 
    border-radius: 4px; 
    border: 1px solid rgba(240, 246, 252, 0.1);
}
QPushButton:hover { background-color: #2ea043; }
QPushButton:disabled { background-color: #21262d; color: #8b949e; }
QPushButton#toolBtn {
    background-color: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    padding: 6px 10px;
}
QPushButton#toolBtn:hover { background-color: #30363d; }
QTreeWidget, QTextEdit {
    background-color: #0d1117; 
    border: 1px solid #30363d; 
    border-radius: 4px; 
}
QProgressBar {
    border: 1px solid #30363d;
    border-radius: 4px;
    text-align: center;
    background-color: #161b22;
}
QProgressBar::chunk { background-color: #238636; }
QStatusBar { border-top: 1px solid #30363d; background-color: #161b22; }
"""


# =============================================================================
# РОБОЧИЙ ПОТІК (QThread)
# =============================================================================
class DumpWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    done = pyqtSignal(str, int, float, int)  # Шлях, Рядки, Розмір, Токени
    error = pyqtSignal(str)

    def __init__(self, selected_files, out_path, mode="STANDARD"):
        super().__init__()
        self.selected_files = selected_files
        self.out_path = out_path
        self.mode = mode

    def get_token_count(self, text):
        """Підрахунок токенів для cl100k_base (GPT-4/o)."""
        if HAS_TIKTOKEN:
            try:
                encoding = tiktoken.get_encoding("cl100k_base")
                return len(encoding.encode(text))
            except Exception:
                return len(text) // 4
        return len(text) // 4  # Груба оцінка (1 токен ~ 4 символи)

    def generate_tree_str(self):
        tree_lines = ["Project Structure:"]
        paths = sorted([Path(f) for f in self.selected_files])
        for p in paths:
            depth = len(p.parts) - 1
            indent = "  " * depth
            tree_lines.append(f"{indent}📄 {p.name}")
        return "\n".join(tree_lines)

    def run(self):
        total_lines = 0
        total_files = len(self.selected_files)
        final_full_text = ""

        try:
            with open(self.out_path, "w", encoding="utf-8") as out:

                # --- ГЕНЕРУЄМО ХЕДЕР ---
                header = ""
                if self.mode == "REPO_PROMPT":
                    header = (
                            "### REPO TO PROMPT CONTEXT ###\n"
                            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                            "INSTRUCTIONS for LLM:\n"
                            "The following is the source code of the project. Analyze the structure and logic.\n"
                            "Each file is wrapped in <file> tags with a 'path' attribute.\n\n"
                            f"{self.generate_tree_str()}\n\n"
                            + "=" * 40 + "\n\n"
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
                final_full_text += header

                # --- ДОДАЄМО КОНТЕНТ ФАЙЛІВ ---
                for i, file_path_str in enumerate(self.selected_files):
                    full_path = ROOT / file_path_str
                    block_content = ""

                    try:
                        raw_content = full_path.read_text(encoding="utf-8")
                        lines = raw_content.count("\n")
                        total_lines += lines

                        if self.mode == "REPO_PROMPT":
                            block_content = f'<file path="{file_path_str}">\n{raw_content}\n</file>\n\n'
                        else:
                            sep = "=" * 60
                            block_content = f"\n{sep}\nFILE: {file_path_str}\n{sep}\n\n{raw_content}\n"

                        out.write(block_content)
                        final_full_text += block_content
                        self.log.emit(f"✅ Додано: {file_path_str} ({lines} рядків)")
                    except Exception as e:
                        err_msg = f"# ERROR reading {file_path_str}: {e}\n"
                        out.write(err_msg)
                        self.log.emit(f"❌ {err_msg}")

                    self.progress.emit(int(((i + 1) / total_files) * 100))
                    time.sleep(0.01)

            # Підрахунок токенів
            tokens = self.get_token_count(final_full_text)
            size_kb = os.path.getsize(self.out_path) / 1024
            self.done.emit(str(self.out_path), total_lines, size_kb, tokens)

        except Exception as e:
            self.error.emit(str(e))


# =============================================================================
# ГОЛОВНЕ ВІКНО UI
# =============================================================================
class DumpApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pro Dump Builder & AI Context Manager")
        self.resize(1000, 750)
        self.setStyleSheet(STYLE)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 1. КОМПАКТНА ВЕРХНЯ ПАНЕЛЬ
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("Режим:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Repo-to-Prompt (AI / XML)", "Standard (Звичайний)"])
        top_layout.addWidget(self.mode_combo)

        top_layout.addSpacing(15)
        top_layout.addWidget(QLabel("Файл:"))
        self.filename_input = QLineEdit("project_dump.txt")
        self.filename_input.setFixedWidth(150)
        top_layout.addWidget(self.filename_input)

        top_layout.addSpacing(15)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Швидкий пошук файлів...")
        self.search_input.textChanged.connect(self.filter_tree)
        top_layout.addWidget(self.search_input)
        main_layout.addLayout(top_layout)

        # 2. КНОПКИ КЕРУВАННЯ ДЕРЕВОМ
        tools_layout = QHBoxLayout()
        btn_select_all = QPushButton("☑ Вибрати все")
        btn_select_all.setObjectName("toolBtn")
        btn_select_all.clicked.connect(self.select_all)
        tools_layout.addWidget(btn_select_all)

        btn_deselect_all = QPushButton("☐ Зняти виділення")
        btn_deselect_all.setObjectName("toolBtn")
        btn_deselect_all.clicked.connect(self.deselect_all)
        tools_layout.addWidget(btn_deselect_all)

        tools_layout.addStretch()

        btn_expand = QPushButton("📂 Розгорнути")
        btn_expand.setObjectName("toolBtn")
        btn_expand.clicked.connect(lambda: self.tree.expandAll())
        tools_layout.addWidget(btn_expand)

        btn_collapse = QPushButton("📁 Згорнути")
        btn_collapse.setObjectName("toolBtn")
        btn_collapse.clicked.connect(lambda: self.tree.collapseAll())
        tools_layout.addWidget(btn_collapse)
        main_layout.addLayout(tools_layout)

        # 3. СПЛІТТЕР
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("📦 Структура проєкту")
        splitter.addWidget(self.tree)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setPlaceholderText("Логи генерації з'являться тут...")
        splitter.addWidget(self.log_edit)

        splitter.setSizes([500, 400])
        main_layout.addWidget(splitter)

        # 4. НИЖНЯ ПАНЕЛЬ
        self.progress = QProgressBar()
        self.progress.setValue(0)
        main_layout.addWidget(self.progress)

        self.build_btn = QPushButton("🚀 ЗГЕНЕРУВАТИ ДАМП")
        self.build_btn.clicked.connect(self.start_generation)
        main_layout.addWidget(self.build_btn)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        self.populate_tree()

    def select_all(self):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Checked)

    def deselect_all(self):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.CheckState.Unchecked)

    def populate_tree(self):
        self.tree.clear()
        paths = []
        for root, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(SKIP_DIR_PREFIXES)]
            for file in files:
                if file in SKIP_FILES: continue
                if Path(file).suffix in INCLUDE_EXTENSIONS or file.endswith(".json"):
                    paths.append(Path(root).relative_to(ROOT) / file)

        nodes = {}
        for p in sorted(paths):
            parent = self.tree.invisibleRootItem()
            curr_path = Path()
            for part in p.parts[:-1]:
                curr_path /= part
                if curr_path not in nodes:
                    item = QTreeWidgetItem([part])
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                    item.setCheckState(0, Qt.CheckState.Checked)
                    item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
                    parent.addChild(item)
                    nodes[curr_path] = item
                parent = nodes[curr_path]

            file_item = QTreeWidgetItem([p.name])
            file_item.setFlags(file_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            file_item.setCheckState(0, Qt.CheckState.Checked)
            file_item.setData(0, Qt.ItemDataRole.UserRole, str(p).replace("\\", "/"))
            file_item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
            parent.addChild(file_item)

        self.tree.expandAll()
        self.status.showMessage(f"Знайдено файлів: {len(paths)}")

    def filter_tree(self, text):
        query = text.lower()

        def _filter(node):
            match = query in node.text(0).lower()
            child_match = any(_filter(node.child(i)) for i in range(node.childCount()))
            node.setHidden(not (match or child_match))
            return match or child_match

        for i in range(self.tree.topLevelItemCount()): _filter(self.tree.topLevelItem(i))

    def start_generation(self):
        selected = []

        def _collect(node):
            if node.childCount() == 0 and node.checkState(0) == Qt.CheckState.Checked:
                p = node.data(0, Qt.ItemDataRole.UserRole)
                if p: selected.append(p)
            for i in range(node.childCount()): _collect(node.child(i))

        for i in range(self.tree.topLevelItemCount()): _collect(self.tree.topLevelItem(i))

        if not selected: return QMessageBox.warning(self, "Увага", "Виберіть хоча б один файл!")

        is_repo_mode = self.mode_combo.currentIndex() == 0
        mode = "REPO_PROMPT" if is_repo_mode else "STANDARD"

        self.log_edit.clear()
        self.log_edit.append(f"<b>Режим: {mode}</b>")
        self.build_btn.setEnabled(False)
        self.progress.setValue(0)

        self._worker = DumpWorker(selected, ROOT / self.filename_input.text(), mode)
        self._worker.log.connect(lambda t: self.log_edit.append(t))
        self._worker.progress.connect(self.progress.setValue)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, path, lines, size_kb, tokens):
        self.log_edit.append(f"<br><font color='#a6e3a1'><b>🎉 УСПІХ! ГЕНЕРАЦІЮ ЗАВЕРШЕНО.</b></font>")
        self.log_edit.append(f"📄 Рядків: {lines:,}")
        self.log_edit.append(f"📏 Розмір: {size_kb:.1f} KB")

        # Підсвітка токенів: синій (норма), помаранчевий (багато)
        token_color = "#89b4fa" if tokens < 100000 else "#fab387"
        self.log_edit.append(f"🧠 <b>Токенів (cl100k): <span style='color:{token_color};'>{tokens:,}</span></b>")

        self.build_btn.setEnabled(True)
        self.progress.setValue(100)
        self.status.showMessage(f"✅ Готово! Токенів: {tokens:,}")

        if not HAS_TIKTOKEN:
            self.log_edit.append(
                "<br><small>⚠️ Примітка: Встановіть 'pip install tiktoken' для точного підрахунку.</small>")

    def _on_error(self, err):
        self.log_edit.append(f"<br><font color='#f38ba8'>❌ ПОМИЛКА: {err}</font>")
        self.build_btn.setEnabled(True)
        QMessageBox.critical(self, "Критична помилка", err)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DumpApp()
    window.show()
    sys.exit(app.exec())