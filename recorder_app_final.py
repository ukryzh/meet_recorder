"""
MeetRecorder v1.4 — фиксы:
  - видео: точный тайминг через time.perf_counter (нет ускорения)
  - аудио: raw-файлы явно закрываются до build_output
  - stop(): ждёт пока все потоки реально завершатся перед постобработкой
  - ffmpeg screen процесс изолирован (не затирается другими потоками)
  - transcript: логирует что именно пошло не так + проверка mp3 перед Whisper
  - CABLE Output: добавлен fallback если device 23 недоступен
  - логотип: иконка окна + логотип в GUI, зашиты в base64 (нет внешних файлов)

Зависимости:
    pip install sounddevice numpy mss Pillow pydub openai-whisper
    + ffmpeg в PATH
"""

import os
import math
import time
import threading
import subprocess
import warnings
import io
import base64
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
from datetime import datetime

import numpy as np
import sounddevice as sd
import mss
import whisper

warnings.filterwarnings("ignore")

# ──────────────────────────────────────────────
# НАСТРОЙКИ ЗАПИСИ
# ──────────────────────────────────────────────
FPS         = 10          # кадров/сек экрана
SEGMENT_SEC = 30          # чанки для Whisper

# ──────────────────────────────────────────────
# АВТОПОДБОР УСТРОЙСТВ
# ──────────────────────────────────────────────
def detect_loopback_device():
    """
    Находит loopback-устройство активного аудиовыхода через pyaudiowpatch.
    Это то же самое что делает OBS — читает поток с динамиков/наушников
    без VB-Cable, пользователь при этом всё слышит.
    Возвращает dict с полями index, name, channels, rate или None.
    """
    try:
        import pyaudiowpatch as pyaudio
        with pyaudio.PyAudio() as p:
            # wasapi_loopback_auto — находит дефолтное устройство вывода
            # и возвращает его loopback-аналог
            try:
                loopback = p.get_default_wasapi_loopback()
            except AttributeError:
                # старые версии pyaudiowpatch
                loopback = None
                for i in range(p.get_device_count()):
                    d = p.get_device_info_by_index(i)
                    if d.get("isLoopbackDevice") and d["maxInputChannels"] > 0:
                        loopback = d
                        break

            if loopback is None:
                return None

            return {
                "index":    int(loopback["index"]),
                "name":     loopback["name"],
                "channels": min(int(loopback["maxInputChannels"]), 2),
                "rate":     int(loopback["defaultSampleRate"]),
                "loopback": True,   # флаг — записываем через pyaudiowpatch
            }
    except ImportError:
        return None
    except Exception as e:
        print(f"detect_loopback_device error: {e}")
        return None


def detect_devices():
    """
    Возвращает (mic_info, desktop_info).
    desktop_info — loopback устройство (без VB-Cable).
    """
    devices = sd.query_devices()

    # --- Микрофон: системный по умолчанию ---
    mic = None
    try:
        def_idx = sd.default.device[0]
        if def_idx is not None and def_idx >= 0:
            d = devices[def_idx]
            mic = {
                "index":    def_idx,
                "name":     d["name"],
                "channels": min(d["max_input_channels"], 1),
                "rate":     int(d["default_samplerate"]),
                "loopback": False,
            }
    except Exception:
        pass

    # --- Desktop audio: WASAPI loopback через pyaudiowpatch ---
    desktop = detect_loopback_device()

    return mic, desktop


def list_input_devices():
    """
    Возвращает два раздельных списка через pyaudiowpatch:
      - mic_devices:     обычные входные устройства (не loopback), без дублей по имени
      - desktop_devices: loopback устройства
    Не фильтруем по hostapi внутри pyaudiowpatch — там все устройства
    идут через один hostapi и WASAPI-фильтр даёт пустой список.
    """
    mic_devices = []
    desktop_devices = []
    seen = set()

    try:
        import pyaudiowpatch as pyaudio
        with pyaudio.PyAudio() as p:
            for i in range(p.get_device_count()):
                d = p.get_device_info_by_index(i)
                if d["maxInputChannels"] < 1:
                    continue
                name = d["name"]
                if name in seen:
                    continue
                seen.add(name)
                is_loopback = bool(d.get("isLoopbackDevice", False))
                info = {
                    "index":    i,
                    "name":     name,
                    "channels": min(int(d["maxInputChannels"]), 2),
                    "rate":     int(d["defaultSampleRate"]),
                    "loopback": is_loopback,
                }
                if is_loopback:
                    desktop_devices.append(info)
                else:
                    mic_devices.append(info)
    except ImportError:
        # fallback через sounddevice — только WASAPI
        seen2 = set()
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] < 1:
                continue
            hostapi = sd.query_hostapis(d["hostapi"])["name"]
            if "wasapi" not in hostapi.lower():
                continue
            name = d["name"]
            if name in seen2:
                continue
            seen2.add(name)
            mic_devices.append({
                "index":    i,
                "name":     name,
                "channels": min(d["max_input_channels"], 1),
                "rate":     int(d["default_samplerate"]),
                "loopback": False,
            })

    return mic_devices, desktop_devices


# ──────────────────────────────────────────────
# ЛОГОТИП (зашит в base64, внешние файлы не нужны)
# ──────────────────────────────────────────────
_LOGO_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAYAAABccqhmAAAFUElEQVR4nO3a2W3cShBA0dGDg3BGjtkZOQu9DxmQtczGtcl7TgAGwaq+7BH8crlcXi9A0n97PwCwHwGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAMAGAsB97PwDr+vP6a/a/8fPl9wJPwoheLpfL694PwXxLHPRnCcPxCcBB7XHg7xGE4xGAAxnx0F8jBscgAIM70qG/RgzGJQADOsOhv0YMxiIAAznzwf9MCMYgAAMoHfzPhGBfArCj8sH/TAj2IQA7cPCvE4Jt+a/AG3P4b/N+tuUGsBGL/Ty3gfW5AWzA4Z/Ge1ufG8CKLPBy3AbW4QawEod/Wd7nOgRgBZZ1Hd7r8vwEWJAF3Y6fBMtwA1iIw78t73sZArAAy7gP730+AZjJEu7L+59HAGawfGMwh+kEYCJLNxbzmEYAJrBsYzKX5wnAkyzZ2MznOQLwBMt1DOb0OAF4kKU6FvN6jABAmAA8wNfkmMztPgG4wxIdm/ndJgA3WJ5zMMfrBADCBOAKX41zMc/vCcA3LMs5metXAgBhAvCJr8S5me9HAvAPy9Fgzu8EAMIE4C9fhRbzfiMAECYAF1+DKnMXAEjLB8BXoK0+/3wAoCwdgHr9eVPeg3QAoE4AICwbgPK1j6+q+5ANACAAkJYMQPW6x23FvUgGAHgjABCWC0DxmsfjavuRCwDwTgAgTAAgLBWA2u87pintSSoAwEcCAGECAGECAGECAGGZAJT+sst8lX3JBAD4SgAgTAAgTAAgTAAgTAAgTAAgTAAgTAAgTAAgTAAgTAAgTAAgTAAgTAAgTAAgLBOAny+/934EDqSyL5kAAF8JAIQJAIQJAIQJAISlAlD5yy7zlPYkFQDgIwGAMAGAsFwASr/veF5tP3IBAN4JAIQlA1C75vGY4l4kAwC8EQAIywageN3juuo+ZAMACACkpQNQvfbxUXkP0gGAunwAyvXH/PMBgDIBuPgKVJm7AECaAPzla9Bi3m8EAMIE4B++Cg3m/E4APrEc52a+HwkAhAnAN3wlzslcvxKAKyzLuZjn9wQAwgTgBl+NczDH6wTgDstzbOZ3mwA8wBIdk7ndJwAQJgAP8jU5FvN6jAA8wVIdgzk9TgCeZLnGZj7PEYAJLNmYzOV5AjCRZRuLeUwjADNYujGYw3QCMJPl25f3P48ALMAS7sN7n08AFmIZt+V9L+Plcrm87v0QZ/Pn9dfej3BaDv6y3ABWYEnX4b0uTwBWYlmX5X2uw0+ADfhJMJ2Dvy43gA1Y4mm8t/W5AWzMbeA+B387bgAbs9y3eT/bcgPYkdvAOwd/HwIwgHIIHPx9CcBASiFw8McgAAM6cwgc/LEIwODOEAOHflwCcCBHioFDfwwCcFAjxsChPx4BOIk9guDAH58AnNwSYXDQz0sAIMx/BYYwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYAwAYCw/wFv0BTeYLCfhwAAAABJRU5ErkJggg=="
_LOGO_ICO_B64 = "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"

def _load_logo_png(size=48):
    """Возвращает PIL Image логотипа нужного размера."""
    from PIL import Image
    data = base64.b64decode(_LOGO_PNG_B64)
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    return img.resize((size, size), Image.LANCZOS)

def _set_taskbar_icon():
    """
    Говорит Windows что это отдельное приложение, а не python.exe.
    Должна вызываться ДО создания tkinter окна.
    """
    try:
        import ctypes
        # уникальный ID приложения — Windows группирует окна в таскбаре по нему
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("meetrecorder.app.1")
    except Exception as e:
        print(f"AppUserModelID error: {e}")


def _set_window_icon(root):
    """Устанавливает иконку окна и таскбара."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        ico_path = os.path.join(script_dir, "logo.ico")
        # всегда перезаписываем — чтобы подхватить обновлённую иконку
        with open(ico_path, "wb") as f:
            f.write(base64.b64decode(_LOGO_ICO_B64))
        root.iconbitmap(ico_path)
    except Exception as e:
        print(f"iconbitmap failed: {e}")
        # fallback через wm_iconphoto
        try:
            from PIL import Image, ImageTk
            data = base64.b64decode(_LOGO_PNG_B64)
            img = Image.open(io.BytesIO(data)).resize((64, 64), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            root.wm_iconphoto(True, photo)
            root._icon_photo = photo
        except Exception as e2:
            print(f"wm_iconphoto failed: {e2}")


# ──────────────────────────────────────────────
# УТИЛИТЫ
# ──────────────────────────────────────────────
def unique_folder(base_dir, name):
    path = os.path.join(base_dir, name)
    counter = 1
    while os.path.exists(path):
        path = os.path.join(base_dir, f"{name} ({counter})")
        counter += 1
    os.makedirs(path)
    return path


# ──────────────────────────────────────────────
# РЕКОРДЕР
# ──────────────────────────────────────────────
class Recorder:
    def __init__(self, log_cb=None):
        self.log_cb = log_cb or print
        self.stop_event = threading.Event()
        self.out_dir = None
        self.project_name = None
        self._threads = []
        self._screen_ffmpeg_proc = None
        # устройства — задаются снаружи перед start(), или detect_devices()
        self.mic_info = None
        self.desktop_info = None

    def log(self, msg):
        self.log_cb(msg)

    def start(self, out_dir):
        self.stop_event.clear()
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.project_name = f"meeting_{ts}"
        self.out_dir = unique_folder(out_dir, self.project_name)

        # временные файлы — в отдельную папку, чтобы не путать пользователя
        self.tmp_dir = os.path.join(self.out_dir, "_tmp")
        os.makedirs(self.tmp_dir)

        self._threads = [
            threading.Thread(target=self._record_screen,  daemon=True, name="screen"),
            threading.Thread(target=self._record_mic,     daemon=True, name="mic"),
            threading.Thread(target=self._record_desktop, daemon=True, name="desktop"),
        ]
        for t in self._threads:
            t.start()
        self.log("▶ Запись запущена")

    def stop(self):
        self.log("■ Останавливаем…")
        self.stop_event.set()

        # ФИКС: закрываем stdin ffmpeg чтобы screen-поток завершился
        if self._screen_ffmpeg_proc and self._screen_ffmpeg_proc.poll() is None:
            try:
                self._screen_ffmpeg_proc.stdin.close()
            except Exception:
                pass
            try:
                self._screen_ffmpeg_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._screen_ffmpeg_proc.kill()

        # ФИКС: ждём все потоки — особенно mic и desktop чтобы они дописали raw-файлы
        for t in self._threads:
            t.join(timeout=10)
        self.log("■ Потоки остановлены, файлы записаны")

    # ── Экран → pipe → ffmpeg ─────────────────
    def _record_screen(self):
        video_path = os.path.join(self.tmp_dir, "_video_noaudio.mp4")
        try:
            with mss.mss() as sct:
                mon = sct.monitors[1]
                w = mon["width"]  if mon["width"]  % 2 == 0 else mon["width"]  - 1
                h = mon["height"] if mon["height"] % 2 == 0 else mon["height"] - 1

                cmd = [
                    "ffmpeg", "-y",
                    "-f", "rawvideo", "-vcodec", "rawvideo",
                    "-pix_fmt", "bgr24",
                    "-s", f"{w}x{h}",
                    "-r", str(FPS),
                    "-i", "pipe:0",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                    "-pix_fmt", "yuv420p",
                    video_path
                ]
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                # ФИКС: сохраняем в отдельную переменную (не _ffmpeg_proc)
                self._screen_ffmpeg_proc = proc

                frame_interval = 1.0 / FPS
                self.log(f"✓ Экран: {w}x{h} @ {FPS}fps")

                region = {"left": mon["left"], "top": mon["top"], "width": w, "height": h}

                while not self.stop_event.is_set():
                    # ФИКС: точный тайминг через perf_counter
                    t0 = time.perf_counter()

                    img = sct.grab(region)
                    # BGRA → BGR быстрее через numpy
                    frame = np.frombuffer(img.bgra, dtype=np.uint8).reshape(h, w, 4)
                    bgr = frame[:, :, :3]  # убираем alpha канал

                    try:
                        proc.stdin.write(bgr.tobytes())
                    except (BrokenPipeError, OSError):
                        break

                    # ФИКС: точный sleep — вычитаем время захвата и записи кадра
                    elapsed = time.perf_counter() - t0
                    sleep = frame_interval - elapsed
                    if sleep > 0:
                        time.sleep(sleep)

                # Явно закрываем stdin если ещё не закрыт
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                proc.wait(timeout=10)

        except Exception as e:
            self.log(f"✗ Экран: {e}")

    # ── Микрофон ──────────────────────────────
    def _record_mic(self):
        if not self.mic_info:
            self.log("✗ Микрофон не выбран, пропускаем")
            return
        m = self.mic_info
        path = os.path.join(self.tmp_dir, "_mic_raw.raw")
        try:
            f = open(path, "wb")
            try:
                def cb(indata, frames, time_info, status):
                    f.write(indata.tobytes())

                # пробуем каналы из device info, если падает — fallback на mono
                for ch in ([m["channels"]] if m["channels"] == 1 else [m["channels"], 1]):
                    try:
                        with sd.InputStream(device=m["index"], samplerate=m["rate"],
                                            channels=ch, dtype="int16",
                                            callback=cb, blocksize=1024):
                            self.log(f"✓ Микрофон [{m['index']}]: {m['name']} | {ch}ch {m['rate']}Hz")
                            # сохраняем реальное кол-во каналов для ffmpeg
                            m["channels"] = ch
                            while not self.stop_event.is_set():
                                time.sleep(0.05)
                            self.log("  Микрофон: останавливаем поток…")
                        break  # успешно — выходим из цикла
                    except Exception as e:
                        if ch == 1:
                            raise  # уже пробовали mono — пробрасываем ошибку
                        self.log(f"  {ch}ch не поддерживается, пробуем mono…")
            finally:
                f.flush()
                f.close()
                size = os.path.getsize(path)
                self.log(f"  Микрофон: записано {size // 1024} KB")
        except Exception as e:
            self.log(f"✗ Микрофон: {e}")

    # ── Desktop audio (WASAPI loopback — как OBS) ──
    def _record_desktop(self):
        if not self.desktop_info:
            self.log("⚠ Desktop audio не найден, пропускаем")
            return
        d = self.desktop_info
        path = os.path.join(self.tmp_dir, "_desktop_raw.raw")
        try:
            f = open(path, "wb")
            try:
                if d.get("loopback"):
                    # pyaudiowpatch — настоящий WASAPI loopback без VB-Cable
                    import pyaudiowpatch as pyaudio
                    chunk = 1024
                    with pyaudio.PyAudio() as p:
                        stream = p.open(
                            format=pyaudio.paInt16,
                            channels=d["channels"],
                            rate=d["rate"],
                            frames_per_buffer=chunk,
                            input=True,
                            input_device_index=d["index"],
                        )
                        self.log(f"✓ Desktop audio (loopback): {d['name']} | {d['channels']}ch {d['rate']}Hz")
                        while not self.stop_event.is_set():
                            data = stream.read(chunk, exception_on_overflow=False)
                            f.write(data)
                        stream.stop_stream()
                        stream.close()
                else:
                    # fallback: sounddevice (например, CABLE Output если есть)
                    def cb(indata, frames, time_info, status):
                        f.write(indata.tobytes())
                    with sd.InputStream(device=d["index"], samplerate=d["rate"],
                                        channels=d["channels"], dtype="int16",
                                        callback=cb, blocksize=1024):
                        self.log(f"✓ Desktop audio (fallback): {d['name']} | {d['channels']}ch {d['rate']}Hz")
                        while not self.stop_event.is_set():
                            time.sleep(0.05)
            finally:
                f.flush()
                f.close()
                size = os.path.getsize(path)
                self.log(f"  Desktop audio: записано {size // 1024} KB")
        except Exception as e:
            self.log(f"✗ Desktop audio: {e}")

    # ── Постобработка ─────────────────────────
    def build_output(self):
        # временные файлы — в _tmp/
        mic_raw     = os.path.join(self.tmp_dir, "_mic_raw.raw")
        desktop_raw = os.path.join(self.tmp_dir, "_desktop_raw.raw")
        mic_wav     = os.path.join(self.tmp_dir, "_mic.wav")
        desktop_wav = os.path.join(self.tmp_dir, "_desktop.wav")
        mixed_wav   = os.path.join(self.tmp_dir, "_mixed.wav")
        video_raw   = os.path.join(self.tmp_dir, "_video_noaudio.mp4")
        # финальные файлы — в корне папки встречи
        mp4_path    = os.path.join(self.out_dir, f"{self.project_name}.mp4")
        mp3_path    = os.path.join(self.out_dir, f"{self.project_name}.mp3")

        def run(cmd, label):
            self.log(f"  → {label}")
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode != 0:
                raise RuntimeError(f"{label}:\n{r.stderr.decode(errors='replace')[-500:]}")

        mic_ok = os.path.exists(mic_raw) and os.path.getsize(mic_raw) > 1000
        desktop_ok = os.path.exists(desktop_raw) and os.path.getsize(desktop_raw) > 1000

        if not mic_ok:
            self.log(f"⚠ Микрофон raw пустой или отсутствует")
        if not desktop_ok:
            self.log(f"⚠ Desktop raw пустой или отсутствует")
        if not mic_ok and not desktop_ok:
            raise RuntimeError("Нет ни микрофона, ни desktop audio. Проверь устройства.")

        # mic raw → wav (параметры из mic_info)
        self.log("Конвертируем аудио…")
        if mic_ok and self.mic_info:
            run(["ffmpeg", "-y", "-f", "s16le",
                 "-ar", str(self.mic_info["rate"]),
                 "-ac", str(self.mic_info["channels"]),
                 "-i", mic_raw, mic_wav], "mic → wav")

        # desktop raw → wav (параметры из desktop_info)
        if desktop_ok and self.desktop_info:
            run(["ffmpeg", "-y", "-f", "s16le",
                 "-ar", str(self.desktop_info["rate"]),
                 "-ac", str(self.desktop_info["channels"]),
                 "-i", desktop_raw, desktop_wav], "desktop → wav")

        # микшируем
        if mic_ok and desktop_ok:
            run([
                "ffmpeg", "-y",
                "-i", mic_wav, "-i", desktop_wav,
                "-filter_complex",
                "[0:a]aresample=48000,pan=stereo|c0=c0|c1=c0[mic];"
                "[mic][1:a]amix=inputs=2:duration=longest:dropout_transition=0",
                mixed_wav
            ], "mix mic+desktop")
        elif mic_ok:
            self.log("⚠ Desktop audio пуст — только микрофон")
            run(["ffmpeg", "-y", "-i", mic_wav,
                 "-af", "aresample=48000,pan=stereo|c0=c0|c1=c0",
                 mixed_wav], "mic → stereo")
        else:
            self.log("⚠ Микрофон пуст — только desktop audio")
            run(["ffmpeg", "-y", "-i", desktop_wav,
                 "-af", "aresample=48000",
                 mixed_wav], "desktop → stereo")

        # видео + аудио → mp4
        self.log("Собираем MP4…")
        has_video = os.path.exists(video_raw) and os.path.getsize(video_raw) > 1000
        if has_video:
            run(["ffmpeg", "-y", "-i", video_raw, "-i", mixed_wav,
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest",
                 mp4_path], "видео+аудио → mp4")
        else:
            self.log("⚠ Видео пусто — сохраняем аудио в mp4")
            run(["ffmpeg", "-y", "-i", mixed_wav,
                 "-c:a", "aac", "-b:a", "128k", mp4_path], "аудио → mp4")

        # mp4 → mp3
        self.log("Извлекаем MP3…")
        run(["ffmpeg", "-y", "-i", mp4_path, "-q:a", "2", "-map", "a", mp3_path], "mp4 → mp3")

        # проверяем что mp3 реально содержит аудио
        mp3_size = os.path.getsize(mp3_path) if os.path.exists(mp3_path) else 0
        self.log(f"  MP3 размер: {mp3_size // 1024} KB")
        if mp3_size < 5000:
            self.log("⚠ MP3 подозрительно маленький — возможно аудио не записалось")

        # удаляем временные файлы — пользователю они не нужны
        self.log("  Очищаем временные файлы…")
        try:
            shutil.rmtree(self.tmp_dir)
            self.log("  ✓ временные файлы удалены")
        except Exception as e:
            self.log(f"  ⚠ Не удалось удалить _tmp: {e}")

        return mp4_path, mp3_path


# ──────────────────────────────────────────────
# ТРАНСКРИБАЦИЯ
# ──────────────────────────────────────────────
def transcribe(mp3_path, out_dir, progress_cb=None, stop_event=None, log_cb=None):
    from pydub import AudioSegment

    log = log_cb or print

    # ФИКС: проверяем mp3 перед загрузкой в Whisper
    if not os.path.exists(mp3_path):
        log(f"✗ Транскрипт: файл не найден: {mp3_path}")
        return None
    if os.path.getsize(mp3_path) < 5000:
        log(f"✗ Транскрипт: MP3 слишком маленький ({os.path.getsize(mp3_path)} байт), вероятно пустой")
        return None

    log("Загружаем модель Whisper…")
    model = whisper.load_model("small")

    log("Загружаем аудио…")
    audio = AudioSegment.from_file(mp3_path)
    duration_ms = len(audio)
    log(f"  Длительность: {duration_ms // 1000} сек")

    if duration_ms < 1000:
        log("✗ Транскрипт: аудио короче 1 секунды, нечего транскрибировать")
        return None

    total = math.ceil(duration_ms / (SEGMENT_SEC * 1000))
    transcript_parts = []

    for i in range(total):
        if stop_event and stop_event.is_set():
            return None
        chunk = audio[i * SEGMENT_SEC * 1000 : min((i+1) * SEGMENT_SEC * 1000, duration_ms)]
        tmp = os.path.join(out_dir, f"_chunk_{i}.wav")
        chunk.export(tmp, format="wav")

        # ФИКС: проверяем что чанк не пустой
        chunk_size = os.path.getsize(tmp)
        if chunk_size < 1000:
            log(f"  ⚠ Чанк {i+1} пустой, пропускаем")
            os.remove(tmp)
            if progress_cb:
                progress_cb(i + 1, total)
            continue

        result = model.transcribe(tmp, language="ru")
        text = result["text"].strip()
        if text:
            transcript_parts.append(text)
            log(f"  Чанк {i+1}/{total}: {text[:60]}{'…' if len(text) > 60 else ''}")
        else:
            log(f"  Чанк {i+1}/{total}: (тишина)")
        os.remove(tmp)
        if progress_cb:
            progress_cb(i + 1, total)

    txt_path = os.path.join(out_dir, "transcript.txt")
    final_text = " ".join(transcript_parts).strip()

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(final_text)

    # ФИКС: логируем итог
    if final_text:
        log(f"✓ Транскрипт: {len(final_text)} символов")
    else:
        log("⚠ Транскрипт пустой — речь не распознана (тишина или неверный язык?)")

    return txt_path


# ──────────────────────────────────────────────
# GUI
# ──────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MeetRecorder v1.4")
        self.resizable(True, True)
        self.minsize(440, 620)
        _set_window_icon(self)
        try:
            from PIL import ImageTk
            self._logo_img = ImageTk.PhotoImage(_load_logo_png(40))
        except Exception:
            self._logo_img = None
        # определяем устройства до построения UI
        self._mic_devices, self._desktop_devices = list_input_devices()
        self._mic_auto, self._desktop_auto = detect_devices()
        self._build_ui()
        self.recorder = Recorder(log_cb=self._log)
        self.recording = False
        self.stop_transcribe = threading.Event()
        self.mp3_path = None
        self.out_dir = None

    def _build_ui(self):
        BG   = "#0f0f0f"
        CARD = "#1a1a1a"
        ACC  = "#e8ff47"
        FG   = "#f0f0f0"
        DIM  = "#555555"
        MONO = ("Consolas", 10)

        self.configure(bg=BG)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(10, weight=1)

        # заголовок
        hdr = tk.Frame(self, bg=BG)
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 0))
        if self._logo_img:
            tk.Label(hdr, image=self._logo_img, bg=BG).pack(side="left", padx=(0, 10))
        tk.Label(hdr, text="MEETRECORDER", font=("Consolas", 16, "bold"),
                 bg=BG, fg=ACC).pack(side="left")
        tk.Label(hdr, text="v1.4", font=MONO, bg=BG, fg=DIM).pack(side="left", padx=8)

        # папка
        ff = tk.Frame(self, bg=CARD, pady=10, padx=14)
        ff.grid(row=1, column=0, sticky="ew", padx=20, pady=(14, 0))
        ff.columnconfigure(0, weight=1)
        tk.Label(ff, text="ПАПКА СОХРАНЕНИЯ", font=("Consolas", 9),
                 bg=CARD, fg=DIM).grid(row=0, column=0, columnspan=2, sticky="w")
        self.folder_var = tk.StringVar(value=os.path.expanduser("~/Desktop"))
        tk.Label(ff, textvariable=self.folder_var, font=MONO,
                 bg=CARD, fg=FG, anchor="w").grid(row=1, column=0, sticky="ew")
        tk.Button(ff, text="…", font=MONO, bg="#2a2a2a", fg=ACC,
                  relief="flat", cursor="hand2", activebackground="#333",
                  command=self._pick_folder).grid(row=1, column=1, padx=(8, 0))

        # ── выбор устройств
        dev_frame = tk.Frame(self, bg=CARD, pady=10, padx=14)
        dev_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 0))
        dev_frame.columnconfigure(1, weight=1)

        def dev_label(d):
            return d["name"][:48]

        # раздельные словари для микрофонов и loopback устройств
        self._mic_map     = {dev_label(d): d for d in self._mic_devices}
        self._desktop_map = {dev_label(d): d for d in self._desktop_devices}

        mic_labels     = list(self._mic_map.keys())
        # ── микрофон
        tk.Label(dev_frame, text="МИК", font=("Consolas", 9),
                 bg=CARD, fg=DIM).grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.mic_var = tk.StringVar()
        self.mic_combo = ttk.Combobox(dev_frame, textvariable=self.mic_var,
                                      values=mic_labels, state="readonly", font=MONO)
        self.mic_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        if self._mic_auto:
            lbl = dev_label(self._mic_auto)
            self.mic_var.set(lbl if lbl in mic_labels else (mic_labels[0] if mic_labels else ""))
        elif mic_labels:
            self.mic_var.set(mic_labels[0])

        # ── desktop audio — автоматически, без выпадашки
        # статус показываем в логе при старте
        self._desktop_auto_info = self._desktop_auto or (
            self._desktop_devices[0] if self._desktop_devices else None
        )

        # кнопка REC
        self.rec_btn = tk.Button(
            self, text="⏺  НАЧАТЬ ЗАПИСЬ",
            font=("Consolas", 12, "bold"), pady=12,
            bg=ACC, fg="#0f0f0f", relief="flat",
            activebackground="#d4e83e", cursor="hand2",
            command=self._toggle_record,
        )
        self.rec_btn.grid(row=3, column=0, sticky="ew", padx=20, pady=14)

        # статус
        self.rec_status = tk.Label(self, text="● не записывается", font=MONO, bg=BG, fg=DIM)
        self.rec_status.grid(row=4, column=0)

        tk.Frame(self, bg="#252525", height=1).grid(row=5, column=0, sticky="ew", padx=20, pady=10)

        # ── загрузка существующей записи
        uf = tk.Frame(self, bg=CARD, pady=10, padx=14)
        uf.grid(row=6, column=0, sticky="ew", padx=20)
        uf.columnconfigure(0, weight=1)
        tk.Label(uf, text="ИЛИ ЗАГРУЗИТЬ СУЩЕСТВУЮЩУЮ ЗАПИСЬ", font=("Consolas", 9),
                 bg=CARD, fg=DIM).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.upload_file_var = tk.StringVar(value="файл не выбран")
        tk.Label(uf, textvariable=self.upload_file_var, font=MONO,
                 bg=CARD, fg=FG, anchor="w").grid(row=1, column=0, sticky="ew")
        self.upload_btn = tk.Button(
            uf, text="📂 Выбрать файл",
            font=MONO, bg="#2a2a2a", fg=ACC,
            relief="flat", cursor="hand2", activebackground="#333",
            command=self._pick_upload_file,
        )
        self.upload_btn.grid(row=1, column=1, padx=(8, 0))

        tk.Frame(self, bg="#252525", height=1).grid(row=7, column=0, sticky="ew", padx=20, pady=10)

        # транскрибация — запускается автоматически
        # только кнопка "стоп" если нужно прервать
        tr = tk.Frame(self, bg=BG)
        tr.grid(row=8, column=0, sticky="ew", padx=20)
        tr.columnconfigure(0, weight=1)
        self.tr_status = tk.Label(
            tr, text="", font=MONO,
            bg=BG, fg=DIM, anchor="w",
        )
        self.tr_status.grid(row=0, column=0, sticky="ew")
        self.stop_tr_btn = tk.Button(
            tr, text="⛔ стоп транскрипт", font=MONO,
            bg="#1e1e1e", fg="#ff5555", relief="flat",
            cursor="hand2", state="disabled",
            command=self._stop_transcribe,
        )
        self.stop_tr_btn.grid(row=0, column=1, padx=(6, 0))

        # прогресс
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("R.Horizontal.TProgressbar",
                        troughcolor="#1a1a1a", background=ACC, thickness=5)
        self.progress = ttk.Progressbar(self, style="R.Horizontal.TProgressbar",
                                        orient="horizontal", mode="determinate")
        self.progress.grid(row=9, column=0, sticky="ew", padx=20, pady=10)

        # лог — выделяемый, с прокруткой
        lf = tk.Frame(self, bg=CARD)
        lf.grid(row=10, column=0, sticky="nsew", padx=20, pady=(0, 20))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(1, weight=1)

        tk.Label(lf, text="LOG  (Ctrl+A — выделить всё, Ctrl+C — копировать)",
                 font=("Consolas", 8), bg=CARD, fg=DIM).grid(
                     row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(5, 0))

        self.log_text = tk.Text(
            lf, height=8, font=MONO, bg=CARD, fg="#888888",
            relief="flat", wrap="word", padx=8, pady=4,
            insertbackground=ACC, cursor="arrow",
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")
        self.log_text.bind("<Key>", self._log_key_filter)

        sb = tk.Scrollbar(lf, command=self.log_text.yview, bg=CARD, troughcolor=CARD)
        sb.grid(row=1, column=1, sticky="ns")
        self.log_text["yscrollcommand"] = sb.set

    def _log_key_filter(self, event):
        if event.state & 0x4:
            if event.keysym.lower() in ("c", "a", "insert"):
                return
        return "break"

    def _log(self, msg):
        def _do():
            self.log_text.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
            self.log_text.see("end")
        self.after(0, _do)

    def _log_device_status(self, msg, ok=True):
        """Логирует статус устройства сразу (до старта записи)."""
        color = "#5aff8a" if ok else "#ff9944"
        prefix = "✓" if ok else "⚠"
        self.after(100, lambda: self._log(f"{prefix} {msg}"))

    def _pick_folder(self):
        d = filedialog.askdirectory(title="Выберите папку для сохранения")
        if d:
            self.folder_var.set(d)

    def _toggle_record(self):
        if not self.recording:
            self._start_record()
        else:
            self._stop_record()

    def _start_record(self):
        if not os.path.isdir(self.folder_var.get()):
            messagebox.showerror("Ошибка", "Папка не найдена")
            return

        # микрофон — из выпадашки; desktop audio — автоматически
        mic_lbl = self.mic_var.get()
        self.recorder.mic_info     = self._mic_map.get(mic_lbl)
        self.recorder.desktop_info = self._desktop_auto_info

        if not self.recorder.mic_info:
            messagebox.showwarning("Внимание", "Микрофон не выбран")
        if not self.recorder.desktop_info:
            self._log("⚠ Звук ПК не найден — установи pyaudiowpatch")

        self.recorder.start(self.folder_var.get())
        self.out_dir = self.recorder.out_dir
        self.recording = True
        self.rec_btn.configure(text="⏹  ОСТАНОВИТЬ ЗАПИСЬ",
                               bg="#ff5555", fg="white", activebackground="#cc4444")
        self._tick_timer(0)

    def _tick_timer(self, s):
        if self.recording:
            m, sec = divmod(s, 60)
            self.rec_status.configure(text=f"● ИДЁТ ЗАПИСЬ  {m:02d}:{sec:02d}", fg="#ff4444")
            self.after(1000, self._tick_timer, s + 1)

    def _stop_record(self):
        self.recording = False
        self.rec_btn.configure(text="⏳ обработка…", state="disabled")
        self.rec_status.configure(text="● останавливаем…", fg="#888888")

        def worker():
            self.recorder.stop()
            try:
                mp4, mp3 = self.recorder.build_output()
                self.mp3_path = mp3
                self.after(0, self._record_done, mp4, mp3)
            except Exception as e:
                self.after(0, self._record_error, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _record_done(self, mp4, mp3):
        self._log(f"✓ MP4: {os.path.basename(mp4)}")
        self._log(f"✓ MP3: {os.path.basename(mp3)}")
        self.rec_btn.configure(text="⏺  НАЧАТЬ ЗАПИСЬ",
                               bg="#e8ff47", fg="#0f0f0f",
                               activebackground="#d4e83e", state="normal")
        self.rec_status.configure(text="● запись сохранена", fg="#5aff8a")
        # автоматически запускаем транскрибацию
        self._log("Запускаем транскрибацию автоматически…")
        self._start_transcribe()

    def _record_error(self, err):
        self._log(f"✗ Ошибка: {err}")
        self.rec_btn.configure(text="⏺  НАЧАТЬ ЗАПИСЬ",
                               bg="#e8ff47", fg="#0f0f0f",
                               activebackground="#d4e83e", state="normal")
        self.rec_status.configure(text="● ошибка", fg="#ff5555")

    def _start_transcribe(self):
        if not self.mp3_path or not os.path.exists(self.mp3_path):
            self._log("✗ MP3 не найден, транскрибация невозможна")
            return
        self.stop_transcribe.clear()
        self.stop_tr_btn.configure(state="normal")
        self.tr_status.configure(text="📝 транскрибируем…", fg="#e8ff47")
        self.progress["value"] = 0
        self._log("Транскрибация запущена…")

        def worker():
            def on_prog(done, total):
                self.progress["maximum"] = total
                self.progress["value"] = done
                self.update_idletasks()
            # ФИКС: передаём log_cb в transcribe чтобы видеть что происходит
            result = transcribe(self.mp3_path, self.out_dir,
                                progress_cb=on_prog,
                                stop_event=self.stop_transcribe,
                                log_cb=self._log)
            self.after(0, self._transcribe_done, result)

        threading.Thread(target=worker, daemon=True).start()

    def _stop_transcribe(self):
        self.stop_transcribe.set()
        self.tr_status.configure(text="⛔ остановлено", fg="#ff5555")
        self._log("⛔ транскрибация остановлена")

    def _transcribe_done(self, txt):
        self.stop_tr_btn.configure(state="disabled")
        if txt:
            self._log(f"✓ Транскрипт готов: {os.path.basename(txt)}")
            self.tr_status.configure(text="✓ транскрипт готов", fg="#5aff8a")
            self.progress["value"] = self.progress["maximum"]
            subprocess.Popen(["explorer", os.path.normpath(self.out_dir)])
        else:
            self._log("Транскрибация прервана или ошибка")
            self.tr_status.configure(text="", fg="#555555")

    def _pick_upload_file(self):
        path = filedialog.askopenfilename(
            title="Выберите аудио или видео файл",
            filetypes=[
                ("Все медиа", "*.mp4 *.mkv *.avi *.mov *.webm *.flv *.m4v "
                              "*.mp3 *.wav *.m4a *.ogg *.flac *.aac *.wma *.opus"),
                ("Видео", "*.mp4 *.mkv *.avi *.mov *.webm *.flv *.m4v"),
                ("Аудио", "*.mp3 *.wav *.m4a *.ogg *.flac *.aac *.wma *.opus"),
                ("Все файлы", "*.*"),
            ]
        )
        if not path:
            return
        if not os.path.isdir(self.folder_var.get()):
            messagebox.showerror("Ошибка", "Папка не найдена")
            return

        self.upload_btn.configure(state="disabled", text="⏳ обработка…")
        self.upload_file_var.set(os.path.basename(path))
        self._log(f"Загружаем файл: {os.path.basename(path)}")

        def worker():
            try:
                base_name = os.path.splitext(os.path.basename(path))[0]
                out_dir = unique_folder(self.folder_var.get(), base_name)
                mp3_path = os.path.join(out_dir, f"{base_name}.mp3")
                self.after(0, lambda: self._log("Конвертируем аудио в MP3…"))
                r = subprocess.run(
                    ["ffmpeg", "-y", "-i", path, "-q:a", "2", "-map", "a", mp3_path],
                    capture_output=True
                )
                if r.returncode != 0:
                    err = r.stderr.decode(errors="replace")[-400:]
                    raise RuntimeError(f"ffmpeg завершился с ошибкой:\n{err}")
                self.after(0, self._upload_ready, mp3_path, out_dir)
            except Exception as e:
                self.after(0, self._upload_error, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _upload_ready(self, mp3_path, out_dir):
        self.upload_btn.configure(state="normal", text="📂 Выбрать файл")
        self.mp3_path = mp3_path
        self.out_dir = out_dir
        self._log(f"✓ Аудио извлечено: {os.path.basename(mp3_path)}")
        self._log("Запускаем транскрибацию автоматически…")
        self._start_transcribe()

    def _upload_error(self, err):
        self.upload_btn.configure(state="normal", text="📂 Выбрать файл")
        self._log(f"✗ Ошибка: {err}")


if __name__ == "__main__":
    _set_taskbar_icon()   # до создания окна — иначе Windows не применит
    app = App()
    app.mainloop()
