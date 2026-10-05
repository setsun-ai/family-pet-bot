"""End-to-end: with the real logging setup, secrets from the config never reach the log file."""
import logging

from petbot.app import setup_logging
from petbot.config import Settings

BOT_TOKEN = "123456789:AAFakeTelegramTokenForTestsOnly_x"
API_KEY = "sk-ant-api03-fake-key-for-tests-only-abcdef"
SPORTS_KEY = "sportskey-fake-123456"


def test_secrets_from_config_are_redacted_in_log_file(tmp_path):
    log_file = tmp_path / "logs" / "bot.log"
    settings = Settings(bot_token=BOT_TOKEN, api_key=API_KEY, sports_api_key=SPORTS_KEY)
    setup_logging(settings, str(log_file))
    try:
        # library-style messages that embed credentials in URLs and headers
        logging.getLogger("aiogram").error("GET https://api.telegram.org/bot%s/getUpdates failed", BOT_TOKEN)
        logging.getLogger("petbot.ai").warning("auth failed for x-api-key: %s", API_KEY)
        logging.getLogger("petbot.sports").warning("GET https://www.thesportsdb.com/api/v1/json/%s/x", SPORTS_KEY)
        try:
            raise RuntimeError(f"connect error https://api.telegram.org/bot{BOT_TOKEN}/sendMessage")
        except RuntimeError:
            logging.getLogger("petbot").exception("handler failed")
        for handler in logging.getLogger().handlers:
            handler.flush()
        text = log_file.read_text(encoding="utf-8")
        assert "handler failed" in text  # the events are logged...
        for secret in (BOT_TOKEN, API_KEY, SPORTS_KEY):
            assert secret not in text  # ...but never the secrets, not even inside tracebacks
    finally:
        logging.basicConfig(handlers=[logging.NullHandler()], force=True)
