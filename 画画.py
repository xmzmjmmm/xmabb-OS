# -*- coding: utf-8 -*-
import os, sys, json, time, subprocess, cv2
import numpy as np
from pathlib import Path
from PIL import Image

def bootstrap():
    libs = ["pillow", "numpy", "opencv-python"]
    try:
        import PIL, numpy, cv2
    except ImportError:
        print("\n[信息] 正在同步依赖")
        for lib in libs:
            subprocess.run([sys.executable, "-m", "pip", "install", lib, "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"], capture_output=True)
        print("[完成] 环境就绪\n")
        os.execv(sys.executable, ['python'] + sys.argv)

if __name__ == "__main__" and "SKIP_BOOT" not in os.environ:
    os.environ["SKIP_BOOT"] = "1"
    bootstrap()

class Config:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self._load()

    def _load(self):
        base = {'帧率': 30, '视频': True, '质量': '1080P'}
        if self.path.exists():
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    user = json.load(f)
                    base.update({k: v for k, v in user.items() if k in base})
            except: pass
        return base

    def save(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            print("设置已保存")
        except: pass

    def __getitem__(self, k): return self.data.get(k)
    def __setitem__(self, k, v): self.data[k] = v

class Painter:
    def __init__(self):
        self.cfg = Config('zyf_config.json')

    def _init_video(self, w, h):
        if not self.cfg['视频']: return None, 0, 0
        dims = {'4K': 2160, '2K': 1440, '1080P': 1080, '720P': 720}
        th = dims.get(self.cfg['质量'], 1080)
        vw, vh = (w, h) if h <= th else (int(w * th / h), th)
        out = cv2.VideoWriter("视频回放.mp4", cv2.VideoWriter_fourcc(*'mp4v'), self.cfg['帧率'], (vw, vh))
        return out, vw, vh

    def _write_frame(self, out, img, vw, vh):
        if not out: return
        f = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        if f.shape[1] != vw or f.shape[0] != vh: f = cv2.resize(f, (vw, vh))
        out.write(f)

    def run(self, path, mode='art'):
        start_t = time.time()
        src = Image.open(path).convert('RGB')
        w, h = src.size
        pix = np.array(src)
        canvas = np.full((h, w, 3), 255, dtype=np.uint8)
        video, vw, vh = self._init_video(w, h)

        if mode == 'art':
            print("提取数据")
            flat = pix.reshape(-1, 3)
            uints = flat[:,0].astype(np.uint32) << 16 | flat[:,1].astype(np.uint32) << 8 | flat[:,2].astype(np.uint32)
            unq, inv = np.unique(uints, return_inverse=True)

            lums = 0.299 * ((unq >> 16) & 0xFF) + 0.587 * ((unq >> 8) & 0xFF) + 0.114 * (unq & 0xFF)
            order = np.argsort(lums)

            flat_canvas = canvas.reshape(-1, 3)
            total = len(unq)
            for i, idx in enumerate(order):
                c_int = unq[idx]
                c_rgb = [(c_int >> 16) & 0xFF, (c_int >> 8) & 0xFF, c_int & 0xFF]
                flat_canvas[inv == idx] = c_rgb

                if video and i % max(1, total // 100) == 0:
                    self._write_frame(video, canvas, vw, vh)
                if i % 100 == 0:
                    sys.stdout.write(f"\r进度: {(i+1)/total*100:.1f}%"); sys.stdout.flush()
        else:
            print("提取数据")
            step = max(1, h // 60)
            for y in range(0, h, step):
                ny = min(y + step, h)
                canvas[y:ny, :] = pix[y:ny, :]
                if video: self._write_frame(video, canvas, vw, vh)
                sys.stdout.write(f"\r进度: {ny/h*100:.1f}%"); sys.stdout.flush()

        if video:
            for _ in range(self.cfg['帧率']): self._write_frame(video, canvas, vw, vh)
            video.release()
        Image.fromarray(canvas).save("预览图.png")
        print(f"\n\n分析完成，耗时: {time.time()-start_t:.2f} 秒")
        os.startfile("预览图.png")
        if video: os.startfile("视频回放.mp4")

    def menu(self, path):
        curr = path
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"qq3424025921\n{'-'*50}")
            print(f"1. 视频开关: {'[开启]' if self.cfg['视频'] else '[关闭]'}")
            print(f"2. 视频帧率: {self.cfg['帧率']} FPS")
            print(f"3. 视频质量: {self.cfg['质量']} (4K/2K/1080P/720P)")
            print(f"{'-'*50}\n当前图片: {Path(curr).name}")
            print(" [A] 直接渲染一   [R] 直接渲染二\n [B] 重新选图              [C] 保存设置   [D] 退出程序")
            c = input("\n指令: ").strip().upper()
            if c == 'A': self.run(curr, 'art'); input("\n回车返回")
            elif c == 'R': self.run(curr, 'fast'); input("\n回车返回")
            elif c == 'B':
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk(); root.withdraw()
                p = filedialog.askopenfilename()
                if p: curr = p
            elif c == 'C': self.cfg.save(); time.sleep(1)
            elif c == 'D': break
            elif c == '1': self.cfg['视频'] = not self.cfg['视频']
            elif c == '2':
                v = input("FPS (1-60): ")
                if v.isdigit(): self.cfg['帧率'] = int(v)
            elif c == '3':
                v = input("质量 (4K/2K/1080P/720P): ").upper()
                if v in ['4K', '2K', '1080P', '720P']: self.cfg['质量'] = v

if __name__ == "__main__":
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk(); root.withdraw()
    p = filedialog.askopenfilename(title="请选择图片")
    if p: Painter().menu(p)
