from decimal import Decimal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "sqlite:///./primeledger.db"
    FRONTEND_ORIGINS: str = "http://localhost:5173"
    LLM_MODE: str = "mock"
    WATSONX_API_KEY: str = ""
    WATSONX_PROJECT_ID: str = ""
    WATSONX_URL: str = "https://us-south.ml.cloud.ibm.com"
    WATSONX_MODEL_ID: str = ""


settings = Settings()

# FX table (all rates relative to USD=1)
FX_RATES: dict[str, Decimal] = {
    "USD": Decimal("1.00"),
    "EUR": Decimal("1.08"),
    "GBP": Decimal("1.27"),
}

# Confidence thresholds
AUTO_ACCEPT: float = 0.90
NEEDS_REVIEW: float = 0.60

# Chart of accounts (section 6.4)
CHART_OF_ACCOUNTS: dict[str, str] = {
    "1000": "Cash",
    "6100": "Bank Fees",
    "7900": "FX Gain/Loss",
    "2000": "Accounts Payable",
    "1200": "Accounts Receivable",
    "9999": "Suspense",
}

# Metrics baseline assumption: 2 minutes of manual work per record
MANUAL_BASELINE_MIN_PER_RECORD: int = 2
