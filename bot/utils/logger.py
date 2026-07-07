from loguru import logger
import sys


def setup_logger():
    logger.remove()

    logger.add(
        sys.stdout,
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
    )

    logger.info("Logger initialized")

    return logger