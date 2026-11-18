import json
import os
import sys
from pathlib import Path

from PyQt5.QtCore import QFile, QIODevice, QSettings, QUrl, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QIcon
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import QWebEnginePage, QWebEngineScript, QWebEngineSettings, QWebEngineView
from PyQt5.QtWidgets import QApplication, QMainWindow, QMessageBox

from app import MainWindow
from webui.bridge import WebBridge


WEB_INDEX = Path(__file__).resolve().parent / 'web' / 'index.html'


class ShellPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if url.scheme() in ('http', 'https'):
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def createWindow(self, window_type):
        return None

    def javaScriptConsoleMessage(self, level, message, line, source):
        if os.environ.get('VOICETRANSL_WEB_DEBUG'):
            text = f'[web] {source}:{line} {message}'
            print(text, file=sys.stderr)
            try:
                with open('_web_console.log', 'a', encoding='utf-8') as handle:
                    handle.write(text + '\n')
            except OSError:
                pass


class DropView(QWebEngineView):
    filesDropped = pyqtSignal(list)

    @staticmethod
    def _local_paths(event):
        paths = []
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if path:
                        paths.append(path)
        return paths

    def dragEnterEvent(self, event):
        if self._local_paths(event):
            event.acceptProposedAction()
            self.page.runJavaScript('window.vtDragState && window.vtDragState(true)')
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._local_paths(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self.page.runJavaScript('window.vtDragState && window.vtDragState(false)')
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        paths = self._local_paths(event)
        if not paths:
            super().dropEvent(event)
            return
        event.acceptProposedAction()
        self.page.runJavaScript('window.vtDragState && window.vtDragState(false)')
        self.filesDropped.emit(paths)


class WebShell(QMainWindow):
    def __init__(self):
        super().__init__()
        self._settings = QSettings('VoiceTransl', 'VoiceTransl')
        self.setWindowTitle('聆译 VoiceTransl')
        self.setWindowIcon(QIcon('icon.png'))
        self.resize(1280, 840)

        self.backend = MainWindow(web_mode=True)
        self.backend._web_dialog_parent = self

        self.view = DropView(self)
        self.page = ShellPage(self.view)
        self.view.setPage(self.page)
        self.view.filesDropped.connect(self._on_files_dropped)
        self.setCentralWidget(self.view)
        self.page.setBackgroundColor(QColor('#f5f5f7'))
        self.page.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        self.page.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, False)
        self.page.settings().setAttribute(QWebEngineSettings.JavascriptCanOpenWindows, False)

        self.channel = QWebChannel(self.page)
        self.bridge = WebBridge(self.backend, self)
        self.channel.registerObject('bridge', self.bridge)
        self.page.setWebChannel(self.channel)
        self._install_scripts()

        self.view.loadFinished.connect(self._on_load_finished)
        self.view.load(QUrl.fromLocalFile(str(WEB_INDEX)))

        geometry = self._settings.value('webwindow/geometry')
        if geometry:
            self.restoreGeometry(geometry)

    def _on_files_dropped(self, paths):
        payload = json.dumps(paths, ensure_ascii=False)
        self.page.runJavaScript(f'window.vtAddPaths && window.vtAddPaths({payload})')

    def _install_scripts(self):
        channel_source_file = QFile(':/qtwebchannel/qwebchannel.js')
        channel_source = ''
        if channel_source_file.open(QIODevice.ReadOnly):
            channel_source = bytes(channel_source_file.readAll()).decode('utf-8')
            channel_source_file.close()
        if not channel_source:
            channel_source = (
                'window.QWebChannel=window.QWebChannel||function(t,c){'
                'var e=new XMLHttpRequest();e.open("GET","qrc:///qtwebchannel/qwebchannel.js",false);'
                'e.send(null);(0,eval)(e.responseText);new QWebChannel(t,c)};'
            )

        channel_script = QWebEngineScript()
        channel_script.setName('qwebchannel')
        channel_script.setInjectionPoint(QWebEngineScript.DocumentCreation)
        channel_script.setWorldId(QWebEngineScript.MainWorld)
        channel_script.setRunsOnSubFrames(False)
        channel_script.setSourceCode(channel_source)
        self.page.scripts().insert(channel_script)

        boot_script = QWebEngineScript()
        boot_script.setName('vt-bridge-boot')
        boot_script.setInjectionPoint(QWebEngineScript.DocumentReady)
        boot_script.setWorldId(QWebEngineScript.MainWorld)
        boot_script.setRunsOnSubFrames(False)
        boot_script.setSourceCode(
            '(function(){'
            'function boot(){'
            'if(typeof QWebChannel==="undefined"||!window.qt||!qt.webChannelTransport){setTimeout(boot,40);return;}'
            'new QWebChannel(qt.webChannelTransport,function(channel){'
            'window.vtBridge=channel.objects.bridge;'
            'window.dispatchEvent(new Event("vtbridge-ready"));'
            '});'
            '}boot();'
            '})();'
        )
        self.page.scripts().insert(boot_script)

    def _on_load_finished(self, ok):
        if ok:
            self.show()
        else:
            QMessageBox.critical(self, '界面加载失败', f'无法加载界面文件：\n{WEB_INDEX}')

    def closeEvent(self, event):
        if self.backend._task_is_running():
            reply = QMessageBox.question(
                self, '任务仍在运行',
                '现在退出会停止当前任务，已完成的输出不会删除。确定退出吗？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        self._settings.setValue('webwindow/geometry', self.saveGeometry())
        try:
            self.backend.timer.stop()
        except Exception:
            pass
        try:
            self.backend.save_config(silent=True)
        except Exception:
            pass
        self.backend.shutdown_children()
        event.accept()


def main():
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-features=Translate')
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName('VoiceTransl')
    app.setOrganizationName('VoiceTransl')
    shell = WebShell()
    shell.show()
    sys.exit(app.exec_())
