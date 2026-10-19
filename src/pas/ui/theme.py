STYLESHEET = """
* { font-family: "Segoe UI"; font-size: 10pt; }
QMainWindow, QWidget#page { background: #f6f7f9; }
QListWidget#nav {
    background: #1f2430; color: #d7dae0; border: none; padding: 8px 0; outline: 0;
}
QListWidget#nav::item { padding: 9px 16px; border-left: 3px solid transparent; }
QListWidget#nav::item:selected { background: #2b3242; color: #ffffff; border-left: 3px solid #4f8cff; }
QListWidget#nav::item:hover:!selected { background: #262c3a; }
QListWidget#nav::item:disabled { color: #6b7080; }
QLabel#pageTitle { font-size: 17pt; font-weight: 600; color: #1f2430; }
QLabel#pagePurpose { color: #5b6170; }
QLabel#hint { color: #5b6170; background: #eef2fb; border: 1px solid #d6e0f5; border-radius: 6px; padding: 8px; }
QLabel#errorBox { color: #8a1c1c; background: #fdeeee; border: 1px solid #f3c7c7; border-radius: 6px; padding: 8px; }
QLabel#okBox { color: #1d5e2e; background: #eaf7ee; border: 1px solid #c4e6cf; border-radius: 6px; padding: 8px; }
QGroupBox { background: #ffffff; border: 1px solid #e1e4ea; border-radius: 8px; margin-top: 14px; padding: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #3a4050; font-weight: 600; }
QPushButton { background: #ffffff; border: 1px solid #c9ced8; border-radius: 6px; padding: 6px 14px; }
QPushButton:hover { background: #f0f3f8; }
QPushButton:disabled { color: #a0a5b0; background: #f3f4f6; }
QPushButton#primary { background: #2f6fed; color: white; border: 1px solid #2f6fed; }
QPushButton#primary:hover { background: #255fd1; }
QPushButton#primary:disabled { background: #a9c1f3; border-color: #a9c1f3; }
QPushButton#danger { color: #b42318; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextBrowser {
    background: #ffffff; border: 1px solid #c9ced8; border-radius: 6px; padding: 4px 6px;
}
QTableWidget, QTreeWidget { background: #ffffff; border: 1px solid #e1e4ea; border-radius: 6px; gridline-color: #eef0f3; }
QHeaderView::section { background: #f0f2f5; border: none; border-bottom: 1px solid #e1e4ea; padding: 6px; font-weight: 600; }
QProgressBar { border: 1px solid #c9ced8; border-radius: 6px; background: #ffffff; text-align: center; height: 18px; }
QProgressBar::chunk { background: #4f8cff; border-radius: 5px; }
"""
