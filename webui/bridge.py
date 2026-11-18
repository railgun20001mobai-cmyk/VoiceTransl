import json
import os
import re
from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QFileDialog, QMessageBox

from app import LOG_PATH, UIMessageQueue, open_path


APP_VERSION = 'v1.32'


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _split_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or '').splitlines() if line.strip()]


def _clean_log_line(line: str) -> str:
    line = re.sub(r'\x1b\[[0-9;]*m', '', line)
    line = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', line)
    return line.rstrip()


def _combo_options(combo, value_role='data') -> list[dict]:
    options = []
    for index in range(combo.count()):
        value = combo.itemData(index) if value_role == 'data' else combo.itemText(index)
        if value is None:
            value = combo.itemText(index)
        options.append({'value': value, 'label': combo.itemText(index)})
    return options


def _select_combo_data(combo, value) -> bool:
    if value is None or value == '':
        return False
    index = combo.findData(value)
    if index < 0:
        index = combo.findText(str(value))
    if index >= 0:
        combo.setCurrentIndex(index)
        return True
    return False


def _select_combo_text(combo, value, add_missing=False) -> bool:
    if value is None or value == '':
        return False
    index = combo.findText(str(value))
    if index < 0 and add_missing:
        combo.addItem(str(value))
        index = combo.count() - 1
    if index >= 0:
        combo.setCurrentIndex(index)
        return True
    return False


def _basename(path: str) -> str:
    if re.match(r'^(https?://|BV[0-9A-Za-z]+$)', path or '', re.I):
        return path
    return Path(path).name or path


class WebBridge(QObject):
    messages = pyqtSignal(str)
    taskState = pyqtSignal(str)

    def __init__(self, backend, shell):
        super().__init__(shell)
        self.backend = backend
        self.shell = shell
        self._last_running = False
        self._last_cancelling = False
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(180)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

    # ── state ──────────────────────────────────────────────────────────
    @pyqtSlot(result=str)
    def state(self):
        b = self.backend
        settings = {
            'mode': self._current_mode(),
            'asrModel': b.asr_model_file.currentText(),
            'aligner': b.aligner_file.currentText(),
            'backend': b.asr_backend.currentText(),
            'srcLang': b.transcription_lang.currentData() or 'ja',
            'tgtLang': b.target_lang.currentData() or 'zh-cn',
            'outputTarget': b.output_target_combo.currentData() or '原文',
            'outputType': b.output_type_combo.currentData() or 'SRT',
            'asrLength': b.asr_length_slider.value(),
            'outDir': b.output_dir_edit.text().strip(),
            'useInputDir': b.use_input_dir_checkbox.isChecked(),
            'concurrency': b.max_concurrent_spin.value(),
            'segment': b.enable_segment_checkbox.isChecked(),
            'segmentMinutes': b.segment_duration_spin.value(),
            'autoShutdown': b.auto_shutdown_checkbox.isChecked(),
            'verbose': b.verbose_checkbox.isChecked(),
            'translatorMode': b.translator_mode.currentData() or 'online',
            'onlineTranslator': b.online_translator_group.currentText(),
            'localTranslator': b.local_translator_group.currentText(),
            'apiKey': b.gpt_token.text(),
            'apiAddress': b.gpt_address.text(),
            'apiModel': b.gpt_model.text(),
            'sakuraFile': b.sakura_file.currentText(),
            'sakuraMode': b.sakura_mode.text(),
            'proxy': b.proxy_address.text(),
            'thinking': b.thinking_checkbox.isChecked(),
            'font': b.subtitle_font_combo.currentText(),
            'subtitleType': b.subtitle_type_combo.currentData() or '硬字幕',
            'uvrModel': b.uvr_file.currentText(),
            'dictBefore': b.before_dict.toPlainText(),
            'dictGpt': b.gpt_dict.toPlainText(),
            'dictAfter': b.after_dict.toPlainText(),
            'extraPrompt': b.extra_prompt.toPlainText(),
            'promptMode': b.change_prompt_mode.currentData() or '不修改',
            'summarizePrompt': b.summarize_prompt.toPlainText(),
            'asrParams': b.param_crispasr.toPlainText(),
            'llamaParams': b.param_llama.toPlainText(),
        }
        options = {
            'srcLangs': _combo_options(b.transcription_lang, 'data'),
            'tgtLangs': _combo_options(b.target_lang, 'data'),
            'outputTargets': _combo_options(b.output_target_combo, 'data'),
            'outputTypes': _combo_options(b.output_type_combo, 'data'),
            'asrModels': _combo_options(b.asr_model_file, 'text'),
            'aligners': _combo_options(b.aligner_file, 'text'),
            'backends': _combo_options(b.asr_backend, 'text'),
            'uvrModels': _combo_options(b.uvr_file, 'text'),
            'sakuraModels': _combo_options(b.sakura_file, 'text'),
            'onlineTranslators': _combo_options(b.online_translator_group, 'text'),
            'localTranslators': _combo_options(b.local_translator_group, 'text'),
            'fonts': _combo_options(b.subtitle_font_combo, 'text'),
            'subtitleTypes': _combo_options(b.subtitle_type_combo, 'data'),
            'promptModes': _combo_options(b.change_prompt_mode, 'data'),
        }
        root = Path.cwd()
        runtime = {
            'crispasrExe': (root / 'crispasr' / ('crispasr.exe' if os.name == 'nt' else 'crispasr')).is_file(),
            'llamaExe': (root / 'llama' / ('llama-server.exe' if os.name == 'nt' else 'llama-server')).is_file(),
            'ffmpegExe': (root / 'ffmpeg' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')).is_file(),
            'asrModelCount': b.asr_model_file.count(),
            'alignerCount': b.aligner_file.count(),
            'uvrModelCount': b.uvr_file.count(),
            'sakuraModelCount': b.sakura_file.count(),
        }
        paths = {
            'output': os.path.abspath(os.path.expanduser(settings['outDir'] or b.default_output_dir())),
            'crispasr': str(root / 'crispasr'),
            'llama': str(root / 'llama'),
            'separate': str(root / 'separate'),
            'models': str(root),
            'log': str(root / LOG_PATH),
            'dict': str(root / 'project'),
        }
        return _json({
            'version': APP_VERSION,
            'running': b._task_is_running(),
            'cancelling': bool(getattr(b, '_cancel_requested', False)),
            'queue': _split_lines(b.input_files_list.toPlainText()),
            'settings': settings,
            'options': options,
            'paths': paths,
            'runtime': runtime,
            'logTail': self._read_log_tail(),
        })

    def _current_mode(self) -> str:
        b = self.backend
        if b.enable_transcription_checkbox.isChecked() and b.enable_translation_checkbox.isChecked():
            return 'both'
        if b.enable_transcription_checkbox.isChecked():
            return 'asr'
        if b.enable_translation_checkbox.isChecked():
            return 'mt'
        return 'both'

    def _read_log_tail(self) -> list[str]:
        path = Path(LOG_PATH)
        if not path.is_file():
            return []
        try:
            with path.open('rb') as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 65536))
                data = handle.read().decode('utf-8', errors='replace')
            lines = [_clean_log_line(line) for line in data.splitlines()]
            return [line for line in lines if line][-200:]
        except OSError:
            return []

    # ── settings ───────────────────────────────────────────────────────
    @pyqtSlot(str, result=str)
    def applySettings(self, payload: str):
        try:
            data = json.loads(payload or '{}')
            self._apply(data)
            return _json({'ok': True})
        except Exception as exc:
            return _json({'ok': False, 'error': str(exc)})

    def _apply(self, data: dict):
        b = self.backend
        if 'mode' in data:
            mode = data['mode']
            b.enable_transcription_checkbox.setChecked(mode != 'mt')
            b.enable_translation_checkbox.setChecked(mode != 'asr')
        if 'asrModel' in data:
            _select_combo_text(b.asr_model_file, data['asrModel'])
        if 'aligner' in data:
            _select_combo_text(b.aligner_file, data['aligner'])
        if 'backend' in data:
            _select_combo_text(b.asr_backend, data['backend'])
        if 'srcLang' in data:
            _select_combo_data(b.transcription_lang, data['srcLang'])
        if 'tgtLang' in data:
            _select_combo_data(b.target_lang, data['tgtLang'])
        if 'outputTarget' in data:
            _select_combo_data(b.output_target_combo, data['outputTarget'])
        if 'outputType' in data:
            _select_combo_data(b.output_type_combo, data['outputType'])
        if 'asrLength' in data:
            b.asr_length_slider.setValue(int(data['asrLength'] or 0))
        if 'outDir' in data and str(data['outDir']).strip():
            b.output_dir_edit.setText(str(data['outDir']).strip())
        if 'useInputDir' in data:
            b.use_input_dir_checkbox.setChecked(bool(data['useInputDir']))
        if 'concurrency' in data:
            b.max_concurrent_spin.setValue(int(data['concurrency'] or 0))
        if 'segment' in data:
            b.enable_segment_checkbox.setChecked(bool(data['segment']))
        if 'segmentMinutes' in data:
            b.segment_duration_spin.setValue(int(data['segmentMinutes'] or 10))
        if 'autoShutdown' in data:
            b.auto_shutdown_checkbox.setChecked(bool(data['autoShutdown']))
        if 'verbose' in data:
            b.verbose_checkbox.setChecked(bool(data['verbose']))
        if 'translatorMode' in data:
            _select_combo_data(b.translator_mode, data['translatorMode'])
            b.update_translator_mode_controls()
        if 'onlineTranslator' in data:
            _select_combo_text(b.online_translator_group, data['onlineTranslator'])
        if 'localTranslator' in data:
            _select_combo_text(b.local_translator_group, data['localTranslator'])
        if 'apiKey' in data:
            b.gpt_token.setText(str(data['apiKey']))
        if 'apiAddress' in data:
            b.gpt_address.setText(str(data['apiAddress']))
        if 'apiModel' in data:
            b.gpt_model.setText(str(data['apiModel']))
        if 'sakuraFile' in data:
            _select_combo_text(b.sakura_file, data['sakuraFile'], add_missing=True)
        if 'sakuraMode' in data:
            b.sakura_mode.setText(str(data['sakuraMode']))
        if 'proxy' in data:
            b.proxy_address.setText(str(data['proxy']))
        if 'thinking' in data:
            b.thinking_checkbox.setChecked(bool(data['thinking']))
        if 'font' in data and str(data['font']).strip():
            _select_combo_text(b.subtitle_font_combo, data['font'], add_missing=True)
        if 'subtitleType' in data:
            _select_combo_data(b.subtitle_type_combo, data['subtitleType'])
        if 'uvrModel' in data:
            _select_combo_text(b.uvr_file, data['uvrModel'], add_missing=True)
        if 'dictBefore' in data:
            b.before_dict.setPlainText(str(data['dictBefore']))
        if 'dictGpt' in data:
            b.gpt_dict.setPlainText(str(data['dictGpt']))
        if 'dictAfter' in data:
            b.after_dict.setPlainText(str(data['dictAfter']))
        if 'extraPrompt' in data:
            b.extra_prompt.setPlainText(str(data['extraPrompt']))
        if 'promptMode' in data:
            _select_combo_data(b.change_prompt_mode, data['promptMode'])
        if 'summarizePrompt' in data:
            b.summarize_prompt.setPlainText(str(data['summarizePrompt']))
        if 'asrParams' in data:
            b.param_crispasr.setPlainText(str(data['asrParams']))
        if 'llamaParams' in data:
            b.param_llama.setPlainText(str(data['llamaParams']))
        b.update_processing_controls()
        b.update_output_dir_controls()
        b.save_config(silent=True)

    @pyqtSlot(str, result=str)
    def setQueue(self, payload: str):
        try:
            paths = json.loads(payload or '[]')
            self.backend.input_files_list.setPlainText('\n'.join(paths))
            return _json({'ok': True, 'queue': paths})
        except Exception as exc:
            return _json({'ok': False, 'error': str(exc)})

    # ── core run ───────────────────────────────────────────────────────
    @pyqtSlot(str, result=str)
    def startCore(self, payload: str):
        try:
            data = json.loads(payload or '{}')
            paths = data.pop('queue', None)
            self._apply(data)
            if paths is not None:
                self.backend.input_files_list.setPlainText('\n'.join(paths))
            paths = _split_lines(self.backend.input_files_list.toPlainText())
            if not paths:
                return _json({'ok': False, 'error': '请先添加要处理的文件或链接。'})
            if self.backend._task_is_running():
                return _json({'ok': False, 'error': '已有任务正在运行。'})
            mode = self._current_mode()
            needs_asr = mode != 'mt' and any(not p.lower().endswith(('.srt', '.ass', '.vtt')) for p in paths)
            if needs_asr and not self.backend.asr_model_file.currentText():
                return _json({'ok': False, 'error': 'crispasr 文件夹中没有听写模型，请先放入 .gguf 模型并在「模型」页刷新。'})
            if needs_asr and not self.backend.aligner_file.currentText():
                return _json({'ok': False, 'error': 'crispasr 文件夹中没有强制对齐模型，请先放入 aligner .gguf 文件。'})
            if mode != 'asr' and (self.backend.translator_mode.currentData() == 'local'):
                translator = self.backend.local_translator_group.currentText()
                if ('sakura' in translator or 'llamacpp' in translator) and not self.backend.sakura_file.currentText():
                    return _json({'ok': False, 'error': 'llama 文件夹中没有本地翻译模型，请先放入 .gguf 模型。'})
            self.backend.run_worker()
            return _json({'ok': True})
        except Exception as exc:
            return _json({'ok': False, 'error': str(exc)})

    @pyqtSlot(result=str)
    def cancel(self):
        self.backend.cancel_task()
        return _json({'ok': True, 'running': self.backend._task_is_running()})

    # ── tools ──────────────────────────────────────────────────────────
    @pyqtSlot(str, str, result=str)
    def startTool(self, kind: str, payload: str):
        try:
            data = json.loads(payload or '{}')
            paths = [str(item) for item in (data.get('files') or []) if str(item).strip()]
            if self.backend._task_is_running():
                return _json({'ok': False, 'error': '已有任务正在运行。'})
            if not paths:
                return _json({'ok': False, 'error': '请先选择文件。'})
            self._apply(data.get('settings') or {})
            if kind == 'clip':
                self.backend.clip_files_list.setPlainText('\n'.join(paths))
                self.backend.clip_start_time.setText(str(data.get('start') or '00:00:00.000'))
                self.backend.clip_end_time.setText(str(data.get('end') or '00:01:30.000'))
                self.backend.run_clip()
            elif kind == 'separate':
                if not self.backend.uvr_file.currentText():
                    return _json({'ok': False, 'error': 'separate 文件夹中没有 UVR 模型（.onnx）。'})
                self.backend.uvr_file_list.setPlainText('\n'.join(paths))
                self.backend.run_vocal_split()
            elif kind == 'synth':
                subtitles = [str(item) for item in (data.get('subtitleFiles') or []) if str(item).strip()]
                if not subtitles:
                    return _json({'ok': False, 'error': '请选择与视频一一对应的字幕文件。'})
                if len(paths) != len(subtitles):
                    return _json({'ok': False, 'error': f'视频 {len(paths)} 个、字幕 {len(subtitles)} 个，数量必须一致。'})
                self.backend.synth_video_files_list.setPlainText('\n'.join(paths))
                self.backend.synth_srt_files_list.setPlainText('\n'.join(subtitles))
                self.backend.run_synth()
            elif kind == 'audio':
                audio = [p for p in paths if p.lower().endswith(('.wav', '.mp3', '.flac'))]
                images = [p for p in paths if p.lower().endswith(('.png', '.jpg', '.jpeg'))]
                if not audio or not images:
                    return _json({'ok': False, 'error': '请同时选择音频和封面图片。'})
                if len(audio) != len(images):
                    return _json({'ok': False, 'error': f'音频 {len(audio)} 个、图片 {len(images)} 个，数量必须一致。'})
                self.backend.synth_audio_files_list.setPlainText('\n'.join(paths))
                self.backend.run_synth_audio()
            elif kind == 'summarize':
                self.backend.summarize_files_list.setPlainText('\n'.join(paths))
                self.backend.run_summarize()
            else:
                return _json({'ok': False, 'error': '未知工具。'})
            return _json({'ok': True})
        except Exception as exc:
            return _json({'ok': False, 'error': str(exc)})

    # ── pickers / paths ────────────────────────────────────────────────
    @pyqtSlot(str, result=str)
    def pickFiles(self, kind: str):
        filters = {
            'core': '媒体与字幕 (*.mp4 *.mkv *.mov *.avi *.flv *.webm *.wav *.mp3 *.m4a *.flac *.aac *.srt *.ass *.vtt);;所有文件 (*.*)',
            'video': '视频 (*.mp4 *.mkv *.mov *.avi *.flv *.webm);;所有文件 (*.*)',
            'audio': '音频 (*.wav *.mp3 *.m4a *.flac *.aac);;所有文件 (*.*)',
            'subtitle': '字幕 (*.srt *.ass *.vtt *.txt);;所有文件 (*.*)',
            'media': '音频与视频 (*.mp4 *.mkv *.mov *.wav *.mp3 *.m4a *.flac);;所有文件 (*.*)',
            'audioimage': '音频与图片 (*.wav *.mp3 *.flac *.png *.jpg *.jpeg);;所有文件 (*.*)',
        }
        path, _ = QFileDialog.getOpenFileNames(
            self.shell, '选择文件', str(Path.home()), filters.get(kind, filters['core'])
        )
        return _json({'files': path})

    @pyqtSlot(str, result=str)
    def pickDir(self, current: str = ''):
        start = current or str(Path.home())
        path = QFileDialog.getExistingDirectory(self.shell, '选择输出文件夹', start)
        return _json({'path': path})

    @pyqtSlot(str, result=str)
    def openPath(self, kind: str):
        b = self.backend
        root = Path.cwd()
        targets = {
            'output': lambda: os.path.abspath(os.path.expanduser(
                b.output_dir_edit.text().strip() or b.default_output_dir())),
            'crispasr': lambda: str(root / 'crispasr'),
            'llama': lambda: str(root / 'llama'),
            'separate': lambda: str(root / 'separate'),
            'models': lambda: str(root),
            'log': lambda: str(root / LOG_PATH),
            'dict': lambda: str(root / 'project'),
        }
        try:
            target = targets.get(kind)
            if target:
                open_path(target())
            return _json({'ok': True})
        except Exception as exc:
            return _json({'ok': False, 'error': str(exc)})

    @pyqtSlot(str, result=str)
    def openExternal(self, url: str):
        QDesktopServices.openUrl(QUrl(url))
        return _json({'ok': True})

    # ── maintenance ────────────────────────────────────────────────────
    @pyqtSlot(result=str)
    def refreshModels(self):
        b = self.backend
        b.refresh_speech_model_lists()
        b.refresh_language_model_lists()
        b.refresh_uvr_model_list()
        return self.state()

    @pyqtSlot(result=str)
    def clearCache(self):
        if self.backend._task_is_running():
            return _json({'ok': False, 'error': '任务运行中，暂时不能清理中间文件。'})
        reply = QMessageBox.question(
            self.shell, '清理中间文件',
            '将删除 project 下的缓存和中间字幕，不会删除输出文件。继续吗？',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return _json({'ok': False, 'cancelled': True})
        self.backend.cleaner()
        return _json({'ok': True})

    @pyqtSlot(str, result=str)
    def testModel(self, kind: str):
        if self.backend._task_is_running():
            return _json({'ok': False, 'error': '已有任务正在运行。'})
        try:
            if kind == 'asr':
                self.backend.run_test_offline_asr()
            elif kind == 'local':
                self.backend.run_test_offline_translation()
            elif kind == 'online':
                self.backend.run_test_online_api()
            else:
                return _json({'ok': False, 'error': '这个模型暂不支持自检。'})
            return _json({'ok': True})
        except Exception as exc:
            return _json({'ok': False, 'error': str(exc)})

    @pyqtSlot(str, result=str)
    def saveDictionaries(self, payload: str):
        try:
            data = json.loads(payload or '{}')
            self._apply(data)
            return _json({'ok': True})
        except Exception as exc:
            return _json({'ok': False, 'error': str(exc)})

    # ── polling ────────────────────────────────────────────────────────
    def _poll(self):
        entries = self.backend.msg_queue.drain()
        batch = []
        for target, text in entries:
            if UIMessageQueue.is_completion_entry(target):
                batch.append({'kind': 'completion', 'text': '所有文件处理完成！'})
                try:
                    self.backend._check_auto_shutdown()
                except Exception:
                    pass
            elif target == 'status':
                batch.append({'kind': 'status', 'text': text})
            else:
                batch.append({'kind': 'detail', 'text': text})
        if batch:
            self.messages.emit(_json(batch))

        running = self.backend._task_is_running()
        cancelling = bool(getattr(self.backend, '_cancel_requested', False))
        if running != self._last_running or cancelling != self._last_cancelling:
            self._last_running = running
            self._last_cancelling = cancelling
            self.taskState.emit(_json({'running': running, 'cancelling': cancelling}))
