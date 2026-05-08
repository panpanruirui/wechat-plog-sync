import json, subprocess, shutil, os, sys, time, logging, argparse, re, struct
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_DIR = Path(__file__).parent
CONFIG_FILE = PROJECT_DIR / "config.json"
STATE_FILE = PROJECT_DIR / "state.json"
LOG_FILE = PROJECT_DIR / "plog_sync.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)

MSG_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] (.+?): (.*)$', re.DOTALL)
LOCAL_ID_RE = re.compile(r'local_id=(\d+)')
IMG_PATH_RE = re.compile(r'\[图片\]\s*(\S+)')
FILE_PATH_RE = re.compile(r'\[文件\]\s*(.+?)(?:\n\s+(\S+))?$')

IMAGE_SIGS = {
    b'\xff\xd8\xff': 'jpg',
    b'\x89PNG': 'png',
    b'GIF8': 'gif',
    b'RIFF': 'webp',
    b'BM': 'bmp',
}

def read_json(path):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}

def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def sig_key(dat_byte):
    for sig, ext in IMAGE_SIGS.items():
        key = dat_byte ^ sig[0]
        yield key, ext, sig

def decode_dat(src: Path, dest_dir: Path) -> Optional[Path]:
    try:
        data = src.read_bytes()
    except Exception:
        return None
    for key, ext, sig in sig_key(data[0]):
        decoded = bytes(b ^ key for b in data)
        if decoded[1:len(sig)] == sig[1:]:
            out = dest_dir / f"{src.stem}.{ext}"
            if out.exists():
                return out
            out.write_bytes(decoded)
            return out
    return None

def _wxgf_to_jpg(data: bytes, dest_dir: Path, stem: str) -> Optional[Path]:
    try:
        import av
    except ImportError:
        return None
    vps_sig = b'\x00\x00\x00\x01\x40\x01'
    hevc_start = data.find(vps_sig)
    if hevc_start < 0:
        hevc_start = data.find(b'\x00\x00\x00\x01\x42\x01')
    if hevc_start < 0:
        return None
    h265_path = dest_dir / f"{stem}.h265"
    h265_path.write_bytes(data[hevc_start:])
    try:
        container = av.open(str(h265_path), format='hevc')
        jpg_path = dest_dir / f"{stem}.jpg"
        for frame in container.decode(video=0):
            img = frame.to_image()
            img.save(str(jpg_path), "JPEG", quality=90)
            container.close()
            return jpg_path
        container.close()
    except Exception as e:
        logging.warning(f"HEVC→JPG 转换失败 ({stem}): {e}")
    finally:
        if h265_path.exists():
            h265_path.unlink()
    return None


def decode_v2_dat(src: Path, dest_dir: Path, aes_key: str, xor_key: int = 0x88) -> Optional[Path]:
    try:
        data = src.read_bytes()
    except Exception:
        return None
    if data[:6] != b'\x07\x08V2\x08\x07':
        return None
    try:
        from Crypto.Cipher import AES as _AES
        from Crypto.Util import Padding
    except ImportError:
        return None
    aes_size, xor_size = struct.unpack_from('<II', data, 6)
    aligned = aes_size if aes_size % 16 == 0 else (aes_size + 15) // 16 * 16
    if aes_size % 16 == 0:
        aligned += 16
    off = 15
    aes_data = data[off:off + aligned]
    cipher = _AES.new(aes_key.encode('ascii')[:16], _AES.MODE_ECB)
    dec_aes = Padding.unpad(cipher.decrypt(aes_data), _AES.block_size)
    off += aligned
    raw_data = data[off:len(data) - xor_size]
    xor_data = data[len(data) - xor_size:]
    dec_xor = bytes(b ^ xor_key for b in xor_data)
    result = dec_aes + raw_data + dec_xor
    if result[:3] == b'\xff\xd8\xff':
        ext = '.jpg'
    elif result[:4] == b'\x89PNG':
        ext = '.png'
    elif result[:4] == b'GIF8':
        ext = '.gif'
    elif result[:4] == b'RIFF' and result[8:12] == b'WEBP':
        ext = '.webp'
    elif result[:4] == b'wxgf':
        ext = '.hevc'
    else:
        ext = '.bin'
    out = dest_dir / f"{src.stem}{ext}"
    jpg_path = dest_dir / f"{src.stem}.jpg"
    if out.exists():
        if ext == '.hevc' and not jpg_path.exists():
            result = out.read_bytes()
            jpg = _wxgf_to_jpg(result, dest_dir, src.stem)
            if jpg:
                return jpg
        return out
    out.write_bytes(result)
    if ext == '.hevc':
        jpg = _wxgf_to_jpg(result, dest_dir, src.stem)
        if jpg:
            out.unlink(missing_ok=True)
            return jpg
        return out
    return out


def resolve_image(src_str: str, dest_dir: Path, msg_time: str = "", aes_key: str = "", xor_key: int = 0x88) -> Optional[str]:
    src = Path(src_str)
    if not src.exists():
        return None
    decoded_path = None
    is_v2 = False
    try:
        head = src.read_bytes()[:6]
        is_v2 = head == b'\x07\x08V2\x08\x07'
    except Exception:
        pass
    if is_v2:
        if aes_key:
            decoded_path = decode_v2_dat(src, dest_dir, aes_key, xor_key)
    else:
        if src.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
            decoded_path = dest_dir / src.name
            if not decoded_path.exists():
                shutil.copy2(src, decoded_path)
        elif src.suffix.lower() == '.dat':
            decoded_path = decode_dat(src, dest_dir)
    if not decoded_path or not decoded_path.exists():
        return None
    name = decoded_path.name
    if msg_time:
        dt_part = msg_time.replace(' ', '-').replace(':', '-') + "-00"
        new_name = f"{dt_part}{decoded_path.suffix}"
        new_path = decoded_path.with_name(new_name)
        if decoded_path != new_path:
            if new_path.exists():
                new_path.unlink()
            decoded_path.rename(new_path)
        name = new_name
        if new_path.suffix == '.hevc':
            result = new_path.read_bytes()
            jpg = _wxgf_to_jpg(result, new_path.parent, new_path.stem)
            if jpg:
                new_path.unlink(missing_ok=True)
                name = jpg.name
    return name


class PlogSync:
    def __init__(self):
        self.config = read_json(CONFIG_FILE)
        self.state = read_json(STATE_FILE)
        self.obsidian_path = Path(self.config["obsidian_path"])
        self.attachment_dir = Path(self.config["attachment_dir"])
        self.group_name = self.config["group_name"]
        self.title_suffix = self.config.get("title_suffix", "自动爬取")
        self.obsidian_path.mkdir(parents=True, exist_ok=True)
        self.attachment_dir.mkdir(parents=True, exist_ok=True)

    def save_state(self):
        write_json(STATE_FILE, self.state)

    def check_wechat_running(self):
        r = subprocess.run(
            'tasklist /FI "IMAGENAME eq Weixin.exe" /NH',
            capture_output=True, text=True, shell=True, timeout=10,
        )
        return "Weixin.exe" in r.stdout

    def run_wechat_cli(self, args: list) -> tuple[Optional[str], Optional[str]]:
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            r = subprocess.run(args, capture_output=True, text=False, timeout=120, env=env)
            stdout = r.stdout.decode('utf-8', errors='replace') if r.stdout else ''
            stderr = r.stderr.decode('utf-8', errors='replace') if r.stderr else ''
            return stdout.strip() or None, stderr.strip() or None
        except FileNotFoundError:
            return None, "未找到 wechat-cli，请先安装: pip install -e ./wechat-cli-src"
        except subprocess.TimeoutExpired:
            return None, "wechat-cli 执行超时"

    def get_raw_messages(self, start_date: str, end_date: str = "") -> Optional[list[str]]:
        cmd = ["wechat-cli", "history", self.group_name, "--start-time", start_date, "--format", "json", "--media"]
        if end_date:
            cmd.extend(["--end-time", end_date])
        raw, err = self.run_wechat_cli(cmd)
        if not raw:
            if err:
                logging.error(f"wechat-cli: {err}")
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logging.error("JSON 解析失败")
            return None
        if isinstance(data, dict):
            msgs = data.get("messages") or []
            return msgs if isinstance(msgs, list) else None
        return None

    def parse_msg(self, line: str) -> Optional[dict]:
        m = MSG_RE.match(line)
        if not m:
            return None
        time_str = m.group(1)
        sender = m.group(2)
        content = m.group(3)

        if content.startswith("[表情]"):
            return {"time": time_str, "sender": sender, "type": "sticker", "content": content}
        if content.startswith("[图片]"):
            path_m = IMG_PATH_RE.match(content)
            img_path = path_m.group(1) if path_m else ""
            return {"time": time_str, "sender": sender, "type": "image", "content": img_path, "raw": content}
        if content.startswith("[语音]"):
            return {"time": time_str, "sender": sender, "type": "voice", "content": content}
        if content.startswith("[视频]"):
            return {"time": time_str, "sender": sender, "type": "video", "content": content}
        if content.startswith("[通话]"):
            return {"time": time_str, "sender": sender, "type": "call", "content": content}
        if content.startswith("[位置]"):
            return {"time": time_str, "sender": sender, "type": "location", "content": content}
        if content.startswith("[文件]"):
            file_m = FILE_PATH_RE.match(content)
            file_info = file_m.group(1) if file_m else content
            return {"time": time_str, "sender": sender, "type": "file", "content": file_info}
        if content.startswith("[链接") or content.startswith("[链接/文件") or content.startswith("[小程序"):
            return {"time": time_str, "sender": sender, "type": "link", "content": content}
        if content.startswith("["):
            return {"time": time_str, "sender": sender, "type": "other", "content": content}
        return {"time": time_str, "sender": sender, "type": "text", "content": content}

    def format_entry(self, msg: dict) -> str:
        time_str = msg["time"][-5:]
        sender = msg["sender"]
        t = msg["type"]
        content = msg["content"]

        if t == "text":
            body = f"<div style=\"white-space: pre-wrap;\">\n{content}\n</div>" if "\n" in content else content
        elif t == "image":
            fn = resolve_image(content, self.attachment_dir, msg.get("time"), self.config.get("image_aes_key", ""), self.config.get("image_xor_key", 0x88)) if content else None
            body = f"![[附件/{fn}]]" if fn else "[图片]"
        elif t == "voice":
            body = "🎤 [语音消息]"
        elif t == "video":
            body = "[视频消息]"
        elif t == "sticker":
            body = "[表情]"
        elif t == "link":
            body = content
        elif t == "file":
            body = content
        elif t == "call":
            body = content
        elif t == "location":
            body = content
        else:
            body = content or ""

        return f"###### {time_str} - {sender}\n{body}\n\n---"

    def make_daily_path(self, date_str: str) -> Path:
        return self.obsidian_path / f"{date_str}{self.title_suffix}.md"

    def atomic_write(self, path: Path, content: str):
        content = content.rstrip("\n") + "\n"
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)

    def append_note(self, path: Path, content: str):
        content = content.rstrip("\n") + "\n"
        if path.exists():
            existing = path.read_text(encoding="utf-8").rstrip("\n") + "\n\n"
            content = existing + content
        self.atomic_write(path, content)

    def run_check(self):
        logging.info("=" * 40)
        logging.info("系统诊断")
        logging.info("=" * 40)
        ok = True

        out, err = self.run_wechat_cli(["wechat-cli", "--help"])
        if not out or "wechat-cli" not in out.lower():
            logging.error(f"[FAIL] wechat-cli 不可用")
            ok = False
        else:
            logging.info("[OK] wechat-cli 已安装")

        wechat_config = Path.home() / ".wechat-cli"
        if wechat_config.exists():
            logging.info(f"[OK] wechat-cli 已初始化 ({wechat_config})")
        else:
            logging.warning("[WARN] wechat-cli 未初始化 → 微信登录后运行: wechat-cli init")

        if self.check_wechat_running():
            logging.info("[OK] 微信运行中")
        else:
            logging.error("[FAIL] 微信未运行")
            ok = False

        if self.obsidian_path.exists():
            logging.info(f"[OK] Obsidian 目录: {self.obsidian_path}")
        else:
            logging.error(f"[FAIL] 目录不存在: {self.obsidian_path}")
            ok = False
        if self.attachment_dir.exists():
            logging.info(f"[OK] 附件目录: {self.attachment_dir}")
        else:
            self.attachment_dir.mkdir(parents=True, exist_ok=True)
            logging.info(f"[OK] 已创建附件目录: {self.attachment_dir}")

        raw = self.get_raw_messages(datetime.now().strftime("%Y-%m-%d"))
        if raw is None:
            logging.error("[FAIL] 无法查询消息，请检查群名称是否准确")
            ok = False
        else:
            logging.info(f"[OK] 群 \"{self.group_name}\" 可正常查询 ({len(raw)} 条)")

        aes_key = self.config.get("image_aes_key", "")
        if aes_key:
            logging.info(f"[OK] V2 AES 密钥已配置 ({aes_key[:8]}...)")
        else:
            logging.warning("[WARN] V2 AES 密钥未配置 → 需运行 extract_aes_key.py")

        if ok:
            logging.info("[OK] 全部正常")
        else:
            logging.error("[FAIL] 存在错误，请修复后重试")
        return ok

    def run_dry_run(self):
        today = datetime.now().strftime("%Y-%m-%d")
        since = self.state.get("last_sync_time", "")
        logging.info(f"群组: {self.group_name}")
        logging.info(f"日期: {today}")
        logging.info(f"上次同步: {since or '无'}")

        raw = self.get_raw_messages(today)
        if not raw:
            logging.info("暂无消息")
            return

        new_count = 0
        for line in raw:
            msg = self.parse_msg(line)
            if not msg:
                continue
            if since and f"{msg['time']}:00" <= since:
                continue
            new_count += 1
            label = {"text": "", "image": "[IMG]", "voice": "[VOICE]", "video": "[VIDEO]", "sticker": "[STICKER]", "link": "[LINK]", "file": "[FILE]"}.get(msg["type"], "[?]")
            preview = (msg["content"][:60] if isinstance(msg["content"], str) else str(msg["content"]))[:60]
            logging.info(f"  {msg['time'][-5:]} {label} {msg['sender']}: {preview}")

        daily_path = self.make_daily_path(today)
        logging.info(f"\n当前日记: {daily_path}")
        logging.info(f"新消息: {new_count} 条")
        if daily_path.exists():
            existing = daily_path.read_text(encoding="utf-8")
            logging.info(f"已有内容: {len(existing)} 字符")

    def run_full(self):
        today = datetime.now().strftime("%Y-%m-%d")
        logging.info(f"覆盖模式: {today}")
        raw = self.get_raw_messages(today)
        if not raw:
            logging.info("暂无消息")
            return
        entries = []
        for line in raw:
            msg = self.parse_msg(line)
            if msg:
                f = self.format_entry(msg)
                if f:
                    entries.append(f)
        daily_path = self.make_daily_path(today)
        header = f"# {today}{self.title_suffix}\n\n"
        self.atomic_write(daily_path, header + "\n".join(entries))
        logging.info(f"已覆盖: {daily_path.name} ({len(entries)} 条)")

    def run_backfill(self, start_date: str, end_date: str):
        logging.info(f"补历史: {start_date} ~ {end_date}")
        sd = datetime.strptime(start_date, "%Y-%m-%d")
        ed = datetime.strptime(end_date, "%Y-%m-%d")
        current = sd
        while current <= ed:
            ds = current.strftime("%Y-%m-%d")
            logging.info(f"  {ds}...")
            raw = self.get_raw_messages(ds, ds)
            if raw:
                entries = []
                for line in raw:
                    msg = self.parse_msg(line)
                    if msg:
                        f = self.format_entry(msg)
                        if f:
                            entries.append(f)
                if entries:
                    daily_path = self.make_daily_path(ds)
                    header = f"# {ds}{self.title_suffix}\n\n"
                    self.atomic_write(daily_path, header + "\n".join(entries))
                    logging.info(f"    {daily_path.name} ({len(entries)} 条)")
                else:
                    logging.info(f"    无有效消息")
            else:
                logging.info(f"    无消息")
            current += timedelta(days=1)

    def run_sync(self):
        if not self.check_wechat_running():
            logging.error("微信未运行，退出")
            sys.exit(1)

        today = datetime.now().strftime("%Y-%m-%d")
        since = self.state.get("last_sync_time", "")

        raw = self.get_raw_messages(today)
        if raw is None:
            logging.error("消息查询失败")
            return
        if not raw:
            logging.info("无新消息")
            self.state["last_sync_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.state["last_date"] = today
            self.save_state()
            return

        entries = []
        for line in raw:
            msg = self.parse_msg(line)
            if not msg:
                continue
            if since:
                msg_dt = f"{msg['time']}:00"
                if msg_dt <= since:
                    continue
            f = self.format_entry(msg)
            if f:
                entries.append(f)

        if not entries:
            logging.info("无新增消息")
            self.state["last_sync_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.state["last_date"] = today
            self.save_state()
            return

        daily_path = self.make_daily_path(today)
        if since:
            self.append_note(daily_path, "\n".join(entries))
            logging.info(f"已追加: {daily_path.name} (+{len(entries)} 条)")
        else:
            header = f"# {today}{self.title_suffix}\n\n"
            self.atomic_write(daily_path, header + "\n".join(entries))
            logging.info(f"已创建: {daily_path.name} ({len(entries)} 条)")

        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.state["last_sync_time"] = now_iso
        self.state["last_date"] = today
        msg_ids = [m.get("time") for m in (self.parse_msg(l) for l in raw[-20:]) if m]
        self.state["last_times"] = msg_ids[-20:]
        self.save_state()
        logging.info(f"完成，同步时间: {now_iso}")

    def run_fix_hevc(self):
        logging.info("扫描遗留 .hevc 文件，尝试转 JPG ...")
        hevc_files = list(self.attachment_dir.glob("*.hevc"))
        if not hevc_files:
            logging.info("没有需要修复的 .hevc 文件")
            return
        fixed = 0
        for p in hevc_files:
            jpg = self.attachment_dir / f"{p.stem}.jpg"
            if jpg.exists():
                continue
            logging.info(f"  转换: {p.name}")
            try:
                data = p.read_bytes()
                result = _wxgf_to_jpg(data, self.attachment_dir, p.stem)
                if result:
                    p.unlink(missing_ok=True)
                    fixed += 1
                    logging.info(f"    ✅ {result.name}")
                else:
                    logging.warning(f"    ❌ 转换失败: {p.name}")
            except Exception as e:
                logging.warning(f"    ❌ 异常: {p.name} - {e}")
        logging.info(f"完成: {fixed}/{len(hevc_files)} 个已修复")

    def install_task(self):
        script = Path(__file__).resolve()
        python = sys.executable
        task_name = "PlogSync"
        st = self.config.get("schedule_time", "23:00")

        subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True, timeout=10)
        cmd = [
            "schtasks", "/Create", "/SC", "DAILY",
            "/TN", task_name,
            "/TR", f'"{python}" "{script}"',
            "/ST", st, "/F",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            logging.info(f"[OK] 定时任务已创建: 每天 {st} 执行")
            logging.info(f"   任务名: {task_name}")
        else:
            logging.error(f"[FAIL] 创建失败: {r.stderr.strip() or r.stdout.strip()}")


def main():
    parser = argparse.ArgumentParser(description="同步微信日常plog群消息到Obsidian")
    parser.add_argument("--dry-run", action="store_true", help="预览")
    parser.add_argument("--full", action="store_true", help="覆盖今天日记")
    parser.add_argument("--backfill", nargs=2, metavar=("START", "END"), help="补历史: --backfill 2026-04-01 2026-04-30")
    parser.add_argument("--check", action="store_true", help="系统诊断")
    parser.add_argument("--fix-hevc", action="store_true", help="修复遗留 .hevc 文件")
    parser.add_argument("--install", action="store_true", help="创建定时任务")
    args = parser.parse_args()

    sync = PlogSync()

    if args.check:
        sync.run_check()
    elif args.dry_run:
        sync.run_dry_run()
    elif args.full:
        sync.run_full()
    elif args.backfill:
        sync.run_backfill(args.backfill[0], args.backfill[1])
    elif args.fix_hevc:
        sync.run_fix_hevc()
    elif args.install:
        sync.install_task()
    else:
        sync.run_sync()


if __name__ == "__main__":
    main()
