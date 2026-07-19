# deploy_ui.py
# Graphical interface for packaging and deploying Arbix Quantum P2P Scanner to remote server

import os
import sys
import time
from pathlib import Path
import hashlib
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QProgressBar,
    QStatusBar, QMessageBox, QFrame, QSplitter
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

# Import paramiko and scp for native SSH/SCP handling
import paramiko
from scp import SCPClient

ROOT = Path(__file__).parent
ARCHIVE_NAME = "arbix-quantum.tar.gz"

STYLE = """
QMainWindow, QWidget {
    background-color: #0d1117;
    color: #e6edf3;
    font-family: 'Consolas', 'JetBrains Mono', 'Courier New', monospace;
    font-size: 13px;
}
QLabel { 
    color: #8b949e; 
    font-size: 12px; 
    font-weight: bold; 
}
QLineEdit {
    background-color: #161b22; 
    border: 1px solid #30363d;
    padding: 6px; 
    border-radius: 4px; 
    color: #cdd6f4;
}
QLineEdit:focus {
    border: 1px solid #58a6ff;
}
QPushButton {
    background-color: #21262d; 
    color: #c9d1d9; 
    font-weight: bold;
    padding: 8px 16px; 
    border-radius: 4px;
    border: 1px solid #30363d;
}
QPushButton:hover { 
    background-color: #30363d; 
}
QPushButton#actionBtn {
    background-color: #238636; 
    color: #ffffff;
    border: 1px solid rgba(240,246,252,0.1);
}
QPushButton#actionBtn:hover { 
    background-color: #2ea043; 
}
QPushButton#dangerBtn {
    background-color: #842029; 
    color: #ea868f;
    border: 1px solid #f85149;
}
QPushButton#dangerBtn:hover { 
    background-color: #b32f3a; 
}
QPushButton:disabled { 
    background-color: #161b22; 
    color: #8b949e; 
    border-color: #21262d;
}
QTextEdit {
    background-color: #0d1117; 
    border: 1px solid #30363d; 
    border-radius: 4px;
    color: #c9d1d9;
}
QProgressBar {
    border: 1px solid #30363d; 
    border-radius: 4px;
    text-align: center; 
    background-color: #161b22;
    color: #ffffff;
    font-weight: bold;
}
QProgressBar::chunk { 
    background-color: #238636; 
}
QStatusBar { 
    border-top: 1px solid #30363d; 
    background-color: #161b22; 
    color: #8b949e;
}
QFrame#panel {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 10px;
}
"""

class RunCommandWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    finished_status = pyqtSignal(bool, str)

    def __init__(self, task_type, args=None):
        super().__init__()
        self.task_type = task_type
        self.args = args or {}

    def get_file_md5(self, path):
        h = hashlib.md5()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest().upper()

    def run(self):
        if self.task_type == "BUILD":
            self.run_build()
        else:
            self.run_remote_task()

    def run_build(self):
        self.log.emit("🔧 Starting archive package build process...")
        self.progress.emit(10)
        
        try:
            # Cleanup old archive
            archive_path = ROOT / ARCHIVE_NAME
            if archive_path.exists():
                archive_path.unlink()
            
            self.progress.emit(25)
            # Create temp directory
            temp_dir = ROOT / "deploy_temp"
            if temp_dir.exists():
                import shutil
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy source directories
            dirs_to_copy = ["api", "bot", "config", "core", "exchanges", "filters", "infrastructure", "tests", "tools"]
            files_to_copy = ["Dockerfile", "docker-compose.yml", "main.py", "scanner.py", "state.py", "requirements.txt", "deploy.ps1", "deploy_ui.py", ".dockerignore"]
            
            total_items = len(dirs_to_copy) + len(files_to_copy)
            processed_items = 0
            
            for item in dirs_to_copy:
                src = ROOT / item
                if src.exists():
                    import shutil
                    self.log.emit(f"   Copying directory: {item}...")
                    shutil.copytree(src, temp_dir / item, dirs_exist_ok=True)
                processed_items += 1
                self.progress.emit(25 + int((processed_items / total_items) * 40))

            for item in files_to_copy:
                src = ROOT / item
                if src.exists():
                    import shutil
                    self.log.emit(f"   Copying file: {item}...")
                    shutil.copy2(src, temp_dir / item)
                processed_items += 1
                self.progress.emit(25 + int((processed_items / total_items) * 40))

            # Cleanup heavy browser profiles in copy
            browser_profiles = temp_dir / "tools" / "script" / "data" / "browser_profiles"
            if browser_profiles.exists():
                import shutil
                self.log.emit("   Cleaning up browser profiles from package context...")
                shutil.rmtree(browser_profiles)

            self.progress.emit(75)
            self.log.emit("📦 Compressing into tar.gz archive...")
            
            import tarfile
            with tarfile.open(archive_path, "w:gz") as tar:
                for file_path in temp_dir.iterdir():
                    tar.add(file_path, arcname=file_path.name)
            
            # Cleanup temp dir
            import shutil
            shutil.rmtree(temp_dir)
            
            self.progress.emit(95)
            
            if archive_path.exists():
                h = self.get_file_md5(archive_path)
                s = round(archive_path.stat().st_size / (1024 * 1024), 2)
                self.log.emit(f"✅ Success! Created: {ARCHIVE_NAME}")
                self.log.emit(f"   MD5 Hash: {h}")
                self.log.emit(f"   Size: {s} MB")
                self.progress.emit(100)
                self.finished_status.emit(True, f"BUILD_OK|{h}|{s}")
            else:
                self.log.emit("❌ Failed to create tar.gz archive.")
                self.finished_status.emit(False, "Failed to create archive.")
                
        except Exception as e:
            self.log.emit(f"❌ Build Error: {str(e)}")
            self.finished_status.emit(False, str(e))

    def connect_ssh(self):
        host_input = self.args.get("host", "").strip()
        password = self.args.get("password", "").strip()
        
        # Parse Host
        username = "root"
        ip = "127.0.0.1"
        port = 22
        
        if "@" in host_input:
            username, host_part = host_input.split("@", 1)
        else:
            host_part = host_input
            
        if ":" in host_part:
            ip, port_str = host_part.split(":", 1)
            port = int(port_str)
        else:
            ip = host_part
            
        self.log.emit(f"🔑 Establishing SSH connection to {ip}:{port} as '{username}'...")
        
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Connect using password if provided, else rely on keys
        if password:
            ssh.connect(ip, port=port, username=username, password=password, timeout=15)
        else:
            ssh.connect(ip, port=port, username=username, timeout=15)
            
        return ssh

    def run_ssh_command(self, ssh_client, cmd):
        try:
            stdin, stdout, stderr = ssh_client.exec_command(cmd, get_pty=True)
            
            # Read stdout line by line
            while True:
                line = stdout.readline()
                if not line:
                    break
                self.log.emit(f"   [Remote] {line.strip()}")
                
            exit_status = stdout.channel.recv_exit_status()
            return exit_status == 0
        except Exception as e:
            self.log.emit(f"   [SSH Command Error] {str(e)}")
            return False

    def run_remote_task(self):
        target_dir = self.args.get("dir", "/root/app")
        archive_path = ROOT / ARCHIVE_NAME
        
        ssh = None
        try:
            ssh = self.connect_ssh()
            self.log.emit("✅ SSH connection established.")
            self.progress.emit(25)
            
            if self.task_type == "DEPLOY":
                if not archive_path.exists():
                    self.log.emit("❌ Error: Archive not found. Please build it first.")
                    self.finished_status.emit(False, "Archive missing.")
                    return
                
                h = self.get_file_md5(archive_path)
                
                # 1. SCP Upload with custom progress tracking
                self.log.emit("📤 Uploading archive via SCP...")
                
                last_percent = [-1]
                def scp_progress(filename, size, sent):
                    pct = int(sent / size * 100)
                    if (pct % 10 == 0 or pct == 100) and pct != last_percent[0]:
                        last_percent[0] = pct
                        self.log.emit(f"   Uploading... {pct}% ({round(sent/(1024*1024), 2)}MB / {round(size/(1024*1024), 2)}MB)")
                    self.progress.emit(25 + int(pct * 0.35)) # Map 0-100% of upload to 25-60% of total
                
                with SCPClient(ssh.get_transport(), progress=scp_progress) as scp:
                    scp.put(str(archive_path), f"/root/{ARCHIVE_NAME}")
                    
                self.log.emit("✅ Archive upload complete.")
                self.progress.emit(65)
                
                # 2. Extract and Rebuild Containers
                self.log.emit("⚙️ Extracting and rebuilding containers on VPS...")
                
                cmds = []
                cmds.append("echo '=== Integrity check ==='")
                cmds.append(f"SERVER_MD5=$(md5sum /root/{ARCHIVE_NAME} | awk '{{print toupper($1)}}')")
                cmds.append(f"echo 'Local MD5: {h}'")
                cmds.append("echo \"Server MD5: $SERVER_MD5\"")
                cmds.append(f"if [ \"$SERVER_MD5\" != \"{h}\" ]; then echo 'Error: MD5 mismatch!' && exit 1; fi")
                cmds.append(f"mkdir -p {target_dir}")
                cmds.append(f"tar -xzf /root/{ARCHIVE_NAME} -C {target_dir}")
                cmds.append(f"rm -f /root/{ARCHIVE_NAME}")
                cmds.append(f"cd {target_dir}")
                cmds.append("docker compose up --build -d")
                cmds.append("docker compose ps")
                
                joined_cmd = " && ".join(cmds)
                
                self.progress.emit(75)
                if self.run_ssh_command(ssh, joined_cmd):
                    self.log.emit("🎉 Deployment completed successfully!")
                    self.progress.emit(100)
                    self.finished_status.emit(True, "DEPLOY_OK")
                else:
                    self.log.emit("❌ Command execution failed on server.")
                    self.finished_status.emit(False, "Execution failed.")
                    
            elif self.task_type == "STATUS":
                self.progress.emit(50)
                if self.run_ssh_command(ssh, f"cd {target_dir} && docker compose ps"):
                    self.progress.emit(100)
                    self.finished_status.emit(True, "STATUS_OK")
                else:
                    self.finished_status.emit(False, "Status query failed.")
                    
            elif self.task_type == "LOGS":
                self.progress.emit(50)
                if self.run_ssh_command(ssh, f"cd {target_dir} && docker compose logs --tail=100"):
                    self.progress.emit(100)
                    self.finished_status.emit(True, "LOGS_OK")
                else:
                    self.finished_status.emit(False, "Logs query failed.")
                    
        except Exception as e:
            self.log.emit(f"❌ Remote Task Error: {str(e)}")
            self.finished_status.emit(False, str(e))
        finally:
            if ssh:
                ssh.close()
                self.log.emit("🔌 SSH connection closed.")


class DeployApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Arbix Quantum Deployment Hub")
        self.resize(950, 720)
        self.setStyleSheet(STYLE)
        self._worker = None
        self.init_ui()
        self.update_archive_info()

    def init_ui(self):
        # Central widget and layout
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        # Header Label
        header_title = QLabel("🤖 ARBIX QUANTUM DEPLOYMENT MANAGER")
        header_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #58a6ff;")
        main_layout.addWidget(header_title)

        # Main splitter (left controls, right logger)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # Left panel: Configuration & Actions
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(15)
        splitter.addWidget(left_widget)

        # Panel 1: Local Archive Status
        local_frame = QFrame()
        local_frame.setObjectName("panel")
        local_layout = QVBoxLayout(local_frame)
        local_layout.addWidget(QLabel("📦 LOCAL BUILD CONFIGURATION"))
        
        h_layout_archive = QHBoxLayout()
        h_layout_archive.addWidget(QLabel("Archive:"))
        self.archive_input = QLineEdit(ARCHIVE_NAME)
        self.archive_input.setReadOnly(True)
        h_layout_archive.addWidget(self.archive_input)
        local_layout.addLayout(h_layout_archive)

        h_layout_size = QHBoxLayout()
        self.label_size = QLabel("Size: Unknown")
        self.label_hash = QLabel("MD5 Hash: Unknown")
        h_layout_size.addWidget(self.label_size)
        h_layout_size.addWidget(self.label_hash)
        local_layout.addLayout(h_layout_size)

        self.build_btn = QPushButton("🔧 Package & Rebuild Archive")
        self.build_btn.clicked.connect(self.start_build)
        local_layout.addWidget(self.build_btn)

        left_layout.addWidget(local_frame)

        # Panel 2: Remote VPS Settings
        remote_frame = QFrame()
        remote_frame.setObjectName("panel")
        remote_layout = QVBoxLayout(remote_frame)
        remote_layout.addWidget(QLabel("🌐 REMOTE TARGET VPS CONFIG"))

        h_layout_host = QHBoxLayout()
        h_layout_host.addWidget(QLabel("Host (User@IP):"))
        self.host_input = QLineEdit("root@167.233.147.232")
        h_layout_host.addWidget(self.host_input)
        remote_layout.addLayout(h_layout_host)

        h_layout_pass = QHBoxLayout()
        h_layout_pass.addWidget(QLabel("Password (optional):"))
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        h_layout_pass.addWidget(self.pass_input)
        
        # Toggle password visibility button
        self.toggle_pass_btn = QPushButton("👁️")
        self.toggle_pass_btn.setFixedWidth(38)
        self.toggle_pass_btn.clicked.connect(self.toggle_password_visibility)
        h_layout_pass.addWidget(self.toggle_pass_btn)
        
        remote_layout.addLayout(h_layout_pass)

        h_layout_dir = QHBoxLayout()
        h_layout_dir.addWidget(QLabel("Target Directory:"))
        self.dir_input = QLineEdit("/root/app")
        h_layout_dir.addWidget(self.dir_input)
        remote_layout.addLayout(h_layout_dir)

        # Deploy & Action buttons
        self.deploy_btn = QPushButton("🚀 Upload & Build On Server")
        self.deploy_btn.setObjectName("actionBtn")
        self.deploy_btn.clicked.connect(self.start_deploy)
        remote_layout.addWidget(self.deploy_btn)

        left_layout.addWidget(remote_frame)

        # Panel 3: Remote Server Control Utilities
        util_frame = QFrame()
        util_frame.setObjectName("panel")
        util_layout = QVBoxLayout(util_frame)
        util_layout.addWidget(QLabel("🛠️ VPS UTILITIES & DIAGNOSTICS"))
        
        self.status_btn = QPushButton("🐳 Check Docker Containers Status")
        self.status_btn.clicked.connect(self.check_server_status)
        util_layout.addWidget(self.status_btn)

        self.logs_btn = QPushButton("📄 Fetch Docker Logs (Tail 100)")
        self.logs_btn.clicked.connect(self.tail_server_logs)
        util_layout.addWidget(self.logs_btn)

        left_layout.addWidget(util_frame)
        left_layout.addStretch()

        # Right panel: Console Log outputs
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        splitter.addWidget(right_widget)

        right_layout.addWidget(QLabel("📝 LIVE EXECUTION LOGS"))
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        right_layout.addWidget(self.log_edit)

        # Bottom section: Progress and Status
        self.progress = QProgressBar()
        self.progress.setValue(0)
        main_layout.addWidget(self.progress)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready")

        # Set default splitter sizes
        splitter.setSizes([400, 550])

    def toggle_password_visibility(self):
        if self.pass_input.echoMode() == QLineEdit.EchoMode.Password:
            self.pass_input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_pass_btn.setText("🔒")
        else:
            self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.toggle_pass_btn.setText("👁️")

    def update_archive_info(self):
        archive_path = ROOT / ARCHIVE_NAME
        if archive_path.exists():
            s = round(archive_path.stat().st_size / (1024 * 1024), 2)
            self.label_size.setText(f"Size: {s} MB")
            
            # Compute MD5
            h = hashlib.md5()
            with open(archive_path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    h.update(chunk)
            self.label_hash.setText(f"MD5: {h.hexdigest().upper()}")
        else:
            self.label_size.setText("Size: Not found")
            self.label_hash.setText("MD5: Not found")

    def log_text(self, text):
        self.log_edit.append(text)
        # Scroll to bottom
        cursor = self.log_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log_edit.setTextCursor(cursor)

    def set_buttons_enabled(self, enabled):
        self.build_btn.setEnabled(enabled)
        self.deploy_btn.setEnabled(enabled)
        self.status_btn.setEnabled(enabled)
        self.logs_btn.setEnabled(enabled)
        self.host_input.setEnabled(enabled)
        self.pass_input.setEnabled(enabled)
        self.toggle_pass_btn.setEnabled(enabled)
        self.dir_input.setEnabled(enabled)

    def start_build(self):
        self.set_buttons_enabled(False)
        self.log_edit.clear()
        self.progress.setValue(0)
        self.status.showMessage("Building package archive...")

        self._worker = RunCommandWorker("BUILD")
        self._worker.log.connect(self.log_text)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.finished_status.connect(self.on_build_finished)
        self._worker.start()

    def on_build_finished(self, success, details):
        self.set_buttons_enabled(True)
        if success:
            _, h, s = details.split("|")
            self.status.showMessage(f"✅ Archive built successfully! MD5: {h}")
            self.update_archive_info()
            QMessageBox.information(self, "Success", f"Archive built successfully!\nMD5: {h}\nSize: {s} MB")
        else:
            self.status.showMessage("❌ Build failed!")
            QMessageBox.critical(self, "Error", f"Build failed:\n{details}")

    def start_deploy(self):
        host = self.host_input.text().strip()
        password = self.pass_input.text()
        directory = self.dir_input.text().strip()

        if not host:
            return QMessageBox.warning(self, "Warning", "Please provide a valid Host parameter.")
        if not directory:
            return QMessageBox.warning(self, "Warning", "Please provide a valid target directory.")

        archive_path = ROOT / ARCHIVE_NAME
        if not archive_path.exists():
            return QMessageBox.warning(self, "Warning", "Archive not found! Please compile/build it first.")

        self.set_buttons_enabled(False)
        self.log_edit.clear()
        self.progress.setValue(0)
        self.status.showMessage("Deploying application...")

        args = {"host": host, "password": password, "dir": directory}
        self._worker = RunCommandWorker("DEPLOY", args)
        self._worker.log.connect(self.log_text)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.finished_status.connect(self.on_deploy_finished)
        self._worker.start()

    def on_deploy_finished(self, success, details):
        self.set_buttons_enabled(True)
        if success:
            self.status.showMessage("✅ Deployment finished successfully!")
            QMessageBox.information(self, "Success", "Deployment finished successfully! Container is starting.")
        else:
            self.status.showMessage("❌ Deployment failed!")
            QMessageBox.critical(self, "Error", f"Deployment failed:\n{details}")

    def check_server_status(self):
        host = self.host_input.text().strip()
        password = self.pass_input.text()
        directory = self.dir_input.text().strip()

        if not host or not directory:
            return QMessageBox.warning(self, "Warning", "Please configure Host and Directory first.")

        self.set_buttons_enabled(False)
        self.log_edit.clear()
        self.progress.setValue(0)
        self.status.showMessage("Checking Docker status on remote server...")

        args = {"host": host, "password": password, "dir": directory}
        self._worker = RunCommandWorker("STATUS", args)
        self._worker.log.connect(self.log_text)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.finished_status.connect(self.on_util_finished)
        self._worker.start()

    def tail_server_logs(self):
        host = self.host_input.text().strip()
        password = self.pass_input.text()
        directory = self.dir_input.text().strip()

        if not host or not directory:
            return QMessageBox.warning(self, "Warning", "Please configure Host and Directory first.")

        self.set_buttons_enabled(False)
        self.log_edit.clear()
        self.progress.setValue(0)
        self.status.showMessage("Fetching Docker logs from remote server...")

        args = {"host": host, "password": password, "dir": directory}
        self._worker = RunCommandWorker("LOGS", args)
        self._worker.log.connect(self.log_text)
        self._worker.progress.connect(self.progress.setValue)
        self._worker.finished_status.connect(self.on_util_finished)
        self._worker.start()

    def on_util_finished(self, success, details):
        self.set_buttons_enabled(True)
        if success:
            self.status.showMessage("✅ Remote command finished.")
        else:
            self.status.showMessage("❌ Remote command failed.")
            QMessageBox.critical(self, "Error", f"Command failed:\n{details}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DeployApp()
    window.show()
    sys.exit(app.exec())