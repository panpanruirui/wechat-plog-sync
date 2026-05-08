"""从微信进程内存提取图片 AES 密钥 (V2 .dat 格式)

用法:
  1. 打开微信，点开 2-3 张图片看大图
  2. 立即运行: python extract_aes_key.py
  3. 密钥自动保存到 config.json

V2 文件结构:
  [6B signature: 07 08 V2 08 07] [4B aes_size LE] [4B xor_size LE] [1B padding]
  [AES-128-ECB encrypted] [raw_data] [XOR encrypted tail]
"""
import json, os, sys, re, struct, glob, time
import ctypes
from ctypes import wintypes
from pathlib import Path
from Crypto.Cipher import AES
from Crypto.Util import Padding

PROJECT_DIR = Path(__file__).parent
CONFIG_FILE = PROJECT_DIR / "config.json"

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
PAGE_READWRITE = 0x04
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE_READWRITE = 0x40
PAGE_EXECUTE_WRITECOPY = 0x80

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]

kernel32 = ctypes.windll.kernel32
RE_KEY32 = re.compile(rb'(?<![a-zA-Z0-9])[a-zA-Z0-9]{32}(?![a-zA-Z0-9])')
RE_KEY16 = re.compile(rb'(?<![a-zA-Z0-9])[a-zA-Z0-9]{16}(?![a-zA-Z0-9])')


def find_attach_dir(config_path=None):
    search_paths = []
    if config_path:
        try:
            cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
            wp = cfg.get("wechat_attach_dir", "")
            if wp:
                search_paths.append(wp)
        except Exception:
            pass
    search_paths.append("D:/微信记录/xwechat_files")
    for base_str in search_paths:
        base = Path(base_str)
        if not base.exists():
            continue
        for d in base.iterdir():
            if not d.is_dir():
                continue
            attach = d / "msg" / "attach"
            if attach.is_dir():
                return str(attach)
    return None


def get_wechat_pids():
    import subprocess
    r = subprocess.run(
        ["tasklist.exe", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, timeout=10
    )
    pids = []
    for line in r.stdout.strip().split("\n"):
        if "Weixin.exe" in line:
            parts = line.strip('"').split('","')
            if len(parts) >= 2:
                pids.append(int(parts[1]))
    return pids


def get_v2_ciphertext(attach_dir):
    v2_magic = b"\x07\x08V2\x08\x07"
    pattern = os.path.join(attach_dir, "*", "*", "Img", "*_t.dat")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    for f in files[:200]:
        try:
            with open(f, "rb") as fp:
                header = fp.read(31)
            if header[:6] == v2_magic and len(header) >= 31:
                return header[15:31], os.path.basename(f)
        except Exception:
            continue
    return None, None


def find_xor_key(attach_dir):
    v2_magic = b"\x07\x08V2\x08\x07"
    pattern = os.path.join(attach_dir, "*", "*", "Img", "*_t.dat")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    counts = {}
    for f in files[:32]:
        try:
            sz = os.path.getsize(f)
            with open(f, "rb") as fp:
                head = fp.read(6)
                fp.seek(sz - 2)
                tail = fp.read(2)
            if head == v2_magic and len(tail) == 2:
                k = (tail[0], tail[1])
                counts[k] = counts.get(k, 0) + 1
        except Exception:
            continue
    if not counts:
        return None
    best = max(counts, key=counts.get)
    xor_key = best[0] ^ 0xFF
    if xor_key == best[1] ^ 0xD9:
        return xor_key
    return xor_key


def try_key(key_bytes, ciphertext):
    try:
        cipher = AES.new(key_bytes, AES.MODE_ECB)
        dec = cipher.decrypt(ciphertext)
        if dec[:3] == b"\xff\xd8\xff":
            return "JPEG"
        if dec[:4] == b"\x89PNG":
            return "PNG"
        if dec[:4] == b"RIFF" and dec[8:12] == b"WEBP":
            return "WEBP"
        if dec[:4] == b"wxgf":
            return "WXGF"
        if dec[:3] == b"GIF":
            return "GIF"
    except Exception:
        pass
    return None


def is_rw(protect):
    return bool(protect & (PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY))


def scan_regions(h_process, regions, ciphertext, label=""):
    for idx, (base_addr, region_size) in enumerate(regions):
        if idx % 200 == 0:
            print(f"  {label} scanning {idx}/{len(regions)}", end="\r", flush=True)
        buf = ctypes.create_string_buffer(region_size)
        br = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(h_process, ctypes.c_void_p(base_addr), buf, region_size, ctypes.byref(br))
        if not ok or br.value < 16:
            continue
        data = buf.raw[:br.value]
        for m in RE_KEY32.finditer(data):
            kb = m.group()
            fmt = try_key(kb[:16], ciphertext)
            if fmt:
                return kb[:16].decode("ascii"), fmt
            fmt = try_key(kb, ciphertext)
            if fmt:
                return kb.decode("ascii"), fmt
        for m in RE_KEY16.finditer(data):
            kb = m.group()
            fmt = try_key(kb, ciphertext)
            if fmt:
                return kb.decode("ascii"), fmt
    return None, None


def verify_decrypt(attach_dir, aes_key_str, xor_key):
    v2_magic = b"\x07\x08V2\x08\x07"
    key = aes_key_str.encode("ascii")[:16]
    pattern = os.path.join(attach_dir, "*", "*", "Img", "*_t.dat")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    for f in files[:10]:
        try:
            with open(f, "rb") as fp:
                data = fp.read()
            if data[:6] != v2_magic:
                continue
            aes_size, xor_size = struct.unpack_from("<II", data, 6)
            aligned = aes_size if aes_size % 16 == 0 else (aes_size + 15) // 16 * 16
            if aes_size % 16 == 0:
                aligned += 16
            off = 15
            aes_data = data[off:off + aligned]
            cipher = AES.new(key, AES.MODE_ECB)
            dec_aes = Padding.unpad(cipher.decrypt(aes_data), AES.block_size)
            off += aligned
            raw_data = data[off:len(data) - xor_size]
            off += len(raw_data)
            xor_data = data[off:]
            dec_xor = bytes(b ^ xor_key for b in xor_data) if xor_key is not None else xor_data
            result = dec_aes + raw_data + dec_xor
            if result[:3] == b"\xff\xd8\xff":
                print(f"  {os.path.basename(f)} -> JPEG ({len(result):,}B)", flush=True)
                return True
            if result[:4] == b"\x89PNG":
                print(f"  {os.path.basename(f)} -> PNG ({len(result):,}B)", flush=True)
                return True
        except Exception:
            continue
    return False


def get_rw_regions(h_process):
    regions = []
    address = 0
    mbi = MEMORY_BASIC_INFORMATION()
    while address < 0x7FFFFFFFFFFF:
        ok = kernel32.VirtualQueryEx(h_process, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not ok:
            break
        if mbi.State == MEM_COMMIT and mbi.Protect != PAGE_NOACCESS and not (mbi.Protect & PAGE_GUARD) and mbi.RegionSize <= 50 * 1024 * 1024 and is_rw(mbi.Protect):
            regions.append((mbi.BaseAddress, mbi.RegionSize))
        next_addr = address + mbi.RegionSize
        if next_addr <= address:
            break
        address = next_addr
    return regions


def get_all_regions(h_process):
    regions = []
    address = 0
    mbi = MEMORY_BASIC_INFORMATION()
    while address < 0x7FFFFFFFFFFF:
        ok = kernel32.VirtualQueryEx(h_process, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not ok:
            break
        if mbi.State == MEM_COMMIT and mbi.Protect != PAGE_NOACCESS and not (mbi.Protect & PAGE_GUARD) and mbi.RegionSize <= 50 * 1024 * 1024:
            regions.append((mbi.BaseAddress, mbi.RegionSize))
        next_addr = address + mbi.RegionSize
        if next_addr <= address:
            break
        address = next_addr
    return regions


def main():
    print("=" * 60)
    print("微信 V2 图片 AES 密钥提取工具")
    print("=" * 60)

    attach_dir = find_attach_dir()
    if not attach_dir:
        print("[FAIL] 找不到微信数据目录，请确认 config.json 中的 wechat_attach_dir 正确")
        sys.exit(1)
    print(f"[OK] 微信附件目录: {attach_dir}")

    config = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            config = json.load(f)

    xor_key = find_xor_key(attach_dir)
    if xor_key is not None:
        print(f"[OK] XOR key: 0x{xor_key:02X}")
    else:
        print("[WARN] XOR key 未检测到，使用默认 0x88")
        xor_key = 0x88

    ciphertext, ct_file = get_v2_ciphertext(attach_dir)
    if ciphertext is None:
        print("[FAIL] 未找到 V2 格式的缩略图文件 (*_t.dat)")
        print("  请先在微信中打开聊天，确保有图片消息被加载")
        sys.exit(1)
    print(f"[OK] V2 密文样本: {ct_file}")
    print(f"    密文 Hex: {ciphertext.hex()}")

    if config.get("image_aes_key"):
        print(f"\n[*] config.json 已有密钥: {config['image_aes_key']}")
        fmt = try_key(config["image_aes_key"].encode("ascii")[:16], ciphertext)
        if fmt:
            print(f"[OK] 已有密钥有效 -> {fmt}")
            print("\n[*] 验证解密:")
            verify_decrypt(attach_dir, config["image_aes_key"], xor_key)
            return
        else:
            print("[WARN] 已有密钥无效，重新扫描...")

    pids = get_wechat_pids()
    if not pids:
        print("[FAIL] 微信未运行，请先启动微信")
        sys.exit(1)
    print(f"[OK] 微信进程 PID: {pids}")

    print("\n" + "=" * 60)
    print("请在微信中打开 2-3 张图片看大图（点击放大）")
    print("然后立即按 Enter 键开始扫描内存...")
    print("=" * 60)
    input()

    access = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
    aes_key = None
    for pid in pids:
        print(f"\n[*] 扫描 PID {pid}...", flush=True)
        h_process = kernel32.OpenProcess(access, False, pid)
        if not h_process:
            print(f"  无法打开进程 (尝试以管理员身份运行 Python)", flush=True)
            continue
        try:
            rw_regions = get_rw_regions(h_process)
            rw_mb = sum(r[1] for r in rw_regions) / 1024 / 1024
            all_regions = get_all_regions(h_process)
            all_mb = sum(r[1] for r in all_regions) / 1024 / 1024
            print(f"  RW: {len(rw_regions)} ({rw_mb:.0f} MB), 总计: {len(all_regions)} ({all_mb:.0f} MB)", flush=True)

            key, fmt = scan_regions(h_process, rw_regions, ciphertext, "RW")
            if key:
                aes_key = key
                print(f"\n*** 找到 AES key! -> {fmt} ***", flush=True)
                break

            rw_set = set((r[0], r[1]) for r in rw_regions)
            other = [r for r in all_regions if (r[0], r[1]) not in rw_set]
            key, fmt = scan_regions(h_process, other, ciphertext, "Other")
            if key:
                aes_key = key
                print(f"\n*** 找到 AES key! -> {fmt} ***", flush=True)
                break
        finally:
            kernel32.CloseHandle(h_process)

    if aes_key:
        print(f"\n{'=' * 60}", flush=True)
        print(f"AES key: {aes_key}", flush=True)
        print(f"XOR key: 0x{xor_key:02X}", flush=True)
        print(f"{'=' * 60}", flush=True)

        config["image_aes_key"] = aes_key
        config["image_xor_key"] = xor_key
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"[OK] 已保存到 {CONFIG_FILE}", flush=True)

        print("\n[*] 验证解密:", flush=True)
        verify_decrypt(attach_dir, aes_key, xor_key)
        print("\n[OK] 密钥提取成功！现在可以正常运行 plog_sync.py 了", flush=True)
    else:
        print("\n[FAIL] 未找到 AES key", flush=True)
        print("请确保:", flush=True)
        print("  1. 微信已登录并运行", flush=True)
        print("  2. 已打开 2-3 张图片看大图", flush=True)
        print("  3. 立即运行此脚本（密钥可能很快过期）", flush=True)
        print("  4. 尝试以管理员身份运行 Python", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
