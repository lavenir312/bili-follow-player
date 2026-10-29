# -*- coding: utf-8 -*-
"""BiliPlayer 启动器。

双击快捷方式时跑这个：先看本地服务在不在，不在就拉起来，然后开一个应用窗口。

之所以不写成 bat：这台机器上的 Python 是随工具装进去的，
不在系统 PATH 里，bat 里 `where pythonw` 一定失败。
所以快捷方式直接指向 pythonw.exe，由这个脚本负责剩下的事。
"""
import ctypes
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "data", "launch.log")


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def popup(text):
    """pythonw 下没有控制台，出错只能弹窗告诉人。别的系统就打到终端。"""
    try:
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, text, "BiliPlayer", 0x10)
        else:
            print(text)
    except Exception:
        pass


def port_alive(port, timeout=0.6):
    # 用裸 socket，不走系统代理
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def read_port():
    try:
        import json
        with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
            return int(json.load(f).get("port", 8791))
    except Exception:
        return 8791


def claim_single_instance(port=8791):
    """双击两下（或者点快了）时只让一个往下走。

    port_alive 那种检查有缝隙：两个启动器几乎同时跑，都还没等到端口起来，
    结果各自拉起一个 server，最后两个进程抢同一个端口 —— 请求会随机落到
    旧的那个实例上，改完代码看着像没生效。用命名互斥把这段锁住。
    名字里带端口，不同目录不同端口的两份程序互不干扰。
    """
    if os.name != "nt":
        return True
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW(None, False, "Local\\BiliPlayerLauncher_%d" % port)
        # 句柄不关，进程活着期间一直占着；退出时系统自动释放
        return k32.GetLastError() != 183        # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def find_browser():
    env = os.environ
    if sys.platform == "darwin":
        cands = [
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif os.name == "nt":
        cands = [
            os.path.join(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                         r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(env.get("ProgramFiles", r"C:\Program Files"),
                         r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(env.get("ProgramFiles", r"C:\Program Files"),
                         r"Google\Chrome\Application\chrome.exe"),
            os.path.join(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                         r"Google\Chrome\Application\chrome.exe"),
            os.path.join(env.get("LOCALAPPDATA", ""),
                         r"Google\Chrome\Application\chrome.exe"),
        ]
    else:
        import shutil
        cands = []
        for name in ("microsoft-edge", "google-chrome", "chromium", "brave-browser"):
            p = shutil.which(name)
            if p:
                cands.append(p)
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return None


def main():
    port = read_port()
    if not claim_single_instance(port):
        log("另一个启动器正在跑，这次跳过")
        return
    url = f"http://127.0.0.1:{port}"

    # 已经在跑就别再起一个，直接开窗口
    if not port_alive(port):
        log("服务未运行，启动中")
        exe = sys.executable
        # 万一被 python.exe（带黑框）调用，也让它不起窗口
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            subprocess.Popen(
                [exe, os.path.join(HERE, "server.py")],
                cwd=HERE, creationflags=flags,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception as e:
            log("启动失败: " + repr(e))
            popup(f"启动失败：\n{e}")
            return

        # 等端口就绪，最多 25 秒
        ok = False
        for _ in range(100):
            time.sleep(0.25)
            if port_alive(port):
                ok = True
                break
        if not ok:
            log("等待端口超时")
            popup("服务没能启动起来。\n\n"
                  "看看这个文件里写了什么：\n" + LOG)
            return
        log("服务已就绪")
    else:
        log("服务已在运行")

    br = find_browser()
    profile = os.path.join(HERE, "window_profile")
    if br:
        args = [br, f"--app={url}", "--window-size=1400,880",
                f"--user-data-dir={profile}"]
        try:
            subprocess.Popen(args, close_fds=True)
            log(f"已打开窗口 {br}")
            return
        except Exception as e:
            log("开窗口失败: " + repr(e))

    # 没有 Edge/Chrome 就用默认浏览器
    try:
        import webbrowser
        webbrowser.open(url)
        log("用默认浏览器打开")
    except Exception as e:
        log("打开浏览器失败: " + repr(e))
        popup("打不开浏览器，手动访问：" + url)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log("崩溃: " + repr(e))
        log(traceback.format_exc())
        popup("启动器出错了：\n" + repr(e) + "\n\n详情：\n" + LOG)
