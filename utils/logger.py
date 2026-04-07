"""
Логирование: консоль + файл.
"""
import logging
from pathlib import Path

def setup_logger(name: str, log_file: str = "logs/bot.log") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", datefmt="%H:%M:%S")
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)
    log_path = Path(__file__).parent.parent / log_file
    if log_path.parent.exists():
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger
