# deploy_ui.py
# Деплой Arbix Quantum: сканер (бот) і вебдашборд з одного вікна.
#
# Два компоненти їдуть на сервер по-різному, і це навмисно.
#
# Бот — це Python-образ: пакуємо джерела в архів, заливаємо, збираємо
# образ на сервері. pip кешується шарами, тож перезбірка швидка.
#
# Дашборд — статика. Vite збирає її локально за секунди, на сервер їде
# готовий dist/, який Caddy роздає з bind-mount. Складати образ там
# означало б ганяти npm ci на кожен передеплой: хвилини часу й реальний
# ризик впертись у пам'ять на маленькому VPS.

import os
import sys
import shutil
import tarfile
import hashlib
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar, QCheckBox,
    QStatusBar, QMessageBox, QFrame, QSplitter, QInputDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

import paramiko
from scp import SCPClient

ROOT = Path(__file__).parent
ARCHIVE_NAME = "arbix-quantum.tar.gz"
FRONTEND_ARCHIVE = "arbix-dashboard.tar.gz"

# Каталог фронтенду. Лежить поруч із ботом, бо це окрема гілка того
# самого репозиторію, викачана в сусідню папку.
FRONTEND_ROOT = ROOT.parent / "arbix-quantum-p2p-scanner"

STYLE = """
QMainWindow, QWidget {
    background-color: #0d1117;
    color: #e6edf3;
    font-family: 'Consolas', 'JetBrains Mono', 'Courier New', monospace;
    font-size: 13px;
}
QLabel { color: #8b949e; font-size: 12px; font-weight: bold; }
QLineEdit {
    background-color: #161b22; border: 1px solid #30363d;
    padding: 6px; border-radius: 4px; color: #cdd6f4;
}
QLineEdit:focus { border: 1px solid #58a6ff; }
QCheckBox { color: #c9d1d9; font-size: 12px; spacing: 8px; }
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 3px;
    border: 1px solid #30363d; background-color: #161b22;
}
QCheckBox::indicator:checked { background-color: #238636; border-color: #2ea043; }
QPushButton {
    background-color: #21262d; color: #c9d1d9; font-weight: bold;
    padding: 8px 16px; border-radius: 4px; border: 1px solid #30363d;
}
QPushButton:hover { background-color: #30363d; }
QPushButton#actionBtn {
    background-color: #238636; color: #ffffff;
    border: 1px solid rgba(240,246,252,0.1);
}
QPushButton#actionBtn:hover { background-color: #2ea043; }
QPushButton#dangerBtn {
    background-color: #842029; color: #ea868f; border: 1px solid #f85149;
}
QPushButton#dangerBtn:hover { background-color: #b32f3a; }
QPushButton:disabled {
    background-color: #161b22; color: #8b949e; border-color: #21262d;
}
QTextEdit {
    background-color: #0d1117; border: 1px solid #30363d;
    border-radius: 4px; color: #c9d1d9;
}
QProgressBar {
    border: 1px solid #30363d; border-radius: 4px;
    text-align: center; background-color: #161b22;
    color: #ffffff; font-weight: bold;
}
QProgressBar::chunk { background-color: #238636; }
QStatusBar {
    border-top: 1px solid #30363d; background-color: #161b22; color: #8b949e;
}
QFrame#panel {
    background-color: #161b22; border: 1px solid #30363d;
    border-radius: 6px; padding: 10px;
}
"""


class Worker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished_status = pyqtSignal(bool, str)

    def __init__(self, task_type, args=None):
        super().__init__()
        self.task_type = task_type
        self.args = args or {}

    # ─── Допоміжне ───────────────────────────────────────────────────────

    @staticmethod
    def md5(path: Path) -> str:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest().upper()

    def run(self):
        handlers = {
            "BUILD_BOT": self.build_bot,
            "BUILD_FRONT": self.build_front,
            "DEPLOY": self.deploy,
            "CONTROL": self.control,
        }
        handler = handlers.get(self.task_type)
        if handler:
            handler()
        else:
            self.finished_status.emit(False, f"Невідома задача: {self.task_type}")

    # ─── Локальна збірка ─────────────────────────────────────────────────

    def build_bot(self):
        self.log.emit("🔧 Пакую джерела сканера...")
        self.progress.emit(10)

        try:
            archive_path = ROOT / ARCHIVE_NAME
            if archive_path.exists():
                archive_path.unlink()

            temp_dir = ROOT / "deploy_temp"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)

            dirs = ["api", "bot", "config", "core", "exchanges", "filters",
                    "infrastructure", "tests", "tools"]
            files = ["Dockerfile", "docker-compose.yml", "main.py", "scanner.py",
                     "state.py", "requirements.txt", "deploy_ui.py", ".dockerignore"]

            total = len(dirs) + len(files)
            done = 0

            for item in dirs:
                src = ROOT / item
                if src.exists():
                    self.log.emit(f"   Каталог: {item}")
                    shutil.copytree(src, temp_dir / item, dirs_exist_ok=True)
                done += 1
                self.progress.emit(25 + int(done / total * 40))

            for item in files:
                src = ROOT / item
                if src.exists():
                    self.log.emit(f"   Файл: {item}")
                    shutil.copy2(src, temp_dir / item)
                done += 1
                self.progress.emit(25 + int(done / total * 40))

            # Профілі браузера важать сотні мегабайт і на сервері не потрібні.
            profiles = temp_dir / "tools" / "script" / "data" / "browser_profiles"
            if profiles.exists():
                self.log.emit("   Викидаю профілі браузера з пакета...")
                shutil.rmtree(profiles)

            self.progress.emit(75)
            self.log.emit("📦 Стискаю...")

            with tarfile.open(archive_path, "w:gz") as tar:
                for path in temp_dir.iterdir():
                    tar.add(path, arcname=path.name)

            shutil.rmtree(temp_dir)
            self.progress.emit(95)

            size = round(archive_path.stat().st_size / (1024 * 1024), 2)
            self.log.emit(f"✅ {ARCHIVE_NAME} — {size} МБ, MD5 {self.md5(archive_path)}")
            self.progress.emit(100)
            self.finished_status.emit(True, "BUILD_BOT_OK")

        except Exception as e:
            self.log.emit(f"❌ Помилка збірки сканера: {e}")
            self.finished_status.emit(False, str(e))

    def build_front(self):
        """Vite збирає локально, на сервер поїде тільки dist/."""
        self.log.emit("🔧 Збираю дашборд (npm run build)...")
        self.progress.emit(10)

        if not FRONTEND_ROOT.exists():
            self.log.emit(f"❌ Каталог фронтенду не знайдено: {FRONTEND_ROOT}")
            self.finished_status.emit(False, "Frontend not found")
            return

        try:
            # npm на Windows це .cmd, тому shell=True — інакше FileNotFoundError.
            result = subprocess.run(
                "npm run build",
                cwd=str(FRONTEND_ROOT),
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )

            for line in (result.stdout or "").splitlines()[-25:]:
                if line.strip():
                    self.log.emit(f"   {line.strip()}")

            if result.returncode != 0:
                for line in (result.stderr or "").splitlines()[-20:]:
                    if line.strip():
                        self.log.emit(f"   [err] {line.strip()}")
                self.log.emit("❌ Збірка впала — на сервер нічого не поїде.")
                self.finished_status.emit(False, "npm build failed")
                return

            self.progress.emit(60)

            dist = FRONTEND_ROOT / "dist"
            if not dist.exists():
                self.log.emit("❌ dist/ не з'явився після збірки.")
                self.finished_status.emit(False, "dist missing")
                return

            archive_path = ROOT / FRONTEND_ARCHIVE
            if archive_path.exists():
                archive_path.unlink()

            self.log.emit("📦 Пакую dist і конфіг Caddy...")
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(dist, arcname="dist")
                tar.add(FRONTEND_ROOT / "docker-compose.yml", arcname="docker-compose.yml")
                tar.add(FRONTEND_ROOT / "deploy" / "Caddyfile", arcname="deploy/Caddyfile")

            size = round(archive_path.stat().st_size / 1024, 1)
            self.log.emit(f"✅ {FRONTEND_ARCHIVE} — {size} КБ")
            self.progress.emit(100)
            self.finished_status.emit(True, "BUILD_FRONT_OK")

        except subprocess.TimeoutExpired:
            self.log.emit("❌ Збірка не вклалась у 10 хвилин.")
            self.finished_status.emit(False, "build timeout")
        except Exception as e:
            self.log.emit(f"❌ Помилка збірки дашборду: {e}")
            self.finished_status.emit(False, str(e))

    # ─── SSH ─────────────────────────────────────────────────────────────

    def connect_ssh(self):
        host_input = self.args.get("host", "").strip()
        password = self.args.get("password", "").strip()

        username, ip, port = "root", "127.0.0.1", 22

        if "@" in host_input:
            username, host_part = host_input.split("@", 1)
        else:
            host_part = host_input

        if ":" in host_part:
            ip, port_str = host_part.split(":", 1)
            port = int(port_str)
        else:
            ip = host_part

        self.log.emit(f"🔑 Підключаюсь до {ip}:{port} як '{username}'...")

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if password:
            ssh.connect(ip, port=port, username=username, password=password, timeout=15)
        else:
            ssh.connect(ip, port=port, username=username, timeout=15)

        return ssh

    def run_remote(self, ssh, cmd, quiet=False):
        try:
            _in, out, _err = ssh.exec_command(cmd, get_pty=True)
            while True:
                line = out.readline()
                if not line:
                    break
                if not quiet:
                    self.log.emit(f"   [сервер] {line.rstrip()}")
            return out.channel.recv_exit_status() == 0
        except Exception as e:
            self.log.emit(f"   [SSH] {e}")
            return False

    def upload(self, ssh, local: Path, remote: str, span=(0, 100)):
        """SCP із прогресом, змапованим у заданий діапазон смуги."""
        lo, hi = span
        last = [-1]

        def cb(_filename, size, sent):
            pct = int(sent / size * 100) if size else 100
            if pct != last[0] and (pct % 10 == 0 or pct == 100):
                last[0] = pct
                mb = round(sent / (1024 * 1024), 2)
                total_mb = round(size / (1024 * 1024), 2)
                self.log.emit(f"   Заливаю... {pct}% ({mb} / {total_mb} МБ)")
            self.progress.emit(lo + int(pct / 100 * (hi - lo)))

        with SCPClient(ssh.get_transport(), progress=cb) as scp:
            scp.put(str(local), remote)

    # ─── Деплой ──────────────────────────────────────────────────────────

    def deploy(self):
        bot_dir = self.args.get("bot_dir", "/root/app")
        front_dir = self.args.get("front_dir", "/root/arbix-dashboard")
        do_bot = self.args.get("deploy_bot", False)
        do_front = self.args.get("deploy_front", False)
        full_rebuild = self.args.get("front_full", False)

        ssh = None
        try:
            ssh = self.connect_ssh()
            self.log.emit("✅ SSH встановлено.")

            if do_bot:
                archive = ROOT / ARCHIVE_NAME
                if not archive.exists():
                    self.log.emit("❌ Архів сканера не зібраний.")
                    self.finished_status.emit(False, "bot archive missing")
                    return

                digest = self.md5(archive)
                self.log.emit("📤 Заливаю сканер...")
                self.upload(ssh, archive, f"/root/{ARCHIVE_NAME}", span=(5, 40))

                cmds = [
                    # Цілісність перевіряємо до розпакування: побитий архів
                    # інакше зніс би робочі файли наполовину розпакованими.
                    f"SERVER_MD5=$(md5sum /root/{ARCHIVE_NAME} | awk '{{print toupper($1)}}')",
                    f"if [ \"$SERVER_MD5\" != \"{digest}\" ]; then echo 'MD5 не збігається!' && exit 1; fi",
                    f"mkdir -p {bot_dir}",
                    f"tar -xzf /root/{ARCHIVE_NAME} -C {bot_dir}",
                    f"rm -f /root/{ARCHIVE_NAME}",
                    f"cd {bot_dir}",
                    "docker compose up --build -d",
                ]
                self.log.emit("⚙️ Перезбираю сканер на сервері...")
                self.progress.emit(45)
                if not self.run_remote(ssh, " && ".join(cmds)):
                    self.finished_status.emit(False, "bot deploy failed")
                    return
                self.log.emit("✅ Сканер оновлено.")

            if do_front:
                archive = ROOT / FRONTEND_ARCHIVE
                if not archive.exists():
                    self.log.emit("❌ Пакет дашборду не зібраний.")
                    self.finished_status.emit(False, "front archive missing")
                    return

                self.log.emit("📤 Заливаю дашборд...")
                self.upload(ssh, archive, f"/root/{FRONTEND_ARCHIVE}", span=(60, 85))

                cmds = [
                    f"mkdir -p {front_dir}",
                    # Стару статику зносимо: інакше файли видалених чанків
                    # лишались би назавжди й каталог тільки ріс.
                    f"rm -rf {front_dir}/dist",
                    f"tar -xzf /root/{FRONTEND_ARCHIVE} -C {front_dir}",
                    f"rm -f /root/{FRONTEND_ARCHIVE}",
                    f"cd {front_dir}",
                ]

                if full_rebuild:
                    # Потрібно лише коли змінився Caddyfile або compose.
                    cmds.append("docker compose up -d --force-recreate")
                else:
                    # Статика змонтована в контейнер — Caddy віддає нові
                    # файли одразу, перезапускати нічого не треба.
                    cmds.append("docker compose up -d")

                self.log.emit("⚙️ Оновлюю дашборд...")
                self.progress.emit(90)
                if not self.run_remote(ssh, " && ".join(cmds)):
                    self.finished_status.emit(False, "front deploy failed")
                    return
                self.log.emit("✅ Дашборд оновлено.")

            self.progress.emit(100)
            self.finished_status.emit(True, "DEPLOY_OK")

        except Exception as e:
            self.log.emit(f"❌ Помилка деплою: {e}")
            self.finished_status.emit(False, str(e))
        finally:
            if ssh:
                ssh.close()
                self.log.emit("🔌 SSH закрито.")

    # ─── Керування ───────────────────────────────────────────────────────

    def control(self):
        action = self.args.get("action")
        bot_dir = self.args.get("bot_dir", "/root/app")
        front_dir = self.args.get("front_dir", "/root/arbix-dashboard")
        target = self.args.get("target", "both")

        dirs = []
        if target in ("bot", "both"):
            dirs.append(("сканер", bot_dir))
        if target in ("front", "both"):
            dirs.append(("дашборд", front_dir))

        ssh = None
        try:
            ssh = self.connect_ssh()
            self.progress.emit(30)

            if action == "reboot":
                self.log.emit("🔄 Перезавантажую сервер...")
                # reboot рве з'єднання — ненульовий код тут нормальний.
                self.run_remote(ssh, "nohup sh -c 'sleep 1; reboot' >/dev/null 2>&1 &")
                self.log.emit("   Команду надіслано. Сервер підніметься за хвилину-дві.")
                self.progress.emit(100)
                self.finished_status.emit(True, "REBOOT_SENT")
                return

            if action == "shutdown":
                self.log.emit("⛔ Вимикаю сервер...")
                self.run_remote(ssh, "nohup sh -c 'sleep 1; poweroff' >/dev/null 2>&1 &")
                self.log.emit("   Сервер вимикається. Підняти можна лише з панелі провайдера.")
                self.progress.emit(100)
                self.finished_status.emit(True, "SHUTDOWN_SENT")
                return

            if action == "disk":
                self.run_remote(ssh, "df -h / && echo '---' && free -h && echo '---' && docker system df")
                self.progress.emit(100)
                self.finished_status.emit(True, "DISK_OK")
                return

            command = {
                "status": "docker compose ps",
                "logs": "docker compose logs --tail=100",
                "start": "docker compose up -d",
                "stop": "docker compose stop",
                "restart": "docker compose restart",
            }.get(action)

            if not command:
                self.finished_status.emit(False, f"Невідома дія: {action}")
                return

            ok = True
            for name, path in dirs:
                self.log.emit(f"── {name} ──")
                if not self.run_remote(ssh, f"cd {path} && {command}"):
                    ok = False
                    self.log.emit(f"   ⚠️ Не вдалось: {name}")

            self.progress.emit(100)
            self.finished_status.emit(ok, f"{action.upper()}_DONE")

        except Exception as e:
            self.log.emit(f"❌ Помилка: {e}")
            self.finished_status.emit(False, str(e))
        finally:
            if ssh:
                ssh.close()


class DeployApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Arbix Quantum — деплой")
        self.resize(1050, 780)
        self.setStyleSheet(STYLE)
        self.worker = None
        self.init_ui()

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        title = QLabel("🤖 ARBIX QUANTUM — ДЕПЛОЙ")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #58a6ff;")
        layout.addWidget(title)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        splitter.addWidget(left)

        # ── Сервер ───────────────────────────────────────────────────────
        server = QFrame()
        server.setObjectName("panel")
        sl = QVBoxLayout(server)
        sl.addWidget(QLabel("🌐 СЕРВЕР"))

        row = QHBoxLayout()
        row.addWidget(QLabel("Хост:"))
        self.host_input = QLineEdit("root@167.233.147.232")
        row.addWidget(self.host_input)
        sl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Пароль:"))
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        row.addWidget(self.pass_input)
        self.toggle_pass = QPushButton("👁")
        self.toggle_pass.setFixedWidth(40)
        self.toggle_pass.clicked.connect(self.toggle_password)
        row.addWidget(self.toggle_pass)
        sl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Каталог сканера:"))
        self.bot_dir_input = QLineEdit("/root/app")
        row.addWidget(self.bot_dir_input)
        sl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Каталог дашборду:"))
        self.front_dir_input = QLineEdit("/root/arbix-dashboard")
        row.addWidget(self.front_dir_input)
        sl.addLayout(row)

        left_layout.addWidget(server)

        # ── Що деплоїмо ──────────────────────────────────────────────────
        what = QFrame()
        what.setObjectName("panel")
        wl = QVBoxLayout(what)
        wl.addWidget(QLabel("📦 ЩО ВИКОЧУЄМО"))

        self.cb_bot = QCheckBox("Сканер (бот) — перезбірка образу на сервері")
        self.cb_bot.setChecked(True)
        wl.addWidget(self.cb_bot)

        self.cb_front = QCheckBox("Дашборд — збірка локально, заливка статики")
        self.cb_front.setChecked(True)
        wl.addWidget(self.cb_front)

        self.cb_front_full = QCheckBox("   └ перестворити контейнер (якщо змінився Caddyfile)")
        wl.addWidget(self.cb_front_full)

        self.deploy_btn = QPushButton("🚀 Зібрати і викотити")
        self.deploy_btn.setObjectName("actionBtn")
        self.deploy_btn.clicked.connect(self.start_deploy)
        wl.addWidget(self.deploy_btn)

        left_layout.addWidget(what)

        # ── Керування ────────────────────────────────────────────────────
        control = QFrame()
        control.setObjectName("panel")
        cl = QVBoxLayout(control)
        cl.addWidget(QLabel("🎛 КЕРУВАННЯ"))

        grid = QGridLayout()
        buttons = [
            ("🐳 Статус", "status", 0, 0), ("📄 Логи", "logs", 0, 1),
            ("▶️ Запустити", "start", 1, 0), ("⏸ Зупинити", "stop", 1, 1),
            ("🔁 Рестарт контейнерів", "restart", 2, 0), ("💾 Диск і пам'ять", "disk", 2, 1),
        ]
        for label, action, r, c in buttons:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, a=action: self.run_control(a))
            grid.addWidget(btn, r, c)
        cl.addLayout(grid)

        row = QHBoxLayout()
        reboot = QPushButton("🔄 Перезавантажити сервер")
        reboot.clicked.connect(lambda: self.run_control("reboot", confirm="REBOOT"))
        row.addWidget(reboot)

        shutdown = QPushButton("⛔ Вимкнути сервер")
        shutdown.setObjectName("dangerBtn")
        shutdown.clicked.connect(lambda: self.run_control("shutdown", confirm="SHUTDOWN"))
        row.addWidget(shutdown)
        cl.addLayout(row)

        left_layout.addWidget(control)
        left_layout.addStretch()

        # ── Лог ──────────────────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("📜 ЖУРНАЛ"))
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        rl.addWidget(self.log_view)
        splitter.addWidget(right)
        splitter.setSizes([420, 630])

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Готовий")

    # ─── Дії ─────────────────────────────────────────────────────────────

    def toggle_password(self):
        visible = self.pass_input.echoMode() == QLineEdit.EchoMode.Normal
        self.pass_input.setEchoMode(
            QLineEdit.EchoMode.Password if visible else QLineEdit.EchoMode.Normal
        )

    def log(self, text):
        self.log_view.append(text)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    def set_busy(self, busy):
        self.deploy_btn.setEnabled(not busy)
        self.statusBar().showMessage("Працюю..." if busy else "Готовий")

    def base_args(self):
        return {
            "host": self.host_input.text().strip(),
            "password": self.pass_input.text().strip(),
            "bot_dir": self.bot_dir_input.text().strip() or "/root/app",
            "front_dir": self.front_dir_input.text().strip() or "/root/arbix-dashboard",
        }

    def run_worker(self, task, args, on_done=None):
        self.set_busy(True)
        self.progress.setValue(0)
        self.worker = Worker(task, args)
        self.worker.log.connect(self.log)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished_status.connect(on_done or self.on_generic_done)
        self.worker.start()

    def on_generic_done(self, ok, details):
        self.set_busy(False)
        if not ok:
            self.log(f"⚠️ {details}")

    def start_deploy(self):
        if not self.host_input.text().strip():
            return QMessageBox.warning(self, "Стоп", "Не заданий хост.")

        do_bot = self.cb_bot.isChecked()
        do_front = self.cb_front.isChecked()
        if not (do_bot or do_front):
            return QMessageBox.warning(self, "Стоп", "Нічого не обрано.")

        self.log_view.clear()
        self._pending = {"bot": do_bot, "front": do_front}
        self._build_next()

    def _build_next(self):
        """Збірки йдуть послідовно, потім один спільний захід на сервер."""
        if self._pending.get("bot"):
            self._pending["bot"] = False
            self.run_worker("BUILD_BOT", {}, self._on_build_done)
        elif self._pending.get("front"):
            self._pending["front"] = False
            self.run_worker("BUILD_FRONT", {}, self._on_build_done)
        else:
            args = self.base_args()
            args.update({
                "deploy_bot": self.cb_bot.isChecked(),
                "deploy_front": self.cb_front.isChecked(),
                "front_full": self.cb_front_full.isChecked(),
            })
            self.run_worker("DEPLOY", args, self._on_deploy_done)

    def _on_build_done(self, ok, details):
        if not ok:
            self.set_busy(False)
            self.log(f"⛔ Зупиняюсь: {details}")
            QMessageBox.critical(self, "Збірка впала", details)
            return
        self._build_next()

    def _on_deploy_done(self, ok, details):
        self.set_busy(False)
        if ok:
            self.log("🎉 Готово.")
            QMessageBox.information(self, "Готово", "Викочено.")
        else:
            QMessageBox.critical(self, "Не вдалось", details)

    def run_control(self, action, confirm=None):
        if not self.host_input.text().strip():
            return QMessageBox.warning(self, "Стоп", "Не заданий хост.")

        if confirm:
            # Пароль тут не питаємо навмисно: він уже введений для
            # підключення, тож повторний ввід захищає від чужої людини за
            # клавіатурою, а не від промаху пальцем — а промах тут і є
            # реальним ризиком. Від нього рятує те, чого не зробиш на
            # автоматі: набрати слово руками.
            hint = (
                "Сервер вимкнеться. Підняти його по SSH буде НЕМОЖЛИВО —\n"
                "тільки через панель провайдера.\n\nНабери SHUTDOWN, щоб підтвердити:"
                if confirm == "SHUTDOWN" else
                "Сервер перезавантажиться, сканер буде недоступний хвилину-дві.\n\n"
                "Набери REBOOT, щоб підтвердити:"
            )
            text, ok = QInputDialog.getText(self, "Підтвердження", hint)
            if not ok or text.strip() != confirm:
                self.log("⏹ Скасовано.")
                return

        args = self.base_args()
        args["action"] = action
        args["target"] = (
            "both" if (self.cb_bot.isChecked() and self.cb_front.isChecked())
            else "bot" if self.cb_bot.isChecked()
            else "front"
        )
        self.run_worker("CONTROL", args)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DeployApp()
    window.show()
    sys.exit(app.exec())
