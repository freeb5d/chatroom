"""
Chat Client with Exclusive Voice
=================================
A PyQt5 GUI client for the exclusive-voice chatroom. Supports text chat,
push-to-talk voice (only one speaker at a time), and a live online-users list.

Run:
    python client.py
"""

import os
import sys
import socket
import threading
import base64

import pyaudio
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QPushButton, QListWidget,
    QLabel, QMessageBox, QSizePolicy, QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal, QObject
from PyQt5.QtGui import QFont

# --------------------------------------------------------------------------- #
# Configuration (override via environment variables if needed)
# --------------------------------------------------------------------------- #
SERVER_HOST = os.environ.get("CHAT_SERVER_HOST", "localhost")
SERVER_PORT = int(os.environ.get("CHAT_SERVER_PORT", 5050))

# Voice configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100

# --------------------------------------------------------------------------- #
# Visual theme
# --------------------------------------------------------------------------- #
STYLESHEET = """
QWidget {
    background-color: #1e2129;
    color: #e6e6e6;
    font-family: "Segoe UI", "Vazirmatn", sans-serif;
    font-size: 14px;
}
QLineEdit {
    background-color: #2a2e38;
    border: 1px solid #3a3f4b;
    border-radius: 6px;
    padding: 6px 10px;
    color: #f0f0f0;
}
QLineEdit:focus {
    border: 1px solid #5b8cff;
}
QTextEdit {
    background-color: #23262f;
    border: 1px solid #3a3f4b;
    border-radius: 8px;
    padding: 8px;
}
QListWidget {
    background-color: #23262f;
    border: 1px solid #3a3f4b;
    border-radius: 8px;
    padding: 4px;
}
QListWidget::item {
    padding: 6px 4px;
    border-radius: 4px;
}
QListWidget::item:selected {
    background-color: #33394a;
}
QPushButton {
    background-color: #3a5bd9;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #4a6bea;
}
QPushButton:disabled {
    background-color: #2f3340;
    color: #6b6f7a;
}
QPushButton#talkButton {
    background-color: #2e7d4f;
}
QPushButton#talkButton:hover {
    background-color: #35915b;
}
QPushButton#talkButton:checked {
    background-color: #c0392b;
}
QLabel {
    color: #b7bcc9;
    font-weight: 500;
}
QLabel#titleLabel {
    color: #ffffff;
    font-size: 16px;
    font-weight: 700;
}
"""


class Communicate(QObject):
    """Bridges the network thread's events into Qt's signal/slot system."""
    message_received = pyqtSignal(str)
    userlist_updated = pyqtSignal(list)
    status_updated = pyqtSignal(str, str)
    error_occurred = pyqtSignal(str)


class ChatClient(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chatroom — Exclusive Voice")
        self.setGeometry(100, 100, 860, 600)
        self.setStyleSheet(STYLESHEET)

        self.nickname = ""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        self.comm = Communicate()
        self.comm.message_received.connect(self.display_message)
        self.comm.userlist_updated.connect(self.update_users_list_display)
        self.comm.status_updated.connect(self.handle_status_update)
        self.comm.error_occurred.connect(self.handle_error)

        self.connected = False
        self.listening = False
        self.pyaudio_instance = pyaudio.PyAudio()
        self.stream = None
        self.voice_thread = None
        self.play_stream = None
        self.play_lock = threading.Lock()
        self.currently_talking_user = None

        self.init_ui()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def init_ui(self):
        main_layout = QHBoxLayout()
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # --- Left column: chat -------------------------------------- #
        chat_layout = QVBoxLayout()
        chat_layout.setSpacing(10)

        title = QLabel("💬 Exclusive Voice Chatroom")
        title.setObjectName("titleLabel")
        chat_layout.addWidget(title)

        nickname_layout = QHBoxLayout()
        self.nickname_input = QLineEdit()
        self.nickname_input.setPlaceholderText("Choose a nickname…")
        nickname_layout.addWidget(QLabel("Nickname:"))
        nickname_layout.addWidget(self.nickname_input)

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.connect_to_server)
        nickname_layout.addWidget(self.connect_button)
        chat_layout.addLayout(nickname_layout)

        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        chat_layout.addWidget(self.chat_display)

        message_layout = QHBoxLayout()
        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("Type your message…")
        self.message_input.returnPressed.connect(self.send_message)
        self.message_input.setDisabled(True)
        message_layout.addWidget(self.message_input)

        self.send_button = QPushButton("Send")
        self.send_button.setDisabled(True)
        self.send_button.clicked.connect(self.send_message)
        message_layout.addWidget(self.send_button)

        self.talk_button = QPushButton("🎤 Talk")
        self.talk_button.setObjectName("talkButton")
        self.talk_button.setDisabled(True)
        self.talk_button.setCheckable(True)
        self.talk_button.clicked.connect(self.toggle_talking)
        message_layout.addWidget(self.talk_button)
        chat_layout.addLayout(message_layout)

        main_layout.addLayout(chat_layout, 3)

        # --- Right column: online users ------------------------------ #
        users_layout = QVBoxLayout()
        users_layout.setSpacing(10)

        users_label = QLabel("Online Users")
        users_label.setObjectName("titleLabel")
        users_layout.addWidget(users_label)

        self.users_list = QListWidget()
        self.users_list.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        users_layout.addWidget(self.users_list)

        main_layout.addLayout(users_layout, 1)

        self.setLayout(main_layout)

    # ------------------------------------------------------------------ #
    # Connection handling
    # ------------------------------------------------------------------ #
    def connect_to_server(self):
        if self.connected:
            QMessageBox.warning(self, "Already Connected", "You are already connected to the server.")
            return

        self.nickname = self.nickname_input.text().strip()
        if not self.nickname:
            QMessageBox.warning(self, "Input Error", "Please enter a nickname.")
            return

        try:
            self.socket.connect((SERVER_HOST, SERVER_PORT))
            self.socket.sendall((self.nickname + "\n").encode("utf-8"))
        except OSError as exc:
            QMessageBox.critical(self, "Connection Failed", f"Could not connect to server: {exc}")
            return

        self.connected = True
        self.connect_button.setDisabled(True)
        self.nickname_input.setDisabled(True)
        self.message_input.setDisabled(False)
        self.send_button.setDisabled(False)
        self.talk_button.setDisabled(False)
        self.chat_display.append("✅ Connected to the server.")

        threading.Thread(target=self.listen_for_messages, daemon=True).start()

    def listen_for_messages(self):
        buffer = ""
        try:
            while True:
                data = self.socket.recv(4096)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="ignore")

                while "\n" in buffer:
                    message, buffer = buffer.split("\n", 1)
                    message = message.strip()
                    if not message:
                        continue

                    if message.startswith("USERLIST:"):
                        users = [u for u in message[len("USERLIST:"):].split(",") if u]
                        self.comm.userlist_updated.emit(users)
                    elif message.startswith("STATUS:"):
                        parts = message.split(":", 2)
                        if len(parts) == 3:
                            _, action, user = parts
                            self.comm.status_updated.emit(action, user)
                    elif message.startswith(("VOICE:", "MSG:", "SERVER:")):
                        self.comm.message_received.emit(message)
        except OSError as exc:
            self.comm.error_occurred.emit(f"Error receiving messages: {exc}")
        finally:
            self.socket.close()
            self.connected = False
            self.comm.error_occurred.emit("Disconnected from the server.")

    # ------------------------------------------------------------------ #
    # Messaging
    # ------------------------------------------------------------------ #
    def display_message(self, message: str):
        if message.startswith("VOICE:"):
            encoded_data = message[len("VOICE:"):].strip()
            try:
                self.play_audio(base64.b64decode(encoded_data))
            except Exception as exc:
                print(f"Error decoding audio data: {exc}")
        elif message.startswith("MSG:"):
            self.chat_display.append(message[len("MSG:"):].strip())
        elif message.startswith("SERVER:"):
            self.chat_display.append(f"<i>{message[len('SERVER:'):].strip()}</i>")

    def send_message(self):
        if not self.connected:
            QMessageBox.warning(self, "Not Connected", "You are not connected to any server.")
            return

        message = self.message_input.text().strip()
        if not message:
            return

        try:
            self.socket.sendall(f"MSG:{message}\n".encode("utf-8"))
            self.message_input.clear()
        except OSError as exc:
            QMessageBox.critical(self, "Send Failed", f"Could not send message: {exc}")

    # ------------------------------------------------------------------ #
    # Voice / exclusive talking
    # ------------------------------------------------------------------ #
    def toggle_talking(self):
        if self.talk_button.isChecked():
            self.socket.sendall(f"STATUS:START:{self.nickname}\n".encode("utf-8"))
        else:
            self.socket.sendall(f"STATUS:STOP:{self.nickname}\n".encode("utf-8"))
            self.stop_sending_voice()

    def handle_status_update(self, action: str, user: str):
        if action == "START":
            if self.currently_talking_user is None:
                self.currently_talking_user = user
                if user == self.nickname:
                    self.talk_button.setText("🎤 Stop Talking")
                    self.start_sending_voice()
                else:
                    self.talk_button.setDisabled(True)
                self.update_users_list_display(self.current_users())
            elif user != self.nickname:
                self.talk_button.setDisabled(True)

        elif action == "STOP":
            if self.currently_talking_user == user:
                self.currently_talking_user = None
                if user == self.nickname:
                    self.talk_button.setText("🎤 Talk")
                    self.stop_sending_voice()
                self.talk_button.setDisabled(False)
                self.update_users_list_display(self.current_users())

        elif action == "BUSY":
            self.talk_button.setChecked(False)
            self.talk_button.setText("🎤 Talk")
            QMessageBox.information(self, "Mic Busy", "Someone else is currently talking. Please wait until they finish.")

    def start_sending_voice(self):
        self.stream = self.pyaudio_instance.open(
            format=FORMAT, channels=CHANNELS, rate=RATE,
            input=True, frames_per_buffer=CHUNK,
        )
        self.listening = True
        self.voice_thread = threading.Thread(target=self.capture_and_send_voice, daemon=True)
        self.voice_thread.start()

    def capture_and_send_voice(self):
        try:
            while self.listening:
                data = self.stream.read(CHUNK, exception_on_overflow=False)
                encoded_data = base64.b64encode(data).decode("utf-8")
                self.socket.sendall(f"VOICE:{encoded_data}\n".encode("utf-8"))
        except Exception as exc:
            self.comm.error_occurred.emit(f"Error capturing/sending voice: {exc}")

    def stop_sending_voice(self):
        self.listening = False
        if self.voice_thread is not None:
            self.voice_thread.join()
            self.voice_thread = None
        if self.stream is not None:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None

    def play_audio(self, audio_data: bytes):
        try:
            with self.play_lock:
                if self.play_stream is None:
                    self.play_stream = self.pyaudio_instance.open(
                        format=FORMAT, channels=CHANNELS, rate=RATE,
                        output=True, frames_per_buffer=CHUNK,
                    )
                self.play_stream.write(audio_data)
        except Exception as exc:
            print(f"Error playing audio data: {exc}")

    # ------------------------------------------------------------------ #
    # Users list
    # ------------------------------------------------------------------ #
    def update_users_list_display(self, users):
        self.users_list.clear()
        for user in users:
            label = f"🎤 {user}" if user == self.currently_talking_user else f"👤 {user}"
            self.users_list.addItem(label)

    def current_users(self):
        items = [self.users_list.item(i).text() for i in range(self.users_list.count())]
        return [item.replace("🎤 ", "").replace("👤 ", "") for item in items]

    # ------------------------------------------------------------------ #
    # Errors & shutdown
    # ------------------------------------------------------------------ #
    def handle_error(self, error_message: str):
        print(error_message)
        if "Disconnected" in error_message:
            self.chat_display.append(f"⚠️ {error_message}")
            QMessageBox.information(self, "Disconnected", error_message)
        else:
            self.chat_display.append(f"⚠️ Error: {error_message}")

    def closeEvent(self, event):
        if self.connected:
            if self.talk_button.isChecked():
                self.talk_button.setChecked(False)
                self.toggle_talking()
            try:
                self.socket.close()
            except OSError:
                pass

        if self.play_stream is not None:
            self.play_stream.stop_stream()
            self.play_stream.close()
        self.pyaudio_instance.terminate()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    client = ChatClient()
    client.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
