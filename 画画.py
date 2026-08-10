# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List
import numpy as np
from PIL import Image, ImageDraw

def bootstrap():
    required_libs = ["pillow", "numpy", "opencv-python"]
    try:
        import PIL, numpy, cv2
    except ImportError:
        print("\n[信息] 正在同步依赖")
        for lib in required_libs:
            subprocess.run([sys.executable, "-m", "pip", "install", lib, "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"], capture_output=True)
        print("[完成] 环境就绪\n")
        os.execv(sys.executable, ['python'] + sys.argv)

if __name__ == "__main__" and "SKIP_BOOT" not in os.environ:
    os.environ["SKIP_BOOT"] = "1"
    bootstrap()

import cv2

CONFIG_FILE = 'zyf_config.json'
PREVIEW_IMG_NAME = "预览图.png"
PREVIEW_VIDEO_NAME = "视频回放.mp4"

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger(__name__)

class AppConfig:
    def __init__(self, config_path: str):
        self.config_path = Path(config_path)
        self.settings = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        defaults = {
            '视频帧率': 30,
            '生成视频': True
        }
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for k, v in data.items():
                        if k in defaults: defaults[k] = v
            except: pass
        return defaults

    def save_config(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
            print("设置已保存")
        except: pass

    def __getitem__(self, key: str) -> Any: return self.settings.get(key)
    def __setitem__(self, key: str, value: Any): self.settings[key] = value

class BitmapSimulator:
    def __init__(self):
        self.config = AppConfig(CONFIG_FILE)

    def extract_bitmap_layers(self, image_path: str):
        logger.info("提取数据中")
        img_src = Image.open(image_path).convert('RGB')
        w, h = img_src.size

        pixels_rgb = np.array(img_src)

        flat_pixels = pixels_rgb.reshape(-1, 3)
        pixel_ints = flat_pixels[:,0].astype(np.uint32) << 16 | flat_pixels[:,1].astype(np.uint32) << 8 | flat_pixels[:,2].astype(np.uint32)
        unique_ints, indices = np.unique(pixel_ints, return_inverse=True)

        u_r, u_g, u_b = (unique_ints >> 16) & 0xFF, (unique_ints >> 8) & 0xFF, unique_ints & 0xFF
        valid_mask = (u_r.astype(int) + u_g + u_b) <= 750

        y_coords, x_coords = np.indices((h, w))
        flat_y, flat_x = y_coords.ravel(), x_coords.ravel()

        layers = []
        for i, is_valid in enumerate(valid_mask):
            if not is_valid: continue
            color_mask = (indices == i)
            pts = np.column_stack((flat_x[color_mask], flat_y[color_mask]))
            layers.append({"color": (int(u_r[i]), int(u_g[i]), int(u_b[i])), "points": pts})
            if i % 500 == 0:
                sys.stdout.write(f"\r[分析] 提取进度: {i}/{len(unique_ints)} 色"); sys.stdout.flush()

        print(f"\n[完成] 分析完成 (分辨率: {w}x{h})")
        return layers, (w, h)

    def run_simulation(self, image_path: str):
        layers, size = self.extract_bitmap_layers(image_path)
        if not layers: return
        w, h = size

        canvas_np = np.full((h, w, 3), 255, dtype=np.uint8)

        video_out = None
        if self.config['生成视频']:
            vw, vh = (w, h) if max(w, h) <= 1080 else (int(w * 1080/max(w,h)), int(h * 1080/max(w,h)))
            video_out = cv2.VideoWriter(PREVIEW_VIDEO_NAME, cv2.VideoWriter_fourcc(*'mp4v'), self.config['视频帧率'], (vw, vh))

        total_layers = len(layers)
        print(f"[进度] 正在生成结果 (共 {total_layers} 个色)...")

        for idx, l in enumerate(layers):
            pts = l['points']
            canvas_np[pts[:, 1], pts[:, 0]] = l['color']

            if video_out and idx % max(1, total_layers // 100) == 0:
                frame = cv2.cvtColor(canvas_np, cv2.COLOR_RGB2BGR)
                if frame.shape[1] != vw or frame.shape[0] != vh:
                    frame = cv2.resize(frame, (vw, vh))
                video_out.write(frame)

            sys.stdout.write(f"\r生成进度: {((idx+1) / total_layers) * 100:.1f}%")
            sys.stdout.flush()

        if video_out: video_out.release()

        final_img = Image.fromarray(canvas_np)
        final_img.save(PREVIEW_IMG_NAME)
        print(f"\n\n[成功] 模拟完成！")
        print(f"图片: {PREVIEW_IMG_NAME} ({w}x{h})")

        os.startfile(PREVIEW_IMG_NAME)
        if video_out: os.startfile(PREVIEW_VIDEO_NAME)

    def main_menu(self, image_path: str):
        curr_p = image_path
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print("qq3424025921")
            print("-" * 50)
            print(f"1. 视频开关:     {'[开启]' if self.config['生成视频'] else '[关闭]'}")
            print(f"2. 视频帧率:     {self.config['视频帧率']} FPS")
            print("-" * 50)
            print(f"当前图片: {Path(curr_p).name}")
            print(" [A] 开始画图   [B] 重新选图   [C] 保存设置   [D] 退出程序")

            c = input("\n指令: ").strip().upper()
            if c == 'A':
                self.run_simulation(curr_p)
                input("\n按回车返回菜单")
            elif c == 'B':
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk(); root.withdraw()
                new_p = filedialog.askopenfilename(title="选择图片")
                if new_p: curr_p = new_p
            elif c == 'C':
                self.config.save_config()
                time.sleep(1)
            elif c == 'D':
                sys.exit()
            elif c == '1':
                self.config['生成视频'] = not self.config['生成视频']
            elif c == '2':
                val = input("请输入 FPS (1-60): ")
                if val.isdigit(): self.config['视频帧率'] = int(val)

if __name__ == "__main__":
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk(); root.withdraw()
    p = filedialog.askopenfilename(title="请选择图片")
    if p:
        sim = BitmapSimulator()
        sim.main_menu(p)
