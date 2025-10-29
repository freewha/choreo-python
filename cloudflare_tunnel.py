import os
import re
import socket
import subprocess
import time
import platform
import urllib.request
import ssl
import shutil
from pathlib import Path

# 全局变量
INSTALL_DIR = Path.home() / ".agsb"
ARGO_PID_FILE = INSTALL_DIR / "sbargopid.log"
LOG_FILE = INSTALL_DIR / "argo.log"
CUSTOM_DOMAIN_FILE = INSTALL_DIR / "custom_domain.txt"

# 默认配置
CF_TOKEN = os.getenv("CF_TOKEN", "eyJhIjoiZTllOWFmNzY0NTA2NjdhYTM1YmU0YjdkM2M2NGM0YTUiLCJ0IjoiZTYxMDFiNjUtMmY0Ny00MWUwLWJiNTQtNmQ4ZTNhMzM3MjFhIiwicyI6Ik5qTmtNR1V6TnpFdE5qYzFaUzAwTlRaaExUZzRZamd0WWpjMlptRTRNVFJpTnpneCJ9")
DOMAIN = os.getenv("DOMAIN", "sdfsfy4fff.8.1.f.f.0.d.0.0.1.0.a.2.ip6.arpa")

# 检查端口是否被占用
def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return False
        except socket.error:
            return True

# 检查进程是否运行
def is_process_running(pid_file, process_name=""):
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        if platform.system() == "Linux" and os.path.exists(f"/proc/{pid}"):
            if process_name:
                with open(f"/proc/{pid}/cmdline", "r") as f:
                    if process_name in f.read():
                        return True
            else:
                return True
        os.remove(pid_file)
        return False
    except (ValueError, FileNotFoundError):
        return False

# 下载 cloudflared
def download_binary(name, download_url, target_path):
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        req = urllib.request.Request(download_url, headers=headers)
        with urllib.request.urlopen(req, context=ctx) as response, open(target_path, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)
        os.chmod(target_path, 0o755)
        return True
    except Exception:
        return False

# 获取临时隧道域名
def get_tunnel_domain():
    retry_count = 0
    max_retries = 15
    while retry_count < max_retries:
        if LOG_FILE.exists():
            try:
                log_content = LOG_FILE.read_text()
                match = re.search(r'https://([a-zA-Z0-9.-]+\.trycloudflare\.com)', log_content)
                if match:
                    return match.group(1)
            except Exception:
                pass
        retry_count += 1
        time.sleep(3)
    return None

# 检查隧道安装和运行状态
def check_tunnel_status():
    cloudflared_path = INSTALL_DIR / "cloudflared"
    cf_running = is_process_running(ARGO_PID_FILE, "cloudflared")
    cf_installed = cloudflared_path.exists() and (INSTALL_DIR / "start_cf.sh").exists()
    domain = CUSTOM_DOMAIN_FILE.read_text().strip() if CUSTOM_DOMAIN_FILE.exists() else None
    return {
        "installed": cf_installed,
        "running": cf_running,
        "domain": domain
    }

# 创建 Cloudflare 隧道启动脚本
def create_tunnel_script(port, argo_token, custom_domain):
    if not INSTALL_DIR.exists():
        INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    cloudflared_path = INSTALL_DIR / "cloudflared"
    if not cloudflared_path.exists():
        system = platform.system().lower()
        machine = platform.machine().lower()
        arch = "amd64"
        if system == "linux":
            if "x86_64" in machine or "amd64" in machine: arch = "amd64"
            elif "aarch64" in machine or "arm64" in machine: arch = "arm64"
            elif "armv7" in machine: arch = "arm"
        else:
            raise Exception(f"不支持的系统: {system}")

        cf_url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
        if not download_binary("cloudflared", cf_url, cloudflared_path):
            cf_url_backup = f"https://github.91chi.fun/https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
            if not download_binary("cloudflared", cf_url_backup, cloudflared_path):
                raise Exception("cloudflared 下载失败")

    cf_cmd_base = f"./cloudflared tunnel --no-autoupdate"
    if argo_token:
        cf_cmd = f"{cf_cmd_base} run --token {argo_token}"
    else:
        cf_cmd = f"{cf_cmd_base} --url http://localhost:{port} --edge-ip-version auto --protocol http2"

    cf_start_script_path = INSTALL_DIR / "start_cf.sh"
    cf_start_content = f"""#!/bin/bash
cd {INSTALL_DIR.resolve()}
{cf_cmd} >> {LOG_FILE.name} 2>&1 &
echo $! > {ARGO_PID_FILE.name}
"""
    cf_start_script_path.write_text(cf_start_content)
    os.chmod(cf_start_script_path, 0o755)

# 启动隧道服务
def start_tunnel():
    status = check_tunnel_status()
    if status["running"]:
        return
    try:
        subprocess.run(str(INSTALL_DIR / "start_cf.sh"), shell=True, check=True)
        time.sleep(5)
    except subprocess.CalledProcessError as e:
        raise Exception(f"启动隧道失败: {e}")

# 初始化隧道
def init_tunnel(port, argo_token=None, custom_domain=None):
    argo_token = argo_token or CF_TOKEN
    custom_domain = custom_domain or DOMAIN

    if argo_token and not custom_domain:
        raise Exception("错误: 使用 Argo Token 时缺少自定义域名")

    status = check_tunnel_status()
    if status["installed"] and status["running"] and status["domain"]:
        return status["domain"]

    create_tunnel_script(port, argo_token, custom_domain)
    start_tunnel()

    final_domain = custom_domain
    if not argo_token and not custom_domain:
        final_domain = get_tunnel_domain()
        if not final_domain:
            raise Exception("无法获取临时隧道域名")

    CUSTOM_DOMAIN_FILE.write_text(final_domain)
    return final_domain
