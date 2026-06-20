# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List

def bootstrap():
    required_libs = ["pillow", "numpy", "scipy", "scikit-learn", "opencv-python"]
    try:
        import PIL, numpy, scipy, sklearn, cv2
    except ImportError:
        print("\n[信息] 正在同步依赖")
        for lib in required_libs:
            subprocess.run([sys.executable, "-m", "pip", "install", lib, "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"], capture_output=True)
        print("[完成] 环境就绪\n")
        os.execv(sys.executable, ['python'] + sys.argv)

if __name__ == "__main__" and "SKIP_BOOT" not in os.environ:
    os.environ["SKIP_BOOT"] = "1"
    bootstrap()

import numpy as np
from PIL import Image, ImageDraw

try:
    from scipy.spatial import KDTree
    from scipy.ndimage import label
    from sklearn.cluster import MiniBatchKMeans
    import cv2
    HAS_LIBS = True
except ImportError:
    KDTree = label = MiniBatchKMeans = cv2 = None
    HAS_LIBS = False

CONFIG_FILE = 'zyf_config.json'
TEMP_SH_REMOTE = '/data/local/tmp/zyf.sh'
LOCAL_SCAN_TMP = 'zyf.png'
LOCAL_DRAW_SH = 'zyf.sh'
PREVIEW_IMG_NAME = "预览图.png"
PREVIEW_VIDEO_NAME = "视频回放.mp4"
FINAL_RECORD_NAME = "录制回放.mp4"

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

class AppConfig:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.settings = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        defaults = {
            '分辨率倍数': 1.0,
            '帧连贯': 15,
            '速度控制': 100,
            '截图频率': 15,
            '清除噪点': 0,
            '快速画图模式': False,
            '极速模式': False,
            '视频帧率': 30,
            '生成mp4': True,
            '单色模式': False,
            'X偏移': 10,
            'Y偏移': 712,
            '画布宽度': 1060,
            '画布高度': 1060
        }
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for k, v in data.items():
                        if k in defaults: defaults[k] = v
            except (json.JSONDecodeError, IOError):
                pass
        return defaults

    def save_config(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def __getitem__(self, key: str) -> Any: return self.settings.get(key)
    def __setitem__(self, key: str, value: Any): self.settings[key] = value

class ColorUtils:
    @staticmethod
    def rgb_to_lab(img_array: np.ndarray) -> np.ndarray:
        rgb = img_array.astype(float) / 255.0
        mask = rgb > 0.04045
        rgb[mask] = ((rgb[mask] + 0.055) / 1.055) ** 2.4
        rgb[~mask] /= 12.92
        xyz_m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
        xyz = (rgb @ xyz_m.T) / np.array([0.95047, 1.00000, 1.08883])
        mask = xyz > 0.008856
        xyz[mask] = xyz[mask] ** (1/3)
        xyz[~mask] = 7.787 * xyz[~mask] + (16 / 116)
        return np.stack([116 * xyz[..., 1] - 16, 500 * (xyz[..., 0] - xyz[..., 1]), 200 * (xyz[..., 1] - xyz[..., 2])], axis=-1)

class AdbInterface:
    def __init__(self):
        self.adb = 'adb'
        if os.path.exists('adb.exe'): self.adb = os.path.abspath('adb.exe')
        self.device_id = None

    def check_device(self) -> bool:
        try:
            res = subprocess.run([self.adb, 'devices'], capture_output=True, text=True)
            lines = [l for l in res.stdout.splitlines() if 'device' in l and not l.startswith('List')]
            if not lines: return False
            self.device_id = lines[0].split()[0]; return True
        except (subprocess.SubprocessError, IndexError, FileNotFoundError):
            return False

    def shell(self, cmd: str):
        return subprocess.run([self.adb, '-s', self.device_id, 'shell', cmd], capture_output=True, text=True)

    def screencap(self, local_path: str):
        if not self.device_id: return
        self.shell('screencap -p /sdcard/s.png')
        subprocess.run([self.adb, '-s', self.device_id, 'pull', '/sdcard/s.png', local_path], capture_output=True)

class AdbPainter:
    def __init__(self):
        self.config = AppConfig(CONFIG_FILE)
        self.adb = AdbInterface()
        self.img_info = {"w": 0, "h": 0}

    def load_image(self, path: str):
        with Image.open(path) as img:
            self.img_info["w"], self.img_info["h"] = img.size

    def extract_layers(self, image_path: str) -> List[Dict[str, Any]]:
        logger.info("正在执行")
        try:
            img_src = Image.open(image_path).convert('RGB')
            target_limit = int(max(img_src.size) * self.config['分辨率倍数'])
            ratio = target_limit / max(img_src.size)
            draw_w, draw_h = int(img_src.width * ratio), int(img_src.height * ratio)
            img = img_src.resize((draw_w, draw_h), Image.Resampling.LANCZOS)

            layers = []
            if self.config['单色模式']:
                # 单色模式：直接使用灰度图，跳过昂贵的 LAB 转换
                gray = np.array(img.convert('L'))
                mask = (gray < 128)
                if not np.any(mask): return []

                color = (0, 0, 0) # 默认为黑色
                if self.config['快速画图模式'] and cv2 is not None:
                    mask_img = (mask.astype(np.uint8) * 255)
                    contours, _ = cv2.findContours(mask_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_TC89_KCOS)
                    all_strokes = [cnt.reshape(-1, 2) for cnt in contours if cv2.contourArea(cnt) >= self.config['清除噪点']]
                    if all_strokes:
                        layers.append({"color": color, "strokes": all_strokes, "size": (draw_w, draw_h)})
                elif label is not None:
                    labeled, _ = label(mask)
                    counts = np.bincount(labeled.ravel())
                    valid_labels = np.where(counts >= self.config['清除噪点'])[0]
                    valid_labels = valid_labels[valid_labels != 0]
                    mask = np.isin(labeled, valid_labels)
                    y_idx, x_idx = np.where(mask)
                    if len(y_idx) > 0:
                        pts = np.column_stack((x_idx, y_idx))
                        layers.append({"color": color, "points": pts, "size": (draw_w, draw_h)})
            elif MiniBatchKMeans is not None:
                pixels_rgb = np.array(img).reshape(-1, 3)
                pixels_lab = ColorUtils.rgb_to_lab(pixels_rgb)
                num_colors = 128
                samples = pixels_lab[np.random.choice(len(pixels_lab), min(len(pixels_lab), 80000), replace=False)]
                kmeans = MiniBatchKMeans(n_clusters=num_colors, batch_size=2048, random_state=42).fit(samples)
                labels = kmeans.predict(pixels_lab).reshape(draw_h, draw_w)
                for i in range(num_colors):
                    mask = (labels == i)
                    if self.config['快速画图模式'] and cv2 is not None:
                        mask_img = (mask.astype(np.uint8) * 255)
                        contours, _ = cv2.findContours(mask_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_TC89_KCOS)
                        all_strokes = []
                        for cnt in contours:
                            if cv2.contourArea(cnt) < self.config['清除噪点']: continue
                            all_strokes.append(cnt.reshape(-1, 2))
                        if not all_strokes: continue
                        color_vals = pixels_rgb[mask.flatten()]
                        color_arr = np.median(color_vals, axis=0).astype(int)
                        color = (int(color_arr[0]), int(color_arr[1]), int(color_arr[2]))
                        if sum(color) > 764: continue
                        layers.append({"color": color, "strokes": all_strokes, "size": (draw_w, draw_h)})
                    elif label is not None:
                        labeled, _ = label(mask)
                        counts = np.bincount(labeled.ravel())
                        valid_labels = np.where(counts >= self.config['清除噪点'])[0]
                        valid_labels = valid_labels[valid_labels != 0]
                        mask = np.isin(labeled, valid_labels)
                        y_idx, x_idx = np.where(mask)
                        if len(y_idx) == 0: continue
                        color_vals = pixels_rgb[mask.flatten()]
                        color_arr = np.median(color_vals, axis=0).astype(int)
                        color = (int(color_arr[0]), int(color_arr[1]), int(color_arr[2]))
                        if sum(color) > 764: continue
                        pts = np.column_stack((x_idx, y_idx))
                        layers.append({"color": color, "points": pts, "size": (draw_w, draw_h)})

            if not self.config['单色模式']:
                layers.sort(key=lambda x: sum(x['color']), reverse=True)
            return layers
        except (IOError, ValueError, RuntimeError, TypeError) as e:
            logger.error(f"失败: {e}"); return []

    @staticmethod
    def draw_progress(current, total, prefix='进度'):
        percent = (current / total) * 100
        bar_len = 40
        filled_len = int(bar_len * current // total)
        bar = '=' * filled_len + '>' + ' ' * (bar_len - filled_len - 1)
        sys.stdout.write(f"\r[{prefix}] [{bar}] {percent:.1f}%")
        sys.stdout.flush()

    def execute_draw(self, image_path: str):
        if not self.adb.check_device(): print("\n[错误] 未连接"); return
        self.auto_detect_canvas(apply=True)
        layers = self.extract_layers(image_path)
        if not layers: return
        ox, oy = self.config['X偏移'], self.config['Y偏移']
        cw, ch = self.config['画布宽度'], self.config['画布高度']
        w, h = layers[0]['size']; sx, sy = cw/w, ch/h
        rec_dir = Path("recordings") / time.strftime("%Y%m%d_%H%M%S")
        if self.config['生成mp4']: rec_dir.mkdir(parents=True, exist_ok=True)
        f_idx = 0
        for idx, layer in enumerate(layers):
            print(f"\n图层 {idx+1}/{len(layers)} | RGB: {layer['color']}")
            input("换色后按 [回车] 开始")
            cmds = []
            if 'strokes' in layer:
                for s in layer['strokes']: cmds.extend(self._build_motion_cmds([(p[0]*sx+ox, p[1]*sy+oy) for p in s]))
            elif KDTree is not None:
                pts = layer['points'].astype(float); tree = KDTree(pts); visited = np.zeros(len(pts), dtype=bool)
                while not np.all(visited):
                    unvisited = np.where(~visited)[0]; curr = unvisited[0]; stroke = []
                    while True:
                        stroke.append((pts[curr][0]*sx+ox, pts[curr][1]*sy+oy)); visited[curr] = True
                        d, i = tree.query(pts[curr], k=2)
                        target = -1
                        if np.isscalar(d):
                            if not visited[i] and d**2 <= self.config['帧连贯']**2: target = i
                        else:
                            for d_val, idx_val in zip(d, i):
                                if not visited[idx_val] and d_val**2 <= self.config['帧连贯']**2: target = idx_val; break
                        if target != -1: curr = target
                        else: break
                    cmds.extend(self._build_motion_cmds(stroke))
            batch, step = self.config['速度控制'], self.config['截图频率']
            for i in range(0, len(cmds), batch):
                with open(LOCAL_DRAW_SH, 'w') as f: f.write("#!/system/bin/sh\n" + "\n".join(cmds[i:i+batch]))
                subprocess.run([self.adb.adb, '-s', self.adb.device_id, 'push', LOCAL_DRAW_SH, TEMP_SH_REMOTE], capture_output=True)
                self.adb.shell(f"sh {TEMP_SH_REMOTE}")
                self.draw_progress(i, len(cmds), '绘画')
                if self.config['生成mp4'] and (i//batch)%step == 0:
                    self.adb.screencap(str(rec_dir / f"f_{f_idx:04d}.png")); f_idx += 1
        if self.config['生成mp4'] and f_idx > 2 and cv2 is not None:
            self._save_video(rec_dir, FINAL_RECORD_NAME)

    def _build_motion_cmds(self, path: list) -> List[str]:
        if not path: return []
        if len(path) == 1: return [f"input tap {path[0][0]:.0f} {path[0][1]:.0f}"]
        if not self.config['极速模式']: return [f"input swipe {path[0][0]:.0f} {path[0][1]:.0f} {path[-1][0]:.0f} {path[-1][1]:.0f} 20"]
        cmds = [f"input motionevent DOWN {path[0][0]:.0f} {path[0][1]:.0f}"]
        for i in range(1, len(path), 2): cmds.append(f"input motionevent MOVE {path[i][0]:.0f} {path[i][1]:.0f}")
        cmds.append(f"input motionevent UP {path[-1][0]:.0f} {path[-1][1]:.0f}"); return cmds

    def generate_preview(self, path):
        self.auto_detect_canvas(apply=True)
        layers = self.extract_layers(path)
        if not layers: return
        w, h = layers[0]['size']; canvas = Image.new('RGB', (w, h), (255, 255, 255)); draw = ImageDraw.Draw(canvas)
        video_out = None
        vw, vh = 0, 0
        if cv2 is not None and self.config['生成mp4']:
            r = 800 / max(w, h); vw, vh = int(w * r), int(h * r)
            video_out = cv2.VideoWriter(PREVIEW_VIDEO_NAME, cv2.VideoWriter_fourcc(*'mp4v'), self.config['视频帧率'], (vw, vh))
        total_pts, curr_pts = 0, 0
        for l in layers: total_pts += (len(l['strokes']) if 'strokes' in l else len(l['points']))
        record_step = max(50, total_pts // 300)
        for l in layers:
            if 'strokes' in l:
                for s in l['strokes']:
                    draw.line([tuple(p) for p in s], fill=l['color'], width=2)
                    curr_pts += 1
                    if video_out and curr_pts % 20 == 0:
                        f_cv = cv2.resize(cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR), (vw, vh))
                        video_out.write(f_cv)
            else:
                for p in l['points']:
                    draw.point(tuple(p), fill=l['color'])
                    curr_pts += 1
                    if video_out and curr_pts % record_step == 0:
                        f_cv = cv2.resize(cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR), (vw, vh))
                        video_out.write(f_cv)
            self.draw_progress(curr_pts, total_pts, '预览')
        if video_out: video_out.release()
        canvas.save(PREVIEW_IMG_NAME); os.startfile(PREVIEW_IMG_NAME)
        if video_out: os.startfile(PREVIEW_VIDEO_NAME)

    def _save_video(self, folder, output_name):
        if cv2 is None: return
        files = sorted(list(folder.glob("f_*.png")))
        if not files: return
        sample = cv2.imread(str(files[0])); h, w = sample.shape[:2]
        out = cv2.VideoWriter(output_name, cv2.VideoWriter_fourcc(*'mp4v'), self.config['视频帧率'], (w, h))
        for f in files: out.write(cv2.imread(str(f)))
        out.release(); os.startfile(output_name)

    def auto_detect_canvas(self, apply=True):
        if not self.adb.check_device(): print("\n[错误] 未连接"); return
        print(f"\n正在{'应用' if apply else '预览'}画布识别...")
        self.adb.screencap(LOCAL_SCAN_TMP)
        if cv2 is None: print("[错误] 未安装 OpenCV"); return

        img = cv2.imread(LOCAL_SCAN_TMP)
        if img is None: print("[错误] 无法读取截图"); return

        # 转换到 HSV 空间识别白色
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        # 更加严格的白色识别 (V值调高，S值调低)
        lower_white = np.array([0, 0, 240])
        upper_white = np.array([180, 15, 255])
        mask = cv2.inRange(hsv, lower_white, upper_white)

        # 闭运算填充空隙，但使用较小的核
        kernel = np.ones((3,3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours: print("[错误] 未发现白色区域"); return

        # 找面积最大的矩形
        max_area = 0
        best_rect = None
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area = w * h
            if area > max_area:
                max_area = area
                best_rect = (x, y, w, h)

        if best_rect:
            x, y, w, h = best_rect
            # 自动收缩 10 像素的内边距，避开可能的边缘 UI
            padding = 10
            x, y, w, h = x + padding, y + padding, w - 2*padding, h - 2*padding

            if apply:
                print(f"[发现] X:{x} Y:{y} W:{w} H:{h} (已应用内边距:{padding})")
                self.config['X偏移'], self.config['Y偏移'] = x, y
                self.config['画布宽度'], self.config['画布高度'] = w, h
                print("[完成] 坐标已临时应用（不保存至配置），重新选图前有效")
            else:
                print(f"[识别] 建议坐标 -> X:{x} Y:{y} W:{w} H:{h} (已含内边距)")
        else:
            print("[错误] 未能定位到有效画布")

        # 绘制并保存调试图
        debug_img = img.copy()
        if best_rect:
            x, y, w, h = best_rect
            cv2.rectangle(debug_img, (x, y), (x + w, y + h), (0, 0, 255), 8) # 红色粗框

        DEBUG_FILE = 'zyf_debug.png'
        cv2.imwrite(DEBUG_FILE, debug_img)
        print(f"[提示] 识别结果已保存至 {DEBUG_FILE}，请查看红框位置是否正确")
        try:
            os.startfile(DEBUG_FILE)
        except Exception:
            pass

    def main_menu(self, image_path):
        current_path = image_path
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print("zyf 开源绘画工具 | QQ: 3424025921 / 3824503929")
            print("-" * 50)
            print(f"图片尺寸: {self.img_info['w']} x {self.img_info['h']}")
            print("设置项              当前数值")
            print(f"1. 分辨率倍数       {self.config['分辨率倍数']}x")
            print(f"2. 矢量模式         {'[开启]' if self.config['快速画图模式'] else '[关闭]'}")
            print(f"3. 极速模式         {'[开启]' if self.config['极速模式'] else '[关闭]'}")
            print(f"4. 清除噪点         {self.config['清除噪点']}px")
            print(f"5. 帧连贯           {self.config['帧连贯']}")
            print(f"6. 速度控制         {self.config['速度控制']}")
            print(f"7. 截图频率         {self.config['截图频率']}")
            print(f"8. 视频帧率         {self.config['视频帧率']} fps")
            print(f"9. 生成视频         {'[开启]' if self.config['生成mp4'] else '[关闭]'}")
            print(f"10. 单色模式        {'[开启]' if self.config['单色模式'] else '[关闭]'}")
            print(f"11. 坐标设置        {self.config['X偏移']},{self.config['Y偏移']} | {self.config['画布宽度']}x{self.config['画布高度']}")
            print(" [A] 预览画布   [B] 模拟预览   [C] 开始绘画")
            print(" [D] 重新选图   [E] 保存设置   [F] 退出程序")
            c = input("\n指令: ").strip().upper()
            if c == 'B': self.generate_preview(current_path); input("\n\n预览完成")
            elif c == 'C': self.execute_draw(current_path); input("\n\n绘画结束")
            elif c == 'D':
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk(); root.withdraw()
                new_p = filedialog.askopenfilename(title="选择图片")
                if new_p: current_path = new_p; self.load_image(new_p)
            elif c == 'F': sys.exit()
            elif c == 'E': self.config.save_config(); print("已保存")
            elif c == '1':
                val = input("倍数: ")
                if val: self.config['分辨率倍数'] = float(val)
            elif choice_key := {'4':'清除噪点','5':'帧连贯','6':'速度控制','7':'截图频率','8':'视频帧率','11':'X偏移','12':'Y偏移','13':'画布宽度','14':'画布高度'}.get(c):
                val = input(f"新数值: ")
                if val: self.config[choice_key] = int(val)
            elif c == '2': self.config['快速画图模式'] = not self.config['快速画图模式']
            elif c == '3': self.config['极速模式'] = not self.config['极速模式']
            elif c == '9': self.config['生成mp4'] = not self.config['生成mp4']
            elif c == '10': self.config['单色模式'] = not self.config['单色模式']
            elif c == '11':
                print(f"当前: {self.config['X偏移']},{self.config['Y偏移']} {self.config['画布宽度']}x{self.config['画布高度']}")
                try:
                    self.config['X偏移'] = int(input("X偏移: "))
                    self.config['Y偏移'] = int(input("Y偏移: "))
                    self.config['画布宽度'] = int(input("画布宽度: "))
                    self.config['画布高度'] = int(input("画布高度: "))
                except ValueError: print("输入无效")
            elif c == 'A': self.auto_detect_canvas(apply=False); input("\n按回车返回")

if __name__ == "__main__":
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk(); root.withdraw()
    p = filedialog.askopenfilename(title="选择图片")
    if p:
        painter = AdbPainter()
        painter.load_image(p)
        painter.main_menu(p)
