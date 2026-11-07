"""Network diagnostics for the venue: Wi-Fi, LAN neighbours, SSDP/mDNS devices, LG TVs, Bluetooth.

Discovery is deliberately light: it reads the OS neighbour table, sends one round of
SSDP/mDNS multicast queries and only knocks on the webOS ports (3000/3001) of
devices already in that table - no subnet-wide port scanning.
"""
import asyncio
import json
import logging
import platform
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Optional

from .tv import probe

log = logging.getLogger("bartv.netdiag")
WINDOWS = platform.system() == "Windows"

# Offline vendor hints for gear typically seen in venues; everything else is looked up
# online once (api.macvendors.com, 1 req/s) and cached in vendors.json.
KNOWN_OUI = {
    "34E380": "Genexis (router)", "743FC2": "Hikvision (camera/NVR)", "DC07F8": "Hikvision (camera/NVR)",
    "F09FC2": "Ubiquiti (Wi-Fi AP)", "101EDA": "Ingenico (card terminal)", "4437E6": "Hon Hai / Foxconn",
}


def _run(cmd: list[str], timeout: float = 10) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout.decode("utf-8", errors="replace") if r.stdout else ""
    except Exception:
        log.debug("command failed: %s", cmd, exc_info=True)
        return ""


def local_ip(target: str = "8.8.8.8") -> Optional[str]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 1))  # no packet sent; picks the interface that routes there
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


# ---------- Wi-Fi ----------

def parse_netsh_interfaces(text: str) -> dict:
    out = {}
    for key, field in (("SSID", "ssid"), ("BSSID", "bssid"), ("Signal", "signal"), ("Authentication", "auth"),
                       ("Band", "band"), ("Channel", "channel"), ("State", "state")):
        m = re.search(rf"^\s*{key}\s*:\s*(.+?)\s*$", text, re.M)
        if m:
            out[field] = m.group(1)
    return out


def parse_netsh_networks(text: str) -> list[dict]:
    nets, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^SSID \d+ : ?(.*)$", line.strip())
        if m:
            cur = {"ssid": m.group(1).strip() or "(hidden)", "auth": "", "signal": 0, "bssids": 0}
            nets.append(cur)
            continue
        if cur is None:
            continue
        kv = re.match(r"^\s*([A-Za-z0-9 ]+?)\s*:\s*(.+?)\s*$", line)
        if not kv:
            continue
        k, v = kv.group(1).lower(), kv.group(2)
        if k == "authentication":
            cur["auth"] = v
        elif k.startswith("bssid"):
            cur["bssids"] += 1
        elif k == "signal":
            cur["signal"] = max(cur["signal"], int(re.sub(r"\D", "", v) or 0))
    return sorted(nets, key=lambda n: -n["signal"])


def wifi() -> dict:
    if not WINDOWS:
        return {"supported": False, "note": "Wi-Fi details are only read on Windows"}
    return {"supported": True,
            "connected": parse_netsh_interfaces(_run(["netsh", "wlan", "show", "interfaces"])),
            "networks": parse_netsh_networks(_run(["netsh", "wlan", "show", "networks", "mode=bssid"]))}


# ---------- LAN neighbours ----------

def parse_arp(text: str, my_ip: Optional[str]) -> list[dict]:
    prefix = ".".join(my_ip.split(".")[:3]) + "." if my_ip else ""
    out = []
    for ip, mac in re.findall(r"(\d+\.\d+\.\d+\.\d+)\D+?((?:[0-9a-fA-F]{2}[-:]){5}[0-9a-fA-F]{2})", text):
        mac = mac.upper().replace("-", ":")
        last = int(ip.split(".")[-1])
        if not ip.startswith(prefix) or ip == my_ip or last in (0, 255) or mac in ("FF:FF:FF:FF:FF:FF",):
            continue
        if any(ip == d["ip"] for d in out):
            continue
        out.append({"ip": ip, "mac": mac})
    return sorted(out, key=lambda d: int(d["ip"].split(".")[-1]))


def is_random_mac(mac: str) -> bool:
    return bool(int(mac[:2], 16) & 0x02)  # locally administered bit: phones' private MACs


class VendorLookup:
    def __init__(self, cache: Path):
        self.cache_path = cache
        try:
            self.cache = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            self.cache = {}

    def lookup(self, mac: str, online: bool = True) -> str:
        oui = mac.replace(":", "")[:6]
        if is_random_mac(mac):
            return "Private (random) MAC - usually a phone/laptop"
        if oui in KNOWN_OUI:
            return KNOWN_OUI[oui]
        if oui in self.cache:
            return self.cache[oui]
        if not online:
            return ""
        try:
            time.sleep(1.1)  # free API allows ~1 request/second
            req = urllib.request.Request(f"https://api.macvendors.com/{oui}", headers={"User-Agent": "BarTV"})
            name = urllib.request.urlopen(req, timeout=4).read().decode(errors="replace").strip()
        except Exception as e:
            name = "Unknown" if "404" in str(e) else ""
        if name:
            self.cache[oui] = name
            try:
                self.cache_path.write_text(json.dumps(self.cache, indent=1), encoding="utf-8")
            except OSError:
                pass
        return name


def neighbours(my_ip: Optional[str]) -> list[dict]:
    text = _run(["arp", "-a"]) if WINDOWS else (_run(["ip", "neigh"]) or _run(["arp", "-an"]))
    return parse_arp(text, my_ip)


# ---------- SSDP / mDNS ----------

SSDP_ST = ("ssdp:all", "urn:lge-com:service:webos-second-screen:1", "urn:dial-multiscreen-org:service:dial:1")


def _mcast_socket(my_ip: Optional[str]) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    if my_ip:
        # Multi-homed laptops (VPN adapters) would otherwise send the query out the wrong interface.
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(my_ip))
    s.bind((my_ip or "0.0.0.0", 0))
    s.settimeout(0.5)
    return s


def ssdp(my_ip: Optional[str], seconds: float = 4.0) -> dict[str, dict]:
    found: dict[str, dict] = {}
    try:
        s = _mcast_socket(my_ip)
    except OSError:
        return found
    with s:
        for st in SSDP_ST:
            q = (f"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
                 f"MX: 2\r\nST: {st}\r\n\r\n").encode()
            try:
                s.sendto(q, ("239.255.255.250", 1900))
            except OSError:
                return found
        end = time.time() + seconds
        while time.time() < end:
            try:
                data, addr = s.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            h = {k.lower(): v.strip() for k, v in re.findall(r"^([^:\r\n]+):(.*)$", data.decode(errors="replace"), re.M)}
            d = found.setdefault(addr[0], {"server": "", "types": set(), "location": "", "name": ""})
            d["server"] = d["server"] or h.get("server", "")
            d["types"].add(h.get("st", ""))
            d["location"] = d["location"] or h.get("location", "")
            d["name"] = d["name"] or h.get("dlnadevicename.lge.com", "")
    for ip, d in found.items():
        if d["location"] and not d["name"]:
            try:
                x = urllib.request.urlopen(d["location"], timeout=2).read().decode(errors="replace")
                g = lambda t: (re.search(f"<{t}>(.*?)</{t}>", x, re.S) or [None, ""])[1].strip()
                d["name"] = g("friendlyName")
                d["maker"] = g("manufacturer")
                d["model"] = (g("modelName") + " " + g("modelNumber")).strip()
            except Exception:
                pass
        d["types"] = sorted(t for t in d["types"] if t)
    return found


def mdns(my_ip: Optional[str], seconds: float = 3.0) -> dict[str, list[str]]:
    found: dict[str, set] = {}
    try:
        s = _mcast_socket(my_ip)
    except OSError:
        return {}
    with s:
        for n in ("_services._dns-sd._udp.local", "_googlecast._tcp.local", "_airplay._tcp.local", "_http._tcp.local"):
            q = b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
            for part in n.split("."):
                q += bytes([len(part)]) + part.encode()
            q += b"\x00\x00\x0c\x80\x01"  # PTR, ask for unicast reply
            try:
                s.sendto(q, ("224.0.0.251", 5353))
            except OSError:
                return {}
        end = time.time() + seconds
        while time.time() < end:
            try:
                data, addr = s.recvfrom(9000)
            except (socket.timeout, OSError):
                continue
            labels = {l.decode(errors="replace") for l in re.findall(rb"[A-Za-z0-9_\-][A-Za-z0-9 _\-\.]{3,40}", data)}
            found.setdefault(addr[0], set()).update(l for l in labels if not l.startswith("_") or "._tcp" in l)
    return {ip: sorted(v)[:10] for ip, v in found.items()}


def is_lg(*texts: str) -> bool:
    t = " ".join(texts).lower()
    return any(k in t for k in ("lge", "webos", "lg electronics", "[lg]", " lg "))


# ---------- Bluetooth ----------

def bluetooth_paired() -> list[dict]:
    if not WINDOWS:
        return []
    raw = _run(["powershell", "-NoProfile", "-Command",
                "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
                "Where-Object { $_.InstanceId -like 'BTHENUM\\DEV_*' -or $_.InstanceId -like 'BTHLE\\DEV_*' } | "
                "Select-Object Status,FriendlyName,InstanceId | ConvertTo-Json -Compress"], timeout=15)
    try:
        items = json.loads(raw) if raw.strip() else []
    except ValueError:
        return []
    items = items if isinstance(items, list) else [items]
    out = []
    for i in items:
        m = re.search(r"DEV_([0-9A-F]{12})", i.get("InstanceId", ""), re.I)
        out.append({"name": i.get("FriendlyName") or "?", "address": ":".join(re.findall("..", m.group(1))) if m else "",
                    "connected": i.get("Status") == "OK"})
    return out


async def bluetooth_nearby(seconds: float = 5.0) -> dict:
    try:
        from bleak import BleakScanner
    except ImportError:
        return {"supported": False, "note": "Install 'bleak' (setup.bat) to scan nearby Bluetooth LE devices"}
    try:
        found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    except Exception as e:
        return {"supported": False, "note": f"Bluetooth scan unavailable: {e or 'adapter off?'}"}
    devs = []
    for addr, (dev, adv) in found.items():
        name = adv.local_name or dev.name or ""
        devs.append({"address": addr, "name": name, "rssi": adv.rssi, "lg": is_lg(" " + name + " ")})
    devs.sort(key=lambda d: (not d["name"], -(d["rssi"] or -999)))
    return {"supported": True, "devices": devs}


# ---------- full scan ----------

async def scan(tv_ip: Optional[str], vendor_cache: Path) -> dict:
    t0 = time.time()
    my_ip = local_ip(tv_ip or "8.8.8.8")
    loop = asyncio.get_running_loop()
    wifi_f = loop.run_in_executor(None, wifi)
    ssdp_f = loop.run_in_executor(None, ssdp, my_ip)
    mdns_f = loop.run_in_executor(None, mdns, my_ip)
    bt_paired_f = loop.run_in_executor(None, bluetooth_paired)
    bt_near = asyncio.create_task(bluetooth_nearby())
    ssdp_res, mdns_res = await ssdp_f, await mdns_f
    neigh = await loop.run_in_executor(None, neighbours, my_ip)
    known = {d["ip"] for d in neigh}
    for ip in list(ssdp_res) + list(mdns_res):
        if ip not in known and ip != my_ip:
            neigh.append({"ip": ip, "mac": ""})
            known.add(ip)
    probes = await asyncio.gather(*(probe(d["ip"], timeout=1.0) for d in neigh))
    vendors = VendorLookup(vendor_cache)
    for d, ports in zip(neigh, probes):
        d["webos_ports"] = ports
        d["vendor"] = await loop.run_in_executor(None, vendors.lookup, d["mac"]) if d["mac"] else ""
        sd = ssdp_res.get(d["ip"])
        if sd:
            d["ssdp"] = sd
        if d["ip"] in mdns_res:
            d["mdns"] = mdns_res[d["ip"]]
        d["lg_tv"] = any(ports.values()) or is_lg(d["vendor"], json.dumps(sd or {}), " ".join(mdns_res.get(d["ip"], [])))
        d["is_tv_ip"] = d["ip"] == tv_ip
    neigh.sort(key=lambda d: int(d["ip"].split(".")[-1]))
    wifi_res = await wifi_f
    bt = {"paired": await bt_paired_f, "nearby": await bt_near}
    answered = bool(ssdp_res or mdns_res or any(any(d["webos_ports"].values()) for d in neigh))
    return {
        "laptop_ip": my_ip, "tv_ip": tv_ip, "wifi": wifi_res, "devices": neigh,
        "lg_tvs": [d for d in neigh if d["lg_tv"]],
        "multicast_answers": len(ssdp_res) + len(mdns_res),
        "isolation_suspected": bool(neigh) and not answered,
        "tv_ip_in_range": bool(my_ip and tv_ip and my_ip.rsplit(".", 1)[0] == tv_ip.rsplit(".", 1)[0]),
        "bluetooth": bt, "seconds": round(time.time() - t0, 1),
    }


async def find_tvs(my_ip: Optional[str]) -> dict:
    """LG TVs via SSDP, plus a webOS-port knock on visible neighbours (TVs that ignore SSDP),
    plus a Bluetooth LE look so a TV on another / isolated network is still reported as nearby."""
    loop = asyncio.get_running_loop()
    ssdp_f = loop.run_in_executor(None, ssdp, my_ip, 3.0)
    bt_f = asyncio.create_task(bluetooth_nearby(4.0))
    neigh = await loop.run_in_executor(None, neighbours, my_ip)
    probes = await asyncio.gather(*(probe(d["ip"], timeout=1.0) for d in neigh))
    found = await ssdp_f
    tvs = {ip: {"ip": ip, "name": d.get("name") or "LG webOS TV"} for ip, d in found.items()
           if is_lg(d.get("server", ""), " ".join(d.get("types", [])), d.get("maker", ""), d.get("name", ""))}
    for d, ports in zip(neigh, probes):
        if any(ports.values()) and d["ip"] not in tvs:
            tvs[d["ip"]] = {"ip": d["ip"], "name": "webOS TV (control port open)"}
    bt = await bt_f
    bt_tvs = [{"name": b["name"].replace("[LG] ", ""), "rssi": b["rssi"]} for b in bt.get("devices", []) if b["lg"]]
    return {"tvs": list(tvs.values()), "bluetooth_tvs": bt_tvs,
            "isolated": bool(neigh) and not found and not tvs}
