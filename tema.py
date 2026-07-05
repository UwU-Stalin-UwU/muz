import sys
import hashlib
import sqlite3
import io
from pathlib import Path
from PIL import Image
import weakref
import traceback
import logging
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem,
    QTabWidget, QScrollArea, QFrame, QMessageBox, QFileDialog,
    QTextEdit, QSplitter, QDialog, QDialogButtonBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QEvent
from PyQt6.QtGui import QPixmap, QImage, QFont, QKeySequence, QAction


# ==================== DATABASE CLASS ====================
class ImageDatabase:
    def __init__(self, db_path="gallery.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        try:
            with self._get_connection() as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL,
                        role TEXT NOT NULL
                    )
                ''')
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS albums (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL UNIQUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS images (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        album_id INTEGER NOT NULL,
                        filename TEXT NOT NULL,
                        image_data BLOB NOT NULL,
                        thumbnail BLOB,
                        description TEXT,
                        format TEXT,
                        width INTEGER,
                        height INTEGER,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (album_id) REFERENCES albums(id) ON DELETE CASCADE
                    )
                ''')
                conn.execute('CREATE INDEX IF NOT EXISTS idx_images_album ON images(album_id)')
                conn.commit()

                cursor = conn.execute("SELECT COUNT(*) FROM users")
                if cursor.fetchone()[0] == 0:
                    admin_pass = hashlib.sha256("admin123".encode()).hexdigest()
                    conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                                ("admin", admin_pass, "admin"))
                    user_pass = hashlib.sha256("user123".encode()).hexdigest()
                    conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                                ("user", user_pass, "user"))
                    conn.commit()
        except Exception as e:
            logger.error(f"Database initialization error: {e}")

    def authenticate(self, username, password):
        try:
            hashed = hashlib.sha256(password.encode()).hexdigest()
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id, username, role FROM users WHERE username = ? AND password = ?", 
                    (username, hashed)
                )
                return cursor.fetchone()
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return None

    def add_album(self, name):
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("INSERT INTO albums (name) VALUES (?)", (name,))
                conn.commit()
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None
        except Exception as e:
            logger.error(f"Add album error: {e}")
            return None

    def delete_album(self, album_id):
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Delete album error: {e}")
            return False

    def get_all_albums(self):
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT id, name FROM albums ORDER BY name")
                return cursor.fetchall()
        except Exception as e:
            logger.error(f"Get albums error: {e}")
            return []

    def add_image(self, album_id, image_path, description=""):
        try:
            with Image.open(image_path) as img:
                width, height = img.size
                
                with open(image_path, 'rb') as f:
                    image_data = f.read()
                
                thumb = img.copy()
                thumb.thumbnail((100, 100), Image.Resampling.LANCZOS)
                
                if thumb.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', thumb.size, (255, 255, 255))
                    if thumb.mode == 'P':
                        thumb = thumb.convert('RGBA')
                    if thumb.mode == 'RGBA':
                        if len(thumb.split()) > 3 and thumb.split()[-1]:
                            background.paste(thumb, mask=thumb.split()[-1])
                        else:
                            background.paste(thumb)
                    else:
                        background.paste(thumb)
                    thumb = background
                elif thumb.mode != 'RGB':
                    thumb = thumb.convert('RGB')
                
                thumb_buffer = io.BytesIO()
                thumb.save(thumb_buffer, format='JPEG', quality=85)
                thumbnail_data = thumb_buffer.getvalue()

                with self._get_connection() as conn:
                    conn.execute('''
                        INSERT INTO images (album_id, filename, image_data, thumbnail, description, format, width, height)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (album_id, Path(image_path).name, image_data, 
                          thumbnail_data, description, 'JPEG', width, height))
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Add image error: {e}")
            return False

    def delete_image(self, image_id):
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Delete image error: {e}")
            return False

    def update_image_description(self, image_id, description):
        try:
            with self._get_connection() as conn:
                conn.execute("UPDATE images SET description = ? WHERE id = ?", (description, image_id))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Update description error: {e}")
            return False

    def get_album_images(self, album_id):
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT id, filename, description, thumbnail FROM images WHERE album_id = ? ORDER BY filename", 
                    (album_id,)
                )
                rows = cursor.fetchall()
                return [(row[0], row[1], row[2], row[3]) for row in rows]
        except Exception as e:
            logger.error(f"Get album images error: {e}")
            return []

    def get_full_image(self, image_id):
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT image_data, description, format, width, height FROM images WHERE id = ?", 
                    (image_id,)
                )
                row = cursor.fetchone()
                if row and row[0]:
                    return (row[0], row[1], row[2], row[3], row[4])
                return None
        except Exception as e:
            logger.error(f"Get full image error: {e}")
            return None


# ==================== IMAGE LOADER THREAD ====================
class ImageLoaderThread(QThread):
    image_loaded = pyqtSignal(int, object, int, int, bool, str)
    
    def __init__(self, db, image_id, target_size=None):
        super().__init__()
        self.db = db
        self.image_id = image_id
        self.target_size = target_size
        
    def run(self):
        try:
            result = self.db.get_full_image(self.image_id)
            if result and result[0]:
                image_data, desc, fmt, width, height = result
                pil_img = Image.open(io.BytesIO(image_data))
                
                if self.target_size and self.target_size[0] > 0 and self.target_size[1] > 0:
                    img_w, img_h = pil_img.size
                    ratio = min(self.target_size[0] / img_w, self.target_size[1] / img_h)
                    if ratio < 1:
                        new_w = max(100, int(img_w * ratio))
                        new_h = max(100, int(img_h * ratio))
                        pil_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                
                self.image_loaded.emit(self.image_id, pil_img, width, height, True, "")
            else:
                self.image_loaded.emit(self.image_id, None, 0, 0, False, "Изображение не найдено")
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error loading image {self.image_id}: {error_msg}")
            self.image_loaded.emit(self.image_id, None, 0, 0, False, error_msg)


# ==================== THUMBNAIL LOADER THREAD ====================
class ThumbnailLoaderThread(QThread):
    thumbnail_loaded = pyqtSignal(int, object, bool, str)
    
    def __init__(self, db, image_id, thumb_data):
        super().__init__()
        self.db = db
        self.image_id = image_id
        self.thumb_data = thumb_data
        
    def run(self):
        try:
            pil_img = Image.open(io.BytesIO(self.thumb_data))
            pil_img = pil_img.convert('RGB')
            
            data = pil_img.tobytes('raw', 'RGB')
            qimage = QImage(data, pil_img.size[0], pil_img.size[1], QImage.Format.Format_RGB888)
            qpixmap = QPixmap.fromImage(qimage)
            
            self.thumbnail_loaded.emit(self.image_id, qpixmap, True, "")
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Thumbnail error for {self.image_id}: {error_msg}")
            self.thumbnail_loaded.emit(self.image_id, None, False, error_msg)


# ==================== LOGIN DIALOG ====================
class LoginDialog(QDialog):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.user_id = None
        self.user_role = None
        self.setWindowTitle("📷 Вход в фотогалерею")
        self.setFixedSize(400, 450)
        self.setModal(True)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(30, 40, 30, 40)
        
        title = QLabel("📷 Фотогалерея")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont()
        title_font.setPointSize(28)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        
        layout.addSpacing(20)
        
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("Логин")
        self.username_input.setMinimumHeight(40)
        layout.addWidget(self.username_input)
        
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Пароль")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setMinimumHeight(40)
        layout.addWidget(self.password_input)
        
        layout.addSpacing(10)
        
        self.login_btn = QPushButton("🔐 Войти")
        self.login_btn.setMinimumHeight(45)
        self.login_btn.clicked.connect(self.login)
        layout.addWidget(self.login_btn)
        
        info_label = QLabel("\n🧪 Тестовые учётные записи:\n👑 admin / admin123\n👤 user / user123")
        info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info_label)
        
        self.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 1px solid #555;
                border-radius: 5px;
                font-size: 14px;
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QLabel {
                color: #ffffff;
            }
        """)

    def login(self):
        username = self.username_input.text().strip()
        password = self.password_input.text()
        
        if not username or not password:
            QMessageBox.warning(self, "Ошибка", "Введите логин и пароль")
            return
            
        user = self.db.authenticate(username, password)
        if user:
            self.user_id = user[0]
            self.user_role = user[2]
            self.accept()
        else:
            QMessageBox.warning(self, "Ошибка", "Неверный логин или пароль")
            self.password_input.clear()
            self.password_input.setFocus()


# ==================== PASSWORD DIALOG ====================
class PasswordDialog(QDialog):
    def __init__(self, db):
        super().__init__()
        self.db = db
        self.setWindowTitle("🔐 Подтверждение пароля")
        self.setFixedSize(400, 250)
        self.setModal(True)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(30, 30, 30, 30)
        
        title = QLabel("🔐 Требуется авторизация")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        layout.addSpacing(10)
        
        label = QLabel("Введите пароль администратора:")
        layout.addWidget(label)
        
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setMinimumHeight(40)
        layout.addWidget(self.password_input)
        
        layout.addSpacing(10)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.check_password)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 1px solid #555;
                border-radius: 5px;
                font-size: 14px;
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QLabel {
                color: #ffffff;
            }
        """)
        
    def check_password(self):
        password = self.password_input.text()
        if not password:
            QMessageBox.warning(self, "Ошибка", "Введите пароль")
            return
            
        user = self.db.authenticate("admin", password)
        if user:
            self.accept()
        else:
            QMessageBox.warning(self, "Ошибка", "Неверный пароль")
            self.password_input.clear()
            self.password_input.setFocus()


# ==================== ADD ALBUM DIALOG ====================
class AddAlbumDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.album_name = ""
        self.setWindowTitle("➕ Новая вкладка")
        self.setFixedSize(400, 200)
        self.setModal(True)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(30, 30, 30, 30)
        
        title = QLabel("Создание новой вкладки")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        layout.addSpacing(10)
        
        label = QLabel("Название вкладки:")
        layout.addWidget(label)
        
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Введите название")
        self.name_input.setMinimumHeight(40)
        layout.addWidget(self.name_input)
        
        layout.addSpacing(10)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
        self.setStyleSheet("""
            QLineEdit {
                padding: 8px;
                border: 1px solid #555;
                border-radius: 5px;
                font-size: 14px;
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QLabel {
                color: #ffffff;
            }
        """)
        
    def save(self):
        name = self.name_input.text().strip()
        if name:
            self.album_name = name
            self.accept()
        else:
            QMessageBox.warning(self, "Ошибка", "Введите название вкладки")


# ==================== IMAGE ITEM WIDGET ====================
class ImageItemWidget(QFrame):
    def __init__(self, image_id, filename, thumbnail=None):
        super().__init__()
        self.image_id = image_id
        self.filename = filename
        self.is_alive = True
        self.setup_ui(thumbnail)
        
    def setup_ui(self, thumbnail):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)
        
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(50, 50)
        self.thumb_label.setScaledContents(True)
        self.thumb_label.setStyleSheet("border: 1px solid #555; border-radius: 5px; background-color: #2b2b2b;")
        if thumbnail:
            self.thumb_label.setPixmap(thumbnail.scaled(50, 50, Qt.AspectRatioMode.KeepAspectRatio))
        else:
            self.thumb_label.setText("🖼️")
            self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.thumb_label)
        
        self.name_label = QLabel(self.filename[:30] + "..." if len(self.filename) > 30 else self.filename)
        self.name_label.setStyleSheet("font-size: 12px; color: #ffffff;")
        self.name_label.setWordWrap(True)
        layout.addWidget(self.name_label, 1)
        
        self.delete_button = None
        
        self.setStyleSheet("""
            ImageItemWidget {
                background-color: #34495e;
                border-radius: 5px;
                margin: 2px;
            }
            ImageItemWidget:hover {
                background-color: #3d566e;
            }
        """)
    
    def update_thumbnail(self, pixmap):
        try:
            if self.is_alive and pixmap and self.thumb_label:
                self.thumb_label.setPixmap(pixmap.scaled(50, 50, Qt.AspectRatioMode.KeepAspectRatio))
        except RuntimeError:
            pass
    
    def add_delete_button(self, callback):
        if self.delete_button is None:
            self.delete_button = QPushButton("🗑")
            self.delete_button.setFixedSize(30, 30)
            self.delete_button.setStyleSheet("""
                QPushButton {
                    background-color: #e74c3c;
                    border: none;
                    border-radius: 5px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #c0392b;
                }
            """)
            self.delete_button.clicked.connect(lambda: callback(self.image_id))
            self.layout().addWidget(self.delete_button)


# ==================== MAIN WINDOW ====================
class MainWindow(QMainWindow):
    def __init__(self, db, user_id, user_role):
        super().__init__()
        self.db = db
        self.user_id = user_id
        self.user_role = user_role
        self._is_closing = False
        self.is_fullscreen = False
        
        self.albums = {}
        self.current_album_id = None
        self.current_images = []
        self.current_image_id = None
        self.current_image_pixmap = None
        self.current_display_pixmap = None
        
        self.image_threads = []
        self.thumbnail_threads = []
        
        self.resize_timer = None
        self.status_timer = None
        
        self.init_ui()
        self.load_albums()
        self.setDarkTheme()
        
        # Настройка горячих клавиш
        self.setup_shortcuts()
    
    def setup_shortcuts(self):
        """Настройка горячих клавиш"""
        fullscreen_action = QAction("Fullscreen", self)
        fullscreen_action.setShortcut(QKeySequence(Qt.Key.Key_F11))
        fullscreen_action.triggered.connect(self.toggle_fullscreen)
        self.addAction(fullscreen_action)
        
        escape_action = QAction("Exit Fullscreen", self)
        escape_action.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        escape_action.triggered.connect(self.exit_fullscreen)
        self.addAction(escape_action)
    
    def toggle_fullscreen(self):
        """Переключение полноэкранного режима"""
        # Для пользователя требуем пароль админа перед переключением
        if self.user_role == "user":
            dialog = PasswordDialog(self.db)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
        
        if self.is_fullscreen:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()
    
    def enter_fullscreen(self):
        """Вход в полноэкранный режим"""
        if not self.is_fullscreen:
            self.is_fullscreen = True
            self.showFullScreen()
            QTimer.singleShot(100, self.update_image_display)
    
    def exit_fullscreen(self):
        """Выход из полноэкранного режима"""
        if self.is_fullscreen:
            self.is_fullscreen = False
            self.showNormal()
            QTimer.singleShot(100, self.update_image_display)
    
    def update_image_display(self):
        """Обновление отображения изображения"""
        if self.resize_timer:
            self.resize_timer.stop()
        
        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self._do_update_display)
        self.resize_timer.start(50)
    
    def _do_update_display(self):
        """Фактическое обновление отображения"""
        if self._is_closing:
            return
        if self.current_image_id and self.current_image_pixmap:
            view_size = self.image_scroll.viewport().size()
            if view_size.width() > 10 and view_size.height() > 10:
                scaled_pixmap = self.current_image_pixmap.scaled(
                    view_size.width() - 40, view_size.height() - 40,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self.current_display_pixmap = scaled_pixmap
                self.image_label.setPixmap(scaled_pixmap)
                self.image_label.setText("")
    
    def setDarkTheme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1a1a1a;
                color: #ffffff;
            }
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #1f618d;
            }
            QListWidget {
                background-color: #2b2b2b;
                border: 1px solid #555;
                border-radius: 5px;
                outline: none;
            }
            QListWidget::item {
                padding: 5px;
            }
            QListWidget::item:selected {
                background-color: #3498db;
            }
            QTabWidget::pane {
                border: 1px solid #555;
                border-radius: 5px;
                background-color: #1a1a1a;
            }
            QTabBar::tab {
                background-color: #34495e;
                color: #ffffff;
                padding: 8px 20px;
                margin: 2px;
                border-radius: 5px;
            }
            QTabBar::tab:selected {
                background-color: #3498db;
            }
            QTabBar::tab:hover {
                background-color: #2980b9;
            }
            QScrollArea {
                border: 1px solid #555;
                border-radius: 5px;
                background-color: #2b2b2b;
            }
            QTextEdit {
                background-color: #2b2b2b;
                border: 1px solid #555;
                border-radius: 5px;
                color: #ffffff;
                padding: 5px;
            }
            QLineEdit {
                background-color: #2b2b2b;
                border: 1px solid #555;
                border-radius: 5px;
                color: #ffffff;
                padding: 5px;
            }
            QLabel {
                color: #ffffff;
            }
            QSplitter::handle {
                background-color: #555;
            }
        """)
    
    def update_status(self, message):
        if hasattr(self, 'status_label') and not self._is_closing:
            self.status_label.setText(message)
            if self.status_timer:
                self.status_timer.stop()
            self.status_timer = QTimer()
            self.status_timer.setSingleShot(True)
            self.status_timer.timeout.connect(lambda: self.status_label.setText("Готов к работе"))
            self.status_timer.start(3000)
    
    def load_albums(self):
        """Загрузка альбомов"""
        try:
            albums = self.db.get_all_albums()
            self.albums.clear()
            self.tab_widget.clear()
            
            for album_id, name in albums:
                self.albums[album_id] = name
                tab = QWidget()
                tab_layout = QVBoxLayout(tab)
                tab_layout.setContentsMargins(0, 0, 0, 0)
                
                if self.user_role == "admin":
                    btn_frame = QWidget()
                    btn_layout = QHBoxLayout(btn_frame)
                    btn_layout.setContentsMargins(10, 10, 10, 5)
                    
                    delete_btn = QPushButton("🗑 Удалить вкладку")
                    delete_btn.clicked.connect(lambda checked, aid=album_id: self.delete_album(aid))
                    delete_btn.setStyleSheet("background-color: #e74c3c;")
                    btn_layout.addWidget(delete_btn, 0, Qt.AlignmentFlag.AlignRight)
                    tab_layout.addWidget(btn_frame)
                
                tab_layout.addStretch()
                self.tab_widget.addTab(tab, name)
                
            if albums:
                first_id, first_name = albums[0]
                self.current_album_id = first_id
                self.tab_widget.setCurrentIndex(0)
                self.load_images(first_id)
        except Exception as e:
            logger.error(f"Load albums error: {e}")
    
    def on_tab_changed(self, index):
        """Обработка смены вкладки"""
        if self._is_closing:
            return
        if index >= 0 and index < self.tab_widget.count():
            tab_text = self.tab_widget.tabText(index)
            for album_id, name in self.albums.items():
                if name == tab_text:
                    self.current_album_id = album_id
                    self.load_images(album_id)
                    break
    
    def load_images(self, album_id):
        """Загрузка изображений альбома"""
        if self._is_closing:
            return
            
        # Останавливаем старые потоки
        for thread in self.thumbnail_threads:
            if thread.isRunning():
                thread.quit()
                thread.wait(500)
        self.thumbnail_threads.clear()
        
        self.image_list.clear()
        self.current_images = []
        self.current_image_id = None
        self.current_image_pixmap = None
        self.current_display_pixmap = None
        
        images = self.db.get_album_images(album_id)
        
        if not images:
            self.image_label.setText("📭 В этой вкладке нет изображений")
            self.image_label.setPixmap(QPixmap())
            return
            
        for idx, (img_id, filename, description, thumb_blob) in enumerate(images):
            self.current_images.append((img_id, filename, description))
            
            item_widget = ImageItemWidget(img_id, filename)
            item_widget.setProperty("index", idx)
            
            if self.user_role == "admin":
                item_widget.add_delete_button(self.delete_image)
            
            if thumb_blob:
                thread = ThumbnailLoaderThread(self.db, img_id, thumb_blob)
                widget_ref = weakref.ref(item_widget)
                thread.thumbnail_loaded.connect(
                    lambda iid, thumb, success, err, ref=widget_ref: self.on_thumbnail_loaded_safe(iid, thumb, success, err, ref)
                )
                thread.start()
                self.thumbnail_threads.append(thread)
            
            item = QListWidgetItem(self.image_list)
            item.setSizeHint(item_widget.sizeHint())
            self.image_list.setItemWidget(item, item_widget)
    
    def on_thumbnail_loaded_safe(self, image_id, thumbnail, success, error, widget_ref):
        """Безопасная обработка загрузки миниатюры"""
        if self._is_closing:
            return
        try:
            widget = widget_ref()
            if widget is not None and success and thumbnail is not None:
                QTimer.singleShot(0, lambda: widget.update_thumbnail(thumbnail))
        except (RuntimeError, AttributeError):
            pass
    
    def on_image_clicked(self, item):
        """Выбор изображения"""
        if self._is_closing:
            return
        widget = self.image_list.itemWidget(item)
        if widget:
            index = widget.property("index")
            if index is not None and 0 <= index < len(self.current_images):
                self.show_image(index)
    
    def show_image(self, index):
        """Показ изображения"""
        if self._is_closing or not self.current_images or index >= len(self.current_images):
            return
            
        img_id, filename, description = self.current_images[index]
        self.current_image_id = img_id
        
        if self.user_role == "admin" and hasattr(self, 'desc_text'):
            self.desc_text.setPlainText(description if description else "")
        
        self.update_status(f"⏳ Загрузка: {filename}")
        
        self.image_label.setText("⏳ Загрузка изображения...")
        self.image_label.setPixmap(QPixmap())
        
        view_size = self.image_scroll.viewport().size()
        target_size = (max(100, view_size.width() - 40), max(100, view_size.height() - 40))
        
        thread = ImageLoaderThread(self.db, img_id, target_size)
        thread.image_loaded.connect(self.on_full_image_loaded)
        thread.start()
        self.image_threads.append(thread)
    
    def on_full_image_loaded(self, image_id, pil_img, original_width, original_height, success, error_msg):
        """Обработка загрузки полного изображения"""
        if self._is_closing:
            return
            
        if image_id != self.current_image_id:
            return
            
        if not success or pil_img is None:
            self.image_label.setText(f"❌ Ошибка: {error_msg[:100]}")
            self.image_label.setPixmap(QPixmap())
            self.update_status(f"⚠️ Ошибка загрузки")
            return
            
        try:
            if pil_img.mode == 'RGB':
                data = pil_img.tobytes('raw', 'RGB')
                qimage = QImage(data, pil_img.size[0], pil_img.size[1], QImage.Format.Format_RGB888)
            elif pil_img.mode == 'RGBA':
                data = pil_img.tobytes('raw', 'RGBA')
                qimage = QImage(data, pil_img.size[0], pil_img.size[1], QImage.Format.Format_RGBA8888)
            else:
                pil_img = pil_img.convert('RGB')
                data = pil_img.tobytes('raw', 'RGB')
                qimage = QImage(data, pil_img.size[0], pil_img.size[1], QImage.Format.Format_RGB888)
                
            self.current_image_pixmap = QPixmap.fromImage(qimage)
            self.update_image_display()
            
            filename = next((fname for img_id, fname, _ in self.current_images if img_id == image_id), "")
            self.update_status(f"✨ {filename} | {original_width}×{original_height}")
            
        except Exception as e:
            logger.error(f"Display error: {e}")
            self.image_label.setText(f"❌ Ошибка: {str(e)[:100]}")
            self.image_label.setPixmap(QPixmap())
            self.update_status("⚠️ Ошибка отображения")
    
    def add_album(self):
        """Добавление альбома"""
        if self.user_role != "admin" or self._is_closing:
            return
            
        dialog = AddAlbumDialog()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.album_name
            album_id = self.db.add_album(name)
            if album_id:
                self.load_albums()
                self.update_status(f"✅ Вкладка '{name}' создана")
            else:
                QMessageBox.warning(self, "Ошибка", "Вкладка с таким названием уже существует")
    
    def delete_album(self, album_id):
        """Удаление альбома"""
        if self.user_role != "admin" or self._is_closing:
            return
            
        album_name = self.albums.get(album_id, "Неизвестный")
        reply = QMessageBox.question(
            self, "⚠️ Подтверждение",
            f"Удалить вкладку '{album_name}' и все изображения в ней?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if self.db.delete_album(album_id):
                self.load_albums()
                self.update_status(f"✅ Вкладка '{album_name}' удалена")
    
    def add_photos(self):
        """Добавление фотографий"""
        if self.user_role != "admin" or self._is_closing:
            return
            
        if not self.current_album_id:
            QMessageBox.warning(self, "Предупреждение", "Сначала выберите вкладку")
            return
            
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "📁 Выберите изображения",
            str(Path.home()),
            "Изображения (*.jpg *.jpeg *.png *.gif *.bmp *.tiff *.webp);;Все файлы (*.*)"
        )
        
        if not files:
            return
            
        added = 0
        failed = 0
        
        for file_path in files:
            if self.db.add_image(self.current_album_id, Path(file_path)):
                added += 1
            else:
                failed += 1
                
        if added > 0:
            self.load_images(self.current_album_id)
            msg = f"✅ Добавлено: {added}"
            if failed > 0:
                msg += f" | ❌ Ошибок: {failed}"
            self.update_status(msg)
            QMessageBox.information(self, "Результат", msg)
        elif failed > 0:
            QMessageBox.warning(self, "Ошибка", f"Не удалось добавить {failed} изображений")
    
    def delete_image(self, image_id):
        """Удаление изображения"""
        if self.user_role != "admin" or self._is_closing:
            return
            
        reply = QMessageBox.question(
            self, "⚠️ Подтверждение",
            "Удалить это изображение?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if self.db.delete_image(image_id):
                self.load_images(self.current_album_id)
                self.update_status("✅ Изображение удалено")
    
    def save_description(self):
        """Сохранение описания"""
        if self.user_role != "admin" or self._is_closing:
            return
            
        if not self.current_image_id:
            QMessageBox.warning(self, "Предупреждение", "Сначала выберите изображение")
            return
            
        description = self.desc_text.toPlainText().strip()
        if self.db.update_image_description(self.current_image_id, description):
            self.update_status("✅ Описание сохранено")
            QMessageBox.information(self, "Успех", "Описание сохранено")
    
    def resizeEvent(self, event):
        """Обработка изменения размера окна"""
        super().resizeEvent(event)
        if not self._is_closing and self.current_image_id and self.current_image_pixmap:
            self.update_image_display()

    def changeEvent(self, event):
        """Обработка изменения состояния окна (сворачивание и т.д.)"""
        if event.type() == QEvent.Type.WindowStateChange:
            old_state = event.oldState()
            new_state = self.windowState()
            
            # Проверяем, что окно было только что свёрнуто
            if not (old_state & Qt.WindowState.WindowMinimized) and \
               (new_state & Qt.WindowState.WindowMinimized):
                if self.user_role == "user" and not self._is_closing:
                    dialog = PasswordDialog(self.db)
                    if dialog.exec() != QDialog.DialogCode.Accepted:
                        # Отменяем сворачивание — восстанавливаем предыдущее состояние
                        if old_state & Qt.WindowState.WindowMaximized:
                            self.showMaximized()
                        else:
                            self.showNormal()
                        return
        super().changeEvent(event)
    
    def closeEvent(self, event):
        """Обработка закрытия окна"""
        # Для пользователя требуем пароль админа перед закрытием
        if self.user_role == "user" and not self._is_closing:
            dialog = PasswordDialog(self.db)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                event.ignore()
                return
        
        if not self._is_closing:
            self._is_closing = True
            
            if self.resize_timer:
                self.resize_timer.stop()
            if self.status_timer:
                self.status_timer.stop()
            
            for thread in self.image_threads:
                if thread.isRunning():
                    thread.quit()
                    thread.wait(500)
            for thread in self.thumbnail_threads:
                if thread.isRunning():
                    thread.quit()
                    thread.wait(500)
            
            self.image_threads.clear()
            self.thumbnail_threads.clear()
            
            event.accept()
    
    def init_ui(self):
        """Инициализация интерфейса"""
        self.setWindowTitle("📷 Фотогалерея — Администратор" if self.user_role == "admin" else "📷 Фотогалерея — Просмотр")
        self.setGeometry(100, 100, 1400, 800)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # Верхняя панель
        top_panel = QWidget()
        top_layout = QHBoxLayout(top_panel)
        top_layout.setContentsMargins(10, 5, 10, 5)
        
        if self.user_role == "admin":
            self.add_album_btn = QPushButton("➕ Новая вкладка")
            self.add_album_btn.clicked.connect(self.add_album)
            top_layout.addWidget(self.add_album_btn)
            
            self.add_photos_btn = QPushButton("🖼️ Добавить фото")
            self.add_photos_btn.clicked.connect(self.add_photos)
            top_layout.addWidget(self.add_photos_btn)
        
        self.status_label = QLabel("Готов к работе")
        top_layout.addWidget(self.status_label, 1)
        
        role_icon = "👑" if self.user_role == "admin" else "👤"
        role_text = "Администратор" if self.user_role == "admin" else "Пользователь"
        user_label = QLabel(f"{role_icon} {role_text}")
        user_label.setStyleSheet("font-weight: bold; color: #2ecc71;")
        top_layout.addWidget(user_label)
        
        fullscreen_hint = QLabel("F11 - полноэкранный режим")
        fullscreen_hint.setStyleSheet("font-size: 10px; color: #95a5a6;")
        top_layout.addWidget(fullscreen_hint)
        
        main_layout.addWidget(top_panel)
        
        self.tab_widget = QTabWidget()
        self.tab_widget.currentChanged.connect(self.on_tab_changed)
        main_layout.addWidget(self.tab_widget)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter, 1)
        
        # Левая панель
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        left_title = QLabel("📁 Изображения")
        left_title.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px;")
        left_layout.addWidget(left_title)
        
        self.image_list = QListWidget()
        self.image_list.itemClicked.connect(self.on_image_clicked)
        left_layout.addWidget(self.image_list)
        
        splitter.addWidget(left_widget)
        splitter.setSizes([300, 1100])
        
        # Правая панель
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        self.image_scroll = QScrollArea()
        self.image_scroll.setWidgetResizable(True)
        self.image_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setText("🖼️ Выберите вкладку и изображение для просмотра\n\nF11 - полноэкранный режим")
        self.image_label.setStyleSheet("font-size: 16px; color: #95a5a6; padding: 50px;")
        self.image_label.setWordWrap(True)
        self.image_scroll.setWidget(self.image_label)
        
        right_layout.addWidget(self.image_scroll, 1)
        
        if self.user_role == "admin":
            desc_frame = QFrame()
            desc_layout = QVBoxLayout(desc_frame)
            desc_layout.setContentsMargins(10, 10, 10, 10)
            
            desc_title = QLabel("📝 Описание изображения:")
            desc_title.setStyleSheet("font-weight: bold;")
            desc_layout.addWidget(desc_title)
            
            self.desc_text = QTextEdit()
            self.desc_text.setMaximumHeight(120)
            self.desc_text.setPlaceholderText("Введите описание изображения...")
            desc_layout.addWidget(self.desc_text)
            
            self.save_desc_btn = QPushButton("💾 Сохранить описание")
            self.save_desc_btn.clicked.connect(self.save_description)
            desc_layout.addWidget(self.save_desc_btn)
            
            right_layout.addWidget(desc_frame)
        
        splitter.addWidget(right_widget)


# ==================== ЗАПУСК ====================
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    
    db = ImageDatabase()
    
    login_dialog = LoginDialog(db)
    if login_dialog.exec() == QDialog.DialogCode.Accepted:
        window = MainWindow(db, login_dialog.user_id, login_dialog.user_role)
        window.show()
        sys.exit(app.exec())
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
