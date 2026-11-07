import json
import os
import secrets
import string
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("BARTV_CONFIG", ROOT / "config.json"))


def _gen_pin() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


class Config(BaseModel):
    tv_ip: str = "192.168.1.150"
    client_key: Optional[str] = None
    normal_input: str = "HDMI_1"
    signage_input: str = "HDMI_2"
    ad_interval_minutes: int = 30
    ad_duration_seconds: float = 30
    input_switch_delay_seconds: float = 2
    promo_video_path: str = "static/media/promo.mp4"
    scheduler_enabled: bool = True
    mock_tv: bool = False
    # PIN protecting dashboard actions. Generated on first run and printed to console.
    pin: str = ""
    # Where /display looks for playable media (uploaded files served under /media/).
    media_dir: str = "static/media"
    # Known TVs [{ip, name, client_key}] so switching TVs keeps each pairing. Active = tv_ip.
    tvs: list[dict] = []

    @property
    def pin_required(self) -> bool:
        return bool(self.pin)

    @property
    def promo_url(self) -> str:
        return "/" + self.promo_video_path.lstrip("/")


def load_config(path: Optional[Path] = None) -> Config:
    path = path or CONFIG_PATH
    cfg = Config(**json.loads(path.read_text())) if path.exists() else Config()
    if not cfg.pin:
        cfg.pin = _gen_pin()
        save_config(cfg, path)
    return cfg


def save_config(cfg: Config, path: Optional[Path] = None) -> None:
    path = path or CONFIG_PATH
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg.model_dump(), indent=2) + "\n")
    tmp.replace(path)
