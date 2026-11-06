import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("BARTV_CONFIG", ROOT / "config.json"))


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

    @property
    def promo_url(self) -> str:
        return "/" + self.promo_video_path.lstrip("/")


def load_config(path: Path = CONFIG_PATH) -> Config:
    if path.exists():
        return Config(**json.loads(path.read_text()))
    cfg = Config()
    save_config(cfg, path)
    return cfg


def save_config(cfg: Config, path: Path = CONFIG_PATH) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg.model_dump(), indent=2) + "\n")
    tmp.replace(path)
